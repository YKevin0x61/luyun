#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新预检必须提前发现「pg_dump 版本低于服务端」。

现场更新作业卡在 ``backing_up``：pg_dump 15 dump 不了服务端 16，而预检当时只探了
pg_isready —— 库是通的，备份却必然失败。这个红灯应该出现在「版本检测」页，而不是
等更新走到一半才炸。
"""

import os
import types
import unittest
from pathlib import Path
from unittest import mock

from config import settings
from services.release_update.preflight_env import DefaultPreflightEnvAdapter

DSN = "postgresql://luyun:pw@pg:5432/luyun"


def _fake_run(
    *, pg_isready_ok: bool = True, pg_dump: str = "pg_dump (PostgreSQL) 15.8", server: str = "16.4"
):
    def run(cmd, **_kwargs):
        exe = str(cmd[0])
        if exe.endswith("pg_isready"):
            return types.SimpleNamespace(
                returncode=0 if pg_isready_ok else 1, stdout="", stderr=""
            )
        if exe.endswith("pg_dump"):
            return types.SimpleNamespace(returncode=0, stdout=f"{pg_dump}\n", stderr="")
        if exe.endswith("psql"):
            return types.SimpleNamespace(returncode=0, stdout=f"{server}\n", stderr="")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    return run


class PreflightPgDumpGateTest(unittest.TestCase):
    def _database_state(self, *, which=None, **run_kwargs):
        adapter = DefaultPreflightEnvAdapter(Path("/tmp/deploy"))
        with mock.patch.object(settings, "DATABASE_BACKEND", "postgres"), mock.patch.dict(
            os.environ, {"LUYUN_POSTGRES_DSN": DSN}
        ), mock.patch(
            "services.release_update.preflight_env.shutil.which",
            side_effect=which or (lambda name: f"/usr/bin/{name}"),
        ), mock.patch(
            "services.release_update.preflight_env.subprocess.run",
            side_effect=_fake_run(**run_kwargs),
        ):
            return adapter._database_state()

    def test_old_client_blocks_update(self):
        """客户端 15 < 服务端 16：红灯，且要说清怎么修。"""
        ok, detail = self._database_state()
        self.assertFalse(ok)
        self.assertIn("低于服务端", detail)
        self.assertIn("重建镜像", detail)

    def test_matching_client_passes(self):
        ok, detail = self._database_state(pg_dump="pg_dump (PostgreSQL) 16.4")
        self.assertTrue(ok)
        self.assertIn("pg_dump 16", detail)

    def test_newer_client_passes(self):
        ok, _ = self._database_state(pg_dump="pg_dump (PostgreSQL) 17.2", server="16.4")
        self.assertTrue(ok)

    def test_missing_pg_dump_blocks_update(self):
        """pg_dump 缺失同样是「备份必失败」，不能按「探测工具缺失」放过。"""
        ok, detail = self._database_state(
            which=lambda name: None if name == "pg_dump" else f"/usr/bin/{name}"
        )
        self.assertFalse(ok)
        self.assertIn("pg_dump", detail)

    def test_unreachable_database_still_blocks(self):
        ok, detail = self._database_state(pg_isready_ok=False)
        self.assertFalse(ok)
        self.assertIn("不可访问", detail)

    def test_sqlite_backend_is_untouched(self):
        adapter = DefaultPreflightEnvAdapter(Path("/tmp/deploy"))
        with mock.patch.object(settings, "DATABASE_BACKEND", "sqlite"):
            ok, detail = adapter._database_state()
        self.assertTrue(ok)
        self.assertIn("SQLite", detail)


if __name__ == "__main__":
    unittest.main()
