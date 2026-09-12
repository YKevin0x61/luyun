#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EmployeeAccounts: register, approve, login, disable, 职位, 卫生权限."""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.accounts import EmployeeAccounts, EmployeeAccountsError

PHONE = "13800138000"
PASSWORD = "password123"


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
        await self.accounts.register(PHONE, PASSWORD)
        result = await self.accounts.login(PHONE, PASSWORD)
        self.assertIsNone(result)

    async def test_login_succeeds_after_super_approves(self):
        employee = await self.accounts.register(PHONE, PASSWORD)
        await self.accounts.approve(employee["id"])
        result = await self.accounts.login(PHONE, PASSWORD)
        self.assertIsNotNone(result)
        self.assertEqual(result["employee"]["phone"], PHONE)
        self.assertEqual(result["employee"]["permission"], "普通员工")
        self.assertTrue(result["employee"]["approved"])
        self.assertFalse(result["employee"]["disabled"])
        self.assertTrue(result["session_id"])

    async def test_disable_blocks_login_and_keeps_roster_row(self):
        employee = await self.accounts.register(PHONE, PASSWORD)
        await self.accounts.approve(employee["id"])
        await self.accounts.disable(employee["id"])
        self.assertIsNone(await self.accounts.login(PHONE, PASSWORD))
        roster = await self.accounts.list_roster()
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster[0]["phone"], PHONE)
        self.assertTrue(roster[0]["disabled"])
        self.assertTrue(roster[0]["approved"])

    async def test_job_title_is_display_only_permission_is_staff_or_admin(self):
        employee = await self.accounts.register(PHONE, PASSWORD)
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
        employee = await self.accounts.register(PHONE, PASSWORD)
        await self.accounts.approve(employee["id"])
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.set_permission(employee["id"], "超级管理员")
        self.assertEqual(raised.exception.code, "invalid_permission")
        roster = await self.accounts.list_roster()
        self.assertEqual(roster[0]["permission"], "普通员工")
        self.assertNotIn("超级管理员", [row["permission"] for row in roster])
