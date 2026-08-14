#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""换孔 / 下笼 / 蒸孔容量: OrdersPort writes; phase stays derived."""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from fastapi import HTTPException

from services.kds_orders import (
    derive_steamer_phase,
    load_steamer,
    move_steamer,
    unload_steamer,
)


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


async def _load(orders, order_id, *, steamer_id="1", port_index=3, loaded_at=None):
    return await load_steamer(
        orders,
        {
            "order_ids": [order_id],
            "steamer_id": steamer_id,
            "port_index": port_index,
            "loaded_at": loaded_at or "2026-08-14T10:05:00+08:00",
        },
    )


class SteamerMoveOrdersPortTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_move_puts_cage_on_dest_top_and_compacts_source(self):
        await self.db.orders.batch_insert_orders([
            _pending_order(business_flow_id="steam-001", table_number="A1"),
            _pending_order(business_flow_id="steam-002", table_number="A2"),
            _pending_order(business_flow_id="steam-003", table_number="A3"),
        ])
        rows = await self.db.orders.get_orders(limit=-1)
        by_flow = {row["business_flow_id"]: row for row in rows}

        await _load(self.db.orders, by_flow["steam-001"]["_id"], port_index=1)
        await _load(
            self.db.orders,
            by_flow["steam-002"]["_id"],
            port_index=1,
            loaded_at="2026-08-14T10:06:00+08:00",
        )
        await _load(
            self.db.orders,
            by_flow["steam-003"]["_id"],
            port_index=2,
            loaded_at="2026-08-14T10:07:00+08:00",
        )

        result = await move_steamer(
            self.db.orders,
            {
                "order_ids": [by_flow["steam-001"]["_id"]],
                "steamer_id": "1",
                "port_index": 2,
            },
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["updated_count"], 1)

        after = {
            row["business_flow_id"]: row
            for row in await self.db.orders.get_orders(limit=-1)
        }
        moved = after["steam-001"]
        leftover = after["steam-002"]
        dest_bottom = after["steam-003"]

        self.assertEqual(moved["dish_status"], "待出餐")
        self.assertEqual(derive_steamer_phase(moved), "在蒸")
        self.assertEqual(moved["placement"]["steamer_id"], "1")
        self.assertEqual(moved["placement"]["port_index"], 2)
        self.assertEqual(moved["placement"]["stack_order"], 2)
        self.assertEqual(moved["placement"]["loaded_at"], "2026-08-14T10:05:00+08:00")

        self.assertEqual(leftover["placement"]["port_index"], 1)
        self.assertEqual(leftover["placement"]["stack_order"], 1)
        self.assertEqual(leftover["dish_status"], "待出餐")

        self.assertEqual(dest_bottom["placement"]["stack_order"], 1)
        self.assertEqual(dest_bottom["placement"]["port_index"], 2)

    async def test_move_onto_same_hole_is_noop(self):
        await self.db.orders.batch_insert_orders([
            _pending_order(business_flow_id="steam-001", table_number="A1"),
        ])
        row = (await self.db.orders.get_orders(limit=-1))[0]
        await _load(self.db.orders, row["_id"], port_index=3)
        before = (await self.db.orders.get_orders(limit=-1))[0]

        result = await move_steamer(
            self.db.orders,
            {
                "order_ids": [row["_id"]],
                "steamer_id": "1",
                "port_index": 3,
            },
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(result["stations"], [])

        after = (await self.db.orders.get_orders(limit=-1))[0]
        self.assertEqual(after["placement"], before["placement"])
        self.assertEqual(after["dish_status"], "待出餐")


class SteamerUnloadOrdersPortTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_unload_clears_placement_and_returns_to_awaiting(self):
        await self.db.orders.batch_insert_orders([
            _pending_order(business_flow_id="steam-001", table_number="A1"),
            _pending_order(business_flow_id="steam-002", table_number="A2"),
        ])
        rows = await self.db.orders.get_orders(limit=-1)
        by_flow = {row["business_flow_id"]: row for row in rows}

        await _load(self.db.orders, by_flow["steam-001"]["_id"], port_index=3)
        await _load(
            self.db.orders,
            by_flow["steam-002"]["_id"],
            port_index=3,
            loaded_at="2026-08-14T10:06:00+08:00",
        )

        result = await unload_steamer(
            self.db.orders,
            {"order_ids": [by_flow["steam-001"]["_id"]]},
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["updated_count"], 1)

        after = {
            row["business_flow_id"]: row
            for row in await self.db.orders.get_orders(limit=-1)
        }
        unloaded = after["steam-001"]
        remaining = after["steam-002"]

        self.assertEqual(unloaded["dish_status"], "待出餐")
        self.assertIsNone(unloaded.get("placement"))
        self.assertEqual(derive_steamer_phase(unloaded), "待上笼")

        self.assertEqual(remaining["dish_status"], "待出餐")
        self.assertEqual(derive_steamer_phase(remaining), "在蒸")
        self.assertEqual(
            remaining["placement"],
            {
                "steamer_id": "1",
                "port_index": 3,
                "stack_order": 1,
                "loaded_at": "2026-08-14T10:06:00+08:00",
            },
        )


def _port_capacity():
    return settings.KITCHEN_STATIONS["shulong"]["steamer_layout"]["port_capacity"]


class SteamerCapacityOrdersPortTests(unittest.IsolatedAsyncioTestCase):
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

    async def _fill_hole(self, *, steamer_id="1", port_index=4, count=None, flow_prefix="cap"):
        n = _port_capacity() if count is None else count
        await self.db.orders.batch_insert_orders([
            _pending_order(
                business_flow_id=f"{flow_prefix}-{i:02d}",
                table_number=f"T{i}",
            )
            for i in range(n)
        ])
        rows = await self.db.orders.get_orders(limit=-1)
        ids = [
            row["_id"]
            for row in rows
            if str(row["business_flow_id"]).startswith(f"{flow_prefix}-")
        ]
        await load_steamer(
            self.db.orders,
            {
                "order_ids": ids,
                "steamer_id": steamer_id,
                "port_index": port_index,
                "loaded_at": "2026-08-14T10:05:00+08:00",
            },
        )
        return ids

    async def test_load_rejects_when_occupied_plus_incoming_exceeds_capacity(self):
        await self._fill_hole()
        await self.db.orders.batch_insert_orders([
            _pending_order(business_flow_id="incoming-01", table_number="Z1"),
        ])
        incoming = next(
            row for row in await self.db.orders.get_orders(limit=-1)
            if row["business_flow_id"] == "incoming-01"
        )
        before = {
            row["_id"]: row.get("placement")
            for row in await self.db.orders.get_orders(limit=-1)
        }

        with self.assertRaises(HTTPException) as raised:
            await load_steamer(
                self.db.orders,
                {
                    "order_ids": [incoming["_id"]],
                    "steamer_id": "1",
                    "port_index": 4,
                },
            )
        self.assertEqual(raised.exception.status_code, 409)

        after = {
            row["_id"]: row.get("placement")
            for row in await self.db.orders.get_orders(limit=-1)
        }
        self.assertEqual(after, before)
        self.assertIsNone(after[incoming["_id"]])

    async def test_move_rejects_when_dest_already_at_capacity(self):
        await self._fill_hole()
        await self.db.orders.batch_insert_orders([
            _pending_order(business_flow_id="mover-01", table_number="M1"),
        ])
        mover = next(
            row for row in await self.db.orders.get_orders(limit=-1)
            if row["business_flow_id"] == "mover-01"
        )
        await _load(self.db.orders, mover["_id"], port_index=1)
        before = {
            row["business_flow_id"]: row.get("placement")
            for row in await self.db.orders.get_orders(limit=-1)
        }

        with self.assertRaises(HTTPException) as raised:
            await move_steamer(
                self.db.orders,
                {
                    "order_ids": [mover["_id"]],
                    "steamer_id": "1",
                    "port_index": 4,
                },
            )
        self.assertEqual(raised.exception.status_code, 409)

        after = {
            row["business_flow_id"]: row.get("placement")
            for row in await self.db.orders.get_orders(limit=-1)
        }
        self.assertEqual(after, before)
        self.assertEqual(after["mover-01"]["port_index"], 1)


class SteamerCancelHoldOrdersPortTests(unittest.IsolatedAsyncioTestCase):
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

    async def _cancel_loaded(self, *, flow_id="bs88_1", table="C1"):
        await self.db.orders.batch_insert_orders([
            _pending_order(
                business_flow_id=flow_id,
                table_number=table,
                source="delivery",
            ),
        ])
        row = next(
            item for item in await self.db.orders.get_orders(limit=-1)
            if item["business_flow_id"] == flow_id
        )
        await _load(self.db.orders, row["_id"], port_index=5)
        await self.db.orders.mark_delivery_cancelled(flow_id.split("_")[0])
        return row["_id"]

    async def test_move_rejects_cancelled_hold_and_keeps_placement(self):
        hold_id = await self._cancel_loaded()
        before = await self.db.orders.get_order_by_id(hold_id)
        self.assertIsNotNone(before.get("placement"))

        with self.assertRaises(HTTPException) as raised:
            await move_steamer(
                self.db.orders,
                {
                    "order_ids": [hold_id],
                    "steamer_id": "1",
                    "port_index": 2,
                },
            )
        self.assertEqual(raised.exception.status_code, 409)

        after = await self.db.orders.get_order_by_id(hold_id)
        self.assertEqual(after.get("placement"), before.get("placement"))

    async def test_cancelled_hold_counts_toward_hole_capacity(self):
        capacity = settings.KITCHEN_STATIONS["shulong"]["steamer_layout"]["port_capacity"]
        await self.db.orders.batch_insert_orders([
            _pending_order(
                business_flow_id=f"fill-{i:02d}",
                table_number=f"F{i}",
            )
            for i in range(capacity - 1)
        ] + [
            _pending_order(
                business_flow_id="bs77_1",
                table_number="H1",
                source="delivery",
            ),
            _pending_order(business_flow_id="extra-01", table_number="E1"),
        ])
        rows = {row["business_flow_id"]: row for row in await self.db.orders.get_orders(limit=-1)}
        fill_ids = [rows[f"fill-{i:02d}"]["_id"] for i in range(capacity - 1)]
        await load_steamer(
            self.db.orders,
            {
                "order_ids": fill_ids + [rows["bs77_1"]["_id"]],
                "steamer_id": "1",
                "port_index": 6,
                "loaded_at": "2026-08-14T10:05:00+08:00",
            },
        )
        await self.db.orders.mark_delivery_cancelled("bs77")
        before = {
            row["_id"]: row.get("placement")
            for row in await self.db.orders.get_orders(limit=-1)
        }

        with self.assertRaises(HTTPException) as raised:
            await load_steamer(
                self.db.orders,
                {
                    "order_ids": [rows["extra-01"]["_id"]],
                    "steamer_id": "1",
                    "port_index": 6,
                },
            )
        self.assertEqual(raised.exception.status_code, 409)
        after = {
            row["_id"]: row.get("placement")
            for row in await self.db.orders.get_orders(limit=-1)
        }
        self.assertEqual(after, before)

    async def test_move_rejects_refund_flow_cage_with_placement(self):
        await self.db.orders.batch_insert_orders([
            _pending_order(
                business_flow_id="desk_refund_9",
                table_number="R1",
            ),
        ])
        row = (await self.db.orders.get_orders(limit=-1))[0]
        await _load(self.db.orders, row["_id"], port_index=3)
        before = await self.db.orders.get_order_by_id(row["_id"])

        with self.assertRaises(HTTPException) as raised:
            await move_steamer(
                self.db.orders,
                {
                    "order_ids": [row["_id"]],
                    "steamer_id": "2",
                    "port_index": 1,
                },
            )
        self.assertEqual(raised.exception.status_code, 409)
        after = await self.db.orders.get_order_by_id(row["_id"])
        self.assertEqual(after.get("placement"), before.get("placement"))
        self.assertEqual(after["dish_status"], "待出餐")
