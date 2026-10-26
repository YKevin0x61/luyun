#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现（切片 A · 权限/身份）：有「加班与补钟审批」能力的店长可以**批自己**的登记。

## 缺陷

店长（花名册上的一个真人 + 员工会话 + `admin_caps` 里的 `overtime`）既是提交人、
又是唯一的审批人，而两道门都不拦「自己批自己」：

- `api/overtime.py:133` 的 `_require_reviewer` 只判那一项能力开关，不看「这一笔是谁的」；
- `services/overtime/ledger.py:697` 的 `approve()` 只判状态谓词（`pending → approved`），
  没有「提交人 == 审批人」这一条，还会顺带写下该月的计费底薪快照（钱）。

同仓的卫生那一档有明确的反例口径：`services/hygiene/work.py:2054`
`_require_reviewer` 里有 `cannot_self_accept`（「交这张的人不能自己验收」）。加班这一档
既没人复核、又直接进加班费，口径比卫生还松。

## 预期 vs 实际

- 预期：提交人不能批准自己的登记（403 / 400，理由与卫生那条对齐）。
- 实际：`POST /api/overtime/review/{id}/approve` 回 200，`status=approved`，
  `decided_by` 就是提交人自己，并落一笔底薪快照。

## 修的方向（不写完整补丁）

在 `_require_reviewer` 之后、或 `ledger.approve/reject` 的写锁里加一条
「`entry.employee_id != actor.employee_id`」（同卫生 `cannot_self_accept` 的形状），
且**审批队列**里自己的那几笔要么不出现、要么标成「待别人复核」。

## 跑法

    .venv/bin/python -m pytest tests/audit/repro_auth_2.py -x -q

红 = 缺陷存在。
"""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.admin as admin_module
import api.auth as auth_module
import api.overtime as overtime_module
import main as main_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.overtime.ledger import OvertimeLedger

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-24"
SUPERVISOR_PHONE = "13800138011"
SUPERVISOR_NAME = "李四"


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


@pytest.fixture
def overtime_http(tmp_path, monkeypatch):
    """与 `tests/test_overtime.py::overtime_http` 同形：真库（测试库）+ 钉住的时钟。"""
    real_init = OvertimeLedger.__init__

    def _pinned_init(self, *args, **kwargs):
        kwargs.setdefault("now", lambda: FIXED_NOW)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(OvertimeLedger, "__init__", _pinned_init)

    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    monkeypatch.setattr(main_module, "employee_accounts", accounts)

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(admin_module.router)
    app.include_router(overtime_module.router)
    with TestClient(app) as client:
        init = client.post("/api/auth/init", json=ADMIN)
        assert init.status_code == 200, init.text
        login = client.post(
            "/api/auth/login",
            json={"username": ADMIN["username"], "password": ADMIN["password"]},
        )
        assert login.status_code == 200, login.text
        yield client, db, accounts
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _supervisor(accounts):
    """开好「加班与补钟审批」的店长：员工号 + 底薪（审批要算加班费，没底薪会被拒）。"""
    employee = _run(accounts.register(SUPERVISOR_PHONE, PASSWORD, SUPERVISOR_NAME))
    _run(accounts.approve(employee["id"]))
    _run(
        accounts.update_fields(
            employee["id"], base_salary=5100, hire_date="2024-03-01"
        )
    )
    _run(accounts.set_admin_caps(employee["id"], ["overtime"]))
    return employee["id"]


def _staff_cookie(client, accounts, phone=SUPERVISOR_PHONE):
    session_id = _run(accounts.login(phone, PASSWORD))["session_id"]
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, session_id)
    return session_id


def _submit(client, half_hours=4, reason="晚市加班"):
    resp = client.post(
        "/api/overtime/me",
        json={"entry_date": TODAY, "half_hours": half_hours, "reason": reason},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["entry"]


def test_supervisor_cannot_approve_his_own_overtime(overtime_http):
    """店长提了自己的加班，又用同一份员工会话批它 —— 必须被拦（实际 200 通过）。"""
    client, _db, accounts = overtime_http
    supervisor_id = _supervisor(accounts)
    _staff_cookie(client, accounts)

    own = _submit(client, half_hours=4, reason="我自己加的班")
    assert own["employee_id"] == supervisor_id
    assert own["status"] == "pending"

    approved = client.post(f"/api/overtime/review/{own['id']}/approve")
    assert approved.status_code != 200, (
        "店长批准了自己提的加班（钱与留痕都是自己给自己）："
        f"status={approved.status_code} body={approved.text}"
    )


def test_review_queue_exposes_his_own_entry_as_approvable(overtime_http):
    """待审批队列里出现的是**自己**那一笔，且没有任何「不能自审」的标记。"""
    client, _db, accounts = overtime_http
    supervisor_id = _supervisor(accounts)
    _staff_cookie(client, accounts)

    own = _submit(client, half_hours=4, reason="我自己加的班")

    queue = client.get("/api/overtime/review/pending")
    assert queue.status_code == 200, queue.text
    rows = queue.json()["entries"]
    mine = [row for row in rows if row["employee_id"] == supervisor_id]
    assert not mine, (
        "审批队列把**审批人自己**的登记当成待办摆出来（页面上就是一颗可点的「批准」）："
        f"rows={rows}"
    )
