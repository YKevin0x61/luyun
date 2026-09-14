#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Image derivative generation and variant fallback tests."""

import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image

from config import settings
from database import CHINA_TZ, DatabaseManager
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


if __name__ == "__main__":
    unittest.main()
