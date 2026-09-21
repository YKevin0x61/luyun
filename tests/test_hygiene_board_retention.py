#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看板事件要有保留策略与条数上限。

审查报告 §3.4/§3.6：``hygiene_board_events`` 只增不减（每次提交/驳回/逾期各一条，
100 个检查项 × 2 班一天几百条，一年几十万行），而 ``/admin/board-events`` 无时间窗、
无 LIMIT，一旦接上消费者就是 O(历史) 响应体。
"""

import asyncio
from datetime import datetime, timedelta

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
from services.hygiene.work import (
    BOARD_EVENT_RETENTION_DAYS,
    HygieneWork,
)

SUPER = {"kind": "super"}
CLOCK = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture
def env(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: CLOCK)
    work = HygieneWork(
        db,
        captures=FakeCaptureStore(),
        now=lambda: CLOCK,
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    app = FastAPI()
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    app.dependency_overrides[hygiene_module.require_session] = lambda: "admin"
    with TestClient(app) as client:
        yield client, db, work
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _insert_event(db, board, occurred_at, event_type="实拍", employee_id=1):
    _run(
        db._conn.execute(
            """INSERT INTO hygiene_board_events
               (board, event_type, zone_id, employee_id, item_id, shift,
                business_date, occurred_at)
               VALUES (?, ?, 1, ?, 1, '白班', '2026-09-13', ?)""",
            (board, event_type, employee_id, occurred_at),
        )
    )
    _run(db._conn.commit())


def test_purge_keeps_the_retention_window_and_drops_older(env):
    _client, db, work = env
    stale = (CLOCK - timedelta(days=BOARD_EVENT_RETENTION_DAYS + 1)).isoformat()
    fresh = (CLOCK - timedelta(days=1)).isoformat()
    _insert_event(db, "person", stale)
    _insert_event(db, "person", fresh)

    removed = _run(work.purge_old_board_events(now=CLOCK))
    assert removed == 1

    remaining = _run(work.list_person_board_events(limit=50))
    assert [event["occurred_at"] for event in remaining] == [fresh]


def test_purge_is_idempotent(env):
    _client, db, work = env
    stale = (CLOCK - timedelta(days=BOARD_EVENT_RETENTION_DAYS + 5)).isoformat()
    _insert_event(db, "person", stale)

    assert _run(work.purge_old_board_events(now=CLOCK)) == 1
    assert _run(work.purge_old_board_events(now=CLOCK)) == 0


def test_event_listing_respects_limit_and_since(env):
    _client, db, work = env
    for offset in range(5):
        _insert_event(db, "person", (CLOCK - timedelta(hours=offset)).isoformat())

    assert len(_run(work.list_person_board_events())) == 5
    assert len(_run(work.list_person_board_events(limit=2))) == 2

    since = (CLOCK - timedelta(hours=2)).isoformat()
    windowed = _run(work.list_person_board_events(since=since, limit=50))
    assert len(windowed) == 3, "since 之后应只剩 3 条（含边界那条）"


def test_admin_endpoint_applies_a_default_limit(env):
    client, db, _work = env
    for offset in range(8):
        _insert_event(db, "person", (CLOCK - timedelta(minutes=offset)).isoformat())

    response = client.get("/api/hygiene/admin/board-events", params={"limit": 3})
    assert response.status_code == 200
    body = response.json()
    assert len(body["people"]) == 3

    too_big = client.get("/api/hygiene/admin/board-events", params={"limit": 99999})
    assert too_big.status_code == 422, "条数上限要有闸"
