#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ReportsPort adapter — delegates to DatabaseManager mixin methods."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class ReportsPortAdapter:
    __slots__ = ("_db",)

    def __init__(self, db: Any) -> None:
        self._db = db

    async def compute_sales_report(
        self, start_date: str, end_date: str, station: Optional[str] = None
    ) -> Dict:
        return await self._db.compute_sales_report(start_date, end_date, station=station)

    async def aggregate_kds_backlog(self) -> Dict[str, Any]:
        return await self._db.aggregate_kds_backlog()

    async def aggregate_dashboard_extras(self) -> Dict[str, Any]:
        return await self._db.aggregate_dashboard_extras()

    async def aggregate_table_operations(
        self, start_date: str, end_date: str
    ) -> Dict[str, Any]:
        return await self._db.aggregate_table_operations(start_date, end_date)

    async def aggregate_sales_trend(
        self,
        start_date: str,
        end_date: str,
        granularity: str = "day",
        station: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return await self._db.aggregate_sales_trend(
            start_date, end_date, granularity=granularity, station=station
        )

    async def aggregate_refund_stats(
        self, start_date: str, end_date: str, station: Optional[str] = None
    ) -> Dict[str, Any]:
        return await self._db.aggregate_refund_stats(
            start_date, end_date, station=station
        )
