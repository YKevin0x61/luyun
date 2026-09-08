#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""固定报表菜品的菜品库来源：须含菜品档口目录，不能误用半成品「可新建」列表。"""

import tempfile
import unittest
from datetime import datetime

from api.semi_rules import get_available_dishes
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.dish_catalog import DishCatalog


class ReportDishCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.catalog = DishCatalog(self.db)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old
        self._tmpdir.cleanup()

    async def _seed_picker_gaps(self):
        """Catalog has 虾饺 + 杨枝甘露; only 虾饺 has orders; 虾饺 also has a semi-rule."""
        await self.catalog.upsert("虾饺", "changfen")
        await self.catalog.upsert("杨枝甘露", "xibing")
        await self.db.batch_insert_orders([{
            "business_flow_id": "rd-001",
            "table_number": "A1",
            "dish_name": "虾饺",
            "quantity": 1,
            "order_time": datetime(2026, 6, 30, 10, 0, tzinfo=CHINA_TZ),
            "station": "changfen",
        }])
        await self.db.semi_rules_upsert({
            "dish_name": "虾饺",
            "semi_name": "虾滑",
            "factor": 1.0,
            "unit": "份",
        })

    async def test_semi_rules_available_drops_ruled_and_catalog_only_dishes(self):
        """Current picker source /api/semi-rules/dishes/available is not the dish catalog."""
        await self._seed_picker_gaps()
        body = await get_available_dishes(db=self.db)
        names = body["dishes"]
        self.assertNotIn("虾饺", names)
        self.assertNotIn("杨枝甘露", names)

    async def test_report_dish_catalog_includes_ruled_and_catalog_only_dishes(self):
        from api.report_dishes import list_report_dish_catalog

        await self._seed_picker_gaps()
        body = await list_report_dish_catalog(db=self.db, dish_catalog=self.catalog)
        names = body["dishes"]
        self.assertIn("虾饺", names)
        self.assertIn("杨枝甘露", names)

    async def test_report_dish_catalog_not_capped_at_500_order_names(self):
        last = "菜品0501"
        await self.catalog.upsert(last, "changfen")
        await self.db.batch_insert_orders([
            {
                "business_flow_id": f"rd-cap-{i:04d}",
                "table_number": "A1",
                "dish_name": f"菜品{i:04d}",
                "quantity": 1,
                "order_time": datetime(2026, 6, 30, 10, 0, tzinfo=CHINA_TZ),
            }
            for i in range(1, 502)
        ])

        available = (await get_available_dishes(db=self.db))["dishes"]
        self.assertEqual(len(available), 500)
        self.assertNotIn(last, available)

        from api.report_dishes import list_report_dish_catalog

        catalog_names = (await list_report_dish_catalog(db=self.db, dish_catalog=self.catalog))["dishes"]
        self.assertIn(last, catalog_names)
        self.assertGreaterEqual(len(catalog_names), 501)
