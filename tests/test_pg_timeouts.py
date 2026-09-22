#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连接级超时必须默认生效（生产零语句超时 → 一条挂起事务冻结全站）。

全进程只有一条 ``PgConnection``，写事务全程持全局串行锁。``connect()`` 原来只在
``LUYUN_PG_STATEMENT_TIMEOUT_MS`` 有值时设超时，而这个变量此前只出现在
``tests/conftest.py``——生产连接的 ``statement_timeout`` / ``lock_timeout`` 都是
PG 默认的 0（无限等待），一条挂起的写事务会把 API、``/api/healthz`` 与全部后台
循环一起排住且无法打断。

这里断言的是**连接建立后 PG 会话上的真实取值**（``SHOW``），不是实现细节：
默认有限等待、env 可覆盖、0 = 显式关闭、测试侧既有的 30000 语义不变。
"""

import asyncio
import os
import unittest
from unittest import mock

try:  # asyncpg 是 PG 后端依赖；缺失时跳过（与 test_pg_backend.py 同款守卫）
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None  # type: ignore[assignment]

from db_core.backend import pg as pg_backend

# 部署模板（deploy/env.production.example、deploy/.env.docker.example）里的名字。
STATEMENT_ENV = "LUYUN_PG_STATEMENT_TIMEOUT_MS"
LOCK_ENV = "LUYUN_PG_LOCK_TIMEOUT_MS"


def pg_available() -> bool:
    if asyncpg is None:
        return False

    async def probe() -> bool:
        try:
            conn = await asyncpg.connect(pg_backend.dsn_from_env(), timeout=2)
        except Exception:
            return False
        await conn.close()
        return True

    try:
        return asyncio.run(probe())
    except Exception:
        return False


@unittest.skipUnless(pg_available(), "PostgreSQL 不可用，跳过连接超时测试")
class PgConnectionTimeoutsTest(unittest.IsolatedAsyncioTestCase):
    """连接期超时的默认值与覆盖（seam：``pg_backend.connect()`` 建出的连接）。"""

    async def _show(self, conn, name: str) -> str:
        """会话上的实际取值。``SHOW x`` 与 ``SELECT current_setting('x')`` 等价。"""
        return await conn.raw.fetchval(f"SHOW {name}")

    async def _connect_with(self, **env):
        """在指定超时 env 下建连接；未给出的超时变量先清掉（= 生产形态）。"""
        with mock.patch.dict(os.environ):
            os.environ.pop(STATEMENT_ENV, None)
            os.environ.pop(LOCK_ENV, None)
            os.environ.update({k: v for k, v in env.items() if v is not None})
            return await pg_backend.connect()

    async def test_defaults_are_finite(self):
        """两个 env 都没设时，超时必须非 0——不能是 PG 默认的无限等待。"""
        conn = await self._connect_with()
        try:
            statement = await self._show(conn, "statement_timeout")
            lock = await self._show(conn, "lock_timeout")
        finally:
            await conn.close()

        self.assertNotEqual(
            statement,
            "0",
            "默认（生产形态）statement_timeout 是 0：一条挂起的语句会无限等待",
        )
        self.assertNotEqual(
            lock,
            "0",
            "默认（生产形态）lock_timeout 是 0：等锁会无限等待",
        )

    async def test_defaults_are_the_documented_safe_values(self):
        """默认值即安全默认：statement_timeout 30s、lock_timeout 5s。

        依据：测试库 21 万行 orders（生产 204,297 行）上最重的合法查询
        （180 天区间报表，reports.aggregate_table_operations）实测约 0.6s。
        """
        conn = await self._connect_with()
        try:
            self.assertEqual(await self._show(conn, "statement_timeout"), "30s")
            self.assertEqual(await self._show(conn, "lock_timeout"), "5s")
        finally:
            await conn.close()

    async def test_env_overrides_defaults(self):
        """env 覆盖按毫秒生效（部署模板里写的就是这两个变量名）。"""
        conn = await self._connect_with(**{STATEMENT_ENV: "1234", LOCK_ENV: "250"})
        try:
            self.assertEqual(await self._show(conn, "statement_timeout"), "1234ms")
            self.assertEqual(await self._show(conn, "lock_timeout"), "250ms")
        finally:
            await conn.close()

    async def test_zero_disables_one_timeout_only(self):
        """0 = 显式关闭该项（逃生门），另一项不受影响。"""
        conn = await self._connect_with(**{STATEMENT_ENV: "0"})
        try:
            self.assertEqual(await self._show(conn, "statement_timeout"), "0")
            self.assertNotEqual(await self._show(conn, "lock_timeout"), "0")
        finally:
            await conn.close()

    async def test_test_side_30000_still_yields_30s(self):
        """测试侧 ``setdefault(..., "30000")`` 的行为不变：设 30000 就是 30s。"""
        conn = await self._connect_with(**{STATEMENT_ENV: "30000"})
        try:
            self.assertEqual(await self._show(conn, "statement_timeout"), "30s")
        finally:
            await conn.close()

    async def test_conftest_still_sets_the_documented_env_var(self):
        """测试侧的设置点不能被搬走：conftest 仍在设这个变量名。"""
        self.assertTrue(
            os.environ.get(STATEMENT_ENV),
            f"tests/conftest.py 应设置 {STATEMENT_ENV}（测试连接的超时设置点）",
        )

    async def test_unparsable_env_fails_loudly(self):
        """值不是非负毫秒数时报错，不能静默关掉超时——那正是本票要消掉的形态。"""
        for bad in ("30s", "-1", "abc"):
            with self.subTest(value=bad), mock.patch.dict(os.environ):
                os.environ[STATEMENT_ENV] = bad
                with self.assertRaises(ValueError) as ctx:
                    await pg_backend.connect()
                self.assertIn(STATEMENT_ENV, str(ctx.exception))


@unittest.skipUnless(pg_available(), "PostgreSQL 不可用，跳过连接超时测试")
class AppConnectionTimeoutsTest(unittest.IsolatedAsyncioTestCase):
    """应用真实连接路径（``DatabaseManager.connect()``）也必须带上超时。

    ``pg_backend.connect()`` 只是驱动层；生产走的是 ``db_core/connection.py`` 这条
    路径，超时必须在应用自己的那条连接上同样生效。
    """

    async def test_app_connection_has_finite_timeouts(self):
        from database import DatabaseManager

        db = DatabaseManager()
        self.assertTrue(await db.connect(), "应用连接建立失败")
        try:
            raw = db._conn  # 应用唯一连接；这里只读 SHOW，不碰任何表
            statement = await raw.raw.fetchval("SHOW statement_timeout")
            lock = await raw.raw.fetchval("SHOW lock_timeout")
        finally:
            await db.close()

        self.assertNotEqual(statement, "0", "应用连接没有 statement_timeout")
        self.assertNotEqual(lock, "0", "应用连接没有 lock_timeout")


if __name__ == "__main__":
    unittest.main()
