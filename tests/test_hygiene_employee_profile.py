#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工档案（2026-10-07）：四列迁移 + 身份证校验 + 注册必填 + 批准门槛 + 导出 + 待办。

覆盖票面点名的七件事：

1. 身份证校验位（正确 / 错误 / 末位 X 的小写）；
2. 同号重复注册 → 400（不是 500）；
3. 注册两项必填与逐字中文文案；
4. 批准门槛（底薪 + 入职日期），`/enable` 不走门槛；
5. 健康证三态（ok / soon / expired）与到期日派生（+12 个月，到期日当天算临期）；
6. CSV 导出（BOM / 列 / 分段 / 关键字 / 日志不落身份证号）；
7. 员工端 `staff/me` 有档案、**没有**敏感字段；首页待办里有健康证条目。

口径与文案都取自 `.scratch/roster-redesign/design.md`（§0.2 / §7.4 / §8 / §9 / §10）。
"""

import asyncio
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from db_core.backend.pg import dsn_from_env
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.work import HygieneWork
from tests.hygiene_profile import id_card
from tests.pg_probe import fetch_all, scalar

# 与别的卫生用例同一个固定时刻：服务端的「今天」就是它。
FIXED_NOW = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-13"

PASSWORD = "password123"
ADMIN_INIT = {
    "username": "admin",
    "password": "password123",
    "confirm_password": "password123",
}

# 健康证办理日期 → 到期日（+12 个月），相对 FIXED_NOW 的三种状态。
CERT_SOON = "2025-10-03"  # 到期 2026-10-03：20 天后 → soon
CERT_OK = "2025-12-01"  # 到期 2026-12-01：79 天后 → ok
CERT_EXPIRED = "2025-08-09"  # 到期 2026-08-09：35 天前 → expired
CERT_EXPIRES_TODAY = "2025-09-13"  # 到期 2026-09-13 = 今天 → 临期最后一天
CERT_EXPIRED_YESTERDAY = "2025-09-12"  # 到期 2026-09-12 → 昨天到期 → expired

EXPORT_PATH = "/api/hygiene/admin/roster-export.csv"
EXPORT_HEADER = (
    "姓名,手机号,职位,状态,卫生权限,管理权限,身份证号,健康证办理日期,"
    "健康证到期日,底薪(元/月),入职日期,注册时间"
)

# 逐字文案（design §7.4）：服务端这一份改了，前端那份必须跟着改 —— 两边各有测试。
DETAIL_ID_CARD_REQUIRED = "请填写身份证号"
DETAIL_ID_CARD_LENGTH = "身份证号应为 18 位"
DETAIL_ID_CARD_CHECKSUM = "身份证号校验位不对"
DETAIL_ID_CARD_DUPLICATE = "该身份证号已建档"
DETAIL_CERT_REQUIRED = "请选择健康证办理日期"
DETAIL_CERT_FUTURE = "健康证办理日期不能是将来"
DETAIL_SALARY_NOT_INT = "底薪只能是整数元"
DETAIL_SALARY_NEGATIVE = "底薪不能是负数"
DETAIL_SALARY_TOO_LARGE = "底薪最多 999999 元"
DETAIL_APPROVE_BOTH = "底薪与入职日期补齐后才能批准"
DETAIL_APPROVE_SALARY = "还缺底薪"
DETAIL_APPROVE_HIRE = "还缺入职日期"
DETAIL_NO_CHANGES = "没有要保存的改动"
DETAIL_EXPORT_EMPTY = "这个范围里没有可导出的记录"

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "pg"
    / "0020_hygiene_employee_profile.sql"
)


# ── 夹具：身份证号与手机号 ───────────────────────────────────────────────────
# 校验位由 `tests/hygiene_profile.py` **独立**算一遍（刻意不 import 被测实现）：
# 那份若把加权因子写错，这里生成的"合法号"会被它拒绝，用例就会红。
def phone(serial: int) -> str:
    """11 位手机号（`1[3-9]\\d{9}`），按序号区分。"""
    return f"1380013{serial % 10000:04d}"


# 校验位是 X 的那个号（末位小写 x 也要能收，并归一成大写）。
X_ID_CARD = next(card for card in (id_card(i) for i in range(1000)) if card.endswith("X"))


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
def profile_http():
    """管理端 + 员工端的真 HTTP 链路（同 `tests/test_hygiene_auth.py` 的夹具）。"""
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


def _admin(client) -> TestClient:
    """建管理员并留下管理端 cookie（花名册那些接口都要它）。"""
    resp = client.post("/api/auth/init", json=ADMIN_INIT)
    assert resp.status_code == 200, resp.text
    return client


def _register(client, *, name: str, phone_no: str, card: str, cert_date: str):
    return client.post(
        "/api/hygiene/staff/register",
        json={
            "name": name,
            "phone": phone_no,
            "password": PASSWORD,
            "id_card_no": card,
            "health_cert_date": cert_date,
        },
    )


def _seed_profile(
    client,
    accounts,
    *,
    serial: int,
    name: str,
    cert_date=CERT_OK,
    base_salary=None,
    hire_date=None,
) -> int:
    """服务层建一个带身份证 + 健康证的人（注册那份必填在别的用例里单独覆盖）。

    `cert_date=None` = 连健康证办理日期也不填（造「没有健康证」的存量行）。
    """
    employee = _run(
        accounts.register(
            phone(serial),
            PASSWORD,
            name,
            id_card_no=id_card(serial),
            health_cert_date=cert_date,
        )
    )
    payload = {}
    if base_salary is not None:
        payload["base_salary"] = base_salary
    if hire_date is not None:
        payload["hire_date"] = hire_date
    if payload:
        resp = client.patch(f"/api/hygiene/admin/roster/{employee['id']}", json=payload)
        assert resp.status_code == 200, resp.text
    return employee["id"]


def _roster(client) -> list:
    resp = client.get("/api/hygiene/admin/roster")
    assert resp.status_code == 200, resp.text
    return resp.json()["employees"]


def _row(client, employee_id: int) -> dict:
    return next(row for row in _roster(client) if row["id"] == employee_id)


def _export_names(client, **params):
    resp = client.get(EXPORT_PATH, params=params)
    assert resp.status_code == 200, resp.text
    rows = resp.content.decode("utf-8-sig").rstrip("\r\n").split("\r\n")[1:]
    return [row.split(",")[0] for row in rows]


# ── 1. 身份证校验位与形状（注册与 PATCH 共用同一个实现）────────────────────────

def test_register_rejects_wrong_id_card_length_and_checksum(profile_http):
    client, _db, _accounts = profile_http
    valid = id_card(1)

    short = _register(
        client, name="张三", phone_no=phone(1), card=valid[:17], cert_date=CERT_OK
    )
    assert short.status_code == 400, short.text
    assert short.json()["detail"] == DETAIL_ID_CARD_LENGTH

    # 位数够、末位是字母（不是 X）—— 校验位不对，而不是"位数不对"。
    letter_tail = _register(
        client, name="张三", phone_no=phone(2), card=valid[:17] + "A", cert_date=CERT_OK
    )
    assert letter_tail.status_code == 400, letter_tail.text
    assert letter_tail.json()["detail"] == DETAIL_ID_CARD_CHECKSUM

    # 把校验位改掉（换一个不同余数的数字）—— 加权因子算出来的那一套要挡住它。
    wrong_check = "0" if not valid.endswith("0") else "1"
    bad_sum = _register(
        client,
        name="张三",
        phone_no=phone(3),
        card=valid[:17] + wrong_check,
        cert_date=CERT_OK,
    )
    assert bad_sum.status_code == 400, bad_sum.text
    assert bad_sum.json()["detail"] == DETAIL_ID_CARD_CHECKSUM

    ok = _register(client, name="张三", phone_no=phone(4), card=valid, cert_date=CERT_OK)
    assert ok.status_code == 200, ok.text
    assert ok.json()["employee"]["name"] == "张三"


def test_id_card_with_x_check_digit_is_accepted_and_uppercased(profile_http):
    client, _db, _accounts = profile_http
    _admin(client)
    lower = X_ID_CARD[:-1] + X_ID_CARD[-1].lower()
    resp = _register(client, name="李四", phone_no=phone(11), card=lower, cert_date=CERT_OK)
    assert resp.status_code == 200, resp.text
    row = _row(client, resp.json()["employee"]["id"])
    assert row["id_card_no"] == X_ID_CARD, "末位小写 x 要归一成大写存下来"


def test_duplicate_id_card_is_400_not_500(profile_http):
    client, _db, _accounts = profile_http
    card = id_card(7)
    first = _register(client, name="张三", phone_no=phone(21), card=card, cert_date=CERT_OK)
    assert first.status_code == 200, first.text

    again = _register(client, name="李四", phone_no=phone(22), card=card, cert_date=CERT_OK)
    assert again.status_code == 400, again.text
    assert again.json()["detail"] == DETAIL_ID_CARD_DUPLICATE

    # 同一个人存不下两份档：库里那一行还是第一个人的。
    _admin(client)
    rows = [row for row in _roster(client) if row["id_card_no"] == card]
    assert len(rows) == 1
    assert rows[0]["name"] == "张三"


def test_patch_uses_the_same_id_card_validation(profile_http):
    client, _db, accounts = profile_http
    _admin(client)
    employee_id = _seed_profile(client, accounts, serial=31, name="张三")
    other_id = _seed_profile(client, accounts, serial=32, name="李四")

    bad = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}",
        json={"id_card_no": id_card(31)[:17]},
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["detail"] == DETAIL_ID_CARD_LENGTH

    duplicated = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}",
        json={"id_card_no": _row(client, other_id)["id_card_no"]},
    )
    assert duplicated.status_code == 400, duplicated.text
    assert duplicated.json()["detail"] == DETAIL_ID_CARD_DUPLICATE


# ── 2. 注册两项必填（design §8；文案与前端逐字一致）──────────────────────────

def test_register_requires_id_card_and_health_cert_date(profile_http):
    client, _db, _accounts = profile_http
    missing_card = client.post(
        "/api/hygiene/staff/register",
        json={
            "name": "张三",
            "phone": phone(41),
            "password": PASSWORD,
            "health_cert_date": CERT_OK,
        },
    )
    assert missing_card.status_code == 400, missing_card.text
    assert missing_card.json()["detail"] == DETAIL_ID_CARD_REQUIRED

    empty_card = client.post(
        "/api/hygiene/staff/register",
        json={
            "name": "张三",
            "phone": phone(42),
            "password": PASSWORD,
            "id_card_no": "   ",
            "health_cert_date": CERT_OK,
        },
    )
    assert empty_card.status_code == 400, empty_card.text
    assert empty_card.json()["detail"] == DETAIL_ID_CARD_REQUIRED

    missing_cert = client.post(
        "/api/hygiene/staff/register",
        json={
            "name": "张三",
            "phone": phone(43),
            "password": PASSWORD,
            "id_card_no": id_card(43),
        },
    )
    assert missing_cert.status_code == 400, missing_cert.text
    assert missing_cert.json()["detail"] == DETAIL_CERT_REQUIRED


def test_health_cert_date_cannot_be_in_the_future(profile_http):
    client, _db, _accounts = profile_http
    future = _register(
        client, name="张三", phone_no=phone(51), card=id_card(51), cert_date="2026-09-14"
    )
    assert future.status_code == 400, future.text
    assert future.json()["detail"] == DETAIL_CERT_FUTURE

    # 今天可以（不是将来）。
    today_ok = _register(
        client, name="张三", phone_no=phone(52), card=id_card(52), cert_date=TODAY
    )
    assert today_ok.status_code == 200, today_ok.text

    bad_shape = _register(
        client, name="张三", phone_no=phone(53), card=id_card(53), cert_date="2026/01/01"
    )
    assert bad_shape.status_code == 400, bad_shape.text
    assert bad_shape.json()["detail"] == "日期格式应为 YYYY-MM-DD"


# ── 3. 批准门槛 ──────────────────────────────────────────────────────────────

def test_approve_needs_base_salary_and_hire_date(profile_http):
    client, _db, accounts = profile_http
    _admin(client)
    both_missing = _seed_profile(client, accounts, serial=61, name="陈晓", cert_date=CERT_OK)
    blocked = client.post(f"/api/hygiene/admin/roster/{both_missing}/approve")
    assert blocked.status_code == 400, blocked.text
    assert blocked.json()["detail"] == DETAIL_APPROVE_BOTH

    only_salary = _seed_profile(client, accounts, serial=62, name="李娜", base_salary=8000)
    blocked_salary = client.post(f"/api/hygiene/admin/roster/{only_salary}/approve")
    assert blocked_salary.status_code == 400, blocked_salary.text
    assert blocked_salary.json()["detail"] == DETAIL_APPROVE_HIRE

    only_hire = _seed_profile(client, accounts, serial=63, name="王强", hire_date="2026-08-01")
    blocked_hire = client.post(f"/api/hygiene/admin/roster/{only_hire}/approve")
    assert blocked_hire.status_code == 400, blocked_hire.text
    assert blocked_hire.json()["detail"] == DETAIL_APPROVE_SALARY

    complete = _seed_profile(
        client,
        accounts,
        serial=64,
        name="赵敏",
        base_salary=0,
        hire_date="2026-08-01",
    )
    approved = client.post(f"/api/hygiene/admin/roster/{complete}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["employee"]["approved"] is True


def test_enable_is_not_gated_by_the_approval_threshold(profile_http):
    """重新启用已停用的人不走批准门槛（恢复不是新入职）。"""
    client, _db, accounts = profile_http
    _admin(client)
    employee_id = _seed_profile(client, accounts, serial=71, name="孙平")
    assert (
        client.post(f"/api/hygiene/admin/roster/{employee_id}/disable").status_code == 200
    )
    enabled = client.post(f"/api/hygiene/admin/roster/{employee_id}/enable")
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["employee"]["disabled"] is False


# ── 4. PATCH：四个字段各自可选、空 = 清空、admin_caps 仍是整组替换 ─────────────

def test_patch_profile_fields_and_clearing(profile_http):
    client, _db, accounts = profile_http
    _admin(client)
    employee_id = _seed_profile(client, accounts, serial=81, name="王强")
    card = id_card(81)
    resp = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}",
        json={
            "job_title": "领班",
            "base_salary": 6000,
            "hire_date": "2025-03-01",
            "id_card_no": card,
            "health_cert_date": CERT_SOON,
        },
    )
    assert resp.status_code == 200, resp.text
    row = _row(client, employee_id)
    assert row["job_title"] == "领班"
    assert row["base_salary"] == 6000
    assert row["hire_date"] == "2025-03-01"
    assert row["id_card_no"] == card
    assert row["health_cert_date"] == CERT_SOON
    assert row["health_cert_expires_on"] == "2026-10-03"
    assert row["profile_incomplete"] is False

    cleared = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}",
        json={
            "base_salary": None,
            "hire_date": "",
            "health_cert_date": None,
            "id_card_no": "",
        },
    )
    assert cleared.status_code == 200, cleared.text
    row = _row(client, employee_id)
    assert row["base_salary"] is None
    assert row["hire_date"] is None
    assert row["id_card_no"] is None
    assert row["health_cert_date"] is None
    assert row["health_cert_state"] == "none"
    assert row["profile_incomplete"] is True


def test_patch_base_salary_validation_messages(profile_http):
    client, _db, accounts = profile_http
    _admin(client)
    employee_id = _seed_profile(client, accounts, serial=91, name="赵敏")
    for value, detail in (
        (12.5, DETAIL_SALARY_NOT_INT),
        ("abc", DETAIL_SALARY_NOT_INT),
        (-1, DETAIL_SALARY_NEGATIVE),
        (1000000, DETAIL_SALARY_TOO_LARGE),
    ):
        resp = client.patch(
            f"/api/hygiene/admin/roster/{employee_id}", json={"base_salary": value}
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == detail

    # `0` 是合法值，而且不算「待补」。
    zero = client.patch(f"/api/hygiene/admin/roster/{employee_id}", json={"base_salary": 0})
    assert zero.status_code == 200, zero.text
    row = _row(client, employee_id)
    assert row["base_salary"] == 0
    assert row["profile_incomplete"] is True  # 入职日期还空着


def test_patch_admin_caps_is_still_a_whole_group_replacement(profile_http):
    client, _db, accounts = profile_http
    _admin(client)
    employee_id = _seed_profile(client, accounts, serial=101, name="周伟")
    first = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}",
        json={"admin_caps": ["daily_review", "data"]},
    )
    assert first.status_code == 200, first.text
    row = _row(client, employee_id)
    assert row["admin_caps"] == ["daily_review", "data"]
    assert row["permission"] == "管理员"  # 标签由开关派生（ADR 0093）

    second = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}", json={"admin_caps": ["fix"]}
    )
    assert second.status_code == 200, second.text
    row = _row(client, employee_id)
    assert row["admin_caps"] == ["fix"], "整组替换：没带回的键就该没了"
    # 没有 permission ↔ caps 的一致性校验或派生写回：库那一列不被这次 PATCH 碰。
    assert row["permission"] == "管理员"


def test_patch_without_any_field_is_400(profile_http):
    client, _db, accounts = profile_http
    _admin(client)
    employee_id = _seed_profile(client, accounts, serial=111, name="张三")
    resp = client.patch(f"/api/hygiene/admin/roster/{employee_id}", json={})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == DETAIL_NO_CHANGES


# ── 5. 花名册读：三态、派生到期日、待补、注册时间 ──────────────────────────────

def test_roster_rows_carry_profile_and_health_cert_state(profile_http):
    client, _db, accounts = profile_http
    _admin(client)
    soon = _seed_profile(client, accounts, serial=121, name="王强", cert_date=CERT_SOON)
    ok = _seed_profile(client, accounts, serial=122, name="李娜", cert_date=CERT_OK)
    expired = _seed_profile(client, accounts, serial=123, name="孙平", cert_date=CERT_EXPIRED)
    nothing = _seed_profile(client, accounts, serial=124, name="赵敏", cert_date=None)

    rows = {row["id"]: row for row in _roster(client)}
    assert rows[soon]["health_cert_state"] == "soon"
    assert rows[soon]["health_cert_expires_on"] == "2026-10-03"
    assert rows[soon]["health_cert_days_left"] == 20
    assert rows[ok]["health_cert_state"] == "ok"
    assert rows[expired]["health_cert_state"] == "expired"
    assert rows[expired]["health_cert_days_left"] == -35
    assert rows[nothing]["health_cert_state"] == "none"
    assert rows[nothing]["health_cert_expires_on"] is None
    assert rows[nothing]["health_cert_days_left"] is None

    # 「待补」= 四项任一为空。
    assert rows[soon]["profile_incomplete"] is True
    assert rows[nothing]["profile_incomplete"] is True
    # 注册时间：列本来就在，这次才放进返回；格式是带偏移的 ISO（前端按北京时渲染）。
    assert rows[soon]["created_at"].startswith("2026-09-13T10:00:00")
    assert rows[soon]["created_at"].endswith("+08:00")
    # 列表行不该混进敏感字段之外的东西：这里只确认四个档案键都在（空值为 null）。
    for key in ("base_salary", "hire_date", "id_card_no", "health_cert_date"):
        assert key in rows[nothing]


def test_health_cert_expiry_boundary(profile_http):
    """到期日**当天**算临期最后一天；过期从次日算起（design §13.4）。"""
    client, _db, accounts = profile_http
    _admin(client)
    last_day = _seed_profile(
        client, accounts, serial=131, name="今天到期", cert_date=CERT_EXPIRES_TODAY
    )
    just_expired = _seed_profile(
        client, accounts, serial=132, name="昨天到期", cert_date=CERT_EXPIRED_YESTERDAY
    )
    rows = {row["id"]: row for row in _roster(client)}
    assert rows[last_day]["health_cert_expires_on"] == TODAY
    assert rows[last_day]["health_cert_days_left"] == 0
    assert rows[last_day]["health_cert_state"] == "soon"
    assert rows[just_expired]["health_cert_expires_on"] == "2026-09-12"
    assert rows[just_expired]["health_cert_state"] == "expired"


def test_profile_incomplete_ignores_zero_salary(profile_http):
    client, _db, accounts = profile_http
    _admin(client)
    employee_id = _seed_profile(
        client,
        accounts,
        serial=141,
        name="张三",
        base_salary=0,
        hire_date="2026-01-01",
    )
    assert _row(client, employee_id)["profile_incomplete"] is False


# ── 6. 员工端：只多三个键，敏感字段一个都不下发 ───────────────────────────────

def test_staff_me_has_profile_but_no_sensitive_field(profile_http):
    client, _db, accounts = profile_http
    _admin(client)
    card = id_card(151)
    employee_id = _seed_profile(
        client,
        accounts,
        serial=151,
        name="王强",
        cert_date=CERT_SOON,
        base_salary=8888,
        hire_date="2025-03-01",
    )
    assert client.post(f"/api/hygiene/admin/roster/{employee_id}/approve").status_code == 200
    # 管理端看得见身份证号（台账），员工端那份不许有它。
    assert _row(client, employee_id)["id_card_no"] == card

    client.cookies.delete(settings.SESSION_COOKIE_NAME)
    login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": phone(151), "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    me = client.get("/api/hygiene/staff/me")
    assert me.status_code == 200, me.text
    employee = me.json()["employee"]
    assert employee["hire_date"] == "2025-03-01"
    assert employee["health_cert_expires_on"] == "2026-10-03"
    assert employee["health_cert_state"] == "soon"
    for forbidden in (
        "base_salary",
        "id_card_no",
        "health_cert_date",
        "health_cert_days_left",
    ):
        assert forbidden not in employee, f"员工端不得下发 {forbidden}"
    # 连值本身都不能出现在响应文本里（防将来有人换个键名再塞回去）。
    assert card not in me.text
    assert "8888" not in me.text
    assert CERT_SOON not in me.text


# ── 7. 首页待办：健康证临期 / 已过期 ─────────────────────────────────────────

def test_daily_queue_reports_health_cert_due(profile_http):
    client, _db, accounts = profile_http
    _admin(client)
    _seed_profile(client, accounts, serial=161, name="王强", cert_date=CERT_SOON)
    _seed_profile(client, accounts, serial=162, name="孙平", cert_date=CERT_EXPIRED)
    _seed_profile(client, accounts, serial=163, name="李娜", cert_date=CERT_OK)
    stopped = _seed_profile(client, accounts, serial=164, name="周伟", cert_date=CERT_EXPIRED)
    assert client.post(f"/api/hygiene/admin/roster/{stopped}/disable").status_code == 200

    queue = client.get("/api/hygiene/admin/daily-queue")
    assert queue.status_code == 200, queue.text
    due = queue.json()["health_cert_due"]
    assert due["count"] == 2, "已停用的人不用催办，ok 的不进这一块"
    assert [item["name"] for item in due["items"]] == ["孙平", "王强"], "按到期日升序"
    assert due["items"][0]["state"] == "expired"
    assert due["items"][1]["state"] == "soon"
    assert due["items"][0]["expires_on"] == "2026-08-09"
    assert due["items"][1]["expires_on"] == "2026-10-03"

    # 历史回看分支也带这块（它是"现在"的事，与查的是哪一天无关）。
    with_date = client.get("/api/hygiene/admin/daily-queue", params={"date": "2026-09-01"})
    assert with_date.status_code == 200, with_date.text
    assert with_date.json()["health_cert_due"]["count"] == 2


def test_health_cert_derivation_is_shared_by_all_three_reads(profile_http):
    """三处读路径（花名册 / 员工端 me / 首页待办）算出同一个答案。"""
    client, _db, accounts = profile_http
    _admin(client)
    card = id_card(171)
    employee_id = _seed_profile(
        client,
        accounts,
        serial=171,
        name="王强",
        cert_date=CERT_SOON,
        base_salary=5000,
        hire_date="2025-01-01",
    )
    assert client.post(f"/api/hygiene/admin/roster/{employee_id}/approve").status_code == 200
    roster_row = _row(client, employee_id)
    queue_item = next(
        item
        for item in client.get("/api/hygiene/admin/daily-queue").json()["health_cert_due"][
            "items"
        ]
        if item["id"] == employee_id
    )
    client.cookies.delete(settings.SESSION_COOKIE_NAME)
    assert (
        client.post(
            "/api/hygiene/staff/login",
            json={"phone": phone(171), "password": PASSWORD},
        ).status_code
        == 200
    )
    me = client.get("/api/hygiene/staff/me").json()["employee"]

    assert roster_row["health_cert_expires_on"] == queue_item["expires_on"]
    assert me["health_cert_expires_on"] == queue_item["expires_on"]
    assert roster_row["health_cert_state"] == queue_item["state"] == me["health_cert_state"]
    assert roster_row["id_card_no"] == card


# ── 8. CSV 导出 ─────────────────────────────────────────────────────────────

def test_roster_export_csv_shape_and_columns(profile_http):
    client, _db, accounts = profile_http
    _admin(client)
    card = id_card(181)
    employee_id = _seed_profile(
        client,
        accounts,
        serial=181,
        name="王强",
        cert_date=CERT_SOON,
        base_salary=6000,
        hire_date="2025-03-01",
    )
    assert client.post(f"/api/hygiene/admin/roster/{employee_id}/approve").status_code == 200
    patched = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}",
        json={"job_title": "领班", "id_card_no": card, "admin_caps": ["daily_review", "data"]},
    )
    assert patched.status_code == 200, patched.text

    resp = client.get(EXPORT_PATH)
    assert resp.status_code == 200, resp.text
    assert resp.content.startswith(b"\xef\xbb\xbf"), "UTF-8 必须带 BOM（Excel 中文不乱码）"
    text = resp.content.decode("utf-8-sig")
    assert "\r\n" in text, "行尾要 CRLF（RFC4180）"
    lines = text.rstrip("\r\n").split("\r\n")
    assert lines[0] == EXPORT_HEADER
    assert len(lines) == 2
    cells = lines[1].split(",")
    assert cells[0] == "王强"
    assert cells[1] == phone(181)
    assert cells[2] == "领班"
    assert cells[3] == "已批准"
    assert cells[4] == "管理员"
    assert cells[5] == "日常验收", "只导员工端真生效的三项（超管专属七项不导）"
    assert cells[6] == card, "身份证号原样输出（台账用途）"
    assert cells[7] == CERT_SOON
    assert cells[8] == "2026-10-03"
    assert cells[9] == "6000"
    assert cells[10] == "2025-03-01"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", cells[11]), cells[11]

    disposition = resp.headers["content-disposition"]
    assert disposition == 'attachment; filename="roster-2026-09-13.csv"', disposition


def test_roster_export_follows_segment_and_keyword(profile_http):
    client, _db, accounts = profile_http
    _admin(client)
    _seed_profile(
        client,
        accounts,
        serial=191,
        name="王强",
        base_salary=6000,
        hire_date="2025-03-01",
    )
    _seed_profile(client, accounts, serial=192, name="赵敏")
    stopped = _seed_profile(client, accounts, serial=193, name="周伟")
    assert client.post(f"/api/hygiene/admin/roster/{stopped}/disable").status_code == 200

    assert _export_names(client) == ["王强", "赵敏"], "默认 = 全部（不含已停用）"
    assert _export_names(client, filter="all") == ["王强", "赵敏"]
    assert _export_names(client, filter="missing") == ["赵敏"]
    assert _export_names(client, filter="disabled") == ["周伟"]
    assert _export_names(client, status="missing") == ["赵敏"], "票面用 status 写同一件事"
    assert _export_names(client, filter="missing", q="赵") == ["赵敏"]
    assert _export_names(client, q=phone(191)) == ["王强"], "q 跟搜索框同一口径"
    assert _export_names(client, filter="all", q="赵") == ["赵敏"]

    invalid = client.get(EXPORT_PATH, params={"filter": "bogus"})
    assert invalid.status_code == 400, invalid.text

    # 分段与关键字一起把行筛空时，回一句人话（0 行的文件对管理员毫无意义）。
    # 王强档案齐备，所以「待补 + 王」必然是空集。
    for params in ({"filter": "missing", "q": "王"}, {"filter": "missing", "q": "没有人"}):
        empty = client.get(EXPORT_PATH, params=params)
        assert empty.status_code == 400, empty.text
        assert empty.json()["detail"] == DETAIL_EXPORT_EMPTY


def test_roster_export_requires_admin_session(profile_http):
    client, _db, _accounts = profile_http
    assert client.get(EXPORT_PATH).status_code == 401


def test_no_id_card_number_in_logs_or_error_messages(profile_http, caplog):
    """身份证号是敏感字段：库里明文可以，日志与异常消息里绝不许出现。"""
    client, _db, accounts = profile_http
    _admin(client)
    card = id_card(201)
    with caplog.at_level(logging.DEBUG):
        created = _register(
            client, name="王强", phone_no=phone(201), card=card, cert_date=CERT_SOON
        )
        assert created.status_code == 200, created.text
        employee_id = created.json()["employee"]["id"]
        assert (
            client.patch(
                f"/api/hygiene/admin/roster/{employee_id}",
                json={"base_salary": 6000, "hire_date": "2025-03-01"},
            ).status_code
            == 200
        )
        assert (
            client.post(f"/api/hygiene/admin/roster/{employee_id}/approve").status_code == 200
        )
        exported = client.get(EXPORT_PATH)
        assert exported.status_code == 200, exported.text
        assert card in exported.text, "导出带身份证号（台账用途）"
        # 不合法的号码也不能被回显（错误消息只说"哪里不对"）。
        bad = client.patch(
            f"/api/hygiene/admin/roster/{employee_id}",
            json={"id_card_no": card[:16] + "99"},
        )
        assert bad.status_code == 400, bad.text
        assert card not in bad.text

    assert "花名册导出" in caplog.text, "导出必须记一条日志"
    assert "rows=1" in caplog.text, "日志里有行数"
    logged = caplog.text
    assert card not in logged
    assert _row(client, employee_id)["id_card_no"] == card, "库里仍是明文（用户明确选择）"
    assert "6000" not in logged


# ── 9. 迁移 0020：加成性、可重复执行、部分唯一索引真的在库里 ──────────────────

def _apply_migration() -> None:
    """按管理员在「数据库迁移」面板里应用脚本的同一条路径跑一次 0020。

    会话开始时 `tests/conftest.py` 已经按序号应用过它了 —— 这里再跑两遍，正好把
    「可重复执行」也一起验了（门店重跑一次不该炸）。
    """
    proc = subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsn_from_env(), "-f", str(MIGRATION_PATH)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr.strip() or proc.stdout.strip()


def _insert_employee(phone_no: str, card) -> int:
    return int(
        scalar(
            """INSERT INTO hygiene_employees
                 (phone, password_hash, job_title, permission, approved, disabled,
                  created_at, updated_at, name, id_card_no)
               VALUES (?, 'x', '', '普通员工', 0, 0, '2026-09-13T10:00:00+08:00',
                       '2026-09-13T10:00:00+08:00', '测试', ?)
               RETURNING id""",
            (phone_no, card),
        )
    )


def test_migration_is_additive_and_repeatable():
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "DROP" not in sql.upper(), "迁移只做加成性变更：不 DROP 任何东西"
    assert sql.count("ADD COLUMN IF NOT EXISTS") == 4
    assert sql.count("CREATE UNIQUE INDEX IF NOT EXISTS") == 1

    _apply_migration()
    _apply_migration()  # 幂等：重复执行不报错

    columns = {
        row[0]: (row[1], row[2])
        for row in fetch_all(
            """SELECT column_name, data_type, is_nullable
               FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'hygiene_employees'
                 AND column_name IN ('id_card_no', 'health_cert_date', 'base_salary',
                                     'hire_date')"""
        )
    }
    # 日期列与本库既有的日期列同构（TEXT / ISO 字符串），底薪是整数元（BIGINT）。
    assert columns["id_card_no"] == ("text", "YES")
    assert columns["health_cert_date"] == ("text", "YES")
    assert columns["hire_date"] == ("text", "YES")
    assert columns["base_salary"] == ("bigint", "YES")


def test_partial_unique_index_blocks_a_second_row_with_the_same_id_card():
    """同号只留一份档，靠的是**库里的局部唯一索引**（不只服务层那次预检查）。"""
    card = id_card(900)
    _insert_employee("13900139001", card)
    with pytest.raises(Exception) as raised:
        _insert_employee("13900139002", card)
    assert "idx_hygiene_employees_id_card" in str(raised.value) or "duplicate key" in str(
        raised.value
    ).lower()

    # NULL（还没填身份证的人）不在索引范围内：几个存量员工可以共存。
    _insert_employee("13900139003", None)
    _insert_employee("13900139004", None)
