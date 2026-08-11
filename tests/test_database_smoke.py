#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight smoke tests for the multi-database manager."""

import os
import tempfile
import unittest
from datetime import datetime

import aiosqlite

from config import settings
from database import ALL_TABLES, CHINA_TZ, DatabaseManager


class DatabaseManagerSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name

        self.db = DatabaseManager()
        connected = await self.db.connect()
        self.assertTrue(connected)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_connect_creates_single_app_db_with_all_tables(self):
        app_db_path = settings.APP_DB_PATH
        self.assertTrue(os.path.exists(app_db_path), "missing app.db")

        async with self.db._conn.cursor() as cursor:
            await cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            existing = {row[0] for row in await cursor.fetchall()}

        for table in ALL_TABLES:
            db_path = settings.DATABASE_PATHS[table]
            self.assertEqual(db_path, app_db_path, f"{table} 未指向单库 app.db")
            self.assertIsNotNone(self.db.table_or_none(table), f"missing TableView for {table}")
            if table == "auth":
                # auth 表结构由多张表组成，见 db_core/schema.py
                self.assertIn("admin_user", existing)
                self.assertIn("sessions", existing)
                self.assertIn("api_tokens", existing)
            else:
                self.assertIn(table, existing)
        self.assertEqual(self.db._attached_tables, set())

    async def test_cross_table_query_works_on_single_connection(self):
        """单库架构下跨表查询直接引用表名，无需 ATTACH。"""
        self.assertEqual(self.db._attached_tables, set())
        async with self.db._conn.cursor() as cursor:
            await cursor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                ("dish_stations",),
            )
            row = await cursor.fetchone()

        self.assertIsNotNone(row)

    async def test_save_orders_deduplicates_by_business_flow_id(self):
        order = {
            "business_flow_id": "smoke-001",
            "table_number": "1",
            "dish_name": "虾饺",
            "quantity": 2,
            "order_time": datetime(2026, 5, 1, 10, 0, tzinfo=CHINA_TZ),
            "price": 12.0,
            "total_amount": 24.0,
            "status": "未结",
            "category": "点心",
            "station": "shulong",
            "priority": "normal",
        }

        self.assertTrue(await self.db.save_orders([order]))
        self.assertTrue(await self.db.save_orders([order]))

        rows = await self.db.get_orders(station="shulong", limit=-1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["business_flow_id"], "smoke-001")
        self.assertEqual(rows[0]["dish_name"], "虾饺")

    async def test_cross_table_join_orders_and_dish_stations_without_attach(self):
        """单库合并后，orders×dish_stations 可直接 JOIN，无需 ATTACH。"""
        order = {
            "business_flow_id": "join-001",
            "table_number": "8",
            "dish_name": "虾饺",
            "quantity": 3,
            "order_time": datetime(2026, 5, 2, 9, 0, tzinfo=CHINA_TZ),
            "price": 12.0,
            "total_amount": 36.0,
            "status": "未结",
            "category": "点心",
            "station": "",
            "priority": "normal",
        }
        self.assertTrue(await self.db.save_orders([order]))
        from services.dish_catalog import DishCatalog

        catalog = DishCatalog(self.db)
        await catalog.upsert("虾饺", "shulong")

        async with self.db._conn.execute(
            """
            SELECT o.dish_name, o.quantity, ds.station_id
            FROM orders o
            JOIN dish_stations ds ON ds.dish_name = o.dish_name
            WHERE o.business_flow_id = ?
            """,
            ("join-001",),
        ) as cursor:
            row = await cursor.fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["dish_name"], "虾饺")
        self.assertEqual(row["quantity"], 3)
        self.assertEqual(row["station_id"], "shulong")

    async def test_export_merged_sqlite_file_contains_core_tables(self):
        order = {
            "business_flow_id": "export-001",
            "table_number": "9",
            "dish_name": "烧卖",
            "quantity": 2,
            "order_time": datetime(2026, 5, 3, 9, 0, tzinfo=CHINA_TZ),
            "price": 10.0,
            "total_amount": 20.0,
            "status": "未结",
            "category": "点心",
            "station": "shulong",
            "priority": "normal",
        }
        self.assertTrue(await self.db.save_orders([order]))

        output_path = os.path.join(self._tmpdir.name, "merged.sqlite")
        await self.db.export_merged_sqlite_file(output_path)

        self.assertTrue(os.path.exists(output_path))
        self.assertGreater(os.path.getsize(output_path), 0)

        async with aiosqlite.connect(output_path) as export_conn:
            export_conn.row_factory = aiosqlite.Row
            async with export_conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cur:
                tables = {r[0] for r in await cur.fetchall()}
            for table in ALL_TABLES:
                if table == "auth":
                    continue
                self.assertIn(table, tables)

            async with export_conn.execute(
                "SELECT dish_name, quantity FROM orders WHERE business_flow_id = ?",
                ("export-001",),
            ) as cur:
                row = await cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["dish_name"], "烧卖")
        self.assertEqual(row["quantity"], 2)

    async def test_station_counts_and_speed_use_orders_table_connection(self):
        orders = [
            {
                "business_flow_id": "speed-001",
                "table_number": "1",
                "dish_name": "虾饺",
                "quantity": 1,
                "order_time": datetime(2026, 5, 1, 7, 30, tzinfo=CHINA_TZ),
                "price": 12.0,
                "total_amount": 12.0,
                "station": "shulong",
            },
            {
                "business_flow_id": "speed-002",
                "table_number": "2",
                "dish_name": "烧卖",
                "quantity": 1,
                "order_time": datetime(2026, 5, 1, 7, 34, tzinfo=CHINA_TZ),
                "price": 10.0,
                "total_amount": 10.0,
                "station": "shulong",
            },
            {
                "business_flow_id": "speed-003",
                "table_number": "3",
                "dish_name": "肠粉",
                "quantity": 1,
                "order_time": datetime(2026, 5, 1, 7, 35, tzinfo=CHINA_TZ),
                "price": 9.0,
                "total_amount": 9.0,
                "station": "changfen",
            },
            {
                "business_flow_id": "speed-004",
                "table_number": "4",
                "dish_name": "楼面项目",
                "quantity": 1,
                "order_time": datetime(2026, 5, 1, 7, 35, tzinfo=CHINA_TZ),
                "price": 1.0,
                "total_amount": 1.0,
                "station": "loumian",
            },
        ]

        self.assertTrue(await self.db.save_orders(orders))

        counts = await self.db.aggregate_station_counts(datetime(2026, 5, 1, tzinfo=CHINA_TZ))
        self.assertEqual(
            {item["station_id"]: item["count"] for item in counts},
            {"shulong": 2, "changfen": 1, "loumian": 1},
        )

        speed = await self.db.aggregate_station_speed(datetime(2026, 5, 1, tzinfo=CHINA_TZ))
        self.assertEqual(speed["date"], "2026-05-01")
        self.assertIn("shulong", speed["stations"])
        self.assertIn("changfen", speed["stations"])
        self.assertNotIn("loumian", speed["stations"])

        by_name = {item["name"]: item["data"] for item in speed["series"]}
        self.assertEqual(by_name["shulong"][6], 2)
        self.assertEqual(by_name["changfen"][7], 1)

    async def test_aggregate_hot_dishes_excludes_loumian_by_default(self):
        today = datetime.now(CHINA_TZ).replace(hour=12, minute=0, second=0, microsecond=0)
        orders = [
            {
                "business_flow_id": "hot-001",
                "table_number": "1",
                "dish_name": "楼面茶位",
                "quantity": 99,
                "order_time": today,
                "price": 5.0,
                "total_amount": 495.0,
                "station": "loumian",
            },
            {
                "business_flow_id": "hot-002",
                "table_number": "2",
                "dish_name": "虾饺",
                "quantity": 10,
                "order_time": today,
                "price": 12.0,
                "total_amount": 120.0,
                "station": "shulong",
            },
            {
                "business_flow_id": "hot-003",
                "table_number": "3",
                "dish_name": "肠粉",
                "quantity": 8,
                "order_time": today,
                "price": 9.0,
                "total_amount": 72.0,
                "station": "changfen",
            },
        ]
        self.assertTrue(await self.db.save_orders(orders))

        hot_dishes = await self.db.aggregate_hot_dishes(limit_n=10)
        stations = {item["station"] for item in hot_dishes}
        dish_names = [item["dish_name"] for item in hot_dishes]

        self.assertNotIn("loumian", stations)
        self.assertNotIn("楼面茶位", dish_names)
        self.assertEqual(hot_dishes[0]["dish_name"], "虾饺")

    async def test_dish_station_stats_use_table_connection(self):
        from services.dish_catalog import DishCatalog

        catalog = DishCatalog(self.db)
        await catalog.upsert_many([
            {"dish_name": "虾饺", "station_id": "shulong"},
            {"dish_name": "烧卖", "station_id": "shulong"},
            {"dish_name": "肠粉", "station_id": "changfen"},
        ])
        stats = await self.db.dish_stations_stats_by_station()
        self.assertEqual(
            {item["station_id"]: item["count"] for item in stats},
            {"shulong": 2, "changfen": 1},
        )


if __name__ == "__main__":
    unittest.main()
