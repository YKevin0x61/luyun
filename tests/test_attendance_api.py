#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""考勤表 HTTP 面（**主缝**）：一条真实请求链路 + 钉死的钟 + 真测试库。

票 01 建这条缝，后面几票（假期台账 03、封账 04、手填列 05、逐日改格 06、半天休 07、
导出 08）全部往这条缝里加用例，所以这里刻意只有两件东西：

* **一条真链路**：薄 FastAPI + `include_router` + `TestClient`，接口进去、JSON 出来。
  排班 / 加班的数据用**服务层**写进真库（真表真行），不走它们的 HTTP 面 —— 这道缝要
  回答的是考勤自己那一面「谁能打、打进去会看到什么」。
* **钉死的钟**：`AttendanceStore.__init__` 的 `now` 默认值被 monkeypatch 换成
  `FIXED_NOW`（2026-09-24 10:00 北京时）。路由里现造的实例因此走同一个「今天」——
  真钟一走过 06:00 那个切日点，这份文件里写死的日子就整片错位（同
  `tests/test_scheduling_api.py` 的 `scheduling_http`）。

口径见 `GLOSSARY.md` 的「考勤与假期」、`docs/adr/0112`。
"""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.attendance as attendance_module
import api.auth as auth_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.attendance import AttendanceStore
from services.hygiene.accounts import EmployeeAccounts
from services.overtime.ledger import EntryActor, OvertimeLedger
from services.scheduling.store import SchedulingStore

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-24"
MONTH = "2026-09"


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
def attendance_http(tmp_path, monkeypatch):
    """一条真实请求链路，考勤服务的钟钉在 `FIXED_NOW` 上。"""
    real_init = AttendanceStore.__init__

    def _pinned_init(self, *args, **kwargs):
        # 不抄真实签名：将来 `__init__` 多一个参数，抄下来的那份会以 `TypeError`
        # 打死这一整个文件；`setdefault` 也让显式传 `now=` 的调用方照旧说了算。
        kwargs.setdefault("now", lambda: FIXED_NOW)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(AttendanceStore, "__init__", _pinned_init)

    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    _run(SchedulingStore(db, now=lambda: FIXED_NOW).prepare())

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(attendance_module.router)
    with TestClient(app) as client:
        assert client.post("/api/auth/init", json=ADMIN).status_code == 200
        login = client.post(
            "/api/auth/login",
            json={"username": ADMIN["username"], "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        yield client, db, accounts
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _employee_id(accounts, phone=PHONE, name=NAME, **profile):
    employee = _run(accounts.register(phone, PASSWORD, name))
    _run(accounts.approve(employee["id"]))
    if profile:
        _run(accounts.update_fields(employee["id"], **profile))
    return employee["id"]


def _scheduler(db):
    return SchedulingStore(db, now=lambda: FIXED_NOW)


def _shift_ids(db):
    return {shift["name"]: shift["id"] for shift in _run(_scheduler(db).list_shifts())}


def _ledger(db):
    return OvertimeLedger(db, now=lambda: FIXED_NOW)


def _admin_cookie(client):
    """先把管理端会话记下来：换 cookie 的那几条用例会清掉整个 jar。"""
    return client.cookies.get(settings.SESSION_COOKIE_NAME)


def _as_admin(client, session_id):
    client.cookies.clear()
    client.cookies.set(settings.SESSION_COOKIE_NAME, session_id)


def _sheet(client, admin, params=None):
    """以管理端会话读一次当月考勤表（这一页只有这一扇门）。"""
    _as_admin(client, admin)
    resp = client.get("/api/attendance/sheet", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _card(sheet, employee_id):
    found = [row for row in sheet["employees"] if row["id"] == employee_id]
    assert found, f"名单里没有 {employee_id}：{[row['id'] for row in sheet['employees']]}"
    return found[0]


def _cells(sheet, employee_id):
    """这个人整月的逐日状态：``{营业日: 'work' | 'rest' | 'todo' | None}``。"""
    return {day["business_date"]: day["state"] for day in _card(sheet, employee_id)["days"]}


# ── 票 01 验收 2/8：当月、且只有管理端那扇门 ────────────────────────────────


def test_the_sheet_is_the_current_month_behind_the_admin_door(attendance_http):
    client, _db, accounts = attendance_http
    admin = _admin_cookie(client)
    _employee_id(accounts)
    staff_session = _run(accounts.login(PHONE, PASSWORD))["session_id"]

    # 匿名打不开这一页；**员工会话也不是它的钥匙**（验收 8）—— 这一页有全店人的
    # 底薪，员工那一档连页面都进不去（页面清单里 `audience: admin`）。
    client.cookies.clear()
    assert client.get("/api/attendance/sheet").status_code == 401
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, staff_session)
    denied = client.get("/api/attendance/sheet")
    assert denied.status_code == 401
    assert denied.json()["detail"] == "需要登录"

    _as_admin(client, admin)
    resp = client.get("/api/attendance/sheet")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # 打开就是当月（验收 2）：没有月份参数，给了也不认 —— 选不到历史月份。
    assert (body["month"], body["today"]) == (MONTH, TODAY)
    assert client.get("/api/attendance/sheet", params={"month": "2026-08"}).json()["month"] == MONTH

    assert [(day["day"], day["business_date"]) for day in body["days"]][:2] == [
        (1, "2026-09-01"),
        (2, "2026-09-02"),
    ]
    assert len(body["days"]) == 30  # 9 月 30 天
    assert [day["business_date"] for day in body["days"] if day["is_today"]] == [TODAY]
    # 今天之后的日期标着「还没到」（验收 4）：前端据此留空，而不是画成「待补」。
    assert [day["business_date"] for day in body["days"] if day["is_future"]] == [
        f"2026-09-{day:02d}" for day in range(25, 31)
    ]


# ── 票 01 验收 3/4：逐日三态，今天之后留空 ──────────────────────────────────


def test_every_day_is_work_rest_or_todo_and_the_future_stays_blank(attendance_http):
    """排了班 = 上班、有行但班次为空 = 休、没有排班行 = 待补；今天之后留空。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    shifts = _shift_ids(db)
    store = _scheduler(db)
    # 今天起按轮转规则铺（`set_rule` 只往今天以后铺）。
    _run(store.set_rule(employee_id, [shifts["白班"]]))
    # 过去的日子在可补记窗口里补两天：一天换成夜班、一天改成休。
    _run(store.set_override(employee_id, "2026-09-21", shift_id=shifts["夜班"]))
    _run(store.set_override(employee_id, "2026-09-22", is_rest=True))

    cells = _cells(_sheet(client, admin), employee_id)

    assert len(cells) == 30  # 整月每天一格（9 月 30 天）
    assert cells["2026-09-01"] == "todo"  # 规则没铺到的过去：待补，不是休
    assert cells["2026-09-20"] == "todo"
    assert cells["2026-09-21"] == "work"  # 补记的夜班
    assert cells["2026-09-22"] == "rest"  # 有行、班次为空
    assert cells["2026-09-23"] == "todo"
    assert cells["2026-09-24"] == "work"  # 今天（规则铺的）
    # 今天之后留空 —— 不是「待补」（那些日子还没发生，不该被当成要人工确认）。
    assert cells["2026-09-25"] is None
    assert cells["2026-09-30"] is None


# ── 票 01 验收 5：名单 = 在职 + 当月有记录的人（含当月离职） ──────────────────


def test_the_roster_is_the_staff_plus_anyone_with_this_months_records(attendance_http):
    """在职的全在；当月有排班或加班记录的人也在（哪怕当月离职）；历史离职的不在。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    shifts = _shift_ids(db)
    scheduler = _scheduler(db)

    on_duty = _employee_id(accounts, "13800138001", "在职的")
    # 当月还有班、但人已经离职（停用）：漏掉他就漏结他那几天。
    shift_leaver = _employee_id(accounts, "13800138002", "当月离职还有班")
    # 当月只剩一笔**还没批**的加班登记，人也已经离职：「有记录」不等于「已批准」。
    overtime_leaver = _employee_id(accounts, "13800138003", "当月离职只留加班")
    # 早就离职、当月一条记录也没有的人：不进表。
    gone_long_ago = _employee_id(accounts, "13800138004", "早就离职的")
    # 还没批下来的人不是「在职」。
    not_approved = _run(accounts.register("13800138005", PASSWORD, "还没批的"))["id"]

    _run(scheduler.set_override(shift_leaver, "2026-09-23", shift_id=shifts["白班"]))
    _run(
        _ledger(db).submit(
            EntryActor(employee_id=overtime_leaver, can_backfill=True),
            "2026-09-18",
            4,
            "离职前那一晚",
        )
    )
    _run(accounts.disable(shift_leaver))
    _run(accounts.disable(overtime_leaver))
    _run(accounts.disable(gone_long_ago))

    listed = [row["id"] for row in _sheet(client, admin)["employees"]]

    assert sorted(listed) == sorted([on_duty, shift_leaver, overtime_leaver])
    assert not_approved not in listed
    assert gone_long_ago not in listed


# ── 票 01 验收 6：卡片上那四项跟着花名册 ─────────────────────────────────────


def test_the_card_columns_follow_the_roster(attendance_http):
    """职位 / 底薪 / 年限 / 是否新入职；入职日期为空的人「是否新入职」留空。"""
    client, _db, accounts = attendance_http
    admin = _admin_cookie(client)

    settled = _employee_id(
        accounts,
        "13800138001",
        "老员工",
        job_title="炒锅",
        base_salary=6800,
        seniority_bonus=300,
        hire_date="2024-05-01",
    )
    joined = _employee_id(
        accounts, "13800138002", "新来的", job_title="打荷", base_salary=4500, hire_date="2026-09-03"
    )
    blank = _employee_id(accounts, "13800138003", "没录入职日期")

    sheet = _sheet(client, admin)

    assert _card(sheet, settled)["job_title"] == "炒锅"
    assert _card(sheet, settled)["base_salary"] == 6800
    assert _card(sheet, settled)["seniority_bonus"] == 300
    assert _card(sheet, settled)["new_hire"] is False
    assert _card(sheet, joined)["new_hire"] is True
    # 入职日期为空 = 这一项算不出来 —— 留空，不是「否」。
    assert _card(sheet, blank)["new_hire"] is None


# ── 票 01 验收 7：当月净时长只算已批准的那几笔 ──────────────────────────────


def test_net_hours_is_approved_overtime_minus_makeup_in_the_entry_month(attendance_http):
    """已批准的加班与补钟正负相抵；待批 / 已驳 / 已撤 / 已作废一分都不算。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(
        accounts, base_salary=5100, hire_date="2024-03-01"
    )
    ledger = _ledger(db)
    mine = EntryActor(employee_id=employee_id, can_backfill=True)
    super_actor = EntryActor()

    overtime = _run(ledger.submit(mine, "2026-09-10", 7, "中秋加班"))
    makeup = _run(ledger.submit(mine, "2026-09-11", -3, "那天迟来两小时"))
    _run(ledger.approve(super_actor, overtime["id"]))
    _run(ledger.approve(super_actor, makeup["id"]))
    # 不计数的那四种状态，一样来一笔。
    _run(ledger.submit(mine, "2026-09-12", 4, "还没人批"))
    rejected = _run(ledger.submit(mine, "2026-09-13", 4, "会被驳"))["id"]
    _run(ledger.reject(super_actor, rejected, "不算"))
    withdrawn = _run(ledger.submit(mine, "2026-09-14", 4, "自己撤"))["id"]
    _run(ledger.cancel(employee_id, withdrawn))
    voided = _run(ledger.submit(mine, "2026-09-15", 5, "批了又作废"))["id"]
    _run(ledger.approve(super_actor, voided))
    _run(ledger.void(super_actor, voided))
    # 上个月那一笔已经批了：归月按登记日期，不进九月。
    last_month = _run(ledger.submit(mine, "2026-08-31", 16, "八月的班"))
    _run(ledger.approve(super_actor, last_month["id"]))

    card = _card(_sheet(client, admin), employee_id)

    assert card["net_hours"] == 2.0  # (7 − 3) / 2


# ── 迁移还没应用：503 说清该跑哪个脚本，不是一个 500 ────────────────────────


def test_missing_tables_are_a_503_that_names_the_scripts(attendance_http):
    """0005 / 0021 还没应用时，页面拿到的是「去数据库迁移面板」，不是 500。

    考勤表要读排班结果与加班登记两张表，两张各自点得到名 —— 只说「读不出来」，
    店长只能来问人。
    """
    client, db, _accounts = attendance_http
    admin = _admin_cookie(client)
    _as_admin(client, admin)

    async def _rename(source, target):
        await db._conn.execute(f"ALTER TABLE {source} RENAME TO {target}")
        await db._conn.commit()

    _run(_rename("staff_assignments", "staff_assignments_tmp"))
    try:
        resp = client.get("/api/attendance/sheet")
        assert resp.status_code == 503, resp.text
        assert "0005_scheduling.sql" in resp.json()["detail"]
    finally:
        _run(_rename("staff_assignments_tmp", "staff_assignments"))

    _run(_rename("overtime_entries", "overtime_entries_tmp"))
    try:
        resp = client.get("/api/attendance/sheet")
        assert resp.status_code == 503, resp.text
        assert "0021_overtime_entries.sql" in resp.json()["detail"]
    finally:
        _run(_rename("overtime_entries_tmp", "overtime_entries"))
