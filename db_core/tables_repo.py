#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的餐桌仓储职责：保存餐桌快照、餐桌占用统计。
"""

import logging
from typing import List, Dict, Any

from db_core.utils import CHINA_TZ, timing_decorator
from datetime import datetime

logger = logging.getLogger(__name__)


class _TablesRepoMixin:
    """餐桌数据保存与占用统计。"""

    @timing_decorator
    async def save_table_data(self, tables_data: List[Dict]) -> bool:
        try:
            if not tables_data:
                return True
            tdb = self.table("tables")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute("DELETE FROM tables")
                for table in tables_data:
                    await cursor.execute(
                        """INSERT INTO tables (table_number, amount, people, duration, status, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            table.get('table_number', ''),
                            table.get('amount', 0.0),
                            table.get('people', 0),
                            table.get('duration', 0),
                            'occupied' if table.get('amount', 0) > 0 else 'empty',
                            datetime.now(CHINA_TZ).isoformat()
                        )
                    )
            await tdb.commit()
            logger.info(f"✅ 成功保存 {len(tables_data)} 条餐桌数据")
            return True
        except Exception as e:
            logger.error(f"❌ 保存餐桌数据失败: {e}")
            return False

    async def get_table_snapshot_stats(self) -> Dict[str, Any]:
        tdb = self.table("tables")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) FROM tables")
            total = (await cursor.fetchone())[0] or 0
            await cursor.execute(
                """SELECT COUNT(*), COALESCE(SUM(amount),0), COALESCE(SUM(people),0),
                          COALESCE(AVG(duration),0)
                   FROM tables WHERE status = 'occupied' OR amount > 0 OR people > 0"""
            )
            row = await cursor.fetchone()
        occupied = row[0] or 0
        return {
            "total_tables": total,
            "occupied_tables": occupied,
            "occupancy_percent": round(occupied / total * 100, 1) if total else 0.0,
            "total_amount": round(row[1] or 0, 2),
            "total_people": int(row[2] or 0),
            "avg_duration_minutes": round((row[3] or 0) / 60, 1) if row[3] else 0,
        }

    async def get_table_live_list(self) -> Dict[str, Any]:
        """当前占用桌台列表，按用餐时长降序。"""
        tdb = self.table("tables")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """SELECT table_number, people, amount, duration, status
                   FROM tables
                   WHERE status = 'occupied' OR amount > 0 OR people > 0
                   ORDER BY duration DESC, table_number ASC"""
            )
            rows = [dict(r) for r in await cursor.fetchall()]

        tables = []
        for row in rows:
            duration_sec = int(row.get("duration") or 0)
            tables.append(
                {
                    "table_number": row.get("table_number") or "",
                    "people": int(row.get("people") or 0),
                    "amount": round(float(row.get("amount") or 0), 2),
                    "duration_minutes": round(duration_sec / 60.0, 1) if duration_sec else 0.0,
                    "status": row.get("status") or "occupied",
                }
            )

        return {
            "tables": tables,
            "total_occupied": len(tables),
        }
