#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from scraper.order_flow_ids import (
    allocate_incremental_flow_ids,
    allocate_reconcile_flow_ids,
    parse_order_flow_id,
)


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


if __name__ == "__main__":
    unittest.main()
