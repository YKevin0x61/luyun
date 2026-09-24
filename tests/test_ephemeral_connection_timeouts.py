#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旁路 PostgreSQL 连接也必须有超时（PERF-09）。

``db_core/backend/pg.py`` 的主连接一直带 ``statement_timeout`` / ``lock_timeout``
（见 tests/test_pg_timeouts.py），但三处「自己 ``asyncpg.connect()``」的旁路连接
没有：``services/backup_retention.py``（冷备读保留配置）、
``services/pg_credentials.py``（改密码前的 verify 与新连接）、
``scripts/cold_backup.py``（走前者）。它们实测 ``SHOW statement_timeout`` 都是 0，
即无限等待——冷备脚本和改密码接口能被一条挂起的语句永远卡住。

这里断言的是**连接上的真实取值**（``SHOW``），以及三处旁路都已改走
``connect_ephemeral()``（seam：``db_core.backend.pg.connect_ephemeral``）。
"""

import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest import mock

try:  # asyncpg 是 PG 后端依赖；缺失时跳过（与 test_pg_timeouts.py 同款守卫）
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None  # type: ignore[assignment]

from db_core.backend import pg as pg_backend
from services import backup_retention, pg_credentials

STATEMENT_ENV = "LUYUN_PG_STATEMENT_TIMEOUT_MS"
LOCK_ENV = "LUYUN_PG_LOCK_TIMEOUT_MS"

REPO_ROOT = Path(__file__).resolve().parents[1]

# 改密码用例里的当前 DSN（连接被 seam 替身顶掉，不会真连）。
RESET_DSN = "postgresql://luyun:oldpw@localhost:5432/luyun_test_db2"


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


@unittest.skipUnless(pg_available(), "PostgreSQL 不可用，跳过旁路连接超时测试")
class EphemeralConnectionTimeoutsTest(unittest.IsolatedAsyncioTestCase):
    """``connect_ephemeral()`` 建出的会话必须带有限的语句/锁超时。"""

    async def _connect_with(self, **env):
        """在指定超时 env 下建旁路连接；未给出的超时变量先清掉（= 生产形态）。"""
        with mock.patch.dict(os.environ):
            os.environ.pop(STATEMENT_ENV, None)
            os.environ.pop(LOCK_ENV, None)
            os.environ.update({k: v for k, v in env.items() if v is not None})
            return await pg_backend.connect_ephemeral(
                pg_backend.dsn_from_env(), timeout=5
            )

    async def test_defaults_are_finite(self):
        """两个 env 都没设时，旁路连接的 statement_timeout 必须非 0。"""
        conn = await self._connect_with()
        try:
            statement = await conn.fetchval("SHOW statement_timeout")
            lock = await conn.fetchval("SHOW lock_timeout")
        finally:
            await conn.close()
        self.assertNotEqual(statement, "0")
        self.assertNotEqual(lock, "0")

    async def test_defaults_are_the_config_values(self):
        """默认值与主连接同源：config 的 30s / 5s。"""
        conn = await self._connect_with()
        try:
            statement = await conn.fetchval("SHOW statement_timeout")
            lock = await conn.fetchval("SHOW lock_timeout")
        finally:
            await conn.close()
        self.assertEqual(statement, "30s")
        self.assertEqual(lock, "5s")

    async def test_env_override_wins(self):
        conn = await self._connect_with(**{STATEMENT_ENV: "1234", LOCK_ENV: "250"})
        try:
            statement = await conn.fetchval("SHOW statement_timeout")
            lock = await conn.fetchval("SHOW lock_timeout")
        finally:
            await conn.close()
        self.assertEqual(statement, "1234ms")
        self.assertEqual(lock, "250ms")


class _FakeConn:
    """只实现旁路调用点用到的三个方法。"""

    def __init__(self, value=None):
        self._value = value
        self.closed = False

    async def fetchval(self, *_args, **_kwargs):
        return self._value

    async def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed


def _spy(record):
    async def fake(dsn=None, timeout=15.0):
        record.append({"dsn": dsn, "timeout": timeout})
        return _FakeConn("SELECT 1")

    return fake


class BypassCallSitesUseConnectEphemeralTest(unittest.IsolatedAsyncioTestCase):
    """三处旁路都走 ``connect_ephemeral()``，不再自建裸连接。"""

    def test_source_has_no_raw_asyncpg_connect(self):
        for relative in ("services/backup_retention.py", "services/pg_credentials.py"):
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("asyncpg.connect(", source, relative)

    def test_cold_backup_script_uses_detailed_loader(self):
        source = (REPO_ROOT / "scripts" / "cold_backup.py").read_text(encoding="utf-8")
        self.assertIn("load_from_pg_sync_detailed", source)

    async def test_credentials_verify_connects_through_seam(self):
        calls: list = []
        with mock.patch.object(
            pg_backend, "connect_ephemeral", new=_spy(calls)
        ):
            await pg_credentials._verify_dsn_connects(
                "postgresql://localhost:5432/luyun_test_db2"
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["dsn"], "postgresql://localhost:5432/luyun_test_db2")
        self.assertEqual(calls[0]["timeout"], 15)

    def test_retention_loader_connects_through_seam(self):
        """读取失败的旁路连接也必须是带超时的 connect_ephemeral（不是裸 connect）。

        ``load_from_pg_sync_detailed`` 是同步入口（脚本里调用，内部 ``asyncio.run``），
        所以这条用例保持同步——在事件循环里调它会直接报 ``cannot be called from a
        running event loop``，那测的就不是旁路超时了。
        """
        calls: list = []

        async def failing(dsn=None, timeout=15.0):
            calls.append({"dsn": dsn, "timeout": timeout})
            raise ConnectionError("数据库连不上")

        with mock.patch.object(pg_backend, "connect_ephemeral", new=failing):
            loaded = backup_retention.load_from_pg_sync_detailed()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["timeout"], backup_retention.RETENTION_READ_TIMEOUT_SECONDS)
        # 读取失败 ⇒ 回退默认值且标记 fallback（PERF-10：据此跳过冷备剪除）。
        self.assertEqual(loaded.config.cold_keep, backup_retention.COLD_KEEP_DEFAULT)
        self.assertEqual(loaded.source, backup_retention.RETENTION_SOURCE_FALLBACK)

    def test_retention_loader_reads_configured_value(self):
        calls: list = []
        payload = json.dumps({"snapshot_keep": 5, "cold_keep": 3, "export_keep": 5})

        async def fake(dsn=None, timeout=15.0):
            calls.append({"dsn": dsn, "timeout": timeout})
            return _FakeConn(payload)

        with mock.patch.object(pg_backend, "connect_ephemeral", new=fake):
            loaded = backup_retention.load_from_pg_sync_detailed()
        self.assertEqual(loaded.config.cold_keep, 3)
        self.assertEqual(loaded.source, backup_retention.RETENTION_SOURCE_CONFIGURED)
        self.assertEqual(calls[0]["timeout"], backup_retention.RETENTION_READ_TIMEOUT_SECONDS)

    async def test_password_reset_connects_through_seam(self):
        """第三处旁路（T2-V3）：``reset_database_password`` 的连接也走 seam。

        ``ALTER USER`` 是 DDL，连接必须带 statement_timeout/lock_timeout——这里钉
        「走的是 ``connect_ephemeral``（⇒ 必然带 server_settings）且超时是 15s」，
        同时把「真写 env.production」与「真重启进程」两处副作用换成替身。
        """
        calls: list = []
        hashes = ["hash-before", "hash-after"]
        written: list = []

        async def fake_hash(_conn, _username):
            return hashes.pop(0)

        async def fake_apply(_conn, _username, _password):
            return None

        def fake_write(dsn):
            written.append(dsn)
            return REPO_ROOT / "env.production"

        with mock.patch.object(pg_backend, "connect_ephemeral", new=_spy(calls)), \
             mock.patch.object(pg_credentials, "_dsn_env_override", return_value=None), \
             mock.patch.object(pg_credentials, "current_dsn", return_value=RESET_DSN), \
             mock.patch.object(pg_credentials, "_password_hash", new=fake_hash), \
             mock.patch.object(pg_credentials, "_apply_password", new=fake_apply), \
             mock.patch.object(pg_credentials, "_write_env_production", new=fake_write), \
             mock.patch.object(pg_credentials, "_restart_application", new=lambda: None):
            result = await pg_credentials.reset_database_password(actor="t2r-test")

        self.assertTrue(result["ok"])
        self.assertTrue(result["restart_triggered"])
        # 两次连接：第一次是旧密码跑 ALTER USER，第二次用新密码 verify；都走旁路 + 15s。
        self.assertEqual([call["timeout"] for call in calls], [15, 15])
        self.assertEqual(calls[0]["dsn"], RESET_DSN)
        self.assertNotEqual(calls[1]["dsn"], RESET_DSN)
        self.assertEqual(len(written), 1)
