#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pg_dump 失败时必须说清原因（脱敏后），不能只报一个退出码。

现场更新作业卡在 ``backing_up``，日志里只有「pg_dump 失败（退出码 1）」——
stderr 被整段丢弃，真正的根因（客户端 15 低于服务端 16）完全不可见，排查只能靠猜。
"""

import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from services.backup_service import _pg_dump_sync

DSN = "postgresql://luyun:sup3r-s3cret@pg:5432/luyun"


class PgDumpFailureMessageTest(unittest.TestCase):
    def _failure_message(self, stderr: str) -> str:
        fake = types.SimpleNamespace(returncode=1, stdout="", stderr=stderr)
        with mock.patch.dict(os.environ, {"LUYUN_POSTGRES_DSN": DSN}), mock.patch(
            "services.backup_service.subprocess.run", return_value=fake
        ):
            with tempfile.TemporaryDirectory() as tmp:
                dst = str(Path(tmp) / "app.pgdump")
                with self.assertRaises(RuntimeError) as ctx:
                    _pg_dump_sync(dst)
        return str(ctx.exception)

    def test_version_mismatch_is_explained(self):
        """版本不匹配要带上 stderr 原文 + 可执行的处置建议。"""
        msg = self._failure_message(
            "pg_dump: error: server version: 16.4; pg_dump version: 15.8\n"
            "pg_dump: error: aborting because of server version mismatch\n"
        )
        self.assertIn("aborting because of server version mismatch", msg)
        self.assertIn("重建镜像", msg)

    def test_auth_failure_is_explained(self):
        msg = self._failure_message(
            'pg_dump: error: connection to server at "pg" failed: '
            'FATAL:  password authentication failed for user "luyun"\n'
        )
        self.assertIn("password authentication failed", msg)
        self.assertIn("认证失败", msg)

    def test_password_in_stderr_is_redacted(self):
        """pg_dump 偶尔会把连接串回显出来，密码不能跟着进日志。"""
        msg = self._failure_message(
            f"pg_dump: error: could not connect: {DSN}\n"
        )
        self.assertNotIn("sup3r-s3cret", msg)
        self.assertIn("***", msg)

    def test_exit_code_is_still_reported(self):
        msg = self._failure_message("pg_dump: error: something odd\n")
        self.assertIn("退出码 1", msg)
        self.assertIn("something odd", msg)


if __name__ == "__main__":
    unittest.main()
