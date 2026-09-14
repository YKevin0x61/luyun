#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kitchen work derivation: 熟笼工作阶段 / 待出餐工作 / 进入待出餐工作时刻."""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.orders as orders_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services import auth_service
from services.app_runtime import AppRuntime, set_runtime
from services.kitchen_work import (
    annotate_kitchen_work,
    derive_steamer_phase,
    is_pending_kitchen_work,
    work_enter_time,
)


def test_awaiting_and_steaming_phases():
    assert derive_steamer_phase({"dish_status": "待出餐"}) == "待上笼"
    steaming = {
        "dish_status": "待出餐",
        "placement": {"steamer_id": "1", "port_index": 1},
    }
    assert derive_steamer_phase(steaming) == "在蒸"


def test_hold_and_refund_are_not_steamer_work():
    assert derive_steamer_phase({"dish_status": "待出餐", "is_hold": True}) is None
    assert derive_steamer_phase({"dish_status": "待出餐", "status": "退菜"}) is None
    assert derive_steamer_phase({
        "dish_status": "待出餐",
        "business_flow_id": "t8_虾饺_refund_1",
    }) is None


def test_cancel_notice_stays_after_would_be_ack():
    notice = {"dish_status": "已取消", "placement": None}
    assert derive_steamer_phase(notice) == "待上笼退示"
    hold = {
        "dish_status": "已取消",
        "placement": {"steamer_id": "1", "port_index": 1},
    }
    assert derive_steamer_phase(hold) == "退菜占位"


def test_annotate_stamps_three_fields():
    order = {
        "dish_status": "待出餐",
        "order_time": "2026-08-18T10:00:00+08:00",
        "quantity": 1,
    }
    stamped = annotate_kitchen_work(order)
    assert stamped is order
    assert order["steamer_phase"] == "待上笼"
    assert order["is_pending_kitchen_work"] is True
    assert order["work_enter_time"] == "2026-08-18T10:00:00+08:00"


def test_annotate_hold_is_not_pending_work():
    order = {
        "dish_status": "待出餐",
        "is_hold": True,
        "order_time": "2026-08-18T10:00:00+08:00",
        "fired_at": "2026-08-18T10:20:00+08:00",
        "quantity": 1,
    }
    annotate_kitchen_work(order)
    assert order["steamer_phase"] is None
    assert order["is_pending_kitchen_work"] is False
    assert order["work_enter_time"] == "2026-08-18T10:20:00+08:00"
    assert is_pending_kitchen_work(order) is False
    assert work_enter_time(order) == "2026-08-18T10:20:00+08:00"


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


def test_get_orders_stamps_kitchen_work_fields(orders_client):
    client, db, _headers = orders_client
    _run_async(db.batch_insert_orders([{
        "business_flow_id": "kw-001",
        "table_number": "8",
        "dish_name": "虾饺",
        "quantity": 1,
        "order_time": datetime(2026, 9, 5, 11, 0, tzinfo=CHINA_TZ),
        "station": "shulong",
        "status": "未结",
    }]))
    resp = client.get(
        "/api/orders/",
        params={"start_time": "2026-09-05", "end_time": "2026-09-05"},
    )
    assert resp.status_code == 200
    row = resp.json()["data"][0]
    assert row["steamer_phase"] == "待上笼"
    assert row["is_pending_kitchen_work"] is True
    assert row["work_enter_time"]

    raw = _run_async(db.orders.get_orders(limit=1))[0]
    assert "steamer_phase" not in raw
    assert "is_pending_kitchen_work" not in raw
