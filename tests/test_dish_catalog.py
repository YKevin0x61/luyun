#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DishCatalog（菜品→档口映射的惰性缓存 + 派生查询）的测试。"""

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.dish_catalog import DishCatalog


class DishCatalogTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name

        self.db = DatabaseManager()
        connected = await self.db.connect()
        self.assertTrue(connected)
        self.catalog = DishCatalog(self.db)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _add_mapping(self, dish_name, station_id):
        await self.catalog.upsert(dish_name, station_id)

    async def _add_order(self, dish_name, order_time):
        await self.db.save_orders([{
            "business_flow_id": f"TEST_{dish_name}_{order_time.isoformat()}",
            "table_number": "1",
            "dish_name": dish_name,
            "quantity": 1,
            "order_time": order_time,
        }])


class ResolveTest(DishCatalogTestBase):
    async def test_resolve_returns_mapped_station(self):
        await self._add_mapping("金牌LuckIn虾饺皇", "changfen")
        self.assertEqual(await self.catalog.resolve("金牌LuckIn虾饺皇"), "changfen")

    async def test_resolve_returns_empty_for_unmapped_dish(self):
        await self._add_mapping("金牌LuckIn虾饺皇", "changfen")
        self.assertEqual(await self.catalog.resolve("从未见过的菜"), "")

    async def test_resolve_returns_empty_for_empty_input(self):
        self.assertEqual(await self.catalog.resolve(""), "")
        self.assertEqual(await self.catalog.resolve(None), "")

    async def test_resolve_strips_whitespace(self):
        await self._add_mapping("杨枝甘露", "xibing")
        self.assertEqual(await self.catalog.resolve("  杨枝甘露  "), "xibing")

    async def test_concurrent_resolve_loads_cache_once(self):
        """并发首次 resolve() 只应触发一次数据库加载（asyncio.Lock 去重）。"""
        # Seed via raw SQL so catalog cache is cold for the first resolve.
        tdb = self.db.table("dish_stations")
        now = datetime.now(CHINA_TZ).isoformat()
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """INSERT INTO dish_stations (dish_name, station_id, notes, created_at, updated_at)
                   VALUES (?, ?, NULL, ?, ?)""",
                ("虾饺", "changfen", now, now),
            )
        await tdb.commit()

        load_calls = 0
        original_find = self.db.dish_stations_find

        async def counting_find(*args, **kwargs):
            nonlocal load_calls
            load_calls += 1
            await asyncio.sleep(0)  # 让其它协程有机会在加锁前抢跑
            return await original_find(*args, **kwargs)

        self.db.dish_stations_find = counting_find

        results = await asyncio.gather(*(self.catalog.resolve("虾饺") for _ in range(10)))
        self.assertTrue(all(r == "changfen" for r in results))
        self.assertEqual(load_calls, 1)


class InvalidateTest(DishCatalogTestBase):
    async def test_invalidate_forces_reload_on_next_resolve(self):
        await self._add_mapping("布拉肠", "changfen")
        self.assertEqual(await self.catalog.resolve("布拉肠"), "changfen")

        # 外源写库（模拟 .db 导入），缓存未失效前应仍返回旧值
        tdb = self.db.table("dish_stations")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE dish_stations SET station_id = ? WHERE dish_name = ?",
                ("shulong", "布拉肠"),
            )
        await tdb.commit()
        self.assertEqual(await self.catalog.resolve("布拉肠"), "changfen")

        self.catalog.invalidate()
        self.assertEqual(await self.catalog.resolve("布拉肠"), "shulong")


class UpsertTest(DishCatalogTestBase):
    async def test_upsert_creates_and_updates(self):
        created = await self.catalog.upsert("虾饺", "changfen")
        self.assertTrue(created["created"])
        self.assertEqual(await self.catalog.resolve("虾饺"), "changfen")

        updated = await self.catalog.upsert("虾饺", "shulong", notes="改档")
        self.assertFalse(updated["created"])
        self.assertEqual(await self.catalog.resolve("虾饺"), "shulong")
        row = await self.catalog.get("虾饺")
        self.assertEqual(row["notes"], "改档")

    async def test_upsert_rejects_invalid_station(self):
        from services.dish_catalog import InvalidStationError

        with self.assertRaises(InvalidStationError):
            await self.catalog.upsert("虾饺", "not_a_station")

    async def test_upsert_many_merges_without_wiping(self):
        await self.catalog.upsert("保留菜", "changfen")
        result = await self.catalog.upsert_many([
            {"dish_name": "新菜", "station_id": "xibing"},
        ])
        self.assertEqual(result["upserted"], 1)
        self.assertEqual(await self.catalog.resolve("保留菜"), "changfen")
        self.assertEqual(await self.catalog.resolve("新菜"), "xibing")

    async def test_remove_deletes_mapping(self):
        await self.catalog.upsert("虾饺", "changfen")
        self.assertTrue(await self.catalog.remove("虾饺"))
        self.assertEqual(await self.catalog.resolve("虾饺"), "")
        self.assertFalse(await self.catalog.remove("虾饺"))


class SyncOrdersSinceTest(DishCatalogTestBase):
    async def test_updates_only_mapped_orders_at_or_after_cutoff(self):
        await self._add_mapping("虾饺", "changfen")
        cutoff = datetime.now(CHINA_TZ).replace(microsecond=0)

        await self._add_order("虾饺", cutoff + timedelta(minutes=1))       # 映射命中，在范围内
        await self._add_order("未分类菜品", cutoff + timedelta(minutes=2))  # 无映射
        await self._add_order("虾饺", cutoff - timedelta(minutes=1))       # 命中但早于 cutoff

        result = await self.catalog.sync_orders_since(cutoff)

        self.assertTrue(result["success"])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["total_mappings"], 1)

        async with self.db.table("orders").conn.cursor() as cursor:
            await cursor.execute(
                "SELECT dish_name, station, order_time FROM orders ORDER BY order_time"
            )
            rows = await cursor.fetchall()

        by_dish_and_time = {(r["dish_name"], r["order_time"]): r["station"] for r in rows}
        self.assertEqual(
            by_dish_and_time[("虾饺", (cutoff + timedelta(minutes=1)).isoformat())], "changfen"
        )
        self.assertEqual(
            by_dish_and_time[("未分类菜品", (cutoff + timedelta(minutes=2)).isoformat())], ""
        )
        self.assertEqual(
            by_dish_and_time[("虾饺", (cutoff - timedelta(minutes=1)).isoformat())], ""
        )

    async def test_empty_mapping_table_short_circuits(self):
        cutoff = datetime.now(CHINA_TZ)
        result = await self.catalog.sync_orders_since(cutoff)
        self.assertEqual(result, {
            "success": True, "updated": 0, "skipped": 0,
            "total_mappings": 0, "message": "映射表为空",
        })


class UnmappedDishesTest(DishCatalogTestBase):
    async def test_lists_order_dishes_without_a_mapping(self):
        await self._add_mapping("虾饺", "changfen")
        now = datetime.now(CHINA_TZ)
        await self._add_order("虾饺", now)
        await self._add_order("新品试做菜", now)

        result = await self.catalog.unmapped_dishes()

        self.assertTrue(result["success"])
        self.assertEqual(result["dishes"], ["新品试做菜"])
        self.assertEqual(result["existing_mappings"], ["虾饺"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["existing_count"], 1)

    async def test_reflects_invalidated_cache(self):
        now = datetime.now(CHINA_TZ)
        await self._add_order("新品试做菜", now)

        result = await self.catalog.unmapped_dishes()
        self.assertEqual(result["dishes"], ["新品试做菜"])

        await self._add_mapping("新品试做菜", "changfen")
        self.catalog.invalidate()

        result = await self.catalog.unmapped_dishes()
        self.assertEqual(result["dishes"], [])


if __name__ == "__main__":
    unittest.main()
