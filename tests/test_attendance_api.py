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
import api.scheduling as scheduling_module
import main
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
