#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的固定报表菜品（report_dishes 表）职责。
"""

import logging
from datetime import datetime
from typing import List, Dict

from db_core.utils import CHINA_TZ

logger = logging.getLogger(__name__)


class _ReportDishesRepoMixin:
    """固定报表菜品的增删改与排序。"""

    async def report_dishes_all(self) -> List[Dict]:
        try:
            tdb = self.table("report_dishes")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT id, dish_name, display_order, notes FROM report_dishes ORDER BY display_order ASC, id ASC"
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取固定报表菜品失败: {e}")
            return []

    async def report_dishes_add(self, dish_name: str, notes: str = "") -> int:
        try:
            tdb = self.table("report_dishes")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute("SELECT MAX(display_order) FROM report_dishes")
                row = await cursor.fetchone()
                max_order = (row[0] or 0) + 1
            now = datetime.now(CHINA_TZ).isoformat()
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT OR IGNORE INTO report_dishes (dish_name, display_order, notes, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (dish_name, max_order, notes, now)
                )
                row_id = cursor.lastrowid
            await tdb.commit()
            return row_id or 0
        except Exception as e:
            logger.error(f"❌ 添加固定报表菜品失败: {e}")
            return 0

    async def report_dishes_remove(self, id: int) -> bool:
        try:
            tdb = self.table("report_dishes")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute("DELETE FROM report_dishes WHERE id = ?", (id,))
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 删除固定报表菜品失败: {e}")
            return False

    async def report_dishes_reorder(self, ids: List[int]) -> bool:
        try:
            tdb = self.table("report_dishes")
            async with tdb.conn.cursor() as cursor:
                for i, rid in enumerate(ids):
                    await cursor.execute(
                        "UPDATE report_dishes SET display_order = ? WHERE id = ?", (i, rid)
                    )
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 更新固定报表菜品顺序失败: {e}")
            return False
