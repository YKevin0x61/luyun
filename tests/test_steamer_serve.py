#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""笼上出餐: complete_cooking clears 蒸笼位 and compacts the hole."""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.kds_orders import complete_cooking, derive_steamer_phase, load_steamer


def _pending_order(**overrides):
    row = {
        "business_flow_id": "steam-001",
        "table_number": "A1",
        "dish_name": "虾饺",
        "quantity": 1,
        "order_time": datetime(2026, 8, 14, 10, 0, tzinfo=CHINA_TZ),
        "station": "shulong",
        "status": "未结",
        "dish_status": "待出餐",
    }
    row.update(overrides)
    return row


async def _load_on_hole(orders, order_id, *, steamer_id="1", port_index=3, loaded_at=None):
    return await load_steamer(
        orders,
        {
            "order_ids": [order_id],
            "steamer_id": steamer_id,
            "port_index": port_index,
            "loaded_at": loaded_at or "2026-08-14T10:05:00+08:00",
        },
    )


class SteamerServeOrdersPortTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_serving_selected_line_clears_placement_and_compacts_hole(self):
        await self.db.orders.batch_insert_orders([
            _pending_order(business_flow_id="steam-001", table_number="A1"),
            _pending_order(business_flow_id="steam-002", table_number="A2"),
        ])
        rows = await self.db.orders.get_orders(limit=-1)
        by_flow = {row["business_flow_id"]: row for row in rows}

        await _load_on_hole(self.db.orders, by_flow["steam-001"]["_id"])
        await _load_on_hole(
            self.db.orders,
            by_flow["steam-002"]["_id"],
            loaded_at="2026-08-14T10:06:00+08:00",
        )

        served_id = by_flow["steam-001"]["_id"]
        result = await complete_cooking(
            self.db.orders,
            {
                "dish_name": "虾饺",
                "orders": [
                    {
                        "order_id": served_id,
                        "business_flow_id": "steam-001",
                        "table_number": "A1",
                        "complete_quantity": 1,
                    }
                ],
            },
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["updated_count"], 1)

        after = {
            row["business_flow_id"]: row
            for row in await self.db.orders.get_orders(limit=-1)
        }
        served = after["steam-001"]
        remaining = after["steam-002"]

        self.assertEqual(served["dish_status"], "已制作待上菜")
        self.assertIsNone(served.get("placement"))
        self.assertIsNone(derive_steamer_phase(served))

        self.assertEqual(remaining["dish_status"], "待出餐")
        self.assertEqual(
            remaining["placement"],
            {
                "steamer_id": "1",
                "port_index": 3,
                "stack_order": 1,
                "loaded_at": "2026-08-14T10:06:00+08:00",
            },
        )
        self.assertEqual(derive_steamer_phase(remaining), "在蒸")

    async def test_serving_middle_cage_preserves_physical_stack_order(self):
        await self.db.orders.batch_insert_orders([
            _pending_order(business_flow_id="steam-001", table_number="A1"),
            _pending_order(business_flow_id="steam-002", table_number="A2"),
            _pending_order(business_flow_id="steam-003", table_number="A3"),
        ])
        rows = await self.db.orders.get_orders(limit=-1)
        by_flow = {row["business_flow_id"]: row for row in rows}

        await _load_on_hole(self.db.orders, by_flow["steam-001"]["_id"])
        await _load_on_hole(
            self.db.orders,
            by_flow["steam-002"]["_id"],
            loaded_at="2026-08-14T10:06:00+08:00",
        )
        await _load_on_hole(
            self.db.orders,
            by_flow["steam-003"]["_id"],
            loaded_at="2026-08-14T10:07:00+08:00",
        )

        await complete_cooking(
            self.db.orders,
            {
                "dish_name": "虾饺",
                "orders": [
                    {
                        "order_id": by_flow["steam-002"]["_id"],
                        "table_number": "A2",
                        "complete_quantity": 1,
                    }
                ],
            },
        )

        after = {
            row["business_flow_id"]: row
            for row in await self.db.orders.get_orders(limit=-1)
        }
        self.assertEqual(after["steam-002"]["dish_status"], "已制作待上菜")
        self.assertIsNone(after["steam-002"].get("placement"))
        self.assertEqual(after["steam-001"]["placement"]["stack_order"], 1)
        self.assertEqual(after["steam-003"]["placement"]["stack_order"], 2)
        self.assertEqual(after["steam-001"]["placement"]["loaded_at"], "2026-08-14T10:05:00+08:00")
        self.assertEqual(after["steam-003"]["placement"]["loaded_at"], "2026-08-14T10:07:00+08:00")
        self.assertEqual(derive_steamer_phase(after["steam-001"]), "在蒸")
        self.assertEqual(derive_steamer_phase(after["steam-003"]), "在蒸")

    async def test_partial_split_keeps_placement_on_remaining_pending_row(self):
        await self.db.orders.batch_insert_orders([
            _pending_order(business_flow_id="steam-qty", table_number="A1", quantity=2),
        ])
        before = (await self.db.orders.get_orders(limit=-1))[0]
        await _load_on_hole(self.db.orders, before["_id"])

        await complete_cooking(
            self.db.orders,
            {
                "dish_name": "虾饺",
                "orders": [
                    {
                        "order_id": before["_id"],
                        "table_number": "A1",
                        "complete_quantity": 1,
                    }
                ],
            },
        )

        rows = await self.db.orders.get_orders(limit=-1)
        pending = [row for row in rows if row["dish_status"] == "待出餐"]
        ready = [row for row in rows if row["dish_status"] == "已制作待上菜"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(len(ready), 1)
        self.assertEqual(pending[0]["quantity"], 1)
        self.assertEqual(
            pending[0]["placement"],
            {
                "steamer_id": "1",
                "port_index": 3,
                "stack_order": 1,
                "loaded_at": "2026-08-14T10:05:00+08:00",
            },
        )
        self.assertEqual(derive_steamer_phase(pending[0]), "在蒸")
        self.assertEqual(ready[0]["quantity"], 1)
        self.assertIsNone(ready[0].get("placement"))
        self.assertIsNone(derive_steamer_phase(ready[0]))

    async def test_serving_never_loaded_pending_stays_off_hole(self):
        await self.db.orders.batch_insert_orders([_pending_order()])
        before = (await self.db.orders.get_orders(limit=-1))[0]
        self.assertIsNone(before.get("placement"))
        self.assertEqual(derive_steamer_phase(before), "待上笼")

        result = await complete_cooking(
            self.db.orders,
            {
                "dish_name": "虾饺",
                "orders": [
                    {
                        "order_id": before["_id"],
                        "business_flow_id": "steam-001",
                        "table_number": "A1",
                        "complete_quantity": 1,
                    }
                ],
            },
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["updated_count"], 1)

        after = (await self.db.orders.get_orders(limit=-1))[0]
        self.assertEqual(after["dish_status"], "已制作待上菜")
        self.assertIsNone(after.get("placement"))
        self.assertIsNone(derive_steamer_phase(after))
