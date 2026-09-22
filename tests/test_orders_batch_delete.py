#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_delete_orders 既支持主键 id 也支持 business_flow_id。

回归背景（DATA-01）：原实现用一条 `DELETE ... WHERE id = ? OR business_flow_id = ?`
把同一个 oid 同时绑给 bigint 主键和文本列。PostgreSQL 是强类型，$1 按 bigint 解析，
传业务号时 asyncpg 直接抛 DataError，`OR business_flow_id = ?` 永远走不到，异常被
外层 except 吞成 `{"success": False}`——记录还在，上层却以为删除失败/成功口径不一致。
"""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager

FLOW_ID = "YY01101-X_虾饺_001"


def _order(flow_id: str, dish: str = "虾饺") -> dict:
    return {
        "business_flow_id": flow_id,
        "table_number": "1",
        "dish_name": dish,
        "quantity": 1,
        "order_time": datetime(2026, 7, 20, 11, 0, tzinfo=CHINA_TZ),
        "station": "changfen",
    }


class BatchDeleteOrdersTest(unittest.IsolatedAsyncioTestCase):
    """真实测试库：按 business_flow_id / 主键删除的返回契约。"""

    async def asyncSetUp(self):
        self._old_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_dir
        self._tmpdir.cleanup()

    async def _insert(self, flow_id: str, dish: str = "虾饺") -> dict:
        result = await self.db.orders.batch_insert_orders([_order(flow_id, dish)])
        self.assertTrue(result["success"], result)
        self.assertEqual(result["inserted_count"], 1)
        return result

    async def _rows(self):
        return await self.db.orders.search_orders_raw({}, 100)

    async def test_delete_by_business_flow_id(self):
        await self._insert(FLOW_ID)
        self.assertEqual(len(await self._rows()), 1)

        result = await self.db.orders.batch_delete_orders([FLOW_ID])

        self.assertTrue(result["success"], result)
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(await self._rows(), [])

    async def test_delete_by_numeric_primary_key_still_works(self):
        await self._insert(FLOW_ID)
        rows = await self._rows()
        primary_key = str(rows[0]["_id"])

        result = await self.db.orders.batch_delete_orders([primary_key])

        self.assertTrue(result["success"], result)
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(await self._rows(), [])

    async def test_delete_unknown_business_flow_id_is_noop(self):
        await self._insert(FLOW_ID)

        result = await self.db.orders.batch_delete_orders(["YY01101-X_虾饺_999"])

        self.assertTrue(result["success"], result)
        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(len(await self._rows()), 1)

    async def test_delete_by_business_flow_id_with_dish_name(self):
        await self.db.orders.batch_insert_orders([_order(FLOW_ID, "虾饺"), _order(FLOW_ID, "凤爪")])

        result = await self.db.orders.batch_delete_orders([FLOW_ID], dish_name="虾饺")

        self.assertTrue(result["success"], result)
        self.assertEqual(result["deleted_count"], 1)
        remaining = await self._rows()
        self.assertEqual([row["dish_name"] for row in remaining], ["凤爪"])
