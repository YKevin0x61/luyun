#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补钟用存假抵扣（ADR 0124）：上限、封账锁、作废冲突、净时长与存假口径。

主缝与 `tests/test_overtime.py` 同一条：服务层 + 真库 + 注入时钟。这里多一层
`AttendanceStore`（存假余额与封账）—— 抵扣是跨那两个域的事，只测一边就测不到
「存假真的少了 / 净时长真的加回来了」。

口径（用户 2026-10-31 裁定）：8.5 小时 = 1 天、只能整天；归补钟登记月；只能抵未封账
的月；补钟与存假两头封顶、不能把存假抵成负数；封账前超管可作废、封账后锁死；已经
抵过的补钟单不能作废。
"""

import asyncio
import tempfile
import unittest
from calendar import monthrange
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.admin as admin_module
import api.attendance as attendance_module
import api.auth as auth_module
import api.overtime as overtime_module
import main as main_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.attendance import AttendanceStore
from services.hygiene.accounts import EmployeeAccounts
from services.overtime.ledger import EntryActor, OvertimeError, OvertimeLedger

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
# 2026-09-24 10:00（北京时）：9 月还没过完，9 月的账封不了；8 月可以封。
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-24"
AUGUST_DAY = "2026-08-10"


def _get_loop():
    """同 `tests/test_overtime.py` 的那一份：HTTP 薄缝与 `_run` 共用一个 loop。"""
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


class DeductionLedgerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.accounts = EmployeeAccounts(self.db, now=lambda: FIXED_NOW)
        self.ledger = OvertimeLedger(self.db, now=lambda: FIXED_NOW)
        self.attendance = AttendanceStore(self.db, now=lambda: FIXED_NOW)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _employee(self, phone=PHONE, name=NAME, salary=5100):
        employee = await self.accounts.register(phone, PASSWORD, name)
        await self.accounts.approve(employee["id"])
        if salary is not None:
            await self.accounts.update_fields(
                employee["id"], base_salary=salary, hire_date="2024-03-01"
            )
        return employee

    async def _approve_makeup(self, employee_id: int, day: str, hours: float) -> dict:
        """造一笔**已批准**的补钟（`hours` 是正数；台账里存的是半小时数，取负）。"""
        entry = await self.ledger.submit(
            EntryActor(),
            day,
            -int(round(float(hours) * 2)),
            "补钟",
            target_employee_id=employee_id,
        )
        return await self.ledger.approve(EntryActor(), entry["id"])

    async def _enough_leave_in(self, employee_id: int, month: str, days: float = 5.0) -> float:
        """给**任意月份**造出「够扣的存假」：直接写那一行的人工期初。

        `set_manual_columns` 只写「本月」那一行（考勤表页的口径），所以测历史月（8 月）
        要绕过它 —— 抵扣单只能归到补钟登记月，8 月的账得在 8 月那一行上有余额。
        """
        start = datetime.strptime(f"{month}-01", "%Y-%m-%d").date()
        days_in = monthrange(start.year, start.month)[1]
        stamp = FIXED_NOW.isoformat()
        await self.attendance._conn.execute(
            """INSERT INTO attendance_ledger
               (tenant_id, employee_id, "month", opening_balance, created_at, updated_at)
               VALUES (1, ?, ?, ?, ?, ?)
               ON CONFLICT (tenant_id, employee_id, "month") DO UPDATE
                 SET opening_balance = excluded.opening_balance""",
            (employee_id, month, float(days) + days_in, stamp, stamp),
        )
        data = await self.attendance.leave_summary(employee_id, month)
        return float(data["leave"]["balance"])

    async def _enough_leave(self, employee_id: int, days: float = 5.0) -> float:
        """用期初余额造出「够扣的存假」：9 月 30 天，期初给 `days + 30` 天即余额 = days。

        新人零排班时公式值是 `0 − 30 = −30`（欠班），直接拿来测「存假够」是测反了。
        """
        await self.attendance.set_manual_columns(
            employee_id, {"opening_balance": float(days) + 30.0}
        )
        data = await self.attendance.leave_summary(employee_id, "2026-09")
        return float(data["leave"]["balance"])

    # ── 上限与形状 ─────────────────────────────────────────────────────────

    async def test_capacity_需要补钟与存假两头都够(self):
        employee = await self._employee()
        await self._enough_leave(employee["id"], days=5)

        # 只有 6 小时补钟：不够一天。
        await self._approve_makeup(employee["id"], TODAY, 6)
        capacity = await self.ledger.deduction_capacity(employee["id"], "2026-09")
        self.assertEqual(capacity["max_days_by_makeup"], 0)
        self.assertEqual(capacity["max_days"], 0)

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.submit_deduction(
                EntryActor(employee_id=employee["id"]), "2026-09", 1
            )
        self.assertEqual(caught.exception.code, "deduction_exceeds_makeup")

    async def test_capacity_存假不够时不能抵成负数(self):
        employee = await self._employee()
        await self.attendance.set_manual_columns(
            employee["id"], {"opening_balance": 30.0}
        )  # pool 30 − 在岗 30 = 0 天
        await self._approve_makeup(employee["id"], TODAY, 8.5)

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.submit_deduction(
                EntryActor(employee_id=employee["id"]), "2026-09", 1
            )
        self.assertEqual(caught.exception.code, "deduction_exceeds_leave")

    async def test_submit_validates_days_and_month(self):
        employee = await self._employee()
        await self._enough_leave(employee["id"], days=5)
        actor = EntryActor(employee_id=employee["id"])
        for bad in (0, -1, 1.5, "x", True):
            with self.assertRaises(OvertimeError) as caught:
                await self.ledger.submit_deduction(actor, "2026-09", bad)
            self.assertEqual(caught.exception.code, "invalid_days")
        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.submit_deduction(actor, "2026-9", 1)
        self.assertEqual(caught.exception.code, "invalid_month")

    async def test_离职员工不能提抵扣(self):
        employee = await self._employee()
        await self._enough_leave(employee["id"], days=5)
        await self._approve_makeup(employee["id"], TODAY, 8.5)
        await self.accounts.resign(employee["id"], "2026-09-20")

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.submit_deduction(
                EntryActor(employee_id=employee["id"]), "2026-09", 1
            )
        self.assertEqual(caught.exception.code, "employee_resigned")

    # ── 批准之后：存假少了、净时长加回来了 ─────────────────────────────────

    async def test_approve_moves_one_day_and_restores_net_hours(self):
        employee = await self._employee()
        balance_before = await self._enough_leave(employee["id"], days=5)
        await self._approve_makeup(employee["id"], TODAY, 8.5)

        deduction = await self.ledger.submit_deduction(
            EntryActor(employee_id=employee["id"]), "2026-09", 1
        )
        self.assertEqual(deduction["status"], "pending")
        # 待批的抵扣一分都不算：存假没少、净时长还是 −8.5。
        leave = (await self.attendance.leave_summary(employee["id"], "2026-09"))["leave"]
        self.assertAlmostEqual(float(leave["balance"]), balance_before)
        summary = (await self.ledger.list_mine(employee["id"]))["summary"]
        self.assertEqual(summary["approved"]["net_half_hours"], -17)

        approved = await self.ledger.approve_deduction(EntryActor(), deduction["id"])
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["days"], 1)
        self.assertEqual(approved["half_hours"], 17)
        self.assertEqual(approved["kind"], "leave_deduction")

        leave = (await self.attendance.leave_summary(employee["id"], "2026-09"))["leave"]
        self.assertAlmostEqual(float(leave["balance"]), balance_before - 1)
        self.assertEqual(leave["deducted_days"], 1)
        summary = (await self.ledger.list_mine(employee["id"]))["summary"]
        # 净时长 = 0 − 17 + 17 = 0：那 8.5 小时按休假算，不再欠班。
        self.assertEqual(summary["approved"]["net_half_hours"], 0)
        self.assertEqual(summary["approved"]["deducted_days"], 1)

    async def test_cannot_approve_own_and_reject_needs_a_reason(self):
        employee = await self._employee()
        await self._enough_leave(employee["id"], days=5)
        await self._approve_makeup(employee["id"], TODAY, 8.5)
        deduction = await self.ledger.submit_deduction(
            EntryActor(employee_id=employee["id"]), "2026-09", 1
        )

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.approve_deduction(
                EntryActor(employee_id=employee["id"]), deduction["id"]
            )
        self.assertEqual(caught.exception.code, "cannot_self_accept")

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.reject_deduction(EntryActor(), deduction["id"], "  ")
        self.assertEqual(caught.exception.code, "missing_reject_reason")

        rejected = await self.ledger.reject_deduction(
            EntryActor(), deduction["id"], "这个月不用抵"
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["reject_reason"], "这个月不用抵")

    async def test_cancel_only_own_pending(self):
        employee = await self._employee()
        await self._enough_leave(employee["id"], days=5)
        await self._approve_makeup(employee["id"], TODAY, 8.5)
        deduction = await self.ledger.submit_deduction(
            EntryActor(employee_id=employee["id"]), "2026-09", 1
        )

        other = await self._employee(phone="13900139000", name="李四")
        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.cancel_deduction(other["id"], deduction["id"])
        self.assertEqual(caught.exception.code, "unknown_deduction")

        cancelled = await self.ledger.cancel_deduction(employee["id"], deduction["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.cancel_deduction(employee["id"], deduction["id"])
        self.assertEqual(caught.exception.code, "deduction_not_pending")

    # ── 封账 ───────────────────────────────────────────────────────────────

    async def test_sealed_month_cannot_be_deducted(self):
        employee = await self._employee()
        await self._enough_leave_in(employee["id"], "2026-08", days=2)
        await self._approve_makeup(employee["id"], AUGUST_DAY, 8.5)
        sealed = await self.attendance.seal("2026-08")
        self.assertEqual(sealed["sealed_employees"], 1)

        actor = EntryActor(employee_id=employee["id"])
        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.submit_deduction(actor, "2026-08", 1)
        self.assertEqual(caught.exception.code, "month_sealed")

        capacity = await self.ledger.deduction_capacity(employee["id"], "2026-08")
        self.assertTrue(capacity["sealed"])
        self.assertEqual(capacity["max_days"], 0)

    async def test_void_deduction_locked_after_seal(self):
        employee = await self._employee()
        await self._enough_leave_in(employee["id"], "2026-08", days=2)
        await self._approve_makeup(employee["id"], AUGUST_DAY, 8.5)
        deduction = await self.ledger.submit_deduction(
            EntryActor(employee_id=employee["id"]), "2026-08", 1
        )
        await self.ledger.approve_deduction(EntryActor(), deduction["id"])
        await self.attendance.seal("2026-08")

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.void_deduction(EntryActor(), deduction["id"])
        self.assertEqual(caught.exception.code, "month_sealed")

    async def test_seal_writes_the_balance_with_deductions_applied(self):
        """封账写下的余额必须已经减过抵扣（否则封完账抵扣就凭空消失）。"""
        employee = await self._employee()
        await self._enough_leave_in(employee["id"], "2026-08", days=2)
        # 17 小时 = 2 天；单笔上限 12 小时，拆成两笔 8.5。
        await self._approve_makeup(employee["id"], AUGUST_DAY, 8.5)
        await self._approve_makeup(employee["id"], AUGUST_DAY, 8.5)
        before = (await self.attendance.leave_summary(employee["id"], "2026-08"))["leave"]
        self.assertEqual(before["deducted_days"], 0)

        deduction = await self.ledger.submit_deduction(
            EntryActor(employee_id=employee["id"]), "2026-08", 2
        )
        await self.ledger.approve_deduction(EntryActor(), deduction["id"])
        await self.attendance.seal("2026-08")

        # 封账之后读 8 月：手填/封账值就是含抵扣的那个数。
        after = (await self.attendance.leave_summary(employee["id"], "2026-08"))["leave"]
        self.assertAlmostEqual(float(after["balance"]), float(before["balance"]) - 2)
        self.assertFalse(
            (await self.attendance.leave_summary(employee["id"], "2026-08"))["provisional"]
        )
        # 9 月的「上月存假」读的就是封下的那个数（抵扣已经结转过去）。
        september = (await self.attendance.leave_summary(employee["id"], "2026-09"))[
            "leave"
        ]
        self.assertAlmostEqual(
            float(september["previous_balance"]), float(before["balance"]) - 2
        )

    # ── 作废冲突 ───────────────────────────────────────────────────────────

    async def test_voiding_a_makeup_that_is_used_by_deduction_is_refused(self):
        employee = await self._employee()
        await self._enough_leave(employee["id"], days=5)
        makeup = await self._approve_makeup(employee["id"], TODAY, 8.5)
        deduction = await self.ledger.submit_deduction(
            EntryActor(employee_id=employee["id"]), "2026-09", 1
        )
        await self.ledger.approve_deduction(EntryActor(), deduction["id"])

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.void(EntryActor(), makeup["id"])
        self.assertEqual(caught.exception.code, "makeup_in_use")

        # 先作废抵扣单，再作废补钟就通了（存假也回来了）。
        voided = await self.ledger.void_deduction(EntryActor(), deduction["id"])
        self.assertEqual(voided["status"], "voided")
        leave = (await self.attendance.leave_summary(employee["id"], "2026-09"))["leave"]
        self.assertEqual(leave["deducted_days"], 0)
        await self.ledger.void(EntryActor(), makeup["id"])

    async def test_unused_makeup_can_still_be_voided(self):
        """没被抵扣的补钟照旧能作废（别把那道闸修成「所有补钟都不能动」）。"""
        employee = await self._employee()
        await self._enough_leave(employee["id"], days=5)
        makeup = await self._approve_makeup(employee["id"], TODAY, 4)

        voided = await self.ledger.void(EntryActor(), makeup["id"])
        self.assertEqual(voided["status"], "voided")


# ── HTTP 面：两扇门各认各的 cookie ──────────────────────────────────────────


@pytest.fixture()
def deduction_http(monkeypatch, tmp_path):
    real_overtime_init = OvertimeLedger.__init__
    real_attendance_init = AttendanceStore.__init__

    def _pinned_overtime_init(self, *args, **kwargs):
        kwargs.setdefault("now", lambda: FIXED_NOW)
        return real_overtime_init(self, *args, **kwargs)

    def _pinned_attendance_init(self, *args, **kwargs):
        kwargs.setdefault("now", lambda: FIXED_NOW)
        return real_attendance_init(self, *args, **kwargs)

    monkeypatch.setattr(OvertimeLedger, "__init__", _pinned_overtime_init)
    monkeypatch.setattr(AttendanceStore, "__init__", _pinned_attendance_init)

    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    monkeypatch.setattr(main_module, "employee_accounts", accounts)

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(admin_module.router)
    app.include_router(overtime_module.router)
    app.include_router(attendance_module.router)
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


def _employee_id(accounts, phone=PHONE, name=NAME, salary=5100):
    employee = _run(accounts.register(phone, PASSWORD, name))
    _run(accounts.approve(employee["id"]))
    if salary is not None:
        _run(
            accounts.update_fields(
                employee["id"], base_salary=salary, hire_date="2024-03-01"
            )
        )
    return employee["id"]


def _staff_cookie(client, accounts, phone=PHONE):
    session_id = _run(accounts.login(phone, PASSWORD))["session_id"]
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, session_id)


def test_staff_reads_own_leave_and_applies_for_deduction(deduction_http):
    """/api/attendance/me 只回自己；/api/overtime/me 带可抵天数；申请进待批。"""
    client, db, accounts = deduction_http
    employee_id = _employee_id(accounts)
    attendance = AttendanceStore(db, now=lambda: FIXED_NOW)
    _run(attendance.set_manual_columns(employee_id, {"opening_balance": 35.0}))
    ledger = OvertimeLedger(db, now=lambda: FIXED_NOW)
    entry = _run(
        ledger.submit(EntryActor(), TODAY, -17, "补钟", target_employee_id=employee_id)
    )
    _run(ledger.approve(EntryActor(), entry["id"]))

    _staff_cookie(client, accounts)
    mine = client.get("/api/attendance/me?month=2026-09")
    assert mine.status_code == 200, mine.text
    payload = mine.json()
    assert payload["employee"]["id"] == employee_id
    assert payload["month"] == "2026-09"
    assert payload["leave"]["deducted_days"] == 0
    assert payload["provisional"] is True

    overtime = client.get("/api/overtime/me")
    assert overtime.status_code == 200, overtime.text
    capacity = overtime.json()["deduction_capacity"]
    assert capacity["max_days"] == 1

    applied = client.post(
        "/api/overtime/me/deductions", json={"month": "2026-09", "days": 1}
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["deduction"]["status"] == "pending"

    # 超过上限：这个月只剩 1 天可抵。
    too_many = client.post(
        "/api/overtime/me/deductions", json={"month": "2026-09", "days": 2}
    )
    assert too_many.status_code == 400, too_many.text
    assert "最多" in too_many.json()["detail"]

    # 员工会话打不了店长/管理端那两扇门：全店考勤表要「人事管理」能力（403），
    # 管理端抵扣审批要管理端 cookie（401）。
    assert client.get("/api/attendance/sheet").status_code == 403
    assert client.post("/api/overtime/admin/deductions/1/approve").status_code == 401


def test_admin_pending_lists_deductions_and_approves(deduction_http):
    client, db, accounts = deduction_http
    employee_id = _employee_id(accounts)
    attendance = AttendanceStore(db, now=lambda: FIXED_NOW)
    _run(attendance.set_manual_columns(employee_id, {"opening_balance": 35.0}))
    ledger = OvertimeLedger(db, now=lambda: FIXED_NOW)
    entry = _run(
        ledger.submit(EntryActor(), TODAY, -17, "补钟", target_employee_id=employee_id)
    )
    _run(ledger.approve(EntryActor(), entry["id"]))
    deduction = _run(
        ledger.submit_deduction(
            EntryActor(employee_id=employee_id), "2026-09", 1
        )
    )

    pending = client.get("/api/overtime/admin/pending")
    assert pending.status_code == 200, pending.text
    assert pending.json()["deduction_count"] == 1
    assert pending.json()["deductions"][0]["id"] == deduction["id"]

    approved = client.post(
        f"/api/overtime/admin/deductions/{deduction['id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["deduction"]["status"] == "approved"

    # 批准之后存假余额就少了 1 天。
    staff = _run(attendance.leave_summary(employee_id, "2026-09"))["leave"]
    assert staff["deducted_days"] == 1


if __name__ == "__main__":
    unittest.main()
