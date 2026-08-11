#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DishStationsPort adapter — delegates to DatabaseManager mixin methods."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class DishStationsPortAdapter:
    __slots__ = ("_db",)

    def __init__(self, db: Any) -> None:
        self._db = db

    async def dish_stations_count(
        self, query: Dict, *, dish_name_contains: Optional[str] = None
    ) -> int:
        return await self._db.dish_stations_count(
            query, dish_name_contains=dish_name_contains
        )

    async def dish_stations_stats_by_station(self) -> List[Dict[str, Any]]:
        return await self._db.dish_stations_stats_by_station()

    async def dish_stations_find(
        self,
        query: Dict,
        sort_field: str = "dish_name",
        sort_dir: int = 1,
        skip: int = 0,
        limit: int = -1,
        *,
        dish_name_contains: Optional[str] = None,
    ) -> List[Dict]:
        return await self._db.dish_stations_find(
            query,
            sort_field=sort_field,
            sort_dir=sort_dir,
            skip=skip,
            limit=limit,
            dish_name_contains=dish_name_contains,
        )

    async def dish_stations_find_one(self, query: Dict) -> Optional[Dict]:
        return await self._db.dish_stations_find_one(query)

    async def dish_stations_aggregate(self, pipeline: List[Dict]) -> List[Dict]:
        return await self._db.dish_stations_aggregate(pipeline)

    async def dish_stations_insert(self, document: Dict[str, Any]) -> None:
        return await self._db.dish_stations_insert(document)

    async def dish_stations_update(
        self, dish_name: str, update_fields: Dict[str, Any]
    ) -> int:
        return await self._db.dish_stations_update(dish_name, update_fields)

    async def dish_stations_delete(self, dish_name: str) -> int:
        return await self._db.dish_stations_delete(dish_name)
