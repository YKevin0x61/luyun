#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Log storage self-healing contract."""

import tempfile
import unittest
from pathlib import Path

from services.log_storage import LogStorage


class LogStorageRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "logs.db"
        self.storage = LogStorage()
        self.storage._db_path = str(self.db_path)

    async def asyncTearDown(self):
        await self.storage.stop()
        self._tmpdir.cleanup()

    async def test_corrupt_store_is_quarantined_and_rebuilt(self):
        self.db_path.write_bytes(b"\xff" * 4096)

        self.assertTrue(await self.storage.start())

        quarantined = list(self.db_path.parent.glob("logs.db.corrupt.*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_bytes(), b"\xff" * 4096)
        stats = await self.storage.stats()
        self.assertEqual(stats["total"], 0)
        self.assertFalse(stats["degraded"])

    async def test_public_corruption_predicate(self):
        self.assertTrue(
            LogStorage.is_corruption_error(Exception("database disk image is malformed"))
        )
        self.assertTrue(
            LogStorage.is_corruption_error(Exception("file is not a database"))
        )
        self.assertFalse(
            LogStorage.is_corruption_error(Exception("no such table: logs"))
        )


if __name__ == "__main__":
    unittest.main()
