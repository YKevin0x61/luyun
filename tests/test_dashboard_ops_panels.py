#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard kds_backlog aggregation and table live list."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from config import settings
from database import CHINA_TZ, DatabaseManager


class DashboardOpsPanelsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.now = datetime.now(CHINA_TZ)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _insert_order(self, **kwargs):
        tdb = self.db.table("orders")
        now_iso = self.now.isoformat()
        await tdb.execute(
            """INSERT INTO orders
               (business_flow_id, table_number, dish_name, quantity, order_time,
                price, total_amount, status, category, station, dish_status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kwargs.get("business_flow_id", "flow-001"),
                kwargs.get("table_number", "A1"),
                kwargs.get("dish_name", "虾饺"),
                kwargs.get("quantity", 1),
                kwargs.get("order_time", (self.now - timedelta(minutes=5)).isoformat()),
                10.0,
                10.0,
                kwargs.get("status", "未结"),
                "点心",
                kwargs.get("station", "dimsum"),
                kwargs.get("dish_status", "待出餐"),
                now_iso,
                now_iso,
            ),
        )
        await tdb.commit()

    async def test_aggregate_kds_backlog_groups_pending_by_station(self):
        await self._insert_order(
            business_flow_id="pending-1",
            station="dimsum",
            order_time=(self.now - timedelta(minutes=5)).isoformat(),
        )
        await self._insert_order(
            business_flow_id="pending-2",
            station="dimsum",
            order_time=(self.now - timedelta(minutes=45)).isoformat(),
        )
        await self._insert_order(
            business_flow_id="pending-3",
            station="wok",
            order_time=(self.now - timedelta(minutes=3)).isoformat(),
        )
        await self._insert_order(
            business_flow_id="done-1",
            station="wok",
            dish_status="已制作待上菜",
            order_time=(self.now - timedelta(minutes=30)).isoformat(),
        )

        backlog = await self.db.aggregate_kds_backlog()

        self.assertEqual(backlog["total_pending"], 3)
        self.assertEqual(backlog["overdue_count"], 1)
        self.assertEqual(backlog["busiest_station"]["station_id"], "dimsum")
        self.assertEqual(backlog["busiest_station"]["pending"], 2)
        dimsum = next(s for s in backlog["stations"] if s["station_id"] == "dimsum")
        self.assertEqual(dimsum["pending"], 2)
        self.assertEqual(dimsum["overdue"], 1)
        self.assertEqual(dimsum["load_level"], "low")
        self.assertGreaterEqual(dimsum["oldest_wait_minutes"], 20.0)

    async def test_aggregate_kds_backlog_load_levels(self):
        tdb = self.db.table("orders")
        now_iso = self.now.isoformat()
        for idx in range(8):
            await tdb.execute(
                """INSERT INTO orders
                   (business_flow_id, table_number, dish_name, quantity, order_time,
                    price, total_amount, status, category, station, dish_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"medium-{idx}",
                    "B1",
                    "肠粉",
                    1,
                    (self.now - timedelta(minutes=1)).isoformat(),
                    10.0,
                    10.0,
                    "未结",
                    "点心",
                    "changfen",
                    "待出餐",
                    now_iso,
                    now_iso,
                ),
            )
        await tdb.commit()

        backlog = await self.db.aggregate_kds_backlog()
        changfen = next(s for s in backlog["stations"] if s["station_id"] == "changfen")
        self.assertEqual(changfen["pending"], 8)
        self.assertEqual(changfen["load_level"], "medium")

    async def test_get_table_live_list_returns_occupied_sorted_by_duration(self):
        tables = [
            {"table_number": "T1", "amount": 120.0, "people": 2, "duration": 1800},
            {"table_number": "T2", "amount": 80.0, "people": 3, "duration": 3600},
            {"table_number": "T3", "amount": 0.0, "people": 0, "duration": 0},
        ]
        await self.db.save_table_data(tables)

        live = await self.db.get_table_live_list()

        self.assertEqual(live["total_occupied"], 2)
        self.assertEqual([row["table_number"] for row in live["tables"]], ["T2", "T1"])
        self.assertEqual(live["tables"][0]["duration_minutes"], 60.0)
        self.assertEqual(live["tables"][1]["amount"], 120.0)

    async def test_aggregate_kds_backlog_excludes_loumian_and_yesterday(self):
        await self._insert_order(
            business_flow_id="kitchen-today",
            station="shulong",
            order_time=(self.now - timedelta(minutes=5)).isoformat(),
        )
        await self._insert_order(
            business_flow_id="loumian-today",
            station="loumian",
            order_time=(self.now - timedelta(minutes=5)).isoformat(),
        )
        await self._insert_order(
            business_flow_id="kitchen-yesterday",
            station="shulong",
            order_time=(self.now - timedelta(days=1)).isoformat(),
        )

        backlog = await self.db.aggregate_kds_backlog()

        self.assertEqual(backlog["total_pending"], 1)
        self.assertEqual(backlog["busiest_station"]["station_id"], "shulong")
        station_ids = {s["station_id"] for s in backlog["stations"]}
        self.assertNotIn("loumian", station_ids)


if __name__ == "__main__":
    unittest.main()
