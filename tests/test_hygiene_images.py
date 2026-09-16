#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Image derivative generation and variant fallback tests."""

import hashlib
import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from PIL import Image

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene import work as hygiene_work_module
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.captures import FileCaptureStore
from services.hygiene.images import ImageVariantGenerator, InvalidImageError
from services.hygiene.work import HygieneWork, HygieneWorkError

SUPER = {"kind": "super"}


def jpeg_bytes(width=800, height=600, color=(120, 180, 220)):
    image = Image.new("RGB", (width, height), color)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=88)
    return output.getvalue()


class ImageVariantGeneratorTest(unittest.TestCase):
    def test_generates_bounded_jpeg_variants_and_keeps_original_unchanged(self):
        original = jpeg_bytes(2000, 1200)
        generated = ImageVariantGenerator().generate(original)
        self.assertEqual(set(generated), {"thumb", "preview"})
        self.assertLessEqual(generated["thumb"].width, 480)
        self.assertLessEqual(generated["thumb"].height, 480)
        self.assertLessEqual(generated["preview"].width, 1600)
        self.assertLessEqual(generated["preview"].height, 1600)
        self.assertNotEqual(generated["thumb"].data, original)
        self.assertEqual(original, original)

    def test_rejects_invalid_bytes(self):
        with self.assertRaises(InvalidImageError):
            ImageVariantGenerator().generate(b"not-an-image")


class FileCaptureStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_async_put_get_exists_path_and_delete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileCaptureStore(Path(tmpdir))
            capture_id = await store.put_async(b"original", "image/jpeg")
            self.assertTrue(await store.exists_async(capture_id))
            self.assertEqual(await store.get_async(capture_id), b"original")
            self.assertIsNotNone(await store.path_async(capture_id))
            self.assertEqual(await store.list_ids_async(), [capture_id])

            await store.delete_async(capture_id)
            self.assertFalse(await store.exists_async(capture_id))
            self.assertEqual(await store.list_ids_async(), [])


class HygieneVariantIntegrationTest(unittest.IsolatedAsyncioTestCase):
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
            image_variants=ImageVariantGenerator(),
        )
        await self.work.prepare()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_new_standard_stores_original_and_variants(self):
        original = jpeg_bytes()
        zone = (await self.work.list_zones())[0]
        item = await self.work.add_daily_item(
            SUPER,
            zone["id"],
            "案板表面",
            {"bytes": original, "content_type": "image/jpeg", "markup": []},
        )
        standard = await self.work.standard_version(item["current_standard_id"])
        self.assertEqual(await self.work.capture_bytes(standard["capture_id"]), original)
        thumb = await self.work.capture_view(standard["capture_id"], "thumb")
        preview = await self.work.capture_view(standard["capture_id"], "preview")
        self.assertEqual(thumb["variant"], "thumb")
        self.assertEqual(preview["variant"], "preview")
        self.assertNotEqual(thumb["capture_id"], standard["capture_id"])
        self.assertNotEqual(preview["capture_id"], standard["capture_id"])
        self.assertEqual(thumb["content_type"], "image/jpeg")
        self.assertNotEqual(await self.work.capture_bytes(thumb["capture_id"]), original)

    async def test_missing_variant_falls_back_to_original(self):
        original = jpeg_bytes()
        zone = (await self.work.list_zones())[0]
        item = await self.work.add_daily_item(
            SUPER,
            zone["id"],
            "案板表面",
            {"bytes": original, "content_type": "image/jpeg", "markup": []},
        )
        standard = await self.work.standard_version(item["current_standard_id"])
        await self.db._conn.execute(
            "DELETE FROM hygiene_capture_variants WHERE source_capture_id = ?",
            (standard["capture_id"],),
        )
        await self.db._conn.commit()
        view = await self.work.capture_view(standard["capture_id"], "thumb")
        self.assertTrue(view["fallback"])
        self.assertEqual(view["capture_id"], standard["capture_id"])

    async def test_invalid_standard_rejected_with_real_generator(self):
        zone = (await self.work.list_zones())[0]
        with self.assertRaises(HygieneWorkError) as raised:
            await self.work.add_daily_item(
                SUPER,
                zone["id"],
                "坏图",
                {"bytes": b"not-an-image", "content_type": "image/jpeg", "markup": []},
            )
        self.assertEqual(raised.exception.code, "invalid_image")

    async def test_backfill_adds_variants_for_legacy_capture(self):
        original = jpeg_bytes()
        zone = (await self.work.list_zones())[0]
        no_variant_work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: datetime(2026, 9, 14, 10, 0, tzinfo=CHINA_TZ),
        )
        item = await no_variant_work.add_daily_item(
            SUPER,
            zone["id"],
            "旧检查项",
            {"bytes": original, "content_type": "image/jpeg", "markup": []},
        )
        standard = await self.work.standard_version(item["current_standard_id"])
        created = await self.work.backfill_capture_variants_once()
        self.assertEqual(created, 1)
        thumb = await self.work.capture_view(standard["capture_id"], "thumb")
        self.assertFalse(thumb["fallback"])

    async def test_backfill_never_treats_derivatives_as_sources(self):
        zone = (await self.work.list_zones())[0]
        legacy_work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: datetime(2026, 9, 14, 10, 0, tzinfo=CHINA_TZ),
        )
        item = await legacy_work.add_daily_item(
            SUPER,
            zone["id"],
            "旧检查项",
            {"bytes": jpeg_bytes(), "content_type": "image/jpeg", "markup": []},
        )
        self.assertEqual(await self.work.backfill_capture_variants_once(), 1)
        self.assertEqual(await self._variant_sources(), {item["capture_id"]})
        # A second pass must be a no-op: thumbnails of thumbnails never become work.
        self.assertEqual(await self.work.backfill_capture_variants_once(), 0)
        self.assertEqual(await self._variant_sources(), {item["capture_id"]})
        self.assertEqual(len(self.captures.blobs), 3)

    async def test_backfill_chunks_referenced_ids(self):
        zone = (await self.work.list_zones())[0]
        legacy_work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: datetime(2026, 9, 14, 10, 0, tzinfo=CHINA_TZ),
        )
        expected = set()
        for name in ("旧项一", "旧项二", "旧项三"):
            item = await legacy_work.add_daily_item(
                SUPER,
                zone["id"],
                name,
                {"bytes": jpeg_bytes(), "content_type": "image/jpeg", "markup": []},
            )
            expected.add(item["capture_id"])
        with mock.patch.object(hygiene_work_module, "SQL_ID_CHUNK_SIZE", 1):
            self.assertEqual(await self.work.backfill_capture_variants_once(), 3)
            self.assertEqual(await self.work.backfill_capture_variants_once(), 0)
        self.assertEqual(await self._variant_sources(), expected)

    async def test_sweep_drops_recursive_variant_rows_and_files(self):
        zone = (await self.work.list_zones())[0]
        item = await self.work.add_daily_item(
            SUPER,
            zone["id"],
            "案板表面",
            {"bytes": jpeg_bytes(), "content_type": "image/jpeg", "markup": []},
        )
        thumb = await self.work.capture_view(item["capture_id"], "thumb")
        # Simulate the runaway: a variant row whose source is itself a derivative.
        junk = jpeg_bytes(64, 48)
        junk_id = await self.captures.put_async(junk, content_type="image/jpeg")
        await self.db._conn.execute(
            """INSERT INTO hygiene_capture_variants
               (source_capture_id, variant, capture_id, content_type,
                width, height, byte_size, content_sha256, created_at)
               VALUES (?, 'thumb', ?, 'image/jpeg', 64, 48, ?, ?, ?)""",
            (
                thumb["capture_id"],
                junk_id,
                len(junk),
                hashlib.sha256(junk).hexdigest(),
                "2026-09-14T09:00:00+08:00",
            ),
        )
        await self.db._conn.commit()

        # The fake store reports mtimes from `time.time()`, so sweep with a clock
        # a little ahead of it to make every capture old enough to be collected.
        sweeper = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: datetime.now(CHINA_TZ) + timedelta(hours=1),
        )
        self.assertEqual(await sweeper.sweep_capture_orphans(max_age_seconds=0), 1)
        self.assertEqual(await self._variant_sources(), {item["capture_id"]})
        self.assertFalse(await self.captures.exists_async(junk_id))
        self.assertTrue(await self.captures.exists_async(item["capture_id"]))
        self.assertTrue(await self.captures.exists_async(thumb["capture_id"]))

    async def _variant_sources(self) -> set[str]:
        cur = await self.db._conn.execute(
            "SELECT DISTINCT source_capture_id FROM hygiene_capture_variants"
        )
        return {str(row[0]) for row in await cur.fetchall()}


if __name__ == "__main__":
    unittest.main()
