#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EmployeeAccounts: register/name, approve, login, disable, 职位, 卫生权限, 班次."""

import tempfile
import unittest
from datetime import datetime, timedelta


from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.accounts import (
    EmployeeAccounts,
    EmployeeAccountsError,
    hygiene_business_date,
)
from services.scheduling.store import SchedulingStore
from tests.hygiene_duty import assign_duty

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

    async def test_remember_login_session_outlives_shift_ttl(self):
        """勾了「记住密码，自动登录」的员工会话活 30 天；不勾仍是 8 小时班次级。"""
        employee = await self.accounts.register(PHONE, PASSWORD, NAME)
        await self.accounts.approve(employee["id"])
        remembered = await self.accounts.login(PHONE, PASSWORD, remember=True)
        session_only = await self.accounts.login(PHONE, PASSWORD)

        after_shift = EmployeeAccounts(
            self.db,
            now=lambda: self.fixed_now + timedelta(hours=settings.SESSION_TTL_HOURS + 1),
        )
        self.assertIsNotNone(await after_shift.get_staff_session(remembered["session_id"]))
        self.assertIsNone(await after_shift.get_staff_session(session_only["session_id"]))

        after_month = EmployeeAccounts(
            self.db,
            now=lambda: self.fixed_now + timedelta(days=settings.SESSION_REMEMBER_DAYS + 1),
        )
        self.assertIsNone(await after_month.get_staff_session(remembered["session_id"]))

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
        # 2026-10-06 起那一个标签**由开关派生**（票 02，ADR 0093）：`set_permission` 只写库列，
        # 一项开关都没给的人读出来仍是「普通员工」—— 两者不一致时一律以开关为准。
        self.assertEqual(admined["permission"], "普通员工")
        self.assertEqual(admined["admin_caps"], [])
        self.assertEqual(admined["job_title"], "领班")
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.set_permission(employee["id"], "店长")
        self.assertEqual(raised.exception.code, "invalid_permission")
        # 给出任一项管理能力，派生出来的标签才是「管理员」。
        granted = await self.accounts.set_admin_caps(employee["id"], ["daily_review"])
        self.assertEqual(granted["permission"], "管理员")
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

    async def test_employee_can_update_own_name_and_login_phone(self):
        employee = await self._approved_employee()
        updated = await self.accounts.update_profile(
            employee["id"],
            name="李四",
            phone="13800138009",
        )
        self.assertEqual(updated["name"], "李四")
        self.assertEqual(updated["phone"], "13800138009")
        self.assertIsNone(await self.accounts.login(PHONE, PASSWORD))
        login = await self.accounts.login("13800138009", PASSWORD)
        self.assertIsNotNone(login)
        self.assertEqual(login["employee"]["name"], "李四")

    async def test_employee_profile_rejects_duplicate_phone(self):
        employee = await self._approved_employee()
        other = await self._approved_employee(PHONE_ADMIN)
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.update_profile(employee["id"], phone=other["phone"])
        self.assertEqual(raised.exception.code, "duplicate_phone")

    async def test_employee_changes_password_and_other_sessions_are_revoked(self):
        employee = await self._approved_employee()
        first = await self.accounts.login(PHONE, PASSWORD)
        second = await self.accounts.login(PHONE, PASSWORD)
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.change_password(
                employee["id"],
                "wrong-password",
                "new-password123",
                keep_session_id=first["session_id"],
            )
        self.assertEqual(raised.exception.code, "invalid_current_password")

        await self.accounts.change_password(
            employee["id"],
            PASSWORD,
            "new-password123",
            keep_session_id=first["session_id"],
        )
        self.assertIsNotNone(
            await self.accounts.get_staff_session(first["session_id"])
        )
        self.assertIsNone(
            await self.accounts.get_staff_session(second["session_id"])
        )
        self.assertIsNone(await self.accounts.login(PHONE, PASSWORD))
        self.assertIsNotNone(await self.accounts.login(PHONE, "new-password123"))

    async def test_the_business_day_cut_decides_which_schedule_row_is_today(self):
        """06:00 切日：卫生认的「今天」跟排班展开用的是同一个切法（票 10）。

        票 10 之前这里测的是「05:59 自己选白班算前一天、06:00 选夜班算今天」。自选入口
        撤了之后，同一件事的另一面要守住：9/12 那天排的是白班、9/13 起改成夜班，那么
        9/13 05:59（营业日还是 9/12）读到白班，06:00 之后读到夜班。
        """
        employee = await self._approved_employee()
        await assign_duty(
            self.db, employee["id"], slot="day",
            now=datetime(2026, 9, 12, 10, 0, tzinfo=CHINA_TZ),
        )
        await assign_duty(
            self.db, employee["id"], slot="night",
            now=datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
        )

        self.fixed_now = datetime(2026, 9, 13, 5, 59, tzinfo=CHINA_TZ)
        self.assertEqual(await self.accounts.current_shift(employee["id"]), "白班")

        self.fixed_now = datetime(2026, 9, 13, 6, 0, tzinfo=CHINA_TZ)
        self.assertEqual(await self.accounts.current_shift(employee["id"]), "夜班")

    async def test_staff_cannot_pick_their_own_assignment_any_more(self):
        """票 10：员工当天改不了班次和区 —— 改的是排班那一天，卫生这一侧只读。

        票 10 之前这条测的是「同一天可以反复改」。自选入口撤了之后，同一个入口要守的是
        相反的承诺：它必须被拒，而且拒绝码是稳定的那个（前端照它说「去今天页看你的班」）。
        """
        employee = await self._approved_employee()
        now = self.fixed_now.isoformat()
        await self.db._conn.execute(
            "INSERT INTO hygiene_zones (name, created_at, updated_at) VALUES (?, ?, ?)",
            ("案板", now, now),
        )
        await self.db._conn.commit()

        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.pick_assignment(employee["id"], "白班", 1)
        self.assertEqual(raised.exception.code, "assignment_from_schedule")
        self.assertIsNone(await self.accounts.current_shift(employee["id"]))

    async def test_the_self_pick_entry_is_gone_for_every_input(self):
        """什么参数都到不了写库那一层 —— 连未知工作区也不再报 `zone_not_found`。

        （票 10 之前这条测的是「未知区被拒」。现在拒的原因只剩一个：这个入口没了。）
        """
        employee = await self._approved_employee()
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.pick_assignment(employee["id"], "白班", 99999)
        self.assertEqual(raised.exception.code, "assignment_from_schedule")

    async def test_pick_shift_alone_is_gone_too(self):
        """「先选班次、之后再补工作区」那条老路也走不通了（票 10 之前它可以分两步）。"""
        employee = await self._approved_employee()
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.pick_shift(employee["id"], "白班")
        self.assertEqual(raised.exception.code, "shift_from_schedule")

    async def test_a_second_self_pick_is_the_same_refusal_not_a_conflict(self):
        """重复自选不再是 409「当天班次已选定」，而是同一个 403：这件事不该在这儿做。"""
        employee = await self._approved_employee()
        for code in ("shift_from_schedule", "shift_from_schedule"):
            with self.assertRaises(EmployeeAccountsError) as raised:
                await self.accounts.pick_shift(employee["id"], "白班")
            self.assertEqual(raised.exception.code, code)
        self.assertIsNone(await self.accounts.current_shift(employee["id"]))

    async def test_super_cannot_fix_a_shift_from_the_hygiene_side_any_more(self):
        """票 10：管理员改派也不在卫生这一侧做了 —— 它落成**排班的单日覆盖**。

        卫生不 import 排班（DESIGN 决定 3：两边代码不互相 import），所以这条能力搬到了
        排班接口上（前端直接调 `/api/scheduling/overrides/...`）。这一层留一句明确的错，
        别让调用方以为改成功了。
        """
        employee = await self._approved_employee()
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.super_set_shift(employee["id"], "夜班")
        self.assertEqual(raised.exception.code, "assignment_from_schedule")
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.super_set_assignment(employee["id"], "夜班", 1)
        self.assertEqual(raised.exception.code, "assignment_from_schedule")

    async def test_a_hygiene_manager_cannot_change_anyones_shift_either(self):
        """卫生侧的「管理员」是卫生后台的权限，管不到排班：他同样改不了任何人的班。

        票 10 之前这条测的是「管理员权限不妨碍他自己选、也不让他改别人」。现在谁都改不了
        —— 要改去排班页改那一天，或者让店长改规则。
        """
        staff = await self._approved_employee()
        manager = await self._approved_employee(PHONE_ADMIN)
        # 2026-10-06 起「管理员」这个标签由开关派生（票 02，ADR 0093）：要让他真是管理员，
        # 得给管理能力开关；只写 `permission` 那一列已经不影响读出来的档位。
        await self.accounts.set_admin_caps(
            manager["id"], ["daily_review", "deep_review", "fix"]
        )
        roster = await self.accounts.list_roster()
        self.assertEqual(
            next(row for row in roster if row["id"] == manager["id"])["permission"],
            "管理员",
        )
        await assign_duty(self.db, staff["id"], slot="day", now=self.fixed_now)
        self.assertEqual(await self.accounts.current_shift(staff["id"]), "白班")

        for target in (staff["id"], manager["id"]):
            with self.assertRaises(EmployeeAccountsError) as raised:
                await self.accounts.pick_shift(target, "夜班")
            self.assertEqual(raised.exception.code, "shift_from_schedule")

        # 班次还是排班说了算：没排到的那个人依然是 None。
        self.assertEqual(await self.accounts.current_shift(staff["id"]), "白班")
        self.assertIsNone(await self.accounts.current_shift(manager["id"]))

    async def test_staff_session_exposes_todays_shift_or_null(self):
        """会话里那份「今天的班」读的是**排班结果**（票 10）：没排到是空，排到就是那一档。"""
        employee = await self._approved_employee()
        login = await self.accounts.login(PHONE, PASSWORD)
        session = await self.accounts.get_staff_session(login["session_id"])
        self.assertIsNone(session["shift"])

        await assign_duty(self.db, employee["id"], slot="night", now=self.fixed_now)

        session = await self.accounts.get_staff_session(login["session_id"])
        self.assertEqual(session["shift"], "夜班")
        roster = await self.accounts.list_roster()
        self.assertEqual(roster[0]["shift"], "夜班")

    async def test_the_old_shift_argument_check_went_with_the_entry(self):
        """以前这里挡「班次只能是白班或夜班」；入口撤了之后，什么值都到不了那道校验。

        （票 10 之前这条还顺带测 `super_set_shift("中班")` 报 `invalid_shift` —— 两个入口
        都没了，同一件事由这一条守着。）
        """
        employee = await self._approved_employee()
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.pick_shift(employee["id"], "早班")
        self.assertEqual(raised.exception.code, "shift_from_schedule")
