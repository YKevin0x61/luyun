#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""花名册抽屉里的「重设登录密码」（2026-11-02）。

老板要的那件事：员工忘了密码时，店长在花名册里直接给他重设。这里压死四条：

1. **管理端会话才进得来** —— 员工拿自己的 cookie 调这条是 401，自助改密仍走
   `PATCH /staff/password`（那条要旧密码）；
2. **两次输入必须一致**，不一致 400 且一个字节都不写（旧密码照常能登）；
3. **密码规则共用一份**：太短回的是注册/自助改密那同一句话，不是另起一套；
4. **重设之后旧凭据全废** —— 旧密码登不进、他之前登录的设备上的会话也被删了，
   新密码能登。

密码在库里只有哈希，管理端拿不到原文 —— 所以接口叫「reset」不叫「change」，
也没有 `current_password` 这一格。
"""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.hygiene as hygiene_module
import main as main_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.work import HygieneWork

FIXED_NOW = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)

PASSWORD = "password123"
NEW_PASSWORD = "reset-password-123"
PHONE = "13800138000"
NAME = "张三"

ADMIN_INIT = {
    "username": "admin",
    "password": "password123",
    "confirm_password": "password123",
}

# 逐字文案（服务端这一份与 `_ERROR_DETAILS` 同一个来源，前端那份另有测试）。
DETAIL_PASSWORDS_DO_NOT_MATCH = "两次输入的新密码不一致"


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
def roster_password_http(monkeypatch):
    """管理端 + 员工端的真 HTTP 链路（同 `tests/test_hygiene_employee_profile.py`）。

    `main.employee_accounts` 必须挂上：员工会话的查找口在 `api/security.py` 的
    `require_staff_session` 那一侧，它读的是 `main` 的模块全局。
    """
    old = settings.DATABASE_DIR
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    work = HygieneWork(
        db, captures=FakeCaptureStore(), now=lambda: FIXED_NOW, notifier=None
    )
    _run(work.prepare())
    monkeypatch.setattr(main_module, "employee_accounts", accounts)
    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    with TestClient(app) as client:
        yield client, db, accounts
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _path(employee_id: int) -> str:
    return f"/api/hygiene/admin/roster/{employee_id}/reset-password"


def _reset(client, employee_id: int, new_password=NEW_PASSWORD, confirm=None):
    return client.post(
        _path(employee_id),
        json={
            "new_password": new_password,
            "confirm_password": new_password if confirm is None else confirm,
        },
    )


def _staff_login(client, password=PASSWORD):
    return client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": password},
    )


def _approved_employee(client, accounts) -> int:
    """注册 + 批准（走服务层，绕过注册页的三项档案门槛 —— 本票只关心密码）。"""
    employee = _run(accounts.register(PHONE, PASSWORD, NAME))
    _run(accounts.approve(employee["id"]))
    return employee["id"]


def _admin(client):
    resp = client.post("/api/auth/init", json=ADMIN_INIT)
    assert resp.status_code == 200, resp.text
    return client


# ── 1. 主路径：重设 → 旧密码与已登录设备一起作废 ──────────────────────────────

def test_reset_password_swaps_login_and_kills_staff_sessions(roster_password_http):
    client, _db, accounts = roster_password_http
    employee_id = _approved_employee(client, accounts)

    # 他先在手机上登着（这就是"已登录设备"那一条会话）。
    assert _staff_login(client).status_code == 200
    assert client.get("/api/hygiene/staff/me").status_code == 200

    _admin(client)
    resp = _reset(client, employee_id)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"success": True}

    # 旧 cookie 还在浏览器里，但它指向的会话已经被删了 → 401。
    # （`/staff/session` 只回答 `authenticated: false`，状态码永远是 200，拿它判不出这条。）
    assert client.get("/api/hygiene/staff/me").status_code == 401
    session = client.get("/api/hygiene/staff/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is False
    # 旧密码登不进，新密码能登。
    assert _staff_login(client, PASSWORD).status_code == 401
    assert _staff_login(client, NEW_PASSWORD).status_code == 200


# ── 2. 两次输入不一致：400 + 逐字文案，且不落库 ───────────────────────────────

def test_reset_password_requires_matching_confirmation(roster_password_http):
    client, _db, accounts = roster_password_http
    employee_id = _approved_employee(client, accounts)
    _admin(client)

    resp = _reset(client, employee_id, new_password=NEW_PASSWORD, confirm=NEW_PASSWORD + "x")
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == DETAIL_PASSWORDS_DO_NOT_MATCH

    # 一个字节都没写：旧密码照常能登，新密码不存在。
    assert _staff_login(client, PASSWORD).status_code == 200
    assert _staff_login(client, NEW_PASSWORD).status_code == 401


# ── 3. 密码规则与注册 / 自助改密共用一份文案 ──────────────────────────────────

def test_reset_password_rejects_short_password_with_the_shared_wording(roster_password_http):
    client, _db, accounts = roster_password_http
    employee_id = _approved_employee(client, accounts)
    _admin(client)

    too_short = "x" * (settings.AUTH_MIN_PASSWORD_LENGTH - 1)
    resp = _reset(client, employee_id, new_password=too_short)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == f"密码至少 {settings.AUTH_MIN_PASSWORD_LENGTH} 位"

    assert _staff_login(client, PASSWORD).status_code == 200


# ── 4. 权限与不存在：员工进不来、查不到的人 404 ──────────────────────────────

def test_reset_password_is_admin_session_only(roster_password_http):
    client, _db, accounts = roster_password_http
    employee_id = _approved_employee(client, accounts)
    assert _staff_login(client).status_code == 200

    # 员工拿着自己的 cookie 调这条：没有管理端会话 → 401（不是 403 —— 这里压根没认出身份）。
    resp = _reset(client, employee_id)
    assert resp.status_code == 401, resp.text
    # 他的密码没被改。
    assert _staff_login(client, PASSWORD).status_code == 200


def test_reset_password_404_for_unknown_employee(roster_password_http):
    client, _db, _accounts = roster_password_http
    _admin(client)
    resp = _reset(client, 999999)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "员工不存在"
