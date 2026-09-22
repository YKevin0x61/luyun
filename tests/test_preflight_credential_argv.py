#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新预检的探测命令不许把密码放进 argv。

缺陷背景（`.scratch/project-review-2026-09-22` SEC-04）：`preflight_env.py` 把
`REDIS_URL` / `POSTGRES_DSN` 整串塞进子进程参数（`redis-cli -u <url>`、
`pg_isready -d <dsn>`、`psql -d <dsn>`）。按惯例这两个值可以写成
`scheme://user:password@host/db`，于是预检期间 `ps -ef` / `/proc/<pid>/cmdline`
对同机其它用户可见完整口令——而同一文件的 docstring 自己写着"不回显 URL：它可能带密码"。

修法：解析 URL，改成 `PGPASSWORD` / `REDISCLI_AUTH` 环境变量 + `-h/-p/-U/-d`，
argv 里只留主机/端口/用户名/库名。这些用例直接捕获 `subprocess.run` 的调用参数
（argv + env），断言密码**既不在 argv 里、也不在额外参数里**，只出现在 env。

`psql -d` 那条同属 argv 暴露面（`_server_major_version`），一并纳入断言。
"""

from __future__ import annotations

import os
import types
import unittest
from pathlib import Path
from unittest import mock

from config import settings
from services.release_update.preflight_env import DefaultPreflightEnvAdapter

PASSWORD = "s3cr3t-pw"
REDIS_URL = f"redis://:{PASSWORD}@redis.internal:6380/2"
PG_DSN = f"postgresql://luyun:{PASSWORD}@pg.internal:5433/luyun"


def _fake_run(calls, *, returncode: int = 0, stdout: str = "PONG\n"):
    def run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    return run


def _adapter() -> DefaultPreflightEnvAdapter:
    return DefaultPreflightEnvAdapter(Path("/tmp/deploy"))


def _patched(calls, **run_kwargs):
    return (
        mock.patch(
            "services.release_update.preflight_env.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}",
        ),
        mock.patch(
            "services.release_update.preflight_env.subprocess.run",
            side_effect=_fake_run(calls, **run_kwargs),
        ),
    )


class RedisProbeArgvTest(unittest.TestCase):
    def _redis_state(self, calls, **run_kwargs):
        which, run = _patched(calls, **run_kwargs)
        with mock.patch.dict(os.environ, {"LUYUN_REDIS_URL": REDIS_URL}), which, run:
            return _adapter()._redis_state()

    def test_redis_password_never_reaches_argv(self):
        calls = []
        configured, reachable, _detail = self._redis_state(calls)

        self.assertTrue(configured)
        self.assertTrue(reachable)
        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]

        joined = " ".join(argv)
        self.assertNotIn(PASSWORD, joined, f"argv 里有密码: {argv}")
        self.assertNotIn(REDIS_URL, joined, f"argv 里回显了整串 URL: {argv}")

        env = kwargs.get("env") or {}
        self.assertEqual(env.get("REDISCLI_AUTH"), PASSWORD)
        # 主机/端口仍要传到位，否则探测的是本机默认实例，红灯变成假绿灯。
        self.assertIn("-h", argv)
        self.assertEqual(argv[argv.index("-h") + 1], "redis.internal")
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("-p") + 1], "6380")

    def test_unparseable_redis_url_skips_probe_instead_of_passing_it_through(self):
        """解析不出主机时不许退回 `-u <url>`（那正是暴露面），宁可跳过探测。"""
        calls = []
        which, run = _patched(calls)
        with mock.patch.dict(os.environ, {"LUYUN_REDIS_URL": "not-a-url"}), which, run:
            configured, reachable, detail = _adapter()._redis_state()

        self.assertTrue(configured)
        self.assertIsNone(reachable)
        self.assertIn("跳过", detail)
        self.assertEqual(calls, [], "解析失败时不该起子进程")


class PostgresProbeArgvTest(unittest.TestCase):
    def _database_state(self, calls, **run_kwargs):
        which, run = _patched(calls, **run_kwargs)
        with mock.patch.object(settings, "DATABASE_BACKEND", "postgres"), mock.patch.dict(
            os.environ, {"LUYUN_POSTGRES_DSN": PG_DSN}
        ), which, run:
            return _adapter()._database_state()

    def test_dsn_password_never_reaches_argv(self):
        calls = []
        # pg_isready 通、pg_dump 版本比对也走通：让三条探测命令都真的跑一遍。
        with mock.patch.object(
            DefaultPreflightEnvAdapter,
            "_tool_major_version",
            staticmethod(lambda exe: 16),
        ):
            self._database_state(calls, stdout="16.4\n")

        self.assertGreaterEqual(len(calls), 2, f"没跑到探测命令: {calls}")
        ready = next((c for c in calls if str(c[0][0]).endswith("pg_isready")), None)
        self.assertIsNotNone(ready, f"没跑 pg_isready: {[c[0][0] for c in calls]}")
        argv, kwargs = ready

        joined = " ".join(argv)
        self.assertNotIn(PASSWORD, joined, f"pg_isready argv 里有密码: {argv}")
        self.assertNotIn(PG_DSN, joined, f"pg_isready argv 里回显了整串 DSN: {argv}")

        env = kwargs.get("env") or {}
        self.assertEqual(env.get("PGPASSWORD"), PASSWORD)
        self.assertIn("-h", argv)
        self.assertEqual(argv[argv.index("-h") + 1], "pg.internal")
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("-p") + 1], "5433")
        self.assertIn("-U", argv)
        self.assertEqual(argv[argv.index("-U") + 1], "luyun")
        self.assertIn("-d", argv)
        self.assertEqual(argv[argv.index("-d") + 1], "luyun")

    def test_psql_probe_also_keeps_the_password_out_of_argv(self):
        calls = []
        with mock.patch.object(
            DefaultPreflightEnvAdapter,
            "_tool_major_version",
            staticmethod(lambda exe: 16),
        ):
            self._database_state(calls, stdout="16.4\n")

        psql_call = next((c for c in calls if str(c[0][0]).endswith("psql")), None)
        self.assertIsNotNone(psql_call, f"没跑 psql: {[c[0][0] for c in calls]}")
        argv, kwargs = psql_call
        self.assertNotIn(PASSWORD, " ".join(argv), f"psql argv 里有密码: {argv}")
        self.assertEqual((kwargs.get("env") or {}).get("PGPASSWORD"), PASSWORD)

    def test_keyword_value_dsn_is_supported(self):
        """libpq 的 keyword/value DSN 也要能解析出 host/port/user/dbname。"""
        calls = []
        which, run = _patched(calls, stdout="16.4\n")
        dsn = f"host=pg.internal port=5433 user=luyun password={PASSWORD} dbname=luyun"
        with mock.patch.object(settings, "DATABASE_BACKEND", "postgres"), mock.patch.dict(
            os.environ, {"LUYUN_POSTGRES_DSN": dsn}
        ), which, run, mock.patch.object(
            DefaultPreflightEnvAdapter,
            "_tool_major_version",
            staticmethod(lambda exe: 16),
        ):
            _adapter()._database_state()

        ready = next(c for c in calls if str(c[0][0]).endswith("pg_isready"))
        argv, kwargs = ready
        self.assertNotIn(PASSWORD, " ".join(argv), f"argv 里有密码: {argv}")
        self.assertEqual(argv[argv.index("-h") + 1], "pg.internal")
        self.assertEqual(argv[argv.index("-p") + 1], "5433")
        self.assertEqual(argv[argv.index("-U") + 1], "luyun")
        self.assertEqual(argv[argv.index("-d") + 1], "luyun")
        self.assertEqual((kwargs.get("env") or {}).get("PGPASSWORD"), PASSWORD)


if __name__ == "__main__":
    unittest.main()
