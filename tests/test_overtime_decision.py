#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两台账，一台状态机：`decide` / `withdraw` 的判据与方向（ADR 0153）。

主缝仍是服务层 + 真库 + 注入时钟；这里不重测「批准之后钱怎么算」（那在
`tests/test_overtime.py` / `tests/test_overtime_deductions.py`），只测**裁决本身**：
状态 → 身份 → 理由的次序、两本账的错误码、作废的两道额外守卫，以及「只有一处
`SET status`」这条源码契约。
"""

import ast
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import asyncpg

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.attendance import AttendanceStore
from services.hygiene.accounts import EmployeeAccounts
from services.overtime.ledger import (
    ACTION_APPROVE,
    ACTION_REJECT,
    ACTION_VOID,
    BOOK_DEDUCTION,
    BOOK_ENTRY,
    EntryActor,
    OvertimeError,
    OvertimeLedger,
)

PASSWORD = "password123"
PHONE = "13800138000"
NAME = "张三"
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-24"
AUGUST_DAY = "2026-08-10"
SEPTEMBER = "2026-09"


class DecisionLedgerTest(unittest.IsolatedAsyncioTestCase):
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

    async def _entry(self, employee_id, day=TODAY, half_hours=13, reason="加班"):
        return await self.ledger.submit(
            EntryActor(), day, half_hours, reason, target_employee_id=employee_id
        )

    async def _approved_entry(self, employee_id, day=TODAY, half_hours=13):
        entry = await self._entry(employee_id, day, half_hours)
        return await self.ledger.decide(
            BOOK_ENTRY, entry["id"], ACTION_APPROVE, EntryActor()
        )

    async def _enough_leave_in(self, employee_id, month, days=5.0):
        """给某个月造出「够扣的存假」：期初 = 目标余额 + 当月天数（同抵扣测试）。"""
        from calendar import monthrange

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

    async def _enough_leave(self, employee_id, days=5.0):
        """9 月 30 天，期初给 `days + 30` 天即余额 = days（同抵扣测试）。"""
        await self._enough_leave_in(employee_id, SEPTEMBER, days)

    async def _deduction(self, employee, days=1):
        await self._enough_leave(employee["id"], days=5)
        await self._approved_entry(employee["id"], TODAY, -17)
        return await self.ledger.submit_deduction(
            EntryActor(employee_id=employee["id"]), SEPTEMBER, days
        )

    async def _assert_code(self, code, coro):
        with self.assertRaises(OvertimeError) as caught:
            await coro
        self.assertEqual(caught.exception.code, code)

    async def test_entry_approve_and_reject_share_one_transition(self):
        employee = await self._employee()
        pending = await self._entry(employee["id"])

        approved = await self.ledger.decide(
            BOOK_ENTRY, pending["id"], ACTION_APPROVE, EntryActor()
        )
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["decided_by"], "super")
        await self._assert_code(
            "entry_not_pending",
            self.ledger.decide(BOOK_ENTRY, pending["id"], ACTION_APPROVE, EntryActor()),
        )

        second = await self._entry(employee["id"])
        rejected = await self.ledger.decide(
            BOOK_ENTRY, second["id"], ACTION_REJECT, EntryActor(), "那天没有排班"
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["reject_reason"], "那天没有排班")

    async def test_deduction_approve_and_reject_share_the_same_table(self):
        employee = await self._employee()
        deduction = await self._deduction(employee)

        approved = await self.ledger.decide(
            BOOK_DEDUCTION, deduction["id"], ACTION_APPROVE, EntryActor()
        )
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["kind"], "leave_deduction")
        await self._assert_code(
            "deduction_not_pending",
            self.ledger.decide(
                BOOK_DEDUCTION, deduction["id"], ACTION_APPROVE, EntryActor()
            ),
        )

        other = await self._deduction(employee)
        rejected = await self.ledger.decide(
            BOOK_DEDUCTION, other["id"], ACTION_REJECT, EntryActor(), "这个月不用抵"
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["reject_reason"], "这个月不用抵")

    async def test_self_approval_and_self_rejection_are_refused_on_both_books(self):
        employee = await self._employee()
        owner = EntryActor(employee_id=employee["id"])
        entry = await self._entry(employee["id"])
        deduction = await self._deduction(employee)

        for book, target in ((BOOK_ENTRY, entry), (BOOK_DEDUCTION, deduction)):
            for action in (ACTION_APPROVE, ACTION_REJECT):
                await self._assert_code(
                    "cannot_self_accept",
                    self.ledger.decide(book, target["id"], action, owner, "我不批自己"),
                )

    async def test_status_is_checked_before_identity_and_reason(self):
        employee = await self._employee()
        owner = EntryActor(employee_id=employee["id"])
        entry = await self._entry(employee["id"])
        await self.ledger.decide(BOOK_ENTRY, entry["id"], ACTION_APPROVE, EntryActor())

        # 已批准：本人来驳、理由空着，报的仍是「处理过了」，不是身份或理由。
        await self._assert_code(
            "entry_not_pending",
            self.ledger.decide(BOOK_ENTRY, entry["id"], ACTION_REJECT, owner, ""),
        )

        deduction = await self._deduction(employee)
        # 待批准：非超管来作废，报的仍是「不是已批准」，不是 forbidden。
        await self._assert_code(
            "deduction_not_approved",
            self.ledger.decide(
                BOOK_DEDUCTION, deduction["id"], ACTION_VOID, owner
            ),
        )

    async def test_only_super_can_void_both_books(self):
        employee = await self._employee()
        entry = await self._entry(employee["id"])
        await self.ledger.decide(BOOK_ENTRY, entry["id"], ACTION_APPROVE, EntryActor())
        manager = EntryActor(employee_id=employee["id"], can_backfill=True)

        await self._assert_code(
            "forbidden",
            self.ledger.decide(BOOK_ENTRY, entry["id"], ACTION_VOID, manager),
        )
        voided = await self.ledger.decide(
            BOOK_ENTRY, entry["id"], ACTION_VOID, EntryActor()
        )
        self.assertEqual(voided["status"], "voided")

        deduction = await self._deduction(employee)
        await self.ledger.decide(
            BOOK_DEDUCTION, deduction["id"], ACTION_APPROVE, EntryActor()
        )
        await self._assert_code(
            "forbidden",
            self.ledger.decide(BOOK_DEDUCTION, deduction["id"], ACTION_VOID, manager),
        )
        voided = await self.ledger.decide(
            BOOK_DEDUCTION, deduction["id"], ACTION_VOID, EntryActor()
        )
        self.assertEqual(voided["status"], "voided")

    async def test_void_keeps_both_extra_guards(self):
        employee = await self._employee()
        deduction = await self._deduction(employee)
        await self.ledger.decide(
            BOOK_DEDUCTION, deduction["id"], ACTION_APPROVE, EntryActor()
        )
        makeup = (await self.ledger.list_mine(employee["id"]))["entries"][0]
        self.assertLess(makeup["half_hours"], 0)
        await self._assert_code(
            "makeup_in_use",
            self.ledger.decide(BOOK_ENTRY, makeup["id"], ACTION_VOID, EntryActor()),
        )

        await self._enough_leave_in(employee["id"], "2026-08", days=5)
        august = await self._entry(employee["id"], AUGUST_DAY, -17)
        august = await self.ledger.decide(
            BOOK_ENTRY, august["id"], ACTION_APPROVE, EntryActor(),
        )
        august_deduction = await self.ledger.submit_deduction(
            EntryActor(employee_id=employee["id"]), "2026-08", 1
        )
        await self.ledger.decide(
            BOOK_DEDUCTION, august_deduction["id"], ACTION_APPROVE, EntryActor()
        )
        await self.attendance.seal("2026-08")
        await self._assert_code(
            "month_sealed",
            self.ledger.decide(
                BOOK_DEDUCTION, august_deduction["id"], ACTION_VOID, EntryActor()
            ),
        )

    async def test_reject_reason_is_required_after_the_state_check(self):
        employee = await self._employee()
        entry = await self._entry(employee["id"])
        await self._assert_code(
            "missing_reject_reason",
            self.ledger.decide(BOOK_ENTRY, entry["id"], ACTION_REJECT, EntryActor(), "  "),
        )
        deduction = await self._deduction(employee)
        await self._assert_code(
            "missing_reject_reason",
            self.ledger.decide(
                BOOK_DEDUCTION, deduction["id"], ACTION_REJECT, EntryActor(), ""
            ),
        )

    async def test_unknown_ids_keep_each_books_code(self):
        for book, code in (
            (BOOK_ENTRY, "unknown_entry"),
            (BOOK_DEDUCTION, "unknown_deduction"),
        ):
            for action in (ACTION_APPROVE, ACTION_REJECT, ACTION_VOID):
                await self._assert_code(
                    code, self.ledger.decide(book, 999999, action, EntryActor())
                )

    async def test_withdraw_only_own_pending_on_both_books(self):
        employee = await self._employee()
        other = await self._employee(phone="13900139000", name="李四")
        entry = await self._entry(employee["id"])
        deduction = await self._deduction(employee)

        for book, target in ((BOOK_ENTRY, entry), (BOOK_DEDUCTION, deduction)):
            code = "unknown_entry" if book == BOOK_ENTRY else "unknown_deduction"
            await self._assert_code(
                code, self.ledger.withdraw(book, other["id"], target["id"])
            )
            cancelled = await self.ledger.withdraw(
                book, employee["id"], target["id"]
            )
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(cancelled["decided_by"], f"staff:{employee['id']}")
            await self._assert_code(
                "entry_not_pending" if book == BOOK_ENTRY else "deduction_not_pending",
                self.ledger.withdraw(book, employee["id"], target["id"]),
            )

    async def test_approve_still_writes_the_salary_snapshot(self):
        employee = await self._employee(salary=None)
        entry = await self._entry(employee["id"])
        await self._assert_code(
            "salary_missing",
            self.ledger.decide(BOOK_ENTRY, entry["id"], ACTION_APPROVE, EntryActor()),
        )
        await self.accounts.update_fields(
            employee["id"], base_salary=5100, hire_date="2024-03-01"
        )
        await self.ledger.decide(BOOK_ENTRY, entry["id"], ACTION_APPROVE, EntryActor())
        self.assertEqual(
            await self.ledger.salary_snapshot(employee["id"], SEPTEMBER), 5100
        )

    async def test_database_rejects_an_unknown_status(self):
        employee = await self._employee()
        entry = await self._entry(employee["id"])
        with self.assertRaises(asyncpg.exceptions.CheckViolationError):
            await self.ledger._conn.execute(
                "UPDATE overtime_entries SET status = 'bogus' WHERE id = ?",
                (entry["id"],),
            )
        await self.ledger._conn.rollback()

    async def test_source_keeps_one_transition_and_no_legacy_doors(self):
        source = Path("services/overtime/ledger.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("SET status ="), 1)
        tree = ast.parse(source)
        methods = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue({"decide", "withdraw", "_transition"}.issubset(methods))
        for name in (
            "_claim",
            "_claim_deduction",
            "_decide",
            "_decide_locked",
            "_decide_deduction_locked",
            "approve",
            "reject",
            "void",
            "cancel",
            "approve_deduction",
            "reject_deduction",
            "void_deduction",
            "cancel_deduction",
        ):
            self.assertNotIn(name, methods, name)


class OvertimeErrorTranslationTest(unittest.TestCase):
    def test_forbidden_is_403_with_a_super_admin_hint(self):
        """服务层只给稳定码；403 与那句「找超管」由 API 域的错误表负责。"""
        from api import overtime as api_overtime

        response = api_overtime._OVERTIME_ERRORS.http(OvertimeError("forbidden"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["X-Error-Code"], "forbidden")
        self.assertIn("超级管理员", response.detail)


if __name__ == "__main__":
    unittest.main()