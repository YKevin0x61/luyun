#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加班与补钟登记·员工那一面（票 01）：提交、看自己的记录、撤回。

主缝 = 服务层 + 真库 + 注入时钟（`.scratch/overtime-and-reminders/spec.md` 的
「Testing Decisions」）：申报窗口由**自然日**决定（不是 06:00 的营业日），所以时钟
必须能拨；HTTP 面另有一小段薄缝，只回答「谁能打、打进去会怎样」。

术语与口径见 `CONTEXT.md` 的「加班与补钟登记」「净时长」，决策理由见
`docs/adr/0100-overtime-and-makeup-ledger.md`。
"""

import asyncio
import tempfile
import unittest
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.admin as admin_module
import api.auth as auth_module
import api.overtime as overtime_module
import main as main_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from db_core.schema import ADMIN_READ_ONLY_TABLES, OVERTIME_TABLES
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.overtime.ledger import (
    MAX_REASON,
    EntryActor,
    OvertimeError,
    OvertimeLedger,
)

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
# 2026-09-24 10:00（北京时）→ 自然日的今天 = 9/24、昨天 = 9/23。
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-24"
YESTERDAY = "2026-09-23"


class OvertimeLedgerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.accounts = EmployeeAccounts(self.db, now=lambda: FIXED_NOW)
        self.ledger = OvertimeLedger(self.db, now=lambda: FIXED_NOW)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _employee(self, phone="13800138000", name="张三"):
        employee = await self.accounts.register(phone, PASSWORD, name)
        await self.accounts.approve(employee["id"])
        return employee

    async def test_employee_submits_overtime_and_sees_it_pending(self):
        """验收 1：提交一笔加班，立刻在「我的记录」里看到，状态是待审批。"""
        employee = await self._employee()
        actor = EntryActor(employee_id=employee["id"])

        created = await self.ledger.submit(actor, TODAY, 13, "中秋加班")

        self.assertEqual(created["status"], "pending")
        self.assertEqual(created["entry_date"], TODAY)
        self.assertEqual(created["half_hours"], 13)
        self.assertEqual(created["kind"], "overtime")
        self.assertEqual(created["reason"], "中秋加班")

        mine = await self.ledger.list_mine(employee["id"])
        self.assertEqual([row["id"] for row in mine["entries"]], [created["id"]])
        self.assertEqual(mine["today"], TODAY)
        self.assertEqual(mine["yesterday"], YESTERDAY)

    async def test_makeup_is_the_same_ledger_with_a_negative_hours(self):
        """验收 1：补钟记负数 —— 加班与补钟是同一张台账，**符号就是类型**（不存 kind）。"""
        employee = await self._employee()
        actor = EntryActor(employee_id=employee["id"])

        overtime = await self.ledger.submit(actor, TODAY, 13, "中秋加班")
        makeup = await self.ledger.submit(actor, YESTERDAY, -4, "前天早退两小时")

        self.assertEqual(overtime["kind"], "overtime")
        self.assertEqual(overtime["hours"], 6.5)
        self.assertEqual(makeup["kind"], "makeup")
        self.assertEqual(makeup["half_hours"], -4)
        self.assertEqual(makeup["hours"], -2)

        mine = await self.ledger.list_mine(employee["id"])
        # 新的在前：今天那条排在昨天那条前面。
        self.assertEqual(
            [row["id"] for row in mine["entries"]], [overtime["id"], makeup["id"]]
        )

    async def test_employee_can_only_report_today_and_yesterday(self):
        """验收 3：员工只能报今天与昨天（自然日），前天或更早被拒。"""
        employee = await self._employee()
        actor = EntryActor(employee_id=employee["id"])

        self.assertEqual(
            (await self.ledger.submit(actor, YESTERDAY, 2, "昨天加班"))["entry_date"],
            YESTERDAY,
        )
        for early in ("2026-09-22", "2026-08-01"):
            with self.assertRaises(OvertimeError) as caught:
                await self.ledger.submit(actor, early, 2, "前天加班")
            self.assertEqual(caught.exception.code, "past_window")

    async def test_backfill_window_follows_the_submitter(self):
        """窗口按**提交人**判：有能力的店长与超管可补录任意过去日期，未来日期谁都收。

        这一条只走服务层：票 01 的 HTTP 面还没有店长那条路（票 04 才开），但判据
        必须一次写对 —— 它是同一个 `submit()` 里的一个分支。
        """
        employee = await self._employee()
        manager = await self._employee(phone="13800138001", name="李四")
        backfiller = EntryActor(employee_id=manager["id"], can_backfill=True)

        backfilled = await self.ledger.submit(
            backfiller, "2026-08-01", 8, "上月忘登的加班", target_employee_id=employee["id"]
        )
        self.assertEqual(backfilled["entry_date"], "2026-08-01")
        self.assertEqual(backfilled["employee_id"], employee["id"])
        # 代录记的是**代录人**，不是被代录的那个人。
        self.assertEqual(backfilled["created_by"], f"staff:{manager['id']}")

        by_super = await self.ledger.submit(
            EntryActor(), "2026-07-15", -3, "上月补钟", target_employee_id=employee["id"]
        )
        self.assertEqual(by_super["created_by"], "super")

        for actor in (EntryActor(employee_id=employee["id"]), backfiller, EntryActor()):
            with self.assertRaises(OvertimeError) as caught:
                await self.ledger.submit(
                    actor, "2026-09-25", 2, "明天加班", target_employee_id=employee["id"]
                )
            self.assertEqual(caught.exception.code, "future_day")

    async def test_mine_shows_only_my_own_entries(self):
        """验收 5 的前半：员工会话读到的只有自己那几笔。"""
        me = await self._employee()
        other = await self._employee(phone="13800138001", name="李四")

        mine = await self.ledger.submit(EntryActor(employee_id=me["id"]), TODAY, 3, "我加班")
        await self.ledger.submit(EntryActor(employee_id=other["id"]), TODAY, 5, "别人加班")

        rows = (await self.ledger.list_mine(me["id"]))["entries"]
        self.assertEqual([row["id"] for row in rows], [mine["id"]])

    async def test_hours_shape_is_half_hour_steps_within_the_cap(self):
        """验收 2：时长按 0.5 小时加减；0 拒、超过 ±12 小时拒、事由必填、日期要真日期。"""
        employee = await self._employee()
        actor = EntryActor(employee_id=employee["id"])

        # 0.5 的整数倍都收：1 = 半小时、3 = 1.5 小时、±24 = ±12 小时（上限本身合法）。
        for half_hours in (1, 3, -1, 13, 24, -24):
            entry = await self.ledger.submit(actor, TODAY, half_hours, "半步也算加班")
            self.assertEqual(entry["half_hours"], half_hours)

        for bad, code in (
            (0, "zero_half_hours"),
            (25, "hours_too_large"),
            (-25, "hours_too_large"),
            (2.5, "invalid_half_hours"),  # 1.25 小时不是 0.5 的整数倍
            ("abc", "invalid_half_hours"),
            (None, "invalid_half_hours"),
        ):
            with self.assertRaises(OvertimeError) as caught:
                await self.ledger.submit(actor, TODAY, bad, "半步也算加班")
            self.assertEqual(caught.exception.code, code, bad)

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.submit(actor, TODAY, 2, "   ")
        self.assertEqual(caught.exception.code, "missing_reason")

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.submit(actor, TODAY, 2, "字" * (MAX_REASON + 1))
        self.assertEqual(caught.exception.code, "reason_too_long")

        for bad_day in ("2026-9-24", "20260924", "", None, "2026-02-30"):
            with self.assertRaises(OvertimeError) as caught:
                await self.ledger.submit(actor, bad_day, 2, "日期打错了")
            self.assertEqual(caught.exception.code, "invalid_date", bad_day)

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.submit(
                EntryActor(can_backfill=True), TODAY, 2, "查无此人", target_employee_id=999999
            )
        self.assertEqual(caught.exception.code, "unknown_employee")

    async def test_employee_cancels_own_pending_entry(self):
        """验收 4：撤回自己待审批的那笔 —— 状态变已撤回，管理端的待办里就没有它了。"""
        employee = await self._employee()
        entry = await self.ledger.submit(
            EntryActor(employee_id=employee["id"]), TODAY, 4, "临时加班"
        )

        cancelled = await self.ledger.cancel(employee["id"], entry["id"])

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNotNone(cancelled["decided_at"])
        # 撤回不是删除：记录还在，员工自己的列表里看得见它已经撤回了。
        rows = (await self.ledger.list_mine(employee["id"]))["entries"]
        self.assertEqual(
            [(row["id"], row["status"]) for row in rows], [(entry["id"], "cancelled")]
        )

    async def test_cancel_only_works_on_my_own_pending_entry(self):
        """验收 5：拿别人的 id 撤回必须失败；撤过一次的不能再撤。"""
        me = await self._employee()
        other = await self._employee(phone="13800138001", name="李四")
        mine = await self.ledger.submit(EntryActor(employee_id=me["id"]), TODAY, 4, "我的加班")

        # 别人的登记一律当作「不存在」：不告诉调用方「它在，但不是你的」。
        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.cancel(other["id"], mine["id"])
        self.assertEqual(caught.exception.code, "unknown_entry")

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.cancel(me["id"], 999999)
        self.assertEqual(caught.exception.code, "unknown_entry")

        # 状态机只往前走：撤过一次就不能再撤。
        await self.ledger.cancel(me["id"], mine["id"])
        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.cancel(me["id"], mine["id"])
        self.assertEqual(caught.exception.code, "entry_not_pending")

    async def _approve_raw(self, entry_id: int) -> None:
        """直接把状态标成已批准 —— 票 02 才有点头的地方，这一票只借它的结果造前置状态。"""
        stamp = FIXED_NOW.isoformat()
        await self.db._conn.execute(
            "UPDATE overtime_entries SET status = 'approved', decided_at = ?, updated_at = ?"
            " WHERE id = ?",
            (stamp, stamp, int(entry_id)),
        )
        await self.db._conn.commit()

    async def test_monthly_summary_splits_pending_from_approved(self):
        """员工端「每个月的加班时长」：净时长 = 加班与补钟相抵，待审批与已批准分开。"""
        employee = await self._employee()
        actor = EntryActor(employee_id=employee["id"])
        approved = await self.ledger.submit(actor, YESTERDAY, 13, "已批的加班")
        await self._approve_raw(approved["id"])
        await self.ledger.submit(actor, TODAY, -4, "待批的补钟")
        # 上个月那笔只能由管理端补录（员工本人的窗口够不着它）—— 顺带钉住
        # 「代录的登记进被代录那个人的月度合计」。
        await self.ledger.submit(
            EntryActor(), "2026-08-31", 6, "上个月那笔", target_employee_id=employee["id"]
        )

        mine = await self.ledger.list_mine(employee["id"])
        summary = mine["summary"]
        self.assertEqual(summary["month"], "2026-09")
        self.assertEqual(summary["approved"]["overtime_half_hours"], 13)
        self.assertEqual(summary["approved"]["makeup_half_hours"], 0)
        self.assertEqual(summary["approved"]["net_half_hours"], 13)
        self.assertEqual(summary["pending"]["net_half_hours"], -4)
        # 上个月那笔不混进本月，但换个月份问得出来。
        august = (await self.ledger.list_mine(employee["id"], "2026-08"))["summary"]
        self.assertEqual(august["month"], "2026-08")
        self.assertEqual(august["pending"]["net_half_hours"], 6)
        self.assertEqual(august["approved"]["net_half_hours"], 0)

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.list_mine(employee["id"], "2026-9")
        self.assertEqual(caught.exception.code, "invalid_month")


class OvertimeTableContractTest(unittest.TestCase):
    """验收 7：新表进表名清单，Admin 的数据浏览器里只读（写入口在 HTTP 那一侧钉）。"""

    def test_new_tables_are_registered_read_only(self):
        self.assertEqual(set(OVERTIME_TABLES), {"overtime_entries"})
        for table in OVERTIME_TABLES:
            self.assertIn(table, ADMIN_READ_ONLY_TABLES)

        from api.admin import _admin_catalog

        catalog = _admin_catalog()
        for table in OVERTIME_TABLES:
            self.assertIn(table, catalog["tables"])
            self.assertEqual(catalog["table_meta"][table]["group"], "overtime")
            self.assertTrue(catalog["table_meta"][table]["read_only"])
        groups = {group["key"]: group for group in catalog["groups"]}
        self.assertEqual(set(groups["overtime"]["tables"]), set(OVERTIME_TABLES))
        self.assertEqual(groups["overtime"]["label"], "人事")


# ── HTTP 薄缝：谁能打、打进去会怎样 ──────────────────────────────────────────
#
# 一条真实请求链路（照 `tests/test_scheduling_api.py` 的薄缝）：路由里现造的
# `OvertimeLedger(db)` 用默认时钟，而这一份断言全围着 2026-09-24 写，所以把
# `__init__` 的 `now=` 钉住 —— 真钟一走过零点，写死的今天 / 昨天就整片错位。


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
def overtime_http(tmp_path, monkeypatch):
    real_init = OvertimeLedger.__init__

    def _pinned_init(self, *args, **kwargs):
        kwargs.setdefault("now", lambda: FIXED_NOW)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(OvertimeLedger, "__init__", _pinned_init)

    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    # `require_staff_session` 从 `main` 取员工账号服务（同 `tests/test_scheduling_api.py`）。
    monkeypatch.setattr(main_module, "employee_accounts", accounts)

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(admin_module.router)
    app.include_router(overtime_module.router)
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
    settings.DATABASE_DIR = old


def _employee_id(accounts, phone=PHONE, name=NAME):
    employee = _run(accounts.register(phone, PASSWORD, name))
    _run(accounts.approve(employee["id"]))
    return employee["id"]


def _staff_cookie(client, accounts, phone=PHONE):
    """换成员工那个 cookie（跟管理端是两个名字），登录用的是同一套账号。"""
    session_id = _run(accounts.login(phone, PASSWORD))["session_id"]
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, session_id)
    return session_id


def test_every_endpoint_needs_a_staff_session(overtime_http):
    """两扇门各认各的 cookie：匿名一律 401，管理端会话也不是员工那扇门的钥匙。"""
    client, _db, _accounts = overtime_http
    body = {"entry_date": TODAY, "half_hours": 2, "reason": "加班"}

    # fixture 里此刻是管理端会话。
    assert client.get("/api/overtime/me").status_code == 401
    assert client.post("/api/overtime/me", json=body).status_code == 401
    assert client.delete("/api/overtime/me/1").status_code == 401

    client.cookies.clear()
    assert client.get("/api/overtime/me").status_code == 401
    assert client.post("/api/overtime/me", json=body).status_code == 401
    assert client.delete("/api/overtime/me/1").status_code == 401


def test_staff_submits_sees_and_cancels(overtime_http):
    """验收 1/4/6：提交两种登记 → 我的记录里看到 → 撤回自己待审批的那笔。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    overtime = client.post(
        "/api/overtime/me",
        json={"entry_date": YESTERDAY, "half_hours": 13, "reason": "中秋加班"},
    )
    assert overtime.status_code == 200, overtime.text
    entry = overtime.json()["entry"]
    assert (
        entry["entry_date"],
        entry["half_hours"],
        entry["kind"],
        entry["status"],
    ) == (YESTERDAY, 13, "overtime", "pending")

    makeup = client.post(
        "/api/overtime/me",
        json={"entry_date": TODAY, "half_hours": -3, "reason": "迟到半小时"},
    )
    assert makeup.status_code == 200, makeup.text
    assert makeup.json()["entry"]["kind"] == "makeup"

    mine = client.get("/api/overtime/me")
    assert mine.status_code == 200, mine.text
    payload = mine.json()
    # 新的在前（今天那笔排在昨天那笔前面），每笔带日期 / 带符号时长 / 事由 / 状态。
    assert [row["id"] for row in payload["entries"]] == [
        makeup.json()["entry"]["id"],
        entry["id"],
    ]
    assert payload["entries"][0]["reason"] == "迟到半小时"
    assert payload["summary"]["pending"]["net_half_hours"] == 10

    cancelled = client.delete(f"/api/overtime/me/{entry['id']}")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["entry"]["status"] == "cancelled"
    assert client.delete("/api/overtime/me/999999").status_code == 404


def test_staff_responses_carry_no_money(overtime_http):
    """验收 5 的边界：员工门任何响应里都没有底薪与金额字段（0098 那条的延伸）。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    created = client.post(
        "/api/overtime/me",
        json={"entry_date": TODAY, "half_hours": 4, "reason": "下午加班"},
    )
    assert created.status_code == 200, created.text

    for response in (client.get("/api/overtime/me"), created):
        for forbidden in ("base_salary", "salary", "amount", "money", "pay"):
            assert forbidden not in response.text, f"员工门不得下发 {forbidden}"


def test_rejections_are_chinese_400(overtime_http):
    """窗口与形状被拒时是 400 + 一句能照做的中文（不是英文 422）。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    def submit(**overrides):
        body = {"entry_date": TODAY, "half_hours": 2, "reason": "加班"}
        body.update(overrides)
        return client.post("/api/overtime/me", json=body)

    too_early = submit(entry_date="2026-09-22")
    assert too_early.status_code == 400
    assert too_early.json()["detail"] == "只能登记今天和昨天的加班：更早的请找店长补录"

    zero = submit(half_hours=0)
    assert zero.status_code == 400
    assert zero.json()["detail"] == "时长不能是 0：请用 + / − 按钮调出这笔的时长"

    too_big = submit(half_hours=25)
    assert too_big.status_code == 400
    assert too_big.json()["detail"] == "单笔最多 12 小时：超过了请分成两笔"

    no_reason = submit(reason="   ")
    assert no_reason.status_code == 400
    assert no_reason.json()["detail"] == "请写一句事由：这笔加班 / 补钟是干什么的"

    tomorrow = submit(entry_date="2026-09-25")
    assert tomorrow.status_code == 400
    assert tomorrow.json()["detail"] == "还没到的日子登不了：加班记的是已经发生的事"


def test_generic_admin_write_path_rejects_the_new_table(overtime_http):
    """验收 7 的后半：通用写入口对这张表 403（只读不是只写在页面上）。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    created = client.post(
        "/api/overtime/me",
        json={"entry_date": TODAY, "half_hours": 2, "reason": "加班"},
    )
    assert created.status_code == 200, created.text
    entry_id = created.json()["entry"]["id"]

    login = client.post(
        "/api/auth/login",
        json={"username": ADMIN["username"], "password": ADMIN["password"]},
    )
    assert login.status_code == 200, login.text
    # 登录本身就把管理端 cookie 放进了 cookie jar（员工那个也还在，两套并存是正常的）。

    blocked = client.delete(f"/api/admin/tables/overtime_entries/rows/{entry_id}")
    assert blocked.status_code == 403, blocked.text
