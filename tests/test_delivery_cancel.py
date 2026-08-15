#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外卖“消失即取消”检测、软删除/自愈、对账兜底与 source 列迁移的测试。"""

import logging
import tempfile
import unittest
from datetime import datetime

import aiosqlite

from config import settings
from database import CHINA_TZ, DatabaseManager
from db_core.table_db import migrate_orders_kds_columns
from scraper.delivery_bill_tracker import DELIVERY_CANCEL_MISS_THRESHOLD, DeliveryBillTracker
from scraper.settled_reconcile import sweep_cancelled_delivery_for_biz_date


def _delivery_order(bs_code: str, dish: str, seq: int = 1, *, price=10.0):
    return {
        "business_flow_id": f"{bs_code}_{dish}_{seq:03d}",
        "table_number": "美团1",
        "dish_name": dish,
        "quantity": 1,
        "order_time": datetime(2026, 7, 20, 11, 0, tzinfo=CHINA_TZ),
        "price": price,
        "total_amount": price,
        "status": "已结",
        "category": "其他",
        "station": "mingdang1",
        "priority": "normal",
        "notes": f"外卖平台:美团|来源:美团1",
        "source": "delivery",
    }


class DeliveryRepoTest(unittest.IsolatedAsyncioTestCase):
    """真实 DB：source 列持久化、退菜+归零、恢复、查询。"""

    async def asyncSetUp(self):
        self._old_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_dir
        self._tmpdir.cleanup()

    async def test_source_column_persisted(self):
        await self.db.save_orders([_delivery_order("YY001301-260720-0064", "香煎腊味萝卜糕")])
        async with self.db._conn.execute(
            "SELECT source FROM orders WHERE business_flow_id LIKE 'YY001301-260720-0064_%'"
        ) as cur:
            row = await cur.fetchone()
        self.assertEqual(row["source"], "delivery")

    async def test_mark_cancelled_zeroes_and_soft_deletes(self):
        bs = "YY001301-260720-0064"
        await self.db.save_orders([
            _delivery_order(bs, "萝卜糕", 1, price=12.0),
            _delivery_order(bs, "凤爪", 1, price=8.0),
        ])
        # 同 bsCode 前缀但堂食（source='')，验证不被误伤
        dine_in = _delivery_order(bs, "堂食菜", 1)
        dine_in["source"] = ""
        await self.db.save_orders([dine_in])

        affected = await self.db.mark_delivery_cancelled(bs)
        self.assertEqual(affected, 2)

        async with self.db._conn.execute(
            "SELECT quantity, total_amount, status, dish_status FROM orders "
            "WHERE source='delivery' AND business_flow_id LIKE ?",
            (f"{bs}_%",),
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            self.assertEqual(row["quantity"], 0)
            self.assertEqual(row["total_amount"], 0)
            self.assertEqual(row["status"], "退菜")
            self.assertEqual(row["dish_status"], "已取消")

        # 堂食行不受影响
        async with self.db._conn.execute(
            "SELECT quantity, status FROM orders WHERE source='' AND dish_name='堂食菜'"
        ) as cur:
            dine = await cur.fetchone()
        self.assertEqual(dine["quantity"], 1)
        self.assertEqual(dine["status"], "已结")

    async def test_mark_cancelled_is_idempotent(self):
        bs = "YY001301-260720-0100"
        await self.db.save_orders([_delivery_order(bs, "虾饺")])
        self.assertEqual(await self.db.mark_delivery_cancelled(bs), 1)
        # 已退菜的行不再重复计入
        self.assertEqual(await self.db.mark_delivery_cancelled(bs), 0)

    async def test_get_delivery_flow_ids_excludes_cancelled(self):
        bs = "YY001301-260720-0200"
        await self.db.save_orders([_delivery_order(bs, "叉烧包")])
        ids = await self.db.get_delivery_flow_ids()
        self.assertIn(f"{bs}_叉烧包_001", ids)
        await self.db.mark_delivery_cancelled(bs)
        ids_after = await self.db.get_delivery_flow_ids()
        self.assertNotIn(f"{bs}_叉烧包_001", ids_after)

    async def test_revert_restores_rows(self):
        bs = "YY001301-260720-0300"
        order = _delivery_order(bs, "肠粉", price=15.0)
        await self.db.save_orders([order])
        await self.db.mark_delivery_cancelled(bs)

        restored = await self.db.revert_delivery_cancelled([order])
        self.assertEqual(restored, 1)
        async with self.db._conn.execute(
            "SELECT quantity, total_amount, price, order_time, status, dish_status FROM orders "
            "WHERE business_flow_id = ?",
            (f"{bs}_肠粉_001",),
        ) as cur:
            row = await cur.fetchone()
        self.assertEqual(row["quantity"], 1)
        self.assertEqual(row["total_amount"], 15.0)
        self.assertEqual(row["price"], 15.0)
        self.assertEqual(row["order_time"], order["order_time"].isoformat())
        self.assertEqual(row["status"], "已结")
        self.assertEqual(row["dish_status"], "待出餐")

    async def test_revert_overwrites_with_freshly_refetched_order_time_and_price(self):
        """取消期间若 POS 侧改过下单时间/单价，重现自愈应以重新拉取的新鲜值覆盖旧值，而非沿用取消前的旧值。"""
        bs = "YY001301-260720-0301"
        original = _delivery_order(bs, "凤爪", price=8.0)
        await self.db.save_orders([original])
        await self.db.mark_delivery_cancelled(bs)

        fresh = dict(original)
        fresh["price"] = 9.5
        fresh["total_amount"] = 9.5
        fresh["order_time"] = datetime(2026, 7, 20, 12, 30, tzinfo=CHINA_TZ)

        restored = await self.db.revert_delivery_cancelled([fresh])
        self.assertEqual(restored, 1)
        async with self.db._conn.execute(
            "SELECT price, total_amount, order_time FROM orders WHERE business_flow_id = ?",
            (f"{bs}_凤爪_001",),
        ) as cur:
            row = await cur.fetchone()
        self.assertEqual(row["price"], 9.5)
        self.assertEqual(row["total_amount"], 9.5)
        self.assertEqual(row["order_time"], fresh["order_time"].isoformat())


class SourceMigrationBackfillTest(unittest.IsolatedAsyncioTestCase):
    """旧库无 source 列 → 迁移新增列并按 notes 回填外卖行。"""

    async def test_migration_adds_source_and_backfills(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            async with aiosqlite.connect(tmp.name) as conn:
                await conn.execute(
                    """CREATE TABLE orders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        business_flow_id TEXT, table_number TEXT NOT NULL,
                        dish_name TEXT NOT NULL, quantity INTEGER NOT NULL,
                        order_time TEXT NOT NULL, price REAL DEFAULT 0.0,
                        total_amount REAL DEFAULT 0.0, status TEXT DEFAULT '未结',
                        category TEXT DEFAULT '', station TEXT DEFAULT '',
                        priority TEXT DEFAULT 'normal', notes TEXT,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    )"""
                )
                await conn.execute(
                    "INSERT INTO orders (business_flow_id, table_number, dish_name, quantity, "
                    "order_time, notes, created_at, updated_at) VALUES "
                    "('b1_菜_001','美团1','菜',1,'t','外卖平台:美团|来源:美团1','t','t')"
                )
                await conn.execute(
                    "INSERT INTO orders (business_flow_id, table_number, dish_name, quantity, "
                    "order_time, notes, created_at, updated_at) VALUES "
                    "('t1_菜_001','8','菜',1,'t',NULL,'t','t')"
                )
                await conn.commit()

                await migrate_orders_kds_columns(conn)

                async with conn.execute("PRAGMA table_info(orders)") as cur:
                    cols = {row[1] for row in await cur.fetchall()}
                self.assertIn("source", cols)

                async with conn.execute(
                    "SELECT source FROM orders WHERE business_flow_id='b1_菜_001'"
                ) as cur:
                    delivery_row = await cur.fetchone()
                async with conn.execute(
                    "SELECT source FROM orders WHERE business_flow_id='t1_菜_001'"
                ) as cur:
                    table_row = await cur.fetchone()
                self.assertEqual(delivery_row[0], "delivery")
                self.assertEqual(table_row[0], "dine_in")


class _FakeSession:
    def __init__(self):
        self.config = {}


class _MinimalState:
    def __init__(self):
        self.delivery_bill_state = {}
        self.collected_delivery_bills = set()
        self.save_calls = 0

    def save_delivery_bills(self):
        self.save_calls += 1


class _StubDeliveryAdapter(DeliveryBillTracker):
    """仅用于单测 _sweep_cancelled_delivery 的最小载体。"""

    def __init__(self):
        self._minimal_state = _MinimalState()
        super().__init__(_FakeSession(), self._minimal_state, logger_=logging.getLogger("test-delivery"))

    @property
    def _delivery_bill_state(self):
        return self._state.delivery_bill_state

    @_delivery_bill_state.setter
    def _delivery_bill_state(self, value):
        self._state.delivery_bill_state = value

    @property
    def config(self):
        return self._session.config

    @config.setter
    def config(self, value):
        self._session.config = value

    async def _get_delivery_bill_dishes(self, bill):
        return bill.get("_dishes", [])


class _FakeDB:
    def __init__(self, mark_rows=3):
        self.cancelled = []
        self.reverted = []
        self._mark_rows = mark_rows

    @property
    def orders(self):
        return self

    async def mark_delivery_cancelled(self, bs_code):
        self.cancelled.append(bs_code)
        return self._mark_rows

    async def revert_delivery_cancelled(self, orders):
        self.reverted.append(orders)
        return len(orders)


class SweepThresholdTest(unittest.IsolatedAsyncioTestCase):
    """消失即取消：阈值、重现清零、已取消自愈。"""

    async def test_absent_reaches_threshold_then_cancelled(self):
        adapter = _StubDeliveryAdapter()
        adapter._delivery_bill_state = {"A": {"bs_id": "a", "miss_count": 0, "cancelled": False}}
        db = _FakeDB()

        # 连续缺席 K-1 次不取消
        for _ in range(DELIVERY_CANCEL_MISS_THRESHOLD - 1):
            await adapter._sweep_cancelled_delivery(db, {"OTHER"}, {"OTHER": {}})
            self.assertEqual(db.cancelled, [])
        # 第 K 次触发取消
        await adapter._sweep_cancelled_delivery(db, {"OTHER"}, {"OTHER": {}})
        self.assertEqual(db.cancelled, ["A"])
        self.assertTrue(adapter._delivery_bill_state["A"]["cancelled"])
        self.assertEqual(adapter._last_delivery_cancel_count, 3)

    async def test_configured_threshold_overrides_default(self):
        adapter = _StubDeliveryAdapter()
        adapter.config = {"settings": {"delivery_cancel_miss_threshold": 2}}
        adapter._delivery_bill_state = {"A": {"bs_id": "a", "miss_count": 0, "cancelled": False}}
        db = _FakeDB()
        await adapter._sweep_cancelled_delivery(db, {"OTHER"}, {"OTHER": {}})
        self.assertEqual(db.cancelled, [])  # 第 1 次不取消
        await adapter._sweep_cancelled_delivery(db, {"OTHER"}, {"OTHER": {}})
        self.assertEqual(db.cancelled, ["A"])  # 第 2 次即取消（自定义阈值=2）

    async def test_reappear_before_threshold_resets(self):
        adapter = _StubDeliveryAdapter()
        adapter._delivery_bill_state = {"A": {"bs_id": "a", "miss_count": 2, "cancelled": False}}
        db = _FakeDB()
        # A 重新出现 → 计数清零，不取消
        await adapter._sweep_cancelled_delivery(db, {"A"}, {"A": {"_dishes": []}})
        self.assertEqual(db.cancelled, [])
        self.assertEqual(adapter._delivery_bill_state["A"]["miss_count"], 0)

    async def test_cancelled_reappear_triggers_revert(self):
        adapter = _StubDeliveryAdapter()
        adapter._delivery_bill_state = {"A": {"bs_id": "a", "miss_count": 3, "cancelled": True}}
        db = _FakeDB()
        bill = {"_dishes": [{"business_flow_id": "A_菜_001", "quantity": 1, "total_amount": 10.0, "status": "已结"}]}
        await adapter._sweep_cancelled_delivery(db, {"A"}, {"A": bill})
        self.assertEqual(len(db.reverted), 1)
        self.assertFalse(adapter._delivery_bill_state["A"]["cancelled"])
        self.assertEqual(adapter._delivery_bill_state["A"]["miss_count"], 0)


class _StubReconcileAdapter:
    def __init__(self, present_bills, raise_fetch=False):
        self._present = present_bills
        self._raise = raise_fetch

    def biz_datetime_range(self, biz_date=None):
        return (
            datetime(2026, 7, 20, 6, tzinfo=CHINA_TZ),
            datetime(2026, 7, 21, 6, tzinfo=CHINA_TZ),
        )

    async def fetch_settled_bill_list(self, begin, end, *, delivery_only=False):
        if self._raise:
            raise RuntimeError("boom")
        return self._present


class _FakeReconcileDB:
    def __init__(self, flow_ids):
        self._flow_ids = flow_ids
        self.cancelled = []

    @property
    def orders(self):
        return self

    async def get_delivery_flow_ids(self, begin=None, end=None):
        return self._flow_ids

    async def mark_delivery_cancelled(self, bs_code):
        self.cancelled.append(bs_code)
        return 2


class ReconcileSafetyNetTest(unittest.IsolatedAsyncioTestCase):
    async def test_vanished_bs_codes_cancelled(self):
        present = [{"bsCode": "YY001301-260720-0001"}, {"bsCode": "YY001301-260720-0002"}]
        db = _FakeReconcileDB([
            "YY001301-260720-0001_菜_001",
            "YY001301-260720-0002_菜_001",
            "YY001301-260720-0003_菜_001",  # 消失
        ])
        adapter = _StubReconcileAdapter(present)
        summary = await sweep_cancelled_delivery_for_biz_date(adapter, db, "2026-07-20")
        self.assertEqual(db.cancelled, ["YY001301-260720-0003"])
        self.assertEqual(summary["cancelled_bills"], 1)
        self.assertEqual(summary["cancelled_rows"], 2)

    async def test_empty_present_is_skipped(self):
        db = _FakeReconcileDB(["YY001301-260720-0003_菜_001"])
        adapter = _StubReconcileAdapter([])
        summary = await sweep_cancelled_delivery_for_biz_date(adapter, db, "2026-07-20")
        self.assertTrue(summary["skipped"])
        self.assertEqual(db.cancelled, [])

    async def test_fetch_failure_is_skipped(self):
        db = _FakeReconcileDB(["YY001301-260720-0003_菜_001"])
        adapter = _StubReconcileAdapter([], raise_fetch=True)
        summary = await sweep_cancelled_delivery_for_biz_date(adapter, db, "2026-07-20")
        self.assertTrue(summary["skipped"])
        self.assertEqual(db.cancelled, [])


if __name__ == "__main__":
    unittest.main()
