#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Explicit dish_name_contains replaces Mongo $regex in dish_stations / orders search."""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager


class DishNameContainsTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_dish_stations_contains_substring(self):
        from services.dish_catalog import DishCatalog

        catalog = DishCatalog(self.db)
        await catalog.upsert("金牌LuckIn虾饺皇", "changfen")
        await catalog.upsert("杨枝甘露", "xibing")
        rows = await self.db.dish_stations_find({}, dish_name_contains="虾饺")
        self.assertEqual([r["dish_name"] for r in rows], ["金牌LuckIn虾饺皇"])
        self.assertEqual(await self.db.dish_stations_count({}, dish_name_contains="虾饺"), 1)

    async def test_dish_stations_rejects_regex_dict(self):
        with self.assertRaises(ValueError):
            await self.db.dish_stations_find({"dish_name": {"$regex": "虾饺"}})

    async def test_search_orders_raw_contains(self):
        await self.db.save_orders([{
            "business_flow_id": "contains-1",
            "table_number": "1",
            "dish_name": "金牌LuckIn虾饺皇",
            "quantity": 1,
            "order_time": datetime(2026, 7, 1, 10, 0, tzinfo=CHINA_TZ),
            "station": "changfen",
        }])
        await self.db.save_orders([{
            "business_flow_id": "contains-2",
            "table_number": "1",
            "dish_name": "杨枝甘露",
            "quantity": 1,
            "order_time": datetime(2026, 7, 1, 11, 0, tzinfo=CHINA_TZ),
            "station": "xibing",
        }])
        rows = await self.db.search_orders_raw({}, 100, dish_name_contains="虾饺")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dish_name"], "金牌LuckIn虾饺皇")

    async def test_search_orders_raw_rejects_dish_name_in_match(self):
        with self.assertRaises(ValueError):
            await self.db.search_orders_raw({"dish_name": {"$regex": "虾饺"}}, 10)
