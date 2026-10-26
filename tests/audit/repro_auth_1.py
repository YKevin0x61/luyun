#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现（切片 A · 权限/身份）：共用电脑「双 cookie」下，员工页按管理端身份执行。

## 缺陷

`require_any_identity_session`（`api/security.py`）的优先级是「两套 cookie 都在时管理端优先」，
而 `/workbench/staff/loss`（页面清单 `audience: both`）用的是管理端 `api` 客户端
（`admin-web/src/views/wastage/WastageRegisterView.vue:27`，`credentials: 'include'`），
于是店里那台共用电脑（管理端 cookie + 员工 cookie 同时存在，ADR 0091 明确的场景）上：

1. 员工在「损耗登记」页记的一笔，台账里记成**管理端**（`operator_kind=admin`、`employee_id=NULL`），
   不是他自己 —— 「谁记的」那一列在分析页显示「管理端」，归属错了；
2. 同一个页面的明细列表（`GET /api/wastage/records`）拿的是 `sees_all` 那一支 ——
   员工在标着「员工」的页面上看到**全店所有人**记的损耗（含照片）。

页面侧的契约写得很清楚（`pageRoutes.json` 对 `/workbench/staff/loss` 的说明、
`api/wastage.py` 模块头）：员工那一档「只看自己记的」。工作台的「只降不升」身份记忆
（`utils/workbenchIdentity.js`）也只改视图、改不了这条接口判定 —— 顶栏写着「员工」，
后端按管理员处理。

## 预期 vs 实际

- 预期：双 cookie 时，员工页上的写/读按**员工**归属（`employee_id` 是他自己、只回自己的记录）。
- 实际：按管理端归属（`operator_kind == "admin"`），明细里能看到同店另一位员工的记录。

## 修的方向（不写完整补丁）

要么让 `/workbench/staff/loss` 这一类「员工那一档」的页走员工专属端点/显式身份声明
（WS 的 `?identity=` 就是同一类问题的既有解法，见 `api/security.py` 的 `identify_ws`），
要么给 `require_any_identity_session` 加一个「本次请求按员工那一支解析」的显式参数，
由页面声明；纯靠 cookie 优先级无法表达「此刻以谁的身份在看」。

## 跑法

    .venv/bin/python -m pytest tests/audit/repro_auth_1.py -x -q

红 = 缺陷存在。
"""

import asyncio
import io
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.wastage as wastage_module
import main as main_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.wastage.store import WastageStore

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
PHONE2 = "13900139000"
NAME2 = "李四"
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-24"


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


def _jpeg_bytes(color="red"):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def wastage_http(tmp_path, monkeypatch):
    """与 `tests/test_wastage.py::wastage_http` 同形：真库（测试库）+ 钉住的时钟。"""
    real_init = WastageStore.__init__

    def _pinned_init(self, *args, **kwargs):
        kwargs.setdefault("now", lambda: FIXED_NOW)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(WastageStore, "__init__", _pinned_init)
    monkeypatch.setattr(wastage_module, "_capture_store", None)

    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    monkeypatch.setattr(main_module, "employee_accounts", accounts)

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(wastage_module.router)
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
    monkeypatch.setattr(wastage_module, "_capture_store", None)
    settings.DATABASE_DIR = old


def _employee_id(accounts, phone=PHONE, name=NAME):
    employee = _run(accounts.register(phone, PASSWORD, name))
    _run(accounts.approve(employee["id"]))
    return employee["id"]


def _admin_cookie(client):
    client.cookies.clear()
    login = client.post(
        "/api/auth/login",
        json={"username": ADMIN["username"], "password": ADMIN["password"]},
    )
    assert login.status_code == 200, login.text


def _staff_session_id(accounts, phone=PHONE):
    return _run(accounts.login(phone, PASSWORD))["session_id"]


def _record_payload(item="青菜", qty=1.5):
    return {
        "item_name": item,
        "quantity": qty,
        "unit": "kg",
        "reason": "expired",
        "wastage_date": TODAY,
    }


def test_single_staff_cookie_attributes_to_the_employee(wastage_http):
    """对照组：只有员工 cookie 时归属正确 —— 缺陷只出在「双 cookie」那台共用电脑上。"""
    client, _db, accounts = wastage_http
    employee_id = _employee_id(accounts)
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, _staff_session_id(accounts))

    created = client.post("/api/wastage/records", json=_record_payload())
    assert created.status_code == 200, created.text
    record = created.json()["record"]
    assert record["operator_kind"] == "staff"
    assert record["employee_id"] == employee_id


def test_dual_cookie_employee_page_is_executed_as_admin(wastage_http):
    """共用电脑（两套 cookie 都在）：员工在员工页登记一笔，归属必须是他自己。

    实际会归在「管理端」名下（`operator_kind == "admin"`、`employee_id is None`）。
    """
    client, _db, accounts = wastage_http
    employee_id = _employee_id(accounts)

    # 共用电脑：管理端会话（店员共用账号）还在 cookie 里，员工又登了自己的会话。
    # 这正是 `utils/workbenchIdentity.js` 的「只降不升」要处理的场景（ADR 0091）。
    _admin_cookie(client)
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, _staff_session_id(accounts))

    created = client.post("/api/wastage/records", json=_record_payload())
    assert created.status_code == 200, created.text
    record = created.json()["record"]
    assert record["operator_kind"] == "staff", (
        "双 cookie 时员工页的写按管理端身份执行："
        f"operator_kind={record['operator_kind']!r} employee_id={record.get('employee_id')!r}"
    )
    assert record["employee_id"] == employee_id


def test_dual_cookie_employee_page_sees_the_whole_shop(wastage_http):
    """共用电脑：员工在员工页的明细列表只能有自己记的那几笔（实际拿到全店）。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _employee_id(accounts, phone=PHONE2, name=NAME2)
    client.cookies.clear()

    # 同事先用他自己的员工会话记一笔。
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, _staff_session_id(accounts, PHONE2))
    other = client.post("/api/wastage/records", json=_record_payload(item="豆腐", qty=2))
    assert other.status_code == 200, other.text
    other_record = other.json()["record"]
    assert other_record["employee_id"] != 1  # 确实是另一位员工名下的

    # 共用电脑：管理端 cookie + 本人员工 cookie，打开员工那一页。
    client.cookies.clear()
    _admin_cookie(client)
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, _staff_session_id(accounts))

    listed = client.get("/api/wastage/records")
    assert listed.status_code == 200, listed.text
    ids = [row["id"] for row in listed.json()["records"]]
    assert other_record["id"] not in ids, (
        "员工页拿到了同事记的损耗（含「谁记的」与照片）："
        f"records={listed.json()['records']}"
    )
