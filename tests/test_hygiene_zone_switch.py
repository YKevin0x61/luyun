#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工当天自行换责任区必须留痕（红黑榜可追溯）。

背景：责任区隔离是分工而不是安全边界——员工可以随时把当天的责任区从 A 改成 B
（前端有「重新选择区域和班次」入口，``pick_assignment`` 也按 upsert 语义覆盖）。
既然不阻止换区，就必须能在个人榜上看到"谁换过区"，否则跨区刷实拍的人与只在
本区干活的人，红黑榜数字看起来一模一样。

这里的用例锁住三件事：
1. 首次选择、同区重选、管理员改派都不算换区；
2. 真正换区记一条事件，并在个人榜聚合出「换区」计数；
3. 跨营业日后重新选择不算换区（营业日 06:00 切日）。
"""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import EVENT_ZONE_SWITCH, HygieneWork

PHONE = "13800138000"
PASSWORD = "password123"


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class _Clock:
    """可推进的假时钟，用来跨营业日。"""

    def __init__(self):
        self.now = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)

    def __call__(self):
        return self.now


@pytest.fixture
def hygiene_http(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    clock = _Clock()
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=clock)
    work = HygieneWork(
        db,
        captures=FakeCaptureStore(),
        now=clock,
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    employee = _run(accounts.register(PHONE, PASSWORD, "张三"))
    _run(accounts.approve(employee["id"]))
    app = FastAPI()
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    app.dependency_overrides[hygiene_module.require_session] = lambda: "test-admin-session"
    with TestClient(app) as client:
        yield client, db, accounts, work, clock
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _login(client, remember=False):
    response = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD, "remember": remember},
    )
    assert response.status_code == 200
    return response


def _pick(client, zone_id, shift="白班"):
    return client.post(
        "/api/hygiene/staff/assignment",
        json={"shift": shift, "zone_id": zone_id},
    )


def _switch_events(db):
    cur = _run(
        db._conn.execute(
            "SELECT COUNT(*) AS n FROM hygiene_board_events WHERE event_type = ?",
            (EVENT_ZONE_SWITCH,),
        )
    )
    return int(dict(_run(cur.fetchone()))["n"])


def _zone_ids(work):
    return [zone["id"] for zone in _run(work.list_zones())]


def test_first_pick_and_same_zone_repick_are_not_switches(hygiene_http):
    client, db, _accounts, work, _clock = hygiene_http
    _login(client)
    first, second = _zone_ids(work)[:2]

    assert _pick(client, first).status_code == 200
    assert _switch_events(db) == 0, "首次选择不是换区"

    assert _pick(client, first, "夜班").status_code == 200
    assert _switch_events(db) == 0, "同区重选（哪怕换班次）不是换区"


def test_changing_zone_records_one_event_each_time(hygiene_http):
    client, db, _accounts, work, _clock = hygiene_http
    _login(client)
    first, second, third = _zone_ids(work)[:3]

    assert _pick(client, first).status_code == 200
    assert _pick(client, second).status_code == 200
    assert _pick(client, third).status_code == 200
    assert _switch_events(db) == 2

    cur = _run(
        db._conn.execute(
            """SELECT zone_id, employee_id, business_date
               FROM hygiene_board_events WHERE event_type = ?
               ORDER BY id ASC""",
            (EVENT_ZONE_SWITCH,),
        )
    )
    rows = [dict(row) for row in _run(cur.fetchall())]
    assert [int(row["zone_id"]) for row in rows] == [second, third]
    assert all(row["business_date"] == "2026-09-13" for row in rows)
    assert all(row["employee_id"] is not None for row in rows)


def test_switch_shows_up_in_person_board(hygiene_http):
    client, _db, _accounts, work, _clock = hygiene_http
    _login(client)
    first, second = _zone_ids(work)[:2]
    _pick(client, first)
    _pick(client, second)

    boards = _run(work.list_boards())
    people = {int(row["employee_id"]): row for row in boards["people"]}
    employee_id = _run(_accounts.current_assignment(1))["employee_id"]
    assert people[int(employee_id)]["换区"] == 1
    assert "换区" in people[int(employee_id)]


def test_new_business_day_resets_the_baseline(hygiene_http):
    client, db, _accounts, work, clock = hygiene_http
    # 跨营业日至少要过 20 小时，班次级会话会先过期，所以这里用「记住登录」的 30 天会话。
    _login(client, remember=True)
    first, second = _zone_ids(work)[:2]
    assert _pick(client, first).status_code == 200

    # 06:00 之后属于新的营业日：此时"重新选区"是当天首次选择，不是换区。
    clock.now = datetime(2026, 9, 14, 9, 0, tzinfo=CHINA_TZ)
    assert _pick(client, second).status_code == 200
    assert _switch_events(db) == 0


def test_admin_reassignment_is_not_a_self_switch(hygiene_http):
    client, db, _accounts, work, _clock = hygiene_http
    _login(client)
    first, second = _zone_ids(work)[:2]
    assert _pick(client, first).status_code == 200

    response = client.post(
        f"/api/hygiene/admin/roster/{1}/assignment",
        json={"shift": "夜班", "zone_id": second},
    )
    assert response.status_code == 200
    assert _switch_events(db) == 0, "管理员排班不是员工自换区"
