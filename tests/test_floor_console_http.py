#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP seam for 楼面控制台：GET /floor-console and POST /hold /fire."""

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
