#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""logs.db 抗满盘硬化：磁盘满 vs 损坏、quarantine 上限、运行期维护。"""

import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from services.log_storage import CHINA_TZ, LogStorage


class _FakeConn:
    """只实现 _flush 用到的最小接口，用来注入指定错误。"""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.commits = 0
        self.rollbacks = 0

    async def executemany(self, *args, **kwargs):
        raise self.error

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _record(message: str = "hello") -> dict:
    return {
        "ts": datetime.now(CHINA_TZ).isoformat(),
        "ts_epoch": time.time(),
        "level": "INFO",
        "logger": "tests",
        "message": message,
        "exception": None,
    }


class LogStorageDiskFullTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "logs.db"
        self.storage = LogStorage()
        self.storage._db_path = str(self.db_path)

    async def asyncTearDown(self):
        self.storage._conn = None
        try:
            await self.storage.stop()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_disk_full_predicate_does_not_match_corruption(self):
        self.assertTrue(
            LogStorage.is_disk_full_error(
                sqlite3.OperationalError("database or disk is full")
            )
        )
        self.assertTrue(
            LogStorage.is_disk_full_error(
                sqlite3.OperationalError("disk I/O error")
            )
        )
        self.assertFalse(
            LogStorage.is_disk_full_error(
                sqlite3.DatabaseError("database disk image is malformed")
            )
        )

    async def test_disk_full_drops_batch_without_quarantining(self):
        fake = _FakeConn(sqlite3.OperationalError("database or disk is full"))
        self.storage._conn = fake

        await self.storage._flush([_record()])

        self.assertEqual(fake.rollbacks, 1, "must roll back the half-open transaction")
        self.assertEqual(self.storage._dropped, 1)
        self.assertEqual(list(self.db_path.parent.glob("logs.db.corrupt.*")), [])
        self.assertFalse(self.storage._degraded)

    async def test_disk_full_keeps_writable_after_space_returns(self):
        self.storage._conn = _FakeConn(
            sqlite3.OperationalError("database or disk is full")
        )
        await self.storage._flush([_record()])

        # 空间恢复后仍能正常写入（说明连接没有被隔离/关闭）。
        await self.storage._open_and_prepare()
        await self.storage._flush([_record("after")])
        _, total = await self.storage.query()
        self.assertEqual(total, 1)


class LogStorageQuarantineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "logs.db"
        self.storage = LogStorage()
        self.storage._db_path = str(self.db_path)

    async def asyncTearDown(self):
        self.storage._conn = None
        try:
            await self.storage.stop()
        except Exception:
            pass
        self._tmpdir.cleanup()

    async def test_malformed_flush_quarantines_and_rebuilds(self):
        await self.storage._open_and_prepare()
        self.storage._conn = _FakeConn(
            sqlite3.DatabaseError("database disk image is malformed")
        )

        await self.storage._flush([_record()])

        copies = list(self.db_path.parent.glob("logs.db.corrupt.*"))
        self.assertTrue(copies, "damaged store must be kept for inspection")
        self.assertTrue(self.db_path.exists(), "a fresh store must be created")

    async def test_force_quarantine_accepts_non_malformed_reason(self):
        """quick_check 报的是 "Page N is never used" 这类文本，也要按损坏处理。"""
        await self.storage._open_and_prepare()

        ok = await self.storage._rebuild_after_corruption(
            RuntimeError("Page 3 is never used"), force=True
        )

        self.assertTrue(ok)
        self.assertTrue(list(self.db_path.parent.glob("logs.db.corrupt.*")))

    async def test_forensics_sidecar_records_disk_state(self):
        await self.storage._open_and_prepare()

        await self.storage._rebuild_after_corruption(RuntimeError("boom"), force=True)

        sidecars = list(self.db_path.parent.glob("logs.db.corrupt.*.forensics.txt"))
        self.assertEqual(len(sidecars), 1)
        text = sidecars[0].read_text(encoding="utf-8")
        self.assertIn("disk_free_mb=", text)
        self.assertIn("boom", text)

    async def test_prune_keeps_only_latest_copies(self):
        parent = self.db_path.parent
        stamps = ("20260101_010101", "20260102_010101", "20260103_010101")
        for stamp in stamps:
            (parent / f"logs.db.corrupt.{stamp}").write_bytes(b"x")
            (parent / f"logs.db.corrupt.{stamp}.forensics.txt").write_text(
                "y", encoding="utf-8"
            )

        removed = self.storage._prune_quarantine_copies(2)

        self.assertEqual(removed, 2)
        left = sorted(p.name for p in parent.glob("logs.db.corrupt.*"))
        self.assertEqual(
            left,
            [
                "logs.db.corrupt.20260102_010101",
                "logs.db.corrupt.20260102_010101.forensics.txt",
                "logs.db.corrupt.20260103_010101",
                "logs.db.corrupt.20260103_010101.forensics.txt",
            ],
        )

    def test_prune_zero_means_unlimited(self):
        parent = self.db_path.parent
        (parent / "logs.db.corrupt.20260101_010101").write_bytes(b"x")
        self.assertEqual(self.storage._prune_quarantine_copies(0), 0)
        self.assertTrue((parent / "logs.db.corrupt.20260101_010101").exists())


class LogStorageMaintenanceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "logs.db"
        self.storage = LogStorage()
        self.storage._db_path = str(self.db_path)

    async def asyncTearDown(self):
        await self.storage.stop()
        self._tmpdir.cleanup()

    async def test_pragmas_are_wal_and_normal(self):
        await self.storage._open_and_prepare()

        async with self.storage._conn.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
        self.assertEqual(str(row[0]).lower(), "wal")

        async with self.storage._conn.execute("PRAGMA synchronous") as cur:
            row = await cur.fetchone()
        self.assertEqual(int(row[0]), 1, "NORMAL (1) is the intended trade-off")

    async def test_quick_check_passes_on_fresh_store(self):
        await self.storage._open_and_prepare()
        self.assertIsNone(await self.storage._quick_check())

    async def test_maintenance_deletes_expired_rows(self):
        await self.storage._open_and_prepare()
        old_epoch = (datetime.now(CHINA_TZ) - timedelta(days=30)).timestamp()
        await self.storage._conn.executemany(
            "INSERT INTO logs (ts, ts_epoch, level, logger, message, exception) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("old", old_epoch, "INFO", "tests", "expired", None),
                ("new", time.time(), "INFO", "tests", "fresh", None),
            ],
        )
        await self.storage._conn.commit()

        await self.storage._run_maintenance()

        _, total = await self.storage.query()
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
