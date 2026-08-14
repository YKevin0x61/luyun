#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OrdersPort adapter — delegates to DatabaseManager mixin methods."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


class OrdersPortAdapter:
    __slots__ = ("_db",)

    def __init__(self, db: Any) -> None:
        self._db = db

    async def get_orders(
        self,
        station: Optional[str] = None,
        table_number: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        dish_status: Optional[str] = None,
        limit: int = 10000,
    ) -> List[Dict]:
        return await self._db.get_orders(
            station=station,
            table_number=table_number,
            start_time=start_time,
            end_time=end_time,
            dish_status=dish_status,
            limit=limit,
        )

    async def get_order_by_id(
        self, order_id: str, dish_name: Optional[str] = None
    ) -> Optional[Dict]:
        return await self._db.get_order_by_id(order_id, dish_name=dish_name)

    async def resolve_order_for_cooking(self, *args: Any, **kwargs: Any) -> Any:
        return await self._db.resolve_order_for_cooking(*args, **kwargs)

    async def apply_cooking_completion(
        self,
        *,
        ready_time: str,
        completions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return await self._db.apply_cooking_completion(
            ready_time=ready_time, completions=completions
        )

    async def apply_steamer_load(
        self,
        *,
        steamer_id: str,
        port_index: int,
        loaded_at: str,
        order_ids: List[str],
    ) -> Dict[str, Any]:
        return await self._db.apply_steamer_load(
            steamer_id=steamer_id,
            port_index=port_index,
            loaded_at=loaded_at,
            order_ids=order_ids,
        )

    async def apply_steamer_move(
        self,
        *,
        steamer_id: str,
        port_index: int,
        order_ids: List[str],
    ) -> Dict[str, Any]:
        return await self._db.apply_steamer_move(
            steamer_id=steamer_id,
            port_index=port_index,
            order_ids=order_ids,
        )

    async def apply_steamer_unload(
        self,
        *,
        order_ids: List[str],
    ) -> Dict[str, Any]:
        return await self._db.apply_steamer_unload(order_ids=order_ids)

    async def apply_steamer_pluck(
        self,
        *,
        order_ids: List[str],
    ) -> Dict[str, Any]:
        return await self._db.apply_steamer_pluck(order_ids=order_ids)

    async def get_merged_dishes(
        self, station: Optional[str] = None, **kwargs: Any
    ) -> List[Dict]:
        return await self._db.get_merged_dishes(station=station, **kwargs)

    async def save_orders(self, orders_data: List[Dict]) -> bool:
        return await self._db.save_orders(orders_data)

    async def batch_insert_orders(self, orders: List[Dict]) -> Dict[str, Any]:
        return await self._db.batch_insert_orders(orders)

    async def batch_delete_orders(
        self, order_ids: List[str], dish_name: Optional[str] = None
    ) -> Dict[str, Any]:
        return await self._db.batch_delete_orders(order_ids, dish_name=dish_name)

    async def mark_delivery_cancelled(self, bs_code: str) -> int:
        return await self._db.mark_delivery_cancelled(bs_code)

    async def revert_delivery_cancelled(self, orders: List[Dict]) -> int:
        return await self._db.revert_delivery_cancelled(orders)

    async def get_delivery_flow_ids(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[str]:
        return await self._db.get_delivery_flow_ids(start_time, end_time)

    async def search_orders_raw(
        self,
        match_condition: Dict,
        limit: int,
        *,
        dish_name_contains: Optional[str] = None,
        order_time_start: Any = None,
        order_time_end: Any = None,
    ) -> List[Dict]:
        return await self._db.search_orders_raw(
            match_condition,
            limit,
            dish_name_contains=dish_name_contains,
            order_time_start=order_time_start,
            order_time_end=order_time_end,
        )

    async def get_unique_dish_names(self, limit: int = 500) -> List[str]:
        return await self._db.get_unique_dish_names(limit=limit)

    async def list_distinct_order_dish_names(self, limit: int = 100000) -> List[str]:
        return await self._db.list_distinct_order_dish_names(limit=limit)

    async def sync_order_stations(
        self,
        since: datetime,
        mapping: Dict[str, str],
        *,
        batch_size: int = 200,
    ) -> Dict[str, Any]:
        return await self._db.sync_order_stations(
            since, mapping, batch_size=batch_size
        )

    async def get_station_stats(
        self,
        station_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict:
        return await self._db.get_station_stats(
            station_id, start_time=start_time, end_time=end_time
        )

    async def aggregate_orders_paginated(
        self,
        match_condition: Dict,
        skip: int,
        limit: int,
        *,
        order_time_start: Any = None,
        order_time_end: Any = None,
    ) -> Tuple:
        return await self._db.aggregate_orders_paginated(
            match_condition,
            skip,
            limit,
            order_time_start=order_time_start,
            order_time_end=order_time_end,
        )

    async def aggregate_orders_stats(self, station: Optional[str] = None) -> Dict:
        return await self._db.aggregate_orders_stats(station=station)

    async def aggregate_station_counts(
        self, start_time: datetime
    ) -> List[Dict[str, Any]]:
        return await self._db.aggregate_station_counts(start_time)

    async def aggregate_station_speed(self, target: datetime) -> Dict[str, Any]:
        return await self._db.aggregate_station_speed(target)

    async def aggregate_dishes_summary(
        self, station: Optional[str] = None
    ) -> List[Dict]:
        return await self._db.aggregate_dishes_summary(station=station)

    async def aggregate_hot_dishes(
        self, station: Optional[str] = None, limit_n: int = 10
    ) -> List[Dict]:
        return await self._db.aggregate_hot_dishes(station=station, limit_n=limit_n)
