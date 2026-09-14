#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Current-standard manifest and immutable standard-version contract."""

import hashlib
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.work import HygieneWork, HygieneWorkError

SUPER = {"kind": "super"}
OLD_BYTES = b"OLD-STANDARD-JPEG"
NEW_BYTES = b"NEW-STANDARD-JPEG"


class HygieneStandardCacheTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.captures = FakeCaptureStore()
        self.work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: datetime(2026, 9, 14, 10, 0, tzinfo=CHINA_TZ),
            notifier=None,
        )
        await self.work.prepare()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def _capture(self, data):
        return {"bytes": data, "content_type": "image/jpeg", "markup": []}

    async def _add_item(self):
        zone = (await self.work.list_zones())[0]
        return await self.work.add_daily_item(
            SUPER,
            zone["id"],
            "案板表面",
            self._capture(OLD_BYTES),
        )

    async def test_manifest_lists_current_version_and_keeps_old_version_addressable(self):
        item = await self._add_item()
        first = await self.work.standard_manifest()
        first_entry = first["standards"][0]
        self.assertEqual(first_entry["item_id"], item["id"])
        self.assertEqual(first_entry["standard_id"], item["current_standard_id"])
        self.assertEqual(first_entry["byte_size"], len(OLD_BYTES))
        self.assertEqual(first_entry["sha256"], hashlib.sha256(OLD_BYTES).hexdigest())
        self.assertTrue(first["version"])

        replaced = await self.work.replace_standard(
            SUPER,
            item["id"],
            self._capture(NEW_BYTES),
        )
        second = await self.work.standard_manifest()
        second_entry = second["standards"][0]
        self.assertEqual(second_entry["standard_id"], replaced["current_standard_id"])
        self.assertEqual(second_entry["sha256"], hashlib.sha256(NEW_BYTES).hexdigest())
        self.assertNotEqual(first["version"], second["version"])

        old = await self.work.standard_version(item["current_standard_id"])
        self.assertEqual(self.work.capture_bytes(old["capture_id"]), OLD_BYTES)
        current = await self.work.standard_version(replaced["current_standard_id"])
        self.assertEqual(self.work.capture_bytes(current["capture_id"]), NEW_BYTES)

    async def test_prepare_backfills_legacy_metadata(self):
        item = await self._add_item()
        await self.db._conn.execute(
            "UPDATE hygiene_standards SET byte_size = NULL, content_sha256 = NULL WHERE id = ?",
            (item["current_standard_id"],),
        )
        await self.db._conn.commit()

        await self.work.prepare()

        standard = await self.work.standard_version(item["current_standard_id"])
        self.assertEqual(standard["byte_size"], len(OLD_BYTES))
        self.assertEqual(standard["sha256"], hashlib.sha256(OLD_BYTES).hexdigest())

    async def test_manifest_skips_missing_capture_without_advertising_it(self):
        item = await self._add_item()
        self.captures.blobs.pop(item["capture_id"])

        await self.work.prepare()
        manifest = await self.work.standard_manifest()

        self.assertEqual(manifest["standards"], [])

    async def test_new_standard_over_limit_is_rejected(self):
        zone = (await self.work.list_zones())[0]
        with patch("services.hygiene.work.MAX_STANDARD_BYTES", 8):
            with self.assertRaises(HygieneWorkError) as raised:
                await self.work.add_daily_item(
                    SUPER,
                    zone["id"],
                    "案板表面",
                    self._capture(b"123456789"),
                )
        self.assertEqual(raised.exception.code, "standard_too_large")
