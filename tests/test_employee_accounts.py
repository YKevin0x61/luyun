#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EmployeeAccounts: register/name, approve, login, disable, 职位, 卫生权限, 班次."""

import tempfile
import unittest
from datetime import datetime

import aiosqlite

from config import settings
from database import CHINA_TZ, DatabaseManager
from db_core.schema import migrate_hygiene_columns
from services.hygiene.accounts import (
    EmployeeAccounts,
    EmployeeAccountsError,
    hygiene_business_date,
)

PHONE = "13800138000"
PHONE_ADMIN = "13800138001"
PASSWORD = "password123"
NAME = "张三"


class EmployeeAccountsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
        self.accounts = EmployeeAccounts(self.db, now=lambda: self.fixed_now)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_pending_register_cannot_login_with_correct_password(self):
        await self.accounts.register(PHONE, PASSWORD, NAME)
        result = await self.accounts.login(PHONE, PASSWORD)
        self.assertIsNone(result)

    async def test_login_succeeds_after_super_approves(self):
        employee = await self.accounts.register(PHONE, PASSWORD, NAME)
        await self.accounts.approve(employee["id"])
        result = await self.accounts.login(PHONE, PASSWORD)
        self.assertIsNotNone(result)
        self.assertEqual(result["employee"]["phone"], PHONE)
        self.assertEqual(result["employee"]["name"], NAME)
        self.assertEqual(result["employee"]["permission"], "普通员工")
        self.assertTrue(result["employee"]["approved"])
        self.assertFalse(result["employee"]["disabled"])
        self.assertTrue(result["session_id"])

    async def test_disable_blocks_login_and_keeps_roster_row(self):
        employee = await self.accounts.register(PHONE, PASSWORD, NAME)
        await self.accounts.approve(employee["id"])
        await self.accounts.disable(employee["id"])
        self.assertIsNone(await self.accounts.login(PHONE, PASSWORD))
        roster = await self.accounts.list_roster()
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster[0]["phone"], PHONE)
        self.assertEqual(roster[0]["name"], NAME)
        self.assertTrue(roster[0]["disabled"])
        self.assertTrue(roster[0]["approved"])

    async def test_job_title_is_display_only_permission_is_staff_or_admin(self):
        employee = await self.accounts.register(PHONE, PASSWORD, NAME)
        await self.accounts.approve(employee["id"])
        titled = await self.accounts.set_job_title(employee["id"], "领班")
        self.assertEqual(titled["job_title"], "领班")
        self.assertEqual(titled["permission"], "普通员工")
        login = await self.accounts.login(PHONE, PASSWORD)
        self.assertEqual(login["employee"]["permission"], "普通员工")
        self.assertEqual(login["employee"]["job_title"], "领班")
        admined = await self.accounts.set_permission(employee["id"], "管理员")
        self.assertEqual(admined["permission"], "管理员")
        self.assertEqual(admined["job_title"], "领班")
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.set_permission(employee["id"], "店长")
        self.assertEqual(raised.exception.code, "invalid_permission")
        still = await self.accounts.list_roster()
        self.assertEqual(still[0]["permission"], "管理员")

    async def test_cannot_promote_employee_to_super_admin(self):
        employee = await self.accounts.register(PHONE, PASSWORD, NAME)
        await self.accounts.approve(employee["id"])
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.set_permission(employee["id"], "超级管理员")
        self.assertEqual(raised.exception.code, "invalid_permission")
        roster = await self.accounts.list_roster()
        self.assertEqual(roster[0]["permission"], "普通员工")
        self.assertNotIn("超级管理员", [row["permission"] for row in roster])

    def test_hygiene_business_date_cuts_at_six(self):
        self.assertEqual(
            hygiene_business_date(datetime(2026, 9, 13, 5, 59, tzinfo=CHINA_TZ)),
            "2026-09-12",
        )
        self.assertEqual(
            hygiene_business_date(datetime(2026, 9, 13, 6, 0, tzinfo=CHINA_TZ)),
            "2026-09-13",
        )

    async def test_legacy_employee_table_gets_name_column(self):
        conn = await aiosqlite.connect(":memory:")
        try:
            await conn.execute(
                "CREATE TABLE hygiene_employees (id INTEGER PRIMARY KEY, phone TEXT)"
            )
            await migrate_hygiene_columns(conn)
            cur = await conn.execute("PRAGMA table_info(hygiene_employees)")
            columns = {row[1] for row in await cur.fetchall()}
            self.assertIn("name", columns)
        finally:
            await conn.close()

    async def _approved_employee(self, phone=PHONE):
        employee = await self.accounts.register(phone, PASSWORD, NAME)
        return await self.accounts.approve(employee["id"])

    async def test_name_is_required_and_admin_can_fix_legacy_row(self):
        with self.assertRaises(EmployeeAccountsError) as missing:
            await self.accounts.register(PHONE, PASSWORD, "  ")
        self.assertEqual(missing.exception.code, "invalid_name")

        employee = await self.accounts.register(PHONE, PASSWORD, NAME)
        renamed = await self.accounts.set_name(employee["id"], "李四")
        self.assertEqual(renamed["name"], "李四")
        roster = await self.accounts.list_roster()
        self.assertEqual(roster[0]["name"], "李四")

    async def test_pick_at_0559_is_previous_business_day_0600_is_new_day(self):
        employee = await self._approved_employee()
        self.fixed_now = datetime(2026, 9, 13, 5, 59, tzinfo=CHINA_TZ)
        early = await self.accounts.pick_shift(employee["id"], "白班")
        self.assertEqual(early["business_date"], "2026-09-12")
        self.assertEqual(early["shift"], "白班")
        self.assertEqual(await self.accounts.current_shift(employee["id"]), "白班")

        self.fixed_now = datetime(2026, 9, 13, 6, 0, tzinfo=CHINA_TZ)
        self.assertIsNone(await self.accounts.current_shift(employee["id"]))
        later = await self.accounts.pick_shift(employee["id"], "夜班")
        self.assertEqual(later["business_date"], "2026-09-13")
        self.assertEqual(later["shift"], "夜班")
        self.assertEqual(await self.accounts.current_shift(employee["id"]), "夜班")

    async def test_second_self_pick_same_business_day_fails(self):
        employee = await self._approved_employee()
        first = await self.accounts.pick_shift(employee["id"], "白班")
        self.assertEqual(first["shift"], "白班")
        with self.assertRaises(EmployeeAccountsError) as same:
            await self.accounts.pick_shift(employee["id"], "白班")
        self.assertEqual(same.exception.code, "shift_already_picked")
        with self.assertRaises(EmployeeAccountsError) as other:
            await self.accounts.pick_shift(employee["id"], "夜班")
        self.assertEqual(other.exception.code, "shift_already_picked")
        self.assertEqual(await self.accounts.current_shift(employee["id"]), "白班")

    async def test_super_set_shift_changes_employee_shift_that_business_day(self):
        employee = await self._approved_employee()
        await self.accounts.pick_shift(employee["id"], "白班")
        fixed = await self.accounts.super_set_shift(employee["id"], "夜班")
        self.assertEqual(fixed["business_date"], "2026-09-13")
        self.assertEqual(fixed["shift"], "夜班")
        self.assertEqual(await self.accounts.current_shift(employee["id"]), "夜班")
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.pick_shift(employee["id"], "白班")
        self.assertEqual(raised.exception.code, "shift_already_picked")
        self.assertEqual(await self.accounts.current_shift(employee["id"]), "夜班")

    async def test_staff_admin_cannot_change_another_employees_shift(self):
        staff = await self._approved_employee()
        manager = await self._approved_employee(PHONE_ADMIN)
        await self.accounts.set_permission(manager["id"], "管理员")
        roster = await self.accounts.list_roster()
        self.assertEqual(
            next(row for row in roster if row["id"] == manager["id"])["permission"],
            "管理员",
        )
        await self.accounts.pick_shift(staff["id"], "白班")
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.pick_shift(staff["id"], "夜班")
        self.assertEqual(raised.exception.code, "shift_already_picked")
        self.assertEqual(await self.accounts.current_shift(staff["id"]), "白班")
        await self.accounts.pick_shift(manager["id"], "夜班")
        self.assertEqual(await self.accounts.current_shift(manager["id"]), "夜班")
        self.assertEqual(await self.accounts.current_shift(staff["id"]), "白班")

    async def test_staff_session_exposes_todays_shift_or_null(self):
        employee = await self._approved_employee()
        login = await self.accounts.login(PHONE, PASSWORD)
        session = await self.accounts.get_staff_session(login["session_id"])
        self.assertIsNone(session["shift"])
        await self.accounts.pick_shift(employee["id"], "夜班")
        session = await self.accounts.get_staff_session(login["session_id"])
        self.assertEqual(session["shift"], "夜班")
        roster = await self.accounts.list_roster()
        self.assertEqual(roster[0]["shift"], "夜班")

    async def test_shift_must_be_day_or_night(self):
        employee = await self._approved_employee()
        with self.assertRaises(EmployeeAccountsError) as picked:
            await self.accounts.pick_shift(employee["id"], "早班")
        self.assertEqual(picked.exception.code, "invalid_shift")
        with self.assertRaises(EmployeeAccountsError) as fixed:
            await self.accounts.super_set_shift(employee["id"], "中班")
        self.assertEqual(fixed.exception.code, "invalid_shift")
        self.assertIsNone(await self.accounts.current_shift(employee["id"]))
