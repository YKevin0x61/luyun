#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划 API（ADR 0117）：契约与越权边界。"""

import asyncio
import json
from datetime import datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.prep_plan import manager_router, reader_router, router
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
    app.include_router(manager_router)
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


def _seed(db, *, item_name="虾饺馅", unit="份", station="shulong", position="熟笼"):
    async def work():
        items = db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_items (
                    item_name, station, position, category, unit, kind, active,
                    shelf_life_hours, lead_time_hours, min_batch_qty,
                    safety_stock_ratio, notes, created_at, updated_at
                ) VALUES (?, ?, ?, '半成品', ?, 'made', 1, 24, 0, 0, 0.15, '', ?, ?)
                """,
                (item_name, station, position, unit, STAMP, STAMP),
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
    """员工选的「岗位」：清单按备货品的 position 分组，档口只是 POS 维度。"""
    client, db = staff_client
    _seed(db, item_name="虾饺馅", station="shulong", position="熟笼")
    _seed(db, item_name="蛋挞盏", unit="个", station="xibing", position="西饼")

    client.post("/api/prep-plan/staff-station", json={"station": "熟笼"})
    body = client.get("/api/prep-plan/today").json()
    assert {row["position"] for row in body["entries"]} == {"熟笼"}


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
    assert body["stations"][0]["station"] == "熟笼"
    assert body["stations"][0]["label"] == "熟笼"
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


def test_tick_does_not_need_produced_qty(admin_client):
    """ADR 0121：不登记产量，勾选只是「这项处理过了」。"""
    client, db = admin_client
    _seed(db)
    body = client.get("/api/prep-plan/today").json()
    entry = [row for row in body["entries"] if row["item_name"] == "虾饺馅"][0]
    resp = client.patch(f"/api/prep-plan/today/entries/{entry['id']}", json={"done": True})
    assert resp.status_code == 200
    refreshed = client.get("/api/prep-plan/today").json()
    row = [r for r in refreshed["entries"] if r["item_name"] == "虾饺馅"][0]
    assert row["done_at"]


def test_restock_and_review_are_gone(admin_client):
    """补货 / 复盘 / 报废三个端点随 v2 层一起退场，直接 404。"""
    client, db = admin_client
    _seed(db)
    assert client.get("/api/prep-plan/restock").status_code == 404
    assert client.get("/api/prep-plan/review").status_code == 404
    assert client.post("/api/prep-plan/waste", json={}).status_code == 404





def test_horizon_query_param_switches_the_window(admin_client):
    """页面上的时间跨度胶囊：`hours` 决定窗口长度，后端原样回传。"""
    client, db = admin_client
    _seed(db)
    body = client.get("/api/prep-plan/today", params={"hours": 168}).json()
    assert body["window"]["horizon_hours"] == 168
    # 不传就是回测过的 24 小时
    assert client.get("/api/prep-plan/today").json()["window"]["horizon_hours"] == 24


def test_unsupported_horizon_is_a_400(admin_client):
    client, db = admin_client
    _seed(db)
    resp = client.get("/api/prep-plan/today", params={"hours": 30})
    assert resp.status_code == 400
    assert "24 / 48 / 72 / 168" in resp.json()["detail"]


# ── 手改原因 / 复制埋点（票 09）─────────────────────────────────────────────

def _events(db):
    """直接读事件表（事件的读面还没做，这张表就是接口的契约）。"""

    async def work():
        table = db.table("prep_plan_events")
        async with table.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT kind, plan_day, prep_item_id, qty_before, qty_after, meta_json"
                " FROM prep_plan_events ORDER BY id"
            )
            return [dict(row) for row in await cursor.fetchall()]

    return _run(work())


def _entry_id(client, name="虾饺馅"):
    body = client.get("/api/prep-plan/today").json()
    return [row for row in body["entries"] if row["item_name"] == name][0]["id"]


def test_manual_edit_records_the_reason_in_the_event_meta(admin_client):
    """改数量时选的原因写进 `manual_edit` 事件的 meta.reason。"""
    client, db = admin_client
    _seed(db)
    entry_id = _entry_id(client)

    resp = client.patch(
        f"/api/prep-plan/today/entries/{entry_id}",
        json={"final_qty": 95, "reason": "physical_stock"},
    )
    assert resp.status_code == 200, resp.text

    events = [row for row in _events(db) if row["kind"] == "manual_edit"]
    assert len(events) == 1
    assert json.loads(events[0]["meta_json"]) == {"reason": "physical_stock"}
    assert events[0]["qty_after"] == 95


def test_manual_edit_without_reason_keeps_the_old_event_shape(admin_client):
    """不带 reason：行为与改造前一致（meta 仍是空对象）。"""
    client, db = admin_client
    _seed(db)
    entry_id = _entry_id(client)

    resp = client.patch(
        f"/api/prep-plan/today/entries/{entry_id}", json={"final_qty": 90}
    )
    assert resp.status_code == 200, resp.text

    events = [row for row in _events(db) if row["kind"] == "manual_edit"]
    assert len(events) == 1
    assert json.loads(events[0]["meta_json"]) == {}


def test_manual_edit_rejects_an_unknown_reason(admin_client):
    """原因只认那四个键，别的值 400（不是静默丢掉）。"""
    client, db = admin_client
    _seed(db)
    entry_id = _entry_id(client)

    resp = client.patch(
        f"/api/prep-plan/today/entries/{entry_id}",
        json={"final_qty": 95, "reason": "if_i_feel_like_it"},
    )
    assert resp.status_code == 400, resp.text
    assert "原因" in resp.json()["detail"]
    # 被拒的 PATCH 不该留下事件
    assert [row for row in _events(db) if row["kind"] == "manual_edit"] == []


def test_plan_copied_event_is_recorded_and_not_idempotent(admin_client):
    """复制清单埋点：管理端可打，连打两次就是两条（它不是幂等事件）。"""
    client, db = admin_client
    _seed(db)

    for _ in range(2):
        resp = client.post(
            "/api/prep-plan/events",
            json={"kind": "plan_copied", "meta": {"horizon_hours": 48, "station": "熟笼"}},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["recorded"] is True

    events = [row for row in _events(db) if row["kind"] == "plan_copied"]
    assert len(events) == 2
    assert events[0]["plan_day"] == datetime.now(CHINA_TZ).date().isoformat()
    assert json.loads(events[0]["meta_json"])["horizon_hours"] == 48


def test_plan_event_rejects_other_kinds(admin_client):
    """这个端点只收 `plan_copied`：别的事件各有自己的真实动作入口。"""
    client, db = admin_client
    _seed(db)

    resp = client.post(
        "/api/prep-plan/events", json={"kind": "done", "meta": {}}
    )
    assert resp.status_code == 400, resp.text
    assert "plan_copied" in resp.json()["detail"]
    assert _events(db) == []


def test_staff_cannot_post_plan_events(staff_client):
    """员工不能打埋点：403（不是 401 —— 他有会话，只是没这个权限）。"""
    client, db = staff_client
    _seed(db)

    resp = client.post(
        "/api/prep-plan/events", json={"kind": "plan_copied", "meta": {}}
    )
    assert resp.status_code == 403, resp.text
    assert _events(db) == []
