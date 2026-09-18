#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""迁移脚本的两条现场教训。

1. DSN 里带密码，而脚本会把它打印出来 —— 现场报告里 DSN 就连着密码被贴进了
   工单/聊天记录。输出必须脱敏。
2. 目标库连不上时要给人话（返回 1），而不是甩一段 traceback；并且要明确宣告
   「dry-run 也真连了库」，避免再被当成假阳性。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.archive.migrate_sqlite_to_postgres import migrate, redact_dsn


class RedactDsnTest(unittest.TestCase):
    def test_password_is_masked(self):
        self.assertEqual(
            redact_dsn("postgresql://luyun:s3cret@postgres:5432/luyun"),
            "postgresql://luyun:***@postgres:5432/luyun",
        )

    def test_encoded_special_chars_in_password_are_masked(self):
        self.assertEqual(
            redact_dsn("postgresql://u:p%40ss%2Fw0rd@h:5432/db"),
            "postgresql://u:***@h:5432/db",
        )

    def test_dsn_without_password_is_unchanged(self):
        self.assertEqual(
            redact_dsn("postgresql://localhost:5432/luyun"),
            "postgresql://localhost:5432/luyun",
        )


class MigrateConnectFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_unreachable_target_returns_1_instead_of_raising(self):
        """连不上目标库：返回 1 并说明检查点，不抛 traceback。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "app.db"
            sqlite3.connect(str(db)).close()  # 空源库，唯一失败点是目标库
            rc = await migrate(str(db), "postgresql://127.0.0.1:1/nope", apply=False)
        self.assertEqual(rc, 1)

    async def test_missing_source_returns_1(self):
        rc = await migrate("/nonexistent/app.db", "postgresql://localhost/luyun", apply=False)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
