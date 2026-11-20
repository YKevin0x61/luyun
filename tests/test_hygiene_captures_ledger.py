#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""照片台账：一张表回答「谁持有、谁可达、这张图是什么」。

这条链上有三个问题，以前各写各的：

1. **谁持有这张图**（清扫用它判断能不能删）—— `_referenced_capture_ids` 列了 10 处，
   但 `_original_capture_meta` 只认 6 处；
2. **这张图是什么**（Content-Type / 大小 / 摘要）—— 6 处之外的一律按 `image/jpeg` 猜，
   于是教学对照图与仪容的 PNG 被当成 JPEG 下发、归档导出成 .jpg、也没有 ETag 可 304；
3. **变体归谁**（带尺寸的缩略图 / 预览图）—— 台账之外还有五处直接读变体表。

现在四份清单收成一张 `SOURCES` 表。这里钉三件事：**十处来源都算根**、**元数据按实际类型回答**、
**表结构漂移会当场失败**（新加一张带 capture_id 的表却没登记，守卫会红）。
"""

import hashlib
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene import captures_ledger as ledger
from services.hygiene.captures import FileCaptureStore
from services.hygiene.work import HygieneWork

DAY = "2026-09-13"
NOW = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
STAMP = NOW.isoformat()


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 120, 200)).save(output, format="PNG")
    return output.getvalue()


class CaptureLedgerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.captures = FileCaptureStore(Path(self._tmpdir.name) / "hygiene-captures")
        self.work = HygieneWork(self.db, captures=self.captures)
        self.conn = self.db._conn
        self.ids = await self._seed_every_source()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _exec(self, sql: str, params=()) -> None:
        await self.conn.execute(sql, params)

    async def _seed_every_source(self) -> dict:
        """十处来源各来一行，返回 {标签: capture_id}。"""
        ids = {
            "standard": "cap-standard",
            "daily": "cap-daily",
            "deep_before": "cap-deep-before",
            "deep_after": "cap-deep-after",
            "fix": "cap-fix",
            "reshoot": "cap-reshoot",
            "teaching_left": "cap-teach-left",
            "teaching_right": "cap-teach-right",
            "attire_shot": "cap-attire-shot",
            "attire_standard": "cap-attire-standard",
        }
        # 先铺好外键指向的那几行（工作区 / 检查项 / 实例 / 标准图行）。
        await self._exec(
            "INSERT INTO hygiene_zones (id, name, created_at, updated_at) VALUES (1, '馅档', ?, ?)",
            (STAMP, STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_daily_items (id, zone_id, name, created_at, updated_at)
               VALUES (1, 1, '台面', ?, ?)""",
            (STAMP, STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_daily_instances
                   (id, business_date, shift, item_id, status, created_at, updated_at)
               VALUES (1, ?, '白班', 1, '待拍', ?, ?)""",
            (DAY, STAMP, STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_daily_instances
                   (id, business_date, shift, item_id, status, created_at, updated_at)
               VALUES (2, ?, '夜班', 1, '待拍', ?, ?)""",
            (DAY, STAMP, STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_deep_clean_items (id, weekday, name, created_at, updated_at)
               VALUES (1, 0, '冷柜', ?, ?)""",
            (STAMP, STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_deep_clean_instances
                   (id, business_date, item_id, status, created_at, updated_at)
               VALUES (1, ?, 1, '待拍', ?, ?)""",
            (DAY, STAMP, STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_standards
                   (id, item_id, capture_id, content_type, created_at, byte_size, content_sha256)
               VALUES (1, 1, ?, 'image/jpeg', ?, 4321, 'sha-standard')""",
            (ids["standard"], STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_daily_submissions
                   (instance_id, capture_id, content_type, frozen_standard_id, submitter_id,
                    submitter_phone, zone_name, captured_at, created_at)
               VALUES (1, ?, 'image/jpeg', 1, 1, '13800000001', '馅档', ?, ?)""",
            (ids["daily"], STAMP, STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_deep_clean_submissions
                   (instance_id, before_capture_id, after_capture_id, before_content_type,
                    after_content_type, submitter_id, submitter_phone, item_name,
                    before_captured_at, after_captured_at, created_at)
               VALUES (1, ?, ?, 'image/png', 'image/png', 1, '13800000001', '冷柜', ?, ?, ?)""",
            (ids["deep_before"], ids["deep_after"], STAMP, STAMP, STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_fix_tickets
                   (id, zone_id, ticket_type, body_text, duration_seconds, deadline, opener_kind,
                    opener_phone, status, capture_id, content_type, created_at, updated_at)
               VALUES (1, 1, '整改', '正文', 3600, ?, 'super', '13800000001', '待回拍', ?,
                       'image/jpeg', ?, ?)""",
            (STAMP, ids["fix"], STAMP, STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_fix_reshoots
                   (ticket_id, capture_id, content_type, photographer_id, photographer_phone,
                    zone_name, captured_at, created_at)
               VALUES (1, ?, 'image/jpeg', 1, '13800000001', '馅档', ?, ?)""",
            (ids["reshoot"], STAMP, STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_teaching_examples
                   (kind, title, left_label, right_label, left_capture_id, right_capture_id,
                    left_content_type, right_content_type, created_at)
               VALUES ('compare', '标题', '左', '右', ?, ?, 'image/png', 'image/jpeg', ?)""",
            (ids["teaching_left"], ids["teaching_right"], STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_attire_shots
                   (employee_id, business_date, status, capture_id, content_type,
                    created_at, updated_at)
               VALUES (1, ?, 'pending', ?, 'image/png', ?, ?)""",
            (DAY, ids["attire_shot"], STAMP, STAMP),
        )
        await self._exec(
            """INSERT INTO hygiene_attire_standard (capture_id, content_type, created_at)
               VALUES (?, 'image/png', ?)""",
            (ids["attire_standard"], STAMP),
        )
        await self.conn.commit()
        return ids

    # ── 1. 谁持有 ────────────────────────────────────────────────────────

    async def test_every_source_is_a_root(self):
        """十处来源都算「有人要」，少一处就是每分钟被清扫杀掉一张。"""
        roots = await ledger.roots(self.conn)
        self.assertEqual(roots, set(self.ids.values()))

    async def test_owners_label_every_root_and_prefer_the_first_source(self):
        """owners 的键集必须等于 roots，kind 取先出现的那处（标准图排在前面）。"""
        owners = await ledger.owners(self.conn)
        self.assertEqual(set(owners), set(self.ids.values()))
        self.assertEqual(owners[self.ids["standard"]], "standard")
        self.assertEqual(owners[self.ids["daily"]], "daily")
        self.assertEqual(owners[self.ids["deep_before"]], "deep")
        self.assertEqual(owners[self.ids["deep_after"]], "deep")
        self.assertEqual(owners[self.ids["fix"]], "fix")
        self.assertEqual(owners[self.ids["reshoot"]], "fix_reshoot")
        self.assertEqual(owners[self.ids["teaching_left"]], "teaching")
        self.assertEqual(owners[self.ids["attire_shot"]], "attire")
        self.assertEqual(owners[self.ids["attire_standard"]], "attire")
        for kind in owners.values():
            self.assertIn(kind, ledger.KIND_LABELS)

    async def test_a_capture_shared_by_two_sources_keeps_the_first_kind(self):
        """同一张图既是标准图又是日常实拍（冻结版本），按表里的先后归一。"""
        await self._exec(
            """INSERT INTO hygiene_daily_submissions
                   (instance_id, capture_id, content_type, frozen_standard_id, submitter_id,
                    submitter_phone, zone_name, captured_at, created_at)
               VALUES (2, ?, 'image/jpeg', 1, 1, '13800000001', '馅档', ?, ?)""",
            (self.ids["standard"], STAMP, STAMP),
        )
        await self.conn.commit()
        owners = await ledger.owners(self.conn)
        self.assertEqual(owners[self.ids["standard"]], "standard")

    # ── 2. 这张图是什么 ──────────────────────────────────────────────────

    async def test_meta_answers_with_the_real_content_type_for_every_source(self):
        """教学对照图与仪容的 PNG 以前被当成 JPEG，现在按表里的类型回答。"""
        for label, capture_id in self.ids.items():
            with self.subTest(label):
                meta = await ledger.meta(self.conn, capture_id)
                self.assertIsNotNone(meta)
                self.assertEqual(meta["capture_id"], capture_id)
                expected = "image/png" if label in {
                    "deep_before",
                    "deep_after",
                    "teaching_left",
                    "attire_shot",
                    "attire_standard",
                } else "image/jpeg"
                self.assertEqual(meta["content_type"], expected)
        standard = await ledger.meta(self.conn, self.ids["standard"])
        self.assertEqual(standard["byte_size"], 4321)
        self.assertEqual(standard["sha256"], "sha-standard")

    async def test_meta_is_none_for_a_capture_no_table_owns(self):
        self.assertIsNone(await ledger.meta(self.conn, "cap-nobody"))

    async def test_meta_fills_size_and_sha_from_the_file_once(self):
        """库里没存大小与摘要的（日常实拍），按实际下发的文件补，同一文件只算一次。"""
        data = _png()
        capture_id = await self.captures.put_async(data, "image/png")
        await self._exec(
            """INSERT INTO hygiene_daily_submissions
                   (instance_id, capture_id, content_type, frozen_standard_id, submitter_id,
                    submitter_phone, zone_name, captured_at, created_at)
               VALUES (1, ?, 'image/png', 1, 1, '13800000001', '馅档', ?, ?)""",
            (capture_id, STAMP, STAMP),
        )
        await self.conn.commit()

        calls = {"n": 0}
        original = self.captures.get_async

        async def counting(cid: str) -> bytes:
            calls["n"] += 1
            return await original(cid)

        self.captures.get_async = counting  # type: ignore[method-assign]

        first = await ledger.meta(self.conn, capture_id, captures=self.captures)
        self.assertEqual(first["byte_size"], len(data))
        self.assertEqual(first["sha256"], hashlib.sha256(data).hexdigest())
        second = await ledger.meta(self.conn, capture_id, captures=self.captures)
        self.assertEqual(second["sha256"], first["sha256"])
        self.assertEqual(calls["n"], 1, "同一张图不该每读一次就重算一次摘要")

    async def test_capture_view_reports_the_real_type_for_a_teaching_capture(self):
        """回归：6 源那张清单漏了 teaching，PNG 被当 JPEG 且拿不到 ETag。"""
        data = _png()
        capture_id = await self.captures.put_async(data, "image/png")
        await self._exec(
            """INSERT INTO hygiene_teaching_examples
                   (kind, title, left_label, right_label, left_capture_id, right_capture_id,
                    left_content_type, right_content_type, created_at)
               VALUES ('compare', '标题', '左', '右', ?, 'cap-teach-other',
                       'image/png', 'image/jpeg', ?)""",
            (capture_id, STAMP),
        )
        await self.conn.commit()

        view = await self.work.capture_view(capture_id)
        self.assertEqual(view["content_type"], "image/png")
        self.assertEqual(view["byte_size"], len(data))
        self.assertEqual(view["sha256"], hashlib.sha256(data).hexdigest())

    # ── 3. 变体 ──────────────────────────────────────────────────────────

    async def test_variants_and_index_answer_from_the_same_table(self):
        await self._exec(
            """INSERT INTO hygiene_capture_variants
                   (source_capture_id, variant, capture_id, content_type,
                    width, height, byte_size, content_sha256, created_at)
               VALUES (?, 'thumb', 'cap-thumb', 'image/webp', 120, 90, 900, 'sha-thumb', ?),
                      (?, 'preview', 'cap-preview', 'image/webp', 600, 450,
                       4200, 'sha-preview', ?)""",
            (self.ids["standard"], STAMP, self.ids["standard"], STAMP),
        )
        await self.conn.commit()

        variants = await ledger.variants(self.conn, self.ids["standard"])
        self.assertEqual(set(variants), {"thumb", "preview"})
        self.assertEqual(variants["thumb"]["capture_id"], "cap-thumb")
        self.assertEqual(variants["preview"]["byte_size"], 4200)
        self.assertEqual(await ledger.variants(self.conn, "cap-nobody"), {})

        index = await ledger.variant_index(self.conn)
        self.assertEqual(set(index[self.ids["standard"]]), {"cap-thumb", "cap-preview"})

    async def test_reachable_is_roots_plus_derivatives(self):
        await self._exec(
            """INSERT INTO hygiene_capture_variants
                   (source_capture_id, variant, capture_id, content_type,
                    width, height, byte_size, content_sha256, created_at)
               VALUES (?, 'thumb', 'cap-thumb', 'image/webp', 120, 90, 900, 'sha-thumb', ?)""",
            (self.ids["daily"], STAMP),
        )
        await self.conn.commit()

        self.assertEqual(await ledger.derivatives(self.conn), {"cap-thumb"})
        reachable = await ledger.reachable(self.conn)
        self.assertEqual(reachable, set(self.ids.values()) | {"cap-thumb"})

    # ── 4. 守卫：表结构漂移与登记表本身 ─────────────────────────────────

    async def _capture_columns(self) -> set:
        cur = await self.conn.execute(
            """SELECT table_name, column_name FROM information_schema.columns
               WHERE table_schema = current_schema()
                 AND left(table_name::text, 8) = 'hygiene_'
                 AND right(column_name::text, 10) = 'capture_id'"""
        )
        return {(dict(row)["table_name"], dict(row)["column_name"]) for row in await cur.fetchall()}

    async def test_every_capture_column_in_the_schema_is_registered(self):
        """新加一张带 capture_id 的表却没进台账，这一条会红。"""
        columns = await self._capture_columns()
        self.assertEqual(columns, ledger.claimed_columns() | ledger.ALLOWED_UNREGISTERED)

    async def test_every_registered_column_exists_in_the_schema(self):
        """台账里写了库里没有的列（改过表名、拼错）同样会红。"""
        columns = await self._capture_columns()
        missing = ledger.claimed_columns() - columns
        self.assertEqual(missing, set())


if __name__ == "__main__":
    unittest.main()