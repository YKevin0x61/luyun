#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""退菜占位 / 抽笼 / 待上笼退示: phase is derived; dish_status stays the cancel enum."""

import tempfile
import unittest
from datetime import datetime, timedelta

from fastapi import HTTPException

from config import settings
from database import CHINA_TZ, DatabaseManager, ensure_beijing_datetime
from services.kds_orders import (
    complete_cooking,
    derive_steamer_phase,
    load_steamer,
    pluck_steamer,
)


def _pending_order(**overrides):
    row = {
        "business_flow_id": "bs88_1",
        "table_number": "A1",
        "dish_name": "虾饺",
        "quantity": 1,
        "order_time": datetime(2026, 8, 14, 10, 0, tzinfo=CHINA_TZ),
        "station": "shulong",
        "status": "未结",
        "dish_status": "待出餐",
        "source": "delivery",
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


class SteamerCancelHoldPhaseTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_load_then_cancel_keeps_placement_as_cancel_hold(self):
        await self.db.orders.batch_insert_orders([_pending_order()])
        row = (await self.db.orders.get_orders(limit=-1))[0]
        await _load(self.db.orders, row["_id"], port_index=5)
        await self.db.orders.mark_delivery_cancelled("bs88")

        after = await self.db.orders.get_order_by_id(row["_id"])
        self.assertIsNotNone(after.get("placement"))
        self.assertEqual(after["placement"]["steamer_id"], "1")
        self.assertEqual(after["placement"]["port_index"], 5)
        self.assertEqual(after["dish_status"], "已取消")
        self.assertNotEqual(after["dish_status"], "退菜占位")
        self.assertEqual(derive_steamer_phase(after), "退菜占位")
        self.assertEqual(after["status"], "退菜")

    async def test_dine_in_load_then_cancel_keeps_placement_as_cancel_hold(self):
        await self.db.orders.batch_insert_orders(
            [_pending_order(source="dine_in", business_flow_id="t8_虾饺_hold")]
        )
        row = (await self.db.orders.get_orders(limit=-1))[0]
        await _load(self.db.orders, row["_id"], port_index=5)
        await self.db.orders.cancel_dine_in_portions("A1", "虾饺", 1)

        after = await self.db.orders.get_order_by_id(row["_id"])
        self.assertEqual(after["dish_status"], "已取消")
        self.assertEqual(after["placement"]["port_index"], 5)
        self.assertEqual(derive_steamer_phase(after), "退菜占位")

        plucked = await pluck_steamer(self.db.orders, {"order_ids": [row["_id"]]})
        self.assertTrue(plucked["success"])
        gone = await self.db.orders.get_order_by_id(row["_id"])
        self.assertIsNone(gone.get("placement"))
        self.assertEqual(gone["dish_status"], "已取消")

    async def test_pluck_clears_hold_and_compacts_remaining_hole(self):
        await self.db.orders.batch_insert_orders([
            _pending_order(business_flow_id="bs88_1", table_number="A1"),
            _pending_order(business_flow_id="keep-001", table_number="A2", source="pos"),
        ])
        rows = {row["business_flow_id"]: row for row in await self.db.orders.get_orders(limit=-1)}
        await _load(self.db.orders, rows["bs88_1"]["_id"], port_index=5)
        await _load(
            self.db.orders,
            rows["keep-001"]["_id"],
            port_index=5,
            loaded_at="2026-08-14T10:06:00+08:00",
        )
        await self.db.orders.mark_delivery_cancelled("bs88")

        result = await pluck_steamer(
            self.db.orders,
            {"order_ids": [rows["bs88_1"]["_id"]]},
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["updated_count"], 1)

        plucked = await self.db.orders.get_order_by_id(rows["bs88_1"]["_id"])
        remaining = await self.db.orders.get_order_by_id(rows["keep-001"]["_id"])

        self.assertEqual(plucked["dish_status"], "已取消")
        self.assertIsNone(plucked.get("placement"))
        self.assertTrue(plucked.get("loaded_at"))
        self.assertIsNone(
            derive_steamer_phase(
                plucked, now=datetime(2026, 8, 14, 10, 10, tzinfo=CHINA_TZ)
            )
        )
        self.assertIsNone(derive_steamer_phase(plucked, now=datetime(2026, 8, 14, 12, 0, tzinfo=CHINA_TZ)))
        self.assertNotEqual(plucked["dish_status"], "已制作待上菜")
        self.assertFalse(plucked.get("ready_time"))

        self.assertEqual(remaining["dish_status"], "待出餐")
        self.assertEqual(derive_steamer_phase(remaining), "在蒸")
        self.assertEqual(
            remaining["placement"],
            {
                "steamer_id": "1",
                "port_index": 5,
                "stack_order": 1,
                "loaded_at": "2026-08-14T10:06:00+08:00",
            },
        )


class SteamerAwaitingCancelNoticeTests(unittest.IsolatedAsyncioTestCase):
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

    async def _cancel_unloaded(self, *, flow_id="bs90_1"):
        await self.db.orders.batch_insert_orders([
            _pending_order(business_flow_id=flow_id, table_number="B1"),
        ])
        row = next(
            item for item in await self.db.orders.get_orders(limit=-1)
            if item["business_flow_id"] == flow_id
        )
        await self.db.orders.mark_delivery_cancelled(flow_id.split("_")[0])
        return await self.db.orders.get_order_by_id(row["_id"])

    async def test_cancel_without_placement_stays_awaiting_notice_regardless_of_elapsed_time(self):
        after = await self._cancel_unloaded()
        self.assertIsNone(after.get("placement"))
        self.assertEqual(after["dish_status"], "已取消")
        cancelled_at = after["updated_at"]
        inside = ensure_beijing_datetime(cancelled_at) + timedelta(seconds=30)
        expired = ensure_beijing_datetime(cancelled_at) + timedelta(seconds=181)

        self.assertEqual(
            derive_steamer_phase(after, now=inside, notice_seconds=180),
            "待上笼退示",
        )
        self.assertEqual(
            derive_steamer_phase(after, now=expired, notice_seconds=180),
            "待上笼退示",
        )

    async def test_awaiting_notice_cannot_load_or_complete_cooking(self):
        after = await self._cancel_unloaded(flow_id="bs91_1")
        before_placement = after.get("placement")

        with self.assertRaises(HTTPException) as load_err:
            await load_steamer(
                self.db.orders,
                {
                    "order_ids": [after["_id"]],
                    "steamer_id": "1",
                    "port_index": 2,
                },
            )
        self.assertEqual(load_err.exception.status_code, 409)

        with self.assertRaises(HTTPException) as cook_err:
            await complete_cooking(
                self.db.orders,
                {
                    "dish_name": "虾饺",
                    "orders": [
                        {
                            "order_id": after["_id"],
                            "table_number": "B1",
                            "complete_quantity": 1,
                        }
                    ],
                },
            )
        self.assertEqual(cook_err.exception.status_code, 409)

        still = await self.db.orders.get_order_by_id(after["_id"])
        self.assertEqual(still.get("placement"), before_placement)
        self.assertEqual(still["dish_status"], "已取消")
        self.assertFalse(still.get("ready_time"))

    def test_parallel_refund_pending_row_is_not_awaiting_notice(self):
        refund = {
            "business_flow_id": "t8_虾饺_refund_1",
            "dish_status": "待出餐",
            "status": "退菜",
            "quantity": 1,
        }
        self.assertIsNone(derive_steamer_phase(refund))
