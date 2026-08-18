#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

from config import settings
from database import CHINA_TZ, DatabaseManager
from scraper.order_flow_ids import (
    allocate_incremental_flow_ids,
    allocate_reconcile_flow_ids,
    parse_order_flow_id,
)
from scraper.table_change_detector import TableChangeDetector


class OrderFlowIdsTest(unittest.TestCase):
    def test_allocate_incremental_flow_ids_unique_for_batch(self):
        template = {
            "dish_name": "金牌LuckIn虾饺皇",
            "price": 12.0,
            "business_flow_id": "YY01101-260428-0053_金牌LuckIn虾饺皇_001",
        }
        previous = [template.copy()]
        new_ids = allocate_incremental_flow_ids(template, previous, 5)
        self.assertEqual(len(new_ids), 5)
        self.assertEqual(len(set(new_ids)), 5)
        self.assertEqual(new_ids[0], "YY01101-260428-0053_金牌LuckIn虾饺皇_002")
        self.assertEqual(new_ids[-1], "YY01101-260428-0053_金牌LuckIn虾饺皇_006")

    def test_refund_ids_are_unique(self):
        template = {
            "dish_name": "杨枝甘露",
            "price": 18.0,
            "business_flow_id": "YY01101-260428-0015_杨枝甘露_001",
        }
        refund_ids = allocate_incremental_flow_ids(template, [template], 2, refund=True)
        self.assertEqual(len(refund_ids), 2)
        self.assertEqual(len(set(refund_ids)), 2)
        self.assertTrue(all("_refund_" in flow_id for flow_id in refund_ids))

    def test_parse_order_flow_id(self):
        parsed = parse_order_flow_id("YY01101-260428-0053_(普通)桐乡胎菊_006")
        self.assertEqual(parsed, ("YY01101-260428-0053", "(普通)桐乡胎菊"))

    def test_reconcile_flow_ids(self):
        ids = allocate_reconcile_flow_ids("YY01101-260428-0001", "虾饺", 2, start_index=3)
        self.assertEqual(
            ids,
            [
                "YY01101-260428-0001_虾饺_reconcile_003",
                "YY01101-260428-0001_虾饺_reconcile_004",
            ],
        )


class DetectDishChangesIntegrationTest(unittest.TestCase):
    """模拟 _detect_dish_changes 加菜路径的 ID 分配。"""

    def test_quantity_increase_produces_unique_insertable_ids(self):
        template = {
            "dish_name": "一品珍珠糯米鸡",
            "price": 22.0,
            "quantity": 1,
            "business_flow_id": "YY01101-260428-0053_一品珍珠糯米鸡_001",
        }
        current_orders = [
            template.copy(),
            {**template, "business_flow_id": "YY01101-260428-0053_一品珍珠糯米鸡_002"},
            {**template, "business_flow_id": "YY01101-260428-0053_一品珍珠糯米鸡_003"},
            {**template, "business_flow_id": "YY01101-260428-0053_一品珍珠糯米鸡_004"},
            {**template, "business_flow_id": "YY01101-260428-0053_一品珍珠糯米鸡_005"},
            {**template, "business_flow_id": "YY01101-260428-0053_一品珍珠糯米鸡_006"},
        ]
        previous_orders = [template.copy()]
        added_quantity = 5
        new_ids = allocate_incremental_flow_ids(
            template,
            current_orders + previous_orders,
            added_quantity,
        )
        self.assertEqual(len(new_ids), 5)
        self.assertEqual(len(set(new_ids)), 5)
        self.assertTrue(all(flow_id not in {o["business_flow_id"] for o in current_orders} for flow_id in new_ids))


def _pos_line(flow_id: str, *, qty: int = 1, dish: str = "虾饺", price: float = 12.0):
    return {
        "business_flow_id": flow_id,
        "table_number": "8",
        "dish_name": dish,
        "quantity": qty,
        "price": price,
        "total_amount": price * qty,
        "status": "未结",
        "order_time": datetime(2026, 8, 18, 10, 0, tzinfo=CHINA_TZ),
        "source": "dine_in",
        "station": "shulong",
    }


class DetectDishChangesDineInCancelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.state = SimpleNamespace(previous_table_orders={})
        self.detector = TableChangeDetector(
            session=None,
            state_store=self.state,
            logger_=logging.getLogger("test-dish-changes"),
        )

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old
        self._tmpdir.cleanup()

    async def test_qty_down_cancels_original_and_does_not_emit_refund_rows(self):
        line_a = _pos_line("t8_虾饺_001")
        line_b = _pos_line("t8_虾饺_002")
        await self.db.orders.batch_insert_orders([line_a, line_b])
        self.state.previous_table_orders["8"] = [line_a, line_b]

        changed, _snapshot = await self.detector._detect_dish_changes(
            "8",
            [line_a],
            24.0,
            12.0,
            orders=self.db.orders,
        )
        self.assertEqual(changed, [])
        self.assertFalse(
            any("_refund_" in (row.get("business_flow_id") or "") for row in changed)
        )
        cancelled = await self.db.orders.get_order_by_id("t8_虾饺_001")
        kept = await self.db.orders.get_order_by_id("t8_虾饺_002")
        # earlier 下单时间 first among 未做; both unmade so 001 cancelled
        self.assertEqual(cancelled["dish_status"], "已取消")
        self.assertEqual(cancelled["quantity"], 0)
        self.assertEqual(kept["dish_status"], "待出餐")
        self.assertEqual(kept["quantity"], 1)
        rows = await self.db.orders.get_orders(limit=-1)
        self.assertEqual(len(rows), 2)

    async def test_qty_up_restores_then_inserts_extras_without_refund_ids(self):
        original = _pos_line("t8_虾饺_001")
        await self.db.orders.batch_insert_orders([original])
        await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1)
        self.state.previous_table_orders["8"] = []
        current = [
            _pos_line("t8_虾饺_pos_1"),
            _pos_line("t8_虾饺_pos_2"),
        ]

        changed, _snapshot = await self.detector._detect_dish_changes(
            "8",
            current,
            0.0,
            24.0,
            orders=self.db.orders,
        )
        restored = await self.db.orders.get_order_by_id("t8_虾饺_001")
        self.assertEqual(restored["dish_status"], "待出餐")
        self.assertEqual(restored["quantity"], 1)
        self.assertEqual(len(changed), 1)
        self.assertNotIn("_refund_", changed[0]["business_flow_id"])
        self.assertEqual(changed[0]["quantity"], 1)
        self.assertIn(changed[0]["change_type"], ("新增", "增加"))

    async def test_qty_down_without_original_row_does_not_insert_refund(self):
        self.state.previous_table_orders["8"] = [_pos_line("t8_虾饺_001")]
        changed, _snapshot = await self.detector._detect_dish_changes(
            "8",
            [],
            12.0,
            0.0,
            orders=self.db.orders,
        )
        self.assertEqual(changed, [])
        self.assertEqual(await self.db.orders.get_orders(limit=-1), [])


if __name__ == "__main__":
    unittest.main()
