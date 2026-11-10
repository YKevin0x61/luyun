#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工自己更新健康证（2026-11，工作台未落地项「健康证员工端修复入口」）。

接口：`PATCH /api/hygiene/staff/me/health-cert`
契约：body `expires_on`（新证**到期日**，YYYY-MM-DD，不得早于北京时今天）+ 可选
`note`；响应 `{"employee": 投影}`（与 `/staff/me` 同形）。

覆盖：
1. 员工改自己的健康证 → 库里反推成办理日期（到期日 −12 个月）、花名册看到新日期、
   员工端 `health_cert_state` 从 expired 变 ok；
2. 「只改自己」：body 里塞别人的 employee_id 也改不到别人；
3. 非法输入：空 / 格式错 / 昨天 / 2 月 29 日反推不回去 → 400 + 中文原话；
4. 未登录（无员工会话 / 管理端 cookie）→ 401；
5. 响应对员工端不下发 `health_cert_date` 这类敏感字段。
"""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.work import HygieneWork
from tests.hygiene_profile import id_card
from tests.pg_probe import scalar

# 与别的卫生用例同一个固定时刻：服务端的「今天」= 2026-09-13（北京时自然日）。
FIXED_NOW = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-13"
# 另一个时钟：这个「今天」往后 12 个月的窗口里含 2028-02-29，用来测反推不唯一那条。
FIXED_NOW_LATE = datetime(2027, 6, 1, 10, 0, tzinfo=CHINA_TZ)
PASSWORD = "password123"
ADMIN_INIT = {
    "username": "admin",
    "password": "password123",
    "confirm_password": "password123",
}

# 员工端的逐字文案（与 api/hygiene.py 的 _ERROR_DETAILS 同步，前端也用同一批）。
DETAIL_EXPIRY_REQUIRED = "请选择新的健康证到期日"
DETAIL_EXPIRY_PAST = "新的健康证到期日不能早于今天"
DETAIL_EXPIRY_TOO_FAR = "新的健康证到期日最晚是今天往后 12 个月"
DETAIL_EXPIRY_UNMAPPED = "这个到期日没有对应的办理日期，请改选一天"
DETAIL_BAD_DATE = "日期格式应为 YYYY-MM-DD"
DETAIL_STAFF_SESSION = "需要员工登录"


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


def phone(serial: int) -> str:
    return f"1380013{serial % 10000:04d}"


@pytest.fixture
def health_cert_http():
    """管理端 + 员工端的真 HTTP 链路（同 `test_hygiene_employee_profile.py`）。"""
    old = settings.DATABASE_DIR
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    work = HygieneWork(
        db, captures=FakeCaptureStore(), now=lambda: FIXED_NOW, notifier=None
    )
    _run(work.prepare())
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


@pytest.fixture
def health_cert_http_late():
    """同一条链路，但「今天」是 2027-06-01（窗口 2027-06-01 → 2028-06-01）。"""
    old = settings.DATABASE_DIR
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW_LATE)
    work = HygieneWork(
        db, captures=FakeCaptureStore(), now=lambda: FIXED_NOW_LATE, notifier=None
    )
    _run(work.prepare())
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


def _admin(client) -> TestClient:
    resp = client.post("/api/auth/init", json=ADMIN_INIT)
    assert resp.status_code == 200, resp.text
    return client


def _seed(client, accounts, *, serial: int, name: str, cert_date: str) -> int:
    """服务层建一个可批准的人；底薪 + 入职日期补齐后批准（登录要 approved）。"""
    employee = _run(
        accounts.register(
            phone(serial),
            PASSWORD,
            name,
            id_card_no=id_card(serial),
            health_cert_date=cert_date,
        )
    )
    patch = client.patch(
        f"/api/hygiene/admin/roster/{employee['id']}",
        json={"base_salary": 6000, "hire_date": "2025-01-06"},
    )
    assert patch.status_code == 200, patch.text
    approve = client.post(f"/api/hygiene/admin/roster/{employee['id']}/approve")
    assert approve.status_code == 200, approve.text
    return employee["id"]


def _staff_login(client, *, serial: int) -> None:
    """切到员工会话：先摘掉管理端 cookie（两套 cookie 同 path，摘了才干净）。"""
    client.cookies.delete(settings.SESSION_COOKIE_NAME)
    resp = client.post(
        "/api/hygiene/staff/login",
        json={"phone": phone(serial), "password": PASSWORD},
    )
    assert resp.status_code == 200, resp.text


def _admin_again(client) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "password123"},
    )
    assert resp.status_code == 200, resp.text


def _roster_row(client, employee_id: int) -> dict:
    resp = client.get("/api/hygiene/admin/roster")
    assert resp.status_code == 200, resp.text
    return next(row for row in resp.json()["employees"] if row["id"] == employee_id)


# ── 1. 主路径：改自己的 → 库、员工端、花名册三处一致 ─────────────────────────

def test_staff_updates_own_health_cert(health_cert_http):
    client, _db, accounts = health_cert_http
    _admin(client)
    # 过期状态（2025-08-09 办理 → 2026-08-09 到期，早于 2026-09-13）。
    employee_id = _seed(
        client, accounts, serial=201, name="彭灼洪", cert_date="2025-08-09"
    )
    _staff_login(client, serial=201)

    before = client.get("/api/hygiene/staff/me")
    assert before.status_code == 200, before.text
    assert before.json()["employee"]["health_cert_state"] == "expired"

    resp = client.patch(
        "/api/hygiene/staff/me/health-cert",
        json={"expires_on": "2027-09-13", "note": "新证已办，原件在店里"},
    )
    assert resp.status_code == 200, resp.text
    employee = resp.json()["employee"]
    # 响应里就是新值，且是员工端投影（不带办理日期）。
    assert employee["health_cert_expires_on"] == "2027-09-13"
    assert employee["health_cert_state"] == "ok"
    assert "health_cert_date" not in employee

    # 再读一次 /staff/me：红条依据的那两个字段跟着变。
    after = client.get("/api/hygiene/staff/me")
    assert after.status_code == 200, after.text
    assert after.json()["employee"]["health_cert_expires_on"] == "2027-09-13"
    assert after.json()["employee"]["health_cert_state"] == "ok"

    # 库里只存办理日期：到期日 − 12 个月。
    assert scalar(
        "SELECT health_cert_date FROM hygiene_employees WHERE id = ?", (employee_id,)
    ) == TODAY

    # 管理端花名册读的是同一列：那一行自然显示新日期。
    _admin_again(client)
    row = _roster_row(client, employee_id)
    assert row["health_cert_date"] == TODAY
    assert row["health_cert_expires_on"] == "2027-09-13"


def test_employee_cannot_update_someone_else(health_cert_http):
    """接口没有 employee_id 可填：body 里塞别人的 id 也只改自己（pydantic 忽略未知字段）。"""
    client, _db, accounts = health_cert_http
    _admin(client)
    me_id = _seed(client, accounts, serial=202, name="张织纷", cert_date="2025-08-09")
    other_id = _seed(client, accounts, serial=203, name="余威威", cert_date="2025-08-09")
    _staff_login(client, serial=202)

    resp = client.patch(
        "/api/hygiene/staff/me/health-cert",
        json={
            "expires_on": "2027-09-13",
            "employee_id": other_id,  # 越权尝试：不在契约里，应被忽略
            "id": other_id,
        },
    )
    assert resp.status_code == 200, resp.text

    assert scalar(
        "SELECT health_cert_date FROM hygiene_employees WHERE id = ?", (me_id,)
    ) == TODAY
    # 另一个人的办理日期一个字没变。
    assert scalar(
        "SELECT health_cert_date FROM hygiene_employees WHERE id = ?", (other_id,)
    ) == "2025-08-09"


# ── 2. 校验：空 / 格式 / 过去 / 闰日反推不回去 → 400 ────────────────────────

def test_health_cert_rejects_empty_and_bad_shape(health_cert_http):
    client, _db, accounts = health_cert_http
    _admin(client)
    _seed(client, accounts, serial=204, name="王强", cert_date="2025-08-09")
    _staff_login(client, serial=204)

    empty = client.patch("/api/hygiene/staff/me/health-cert", json={})
    assert empty.status_code == 400, empty.text
    assert empty.json()["detail"] == DETAIL_EXPIRY_REQUIRED

    bad_shape = client.patch(
        "/api/hygiene/staff/me/health-cert", json={"expires_on": "2027/09/13"}
    )
    assert bad_shape.status_code == 400, bad_shape.text
    assert bad_shape.json()["detail"] == DETAIL_BAD_DATE

    bad_calendar = client.patch(
        "/api/hygiene/staff/me/health-cert", json={"expires_on": "2027-02-30"}
    )
    assert bad_calendar.status_code == 400, bad_calendar.text
    assert bad_calendar.json()["detail"] == DETAIL_BAD_DATE


def test_health_cert_rejects_yesterday(health_cert_http):
    client, _db, accounts = health_cert_http
    _admin(client)
    _seed(client, accounts, serial=205, name="李四", cert_date="2025-08-09")
    _staff_login(client, serial=205)

    resp = client.patch(
        "/api/hygiene/staff/me/health-cert", json={"expires_on": "2026-09-12"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == DETAIL_EXPIRY_PAST


def test_health_cert_rejects_expiry_beyond_12_months(health_cert_http):
    """有效期固定 12 个月：到期日最多 = 今天 + 12 个月（2027-09-13），再多一天就是将来办证。"""
    client, _db, accounts = health_cert_http
    _admin(client)
    _seed(client, accounts, serial=209, name="吴九", cert_date="2025-08-09")
    _staff_login(client, serial=209)

    resp = client.patch(
        "/api/hygiene/staff/me/health-cert", json={"expires_on": "2027-09-14"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == DETAIL_EXPIRY_TOO_FAR

    # 边界那一天（今天 + 12 个月）收，反推出来正好是今天办的证。
    ok = client.patch(
        "/api/hygiene/staff/me/health-cert", json={"expires_on": "2027-09-13"}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["employee"]["health_cert_expires_on"] == "2027-09-13"


def test_health_cert_accepts_expiry_today(health_cert_http):
    """边界允许「不早于今天」：到期日=今天仍收，状态是临期最后一天（soon）。"""
    client, _db, accounts = health_cert_http
    _admin(client)
    employee_id = _seed(client, accounts, serial=206, name="赵六", cert_date="2025-08-09")
    _staff_login(client, serial=206)

    resp = client.patch(
        "/api/hygiene/staff/me/health-cert", json={"expires_on": TODAY}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["employee"]["health_cert_state"] == "soon"
    assert scalar(
        "SELECT health_cert_date FROM hygiene_employees WHERE id = ?", (employee_id,)
    ) == "2025-09-13"


def test_health_cert_rejects_unmapped_leap_day(health_cert_http_late):
    """2028-02-29 反推回 2027-02-28、再加 12 个月回到 2028-02-28 —— 对不回去就拒。

    这一条得在「窗口里含闰日」的时钟下测：默认时钟（2026-09）的 12 个月窗口里
    没有 2 月 29 日，那种日期会先被「最晚今天往后 12 个月」挡掉。
    """
    client, _db, accounts = health_cert_http_late
    _admin(client)
    _seed(client, accounts, serial=207, name="孙七", cert_date="2026-08-09")
    _staff_login(client, serial=207)

    resp = client.patch(
        "/api/hygiene/staff/me/health-cert", json={"expires_on": "2028-02-29"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == DETAIL_EXPIRY_UNMAPPED


# ── 3. 鉴权：没有员工会话 / 只有管理端 cookie → 401 ─────────────────────────

def test_health_cert_requires_staff_session(health_cert_http):
    client, _db, accounts = health_cert_http
    _admin(client)
    _seed(client, accounts, serial=208, name="周八", cert_date="2025-08-09")

    anonymous = TestClient(client.app)
    resp = anonymous.patch(
        "/api/hygiene/staff/me/health-cert", json={"expires_on": "2027-09-13"}
    )
    assert resp.status_code == 401, resp.text

    # 管理端 cookie 不是员工会话：同样 401（不给管理员代员工改这一栏的口子）。
    admin_only = client.patch(
        "/api/hygiene/staff/me/health-cert", json={"expires_on": "2027-09-13"}
    )
    assert admin_only.status_code == 401, admin_only.text
    assert admin_only.json()["detail"] == DETAIL_STAFF_SESSION
