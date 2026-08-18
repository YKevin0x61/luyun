#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的订单仓储职责：查询、保存、批量插入/删除、原始条件搜索。
"""

import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Set, Tuple

from db_core.utils import (
    CHINA_TZ,
    ORDER_DEDUP_BATCH_SIZE,
    timing_decorator,
    ensure_beijing_datetime,
    to_sql_datetime,
)
from services.urgency_policy import level_for_wait_ms

logger = logging.getLogger(__name__)


def _shape_order_placement(order: Optional[Dict]) -> Optional[Dict]:
    """Nest 蒸笼位 on the public order dict; absent/None when not loaded."""
    if not order:
        return order
    steamer_id = order.pop("steamer_id", None)
    port_index = order.pop("port_index", None)
    stack_order = order.pop("stack_order", None)
    loaded_at = order.pop("loaded_at", None)
    if steamer_id in (None, "") or port_index is None:
        order["placement"] = None
        # Kept after 抽笼 so 待上笼退示 only applies to never-loaded cancels.
        if loaded_at not in (None, ""):
            order["loaded_at"] = loaded_at
        return order
    order["placement"] = {
        "steamer_id": str(steamer_id),
        "port_index": int(port_index),
        "stack_order": int(stack_order) if stack_order is not None else 1,
        "loaded_at": loaded_at,
    }
    return order


def _placement_hole(order: Optional[Dict]) -> Optional[Tuple[str, int]]:
    """Hole identity from a shaped order, or None when not on a 蒸笼位."""
    if not order:
        return None
    placement = order.get("placement") or {}
    steamer_id = placement.get("steamer_id")
    port_index = placement.get("port_index")
    if steamer_id in (None, "") or port_index is None:
        return None
    return (str(steamer_id), int(port_index))


class _OrdersRepoMixin:
    """订单查询、写入与去重逻辑。"""

    @timing_decorator
    async def get_orders(self, station: Optional[str] = None,
                         table_number: Optional[str] = None,
                         start_time: Optional[datetime] = None,
                         end_time: Optional[datetime] = None,
                         dish_status: Optional[str] = None,
                         limit: int = 10000) -> List[Dict]:
        try:
            conditions, params = [], []
            if station and station != 'all':
                conditions.append("station = ?"); params.append(station)
            if table_number:
                conditions.append("table_number = ?"); params.append(table_number)
            if start_time:
                conditions.append("order_time >= ?"); params.append(start_time.isoformat())
            if end_time:
                conditions.append("order_time <= ?"); params.append(end_time.isoformat())
            if dish_status:
                conditions.append("dish_status = ?"); params.append(dish_status)
            where = " AND ".join(conditions) if conditions else "1=1"

            sql = f"""
                SELECT id, business_flow_id, table_number, dish_name, quantity,
                       order_time, station, priority, price, category, status,
                       dish_status, ready_time, steamer_id, port_index,
                       stack_order, loaded_at, updated_at
                FROM orders WHERE {where} ORDER BY order_time DESC
            """
            if limit > 0:
                sql += f" LIMIT {limit}"

            async with self.table("orders").conn.cursor() as cursor:
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()
            orders = [_shape_order_placement(self._row_to_dict(row)) for row in rows]
            self.stats['queries_executed'] += 1
            return orders
        except Exception as e:
            logger.error(f"❌ 获取订单数据失败: {e}")
            raise RuntimeError(f"获取订单数据失败: {e}") from e

    @timing_decorator
    async def get_order_by_id(self, order_id: str, dish_name: Optional[str] = None) -> Optional[Dict]:
        try:
            tdb = self.table("orders")
            cursor = await tdb.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            )
            row = await cursor.fetchone()
            if not row:
                cursor = await tdb.execute(
                    "SELECT * FROM orders WHERE business_flow_id = ?", (order_id,)
                )
                row = await cursor.fetchone()
            return _shape_order_placement(self._row_to_dict(row)) if row else None
        except Exception as e:
            logger.error(f"❌ 根据ID获取订单失败: {e}")
            return None

    @timing_decorator
    async def resolve_order_for_cooking(
        self,
        *,
        order_id: str = "",
        business_flow_id: str = "",
        table_number: str = "",
        dish_name: str = "",
    ) -> Optional[Dict]:
        """按 id → business_flow_id → 桌号+菜名 依次解析待出餐订单。"""
        for candidate in (order_id, business_flow_id):
            if candidate:
                order = await self.get_order_by_id(str(candidate))
                if order:
                    return order

        if table_number and dish_name:
            tdb = self.table("orders")
            cursor = await tdb.execute(
                """SELECT * FROM orders
                   WHERE table_number = ? AND dish_name = ? AND dish_status = '待出餐'
                   ORDER BY order_time ASC LIMIT 1""",
                (table_number, dish_name),
            )
            row = await cursor.fetchone()
            if row:
                return _shape_order_placement(self._row_to_dict(row))
        return None

    @timing_decorator
    async def get_merged_dishes(self, station: Optional[str] = None,
                                sort_by: str = 'time',
                                limit_hours: int = 24) -> List[Dict]:
        try:
            now = datetime.now(CHINA_TZ)
            cutoff = (now - timedelta(hours=limit_hours)).isoformat()
            conditions, params = [f"order_time >= ?"], [cutoff]
            if station and station != 'all':
                conditions.append("station = ?"); params.append(station)
            where = " AND ".join(conditions)

            sql = f"""
                SELECT dish_name, station,
                       SUM(quantity) as total_quantity,
                       COUNT(*) as order_count,
                       AVG(price) as avg_price,
                       MIN(order_time) as earliest_order,
                       MAX(order_time) as latest_order
                FROM orders WHERE {where}
                GROUP BY dish_name, station
            """
            async with self.table("orders").conn.cursor() as cursor:
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()

            dishes = []
            for row in rows:
                d = dict(row)
                name = d['dish_name']
                st = d['station'] or ''
                earliest = datetime.fromisoformat(d['earliest_order']) if d['earliest_order'] else now
                latest = datetime.fromisoformat(d['latest_order']) if d['latest_order'] else now
                max_wait_ms = int((now - earliest).total_seconds() * 1000)
                avg_wait_ms = int((max_wait_ms + int((now - latest).total_seconds() * 1000)) / 2)
                priority = level_for_wait_ms(max_wait_ms)
                dishes.append({
                    'name': name, 'station': st,
                    'total_quantity': d['total_quantity'],
                    'order_count': d['order_count'],
                    'avg_price': round(d['avg_price'] or 0, 2),
                    'max_wait_time': int((now - earliest).total_seconds()),
                    'avg_wait_time': int(avg_wait_ms / 1000),
                    'priority': priority,
                })

            if sort_by == 'time':
                dishes.sort(key=lambda x: x['max_wait_time'], reverse=True)
            elif sort_by == 'priority':
                priority_order = {'urgent': 3, 'high': 2, 'normal': 1}
                dishes.sort(key=lambda x: (priority_order.get(x['priority'], 1), x['max_wait_time']), reverse=True)
            elif sort_by == 'quantity':
                dishes.sort(key=lambda x: x['total_quantity'], reverse=True)
            elif sort_by == 'station':
                dishes.sort(key=lambda x: x['station'])
            self.stats['queries_executed'] += 1
            return dishes[:200]
        except Exception as e:
            logger.error(f"❌ 获取合并菜品数据失败: {e}")
            return []

    @timing_decorator
    async def save_orders(self, orders_data: List[Dict]) -> bool:
        if not orders_data:
            return True
        try:
            inserted = 0
            tdb = self.table("orders")
            business_flow_ids = [
                order.get('business_flow_id', '')
                for order in orders_data
            ]
            existing_business_flow_ids = set()
            async with tdb.conn.cursor() as cursor:
                for start_index in range(0, len(business_flow_ids), ORDER_DEDUP_BATCH_SIZE):
                    business_flow_id_batch = business_flow_ids[start_index:start_index + ORDER_DEDUP_BATCH_SIZE]
                    placeholders = ",".join("?" for _ in business_flow_id_batch)
                    await cursor.execute(
                        f"SELECT business_flow_id FROM orders WHERE business_flow_id IN ({placeholders})",
                        business_flow_id_batch
                    )
                    rows = await cursor.fetchall()
                    existing_business_flow_ids.update(row["business_flow_id"] for row in rows)

                for order in orders_data:
                    business_flow_id = order.get('business_flow_id', '')
                    if business_flow_id in existing_business_flow_ids:
                        continue
                    order_time = ensure_beijing_datetime(order.get('order_time', ''))
                    current = datetime.now(CHINA_TZ).isoformat()
                    await cursor.execute(
                        """INSERT INTO orders
                           (business_flow_id, table_number, dish_name, quantity,
                            order_time, price, total_amount, status, category, station,
                            priority, notes, source, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            business_flow_id,
                            order.get('table_number', ''),
                            order.get('dish_name', ''),
                            order.get('quantity', 0),
                            order_time.isoformat(),
                            order.get('price', 0.0),
                            order.get('total_amount', 0.0),
                            order.get('status', '未结'),
                            order.get('category', ''),
                            order.get('station', ''),
                            order.get('priority', 'normal'),
                            order.get('notes'),
                            order.get('source', ''),
                            current, current
                        )
                    )
                    existing_business_flow_ids.add(business_flow_id)
                    inserted += 1
            await tdb.commit()
            logger.info(f"✅ 成功保存 {inserted} 条订单数据")
            return True
        except Exception as e:
            logger.error(f"❌ 保存订单数据失败: {e}")
            return False

    async def batch_insert_orders(self, orders: List[Dict]) -> Dict[str, Any]:
        try:
            if not orders:
                return {"success": True, "inserted_count": 0, "errors": []}
            inserted_count, errors = 0, []
            now = datetime.now(CHINA_TZ).isoformat()
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                for order in orders:
                    try:
                        order_time = ensure_beijing_datetime(order.get('order_time', '')).isoformat()
                        await cursor.execute(
                            """INSERT INTO orders
                               (business_flow_id, table_number, dish_name, quantity,
                                order_time, price, total_amount, status, category, station,
                                priority, notes, source, created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                order.get('business_flow_id', f"AUTO_{int(time.time())}"),
                                order.get('table_number', ''),
                                order.get('dish_name', ''),
                                order.get('quantity', 0),
                                order_time,
                                order.get('price', 0.0),
                                order.get('total_amount', 0.0),
                                order.get('status', '未结'),
                                order.get('category', ''),
                                order.get('station', ''),
                                order.get('priority', 'normal'),
                                order.get('notes'),
                                order.get('source', ''),
                                now, now
                            )
                        )
                        inserted_count += 1
                    except Exception as e:
                        errors.append(str(e))
            await tdb.commit()
            logger.info(f"✅ 批量插入完成 - 插入: {inserted_count}条")
            return {"success": True, "inserted_count": inserted_count, "errors": errors}
        except Exception as e:
            logger.error(f"❌ 批量插入订单失败: {e}")
            return {"success": False, "error": str(e), "inserted_count": 0}

    async def batch_delete_orders(self, order_ids: List[str], dish_name: Optional[str] = None) -> Dict[str, Any]:
        try:
            if not order_ids:
                return {"success": True, "deleted_count": 0}
            deleted_count = 0
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                for oid in order_ids:
                    if dish_name:
                        await cursor.execute(
                            "DELETE FROM orders WHERE (id = ? OR business_flow_id = ?) AND dish_name = ?",
                            (oid, oid, dish_name)
                        )
                    else:
                        await cursor.execute(
                            "DELETE FROM orders WHERE id = ? OR business_flow_id = ?",
                            (oid, oid)
                        )
                    deleted_count += cursor.rowcount
            await tdb.commit()
            logger.info(f"✅ 批量删除完成 - 删除: {deleted_count}条")
            return {"success": True, "deleted_count": deleted_count}
        except Exception as e:
            logger.error(f"❌ 批量删除订单失败: {e}")
            return {"success": False, "error": str(e)}

    async def mark_delivery_cancelled(self, bs_code: str) -> int:
        """外卖单取消：把该 bsCode 的所有外卖行软删除（退菜 + 数量/金额归零 + 出餐置已取消）。

        只影响 source='delivery' 且尚未退菜的行，返回受影响行数。
        采用归零而非物理删除，保留审计痕迹；主销量查询按 SUM(quantity) 自然不计。
        """
        if not bs_code:
            return 0
        try:
            now = datetime.now(CHINA_TZ).isoformat()
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """UPDATE orders
                       SET status = '退菜', quantity = 0, total_amount = 0,
                           dish_status = '已取消', updated_at = ?
                       WHERE source = 'delivery'
                         AND business_flow_id LIKE ?
                         AND status != '退菜'""",
                    (now, f"{bs_code}_%"),
                )
                affected = cursor.rowcount
            await tdb.commit()
            if affected:
                logger.info(f"🚫 外卖单取消，软删除 {affected} 行: bsCode={bs_code}")
            return affected
        except Exception as e:
            logger.error(f"❌ 标记外卖取消失败 bsCode={bs_code}: {e}")
            return 0

    async def revert_delivery_cancelled(self, orders: List[Dict]) -> int:
        """外卖单重现（POS 反悔/恢复）：按重新拉取的明细恢复此前被软删除的行。

        逐条按 business_flow_id 精确匹配，仅恢复当前处于 '退菜' 的外卖行。
        重现时重新拉取的明细带着当下 POS 的下单时间/单价，取消期间若被 POS 侧
        改过（如重新结算），应以这份新鲜值覆盖，而非继续沿用取消前的旧值。
        """
        if not orders:
            return 0
        try:
            now = datetime.now(CHINA_TZ).isoformat()
            tdb = self.table("orders")
            restored = 0
            async with tdb.conn.cursor() as cursor:
                for order in orders:
                    flow_id = order.get('business_flow_id', '')
                    if not flow_id:
                        continue
                    order_time = ensure_beijing_datetime(order.get('order_time', '')).isoformat()
                    await cursor.execute(
                        """UPDATE orders
                           SET status = ?, quantity = ?, order_time = ?, price = ?, total_amount = ?,
                               dish_status = '待出餐', updated_at = ?
                           WHERE business_flow_id = ? AND source = 'delivery'
                             AND status = '退菜'""",
                        (
                            order.get('status', '已结'),
                            order.get('quantity', 1),
                            order_time,
                            order.get('price', 0.0),
                            order.get('total_amount', 0.0),
                            now,
                            flow_id,
                        ),
                    )
                    restored += cursor.rowcount
            await tdb.commit()
            if restored:
                logger.info(f"↩️ 外卖单重现，恢复 {restored} 行")
            return restored
        except Exception as e:
            logger.error(f"❌ 恢复外卖取消行失败: {e}")
            return 0

    async def cancel_dine_in_portions(
        self, table_number: str, dish_name: str, portions: int
    ) -> int:
        """Mark N dine-in 退菜对象 as cancelled by 桌号+菜名.

        Selection: 未做 (待出餐, not loaded) → 在蒸 → 已出餐. Same bucket uses
        earlier 下单时间. Soft-cancel fields match delivery: 已取消 / 退菜 /
        qty+amount 0; steamer placement is left untouched.
        """
        if not table_number or not dish_name or portions <= 0:
            return 0
        try:
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id FROM orders
                       WHERE table_number = ?
                         AND dish_name = ?
                         AND IFNULL(source, '') != 'delivery'
                         AND status != '退菜'
                         AND IFNULL(dish_status, '待出餐') != '已取消'
                         AND IFNULL(business_flow_id, '') NOT LIKE '%_refund_%'
                         AND IFNULL(dish_status, '待出餐') IN (
                             '待出餐', '已制作待上菜', '已上菜'
                         )
                       ORDER BY
                         CASE
                           WHEN IFNULL(dish_status, '待出餐') = '待出餐'
                                AND (steamer_id IS NULL OR steamer_id = '') THEN 0
                           WHEN IFNULL(dish_status, '待出餐') = '待出餐' THEN 1
                           ELSE 2
                         END,
                         order_time ASC,
                         id ASC
                       LIMIT ?""",
                    (table_number, dish_name, int(portions)),
                )
                target_ids = [row[0] for row in await cursor.fetchall()]
                affected = 0
                for row_id in target_ids:
                    now = datetime.now(CHINA_TZ).isoformat()
                    await cursor.execute(
                        """UPDATE orders
                           SET status = '退菜', quantity = 0, total_amount = 0,
                               dish_status = '已取消', updated_at = ?
                           WHERE id = ?""",
                        (now, row_id),
                    )
                    affected += cursor.rowcount
            await tdb.commit()
            if affected:
                logger.info(
                    "🚫 堂食退菜，软取消 %s 行: table=%s dish=%s",
                    affected,
                    table_number,
                    dish_name,
                )
            return affected
        except Exception as e:
            logger.error(
                "❌ 堂食退菜失败 table=%s dish=%s: %s", table_number, dish_name, e
            )
            return 0

    async def restore_dine_in_cancelled(
        self, table_number: str, dish_name: str, order: Optional[Dict] = None
    ) -> Optional[Dict]:
        """Restore the most recently cancelled original dine-in 订单行.

        After restore dish_status is 待出餐. Placement is kept: still on a hole
        → 在蒸; no placement → 未做. Does not insert a new row.
        """
        if not table_number or not dish_name:
            return None
        fields = order or {}
        quantity = int(fields.get("quantity") or 1)
        if quantity <= 0:
            quantity = 1
        price = float(fields.get("price") or 0.0)
        total_amount = fields.get("total_amount")
        if total_amount is None:
            total_amount = price * quantity
        status = fields.get("status") or "未结"
        try:
            now = datetime.now(CHINA_TZ).isoformat()
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id FROM orders
                       WHERE table_number = ?
                         AND dish_name = ?
                         AND status = '退菜'
                         AND dish_status = '已取消'
                         AND IFNULL(source, '') != 'delivery'
                         AND IFNULL(business_flow_id, '') NOT LIKE '%_refund_%'
                       ORDER BY updated_at DESC, id DESC
                       LIMIT 1""",
                    (table_number, dish_name),
                )
                row = await cursor.fetchone()
                if not row:
                    return None
                row_id = row[0]
                await cursor.execute(
                    """UPDATE orders
                       SET status = ?, quantity = ?, price = ?, total_amount = ?,
                           dish_status = '待出餐', ready_time = NULL, updated_at = ?
                       WHERE id = ? AND status = '退菜' AND dish_status = '已取消'""",
                    (status, quantity, price, float(total_amount), now, row_id),
                )
                if cursor.rowcount <= 0:
                    return None
            await tdb.commit()
            logger.info(
                "↩️ 堂食退后重下，恢复行 id=%s table=%s dish=%s",
                row_id,
                table_number,
                dish_name,
            )
            return await self.get_order_by_id(str(row_id))
        except Exception as e:
            logger.error(
                "❌ 堂食退后重下失败 table=%s dish=%s: %s",
                table_number,
                dish_name,
                e,
            )
            return None

    async def get_delivery_flow_ids(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[str]:
        """时间段内所有未取消外卖行的 business_flow_id（对账消失兜底用）。"""
        try:
            conditions = ["source = 'delivery'", "status != '退菜'"]
            params: List[Any] = []
            if start_time:
                conditions.append("order_time >= ?"); params.append(start_time.isoformat())
            if end_time:
                conditions.append("order_time <= ?"); params.append(end_time.isoformat())
            where = " AND ".join(conditions)
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"SELECT business_flow_id FROM orders WHERE {where}", params
                )
                rows = await cursor.fetchall()
            return [row[0] for row in rows if row and row[0]]
        except Exception as e:
            logger.error(f"❌ 获取外卖流水号失败: {e}")
            return []

    async def search_orders_raw(
        self,
        match_condition: Dict,
        limit: int,
        *,
        dish_name_contains: Optional[str] = None,
        order_time_start=None,
        order_time_end=None,
    ) -> List[Dict]:
        try:
            conditions, params = [], []
            for key, val in match_condition.items():
                if key == 'order_time':
                    raise ValueError(
                        "order_time in match_condition is not supported; "
                        "use order_time_start= / order_time_end="
                    )
                elif key == 'station':
                    conditions.append("station = ?"); params.append(val)
                elif key == 'table_number':
                    conditions.append("table_number = ?"); params.append(val)
                elif key == 'dish_name':
                    raise ValueError(
                        "dish_name in match_condition is not supported; use dish_name_contains="
                    )
            start_sql = to_sql_datetime(order_time_start)
            end_sql = to_sql_datetime(order_time_end)
            if start_sql is not None:
                conditions.append("order_time >= ?")
                params.append(start_sql)
            if end_sql is not None:
                conditions.append("order_time <= ?")
                params.append(end_sql)
            contains = (dish_name_contains or "").strip()
            if contains:
                conditions.append("dish_name LIKE ?")
                params.append(f"%{contains}%")
            where = " AND ".join(conditions) if conditions else "1=1"
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"SELECT * FROM orders WHERE {where} ORDER BY order_time DESC LIMIT ?",
                    params + [limit]
                )
                rows = await cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ 搜索订单失败: {e}")
            return []

    async def _get_dish_station_mapping(self) -> Dict[str, str]:
        """获取每个菜品最常见的档口（从 orders 表统计）"""
        try:
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT dish_name, station, COUNT(*) as cnt
                       FROM orders WHERE station != '' AND station IS NOT NULL
                       GROUP BY dish_name, station ORDER BY dish_name, cnt DESC"""
                )
                rows = await cursor.fetchall()
            result: Dict[str, str] = {}
            for row in rows:
                dish, station = row[0], row[1]
                if dish not in result and station:
                    result[dish] = station
            return result
        except Exception as e:
            logger.warning(f"⚠️ 获取菜品档口映射失败: {e}")
            return {}

    async def get_unique_dish_names(self, limit: int = 500) -> List[str]:
        try:
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT DISTINCT dish_name FROM orders ORDER BY dish_name LIMIT ?", (limit,)
                )
                rows = await cursor.fetchall()
            return [r[0] for r in rows if r[0]]
        except Exception as e:
            logger.error(f"❌ 获取菜品名列表失败: {e}")
            return []

    async def list_distinct_order_dish_names(self, limit: int = 100000) -> List[str]:
        """Distinct dish names recently seen on orders (newest rowids first)."""
        try:
            tdb = self.table("orders")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT DISTINCT dish_name FROM orders ORDER BY rowid DESC LIMIT ?",
                    (limit,),
                )
                rows = await cursor.fetchall()
            return [r[0] for r in rows if r[0]]
        except Exception as e:
            logger.error(f"❌ 获取订单菜品名列表失败: {e}")
            return []

    async def apply_cooking_completion(
        self,
        *,
        ready_time: str,
        completions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Apply validated cooking completions (full mark or partial split).

        Each item: ``{"order": <row dict with _id>, "complete_quantity": int}``.
        """
        tdb = self.table("orders")
        now = datetime.now(CHINA_TZ).isoformat()
        updated_count = 0
        stations = set()
        holes_to_compact: Set[Tuple[str, int]] = set()

        for item in completions:
            order = item["order"]
            complete_qty = int(item["complete_quantity"])
            db_qty = int(order.get("quantity") or 1)
            station = order.get("station") or ""
            if station:
                stations.add(station)

            hole = _placement_hole(order)
            if hole:
                holes_to_compact.add(hole)

            oid = order["_id"]
            if complete_qty == db_qty:
                await tdb.execute(
                    """UPDATE orders SET dish_status = ?, ready_time = ?, updated_at = ?,
                       steamer_id = NULL, port_index = NULL, stack_order = NULL,
                       loaded_at = NULL
                       WHERE id = ?""",
                    ("已制作待上菜", ready_time, now, oid),
                )
                updated_count += 1
            else:
                remaining = db_qty - complete_qty
                await tdb.execute(
                    "UPDATE orders SET quantity = ?, updated_at = ? WHERE id = ?",
                    (remaining, now, oid),
                )
                order_time = order.get("order_time")
                if hasattr(order_time, "isoformat"):
                    order_time = order_time.isoformat()
                await tdb.execute(
                    """INSERT INTO orders
                       (business_flow_id, table_number, dish_name, quantity, order_time,
                        price, total_amount, status, category, station, priority, notes,
                        dish_status, ready_time, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        order.get("business_flow_id", ""),
                        order.get("table_number", ""),
                        order.get("dish_name", ""),
                        complete_qty,
                        order_time,
                        order.get("price", 0.0),
                        order.get("total_amount", 0.0),
                        order.get("status", "未结"),
                        order.get("category", ""),
                        order.get("station", ""),
                        order.get("priority", "normal"),
                        order.get("notes"),
                        "已制作待上菜",
                        ready_time,
                        now,
                        now,
                    ),
                )
                updated_count += 1

        for steamer_id, port_index in holes_to_compact:
            await self.compact_steamer_hole(steamer_id, port_index)

        await tdb.commit()
        return {
            "updated_count": updated_count,
            "stations": sorted(stations),
        }

    async def compact_steamer_hole(self, steamer_id: str, port_index: int) -> None:
        """Reindex remaining cages on a hole to stack_order 1..n. Does not commit."""
        tdb = self.table("orders")
        now = datetime.now(CHINA_TZ).isoformat()
        cursor = await tdb.execute(
            """SELECT id FROM orders
               WHERE steamer_id = ? AND port_index = ?
               ORDER BY stack_order ASC, id ASC""",
            (steamer_id, int(port_index)),
        )
        rows = await cursor.fetchall()
        for index, row in enumerate(rows, start=1):
            await tdb.execute(
                "UPDATE orders SET stack_order = ?, updated_at = ? WHERE id = ?",
                (index, now, row[0]),
            )

    async def apply_steamer_load(
        self,
        *,
        steamer_id: str,
        port_index: int,
        loaded_at: str,
        order_ids: List[str],
    ) -> Dict[str, Any]:
        """Write 蒸笼位; stack_order appends at the hole top."""
        tdb = self.table("orders")
        now = datetime.now(CHINA_TZ).isoformat()
        hole_port = int(port_index)
        cursor = await tdb.execute(
            """SELECT MAX(stack_order) FROM orders
               WHERE steamer_id = ? AND port_index = ?""",
            (steamer_id, hole_port),
        )
        row = await cursor.fetchone()
        next_stack = int(row[0] or 0) + 1

        updated_count = 0
        stations = set()
        for oid in order_ids:
            order = await self.get_order_by_id(str(oid))
            if not order:
                continue
            row_id = order["_id"]
            station = order.get("station") or ""
            if station:
                stations.add(station)
            await tdb.execute(
                """UPDATE orders SET steamer_id = ?, port_index = ?, stack_order = ?,
                   loaded_at = ?, updated_at = ? WHERE id = ?""",
                (steamer_id, hole_port, next_stack, loaded_at, now, row_id),
            )
            next_stack += 1
            updated_count += 1

        await tdb.commit()
        return {
            "updated_count": updated_count,
            "stations": sorted(stations),
        }

    async def apply_steamer_move(
        self,
        *,
        steamer_id: str,
        port_index: int,
        order_ids: List[str],
    ) -> Dict[str, Any]:
        """Move 在蒸 cages onto dest hole top; compact each source hole."""
        tdb = self.table("orders")
        now = datetime.now(CHINA_TZ).isoformat()
        dest_port = int(port_index)
        dest = (str(steamer_id), dest_port)
        cursor = await tdb.execute(
            """SELECT MAX(stack_order) FROM orders
               WHERE steamer_id = ? AND port_index = ?""",
            (steamer_id, dest_port),
        )
        row = await cursor.fetchone()
        next_stack = int(row[0] or 0) + 1

        updated_count = 0
        stations = set()
        holes_to_compact: Set[Tuple[str, int]] = set()
        for oid in order_ids:
            order = await self.get_order_by_id(str(oid))
            if not order:
                continue
            source = _placement_hole(order)
            if source == dest:
                continue
            row_id = order["_id"]
            station = order.get("station") or ""
            if station:
                stations.add(station)
            if source:
                holes_to_compact.add(source)
            await tdb.execute(
                """UPDATE orders SET steamer_id = ?, port_index = ?, stack_order = ?,
                   updated_at = ? WHERE id = ?""",
                (steamer_id, dest_port, next_stack, now, row_id),
            )
            next_stack += 1
            updated_count += 1

        for source_id, source_port in holes_to_compact:
            await self.compact_steamer_hole(source_id, source_port)

        await tdb.commit()
        return {
            "updated_count": updated_count,
            "stations": sorted(stations),
        }

    async def apply_steamer_unload(
        self,
        *,
        order_ids: List[str],
    ) -> Dict[str, Any]:
        """Clear 蒸笼位; compact each source hole. dish_status is unchanged."""
        tdb = self.table("orders")
        now = datetime.now(CHINA_TZ).isoformat()
        updated_count = 0
        stations = set()
        holes_to_compact: Set[Tuple[str, int]] = set()
        for oid in order_ids:
            order = await self.get_order_by_id(str(oid))
            if not order:
                continue
            source = _placement_hole(order)
            if not source:
                continue
            row_id = order["_id"]
            station = order.get("station") or ""
            if station:
                stations.add(station)
            holes_to_compact.add(source)
            await tdb.execute(
                """UPDATE orders SET steamer_id = NULL, port_index = NULL,
                   stack_order = NULL, loaded_at = NULL, updated_at = ?
                   WHERE id = ?""",
                (now, row_id),
            )
            updated_count += 1

        for source_id, source_port in holes_to_compact:
            await self.compact_steamer_hole(source_id, source_port)

        await tdb.commit()
        return {
            "updated_count": updated_count,
            "stations": sorted(stations),
        }

    async def apply_steamer_pluck(
        self,
        *,
        order_ids: List[str],
    ) -> Dict[str, Any]:
        """Clear 蒸笼位 on a 退菜占位; compact the hole.

        dish_status stays cancelled. updated_at is left as the cancel timestamp
        so 待上笼退示 does not restart after 抽笼.
        """
        tdb = self.table("orders")
        updated_count = 0
        stations = set()
        holes_to_compact: Set[Tuple[str, int]] = set()
        for oid in order_ids:
            order = await self.get_order_by_id(str(oid))
            if not order:
                continue
            source = _placement_hole(order)
            if not source:
                continue
            row_id = order["_id"]
            station = order.get("station") or ""
            if station:
                stations.add(station)
            holes_to_compact.add(source)
            await tdb.execute(
                """UPDATE orders SET steamer_id = NULL, port_index = NULL,
                   stack_order = NULL
                   WHERE id = ?""",
                (row_id,),
            )
            updated_count += 1

        for source_id, source_port in holes_to_compact:
            await self.compact_steamer_hole(source_id, source_port)

        await tdb.commit()
        return {
            "updated_count": updated_count,
            "stations": sorted(stations),
        }

    async def sync_order_stations(
        self,
        since: datetime,
        mapping: Dict[str, str],
        *,
        batch_size: int = 200,
    ) -> Dict[str, Any]:
        """Set orders.station from mapping for rows with order_time >= since."""
        if not mapping:
            return {
                "success": True,
                "updated": 0,
                "skipped": 0,
                "total_mappings": 0,
                "message": "映射表为空",
            }

        tdb = self.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT rowid, dish_name FROM orders WHERE order_time >= ?",
                (since.isoformat(),),
            )
            rows = await cursor.fetchall()

        now_iso = datetime.now(CHINA_TZ).isoformat()
        updated = 0
        batch: List[tuple] = []

        async def _flush(pending: List[tuple]) -> None:
            async with tdb.conn.cursor() as cursor:
                for station, ts, rowid in pending:
                    await cursor.execute(
                        "UPDATE orders SET station = ?, updated_at = ? WHERE rowid = ?",
                        (station, ts, rowid),
                    )
            await tdb.commit()

        for rowid, dish_name in rows:
            station = mapping.get(dish_name)
            if station:
                batch.append((station, now_iso, rowid))
                if len(batch) >= batch_size:
                    await _flush(batch)
                    updated += len(batch)
                    batch = []
        if batch:
            await _flush(batch)
            updated += len(batch)

        skipped = len(rows) - updated
        return {
            "success": True,
            "updated": updated,
            "skipped": skipped,
            "total_mappings": len(mapping),
            "message": f"同步完成：更新 {updated} 条，无映射菜品 {skipped} 条",
        }
