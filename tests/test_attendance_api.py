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
import contextlib
import re
import unittest
from datetime import date, datetime
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl.utils import get_column_letter

import api.attendance as attendance_module
import api.auth as auth_module
import api.scheduling as scheduling_module
import main
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.attendance import AttendanceStore
from services.hygiene.accounts import EmployeeAccounts
from services.overtime.ledger import EntryActor, OvertimeLedger
from services.scheduling.store import SchedulingStore
from tests.hygiene_profile import id_card

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
    """一条真实请求链路，考勤服务的钟钉在 `FIXED_NOW` 上。

    票 06 起这条缝还挂着**排班那扇门**（`include_router(scheduling_module.router)`），
    并把员工账号挂到 `main.employee_accounts` 上：逐日改格复用排班的单日覆盖写入口，
    验收「改完那个员工手机上立刻变」要能真的以员工 cookie 打一次排班的 `/me`。
    **排班的钟跟着一起钉**：那一支的「今天」也必须是 `FIXED_NOW` —— 路由里现造的
    `SchedulingStore(db)` 用的是真钟，不钉的话员工手机上看到的是现实里的今天，
    跟这份文件里写死的 2026-09 对不上。票 01 时刻意没提前挂这两样，保持这条缝
    只属于考勤面。
    """
    real_init = AttendanceStore.__init__

    def _pinned_init(self, *args, **kwargs):
        # 不抄真实签名：将来 `__init__` 多一个参数，抄下来的那份会以 `TypeError`
        # 打死这一整个文件；`setdefault` 也让显式传 `now=` 的调用方照旧说了算。
        kwargs.setdefault("now", lambda: FIXED_NOW)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(AttendanceStore, "__init__", _pinned_init)

    real_scheduling_init = SchedulingStore.__init__

    def _pinned_scheduling_init(self, *args, **kwargs):
        kwargs.setdefault("now", lambda: FIXED_NOW)
        return real_scheduling_init(self, *args, **kwargs)

    monkeypatch.setattr(SchedulingStore, "__init__", _pinned_scheduling_init)

    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    _run(SchedulingStore(db, now=lambda: FIXED_NOW).prepare())
    # 员工会话的查找口在 `main.employee_accounts`（同 `tests/test_scheduling_api.py`
    # 的 `_wire_staff_accounts`）—— 不改它，「员工手机上的班」这一条根本打不通。
    monkeypatch.setattr(main, "employee_accounts", accounts)

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(attendance_module.router)
    app.include_router(scheduling_module.router)
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


def _shift_ids_of(person):
    """卡片上那个人的班次选项：``{班次名: 班次号}``（票 06 的逐日改格要能挑另一个班）。"""
    return {shift["name"]: shift["id"] for shift in person["shifts"]}


def _edit(client, admin, employee_id, business_date, body):
    """以管理端会话改某人某一天（票 06 的那扇门）。"""
    _as_admin(client, admin)
    return client.put(f"/api/attendance/sheet/{employee_id}/{business_date}", json=body)


def _seal(client, admin, body=None):
    """以管理端会话封一次账：不给月份 = 封**上一个自然月**（页面上那颗按钮打的就是这一条）。"""
    _as_admin(client, admin)
    return client.post("/api/attendance/seal", json={} if body is None else body)


def _staff_days(client, staff_session):
    """以**员工自己的** cookie 读他手机上的班（票 06 验收 5）。"""
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, staff_session)
    resp = client.get("/api/scheduling/me")
    assert resp.status_code == 200, resp.text
    return {day["business_date"]: day for day in resp.json()["days"]}


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


# ── 票 06 验收 1/2/4/5：点某一天能改成休、或换另一个启用中的班次 ─────────────
#
# 写路径**复用排班的单日覆盖**（`SchedulingStore.set_override`，结果行来源 `override`）：
# 不新建第二套写排班的方式，也不写轮转规则。验收 2 里「轮转规则与下个月不变」落在
# 服务层那条既有测试上（`tests/test_scheduling.py`，那里能直接读结果行），这条缝只答
# 「谁能打、打进去看到什么」。


def test_a_day_can_be_switched_to_another_shift_and_only_that_day_moves(
    attendance_http, monkeypatch
):
    """验收 1/2/4/5：换成另一个启用中的班次 —— 卡片立刻变、别的日子不变、只播一条。

    **今天之后的日子不能靠卡片验**：`state` 是空的（那是「还没到」，票 01 验收 4 钉过），
    所以这里挑的是**今天**与可补记窗口里的**昨天** —— 两处都真看得见变化。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    _run(_scheduler(db).set_rule(employee_id, [_shift_ids(db)["白班"]]))

    card = _card(_sheet(client, admin), employee_id)
    shifts = _shift_ids_of(card)
    assert set(shifts) >= {"白班", "夜班"}  # 能挑的班次由服务端下发（启用的那些）
    assert len(shifts) == len(_run(_scheduler(db).list_shifts()))  # 停用的不在里面

    seen = []

    async def _record(topic, scope=None):
        seen.append((topic, scope))

    monkeypatch.setattr(scheduling_module.realtime_hub, "broadcast_nudge", _record)

    # 1) 今天是规则铺的白班 → 换成夜班。今天那一格必须立刻变（这是店长看的那一眼）。
    resp = _edit(client, admin, employee_id, TODAY, {"shift_id": shifts["夜班"]})

    assert resp.status_code == 200, resp.text
    assert (resp.json()["shift_id"], resp.json()["is_rest"]) == (shifts["夜班"], False)
    # 验收 4：一人一条、scope 带他自己的 id（不写成全店一条）。
    assert seen == [("scheduling", {"reason": "override_set", "employee_id": employee_id})]

    after = _sheet(client, admin)
    mine = {day["business_date"]: day for day in _card(after, employee_id)["days"]}
    assert (mine[TODAY]["state"], mine[TODAY]["shift_id"]) == ("work", shifts["夜班"])
    # 同一月别的日子没被顺手改动：前一天还是「待补」（规则没铺到那里 = 没有行）。
    assert (mine["2026-09-23"]["state"], mine["2026-09-23"]["shift_id"]) == ("todo", None)
    # 今天之后照旧留空，也没被这次改动画上（票 01 验收 4 的口径没被破坏）。
    assert mine["2026-09-25"]["state"] is None
    assert mine["2026-09-30"]["state"] is None
    assert _shift_ids_of(_card(after, employee_id)) == shifts  # 班次选项没被写动作改掉

    # 2) 昨天（可补记窗口内的过去）本来是「待补」→ 补成夜班：
    #    **有行** 之后那一格就不再是「待补」，而别的天、别的格子都还照旧。
    past = _edit(client, admin, employee_id, "2026-09-23", {"shift_id": shifts["夜班"]})

    assert past.status_code == 200, past.text
    assert seen == [
        ("scheduling", {"reason": "override_set", "employee_id": employee_id}),
        ("scheduling", {"reason": "override_set", "employee_id": employee_id}),
    ]
    mine = {
        day["business_date"]: day
        for day in _card(_sheet(client, admin), employee_id)["days"]
    }
    assert (mine["2026-09-23"]["state"], mine["2026-09-23"]["shift_id"]) == (
        "work",
        shifts["夜班"],
    )
    assert mine["2026-09-22"]["state"] == "todo"  # 再前一天没被连带写上
    assert (mine[TODAY]["state"], mine[TODAY]["shift_id"]) == ("work", shifts["夜班"])

    # 验收 5：那个员工自己的 cookie 打排班的口，**今天**就是新班次（nudge 只是叫他重拉，
    # 数据得真的变了）—— 这是「他手机上立刻变」在服务端能验到的那一半。
    staff_session = _run(accounts.login(PHONE, PASSWORD))["session_id"]
    days = _staff_days(client, staff_session)
    assert (days[TODAY]["shift_name"], days[TODAY]["scheduled"]) == ("夜班", True)


def test_a_day_can_be_turned_into_rest_without_moving_anything_else(attendance_http, monkeypatch):
    """验收 1：改成休 —— 那天没有班次（行还在）、别的日子不变、照样播一条。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    _run(_scheduler(db).set_rule(employee_id, [_shift_ids(db)["白班"]]))

    seen = []

    async def _record(topic, scope=None):
        seen.append((topic, scope))

    monkeypatch.setattr(scheduling_module.realtime_hub, "broadcast_nudge", _record)

    # 昨天本来是「待补」（没有行）→ 改成休：**行被写进去了**，于是它从「待补」变成「休」
    # ——「那天休」与「没铺到」在表上必须是两件事（票 01 验收 3 的口径）。
    resp = _edit(client, admin, employee_id, "2026-09-23", {"is_rest": True})

    assert resp.status_code == 200, resp.text
    assert (resp.json()["shift_id"], resp.json()["is_rest"]) == (None, True)
    assert seen == [("scheduling", {"reason": "override_set", "employee_id": employee_id})]
    cells = _cells(_sheet(client, admin), employee_id)
    assert (cells["2026-09-23"], cells[TODAY], cells["2026-09-22"]) == (
        "rest",  # 有行、班次为空 = 休（不是「待补」）
        "work",
        "todo",  # 前一天照旧「待补」：改一天不顺手铺别人的格子
    )


def test_a_day_that_still_has_an_undecided_request_is_refused_and_nothing_is_written(
    attendance_http, monkeypatch
):
    """验收 3：那天有未决的换班 / 请假请求 → 拒绝、说人话、**一个字都没写进去**。

    换班与请假两种都试。判据都用**这一天现在挂着哪个班**（`shift_id`）：只断言
    「还是 work」看不出「改成休」有没有落库，得看班次有没有被换掉。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    peer_id = _employee_id(accounts, "13800138001", "李四")
    shifts = _shift_ids(db)
    store = _scheduler(db)
    _run(store.set_rule(employee_id, [shifts["白班"]]))
    _run(store.set_rule(peer_id, [shifts["白班"]]))
    # 今天先点成夜班（覆盖）：拒绝之后要能看出它**没被改成休**，也不会被换成白班。
    # **这条覆盖必须落在两次 `set_rule` 之后**：`set_rule` 会删掉今天以后的规则行、
    # 重铺一遍（哪怕是同一个人的同一条规则），先写的覆盖会被那一步冲掉。
    _run(store.set_override(employee_id, TODAY, shift_id=shifts["夜班"]))

    seen = []

    async def _record(topic, scope=None):
        seen.append((topic, scope))

    monkeypatch.setattr(scheduling_module.realtime_hub, "broadcast_nudge", _record)
    # 1) 员工自己提了今天的请假，还没人批 → 店长不能在这一页把它冲掉。
    leave = _run(store.submit_leave(employee_id, TODAY, note="家里有事"))
    refused = _edit(client, admin, employee_id, TODAY, {"is_rest": True})

    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    # 提示得能让店长看懂「为什么改不了」——不是一句「排班参数不合法」。
    assert "请假" in detail
    assert str(leave["id"]) not in detail  # 不把内部编号端给店长
    assert seen == []  # 被拒的请求不广播
    mine = {day["business_date"]: day for day in _card(_sheet(client, admin), employee_id)["days"]}
    assert (mine[TODAY]["state"], mine[TODAY]["shift_id"]) == ("work", shifts["夜班"])

    # 2) 换班：申请人是我、对方还没点头（`pending_peer`）—— 也算未决。
    #    先把上面那条请假收尾，免得它把这一步也一起挡住（那会把这条用例验成一句空话）。
    _run(store.cancel_request(employee_id, leave["id"]))
    swap = _run(store.submit_swap(employee_id, peer_id, TODAY, note="想调个班"))
    refused_swap = _edit(client, admin, employee_id, TODAY, {"shift_id": shifts["白班"]})

    assert refused_swap.status_code == 409, refused_swap.text
    assert "换班" in refused_swap.json()["detail"]
    assert seen == []
    mine = {day["business_date"]: day for day in _card(_sheet(client, admin), employee_id)["days"]}
    assert mine[TODAY]["shift_id"] == shifts["夜班"]  # 还是刚才那个夜班

    # 3) 把那条换班撤回、今天也没有别的申请了：照旧改得动 —— 另外两天都没被顺手写上。
    _run(store.cancel_request(employee_id, swap["id"]))
    assert _edit(client, admin, employee_id, "2026-09-23", {"is_rest": True}).status_code == 200
    cells = _cells(_sheet(client, admin), employee_id)
    assert cells["2026-09-23"] == "rest"
    assert cells[TODAY] == "work"
    assert cells["2026-09-22"] == "todo"


def test_the_day_editor_is_still_the_admin_door(attendance_http):
    """验收 6：匿名与员工会话都被拒 —— 这一页有全店人的底薪，员工那一档进不来。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    _run(_scheduler(db).set_rule(employee_id, [_shift_ids(db)["白班"]]))
    shifts = _shift_ids(db)
    staff_session = _run(accounts.login(PHONE, PASSWORD))["session_id"]

    client.cookies.clear()
    anonymous = client.put(
        f"/api/attendance/sheet/{employee_id}/{TODAY}", json={"is_rest": True}
    )
    assert anonymous.status_code == 401
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, staff_session)
    denied = client.put(f"/api/attendance/sheet/{employee_id}/{TODAY}", json={"is_rest": True})
    assert denied.status_code == 401
    assert denied.json()["detail"] == "需要登录"

    # 两次都被拒 = 今天还是规则那条白班：凭据不对就没写进去。
    mine = {day["business_date"]: day for day in _card(_sheet(client, admin), employee_id)["days"]}
    assert (mine[TODAY]["state"], mine[TODAY]["shift_id"]) == ("work", shifts["白班"])


def test_stopping_a_shift_takes_it_out_of_the_day_editors_choices(attendance_http):
    """验收 1 的另一半：能挑的只有**启用中**的班次（停用的挑不动，挑了就 400）。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    store = _scheduler(db)
    _run(store.set_rule(employee_id, [_shift_ids(db)["白班"]]))
    stopped = _shift_ids(db)["夜班"]
    _run(store.update_shift(stopped, is_active=False))

    card = _card(_sheet(client, admin), employee_id)
    assert "夜班" not in _shift_ids_of(card)
    refused = _edit(client, admin, employee_id, TODAY, {"shift_id": stopped})

    assert refused.status_code == 400, refused.text
    assert "班次" in refused.json()["detail"]
    # 拒绝之后今天还是白班：停用的班次没被写进去。
    mine = {day["business_date"]: day for day in _card(_sheet(client, admin), employee_id)["days"]}
    assert mine[TODAY]["shift_id"] == _shift_ids(db)["白班"]
# ══ 票 03：假期台账与存假（下面这一整段都是票 03 加的） ═════════════════════════
#
# 口径见 `GLOSSARY.md` 的「考勤与假期」与 `docs/adr/0113`。判据一律从 HTTP 主缝进：
# 造数走服务层（排班 / 加班 / 花名册）与**库里那两行台账**（期初 / 封账），断言只看
# 接口返回值；`leave` 是票 03 往卡片上加的那一块（`services/attendance/store.py`）。


def _leave(sheet, employee_id):
    """这个人卡片上那一块假期算式：实际出勤 / 例休 / 补休 / 生日假 / 年假 / 上月存假
    与由此得到的池、本月出勤天数、存假。"""
    return _card(sheet, employee_id)["leave"]


def _mark_holidays(db, days):
    """把几天标成法定节假日（走日历的服务层 —— 它是「哪天是法定节假日」的唯一依据）。"""
    from services.holiday_calendar import HolidayCalendar

    calendar = HolidayCalendar(db, now=lambda: FIXED_NOW)
    for day in days:
        _run(calendar.mark(day, "国庆"))


def _put_ledger_row(db, employee_id, month, **values):
    """往月度考勤台账里造一行（**票 03 没有写入口**：期初由人录入、结余由票 04 封账写）。

    这是造数，不是断言通道 —— 断言全部看接口返回值。字段与 `0027_attendance_ledger.sql`
    一一对应；两个时刻列给个默认值（表里是 NOT NULL，票 03 不关心它们）。
    """
    values.setdefault("created_at", "2026-09-01T00:00:00+08:00")
    values.setdefault("updated_at", "2026-09-01T00:00:00+08:00")
    columns = ["tenant_id", "employee_id", "month", *values]
    placeholders = ", ".join(["?"] * len(columns))
    _run(
        db._conn.execute(
            f"INSERT INTO attendance_ledger ({', '.join(columns)})"
            f" VALUES ({placeholders})",
            (1, int(employee_id), month, *values.values()),
        )
    )
    _run(db._conn.commit())


def _ledger_row(db, employee_id, month):
    """直接读台账里那一行（封账的**落库**证据：结余与封账时刻，造数通道之外只读一次）。

    断言的主体仍然是接口返回值；这里只用来回答「库里那行到底是什么」—— 尤其是
    「第二次封账有没有动它」与「别人写下的期初还在不在」这两件从响应上看不见的事。
    """
    async def _read():
        cur = await db._conn.execute(
            """SELECT * FROM attendance_ledger
               WHERE employee_id = ? AND "month" = ?""",
            (int(employee_id), month),
        )
        rows = await cur.fetchall()
        return dict(rows[0]) if rows else None

    return _run(_read())


# ── 票 03 验收 1/2：实际出勤只数已过的日子，月例休按它折算 ──────────────────


def test_actual_days_counts_only_the_days_that_have_already_passed(attendance_http):
    """已过、排了班、又没标休的那几天各算一天；**今天之后排的班一天都不算**。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    shifts = _shift_ids(db)
    scheduler = _scheduler(db)
    # 今天起按轮转规则铺：09-24（今天）到月末都排了白班 —— 其中 25 号之后还没发生。
    _run(scheduler.set_rule(employee_id, [shifts["白班"]]))
    # 过去补记：五天班 + 一天休（休的那天不算出勤）。
    for day in range(1, 6):
        _run(
            scheduler.set_override(
                employee_id, f"2026-09-{day:02d}", shift_id=shifts["白班"]
            )
        )
    _run(scheduler.set_override(employee_id, "2026-09-06", is_rest=True))

    leave = _leave(_sheet(client, admin), employee_id)

    # 补记的 5 天 + 今天 1 天 = 6；09-25..09-30 的班不算（还没到），
    # 09-06 是休（有排班行但没上班）。算成 12 天的话这里会看到 2。
    assert leave["actual_days"] == 6.0
    assert leave["monthly_rest"] == 1


# 月末那一天的钟：12 月 31 天全过完，「出勤 30 天 → 上限 4」那一条才验得了。
LATE_NOW = datetime(2026, 12, 31, 10, 0, tzinfo=CHINA_TZ)
LATE_MONTH = "2026-12"
# 平年 2 月的最后一天：闰日出生的生日假要在这一个月里看出来（`birthday_in_year` 折到 28 日）。
LEAP_FEB_NOW = datetime(2027, 2, 28, 10, 0, tzinfo=CHINA_TZ)
LEAP_FEB_MONTH = "2027-02"


@contextlib.contextmanager
def _attendance_client(tmp_path, monkeypatch, now):
    """同一条缝（薄 FastAPI + `include_router` + `TestClient` + 真库），钟钉在 ``now``。

    **不去动 01 留下的 `attendance_http`**：那条缝 06 也要改（它要再挂排班路由），
    所以票 03 换个月份来看的那几条用例走这条自己的装配，合并时两边不碰同一段代码。
    """
    real_init = AttendanceStore.__init__

    def _pinned_init(self, *args, **kwargs):
        kwargs.setdefault("now", lambda: now)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(AttendanceStore, "__init__", _pinned_init)

    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: now)
    _run(SchedulingStore(db, now=lambda: now).prepare())

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(attendance_module.router)
    try:
        with TestClient(app) as client:
            assert client.post("/api/auth/init", json=ADMIN).status_code == 200
            login = client.post(
                "/api/auth/login",
                json={"username": ADMIN["username"], "password": PASSWORD},
            )
            assert login.status_code == 200, login.text
            yield client, db, accounts
    finally:
        _run(db.close())
        set_runtime(None)
        settings.DATABASE_DIR = old


@pytest.fixture
def attendance_http_late(tmp_path, monkeypatch):
    """钟钉在 2026-12-31 的那条缝（12 月 31 天全过完）。"""
    with _attendance_client(tmp_path, monkeypatch, LATE_NOW) as handle:
        yield handle


@pytest.fixture
def attendance_http_leap_february(tmp_path, monkeypatch):
    """钟钉在 2027-02-28（平年 2 月的最后一天）的那条缝。"""
    with _attendance_client(tmp_path, monkeypatch, LEAP_FEB_NOW) as handle:
        yield handle


def _scheduler_late(db):
    return SchedulingStore(db, now=lambda: LATE_NOW)


# ── 票 03 验收 2：月例休按实际出勤折算，上限 4 ──────────────────────────────


def test_monthly_rest_folds_actual_days_and_caps_at_four(attendance_http_late):
    """出勤 12 → 2、6 → 1、5 → 0、31 → 上限 4（`GLOSSARY.md` 的月例休）。"""
    client, db, accounts = attendance_http_late
    admin = _admin_cookie(client)
    shifts = _shift_ids(db)
    scheduler = _scheduler_late(db)
    days = [f"{LATE_MONTH}-{day:02d}" for day in range(1, 32)]

    counted = {}
    for index, (name, count) in enumerate(
        (("十二天", 12), ("六天", 6), ("五天", 5), ("整月", 31))
    ):
        employee_id = _employee_id(accounts, f"1380013802{index}", name)
        counted[employee_id] = count
        _run(
            scheduler.set_overrides_batch(
                "shift",
                shift_id=shifts["白班"],
                items=[
                    {"employee_id": employee_id, "business_date": day}
                    for day in days[:count]
                ],
            )
        )

    sheet = _sheet(client, admin)

    # 期望值是票面写死的那几个数（不是把公式再算一遍 —— 那样断言只会跟着实现走）。
    expected = {12: 2, 6: 1, 5: 0, 31: 4}
    for employee_id, count in counted.items():
        leave = _leave(sheet, employee_id)
        assert leave["actual_days"] == float(count)
        assert leave["monthly_rest"] == expected[count]


# ── 票 03 验收 3：法定节假日补休 ────────────────────────────────────────────


def test_statutory_holiday_make_up_leave_needs_the_day_marked_and_worked(attendance_http):
    """日历上标了的那天排了班 → +1；那天休不给；没标的班不给（日历是唯一依据）。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    shifts = _shift_ids(db)
    scheduler = _scheduler(db)
    worker = _employee_id(accounts, "13800138021", "节假日上班")
    rester = _employee_id(accounts, "13800138022", "节假日休")
    unmarked_worker = _employee_id(accounts, "13800138023", "上班那天没标")

    _mark_holidays(db, ["2026-09-15"])
    _run(scheduler.set_override(worker, "2026-09-15", shift_id=shifts["白班"]))
    _run(scheduler.set_override(rester, "2026-09-15", is_rest=True))
    # 同一个人：上班但不是节假日的那天（09-17）不给补休。
    _run(scheduler.set_override(worker, "2026-09-17", shift_id=shifts["白班"]))
    _run(scheduler.set_override(unmarked_worker, "2026-09-17", shift_id=shifts["夜班"]))

    sheet = _sheet(client, admin)

    assert _leave(sheet, worker)["holiday_makeup"] == 1
    assert _leave(sheet, rester)["holiday_makeup"] == 0
    assert _leave(sheet, unmarked_worker)["holiday_makeup"] == 0


def test_holiday_make_up_leave_covers_the_whole_month_not_only_past_days(attendance_http):
    """补休按**当月每个**法定节假日算（票面口径），不是只算已过的那些。

    日历与轮转都已经知道那天他上班；「已过」那条限制只属于**实际出勤天数**
    （票 03 验收 1），别把它顺手套到补休上。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    shifts = _shift_ids(db)
    scheduler = _scheduler(db)
    worker = _employee_id(accounts, "13800138024", "月末节假日上班")
    idle = _employee_id(accounts, "13800138025", "月末节假日没排班")

    _mark_holidays(db, ["2026-09-30"])  # 今天（09-24）之后
    _run(scheduler.set_override(worker, "2026-09-30", shift_id=shifts["白班"]))

    sheet = _sheet(client, admin)

    assert _leave(sheet, worker)["holiday_makeup"] == 1
    assert _leave(sheet, idle)["holiday_makeup"] == 0
    # 那天还没到：补休算得到，**出勤天数**一分都不加。
    assert _leave(sheet, worker)["actual_days"] == 0.0


# ── 票 03 验收 4：生日假按身份证号派生的生日月份 ────────────────────────────


def test_birthday_leave_needs_the_id_card_month_to_match(attendance_http):
    """生日月份落在当月 +1；别的月份不给；**没填身份证号的人算不出来**（不是 0）。"""
    client, _db, accounts = attendance_http
    admin = _admin_cookie(client)
    this_month = _employee_id(
        accounts, "13800138031", "九月生日", id_card_no=id_card(31, birth="19900915")
    )
    other_month = _employee_id(
        accounts, "13800138032", "三月生日", id_card_no=id_card(32, birth="19900315")
    )
    no_id_card = _employee_id(accounts, "13800138033", "没填身份证号")

    sheet = _sheet(client, admin)

    assert _leave(sheet, this_month)["birthday_leave"] == 1
    assert _leave(sheet, other_month)["birthday_leave"] == 0
    assert _leave(sheet, no_id_card)["birthday_leave"] == 0


def test_leap_day_birthday_still_lands_in_february(attendance_http_leap_february):
    """2 月 29 日出生的人：平年那一个月按 2 月 28 日算（`birthday_in_year` 的折算）。

    折算改的是**哪一天**，不改变「落在 2 月」这件事 —— 生日假按月份判，所以这一条
    要在 2 月里看：平年 2 月的钟下照样 +1，3 月出生的人不给。
    """
    client, _db, accounts = attendance_http_leap_february
    admin = _admin_cookie(client)
    leap_day = _employee_id(
        accounts, "13800138041", "闰日生", id_card_no=id_card(41, birth="20000229")
    )
    march = _employee_id(
        accounts, "13800138042", "三月初生", id_card_no=id_card(42, birth="19900301")
    )

    sheet = _sheet(client, admin)

    assert _leave(sheet, leap_day)["birthday_leave"] == 1
    assert _leave(sheet, march)["birthday_leave"] == 0


# ── 票 03 验收 5：年假只在工龄奖调整发生的那一个月 ──────────────────────────


def test_annual_leave_only_in_the_month_the_seniority_bonus_lands(attendance_http):
    """「同期调整」= 应为值带来的那次调整就在当月（2022-09-10 入职 → 2026-09）。

    判据（`GLOSSARY.md` 的员工年假 + `profile.seniority_due_month`）：**应为值是哪一次
    调整带来的**就在当月，而且「当前」确实已经与「应为」对上（还没调的、调过头的都不算）。
    管理层 5 天、其他 3 天，**管理层按管理能力开关判、与职位名无关**。
    """
    client, _db, accounts = attendance_http
    admin = _admin_cookie(client)
    # 2022-09-10 入职：满 4 年那次调整落在 2026-09（应为 400）。
    manager = _employee_id(
        accounts,
        "13800138051",
        "有开关的管理层",
        hire_date="2022-09-10",
        seniority_bonus=400,
        admin_caps=["roster"],
    )
    staff = _employee_id(
        accounts, "13800138052", "普通员工", hire_date="2022-09-10", seniority_bonus=400
    )
    # 职位名写成「管理层」但一项管理能力都没开：按开关判 → 3 天，不是 5。
    titled = _employee_id(
        accounts,
        "13800138053",
        "职位叫管理层",
        job_title="管理层",
        hire_date="2022-09-10",
        seniority_bonus=400,
    )
    # 调整月是 8 月（2022-08-10 入职）的那个人：这个月不给。
    other_month = _employee_id(
        accounts, "13800138054", "八月调整月", hire_date="2022-08-10", seniority_bonus=400
    )
    # 到了调整月却还没落账（当前值空）：应为与当前没对上 → 不给。
    not_adjusted = _employee_id(
        accounts, "13800138055", "还没调", hire_date="2022-09-10"
    )

    sheet = _sheet(client, admin)

    assert _leave(sheet, manager)["annual_leave"] == 5
    assert _leave(sheet, staff)["annual_leave"] == 3
    assert _leave(sheet, titled)["annual_leave"] == 3
    assert _leave(sheet, other_month)["annual_leave"] == 0
    assert _leave(sheet, not_adjusted)["annual_leave"] == 0


# ── 票 03 验收 6/7：池 / 本月出勤天数 / 存假（负值与 .5），上月存假从哪来 ──────


def test_leave_pool_fills_the_month_and_the_balance_can_be_negative_or_half(attendance_http):
    """池 ≥ 自然天数 → 出勤天数填满；池 < 自然天数 → 按池；存假 = 池 − 天数（可负、可 .5）。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    shifts = _shift_ids(db)
    scheduler = _scheduler(db)
    days = [f"2026-09-{day:02d}" for day in range(1, 31)]

    short = _employee_id(accounts, "13800138061", "出勤十二天")
    full = _employee_id(accounts, "13800138062", "出勤整月")
    for employee_id, count in ((short, 12), (full, 24)):
        _run(
            scheduler.set_overrides_batch(
                "shift",
                shift_id=shifts["白班"],
                items=[
                    {"employee_id": employee_id, "business_date": day}
                    for day in days[:count]
                ],
            )
        )
    # 上月（2026-08）已封账的结余 —— 这是「上月存假」的正源。
    _put_ledger_row(
        db, short, "2026-08", closing_balance=5.5, sealed_at="2026-09-01T10:00:00+08:00"
    )
    _put_ledger_row(
        db, full, "2026-08", closing_balance=20.0, sealed_at="2026-09-01T10:00:00+08:00"
    )

    sheet = _sheet(client, admin)

    short_leave = _leave(sheet, short)
    assert (short_leave["actual_days"], short_leave["monthly_rest"]) == (12.0, 2)
    assert short_leave["month_days"] == 30
    assert short_leave["previous_balance"] == 5.5
    assert short_leave["pool"] == 19.5  # 12 + 2 + 5.5
    assert short_leave["month_attendance_days"] == 19.5  # 池 < 30：按池
    assert short_leave["balance"] == -10.5  # 欠班：负值 + .5 如实出来

    full_leave = _leave(sheet, full)
    assert (full_leave["actual_days"], full_leave["monthly_rest"]) == (24.0, 4)
    assert full_leave["pool"] == 48.0  # 24 + 4 + 20
    assert full_leave["month_attendance_days"] == 30.0  # 池 ≥ 30：按自然天数
    assert full_leave["balance"] == 18.0


def test_previous_balance_comes_from_the_sealed_month_else_the_manual_opening(attendance_http):
    """上月存假读上一月**已封账**的结余；还没封账时退回本月那一行的**人工期初**。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    sealed = _employee_id(accounts, "13800138071", "上个月封过账")
    manual = _employee_id(accounts, "13800138072", "上线当月人工期初")
    unsealed = _employee_id(accounts, "13800138073", "上个月没封账")
    blank = _employee_id(accounts, "13800138074", "台账里一行都没有")

    # 上一月已封账：以封账的结余为准 —— 本月那一行的人工期初（99）不该赢过它。
    _put_ledger_row(
        db, sealed, "2026-08", closing_balance=3.5, sealed_at="2026-09-01T10:00:00+08:00"
    )
    _put_ledger_row(db, sealed, "2026-09", opening_balance=99.0)
    # 上线当月（2026-10 之前的 2026-09）：没有可读的上月结余，只有人工期初。
    _put_ledger_row(db, manual, "2026-09", opening_balance=4.5)
    # 上一月有一行、但**没封账**：那行结余不作数，退回本月的人工期初。
    _put_ledger_row(db, unsealed, "2026-08", closing_balance=7.0)
    _put_ledger_row(db, unsealed, "2026-09", opening_balance=1.5)

    sheet = _sheet(client, admin)

    assert _leave(sheet, sealed)["previous_balance"] == 3.5
    assert _leave(sheet, manual)["previous_balance"] == 4.5
    assert _leave(sheet, unsealed)["previous_balance"] == 1.5
    assert _leave(sheet, blank)["previous_balance"] == 0.0


# ── 票 01 留下的缺口（故事 14）：未审批的加班只标色、不计数 ──────────────────


def test_unapproved_overtime_is_flagged_with_its_hours_but_never_counted(attendance_http):
    """待批的加班与补钟给「待批小时数 + 笔数」（页面据此标色），**不进净时长、也不进存假**。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    # 底薪要有：批准加班那一笔会落一张月度计费底薪快照（迁移 0022），没底薪批不了。
    employee_id = _employee_id(accounts, base_salary=5100, hire_date="2024-03-01")
    ledger = _ledger(db)
    mine = EntryActor(employee_id=employee_id, can_backfill=True)
    super_actor = EntryActor()

    approved = _run(ledger.submit(mine, "2026-09-10", 7, "批了的加班"))
    _run(ledger.approve(super_actor, approved["id"]))
    # 等着批的两笔：一笔加班、一笔补钟（负的）。
    _run(ledger.submit(mine, "2026-09-11", 5, "还没批的加班"))
    _run(ledger.submit(mine, "2026-09-12", -3, "还没批的补钟"))
    # 另外三种状态都不是「等着批」：驳了、自己撤了、批了又作废。
    rejected = _run(ledger.submit(mine, "2026-09-13", 6, "会被驳"))["id"]
    _run(ledger.reject(super_actor, rejected, "不算"))
    withdrawn = _run(ledger.submit(mine, "2026-09-14", 6, "自己撤"))["id"]
    _run(ledger.cancel(employee_id, withdrawn))
    voided = _run(ledger.submit(mine, "2026-09-15", 6, "批了又作废"))["id"]
    _run(ledger.approve(super_actor, voided))
    _run(ledger.void(super_actor, voided))

    card = _card(_sheet(client, admin), employee_id)

    assert card["net_hours"] == 3.5  # 只有已批准那 7 小时那笔（7 / 2）
    assert card["pending_hours"] == 1.0  # (5 − 3) / 2：待批的那两笔
    assert card["pending_count"] == 2
    # 加班一分都不进存假：池里只有假期台账那几项（这个人一条排班都没有 → 0）。
    assert card["leave"]["pool"] == 0.0
    assert card["leave"]["balance"] == -30.0


# ── 票 03 验收 9：当月是暂算值（页面据此标出来） ────────────────────────────


def test_the_current_month_is_flagged_as_provisional(attendance_http):
    """当月只算不落库：响应里明说这是**暂算值**（未封账），页面照着标。"""
    client, _db, accounts = attendance_http
    admin = _admin_cookie(client)
    _employee_id(accounts)

    body = _sheet(client, admin)

    assert body["provisional"] is True


# ══ 票 04：封账上月（下面这一整段都是票 04 加的） ═════════════════════════════
#
# 封账 = 把**已经过完的**某个自然月的存假结余写进月度考勤台账并定稿（`GLOSSARY.md`
# 的「封账」、`docs/adr/0113`）。写的是**上一个自然月那一行**的 `closing_balance` +
# `sealed_at`；当月那一位 `provisional` 一个字都不改 —— 当月永远只算不落库。
# 判据全部从 HTTP 主缝进，库里那一行只在「响应上看不见的事」上用一次只读。


def test_sealing_the_last_month_writes_the_balance_the_card_then_reads(attendance_http):
    """验收 1/5：点一次封账，上月结余落库、带上封账时刻，当月卡片上的「上月存假」随即换成它。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    shifts = _shift_ids(db)
    # 上月（2026-08，31 天）在可补记窗口里铺 7 天白班：出勤 7 → 例休 1 → 池 8，结余 8 − 31。
    for day in range(25, 32):
        _run(_scheduler(db).set_override(employee_id, f"2026-08-{day:02d}", shift_id=shifts["白班"]))
    # 上月那一行本来就有**人工期初**（票 03 的入口），本月那一行是人工写的「上月末余额」。
    _put_ledger_row(db, employee_id, "2026-08", opening_balance=2.0)
    _put_ledger_row(db, employee_id, "2026-09", opening_balance=4.5)

    before = _sheet(client, admin)
    # 还没封账：卡片上的「上月存假」来自本月那一行的人工期初，页面也还没有封账时刻。
    assert _leave(before, employee_id)["previous_balance"] == 4.5
    assert before["provisional"] is True
    assert before["previous_month"] == {"month": "2026-08", "sealed_at": None}

    resp = _seal(client, admin)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["month"] == "2026-08"
    assert body["sealed_at"]
    assert body["sealed_employees"] >= 1

    row = _ledger_row(db, employee_id, "2026-08")
    # 封账写的是**结余与封账时刻**：期初 2 参与了这个月的算式，但一个字都没被改掉。
    assert row["opening_balance"] == 2.0
    assert row["closing_balance"] == -21.0
    assert row["sealed_at"] == body["sealed_at"]

    after = _sheet(client, admin)
    assert _leave(after, employee_id)["previous_balance"] == -21.0
    # 封账行看得出是什么时候封的（验收 5）：时刻随当月这张表一起下发。
    assert after["previous_month"] == {"month": "2026-08", "sealed_at": body["sealed_at"]}
    # 当月照旧是暂算值：封的是上月，不是这个月。
    assert after["provisional"] is True


def test_sealing_refuses_a_month_that_has_not_finished(attendance_http):
    """验收 2：当月（与未来）封不了，被拒且中文说得清；一个字都没写下去。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)

    for month in (MONTH, "2026-10"):  # 当月与未来的月都还没过完
        refused = _seal(client, admin, {"month": month})
        assert refused.status_code == 400, refused.text
        assert "过完" in refused.json()["detail"]

    bad = _seal(client, admin, {"month": "2026-8"})
    assert bad.status_code == 400, bad.text
    assert "YYYY-MM" in bad.json()["detail"]

    # 三次都被拒 = 台账里一行都没多出来。
    assert _ledger_row(db, employee_id, MONTH) is None


def test_sealing_a_month_twice_is_refused_and_the_written_row_stands(attendance_http):
    """验收 3：封过的月再封一次被拒，且已写下的那一行一个字都不动。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    shifts = _shift_ids(db)
    _run(_scheduler(db).set_override(employee_id, "2026-08-25", shift_id=shifts["白班"]))

    assert _seal(client, admin).status_code == 200
    stamped = _ledger_row(db, employee_id, "2026-08")
    assert stamped["sealed_at"]
    assert stamped["closing_balance"] == -30.0  # 出勤 1 天：池 1 − 31 天

    again = _seal(client, admin)
    assert again.status_code == 409, again.text
    assert "封过" in again.json()["detail"]

    # 上月的排班也改了（真重算会是另一个数）：封下的那一行仍然不动。
    _run(_scheduler(db).set_override(employee_id, "2026-08-26", shift_id=shifts["白班"]))
    assert _seal(client, admin).status_code == 409

    after = _ledger_row(db, employee_id, "2026-08")
    assert after["sealed_at"] == stamped["sealed_at"]
    assert after["closing_balance"] == stamped["closing_balance"]


def test_a_sealed_balance_does_not_move_when_last_months_schedule_changes(attendance_http):
    """验收 4：封账之后再去改上月的排班，封下的结余不变（当月卡片读的是那一行）。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    shifts = _shift_ids(db)
    for day in range(25, 32):
        _run(_scheduler(db).set_override(employee_id, f"2026-08-{day:02d}", shift_id=shifts["白班"]))

    assert _seal(client, admin).status_code == 200
    sealed = _leave(_sheet(client, admin), employee_id)["previous_balance"]
    assert sealed == -23.0

    # 封完再改上月的排班。**这一处驱动在收尾修复 ③ 之后改过**：原先是从考勤表页那条
    # 逐日写口打的 HTTP，而 ③ 正是要给那条路补上「非当月 / 已封账」——封过的月只读
    # （故事 33）。所以改走**排班服务层**（本文件的既有约定：造数走服务层）—— 店长在
    # 排班月历上补记那条路照旧开着（ADR 0108 的可补记窗口）。这一条要验的
    # 「封下的结余不随后来的排班改动漂」，与从哪扇门改上月无关。
    _run(_scheduler(db).set_override(employee_id, "2026-08-26", is_rest=True))
    _run(
        _scheduler(db).set_override(employee_id, "2026-08-27", shift_id=shifts["夜班"])
    )

    assert _leave(_sheet(client, admin), employee_id)["previous_balance"] == sealed


def test_sealing_is_the_admin_door(attendance_http):
    """验收 6：匿名与员工会话都被拒 —— 封账是管理端那一档的事。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    staff_session = _run(accounts.login(PHONE, PASSWORD))["session_id"]

    client.cookies.clear()
    anonymous = client.post("/api/attendance/seal", json={})
    assert anonymous.status_code == 401
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, staff_session)
    denied = client.post("/api/attendance/seal", json={})
    assert denied.status_code == 401
    assert denied.json()["detail"] == "需要登录"

    # 两次都被拒 = 台账里没有封账行。
    assert _ledger_row(db, employee_id, "2026-08") is None
    _as_admin(client, admin)


# ── 票 03 验收 8：加成性迁移 / 进表清单 / 管理端只读 / 唯一键 ────────────────


class AttendanceLedgerTableContractTest(unittest.TestCase):
    """票 03 验收 8：新表在表名清单里、Admin 数据浏览器只读、唯一键挡住第二行。

    断言的是「这张表在里面」，不是「清单里只有它」：别的票还会往同一个清单里追加。
    """

    TABLE = "attendance_ledger"

    def test_new_table_is_registered_read_only(self):
        from fastapi import HTTPException

        from api.admin import _admin_catalog, _reject_read_only_table_write
        from db_core.schema import ADMIN_READ_ONLY_TABLES, ATTENDANCE_TABLES

        self.assertIn(self.TABLE, ATTENDANCE_TABLES)
        for table in ATTENDANCE_TABLES:
            self.assertIn(table, ADMIN_READ_ONLY_TABLES)

        catalog = _admin_catalog()
        self.assertIn(self.TABLE, catalog["tables"])
        self.assertEqual(catalog["table_meta"][self.TABLE]["group"], "attendance")
        self.assertTrue(catalog["table_meta"][self.TABLE]["read_only"])
        groups = {group["key"]: group for group in catalog["groups"]}
        self.assertEqual(set(groups["attendance"]["tables"]), set(ATTENDANCE_TABLES))

        # 只读不是「按钮变灰」：通用写入口真的拒。
        with self.assertRaises(HTTPException) as caught:
            _reject_read_only_table_write(self.TABLE)
        self.assertEqual(caught.exception.status_code, 403)

    def test_migration_creates_the_ledger_with_a_unique_employee_month(self):
        """迁移 0027 真的落库了：表在、业务列齐、`(员工, 月份)` 唯一。"""
        from tests.pg_probe import list_tables, scalar, table_columns

        self.assertIn(self.TABLE, list_tables())
        columns = table_columns(self.TABLE)
        self.assertEqual(columns[:2], ["tenant_id", "id"])
        for column in (
            "employee_id",
            "month",
            "opening_balance",
            "closing_balance",
            "sealed_at",
        ):
            self.assertIn(column, columns)

        indexdef = scalar(
            "SELECT indexdef FROM pg_indexes"
            " WHERE schemaname = 'public'"
            " AND indexname = 'idx_attendance_ledger_employee_month'"
        )
        self.assertIsNotNone(indexdef, "0027 的员工 × 月份唯一索引不在库里")
        self.assertIn("UNIQUE", indexdef.upper())
        for column in ("employee_id", "month"):
            self.assertIn(column, indexdef)

    def test_the_unique_key_blocks_a_second_row_for_the_same_employee_month(self):
        """同一员工同一自然月只能有一行 —— 真插两行，第二行必须被唯一键拒。"""
        import asyncpg

        from tests.pg_probe import fetch_all

        insert = (
            "INSERT INTO attendance_ledger"
            " (tenant_id, employee_id, month, created_at, updated_at)"
            " VALUES (1, ?, ?, '2026-10-01T00:00:00+08:00', '2026-10-01T00:00:00+08:00')"
        )
        fetch_all(insert, (990001, "2026-10"))
        with self.assertRaises(asyncpg.UniqueViolationError):
            fetch_all(insert, (990001, "2026-10"))


# ── 缺表（0026 / 0027）照旧是一个点名脚本的 503 ─────────────────────────────


def test_missing_holiday_and_ledger_tables_are_named_in_the_503(attendance_http):
    """日历（0026）与台账（0027）没应用时，页面点得到这两个脚本名 —— 不是一个 500。"""
    client, db, _accounts = attendance_http
    admin = _admin_cookie(client)
    _as_admin(client, admin)

    async def _rename(source, target):
        await db._conn.execute(f"ALTER TABLE {source} RENAME TO {target}")
        await db._conn.commit()

    for table, script in (
        ("statutory_holidays", "0026_statutory_holidays.sql"),
        ("attendance_ledger", "0027_attendance_ledger.sql"),
    ):
        _run(_rename(table, f"{table}_tmp"))
        try:
            resp = client.get("/api/attendance/sheet")
            assert resp.status_code == 503, resp.text
            assert script in resp.json()["detail"]
        finally:
            _run(_rename(f"{table}_tmp", table))


# ══ 票 05：手填列（下面这一整段都是票 05 加的） ═════════════════════════════════
#
# 那四项系统推不出来的值（本月班次 / 是否住宿 / 借调门店 / 是否离职）由超管在卡片上
# 填一次、存在**月度考勤台账**（0027，`GLOSSARY.md` 的「月度考勤台账」）当月那一行上：
#
# * 读：卡片上四个键（`shift_name` / `lodging` / `borrowed_store` / `resigned`）。
#   台账里没填过 `shift_name` 时给**当月众数**（并列取 `staff_shifts.sort_order` 靠前）；
#   当月一条排班都没有的人**留空**，不是报错。
# * 写：`PUT /api/attendance/sheet/{员工}/manual` —— 台账那一行 upsert，
#   **只更新点名的那几列**（票 04 写的 `closing_balance` / `sealed_at` 一个都不碰）。
#
# 「从没填过」与「清空」同义：清空存 NULL，读回来照旧退回上面那条默认。


MANUAL_FIELDS = ("shift_name", "lodging", "borrowed_store", "resigned")


def _manual(card):
    """这个人卡片上那四个手填列。"""
    return {field: card[field] for field in MANUAL_FIELDS}


def _put_manual(client, admin, employee_id, body):
    """以管理端会话填某人的手填列（票 05 的那扇门）。"""
    _as_admin(client, admin)
    return client.put(f"/api/attendance/sheet/{employee_id}/manual", json=body)


# ── 票 05 验收 1：四列能填能改、刷新后还在（读侧先落库一行看它读不读得出来） ──


def test_the_card_reads_the_four_manual_columns_back(attendance_http):
    """台账那一行上填过的值原样回到卡片上；没填过的人是「空」（不是报错）。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    filled = _employee_id(accounts, "13800138081", "填过的")
    blank = _employee_id(accounts, "13800138082", "没填过的")

    _put_ledger_row(
        db,
        filled,
        MONTH,
        shift_name="早班",
        lodging="是",
        borrowed_store="调东莞",
        resigned="是",
    )

    sheet = _sheet(client, admin)

    assert _manual(_card(sheet, filled)) == {
        "shift_name": "早班",
        "lodging": "是",
        "borrowed_store": "调东莞",
        "resigned": "是",
    }
    # 没填过的人四项都是空：页面上显示为空，不是编一个值出来。
    assert _manual(_card(sheet, blank)) == {field: None for field in MANUAL_FIELDS}


# ── 票 05 验收 2：本月班次默认是当月众数；没有排班的人留空（不是报错） ────────


def test_the_month_shift_default_is_the_months_mode_by_display_order(attendance_http):
    """当月出现次数最多的那个班次名；**并列取 `sort_order` 靠前的**；没排班的人留空。

    这条默认值属于**读侧**（票 05 的「本月班次」那一格）：它与票 06 卡片上那个
    「启用中的班次」下拉不是同一件事 —— 那个是能挑什么，这个是**现在是什么**。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    shifts = _shift_ids(db)
    scheduler = _scheduler(db)

    majority = _employee_id(accounts, "13800138091", "白班多")
    tied = _employee_id(accounts, "13800138092", "一样多")
    unscheduled = _employee_id(accounts, "13800138093", "这个月没排班")
    # 多数：白班 3 天、夜班 1 天 → 白班。
    _run(
        scheduler.set_overrides_batch(
            "shift",
            shift_id=shifts["白班"],
            items=[
                {"employee_id": majority, "business_date": day}
                for day in ("2026-09-01", "2026-09-02", "2026-09-03")
            ],
        )
    )
    _run(scheduler.set_override(majority, "2026-09-04", shift_id=shifts["夜班"]))
    # 并列：把**夜班**的显示顺序挪到白班前面（默认表里白班 10、夜班 20）—— 这时
    # 「取 `sort_order` 靠前的」与「取班次号小的」给出两个不同答案（夜班号更大）。
    _run(scheduler.update_shift(shifts["夜班"], sort_order=5))
    _run(scheduler.set_override(tied, "2026-09-01", shift_id=shifts["白班"]))
    _run(scheduler.set_override(tied, "2026-09-02", shift_id=shifts["夜班"]))

    sheet = _sheet(client, admin)

    assert _manual(_card(sheet, majority))["shift_name"] == "白班"
    assert _manual(_card(sheet, tied))["shift_name"] == "夜班"
    # 单独再给一份**没填过时的默认**（页面上放进输入框的提示语）：不能靠 `shift_name`
    # 自己反推「这是默认还是他定的」，否则店长分不出这两件事。
    assert _card(sheet, majority)["default_shift_name"] == "白班"
    assert _card(sheet, tied)["default_shift_name"] == "夜班"
    # 当月一条排班行都没有的人：留空（`None`），不是报错、也不是编一个默认班次。
    assert _manual(_card(sheet, unscheduled))["shift_name"] is None
    assert _card(sheet, unscheduled)["default_shift_name"] is None


# ── 票 05 验收 1/3/5：填与改 —— 一次点名一项、只动自己那一列 ─────────────────
#
# 台账那一行是票 04（封账写 `closing_balance` / `sealed_at`）与票 05（手填那四列）
# **共用**的一行：两边的 upsert 都只更新自己点名的列，谁都不整行覆盖谁。


def _ledger_values(db, employee_id, month, columns):
    """直接读台账那一行上几列的原值（判据用：接口返回之外还要看**没被覆盖**）。"""
    sql = (
        f"SELECT {', '.join(columns)} FROM attendance_ledger"
        ' WHERE employee_id = ? AND "month" = ?'
    )
    cur = _run(db._conn.execute(sql, (int(employee_id), month)))
    row = _run(cur.fetchone())
    assert row is not None, f"台账里没有 {employee_id} / {month} 那一行"
    return dict(row)


def test_each_manual_column_is_written_on_its_own_without_clearing_the_others(attendance_http):
    """一次点名一项：写了谁就是谁，别的那几列原样留着（**不清空、不整行覆盖**）。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138094", "一项一项填")

    # 1) 先填班次与住宿。
    first = _put_manual(
        client, admin, employee_id, {"shift_name": "早班", "lodging": "是"}
    )
    assert first.status_code == 200, first.text
    assert first.json() == {
        "employee_id": employee_id,
        "month": MONTH,
        "shift_name": "早班",
        "lodging": "是",
        "borrowed_store": None,
        "resigned": None,
        # 这一次是**手填**，回执里没有「默认」这一份（它不是台账上的值）。
        "default_shift_name": None,
    }
    # 刚写下的那一行，卡片上立刻是这两个值；没填过的两项还是空。
    assert _manual(_card(_sheet(client, admin), employee_id)) == {
        "shift_name": "早班",
        "lodging": "是",
        "borrowed_store": None,
        "resigned": None,
    }

    # 2) 再只填借调门店：上面那两项**不动**（只更新点名的那一列）。
    second = _put_manual(client, admin, employee_id, {"borrowed_store": "调东莞"})
    assert second.status_code == 200, second.text
    assert second.json() == {
        "employee_id": employee_id,
        "month": MONTH,
        "shift_name": "早班",
        "lodging": "是",
        "borrowed_store": "调东莞",
        "resigned": None,
        "default_shift_name": None,
    }

    # 3) 最后只点离职：前三项一个都不动。
    third = _put_manual(client, admin, employee_id, {"resigned": "是"})
    assert third.status_code == 200, third.text
    assert third.json() == {
        "employee_id": employee_id,
        "month": MONTH,
        "shift_name": "早班",
        "lodging": "是",
        "borrowed_store": "调东莞",
        "resigned": "是",
        "default_shift_name": None,
    }
    # 刷新之后还在（验收 1 的后半句）—— 而且是同一行，不是每写一次多一行。
    assert _manual(_card(_sheet(client, admin), employee_id)) == {
        "shift_name": "早班",
        "lodging": "是",
        "borrowed_store": "调东莞",
        "resigned": "是",
    }
    assert _ledger_values(db, employee_id, MONTH, ["shift_name", "lodging"]) == {
        "shift_name": "早班",
        "lodging": "是",
    }


def test_writing_a_manual_column_never_touches_the_sealed_columns(attendance_http):
    """同一行上封账那两列（票 04 写的）不被手填列冲掉；写之前是空的两列也不被顺手写上。

    台账那一行是**两个写入口共用**的一行：这一次只点名了 `lodging`，`closing_balance`
    与 `sealed_at` 连语句里都不该出现（整行覆盖就是在这里把票 04 的账抹掉的）。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    sealed = _employee_id(accounts, "13800138095", "已经封过账的")
    fresh = _employee_id(accounts, "13800138096", "还没封账的")
    _put_ledger_row(
        db, sealed, MONTH, opening_balance=2.5, closing_balance=8.0, sealed_at="2026-10-01T10:00:00+08:00"
    )

    assert _put_manual(client, admin, sealed, {"lodging": "否"}).status_code == 200
    assert _put_manual(client, admin, fresh, {"borrowed_store": "同和"}).status_code == 200

    # 封账那一行原样：期初 / 结余 / 封账时刻一个都没动，只多了这次点名的那一列。
    assert _ledger_values(
        db,
        sealed,
        MONTH,
        ["opening_balance", "closing_balance", "sealed_at", "lodging"],
    ) == {
        "opening_balance": 2.5,
        "closing_balance": 8.0,
        "sealed_at": "2026-10-01T10:00:00+08:00",
        "lodging": "否",
    }
    # 这一次是**新建**那一行：只该写员工 / 月份 / 点名的列 + 两个时刻，不该顺手把
    # 结余写成 0（「还没算过」与「结余是 0」是两件事）。
    assert _ledger_values(
        db, fresh, MONTH, ["opening_balance", "closing_balance", "sealed_at"]
    ) == {"opening_balance": None, "closing_balance": None, "sealed_at": None}
    # 同一员工同一月仍然只有一行（写三次也是 upsert，不是追加）。
    cur = _run(
        db._conn.execute(
            'SELECT COUNT(*) AS n FROM attendance_ledger WHERE employee_id = ? AND "month" = ?',
            (int(fresh), MONTH),
        )
    )
    assert dict(_run(cur.fetchone()))["n"] == 1


def test_clearing_a_manual_column_stores_null_and_falls_back_to_the_default(attendance_http):
    """清空某一项存 NULL（与从没填过同义）：页面上显示为空，「本月班次」退回当月众数。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138097", "清空看看")
    _run(_scheduler(db).set_override(employee_id, "2026-09-01", shift_id=_shift_ids(db)["夜班"]))

    filled = _put_manual(
        client,
        admin,
        employee_id,
        {
            "shift_name": "早班",
            "lodging": "是",
            "borrowed_store": "调东莞",
            "resigned": "是",
        },
    )
    assert filled.status_code == 200, filled.text
    assert _manual(_card(_sheet(client, admin), employee_id))["shift_name"] == "早班"

    # 四项一起清空（页面上的保存按钮就是这么发的：空着的项发 `null`）。
    cleared = _put_manual(
        client,
        admin,
        employee_id,
        {"shift_name": None, "lodging": None, "borrowed_store": None, "resigned": None},
    )

    assert cleared.status_code == 200, cleared.text
    assert cleared.json() == {
        "employee_id": employee_id,
        "month": MONTH,
        "shift_name": None,  # 存的是 NULL：这一次写进去的没有值
        "lodging": None,
        "borrowed_store": None,
        "resigned": None,
        "default_shift_name": None,
    }
    # 库里真的存的是 NULL（不是空串）：显式 `null` = 清空，与从没填过同义。
    assert _ledger_values(
        db, employee_id, MONTH, ["shift_name", "lodging", "borrowed_store", "resigned"]
    ) == {field: None for field in MANUAL_FIELDS}
    # 读回来：三项留空，「本月班次」退回**当月众数**（这个人这个月只排了夜班）。
    assert _manual(_card(_sheet(client, admin), employee_id)) == {
        "shift_name": "夜班",
        "lodging": None,
        "borrowed_store": None,
        "resigned": None,
    }


def test_the_manual_columns_stay_in_the_ledger_and_never_touch_the_roster(attendance_http):
    """手填的值只进卡片（与票 08 的导出件），**不写回员工档案**（验收 5）。

    档案里压根没有「是否住宿 / 借调门店 / 是否离职」这几个字段 —— 这条用例盯的是
    「没有谁顺手在花名册上开一栏、或者拿档案上的字段冒充这几个值」。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138098", "只进台账")

    assert (
        _put_manual(
            client,
            admin,
            employee_id,
            {"lodging": "是", "borrowed_store": "调东莞", "resigned": "是", "shift_name": "夜班"},
        ).status_code
        == 200
    )

    roster = _run(accounts.list_roster())
    mine = [row for row in roster if int(row["id"]) == employee_id][0]
    for field in MANUAL_FIELDS:
        assert field not in mine, f"档案里不该有 {field}"
    # 档案上那几个值一个都没被这次写入改动（离职不是「停用」：是否离职是人事口的手填，
    # 与档案里的停用状态是两件事）。
    assert mine["disabled"] in (False, 0)
    # 卡片上照旧是填进去的那几个值。
    assert _manual(_card(_sheet(client, admin), employee_id))["borrowed_store"] == "调东莞"
    assert _ledger_values(db, employee_id, MONTH, ["resigned"]) == {"resigned": "是"}


def test_the_manual_columns_door_is_still_the_admin_door(attendance_http):
    """验收 6：匿名与员工会话都被拒 —— 而且**一个字都没写进去**。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138099", "员工打不开这扇门")
    staff_session = _run(accounts.login("13800138099", PASSWORD))["session_id"]
    url = f"/api/attendance/sheet/{employee_id}/manual"

    client.cookies.clear()
    assert client.put(url, json={"lodging": "是"}).status_code == 401
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, staff_session)
    denied = client.put(url, json={"lodging": "是"})
    assert denied.status_code == 401
    assert denied.json()["detail"] == "需要登录"

    # 两次都被拒 = 台账那一行压根没被建出来（凭据不对就没写进去）。
    cur = _run(
        db._conn.execute(
            'SELECT COUNT(*) AS n FROM attendance_ledger WHERE employee_id = ? AND "month" = ?',
            (int(employee_id), MONTH),
        )
    )
    assert dict(_run(cur.fetchone()))["n"] == 0
    # 管理端那扇门照旧打得开。
    assert _put_manual(client, admin, employee_id, {"lodging": "是"}).status_code == 200


def test_a_blank_string_is_clearing_not_a_stored_empty_value(attendance_http):
    """空串与清空同义：存 NULL、读回来照旧退回默认 —— 页面上不许出现「一个空串」这种值。

    页面上那四个输入口是文本输入（清空之后 `value` 就是 `''`，不是 `null`），所以服务端
    必须把空串收敛成「没填」，否则导出的表里会多出一格空字符串。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138101", "清成空串")

    resp = _put_manual(client, admin, employee_id, {"lodging": "", "borrowed_store": "   "})

    assert resp.status_code == 200, resp.text
    assert resp.json()["lodging"] is None
    assert resp.json()["borrowed_store"] is None
    assert _ledger_values(db, employee_id, MONTH, ["lodging", "borrowed_store"]) == {
        "lodging": None,
        "borrowed_store": None,
    }


def test_a_request_that_names_no_column_is_refused_with_a_readable_line(attendance_http):
    """四个字段一个都不给：拒绝（400）并说人话，而不是默默写一行空台账出来。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138102", "什么都没点名")

    refused = _put_manual(client, admin, employee_id, {})

    assert refused.status_code == 400, refused.text
    assert "没有" in refused.json()["detail"]
    cur = _run(
        db._conn.execute(
            'SELECT COUNT(*) AS n FROM attendance_ledger WHERE employee_id = ? AND "month" = ?',
            (int(employee_id), MONTH),
        )
    )
    assert dict(_run(cur.fetchone()))["n"] == 0


def test_a_missing_ledger_table_is_a_503_on_the_write_too(attendance_http):
    """台账表没建时手填列照旧是「去做数据库迁移」（503 点名脚本），不是一个 500。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138103", "台账还没建")

    async def _rename(source, target):
        await db._conn.execute(f"ALTER TABLE {source} RENAME TO {target}")
        await db._conn.commit()

    _run(_rename("attendance_ledger", "attendance_ledger_tmp"))
    try:
        resp = _put_manual(client, admin, employee_id, {"lodging": "是"})
        assert resp.status_code == 503, resp.text
        assert "0027_attendance_ledger.sql" in resp.json()["detail"]
    finally:
        _run(_rename("attendance_ledger_tmp", "attendance_ledger"))


# ── 票 07 验收 6：半天休是排班结果上的**加成性**一列（0028） ─────────────────


class StaffAssignmentHalfRestMigrationTest(unittest.TestCase):
    """验 6：`staff_assignments.half_rest` 是加成性加列（带默认值），既有行不受影响。

    断言的是「这一列在、非空、默认 0、没写它的行读出来是 0」—— 不是「表里只有它」：
    排班那几张表别的票还会继续加列。
    """

    TABLE = "staff_assignments"
    COLUMN = "half_rest"

    def test_the_column_is_there_not_null_and_defaults_to_zero(self):
        from tests.pg_probe import fetch_one, table_columns

        self.assertIn(self.COLUMN, table_columns(self.TABLE))
        nullable, default = fetch_one(
            "SELECT is_nullable, column_default FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = ? AND column_name = ?",
            (self.TABLE, self.COLUMN),
        )
        # 非空 + 默认 0：不加列也能读的所有既有查询，行为一个字都不变。
        self.assertEqual(nullable, "NO")
        self.assertIn("0", str(default))

    def test_a_row_written_without_the_column_reads_as_zero(self):
        """既有写得出来的行（不列这一列）读出来是 0，不是 NULL —— 「默认否」是数据事实。"""
        from tests.pg_probe import fetch_all, fetch_one

        # 这一列在迁移前就存在的行不可能还在测试库里（conftest 每次重建 schema），所以
        # 这里造一行**不写这一列**的，验的就是默认值本身。
        fetch_all(
            "INSERT INTO staff_assignments"
            " (tenant_id, employee_id, business_date, shift_id, source, created_at, updated_at)"
            " VALUES (1, ?, ?, NULL, 'rule',"
            " '2026-10-01T00:00:00+08:00', '2026-10-01T00:00:00+08:00')",
            (990071, "2026-10-01"),
        )
        row = fetch_one(
            "SELECT half_rest FROM staff_assignments"
            " WHERE employee_id = ? AND business_date = ?",
            (990071, "2026-10-01"),
        )
        self.assertEqual(row[0], 0)


# ══ 票 07：半天休（下面这一整段都是票 07 加的） ══════════════════════════════
#
# 见 `GLOSSARY.md` 的「半天休」与 `docs/adr/0114`：半天休是**排班结果那一行上**的一个
# 标志，不是第二个班次 —— 那天仍然只挂本来那个班次、仍然只有一行。只有考勤表页能设它
# （`PUT /api/attendance/sheet/{员工}/{营业日}` 的 body 里那个 `half_rest`），
# 排班月历 / 周表 / 员工端只**读**它并显示「休半天」。


def _half(client, admin, employee_id, business_date, half_rest=True):
    """以管理端会话标 / 取消「休半天」（票 07 的那扇门，与票 06 同一条路径）。"""
    return _edit(client, admin, employee_id, business_date, {"half_rest": half_rest})


def _assignment_rows(employee_id, business_date):
    """库里那一天的那几行（`(班次 id, 半天休标志)`）—— 验「仍然只有一行」。"""
    from tests.pg_probe import fetch_all

    return fetch_all(
        "SELECT shift_id, half_rest FROM staff_assignments"
        " WHERE employee_id = ? AND business_date = ?",
        (int(employee_id), business_date),
    )


def test_a_day_can_be_marked_half_rest_and_unmarked(attendance_http, monkeypatch):
    """验收 1：标成「休半天」也能取消；卡片上那一格立刻是「休半天」，班次没被动过。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    shifts = _shift_ids(db)
    _run(_scheduler(db).set_rule(employee_id, [shifts["白班"]]))

    seen = []

    async def _record(topic, scope=None):
        seen.append((topic, scope))

    monkeypatch.setattr(scheduling_module.realtime_hub, "broadcast_nudge", _record)

    resp = _half(client, admin, employee_id, TODAY)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "employee_id": employee_id,
        "business_date": TODAY,
        "half_rest": True,
    }
    # 一人一条：员工手机上那一屏要跟着重拉（nudge 不带数据）。
    assert seen == [("scheduling", {"reason": "half_rest_set", "employee_id": employee_id})]
    # 那天**仍然只挂一个班次、仍然只有一行**（ADR 0114）：标志是加在结果行上的。
    assert _assignment_rows(employee_id, TODAY) == [(shifts["白班"], 1)]
    mine = {day["business_date"]: day for day in _card(_sheet(client, admin), employee_id)["days"]}
    assert (mine[TODAY]["half_rest"], mine[TODAY]["state"], mine[TODAY]["shift_id"]) == (
        True,
        "work",
        shifts["白班"],
    )

    # 取消：同一天再来一次 `half_rest: false`，那天回到「上班」而不是「休」。
    assert _half(client, admin, employee_id, TODAY, False).status_code == 200
    assert _assignment_rows(employee_id, TODAY) == [(shifts["白班"], 0)]
    mine = {day["business_date"]: day for day in _card(_sheet(client, admin), employee_id)["days"]}
    assert (mine[TODAY]["half_rest"], mine[TODAY]["state"]) == (False, "work")


def test_half_rest_needs_a_shift_on_that_day(attendance_http):
    """那天没有班次（休 / 还没排到）就标不上：「休半天」不是「休」的另一种写法。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    _run(_scheduler(db).set_override(employee_id, "2026-09-22", is_rest=True))

    not_rostered = _half(client, admin, employee_id, "2026-09-21")  # 压根没有排班行
    on_rest = _half(client, admin, employee_id, "2026-09-22")  # 有行、班次为空 = 休

    assert (not_rostered.status_code, on_rest.status_code) == (400, 400)
    assert "班次" in not_rostered.json()["detail"]
    assert _assignment_rows(employee_id, "2026-09-21") == []  # 没顺手写出一行
    assert _assignment_rows(employee_id, "2026-09-22") == [(None, 0)]


def test_half_rest_is_refused_outside_the_current_month(attendance_http):
    """这一页只算当月：上个月的格子标不了（那条路上的门就该是关着的）。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    _run(_scheduler(db).set_rule(employee_id, [_shift_ids(db)["白班"]]))

    resp = _half(client, admin, employee_id, "2026-08-20")

    assert resp.status_code == 400, resp.text
    assert "当月" in resp.json()["detail"]


def test_a_half_rest_day_counts_as_half_a_day_of_attendance(attendance_http):
    """验收 4：标了「休半天」的那天按**实际出勤 0.5 天**算，月例休与存假跟着走。

    三张卡把这个 0.5 钉住（期望值是票面写死的数，不是把公式再算一遍）：

    * 12 整天 + 1 个半天休 = 实际出勤 **12.5 天** → 月例休 2 天（票面原话）；
    * 5 整天 + 1 个半天休 = **5.5 天** → 月例休 0 天。**这一条认的是 0.5 而不是 1**：
      把半天当整天就是 6 天 → 1 天。
    * 对照组：13 整天 → 池比上面那位多 0.5 天，存假就跟着多 0.5 天。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    shifts = _shift_ids(db)
    scheduler = _scheduler(db)
    days = [f"2026-09-{day:02d}" for day in range(1, 14)]

    def paint(employee_id, count):
        _run(
            scheduler.set_overrides_batch(
                "shift",
                shift_id=shifts["白班"],
                items=[
                    {"employee_id": employee_id, "business_date": day}
                    for day in days[:count]
                ],
            )
        )

    twelve_and_a_half = _employee_id(accounts, "13800138081", "十二天半")
    five_and_a_half = _employee_id(accounts, "13800138082", "五天半")
    thirteen = _employee_id(accounts, "13800138083", "十三整天")
    paint(twelve_and_a_half, 13)
    paint(five_and_a_half, 6)
    paint(thirteen, 13)
    # 各自把最后那一天标成半天休（走的是考勤表页那扇门）。
    assert _half(client, admin, twelve_and_a_half, "2026-09-13").status_code == 200
    assert _half(client, admin, five_and_a_half, "2026-09-06").status_code == 200

    sheet = _sheet(client, admin)

    half_card = _leave(sheet, twelve_and_a_half)
    assert half_card["actual_days"] == 12.5
    assert half_card["monthly_rest"] == 2
    assert half_card["pool"] == 14.5  # 12.5 + 2（没有别的假）
    assert half_card["balance"] == -15.5  # 14.5 − 30：那 0.5 天真进了存假

    short_card = _leave(sheet, five_and_a_half)
    assert short_card["actual_days"] == 5.5
    assert short_card["monthly_rest"] == 0  # 当整天算的话这里会是 1

    full_card = _leave(sheet, thirteen)
    assert full_card["actual_days"] == 13.0
    assert full_card["pool"] - half_card["pool"] == 0.5


def test_only_the_attendance_sheet_can_set_half_rest(attendance_http):
    """验收 7：员工会话只能读不能设；排班那扇门（单日覆盖）也设不了它。

    这一条钉的是「写入口只有一处」：员工自己打不了，排班页那条 PUT 的 body 里塞
    `half_rest` 也写不进去（它只认 `is_rest` / `shift_id` / `zone_id`）。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    shifts = _shift_ids(db)
    _run(_scheduler(db).set_rule(employee_id, [shifts["白班"]]))
    staff_session = _run(accounts.login(PHONE, PASSWORD))["session_id"]

    # 1) 员工 / 匿名都打不了这一页的写口。
    client.cookies.clear()
    anonymous = client.put(
        f"/api/attendance/sheet/{employee_id}/{TODAY}", json={"half_rest": True}
    )
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, staff_session)
    denied = client.put(
        f"/api/attendance/sheet/{employee_id}/{TODAY}", json={"half_rest": True}
    )
    assert (anonymous.status_code, denied.status_code) == (401, 401)
    assert denied.json()["detail"] == "需要登录"
    assert _assignment_rows(employee_id, TODAY) == [(shifts["白班"], 0)]

    # 2) 排班页那条单日覆盖：body 里带 `half_rest` 也只会落回它自己那套校验，
    #    标志一个都不写（要改成休得走 `is_rest`，那会**清掉**半天休）。
    _as_admin(client, admin)
    refused = client.put(
        f"/api/scheduling/overrides/{employee_id}/{TODAY}", json={"half_rest": True}
    )
    assert refused.status_code == 400, refused.text
    assert _assignment_rows(employee_id, TODAY) == [(shifts["白班"], 0)]

    # 3) 考勤表页设得上 —— 与上面两条对照，「只有一处能设」才不是空话。
    assert _half(client, admin, employee_id, TODAY).status_code == 200
    assert _assignment_rows(employee_id, TODAY) == [(shifts["白班"], 1)]


def test_half_rest_does_not_touch_the_hygiene_duty(attendance_http):
    """验收 5：卫生的「当日分工」读到的仍是一个班次、一个工作区（半天休不改分工）。"""
    from services.identity.duty import DutyRoster

    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    shifts = _shift_ids(db)
    _run(_scheduler(db).set_rule(employee_id, [shifts["白班"]]))

    before = _run(DutyRoster(db).duty_for(employee_id, TODAY))
    assert _half(client, admin, employee_id, TODAY).status_code == 200
    after = _run(DutyRoster(db).duty_for(employee_id, TODAY))

    # 半天休前后**逐字段一样**：那天仍是一个班次、一个工作区（没配区就是没配）。
    assert after == before
    assert (after["scheduled"], after["shift_id"], after["zone_id"]) == (
        True,
        shifts["白班"],
        None,
    )


def test_sealing_the_last_month_also_folds_half_rest_at_half_a_day(attendance_http):
    """验收 4 的另一半（与票 04 的接线）：**封账那条算式**也按 0.5 天折算。

    「休半天」只能在**当月**设（写口那一道闸），所以这一天是在它还叫「当月」的时候标上
    的 —— 真实流程正是这样：8 月 31 日标上半天休，9 月封 8 月的账。这里用一个「8 月的
    钟」走同一扇门，而不是往库里手写标志；封账仍走 9 月这张表的 HTTP 口。

    期望值：8 月铺 7 天班、其中 1 天标半天休 → 实际出勤 6.5 → 例休 1 → 池 7.5 →
    结余 7.5 − 31 = **−23.5**。当整天算会是 −23（8 − 31），当 0 天算会是 −24
    （6 + 1 − 31）—— 三个数互不相等，所以这一个数认得出「0.5」。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts)
    shifts = _shift_ids(db)
    for day in range(25, 32):
        _run(
            _scheduler(db).set_override(
                employee_id, f"2026-08-{day:02d}", shift_id=shifts["白班"]
            )
        )
    august = datetime(2026, 8, 31, 10, 0, tzinfo=CHINA_TZ)
    _run(AttendanceStore(db, now=lambda: august).set_half_rest(employee_id, "2026-08-31", True))

    resp = _seal(client, admin)

    assert resp.status_code == 200, resp.text
    assert _ledger_row(db, employee_id, "2026-08")["closing_balance"] == -23.5


# ══ 票 08：一键导出 .xlsx（下面这一整段都是票 08 加的） ══════════════════════
#
# 版式规格在 `.scratch/考勤表导出/export-layout.md`（6 份历史样表逐格实测）。这一条缝
# **不给生成器另开一扇门**（spec.md 的 Testing Decisions）：从 HTTP 响应体里把那个工作簿
# **读回来**再断言 —— 请求进去、.xlsx 出来，与页面看到的是同一份投影。
#
# 断言只碰「店长看得见的那几样」：表名 / 表头逐字 / 列数随天数 / 日格取值 / 底部三块 /
# 文件名 / 打印设置。字体名、色值与列宽的具体数字也在样表里实测过，但那些是「版式复刻」
# 的细节，逐位钉死只会让以后微调版式时被迫改测试。

EXPORT_URL = "/api/attendance/sheet.xlsx"
# 样表里底部「注意」那一行的原文（六份一字不差，票面要求照抄）。
NOTE_TEXT = (
    "注意：①早班、夜班人员都填在一张表；②对应人员新入职或离职都要填"
    "写好；③班次时间可根据自个门店修改；④人员对应班次一定要填写；⑤"
    "考勤表中有啥特殊的事项就写在“备注”"
)


def _export(client, session):
    """以管理端会话导出当月考勤表（票 08 那扇门）。"""
    _as_admin(client, session)
    return client.get(EXPORT_URL)


def _workbook(resp):
    """把响应体读回工作簿（**从 HTTP 出来**，不经生成器）。

    表名在这里**不 strip**：`Sheet1 ` 那个尾随空格是票面点名要的一位，剥掉就验不出来了。
    """
    from io import BytesIO

    from openpyxl import load_workbook

    assert resp.status_code == 200, resp.text
    return load_workbook(BytesIO(resp.content))


def _only_sheet(workbook):
    assert workbook.sheetnames == ["Sheet1 "], workbook.sheetnames
    return workbook["Sheet1 "]


def _disposition_filename(resp):
    """取 `Content-Disposition` 里的文件名（前端 `api.download` 读的是同一位）。

    优先 RFC 5987 的 `filename*=UTF-8''…`（中文名要靠它），退回纯 `filename="…"`。
    """
    header = resp.headers["content-disposition"]
    encoded = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", header)
    if encoded:
        return unquote(encoded.group(1))
    plain = re.search(r'filename\s*=\s*"?([^";]+)"?', header)
    assert plain, header
    return plain.group(1)


def _export_employees(accounts, db):
    """造两个导出件要看的人，返回 `{姓名: 员工号}`。

    * 「整月都在」：21 号上班、22 号休、23 号**休半天**（票 07 的标志进导出件的那一格）；
    * 「月底新来的」：这个月一条排班行都没有（每格 = 待补）。

    两件事覆盖日格的全部四种写法：出勤留空 / 休 / 休半天 / 待补。
    """
    shifts = _shift_ids(db)
    scheduler = _scheduler(db)
    on_duty = _employee_id(
        accounts,
        "13800138111",
        "整月都在",
        job_title="炒锅",
        base_salary=6800,
        seniority_bonus=300,
        hire_date="2024-05-01",
    )
    newcomer = _employee_id(accounts, "13800138112", "月底新来的", hire_date="2026-09-03")
    for day in ("2026-09-21", "2026-09-22", "2026-09-23"):
        _run(scheduler.set_override(on_duty, day, shift_id=shifts["白班"]))
    # 22 号改成休（有排班行、班次为空）—— 待补与休在表上是两件事（票 01 的口径）。
    _run(scheduler.set_override(on_duty, "2026-09-22", is_rest=True))
    return {"整月都在": on_duty, "月底新来的": newcomer}


def _mark_half_rest(db, employee_id, business_date):
    """标「休半天」走服务层那扇唯一的写口（票 07）—— 造数，不是断言通道。"""
    _run(
        AttendanceStore(db, now=lambda: FIXED_NOW).set_half_rest(
            employee_id, business_date, True
        )
    )


# ── 票 08 验收 1/2/3/7：表名、表头逐字、列数随天数、汇总列顺序 ──────────────


def test_the_export_is_the_history_template_named_sheet1_with_a_trailing_space(attendance_http):
    """验收 1/2：表名 `Sheet1 `（**带尾随空格**）、R1 大标题、月份行**不写店名**。

    表头逐字与历史模板一致：A~H 那八个字段列 + 日期列 1..N + 星期行 + 三个汇总列。
    日期列的数字与星期汉字按**真实月历**（2026-09-01 是周二）。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    _export_employees(accounts, db)
    month_days = 30  # 2026-09

    worksheet = _only_sheet(_workbook(_export(client, admin)))

    assert worksheet["A1"].value == "厨房排班表"
    # 月份行只说月份：**门店那一段不写**（系统里没有这一项、只有一家店）。
    assert worksheet["A2"].value == "月份 ：9月份"
    assert "猎德" not in str(worksheet["A2"].value)

    headers = [worksheet.cell(row=4, column=column).value for column in range(1, 9)]
    assert headers == ["班次", "姓名", "职位", "底薪", "是否住宿", "本月出勤天数", "加班小时", "年限"]
    # 日期列从 I 列起，R4 放 1..30；30 天的月**没有 31 日那一列**（验收 3）。
    assert [worksheet.cell(row=4, column=9 + index).value for index in range(month_days)] == list(
        range(1, month_days + 1)
    )
    assert worksheet.cell(row=4, column=9 + month_days).value == "存休\n天数"
    assert worksheet.cell(row=4, column=10 + month_days).value == "是否新入职"
    assert worksheet.cell(row=4, column=11 + month_days).value == "是否离职"
    # 汇总三列就停在 AP：没有第 32 列（30 天的月那个 31 日列不存在）。
    assert worksheet.max_column == 8 + month_days + 3

    # 星期汉字按真实月历（2026-09-01 周二），六/日两列正是周末那两格。
    weekdays = [worksheet.cell(row=5, column=9 + index).value for index in range(month_days)]
    assert weekdays == ["二", "三", "四", "五", "六", "日", "一"] * 4 + ["二", "三"]
    weekend_columns = [
        9 + index
        for index in range(month_days)
        if date(2026, 9, index + 1).weekday() >= 5
    ]
    assert len(weekend_columns) == 8  # 九月的四个周末
    for column in weekend_columns:
        assert worksheet.cell(row=5, column=column).fill.fgColor.rgb == "FFFFFF00"
    assert worksheet.cell(row=5, column=9).fill.patternType is None  # 周二那格没有底色

    # A~H 是 R4:R5 纵向合并（两行表头）；三个汇总列也是。
    for column in list(range(1, 9)) + [9 + month_days + offset for offset in (0, 1, 2)]:
        letter = get_column_letter(column)
        assert f"{letter}4:{letter}5" in {
            str(merged) for merged in worksheet.merged_cells.ranges
        }, letter


def test_a_thirty_day_month_has_no_day_31_column(attendance_http):
    """验收 3 的另一半：**列数随当月天数变** —— 与历史表同一件事，不是写死 31 列。

    这一条在 30 天的月里看「没有 31 日那一列」；31 天的月里同一条会看到 31 那一列
    （`test_the_export_grows_a_day_31_column_in_a_thirty_one_day_month`）。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    _export_employees(accounts, db)

    worksheet = _only_sheet(_workbook(_export(client, admin)))

    assert worksheet.cell(row=4, column=8 + 30).value == 30  # 第 30 天是这一列
    assert worksheet.cell(row=4, column=9 + 30).value == "存休\n天数"  # 紧接着就是表末三列
    assert worksheet.cell(row=5, column=8 + 30).value == "三"  # 2026-09-30 是周三
    # 31 那一列根本不存在：第 30 天往右就是汇总列（不是「31 日」）。
    assert 31 not in [
        worksheet.cell(row=4, column=column).value for column in range(9, 9 + 40)
    ]


# ── 票 08 验收 4/5/6：日格取值、汇总列、手填项 ──────────────────────────────


def test_the_day_cells_write_only_rest_half_rest_and_todo(attendance_http):
    """验收 4：出勤**留空**、休写「休」、半天写「休半天」、没有排班行写「待补」并标色。

    汇总列（验收 6/7 的另一半）也在同一行上核一遍：本月出勤天数 / 加班小时 / 年限 /
    存休天数（票 03 的 `balance`，可为负）/ 是否新入职 / 是否离职（**台账里手填的那一项**）。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    ids = _export_employees(accounts, db)
    _mark_half_rest(db, ids["整月都在"], "2026-09-23")
    # 手填「是否离职」（票 05 那一列）——导出件读的就是台账上这个值。
    assert (
        _put_manual(client, admin, ids["整月都在"], {"resigned": "是"}).status_code == 200
    )

    worksheet = _only_sheet(_workbook(_export(client, admin)))
    row = 6  # 第一个人在 R6
    cells = {
        day: worksheet.cell(row=row, column=8 + day).value for day in (1, 21, 22, 23, 24)
    }

    # 1 号没铺到 = 待补；21 号上班 = **留空**（出勤在纸上就是空白）；22 号休；23 号休半天；
    # 24 号（今天）压根没有排班行 = 又是待补 —— 四种写法在同一行上各出现一次。
    assert cells == {1: "待补", 21: None, 22: "休", 23: "休半天", 24: "待补"}
    # 待补那一格**黄底标出**（验收 4 的后半句）：「休」与「上班」不标。
    assert worksheet.cell(row=row, column=8 + 1).fill.fgColor.rgb == "FFFFFF00"
    assert worksheet.cell(row=row, column=8 + 22).fill.patternType is None
    assert worksheet.cell(row=row, column=8 + 21).fill.patternType is None

    # 汇总列：位置随天数平移，顺序是 存休天数 → 是否新入职 → 是否离职（验收 7）。
    # 存休天数 = 票 03 的 `balance`（池 − 本月自然天数）：21 号整天 + 23 号半天 = 池 1.5，
    # 减 30 天 = −28.5（**负值照写**，样表里存休本来就有负数）。
    assert worksheet.cell(row=row, column=9 + 30).value == 1.5 - 30
    assert worksheet.cell(row=row, column=10 + 30).value == "否"  # 2024 年入职，不是新来的
    assert worksheet.cell(row=row, column=11 + 30).value == "是"  # 台账里手填的「是否离职」
    # 其余列跟着花名册与台账：姓名 / 职位 / 底薪 / 年限 / 本月出勤天数 / 加班小时。
    assert worksheet.cell(row=row, column=2).value == "整月都在"
    assert worksheet.cell(row=row, column=3).value == "炒锅"
    assert worksheet.cell(row=row, column=4).value == 6800
    assert worksheet.cell(row=row, column=6).value == 1.5  # 本月出勤天数（半天休按 0.5 折）
    assert worksheet.cell(row=row, column=7).value == 0  # 这个月没有加班（净时长 0）
    assert worksheet.cell(row=row, column=8).value == 300

    # 第二个人：22 / 23 号没有排班行 = 待补；本月新入职 = 「是」；没填离职 = 留空。
    assert worksheet.cell(row=7, column=2).value == "月底新来的"
    assert worksheet.cell(row=7, column=8 + 22).value == "待补"
    assert worksheet.cell(row=7, column=8 + 23).value == "待补"
    assert worksheet.cell(row=7, column=8 + 22).fill.fgColor.rgb == "FFFFFF00"
    assert worksheet.cell(row=7, column=10 + 30).value == "是"
    assert worksheet.cell(row=7, column=11 + 30).value is None


def test_borrowed_store_is_not_written_into_any_day_cell(attendance_http):
    """票面验收 5：**借调门店不写进文件**。

    历史表是把它写在**某一天**的格子上（`城光荟` / `同和` / `调东莞`），而台账是按月记的、
    没有日期可落 —— 父级裁定不造一个日期出来。这一条把那个裁定钉住：卡片上填了借调门店，
    导出的表里**任何一格都不该出现它**（日格与表末三列都不写）。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    ids = _export_employees(accounts, db)
    assert (
        _put_manual(
            client, admin, ids["整月都在"], {"borrowed_store": "调东莞", "resigned": "是"}
        ).status_code
        == 200
    )

    worksheet = _only_sheet(_workbook(_export(client, admin)))
    written = [
        worksheet.cell(row=row, column=column).value
        for row in range(6, worksheet.max_row + 1)
        for column in range(1, worksheet.max_column + 1)
    ]

    assert "调东莞" not in written
    assert "是" in written  # 「是否离职」照旧写（借调门店是**唯一**不进文件的那一项）


# ── 票 08 验收 2/8：底部三块与打印设置 ─────────────────────────────────────


def test_the_footer_blocks_and_the_print_setup_match_the_template(attendance_http):
    """验收 8：底部三块（含「注意：①…⑤」原文）整行合并；横向 / A4 / fitToPage / 页边距 0。

    三块的落位在样表里是**随当月人数浮动**的（注意行在 R30~R33 之间），所以这里按
    「表体之后」相对定位，不写死行号。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    _export_employees(accounts, db)

    worksheet = _only_sheet(_workbook(_export(client, admin)))
    anchors = {}
    for row in range(6, worksheet.max_row + 1):
        value = worksheet.cell(row=row, column=1).value
        if value in ("班次时间：", NOTE_TEXT):
            anchors[value] = row
    assert set(anchors) == {"班次时间：", NOTE_TEXT}, anchors

    shift_row = anchors["班次时间："]
    note_row = anchors[NOTE_TEXT]
    assert note_row == shift_row + 1  # 两块紧挨着
    last_column = 8 + 30 + 3
    merges = {str(merged) for merged in worksheet.merged_cells.ranges}
    last_letter = get_column_letter(last_column)
    assert f"A{shift_row}:{last_letter}{shift_row}" in merges
    assert f"A{note_row}:{last_letter}{note_row + 7}" in merges
    # 尾部还有一块空的合并块（样表 A46:AP53 那一档）。
    assert f"A{note_row + 8}:{last_letter}{note_row + 15}" in merges
    assert worksheet.cell(row=note_row + 8, column=1).value is None

    # 打印设置：横向 / A4(9) / fitToPage / 缩放 24 / 页边距全 0 / 有 Print_Area。
    assert worksheet.page_setup.orientation == "landscape"
    assert worksheet.page_setup.paperSize == 9
    assert worksheet.sheet_properties.pageSetUpPr.fitToPage is True
    assert worksheet.page_setup.scale == 24
    assert worksheet.page_setup.fitToHeight == 0
    assert (
        worksheet.page_margins.left,
        worksheet.page_margins.right,
        worksheet.page_margins.top,
        worksheet.page_margins.bottom,
    ) == (0, 0, 0, 0)
    assert worksheet.print_area  # 定义名 `_xlnm.Print_Area` 在就行，具体行随人数浮动


def test_the_two_kinds_of_conditional_formatting_are_on_the_summary_columns(attendance_http):
    """验收 9：题面那两类条件格式（`F` 列 0~31、末尾两列 containsText「是」）。

    **不作用在日期格上**：历史表里日格的颜色都是手工填的底色。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    _export_employees(accounts, db)

    worksheet = _only_sheet(_workbook(_export(client, admin)))
    by_range = {
        str(rng): rules for rng, rules in worksheet.conditional_formatting._cf_rules.items()
    }

    # 整整三条，而且只落在这三处：日期格上一条都没有。
    assert sorted(by_range) == ["<ConditionalFormatting AN6:AN7>",
                                "<ConditionalFormatting AO6:AO7>",
                                "<ConditionalFormatting F6:F7>"]
    assert [(rule.type, rule.operator, rule.formula) for rule in by_range["<ConditionalFormatting F6:F7>"]] == [
        ("cellIs", "between", ["0", "31"])
    ]
    for letter in ("AN", "AO"):
        key = f"<ConditionalFormatting {letter}6:{letter}7>"
        assert [(rule.type, rule.text, rule.formula) for rule in by_range[key]] == [
            ("containsText", "是", [f'NOT(ISERROR(SEARCH("是",{letter}6)))'])
        ]


# ── 票 08 验收 1：文件名带月份 / 8：鉴权 ───────────────────────────────────


def test_the_download_is_named_after_the_month_the_response_reports(attendance_http):
    """验收 1：文件名 `考勤表-YYYY-MM.xlsx`，月份取**响应上的** `month`（不是服务器时钟另算）。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    _export_employees(accounts, db)

    resp = _export(client, admin)

    assert resp.headers["content-disposition"].startswith("attachment;")
    assert _disposition_filename(resp) == f"考勤表-{MONTH}.xlsx"
    # 表内那个月与文件名同一个（同一份响应算出来的），不是两处各写一遍。
    assert _only_sheet(_workbook(resp))["A2"].value == "月份 ：9月份"
    # 响应体真的是那个 MIME（浏览器据此落盘成 .xlsx）。
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_the_export_door_is_the_same_admin_door_as_the_page(attendance_http):
    """验收 10：匿名与员工会话都被拒 —— 这一份表里有全店人的底薪与假期。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    _export_employees(accounts, db)
    staff_session = _run(accounts.login("13800138111", PASSWORD))["session_id"]

    client.cookies.clear()
    anonymous = client.get(EXPORT_URL)
    assert anonymous.status_code == 401
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, staff_session)
    denied = client.get(EXPORT_URL)
    assert denied.status_code == 401
    assert denied.json()["detail"] == "需要登录"
    # 两次都被拒 = 没有工作簿漏出去（成功那条路是 200 + 那个 MIME）。
    assert "spreadsheetml" not in denied.headers.get("content-type", "")

    assert _export(client, admin).status_code == 200


# ── 票 08 验收 5（返工）：A 列「班次」写台账里手填的那一项 ────────────────────
#
# 历史样表的 A 列就是台账上的「本月班次」（票 05）：填过的人写他填的那一项，没填过的
# 写服务端给的那份默认（当月众数）。当月一天排班都没有的人**留空** —— 不编一个名字。


def _row_of(worksheet, name):
    """按姓名那一格定位那一行（导出件里没有员工号，B 列是唯一认得出人的一列）。"""
    rows = [
        row
        for row in range(6, worksheet.max_row + 1)
        if worksheet.cell(row=row, column=2).value == name
    ]
    assert len(rows) == 1, (name, rows)
    return rows[0]


def test_column_a_writes_the_shift_filled_in_the_ledger(attendance_http):
    """验收 5 的前半句：台账里填过的人，A 列 == 他填的那一项（**压过**当月众数）。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    shifts = _shift_ids(db)
    scheduler = _scheduler(db)
    employee_id = _employee_id(accounts, "13800138201", "手填了班次")
    # 当月铺成白班 3 天 + 夜班 1 天：众数是**白班**。台账里填的却是「早班」——两个
    # 答案不一样，A 列写哪个就验出来了（写众数的那一版会在这里看到「白班」）。
    _run(
        scheduler.set_overrides_batch(
            "shift",
            shift_id=shifts["白班"],
            items=[
                {"employee_id": employee_id, "business_date": day}
                for day in ("2026-09-01", "2026-09-02", "2026-09-03")
            ],
        )
    )
    _run(scheduler.set_override(employee_id, "2026-09-04", shift_id=shifts["夜班"]))
    _put_ledger_row(db, employee_id, MONTH, shift_name="早班")

    worksheet = _only_sheet(_workbook(_export(client, admin)))

    assert worksheet.cell(row=_row_of(worksheet, "手填了班次"), column=1).value == "早班"


def test_column_a_writes_the_months_mode_when_the_ledger_is_blank(attendance_http):
    """验收 5 的后半句：没填过的人 A 列 == 服务端的默认（当月众数）；没排班的人留空。

    众数用票 05 已有的造数形状验：白班 3 天 + 夜班 1 天 → 白班（并列取显示顺序那一半
    由票 05 的用例钉着，这一条只管「导出件取的是这一项」）。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    shifts = _shift_ids(db)
    scheduler = _scheduler(db)
    scheduled = _employee_id(accounts, "13800138202", "没填过班次")
    unscheduled = _employee_id(accounts, "13800138203", "这个月没排班")
    _run(
        scheduler.set_overrides_batch(
            "shift",
            shift_id=shifts["白班"],
            items=[
                {"employee_id": scheduled, "business_date": day}
                for day in ("2026-09-01", "2026-09-02", "2026-09-03")
            ],
        )
    )
    _run(scheduler.set_override(scheduled, "2026-09-04", shift_id=shifts["夜班"]))

    worksheet = _only_sheet(_workbook(_export(client, admin)))

    assert worksheet.cell(row=_row_of(worksheet, "没填过班次"), column=1).value == "白班"
    # 当月一天排班都没有的人：A 列**留空**（卡片上他也是空的），不编一个班次名出来。
    assert worksheet.cell(row=_row_of(worksheet, "这个月没排班"), column=1).value is None


# ══ 收尾修复（两轴 code-review 之后父级挑出来的六条） ═══════════════════════
#
# 这一段**只在文件尾追加**：上面每一票的用例一个字都没动。六条各自的判据都在下面
# 那几条用例里；第五条（删重复定义）另有一条读源码的契约用例。
#
# 一条例外必须点名：`test_a_sealed_balance_does_not_move_when_last_months_schedule_changes`
# （票 04）原先是**从考勤页那条 HTTP 写口**去改上月排班的，而修复 ③ 之后那条路对
# 非当月/已封账的月一律关闭 —— 那两行驱动改走排班服务层（本文件既有约定：造数走
# 服务层），它的核心断言（封下的存假不漂）一个字都没改。


# ── ① E 列「是否住宿」写台账里手填的那一项 ──────────────────────────────────


def test_column_e_writes_the_lodging_filled_in_the_ledger(attendance_http):
    """① E 列 == 票 05 手填的 `lodging`；**没填过就留空**（不是「否」、不是编一个值）。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    ids = _export_employees(accounts, db)
    assert _put_manual(client, admin, ids["整月都在"], {"lodging": "是"}).status_code == 200

    worksheet = _only_sheet(_workbook(_export(client, admin)))

    assert worksheet.cell(row=_row_of(worksheet, "整月都在"), column=5).value == "是"
    # 没填过的人 E 列是**空格子**：「没填过」与「填了个空串」在导出件里同义。
    assert worksheet.cell(row=_row_of(worksheet, "月底新来的"), column=5).value is None


# ── ② 期初存假（故事 35：上线当月手工给一次） ──────────────────────────────


def test_the_manual_door_writes_the_opening_balance_of_this_month(attendance_http):
    """② 月级写口多认 `opening_balance`：写**本月那一行**、可为负、可带 .5、`null` 清空。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138211", "上线当月给期初")
    # 先给**上月**造一行：这次写入只许动本月那一行（03 的裁定：人工期初读本月那一行）。
    _put_ledger_row(db, employee_id, "2026-08", opening_balance=2.0)

    first = _put_manual(client, admin, employee_id, {"opening_balance": 7.5})

    assert first.status_code == 200, first.text
    # 回执形状不变（既有用例逐字钉着那七个键）：期初是台账上的一位，不往这里塞。
    assert set(first.json()) == {
        "employee_id",
        "month",
        "shift_name",
        "lodging",
        "borrowed_store",
        "resigned",
        "default_shift_name",
    }
    assert _ledger_values(db, employee_id, MONTH, ["opening_balance"]) == {
        "opening_balance": 7.5
    }
    assert _ledger_values(db, employee_id, "2026-08", ["opening_balance"]) == {
        "opening_balance": 2.0
    }
    card = _card(_sheet(client, admin), employee_id)
    assert card["opening_balance"] == 7.5
    # 03 的读路径跟着走：上一月还没封账时「上月存假」读的就是本月这一行的期初。
    assert card["leave"]["previous_balance"] == 7.5

    # 可为负（欠班），也可带 .5。
    assert (
        _put_manual(client, admin, employee_id, {"opening_balance": -1.5}).status_code == 200
    )
    assert _ledger_values(db, employee_id, MONTH, ["opening_balance"]) == {
        "opening_balance": -1.5
    }

    # 显式 `null` = 清空（与从没填过同义）：卡片上留空、存假退回 0。
    assert (
        _put_manual(client, admin, employee_id, {"opening_balance": None}).status_code == 200
    )
    assert _ledger_values(db, employee_id, MONTH, ["opening_balance"]) == {
        "opening_balance": None
    }
    card = _card(_sheet(client, admin), employee_id)
    assert card["opening_balance"] is None
    assert card["leave"]["previous_balance"] == 0.0


def test_the_opening_balance_write_never_touches_the_other_ledger_columns(attendance_http):
    """② 同一行上的四列手填 / 04 的结余与封账时刻：点名谁就只写谁，谁都不整行覆盖谁。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138212", "期初与封账同住一行")
    _put_ledger_row(
        db,
        employee_id,
        MONTH,
        opening_balance=2.5,
        closing_balance=8.0,
        sealed_at="2026-10-01T10:00:00+08:00",
        lodging="是",
    )

    assert (
        _put_manual(client, admin, employee_id, {"opening_balance": 4.5}).status_code == 200
    )
    assert _ledger_values(
        db,
        employee_id,
        MONTH,
        ["opening_balance", "closing_balance", "sealed_at", "lodging"],
    ) == {
        "opening_balance": 4.5,
        "closing_balance": 8.0,
        "sealed_at": "2026-10-01T10:00:00+08:00",
        "lodging": "是",
    }

    # 反方向：只点名手填列时，期初一个字都不动。
    assert _put_manual(client, admin, employee_id, {"lodging": "否"}).status_code == 200
    assert _ledger_values(db, employee_id, MONTH, ["opening_balance", "lodging"]) == {
        "opening_balance": 4.5,
        "lodging": "否",
    }


# ── ③ 逐日写口的三道服务端闸（非当月 / 今天之后 / 已封账） ────────────────────


def test_the_day_editor_refuses_a_month_that_is_already_sealed(attendance_http):
    """③ 故事 33：那个月已经有封账行 → 拒（409），理由点名「封账」，那一行一个字都不写。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138221", "封过账的上月")
    shifts = _shift_ids(db)
    _run(_scheduler(db).set_override(employee_id, "2026-08-26", shift_id=shifts["白班"]))
    assert _seal(client, admin, {"month": "2026-08"}).status_code == 200, "8 月没封上"
    before = _assignment_rows(employee_id, "2026-08-26")
    assert before == [(shifts["白班"], 0)]

    refused = _edit(client, admin, employee_id, "2026-08-26", {"is_rest": True})

    assert refused.status_code == 409, refused.text
    assert "封账" in refused.json()["detail"]
    # 「改成休」那一次没落库：那一行还是那个班次、还是那个半天休标志。
    assert _assignment_rows(employee_id, "2026-08-26") == before


def test_the_day_editor_refuses_a_month_that_is_not_this_one(attendance_http):
    """③ 故事 44：**非当月** → 拒（400），理由点名「只算当月」（八月这一条还没封账）。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138222", "没封账的上月")
    shifts = _shift_ids(db)
    _run(_scheduler(db).set_override(employee_id, "2026-08-26", shift_id=shifts["白班"]))

    refused = _edit(client, admin, employee_id, "2026-08-26", {"is_rest": True})

    assert refused.status_code == 400, refused.text
    assert "当月" in refused.json()["detail"]
    assert _assignment_rows(employee_id, "2026-08-26") == [(shifts["白班"], 0)]


def test_the_day_editor_refuses_a_day_after_today(attendance_http):
    """③ 故事 45：**今天之后** → 拒（400）—— 改班次与标「休半天」两条支路都关着。"""
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138223", "明天还没到")
    shifts = _shift_ids(db)
    # 规则铺到今天之后：25 号本来就有一行白班（旧实现改得动它、也标得上半天休）。
    _run(_scheduler(db).set_rule(employee_id, [shifts["白班"]]))
    assert _assignment_rows(employee_id, "2026-09-25") == [(shifts["白班"], 0)]

    refused = _edit(client, admin, employee_id, "2026-09-25", {"is_rest": True})
    half = _half(client, admin, employee_id, "2026-09-25")

    assert refused.status_code == 400, refused.text
    assert "今天之后" in refused.json()["detail"]
    assert half.status_code == 400, half.text
    assert "今天之后" in half.json()["detail"]
    # 规则铺的那一行原样：既没被改成休，也没被标半天休。
    assert _assignment_rows(employee_id, "2026-09-25") == [(shifts["白班"], 0)]


# ── ④ 逐日改格：未决申请改成「写前查」（拒绝的那一次一个字节都不碰） ──────────


def _row_snapshot(db, table, employee_id, business_date):
    """库里那一行的**原样**（`SELECT *`，含 `updated_at` / `created_at`）。

    `updated_at` 是判「这次有没有写过它」的指纹：这条链路上任何一次写都会盖一个新时刻。
    """

    async def _read():
        cur = await db._conn.execute(
            f"SELECT * FROM {table} WHERE employee_id = ? AND business_date = ?",
            (int(employee_id), business_date),
        )
        rows = await cur.fetchall()
        return dict(rows[0]) if rows else None

    return _run(_read())


def test_a_refused_day_edit_never_writes_the_assignment_row(attendance_http, monkeypatch):
    """④ 判出未决申请的那一次**一个字节都不碰** `staff_assignments` / `scheduling_overrides`。

    补偿式实现（先写、再读那道闸、判出未决就恢复原样）的**终态**与「写前查」一样，所以
    只比对内容看不出差别 —— 这里把路由里那个 `SchedulingStore` 的钟**往后拨一小时**：
    写前查则那一行一个字节都没动（时刻还是原来那个），补偿式会留下 11:00 那个新时刻。
    """
    client, db, accounts = attendance_http
    admin = _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138231", "有未决申请的格子")
    shifts = _shift_ids(db)
    store = _scheduler(db)
    _run(store.set_rule(employee_id, [shifts["白班"]]))
    _run(store.set_override(employee_id, TODAY, shift_id=shifts["夜班"]))
    _run(store.submit_leave(employee_id, TODAY, note="家里有事"))

    tables = ("staff_assignments", "scheduling_overrides")
    before = {table: _row_snapshot(db, table, employee_id, TODAY) for table in tables}
    # 那一行确实是「点之前的样子」（覆盖记录上是夜班、时刻是钉死的钟）。
    assert before["scheduling_overrides"]["shift_id"] == shifts["夜班"]
    assert before["staff_assignments"]["updated_at"] == FIXED_NOW.isoformat()

    later = datetime(2026, 9, 24, 11, 0, tzinfo=CHINA_TZ)
    real_init = SchedulingStore.__init__

    def _late_init(self, *args, **kwargs):
        # 路由里现造的那个 `SchedulingStore(db)` 的钟换成 11:00：任何一次写都会留下新时刻。
        kwargs.setdefault("now", lambda: later)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(SchedulingStore, "__init__", _late_init)

    refused = _edit(client, admin, employee_id, TODAY, {"is_rest": True})

    assert refused.status_code == 409, refused.text
    assert "请假" in refused.json()["detail"]
    assert {table: _row_snapshot(db, table, employee_id, TODAY) for table in tables} == before


# ── ⑤ 删掉重复定义（前一份是被后一份盖掉的死代码，行为零变化） ────────────────


def test_the_attendance_store_defines_its_shared_helpers_once():
    """⑤ `_write_lock` 与 `_now_iso` 在同一个类体里**各只留一份**。

    这是一条读源码的契约（同前端那几条 `readFileSync` 式断言）：定义两遍时，前一遍谁也
    不执行 —— 改它没有任何效果，改错了也看不出来。这里把「只剩一份」钉住。
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "services" / "attendance" / "store.py"
    ).read_text(encoding="utf-8")

    assert len(re.findall(r"^    def _write_lock\(", source, re.M)) == 1
    assert len(re.findall(r"^    def _now_iso\(", source, re.M)) == 1

