#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冷备保留份数的来源必须可追溯，读取失败绝不据此删备份（PERF-10）。

原实现：`load_from_pg_sync()` 读不到配置就静默回退默认值 14，脚本随后**按 14 剪除**
冷备。库连不上/配置读失败时，「回退值」和「管理员真正选的份数」无法区分——如果
管理员选的是 30，一次数据库抖动就会把第 15~30 份冷备删掉，而且是静默的。

现在：读取失败 → 回退默认值但**跳过清理**并告警；状态文件写 `retention_source`
与 `cold_keep`，后台面板据此显示本次用的是谁的值。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from config import settings
from scripts import cold_backup
from services import backup_retention, backup_service


class ColdBackupRetentionTest(unittest.TestCase):
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
        self.backup_dir = Path(settings.COLD_BACKUP_DIR)

    def tearDown(self):
        settings.DATABASE_DIR = self._old_database_dir
        settings.COLD_BACKUP_DIR = self._old_cold_dir
        if self._old_data_env is not None:
            os.environ["DATA_DIR"] = self._old_data_env
        if self._old_backup_env is not None:
            os.environ["BACKUP_DIR"] = self._old_backup_env
        self._tmpdir.cleanup()

    # ---- 夹具：不真跑 pg_dump，只喂一份「已产出的归档 + manifest」 ----

    def _fake_archive(self):
        run_dir = self.backup_dir / "20260102_000001"
        run_dir.mkdir(parents=True, exist_ok=True)
        archive = run_dir / backup_service.COLD_ARCHIVE_NAME
        archive.write_bytes(b"fake-archive")
        manifest = {
            "archive_bytes": len(b"fake-archive"),
            "archive_sha256": "0" * 64,
            "contents": ["app_pg"],
            "contents_labels": ["业务数据 (PostgreSQL)"],
            "consistency": {"ok": True, "errors": []},
        }
        return archive, manifest

    def _seed_old_runs(self) -> None:
        for ts in ("20260101_000001", "20260101_000002", "20260101_000003"):
            (self.backup_dir / ts).mkdir(parents=True, exist_ok=True)
            (self.backup_dir / ts / backup_service.COLD_ARCHIVE_NAME).write_bytes(b"old")

    def _status(self) -> dict:
        return json.loads(
            backup_service.cold_status_path().read_text(encoding="utf-8")
        )

    def test_read_failure_skips_prune_and_records_fallback(self):
        """读取失败：不剪除、告警、状态写 default-fallback + 实际回退值。"""
        self._seed_old_runs()
        loaded = backup_retention.RetentionLoad(
            backup_retention.RetentionConfig(),
            backup_retention.RETENTION_SOURCE_FALLBACK,
            error="冷备读取保留配置失败：数据库连不上",
        )
        with mock.patch.object(
            backup_service,
            "build_cold_backup_archive",
            return_value=self._fake_archive(),
        ), mock.patch.object(
            backup_retention, "load_from_pg_sync_detailed", return_value=loaded
        ), mock.patch.object(
            backup_service, "prune_cold_backups", return_value=[]
        ) as prune, self.assertLogs("cold_backup", level="WARNING") as logs:
            code = cold_backup.main([])

        self.assertEqual(code, 0)
        prune.assert_not_called()
        remaining = sorted(d.name for d in self.backup_dir.iterdir() if d.is_dir())
        self.assertEqual(len(remaining), 4)  # 三份旧冷备 + 本次新归档，一份都没删
        status = self._status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["retention_source"], "default-fallback")
        self.assertEqual(status["cold_keep"], backup_retention.COLD_KEEP_DEFAULT)
        self.assertTrue(
            any("跳过清理" in line for line in logs.output),
            logs.output,
        )

    def test_explicit_retention_argument_prunes_as_before(self):
        """命令行显式给出份数：照常剪除，来源记为 argument。"""
        self._seed_old_runs()
        with mock.patch.object(
            backup_service,
            "build_cold_backup_archive",
            return_value=self._fake_archive(),
        ), mock.patch.object(
            backup_service, "prune_cold_backups", return_value=["20260101_000001"]
        ) as prune, mock.patch.object(
            backup_service, "sweep_stale_restore_dumps", return_value=[]
        ) as sweep:
            code = cold_backup.main(["--retention", "2"])

        sweep.assert_called_once()  # SEC-08：冷备入口也顺手扫残留临时 dump
        self.assertEqual(code, 0)
        prune.assert_called_once_with(2)
        status = self._status()
        self.assertEqual(status["retention_source"], "argument")
        self.assertEqual(status["cold_keep"], 2)

    def test_configured_retention_prunes_with_stored_value(self):
        """读到有效配置：按库里的份数剪除，来源记为 configured。"""
        self._seed_old_runs()
        loaded = backup_retention.RetentionLoad(
            backup_retention.RetentionConfig(cold_keep=3),
            backup_retention.RETENTION_SOURCE_CONFIGURED,
        )
        with mock.patch.object(
            backup_service,
            "build_cold_backup_archive",
            return_value=self._fake_archive(),
        ), mock.patch.object(
            backup_retention, "load_from_pg_sync_detailed", return_value=loaded
        ), mock.patch.object(
            backup_service, "prune_cold_backups", return_value=[]
        ) as prune:
            code = cold_backup.main([])

        self.assertEqual(code, 0)
        prune.assert_called_once_with(3)
        status = self._status()
        self.assertEqual(status["retention_source"], "configured")
        self.assertEqual(status["cold_keep"], 3)

    def test_no_stored_config_uses_default_but_still_prunes(self):
        """库里没写过保留配置 = 「默认值」而不是「读取失败」，照常剪除。"""
        loaded = backup_retention.RetentionLoad(
            backup_retention.RetentionConfig(),
            backup_retention.RETENTION_SOURCE_DEFAULT,
        )
        with mock.patch.object(
            backup_service,
            "build_cold_backup_archive",
            return_value=self._fake_archive(),
        ), mock.patch.object(
            backup_retention, "load_from_pg_sync_detailed", return_value=loaded
        ), mock.patch.object(
            backup_service, "prune_cold_backups", return_value=[]
        ) as prune:
            code = cold_backup.main([])

        self.assertEqual(code, 0)
        prune.assert_called_once_with(backup_retention.COLD_KEEP_DEFAULT)
        status = self._status()
        self.assertEqual(status["retention_source"], "default")


if __name__ == "__main__":
    unittest.main()
