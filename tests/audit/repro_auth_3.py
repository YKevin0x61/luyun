#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现（切片 A · 权限/身份）：「排班管理」能力的员工会话能拿到全店同事的手机号。

## 缺陷

`api/scheduling.py:286` 的 `_require_scheduler` 从 2026-10-14 起把「员工会话 + `schedule`
能力」放进店长那一面，它自己的注释写得很清楚：

    **花名册不在这扇门后面**：`roster`（审批入职、停用、改权限）是另一个键，仍只给
    超级管理员 —— 身份证明细与底薪按 `docs/adr/0098` 不下发员工端，所以「能改排班」
    不能连带拿到花名册页。

但 `GET /api/scheduling/roster` **就挂在这扇门后面**（`api/scheduling.py:706`），
服务层 `SchedulingStore.roster_with_rules`（`services/scheduling/store.py:2448`）里逐行
带出 `phone`。于是拿到 `schedule` 那一项的员工可以枚举全店同事的手机号与「已批准 /
已停用」状态。

对照同仓口径：员工端换班选人那条 `GET /api/scheduling/me/colleagues` 明确写着
「**不给手机号**，也不给别人的排班」（`api/scheduling.py:471`）；花名册页
`/workbench/hr/roster` 也是 `audience: admin`、无 `staffCap`。

## 预期 vs 实际

- 预期：`schedule` 能力只拿到排班需要的东西（姓名 / 工号 / 是否停用）；手机号留给自己
  （`/me`）与管理员（`/api/hygiene/admin/roster`）。
- 实际：`GET /api/scheduling/roster` 回 200，`employees[*].phone` 是别人的手机号。

## 修的方向（不写完整补丁）

在 `roster_with_rules` 里按调用方身份投影（与 `_roster_row` / `_project_employee` 分两档
同理），或在这条路由上换一个只出排班需要的字段的投影；不要靠「员工会话进不来」这一条
（2026-10-14 起它进得来了）。

## 跑法

    .venv/bin/python -m pytest tests/audit/repro_auth_3.py -x -q

红 = 缺陷存在。
"""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.scheduling as scheduling_module
import main as main_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.scheduling.store import SchedulingStore

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
PHONE = "13800138000"
NAME = "张三"
PHONE2 = "13900139000"
NAME2 = "李四"


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
def scheduling_http(tmp_path, monkeypatch):
    """与 `tests/test_scheduling_api.py::scheduling_http` 同形。"""
    real_init = SchedulingStore.__init__

    def _pinned_init(self, *args, **kwargs):
        kwargs.setdefault("now", lambda: FIXED_NOW)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(SchedulingStore, "__init__", _pinned_init)

    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    _run(SchedulingStore(db, now=lambda: FIXED_NOW).prepare())
    monkeypatch.setattr(main_module, "employee_accounts", accounts)

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(scheduling_module.router)
    with TestClient(app) as client:
        init = client.post("/api/auth/init", json=ADMIN)
        assert init.status_code == 200, init.text
        login = client.post(
            "/api/auth/login",
            json={"username": ADMIN["username"], "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        yield client, db, accounts
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _employee(accounts, phone=PHONE, name=NAME, caps=()):
    employee = _run(accounts.register(phone, PASSWORD, name))
    _run(accounts.approve(employee["id"]))
    if caps:
        _run(accounts.set_admin_caps(employee["id"], list(caps)))
    return employee["id"]


def _staff_cookie(client, accounts, phone=PHONE):
    client.cookies.clear()
    session_id = _run(accounts.login(phone, PASSWORD))["session_id"]
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, session_id)
    return session_id


def test_without_schedule_cap_the_endpoint_is_closed(scheduling_http):
    """对照组：没勾「排班管理」的员工会话打不开这扇门（403）。"""
    client, _db, accounts = scheduling_http
    _employee(accounts)
    _employee(accounts, phone=PHONE2, name=NAME2)
    _staff_cookie(client, accounts)

    assert client.get("/api/scheduling/roster").status_code == 403


def test_schedule_cap_does_not_leak_colleagues_phone_numbers(scheduling_http):
    """勾了「排班管理」的员工拿到名单可以，但不该拿到别人的手机号。"""
    client, _db, accounts = scheduling_http
    _employee(accounts, caps=["schedule"])
    _employee(accounts, phone=PHONE2, name=NAME2)
    _staff_cookie(client, accounts)

    resp = client.get("/api/scheduling/roster")
    assert resp.status_code == 200, resp.text
    employees = resp.json()["employees"]
    assert any(row["name"] == NAME2 for row in employees), "名单本身该能读到（要排班）"

    leaked = [
        (row["name"], row["phone"])
        for row in employees
        if row["name"] != NAME and row.get("phone")
    ]
    assert not leaked, (
        "「排班管理」能力的员工会话拿到了全店同事的手机号（`my_colleagues` 明确写着不给）："
        f"{leaked}"
    )
