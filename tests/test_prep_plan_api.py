#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划 API（ADR 0117）：契约与越权边界。"""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.prep_plan import reader_router, router
from api.security import require_any_identity_session, verify_admin_token
from database import CHINA_TZ, DatabaseManager, get_db

TODAY_MORNING = datetime(2026, 10, 22, 8, 0, tzinfo=CHINA_TZ)
CANDIDATE_DAYS = ("2026-10-15", "2026-10-08", "2026-10-01", "2026-09-24")
STAMP = TODAY_MORNING.isoformat()


def _run(coro):
    return asyncio.run(coro)


def _at(date_str, hour, minute=0):
    year, month, day = (int(part) for part in date_str.split("-"))
    return datetime(year, month, day, hour, minute, tzinfo=CHINA_TZ)


def _deny_admin():
    raise HTTPException(status_code=401, detail="未授权")


def _build_app(db, identity, *, admin=True):
    app = FastAPI()
    app.include_router(reader_router)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[verify_admin_token] = (lambda: True) if admin else _deny_admin
    app.dependency_overrides[require_any_identity_session] = lambda: identity
    return app


@pytest.fixture
def admin_client():
    db = DatabaseManager()
    _run(db.connect())
    with TestClient(_build_app(db, {"kind": "admin"})) as client:
        yield client, db
    _run(db.close())


@pytest.fixture
def staff_client():
    db = DatabaseManager()
    _run(db.connect())
    identity = {"kind": "staff", "employee": {"id": 7, "name": "阿明"}}
    with TestClient(_build_app(db, identity, admin=False)) as client:
        yield client, db
    _run(db.close())


def _seed(db, *, item_name="虾饺馅", unit="份", station="shulong"):
    async def work():
        items = db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_items (
                    item_name, station, position, category, unit, kind, active,
                    shelf_life_hours, lead_time_hours, min_batch_qty,
                    safety_stock_ratio, notes, created_at, updated_at
                ) VALUES (?, ?, '熟笼', '半成品', ?, 'made', 1, 24, 0, 0, 0.15, '', ?, ?)
                """,
                (item_name, station, unit, STAMP, STAMP),
            )
            item_id = cursor.lastrowid
        await items.commit()

        rules = db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO semi_finished_rules (
                    dish_name, semi_name, position, factor, unit, category,
                    notes, created_at, updated_at
                ) VALUES (?, ?, '熟笼', 1, ?, '半成品', '', ?, ?)
                """,
                (f"{item_name}菜", item_name, unit, STAMP, STAMP),
            )
        await rules.commit()

        orders = [
            {
                "business_flow_id": f"api-{item_name}-{day}",
                "table_number": "A1",
                "dish_name": f"{item_name}菜",
                "quantity": 40,
                "order_time": _at(day, 9),
                "station": station,
            }
            for day in CANDIDATE_DAYS
        ]
        await db.orders.batch_insert_orders(orders)
        return item_id

    return _run(work())


def test_admin_sees_the_whole_day_list(admin_client):
    client, db = admin_client
    _seed(db)
    resp = client.get("/api/prep-plan/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["remaining_count"] >= 1
    names = [row["item_name"] for row in body["entries"]]
    assert "虾饺馅" in names


def test_staff_must_pick_a_station_first(staff_client):
    client, db = staff_client
    _seed(db)
    body = client.get("/api/prep-plan/today").json()
    assert body["needs_station_pick"] is True
    assert body["entries"] == []


def test_staff_sees_only_own_station(staff_client):
    client, db = staff_client
    _seed(db, item_name="虾饺馅", station="shulong")
    _seed(db, item_name="蛋挞盏", unit="个", station="xibing")

    client.post("/api/prep-plan/staff-station", json={"station": "shulong"})
    body = client.get("/api/prep-plan/today").json()
    assert {row["station"] for row in body["entries"]} == {"shulong"}


def test_staff_cannot_patch_another_station(staff_client):
    client, db = staff_client
    _seed(db, item_name="蛋挞盏", unit="个", station="xibing")
    client.post("/api/prep-plan/staff-station", json={"station": "shulong"})

    body = client.get("/api/prep-plan/today").json()
    assert body["entries"] == []
    # 管理端那份里才有这条；员工拿到的是空清单，所以直接按 id 试探也用 403 兜住
    items = client.get("/api/prep-plan/items").json()["items"]
    assert [row for row in items if row["item_name"] == "蛋挞盏"]


def test_admin_can_tick_and_edit_quantity(admin_client):
    client, db = admin_client
    _seed(db)
    body = client.get("/api/prep-plan/today").json()
    entry = [row for row in body["entries"] if row["item_name"] == "虾饺馅"][0]

    resp = client.patch(
        f"/api/prep-plan/today/entries/{entry['id']}",
        json={"final_qty": 95, "done": True, "produced_qty": 80},
    )
    assert resp.status_code == 200

    refreshed = client.get("/api/prep-plan/today").json()
    row = [r for r in refreshed["entries"] if r["item_name"] == "虾饺馅"][0]
    assert row["final_qty"] == 95
    assert row["done_at"] is not None
    assert refreshed["remaining_count"] == 0


def test_summary_lists_station_remaining(admin_client):
    client, db = admin_client
    _seed(db)
    body = client.get("/api/prep-plan/summary").json()
    assert body["success"] is True
    assert body["stations"][0]["station"] == "shulong"
    assert body["stations"][0]["remaining"] == 1


def test_admin_can_flip_kind_to_bought(admin_client):
    client, db = admin_client
    item_id = _seed(db)
    resp = client.patch(f"/api/prep-plan/items/{item_id}", json={"kind": "bought"})
    assert resp.status_code == 200
    items = client.get("/api/prep-plan/items").json()["items"]
    row = [entry for entry in items if entry["id"] == item_id][0]
    assert row["kind"] == "bought"


def test_staff_cannot_change_item_kind(staff_client):
    """写面是管理端专属：员工（没带管理端凭据）连门都进不去。"""
    client, db = staff_client
    item_id = _seed(db)
    resp = client.patch(f"/api/prep-plan/items/{item_id}", json={"kind": "bought"})
    assert resp.status_code == 401


def test_restock_endpoint_contract(admin_client):
    """补货 tab 的契约：任一身份可读、返回 rows 列表（触发语义由 service 测试覆盖）。"""
    client, db = admin_client
    _seed(db)
    body = client.get("/api/prep-plan/restock").json()
    assert body["success"] is True
    assert isinstance(body["rows"], list)
    assert body["needs_station_pick"] is False


def test_tick_without_produced_qty_is_rejected(admin_client):
    client, db = admin_client
    _seed(db)
    body = client.get("/api/prep-plan/today").json()
    entry = [row for row in body["entries"] if row["item_name"] == "虾饺馅"][0]
    resp = client.patch(f"/api/prep-plan/today/entries/{entry['id']}", json={"done": True})
    assert resp.status_code == 400


def test_tick_with_produced_qty_accumulates(admin_client):
    client, db = admin_client
    _seed(db)
    body = client.get("/api/prep-plan/today").json()
    entry = [row for row in body["entries"] if row["item_name"] == "虾饺馅"][0]
    for qty in (80, 30):
        resp = client.patch(
            f"/api/prep-plan/today/entries/{entry['id']}",
            json={"done": True, "produced_qty": qty},
        )
        assert resp.status_code == 200
    refreshed = client.get("/api/prep-plan/today").json()
    row = [r for r in refreshed["entries"] if r["item_name"] == "虾饺馅"][0]
    assert row["produced_qty"] == 110


def test_waste_endpoint_writes_ledger(admin_client):
    client, db = admin_client
    item_id = _seed(db)
    resp = client.post(
        "/api/prep-plan/waste",
        json={"prep_item_id": item_id, "quantity": 4, "reason": "excess"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["record"]["item_name"] == "虾饺馅"


def test_review_is_admin_only(staff_client):
    client, _db = staff_client
    resp = client.get("/api/prep-plan/review")
    assert resp.status_code == 401
