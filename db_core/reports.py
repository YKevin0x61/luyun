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

# 营业日切点与窗口函数的**唯一定义**在 `db_core/business_day.py`（R-T3-03 / 票 21 从本
# 模块的局部实现提上去）：一个营业日 = ``[当日 06:00, 次日 06:00)``。`BUSINESS_DAY_CUT_HOUR`
# 与 `_business_day_range` 仍从本模块 re-export，既有调用点与
# tests/test_sales_report_business_day.py 的交叉核对用例不受影响。
#
# DOC-07：db_core 不得 import services —— `services/business_day.py` 自己
# `from db_core.utils import CHINA_TZ`，db_core 再反向 import 就构成 db_core ↔ services 的
# 真环（全仓唯一一处真的环）。口径下沉到 db_core 后这个环消失；改切点时要同时改
# `db_core/business_day.py` 的常数与 services/business_day.BUSINESS_DAY_CUT_HOUR，
# 两边由 tests/test_sales_report_business_day.py 的边界用例同测。
from db_core.business_day import (
    BUSINESS_DAY_CUT_HOUR,
    business_day_range as _business_day_range,
    business_day_window,
)
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
        """基于数据库订单计算销售报表（跨表查询）。

        两个日期是**营业日**（06:00 切），区间是 ``[start_date 06:00, end_date+1 06:00)``
        ——与采集/对账/卫生同一口径（CORR-05），切点由模块级 ``_business_day_range``
        给出（DOC-07：不再 import `services.business_day`，避免 db_core ↔ services 真环）。
        原先按日历日翻成 ``[00:00:00, 23:59:59.999]``：门店 06:00 前那段的单属于前一
        营业日，两种切法在凌晨对不上（生产库当期 06:00 前订单 0 行，所以没有实际数据
        被算错，属口径隐患）。
        """
        from services.dish_normalize import normalize_dish_name as normalize

        def to_local_dt(date_str: str, end_of_range: bool = False):
            """营业日 → 北京时时间戳。``end_of_range`` 时取**次日** 06:00（开区间右端）。

            06:00 切点与"次日"由模块级 ``_business_day_range`` 手算（DOC-07：不再 import
            `services.business_day`，理由见那里的注释）。解析仍走 `strptime`：它比
            `date.fromisoformat` 宽容（接受 `2026-9-2` 这类不补零写法），换掉解析方式
            等于改了既有输入面。
            """
            start, end = _business_day_range(date_str)
            return end if end_of_range else start

        start_dt = to_local_dt(start_date)
        end_dt = to_local_dt(end_date, end_of_range=True)

        params: List[Any] = [start_dt.isoformat(), end_dt.isoformat()]
        station_clause = ""
        if station and station != 'all':
            station_clause = " AND station = ?"; params.append(station)
        elif station == 'all':
            # `all` 是真正的「全店」：含楼面（套餐/茶位）。区分它是为了避免
            # 「汇总排除楼面、走势图含楼面」的两套口径（本页审查 R3）。
            station_clause = ""
        else:
            # 默认口径 = 厨房出品，排除楼面；页面要标注「不含楼面」。
            station_clause = " AND station != 'loumian'"

        # 1. 菜品销量（查 orders 表）。右端是**开区间**（次日 06:00），与
        # `_business_day_range` 的 [cut, 次日 cut) 语义一致。
        orders_tdb = self._connection.table("orders")
        async with orders_tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""SELECT dish_name, station, SUM(quantity) as total_qty,
                           SUM({ORDER_LINE_REVENUE_SQL}) as total_amount, COUNT(*) as order_count,
                           COUNT(CASE WHEN COALESCE(quantity, 0) > 0
                                       AND LOWER(COALESCE(status, '')) NOT LIKE '%退%'
                                      THEN 1 END) as effective_count
                    FROM orders WHERE order_time >= ? AND order_time < ?{station_clause}
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
                # R43：每道菜的「单数」不再把退菜/零数量行算进去（以前会出现单数 > 份数）。
                "order_count": row[5] if row[5] is not None else 0,
            })

        # 2. 半成品换算规则（查 semi_finished_rules 表）
        semi_tdb = self._connection.table("semi_finished_rules")
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

        # 1b. 账单数：`business_flow_id` 形如
        # ``YY001301-261113-0001_(外卖)酱皇豉椒蒸凤爪_001``，第一个下划线之前是账单前缀。
        # 「订单数」和客单价的分母都应该是账单数，而不是菜品行数（本页审查 R2）。
        # 退菜行 / 数量 0 的行不计入账单，避免整单退菜被当成营业单。
        async with orders_tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""SELECT COUNT(DISTINCT NULLIF(
                               regexp_replace(COALESCE(business_flow_id, ''), '_.*$', ''),
                               ''))
                    FROM orders
                    WHERE order_time >= ? AND order_time < ?{station_clause}
                      AND COALESCE(quantity, 0) > 0
                      AND LOWER(COALESCE(status, '')) NOT LIKE '%退%'""",
                params,
            )
            total_bills = (await cursor.fetchone())[0] or 0

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
        # R40：原来按中文键的 Unicode 码位排序（案板/煎炸/明档…），跟用量无关；
        # 改成按该岗位半成品总量降序，页面上「用得最多的岗位」排在前面。
        position_totals = {p: sum(items.values()) for p, items in position_agg.items()}
        for pos in sorted(position_agg.keys(), key=lambda p: (-position_totals[p], p)):
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

        # 菜品名去重：同一道菜可能出现在多个档口（堂食/外卖），页面「菜品数」与
        # 「规则覆盖」按菜名算，不按 dish_name+station 组合算（本页审查 R22）。
        unique_dish_names = {d["dish_name"] for d in dish_sales}
        covered_dish_names = {d["dish_name"] for d in dish_sales if d.get("has_rules")}
        if station and station != "all":
            scope = "station"
        elif station == "all":
            scope = "full"
        else:
            scope = "kitchen"

        return {
            "date_range": {"start": start_date, "end": end_date},
            "station_filter": station or "all",
            # scope 告诉前端这份汇总覆盖什么：kitchen=默认（不含楼面）、
            # full=全店（含楼面）、station=指定档口。页面据此标注口径。
            "scope": scope,
            "excluded_stations": ["loumian"] if scope == "kitchen" else [],
            "summary": {
                # total_orders / total_order_lines 是菜品行数（旧字段，保留兼容）；
                # 页面上的「订单数」与客单价分母改用 total_bills（账单数）。
                "total_orders": total_orders,
                "total_order_lines": total_orders,
                "total_bills": total_bills,
                "total_dishes": sum(d["qty"] for d in dish_sales),
                "total_revenue": round(total_revenue, 2),
                "unique_dishes": len(unique_dish_names),
                "dish_station_pairs": len(dish_sales),
                "covered_rules": len(covered_dish_names),
            },
            "dish_sales": dish_sales,
            "semi_finished": semi_finished,
        }

    KDS_BACKLOG_MEDIUM = 8
    KDS_BACKLOG_HIGH = 15
    KDS_OVERDUE_MINUTES = 20
    KDS_EXCLUDED_STATION = "loumian"

    async def aggregate_kds_backlog(self) -> Dict[str, Any]:
        """当前营业日（06:00 切）各档口待出餐队列，口径与 KDS pendingCount / load level 一致；排除楼面。"""
        now = datetime.now(CHINA_TZ)
        # 票 25：窗口 = 当前营业日 [06:00, 次日 06:00)，右端开区间；
        # 旧的自然日 [今日 00:00, 现在] 会在 00:00–06:00 丢掉前一夜 06:00 之后入单的待出餐项。
        business_start, business_end = business_day_window(now)
        start_iso = business_start.isoformat()
        end_iso = business_end.isoformat()
        overdue_cutoff = (now - timedelta(minutes=self.KDS_OVERDUE_MINUTES)).isoformat()

        tdb = self._connection.table("orders")
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
                     AND order_time < ?
                     AND station != ?
                   GROUP BY station
                   ORDER BY pending DESC, station ASC""",
                (overdue_cutoff, start_iso, end_iso, self.KDS_EXCLUDED_STATION),
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
        """当前营业日（06:00 切）菜品分类数、餐桌占用、紧急订单数。"""
        now = datetime.now(CHINA_TZ)
        # 票 25：分类数与菜品名兜底走当前营业日 [06:00, 次日 06:00)；
        # 紧急订单数右端仍是最新 20 分钟（now - 20min），只是左端改营业日起点。
        business_start, business_end = business_day_window(now)
        start_iso = business_start.isoformat()
        end_iso = business_end.isoformat()
        urgent_cutoff = (now - timedelta(minutes=20)).isoformat()
        tdb = self._connection.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """SELECT COUNT(DISTINCT CASE
                       WHEN category IS NOT NULL AND TRIM(category) != '' THEN category
                       ELSE NULL END) FROM orders
                   WHERE order_time >= ? AND order_time < ?""",
                (start_iso, end_iso),
            )
            category_count = (await cursor.fetchone())[0] or 0
            if category_count == 0:
                await cursor.execute(
                    """SELECT COUNT(DISTINCT dish_name) FROM orders
                       WHERE order_time >= ? AND order_time < ?""",
                    (start_iso, end_iso),
                )
                category_count = min((await cursor.fetchone())[0] or 0, 50)
            await cursor.execute(
                """SELECT COUNT(*) FROM orders
                   WHERE order_time >= ? AND order_time < ?""",
                (start_iso, urgent_cutoff),
            )
            urgent_count = (await cursor.fetchone())[0] or 0

        ttables = self._connection.table("tables")
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
        # DATA-03：日期是**营业日**（06:00 切），窗口 = [start 06:00, end+1 06:00)
        start_dt, _ = _business_day_range(start_date)
        _, end_dt = _business_day_range(end_date)
        snapshot = await self.get_table_snapshot_stats()
        tdb = self._connection.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""SELECT table_number,
                           COUNT(*) as order_lines,
                           SUM(quantity) as total_qty,
                           SUM({ORDER_LINE_REVENUE_SQL}) as revenue,
                           COUNT(DISTINCT dish_name) as dish_variety
                    FROM orders
                    WHERE order_time >= ? AND order_time < ?
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
        # DATA-03：日期是**营业日**（06:00 切），窗口 = [start 06:00, end+1 06:00)
        start_dt, _ = _business_day_range(start_date)
        _, end_dt = _business_day_range(end_date)
        params: List[Any] = [start_dt.isoformat(), end_dt.isoformat()]
        station_clause = ""
        if station and station != "all":
            station_clause = " AND station = ?"
            params.append(station)
        elif station == "all":
            # 全店含楼面，与 compute_sales_report 的 `all` 语义保持一致（审查 R3）。
            station_clause = ""
        else:
            station_clause = " AND station != 'loumian'"
        if granularity == "hour":
            bucket_expr = "CAST(strftime('%H', order_time) AS INTEGER)"
        elif granularity == "month":
            bucket_expr = "strftime('%Y-%m', order_time)"
        elif granularity == "week":
            bucket_expr = "strftime('%Y-W%W', order_time)"
        else:
            bucket_expr = "date(order_time)"
        tdb = self._connection.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""SELECT {bucket_expr} as bucket,
                           COUNT(*) as order_lines,
                           COUNT(DISTINCT CASE
                               WHEN COALESCE(quantity, 0) > 0
                                AND LOWER(COALESCE(status, '')) NOT LIKE '%退%'
                               THEN NULLIF(regexp_replace(COALESCE(business_flow_id, ''), '_.*$', ''), '')
                           END) as bills,
                           SUM(quantity) as total_quantity,
                           SUM({ORDER_LINE_REVENUE_SQL}) as revenue
                    FROM orders
                    WHERE order_time >= ? AND order_time < ?{station_clause}
                    GROUP BY bucket ORDER BY bucket""",
                params,
            )
            rows = await cursor.fetchall()
        return [
            {
                "period": row[0],
                "order_lines": row[1] or 0,
                "bills": row[2] or 0,
                "total_quantity": row[3] or 0,
                "revenue": round(row[4] or 0, 2),
            }
            for row in rows
        ]

    async def aggregate_refund_stats(
        self,
        start_date: str,
        end_date: str,
        station: Optional[str] = None,
    ) -> Dict[str, Any]:
        # DATA-03：日期是**营业日**（06:00 切），窗口 = [start 06:00, end+1 06:00)
        start_dt, _ = _business_day_range(start_date)
        _, end_dt = _business_day_range(end_date)
        params: List[Any] = [start_dt.isoformat(), end_dt.isoformat()]
        station_clause = ""
        loumian_clause = ""
        if station and station != "all":
            station_clause = " AND station = ?"
            params.append(station)
        elif station == "all":
            # 全店含楼面：不再无条件排除 loumian（审查 R6）。
            station_clause = ""
        else:
            station_clause = ""
            loumian_clause = " AND COALESCE(station, '') <> 'loumian'"
        tdb = self._connection.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""SELECT COUNT(*) FROM orders
                    WHERE order_time >= ? AND order_time < ?{station_clause}
                      AND (quantity < 0 OR LOWER(COALESCE(status,'')) LIKE '%退%')
                      {loumian_clause}""",
                params,
            )
            refund_lines = (await cursor.fetchone())[0] or 0
            await cursor.execute(
                f"""SELECT dish_name, station, SUM(quantity) as qty, COUNT(*) as cnt
                    FROM orders
                    WHERE order_time >= ? AND order_time < ?{station_clause}
                      AND (quantity < 0 OR LOWER(COALESCE(status,'')) LIKE '%退%')
                      {loumian_clause}
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
