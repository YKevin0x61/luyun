#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工龄奖与人事提醒（票 05）：折算月、档位、提醒清单、留痕、员工端投影。

口径与决策取自 `.scratch/overtime-and-reminders/spec.md` 与 `docs/adr/0101`：
**折算月是通用规则**（入职日 ≤ 15 号算当月、≥ 16 号起算下月，9 月不是特例），
第 N 年 = N×100、第 10 年 1000 封顶；「应为」是派生的、「当前工龄奖」是落库的，
两者不一致是合法状态 —— 低于应为的持续提醒（含历史欠调），高于应为的只标出来、
**绝不自动改写**档案里的钱。

本票只做「档案待补」里**缺入职日期**那一类；缺身份证与生日名单是票 06 的活，
它接着扩展同一个端点与同一个页面（响应里每块都是 `{items, count}` 的形状）。
"""

import asyncio
from datetime import datetime
from pathlib import Path

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
from services.identity.profile import (
    birthday_from_id_card,
    birthday_in_year,
    seniority_adjust_month,
    seniority_base_month,
    seniority_next_adjust_month,
    seniority_should_be,
)
from tests.hygiene_profile import id_card
from tests.pg_probe import fetch_all

# 与别的卫生用例同一个固定时刻：服务端的「今天」就是它。
FIXED_NOW = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-13"

PASSWORD = "password123"
ADMIN_INIT = {
    "username": "admin",
    "password": "password123",
    "confirm_password": "password123",
}

REMINDERS_PATH = "/api/hygiene/admin/hr-reminders"

# 逐字文案（服务端这一份改了，前端那份必须跟着改 —— 两边各有测试）。
DETAIL_SENIORITY_NOT_INT = "工龄奖只能是整数元"
DETAIL_SENIORITY_NEGATIVE = "工龄奖不能是负数"
DETAIL_SENIORITY_TOO_LARGE = "工龄奖最多 999999 元"

# 健康证办理日期：只为把「档案四项」凑齐（本票不关心它的到期口径）。
CERT_OK = "2025-12-01"

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "pg"
    / "0023_seniority_bonus.sql"
)


def phone(serial: int) -> str:
    """11 位手机号（`1[3-9]\\d{9}`），按序号区分。"""
    return f"1390013{serial % 10000:04d}"


# ── 1. 折算月：入职日 ≤ 15 号算当月、≥ 16 号起算下月（通用规则）──────────────
# 这些是**已知答案的字面量**（票面点名的三组边界），不是照实现再算一遍。

def test_seniority_base_month_folds_on_the_sixteenth():
    assert seniority_base_month("2025-09-15") == "2025-09", "15 号当天算当月"
    assert seniority_base_month("2025-09-16") == "2025-10", "16 号起算下月"
    assert seniority_base_month("2025-03-16") == "2025-04"
    assert seniority_base_month("2025-02-15") == "2025-02"
    assert seniority_base_month("2025-02-16") == "2025-03"
    assert seniority_base_month("2025-12-16") == "2026-01", "跨年要进位"


def test_seniority_base_month_is_none_for_missing_or_broken_dates():
    assert seniority_base_month(None) is None
    assert seniority_base_month("") is None
    assert seniority_base_month("2025-13-01") is None, "形状对、日历上没有这一天"
    assert seniority_base_month("2025/09/15") is None


def test_seniority_adjust_month_is_the_first_anniversary_month():
    assert seniority_adjust_month("2025-09-15") == "2026-09"
    assert seniority_adjust_month("2025-09-16") == "2026-10"
    assert seniority_adjust_month("2025-03-16") == "2026-04"
    assert seniority_adjust_month("") is None


# ── 2. 档位：第 N 年 = N×100，第 10 年 1000 封顶 ──────────────────────────────

def test_seniority_should_be_counts_full_years_and_caps_at_ten():
    # 9/15 入职 → 折算月 2025-09 → 第 N 次调整在 2025+N 年的 9 月。
    assert seniority_should_be("2025-09-15", "2026-08-31") == 0, "还没满一年"
    assert seniority_should_be("2025-09-15", "2026-09-01") == 100, "满一年那个月"
    assert seniority_should_be("2025-09-15", "2030-09-30") == 500, "第 5 年"
    assert seniority_should_be("2025-09-15", "2035-09-01") == 1000, "第 10 年"
    assert seniority_should_be("2025-09-15", "2040-09-01") == 1000, "第 15 年仍封顶"


def test_seniority_should_be_is_zero_without_a_hire_date():
    assert seniority_should_be(None, "2026-09-13") == 0
    assert seniority_should_be("", "2026-09-13") == 0
    assert seniority_should_be("2025-13-01", "2026-09-13") == 0


def test_next_adjust_month_is_none_once_the_cap_is_reached():
    assert seniority_next_adjust_month("2025-09-15", "2026-08-01") == "2026-09"
    assert seniority_next_adjust_month("2025-09-15", "2026-09-01") == "2027-09"
    assert seniority_next_adjust_month("2025-09-15", "2034-09-01") == "2035-09"
    assert seniority_next_adjust_month("2025-09-15", "2035-09-01") is None, "已封顶"
    assert seniority_next_adjust_month(None, "2026-09-13") is None


# ── 夹具：管理端 + 员工端的真 HTTP 链路 ──────────────────────────────────────

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
def reminders_http():
    """同 `tests/test_hygiene_employee_profile.py` 的夹具：真库、真路由、真 cookie。"""
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
    """建管理员并留下管理端 cookie（花名册与提醒那些接口都要它）。"""
    resp = client.post("/api/auth/init", json=ADMIN_INIT)
    assert resp.status_code == 200, resp.text
    return client


def _seed(
    client,
    accounts,
    *,
    serial: int,
    name: str,
    hire_date=None,
    bonus=None,
    disabled: bool = False,
    birth: str = "19900307",
    cert: str = CERT_OK,
) -> int:
    """建一个档案齐全的人（身份证 + 健康证都填，工龄奖那两栏由参数决定）。

    `cert` 默认是 `CERT_OK`（有效期还早）；要造临期 / 过期的人就传一个更早的办理日。
    """
    employee = _run(
        accounts.register(
            phone(serial),
            PASSWORD,
            name,
            id_card_no=id_card(serial, birth=birth),
            health_cert_date=cert,
        )
    )
    payload = {}
    if hire_date is not None:
        payload["hire_date"] = hire_date
    if bonus is not None:
        payload["seniority_bonus"] = bonus
    if payload:
        resp = client.patch(f"/api/hygiene/admin/roster/{employee['id']}", json=payload)
        assert resp.status_code == 200, resp.text
    if disabled:
        _run(accounts.disable(employee["id"]))
    return employee["id"]


def _roster(client) -> list:
    resp = client.get("/api/hygiene/admin/roster")
    assert resp.status_code == 200, resp.text
    return resp.json()["employees"]


def _row(client, employee_id: int) -> dict:
    return next(row for row in _roster(client) if row["id"] == employee_id)


def _changes(employee_id: int) -> list[tuple]:
    """工龄奖的留痕（谁、何时、从多少到多少）—— 它只写不读，所以这里直接查库。"""
    return fetch_all(
        "SELECT old_value, new_value, changed_by, changed_at"
        " FROM seniority_bonus_changes WHERE employee_id = ? ORDER BY id",
        (employee_id,),
    )


def _reminders(client) -> dict:
    resp = client.get(REMINDERS_PATH)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 3. 落账：花名册 PATCH 写「当前工龄奖」，每次改动留一行 ────────────────────

def test_patch_writes_seniority_bonus_and_leaves_one_change_row(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    employee_id = _seed(client, accounts, serial=1, name="张三", hire_date="2025-09-15")

    resp = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}", json={"seniority_bonus": 100}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["employee"]["seniority_bonus"] == 100, "花名册行要带出这一栏"
    assert _row(client, employee_id)["seniority_bonus"] == 100

    rows = _changes(employee_id)
    assert len(rows) == 1
    old_value, new_value, changed_by, changed_at = rows[0]
    assert (old_value, new_value) == (None, 100), "留痕要记「从多少到多少」"
    assert changed_by == "super"
    assert changed_at, "留痕要记什么时候改的"


def test_the_same_value_does_not_write_another_change_row(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    employee_id = _seed(client, accounts, serial=2, name="李四", hire_date="2025-09-15")
    for _ in range(2):
        resp = client.patch(
            f"/api/hygiene/admin/roster/{employee_id}", json={"seniority_bonus": 200}
        )
        assert resp.status_code == 200, resp.text
    assert len(_changes(employee_id)) == 1, "值没变不该再写一行"


def test_seniority_bonus_can_be_cleared(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    employee_id = _seed(
        client, accounts, serial=3, name="王五", hire_date="2025-09-15", bonus=300
    )
    resp = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}", json={"seniority_bonus": None}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["employee"]["seniority_bonus"] is None, "清空 = 落回 NULL"
    assert _changes(employee_id)[-1][:2] == (300, None)


def test_seniority_bonus_must_be_a_non_negative_integer(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    employee_id = _seed(client, accounts, serial=4, name="赵六", hire_date="2025-09-15")

    bad = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}", json={"seniority_bonus": 12.5}
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["detail"] == DETAIL_SENIORITY_NOT_INT

    negative = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}", json={"seniority_bonus": -100}
    )
    assert negative.status_code == 400, negative.text
    assert negative.json()["detail"] == DETAIL_SENIORITY_NEGATIVE

    too_large = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}", json={"seniority_bonus": 1000000}
    )
    assert too_large.status_code == 400, too_large.text
    assert too_large.json()["detail"] == DETAIL_SENIORITY_TOO_LARGE


def test_seniority_bonus_is_not_part_of_the_incomplete_profile_gate(reminders_http):
    """空值的意思是「还没调过」，不是「档案缺一项」—— 它不挡批准（ADR 0101）。"""
    client, _db, accounts = reminders_http
    _admin(client)
    employee_id = _seed(client, accounts, serial=5, name="孙七", hire_date="2025-09-15")
    resp = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}", json={"base_salary": 5000}
    )
    assert resp.status_code == 200, resp.text

    row = _row(client, employee_id)
    assert row["seniority_bonus"] is None
    assert row["profile_incomplete"] is False, "工龄奖不进「待补」四项"
    approved = client.post(f"/api/hygiene/admin/roster/{employee_id}/approve")
    assert approved.status_code == 200, approved.text


# ── 4. 提醒清单：该调的（含历史欠调）在名单上、调过头的只标出来 ────────────────
# 固定时刻是 2026-09-13；下面每条入职日都换算过一遍（折算月 → 第 N 年 → 应为值）。

def test_reminders_lists_who_is_due_with_the_gap(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    # 2025-09-15 入职 → 折算月 2025-09 → 2026-09 满一年 → 应为 100。
    due = _seed(client, accounts, serial=11, name="该调的", hire_date="2025-09-15")
    # 2025-10-16 入职 → 折算月 2025-11 → 要到 2026-11 才满一年 → 还没到时候。
    early = _seed(client, accounts, serial=12, name="还没满", hire_date="2025-10-16")

    data = _reminders(client)
    items = {item["id"]: item for item in data["seniority"]["items"]}
    assert due in items
    assert early not in items, "还没满一年的人不该被催"
    assert items[due]["state"] == "due"
    assert (items[due]["current"], items[due]["should_be"]) == (None, 100)
    assert items[due]["gap"] == 100, "要给出差额"
    assert items[due]["due_month"] == "2026-09", "满一年那次就是 2026-09"
    assert data["seniority"]["count"] == 1


def test_reminders_keeps_history_arrears_and_flags_overpaid(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    # 2023-09-15 入职 → 到 2026-09 已满 3 年 → 应为 300，档案里却还是 100。
    arrears = _seed(
        client, accounts, serial=13, name="欠调的", hire_date="2023-09-15", bonus=100
    )
    # 2025-09-15 入职 → 应为 100，档案里却写着 500（调过头）。
    over = _seed(
        client, accounts, serial=14, name="调过头", hire_date="2025-09-15", bonus=500
    )
    # 现值等于应为 → 不该出现在名单上。
    settled = _seed(
        client, accounts, serial=15, name="刚好", hire_date="2025-09-15", bonus=100
    )

    data = _reminders(client)
    items = {item["id"]: item for item in data["seniority"]["items"]}
    assert items[arrears]["state"] == "due", "历史欠调要一直挂着"
    assert (items[arrears]["should_be"], items[arrears]["gap"]) == (300, 200)
    assert items[arrears]["due_month"] == "2026-09", "欠的是第 3 年那次"
    assert items[over]["state"] == "over"
    assert items[over]["current"] == 500, "调过头的只标出来"
    assert _row(client, over)["seniority_bonus"] == 500, "绝不自动改写档案里的钱"
    assert settled not in items
    assert data["seniority"]["count"] == 1, "count 只数要处理的（due）"


def test_reminders_goes_quiet_once_the_bonus_reaches_the_cap(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    # 2015-09-15 入职 → 到 2026-09 已满 11 年，档位封在 1000。
    capped = _seed(
        client, accounts, serial=16, name="封顶了", hire_date="2015-09-15", bonus=1000
    )
    data = _reminders(client)
    assert capped not in {item["id"] for item in data["seniority"]["items"]}, "到顶就不再催"


def test_reminders_skips_disabled_employees(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    gone = _seed(
        client, accounts, serial=17, name="停用了", hire_date="2025-09-15", disabled=True
    )
    data = _reminders(client)
    assert gone not in {item["id"] for item in data["seniority"]["items"]}
    assert gone not in {item["id"] for item in data["incomplete"]["items"]}


def test_reminders_puts_missing_hire_date_into_the_incomplete_group(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    missing = _seed(client, accounts, serial=18, name="没入职日")
    settled = _seed(client, accounts, serial=19, name="档案齐", hire_date="2025-09-15")

    data = _reminders(client)
    assert missing not in {item["id"] for item in data["seniority"]["items"]}, "算不出来不进名单"
    incomplete = {item["id"]: item for item in data["incomplete"]["items"]}
    assert incomplete[missing]["missing"] == ["hire_date"], "要点名缺哪一项"
    assert incomplete[missing]["name"] == "没入职日"
    assert settled not in incomplete, "档案齐的人不该出现在待补里"
    assert data["incomplete"]["count"] == 1


def test_reminders_needs_the_admin_session(reminders_http):
    """提醒里有工龄奖（钱）—— 没登录一律拿不到（401），不是空清单。"""
    client, _db, _accounts = reminders_http
    assert client.get(REMINDERS_PATH).status_code == 401


# ── 5. 员工端：看得到自己的档位与下次调整月，看不到身份证号与底薪 ─────────────

def _approve(client, accounts, employee_id: int, serial: int) -> None:
    """把一个人弄成能登录的员工：档案四项齐 → 批准 → 用他自己的手机号登录。"""
    resp = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}", json={"base_salary": 5000}
    )
    assert resp.status_code == 200, resp.text
    approved = client.post(f"/api/hygiene/admin/roster/{employee_id}/approve")
    assert approved.status_code == 200, approved.text
    login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": phone(serial), "password": PASSWORD},
    )
    assert login.status_code == 200, login.text


def test_staff_me_carries_my_seniority_and_next_adjust_month(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    employee_id = _seed(
        client, accounts, serial=21, name="我自己", hire_date="2025-09-15", bonus=100
    )
    _approve(client, accounts, employee_id, 21)

    resp = client.get("/api/hygiene/staff/me")
    assert resp.status_code == 200, resp.text
    employee = resp.json()["employee"]
    assert employee["seniority_bonus"] == 100, "看得到自己当前拿的档位"
    assert employee["seniority_should_be"] == 100
    assert employee["seniority_next_adjust_month"] == "2027-09", "下次调整在明年 9 月"

    for secret in ("id_card_no", "base_salary", "health_cert_date"):
        assert secret not in employee, f"员工端不许下发 {secret}（ADR 0098）"


def test_staff_me_says_nothing_when_the_hire_date_is_missing(reminders_http):
    """入职日期待补的人：档位与下次调整月都回空，而不是编一个 0 或今天。"""
    client, _db, accounts = reminders_http
    _admin(client)
    employee_id = _seed(
        client, accounts, serial=22, name="没入职日", hire_date="2025-09-15"
    )
    _approve(client, accounts, employee_id, 22)
    # 批准之后档案又被清空（存量员工补录到一半的样子）—— 员工端不该自己编一个值。
    cleared = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}", json={"hire_date": None}
    )
    assert cleared.status_code == 200, cleared.text

    employee = client.get("/api/hygiene/staff/me").json()["employee"]
    assert employee["seniority_bonus"] is None
    assert employee["seniority_should_be"] == 0
    assert employee["seniority_next_adjust_month"] is None


# ── 6. 生日：只从 18 位身份证的第 7–14 位取（ADR 0102）────────────────────────
# 期望值是**字面量** —— 生日就写在造号时传进去的 `birth` 里，不是照实现再解一遍。

def test_birthday_comes_from_the_id_card_digits():
    assert birthday_from_id_card(id_card(1, birth="19900307")) == "03-07"
    assert birthday_from_id_card(id_card(2, birth="20001231")) == "12-31"
    assert (
        birthday_from_id_card(id_card(3, birth="19960229")) == "02-29"
    ), "闰年的 2 月 29 日是合法生日（平年折算见下一个用例）"


def test_birthday_is_none_when_it_cannot_be_read():
    """读不出来一律回 ``None`` —— 那个人进「档案待补」，不静默跳过。"""
    assert birthday_from_id_card(None) is None
    assert birthday_from_id_card("") is None
    assert birthday_from_id_card("   ") is None
    assert birthday_from_id_card("11010519900307123") is None, "15 位老号没有这一段"
    assert birthday_from_id_card("110105199002301234") is None, "2 月 30 日：日历上没有"
    assert birthday_from_id_card("110105199013011234") is None, "13 月"
    assert birthday_from_id_card("11010519900A071234") is None, "形状坏"


def test_leap_day_birthday_folds_to_the_28th_in_a_common_year():
    assert birthday_in_year("02-29", 2028) == "02-29", "闰年原样"
    assert birthday_in_year("02-29", 2026) == "02-28", "平年按 2 月 28 日提醒"
    assert birthday_in_year("03-07", 2026) == "03-07"
    assert birthday_in_year(None, 2026) is None
    assert birthday_in_year("13-01", 2026) is None


# ── 7. 提醒清单：本月生日按日期排，标出今天 / 已过 / 未到 ──────────────────────
# 夹具那一刻是 2026-09-13，所以「本月」就是 2026-09。

def test_birthdays_list_the_current_month_sorted_with_state(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    _seed(client, accounts, serial=41, name="初七生", birth="19900907")
    _seed(client, accounts, serial=42, name="十三生", birth="19960913")
    _seed(client, accounts, serial=43, name="二十生", birth="19880920")
    _seed(client, accounts, serial=44, name="十月生", birth="19901005")

    items = _reminders(client)["birthdays"]["items"]

    assert [item["name"] for item in items] == ["初七生", "十三生", "二十生"], (
        "按日期升序；别的月份的人不进这份名单"
    )
    assert [item["on"] for item in items] == ["09-07", "09-13", "09-20"]
    assert [item["state"] for item in items] == ["past", "today", "upcoming"]
    assert _reminders(client)["birthdays"]["count"] == 3


def test_leap_day_birthday_lands_on_february_28th_in_a_common_year(reminders_http):
    """生日本身是 02-29，但 2027 是平年 —— 这一年按 2 月 28 日提醒他。"""
    client, db, accounts = reminders_http
    _admin(client)
    _seed(client, accounts, serial=45, name="闰日生", birth="19960229")

    feb = EmployeeAccounts(db, now=lambda: datetime(2027, 2, 28, 10, 0, tzinfo=CHINA_TZ))
    data = _run(feb.list_hr_reminders())
    item = next(i for i in data["birthdays"]["items"] if i["name"] == "闰日生")

    assert item["birthday"] == "02-29", "生日本身不改写"
    assert item["on"] == "02-28", "平年折到 2 月 28 日"
    assert item["state"] == "today"


def test_birthdays_skip_disabled_employees(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    _seed(client, accounts, serial=46, name="在职的", birth="19900920")
    _seed(client, accounts, serial=47, name="停用的", birth="19900921", disabled=True)

    names = [item["name"] for item in _reminders(client)["birthdays"]["items"]]

    assert names == ["在职的"], "人都停了就不用提醒生日（同健康证待办的口径）"


# ── 8. 档案待补：缺身份证与缺入职日期各点各的名，不互相吞掉 ────────────────────

def test_incomplete_names_every_missing_field(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    # 缺身份证 → 生日与「实名」那一路算不出来
    card_missing = _seed(
        client, accounts, serial=61, name="缺身份证", hire_date="2024-09-10"
    )
    resp = client.patch(
        f"/api/hygiene/admin/roster/{card_missing}", json={"id_card_no": ""}
    )
    assert resp.status_code == 200, resp.text
    # 缺入职日期 → 工龄奖算不出来
    _seed(client, accounts, serial=62, name="缺入职日")
    # 两样都缺 → **一行里**两项都点名，不是两行
    both_missing = _seed(
        client, accounts, serial=63, name="两样都缺", hire_date="2024-09-10"
    )
    resp = client.patch(
        f"/api/hygiene/admin/roster/{both_missing}",
        json={"id_card_no": "", "hire_date": ""},
    )
    assert resp.status_code == 200, resp.text

    data = _reminders(client)
    by_name = {item["name"]: item["missing"] for item in data["incomplete"]["items"]}

    assert by_name["缺身份证"] == ["id_card_no"]
    assert by_name["缺入职日"] == ["hire_date"]
    assert sorted(by_name["两样都缺"]) == ["hire_date", "id_card_no"]
    assert data["incomplete"]["count"] == 3
    birthday_names = [item["name"] for item in data["birthdays"]["items"]]
    assert "缺身份证" not in birthday_names, "读不出生日的人不进名单，只进待补"


# ── 9. 员工端：看得到自己的生日，仍然看不到身份证号（ADR 0098 的边界）──────────

def test_staff_me_shows_my_birthday_but_never_the_id_card(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    employee_id = _seed(
        client, accounts, serial=71, name="三月生日", hire_date="2024-09-10", birth="19960314"
    )
    _approve(client, accounts, employee_id, 71)

    resp = client.get("/api/hygiene/staff/me")
    assert resp.status_code == 200, resp.text
    employee = resp.json()["employee"]

    assert employee["birthday"] == "03-14", "生日是单独派生下发的一列"
    for secret in ("id_card_no", "base_salary", "health_cert_date"):
        assert secret not in employee, f"员工端不许下发 {secret}（ADR 0098）"


def test_staff_me_says_nothing_when_the_id_card_is_missing(reminders_http):
    client, _db, accounts = reminders_http
    _admin(client)
    employee_id = _seed(client, accounts, serial=72, name="没身份证", hire_date="2024-09-10")
    _approve(client, accounts, employee_id, 72)
    cleared = client.patch(
        f"/api/hygiene/admin/roster/{employee_id}", json={"id_card_no": ""}
    )
    assert cleared.status_code == 200, cleared.text

    employee = client.get("/api/hygiene/staff/me").json()["employee"]

    assert employee["birthday"] is None, "没有身份证就没有生日，不编一个"


def test_reminders_carry_the_health_cert_block(reminders_http):
    """健康证到期（2026-10-08 用户裁定：从首页单独一格并进这一页）。

    它**复用** `list_health_cert_due`，判据仍是 `profile.health_cert_status` —— 所以这一块
    与花名册行上的标签必然是同一个答案。这里顺带把「两处一致」也验一次，免得日后有人
    在这一页里另写一份阈值。
    """
    client, _db, accounts = reminders_http
    _admin(client)
    _seed(client, accounts, serial=81, name="过期证", hire_date="2020-09-15", cert="2024-01-10")
    _seed(client, accounts, serial=82, name="临期证", hire_date="2020-09-15", cert="2025-09-30")
    _seed(client, accounts, serial=83, name="证还好", hire_date="2020-09-15")  # CERT_OK
    _seed(
        client,
        accounts,
        serial=84,
        name="停用且过期",
        hire_date="2020-09-15",
        cert="2024-01-10",
        disabled=True,
    )

    data = client.get(REMINDERS_PATH).json()
    certs = data["certs"]
    # 按到期日升序（最急的在上面）；停用的人不进 —— 人都停了就不用催。
    assert [item["name"] for item in certs["items"]] == ["过期证", "临期证"]
    assert certs["count"] == 2
    by_name = {item["name"]: item for item in certs["items"]}
    assert by_name["过期证"]["state"] == "expired"
    assert by_name["临期证"]["state"] == "soon"
    # 到期日与还剩几天都由服务端算（办理日 + 12 个月）。
    assert by_name["过期证"]["expires_on"] == "2025-01-10"
    assert by_name["临期证"]["expires_on"] == "2026-09-30"
    assert by_name["临期证"]["days_left"] == 17

    # 与花名册那份标签同源：同一个人在两处的状态一致。
    roster = {
        row["name"]: row
        for row in client.get("/api/hygiene/admin/roster").json()["employees"]
    }
    assert roster["过期证"]["health_cert_state"] == "expired"
    assert roster["临期证"]["health_cert_state"] == "soon"
    assert roster["证还好"]["health_cert_state"] == "ok"
