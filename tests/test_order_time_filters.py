#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Explicit order_time_start/end replace Mongo $gte/$lte in order queries."""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager


class OrderTimeFilterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _add(self, flow_id: str, dish: str, when: datetime):
        await self.db.save_orders([{
            "business_flow_id": flow_id,
            "table_number": "1",
            "dish_name": dish,
            "quantity": 1,
            "order_time": when,
            "station": "changfen",
        }])

    async def test_search_orders_raw_time_window(self):
        await self._add("t1", "虾饺", datetime(2026, 7, 1, 10, 0, tzinfo=CHINA_TZ))
        await self._add("t2", "虾饺", datetime(2026, 7, 2, 10, 0, tzinfo=CHINA_TZ))
        rows = await self.db.search_orders_raw(
            {},
            100,
            order_time_start=datetime(2026, 7, 1, 0, 0, tzinfo=CHINA_TZ),
            order_time_end=datetime(2026, 7, 1, 23, 59, tzinfo=CHINA_TZ),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["business_flow_id"], "t1")

    async def test_search_orders_raw_rejects_order_time_dict(self):
        with self.assertRaises(ValueError):
            await self.db.search_orders_raw(
                {"order_time": {"$gte": datetime(2026, 7, 1, tzinfo=CHINA_TZ)}},
                10,
            )

    async def test_aggregate_orders_paginated_time_window(self):
        await self._add("p1", "肠粉", datetime(2026, 7, 1, 12, 0, tzinfo=CHINA_TZ))
        await self._add("p2", "肠粉", datetime(2026, 7, 3, 12, 0, tzinfo=CHINA_TZ))
        orders, total = await self.db.aggregate_orders_paginated(
            {},
            0,
            50,
            order_time_start=datetime(2026, 7, 1, 0, 0, tzinfo=CHINA_TZ),
            order_time_end=datetime(2026, 7, 1, 23, 59, tzinfo=CHINA_TZ),
        )
        self.assertEqual(total, 1)
        self.assertEqual(orders[0]["business_flow_id"], "p1")

    async def test_aggregate_orders_paginated_rejects_order_time_dict(self):
        with self.assertRaises(ValueError):
            await self.db.aggregate_orders_paginated(
                {"order_time": {"$gte": datetime(2026, 7, 1, tzinfo=CHINA_TZ),
                                "$lte": datetime(2026, 7, 2, tzinfo=CHINA_TZ)}},
                0,
                10,
            )
