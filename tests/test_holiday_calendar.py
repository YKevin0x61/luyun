#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""法定节假日日历（票 02）：表与表清单 + 一条真请求链路。

判据是**票面那 8 条验收**，逐条落在下面：页面三处清单（`tests/test_spa_page_routes.py`
与前端那两条源码测试里）、表只读登记、以及这一份的 HTTP 行为。

缝的形状照 `tests/test_overtime.py`：薄 FastAPI（auth 路由 + 本票路由）+ 真测试库 +
`TestClient`。**只测外部行为** —— 请求进、JSON 出；不断言内部函数调用、不 mock 服务层。
数据从库里造（一次 PUT 就是一次造数）。
"""

from __future__ import annotations

import asyncio
import unittest

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.attendance_holidays as holidays_module
import api.auth as auth_module
import main as main_module
from config import settings
from database import DatabaseManager
from db_core.schema import ADMIN_READ_ONLY_TABLES, ATTENDANCE_TABLES
from services.app_runtime import AppRuntime, set_runtime
from services.holiday_calendar import MAX_NAME, HolidayCalendar
from services.identity import EmployeeAccounts
from tests.pg_probe import list_tables, scalar, table_columns

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
PATH = "/api/attendance/holidays"


# ── 验收 6：加成性迁移 + 表清单 + 管理端只读 ────────────────────────────────


class HolidayCalendarTableContractTest(unittest.TestCase):
    """验收 6：新表进表名清单，Admin 的数据浏览器里只读（写入口只有节假日日历页）。"""

    def test_new_table_is_registered_read_only(self):
        # 断言的是「这张表在里面」，不是「清单里只有它」：03 的月度考勤台账会往同一个
        # 清单里追加自己的表，写死全集会让别人加一张表就红在这里。
        self.assertIn("statutory_holidays", ATTENDANCE_TABLES)
        for table in ATTENDANCE_TABLES:
            self.assertIn(table, ADMIN_READ_ONLY_TABLES)

        from api.admin import _admin_catalog

        catalog = _admin_catalog()
        for table in ATTENDANCE_TABLES:
            self.assertIn(table, catalog["tables"])
            self.assertEqual(catalog["table_meta"][table]["group"], "hr")
            self.assertTrue(catalog["table_meta"][table]["read_only"])
        groups = {group["key"]: group for group in catalog["groups"]}
        for table in ATTENDANCE_TABLES:
            self.assertIn(table, groups["hr"]["tables"])
        self.assertEqual(groups["hr"]["label"], "人事考勤")

    def test_admin_data_browser_rejects_writes_to_the_calendar(self):
        """只读不是「按钮变灰」：通用写入口真的拒。"""
        from api.admin import _reject_read_only_table_write

        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as caught:
            _reject_read_only_table_write("statutory_holidays")
        self.assertEqual(caught.exception.status_code, 403)

    def test_migration_creates_the_table_with_a_unique_day(self):
        """迁移 0026 真的落库了：表在、日期唯一（唯一的日判据是那条 UNIQUE 索引）。"""
        self.assertIn("statutory_holidays", list_tables())
        columns = table_columns("statutory_holidays")
        self.assertEqual(columns[:2], ["tenant_id", "id"])
        self.assertIn("day", columns)
        self.assertIn("name", columns)

        indexdef = scalar(
            "SELECT indexdef FROM pg_indexes"
            " WHERE schemaname = 'public' AND indexname = 'idx_statutory_holidays_day'"
        )
        self.assertIsNotNone(indexdef, "0026 的日期唯一索引不在库里")
        self.assertIn("UNIQUE", indexdef.upper())
        self.assertIn("day", indexdef)


# ── 验收 7：匿名与员工会话被拒；管理端会话能打 ──────────────────────────────


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
def holidays_http(tmp_path, monkeypatch):
    """一条真实请求链路：auth 路由（建管理端会话）+ 本票路由 + 真测试库。"""
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db)
    # `require_staff_session` 从 `main` 取员工账号服务（同 `tests/test_overtime.py`）。
    monkeypatch.setattr(main_module, "employee_accounts", accounts)

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(holidays_module.router)
    with TestClient(app) as client:
        init = client.post("/api/auth/init", json=ADMIN)
        assert init.status_code == 200, init.text
        login = client.post(
            "/api/auth/login",
            json={"username": ADMIN["username"], "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        yield client, db, accounts
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _staff_cookie(client, accounts, phone=PHONE):
    """换成员工那个 cookie（与管理端是两个名字），登录用的是同一套账号。"""
    employee = _run(accounts.register(phone, PASSWORD, NAME))
    _run(accounts.approve(employee["id"]))
    session_id = _run(accounts.login(phone, PASSWORD))["session_id"]
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, session_id)
    return employee["id"]


def test_every_endpoint_needs_an_admin_session(holidays_http):
    """两扇门各认各的 cookie：匿名 401、员工会话 401、管理端会话 200。"""
    client, _db, accounts = holidays_http

    client.cookies.clear()
    assert client.get(PATH, params={"year": 2026}).status_code == 401
    assert client.put(f"{PATH}/2026-10-01", json={"name": "国庆"}).status_code == 401
    assert client.delete(f"{PATH}/2026-10-01").status_code == 401

    _staff_cookie(client, accounts)
    assert client.get(PATH, params={"year": 2026}).status_code == 401
    assert client.put(f"{PATH}/2026-10-01", json={"name": "国庆"}).status_code == 401
    assert client.delete(f"{PATH}/2026-10-01").status_code == 401

    # 这是夹具里那个管理端会话：三条都通。
    client.cookies.clear()
    login = client.post(
        "/api/auth/login",
        json={"username": ADMIN["username"], "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    assert client.get(PATH, params={"year": 2026}).status_code == 200
    assert client.put(f"{PATH}/2026-10-01", json={"name": "国庆"}).status_code == 200
    assert client.delete(f"{PATH}/2026-10-01").status_code == 200


# ── 验收 2/3/4/5：标记、覆盖、取消，都是请求进 JSON 出 ──────────────────────


def _marked(client, year=2026):
    """这一年被标成法定节假日的日子（从接口读，不查库）。"""
    response = client.get(PATH, params={"year": year})
    assert response.status_code == 200, response.text
    return response.json()["days"]


def test_marking_a_day_records_a_name_visible_in_that_year(holidays_http):
    """验收 2：标记带名称，按年读得到；别的年份看不见它。"""
    client, _db, _accounts = holidays_http

    marked = client.put(f"{PATH}/2026-10-01", json={"name": "国庆"})
    assert marked.status_code == 200, marked.text
    assert marked.json() == {"day": "2026-10-01", "name": "国庆", "created": True}

    client.put(f"{PATH}/2027-01-01", json={"name": "元旦"})
    assert _marked(client, 2026) == [{"day": "2026-10-01", "name": "国庆"}]
    assert _marked(client, 2027) == [{"day": "2027-01-01", "name": "元旦"}]
    assert _marked(client, 2028) == []


def test_marking_the_same_day_twice_overwrites_it(holidays_http):
    """验收 3：同一天重复标记是覆盖那一行，不是新增第二行。"""
    client, _db, _accounts = holidays_http

    client.put(f"{PATH}/2026-10-01", json={"name": "国庆"})
    again = client.put(f"{PATH}/2026-10-01", json={"name": "国庆节"})
    assert again.status_code == 200, again.text
    assert again.json()["created"] is False

    # 第二次标记之后这一天**只有一条**：读回来带名称的那一条就是全部。
    assert _marked(client) == [{"day": "2026-10-01", "name": "国庆节"}]


def test_marking_needs_a_name_of_one_to_twenty_characters(holidays_http):
    """验收 3：名称为空被拒；上限 20 字，超了也拒。"""
    client, _db, _accounts = holidays_http

    for body in ({"name": ""}, {"name": "   "}, {"name": "国" * (MAX_NAME + 1)}):
        rejected = client.put(f"{PATH}/2026-10-01", json=body)
        assert rejected.status_code == 400, (body, rejected.text)
        assert rejected.json()["detail"], "拒绝要说清为什么"
    assert _marked(client) == [], "被拒的标记不该留下任何一行"

    # 边界那一格能标：20 个字正好。
    full = "国" * MAX_NAME
    accepted = client.put(f"{PATH}/2026-10-01", json={"name": full})
    assert accepted.status_code == 200, accepted.text
    assert _marked(client) == [{"day": "2026-10-01", "name": full}]


def test_unmarking_removes_the_day(holidays_http):
    """验收 4：取消之后那天不再是法定节假日；没标的那天取消也不报错。"""
    client, _db, _accounts = holidays_http

    client.put(f"{PATH}/2026-10-01", json={"name": "国庆"})
    removed = client.delete(f"{PATH}/2026-10-01")
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"day": "2026-10-01", "removed": True}
    assert _marked(client) == []

    # 幂等：已经没标的那天再取消一次不是错。
    again = client.delete(f"{PATH}/2026-10-01")
    assert again.status_code == 200, again.text
    assert again.json()["removed"] is False


def test_day_and_year_are_validated(holidays_http):
    """日期与年份写错是 400（中文），不是 500 也不是英文 422。"""
    client, _db, _accounts = holidays_http

    for day in ("2026-10-32", "2026-13-01", "26-10-01", "国庆"):
        bad = client.put(f"{PATH}/{day}", json={"name": "国庆"})
        assert bad.status_code == 400, (day, bad.text)

    for year in (26, 1999, 2101):
        bad = client.get(PATH, params={"year": year})
        assert bad.status_code == 400, (year, bad.text)
    missing = client.get(PATH)
    assert missing.status_code == 400, missing.text


# ── 验收 8：没有写死的年度节假日表，补休只读这张日历 ────────────────────────


def test_nothing_is_a_holiday_until_it_is_marked(holidays_http):
    """验收 8：空的日历上一天都不是法定节假日 —— 代码里没有兜底的年度表。

    这一条同时钉住 03 的补休判据：那天算不算法定节假日**只由这一张日历说了算**，
    连「国庆总是 10-01」这种常识都不许写进代码。
    """
    client, db, _accounts = holidays_http

    assert _marked(client, 2026) == [], "没标过就有了法定节假日 —— 那是写死的年度表"
    assert _marked(client, 2027) == []

    # 服务层那一个读口（03 的补休从这里取当月法定节假日）读的是同一张表。
    calendar = HolidayCalendar(db)
    assert _run(calendar.days_in_month("2026-10")) == []

    client.put(f"{PATH}/2026-10-01", json={"name": "国庆"})
    client.put(f"{PATH}/2026-11-01", json={"name": "临时放假"})
    assert _run(calendar.days_in_month("2026-10")) == ["2026-10-01"]
    assert _run(calendar.days_in_month("2026-11")) == ["2026-11-01"]
    assert _run(calendar.days_in_month("2026-12")) == []
