#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班 HTTP 面（薄缝）：一条真实请求链路，铺排班的服务层测试不用重跑。

薄缝只回答「谁能打、打进去会怎样」：管理端会话能配规则并立刻在月历上看见；
匿名和**员工会话**都不行（排班是店长的门，员工端这一票一个字不改）。
"""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.scheduling as scheduling_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.scheduling.store import SchedulingStore

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
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


@pytest.fixture
def scheduling_http(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    _run(SchedulingStore(db, now=lambda: FIXED_NOW).prepare())

    app = FastAPI()
    app.include_router(auth_module.router)
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


def _employee_id(accounts, phone=PHONE, name=NAME):
    employee = _run(accounts.register(phone, PASSWORD, name))
    _run(accounts.approve(employee["id"]))
    return employee["id"]


def _zone(db, name="案板"):
    """在卫生那张区表里建一个区（`hygiene_zones`）—— 排班这边不建自己的名单。"""

    async def _insert():
        now = FIXED_NOW.isoformat()
        cur = await db._conn.execute(
            """INSERT INTO hygiene_zones (name, day_shift, night_shift, created_at, updated_at)
               VALUES (?, 1, 1, ?, ?)""",
            (name, now, now),
        )
        await db._conn.commit()
        return int(cur.lastrowid)

    return _run(_insert())


def test_scheduling_routes_require_admin_session(scheduling_http):
    client, db, accounts = scheduling_http
    client.cookies.clear()

    assert client.get("/api/scheduling/shifts").status_code == 401
    assert client.get("/api/scheduling/calendar", params={"month": "2026-09"}).status_code == 401
    assert client.get("/api/scheduling/roster").status_code == 401
    assert client.put("/api/scheduling/rules/1", json={"cycle": [1]}).status_code == 401
    assert client.put(
        "/api/scheduling/zone-defaults/1", json={"shift_id": 1, "zone_id": 1}
    ).status_code == 401

    # 员工会话不是店长的门：换一个 cookie 名字照样 401（排班只认管理端会话）。
    employee_id = _employee_id(accounts)
    login = _run(accounts.login(PHONE, PASSWORD))
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, login["session_id"])
    assert client.get("/api/scheduling/calendar", params={"month": "2026-09"}).status_code == 401


def test_admin_can_configure_and_see_it_on_the_calendar(scheduling_http):
    client, _db, accounts = scheduling_http
    employee_id = _employee_id(accounts)

    shifts = client.get("/api/scheduling/shifts").json()["shifts"]
    assert [shift["name"] for shift in shifts] == ["白班", "夜班"]
    day_id = shifts[0]["id"]

    # 一进来月历是空的（没人配规则）。
    empty = client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json()
    assert empty["today"] == TODAY
    assert all(day["total"] == 0 for day in empty["days"])

    saved = client.put(f"/api/scheduling/rules/{employee_id}", json={"cycle": [day_id]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["cycle"] == [day_id]

    calendar = client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json()
    cells = {day["business_date"]: day for day in calendar["days"]}
    assert cells[TODAY]["counts"][str(day_id)] == 1
    assert cells[TODAY]["is_today"] is True

    detail = client.get("/api/scheduling/day", params={"date": TODAY}).json()
    assert detail["total"] == 1
    assert [person["name"] for person in detail["groups"][0]["people"]] == [NAME]

    roster = client.get("/api/scheduling/roster").json()
    listed = [row for row in roster["employees"] if row["id"] == employee_id][0]
    assert listed["rule"]["cycle"] == [day_id]

    assert client.delete(f"/api/scheduling/rules/{employee_id}").status_code == 200
    after = client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json()
    assert all(day["total"] == 0 for day in after["days"])


def test_cycle_editor_round_trip(scheduling_http):
    """票 04：店长编一条「白白夜休」的周期，月历按周期走，写错要说清第几格。"""
    client, _db, accounts = scheduling_http
    employee_id = _employee_id(accounts)
    shifts = client.get("/api/scheduling/shifts").json()["shifts"]
    day_id, night_id = shifts[0]["id"], shifts[1]["id"]

    # 规则编辑页的天数上限跟服务端常量一起下来，前端不写死一份。
    roster = client.get("/api/scheduling/roster").json()
    assert roster["max_cycle_days"] >= 7

    cycle = [day_id, day_id, night_id, None]
    saved = client.put(
        f"/api/scheduling/rules/{employee_id}",
        json={"cycle": cycle, "anchor_date": TODAY},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["anchor_date"] == TODAY

    calendar = client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json()
    cells = {item["business_date"]: item for item in calendar["days"]}
    assert cells["2026-09-24"]["counts"][str(day_id)] == 1  # 周期第 1 格
    assert cells["2026-09-26"]["counts"][str(night_id)] == 1  # 第 3 格
    assert cells["2026-09-27"]["total"] == 0  # 第 4 格 = 休
    assert cells["2026-09-28"]["counts"][str(day_id)] == 1  # 第 5 天回到第 1 格

    rest = client.get("/api/scheduling/day", params={"date": "2026-09-27"}).json()
    assert rest["total"] == 0
    assert rest["off_count"] == 1

    bad = client.put(
        f"/api/scheduling/rules/{employee_id}",
        json={"cycle": [day_id, 999999, day_id]},
    )
    assert bad.status_code == 400, bad.text
    assert "第 2 格" in bad.json()["detail"]


def test_admin_can_pin_a_fixed_zone_per_shift(scheduling_http):
    """票 03：配了固定区之后，当天名单上就写着「谁 · 在哪个区」。"""
    client, db, accounts = scheduling_http
    employee_id = _employee_id(accounts)
    day_id = client.get("/api/scheduling/shifts").json()["shifts"][0]["id"]
    zone_id = _zone(db, "案板")

    roster = client.get("/api/scheduling/roster").json()
    # 名单只有一份，是卫生那条线建的（排班经公共层读出来）。
    assert {"id": zone_id, "name": "案板"} in roster["zones"]
    assert roster["employees"][0]["zone_defaults"] == {}

    saved = client.put(
        f"/api/scheduling/zone-defaults/{employee_id}",
        json={"shift_id": day_id, "zone_id": zone_id},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "employee_id": employee_id,
        "shift_id": day_id,
        "zone_id": zone_id,
    }

    assert (
        client.put(f"/api/scheduling/rules/{employee_id}", json={"cycle": [day_id]}).status_code
        == 200
    )
    detail = client.get("/api/scheduling/day", params={"date": TODAY}).json()
    assert detail["groups"][0]["people"] == [
        {"id": employee_id, "name": NAME, "zone": "案板"}
    ]

    # 挑了一个不存在的区：说得出「责任区不存在」，不是一句「参数不合法」。
    bad = client.put(
        f"/api/scheduling/zone-defaults/{employee_id}",
        json={"shift_id": day_id, "zone_id": 987654},
    )
    assert bad.status_code == 400
    assert bad.json()["detail"] == "责任区不存在：请先在卫生的责任区页面新建，或刷新本页"

    # 清掉 = 回到「未配区」，人还留在那天的名单上（票 03 的验收项）。
    cleared = client.put(
        f"/api/scheduling/zone-defaults/{employee_id}",
        json={"shift_id": day_id, "zone_id": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["zone_id"] is None
    detail = client.get("/api/scheduling/day", params={"date": TODAY}).json()
    assert detail["groups"][0]["people"][0]["zone"] is None


def test_bad_input_is_a_400_and_unknown_employee_is_a_404(scheduling_http):
    client, _db, accounts = scheduling_http
    employee_id = _employee_id(accounts)

    # 每种错各有各的话：店长要能看出是月份写错了还是班次挑错了，
    # 「排班参数不合法」一句糊过去等于没说。
    month = client.get("/api/scheduling/calendar", params={"month": "2026-9"})
    assert month.status_code == 400
    assert month.json()["detail"] == "月份格式应该是 YYYY-MM"
    day = client.get("/api/scheduling/day", params={"date": "24/09/2026"})
    assert day.status_code == 400
    assert day.json()["detail"] == "日期格式应该是 YYYY-MM-DD"
    # 空周期在请求形状这一层就被挡下（pydantic），所以是 422 而不是 400。
    assert client.put(f"/api/scheduling/rules/{employee_id}", json={"cycle": []}).status_code == 422
    shift = client.put(f"/api/scheduling/rules/{employee_id}", json={"cycle": [999]})
    assert shift.status_code == 400
    # 票 04 起这句话带格号：规则编辑页要能指出错在哪一天。
    assert shift.json()["detail"] == "轮转周期第 1 格引用的班次不存在或已停用：请重新选那一天的班次"
    missing = client.put("/api/scheduling/rules/9999", json={"cycle": [1]})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "员工不存在"


def test_missing_table_is_a_503_with_instructions():
    """0005 还没应用时，页面拿到的是「去数据库迁移面板」，不是一个 500。"""
    from api.scheduling import _bad_request
    from services.scheduling import SchedulingError

    exc = _bad_request(SchedulingError("not_migrated", "UndefinedTableError: staff_shifts"))
    assert exc.status_code == 503
    assert "0005_scheduling.sql" in exc.detail


def test_dirty_rule_data_is_a_400_not_a_500(scheduling_http):
    """人工改库把起点写成垃圾：接口给一句人话的 400，不是一个 500。"""
    client, db, accounts = scheduling_http
    employee_id = _employee_id(accounts)
    day_id = client.get("/api/scheduling/shifts").json()["shifts"][0]["id"]
    saved = client.put(f"/api/scheduling/rules/{employee_id}", json={"cycle": [day_id]})
    assert saved.status_code == 200, saved.text

    async def _dirty():
        await db._conn.execute(
            "UPDATE scheduling_rules SET anchor_date = ? WHERE employee_id = ?",
            ("昨天", employee_id),
        )
        await db._conn.commit()

    _run(_dirty())
    calendar = client.get("/api/scheduling/calendar", params={"month": "2026-09"})
    assert calendar.status_code == 400, calendar.text
    assert calendar.json()["detail"] == "轮转规则的起点日不合法（应该是 YYYY-MM-DD）：请重新配一遍这条规则"


def test_error_details_never_leak_the_placeholder():
    """`{}` 是给「说清哪里错」用的：缺细节时不能把字面 `{}` 端给店长看。"""
    from api.scheduling import _ERROR_DETAILS, _bad_request
    from services.scheduling import SchedulingError

    for code in _ERROR_DETAILS:
        detail = _bad_request(SchedulingError(code)).detail
        assert "{}" not in detail, code
    formatted = _bad_request(SchedulingError("unknown_shift_in_cycle", "3")).detail
    assert "第 3 格" in formatted


def test_half_migrated_store_still_serves_the_calendar(scheduling_http):
    """0005 应用了、0006 还没应用：月历照常，只有名单面板给 503 和该跑哪个脚本。

    门店顺序是「先更新代码、再在 Admin 应用迁移」，这段窗口真的会出现；`/roster`
    在票 03 之后要读 `scheduling_zone_defaults`，所以它是第一个撞上的。
    """
    client, db, _accounts = scheduling_http

    async def _rename(source, target):
        await db._conn.execute(f"ALTER TABLE {source} RENAME TO {target}")
        await db._conn.commit()

    _run(_rename("scheduling_zone_defaults", "scheduling_zone_defaults_tmp"))
    try:
        roster = client.get("/api/scheduling/roster")
        assert roster.status_code == 503, roster.text
        assert "0006_scheduling_zone_defaults.sql" in roster.json()["detail"]
        # 不读那张表的两个入口照旧能用（否则整页都打不开，店长连月历都看不了）。
        assert client.get("/api/scheduling/calendar", params={"month": "2026-09"}).status_code == 200
        assert client.get("/api/scheduling/shifts").status_code == 200
    finally:
        _run(_rename("scheduling_zone_defaults_tmp", "scheduling_zone_defaults"))
