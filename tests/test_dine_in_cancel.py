#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""堂食退菜对象: cancel/restore existing 订单行 via OrdersPort."""

import tempfile
import unittest
from datetime import datetime

from fastapi import HTTPException

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.kds_orders import (
    complete_cooking,
    derive_steamer_phase,
    load_steamer,
    pluck_steamer,
)


def _dine_in_order(flow_id: str, dish: str = "虾饺", *, seq_time_hour: int = 10, **overrides):
    row = {
        "business_flow_id": flow_id,
        "table_number": "8",
        "dish_name": dish,
        "quantity": 1,
        "order_time": datetime(2026, 8, 18, seq_time_hour, 0, tzinfo=CHINA_TZ),
        "price": 12.0,
        "total_amount": 12.0,
        "status": "未结",
        "station": "shulong",
        "source": "dine_in",
        "dish_status": "待出餐",
    }
    row.update(overrides)
    return row


async def _by_flow(db):
    rows = await db.orders.get_orders(table_number="8", limit=-1)
    return {row["business_flow_id"]: row for row in rows}


class DineInCancelTargetTests(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_mixed_shrimp(self):
        await self.db.orders.batch_insert_orders(
            [
                _dine_in_order("t8_虾饺_001", seq_time_hour=10),
                _dine_in_order("t8_虾饺_002", seq_time_hour=11),
                _dine_in_order("t8_虾饺_003", seq_time_hour=12),
            ]
        )
        by_flow = await _by_flow(self.db)
        await load_steamer(
            self.db.orders,
            {
                "order_ids": [by_flow["t8_虾饺_002"]["_id"]],
                "steamer_id": "1",
                "port_index": 3,
                "loaded_at": "2026-08-18T11:05:00+08:00",
            },
        )
        await complete_cooking(
            self.db.orders,
            {
                "dish_name": "虾饺",
                "orders": [
                    {
                        "order_id": by_flow["t8_虾饺_003"]["_id"],
                        "table_number": "8",
                        "complete_quantity": 1,
                    }
                ],
            },
        )
        return await _by_flow(self.db)

    async def test_cancel_n_hits_unmade_then_steaming_then_served(self):
        by_flow = await self._seed_mixed_shrimp()
        unmade_id = by_flow["t8_虾饺_001"]["_id"]
        steaming_id = by_flow["t8_虾饺_002"]["_id"]
        served_id = by_flow["t8_虾饺_003"]["_id"]

        affected = await self.db.orders.cancel_dine_in_portions("8", "虾饺", 2)
        self.assertEqual(affected, 2)

        unmade = await self.db.orders.get_order_by_id(unmade_id)
        steaming = await self.db.orders.get_order_by_id(steaming_id)
        served = await self.db.orders.get_order_by_id(served_id)

        self.assertEqual(unmade["dish_status"], "已取消")
        self.assertEqual(unmade["status"], "退菜")
        self.assertEqual(unmade["quantity"], 0)
        self.assertEqual(unmade["total_amount"], 0)
        self.assertIsNone(unmade.get("placement"))

        self.assertEqual(steaming["dish_status"], "已取消")
        self.assertEqual(steaming["status"], "退菜")
        self.assertEqual(steaming["quantity"], 0)
        self.assertEqual(steaming["total_amount"], 0)
        self.assertEqual(steaming["placement"]["steamer_id"], "1")
        self.assertEqual(steaming["placement"]["port_index"], 3)
        self.assertEqual(derive_steamer_phase(steaming), "退菜占位")

        self.assertEqual(served["dish_status"], "已制作待上菜")
        self.assertEqual(served["quantity"], 1)
        self.assertEqual(served["status"], "未结")

    async def test_cancel_without_original_row_does_not_insert(self):
        await self.db.orders.batch_insert_orders(
            [_dine_in_order("t8_凤爪_001", "凤爪")]
        )
        before = await self.db.orders.get_orders(limit=-1)
        self.assertEqual(len(before), 1)

        affected = await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1)
        self.assertEqual(affected, 0)

        after = await self.db.orders.get_orders(limit=-1)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["business_flow_id"], "t8_凤爪_001")
        self.assertEqual(after[0]["dish_status"], "待出餐")
        self.assertFalse(
            any("_refund_" in (row.get("business_flow_id") or "") for row in after)
        )

    async def test_restore_brings_back_most_recently_cancelled(self):
        await self.db.orders.batch_insert_orders(
            [
                _dine_in_order("t8_虾饺_001", seq_time_hour=10),
                _dine_in_order("t8_虾饺_002", seq_time_hour=11),
            ]
        )
        by_flow = await _by_flow(self.db)
        await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1)
        await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1)

        first_cancelled = await self.db.orders.get_order_by_id(by_flow["t8_虾饺_001"]["_id"])
        second_cancelled = await self.db.orders.get_order_by_id(by_flow["t8_虾饺_002"]["_id"])
        self.assertEqual(first_cancelled["dish_status"], "已取消")
        self.assertEqual(second_cancelled["dish_status"], "已取消")
        self.assertGreaterEqual(
            second_cancelled["updated_at"], first_cancelled["updated_at"]
        )

        restored = await self.db.orders.restore_dine_in_cancelled(
            "8",
            "虾饺",
            {"quantity": 1, "price": 12.0, "total_amount": 12.0, "status": "未结"},
        )
        self.assertIsNotNone(restored)
        self.assertEqual(restored["business_flow_id"], "t8_虾饺_002")
        self.assertEqual(restored["dish_status"], "待出餐")
        self.assertEqual(restored["quantity"], 1)
        self.assertEqual(restored["total_amount"], 12.0)
        self.assertEqual(restored["status"], "未结")
        self.assertIsNone(restored.get("placement"))
        self.assertEqual(derive_steamer_phase(restored), "待上笼")

        still_cancelled = await self.db.orders.get_order_by_id(by_flow["t8_虾饺_001"]["_id"])
        self.assertEqual(still_cancelled["dish_status"], "已取消")

    async def test_restore_with_placement_stays_steaming(self):
        await self.db.orders.batch_insert_orders([_dine_in_order("t8_虾饺_010")])
        row = (await self.db.orders.get_orders(limit=-1))[0]
        await load_steamer(
            self.db.orders,
            {
                "order_ids": [row["_id"]],
                "steamer_id": "1",
                "port_index": 4,
                "loaded_at": "2026-08-18T10:05:00+08:00",
            },
        )
        await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1)

        restored = await self.db.orders.restore_dine_in_cancelled(
            "8",
            "虾饺",
            {"quantity": 1, "price": 12.0, "total_amount": 12.0, "status": "未结"},
        )
        self.assertEqual(restored["dish_status"], "待出餐")
        self.assertEqual(restored["placement"]["steamer_id"], "1")
        self.assertEqual(restored["placement"]["port_index"], 4)
        self.assertEqual(derive_steamer_phase(restored), "在蒸")
        self.assertNotEqual(derive_steamer_phase(restored), "待上笼")

    async def test_same_phase_cancels_earlier_order_time_first(self):
        await self.db.orders.batch_insert_orders(
            [
                _dine_in_order("t8_虾饺_001", seq_time_hour=10),
                _dine_in_order("t8_虾饺_002", seq_time_hour=12),
            ]
        )
        affected = await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1)
        self.assertEqual(affected, 1)
        earlier = await self.db.orders.get_order_by_id("t8_虾饺_001")
        later = await self.db.orders.get_order_by_id("t8_虾饺_002")
        self.assertEqual(earlier["dish_status"], "已取消")
        self.assertEqual(later["dish_status"], "待出餐")

    async def test_cancel_skips_historical_refund_flow_rows(self):
        await self.db.orders.batch_insert_orders(
            [
                _dine_in_order("t8_虾饺_001"),
                _dine_in_order("t8_虾饺_refund_1"),
            ]
        )
        affected = await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1)
        self.assertEqual(affected, 1)
        original = await self.db.orders.get_order_by_id("t8_虾饺_001")
        refund_row = await self.db.orders.get_order_by_id("t8_虾饺_refund_1")
        self.assertEqual(original["dish_status"], "已取消")
        self.assertEqual(refund_row["dish_status"], "待出餐")
        self.assertEqual(refund_row["quantity"], 1)

    async def test_restore_after_served_cancel_is_unmade(self):
        await self.db.orders.batch_insert_orders([_dine_in_order("t8_虾饺_050")])
        row = (await self.db.orders.get_orders(limit=-1))[0]
        await complete_cooking(
            self.db.orders,
            {
                "dish_name": "虾饺",
                "orders": [
                    {
                        "order_id": row["_id"],
                        "table_number": "8",
                        "complete_quantity": 1,
                    }
                ],
            },
        )
        await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1)
        restored = await self.db.orders.restore_dine_in_cancelled(
            "8",
            "虾饺",
            {"quantity": 1, "price": 12.0, "total_amount": 12.0, "status": "未结"},
        )
        self.assertEqual(restored["dish_status"], "待出餐")
        self.assertIsNone(restored.get("placement"))
        self.assertFalse(restored.get("ready_time"))
        self.assertEqual(derive_steamer_phase(restored), "待上笼")

    async def test_restore_beyond_cancelled_rows_returns_none_without_insert(self):
        await self.db.orders.batch_insert_orders([_dine_in_order("t8_虾饺_020")])
        await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1)
        template = {"quantity": 1, "price": 12.0, "total_amount": 12.0, "status": "未结"}
        first = await self.db.orders.restore_dine_in_cancelled("8", "虾饺", template)
        self.assertIsNotNone(first)
        second = await self.db.orders.restore_dine_in_cancelled("8", "虾饺", template)
        self.assertIsNone(second)
        rows = await self.db.orders.get_orders(limit=-1)
        self.assertEqual(len(rows), 1)
        self.assertFalse(
            any("_refund_" in (row.get("business_flow_id") or "") for row in rows)
        )

    async def test_dine_in_cancel_does_not_touch_delivery_rows(self):
        await self.db.orders.batch_insert_orders(
            [
                _dine_in_order("t8_虾饺_030"),
                _dine_in_order(
                    "bs99_虾饺_001",
                    source="delivery",
                    table_number="美团1",
                    status="已结",
                ),
            ]
        )
        affected = await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1)
        self.assertEqual(affected, 1)
        delivery = await self.db.orders.get_order_by_id("bs99_虾饺_001")
        self.assertEqual(delivery["dish_status"], "待出餐")
        self.assertEqual(delivery["quantity"], 1)
        self.assertEqual(delivery["status"], "已结")

        dine = await self.db.orders.get_order_by_id("t8_虾饺_030")
        self.assertEqual(dine["dish_status"], "已取消")

        reverted = await self.db.orders.revert_delivery_cancelled(
            [
                {
                    "business_flow_id": "bs99_虾饺_001",
                    "quantity": 1,
                    "price": 12.0,
                    "total_amount": 12.0,
                    "status": "已结",
                    "order_time": datetime(2026, 8, 18, 10, 0, tzinfo=CHINA_TZ),
                }
            ]
        )
        self.assertEqual(reverted, 0)
        marked = await self.db.orders.mark_delivery_cancelled("bs99")
        self.assertEqual(marked, 1)
        dine_after = await self.db.orders.get_order_by_id("t8_虾饺_030")
        self.assertEqual(dine_after["dish_status"], "已取消")
        delivery_after = await self.db.orders.get_order_by_id("bs99_虾饺_001")
        self.assertEqual(delivery_after["dish_status"], "已取消")
        restored_delivery = await self.db.orders.revert_delivery_cancelled(
            [
                {
                    "business_flow_id": "bs99_虾饺_001",
                    "quantity": 1,
                    "price": 12.0,
                    "total_amount": 12.0,
                    "status": "已结",
                    "order_time": datetime(2026, 8, 18, 10, 0, tzinfo=CHINA_TZ),
                }
            ]
        )
        self.assertEqual(restored_delivery, 1)
        delivery_live = await self.db.orders.get_order_by_id("bs99_虾饺_001")
        self.assertEqual(delivery_live["dish_status"], "待出餐")
        self.assertEqual(delivery_live["quantity"], 1)
        dine_still = await self.db.orders.get_order_by_id("t8_虾饺_030")
        self.assertEqual(dine_still["dish_status"], "已取消")

    async def test_cancelled_dine_in_cannot_cook_or_load_but_hold_can_pluck(self):
        await self.db.orders.batch_insert_orders(
            [
                _dine_in_order("t8_虾饺_040", seq_time_hour=10),
                _dine_in_order("t8_虾饺_041", seq_time_hour=11),
            ]
        )
        by_flow = await _by_flow(self.db)
        await load_steamer(
            self.db.orders,
            {
                "order_ids": [by_flow["t8_虾饺_041"]["_id"]],
                "steamer_id": "1",
                "port_index": 2,
                "loaded_at": "2026-08-18T11:05:00+08:00",
            },
        )
        await self.db.orders.cancel_dine_in_portions("8", "虾饺", 2)
        unmade = await self.db.orders.get_order_by_id(by_flow["t8_虾饺_040"]["_id"])
        hold = await self.db.orders.get_order_by_id(by_flow["t8_虾饺_041"]["_id"])
        self.assertEqual(derive_steamer_phase(hold), "退菜占位")

        with self.assertRaises(HTTPException) as cook_err:
            await complete_cooking(
                self.db.orders,
                {
                    "dish_name": "虾饺",
                    "orders": [
                        {
                            "order_id": unmade["_id"],
                            "table_number": "8",
                            "complete_quantity": 1,
                        }
                    ],
                },
            )
        self.assertEqual(cook_err.exception.status_code, 409)

        with self.assertRaises(HTTPException) as load_err:
            await load_steamer(
                self.db.orders,
                {
                    "order_ids": [unmade["_id"]],
                    "steamer_id": "1",
                    "port_index": 5,
                },
            )
        self.assertEqual(load_err.exception.status_code, 409)

        plucked = await pluck_steamer(self.db.orders, {"order_ids": [hold["_id"]]})
        self.assertTrue(plucked["success"])
        after_pluck = await self.db.orders.get_order_by_id(hold["_id"])
        self.assertEqual(after_pluck["dish_status"], "已取消")
        self.assertIsNone(after_pluck.get("placement"))

    async def test_cancel_one_no_onion_leaves_unnoted_same_dish(self):
        await self.db.orders.batch_insert_orders(
            [
                _dine_in_order("t8_虾饺_plain", notes=""),
                _dine_in_order("t8_虾饺_no_onion", notes="免葱"),
            ]
        )
        affected = await self.db.orders.cancel_dine_in_portions(
            "8", "虾饺", 1, notes="免葱"
        )
        self.assertEqual(affected, 1)
        by_flow = await _by_flow(self.db)
        self.assertEqual(by_flow["t8_虾饺_no_onion"]["dish_status"], "已取消")
        self.assertEqual(by_flow["t8_虾饺_no_onion"]["quantity"], 0)
        self.assertEqual(by_flow["t8_虾饺_plain"]["dish_status"], "待出餐")
        self.assertEqual(by_flow["t8_虾饺_plain"]["quantity"], 1)

    async def test_restore_same_notes_skips_later_other_notes_cancel(self):
        await self.db.orders.batch_insert_orders(
            [
                _dine_in_order("t8_虾饺_no_onion", notes="免葱", seq_time_hour=10),
                _dine_in_order("t8_虾饺_plain", notes="", seq_time_hour=11),
            ]
        )
        await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1, notes="免葱")
        await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1, notes="")
        restored = await self.db.orders.restore_dine_in_cancelled(
            "8",
            "虾饺",
            {
                "quantity": 1,
                "price": 12.0,
                "total_amount": 12.0,
                "status": "未结",
                "notes": "免葱",
            },
        )
        self.assertIsNotNone(restored)
        self.assertEqual(restored["business_flow_id"], "t8_虾饺_no_onion")
        self.assertEqual(restored["dish_status"], "待出餐")
        still_cancelled = await self.db.orders.get_order_by_id("t8_虾饺_plain")
        self.assertEqual(still_cancelled["dish_status"], "已取消")

    async def test_platform_prefix_notes_identity_equals_empty_on_cancel_and_restore(self):
        await self.db.orders.batch_insert_orders(
            [
                _dine_in_order(
                    "t8_虾饺_platform",
                    notes="外卖平台:美团|来源:美团1",
                    seq_time_hour=10,
                ),
                _dine_in_order("t8_虾饺_no_onion", notes="免葱", seq_time_hour=11),
            ]
        )
        affected = await self.db.orders.cancel_dine_in_portions(
            "8", "虾饺", 1, notes="外卖平台:饿了么"
        )
        self.assertEqual(affected, 1)
        by_flow = await _by_flow(self.db)
        self.assertEqual(by_flow["t8_虾饺_platform"]["dish_status"], "已取消")
        self.assertEqual(by_flow["t8_虾饺_no_onion"]["dish_status"], "待出餐")
        restored = await self.db.orders.restore_dine_in_cancelled(
            "8",
            "虾饺",
            {
                "quantity": 1,
                "price": 12.0,
                "total_amount": 12.0,
                "status": "未结",
                "notes": "",
            },
        )
        self.assertIsNotNone(restored)
        self.assertEqual(restored["business_flow_id"], "t8_虾饺_platform")
        self.assertEqual(restored["dish_status"], "待出餐")
        still_live = await self.db.orders.get_order_by_id("t8_虾饺_no_onion")
        self.assertEqual(still_live["dish_status"], "待出餐")
