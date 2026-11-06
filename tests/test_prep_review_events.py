#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货复盘事件采集（票 03）：手改 / 完成 / 窗口结束。

只验「真实发生过的动作有没有被记下来」与两条硬约束：

- 幂等：`done` / `window_end` 只留一条；
- 失败隔离：事件写入炸了，主流程（PATCH / 回填）照常成功。

事件的读面还没做（复盘页在后面的票），所以这里直接查 `prep_plan_events` 表 ——
这张表本身就是本票交付的契约。
"""

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import services.prep_review.events as prep_review_events
from api.prep_plan import reader_router, router
from api.security import require_any_identity_session, verify_admin_token
from database import CHINA_TZ, DatabaseManager, get_db
from services.prep_plan_service import PrepPlanService

ITEM_NAME = "虾饺馅"
UNIT = "份"
DISH_NAME = "虾饺皇"
STATION = "shulong"
POSITION = "熟笼"

CANDIDATE_DAYS = ("2026-10-15", "2026-10-08", "2026-10-01", "2026-09-24")


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


def _today() -> str:
    return datetime.now(CHINA_TZ).date().isoformat()


@pytest.fixture
def staff_client():
    db = DatabaseManager()
    _run(db.connect())
    identity = {"kind": "staff", "employee": {"id": 7, "name": "阿明"}}
    with TestClient(_build_app(db, identity, admin=False)) as client:
        yield client, db
    _run(db.close())


def _seed(db, *, item_name=ITEM_NAME, unit=UNIT, station=STATION, position=POSITION):
    """一个备货品 + 一条配方 + 4 个同星期几的销量，够 `get_today` 算出清单。"""

    async def work():
        stamp = _at(CANDIDATE_DAYS[0], 8).isoformat()
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
                (item_name, station, position, unit, stamp, stamp),
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
                ) VALUES (?, ?, ?, 1, ?, '半成品', '', ?, ?)
                """,
                (f"{item_name}菜", item_name, position, unit, stamp, stamp),
            )
        await rules.commit()

        await db.orders.batch_insert_orders(
            [
                {
                    "business_flow_id": f"events-{item_name}-{day}",
                    "table_number": "A1",
                    "dish_name": f"{item_name}菜",
                    "quantity": 40,
                    "order_time": _at(day, 9),
                    "station": station,
                }
                for day in CANDIDATE_DAYS
            ]
        )
        return item_id

    return _run(work())


def _events(db, *, kind=None):
    async def work():
        conn = db._conn
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT * FROM prep_plan_events ORDER BY id")
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    rows = _run(work())
    if kind is not None:
        rows = [row for row in rows if row["kind"] == kind]
    return rows


def _seed_snapshot(db, *, window_start, window_end, plan_day):
    async def work():
        stamp = datetime.now(CHINA_TZ).isoformat()
        snapshots = db.table("prep_plan_snapshots")
        async with snapshots.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_snapshots (
                    snapshot_at, window_start, window_end, plan_day,
                    prep_item_id, item_name, unit, station,
                    suggested_qty, demand_qty, store_factor, quantile,
                    basis_json, rules_fingerprint, created_at
                )
                SELECT ?, ?, ?, ?, i.id, i.item_name, i.unit, i.station,
                       40, 40, 1, 0.5, '{}', 'fp', ?
                  FROM prep_items i
                 WHERE i.item_name = ?
                """,
                (
                    window_start,
                    window_start,
                    window_end,
                    plan_day,
                    stamp,
                    ITEM_NAME,
                ),
            )
        await snapshots.commit()

    _run(work())


def test_manual_edit_records_before_after_and_identity(staff_client):
    """手改一次 → 一条 manual_edit；前后值与库中一致，身份是当前登录员工。"""
    client, db = staff_client
    _seed(db)
    client.post("/api/prep-plan/staff-station", json={"station": POSITION})

    entry = [
        row
        for row in client.get("/api/prep-plan/today").json()["entries"]
        if row["item_name"] == ITEM_NAME
    ][0]
    before = entry["final_qty"]

    resp = client.patch(
        f"/api/prep-plan/today/entries/{entry['id']}", json={"final_qty": 95}
    )
    assert resp.status_code == 200

    refreshed = client.get("/api/prep-plan/today").json()
    row = [r for r in refreshed["entries"] if r["item_name"] == ITEM_NAME][0]
    assert row["final_qty"] == 95

    events = _events(db, kind="manual_edit")
    assert len(events) == 1, events
    event = events[0]
    assert event["plan_day"] == _today()
    assert event["prep_item_id"] == row["prep_item_id"]
    assert event["qty_before"] == before
    assert event["qty_after"] == 95
    assert event["employee_id"] == 7
    assert event["station"] == POSITION
    assert event["at"].startswith(_today())
    assert event["meta_json"] == "{}"


def test_done_is_recorded_only_once(staff_client):
    """勾选完成 → 一条 done；重复勾选不重复追加。"""
    client, db = staff_client
    _seed(db)
    client.post("/api/prep-plan/staff-station", json={"station": POSITION})
    entry = [
        row
        for row in client.get("/api/prep-plan/today").json()["entries"]
        if row["item_name"] == ITEM_NAME
    ][0]

    for _ in range(2):
        resp = client.patch(
            f"/api/prep-plan/today/entries/{entry['id']}", json={"done": True}
        )
        assert resp.status_code == 200

    events = _events(db, kind="done")
    assert len(events) == 1, events
    assert events[0]["prep_item_id"] == entry["prep_item_id"]
    assert events[0]["plan_day"] == _today()
    assert events[0]["employee_id"] == 7
    assert '"done": true' in events[0]["meta_json"]


def test_backfill_records_one_window_end_per_window():
    """回填某窗口 → 一条 window_end（窗口级，prep_item_id 为空）；重跑不重复。"""
    db = DatabaseManager()
    _run(db.connect())
    try:
        _seed(db)
        now = datetime.now(CHINA_TZ)
        window_end = (now - timedelta(hours=2)).replace(microsecond=0)
        window_start = window_end - timedelta(hours=24)
        plan_day = window_start.date().isoformat()
        _seed_snapshot(
            db,
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
            plan_day=plan_day,
        )

        service = PrepPlanService()
        first = _run(service.backfill_actuals(db, now=now))
        assert first["windows"] >= 1
        second = _run(service.backfill_actuals(db, now=now))
        assert second["windows"] == 0

        events = _events(db, kind="window_end")
        assert len(events) == 1, events
        event = events[0]
        assert event["plan_day"] == plan_day
        assert event["prep_item_id"] is None
        assert '"window_end"' in event["meta_json"]
        assert window_end.isoformat() in event["meta_json"]
        assert '"items"' in event["meta_json"]
    finally:
        _run(db.close())


def test_event_write_failure_does_not_break_the_patch(monkeypatch, staff_client):
    """事件表写入抛错：只记日志，PATCH 仍然 200、库里的量照改。"""

    async def boom(*args, **kwargs):
        raise RuntimeError("events table is on fire")

    monkeypatch.setattr(prep_review_events, "_insert_event", boom)

    client, db = staff_client
    _seed(db)
    client.post("/api/prep-plan/staff-station", json={"station": POSITION})
    entry = [
        row
        for row in client.get("/api/prep-plan/today").json()["entries"]
        if row["item_name"] == ITEM_NAME
    ][0]

    resp = client.patch(
        f"/api/prep-plan/today/entries/{entry['id']}",
        json={"final_qty": 77, "done": True},
    )
    assert resp.status_code == 200

    refreshed = client.get("/api/prep-plan/today").json()
    row = [r for r in refreshed["entries"] if r["item_name"] == ITEM_NAME][0]
    assert row["final_qty"] == 77
    assert row["done_at"]
    assert _events(db) == []
