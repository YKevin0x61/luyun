#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准图标注编辑：不换图、只改标注，且不破坏「冻结标准真冻结」。

原来的唯一入口是 POST /admin/items/{id}/standard，`file` 必填——想给现有标准图补
一个圈就得把同一张图再传一遍。`update_standard_markup` 复用当前行的图片（同一个
capture_id / sha256），只插一版新的标准行并把 current_standard_id 指过去。
"""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.work import (
    MAX_CAPTION_LENGTH,
    MAX_MARKUP_MARKS,
    HygieneWork,
    HygieneWorkError,
)

SUPER = {"kind": "super"}
STAFF = {"kind": "staff", "id": 1, "permission": "普通员工"}
STANDARD_BYTES = b"STANDARD-JPEG-LITERAL"
SHOT_BYTES = b"SHOT-JPEG-LITERAL"


CIRCLE = {"kind": "circle", "x": 0.4, "y": 0.3, "r": 0.1}
CAPTION = {"kind": "caption", "x": 0.1, "y": 0.2, "text": "看这里"}


class StandardMarkupTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
        self.captures = FakeCaptureStore()
        self.work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: self.fixed_now,
            notifier=None,
        )
        await self.work.prepare()
        self.zone = await self.work.create_zone(SUPER, "库房", ["白班", "夜班"])
        self.item = await self.work.add_daily_item(
            SUPER,
            self.zone["id"],
            "案板表面",
            {"bytes": STANDARD_BYTES, "markup": [CIRCLE]},
        )

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _standard_rows(self, item_id):
        cur = await self.db._conn.execute(
            """SELECT id, item_id, capture_id, byte_size, content_sha256, markup_json
               FROM hygiene_standards WHERE item_id = ? ORDER BY id ASC""",
            (item_id,),
        )
        return [dict(row) for row in await cur.fetchall()]

    async def _current_item(self, item_id):
        cur = await self.db._conn.execute(
            "SELECT current_standard_id FROM hygiene_daily_items WHERE id = ?",
            (item_id,),
        )
        return dict(await cur.fetchone())

    async def test_edit_creates_new_version_and_reuses_the_same_image(self):
        before = await self._standard_rows(self.item["id"])
        self.assertEqual(len(before), 1)

        updated = await self.work.update_standard_markup(
            SUPER, self.item["id"], [CIRCLE, CAPTION],
        )

        after = await self._standard_rows(self.item["id"])
        self.assertEqual(len(after), 2, "必须新插一版，而不是改旧行")
        old_row, new_row = after
        # 旧行原封不动：已提交记录握着它的 frozen_standard_id。
        self.assertEqual(old_row["id"], before[0]["id"])
        self.assertEqual(old_row["markup_json"], before[0]["markup_json"])
        # 新行复用同一份图片。
        self.assertEqual(new_row["capture_id"], old_row["capture_id"])
        self.assertEqual(new_row["content_sha256"], old_row["content_sha256"])
        self.assertEqual(new_row["byte_size"], old_row["byte_size"])
        self.assertIn("看这里", new_row["markup_json"])
        # current 指到新版本。
        self.assertEqual(updated["current_standard_id"], new_row["id"])
        self.assertEqual(
            (await self._current_item(self.item["id"]))["current_standard_id"],
            new_row["id"],
        )
        self.assertEqual(
            updated["capture_id"], old_row["capture_id"],
            "返回的 capture_id 必须是同一个，调用方据此知道图片没换",
        )

    async def test_edit_does_not_write_a_new_capture_file(self):
        stored = len(self.captures.blobs)

        await self.work.update_standard_markup(SUPER, self.item["id"], [CAPTION])

        self.assertEqual(len(self.captures.blobs), stored, "改标注不应产生新图片文件")

    async def test_clearing_all_marks_is_allowed(self):
        updated = await self.work.update_standard_markup(SUPER, self.item["id"], [])

        rows = await self._standard_rows(self.item["id"])
        self.assertEqual(rows[-1]["markup_json"], "[]")
        self.assertEqual(updated["current_standard_id"], rows[-1]["id"])

    async def test_previous_frozen_standard_keeps_its_own_marks(self):
        """提交过的记录读旧的 frozen_standard_id，看到的仍是当时那版标注。"""
        standard_id = self.item["current_standard_id"]
        actor = {
            "kind": "staff",
            "id": 7,
            "name": "张三",
            "phone": "13800138000",
            "shift": "白班",
            "zone_id": self.zone["id"],
            "permission": "普通员工",
        }
        submission = await self.work.submit_daily(
            actor,
            self.item["id"],
            {"bytes": SHOT_BYTES, "live": True},
        )
        frozen_id = submission["frozen_standard_id"]
        self.assertEqual(frozen_id, standard_id)

        await self.work.update_standard_markup(SUPER, self.item["id"], [CAPTION])

        frozen = await self.work.standard_by_id(frozen_id)
        self.assertEqual(frozen["markup"], [CIRCLE], "冻结的那一版标注不能被改掉")

    async def test_non_super_actor_is_rejected(self):
        with self.assertRaises(HygieneWorkError) as forbidden:
            await self.work.update_standard_markup(STAFF, self.item["id"], [CIRCLE])
        self.assertEqual(forbidden.exception.code, "forbidden")
        self.assertEqual(len(await self._standard_rows(self.item["id"])), 1)

    async def test_missing_item_and_missing_standard(self):
        with self.assertRaises(HygieneWorkError) as missing:
            await self.work.update_standard_markup(SUPER, 99999, [CIRCLE])
        self.assertEqual(missing.exception.code, "item_not_found")

        bare = await self.work.add_daily_item(
            SUPER, self.zone["id"], "还没标准图的项", {"bytes": STANDARD_BYTES},
        )
        await self.db._conn.execute(
            "UPDATE hygiene_daily_items SET current_standard_id = NULL WHERE id = ?",
            (bare["id"],),
        )
        await self.db._conn.commit()
        with self.assertRaises(HygieneWorkError) as no_standard:
            await self.work.update_standard_markup(SUPER, bare["id"], [CIRCLE])
        self.assertEqual(no_standard.exception.code, "standard_not_found")

    async def test_markup_is_normalized_before_storage(self):
        await self.work.update_standard_markup(SUPER, self.item["id"], [
            {"kind": "circle", "x": 5, "y": -3, "r": 99},
            {"kind": "arrow", "x1": "bad", "y1": None, "x2": 2, "y2": 0.5},
            {"kind": "caption", "x": 0.5, "y": 0.5, "text": "字" * 200},
            {"kind": "scratch", "x": 0.1, "y": 0.1},
            "not-an-object",
        ])

        rows = await self._standard_rows(self.item["id"])
        import json
        marks = json.loads(rows[-1]["markup_json"])
        self.assertEqual(len(marks), 3, "认不出的 kind 与非对象项要丢掉")
        self.assertEqual(marks[0], {"kind": "circle", "x": 1.0, "y": 0.0, "r": 0.4})
        self.assertEqual(marks[1], {"kind": "arrow", "x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 0.5})
        self.assertEqual(marks[2]["kind"], "caption")
        self.assertEqual(len(marks[2]["text"]), MAX_CAPTION_LENGTH)

    async def test_markup_count_is_capped(self):
        many = [{"kind": "circle", "x": 0.5, "y": 0.5} for _ in range(MAX_MARKUP_MARKS + 20)]

        await self.work.update_standard_markup(SUPER, self.item["id"], many)

        import json
        rows = await self._standard_rows(self.item["id"])
        self.assertEqual(len(json.loads(rows[-1]["markup_json"])), MAX_MARKUP_MARKS)

    async def test_two_edits_stack_two_versions(self):
        first = await self.work.update_standard_markup(SUPER, self.item["id"], [CAPTION])
        second = await self.work.update_standard_markup(SUPER, self.item["id"], [CIRCLE])

        rows = await self._standard_rows(self.item["id"])
        self.assertEqual([row["id"] for row in rows], [
            self.item["current_standard_id"], first["current_standard_id"], second["current_standard_id"],
        ])
        self.assertEqual(
            (await self._current_item(self.item["id"]))["current_standard_id"],
            second["current_standard_id"],
        )

    async def test_standard_manifest_version_changes_after_edit(self):
        manifest_before = await self.work.standard_manifest()
        await self.work.update_standard_markup(SUPER, self.item["id"], [CAPTION])
        manifest_after = await self.work.standard_manifest()

        self.assertNotEqual(manifest_before["version"], manifest_after["version"])
        entry = next(
            row for row in manifest_after["standards"] if row["item_id"] == self.item["id"]
        )
        self.assertEqual(
            entry["standard_id"],
            (await self._current_item(self.item["id"]))["current_standard_id"],
        )


if __name__ == "__main__":
    unittest.main()
