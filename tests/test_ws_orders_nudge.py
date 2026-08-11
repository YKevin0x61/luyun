#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`complete_cooking` 完成制作后广播 orders nudge（含 station scope）单测。

不启动完整 main.app lifespan：搭建最小 FastAPI app 只挂 `api.orders` 路由，
db 通过依赖覆盖注入，参考 `tests/test_kds_orders.py` 的 `orders_client` fixture
（此处改写为 unittest 风格的 setUp/tearDown，供 `unittest discover` 收集）。
"""

import asyncio
import tempfile
import unittest
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.orders as orders_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services import auth_service
from services.app_runtime import AppRuntime, set_runtime
from services.realtime.hub import realtime_hub


def _run_async(coro):
    return asyncio.run(coro)


def _seed_pending(db, station="shulong", table="N1", flow_id="nudge-001", qty=2):
    _run_async(db.batch_insert_orders([{
        "business_flow_id": flow_id,
        "table_number": table,
        "dish_name": "虾饺",
        "quantity": qty,
        "order_time": datetime(2026, 7, 1, 10, 0, tzinfo=CHINA_TZ),
        "station": station,
        "status": "未结",
    }]))


class CompleteCookingOrdersNudgeTest(unittest.TestCase):
    def setUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name

        self.db = DatabaseManager()
        _run_async(self.db.connect())
        set_runtime(AppRuntime(db=self.db))
        _run_async(auth_service.init_user("admin", "password123"))
        plain, _ = _run_async(auth_service.issue_api_token(label="ws-nudge-test"))
        self.admin_headers = {"X-Admin-Token": plain}

        app = FastAPI()
        app.include_router(orders_module.router)

        async def _get_db():
            return self.db

        app.dependency_overrides[orders_module.get_db] = _get_db
        self._client_cm = TestClient(app)
        self.client = self._client_cm.__enter__()

        self.broadcast_calls = []

        async def _fake_broadcast_nudge(topic, scope=None):
            self.broadcast_calls.append((topic, scope or {}))

        # 仅当 broadcast_nudge 尚未被实例覆盖过时才记录“需要 delattr 还原”，
        # 与 pytest 的 monkeypatch.setattr 对类方法的还原行为保持一致。
        self._had_instance_override = "broadcast_nudge" in vars(realtime_hub)
        self._orig_broadcast_nudge = realtime_hub.__dict__.get("broadcast_nudge")
        realtime_hub.broadcast_nudge = _fake_broadcast_nudge

    def tearDown(self):
        if self._had_instance_override:
            realtime_hub.broadcast_nudge = self._orig_broadcast_nudge
        else:
            del realtime_hub.__dict__["broadcast_nudge"]
        self._client_cm.__exit__(None, None, None)
        _run_async(self.db.close())
        set_runtime(None)
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def _order_nudges(self):
        return [c for c in self.broadcast_calls if c[0] == "orders"]

    def test_complete_cooking_broadcasts_orders_nudge_with_station(self):
        _seed_pending(self.db)
        row = _run_async(self.db.get_orders(limit=1))[0]
        order_id = row["_id"]

        resp = self.client.post("/api/orders/complete-cooking", json={
            "dish_name": "虾饺",
            "station": "shulong",
            "complete_quantity": 2,
            "orders": [{
                "order_id": order_id,
                "table_number": "N1",
                "complete_quantity": 2,
                "original_quantity": 2,
            }],
            "operator_id": "test",
            "ready_time": "2026-07-01T10:05:00+08:00",
        }, headers=self.admin_headers)

        self.assertEqual(resp.status_code, 200)
        self.assertIn(("orders", {"station": "shulong"}), self.broadcast_calls)

    def test_complete_cooking_response_does_not_leak_internal_stations_field(self):
        _seed_pending(self.db)
        row = _run_async(self.db.get_orders(limit=1))[0]
        order_id = row["_id"]

        resp = self.client.post("/api/orders/complete-cooking", json={
            "dish_name": "虾饺",
            "station": "shulong",
            "complete_quantity": 2,
            "orders": [{
                "order_id": order_id,
                "table_number": "N1",
                "complete_quantity": 2,
                "original_quantity": 2,
            }],
            "operator_id": "test",
        }, headers=self.admin_headers)

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("stations", resp.json())

    def test_complete_cooking_multi_station_broadcasts_once_per_station(self):
        _seed_pending(self.db, station="shulong", table="N1", flow_id="nudge-shulong")
        _seed_pending(self.db, station="changfen", table="N2", flow_id="nudge-changfen")
        rows = {r["business_flow_id"]: r for r in _run_async(self.db.get_orders(limit=-1))}
        order_a = rows["nudge-shulong"]
        order_b = rows["nudge-changfen"]

        resp = self.client.post("/api/orders/complete-cooking", json={
            "dish_name": "虾饺",
            "station": "shulong",
            "complete_quantity": 2,
            "orders": [
                {
                    "order_id": order_a["_id"],
                    "table_number": "N1",
                    "complete_quantity": 2,
                    "original_quantity": 2,
                },
                {
                    "order_id": order_b["_id"],
                    "table_number": "N2",
                    "complete_quantity": 2,
                    "original_quantity": 2,
                },
            ],
            "operator_id": "test",
        }, headers=self.admin_headers)

        self.assertEqual(resp.status_code, 200)
        order_nudges = self._order_nudges()
        self.assertIn(("orders", {"station": "shulong"}), order_nudges)
        self.assertIn(("orders", {"station": "changfen"}), order_nudges)
        self.assertEqual(len(order_nudges), 2)

    def test_complete_cooking_failure_does_not_broadcast(self):
        """订单不存在导致 404 时不应广播任何 nudge。"""
        resp = self.client.post("/api/orders/complete-cooking", json={
            "dish_name": "虾饺",
            "station": "shulong",
            "complete_quantity": 1,
            "orders": [{
                "order_id": "does-not-exist",
                "table_number": "N9",
                "complete_quantity": 1,
                "original_quantity": 1,
            }],
            "operator_id": "test",
        }, headers=self.admin_headers)

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.broadcast_calls, [])


if __name__ == "__main__":
    unittest.main()
