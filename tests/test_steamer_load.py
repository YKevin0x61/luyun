#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上笼 writes 蒸笼位 on the 订单行; phase is derived, dish_status stays 待出餐."""

import tempfile
import unittest
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.kds_orders import derive_steamer_phase, load_steamer


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


class SteamerLoadOrdersPortTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_load_writes_placement_and_keeps_pending_dish_status(self):
        await self.db.orders.batch_insert_orders([_pending_order()])
        before = (await self.db.orders.get_orders(limit=-1))[0]
        self.assertIsNone(before.get("placement"))
        self.assertEqual(before["dish_status"], "待出餐")
        self.assertEqual(derive_steamer_phase(before), "待上笼")

        result = await load_steamer(
            self.db.orders,
            {
                "order_ids": [before["_id"]],
                "steamer_id": "1",
                "port_index": 3,
                "loaded_at": "2026-08-14T10:05:00+08:00",
            },
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["updated_count"], 1)

        after = (await self.db.orders.get_orders(limit=-1))[0]
        self.assertEqual(after["dish_status"], "待出餐")
        self.assertEqual(
            after["placement"],
            {
                "steamer_id": "1",
                "port_index": 3,
                "stack_order": 1,
                "loaded_at": "2026-08-14T10:05:00+08:00",
            },
        )
        self.assertEqual(derive_steamer_phase(after), "在蒸")
        self.assertNotIn("在蒸", (after["dish_status"],))
        self.assertNotIn("待上笼", (after["dish_status"],))

    async def test_second_cage_on_same_hole_appends_at_top(self):
        await self.db.orders.batch_insert_orders([
            _pending_order(business_flow_id="steam-001", table_number="A1"),
            _pending_order(business_flow_id="steam-002", table_number="A2"),
        ])
        rows = await self.db.orders.get_orders(limit=-1)
        by_flow = {row["business_flow_id"]: row for row in rows}

        await load_steamer(
            self.db.orders,
            {
                "order_ids": [by_flow["steam-001"]["_id"]],
                "steamer_id": "1",
                "port_index": 2,
                "loaded_at": "2026-08-14T10:05:00+08:00",
            },
        )
        await load_steamer(
            self.db.orders,
            {
                "order_ids": [by_flow["steam-002"]["_id"]],
                "steamer_id": "1",
                "port_index": 2,
                "loaded_at": "2026-08-14T10:06:00+08:00",
            },
        )

        after = {
            row["business_flow_id"]: row
            for row in await self.db.orders.get_orders(limit=-1)
        }
        self.assertEqual(after["steam-001"]["dish_status"], "待出餐")
        self.assertEqual(after["steam-002"]["dish_status"], "待出餐")
        self.assertEqual(after["steam-001"]["placement"]["stack_order"], 1)
        self.assertEqual(after["steam-002"]["placement"]["stack_order"], 2)
        self.assertEqual(after["steam-002"]["placement"]["steamer_id"], "1")
        self.assertEqual(after["steam-002"]["placement"]["port_index"], 2)
        self.assertEqual(derive_steamer_phase(after["steam-002"]), "在蒸")

    async def test_one_load_appends_ids_in_payload_order(self):
        await self.db.orders.batch_insert_orders([
            _pending_order(business_flow_id="steam-001", table_number="A1"),
            _pending_order(business_flow_id="steam-002", table_number="A2"),
        ])
        rows = await self.db.orders.get_orders(limit=-1)
        by_flow = {row["business_flow_id"]: row for row in rows}

        result = await load_steamer(
            self.db.orders,
            {
                "order_ids": [by_flow["steam-002"]["_id"], by_flow["steam-001"]["_id"]],
                "steamer_id": "1",
                "port_index": 2,
                "loaded_at": "2026-08-14T10:05:00+08:00",
            },
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["updated_count"], 2)

        after = {
            row["business_flow_id"]: row
            for row in await self.db.orders.get_orders(limit=-1)
        }
        self.assertEqual(after["steam-002"]["placement"]["stack_order"], 1)
        self.assertEqual(after["steam-001"]["placement"]["stack_order"], 2)
        self.assertEqual(derive_steamer_phase(after["steam-001"]), "在蒸")
        self.assertEqual(derive_steamer_phase(after["steam-002"]), "在蒸")


class _FakeSteamerOrdersPort:
    def __init__(self):
        self.loads: List[Dict[str, Any]] = []
        self._order = {
            "_id": "9",
            "dish_status": "待出餐",
            "station": "shulong",
            "placement": None,
        }

    async def get_order_by_id(
        self, order_id: str, dish_name: Optional[str] = None
    ) -> Optional[Dict]:
        if str(order_id) != "9":
            return None
        return dict(self._order)

    async def get_orders(self, **kwargs: Any) -> List[Dict]:
        return []

    async def apply_steamer_load(
        self,
        *,
        steamer_id: str,
        port_index: int,
        loaded_at: str,
        order_ids: List[str],
    ) -> Dict[str, Any]:
        self.loads.append(
            {
                "steamer_id": steamer_id,
                "port_index": port_index,
                "loaded_at": loaded_at,
                "order_ids": order_ids,
            }
        )
        return {"updated_count": len(order_ids), "stations": ["shulong"]}


class FakeOrdersPortSteamerLoadTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_steamer_accepts_fake_orders_port(self):
        fake = _FakeSteamerOrdersPort()
        result = await load_steamer(
            fake,
            {
                "order_ids": ["9"],
                "steamer_id": "2",
                "port_index": 1,
                "loaded_at": "2026-08-14T11:00:00+08:00",
            },
        )
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["stations"], ["shulong"])
        self.assertEqual(len(fake.loads), 1)
        self.assertEqual(fake.loads[0]["steamer_id"], "2")
        self.assertEqual(fake.loads[0]["port_index"], 1)
        self.assertEqual(fake.loads[0]["order_ids"], ["9"])
