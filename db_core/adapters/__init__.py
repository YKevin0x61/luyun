#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real domain-port adapters (not identity aliases of DatabaseManager)."""

from db_core.adapters.orders import OrdersPortAdapter
from db_core.adapters.dish_stations import DishStationsPortAdapter
from db_core.adapters.reports import ReportsPortAdapter

__all__ = [
    "OrdersPortAdapter",
    "DishStationsPortAdapter",
    "ReportsPortAdapter",
]
