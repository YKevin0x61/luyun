#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
餐厅订单数据采集系统业务逻辑服务层
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from models import StationStats
from config import KITCHEN_STATIONS
from services.urgency_policy import level_for_wait_ms, urgent_threshold_ms

logger = logging.getLogger(__name__)

CHINA_TZ = timezone(timedelta(hours=8))


class DishMergerService:
    """菜品合并服务"""

    @staticmethod
    def merge_dishes_by_name(orders: List[Dict], station_filter: Optional[str] = None) -> List[Dict]:
        """合并菜品数据（按名称+档口分组）"""
        dish_groups = {}

        for order in orders:
            if station_filter and order.get('station') != station_filter:
                continue

            dish_name = order.get('dish_name', '')
            station = order.get('station', '')
            key = f"{dish_name}_{station}"

            if key not in dish_groups:
                dish_groups[key] = {
                    'name': dish_name,
                    'station': station,
                    'orders': [],
                    'total_quantity': 0,
                    'table_numbers': set(),
                    'max_wait_time': 0,
                    'min_wait_time': float('inf'),
                    'avg_wait_time': 0,
                    'priority': 'normal'
                }

            group = dish_groups[key]
            group['orders'].append(order)
            group['total_quantity'] += order.get('quantity', 0)
            group['table_numbers'].add(order.get('table_number', ''))

            order_time = order.get('order_time')
            if isinstance(order_time, datetime):
                wait_time = int((datetime.now(CHINA_TZ) - order_time).total_seconds() * 1000)
                group['max_wait_time'] = max(group['max_wait_time'], wait_time)
                group['min_wait_time'] = min(group['min_wait_time'], wait_time)

        for group in dish_groups.values():
            if group['orders']:
                total_wait_time = sum(
                    int((datetime.now(CHINA_TZ) - order.get('order_time', datetime.now())).total_seconds() * 1000)
                    for order in group['orders']
                    if isinstance(order.get('order_time'), datetime)
                )
                group['avg_wait_time'] = total_wait_time // len(group['orders'])
                group['table_numbers'] = list(group['table_numbers'])
                group['priority'] = DishMergerService._determine_priority(group)

                if group['min_wait_time'] == float('inf'):
                    group['min_wait_time'] = 0

        return list(dish_groups.values())

    @staticmethod
    def _determine_priority(group: Dict) -> str:
        """根据等待时间确定优先级"""
        return level_for_wait_ms(group.get('max_wait_time', 0))

    @staticmethod
    def sort_dishes(dishes: List[Dict], sort_type: str = 'time') -> List[Dict]:
        """排序菜品列表"""
        if sort_type == 'time':
            return sorted(dishes, key=lambda x: x.get('max_wait_time', 0), reverse=True)
        elif sort_type == 'priority':
            priority_order = {'urgent': 3, 'high': 2, 'normal': 1}
            return sorted(dishes, key=lambda x: (priority_order.get(x.get('priority', 'normal'), 1), x.get('max_wait_time', 0)), reverse=True)
        elif sort_type == 'quantity':
            return sorted(dishes, key=lambda x: x.get('total_quantity', 0), reverse=True)
        elif sort_type == 'station':
            return sorted(dishes, key=lambda x: x.get('station', ''))
        return dishes


class StationService:
    """档口管理服务"""

    @staticmethod
    def get_station_info(station_id: str) -> Optional[Dict]:
        """获取档口信息"""
        return KITCHEN_STATIONS.get(station_id)

    @staticmethod
    def get_all_stations() -> List[Dict]:
        """获取所有档口信息"""
        return list(KITCHEN_STATIONS.values())

    @staticmethod
    def calculate_station_stats(orders: List[Dict], station_id: str) -> StationStats:
        """计算档口统计信息"""
        station_orders = [order for order in orders if order.get('station') == station_id]

        total_orders = len(station_orders)
        total_quantity = sum(o.get('quantity', 0) for o in station_orders)

        # 紧急数量（等待超过 urgent 阈值）
        urgent_sec = urgent_threshold_ms() / 1000.0
        urgent_count = len([
            o for o in station_orders
            if isinstance(o.get('order_time'), datetime) and
            (datetime.now(CHINA_TZ) - o.get('order_time')).total_seconds() > urgent_sec
        ])

        wait_times = []
        for order in station_orders:
            if isinstance(order.get('order_time'), datetime):
                wait_time = (datetime.now(CHINA_TZ) - order.get('order_time')).total_seconds() * 1000
                wait_times.append(wait_time)

        avg_wait_time = int(sum(wait_times) / len(wait_times)) if wait_times else 0

        station_info = StationService.get_station_info(station_id)
        station_name = station_info.get('name', station_id) if station_info else station_id

        return StationStats(
            station_id=station_id,
            station_name=station_name,
            total_orders=total_orders,
            total_quantity=total_quantity,
            urgent_count=urgent_count,
            avg_wait_time=avg_wait_time
        )


# 创建全局服务实例
dish_merger_service = DishMergerService()
station_service = StationService()
