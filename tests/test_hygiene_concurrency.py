#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Concurrent hygiene writes must not 500, duplicate, or partially apply."""

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.accounts import EmployeeAccounts, EmployeeAccountsError
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.work import HygieneWork

SUPER = {"kind": "super"}
PHONE = "13800138000"
PASSWORD = "password123"


def staff(employee_id=1, phone=PHONE, shift="白班", permission="管理员"):
    return {
        "kind": "staff",
        "id": employee_id,
        "phone": phone,
        "name": "张三",
        "shift": shift,
        "permission": permission,
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
