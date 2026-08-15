#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集入库剥离菜名尾部 "(-)" 后缀，以及对账两侧聚合键一致性的测试。"""

import logging
import unittest

from scraper.delivery_bill_tracker import DeliveryBillTracker
from scraper.pos_session import PosSession
from scraper.settled_reconcile import (
    aggregate_db_orders,
    aggregate_pos_dishes,
    compute_reconcile_diff,
)
from services.dish_normalize import strip_trailing_dash_suffix


class StripTrailingDashSuffixTest(unittest.TestCase):
    def test_strips_trailing_dash(self):
        self.assertEqual(strip_trailing_dash_suffix("手磨新疆红枣糕(-)"), "手磨新疆红枣糕")

    def test_strips_with_surrounding_spaces(self):
        self.assertEqual(strip_trailing_dash_suffix("纸巾 (-) "), "纸巾")

    def test_keeps_other_prefix_and_suffix(self):
        self.assertEqual(strip_trailing_dash_suffix("(普通)陈香菊普(-)"), "(普通)陈香菊普")
        self.assertEqual(strip_trailing_dash_suffix("(3只)黑金咸蛋黄糖沙翁"), "(3只)黑金咸蛋黄糖沙翁")

    def test_middle_dash_untouched(self):
        self.assertEqual(strip_trailing_dash_suffix("怪名(-)后缀菜"), "怪名(-)后缀菜")

    def test_clean_name_and_empty_input(self):
        self.assertEqual(strip_trailing_dash_suffix("金牌LuckIn虾饺皇"), "金牌LuckIn虾饺皇")
        self.assertEqual(strip_trailing_dash_suffix(None), "")
        self.assertEqual(strip_trailing_dash_suffix("(-)"), "")


class _FakeDishCatalog:
    """DishCatalog 的最小替身：固定返回一个档口，不接触真实数据库。"""

    async def resolve(self, dish_name):
        return "stub_station"


class _StubTableAdapter(PosSession):
    """仅用于单测 _parse_api_order_response 的最小载体。"""

    def __init__(self):
        from scraper.order_line_builder import OrderLineBuilder

        self.logger = logging.getLogger("test-table-scrape")
        self.dish_catalog = _FakeDishCatalog()
        self.order_lines = OrderLineBuilder(self.dish_catalog)


class TableParseStripTest(unittest.IsolatedAsyncioTestCase):
    async def test_dish_and_children_names_stripped(self):
        adapter = _StubTableAdapter()
        data = {
            "success": True,
            "data": {
                "bsCode": "YY01101-260720-0001",
                "scDetail": [
                    {
                        "itemName": "酱皇豉椒蒸凤爪(-)",
                        "lastQty": 2,
                        "lastPrice": 18.0,
                        "subtotal": 36.0,
                        "orderTime": "12:30",
                        "children": [
                            {"itemName": "四色烧卖(-)", "lastQty": 1},
                        ],
                    }
                ],
            },
        }
        orders = await adapter._parse_api_order_response(data, "A1")
        names = {order["dish_name"] for order in orders}
        self.assertEqual(names, {"酱皇豉椒蒸凤爪", "四色烧卖"})
        self.assertTrue(all(order["source"] == "dine_in" for order in orders))
        for order in orders:
            self.assertNotIn("(-)", order["business_flow_id"])
        main_flow_ids = [o["business_flow_id"] for o in orders if o["dish_name"] == "酱皇豉椒蒸凤爪"]
        self.assertEqual(
            main_flow_ids,
            ["YY01101-260720-0001_酱皇豉椒蒸凤爪_001", "YY01101-260720-0001_酱皇豉椒蒸凤爪_002"],
        )

    async def test_meituan_table_marked_delivery(self):
        adapter = _StubTableAdapter()
        data = {
            "success": True,
            "data": {
                "bsCode": "YY01101-260720-0009",
                "pointName": "美团12",
                "peopleQty": 0,
                "scDetail": [
                    {"itemName": "虾饺", "lastQty": 1, "lastPrice": 18.0, "orderTime": "12:30"},
                ],
            },
        }
        orders = await adapter._parse_api_order_response(data, "美团12")
        self.assertEqual(orders[0]["source"], "delivery")


class _FakeSession:
    def __init__(self):
        from scraper.order_line_builder import OrderLineBuilder

        self.dish_catalog = _FakeDishCatalog()
        self.order_lines = OrderLineBuilder(self.dish_catalog)
        self.config = {}


class _MinimalState:
    def __init__(self):
        self.delivery_bill_state = {}
        self.collected_delivery_bills = set()

    def save_delivery_bills(self):
        pass


class _StubDeliveryAdapter(DeliveryBillTracker):
    """仅用于单测 _get_delivery_bill_dishes 解析段的最小载体。"""

    def __init__(self, dish_list):
        super().__init__(_FakeSession(), _MinimalState())
        self._dish_list = dish_list

    def _biz_datetime_range(self, biz_date=None):
        return None, None

    async def fetch_settled_bill_raw_dishes(self, bill, begin, end):
        return self._dish_list


class DeliveryParseStripTest(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_dish_names_stripped(self):
        adapter = _StubDeliveryAdapter(
            [{"name": "美团渠道(-)", "lastQty": 1, "price": 0, "lastSubtotal": 0}]
        )
        bill = {
            "orderSource": "美团",
            "pointName": "美团1",
            "bsId": "b1",
            "bsCode": "YY01101-260720-0002",
            "settleTime": "2026-07-20 12:00:00",
        }
        orders = await adapter._get_delivery_bill_dishes(bill)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["dish_name"], "美团渠道")
        self.assertEqual(orders[0]["business_flow_id"], "YY01101-260720-0002_美团渠道_001")
        self.assertEqual(orders[0]["source"], "delivery")


class ReconcileAggregateStripTest(unittest.TestCase):
    """新旧 flow_id 格式共存时，对账两侧聚合键必须对齐，不产生误报补录。"""

    def test_old_flow_ids_match_pos_names_with_dash(self):
        pos_bills = {
            "YY01101-260720-0003": [
                {"name": "方形饭盒(-)", "lastQty": 2},
            ]
        }
        db_orders = [
            # 旧格式：flow_id 内嵌菜名带 (-)
            {"business_flow_id": "YY01101-260720-0003_方形饭盒(-)_001", "quantity": 1, "status": "已结"},
            # 新格式：干净名
            {"business_flow_id": "YY01101-260720-0003_方形饭盒_002", "quantity": 1, "status": "已结"},
        ]
        diffs = compute_reconcile_diff(
            aggregate_pos_dishes(pos_bills), aggregate_db_orders(db_orders)
        )
        self.assertEqual(diffs, [])

    def test_real_miss_still_detected(self):
        pos_bills = {
            "YY01101-260720-0004": [
                {"name": "纸巾(-)", "lastQty": 3},
            ]
        }
        db_orders = [
            {"business_flow_id": "YY01101-260720-0004_纸巾(-)_001", "quantity": 1, "status": "已结"},
        ]
        diffs = compute_reconcile_diff(
            aggregate_pos_dishes(pos_bills), aggregate_db_orders(db_orders)
        )
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].dish_name, "纸巾")
        self.assertEqual(diffs[0].missed_qty, 2.0)


if __name__ == "__main__":
    unittest.main()
