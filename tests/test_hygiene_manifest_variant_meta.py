#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准图清单必须声明「实际会下发的那一份」的字节数与摘要。

员工端下的是 ``?variant=preview`` 的缩放图，拿到字节后按清单里的 ``byte_size`` /
``sha256`` 校验。清单若声明原图的摘要，客户端必然判「图片大小不一致」——缓存永远
建立不起来，而且每次重试都一样（线上就是 6 张全失败）。这里把两条路径都钉住：
有变体就用变体的元数据，变体文件没了就退回原图（服务端此时也会回落到原图）。
"""

import asyncio
import io
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.captures import FileCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork

SUPER = {"kind": "super"}
STAFF = {"kind": "staff", "id": 1, "permission": "普通员工"}


def _jpeg(width: int = 2000, height: int = 1500) -> bytes:
    """比 PREVIEW_MAX_EDGE(1600) 大，确保真的会生成 preview 变体。"""
    output = io.BytesIO()
    Image.new("RGB", (width, height), (30, 60, 90)).save(output, format="JPEG", quality=95)
    return output.getvalue()


class StandardManifestVariantTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_dir = settings.DATABASE_DIR
        self._tmp = TemporaryDirectory()
        settings.DATABASE_DIR = self._tmp.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.captures = FileCaptureStore(Path(self._tmp.name) / "captures")
        self.work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: datetime(2026, 9, 21, 10, 0, tzinfo=CHINA_TZ),
            image_variants=ImageVariantGenerator(),
        )
        await self.work.prepare()
        # 建库时已经 seed 了 8 个责任区，直接复用，别再建同名区。
        zones = await self.work.list_zones()
        self.zone = zones[0]
        self.other_zone = zones[4]
        self.item = await self.work.add_daily_item(
            SUPER, self.zone["id"], "台面", {"bytes": _jpeg(), "markup": []}
        )

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_dir
        self._tmp.cleanup()

    async def _variant_row(self, capture_id: str, variant: str = "preview"):
        cur = await self.db._conn.execute(
            """SELECT capture_id, byte_size, content_sha256
               FROM hygiene_capture_variants
               WHERE source_capture_id = ? AND variant = ?""",
            (capture_id, variant),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def test_preview_manifest_declares_the_variant_bytes(self):
        variant = await self._variant_row(self.item["capture_id"])
        self.assertIsNotNone(variant, "测试前提：上传后应该生成了 preview 变体")

        manifest = await self.work.standard_manifest(variant="preview")
        self.assertEqual(len(manifest["standards"]), 1)
        entry = manifest["standards"][0]

        self.assertEqual(entry["byte_size"], variant["byte_size"])
        self.assertEqual(entry["sha256"], variant["content_sha256"])

        # 与「声明原图」那条路径必须不同，否则这个用例什么都没钉住。
        original = await self.work.standard_manifest(variant="original")
        self.assertNotEqual(entry["sha256"], original["standards"][0]["sha256"])

    async def _original_row(self):
        cur = await self.db._conn.execute(
            """SELECT content_sha256, byte_size FROM hygiene_standards
               WHERE id = ?""",
            (self.item["current_standard_id"],),
        )
        return dict(await cur.fetchone())

    async def test_manifest_defaults_to_original_bytes(self):
        original_row = await self._original_row()
        manifest = await self.work.standard_manifest()

        self.assertEqual(manifest["standards"][0]["byte_size"], original_row["byte_size"])
        self.assertEqual(manifest["standards"][0]["sha256"], original_row["content_sha256"])

        variant_manifest = await self.work.standard_manifest(variant="preview")
        self.assertNotEqual(
            manifest["standards"][0]["sha256"],
            variant_manifest["standards"][0]["sha256"],
        )

    async def test_missing_variant_file_falls_back_to_original_meta(self):
        variant = await self._variant_row(self.item["capture_id"])
        self.assertIsNotNone(variant)

        # 变体文件被清理掉：服务端会回落到原图，清单也必须跟着退回原图，
        # 否则又变成「声明变体、下发原图」的镜像错误。
        await self.captures.delete_async(variant["capture_id"])
        self.work._readable_capture_cache = None  # 丢掉「磁盘上有哪些文件」的 TTL 缓存

        manifest = await self.work.standard_manifest(variant="preview")
        original = await self.work.standard_manifest(variant="original")

        self.assertEqual(
            manifest["standards"][0]["sha256"],
            original["standards"][0]["sha256"],
        )

    async def test_staff_manifest_stays_sliced_to_own_zone(self):
        other_item = await self.work.add_daily_item(
            SUPER, self.other_zone["id"], "工作台", {"bytes": _jpeg(), "markup": []}
        )

        manifest = await self.work.standard_manifest(
            {**STAFF, "zone_id": self.zone["id"]}, variant="preview"
        )
        item_ids = [entry["item_id"] for entry in manifest["standards"]]

        self.assertIn(self.item["id"], item_ids)
        self.assertNotIn(other_item["id"], item_ids)


if __name__ == "__main__":
    unittest.main()
