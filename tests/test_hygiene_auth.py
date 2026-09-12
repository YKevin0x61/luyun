#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP: which session may call hygiene staff vs admin roster routes."""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services import auth_service
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts

PHONE = "13800138000"
PASSWORD = "password123"
ADMIN_INIT = {
    "username": "admin",
    "password": "password123",
    "confirm_password": "password123",
}


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
def hygiene_http(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(
        db, now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
    )
    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    with TestClient(app) as client:
        yield client, db, accounts
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def test_unauthenticated_can_register_and_attempt_login_not_roster(hygiene_http):
    client, _db, _accounts = hygiene_http
    resp = client.post(
        "/api/hygiene/staff/register",
        json={"phone": PHONE, "password": PASSWORD},
    )
    assert resp.status_code == 200
    login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    )
    assert login.status_code == 401
    assert client.get("/api/hygiene/admin/roster").status_code == 401
    assert client.post("/api/hygiene/admin/roster/1/approve").status_code == 401
    assert client.post("/api/hygiene/admin/roster/1/disable").status_code == 401
    assert client.patch(
        "/api/hygiene/admin/roster/1",
        json={"permission": "管理员"},
    ).status_code == 401


def test_staff_session_can_me_not_admin_roster_writes(hygiene_http):
    client, _db, accounts = hygiene_http
    employee = _run(accounts.register(PHONE, PASSWORD))
    _run(accounts.approve(employee["id"]))
    login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    )
    assert login.status_code == 200
    assert client.cookies.get(settings.STAFF_SESSION_COOKIE_NAME)
    me = client.get("/api/hygiene/staff/me")
    assert me.status_code == 200
    assert me.json()["employee"]["phone"] == PHONE
    assert client.post(
        f"/api/hygiene/admin/roster/{employee['id']}/approve"
    ).status_code == 401
    assert client.patch(
        f"/api/hygiene/admin/roster/{employee['id']}",
        json={"permission": "管理员"},
    ).status_code == 401
    status = client.get("/api/auth/status").json()
    assert status["logged_in"] is False
    logout = client.post("/api/hygiene/staff/logout")
    assert logout.status_code == 200
    assert client.get("/api/hygiene/staff/me").status_code == 401


def test_admin_cookie_can_roster_but_is_not_staff_phone_identity(hygiene_http):
    client, _db, accounts = hygiene_http
    init = client.post("/api/auth/init", json=ADMIN_INIT)
    assert init.status_code == 200
    employee = _run(accounts.register(PHONE, PASSWORD))
    roster = client.get("/api/hygiene/admin/roster")
    assert roster.status_code == 200
    assert roster.json()["employees"][0]["phone"] == PHONE
    approve = client.post(f"/api/hygiene/admin/roster/{employee['id']}/approve")
    assert approve.status_code == 200
    title = client.patch(
        f"/api/hygiene/admin/roster/{employee['id']}",
        json={"job_title": "领班", "permission": "普通员工"},
    )
    assert title.status_code == 200
    staff_as_admin = client.post(
        "/api/hygiene/staff/login",
        json={"phone": "admin", "password": "password123"},
    )
    assert staff_as_admin.status_code == 401
    assert client.get("/api/hygiene/staff/me").status_code == 401


def test_api_token_is_not_staff_session_or_roster_cookie(hygiene_http):
    client, _db, _accounts = hygiene_http
    client.post("/api/auth/init", json=ADMIN_INIT)
    login = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "password123",
            "issue_api_token": True,
        },
    )
    token = login.json()["api_token"]
    client.cookies.clear()
    headers = {"X-Admin-Token": token}
    assert client.get("/api/hygiene/staff/me", headers=headers).status_code == 401
    assert client.get("/api/hygiene/admin/roster", headers=headers).status_code == 401
