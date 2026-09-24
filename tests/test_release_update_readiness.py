#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""就绪口径健康检查：数据库已连接、迁移已完成、关键表可读 + 启动标识。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest

from fastapi.testclient import TestClient

import main as main_module
from config import settings
from database import DatabaseManager
from db_core.backend import pg as pg_backend
from services.db_migrations import SCHEMA_MIGRATIONS_TABLE, list_migration_files
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
        await self._record_all_migrations()

    async def _record_all_migrations(self) -> None:
        """把本发行包里的迁移都记成「已应用」（生产形态）。

        ``tests/conftest.py`` 建 schema 的方式是直接 ``psql -f migrations/pg/*.sql``，
        不写 ``schema_migrations``；而就绪口径现在由「待应用迁移数 == 0」驱动
        （PERF-11），所以这里补上「门店装完点过应用迁移」的那一行事实。
        conftest 的 TRUNCATE 不碰 ``schema_migrations``，写入用 ON CONFLICT 幂等。
        """
        conn = self.db._conn
        await conn.execute(
            f"CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} ("
            "version TEXT PRIMARY KEY, filename TEXT NOT NULL, "
            "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        for item in list_migration_files():
            await conn.execute(
                f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} "
                "(version, filename, checksum, applied_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (version) DO NOTHING",
                (item.version, item.filename, item.checksum, "2026-01-01T00:00:00+08:00"),
            )
        await conn.commit()

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

    async def test_not_ready_when_migrations_pending(self):
        """带未应用迁移时 readiness 报未就绪，并在 details 里给出条数（PERF-11）。

        旧口径只回答「连接已建立」——带 schema 变更的升级、还没点「应用迁移」的
        门店照样报 ready。这里把 0004 从追踪表里拿掉，模拟「包里有、库里没应用」。
        """
        self.tracker.mark_started(migrations_complete=True)
        adapter = AppReadinessAdapter(lambda: self.db, tracker=self.tracker)
        self.assertTrue(
            (await adapter.inspect_readiness()).migrations_complete,
            "前置：迁移齐了应当就绪",
        )

        target = [item for item in list_migration_files() if not item.bootstrap_only][-1]
        conn = self.db._conn
        await conn.execute(
            f"DELETE FROM {SCHEMA_MIGRATIONS_TABLE} WHERE version = ?", (target.version,)
        )
        await conn.commit()
        try:
            readiness = await adapter.inspect_readiness()
            self.assertFalse(readiness.migrations_complete)
            self.assertFalse(readiness.ready)
            self.assertIn("1 条数据库迁移待应用", " ".join(readiness.details))
            self.assertEqual(await self.db.pending_migration_count(), 1)
        finally:
            await conn.execute(
                f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} "
                "(version, filename, checksum, applied_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (version) DO NOTHING",
                (
                    target.version,
                    target.filename,
                    target.checksum,
                    "2026-01-01T00:00:00+08:00",
                ),
            )
            await conn.commit()

        restored = await adapter.inspect_readiness()
        self.assertTrue(restored.migrations_complete, "补回追踪行后应恢复就绪")
        self.assertTrue(restored.ready)

    async def test_migrations_complete_reads_pending_not_connection(self):
        """``migrations_complete()`` 由「待应用迁移数 == 0」驱动（PERF-11）。"""
        self.assertEqual(await self.db.pending_migration_count(), 0)
        self.assertTrue(await self.db.migrations_complete())

    async def test_migrations_complete_is_pending_driven_not_connection_driven(self):
        """独立验证（verifier-db）：连接可用但**有待应用迁移**时必须说 False。

        上面那条用例只在「待应用 == 0」这一侧比对，而这一侧两种实现（待应用驱动 /
        连接推导 `return self.is_connected()`）答案相同——把 ``migrations_complete``
        改回连接推导的变异不会被它杀死（本次独立验证实测该变异存活）。这里补上有
        鉴别力的另一侧：连接正常、关键表可读，只把 1 条追踪行拿掉。

        ``main.py:433`` 启动期用 ``await db_manager.migrations_complete()`` 决定
        ``mark_started(migrations_complete=...)``，所以这一侧不是纸面契约。
        """
        target = [item for item in list_migration_files() if not item.bootstrap_only][-1]
        conn = self.db._conn
        await conn.execute(
            f"DELETE FROM {SCHEMA_MIGRATIONS_TABLE} WHERE version = ?", (target.version,)
        )
        await conn.commit()
        try:
            self.assertEqual(await self.db.pending_migration_count(), 1)
            self.assertTrue(
                self.db.is_connected(),
                "前置：连接状态没有变化——判据若看连接，这里就会误报 True",
            )
            self.assertFalse(
                await self.db.migrations_complete(),
                "有 1 条待应用迁移时 migrations_complete() 必须是 False（PERF-11）",
            )
        finally:
            await conn.execute(
                f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} "
                "(version, filename, checksum, applied_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (version) DO NOTHING",
                (
                    target.version,
                    target.filename,
                    target.checksum,
                    "2026-01-01T00:00:00+08:00",
                ),
            )
            await conn.commit()
        self.assertTrue(await self.db.migrations_complete(), "补回追踪行后应恢复 True")

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

    async def test_not_ready_after_connection_closed(self):
        """连接断掉之后必须报 not ready——判据不能是「连接对象还在」。

        ``DatabaseManager.close()`` **刻意不丢连接对象**（持有者还捏着它，见
        `PgConnection.rebind`），所以旧判据 ``_conn.raw is not None`` 在关闭之后
        一直报「已连接」，更新健康确认会据此认为数据库还活着。``is_connected()``
        现在问 asyncpg 的 ``is_closed()``；这条用例把那个行为钉在真实调用路径上
        （``inspect_readiness`` 是 ``readiness.py:79`` 的唯一生产消费者）。
        """
        self.tracker.mark_started(migrations_complete=True)
        adapter = AppReadinessAdapter(lambda: self.db, tracker=self.tracker)
        self.assertTrue(
            (await adapter.inspect_readiness()).ready, "前置：连上时应当就绪"
        )

        await self.db.close()

        readiness = await adapter.inspect_readiness()
        self.assertFalse(readiness.db_connected, "断开后 db_connected 必须为假")
        self.assertFalse(readiness.ready, "断开后不能报就绪")
        self.assertIn("数据库未连接", " ".join(readiness.details))


class StartupReadinessEndToEndTest(unittest.TestCase):
    """独立验证（verifier-db）：真实启动路径也认「待应用迁移」（PERF-11）。

    ``ReadinessTest`` 直接问 ``inspect_readiness()``；这里把同一判据压到生产入口上：
    ``main.py`` lifespan → ``mark_started(migrations_complete=await db_manager.migrations_complete())``
    → ``GET /api/system/health``。留 1 条迁移待应用时启动，接口必须报
    ``status: unhealthy`` + ``migrations_complete: false`` + details 给出条数；
    补回追踪行再启动一次，必须回到 ``healthy``——证明就绪判据真的跟着
    「待应用迁移数」走，而不是「进程起来了没」。
    """

    _APPLIED_AT = "2026-01-01T00:00:00+08:00"

    @classmethod
    def _sync_tracking(cls) -> str:
        """用独立连接把追踪表调成「除最后一条外都已应用」，返回那条的版本号。"""
        target = [item for item in list_migration_files() if not item.bootstrap_only][-1]

        async def _run() -> None:
            conn = await pg_backend.connect()
            try:
                await conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} ("
                    "version TEXT PRIMARY KEY, filename TEXT NOT NULL, "
                    "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
                )
                for item in list_migration_files():
                    await conn.execute(
                        f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} "
                        "(version, filename, checksum, applied_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT (version) DO NOTHING",
                        (item.version, item.filename, item.checksum, cls._APPLIED_AT),
                    )
                await conn.execute(
                    f"DELETE FROM {SCHEMA_MIGRATIONS_TABLE} WHERE version = ?",
                    (target.version,),
                )
                await conn.commit()
            finally:
                await conn.close()

        asyncio.run(_run())
        return target.version

    @classmethod
    def _restore_tracking(cls, version: str) -> None:
        async def _run() -> None:
            conn = await pg_backend.connect()
            try:
                item = next(i for i in list_migration_files() if i.version == version)
                await conn.execute(
                    f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} "
                    "(version, filename, checksum, applied_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (version) DO NOTHING",
                    (item.version, item.filename, item.checksum, cls._APPLIED_AT),
                )
                await conn.commit()
            finally:
                await conn.close()

        asyncio.run(_run())

    def test_startup_readiness_follows_pending_migrations(self):
        version = self._sync_tracking()
        try:
            with TestClient(main_module.app) as client:
                pending = client.get("/api/system/health")
            self.assertEqual(pending.status_code, 200, pending.text)
            body = pending.json()
            self.assertEqual(body.get("status"), "unhealthy", body)
            self.assertFalse(body.get("ready"), body)
            self.assertFalse(body.get("migrations_complete"), body)
            self.assertIn("1 条数据库迁移待应用", " ".join(body.get("details") or []))
        finally:
            self._restore_tracking(version)

        with TestClient(main_module.app) as client:
            restored = client.get("/api/system/health")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(
            restored.json().get("status"), "healthy", restored.text
        )


if __name__ == "__main__":
    unittest.main()
