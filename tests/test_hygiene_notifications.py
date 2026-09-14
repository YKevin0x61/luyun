#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily and fix overdue digests keep one notification per group."""

import tempfile
import unittest
from datetime import datetime, timedelta

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.notifier import FakeNotifier
from services.hygiene.work import HygieneWork

SUPER = {"kind": "super"}


class HygieneOverdueDigestTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 13, 15, 0, tzinfo=CHINA_TZ)
        self.captures = FakeCaptureStore()
        self.notifier = FakeNotifier()
        self.work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: self.fixed_now,
            notifier=self.notifier,
        )
        await self.work.prepare()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def _capture(self):
        return {"bytes": b"STANDARD", "content_type": "image/jpeg", "markup": []}

    def _live(self):
        return {"bytes": b"LIVE", "content_type": "image/jpeg", "live": True}

    async def test_daily_digest_contains_all_items_in_one_shift(self):
        zone = (await self.work.list_zones())[0]
        for name in ("案板表面", "柜门把手"):
            await self.work.add_daily_item(SUPER, zone["id"], name, self._capture())
        await self.work.set_daily_overdue_clocks(SUPER, "15:00", "21:30")

        await self.work.sweep_overdue()

        self.assertEqual(len(self.notifier.texts), 1)
        self.assertIn("白班", self.notifier.texts[0])
        self.assertIn("案板表面", self.notifier.texts[0])
        self.assertIn("柜门把手", self.notifier.texts[0])

    async def test_fix_digest_contains_all_tickets_in_one_zone(self):
        zone = (await self.work.list_zones())[0]
        tickets = [
            await self.work.open_fix(
                SUPER,
                zone["id"],
                "卫生",
                text,
                timedelta(minutes=1),
                self._live(),
            )
            for text in ("地面积水", "货架油污")
        ]
        open_count = len(self.notifier.texts)
        self.fixed_now = self.fixed_now + timedelta(minutes=2)

        await self.work.sweep_overdue()

        overdue = self.notifier.texts[open_count:]
        self.assertEqual(len(overdue), 1)
        self.assertIn("地面积水", overdue[0])
        self.assertIn("货架油污", overdue[0])
        self.assertEqual(len(tickets), 2)


if __name__ == "__main__":
    unittest.main()
