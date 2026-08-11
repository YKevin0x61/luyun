#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的菜品档口映射（dish_stations 表）查询与底层写。
领域写入仍只经 DishCatalog；本 mixin 供 DishStationsPort adapter 委托。
"""

import logging
from datetime import datetime
from typing import List, Dict, Optional, Any, Tuple

from db_core.utils import CHINA_TZ

logger = logging.getLogger(__name__)


class _DishStationsRepoMixin:
    """dish_stations 表查询 + Catalog 用的底层 insert/update/delete。"""

    def _dish_stations_where(
        self,
        query: Dict,
        dish_name_contains: Optional[str] = None,
    ) -> Tuple[str, list]:
        """Build WHERE clause. Substring match via dish_name_contains; exact via query['dish_name'] str."""
        conditions, params = [], []
        contains = (dish_name_contains or "").strip()
        if contains:
            conditions.append("dish_name LIKE ?")
            params.append(f"%{contains}%")
        elif "dish_name" in query:
            name = query["dish_name"]
            if isinstance(name, dict):
                raise ValueError(
                    "dish_name filter must be an exact string; use dish_name_contains for substring match"
                )
            conditions.append("dish_name = ?")
            params.append(name)
        if "station_id" in query:
            conditions.append("station_id = ?")
            params.append(query["station_id"])
        where = " AND ".join(conditions) if conditions else "1=1"
        return where, params

    async def dish_stations_count(
        self,
        query: Dict,
        *,
        dish_name_contains: Optional[str] = None,
    ) -> int:
        try:
            where, params = self._dish_stations_where(query, dish_name_contains=dish_name_contains)
            tdb = self.table("dish_stations")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(f"SELECT COUNT(*) FROM dish_stations WHERE {where}", params)
                return (await cursor.fetchone())[0]
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ dish_stations 计数失败: {e}")
            return 0

    async def dish_stations_stats_by_station(self) -> List[Dict[str, Any]]:
        try:
            tdb = self.table("dish_stations")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT station_id, COUNT(*) as count FROM dish_stations GROUP BY station_id"
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ dish_stations 档口统计失败: {e}")
            return []

    async def dish_stations_find(
        self,
        query: Dict,
        sort_field: str = "dish_name",
        sort_dir: int = 1,
        skip: int = 0,
        limit: int = -1,
        *,
        dish_name_contains: Optional[str] = None,
    ) -> List[Dict]:
        try:
            where, params = self._dish_stations_where(query, dish_name_contains=dish_name_contains)
            order = f"ORDER BY {sort_field} {'ASC' if sort_dir > 0 else 'DESC'}"
            lim = f"LIMIT {limit} OFFSET {skip}" if limit > 0 else ""
            tdb = self.table("dish_stations")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"SELECT * FROM dish_stations WHERE {where} {order} {lim}", params
                )
                rows = await cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ dish_stations 查询失败: {e}")
            return []

    async def dish_stations_find_one(self, query: Dict) -> Optional[Dict]:
        try:
            conditions, params = [], []
            if 'dish_name' in query:
                conditions.append("dish_name = ?"); params.append(query['dish_name'])
            if 'station_id' in query:
                conditions.append("station_id = ?"); params.append(query['station_id'])
            where = " AND ".join(conditions) if conditions else "1=1"
            tdb = self.table("dish_stations")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"SELECT * FROM dish_stations WHERE {where} LIMIT 1", params
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ dish_stations 单条查询失败: {e}")
            return None

    async def dish_stations_aggregate(self, pipeline: List[Dict]) -> List[Dict]:
        try:
            group_stage = None
            for stage in pipeline:
                if '$group' in stage:
                    group_stage = stage['$group']
                    break
            if not group_stage:
                return []
            group_field = group_stage.get('_id', 'station_id')
            is_count = 'count' in str(group_stage)
            tdb = self.table("dish_stations")
            async with tdb.conn.cursor() as cursor:
                if is_count:
                    await cursor.execute(
                        f"SELECT {group_field}, COUNT(*) as count FROM dish_stations GROUP BY {group_field}"
                    )
                else:
                    await cursor.execute("SELECT * FROM dish_stations")
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ dish_stations 聚合失败: {e}")
            return []

    async def dish_stations_insert(self, document: Dict[str, Any]) -> None:
        now = datetime.now(CHINA_TZ).isoformat()
        tdb = self.table("dish_stations")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """INSERT INTO dish_stations (dish_name, station_id, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    document.get("dish_name"),
                    document.get("station_id"),
                    document.get("notes"),
                    document.get("created_at") or now,
                    document.get("updated_at") or now,
                ),
            )
        await tdb.commit()

    async def dish_stations_update(
        self, dish_name: str, update_fields: Dict[str, Any]
    ) -> int:
        if not update_fields:
            return 0
        set_clause = ", ".join([f"{k} = ?" for k in update_fields.keys()])
        tdb = self.table("dish_stations")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"UPDATE dish_stations SET {set_clause} WHERE dish_name = ?",
                tuple(update_fields.values()) + (dish_name,),
            )
            rowcount = cursor.rowcount
        await tdb.commit()
        return rowcount

    async def dish_stations_delete(self, dish_name: str) -> int:
        tdb = self.table("dish_stations")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM dish_stations WHERE dish_name = ?",
                (dish_name,),
            )
            rowcount = cursor.rowcount
        await tdb.commit()
        return rowcount
