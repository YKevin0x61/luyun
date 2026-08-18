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


def _seed_pending(db, station="shulong", table="N1", flow_id="nudge-001", qty=2, source=""):
    _run_async(db.batch_insert_orders([{
        "business_flow_id": flow_id,
        "table_number": table,
        "dish_name": "虾饺",
        "quantity": qty,
        "order_time": datetime(2026, 7, 1, 10, 0, tzinfo=CHINA_TZ),
        "station": station,
        "status": "未结",
        "source": source,
    }]))


class _OrdersNudgeCase(unittest.TestCase):
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


class CompleteCookingOrdersNudgeTest(_OrdersNudgeCase):
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
        """订单不存在导致 409 时不应广播任何 nudge。"""
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

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"]["conflicts"], [
            {"order_id": "does-not-exist", "reason": "不存在"},
        ])
        self.assertEqual(self.broadcast_calls, [])

    def test_complete_cooking_mixed_conflicts_do_not_broadcast(self):
        _seed_pending(self.db, table="N1", flow_id="nudge-ok", qty=1)
        _seed_pending(self.db, table="N2", flow_id="nudge-refund_虾饺_refund_1", qty=1)
        rows = {row["business_flow_id"]: row for row in _run_async(self.db.get_orders(limit=-1))}
        ok = rows["nudge-ok"]
        refund = rows["nudge-refund_虾饺_refund_1"]

        resp = self.client.post("/api/orders/complete-cooking", json={
            "dish_name": "虾饺",
            "station": "shulong",
            "complete_quantity": 2,
            "orders": [
                {
                    "order_id": ok["_id"],
                    "table_number": "N1",
                    "complete_quantity": 1,
                    "original_quantity": 1,
                },
                {
                    "order_id": refund["_id"],
                    "table_number": "N2",
                    "complete_quantity": 1,
                    "original_quantity": 1,
                },
            ],
            "operator_id": "test",
        }, headers=self.admin_headers)

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.broadcast_calls, [])
        self.assertEqual(
            _run_async(self.db.get_order_by_id(ok["_id"]))["dish_status"],
            "待出餐",
        )


class LoadSteamerOrdersNudgeTest(_OrdersNudgeCase):
    def _load_body(self, order_id, steamer_id="1", port_index=3):
        return {
            "order_ids": [order_id],
            "steamer_id": steamer_id,
            "port_index": port_index,
        }

    def test_load_steamer_broadcasts_orders_nudge_with_station(self):
        _seed_pending(self.db)
        row = _run_async(self.db.get_orders(limit=1))[0]

        resp = self.client.post(
            "/api/orders/load-steamer",
            json=self._load_body(row["_id"]),
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn(("orders", {"station": "shulong"}), self.broadcast_calls)
        after = _run_async(self.db.get_orders(limit=1))[0]
        self.assertEqual(after["dish_status"], "待出餐")
        self.assertEqual(after["placement"]["steamer_id"], "1")
        self.assertEqual(after["placement"]["port_index"], 3)

    def test_load_steamer_response_does_not_leak_internal_stations_field(self):
        _seed_pending(self.db)
        row = _run_async(self.db.get_orders(limit=1))[0]

        resp = self.client.post(
            "/api/orders/load-steamer",
            json=self._load_body(row["_id"]),
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("stations", resp.json())

    def test_load_steamer_404_does_not_broadcast(self):
        resp = self.client.post(
            "/api/orders/load-steamer",
            json=self._load_body("does-not-exist"),
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.broadcast_calls, [])

    def test_load_steamer_409_does_not_broadcast(self):
        _seed_pending(self.db)
        row = _run_async(self.db.get_orders(limit=1))[0]
        first = self.client.post(
            "/api/orders/load-steamer",
            json=self._load_body(row["_id"]),
            headers=self.admin_headers,
        )
        self.assertEqual(first.status_code, 200)
        self.broadcast_calls.clear()

        resp = self.client.post(
            "/api/orders/load-steamer",
            json=self._load_body(row["_id"], steamer_id="2", port_index=1),
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.broadcast_calls, [])


class LoadThenServeOrdersNudgeTest(_OrdersNudgeCase):
    def test_load_then_serve_clears_placement_and_does_not_leak_stations(self):
        _seed_pending(self.db, qty=1)
        row = _run_async(self.db.get_orders(limit=1))[0]
        order_id = row["_id"]

        load = self.client.post(
            "/api/orders/load-steamer",
            json={"order_ids": [order_id], "steamer_id": "1", "port_index": 3},
            headers=self.admin_headers,
        )
        self.assertEqual(load.status_code, 200)
        self.broadcast_calls.clear()

        resp = self.client.post("/api/orders/complete-cooking", json={
            "dish_name": "虾饺",
            "station": "shulong",
            "complete_quantity": 1,
            "orders": [{
                "order_id": order_id,
                "table_number": "N1",
                "complete_quantity": 1,
                "original_quantity": 1,
            }],
            "operator_id": "test",
            "ready_time": "2026-07-01T10:05:00+08:00",
        }, headers=self.admin_headers)

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("stations", resp.json())
        self.assertIn(("orders", {"station": "shulong"}), self.broadcast_calls)

        after = _run_async(self.db.get_order_by_id(order_id))
        self.assertEqual(after["dish_status"], "已制作待上菜")
        self.assertIsNone(after.get("placement"))


class MoveSteamerOrdersNudgeTest(_OrdersNudgeCase):
    def _load(self, order_id, steamer_id="1", port_index=3):
        resp = self.client.post(
            "/api/orders/load-steamer",
            json={"order_ids": [order_id], "steamer_id": steamer_id, "port_index": port_index},
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.broadcast_calls.clear()
        return resp

    def test_move_steamer_broadcasts_orders_nudge_with_station(self):
        _seed_pending(self.db)
        row = _run_async(self.db.get_orders(limit=1))[0]
        self._load(row["_id"], port_index=3)

        resp = self.client.post(
            "/api/orders/move-steamer",
            json={"order_ids": [row["_id"]], "steamer_id": "1", "port_index": 5},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn(("orders", {"station": "shulong"}), self.broadcast_calls)
        after = _run_async(self.db.get_orders(limit=1))[0]
        self.assertEqual(after["dish_status"], "待出餐")
        self.assertEqual(after["placement"]["port_index"], 5)

    def test_move_steamer_response_does_not_leak_internal_stations_field(self):
        _seed_pending(self.db)
        row = _run_async(self.db.get_orders(limit=1))[0]
        self._load(row["_id"])

        resp = self.client.post(
            "/api/orders/move-steamer",
            json={"order_ids": [row["_id"]], "steamer_id": "2", "port_index": 1},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("stations", resp.json())

    def test_move_steamer_capacity_409_does_not_broadcast(self):
        capacity = settings.KITCHEN_STATIONS["shulong"]["steamer_layout"]["port_capacity"]
        for i in range(capacity):
            _seed_pending(self.db, table=f"N{i}", flow_id=f"nudge-full-{i}", qty=1)
        _seed_pending(self.db, table="M1", flow_id="nudge-mover", qty=1)
        rows = {row["business_flow_id"]: row for row in _run_async(self.db.get_orders(limit=-1))}
        dest_ids = [rows[f"nudge-full-{i}"]["_id"] for i in range(capacity)]
        mover_id = rows["nudge-mover"]["_id"]
        fill = self.client.post(
            "/api/orders/load-steamer",
            json={"order_ids": dest_ids, "steamer_id": "1", "port_index": 4},
            headers=self.admin_headers,
        )
        self.assertEqual(fill.status_code, 200)
        self._load(mover_id, port_index=1)

        resp = self.client.post(
            "/api/orders/move-steamer",
            json={"order_ids": [mover_id], "steamer_id": "1", "port_index": 4},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.broadcast_calls, [])
        after = _run_async(self.db.get_order_by_id(mover_id))
        self.assertEqual(after["placement"]["port_index"], 1)


class UnloadSteamerOrdersNudgeTest(_OrdersNudgeCase):
    def test_unload_steamer_broadcasts_orders_nudge_with_station(self):
        _seed_pending(self.db)
        row = _run_async(self.db.get_orders(limit=1))[0]
        load = self.client.post(
            "/api/orders/load-steamer",
            json={"order_ids": [row["_id"]], "steamer_id": "1", "port_index": 3},
            headers=self.admin_headers,
        )
        self.assertEqual(load.status_code, 200)
        self.broadcast_calls.clear()

        resp = self.client.post(
            "/api/orders/unload-steamer",
            json={"order_ids": [row["_id"]]},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn(("orders", {"station": "shulong"}), self.broadcast_calls)
        after = _run_async(self.db.get_orders(limit=1))[0]
        self.assertEqual(after["dish_status"], "待出餐")
        self.assertIsNone(after.get("placement"))

    def test_unload_steamer_response_does_not_leak_internal_stations_field(self):
        _seed_pending(self.db)
        row = _run_async(self.db.get_orders(limit=1))[0]
        self.client.post(
            "/api/orders/load-steamer",
            json={"order_ids": [row["_id"]], "steamer_id": "1", "port_index": 3},
            headers=self.admin_headers,
        )

        resp = self.client.post(
            "/api/orders/unload-steamer",
            json={"order_ids": [row["_id"]]},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("stations", resp.json())


class PluckSteamerOrdersNudgeTest(_OrdersNudgeCase):
    def _load(self, order_id, steamer_id="1", port_index=3):
        resp = self.client.post(
            "/api/orders/load-steamer",
            json={"order_ids": [order_id], "steamer_id": steamer_id, "port_index": port_index},
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.broadcast_calls.clear()
        return resp

    def test_pluck_steamer_broadcasts_orders_nudge_with_station(self):
        _seed_pending(self.db, flow_id="bs44_1", qty=1, source="delivery")
        row = _run_async(self.db.get_orders(limit=1))[0]
        self._load(row["_id"], port_index=3)
        _run_async(self.db.orders.mark_delivery_cancelled("bs44"))
        self.broadcast_calls.clear()

        resp = self.client.post(
            "/api/orders/pluck-steamer",
            json={"order_ids": [row["_id"]]},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn(("orders", {"station": "shulong"}), self.broadcast_calls)
        after = _run_async(self.db.get_order_by_id(row["_id"]))
        self.assertEqual(after["dish_status"], "已取消")
        self.assertIsNone(after.get("placement"))

    def test_pluck_steamer_response_does_not_leak_internal_stations_field(self):
        _seed_pending(self.db, flow_id="bs45_1", qty=1, source="delivery")
        row = _run_async(self.db.get_orders(limit=1))[0]
        self._load(row["_id"])
        _run_async(self.db.orders.mark_delivery_cancelled("bs45"))

        resp = self.client.post(
            "/api/orders/pluck-steamer",
            json={"order_ids": [row["_id"]]},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("stations", resp.json())

    def test_pluck_steaming_cage_409_does_not_broadcast(self):
        _seed_pending(self.db, qty=1)
        row = _run_async(self.db.get_orders(limit=1))[0]
        self._load(row["_id"], port_index=3)

        resp = self.client.post(
            "/api/orders/pluck-steamer",
            json={"order_ids": [row["_id"]]},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.broadcast_calls, [])
        after = _run_async(self.db.get_order_by_id(row["_id"]))
        self.assertEqual(after["dish_status"], "待出餐")
        self.assertIsNotNone(after.get("placement"))


class HoldOrdersNudgeTest(_OrdersNudgeCase):
    def test_hold_broadcasts_orders_nudge_with_station(self):
        _seed_pending(self.db, station="changfen", table="8", flow_id="hold-nudge-001", qty=1)
        row = _run_async(self.db.get_orders(limit=1))[0]

        resp = self.client.post(
            "/api/orders/hold",
            json={"order_ids": [row["_id"]]},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn(("orders", {"station": "changfen"}), self.broadcast_calls)

    def test_hold_response_does_not_leak_internal_stations_field(self):
        _seed_pending(self.db, station="changfen", table="8", flow_id="hold-nudge-002", qty=1)
        row = _run_async(self.db.get_orders(limit=1))[0]

        resp = self.client.post(
            "/api/orders/hold",
            json={"order_ids": [row["_id"]]},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("stations", resp.json())

    def test_hold_all_fail_does_not_broadcast(self):
        _seed_pending(self.db, station="changfen", table="8", flow_id="hold-nudge-409", qty=1)
        row = _run_async(self.db.get_orders(limit=1))[0]
        first = self.client.post(
            "/api/orders/hold",
            json={"order_ids": [row["_id"]]},
            headers=self.admin_headers,
        )
        self.assertEqual(first.status_code, 200)
        self.broadcast_calls.clear()

        resp = self.client.post(
            "/api/orders/hold",
            json={"order_ids": [row["_id"]]},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.broadcast_calls, [])

    def test_hold_multi_station_broadcasts_once_per_station(self):
        _seed_pending(self.db, station="shulong", table="8", flow_id="hold-shulong", qty=1)
        _seed_pending(self.db, station="changfen", table="8", flow_id="hold-changfen", qty=1)
        rows = {row["business_flow_id"]: row for row in _run_async(self.db.get_orders(limit=-1))}

        resp = self.client.post(
            "/api/orders/hold",
            json={
                "order_ids": [
                    rows["hold-shulong"]["_id"],
                    rows["hold-changfen"]["_id"],
                ]
            },
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        order_nudges = self._order_nudges()
        self.assertIn(("orders", {"station": "shulong"}), order_nudges)
        self.assertIn(("orders", {"station": "changfen"}), order_nudges)
        self.assertEqual(len(order_nudges), 2)


class FireOrdersNudgeTest(_OrdersNudgeCase):
    def _hold(self, order_ids):
        resp = self.client.post(
            "/api/orders/hold",
            json={"order_ids": order_ids},
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.broadcast_calls.clear()
        return resp

    def test_fire_broadcasts_orders_nudge_with_station(self):
        _seed_pending(self.db, station="changfen", table="8", flow_id="fire-nudge-001", qty=1)
        row = _run_async(self.db.get_orders(limit=1))[0]
        self._hold([row["_id"]])

        resp = self.client.post(
            "/api/orders/fire",
            json={"order_ids": [row["_id"]]},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn(("orders", {"station": "changfen"}), self.broadcast_calls)
        self.assertNotIn("stations", resp.json())

    def test_fire_all_fail_does_not_broadcast(self):
        _seed_pending(self.db, station="changfen", table="8", flow_id="fire-nudge-409", qty=1)
        row = _run_async(self.db.get_orders(limit=1))[0]
        self.broadcast_calls.clear()

        resp = self.client.post(
            "/api/orders/fire",
            json={"order_ids": [row["_id"]]},
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.broadcast_calls, [])

    def test_fire_multi_station_broadcasts_once_per_station(self):
        _seed_pending(self.db, station="shulong", table="8", flow_id="fire-shulong", qty=1)
        _seed_pending(self.db, station="changfen", table="8", flow_id="fire-changfen", qty=1)
        rows = {row["business_flow_id"]: row for row in _run_async(self.db.get_orders(limit=-1))}
        self._hold([rows["fire-shulong"]["_id"], rows["fire-changfen"]["_id"]])

        resp = self.client.post(
            "/api/orders/fire",
            json={
                "order_ids": [
                    rows["fire-shulong"]["_id"],
                    rows["fire-changfen"]["_id"],
                ]
            },
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        order_nudges = self._order_nudges()
        self.assertIn(("orders", {"station": "shulong"}), order_nudges)
        self.assertIn(("orders", {"station": "changfen"}), order_nudges)
        self.assertEqual(len(order_nudges), 2)


if __name__ == "__main__":
    unittest.main()

