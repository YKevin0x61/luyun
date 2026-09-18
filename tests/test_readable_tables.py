#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""就绪口径的关键表探测必须在两种后端下都工作。

现场 0.6.0 → 0.6.2 升级后卡在「已切换但未健康」：

    关键表不存在：orders、tables、dish_stations
    关键表不可读：'coroutine' object does not support the asynchronous context
    manager protocol

根因有两条，都在 ``db_core/connection.py::readable_tables``：
1. ``async with conn.execute(...)`` —— PG 后端的 ``execute`` 是 ``async def``，返回
   coroutine，不能当异步上下文管理器（SQLite 的 aiosqlite 会返回 Cursor，所以一直
   没暴露）；
2. 表名查询写死了 ``sqlite_master``，PG 里没有这张表。

它只在「更新后健康确认」里被调用，所以直到 PG 后端第一次成功走到更新最后一步才炸。
"""

import asyncio
import unittest
from unittest import mock

from config import settings
from database import DatabaseManager

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None

KEY_TABLES = ["orders", "tables", "dish_stations"]


def pg_available() -> bool:
    if asyncpg is None:
        return False

    async def probe() -> bool:
        try:
            conn = await asyncpg.connect("postgresql://localhost:5432/luyun", timeout=2)
        except Exception:
            return False
        await conn.close()
        return True

    try:
        return asyncio.run(probe())
    except Exception:
        return False


class ReadableTablesSqlSelectionTest(unittest.TestCase):
    """不连库：只验证「随后端选表名查询」，以及执行姿势与后端兼容。"""

    def _probe(self, backend: str):
        executed: list[str] = []

        class FakeCursor:
            async def fetchall(self):
                return [("orders",), ("tables",), ("dish_stations",)]

            async def fetchone(self):
                return (1,)

        class FakeConn:
            async def execute(self, sql, params=()):
                executed.append(sql)
                return FakeCursor()

        db = DatabaseManager.__new__(DatabaseManager)
        db._main_conn = FakeConn()
        with mock.patch.object(settings, "DATABASE_BACKEND", backend):
            result = asyncio.run(db.readable_tables(KEY_TABLES))
        return executed, result

    def test_postgres_uses_pg_tables_not_sqlite_master(self):
        executed, result = self._probe("postgres")
        self.assertTrue(executed, "必须真的执行了查询")
        self.assertIn("pg_tables", executed[0])
        self.assertNotIn("sqlite_master", executed[0], "PG 里没有 sqlite_master")
        self.assertTrue(result["readable"], f"表都在却判为不可读：{result}")
        self.assertEqual(result["missing"], [])

    def test_sqlite_still_uses_sqlite_master(self):
        executed, result = self._probe("sqlite")
        self.assertIn("sqlite_master", executed[0])
        self.assertTrue(result["readable"])

    def test_not_connected_reports_all_missing(self):
        db = DatabaseManager.__new__(DatabaseManager)
        db._main_conn = None
        result = asyncio.run(db.readable_tables(KEY_TABLES))
        self.assertFalse(result["readable"])
        self.assertEqual(result["missing"], KEY_TABLES)


@unittest.skipUnless(pg_available(), "PostgreSQL 不可用，跳过真实 PG 断言")
class ReadableTablesPostgresTest(unittest.IsolatedAsyncioTestCase):
    async def _db(self):
        # patch 必须活到 readable_tables 调用之后：它读的是 settings.DATABASE_BACKEND
        # （测试进程里通常被 DATABASE_BACKEND=sqlite 固定住）。
        backend_patch = mock.patch.object(settings, "DATABASE_BACKEND", "postgres")
        dsn_patch = mock.patch.object(
            settings, "POSTGRES_DSN", "postgresql://localhost:5432/luyun"
        )
        backend_patch.start()
        dsn_patch.start()
        self.addCleanup(backend_patch.stop)
        self.addCleanup(dsn_patch.stop)
        db = DatabaseManager()
        await db.connect()
        return db

    async def test_key_tables_are_readable(self):
        db = await self._db()
        try:
            probe = await db.readable_tables(KEY_TABLES)
        finally:
            await db.close()
        self.assertTrue(probe["readable"], f"PG 上关键表应可读，实际：{probe}")
        self.assertEqual(probe["missing"], [])
        self.assertEqual(probe["errors"], [])

    async def test_missing_table_is_reported(self):
        db = await self._db()
        try:
            probe = await db.readable_tables(["orders", "definitely_not_a_table"])
        finally:
            await db.close()
        self.assertFalse(probe["readable"])
        self.assertEqual(probe["missing"], ["definitely_not_a_table"])


if __name__ == "__main__":
    unittest.main()
