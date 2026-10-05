#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Concurrent hygiene writes must not 500, duplicate, or partially apply."""

import asyncio
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
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.work import HygieneWork
from tests.hygiene_duty import assign_duty

SUPER = {"kind": "super"}
PHONE = "13800138000"
PASSWORD = "password123"


def staff(employee_id=1, phone=PHONE, shift="白班", permission="管理员", caps=None):
    """员工 actor。`caps` 是**管理权限开关**（2026-10-05 起判据看它，不看 `permission`）；
    不给就按迁移 `0015` 的回填规则推 —— 老「管理员」= 日常验收 + 专项验收 + 整改单。"""
    if caps is None:
        caps = ("daily_review", "deep_review", "fix") if permission == "管理员" else ()
    return {
        "kind": "staff",
        "id": employee_id,
        "phone": phone,
        "name": "张三",
        "shift": shift,
        "permission": permission,
        "caps": list(caps),
        "zone_id": 1,
    }


class HygieneConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
        self.captures = FakeCaptureStore()
        self.work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: self.fixed_now,
        )
        self.accounts = EmployeeAccounts(self.db, now=lambda: self.fixed_now)
        await self.work.prepare()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _daily_item(self):
        zone = (await self.work.list_zones())[0]
        return await self.work.add_daily_item(
            SUPER,
            zone["id"],
            "案板表面",
            {"bytes": b"STANDARD", "content_type": "image/jpeg", "markup": []},
        )

    def _live(self, suffix):
        return {
            "bytes": f"LIVE-{suffix}".encode(),
            "content_type": "image/jpeg",
            "live": True,
        }

    async def test_concurrent_daily_submits_do_not_raise(self):
        item = await self._daily_item()
        results = await asyncio.gather(
            *[
                self.work.submit_daily(staff(), item["id"], self._live(index))
                for index in range(8)
            ],
            return_exceptions=True,
        )
        self.assertEqual(
            [result for result in results if isinstance(result, Exception)],
            [],
        )
        review = await self.work.get_daily_review(item["id"], "白班", actor=SUPER)
        self.assertIn(review["capture_id"], self.captures.blobs)

    async def test_concurrent_accept_and_reject_only_one_wins(self):
        item = await self._daily_item()
        await self.work.submit_daily(staff(), item["id"], self._live("A"))
        results = await asyncio.gather(
            self.work.accept_daily(staff(2, "13800138001"), item["id"], "白班"),
            self.work.reject_daily(staff(3, "13800138002"), item["id"], "白班"),
            return_exceptions=True,
        )
        self.assertEqual(sum(1 for result in results if not isinstance(result, Exception)), 1)

    async def test_concurrent_registration_returns_one_duplicate(self):
        results = await asyncio.gather(
            self.accounts.register(PHONE, PASSWORD, "张三"),
            self.accounts.register(PHONE, PASSWORD, "李四"),
            return_exceptions=True,
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        errors = [result for result in results if isinstance(result, EmployeeAccountsError)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "duplicate_phone")

    async def test_concurrent_shift_pick_is_refused_and_schedule_keeps_one_row(self):
        """并发改「今天上哪个班」：自选两个请求全被拒，排班那一侧只留一行。

        票 10 之前这条测的是「同一天两个自选并发只有一个能落库，另一个报
        `shift_already_picked`」（靠 `hygiene_shift_picks` 的唯一键）。自选入口撤了之后，
        这件事整个搬到了排班：卫生这一侧并发发起的两个写请求都得走同一句拒绝（谁也不能从
        竞争窗口里溜进去写下状态），而「一天只有一行班」由 `staff_assignments` 的唯一键
        与全局写锁保证 —— 所以并发改规则之后，当天仍然只有一行，且卫生读到的是那一行。
        """
        employee = await self.accounts.register(PHONE, PASSWORD, "张三")
        await self.accounts.approve(employee["id"])

        refused = await asyncio.gather(
            self.accounts.pick_shift(employee["id"], "白班"),
            self.accounts.pick_shift(employee["id"], "夜班"),
            return_exceptions=True,
        )
        self.assertTrue(
            all(isinstance(result, EmployeeAccountsError) for result in refused),
            f"两个并发自选都必须被拒，实际: {refused!r}",
        )
        self.assertEqual(
            [result.code for result in refused], ["shift_from_schedule"] * 2
        )
        # 一个写都没落：今天没有班，自选表里也没有行。
        self.assertIsNone(await self.accounts.current_shift(employee["id"]))
        cur = await self.db._conn.execute("SELECT COUNT(*) AS n FROM hygiene_shift_picks")
        self.assertEqual(int(dict(await cur.fetchone())["n"]), 0)

        business_date = hygiene_business_date(self.fixed_now)
        raced = await asyncio.gather(
            assign_duty(self.db, employee["id"], slot="day", now=self.fixed_now),
            assign_duty(self.db, employee["id"], slot="night", now=self.fixed_now),
            return_exceptions=True,
        )
        self.assertEqual(
            [result for result in raced if isinstance(result, Exception)], []
        )
        cur = await self.db._conn.execute(
            """SELECT s.duty_slot
                 FROM staff_assignments a
                 LEFT JOIN staff_shifts s ON s.id = a.shift_id
                WHERE a.employee_id = ? AND a.business_date = ?""",
            (employee["id"], business_date),
        )
        rows = await cur.fetchall()
        self.assertEqual(len(rows), 1, "并发改规则后，当天只能剩一行排班")
        slot = dict(rows[0])["duty_slot"]
        self.assertIn(slot, ("day", "night"))
        self.assertEqual(
            await self.accounts.current_shift(employee["id"]),
            "白班" if slot == "day" else "夜班",
        )

    async def test_roster_field_update_is_atomic(self):
        employee = await self.accounts.register(PHONE, PASSWORD, "张三")
        await self.accounts.approve(employee["id"])
        with self.assertRaises(EmployeeAccountsError) as raised:
            await self.accounts.update_fields(
                employee["id"],
                name="李四",
                permission="超级管理员",
            )
        self.assertEqual(raised.exception.code, "invalid_permission")
        roster = await self.accounts.list_roster()
        self.assertEqual(roster[0]["name"], "张三")

    async def test_concurrent_fix_accept_and_reject_only_one_wins(self):
        zone = (await self.work.list_zones())[0]
        ticket = await self.work.open_fix(
            SUPER,
            zone["id"],
            "卫生",
            "地面积水",
            timedelta(hours=1),
            self._live("OPEN"),
        )
        await self.work.reshoot_fix(staff(4, "13800138003"), ticket["id"], self._live("RESHOT"))
        results = await asyncio.gather(
            self.work.accept_fix(SUPER, ticket["id"]),
            self.work.reject_fix(staff(5, "13800138004"), ticket["id"]),
            return_exceptions=True,
        )
        self.assertEqual(sum(1 for result in results if not isinstance(result, Exception)), 1)


if __name__ == "__main__":
    unittest.main()
