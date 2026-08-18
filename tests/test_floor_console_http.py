#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP seam for 楼面控制台：GET /floor-console and POST /hold /fire /rush."""

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.orders as orders_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services import auth_service
from services.app_runtime import AppRuntime, set_runtime


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
    plain, _ = _run_async(auth_service.issue_api_token(label="floor-http-test"))
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


def _seed_dine_in(db, **overrides):
    now = datetime.now(CHINA_TZ)
    row = {
        "business_flow_id": "floor-http-001",
        "table_number": "8",
        "dish_name": "虾饺",
        "quantity": 1,
        "order_time": now,
        "station": "changfen",
        "status": "未结",
        "source": "dine_in",
    }
    row.update(overrides)
    _run_async(db.batch_insert_orders([row]))
    return {item["business_flow_id"]: item for item in _run_async(db.get_orders(limit=-1))}


def _hold_window():
    now = datetime.now(CHINA_TZ)
    return {
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
    }


def test_hold_without_token_fails(orders_client):
    client, db, _admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    resp = client.post("/api/orders/hold", json={"order_ids": [order_id]})
    assert resp.status_code == 401
    after = _run_async(db.get_order_by_id(order_id))
    assert after["dish_status"] == "待出餐"
    assert not after.get("is_hold")


def test_hold_with_admin_token_succeeds(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    resp = client.post(
        "/api/orders/hold",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["updated_count"] == 1
    assert body["conflicts"] == []
    assert "stations" not in body
    after = _run_async(db.get_order_by_id(order_id))
    assert after["dish_status"] == "待出餐"
    assert after.get("is_hold") is True


def test_floor_console_lists_hold_phase(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    hold_resp = client.post(
        "/api/orders/hold",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert hold_resp.status_code == 200
    resp = client.get("/api/orders/floor-console", params=_hold_window())
    assert resp.status_code == 200
    tables = resp.json()["tables"]
    assert len(tables) == 1
    assert tables[0]["table_number"] == "8"
    line = tables[0]["lines"][0]
    assert line["order_id"] == str(order_id)
    assert line["phase"] == "等叫"
    assert line["dish_status"] == "待出餐"
    assert line["is_hold"] is True


def test_floor_console_defaults_to_today(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    hold_resp = client.post(
        "/api/orders/hold",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert hold_resp.status_code == 200
    resp = client.get("/api/orders/floor-console")
    assert resp.status_code == 200
    tables = resp.json()["tables"]
    assert tables[0]["lines"][0]["phase"] == "等叫"


def test_hold_all_conflicts_returns_409(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    first = client.post(
        "/api/orders/hold",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert first.status_code == 200
    resp = client.post(
        "/api/orders/hold",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["conflicts"] == [{"order_id": str(order_id), "reason": "已被等叫"}]


def test_hold_steaming_swaps_same_dish_awaiting(orders_client):
    client, db, admin_headers = orders_client
    _run_async(
        db.batch_insert_orders(
            [
                {
                    "business_flow_id": "t8",
                    "table_number": "8",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": datetime.now(CHINA_TZ),
                    "station": "shulong",
                    "status": "未结",
                    "source": "dine_in",
                },
                {
                    "business_flow_id": "t9",
                    "table_number": "9",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": datetime.now(CHINA_TZ),
                    "station": "shulong",
                    "status": "未结",
                    "source": "dine_in",
                },
            ]
        )
    )
    by_flow = {row["business_flow_id"]: row for row in _run_async(db.get_orders(limit=-1))}
    original_loaded_at = "2026-08-18T10:05:00+08:00"
    load = client.post(
        "/api/orders/load-steamer",
        json={
            "order_ids": [by_flow["t8"]["_id"]],
            "steamer_id": "1",
            "port_index": 3,
            "loaded_at": original_loaded_at,
        },
        headers=admin_headers,
    )
    assert load.status_code == 200
    resp = client.post(
        "/api/orders/hold",
        json={"order_ids": [by_flow["t8"]["_id"]]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["conflicts"] == []
    assert body["substituted"] == [
        {
            "held_id": str(by_flow["t8"]["_id"]),
            "substitute_id": str(by_flow["t9"]["_id"]),
        }
    ]
    assert "stations" not in body
    held = _run_async(db.get_order_by_id(by_flow["t8"]["_id"]))
    sub = _run_async(db.get_order_by_id(by_flow["t9"]["_id"]))
    assert held.get("is_hold") is True
    assert held.get("placement") is None
    assert held["dish_status"] == "待出餐"
    assert held.get("table_number") == "8"
    assert not sub.get("is_hold")
    assert (sub.get("placement") or {}).get("steamer_id") == "1"
    assert (sub.get("placement") or {}).get("port_index") == 3
    assert (sub.get("placement") or {}).get("stack_order") == 1
    assert (sub.get("placement") or {}).get("loaded_at") == original_loaded_at
    assert sub.get("updated_at") != original_loaded_at
    assert sub.get("table_number") == "9"


def test_hold_steaming_without_substitute_partial_200(orders_client):
    client, db, admin_headers = orders_client
    _run_async(
        db.batch_insert_orders(
            [
                {
                    "business_flow_id": "await",
                    "table_number": "8",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": datetime.now(CHINA_TZ),
                    "station": "shulong",
                    "status": "未结",
                    "source": "dine_in",
                },
                {
                    "business_flow_id": "steam",
                    "table_number": "8",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": datetime.now(CHINA_TZ),
                    "station": "shulong",
                    "status": "未结",
                    "source": "dine_in",
                },
            ]
        )
    )
    by_flow = {row["business_flow_id"]: row for row in _run_async(db.get_orders(limit=-1))}
    load = client.post(
        "/api/orders/load-steamer",
        json={
            "order_ids": [by_flow["steam"]["_id"]],
            "steamer_id": "1",
            "port_index": 3,
            "loaded_at": "2026-08-18T10:05:00+08:00",
        },
        headers=admin_headers,
    )
    assert load.status_code == 200
    resp = client.post(
        "/api/orders/hold",
        json={"order_ids": [by_flow["await"]["_id"], by_flow["steam"]["_id"]]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated_count"] == 1
    assert body["conflicts"] == [
        {"order_id": str(by_flow["steam"]["_id"]), "reason": "在蒸且无替补"}
    ]
    assert body.get("substituted") == []
    assert "stations" not in body
    held = _run_async(db.get_order_by_id(by_flow["await"]["_id"]))
    steaming = _run_async(db.get_order_by_id(by_flow["steam"]["_id"]))
    assert held.get("is_hold") is True
    assert held["dish_status"] == "待出餐"
    assert not steaming.get("is_hold")
    assert steaming.get("placement") is not None
    assert steaming.get("table_number") == "8"


def test_hold_partial_conflicts_returns_200(orders_client):
    client, db, admin_headers = orders_client
    _run_async(
        db.batch_insert_orders(
            [
                {
                    "business_flow_id": "ok",
                    "table_number": "8",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": datetime.now(CHINA_TZ),
                    "station": "changfen",
                    "status": "未结",
                    "source": "dine_in",
                },
                {
                    "business_flow_id": "held",
                    "table_number": "8",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": datetime.now(CHINA_TZ),
                    "station": "changfen",
                    "status": "未结",
                    "source": "dine_in",
                },
            ]
        )
    )
    by_flow = {row["business_flow_id"]: row for row in _run_async(db.get_orders(limit=-1))}
    already = client.post(
        "/api/orders/hold",
        json={"order_ids": [by_flow["held"]["_id"]]},
        headers=admin_headers,
    )
    assert already.status_code == 200
    resp = client.post(
        "/api/orders/hold",
        json={"order_ids": [by_flow["ok"]["_id"], by_flow["held"]["_id"]]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated_count"] == 1
    assert body["conflicts"] == [
        {"order_id": str(by_flow["held"]["_id"]), "reason": "已被等叫"}
    ]
    assert "stations" not in body
    ok = _run_async(db.get_order_by_id(by_flow["ok"]["_id"]))
    assert ok.get("is_hold") is True
    assert ok["dish_status"] == "待出餐"


def test_floor_console_and_hold_paths_are_not_order_ids(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    hold_resp = client.post(
        "/api/orders/hold",
        json={"order_ids": [seeded["floor-http-001"]["_id"]]},
        headers=admin_headers,
    )
    assert hold_resp.status_code == 200
    list_resp = client.get("/api/orders/floor-console", params=_hold_window())
    assert list_resp.status_code == 200
    assert "tables" in list_resp.json()
    missing = client.get("/api/orders/not-a-real-order")
    assert missing.status_code == 404


def test_fire_without_token_fails(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    hold_resp = client.post(
        "/api/orders/hold",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert hold_resp.status_code == 200
    resp = client.post("/api/orders/fire", json={"order_ids": [order_id]})
    assert resp.status_code == 401
    after = _run_async(db.get_order_by_id(order_id))
    assert after["dish_status"] == "待出餐"
    assert after.get("is_hold") is True
    assert not after.get("fired_at")


def test_fire_with_admin_token_succeeds(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    hold_resp = client.post(
        "/api/orders/hold",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert hold_resp.status_code == 200
    resp = client.post(
        "/api/orders/fire",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["updated_count"] == 1
    assert body["conflicts"] == []
    assert body["fired_at"]
    assert "stations" not in body
    after = _run_async(db.get_order_by_id(order_id))
    assert after["dish_status"] == "待出餐"
    assert after.get("is_hold") is False
    assert after.get("fired_at")


def test_fire_all_conflicts_returns_409(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    resp = client.post(
        "/api/orders/fire",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["conflicts"] == [{"order_id": str(order_id), "reason": "不是等叫"}]
    after = _run_async(db.get_order_by_id(order_id))
    assert after.get("is_hold") is False
    assert not after.get("fired_at")


def test_fire_partial_conflicts_returns_200(orders_client):
    client, db, admin_headers = orders_client
    _run_async(
        db.batch_insert_orders(
            [
                {
                    "business_flow_id": "held",
                    "table_number": "8",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": datetime.now(CHINA_TZ),
                    "station": "changfen",
                    "status": "未结",
                    "source": "dine_in",
                },
                {
                    "business_flow_id": "open",
                    "table_number": "8",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": datetime.now(CHINA_TZ),
                    "station": "changfen",
                    "status": "未结",
                    "source": "dine_in",
                },
            ]
        )
    )
    by_flow = {row["business_flow_id"]: row for row in _run_async(db.get_orders(limit=-1))}
    already = client.post(
        "/api/orders/hold",
        json={"order_ids": [by_flow["held"]["_id"]]},
        headers=admin_headers,
    )
    assert already.status_code == 200
    resp = client.post(
        "/api/orders/fire",
        json={"order_ids": [by_flow["held"]["_id"], by_flow["open"]["_id"]]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated_count"] == 1
    assert body["conflicts"] == [
        {"order_id": str(by_flow["open"]["_id"]), "reason": "不是等叫"}
    ]
    assert "stations" not in body
    fired = _run_async(db.get_order_by_id(by_flow["held"]["_id"]))
    skipped = _run_async(db.get_order_by_id(by_flow["open"]["_id"]))
    assert fired.get("is_hold") is False
    assert fired.get("fired_at")
    assert skipped.get("is_hold") is False
    assert not skipped.get("fired_at")


def test_fire_path_is_not_an_order_id(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    hold_resp = client.post(
        "/api/orders/hold",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert hold_resp.status_code == 200
    resp = client.post(
        "/api/orders/fire",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    missing = client.get("/api/orders/not-a-real-order")
    assert missing.status_code == 404


def test_rush_without_token_fails(orders_client):
    client, db, _admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    resp = client.post("/api/orders/rush", json={"order_ids": [order_id]})
    assert resp.status_code == 401
    after = _run_async(db.get_order_by_id(order_id))
    assert after["dish_status"] == "待出餐"
    assert not after.get("is_rushed")


def test_rush_with_admin_token_succeeds(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    resp = client.post(
        "/api/orders/rush",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["updated_count"] == 1
    assert body["conflicts"] == []
    assert "stations" not in body
    after = _run_async(db.get_order_by_id(order_id))
    assert after["dish_status"] == "待出餐"
    assert after.get("is_rushed") is True


def test_rush_all_conflicts_returns_409(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    hold_resp = client.post(
        "/api/orders/hold",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert hold_resp.status_code == 200
    resp = client.post(
        "/api/orders/rush",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["conflicts"] == [{"order_id": str(order_id), "reason": "等叫须先叫起"}]
    after = _run_async(db.get_order_by_id(order_id))
    assert after.get("is_hold") is True
    assert not after.get("is_rushed")
    assert after["dish_status"] == "待出餐"


def test_rush_steaming_409_leaves_loaded_at(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db, station="shulong")
    order_id = seeded["floor-http-001"]["_id"]
    load = client.post(
        "/api/orders/load-steamer",
        json={
            "order_ids": [order_id],
            "steamer_id": "1",
            "port_index": 3,
            "loaded_at": "2026-08-18T10:05:00+08:00",
        },
        headers=admin_headers,
    )
    assert load.status_code == 200
    before = _run_async(db.get_order_by_id(order_id))
    loaded_at = (before.get("placement") or {}).get("loaded_at")
    resp = client.post(
        "/api/orders/rush",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["conflicts"] == [{"order_id": str(order_id), "reason": "在蒸"}]
    after = _run_async(db.get_order_by_id(order_id))
    assert after["dish_status"] == "待出餐"
    assert not after.get("is_rushed")
    assert (after.get("placement") or {}).get("loaded_at") == loaded_at


def test_rush_partial_conflicts_returns_200(orders_client):
    client, db, admin_headers = orders_client
    _run_async(
        db.batch_insert_orders(
            [
                {
                    "business_flow_id": "work",
                    "table_number": "8",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": datetime.now(CHINA_TZ),
                    "station": "changfen",
                    "status": "未结",
                    "source": "dine_in",
                },
                {
                    "business_flow_id": "held",
                    "table_number": "8",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": datetime.now(CHINA_TZ),
                    "station": "changfen",
                    "status": "未结",
                    "source": "dine_in",
                },
            ]
        )
    )
    by_flow = {row["business_flow_id"]: row for row in _run_async(db.get_orders(limit=-1))}
    already = client.post(
        "/api/orders/hold",
        json={"order_ids": [by_flow["held"]["_id"]]},
        headers=admin_headers,
    )
    assert already.status_code == 200
    resp = client.post(
        "/api/orders/rush",
        json={"order_ids": [by_flow["work"]["_id"], by_flow["held"]["_id"]]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated_count"] == 1
    assert body["conflicts"] == [
        {"order_id": str(by_flow["held"]["_id"]), "reason": "等叫须先叫起"}
    ]
    assert "stations" not in body
    rushed = _run_async(db.get_order_by_id(by_flow["work"]["_id"]))
    skipped = _run_async(db.get_order_by_id(by_flow["held"]["_id"]))
    assert rushed.get("is_rushed") is True
    assert rushed["dish_status"] == "待出餐"
    assert skipped.get("is_hold") is True
    assert not skipped.get("is_rushed")


def test_rush_path_is_not_an_order_id(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    resp = client.post(
        "/api/orders/rush",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    missing = client.get("/api/orders/not-a-real-order")
    assert missing.status_code == 404


def test_floor_console_http_excludes_delivery(orders_client):
    client, db, _admin_headers = orders_client
    now = datetime.now(CHINA_TZ)
    _run_async(
        db.batch_insert_orders(
            [
                {
                    "business_flow_id": "dine",
                    "table_number": "8",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": now,
                    "station": "changfen",
                    "status": "未结",
                    "source": "dine_in",
                },
                {
                    "business_flow_id": "wm",
                    "table_number": "W1",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": now,
                    "station": "changfen",
                    "status": "未结",
                    "source": "delivery",
                },
            ]
        )
    )
    resp = client.get("/api/orders/floor-console", params=_hold_window())
    assert resp.status_code == 200
    body = resp.json()
    assert "stations" not in body
    numbers = [table["table_number"] for table in body["tables"]]
    assert numbers == ["8"]


def test_floor_console_http_drops_empty_pos_table(orders_client):
    client, db, admin_headers = orders_client
    now = datetime.now(CHINA_TZ)
    _run_async(
        db.batch_insert_orders(
            [
                {
                    "business_flow_id": "open",
                    "table_number": "8",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": now,
                    "station": "changfen",
                    "status": "未结",
                    "source": "dine_in",
                },
                {
                    "business_flow_id": "gone",
                    "table_number": "9",
                    "dish_name": "虾饺",
                    "quantity": 1,
                    "order_time": now,
                    "station": "changfen",
                    "status": "未结",
                    "source": "dine_in",
                },
            ]
        )
    )
    by_flow = {row["business_flow_id"]: row for row in _run_async(db.get_orders(limit=-1))}
    cooked = client.post(
        "/api/orders/complete-cooking",
        json={
            "dish_name": "虾饺",
            "station": "changfen",
            "complete_quantity": 1,
            "orders": [
                {
                    "order_id": by_flow["gone"]["_id"],
                    "table_number": "9",
                    "complete_quantity": 1,
                    "original_quantity": 1,
                }
            ],
            "operator_id": "floor-http",
            "ready_time": now.isoformat(),
        },
        headers=admin_headers,
    )
    assert cooked.status_code == 200
    _run_async(
        db.save_table_data(
            [
                {"table_number": "8", "amount": 88.0, "people": 2, "duration": 20},
                {"table_number": "9", "amount": 0.0, "people": 0, "duration": 0},
            ]
        )
    )
    resp = client.get("/api/orders/floor-console", params=_hold_window())
    assert resp.status_code == 200
    body = resp.json()
    assert "stations" not in body
    numbers = [table["table_number"] for table in body["tables"]]
    assert numbers == ["8"]
    assert body["tables"][0]["lines"][0]["phase"] == "待出餐"


def test_complete_cooking_http_rejects_hold_with_dengjiao_conflict(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db)
    order_id = seeded["floor-http-001"]["_id"]
    hold = client.post(
        "/api/orders/hold",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert hold.status_code == 200
    resp = client.post(
        "/api/orders/complete-cooking",
        json={
            "dish_name": "虾饺",
            "station": "changfen",
            "complete_quantity": 1,
            "orders": [
                {
                    "order_id": order_id,
                    "table_number": "8",
                    "complete_quantity": 1,
                    "original_quantity": 1,
                }
            ],
            "operator_id": "floor-http",
            "ready_time": datetime.now(CHINA_TZ).isoformat(),
        },
        headers=admin_headers,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["conflicts"] == [{"order_id": str(order_id), "reason": "等叫"}]
    after = _run_async(db.get_order_by_id(order_id))
    assert after["dish_status"] == "待出餐"
    assert after.get("is_hold") is True


def test_load_steamer_http_rejects_hold_with_dengjiao_conflict(orders_client):
    client, db, admin_headers = orders_client
    seeded = _seed_dine_in(db, station="shulong")
    order_id = seeded["floor-http-001"]["_id"]
    hold = client.post(
        "/api/orders/hold",
        json={"order_ids": [order_id]},
        headers=admin_headers,
    )
    assert hold.status_code == 200
    resp = client.post(
        "/api/orders/load-steamer",
        json={
            "order_ids": [order_id],
            "steamer_id": "1",
            "port_index": 3,
            "loaded_at": "2026-08-18T10:05:00+08:00",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["conflicts"] == [{"order_id": str(order_id), "reason": "等叫"}]
    after = _run_async(db.get_order_by_id(order_id))
    assert after.get("placement") is None
    assert after.get("is_hold") is True
