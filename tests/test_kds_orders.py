#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KDS 字段迁移：orders 表 dish_status / ready_time。"""

import os
import tempfile
import unittest

import asyncio
import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.dishes as dishes_module
import api.orders as orders_module
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services import auth_service
from services.app_runtime import AppRuntime, set_runtime

_LEGACY_ORDERS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_flow_id TEXT,
        table_number TEXT NOT NULL,
        dish_name TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        order_time TEXT NOT NULL,
        price REAL DEFAULT 0.0,
        total_amount REAL DEFAULT 0.0,
        status TEXT DEFAULT '未结',
        category TEXT DEFAULT '',
        station TEXT DEFAULT '',
        priority TEXT DEFAULT 'normal',
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
"""


class KdsSchemaMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name

    async def asyncTearDown(self):
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _orders_columns(self, db: DatabaseManager) -> set[str]:
        async with db.table("orders").conn.cursor() as cursor:
            await cursor.execute("PRAGMA table_info(orders)")
            rows = await cursor.fetchall()
        return {row[1] for row in rows}

    async def test_fresh_orders_db_has_kds_columns(self):
        db = DatabaseManager()
        self.assertTrue(await db.connect())

        cols = await self._orders_columns(db)
        self.assertIn("dish_status", cols)
        self.assertIn("ready_time", cols)
        self.assertIn("steamer_id", cols)
        self.assertIn("port_index", cols)
        self.assertIn("stack_order", cols)
        self.assertIn("loaded_at", cols)

        await db.close()

    async def test_legacy_orders_db_migrates_kds_columns(self):
        orders_path = settings.DATABASE_PATHS["orders"]
        os.makedirs(os.path.dirname(orders_path), exist_ok=True)

        async with aiosqlite.connect(orders_path) as conn:
            await conn.executescript(_LEGACY_ORDERS_SCHEMA)
            await conn.commit()

        db = DatabaseManager()
        self.assertTrue(await db.connect())

        cols = await self._orders_columns(db)
        self.assertIn("dish_status", cols)
        self.assertIn("ready_time", cols)
        self.assertIn("steamer_id", cols)
        self.assertIn("port_index", cols)
        self.assertIn("stack_order", cols)
        self.assertIn("loaded_at", cols)

        await db.close()


class KdsGetOrdersTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        await self.db.batch_insert_orders([{
            "business_flow_id": "kds-001",
            "table_number": "A1",
            "dish_name": "虾饺",
            "quantity": 2,
            "order_time": datetime(2026, 6, 30, 10, 0, tzinfo=CHINA_TZ),
            "station": "shulong",
            "status": "未结",
        }])
        tdb = self.db.table("orders")
        await tdb.execute(
            "UPDATE orders SET dish_status = '已制作待上菜', ready_time = ? WHERE business_flow_id = ?",
            ("2026-06-30T10:30:00+08:00", "kds-001"),
        )
        await tdb.commit()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_get_orders_includes_dish_status(self):
        rows = await self.db.get_orders(limit=-1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dish_status"], "已制作待上菜")
        self.assertEqual(rows[0]["ready_time"], "2026-06-30T10:30:00+08:00")
        self.assertIn("_id", rows[0])

    async def test_get_orders_filters_by_dish_status(self):
        pending = await self.db.get_orders(dish_status="待出餐", limit=-1)
        self.assertEqual(len(pending), 0)
        ready = await self.db.get_orders(dish_status="已制作待上菜", limit=-1)
        self.assertEqual(len(ready), 1)


def _run_async(coro):
    return asyncio.run(coro)


@pytest.fixture
def orders_client(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run_async(db.connect())
    set_runtime(AppRuntime(db=db))
    _run_async(auth_service.init_user("admin", "password123"))
    plain, _ = _run_async(auth_service.issue_api_token(label="kds-test"))
    admin_headers = {"X-Admin-Token": plain}

    app = FastAPI()
    app.include_router(orders_module.router)

    async def _get_db():
        return db

    app.dependency_overrides[orders_module.get_db] = _get_db
    with TestClient(app) as client:
        yield client, db, admin_headers
    _run_async(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _seed_pending(db):
    _run_async(db.batch_insert_orders([{
        "business_flow_id": "cook-001",
        "table_number": "B2",
        "dish_name": "虾饺",
        "quantity": 3,
        "order_time": datetime(2026, 6, 30, 11, 0, tzinfo=CHINA_TZ),
        "station": "shulong",
        "status": "未结",
    }]))


def _seed_orders(db, rows):
    _run_async(db.batch_insert_orders(rows))
    return {row["business_flow_id"]: row for row in _run_async(db.get_orders(limit=-1))}


def _cooking_line(order, **overrides):
    line = {
        "order_id": order["_id"],
        "table_number": order["table_number"],
        "complete_quantity": int(order["quantity"]),
        "original_quantity": int(order["quantity"]),
    }
    line.update(overrides)
    return line


def _confirm_body(orders, *, dish_name="肠粉", station="changfen", complete_quantity=None):
    if complete_quantity is None:
        complete_quantity = sum(int(item["complete_quantity"]) for item in orders)
    return {
        "dish_name": dish_name,
        "station": station,
        "complete_quantity": complete_quantity,
        "orders": orders,
        "operator_id": "test",
        "ready_time": "2026-06-30T11:05:00+08:00",
    }


def _conflict_pairs(resp):
    detail = resp.json()["detail"]
    return {(item["order_id"], item["reason"]) for item in detail["conflicts"]}


def _mark_dish_status(db, order_id, dish_status):
    async def _run():
        tdb = db.table("orders")
        await tdb.execute(
            "UPDATE orders SET dish_status = ? WHERE id = ?",
            (dish_status, order_id),
        )
        await tdb.commit()

    _run_async(_run())


def test_complete_cooking_full(orders_client):
    client, db, admin_headers = orders_client
    _seed_pending(db)
    row = _run_async(db.get_orders(limit=1))[0]
    order_id = row["_id"]
    resp = client.post("/api/orders/complete-cooking", json={
        "dish_name": "虾饺",
        "station": "shulong",
        "complete_quantity": 3,
        "orders": [{
            "order_id": order_id,
            "table_number": "B2",
            "complete_quantity": 3,
            "original_quantity": 3,
        }],
        "operator_id": "test",
        "ready_time": "2026-06-30T11:05:00+08:00",
    }, headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    updated = _run_async(db.get_order_by_id(order_id))
    assert updated["dish_status"] == "已制作待上菜"
    assert updated["ready_time"] == "2026-06-30T11:05:00+08:00"


def test_complete_cooking_partial_split(orders_client):
    client, db, admin_headers = orders_client
    _seed_pending(db)
    row = _run_async(db.get_orders(limit=1))[0]
    order_id = row["_id"]
    resp = client.post("/api/orders/complete-cooking", json={
        "dish_name": "虾饺",
        "station": "shulong",
        "complete_quantity": 2,
        "orders": [{
            "order_id": order_id,
            "table_number": "B2",
            "complete_quantity": 2,
            "original_quantity": 3,
        }],
        "operator_id": "test",
        "ready_time": "2026-06-30T11:06:00+08:00",
    }, headers=admin_headers)
    assert resp.status_code == 200
    rows = _run_async(db.get_orders(limit=-1))
    assert len(rows) == 2
    pending = [r for r in rows if r["dish_status"] == "待出餐"]
    ready = [r for r in rows if r["dish_status"] == "已制作待上菜"]
    assert len(pending) == 1 and pending[0]["quantity"] == 1
    assert len(ready) == 1 and ready[0]["quantity"] == 2


def test_complete_cooking_repeated_partial_uses_db_quantity(orders_client):
    """连续部分出餐时以数据库当前数量为准，忽略过期的 original_quantity。"""
    client, db, admin_headers = orders_client
    _seed_pending(db)
    row = _run_async(db.get_orders(limit=1))[0]
    order_id = row["_id"]
    payload = {
        "dish_name": "虾饺",
        "station": "shulong",
        "complete_quantity": 1,
        "orders": [{
            "order_id": order_id,
            "table_number": "B2",
            "complete_quantity": 1,
            "original_quantity": 3,
        }],
        "operator_id": "test",
        "ready_time": "2026-06-30T11:06:00+08:00",
    }
    for _ in range(3):
        resp = client.post("/api/orders/complete-cooking", json=payload, headers=admin_headers)
        assert resp.status_code == 200
    rows = _run_async(db.get_orders(limit=-1))
    pending = [r for r in rows if r["dish_status"] == "待出餐"]
    ready = [r for r in rows if r["dish_status"] == "已制作待上菜"]
    assert len(pending) == 0
    assert sum(r["quantity"] for r in ready) == 3


def test_complete_cooking_by_business_flow_id(orders_client):
    """business_flow_id 可替代数字 id 定位订单。"""
    client, db, admin_headers = orders_client
    _seed_pending(db)
    row = _run_async(db.get_orders(limit=1))[0]
    resp = client.post("/api/orders/complete-cooking", json={
        "dish_name": "虾饺",
        "station": "shulong",
        "complete_quantity": 3,
        "orders": [{
            "order_id": "invalid-id",
            "business_flow_id": row["business_flow_id"],
            "table_number": "B2",
            "complete_quantity": 3,
            "original_quantity": 3,
        }],
        "operator_id": "test",
        "ready_time": "2026-06-30T11:05:00+08:00",
    }, headers=admin_headers)
    assert resp.status_code == 200
    updated = _run_async(db.get_order_by_id(row["_id"]))
    assert updated["dish_status"] == "已制作待上菜"


def test_complete_cooking_multi_dish_writes_all(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_orders(db, [
        {
            "business_flow_id": "multi-changfen",
            "table_number": "A1",
            "dish_name": "肠粉",
            "quantity": 1,
            "order_time": datetime(2026, 6, 30, 11, 0, tzinfo=CHINA_TZ),
            "station": "changfen",
            "status": "未结",
        },
        {
            "business_flow_id": "multi-shulong",
            "table_number": "B2",
            "dish_name": "虾饺",
            "quantity": 2,
            "order_time": datetime(2026, 6, 30, 11, 1, tzinfo=CHINA_TZ),
            "station": "shulong",
            "status": "未结",
        },
    ])
    changfen = seeded["multi-changfen"]
    shulong = seeded["multi-shulong"]

    resp = client.post(
        "/api/orders/complete-cooking",
        json=_confirm_body([
            _cooking_line(changfen),
            _cooking_line(shulong),
        ]),
        headers=admin_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["updated_count"] == 2
    assert _run_async(db.get_order_by_id(changfen["_id"]))["dish_status"] == "已制作待上菜"
    assert _run_async(db.get_order_by_id(shulong["_id"]))["dish_status"] == "已制作待上菜"


def test_complete_cooking_mixed_conflicts_writes_nothing(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_orders(db, [
        {
            "business_flow_id": "mix-valid-fen",
            "table_number": "A1",
            "dish_name": "肠粉",
            "quantity": 1,
            "order_time": datetime(2026, 6, 30, 11, 0, tzinfo=CHINA_TZ),
            "station": "changfen",
            "status": "未结",
        },
        {
            "business_flow_id": "mix-valid-jiao",
            "table_number": "B2",
            "dish_name": "虾饺",
            "quantity": 1,
            "order_time": datetime(2026, 6, 30, 11, 1, tzinfo=CHINA_TZ),
            "station": "shulong",
            "status": "未结",
        },
        {
            "business_flow_id": "mix-refund_虾饺_refund_1",
            "table_number": "C3",
            "dish_name": "虾饺",
            "quantity": 1,
            "order_time": datetime(2026, 6, 30, 11, 2, tzinfo=CHINA_TZ),
            "station": "shulong",
            "status": "退菜",
        },
        {
            "business_flow_id": "mix-ready",
            "table_number": "D4",
            "dish_name": "虾饺",
            "quantity": 1,
            "order_time": datetime(2026, 6, 30, 11, 3, tzinfo=CHINA_TZ),
            "station": "shulong",
            "status": "未结",
        },
        {
            "business_flow_id": "mix-qty",
            "table_number": "E5",
            "dish_name": "肠粉",
            "quantity": 1,
            "order_time": datetime(2026, 6, 30, 11, 4, tzinfo=CHINA_TZ),
            "station": "changfen",
            "status": "未结",
        },
        {
            "business_flow_id": "mix-table",
            "table_number": "F6",
            "dish_name": "肠粉",
            "quantity": 1,
            "order_time": datetime(2026, 6, 30, 11, 5, tzinfo=CHINA_TZ),
            "station": "changfen",
            "status": "未结",
        },
    ])
    valid_fen = seeded["mix-valid-fen"]
    valid_jiao = seeded["mix-valid-jiao"]
    refund = seeded["mix-refund_虾饺_refund_1"]
    ready = seeded["mix-ready"]
    over_qty = seeded["mix-qty"]
    table_mismatch = seeded["mix-table"]
    _mark_dish_status(db, ready["_id"], "已制作待上菜")

    resp = client.post(
        "/api/orders/complete-cooking",
        json=_confirm_body([
            _cooking_line(valid_fen),
            _cooking_line(valid_jiao),
            _cooking_line(refund),
            _cooking_line(ready),
            _cooking_line(over_qty, complete_quantity=9),
            _cooking_line(table_mismatch, table_number="Z9"),
            _cooking_line(valid_fen),
            {
                "order_id": "ghost-missing",
                "table_number": "ZZ",
                "complete_quantity": 1,
                "original_quantity": 1,
            },
        ]),
        headers=admin_headers,
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["message"] == "出餐确认冲突"
    assert _conflict_pairs(resp) == {
        (refund["_id"], "退菜"),
        (ready["_id"], "已出餐"),
        (over_qty["_id"], "数量超过"),
        (table_mismatch["_id"], "桌号不匹配"),
        (valid_fen["_id"], "重复"),
        ("ghost-missing", "不存在"),
    }
    assert _run_async(db.get_order_by_id(valid_fen["_id"]))["dish_status"] == "待出餐"
    assert _run_async(db.get_order_by_id(valid_jiao["_id"]))["dish_status"] == "待出餐"
    assert _run_async(db.get_order_by_id(over_qty["_id"]))["dish_status"] == "待出餐"
    assert _run_async(db.get_order_by_id(table_mismatch["_id"]))["dish_status"] == "待出餐"
    assert _run_async(db.get_order_by_id(refund["_id"]))["status"] == "退菜"
    assert _run_async(db.get_order_by_id(ready["_id"]))["dish_status"] == "已制作待上菜"


def test_resolve_order_for_cooking_fallback_by_table_dish(tmp_path):
    async def _run():
        old = settings.DATABASE_DIR
        settings.DATABASE_DIR = str(tmp_path)
        db = DatabaseManager()
        await db.connect()
        await db.orders.batch_insert_orders([{
            "business_flow_id": "resolve-001",
            "table_number": "A3",
            "dish_name": "肠粉",
            "quantity": 1,
            "order_time": datetime(2026, 7, 1, 10, 0, tzinfo=CHINA_TZ),
            "station": "changfen",
            "status": "未结",
        }])
        order = await db.orders.resolve_order_for_cooking(
            order_id="999999",
            business_flow_id="",
            table_number="A3",
            dish_name="肠粉",
        )
        await db.close()
        settings.DATABASE_DIR = old
        return order

    order = _run_async(_run())
    assert order is not None
    assert order["business_flow_id"] == "resolve-001"


def test_complete_cooking_rejects_refund_order(orders_client):
    client, db, admin_headers = orders_client
    _run_async(db.batch_insert_orders([{
        "business_flow_id": "refund-001_虾饺_refund_123_001",
        "table_number": "A1",
        "dish_name": "虾饺",
        "quantity": 1,
        "order_time": datetime(2026, 7, 1, 10, 0, tzinfo=CHINA_TZ),
        "station": "shulong",
        "status": "退菜",
    }]))
    row = _run_async(db.get_orders(limit=1))[0]
    resp = client.post("/api/orders/complete-cooking", json={
        "dish_name": "虾饺",
        "station": "shulong",
        "complete_quantity": 1,
        "orders": [{
            "order_id": row["_id"],
            "business_flow_id": row["business_flow_id"],
            "table_number": "A1",
            "complete_quantity": 1,
            "original_quantity": 1,
        }],
        "operator_id": "test",
    }, headers=admin_headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["conflicts"] == [
        {"order_id": row["_id"], "reason": "退菜"},
    ]


def test_merged_dishes_pending_only(orders_client):
    client, db, _admin_headers = orders_client
    _run_async(db.batch_insert_orders([
        {
            "business_flow_id": "m-001",
            "table_number": "C1",
            "dish_name": "排骨",
            "quantity": 1,
            "order_time": datetime(2026, 6, 30, 12, 0, tzinfo=CHINA_TZ),
            "station": "shulong",
            "status": "未结",
        },
        {
            "business_flow_id": "m-002",
            "table_number": "C2",
            "dish_name": "排骨",
            "quantity": 1,
            "order_time": datetime(2026, 6, 30, 12, 1, tzinfo=CHINA_TZ),
            "station": "shulong",
            "status": "未结",
        },
    ]))
    rows = _run_async(db.get_orders(limit=-1))
    oid = rows[0]["_id"]
    tdb = db.table("orders")

    async def _mark_ready():
        await tdb.execute(
            "UPDATE orders SET dish_status = '已制作待上菜' WHERE id = ?", (oid,)
        )
        await tdb.commit()

    _run_async(_mark_ready())

    app = FastAPI()
    app.include_router(dishes_module.router)
    app.dependency_overrides[dishes_module.get_db] = lambda: db
    with TestClient(app) as c:
        resp = c.get("/api/dishes/merged", params={"station": "shulong"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["total_quantity"] == 1


if __name__ == "__main__":
    unittest.main()
