#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OrderLineBuilder unit tests."""

import unittest
from datetime import datetime

from database import CHINA_TZ
from scraper.order_line_builder import (
    FLOW_MODE_COMBO,
    FLOW_MODE_RECONCILE,
    OrderLineBuilder,
    RawOrderLine,
    classify_dish,
)


class _FakeCatalog:
    async def resolve(self, dish_name):
        return "changfen" if "肠" in dish_name or "虾" in dish_name else "xibing"


class ClassifyDishTest(unittest.TestCase):
    def test_tea_and_dim_sum(self):
        self.assertEqual(classify_dish("陈香菊普"), "茶水")
        self.assertEqual(classify_dish("佳点虾饺"), "佳点")
        self.assertEqual(classify_dish("虾饺点心"), "点心")
        self.assertEqual(classify_dish("清炒时蔬"), "热菜")


class ExpandTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.builder = OrderLineBuilder(_FakeCatalog())
        self.when = datetime(2026, 7, 20, 12, 30, tzinfo=CHINA_TZ)

    async def test_splits_quantity_and_unit_money(self):
        rows = await self.builder.expand(
            RawOrderLine(
                bs_code="YY01101-260720-0001",
                dish_name="虾饺",
                quantity=2,
                unit_price=18.0,
                table_number="A1",
                order_time=self.when,
                overlays={"status": "未结"},
            )
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [r["business_flow_id"] for r in rows],
            [
                "YY01101-260720-0001_虾饺_001",
                "YY01101-260720-0001_虾饺_002",
            ],
        )
        for row in rows:
            self.assertEqual(row["quantity"], 1)
            self.assertEqual(row["price"], 18.0)
            self.assertEqual(row["total_amount"], 18.0)
            self.assertEqual(row["station"], "changfen")
            self.assertEqual(row["status"], "未结")
            self.assertIsInstance(row["order_time"], datetime)

    async def test_combo_flow_mode(self):
        rows = await self.builder.expand(
            RawOrderLine(
                bs_code="YY1",
                dish_name="烧卖",
                quantity=1,
                unit_price=0.0,
                table_number="A1",
                order_time=self.when,
                flow_mode=FLOW_MODE_COMBO,
                overlays={"status": "未结"},
            )
        )
        self.assertEqual(rows[0]["business_flow_id"], "YY1_烧卖_套餐_001")

    async def test_reconcile_flow_mode_and_start_index(self):
        rows = await self.builder.expand(
            RawOrderLine(
                bs_code="YY1",
                dish_name="虾饺",
                quantity=2,
                unit_price=10.0,
                table_number="外卖",
                order_time=self.when,
                flow_mode=FLOW_MODE_RECONCILE,
                start_index=3,
                overlays={"status": "已结", "notes": "reconcile_fix|YY1"},
            )
        )
        self.assertEqual(
            [r["business_flow_id"] for r in rows],
            ["YY1_虾饺_reconcile_003", "YY1_虾饺_reconcile_004"],
        )
        self.assertEqual(rows[0]["notes"], "reconcile_fix|YY1")

    async def test_skips_empty_or_zero_qty(self):
        self.assertEqual(
            await self.builder.expand(
                RawOrderLine(
                    bs_code="YY1",
                    dish_name="",
                    quantity=1,
                    unit_price=1,
                    table_number="1",
                    order_time=self.when,
                )
            ),
            [],
        )
        self.assertEqual(
            await self.builder.expand(
                RawOrderLine(
                    bs_code="YY1",
                    dish_name="虾饺",
                    quantity=0,
                    unit_price=1,
                    table_number="1",
                    order_time=self.when,
                )
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
