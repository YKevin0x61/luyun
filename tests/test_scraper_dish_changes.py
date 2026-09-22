#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

from config import settings
from database import CHINA_TZ, DatabaseManager
from scraper.order_flow_ids import (
    allocate_incremental_flow_ids,
    allocate_reconcile_flow_ids,
    biz_date_from_bs_code,
    parse_order_flow_id,
)
from scraper.pos_session import PosSession
from scraper.state_store import ScraperStateStore
from scraper.table_change_detector import (
    DINE_IN_CANCEL_MISS_THRESHOLD,
    TableChangeDetector,
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

    def test_biz_date_from_bs_code(self):
        self.assertEqual(biz_date_from_bs_code("YY001301-260820-0001"), "2026-08-20")
        self.assertEqual(biz_date_from_bs_code("YY001302-260821-0012"), "2026-08-21")
        self.assertIsNone(biz_date_from_bs_code("A"))
        self.assertIsNone(biz_date_from_bs_code(""))

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


def _pos_line(
    flow_id: str,
    *,
    qty: int = 1,
    dish: str = "虾饺",
    price: float = 12.0,
    notes: str = "",
    table: str = "8",
):
    return {
        "business_flow_id": flow_id,
        "table_number": table,
        "dish_name": dish,
        "quantity": qty,
        "price": price,
        "total_amount": price * qty,
        "status": "未结",
        "order_time": datetime(2026, 8, 18, 10, 0, tzinfo=CHINA_TZ),
        "source": "dine_in",
        "station": "shulong",
        "notes": notes,
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

    async def _drive_shortfall(
        self, table_number, current_orders, previous_amount, current_amount
    ):
        """连续 DINE_IN_CANCEL_MISS_THRESHOLD 轮取材成功却一直缺份，返回最后一轮结果。

        退菜现在要过去抖（对齐外卖链路的 DELIVERY_CANCEL_MISS_THRESHOLD），
        单轮少份只记一次未确认，不再立即退菜。
        """
        result = ([], list(current_orders))
        for _ in range(DINE_IN_CANCEL_MISS_THRESHOLD):
            result = await self.detector._detect_dish_changes(
                table_number,
                current_orders,
                previous_amount,
                current_amount,
                orders=self.db.orders,
            )
        return result

    async def test_qty_down_cancels_original_and_does_not_emit_refund_rows(self):
        line_a = _pos_line("t8_虾饺_001")
        line_b = _pos_line("t8_虾饺_002")
        await self.db.orders.batch_insert_orders([line_a, line_b])
        self.state.previous_table_orders["8"] = [line_a, line_b]

        changed, _snapshot = await self._drive_shortfall("8", [line_a], 24.0, 12.0)
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
        changed, _snapshot = await self._drive_shortfall("8", [], 12.0, 0.0)
        self.assertEqual(changed, [])
        self.assertEqual(await self.db.orders.get_orders(limit=-1), [])

    async def test_qty_down_no_onion_does_not_cancel_unnoted(self):
        plain = _pos_line("t8_虾饺_plain", notes="")
        no_onion = _pos_line("t8_虾饺_no_onion", notes="免葱")
        await self.db.orders.batch_insert_orders([plain, no_onion])
        self.state.previous_table_orders["8"] = [plain, no_onion]

        changed, _snapshot = await self._drive_shortfall("8", [plain], 24.0, 12.0)
        self.assertEqual(changed, [])
        cancelled = await self.db.orders.get_order_by_id("t8_虾饺_no_onion")
        kept = await self.db.orders.get_order_by_id("t8_虾饺_plain")
        self.assertEqual(cancelled["dish_status"], "已取消")
        self.assertEqual(cancelled["quantity"], 0)
        self.assertEqual(kept["dish_status"], "待出餐")
        self.assertEqual(kept["quantity"], 1)

    async def test_qty_up_no_onion_does_not_increase_unnoted(self):
        plain = _pos_line("t8_虾饺_plain", notes="")
        no_onion = _pos_line("t8_虾饺_no_onion", notes="免葱")
        await self.db.orders.batch_insert_orders([plain, no_onion])
        self.state.previous_table_orders["8"] = [plain, no_onion]
        extra = _pos_line("t8_虾饺_pos_extra", notes="免葱")

        changed, _snapshot = await self.detector._detect_dish_changes(
            "8",
            [plain, no_onion, extra],
            24.0,
            36.0,
            orders=self.db.orders,
        )
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["notes"], "免葱")
        self.assertEqual(changed[0]["quantity"], 1)
        self.assertNotIn("_refund_", changed[0]["business_flow_id"])
        plain_row = await self.db.orders.get_order_by_id("t8_虾饺_plain")
        no_onion_row = await self.db.orders.get_order_by_id("t8_虾饺_no_onion")
        self.assertEqual(plain_row["dish_status"], "待出餐")
        self.assertEqual(plain_row["quantity"], 1)
        self.assertEqual(no_onion_row["dish_status"], "待出餐")
        self.assertEqual(no_onion_row["quantity"], 1)

    async def test_platform_prefix_qty_up_restores_empty_notes_row(self):
        original = _pos_line("t8_虾饺_001", notes="")
        no_onion = _pos_line("t8_虾饺_no_onion", notes="免葱")
        await self.db.orders.batch_insert_orders([original, no_onion])
        await self.db.orders.cancel_dine_in_portions("8", "虾饺", 1)
        self.state.previous_table_orders["8"] = [no_onion]
        current = [
            no_onion,
            _pos_line("t8_虾饺_pos", notes="外卖平台:美团|来源:美团1"),
        ]

        changed, _snapshot = await self.detector._detect_dish_changes(
            "8",
            current,
            12.0,
            24.0,
            orders=self.db.orders,
        )
        restored = await self.db.orders.get_order_by_id("t8_虾饺_001")
        live_no_onion = await self.db.orders.get_order_by_id("t8_虾饺_no_onion")
        self.assertEqual(restored["dish_status"], "待出餐")
        self.assertEqual(restored["quantity"], 1)
        self.assertEqual(live_no_onion["dish_status"], "待出餐")
        self.assertEqual(live_no_onion["quantity"], 1)
        self.assertEqual(len(changed), 0)

    async def test_qty_down_empty_identity_cancels_platform_prefix_not_no_onion(self):
        platform = _pos_line("t8_虾饺_platform", notes="外卖平台:美团|来源:美团1")
        no_onion = _pos_line("t8_虾饺_no_onion", notes="免葱")
        await self.db.orders.batch_insert_orders([platform, no_onion])
        self.state.previous_table_orders["8"] = [platform, no_onion]

        changed, _snapshot = await self._drive_shortfall("8", [no_onion], 24.0, 12.0)
        self.assertEqual(changed, [])
        cancelled = await self.db.orders.get_order_by_id("t8_虾饺_platform")
        kept = await self.db.orders.get_order_by_id("t8_虾饺_no_onion")
        self.assertEqual(cancelled["dish_status"], "已取消")
        self.assertEqual(kept["dish_status"], "待出餐")


class FetchTableOrdersFailureShapeTests(unittest.IsolatedAsyncioTestCase):
    """取材失败与“这桌真的没有菜”不能同形：失败返回 None，成功但没明细才返回 []。"""

    def _session(self):
        session = PosSession.__new__(PosSession)  # 不建浏览器、不登录，只验取材契约
        session.logger = logging.getLogger("test-fetch-table-orders")
        return session

    async def test_missing_point_id_returns_none(self):
        session = self._session()
        self.assertIsNone(await session.fetch_table_orders("8", ""))

    async def test_http_error_returns_none(self):
        session = self._session()

        async def fake_raw(point_id):
            return 500, "boom"

        session._bs_detail_api_request_raw = fake_raw
        self.assertIsNone(await session.fetch_table_orders("8", "p8"))

    async def test_non_json_response_returns_none(self):
        session = self._session()

        async def fake_raw(point_id):
            return 200, "<html>登录页</html>"

        session._bs_detail_api_request_raw = fake_raw
        self.assertIsNone(await session.fetch_table_orders("8", "p8"))

    async def test_success_false_returns_none(self):
        session = self._session()

        async def fake_raw(point_id):
            return 200, {"success": False, "errorMsg": "会话失效"}

        session._bs_detail_api_request_raw = fake_raw
        self.assertIsNone(await session.fetch_table_orders("8", "p8"))

    async def test_exception_returns_none(self):
        session = self._session()

        async def boom(point_id):
            raise RuntimeError("网络中断")

        session._bs_detail_api_request_raw = boom
        self.assertIsNone(await session.fetch_table_orders("8", "p8"))

    async def test_successful_response_without_dishes_returns_empty_list(self):
        session = self._session()

        async def fake_raw(point_id):
            return 200, {"success": True, "data": {"bsCode": "B1", "scDetail": []}}

        async def fake_parse(data, table_number):
            return []

        session._bs_detail_api_request_raw = fake_raw
        session._parse_api_order_response = fake_parse
        self.assertEqual(await session.fetch_table_orders("8", "p8"), [])


def _table(number, amount, *, orders=None, detail="ok", point_id=None):
    return {
        "table_number": number,
        "amount": amount,
        "point_id": f"p{number}" if point_id is None else point_id,
        "orders": list(orders or []),
        "detail": detail,
    }


class _FakeTableSession:
    """假 POS session：只实现 monitor_table_orders 用到的口子，不连 POS。"""

    def __init__(self, tables):
        self.tables = list(tables)
        self.fetch_calls = []

    async def ensure_ready(self):
        return True

    async def scrape_table_data(self):
        return [dict(table) for table in self.tables]

    def resolve_point_id(self, table_number):
        return f"p{table_number}"

    async def fetch_table_orders(self, table_number, point_id):
        self.fetch_calls.append((table_number, point_id))
        table = next(t for t in self.tables if t["table_number"] == table_number)
        if table["detail"] == "fail":
            return None
        return [dict(line) for line in table["orders"]]


class _RecordingOrdersPort:
    """记录堂食退菜/恢复调用的假 orders 端口（不连库）。"""

    def __init__(self, *, restore_returns_row=False):
        self.cancelled = []
        self.restored = []
        self.restore_returns_row = restore_returns_row

    async def cancel_dine_in_portions(self, table_number, dish_name, portions, notes=""):
        self.cancelled.append((table_number, dish_name, portions, notes))
        return portions

    async def restore_dine_in_cancelled(
        self, table_number, dish_name, order=None, notes=None
    ):
        self.restored.append((table_number, dish_name))
        return (
            {"id": f"{table_number}-{dish_name}"} if self.restore_returns_row else None
        )


class MonitorTableOrdersDetailFailureTests(unittest.IsolatedAsyncioTestCase):
    """CORR-01：明细取材失败不能进差分、不能推进状态，也不能被当成“这桌没菜”。"""

    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self._tmpdir.name, "table_state.json")
        self.state = ScraperStateStore(
            table_state_file=self.state_file,
            delivery_bills_file=os.path.join(self._tmpdir.name, "delivery_bills.json"),
        )
        self.orders = _RecordingOrdersPort()
        self.detector = TableChangeDetector(
            session=None,
            state_store=self.state,
            logger_=logging.getLogger("test-table-monitor"),
        )

    async def asyncTearDown(self):
        self._tmpdir.cleanup()

    async def _run_round(self, tables):
        self.detector._session = _FakeTableSession(tables)
        return await self.detector.monitor_table_orders(orders=self.orders)

    def _seed_tracked_table(self, table_number, amount, orders):
        self.state.previous_tables_state = {table_number: amount}
        self.state.previous_table_orders = {table_number: list(orders)}
        self.state.is_first_run = False

    async def test_failed_round_cancels_nothing_and_freezes_state(self):
        """第 1 轮：金额 10→20 但明细取材失败 → 不许退菜，状态不许推进。"""
        line = _pos_line("t8_虾饺_001")
        self._seed_tracked_table("8", 10.0, [line])

        changed = await self._run_round([_table("8", 20.0, detail="fail")])

        self.assertEqual(changed, [])
        self.assertEqual(self.orders.cancelled, [])
        self.assertEqual(self.orders.restored, [])
        self.assertEqual(self.state.previous_tables_state["8"], 10.0)
        self.assertEqual(self.state.previous_table_orders["8"], [line])
        with open(self.state_file, encoding="utf-8") as fh:
            persisted = json.load(fh)
        self.assertEqual(persisted["table_states"]["8"], 10.0)

    async def test_round_after_failure_is_a_no_op_when_detail_matches(self):
        """第 2 轮：金额没再变、取材成功 → 既非新增也不退菜，成功后正常推进状态。"""
        line = _pos_line("t8_虾饺_001")
        self._seed_tracked_table("8", 10.0, [line])

        await self._run_round([_table("8", 20.0, detail="fail")])
        changed = await self._run_round([_table("8", 20.0, orders=[line])])

        self.assertEqual(changed, [])
        self.assertEqual(self.orders.cancelled, [])
        self.assertEqual(self.orders.restored, [])
        self.assertEqual(self.state.previous_tables_state["8"], 20.0)

    async def test_self_heal_after_failed_round_restores_dish(self):
        """取材失败那一轮之后，金额再变一次且成功 → 正常补回此前退掉的那份。"""
        line = _pos_line("t8_虾饺_001")
        self._seed_tracked_table("8", 10.0, [line])

        await self._run_round([_table("8", 20.0, detail="fail")])
        self.assertEqual(self.orders.cancelled, [])

        self.orders.restore_returns_row = True
        extra = _pos_line("t8_虾饺_002")
        changed = await self._run_round([_table("8", 24.0, orders=[line, extra])])

        self.assertEqual(self.orders.cancelled, [])
        self.assertEqual(self.orders.restored, [("8", "虾饺")])
        self.assertEqual(changed, [])

    async def test_dish_shortfall_cancels_only_after_consecutive_misses(self):
        """连续缺份未达阈值不退菜，达阈值才退一份。"""
        first = _pos_line("t8_虾饺_001")
        second = _pos_line("t8_虾饺_002")
        self._seed_tracked_table("8", 24.0, [first, second])

        for round_index in range(DINE_IN_CANCEL_MISS_THRESHOLD - 1):
            await self._run_round([_table("8", 12.0, orders=[first])])
            self.assertEqual(
                self.orders.cancelled, [], f"第 {round_index + 1} 轮就退菜了"
            )

        await self._run_round([_table("8", 12.0, orders=[first])])
        self.assertEqual(self.orders.cancelled, [("8", "虾饺", 1, "")])

    async def test_dish_returning_before_threshold_neither_cancels_nor_duplicates(self):
        """缺份未确认期间菜又回来 → 不退菜，也不能被当成新菜重复插行。"""
        first = _pos_line("t8_虾饺_001")
        second = _pos_line("t8_虾饺_002")
        self._seed_tracked_table("8", 24.0, [first, second])

        await self._run_round([_table("8", 12.0, orders=[first])])
        self.assertEqual(self.orders.cancelled, [])
        changed = await self._run_round([_table("8", 24.0, orders=[first, second])])

        self.assertEqual(self.orders.cancelled, [])
        self.assertEqual(changed, [])
        self.assertEqual(self.state.previous_tables_state["8"], 24.0)

    async def test_shortfall_without_amount_drop_never_cancels(self):
        """金额回到基线（没有“金额确实变小”的佐证）→ 一直不退菜。"""
        first = _pos_line("t8_虾饺_001")
        second = _pos_line("t8_虾饺_002")
        self._seed_tracked_table("8", 24.0, [first, second])

        await self._run_round([_table("8", 12.0, orders=[first])])
        for _ in range(DINE_IN_CANCEL_MISS_THRESHOLD + 1):
            await self._run_round([_table("8", 24.0, orders=[first])])

        self.assertEqual(self.orders.cancelled, [])

    async def test_failed_round_neither_cancels_nor_resets_miss_streak(self):
        """取材失败那一轮不参与去抖：不清零也不推进，成功后接着数。"""
        first = _pos_line("t8_虾饺_001")
        second = _pos_line("t8_虾饺_002")
        self._seed_tracked_table("8", 24.0, [first, second])

        await self._run_round([_table("8", 12.0, orders=[first])])  # 第 1 次缺份
        await self._run_round([_table("8", 12.0, detail="fail")])  # 失败轮
        await self._run_round([_table("8", 12.0, orders=[first])])  # 第 2 次缺份
        self.assertEqual(self.orders.cancelled, [])
        await self._run_round([_table("8", 12.0, orders=[first])])  # 第 3 次缺份
        self.assertEqual(self.orders.cancelled, [("8", "虾饺", 1, "")])

    async def test_new_table_detail_failure_is_retried_next_round(self):
        """新桌台首轮取材失败 → 该桌不被写入状态，下一轮重新取材并正常入库。"""
        self.state.is_first_run = False
        line = _pos_line("t9_虾饺_001", table="9")

        changed = await self._run_round([_table("9", 30.0, detail="fail")])

        self.assertEqual(changed, [])
        self.assertNotIn("9", self.state.previous_tables_state)
        self.assertNotIn("9", self.state.previous_table_orders)

        changed = await self._run_round([_table("9", 30.0, orders=[line])])

        self.assertEqual(
            [row["business_flow_id"] for row in changed], ["t9_虾饺_001"]
        )
        self.assertEqual(changed[0]["change_type"], "新增")
        self.assertEqual(self.state.previous_tables_state["9"], 30.0)


if __name__ == "__main__":
    unittest.main()
