#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Table change detection: amount/dish diffs and flow_id allocation."""

import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from db_core.order_notes import canonical_order_notes
from scraper._common import CHINA_TZ
from scraper.order_flow_ids import allocate_incremental_flow_ids


# 同一道菜连续多少轮取材成功却看不到它，才认定堂食菜真的没了。对齐外卖链路的
# DELIVERY_CANCEL_MISS_THRESHOLD：单轮缺席可能只是 POS 明细接口返回不全。
DINE_IN_CANCEL_MISS_THRESHOLD = 3


def _dine_in_dish_key(order: Dict[str, Any]) -> Tuple[Any, Any, str]:
    """Dish name + unit price + canonical notes; blank notes are a distinct identity."""
    return (
        order.get("dish_name", ""),
        order.get("price", 0.0),
        canonical_order_notes(order.get("notes")),
    )


class TableChangeDetector:
    """Detect table amount/dish changes; persist snapshots via ScraperStateStore."""

    def __init__(self, session, state_store, *, logger_: Optional[logging.Logger] = None):
        self._session = session
        self._state = state_store
        self.logger = logger_ or logging.getLogger(__name__)
        self.last_dine_in_port_updates = 0
        # 桌号 -> 菜品身份 -> {"miss_count", "baseline_amount"}：未确认的堂食退菜去抖计数。
        # 只活在进程内：丢了最多让退菜晚几轮，不会凭空退菜，所以不进状态文件。
        self._dine_in_cancel_misses: Dict[str, Dict[Any, Dict[str, Any]]] = {}

    async def monitor_table_orders(
        self,
        current_tables_data: Optional[List[Dict]] = None,
        orders=None,
    ) -> List[Dict]:
        """监控餐桌点菜详情变化 - 智能检测模式"""
        try:
            self.last_dine_in_port_updates = 0
            # 确保已初始化
            if not await self._session.ensure_ready():
                return []

            # 获取当前餐桌状态
            if current_tables_data is None:
                current_tables_data = await self._session.scrape_table_data()
            if not current_tables_data:
                self.logger.info("ℹ️  当前没有餐桌数据")
                return []

            # 当前餐桌状态字典（餐桌号: 金额）
            current_tables_state = {
                table['table_number']: table['amount']
                for table in current_tables_data
            }

            # 桌台消失（结账/清台）后，未确认的退菜不再有意义：清掉计数，
            # 免得同一桌号后来复用旧的去抖计数。
            for table_number in list(self._dine_in_cancel_misses):
                if table_number not in current_tables_state:
                    self._dine_in_cancel_misses.pop(table_number, None)

            # 检测变化的餐桌
            changed_tables = []

            if self._state.is_first_run:
                # 首次运行，获取所有有订单的餐桌详情
                changed_tables = [table for table in current_tables_data if table['amount'] > 0]
                self.logger.info(f"🚀 首次运行: 发现{len(changed_tables)}个有订单的餐桌")
                # 取材失败的桌不会被写进 previous_tables_state，下一轮仍按“新桌”重新取材，
                # 所以首轮标记可以直接清掉。
                self._state.is_first_run = False
            else:
                # 检查变化的餐桌
                for table in current_tables_data:
                    table_number = table['table_number']
                    current_amount = table['amount']

                    # 新餐桌、金额发生变化，或该桌还有未确认的退菜（金额可能刚好回到上一轮的值，
                    # 不复核就永远确认不了）
                    if (table_number not in self._state.previous_tables_state or
                        self._state.previous_tables_state[table_number] != current_amount or
                        self._dine_in_cancel_misses.get(table_number)):
                        changed_tables.append(table)

                        # 简化日志，详情在摘要中显示

            # 获取变化餐桌的详情
            all_orders = []
            failed_tables = set()
            if changed_tables:
                self.logger.info(f"🔍 检测到 {len(changed_tables)} 个餐桌有变化")

                # 获取变化餐桌的订单详情：失败的桌单独收口，绝不与“这桌没菜”混同
                new_orders, failed_tables = await self._get_orders_for_changed_tables(changed_tables)

                # 处理每个变化的餐桌
                for table in changed_tables:
                    table_number = table['table_number']
                    current_amount = table['amount']

                    if table_number in failed_tables:
                        # 取材失败：既不能拿空列表进差分（会把已入库订单整体退菜），
                        # 也不能推进金额/明细快照，否则当天不再复核。
                        continue

                    # 获取当前餐桌的菜品列表（取材已成功，空列表就是“这桌真的没有菜”）
                    current_table_orders = [
                        order for order in new_orders
                        if order.get('table_number') == table_number
                    ]

                    # 检测菜品变化，只返回变化的部分
                    if table_number in self._state.previous_tables_state:
                        previous_amount = self._state.previous_tables_state[table_number]

                        # 检测菜品变化（新增、减少、退菜）
                        changed_dishes, current_table_orders_for_state = await self._detect_dish_changes(
                            table_number,
                            current_table_orders,
                            previous_amount,
                            current_amount,
                            orders=orders,
                        )

                        # 只添加发生变化的菜品
                        all_orders.extend(changed_dishes)

                        if changed_dishes:
                            self.logger.info(f"🔄 {table_number}号桌检测到 {len(changed_dishes)} 个菜品变化")

                        # 更新该餐桌的历史订单记录（使用完整的当前订单）
                        self._state.previous_table_orders[table_number] = current_table_orders_for_state
                    else:
                        # 新餐桌，所有菜品都是变化（新增）
                        for order in current_table_orders:
                            order['change_type'] = '新增'
                        all_orders.extend(current_table_orders)
                        self._state.previous_table_orders[table_number] = current_table_orders

                # 打印变化摘要
                if all_orders:
                    self._print_orders_summary(all_orders, changed_tables)

                if failed_tables:
                    self.logger.warning(
                        "⚠️  %s 张桌明细取材失败，本轮跳过其差分且不推进状态（下轮重新取材）: %s",
                        len(failed_tables),
                        "、".join(sorted(failed_tables)),
                    )
            else:
                current_time = datetime.now(CHINA_TZ).strftime("%H:%M:%S")
                self.logger.info(f"✅ [{current_time}] 餐桌状态无变化")

            # 更新餐桌状态：取材失败的桌保留旧金额（新桌则继续缺席），下一轮重新取材
            next_tables_state = dict(current_tables_state)
            for table_number in failed_tables:
                if table_number in self._state.previous_tables_state:
                    next_tables_state[table_number] = self._state.previous_tables_state[table_number]
                else:
                    next_tables_state.pop(table_number, None)
            self._state.previous_tables_state = next_tables_state
            self._state.save_table_state()

            return all_orders

        except Exception as e:
            self.logger.error(f"❌ 监控餐桌订单失败: {e}")
            import traceback
            self.logger.error(f"详细错误信息: {traceback.format_exc()}")
            return []

    async def _get_orders_for_changed_tables(self, changed_tables: List[Dict]) -> tuple:
        """获取变化餐桌的点菜详情，返回 (订单行, 取材失败的桌号集合)。

        取材失败（金额不可判断、pointId 缺失、明细接口返回 None、抛异常）必须与
        「这桌真的没有菜」（取材成功但返回空列表）区分开：调用方对 failed_tables
        跳过差分、且不推进这些桌的状态，否则单轮取材失败就会被读成“菜品全被退了”。
        """
        all_orders = []
        failed_tables = set()

        for table in changed_tables:
            table_number = table['table_number']

            # 只查询有金额的餐桌；没有金额就没有明细可对，不能据此退菜
            if table['amount'] <= 0:
                failed_tables.add(table_number)
                continue

            # pointId 优先取 getbusypointdata 的返回值；仅在缺失时才回退到 table_mapping 推导，
            # 否则新增的桌台/包间只要没被手工加进映射表就会被静默跳过。
            point_id = table.get('point_id') or self._session.resolve_point_id(table_number)
            if not point_id:
                self.logger.warning(f"⚠️  无法获取 {table_number} 号桌的point_id")
                failed_tables.add(table_number)
                continue

            try:
                table_orders = await self._session.fetch_table_orders(table_number, point_id)
            except Exception as e:
                self.logger.error(f"❌ 获取 {table_number} 号桌详情失败: {e}")
                failed_tables.add(table_number)
                continue

            if table_orders is None:
                self.logger.warning(f"⚠️  {table_number} 号桌明细取材失败，本轮不复核该桌")
                failed_tables.add(table_number)
                continue

            all_orders.extend(table_orders)

        return all_orders, failed_tables

    async def _detect_dish_changes(
        self,
        table_number: str,
        current_orders: List[Dict],
        previous_amount: float,
        current_amount: float,
        orders=None,
    ) -> tuple:
        """Detect qty-up / qty-down. Qty-down cancels existing 订单行 via OrdersPort.

        退菜（qty-down）要三个条件同时成立才落到 orders 端口：
        1. 本轮取材成功——取材失败的桌根本走不到这里（见 monitor_table_orders）；
        2. 同一道菜连续 DINE_IN_CANCEL_MISS_THRESHOLD 轮缺席（对齐外卖链路的去抖：
           单轮缺席也可能只是明细接口返回不全）；
        3. 金额确实比这道菜还在时小（``baseline_amount``）——没有金额佐证就不退菜。
        未确认的缺份会重新写回快照：既让下一轮继续复核，也保证菜后来又出现时按
        「无变化」处理，不会被当成新菜重复插行。
        """
        changed_items = []
        pending = self._dine_in_cancel_misses.setdefault(table_number, {})

        previous_orders = self._state.previous_table_orders.get(table_number, [])
        known_orders = list(current_orders) + list(previous_orders)

        current_dish_counts = {}
        for order in current_orders:
            dish_key = _dine_in_dish_key(order)
            current_dish_counts[dish_key] = current_dish_counts.get(dish_key, 0) + order.get('quantity', 0)

        previous_dish_counts = {}
        previous_orders_map = {}
        previous_rows_by_key: Dict[Any, List[Dict]] = {}
        for order in previous_orders:
            dish_key = _dine_in_dish_key(order)
            previous_dish_counts[dish_key] = previous_dish_counts.get(dish_key, 0) + order.get('quantity', 0)
            if dish_key not in previous_orders_map:
                previous_orders_map[dish_key] = order
            previous_rows_by_key.setdefault(dish_key, []).append(order)

        all_dish_keys = set(current_dish_counts.keys()) | set(previous_dish_counts.keys())
        pending_rows: List[Dict] = []

        for dish_key in all_dish_keys:
            current_qty = current_dish_counts.get(dish_key, 0)
            previous_qty = previous_dish_counts.get(dish_key, 0)
            dish_name, price, notes = dish_key

            if current_qty > previous_qty:
                # 菜回来了（或加份）：该菜未确认的退菜作废
                pending.pop(dish_key, None)
                added_quantity = current_qty - previous_qty
                template_order = None
                for order in current_orders:
                    if _dine_in_dish_key(order) == dish_key:
                        template_order = order
                        break
                if template_order is None:
                    template_order = previous_orders_map.get(dish_key)

                restored = 0
                if orders is not None and template_order is not None:
                    restore_fields = {
                        "quantity": 1,
                        "price": price,
                        "total_amount": price,
                        "status": template_order.get("status") or "未结",
                        "notes": notes,
                    }
                    for _ in range(added_quantity):
                        restored_row = await orders.restore_dine_in_cancelled(
                            table_number, dish_name, restore_fields
                        )
                        if restored_row is None:
                            break
                        restored += 1
                    self.last_dine_in_port_updates += restored

                insert_count = added_quantity - restored
                if template_order is not None and insert_count > 0:
                    new_flow_ids = allocate_incremental_flow_ids(
                        template_order,
                        known_orders,
                        insert_count,
                        refund=False,
                    )
                    for flow_id in new_flow_ids:
                        new_item = template_order.copy()
                        new_item["business_flow_id"] = flow_id
                        new_item["quantity"] = 1
                        new_item["total_amount"] = price
                        new_item["change_type"] = "新增" if previous_qty == 0 else "增加"
                        changed_items.append(new_item)
                        known_orders.append(new_item)

            elif current_qty < previous_qty:
                reduced_quantity = previous_qty - current_qty
                miss = pending.get(dish_key)
                if miss is None:
                    # 首次发现缺份：以「这道菜还在时」的金额作为退菜基线
                    miss = {"miss_count": 0, "baseline_amount": previous_amount}
                    pending[dish_key] = miss
                miss["miss_count"] += 1

                confirmed = (
                    orders is not None
                    and miss["miss_count"] >= DINE_IN_CANCEL_MISS_THRESHOLD
                    and current_amount < miss["baseline_amount"]
                )
                if confirmed:
                    affected = await orders.cancel_dine_in_portions(
                        table_number, dish_name, reduced_quantity, notes=notes
                    )
                    self.last_dine_in_port_updates += affected
                    pending.pop(dish_key, None)
                else:
                    self._log_deferred_dine_in_cancel(
                        table_number,
                        dish_name,
                        reduced_quantity,
                        miss,
                        current_amount,
                        port_available=orders is not None,
                    )
                    # 未确认的缺份留在快照里，下一轮继续复核
                    pending_rows.append(
                        self._pending_snapshot_row(
                            previous_rows_by_key[dish_key][0], reduced_quantity, price
                        )
                    )
            else:
                pending.pop(dish_key, None)

        return changed_items, list(current_orders) + pending_rows

    def _log_deferred_dine_in_cancel(
        self,
        table_number: str,
        dish_name: str,
        reduced_quantity: int,
        miss: Dict[str, Any],
        current_amount: float,
        *,
        port_available: bool,
    ) -> None:
        """退菜被推迟/否决时说明原因，便于从日志区分“抖动”与“真退菜”。"""
        if not port_available:
            self.logger.warning(
                "⚠️  堂食少份未接到订单端口，跳过插入退菜行: table=%s dish=%s qty=%s",
                table_number,
                dish_name,
                reduced_quantity,
            )
        elif miss["miss_count"] < DINE_IN_CANCEL_MISS_THRESHOLD:
            self.logger.warning(
                "⚠️  %s 号桌 %s 少 %s 份，连续 %s/%s 轮缺席，未达阈值暂不退菜",
                table_number,
                dish_name,
                reduced_quantity,
                miss["miss_count"],
                DINE_IN_CANCEL_MISS_THRESHOLD,
            )
        else:
            self.logger.warning(
                "⚠️  %s 号桌 %s 少 %s 份，但金额没变小（%.2f ≥ 基线 %.2f），不判退菜",
                table_number,
                dish_name,
                reduced_quantity,
                current_amount,
                miss["baseline_amount"],
            )

    @staticmethod
    def _pending_snapshot_row(
        template: Dict[str, Any], quantity: int, price: float
    ) -> Dict[str, Any]:
        """把未确认的缺份写回快照：同一道菜只留缺的那几份。"""
        carried = dict(template)
        carried["quantity"] = quantity
        carried["total_amount"] = price * quantity
        return carried

    def _print_orders_summary(self, orders: List[Dict], changed_tables: List[Dict]):
        """打印订单摘要"""
        if not orders:
            return

        # 统计变化类型
        change_stats = {'新增': 0, '增加': 0, '退菜': 0}
        for order in orders:
            change_type = order.get('change_type', '新增')
            change_stats[change_type] = change_stats.get(change_type, 0) + 1

        # 简化的摘要头部
        current_time = datetime.now(CHINA_TZ).strftime("%H:%M:%S")
        self.logger.info(f"\n📊 [{current_time}] 变化摘要: {len(orders)}项 | {len(changed_tables)}桌")

        # 显示变化统计
        stats_str = " | ".join([f"{k}:{v}" for k, v in change_stats.items() if v > 0])
        if stats_str:
            self.logger.info(f"   📈 {stats_str}")

        # 只显示前5个餐桌，减少日志冗余
        table_orders = {}
        for order in orders:
            table_num = order.get('table_number', '')
            if table_num not in table_orders:
                table_orders[table_num] = []
            table_orders[table_num].append(order)

        # 按餐桌排序并只显示前5个
        sorted_tables = sorted(table_orders.items())[:5]

        for table_num, table_order_list in sorted_tables:
            total_amount = sum(order.get('total_amount', 0) for order in table_order_list)

            # 极简的餐桌信息显示 - 只显示核心信息
            self.logger.info(f"   🏷️ {table_num}桌: {len(table_order_list)}项菜品 ¥{total_amount:.0f}")

        # 如果餐桌数超过5个，显示省略信息
        if len(table_orders) > 5:
            remaining_tables = len(table_orders) - 5
            self.logger.info(f"   ⋯ 另外{remaining_tables}个餐桌有变化")
