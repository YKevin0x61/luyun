#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离职编排：删排班与落离职日期要么一起成，要么一起不成。

候选 05 的第二处真 bug：``api/hygiene.py`` 的 ``admin_resign`` 先调
``clear_after_resignation``（自己 commit 一次），再调 ``accounts.resign``（又 commit
一次）。第二步失败时排班已经删了、人还在职 —— docstring 里那句「任何一步失败都不会
留下半截状态」只覆盖了反方向。

这里直接调路由函数（FastAPI 的 ``Depends`` 默认值是普通参数，显式传进去即可），
用真库看那几行排班还在不在。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest import mock

import pytest
from fastapi import HTTPException

import api.hygiene as hygiene_api
from api.hygiene import ResignRequest, admin_resign
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.identity.accounts import EmployeeAccounts, EmployeeAccountsError

EMPLOYEE_ID = 21
RESIGN_DAY = "2026-09-13"
FIXED_NOW = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _days(db):
    async def _one():
        cur = await db._conn.execute(
            "SELECT business_date FROM staff_assignments WHERE employee_id = ? "
            "ORDER BY business_date",
            (EMPLOYEE_ID,),
        )
        return [row["business_date"] for row in await cur.fetchall()]

    return _run(_one())


def _resigned_at(db):
    async def _one():
        cur = await db._conn.execute(
            "SELECT resigned_at FROM hygiene_employees WHERE id = ?", (EMPLOYEE_ID,)
        )
        row = await cur.fetchone()
        return None if row is None else row["resigned_at"]

    return _run(_one())


@pytest.fixture
def runtime(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    now_iso = FIXED_NOW.isoformat()

    async def _seed():
        await db._conn.execute(
            """INSERT INTO hygiene_employees
                   (id, phone, name, password_hash, permission, approved,
                    created_at, updated_at)
               VALUES (?, ?, ?, '', '普通员工', 1, ?, ?)""",
            (EMPLOYEE_ID, "13800000021", "离职测试", now_iso, now_iso),
        )
        for day in ("2026-09-13", "2026-09-14", "2026-09-15"):
            await db._conn.execute(
                """INSERT INTO staff_assignments
                       (employee_id, business_date, shift_id, zone_id, source,
                        created_at, updated_at)
                   VALUES (?, ?, NULL, NULL, 'manual', ?, ?)""",
                (EMPLOYEE_ID, day, now_iso, now_iso),
            )
        await db._conn.commit()

    _run(_seed())
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    yield db, accounts
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _resign(db, accounts):
    payload = ResignRequest(resigned_at=RESIGN_DAY, dry_run=False)
    return _run(
        admin_resign(
            EMPLOYEE_ID,
            payload,
            _session_id="test-admin",
            accounts=accounts,
            db=db,
        )
    )


def test_successful_resigniation_removes_the_following_days(runtime):
    """对照组：成功时离职日当天保留、之后的行删掉、日期落库。"""
    db, accounts = runtime
    with mock.patch.object(hygiene_api, "_hygiene_nudge", new=mock.AsyncMock()):
        body = _resign(db, accounts)
    assert body["employee"]["resigned_at"] == RESIGN_DAY
    assert body["assignments_to_remove"] == 2
    assert _days(db) == ["2026-09-13"]
    assert _resigned_at(db) == RESIGN_DAY


def test_failed_resignation_keeps_the_schedule(runtime):
    """第二步失败 → 第一步删掉的排班要回来（否则就是「排班已删、人还在职」）。"""
    db, accounts = runtime
    boom = EmployeeAccountsError("employee_not_found", "员工不存在")
    with mock.patch.object(accounts, "resign", new=mock.AsyncMock(side_effect=boom)):
        with mock.patch.object(hygiene_api, "_hygiene_nudge", new=mock.AsyncMock()):
            with pytest.raises(HTTPException) as caught:
                _resign(db, accounts)
    assert caught.value.status_code == 404
    assert _days(db) == ["2026-09-13", "2026-09-14", "2026-09-15"], (
        "排班被删了却没落离职日期：离职编排没有落在一个事务里"
    )
    assert _resigned_at(db) is None


def test_dry_run_does_not_write_anything(runtime):
    """dry_run 连写单元都不进：只数不写。"""
    db, accounts = runtime
    payload = ResignRequest(resigned_at=RESIGN_DAY, dry_run=True)
    with mock.patch.object(hygiene_api, "_hygiene_nudge", new=mock.AsyncMock()):
        body = _run(
            admin_resign(
                EMPLOYEE_ID,
                payload,
                _session_id="test-admin",
                accounts=accounts,
                db=db,
            )
        )
    assert body["dry_run"] is True
    assert body["assignments_to_remove"] == 2
    assert _days(db) == ["2026-09-13", "2026-09-14", "2026-09-15"]
    assert _resigned_at(db) is None
