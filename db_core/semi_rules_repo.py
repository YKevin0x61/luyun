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
            tdb = self._connection.table("semi_finished_rules")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute("SELECT * FROM semi_finished_rules ORDER BY dish_name")
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取半成品规则失败: {e}")
            return []

    async def semi_rules_get(self, rule_id: int) -> Optional[Dict]:
        try:
            tdb = self._connection.table("semi_finished_rules")
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
            tdb = self._connection.table("semi_finished_rules")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO semi_finished_rules
                       (dish_name, semi_name, position, factor, unit, category, notes,
                        prep_item_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (rule['dish_name'], rule['semi_name'], rule.get('position', ''),
                     float(rule.get('factor', 1)), rule.get('unit', ''),
                     rule.get('category', ''), rule.get('notes', ''),
                     rule.get('prep_item_id'), now, now)
                )
                row_id = cursor.lastrowid
            await tdb.commit()
            return row_id
        except Exception as e:
            logger.error(f"❌ 保存半成品规则失败: {e}")
            return 0

    async def prep_items_simple(self) -> List[Dict]:
        """备货品主数据（精简）：换算规则页选品用，顺便带出单位/岗位/分类。

        岗位与单位**属于备货品**，不是规则的属性：页面选完备货品就直接带出来，
        不再让人手填一份可能和主数据打架的值。
        """
        try:
            tdb = self._connection.table("prep_items")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT id, item_name, unit, position, station, kind, category
                    FROM prep_items
                    WHERE active = 1
                    ORDER BY position, item_name
                    """
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取备货品列表失败: {e}")
            return []

    async def prep_items_all(self) -> List[Dict]:
        """备货品主数据（管理面）：**含停用行**，并带「被多少条规则引用」。

        与 `prep_items_simple`（只给 active=1、给选品下拉用）的区别就是这两点：
        管理页要能看见停用的品并重新启用，也要能一眼看出哪个品没有任何规则在用。
        """
        try:
            tdb = self._connection.table("prep_items")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT p.id, p.item_name, p.unit, p.position, p.station,
                           p.kind, p.category, p.active, p.notes,
                           (SELECT COUNT(*) FROM semi_finished_rules r
                             WHERE r.prep_item_id = p.id) AS rule_count
                    FROM prep_items p
                    ORDER BY p.position, p.item_name
                    """
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取备货品主数据失败: {e}")
            return []

    async def prep_item_get(self, item_id: int) -> Optional[Dict]:
        """按 id 取一行备货品（编辑前读当前值）。"""
        try:
            tdb = self._connection.table("prep_items")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT p.id, p.item_name, p.unit, p.position, p.station, p.kind,"
                    " p.category, p.active, p.notes,"
                    " (SELECT COUNT(*) FROM semi_finished_rules r"
                    "   WHERE r.prep_item_id = p.id) AS rule_count"
                    " FROM prep_items p WHERE p.id = ?",
                    (item_id,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ 查备货品失败: {e}")
            return None

    async def prep_item_delete(self, item_id: int) -> bool:
        """删一个备货品。

        `semi_finished_rules.prep_item_id` 是 `ON DELETE SET NULL`（0035），引用它的规则
        会自动回到「未对准备货品」状态 —— 不连带删规则（规则是另一件事，页面会标出来）。
        `prep_plan_entries` 没有外键，已算过的备货记录原样保留（名字还在，岗位查不到 →
        那条旧记录下次展开时没有依据，这是删除的已知代价）。
        """
        try:
            tdb = self._connection.table("prep_items")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute("DELETE FROM prep_items WHERE id = ?", (item_id,))
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 删除备货品失败: {e}")
            return False

    async def prep_item_name_taken(self, item_name: str, exclude_id: int = 0) -> bool:
        """改名前的重名检查（同名的另一行存在就算占用）。"""
        try:
            tdb = self._connection.table("prep_items")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT id FROM prep_items WHERE item_name = ? AND id <> ? LIMIT 1",
                    (item_name, exclude_id),
                )
                row = await cursor.fetchone()
            return row is not None
        except Exception as e:
            logger.error(f"❌ 查备货品重名失败: {e}")
            return False

    async def prep_item_update(self, item_id: int, fields: Dict) -> bool:
        """按白名单更新备货品（名称 / 单位 / 岗位 / 档口 / 分类 / 归属 / 启用 / 备注）。"""
        allowed = {
            "item_name", "unit", "position", "station",
            "category", "kind", "active", "notes",
        }
        patch = {key: value for key, value in fields.items() if key in allowed}
        if not patch:
            return False
        try:
            now = datetime.now(CHINA_TZ).isoformat()
            patch["updated_at"] = now
            columns = ", ".join(f"{name} = ?" for name in patch)
            values = list(patch.values()) + [item_id]
            tdb = self._connection.table("prep_items")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"UPDATE prep_items SET {columns} WHERE id = ?", tuple(values)
                )
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 更新备货品失败: {e}")
            return False

    async def prep_item_find(self, item_name: str) -> Optional[Dict]:
        """按名字找备货品（新建前的重名检查）。"""
        try:
            tdb = self._connection.table("prep_items")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT id, item_name, unit, position FROM prep_items"
                    " WHERE item_name = ? ORDER BY id LIMIT 1",
                    (item_name,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ 查备货品失败: {e}")
            return None

    async def prep_item_create(self, item: Dict) -> Optional[Dict]:
        """新建一个备货品（从换算规则页的「新建」入口进来）。"""
        try:
            now = datetime.now(CHINA_TZ).isoformat()
            tdb = self._connection.table("prep_items")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO prep_items
                        (item_name, station, position, category, unit,
                         shelf_life_hours, lead_time_hours, min_batch_qty,
                         safety_stock_ratio, active, notes, created_at, updated_at, kind)
                    VALUES (?, ?, ?, ?, ?, 24, 0, 0, 0.15, 1, ?, ?, ?, ?)
                    """,
                    (
                        item["item_name"], item.get("station", ""), item.get("position", ""),
                        item.get("category", ""), item.get("unit", ""),
                        "manual_from_rules_page", now, now, item.get("kind", "made"),
                    ),
                )
                row_id = cursor.lastrowid
            await tdb.commit()
            return {"id": row_id, **item}
        except Exception as e:
            logger.error(f"❌ 新建备货品失败: {e}")
            return None

    async def semi_rules_board_rows(self):
        """换算规则板的原料：全部规则 + 备货品主数据（JOIN prep_item_id）。

        返回 `(rows, schema_ready)`：`schema_ready=False` 表示 `prep_item_id` 那一列还
        不存在（迁移 0035 没应用），此时退回只读规则表本身、备货品字段全空。
        **不能用「查询失败 = 没有规则」糊过去** —— 那会让页面显示「已配 0 / 待配 104」，
        比迁移前还难用。调用方拿 schema_ready 去提示「先去跑数据库迁移」。
        """
        tdb = self._connection.table("semi_finished_rules")
        try:
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT r.id, r.dish_name, r.semi_name, r.position, r.factor,
                           r.unit, r.category, r.notes, r.prep_item_id,
                           r.created_at, r.updated_at,
                           p.item_name AS prep_item_name,
                           p.unit AS prep_item_unit,
                           p.kind AS prep_item_kind,
                           p.position AS prep_item_position
                    FROM semi_finished_rules r
                    LEFT JOIN prep_items p ON p.id = r.prep_item_id
                    ORDER BY r.dish_name, r.id
                    """
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows], True
        except Exception as e:  # noqa: BLE001 - 列不存在时下面走回退查询
            logger.warning(f"换算规则板 JOIN 失败（0035 可能还没应用），退回不 JOIN：{e}")

        try:
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT r.id, r.dish_name, r.semi_name, r.position, r.factor,
                           r.unit, r.category, r.notes,
                           NULL AS prep_item_id,
                           r.created_at, r.updated_at,
                           NULL AS prep_item_name,
                           NULL AS prep_item_unit,
                           NULL AS prep_item_kind,
                           NULL AS prep_item_position
                    FROM semi_finished_rules r
                    ORDER BY r.dish_name, r.id
                    """
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows], False
        except Exception as e:
            logger.error(f"❌ 获取换算规则板失败: {e}")
            return [], False

    async def orders_dish_qty_since(self, since) -> Dict[str, float]:
        """近一段时间每个菜名的销量（按份数），给规则板显示「卖了多少」。

        `since` 传 datetime（`order_time` 是 TEXT，SQL 里 cast 成 timestamptz，
        asyncpg 要求参数是 datetime 而不是字符串）。
        """
        try:
            tdb = self._connection.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT dish_name, SUM(quantity) AS qty
                    FROM orders
                    WHERE order_time::timestamptz >= ?
                    GROUP BY dish_name
                    """,
                    (since,)
                )
                rows = await cursor.fetchall()
            return {row['dish_name']: float(row['qty'] or 0) for row in rows}
        except Exception as e:
            logger.error(f"❌ 统计菜品销量失败: {e}")
            return {}

    async def semi_rules_delete(self, rule_id: int) -> bool:
        try:
            tdb = self._connection.table("semi_finished_rules")
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
                tdb = self._connection.table("semi_finished_rules")
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
