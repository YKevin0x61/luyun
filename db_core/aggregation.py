#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的订单/档口聚合查询职责：
档口统计、分页查询、订单统计聚合、档口订单数/进单速率、菜品汇总与热门菜品。
（跨表销售报表、仪表盘与经营分析见 db_core/reports.py）
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

from config import ORDER_LINE_REVENUE_SQL

from db_core.utils import CHINA_TZ, timing_decorator, ensure_beijing_datetime, to_sql_datetime
from services.urgency_policy import urgent_cutoff

logger = logging.getLogger(__name__)


class _AggregationMixin:
    """档口统计与订单维度的聚合查询。"""

    @staticmethod
    def _reject_order_time_dict(match_condition: Dict) -> None:
        if "order_time" in match_condition:
            raise ValueError(
                "order_time in match_condition is not supported; "
                "use order_time_start= / order_time_end="
            )

    @timing_decorator
    async def get_station_stats(
        self,
        station_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict:
        try:
            from models import StationStats
            from config import settings
            station_name = settings.KITCHEN_STATIONS.get(station_id, {}).get('name', station_id)
            now = datetime.now(CHINA_TZ)
            if start_time is None:
                start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if end_time is None:
                end_time = now.replace(hour=23, minute=59, second=59, microsecond=999000)
            start_iso, end_iso = start_time.isoformat(), end_time.isoformat()
            urgent_cutoff_iso = urgent_cutoff(now).isoformat()
            tdb = self.table("orders")

            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT COUNT(*) as cnt FROM orders
                       WHERE station = ? AND order_time >= ? AND order_time <= ?""",
                    (station_id, start_iso, end_iso),
                )
                total_orders = (await cursor.fetchone())[0]
                await cursor.execute(
                    """SELECT SUM(quantity) as s FROM orders
                       WHERE station = ? AND order_time >= ? AND order_time <= ?""",
                    (station_id, start_iso, end_iso),
                )
                row = await cursor.fetchone()
                total_quantity = row[0] or 0 if row else 0
                await cursor.execute(
                    """SELECT COUNT(*) FROM orders
                       WHERE station = ? AND order_time >= ? AND order_time <= ?
                         AND order_time < ?""",
                    (station_id, start_iso, end_iso, urgent_cutoff_iso),
                )
                urgent_count = (await cursor.fetchone())[0]
                await cursor.execute(
                    """SELECT order_time FROM orders
                       WHERE station = ? AND order_time >= ? AND order_time <= ?""",
                    (station_id, start_iso, end_iso),
                )
                rows = await cursor.fetchall()
            wait_times = []
            for r in rows:
                try:
                    ot = datetime.fromisoformat(r[0])
                    wait_times.append(int((now - ot).total_seconds() * 1000))
                except Exception:
                    pass
            avg_wait_time = int(sum(wait_times) / len(wait_times)) if wait_times else 0
            stats = StationStats(
                station_id=station_id, station_name=station_name,
                total_orders=total_orders, total_quantity=total_quantity,
                urgent_count=urgent_count, avg_wait_time=avg_wait_time
            )
            return stats.model_dump()
        except Exception as e:
            logger.error(f"❌ 获取档口统计失败 {station_id}: {e}")
            raise RuntimeError(f"获取档口统计失败: {e}") from e

    async def aggregate_orders_paginated(
        self,
        match_condition: Dict,
        skip: int,
        limit: int,
        *,
        order_time_start=None,
        order_time_end=None,
    ) -> tuple:
        try:
            self._reject_order_time_dict(match_condition)
            now = datetime.now(CHINA_TZ)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            conditions, params = [], []
            for key, val in match_condition.items():
                if key == 'station':
                    conditions.append("station = ?"); params.append(val)
                elif key == 'table_number':
                    conditions.append("table_number = ?"); params.append(val)
            start_sql = to_sql_datetime(order_time_start)
            end_sql = to_sql_datetime(order_time_end)
            if start_sql is not None:
                conditions.append("order_time >= ?")
                params.append(start_sql)
            if end_sql is not None:
                conditions.append("order_time <= ?")
                params.append(end_sql)
            if not conditions:
                conditions.append("order_time >= ?")
                params.append(today_start)
            where = " AND ".join(conditions)
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(f"SELECT COUNT(*) FROM orders WHERE {where}", params)
                total_count = (await cursor.fetchone())[0]
                await cursor.execute(
                    f"""SELECT id, business_flow_id, table_number, dish_name, quantity,
                               order_time, station, priority, price, category, updated_at
                        FROM orders WHERE {where} ORDER BY order_time DESC LIMIT ? OFFSET ?""",
                    params + [limit, skip]
                )
                rows = await cursor.fetchall()
            orders = [self._row_to_dict(row) for row in rows]
            return orders, total_count
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ 分页聚合查询失败: {e}")
            raise RuntimeError(f"分页聚合查询失败: {e}") from e

    async def aggregate_orders_stats(self, station: Optional[str] = None) -> Dict:
        try:
            now = datetime.now(CHINA_TZ)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            params: List[Any] = [today_start]
            station_clause = ""
            if station and station != 'all':
                station_clause = " AND station = ?"; params.append(station)
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"""SELECT COUNT(*) as total_orders, SUM(quantity) as total_quantity,
                               SUM({ORDER_LINE_REVENUE_SQL}) as total_revenue
                        FROM orders WHERE order_time >= ?{station_clause}""", params
                )
                row = await cursor.fetchone()
            return {
                "total_orders": row[0] or 0,
                "total_quantity": row[1] or 0,
                "total_revenue": row[2] or 0.0
            }
        except Exception as e:
            logger.error(f"❌ 统计聚合失败: {e}")
            raise RuntimeError(f"统计聚合失败: {e}") from e

    async def aggregate_station_counts(self, start_time: datetime) -> List[Dict[str, Any]]:
        """按档口统计指定起始时间之后的订单数。"""
        try:
            start_dt = ensure_beijing_datetime(start_time)
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT station, COUNT(*) as cnt
                       FROM orders
                       WHERE order_time >= ?
                       GROUP BY station
                       ORDER BY cnt DESC""",
                    (start_dt.isoformat(),),
                )
                rows = await cursor.fetchall()
            return [{"station_id": row["station"] or "", "count": row["cnt"]} for row in rows]
        except Exception as e:
            logger.error(f"❌ 档口订单数聚合失败: {e}")
            raise RuntimeError(f"档口订单数聚合失败: {e}") from e

    async def aggregate_station_speed(self, target: datetime) -> Dict[str, Any]:
        """按 5 分钟粒度聚合档口进单速率，并附带昨日/上周/上月对比。"""
        try:
            target_dt = ensure_beijing_datetime(target).replace(hour=0, minute=0, second=0, microsecond=0)
            target_str = target_dt.strftime("%Y-%m-%d")
            compare_dates = {
                "yesterday": (target_dt - timedelta(days=1)).strftime("%Y-%m-%d"),
                "last_week": (target_dt - timedelta(days=7)).strftime("%Y-%m-%d"),
                "last_month": (target_dt - timedelta(days=30)).strftime("%Y-%m-%d"),
            }
            all_dates = [target_str] + list(compare_dates.values())

            rows_by_date: Dict[str, List[Any]] = {}
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                for date_str in all_dates:
                    day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=CHINA_TZ)
                    day_end = day_start + timedelta(days=1)
                    await cursor.execute(
                        """SELECT station,
                                  CAST(SUBSTR(order_time, 12, 2) AS INTEGER) * 60
                                + CAST(SUBSTR(order_time, 15, 2) AS INTEGER) AS minute_of_day,
                                  COUNT(*) as cnt
                           FROM orders
                           WHERE order_time >= ?
                             AND order_time < ?
                             AND station != ''
                             AND station != 'loumian'
                           GROUP BY station, minute_of_day
                           ORDER BY station, minute_of_day""",
                        (day_start.isoformat(), day_end.isoformat()),
                    )
                    rows_by_date[date_str] = await cursor.fetchall()

            all_stations = sorted({
                row["station"]
                for rows in rows_by_date.values()
                for row in rows
                if row["station"]
            })

            slot_start = 7 * 60
            slot_end = 21 * 60
            slot_step = 5
            slots = list(range(slot_start, slot_end + 1, slot_step))
            slot_count = len(slots)
            slot_labels = [f"{minutes // 60:02d}:{minutes % 60:02d}" for minutes in slots]

            def build_series(date_str: str) -> Dict[str, List[int]]:
                data_map: Dict[str, List[int]] = {}
                for row in rows_by_date.get(date_str, []):
                    station = row["station"]
                    minute_of_day = row["minute_of_day"]
                    count = row["cnt"]
                    if not station:
                        continue
                    idx = (minute_of_day - slot_start) // slot_step
                    if 0 <= idx < slot_count:
                        data_map.setdefault(station, [0] * slot_count)
                        data_map[station][idx] += count
                return data_map

            today_series = build_series(target_str)
            yesterday_series = build_series(compare_dates["yesterday"])
            last_week_series = build_series(compare_dates["last_week"])
            last_month_series = build_series(compare_dates["last_month"])

            series = []
            for station in all_stations:
                today_data = today_series.get(station, [0] * slot_count)
                yesterday_data = yesterday_series.get(station, [0] * slot_count)
                last_week_data = last_week_series.get(station, [0] * slot_count)
                last_month_data = last_month_series.get(station, [0] * slot_count)

                series.append({
                    "name": station,
                    "station_id": station,
                    "type": "today",
                    "data": today_data,
                })
                if any(yesterday_data):
                    series.append({
                        "name": f"{station}_yesterday",
                        "station_id": station,
                        "type": "yesterday",
                        "data": yesterday_data,
                    })
                if any(last_week_data):
                    series.append({
                        "name": f"{station}_last_week",
                        "station_id": station,
                        "type": "last_week",
                        "data": last_week_data,
                    })
                if any(last_month_data):
                    series.append({
                        "name": f"{station}_last_month",
                        "station_id": station,
                        "type": "last_month",
                        "data": last_month_data,
                    })

            return {
                "date": target_str,
                "compare_dates": compare_dates,
                "slots": slot_labels,
                "stations": all_stations,
                "series": series,
            }
        except Exception as e:
            logger.error(f"❌ 档口进单速率聚合失败: {e}")
            return {
                "date": ensure_beijing_datetime(target).strftime("%Y-%m-%d"),
                "compare_dates": {},
                "slots": [],
                "stations": [],
                "series": [],
            }

    async def aggregate_dishes_summary(self, station: Optional[str] = None) -> List[Dict]:
        try:
            now = datetime.now(CHINA_TZ)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            params: List[Any] = [today_start]
            station_clause = ""
            if station and station != 'all':
                station_clause = " AND station = ?"; params.append(station)
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"""SELECT station, COUNT(*) as dish_count, SUM(quantity) as total_quantity,
                               COUNT(DISTINCT dish_name) as unique_dishes,
                               COUNT(DISTINCT table_number) as unique_tables
                        FROM orders WHERE order_time >= ?{station_clause}
                        GROUP BY station""", params
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 菜品汇总聚合失败: {e}")
            return []

    async def aggregate_hot_dishes(self, station: Optional[str] = None, limit_n: int = 10) -> List[Dict]:
        try:
            now = datetime.now(CHINA_TZ)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            params: List[Any] = [today_start]
            station_clause = ""
            if station and station != 'all':
                station_clause = " AND station = ?"; params.append(station)
            else:
                station_clause = " AND station != 'loumian'"
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"""SELECT dish_name, station, SUM(quantity) as total_quantity,
                               COUNT(*) as order_count, COUNT(DISTINCT table_number) as table_count,
                               AVG(price) as avg_price, MIN(order_time) as earliest_order
                        FROM orders WHERE order_time >= ?{station_clause}
                        GROUP BY dish_name, station
                        ORDER BY total_quantity DESC, order_count DESC LIMIT ?""",
                    params + [limit_n]
                )
                rows = await cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                earliest = datetime.fromisoformat(d['earliest_order']) if d['earliest_order'] else now
                wait_minutes = int((now - earliest).total_seconds() / 60)
                result.append({
                    'dish_name': d['dish_name'], 'station': d['station'],
                    'total_quantity': d['total_quantity'], 'order_count': d['order_count'],
                    'table_count': d['table_count'],
                    'avg_price': round(d['avg_price'] or 0, 2),
                    'wait_time_minutes': wait_minutes
                })
            return result
        except Exception as e:
            logger.error(f"❌ 热门菜品聚合失败: {e}")
            return []

