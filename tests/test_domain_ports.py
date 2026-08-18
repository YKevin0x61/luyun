#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Domain port adapters on DatabaseManager (real seams, not identity aliases)."""

import tempfile
import unittest
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from config import settings
from database import DatabaseManager
from db_core.adapters import (
    DishStationsPortAdapter,
    OrdersPortAdapter,
    ReportsPortAdapter,
)
from services.dish_catalog import DishCatalog
from services.kds_orders import complete_cooking


class DomainPortsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old
        self._tmpdir.cleanup()

    def test_port_properties_are_real_adapters(self):
        self.assertIsNot(self.db.orders, self.db)
        self.assertIsNot(self.db.dish_stations, self.db)
        self.assertIsNot(self.db.reports, self.db)
        self.assertIsInstance(self.db.orders, OrdersPortAdapter)
        self.assertIsInstance(self.db.dish_stations, DishStationsPortAdapter)
        self.assertIsInstance(self.db.reports, ReportsPortAdapter)
        # lazy singletons
        self.assertIs(self.db.orders, self.db.orders)
        self.assertIs(self.db.dish_stations, self.db.dish_stations)
        self.assertIs(self.db.reports, self.db.reports)

    def test_port_callables_exist(self):
        self.assertTrue(callable(self.db.orders.get_orders))
        self.assertTrue(callable(self.db.orders.apply_cooking_completion))
        self.assertTrue(callable(self.db.orders.apply_steamer_load))
        self.assertTrue(callable(self.db.orders.apply_steamer_move))
        self.assertTrue(callable(self.db.orders.apply_steamer_unload))
        self.assertTrue(callable(self.db.orders.apply_steamer_pluck))
        self.assertTrue(callable(self.db.orders.apply_floor_mutations))
        self.assertTrue(callable(self.db.orders.aggregate_orders_paginated))
        self.assertTrue(callable(self.db.dish_stations.dish_stations_find))
        self.assertTrue(callable(self.db.dish_stations.dish_stations_insert))
        self.assertTrue(callable(self.db.reports.compute_sales_report))
        self.assertTrue(callable(self.db.reports.aggregate_sales_trend))

    async def test_dish_stations_port_roundtrip(self):
        catalog = DishCatalog(
            dish_stations=self.db.dish_stations,
            orders=self.db.orders,
        )
        await catalog.upsert("端口测试虾饺", "changfen")
        rows = await self.db.dish_stations.dish_stations_find(
            {}, dish_name_contains="端口测试"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["station_id"], "changfen")

    async def test_complete_cooking_via_orders_port(self):
        await self.db.orders.batch_insert_orders(
            [
                {
                    "business_flow_id": "port-cook-1",
                    "table_number": "A1",
                    "dish_name": "虾饺",
                    "quantity": 2,
                    "order_time": datetime.now().isoformat(),
                    "dish_status": "待出餐",
                    "station": "changfen",
                }
            ]
        )
        result = await complete_cooking(
            self.db.orders,
            {
                "dish_name": "虾饺",
                "orders": [
                    {
                        "order_id": "",
                        "business_flow_id": "port-cook-1",
                        "table_number": "A1",
                        "complete_quantity": 2,
                    }
                ],
            },
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["updated_count"], 1)
        rows = await self.db.orders.get_orders(dish_status="已制作待上菜")
        self.assertEqual(len(rows), 1)


class _FakeOrdersPort:
    """Minimal OrdersPort stand-in for service-level unit tests."""

    def __init__(self):
        self.completions: List[Dict[str, Any]] = []
        self._order = {
            "_id": 1,
            "business_flow_id": "fake-1",
            "table_number": "T1",
            "dish_name": "虾饺",
            "quantity": 1,
            "dish_status": "待出餐",
            "station": "changfen",
            "order_time": datetime.now().isoformat(),
        }

    async def resolve_order_for_cooking(self, **kwargs: Any) -> Optional[Dict]:
        return dict(self._order)

    async def apply_cooking_completion(
        self, *, ready_time: str, completions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        self.completions.append({"ready_time": ready_time, "completions": completions})
        return {"updated_count": len(completions), "stations": ["changfen"]}


class FakeOrdersPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_cooking_accepts_fake_orders_port(self):
        fake = _FakeOrdersPort()
        result = await complete_cooking(
            fake,
            {
                "dish_name": "虾饺",
                "orders": [
                    {
                        "order_id": "1",
                        "business_flow_id": "fake-1",
                        "table_number": "T1",
                        "complete_quantity": 1,
                    }
                ],
            },
        )
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["stations"], ["changfen"])
        self.assertEqual(len(fake.completions), 1)


class _ConflictOrdersPort:
    """OrdersPort stand-in that can return mixed rows and record writes."""

    def __init__(self, catalog):
        self.catalog = catalog
        self.completions: List[Dict[str, Any]] = []

    async def resolve_order_for_cooking(self, **kwargs: Any) -> Optional[Dict]:
        oid = str(kwargs.get("order_id") or "")
        row = self.catalog.get(oid)
        return dict(row) if row else None

    async def apply_cooking_completion(
        self, *, ready_time: str, completions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        self.completions.append({"ready_time": ready_time, "completions": completions})
        return {"updated_count": len(completions), "stations": ["changfen"]}


class CookingConflictPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_cooking_conflict_does_not_apply(self):
        fake = _ConflictOrdersPort({
            "1": {
                "_id": "1",
                "table_number": "T1",
                "quantity": 1,
                "dish_status": "待出餐",
                "status": "未结",
            },
            "2": {
                "_id": "2",
                "table_number": "T2",
                "quantity": 1,
                "dish_status": "待出餐",
                "status": "退菜",
            },
        })
        with self.assertRaises(HTTPException) as raised:
            await complete_cooking(
                fake,
                {
                    "dish_name": "肠粉",
                    "orders": [
                        {
                            "order_id": "1",
                            "table_number": "T1",
                            "complete_quantity": 1,
                        },
                        {
                            "order_id": "2",
                            "table_number": "T2",
                            "complete_quantity": 1,
                        },
                    ],
                },
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(
            raised.exception.detail["conflicts"],
            [{"order_id": "2", "reason": "退菜"}],
        )
        self.assertEqual(fake.completions, [])
