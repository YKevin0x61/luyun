#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Domain ports (Orders / DishStations / Reports).

Access via ``db.orders`` / ``db.dish_stations`` / ``db.reports`` — each returns a
real adapter object (not identity-equal to ``DatabaseManager``). Services depend
on these Protocols so tests can inject fakes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple


class OrdersPort(Protocol):
    async def get_orders(
        self,
        station: Optional[str] = None,
        table_number: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        dish_status: Optional[str] = None,
        limit: int = 10000,
    ) -> List[Dict]: ...

    async def get_order_by_id(
        self, order_id: str, dish_name: Optional[str] = None
    ) -> Optional[Dict]: ...

    async def resolve_order_for_cooking(
        self, *args: Any, **kwargs: Any
    ) -> Any: ...

    async def apply_cooking_completion(
        self,
        *,
        ready_time: str,
        completions: List[Dict[str, Any]],
    ) -> Dict[str, Any]: ...

    async def apply_steamer_load(
        self,
        *,
        steamer_id: str,
        port_index: int,
        loaded_at: str,
        order_ids: List[str],
    ) -> Dict[str, Any]: ...

    async def apply_steamer_move(
        self,
        *,
        steamer_id: str,
        port_index: int,
        order_ids: List[str],
    ) -> Dict[str, Any]: ...

    async def apply_steamer_unload(
        self,
        *,
        order_ids: List[str],
    ) -> Dict[str, Any]: ...

    async def apply_steamer_pluck(
        self,
        *,
        order_ids: List[str],
    ) -> Dict[str, Any]: ...

    async def get_merged_dishes(
        self, station: Optional[str] = None, **kwargs: Any
    ) -> List[Dict]: ...

    async def save_orders(self, orders_data: List[Dict]) -> bool: ...

    async def batch_insert_orders(self, orders: List[Dict]) -> Dict[str, Any]: ...

    async def batch_delete_orders(
        self, order_ids: List[str], dish_name: Optional[str] = None
    ) -> Dict[str, Any]: ...

    async def mark_delivery_cancelled(self, bs_code: str) -> int: ...

    async def revert_delivery_cancelled(self, orders: List[Dict]) -> int: ...

    async def cancel_dine_in_portions(
        self, table_number: str, dish_name: str, portions: int
    ) -> int: ...

    async def restore_dine_in_cancelled(
        self, table_number: str, dish_name: str, order: Optional[Dict] = None
    ) -> Optional[Dict]: ...

    async def get_delivery_flow_ids(
        self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
    ) -> List[str]: ...

    async def search_orders_raw(
        self,
        match_condition: Dict,
        limit: int,
        *,
        dish_name_contains: Optional[str] = None,
        order_time_start: Any = None,
        order_time_end: Any = None,
    ) -> List[Dict]: ...

    async def get_unique_dish_names(self, limit: int = 500) -> List[str]: ...

    async def list_distinct_order_dish_names(self, limit: int = 100000) -> List[str]: ...

    async def sync_order_stations(
        self,
        since: datetime,
        mapping: Dict[str, str],
        *,
        batch_size: int = 200,
    ) -> Dict[str, Any]: ...

    async def get_station_stats(
        self,
        station_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict: ...

    async def aggregate_orders_paginated(
        self,
        match_condition: Dict,
        skip: int,
        limit: int,
        *,
        order_time_start: Any = None,
        order_time_end: Any = None,
    ) -> Tuple: ...

    async def aggregate_orders_stats(self, station: Optional[str] = None) -> Dict: ...

    async def aggregate_station_counts(self, start_time: datetime) -> List[Dict[str, Any]]: ...

    async def aggregate_station_speed(self, target: datetime) -> Dict[str, Any]: ...

    async def aggregate_dishes_summary(self, station: Optional[str] = None) -> List[Dict]: ...

    async def aggregate_hot_dishes(
        self, station: Optional[str] = None, limit_n: int = 10
    ) -> List[Dict]: ...


class DishStationsPort(Protocol):
    """dish_stations table access for DishCatalog (+ read APIs).

    Write helpers exist so Catalog never touches ``table().conn``; callers outside
    Catalog must still go through DishCatalog upsert/remove (sole write path).
    """

    async def dish_stations_count(
        self, query: Dict, *, dish_name_contains: Optional[str] = None
    ) -> int: ...

    async def dish_stations_stats_by_station(self) -> List[Dict[str, Any]]: ...

    async def dish_stations_find(
        self,
        query: Dict,
        sort_field: str = "dish_name",
        sort_dir: int = 1,
        skip: int = 0,
        limit: int = -1,
        *,
        dish_name_contains: Optional[str] = None,
    ) -> List[Dict]: ...

    async def dish_stations_find_one(self, query: Dict) -> Optional[Dict]: ...

    async def dish_stations_aggregate(self, pipeline: List[Dict]) -> List[Dict]: ...

    async def dish_stations_insert(self, document: Dict[str, Any]) -> None: ...

    async def dish_stations_update(
        self, dish_name: str, update_fields: Dict[str, Any]
    ) -> int: ...

    async def dish_stations_delete(self, dish_name: str) -> int: ...


class ReportsPort(Protocol):
    async def compute_sales_report(
        self, start_date: str, end_date: str, station: Optional[str] = None
    ) -> Dict: ...

    async def aggregate_kds_backlog(self) -> Dict[str, Any]: ...

    async def aggregate_dashboard_extras(self) -> Dict[str, Any]: ...

    async def aggregate_table_operations(
        self, start_date: str, end_date: str
    ) -> Dict[str, Any]: ...

    async def aggregate_sales_trend(
        self,
        start_date: str,
        end_date: str,
        granularity: str = "day",
        station: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    async def aggregate_refund_stats(
        self, start_date: str, end_date: str, station: Optional[str] = None
    ) -> Dict[str, Any]: ...
