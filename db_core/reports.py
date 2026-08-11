#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的经营报表职责：
销售报表（跨 orders + semi_finished_rules）、仪表盘经营分析、
餐桌经营分析、销售趋势、退款统计。
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

from config import ORDER_LINE_REVENUE_SQL

from db_core.utils import CHINA_TZ, ensure_beijing_datetime

logger = logging.getLogger(__name__)


class _ReportsMixin:
    """跨表销售报表与经营分析类查询。"""

    async def compute_sales_report(
        self,
        start_date: str,
        end_date: str,
        station: Optional[str] = None
    ) -> Dict:
        """基于数据库订单计算销售报表（跨表查询）"""
        from services.dish_normalize import normalize_dish_name as normalize

        def to_local_dt(date_str: str, end_of_day: bool = False):
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            else:
                dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            return dt.replace(tzinfo=CHINA_TZ)

        start_dt = to_local_dt(start_date)
        end_dt = to_local_dt(end_date, end_of_day=True)

        params: List[Any] = [start_dt.isoformat(), end_dt.isoformat()]
        station_clause = ""
        if station and station != 'all':
            station_clause = " AND station = ?"; params.append(station)
        else:
            station_clause = " AND station != 'loumian'"

        # 1. 菜品销量（查 orders 表）
        orders_tdb = self.table("orders")
        async with orders_tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""SELECT dish_name, station, SUM(quantity) as total_qty,
                           SUM({ORDER_LINE_REVENUE_SQL}) as total_amount, COUNT(*) as order_count
                    FROM orders WHERE order_time >= ? AND order_time <= ?{station_clause}
                    GROUP BY dish_name, station ORDER BY total_qty DESC""",
                params
            )
            rows = await cursor.fetchall()

        dish_sales = []
        total_orders, total_revenue = 0, 0.0
        for row in rows:
            qty, amount = row[2] or 0, row[3] or 0.0
            total_orders += row[4] or 0
            total_revenue += amount
            dish_sales.append({
                "dish_name": row[0], "station": row[1] or "",
                "qty": qty, "total_amount": round(amount, 2),
                "order_count": row[4],
            })

        # 2. 半成品换算规则（查 semi_finished_rules 表）
        semi_tdb = self.table("semi_finished_rules")
        async with semi_tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT dish_name, semi_name, position, factor, unit, category FROM semi_finished_rules"
            )
            rule_rows = await cursor.fetchall()

        semi_map: Dict[str, List] = {}
        norm_map: Dict[str, List] = {}
        for r in rule_rows:
            sub_category = str(r[5] or "").strip()
            entry = {
                "semi_name": r[1], "position": r[2] or "未分类",
                "factor": r[3] or 1,
                "unit": r[4] or "",
                # semi_finished_rules.category 字段在业务里作为“子分类”使用
                "category": "",
                "sub_category": sub_category,
            }
            if r[0] not in semi_map:
                semi_map[r[0]] = []
            semi_map[r[0]].append(entry)
            norm = normalize(r[0])
            if norm not in norm_map:
                norm_map[norm] = []
            norm_map[norm].append(entry)

        for dish in dish_sales:
            dish["has_rules"] = dish["dish_name"] in semi_map or normalize(dish["dish_name"]) in norm_map

        # 3. 按岗位聚合用量
        position_agg: Dict[str, Dict] = {}
        for dish in dish_sales:
            exact_rules = semi_map.get(dish["dish_name"], [])
            rules_to_use = exact_rules if exact_rules else norm_map.get(normalize(dish["dish_name"]), [])
            for rule in rules_to_use:
                key = (
                    rule["semi_name"],
                    rule["unit"],
                    rule.get("category", ""),
                    rule.get("sub_category", ""),
                )
                pos = rule["position"]
                qty = float(dish["qty"]) * float(rule["factor"])
                if pos not in position_agg:
                    position_agg[pos] = {}
                position_agg[pos][key] = position_agg[pos].get(key, 0.0) + qty

        def fmt(v):
            try:
                f = float(v)
                return int(f) if abs(f - int(f)) < 1e-9 else round(f, 2)
            except Exception:
                return v

        semi_finished = []
        for pos in sorted(position_agg.keys()):
            items = []
            for (semi_name, unit, category, sub_category), qty in sorted(position_agg[pos].items(), key=lambda x: x[1], reverse=True):
                normalized_sub_category = sub_category or category or ""
                items.append({
                    "semi_name": semi_name,
                    "qty": fmt(qty),
                    "unit": unit,
                    # 兼容前后端不同版本：category 与 sub_category 同步返回
                    "category": normalized_sub_category,
                    "sub_category": normalized_sub_category,
                })
            semi_finished.append({"position": pos, "items": items})

        covered = sum(1 for d in dish_sales if d.get("has_rules"))

        return {
            "date_range": {"start": start_date, "end": end_date},
            "station_filter": station or "all",
            "summary": {
                "total_orders": total_orders,
                "total_dishes": sum(d["qty"] for d in dish_sales),
                "total_revenue": round(total_revenue, 2),
                "unique_dishes": len(dish_sales),
                "covered_rules": covered,
            },
            "dish_sales": dish_sales,
            "semi_finished": semi_finished,
        }

    KDS_BACKLOG_MEDIUM = 8
    KDS_BACKLOG_HIGH = 15
    KDS_OVERDUE_MINUTES = 20
    KDS_EXCLUDED_STATION = "loumian"

    async def aggregate_kds_backlog(self) -> Dict[str, Any]:
        """各档口今日待出餐队列，口径与 KDS pendingCount / load level 一致；排除楼面。"""
        now = datetime.now(CHINA_TZ)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        overdue_cutoff = (now - timedelta(minutes=self.KDS_OVERDUE_MINUTES)).isoformat()

        tdb = self.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """SELECT station,
                          COUNT(*) AS pending,
                          SUM(CASE WHEN order_time < ? THEN 1 ELSE 0 END) AS overdue,
                          MIN(order_time) AS oldest_time
                   FROM orders
                   WHERE dish_status = '待出餐'
                     AND quantity > 0
                     AND status != '退菜'
                     AND business_flow_id NOT LIKE '%_refund_%'
                     AND order_time >= ?
                     AND station != ?
                   GROUP BY station
                   ORDER BY pending DESC, station ASC""",
                (overdue_cutoff, today_start, self.KDS_EXCLUDED_STATION),
            )
            rows = [dict(r) for r in await cursor.fetchall()]

        stations: List[Dict[str, Any]] = []
        total_pending = 0
        overdue_count = 0
        busiest_station: Optional[Dict[str, Any]] = None

        for row in rows:
            pending = int(row.get("pending") or 0)
            overdue = int(row.get("overdue") or 0)
            station_id = row.get("station") or ""
            if not station_id or station_id == self.KDS_EXCLUDED_STATION:
                continue
            oldest_time = row.get("oldest_time")
            oldest_wait_minutes = 0.0
            if oldest_time:
                try:
                    oldest_dt = ensure_beijing_datetime(oldest_time)
                    oldest_wait_minutes = round(
                        max(0.0, (now - oldest_dt).total_seconds() / 60.0), 1
                    )
                except (TypeError, ValueError):
                    oldest_wait_minutes = 0.0

            if pending >= self.KDS_BACKLOG_HIGH:
                load_level = "high"
            elif pending >= self.KDS_BACKLOG_MEDIUM:
                load_level = "medium"
            else:
                load_level = "low"

            stations.append(
                {
                    "station_id": station_id,
                    "pending": pending,
                    "overdue": overdue,
                    "oldest_wait_minutes": oldest_wait_minutes,
                    "load_level": load_level,
                }
            )
            total_pending += pending
            overdue_count += overdue
            if busiest_station is None and station_id:
                busiest_station = {"station_id": station_id, "pending": pending}

        return {
            "total_pending": total_pending,
            "overdue_count": overdue_count,
            "busiest_station": busiest_station,
            "stations": stations,
        }

    async def aggregate_dashboard_extras(self) -> Dict[str, Any]:
        """今日菜品分类数、餐桌占用、紧急订单数。"""
        now = datetime.now(CHINA_TZ)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        urgent_cutoff = (now - timedelta(minutes=20)).isoformat()
        tdb = self.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """SELECT COUNT(DISTINCT CASE
                       WHEN category IS NOT NULL AND TRIM(category) != '' THEN category
                       ELSE NULL END) FROM orders
                   WHERE order_time >= ?""",
                (today_start,),
            )
            category_count = (await cursor.fetchone())[0] or 0
            if category_count == 0:
                await cursor.execute(
                    """SELECT COUNT(DISTINCT dish_name) FROM orders WHERE order_time >= ?""",
                    (today_start,),
                )
                category_count = min((await cursor.fetchone())[0] or 0, 50)
            await cursor.execute(
                """SELECT COUNT(*) FROM orders
                   WHERE order_time >= ? AND order_time < ?""",
                (today_start, urgent_cutoff),
            )
            urgent_count = (await cursor.fetchone())[0] or 0

        ttables = self.table("tables")
        async with ttables.conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) FROM tables")
            total_tables = (await cursor.fetchone())[0] or 0
            await cursor.execute(
                """SELECT COUNT(*) FROM tables
                   WHERE status = 'occupied' OR amount > 0 OR people > 0"""
            )
            occupied = (await cursor.fetchone())[0] or 0

        occupancy_pct = round(occupied / total_tables * 100, 1) if total_tables else 0.0
        return {
            "dish_category_count": category_count,
            "table_occupancy": {
                "occupied": occupied,
                "total": total_tables,
                "percent": occupancy_pct,
            },
            "urgent_order_count": urgent_count,
        }

    async def aggregate_table_operations(self, start_date: str, end_date: str) -> Dict[str, Any]:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, microsecond=999000, tzinfo=CHINA_TZ
        )
        snapshot = await self.get_table_snapshot_stats()
        tdb = self.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""SELECT table_number,
                           COUNT(*) as order_lines,
                           SUM(quantity) as total_qty,
                           SUM({ORDER_LINE_REVENUE_SQL}) as revenue,
                           COUNT(DISTINCT dish_name) as dish_variety
                    FROM orders
                    WHERE order_time >= ? AND order_time <= ?
                      AND table_number IS NOT NULL AND table_number != ''
                    GROUP BY table_number
                    ORDER BY revenue DESC""",
                (start_dt.isoformat(), end_dt.isoformat()),
            )
            rows = [dict(r) for r in await cursor.fetchall()]
        tables_served = len(rows)
        total_revenue = sum(float(r.get("revenue") or 0) for r in rows)
        total_people_est = sum(int(r.get("total_qty") or 0) for r in rows)
        return {
            "date_range": {"start": start_date, "end": end_date},
            "snapshot": snapshot,
            "tables_served": tables_served,
            "total_revenue": round(total_revenue, 2),
            "avg_revenue_per_table": round(total_revenue / tables_served, 2) if tables_served else 0,
            "avg_order_lines_per_table": round(
                sum(int(r.get("order_lines") or 0) for r in rows) / tables_served, 1
            ) if tables_served else 0,
            "by_table": rows[:100],
        }

    async def aggregate_sales_trend(
        self,
        start_date: str,
        end_date: str,
        granularity: str = "day",
        station: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, microsecond=999000, tzinfo=CHINA_TZ
        )
        params: List[Any] = [start_dt.isoformat(), end_dt.isoformat()]
        station_clause = ""
        if station and station != "all":
            station_clause = " AND station = ?"
            params.append(station)
        if granularity == "hour":
            bucket_expr = "CAST(strftime('%H', order_time) AS INTEGER)"
        elif granularity == "month":
            bucket_expr = "strftime('%Y-%m', order_time)"
        elif granularity == "week":
            bucket_expr = "strftime('%Y-W%W', order_time)"
        else:
            bucket_expr = "date(order_time)"
        tdb = self.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""SELECT {bucket_expr} as bucket,
                           COUNT(*) as order_lines,
                           SUM(quantity) as total_quantity,
                           SUM({ORDER_LINE_REVENUE_SQL}) as revenue
                    FROM orders
                    WHERE order_time >= ? AND order_time <= ?{station_clause}
                    GROUP BY bucket ORDER BY bucket""",
                params,
            )
            rows = await cursor.fetchall()
        return [
            {
                "period": row[0],
                "order_lines": row[1] or 0,
                "total_quantity": row[2] or 0,
                "revenue": round(row[3] or 0, 2),
            }
            for row in rows
        ]

    async def aggregate_refund_stats(
        self,
        start_date: str,
        end_date: str,
        station: Optional[str] = None,
    ) -> Dict[str, Any]:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, microsecond=999000, tzinfo=CHINA_TZ
        )
        params: List[Any] = [start_dt.isoformat(), end_dt.isoformat()]
        station_clause = ""
        if station and station != "all":
            station_clause = " AND station = ?"
            params.append(station)
        tdb = self.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""SELECT COUNT(*) FROM orders
                    WHERE order_time >= ? AND order_time <= ?{station_clause}
                      AND (quantity < 0 OR LOWER(COALESCE(status,'')) LIKE '%退%')""",
                params,
            )
            refund_lines = (await cursor.fetchone())[0] or 0
            await cursor.execute(
                f"""SELECT dish_name, station, SUM(quantity) as qty, COUNT(*) as cnt
                    FROM orders
                    WHERE order_time >= ? AND order_time <= ?{station_clause}
                      AND (quantity < 0 OR LOWER(COALESCE(status,'')) LIKE '%退%')
                    GROUP BY dish_name, station
                    ORDER BY qty ASC LIMIT 50""",
                params,
            )
            items = [dict(r) for r in await cursor.fetchall()]
        return {
            "date_range": {"start": start_date, "end": end_date},
            "refund_line_count": refund_lines,
            "items": items,
        }
