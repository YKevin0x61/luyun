#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Table change detection: amount/dish diffs and flow_id allocation."""

import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from db_core.order_notes import canonical_order_notes
from scraper._common import CHINA_TZ
from scraper.order_flow_ids import allocate_incremental_flow_ids


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

            # 检测变化的餐桌
            changed_tables = []

            if self._state.is_first_run:
                # 首次运行，获取所有有订单的餐桌详情
                changed_tables = [table for table in current_tables_data if table['amount'] > 0]
                self.logger.info(f"🚀 首次运行: 发现{len(changed_tables)}个有订单的餐桌")
                self._state.is_first_run = False
            else:
                # 检查变化的餐桌
                for table in current_tables_data:
                    table_number = table['table_number']
                    current_amount = table['amount']

                    # 新餐桌或金额发生变化
                    if (table_number not in self._state.previous_tables_state or
                        self._state.previous_tables_state[table_number] != current_amount):
                        changed_tables.append(table)

                        # 简化日志，详情在摘要中显示

            # 获取变化餐桌的详情
            all_orders = []
            if changed_tables:
                self.logger.info(f"🔍 检测到 {len(changed_tables)} 个餐桌有变化")

                # 获取变化餐桌的订单详情
                new_orders = await self._get_orders_for_changed_tables(changed_tables)

                # 处理每个变化的餐桌
                for table in changed_tables:
                    table_number = table['table_number']
                    current_amount = table['amount']

                    # 获取当前餐桌的菜品列表
                    current_table_orders = [
                        order for order in new_orders
                        if order.get('table_number') == table_number
                    ] if new_orders else []

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
            else:
                current_time = datetime.now(CHINA_TZ).strftime("%H:%M:%S")
                self.logger.info(f"✅ [{current_time}] 餐桌状态无变化")

            # 更新餐桌状态
            self._state.previous_tables_state = current_tables_state
            self._state.save_table_state()

            return all_orders

        except Exception as e:
            self.logger.error(f"❌ 监控餐桌订单失败: {e}")
            import traceback
            self.logger.error(f"详细错误信息: {traceback.format_exc()}")
            return []

    async def _get_orders_for_changed_tables(self, changed_tables: List[Dict]) -> List[Dict]:
        """获取变化餐桌的点菜详情"""
        all_orders = []

        for table in changed_tables:
            table_number = table['table_number']

            # 只查询有金额的餐桌
            if table['amount'] <= 0:
                continue

            # pointId 优先取 getbusypointdata 的返回值；仅在缺失时才回退到 table_mapping 推导，
            # 否则新增的桌台/包间只要没被手工加进映射表就会被静默跳过。
            point_id = table.get('point_id') or self._session.resolve_point_id(table_number)
            if not point_id:
                self.logger.warning(f"⚠️  无法获取 {table_number} 号桌的point_id")
                continue

            try:
                orders = await self._session.fetch_table_orders(table_number, point_id)
                if orders:
                    all_orders.extend(orders)
                # 简化餐桌处理日志，只在摘要中显示

            except Exception as e:
                self.logger.error(f"❌ 获取 {table_number} 号桌详情失败: {e}")
                continue

        return all_orders

    async def _detect_dish_changes(
        self,
        table_number: str,
        current_orders: List[Dict],
        previous_amount: float,
        current_amount: float,
        orders=None,
    ) -> tuple:
        """Detect qty-up / qty-down. Qty-down cancels existing 订单行 via OrdersPort."""
        changed_items = []

        previous_orders = self._state.previous_table_orders.get(table_number, [])
        known_orders = list(current_orders) + list(previous_orders)

        current_dish_counts = {}
        for order in current_orders:
            dish_key = _dine_in_dish_key(order)
            current_dish_counts[dish_key] = current_dish_counts.get(dish_key, 0) + order.get('quantity', 0)

        previous_dish_counts = {}
        previous_orders_map = {}
        for order in previous_orders:
            dish_key = _dine_in_dish_key(order)
            previous_dish_counts[dish_key] = previous_dish_counts.get(dish_key, 0) + order.get('quantity', 0)
            if dish_key not in previous_orders_map:
                previous_orders_map[dish_key] = order

        all_dish_keys = set(current_dish_counts.keys()) | set(previous_dish_counts.keys())

        for dish_key in all_dish_keys:
            current_qty = current_dish_counts.get(dish_key, 0)
            previous_qty = previous_dish_counts.get(dish_key, 0)
            dish_name, price, notes = dish_key

            if current_qty > previous_qty:
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
                if orders is not None:
                    affected = await orders.cancel_dine_in_portions(
                        table_number, dish_name, reduced_quantity, notes=notes
                    )
                    self.last_dine_in_port_updates += affected
                else:
                    self.logger.warning(
                        "⚠️  堂食少份未接到订单端口，跳过插入退菜行: table=%s dish=%s qty=%s",
                        table_number,
                        dish_name,
                        reduced_quantity,
                    )

        return changed_items, current_orders

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
