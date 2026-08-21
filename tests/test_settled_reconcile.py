#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import unittest
from datetime import datetime
from pathlib import Path

from database import CHINA_TZ
from scraper.order_line_builder import OrderLineBuilder
from scraper.settled_reconcile import (
    ReconcileDiffItem,
    aggregate_db_orders,
    aggregate_pos_dishes,
    build_fix_orders_from_diffs,
    build_reconcile_result,
    compute_reconcile_diff,
    render_reconcile_markdown,
    run_settled_reconcile,
)
from services.data_quality_alerts import build_reconcile_alert_message, should_alert_reconcile

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "reconcile_2026-04-28"


class SettledReconcileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pos_path = FIXTURE_DIR / "pos_sample.json"
        db_path = FIXTURE_DIR / "db_sample.json"
        if pos_path.exists():
            cls.pos_bills = json.loads(pos_path.read_text(encoding="utf-8"))
            cls.db_orders = json.loads(db_path.read_text(encoding="utf-8"))
        else:
            cls.pos_bills = {
                "YY01101-260428-0053": [
                    {"name": "(普通)桐乡胎菊", "lastQty": 6},
                    {"name": "(3只)黑金咸蛋黄糖沙翁", "lastQty": 1},
                    {"name": "一品珍珠糯米鸡", "lastQty": 2},
                ],
                "YY01101-260428-0171": [
                    {"name": "冲账", "lastQty": 4},
                    {"name": "(1只)香煎手工韭菜饺", "lastQty": 1},
                ],
            }
            cls.db_orders = [
                {
                    "business_flow_id": "YY01101-260428-0053_(普通)桐乡胎菊_001",
                    "dish_name": "(普通)桐乡胎菊",
                    "quantity": 1,
                    "status": "已结",
                },
                {
                    "business_flow_id": "YY01101-260428-0053_一品珍珠糯米鸡_001",
                    "dish_name": "一品珍珠糯米鸡",
                    "quantity": 1,
                    "status": "已结",
                },
            ]

    def test_compute_partial_miss(self):
        pos = aggregate_pos_dishes(self.pos_bills)
        db = aggregate_db_orders(self.db_orders)
        diffs = compute_reconcile_diff(pos, db)
        by_key = {(d.bs_code, d.dish_name): d for d in diffs}
        self.assertAlmostEqual(by_key[("YY01101-260428-0053", "(普通)桐乡胎菊")].missed_qty, 5.0)
        self.assertEqual(by_key[("YY01101-260428-0053", "(3只)黑金咸蛋黄糖沙翁")].diff_type, "full")

    def test_build_reconcile_result_metrics(self):
        result = build_reconcile_result("2026-04-28", self.pos_bills, self.db_orders)
        self.assertGreater(result.missed_qty, 0)
        self.assertGreater(result.missed_keys, 0)
        markdown = render_reconcile_markdown(result)
        self.assertIn("2026-04-28", markdown)
        self.assertIn("漏抓", markdown)

    def test_alert_threshold(self):
        result = build_reconcile_result("2026-04-28", self.pos_bills, self.db_orders)
        self.assertTrue(should_alert_reconcile(result))
        message = build_reconcile_alert_message(result)
        self.assertIn("数据质量告警", message)


class _FakeCatalog:
    async def resolve(self, dish_name):
        return "changfen"


class BuildFixOrdersSourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_reconcile_fix_sets_dine_in_and_delivery(self):
        builder = OrderLineBuilder(_FakeCatalog())
        diffs = [
            ReconcileDiffItem("YY1", "虾饺", 1, 0, 1, "full"),
            ReconcileDiffItem("YY2", "烧卖", 1, 0, 1, "full"),
        ]
        bills_meta = {
            "YY1": {"pointName": "A区22", "orderSource": "扫码点餐", "peopleQty": 2},
            "YY2": {"pointName": "美团3", "orderSource": "美团", "peopleQty": 0},
        }
        rows = await build_fix_orders_from_diffs(diffs, bills_meta, order_lines=builder)
        by_bs = {row["notes"].split("|")[1]: row for row in rows}
        self.assertEqual(by_bs["YY1"]["source"], "dine_in")
        self.assertEqual(by_bs["YY2"]["source"], "delivery")
        self.assertEqual(by_bs["YY1"]["notes"], "reconcile_fix|YY1")
        self.assertEqual(by_bs["YY2"]["notes"], "reconcile_fix|YY2")


class _StubProgressAdapter:
    """仅为驱动 run_settled_reconcile 的进度回调，最小化 stub。"""

    def __init__(self, bills):
        self._bills = bills

    def biz_datetime_range(self, biz_date):
        return (
            datetime(2026, 7, 20, 6, tzinfo=CHINA_TZ),
            datetime(2026, 7, 21, 6, tzinfo=CHINA_TZ),
        )

    async def fetch_settled_bill_list(self, begin, end, *, delivery_only=False):
        return self._bills

    async def fetch_settled_bill_raw_dishes(self, bill, begin, end):
        return []


class _StubProgressDB:
    @property
    def orders(self):
        return self

    async def get_orders(self, **kwargs):
        return []


class RunSettledReconcileProgressTest(unittest.IsolatedAsyncioTestCase):
    async def test_on_progress_called_once_per_bill_with_running_total(self):
        bills = [{"bsCode": f"YY001301-260720-000{i}"} for i in range(1, 4)]
        adapter = _StubProgressAdapter(bills)
        db = _StubProgressDB()
        calls = []

        async def on_progress(current, total):
            calls.append((current, total))

        await run_settled_reconcile(
            adapter, db, "2026-07-20", sleep_between_bills_s=0, on_progress=on_progress
        )

        self.assertEqual(calls, [(1, 3), (2, 3), (3, 3)])

    async def test_no_progress_callback_is_optional(self):
        adapter = _StubProgressAdapter([{"bsCode": "YY001301-260720-0001"}])
        db = _StubProgressDB()
        # 不传 on_progress 不应报错（向后兼容旧调用方）。
        result, bills_meta = await run_settled_reconcile(adapter, db, "2026-07-20", sleep_between_bills_s=0)
        self.assertEqual(result.biz_date, "2026-07-20")
        self.assertIn("YY001301-260720-0001", bills_meta)


if __name__ == "__main__":
    unittest.main()
