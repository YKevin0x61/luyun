#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班 HTTP 面（薄缝）：一条真实请求链路，铺排班的服务层测试不用重跑。

薄缝只回答「谁能打、打进去会怎样」：管理端会话能配规则并立刻在月历上看见；
匿名两边都不行。票 05 起有两扇门 —— 店长那几条只认管理端会话，员工那几条
（`/me`、`/me/month`、票 08 的 `/me/requests`、票 09 的 `/me/colleagues` 与
`/me/swaps`）只认手机端 cookie（票 02 时这里断言的是「一条员工路由都没有」）。
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
    # 票 07 的两条单日覆盖也挂同一扇门（读写都算店长的动作）。
    assert client.put("/api/scheduling/overrides/1/2026-09-25", json={}).status_code == 401
    assert client.delete("/api/scheduling/overrides/1/2026-09-25").status_code == 401
    # 票 08 的待办三条同理：批 / 驳是店长的动作。
    assert client.get("/api/scheduling/inbox").status_code == 401
    assert client.post("/api/scheduling/inbox/1/approve").status_code == 401
    assert client.post("/api/scheduling/inbox/1/reject").status_code == 401

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
    # `overridden`/`zone_id` 是票 07 加的：这一行是规则铺的，不是店长手改的；
    # 区按 id 也给一份，编辑器要按 id 预填下拉（名字没有唯一约束）。
    # `leave` 是票 08 加的：请假批下来之后那天没有班次，但和「本来就休」分得开。
    assert detail["groups"][0]["people"] == [
        {
            "id": employee_id,
            "name": NAME,
            "zone": "案板",
            "zone_id": zone_id,
            "overridden": False,
            "leave": False,
        }
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


def test_missing_overrides_table_is_a_503_that_names_it(scheduling_http):
    """0007 还没应用：单日覆盖整条给 503，并且点名该跑哪个脚本。

    这是票 07 引入的新耦合 —— 展开（`_expand_rows`）现在要读覆盖表，所以
    「配轮转规则」也跟着一起 503。**不说清楚的话，店长只会看到「保存失败」**：
    文案里三个脚本都点名（0005/0006/0007），照着跑哪个都对。
    """
    client, db, accounts = scheduling_http
    employee_id = _employee_id(accounts)
    day_id = client.get("/api/scheduling/shifts").json()["shifts"][0]["id"]

    async def _rename(source, target):
        await db._conn.execute(f"ALTER TABLE {source} RENAME TO {target}")
        await db._conn.commit()

    _run(_rename("scheduling_overrides", "scheduling_overrides_tmp"))
    try:
        saved = client.put("/api/scheduling/overrides/1/2026-09-25", json={"is_rest": True})
        assert saved.status_code == 503, saved.text
        assert "0007_scheduling_overrides.sql" in saved.json()["detail"]
        # 展开要读覆盖表 → 配规则也撞同一堵墙（读月历/当日不读它，照旧 200）。
        rule = client.put(f"/api/scheduling/rules/{employee_id}", json={"cycle": [day_id]})
        assert rule.status_code == 503, rule.text
        assert client.get("/api/scheduling/calendar", params={"month": "2026-09"}).status_code == 200
        assert client.get("/api/scheduling/day", params={"date": TODAY}).status_code == 200
    finally:
        _run(_rename("scheduling_overrides_tmp", "scheduling_overrides"))


# ── 票 05：员工那条门 ────────────────────────────────────────────────────


def _wire_staff_accounts(monkeypatch, accounts):
    """把员工账号挂到 `main.employee_accounts` 上：员工会话的查找口在那里。"""
    import main

    monkeypatch.setattr(main, "employee_accounts", accounts)


def _staff_cookie(client, accounts, phone=PHONE):
    """换员工那个 cookie（跟管理端是两个名字），登录用的是同一套账号。"""
    session_id = _run(accounts.login(phone, PASSWORD))["session_id"]
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, session_id)
    return session_id


def _admin_cookie(client):
    """先把管理端会话记下来：`_staff_cookie` 会清掉所有 cookie，往返两边时要把它换回来。"""
    return client.cookies.get(settings.SESSION_COOKIE_NAME)


def _as_manager(client, session_id):
    client.cookies.clear()
    client.cookies.set(settings.SESSION_COOKIE_NAME, session_id)


def test_staff_me_returns_their_own_next_days(scheduling_http, monkeypatch):
    """验收 2/3/5/7：员工拿手机端那个 cookie 读到自己的班，跟店长配的是同一件事。"""
    client, db, accounts = scheduling_http
    employee_id = _employee_id(accounts)
    day_id = client.get("/api/scheduling/shifts").json()["shifts"][0]["id"]
    zone_id = _zone(db, "案板")
    assert (
        client.put(
            f"/api/scheduling/zone-defaults/{employee_id}",
            json={"shift_id": day_id, "zone_id": zone_id},
        ).status_code
        == 200
    )
    assert (
        client.put(f"/api/scheduling/rules/{employee_id}", json={"cycle": [day_id]}).status_code
        == 200
    )

    _wire_staff_accounts(monkeypatch, accounts)
    _staff_cookie(client, accounts)

    me = client.get("/api/scheduling/me")
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["employee"] == {"id": employee_id, "name": NAME}
    # 「今天」是排班自己那个 06:00 切日的今天，跟月历同一个日期。
    assert body["today"] == TODAY
    assert [item["business_date"] for item in body["days"]] == [
        "2026-09-24",
        "2026-09-25",
        "2026-09-26",
        "2026-09-27",
    ]
    first = body["days"][0]
    assert (first["is_today"], first["shift_name"], first["zone_name"]) == (
        True,
        "白班",
        "案板",
    )
    assert all(item["is_today"] is False for item in body["days"][1:])


def test_a_rest_day_still_has_its_own_row(scheduling_http, monkeypatch):
    """验收 3：休那天有行（`scheduled` 为真、班次为空）——

    「今天休」和「今天没你的班」在员工页上是两句话，判据得从服务端下来。
    """
    client, _db, accounts = scheduling_http
    employee_id = _employee_id(accounts)
    day_id = client.get("/api/scheduling/shifts").json()["shifts"][0]["id"]
    # 店长配一条「上一天、休一天」：休那天也得有一行下来。
    assert (
        client.put(
            f"/api/scheduling/rules/{employee_id}", json={"cycle": [day_id, None]}
        ).status_code
        == 200
    )

    _wire_staff_accounts(monkeypatch, accounts)
    _staff_cookie(client, accounts)
    body = client.get("/api/scheduling/me").json()

    assert [item["scheduled"] for item in body["days"]] == [True] * 4
    assert [item["shift_name"] for item in body["days"]] == ["白班", None, "白班", None]


def test_staff_without_any_rule_sees_no_shift_not_an_error(scheduling_http, monkeypatch):
    """验收 6：一条规则都没配的人 —— 四天都占着行、都标着「还没排」。"""
    client, _db, accounts = scheduling_http
    _employee_id(accounts)
    _wire_staff_accounts(monkeypatch, accounts)
    _staff_cookie(client, accounts)

    body = client.get("/api/scheduling/me").json()

    assert [item["scheduled"] for item in body["days"]] == [False] * 4
    assert [item["shift_name"] for item in body["days"]] == [None] * 4
    assert [item["business_date"] for item in body["days"]] == [
        "2026-09-24",
        "2026-09-25",
        "2026-09-26",
        "2026-09-27",
    ]


def test_the_two_doors_do_not_open_each_other(scheduling_http, monkeypatch):
    """验收 7：同一套员工账号、同一个 cookie；两扇门看的是两个 cookie 名。"""
    client, _db, accounts = scheduling_http
    _employee_id(accounts)
    _wire_staff_accounts(monkeypatch, accounts)

    # 管理端会话打不开员工的门（薄缝里这会儿挂着的就是管理端会话）。
    denied = client.get("/api/scheduling/me")
    assert denied.status_code == 401
    assert denied.json()["detail"] == "需要员工登录"
    # 票 08：请假申请那三条也是员工门 —— 店长的会话读不到、也提不了。
    assert client.get("/api/scheduling/me/requests").status_code == 401
    assert client.post(
        "/api/scheduling/me/requests", json={"start_date": "2026-09-28"}
    ).status_code == 401
    # 票 09 的换班四条同理：能找谁换、提一条、替对方点头 / 摇头，都只认员工自己的 cookie。
    assert client.get("/api/scheduling/me/colleagues").status_code == 401
    assert client.post(
        "/api/scheduling/me/swaps", json={"peer_employee_id": 2, "business_date": "2026-09-28"}
    ).status_code == 401
    assert client.post("/api/scheduling/me/swaps/1/accept").status_code == 401
    assert client.post("/api/scheduling/me/swaps/1/reject").status_code == 401

    _staff_cookie(client, accounts)
    assert client.get("/api/scheduling/me").status_code == 200
    assert client.get("/api/scheduling/me/requests").status_code == 200
    assert client.get("/api/scheduling/me/colleagues").status_code == 200
    # 反过来也一样：员工会话读不到店长的任何一条（待办也在里面）。
    assert client.get("/api/scheduling/roster").status_code == 401
    assert client.get("/api/scheduling/calendar", params={"month": "2026-09"}).status_code == 401
    assert client.get("/api/scheduling/inbox").status_code == 401
    assert client.post("/api/scheduling/inbox/1/approve").status_code == 401

    client.cookies.clear()
    assert client.get("/api/scheduling/me").status_code == 401
    assert client.get("/api/scheduling/roster").status_code == 401
    assert client.get("/api/scheduling/inbox").status_code == 401


def test_staff_door_says_503_while_the_account_service_is_not_ready(
    scheduling_http, monkeypatch
):
    """服务刚起的那几秒 `main.employee_accounts` 还是 None：503（稍后再试）不是 500。

    这是员工手机上第一条请求最可能撞上的那一支（`api/security.py:_staff_accounts`），
    所以它得真测 —— 不能只留在注释里。
    """
    import main

    client, _db, _accounts = scheduling_http
    monkeypatch.setattr(main, "employee_accounts", None)

    resp = client.get("/api/scheduling/me")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "员工账号服务未就绪（服务正在启动）"
    # 店长那几条不受影响：它们不查员工账号。
    assert client.get("/api/scheduling/shifts").status_code == 200


def test_staff_month_returns_their_own_calendar(scheduling_http, monkeypatch):
    """票 06 验收 1/2/5：员工拿自己的 cookie 读整月，格子里是班别（休是空班名）。"""
    client, _db, accounts = scheduling_http
    employee_id = _employee_id(accounts)
    day_id = client.get("/api/scheduling/shifts").json()["shifts"][0]["id"]
    night_id = client.get("/api/scheduling/shifts").json()["shifts"][1]["id"]
    assert client.put(
        f"/api/scheduling/rules/{employee_id}",
        json={"cycle": [day_id, night_id, None]},
    ).status_code == 200
    _wire_staff_accounts(monkeypatch, accounts)
    _staff_cookie(client, accounts)

    resp = client.get("/api/scheduling/me/month", params={"month": "2026-09"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["employee"]["id"] == employee_id
    assert body["month"] == "2026-09"
    assert body["today"] == TODAY
    assert [item["day"] for item in body["days"]] == list(range(1, 31))
    assert body["lead"] == 2  # 2026-09-01 是周二，表头周日开头
    around_today = [item for item in body["days"] if item["day"] >= 24][:3]
    assert [(item["shift_name"], item["scheduled"]) for item in around_today] == [
        ("白班", True),
        ("夜班", True),
        (None, True),  # 休：行在、班次是空的
    ]
    assert [item["day"] for item in body["days"] if item["is_today"]] == [24]
    # 只读自己：这条门没有 employee_id 参数，换个人读也只能读到自己。
    other = _employee_id(accounts, phone="13800138001", name="李四")
    assert other != employee_id
    assert client.get("/api/scheduling/me/month").json()["employee"]["id"] == employee_id
    # 换成李四的 cookie：读到的就是他（没配规则 → 整月都还没排），张三那几行一个字不露。
    _staff_cookie(client, accounts, phone="13800138001")
    theirs = client.get("/api/scheduling/me/month", params={"month": "2026-09"}).json()
    assert theirs["employee"]["id"] == other
    assert {item["shift_name"] for item in theirs["days"] if item["scheduled"]} == set()


def test_staff_month_is_still_the_staff_door(scheduling_http, monkeypatch):
    """同一扇门：管理端会话打不开它，匿名也打不开（验收 4 的另一半）。"""
    client, _db, accounts = scheduling_http
    _employee_id(accounts)
    _wire_staff_accounts(monkeypatch, accounts)

    denied = client.get("/api/scheduling/me/month", params={"month": "2026-09"})
    assert denied.status_code == 401
    assert denied.json()["detail"] == "需要员工登录"

    _staff_cookie(client, accounts)
    assert client.get("/api/scheduling/me/month").status_code == 200
    client.cookies.clear()
    assert client.get("/api/scheduling/me/month").status_code == 401


def test_a_bad_month_is_a_400_not_a_500(scheduling_http, monkeypatch):
    """月份写错是输入错误：400 + 一句给员工看的话（不是 500，也不是空白月）。"""
    client, _db, accounts = scheduling_http
    _employee_id(accounts)
    _wire_staff_accounts(monkeypatch, accounts)
    _staff_cookie(client, accounts)

    resp = client.get("/api/scheduling/me/month", params={"month": "2026-9"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "月份格式应该是 YYYY-MM"


# ── 单日覆盖（票 07）───────────────────────────────────────────────────────


def test_manager_changes_one_day_and_undoes_it(scheduling_http):
    """验收 1/4/5 的 HTTP 面：改一天 → 月历上有标记 → 撤掉回到规则。"""
    client, _db, accounts = scheduling_http
    employee_id = _employee_id(accounts)
    shifts = client.get("/api/scheduling/shifts").json()["shifts"]
    day_id, night_id = shifts[0]["id"], shifts[1]["id"]
    assert client.put(f"/api/scheduling/rules/{employee_id}", json={"cycle": [day_id]}).status_code == 200

    changed = client.put(
        f"/api/scheduling/overrides/{employee_id}/2026-09-25",
        json={"shift_id": night_id},
    )

    assert changed.status_code == 200, changed.text
    assert changed.json() == {
        "employee_id": employee_id,
        "business_date": "2026-09-25",
        "shift_id": night_id,
        "zone_id": None,
        "is_rest": False,
    }
    # 只动那一天：月历上 9/24 与 9/26 还是白班，9/25 是夜班 + 一个覆盖标记。
    calendar = client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json()
    cells = {day["business_date"]: day for day in calendar["days"]}
    assert cells["2026-09-24"]["counts"] == {str(day_id): 1, str(night_id): 0}
    assert cells["2026-09-25"]["counts"] == {str(day_id): 0, str(night_id): 1}
    assert cells["2026-09-26"]["counts"] == {str(day_id): 1, str(night_id): 0}
    assert [cells[day]["overridden"] for day in ("2026-09-24", "2026-09-25", "2026-09-26")] == [
        0,
        1,
        0,
    ]
    detail = client.get("/api/scheduling/day", params={"date": "2026-09-25"}).json()
    # 那天两组都在（启用的班次各占一组，没人那组 count=0）：人从白班挪到了夜班。
    groups = {group["shift"]["name"]: group for group in detail["groups"]}
    assert (groups["白班"]["count"], groups["夜班"]["count"]) == (0, 1)
    assert groups["夜班"]["people"][0]["overridden"] is True

    undone = client.delete(f"/api/scheduling/overrides/{employee_id}/2026-09-25")

    assert undone.status_code == 200, undone.text
    assert undone.json() == {"employee_id": employee_id, "business_date": "2026-09-25"}
    back = client.get("/api/scheduling/day", params={"date": "2026-09-25"}).json()
    groups = {group["shift"]["name"]: group for group in back["groups"]}
    assert (groups["白班"]["count"], groups["夜班"]["count"]) == (1, 0)
    assert groups["白班"]["people"][0]["overridden"] is False
    calendar = client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json()
    cells = {day["business_date"]: day for day in calendar["days"]}
    assert cells["2026-09-25"]["overridden"] == 0


def test_rest_override_leaves_the_headcount_and_keeps_its_mark(scheduling_http):
    """验收 2 + 4：改成休 —— 白班人数少一个，格子上仍留一个「这天被改过」。"""
    client, _db, accounts = scheduling_http
    employee_id = _employee_id(accounts)
    day_id = client.get("/api/scheduling/shifts").json()["shifts"][0]["id"]
    assert client.put(f"/api/scheduling/rules/{employee_id}", json={"cycle": [day_id]}).status_code == 200

    rest = client.put(
        f"/api/scheduling/overrides/{employee_id}/2026-09-25",
        json={"is_rest": True},
    )

    assert rest.status_code == 200, rest.text
    assert rest.json()["shift_id"] is None and rest.json()["is_rest"] is True
    calendar = client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json()
    cells = {day["business_date"]: day for day in calendar["days"]}
    assert cells["2026-09-25"]["counts"][str(day_id)] == 0
    assert cells["2026-09-25"]["overridden"] == 1
    detail = client.get("/api/scheduling/day", params={"date": "2026-09-25"}).json()
    assert (detail["total"], detail["off_count"]) == (0, 1)


def test_override_errors_are_readable_400s(scheduling_http):
    """改不了的三种情形各说各的 —— 不是一个「参数不合法」打包（票 07 的验收 7）。"""
    client, _db, accounts = scheduling_http
    employee_id = _employee_id(accounts)
    shifts = client.get("/api/scheduling/shifts").json()["shifts"]
    day_id = shifts[0]["id"]
    base = f"/api/scheduling/overrides/{employee_id}"

    past = client.put(f"{base}/2026-09-23", json={"shift_id": day_id})
    assert past.status_code == 400
    assert past.json()["detail"] == "已经过去的日子改不了：排班写下的历史不重写"

    beyond = client.put(f"{base}/2026-12-23", json={"shift_id": day_id})
    assert beyond.status_code == 400
    # 报的是**真实的窗口末日**（`{}` 由服务层填），不是把「90 天」写死在文案里。
    assert beyond.json()["detail"] == (
        "这天还没排到：排班只铺到 2026-12-22，等它进窗口再改"
    )

    both = client.put(f"{base}/2026-09-25", json={"is_rest": True, "shift_id": day_id})
    assert both.status_code == 400
    assert both.json()["detail"] == "「改成休」的那天不能再带班次或责任区：请把这两项留空"

    blank = client.put(f"{base}/2026-09-25", json={})
    assert blank.status_code == 400
    assert blank.json()["detail"] == "要改班次就得给一个班次；班次留空表示那天休，请用「改成休」"

    gone = client.put(f"{base}/2026-09-25", json={"shift_id": 987654, "zone_id": 987654})
    assert gone.status_code == 400
    assert gone.json()["detail"] == "班次不存在或已停用"

    missing_person = client.put("/api/scheduling/overrides/9999/2026-09-25", json={"shift_id": day_id})
    assert missing_person.status_code == 404
    assert missing_person.json()["detail"] == "员工不存在"

    bad_date = client.put(f"{base}/9-25", json={"shift_id": day_id})
    assert bad_date.status_code == 400
    assert bad_date.json()["detail"] == "日期格式应该是 YYYY-MM-DD"

    # 撤销也过同一道日期校验（这条原来被一句「现在不会抛」的 pragma 盖住了）。
    undo_bad_date = client.delete(f"{base}/9-25")
    assert undo_bad_date.status_code == 400
    assert undo_bad_date.json()["detail"] == "日期格式应该是 YYYY-MM-DD"


# ── 请假申请（票 08）───────────────────────────────────────────────────────


def _leave_scene(client, accounts, monkeypatch, people=((PHONE, NAME),)):
    """建人 + 配「每天都白班」，然后切到第一个人的员工 cookie。

    返回 `(管理端会话, [员工 id...], 白班 id)`。店长与员工是两扇门，来回走要换 cookie
    （`_as_manager` 换回去），所以管理端会话先在切换之前存下来。
    """
    day_id = client.get("/api/scheduling/shifts").json()["shifts"][0]["id"]
    employee_ids = []
    for phone, name in people:
        employee_id = _employee_id(accounts, phone=phone, name=name)
        saved = client.put(f"/api/scheduling/rules/{employee_id}", json={"cycle": [day_id]})
        assert saved.status_code == 200, saved.text
        employee_ids.append(employee_id)
    admin = _admin_cookie(client)
    _wire_staff_accounts(monkeypatch, accounts)
    _staff_cookie(client, accounts, phone=people[0][0])
    return admin, employee_ids, day_id


def test_staff_asks_for_leave_and_takes_it_back(scheduling_http, monkeypatch):
    """验收 1：提一天或一段、看得见状态、批之前能撤回；提的时候排班一个字不改。"""
    client, _db, accounts = scheduling_http
    _admin, (employee_id,), _day_id = _leave_scene(client, accounts, monkeypatch)

    submitted = client.post(
        "/api/scheduling/me/requests",
        json={"start_date": "2026-09-25", "end_date": "2026-09-26", "note": "家里有事"},
    )

    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["employee"] == {"id": employee_id, "name": NAME}
    assert body["request"]["kind"] == "leave"
    assert body["request"]["status"] == "pending_manager"
    assert body["request"]["decided_at"] is None
    assert (body["request"]["start_date"], body["request"]["end_date"]) == (
        "2026-09-25",
        "2026-09-26",
    )
    request_id = body["request"]["id"]

    # 还没批：我那几天照旧（申请本身不碰排班）。
    days = client.get("/api/scheduling/me").json()["days"]
    assert [(day["business_date"], day["shift_name"], day["leave"]) for day in days[:2]] == [
        ("2026-09-24", "白班", False),
        ("2026-09-25", "白班", False),
    ]
    mine = client.get("/api/scheduling/me/requests")
    assert mine.status_code == 200, mine.text
    assert mine.json()["employee"] == {"id": employee_id, "name": NAME}
    assert mine.json()["today"] == TODAY
    assert [(row["id"], row["status"]) for row in mine.json()["requests"]] == [
        (request_id, "pending_manager")
    ]

    cancelled = client.delete(f"/api/scheduling/me/requests/{request_id}")

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert [row["status"] for row in client.get("/api/scheduling/me/requests").json()["requests"]] == [
        "cancelled"
    ]
    # 撤回过的不能再撤：状态机只往前走。
    again = client.delete(f"/api/scheduling/me/requests/{request_id}")
    assert again.status_code == 400
    assert again.json()["detail"] == "这条申请已经处理过了：刷新看看它现在到哪一步"


def test_manager_inbox_previews_the_headcount_after_approval(scheduling_http, monkeypatch):
    """验收 2：卡片上直接写着「批了之后那天每个班次还剩几个人」，还有没配规则的新人。"""
    client, _db, accounts = scheduling_http
    admin, (leaver, _stayer), day_id = _leave_scene(
        client, accounts, monkeypatch, people=((PHONE, NAME), ("13800138001", "李四"))
    )
    # 一个还没配规则的新人（名单里有、规则里没有）——待办要把他列出来。
    _employee_id(accounts, phone="13800138002", name="赵六")

    request_id = client.post(
        "/api/scheduling/me/requests", json={"start_date": "2026-09-25", "note": "家里有事"}
    ).json()["request"]["id"]

    _as_manager(client, admin)
    inbox = client.get("/api/scheduling/inbox")

    assert inbox.status_code == 200, inbox.text
    body = inbox.json()
    assert body["today"] == TODAY
    assert [(row["name"], row["approved"], row["disabled"]) for row in body["without_rule"]] == [
        ("赵六", True, False)
    ]
    card = body["requests"][0]
    assert (card["id"], card["employee_id"], card["employee_name"]) == (request_id, leaver, NAME)
    assert (card["kind"], card["note"]) == ("leave", "家里有事")
    assert [day["business_date"] for day in card["days"]] == ["2026-09-25"]
    preview = card["days"][0]
    assert (preview["past"], preview["scheduled"], preview["current_shift_name"]) == (
        False,
        True,
        "白班",
    )
    # 那天本来两个人在白班：批了她（她走）就只剩一个 —— 数字摊开，不拦。
    assert preview["after"] == [{"shift_id": day_id, "shift_name": "白班", "count": 1}]


def test_approving_a_leave_turns_those_days_into_leave(scheduling_http, monkeypatch):
    """验收 3 + 5：批了那几天就变成请假 —— 店长的当日分工与员工那一页都看得出来。"""
    client, _db, accounts = scheduling_http
    admin, (leaver, stayer), day_id = _leave_scene(
        client, accounts, monkeypatch, people=((PHONE, NAME), ("13800138001", "李四"))
    )

    _as_manager(client, admin)
    before = client.get("/api/scheduling/day", params={"date": "2026-09-25"}).json()
    assert (before["total"], before["off_count"]) == (2, 0)

    _staff_cookie(client, accounts)
    request_id = client.post(
        "/api/scheduling/me/requests", json={"start_date": "2026-09-25", "end_date": "2026-09-26"}
    ).json()["request"]["id"]

    _as_manager(client, admin)
    approved = client.post(f"/api/scheduling/inbox/{request_id}/approve")

    assert approved.status_code == 200, approved.text
    result = approved.json()
    assert (result["id"], result["employee_id"], result["status"]) == (
        request_id,
        leaver,
        "approved",
    )
    assert result["applied_days"] == ["2026-09-25", "2026-09-26"]
    assert result["skipped_days"] == []
    assert result["decided_at"]

    after = client.get("/api/scheduling/day", params={"date": "2026-09-25"}).json()
    groups = {group["shift"]["name"]: group for group in after["groups"]}
    assert (after["total"], after["off_count"], groups["白班"]["count"]) == (1, 1, 1)
    assert [person["name"] for person in groups["白班"]["people"]] == ["李四"]
    # 「请假」和「本来就休」都是没有班次，`off_people` 里分得开（票面的验收 5）。
    leave = [person for person in after["off_people"] if person["name"] == NAME][0]
    assert (leave["leave"], leave["overridden"]) == (True, True)
    assert [person["name"] for person in after["off_people"]] == [NAME]
    # 月历上那两天留着「被改过」：批出来的一天也是手改过的一天。
    cells = {
        day["business_date"]: day
        for day in client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json()["days"]
    }
    assert (cells["2026-09-25"]["counts"][str(day_id)], cells["2026-09-25"]["overridden"]) == (1, 1)
    # 待办清空了：这条不再是「等店长批」。
    assert client.get("/api/scheduling/inbox").json()["requests"] == []

    # 员工那一页：那两天显示请假（不是「休」），申请也变成批了。
    _staff_cookie(client, accounts)
    days = {day["business_date"]: day for day in client.get("/api/scheduling/me").json()["days"]}
    assert (days["2026-09-25"]["shift_name"], days["2026-09-25"]["leave"]) == (None, True)
    assert (days["2026-09-26"]["shift_name"], days["2026-09-26"]["leave"]) == (None, True)
    assert (days["2026-09-27"]["shift_name"], days["2026-09-27"]["leave"]) == ("白班", False)
    requests = client.get("/api/scheduling/me/requests").json()["requests"]
    assert [(row["status"], bool(row["decided_at"])) for row in requests] == [("approved", True)]
    assert stayer != leaver


def test_rejecting_a_leave_leaves_the_roster_untouched(scheduling_http, monkeypatch):
    """验收 6：驳回只改申请的状态 —— 月历与当日分工一个字不变。"""
    client, _db, accounts = scheduling_http
    admin, _ids, _day_id = _leave_scene(client, accounts, monkeypatch)

    _as_manager(client, admin)
    before_calendar = client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json()
    before_day = client.get("/api/scheduling/day", params={"date": "2026-09-25"}).json()

    _staff_cookie(client, accounts)
    request_id = client.post(
        "/api/scheduling/me/requests", json={"start_date": "2026-09-25"}
    ).json()["request"]["id"]

    _as_manager(client, admin)
    rejected = client.post(f"/api/scheduling/inbox/{request_id}/reject")

    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert bool(rejected.json()["decided_at"])
    assert client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json() == before_calendar
    assert client.get("/api/scheduling/day", params={"date": "2026-09-25"}).json() == before_day
    assert client.get("/api/scheduling/inbox").json()["requests"] == []
    # 驳过的不再回到待办：再批一次是 400 一句人话，不是 500。
    again = client.post(f"/api/scheduling/inbox/{request_id}/approve")
    assert again.status_code == 400
    assert again.json()["detail"] == "这条申请已经处理过了：刷新看看它现在到哪一步"


def test_leave_errors_are_readable_400s(scheduling_http, monkeypatch):
    """提不了的几种情形各说各的 —— 不是一个「参数不合法」打包（跟票 07 一样的口径）。"""
    client, _db, accounts = scheduling_http
    _leave_scene(client, accounts, monkeypatch)
    base = "/api/scheduling/me/requests"

    backwards = client.post(base, json={"start_date": "2026-09-26", "end_date": "2026-09-25"})
    assert backwards.status_code == 400
    assert backwards.json()["detail"] == "结束日期不能早于开始日期：请重新选一遍这几天"

    past = client.post(base, json={"start_date": "2026-09-23"})
    assert past.status_code == 400
    # 员工读到的是「请不了假」，不是店长那句「排班写下的历史不重写」。
    assert past.json()["detail"] == "已经过去的日子请不了假：请从今天起选"

    beyond = client.post(base, json={"start_date": "2026-12-23"})
    assert beyond.status_code == 400
    assert beyond.json()["detail"] == "排班还没铺到那么远：最多请到 2026-12-22"

    long_note = client.post(base, json={"start_date": "2026-09-25", "note": "家" * 51})
    assert long_note.status_code == 400
    assert long_note.json()["detail"] == "事由最多 50 个字：请缩短一点再提交"

    bad_date = client.post(base, json={"start_date": "9-25"})
    assert bad_date.status_code == 400
    assert bad_date.json()["detail"] == "日期格式应该是 YYYY-MM-DD"

    # 日期没给：缺字段、空串、显式 `null` 都落到服务层那道闸上，400 一句中文 ——
    # 而不是 pydantic 的 422（那个 `detail` 是一串英文的字段错误）。
    for payload in ({}, {"start_date": ""}, {"start_date": None}):
        missing = client.post(base, json=payload)
        assert missing.status_code == 400
        assert missing.json()["detail"] == "请先选一个开始日期：请假从哪天开始"

    gone = client.delete(f"{base}/987654")
    assert gone.status_code == 404
    assert gone.json()["detail"] == "这条申请不存在：可能已经被撤回，刷新看看"

    # 别人的申请也是同一个 404：接口不区分「不存在」与「不是你的」。
    _employee_id(accounts, phone="13800138001", name="李四")
    _staff_cookie(client, accounts, phone="13800138001")
    theirs = client.post(base, json={"start_date": "2026-09-26"}).json()["request"]["id"]
    _staff_cookie(client, accounts)
    not_mine = client.delete(f"{base}/{theirs}")
    assert not_mine.status_code == 404
    assert not_mine.json()["detail"] == "这条申请不存在：可能已经被撤回，刷新看看"
    # 提不成的申请一行都没留下（李四那条是别人撤不掉的，不是没提成）。
    assert [row["id"] for row in client.get(base).json()["requests"]] == []


def test_missing_requests_table_is_a_503_that_names_it(scheduling_http, monkeypatch):
    """0008 还没应用：请假那几条 503 并点名脚本；排班本身照常 —— 它不读新表。"""
    client, db, accounts = scheduling_http
    admin, _ids, _day_id = _leave_scene(client, accounts, monkeypatch)

    async def _rename(source, target):
        await db._conn.execute(f"ALTER TABLE {source} RENAME TO {target}")
        await db._conn.commit()

    _run(_rename("scheduling_requests", "scheduling_requests_tmp"))
    try:
        posted = client.post("/api/scheduling/me/requests", json={"start_date": "2026-09-25"})
        assert posted.status_code == 503, posted.text
        assert "0008_scheduling_requests.sql" in posted.json()["detail"]
        assert client.get("/api/scheduling/me/requests").status_code == 503
        # 员工「今天」那页与店长那几页都不读申请表：照常 200。
        assert client.get("/api/scheduling/me").status_code == 200
        _as_manager(client, admin)
        assert client.get("/api/scheduling/inbox").status_code == 503
        assert client.get("/api/scheduling/calendar", params={"month": "2026-09"}).status_code == 200
        assert client.get("/api/scheduling/day", params={"date": "2026-09-25"}).status_code == 200
        assert client.get("/api/scheduling/roster").status_code == 200
    finally:
        _run(_rename("scheduling_requests_tmp", "scheduling_requests"))


# ── 换班申请（票 09）───────────────────────────────────────────────────────


def _swap_scene(client, accounts, monkeypatch):
    """两个人：张三白班、李四夜班（都从今天起每天），然后切到张三的员工 cookie。

    返回 `(管理端会话, 张三 id, 李四 id, 白班 id, 夜班 id)`。
    """
    shifts = {
        shift["name"]: shift["id"]
        for shift in client.get("/api/scheduling/shifts").json()["shifts"]
    }
    day_id, night_id = shifts["白班"], shifts["夜班"]
    first = _employee_id(accounts, phone=PHONE, name=NAME)
    second = _employee_id(accounts, phone="13800138001", name="李四")
    for employee_id, shift_id in ((first, day_id), (second, night_id)):
        saved = client.put(f"/api/scheduling/rules/{employee_id}", json={"cycle": [shift_id]})
        assert saved.status_code == 200, saved.text
    admin = _admin_cookie(client)
    _wire_staff_accounts(monkeypatch, accounts)
    _staff_cookie(client, accounts, phone=PHONE)
    return admin, first, second, day_id, night_id


def test_staff_proposes_a_swap_and_the_peer_answers(scheduling_http, monkeypatch):
    """验收 1/2/3/7：提一条 → 只有对方看得见 → 对方点头才进店长待办。"""
    client, _db, accounts = scheduling_http
    admin, me, peer, day_id, night_id = _swap_scene(client, accounts, monkeypatch)

    # 能找谁换：名单里在上班的别人，只给 id / 姓名 / 职位。
    people = client.get("/api/scheduling/me/colleagues")
    assert people.status_code == 200, people.text
    assert people.json()["employee"] == {"id": me, "name": NAME}
    assert people.json()["colleagues"] == [
        {"id": peer, "name": "李四", "job_title": people.json()["colleagues"][0]["job_title"]}
    ]
    assert "13800138001" not in people.text

    proposed = client.post(
        "/api/scheduling/me/swaps",
        json={"peer_employee_id": peer, "business_date": "2026-09-25", "note": "想换个班"},
    )

    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert body["employee"] == {"id": me, "name": NAME}
    request = body["request"]
    assert (request["kind"], request["status"], request["peer_employee_id"]) == (
        "swap",
        "pending_peer",
        peer,
    )
    assert (request["start_date"], request["end_date"]) == ("2026-09-25", "2026-09-25")
    assert request["decided_at"] is None
    request_id = request["id"]

    # 验收 7：申请人自己看得到「卡在谁那里」，也还撤得回。
    mine = client.get("/api/scheduling/me/requests").json()
    assert mine["incoming"] == []
    assert [(row["id"], row["status"], row["peer_name"]) for row in mine["requests"]] == [
        (request_id, "pending_peer", "李四")
    ]

    # 验收 3：对方还没点，店长的待办里看不到这条（排班也一个字没改）。
    _as_manager(client, admin)
    assert client.get("/api/scheduling/inbox").json()["requests"] == []

    # 验收 2：对方看到的是一张卡片 —— 他那天白班、我那天夜班。
    _staff_cookie(client, accounts, phone="13800138001")
    incoming = client.get("/api/scheduling/me/requests").json()
    assert incoming["requests"] == []
    assert [
        (
            card["id"],
            card["employee_name"],
            card["their_shift_name"],
            card["my_shift_name"],
            card["note"],
        )
        for card in incoming["incoming"]
    ] == [(request_id, NAME, "白班", "夜班", "想换个班")]

    agreed = client.post(f"/api/scheduling/me/swaps/{request_id}/accept")

    assert agreed.status_code == 200, agreed.text
    assert agreed.json()["status"] == "pending_manager"
    assert client.get("/api/scheduling/me/requests").json()["incoming"] == []

    # 两边都点过头了：这才轮到店长，卡片上摊开两个人那天的班。
    _as_manager(client, admin)
    cards = client.get("/api/scheduling/inbox").json()["requests"]
    assert len(cards) == 1
    card = cards[0]
    assert (card["id"], card["kind"], card["employee_name"], card["peer_name"]) == (
        request_id,
        "swap",
        NAME,
        "李四",
    )
    assert card["days"] == [
        {
            "business_date": "2026-09-25",
            "past": False,
            "scheduled": True,
            "current_shift_id": day_id,
            "current_shift_name": "白班",
            "peer_scheduled": True,
            "peer_shift_id": night_id,
            "peer_shift_name": "夜班",
        }
    ]


def test_a_refusal_ends_it_without_the_manager(scheduling_http, monkeypatch):
    """验收 2/6：对方拒绝 → 这件事到此为止，店长看不到，排班一个字不改。"""
    client, _db, accounts = scheduling_http
    admin, _me, _peer, _day_id, _night_id = _swap_scene(client, accounts, monkeypatch)

    _as_manager(client, admin)
    before_calendar = client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json()
    before_day = client.get("/api/scheduling/day", params={"date": "2026-09-25"}).json()

    _staff_cookie(client, accounts)
    request_id = client.post(
        "/api/scheduling/me/swaps",
        json={"peer_employee_id": _peer, "business_date": "2026-09-25"},
    ).json()["request"]["id"]

    _staff_cookie(client, accounts, phone="13800138001")
    refused = client.post(f"/api/scheduling/me/swaps/{request_id}/reject")

    assert refused.status_code == 200, refused.text
    assert refused.json()["status"] == "rejected"
    assert refused.json()["decided_at"]
    # 回过的不能再回，申请人自己去「同意」也不行。
    again = client.post(f"/api/scheduling/me/swaps/{request_id}/accept")
    assert again.status_code == 400
    assert again.json()["detail"] == "这条申请已经处理过了：刷新看看它现在到哪一步"
    _staff_cookie(client, accounts)
    stranger = client.post(f"/api/scheduling/me/swaps/{request_id}/accept")
    assert stranger.status_code == 404
    assert stranger.json()["detail"] == "这条申请不存在：可能已经被撤回，刷新看看"

    _as_manager(client, admin)
    assert client.get("/api/scheduling/inbox").json()["requests"] == []
    assert client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json() == before_calendar
    assert client.get("/api/scheduling/day", params={"date": "2026-09-25"}).json() == before_day
    # 店长也批不动它：它从来没进过待批那一档。
    denied = client.post(f"/api/scheduling/inbox/{request_id}/approve")
    assert denied.status_code == 400
    assert denied.json()["detail"] == "这条申请已经处理过了：刷新看看它现在到哪一步"


def test_approving_a_swap_swaps_the_two_days(scheduling_http, monkeypatch):
    """验收 4/5：批了两个人那天的班对调，责任区跟着各自的新班次走。"""
    client, db, accounts = scheduling_http
    admin, me, peer, day_id, night_id = _swap_scene(client, accounts, monkeypatch)
    board = _zone(db, "案板")
    cold = _zone(db, "凉菜")
    pastry = _zone(db, "面点")
    # 配固定区是店长那扇门的事：先换回管理端 cookie（场景末尾停在员工的 cookie 上）。
    _as_manager(client, admin)
    for employee_id, shift_id, zone_id in (
        (me, day_id, board),
        (me, night_id, cold),
        (peer, day_id, pastry),
        (peer, night_id, board),
    ):
        pinned = client.put(
            f"/api/scheduling/zone-defaults/{employee_id}",
            json={"shift_id": shift_id, "zone_id": zone_id},
        )
        assert pinned.status_code == 200, pinned.text

    _staff_cookie(client, accounts)
    request_id = client.post(
        "/api/scheduling/me/swaps",
        json={"peer_employee_id": peer, "business_date": "2026-09-25"},
    ).json()["request"]["id"]
    _staff_cookie(client, accounts, phone="13800138001")
    assert client.post(f"/api/scheduling/me/swaps/{request_id}/accept").status_code == 200

    _as_manager(client, admin)
    approved = client.post(f"/api/scheduling/inbox/{request_id}/approve")

    assert approved.status_code == 200, approved.text
    assert approved.json()["applied_days"] == ["2026-09-25"]
    assert approved.json()["skipped_days"] == []
    assert client.get("/api/scheduling/inbox").json()["requests"] == []

    # 那天白班上的是李四（在他白班那个区），夜班上是张三（在他夜班那个区）。
    after = client.get("/api/scheduling/day", params={"date": "2026-09-25"}).json()
    groups = {group["shift"]["name"]: group for group in after["groups"]}
    assert [
        (person["name"], person["zone"]) for person in groups["白班"]["people"]
    ] == [("李四", "面点")]
    assert [
        (person["name"], person["zone"]) for person in groups["夜班"]["people"]
    ] == [("张三", "凉菜")]
    assert (groups["白班"]["count"], groups["夜班"]["count"]) == (1, 1)
    # 月历上那天留着「被改过」的手改痕迹：对调是两个人的两行，标记数是 2。
    cells = {
        day["business_date"]: day
        for day in client.get("/api/scheduling/calendar", params={"month": "2026-09"}).json()["days"]
    }
    assert cells["2026-09-25"]["overridden"] == 2

    # 员工各看各的：张三那天换成夜班，李四那天换成白班；前后两天照旧。
    _staff_cookie(client, accounts)
    days = {day["business_date"]: day for day in client.get("/api/scheduling/me").json()["days"]}
    assert (days["2026-09-24"]["shift_name"], days["2026-09-25"]["shift_name"]) == ("白班", "夜班")
    _staff_cookie(client, accounts, phone="13800138001")
    days = {day["business_date"]: day for day in client.get("/api/scheduling/me").json()["days"]}
    assert (days["2026-09-24"]["shift_name"], days["2026-09-25"]["shift_name"]) == ("夜班", "白班")


def test_swap_errors_are_readable_400s(scheduling_http, monkeypatch):
    """提不了、回不了的几种情形各说各的 —— 不是一个「参数不合法」打包。"""
    client, _db, accounts = scheduling_http
    _admin, me, peer, _day_id, _night_id = _swap_scene(client, accounts, monkeypatch)
    base = "/api/scheduling/me/swaps"

    cases = [
        (
            {"peer_employee_id": me, "business_date": "2026-09-25"},
            "换班得找别人：不能跟自己换",
        ),
        ({"business_date": "2026-09-25"}, "请先选一位同事：换班得跟人说好"),
        ({"peer_employee_id": None, "business_date": "2026-09-25"}, "请先选一位同事：换班得跟人说好"),
        ({"peer_employee_id": 987654, "business_date": "2026-09-25"}, "找不到这位同事：刷新一下名单再选"),
        ({"peer_employee_id": peer, "business_date": ""}, "请先选换哪一天"),
        ({"peer_employee_id": peer}, "请先选换哪一天"),
        ({"peer_employee_id": peer, "business_date": "9-25"}, "日期格式应该是 YYYY-MM-DD"),
        (
            {"peer_employee_id": peer, "business_date": "2026-09-23"},
            "已经过去的日子换不了班：请从今天起选",
        ),
        (
            {"peer_employee_id": peer, "business_date": "2026-12-23"},
            "排班还没铺到那么远：最多换到 2026-12-22",
        ),
        (
            {"peer_employee_id": peer, "business_date": "2026-09-25", "note": "换" * 51},
            "事由最多 50 个字：请缩短一点再提交",
        ),
    ]
    for payload, detail in cases:
        with_payload = client.post(base, json=payload)
        assert with_payload.status_code == 400, payload
        assert with_payload.json()["detail"] == detail, payload

    # 还没批准的同事不能选（停用的同理：都不在排班名单里）。
    pending = _run(accounts.register("13800138002", PASSWORD, "赵六"))
    waiting = client.post(
        base, json={"peer_employee_id": pending["id"], "business_date": "2026-09-25"}
    )
    assert waiting.status_code == 400
    assert waiting.json()["detail"] == "这位同事现在不在排班名单里（已停用或还没批准）：换个人吧"

    # 那天自己手上没班：先让店长配规则，而不是提一条换不成的。
    ruleless = _employee_id(accounts, phone="13800138003", name="王五")
    _staff_cookie(client, accounts, phone="13800138003")
    empty = client.post(
        base, json={"peer_employee_id": peer, "business_date": "2026-09-25"}
    )
    assert empty.status_code == 400
    assert empty.json()["detail"] == "那天你手上没有班可换：先让店长给你配上轮转规则"
    assert ruleless != peer

    # 同一天同一个人只挂一条：对方手机上不该出现两条一样的请求。
    _staff_cookie(client, accounts)
    first = client.post(base, json={"peer_employee_id": peer, "business_date": "2026-09-25"})
    assert first.status_code == 200, first.text
    twice = client.post(base, json={"peer_employee_id": peer, "business_date": "2026-09-25"})
    assert twice.status_code == 400
    assert twice.json()["detail"] == "你刚跟这位同事提过这一天的换班：等对方回应，或先撤回那条"

    # 回一条不存在的：跟请假那条一样是 404 一句人话。
    gone = client.post(f"{base}/987654/accept")
    assert gone.status_code == 404
    assert gone.json()["detail"] == "这条申请不存在：可能已经被撤回，刷新看看"
    # 提不成的都没落库（列表里只剩刚提成的那一条）。
    assert [row["status"] for row in client.get("/api/scheduling/me/requests").json()["requests"]] == [
        "pending_peer"
    ]


# 撤掉 0009 那一列的用例，跑完必须原样补回来 —— **索引也要补**：`DROP COLUMN` 会把
# `idx_scheduling_requests_peer` 和 `idx_scheduling_requests_swap_once` 一起带走，
# 而后面还有用例靠那条唯一索引挡住重复提交（补列不补索引 = 悄悄少一道闸）。
_RESTORE_PEER_COLUMN = (
    "ALTER TABLE scheduling_requests ADD COLUMN IF NOT EXISTS peer_employee_id BIGINT",
    "CREATE INDEX IF NOT EXISTS idx_scheduling_requests_peer "
    "ON scheduling_requests (peer_employee_id, status)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduling_requests_swap_once "
    "ON scheduling_requests (employee_id, peer_employee_id, start_date) "
    "WHERE kind = 'swap' AND status IN ('pending_peer', 'pending_manager')",
)


def _restore_peer_column(db):
    async def _restore():
        for statement in _RESTORE_PEER_COLUMN:
            await db._conn.execute(statement)
        await db._conn.commit()

    _run(_restore())


def test_missing_peer_column_is_a_503_that_names_it(scheduling_http, monkeypatch):
    """0009 还没应用：换班提不了（503 点名脚本），请假照常提、照常读。"""
    client, db, accounts = scheduling_http
    admin, me, peer, _day_id, _night_id = _swap_scene(client, accounts, monkeypatch)

    async def _drop(statement):
        await db._conn.execute(statement)
        await db._conn.commit()

    _run(_drop("ALTER TABLE scheduling_requests DROP COLUMN peer_employee_id"))
    try:
        proposed = client.post(
            "/api/scheduling/me/swaps",
            json={"peer_employee_id": peer, "business_date": "2026-09-25"},
        )
        assert proposed.status_code == 503, proposed.text
        assert "0009_scheduling_swap.sql" in proposed.json()["detail"]
        # 「等我回应」与「我提的」都照常读得到（那条读路径退回没有换班的写法）。
        mine = client.get("/api/scheduling/me/requests")
        assert mine.status_code == 200
        assert mine.json()["incoming"] == []
        # 请假完全不靠这一列：提得成、读得到。
        leave = client.post("/api/scheduling/me/requests", json={"start_date": "2026-09-25"})
        assert leave.status_code == 200, leave.text
        assert leave.json()["request"]["status"] == "pending_manager"
        assert [
            row["kind"] for row in client.get("/api/scheduling/me/requests").json()["requests"]
        ] == ["leave"]
        # 店长那边的待办与日历也不受影响（请假那条照常出）。
        _as_manager(client, admin)
        cards = client.get("/api/scheduling/inbox").json()["requests"]
        assert [(card["kind"], card["employee_name"]) for card in cards] == [("leave", NAME)]
        assert client.get("/api/scheduling/day", params={"date": "2026-09-25"}).status_code == 200
        assert me != peer
    finally:
        _restore_peer_column(db)


def test_a_queued_swap_is_unrenderable_without_the_column(scheduling_http, monkeypatch):
    """0009 被撤掉时，**已经排上队**的换班不是「看不见」而是「办不了」：待办与批准都 503
    点名这个脚本，而不是印一张假的卡、也不是批准时抛成 500。"""
    client, db, accounts = scheduling_http
    admin, _me, peer, _day_id, _night_id = _swap_scene(client, accounts, monkeypatch)
    proposed = client.post(
        "/api/scheduling/me/swaps",
        json={"peer_employee_id": peer, "business_date": "2026-09-25", "note": "想换个班"},
    )
    assert proposed.status_code == 200, proposed.text
    request_id = proposed.json()["request"]["id"]
    # 对方点头，这条才进店长待办（挡板在渲染那一步）。
    _staff_cookie(client, accounts, phone="13800138001")
    answered = client.post(f"/api/scheduling/me/swaps/{request_id}/accept")
    assert answered.status_code == 200, answered.text
    # 两条回应路由直接回那条申请本身（没有 `{"request": …}` 这层包装）。
    assert answered.json()["status"] == "pending_manager"

    # 同一队列里再压一条请假（李四提的）：它不靠 0009 那一列，等下用来证明
    # 「一张换班残骸不该把整页待办拖下水」。
    leave = client.post(
        "/api/scheduling/me/requests", json={"start_date": "2026-09-26", "note": "家里有事"}
    )
    assert leave.status_code == 200, leave.text
    leave_id = leave.json()["request"]["id"]

    async def _drop(statement):
        await db._conn.execute(statement)
        await db._conn.commit()

    _run(_drop("ALTER TABLE scheduling_requests DROP COLUMN peer_employee_id"))
    try:
        _as_manager(client, admin)
        inbox = client.get("/api/scheduling/inbox")
        assert inbox.status_code == 503, inbox.text
        assert "0009_scheduling_swap.sql" in inbox.json()["detail"]
        approved = client.post(f"/api/scheduling/inbox/{request_id}/approve")
        assert approved.status_code == 503, approved.text
        assert "0009_scheduling_swap.sql" in approved.json()["detail"]
    finally:
        _restore_peer_column(db)
        # 补列不等于把值补回来（`DROP COLUMN` 连对方一起丢了）：那条已经成了残骸，
        # 摆不上桌、也批不了 —— 但它**不该把整页待办拖下水**：同一队列里李四的请假
        # 还等着批，所以这一页照常开，只是少了那张换班卡。
        _as_manager(client, admin)
        healed = client.get("/api/scheduling/inbox")
        assert healed.status_code == 200, healed.text
        assert [(card["id"], card["kind"]) for card in healed.json()["requests"]] == [
            (leave_id, "leave")
        ]
        # 迁移补回来之后，新提的一条换班照常走完全程（不是「一次坏掉，永远 503」）。
        _staff_cookie(client, accounts, phone=PHONE)
        again = client.post(
            "/api/scheduling/me/swaps",
            json={"peer_employee_id": peer, "business_date": "2026-09-25", "note": None},
        )
        assert again.status_code == 200, again.text
        second_id = again.json()["request"]["id"]
        _staff_cookie(client, accounts, phone="13800138001")
        assert client.post(f"/api/scheduling/me/swaps/{second_id}/accept").status_code == 200
        _as_manager(client, admin)
        approved = client.post(f"/api/scheduling/inbox/{second_id}/approve")
        assert approved.status_code == 200, approved.text
        # 批准这条路由也直接回结果本身（`applied_days` 在顶层）。
        assert approved.json()["applied_days"] == ["2026-09-25"]
