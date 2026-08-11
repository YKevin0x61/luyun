#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的半成品换算规则（semi_finished_rules 表）职责。
"""

import logging
from datetime import datetime
from typing import List, Dict, Optional

from db_core.utils import CHINA_TZ

logger = logging.getLogger(__name__)


class _SemiRulesRepoMixin:
    """半成品换算规则的增删改查与批量导入。"""

    async def semi_rules_all(self) -> List[Dict]:
        try:
            tdb = self.table("semi_finished_rules")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute("SELECT * FROM semi_finished_rules ORDER BY dish_name")
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取半成品规则失败: {e}")
            return []

    async def semi_rules_get(self, rule_id: int) -> Optional[Dict]:
        try:
            tdb = self.table("semi_finished_rules")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT * FROM semi_finished_rules WHERE id = ?", (rule_id,)
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ 查询半成品规则失败: {e}")
            return None

    async def semi_rules_upsert(self, rule: Dict) -> int:
        try:
            now = datetime.now(CHINA_TZ).isoformat()
            tdb = self.table("semi_finished_rules")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO semi_finished_rules
                       (dish_name, semi_name, position, factor, unit, category, notes, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (rule['dish_name'], rule['semi_name'], rule.get('position', ''),
                     float(rule.get('factor', 1)), rule.get('unit', ''),
                     rule.get('category', ''), rule.get('notes', ''), now, now)
                )
                row_id = cursor.lastrowid
            await tdb.commit()
            return row_id
        except Exception as e:
            logger.error(f"❌ 保存半成品规则失败: {e}")
            return 0

    async def semi_rules_delete(self, rule_id: int) -> bool:
        try:
            tdb = self.table("semi_finished_rules")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM semi_finished_rules WHERE id = ?", (rule_id,)
                )
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 删除半成品规则失败: {e}")
            return False

    async def semi_rules_import_batch(self, rules: List[Dict]) -> int:
        count = 0
        for r in rules:
            try:
                tdb = self.table("semi_finished_rules")
                async with tdb.conn.cursor() as cursor:
                    await cursor.execute(
                        "SELECT 1 FROM semi_finished_rules WHERE dish_name = ? AND semi_name = ?",
                        (r['dish_name'], r.get('semi_name', ''))
                    )
                    existing = await cursor.fetchone()
                if not existing:
                    ok = await self.semi_rules_upsert(r)
                    if ok:
                        count += 1
            except Exception:
                continue
        return count
