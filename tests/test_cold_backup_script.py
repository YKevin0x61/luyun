#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冷备入口脚本契约：退出码、状态文件与不留半成品。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from config import settings
from scripts import cold_backup
from services import backup_service


class ColdBackupScriptTest(unittest.TestCase):
    def setUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._old_cold_dir = settings.COLD_BACKUP_DIR
        self._old_data_env = os.environ.pop("DATA_DIR", None)
        self._old_backup_env = os.environ.pop("BACKUP_DIR", None)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmpdir.name) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        settings.DATABASE_DIR = str(self.data_dir)
        settings.COLD_BACKUP_DIR = str(Path(self._tmpdir.name) / "backups")

    def tearDown(self):
        settings.DATABASE_DIR = self._old_database_dir
        settings.COLD_BACKUP_DIR = self._old_cold_dir
        if self._old_data_env is not None:
            os.environ["DATA_DIR"] = self._old_data_env
        if self._old_backup_env is not None:
            os.environ["BACKUP_DIR"] = self._old_backup_env
        self._tmpdir.cleanup()

    def _make_app_db(self) -> None:
        import sqlite3

        conn = sqlite3.connect(settings.APP_DB_PATH)
        conn.executescript(
            """
            CREATE TABLE orders (id INTEGER PRIMARY KEY, business_flow_id TEXT);
            CREATE TABLE tables (id INTEGER PRIMARY KEY);
            CREATE TABLE dish_stations (id INTEGER PRIMARY KEY, dish_name TEXT);
            """
        )
        conn.commit()
        conn.close()

    def test_success_exits_zero_and_writes_status(self):
        self._make_app_db()
        code = cold_backup.main([])
        self.assertEqual(code, 0)

        status = json.loads(
            backup_service.cold_status_path().read_text(encoding="utf-8")
        )
        self.assertTrue(status["ok"])
        self.assertTrue(Path(status["archive"]).is_file())
        self.assertEqual(Path(status["archive"]).name, backup_service.COLD_ARCHIVE_NAME)
        self.assertIsNone(status["error"])

    def test_missing_database_exits_nonzero_and_records_failure(self):
        code = cold_backup.main([])
        self.assertEqual(code, 1)
        status = json.loads(
            backup_service.cold_status_path().read_text(encoding="utf-8")
        )
        self.assertFalse(status["ok"])
        self.assertIn("业务数据库不存在", status["error"] or "")

    def test_retention_argument_prunes_old_runs(self):
        self._make_app_db()
        backup_dir = Path(settings.COLD_BACKUP_DIR)
        for ts in ("20260101_000001", "20260101_000002", "20260101_000003"):
            (backup_dir / ts).mkdir(parents=True, exist_ok=True)
            (backup_dir / ts / backup_service.COLD_ARCHIVE_NAME).write_bytes(b"old")

        code = cold_backup.main(["--retention", "2"])
        self.assertEqual(code, 0)

        remaining = sorted(
            d.name for d in backup_dir.iterdir() if d.is_dir()
        )
        self.assertEqual(len(remaining), 2)
        self.assertNotIn("20260101_000001", remaining)

    def test_failed_run_removes_half_written_product(self):
        self._make_app_db()
        backup_dir = Path(settings.COLD_BACKUP_DIR)
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / "20260101_000001").mkdir()
        (backup_dir / "20260101_000001" / ".luyun_cold_backup.tar.tmp").write_bytes(b"x")

        with mock.patch.object(
            backup_service,
            "build_cold_backup_archive",
            side_effect=RuntimeError("disk full"),
        ):
            code = cold_backup.main([])

        self.assertEqual(code, 1)
        status = json.loads(
            backup_service.cold_status_path().read_text(encoding="utf-8")
        )
        self.assertFalse(status["ok"])
        self.assertIn("disk full", status["error"] or "")


if __name__ == "__main__":
    unittest.main()
