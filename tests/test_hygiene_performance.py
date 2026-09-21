#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Query-budget guards for hygiene read paths."""

import tempfile
import unittest
from datetime import datetime, timedelta

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.work import HygieneWork

SUPER = {"kind": "super"}
STAFF = {
    "kind": "staff",
    "id": 1,
    "permission": "管理员",
    "name": "张三",
    "phone": "13800138000",
    "shift": "白班",
    "zone_id": 1,
}


class QueryBudgetMixin:
    async def _query_count(self, coro_factory):
        original = self.work._conn.execute
        count = 0

        async def wrapped(sql, *params):
            nonlocal count
            count += 1
            return await original(sql, *params)

        self.work._conn.execute = wrapped
        try:
            await coro_factory()
        finally:
            self.work._conn.execute = original
        return count


class HygieneQueryBudgetTest(QueryBudgetMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.captures = FakeCaptureStore()
        self.work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
        )
        await self.work.prepare()
        self._daily_offset = 0
        self._deep_offset = 0
        self._fix_offset = 0

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def _capture(self):
        return {"bytes": b"JPEG-LITERAL", "content_type": "image/jpeg", "markup": []}

    def _live(self):
        return {"bytes": b"LIVE-JPEG", "content_type": "image/jpeg", "live": True}

    def _live(self):
        return {"bytes": b"LIVE-JPEG", "content_type": "image/jpeg", "live": True}

    async def _add_daily_items(self, count):
        zone = (await self.work.list_zones())[0]
        start = self._daily_offset
        for index in range(start, start + count):
            await self.work.add_daily_item(
                SUPER,
                zone["id"],
                f"检查项 {index}",
                self._capture(),
            )
        self._daily_offset += count

    async def _add_deep_items(self, count):
        start = self._deep_offset
        for index in range(start, start + count):
            await self.work.add_deep_clean_item(SUPER, 6, f"专项 {index}")
        self._deep_offset += count

    async def _open_fix_tickets(self, count):
        zone = (await self.work.list_zones())[0]
        start = self._fix_offset
        for index in range(start, start + count):
            await self.work.open_fix(
                SUPER,
                zone["id"],
                "卫生",
                f"问题 {index}",
                timedelta(hours=2),
                self._live(),
            )
        self._fix_offset += count

    async def test_daily_work_has_fixed_query_budget(self):
        await self._add_daily_items(1)
        # 预热：hygiene_board_events.reason 的列探测只在进程内首次发生，
        # 属于一次性开销，不该混进"查询次数随数据量增长"的判据里。
        await self.work.list_daily_work(SUPER)
        one = await self._query_count(lambda: self.work.list_daily_work(SUPER))
        await self._add_daily_items(49)
        fifty = await self._query_count(lambda: self.work.list_daily_work(SUPER))
        self.assertEqual(one, fifty)

    async def test_deep_clean_work_has_fixed_query_budget(self):
        await self._add_deep_items(1)
        one = await self._query_count(lambda: self.work.list_deep_clean_work(STAFF))
        await self._add_deep_items(49)
        fifty = await self._query_count(lambda: self.work.list_deep_clean_work(STAFF))
        self.assertEqual(one, fifty)

    async def test_fix_list_has_fixed_query_budget(self):
        await self._open_fix_tickets(1)
        one = await self._query_count(lambda: self.work.list_fix_tickets(SUPER))
        await self._open_fix_tickets(49)
        fifty = await self._query_count(lambda: self.work.list_fix_tickets(SUPER))
        self.assertEqual(one, fifty)

    async def test_calendar_has_fixed_query_budget(self):
        await self._add_deep_items(1)
        one = await self._query_count(
            lambda: self.work.list_deep_clean_calendar("2026-09-13", "2026-09-13")
        )
        await self._add_deep_items(49)
        fifty = await self._query_count(
            lambda: self.work.list_deep_clean_calendar("2026-09-13", "2026-09-13")
        )
        self.assertEqual(one, fifty)

    async def test_overdue_sweep_has_fixed_query_budget(self):
        await self._add_daily_items(1)
        one = await self._query_count(self.work.sweep_overdue)
        await self._add_daily_items(49)
        fifty = await self._query_count(self.work.sweep_overdue)
        self.assertEqual(one, fifty)


if __name__ == "__main__":
    unittest.main()
