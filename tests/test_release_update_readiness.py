#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""就绪口径健康检查：数据库已连接、迁移已完成、关键表可读 + 启动标识。"""

from __future__ import annotations

import tempfile
import unittest

from config import settings
from database import DatabaseManager
from services.release_update.readiness import (
    AppReadinessAdapter,
    RuntimeReadinessTracker,
)


class ReadinessTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        # 后端由 conftest 统一钉在 PostgreSQL 测试库（luyun_test）上：这里只考就绪
        # 口径，不再像 SQLite 时代那样为了隔离临时目录而把后端改回 sqlite
        # （ADR 0089 后那样写会让 setUp 直接失败，而 setUp 失败不会走 tearDown，
        # 于是 DATABASE_BACKEND 被永久留在 sqlite 上，把后面所有文件带崩）。
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.tracker = RuntimeReadinessTracker()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_not_ready_when_db_missing(self):
        adapter = AppReadinessAdapter(lambda: None, tracker=self.tracker)
        readiness = await adapter.inspect_readiness()
        self.assertFalse(readiness.ready)
        self.assertFalse(readiness.db_connected)

    async def test_not_ready_when_migrations_incomplete(self):
        adapter = AppReadinessAdapter(lambda: self.db, tracker=self.tracker)
        readiness = await adapter.inspect_readiness()
        self.assertFalse(readiness.ready)
        self.assertTrue(readiness.db_connected)
        self.assertTrue(readiness.key_tables_readable)
        self.assertFalse(readiness.migrations_complete)

    async def test_ready_when_db_connected_migrations_and_tables(self):
        self.tracker.mark_started(migrations_complete=True)
        adapter = AppReadinessAdapter(lambda: self.db, tracker=self.tracker)
        readiness = await adapter.inspect_readiness()
        self.assertTrue(readiness.ready)
        self.assertTrue(readiness.migrations_complete)
        self.assertTrue(readiness.key_tables_readable)
        self.assertIsNotNone(readiness.startup_id)
        self.assertIsNotNone(readiness.started_at)

    async def test_not_ready_when_key_table_missing(self):
        """用一个不存在的表名模拟缺表——不要 DROP 真表（那会删掉开发库里的数据）。

        AppReadinessAdapter 支持传入 key_tables，正是为此。
        """
        self.tracker.mark_started(migrations_complete=True)
        adapter = AppReadinessAdapter(
            lambda: self.db, tracker=self.tracker, key_tables=("definitely_missing_table",)
        )
        readiness = await adapter.inspect_readiness()
        self.assertFalse(readiness.key_tables_readable)
        self.assertFalse(readiness.ready)
        self.assertIn("definitely_missing_table", " ".join(readiness.details))

    async def test_startup_id_differs_per_mark(self):
        self.tracker.mark_started(migrations_complete=True)
        first = self.tracker.startup_id
        self.tracker.mark_started(migrations_complete=True)
        self.assertNotEqual(first, self.tracker.startup_id)

    async def test_payload_shape(self):
        self.tracker.mark_started(migrations_complete=True)
        adapter = AppReadinessAdapter(lambda: self.db, tracker=self.tracker)
        readiness = await adapter.inspect_readiness()
        payload = adapter.readiness_payload(readiness)
        for key in (
            "ready",
            "startup_id",
            "started_at",
            "db_connected",
            "migrations_complete",
            "key_tables_readable",
            "details",
        ):
            self.assertIn(key, payload)


if __name__ == "__main__":
    unittest.main()
