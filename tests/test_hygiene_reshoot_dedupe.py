#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待验收期间重复回拍：被顶替的旧行、旧原图与旧变体必须一起回收。

审查报告 DOC-07：``reshoot_fix`` 原来只挡"已通过"，待验收状态下再拍一次就 INSERT
一行新的 ``hygiene_fix_reshoots`` 并把 ``pending_reshoot_id`` 覆盖成新行 id；旧行
（连同 1 张原图 + 2 个变体）失去引用，却仍被 ``_referenced_capture_ids`` 的整表
UNION 算作"被引用"→ 孤儿清理永远收不走，DB 与磁盘只增不减。

反方向的边界同样要守住：被打回的那张是驳回证据，下一轮回拍不能把它一起删掉。

用 ``FileCaptureStore`` 而不是内存假存储：本票断言的是"磁盘上的文件真的没了"。
"""

import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from PIL import Image

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.captures import FileCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import CAPTURE_VARIANTS, HygieneWork, HygieneWorkError

DAY_PHONE = "13800000001"
NIGHT_PHONE = "13800000002"
OPEN_COLOR = (30, 90, 120)
FIRST_COLOR = (120, 60, 30)
SECOND_COLOR = (200, 40, 40)


def _staff(employee_id, phone, shift, permission="普通员工", name="", caps=None) -> dict:
    """员工 actor。`caps` 是**管理权限开关**（2026-10-05 起判据看它，不看 `permission`）；
    不给就按迁移 `0015` 的回填规则推 —— 老「管理员」= 日常验收 + 专项验收 + 整改单。"""
    if caps is None:
        caps = ("daily_review", "deep_review", "fix") if permission == "管理员" else ()
    return {
        "kind": "staff",
        "id": employee_id,
        "permission": permission,
        "caps": list(caps),
        "name": name,
        "phone": phone,
        "shift": shift,
    }


def _jpeg(color) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (120, 90), color).save(output, format="JPEG", quality=90)
    return output.getvalue()


class ReshootDedupeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.now_value = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
        self.captures = FileCaptureStore(Path(self._tmpdir.name) / "hygiene-captures")
        self.work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: self.now_value,
            image_variants=ImageVariantGenerator(),
        )
        await self.work.prepare()
        self.opener = _staff(11, DAY_PHONE, "白班", permission="管理员", name="张三")
        self.shooter = _staff(12, NIGHT_PHONE, "夜班", name="李四")
        self.zone = (await self.work.list_zones())[0]

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    # ---- 夹具 ----

    def _live(self, color=OPEN_COLOR) -> dict:
        return {
            "bytes": _jpeg(color),
            "content_type": "image/jpeg",
            "live": True,
            "markup": [],
        }

    async def _open(self) -> dict:
        return await self.work.open_fix(
            self.opener,
            self.zone["id"],
            "卫生",
            "案板有油",
            timedelta(hours=2),
            self._live(),
        )

    async def _reshoot_rows(self, ticket_id) -> list:
        cur = await self.db._conn.execute(
            """SELECT id, capture_id FROM hygiene_fix_reshoots
               WHERE ticket_id = ? ORDER BY id ASC""",
            (int(ticket_id),),
        )
        return [dict(row) for row in await cur.fetchall()]

    async def _pending_reshoot_id(self, ticket_id):
        cur = await self.db._conn.execute(
            "SELECT pending_reshoot_id FROM hygiene_fix_tickets WHERE id = ?",
            (int(ticket_id),),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)["pending_reshoot_id"]

    async def _variant_files(self, capture_id) -> list:
        """原图的变体文件 capture ids（正常情况下是 thumb + preview 两个）。"""
        cur = await self.db._conn.execute(
            """SELECT capture_id FROM hygiene_capture_variants
               WHERE source_capture_id = ? ORDER BY variant ASC""",
            (str(capture_id),),
        )
        return [str(dict(row)["capture_id"]) for row in await cur.fetchall()]

    # ---- 用例 ----

    async def test_second_reshoot_while_pending_reclaims_the_replaced_photo(self):
        ticket = await self._open()
        first = await self.work.reshoot_fix(self.shooter, ticket["id"], self._live(FIRST_COLOR))
        first_capture = first["reshoot_capture_id"]
        first_files = [first_capture, *await self._variant_files(first_capture)]
        self.assertEqual(len(first_files), 1 + len(CAPTURE_VARIANTS))
        for capture_id in first_files:
            self.assertTrue(await self.captures.exists_async(capture_id), capture_id)

        second = await self.work.reshoot_fix(
            self.shooter, ticket["id"], self._live(SECOND_COLOR)
        )
        second_capture = second["reshoot_capture_id"]
        self.assertNotEqual(second_capture, first_capture)
        self.assertEqual(second["status"], "待验收")

        rows = await self._reshoot_rows(ticket["id"])
        # 被顶替的那张：原图 + 变体文件、变体行都要走
        for capture_id in first_files:
            self.assertFalse(await self.captures.exists_async(capture_id), capture_id)
        self.assertEqual(await self._variant_files(first_capture), [])

        self.assertEqual(len(rows), 1, "待验收期间的重复回拍只该留一行")
        self.assertEqual(rows[0]["capture_id"], second_capture)
        self.assertEqual(int(await self._pending_reshoot_id(ticket["id"])), rows[0]["id"])

        # 顶替上来的那张连同它的变体完好
        second_variants = await self._variant_files(second_capture)
        self.assertEqual(len(second_variants), len(CAPTURE_VARIANTS))
        for capture_id in [second_capture, *second_variants]:
            self.assertTrue(await self.captures.exists_async(capture_id), capture_id)
        review = await self.work.get_fix_review(ticket["id"], actor=self.opener)
        self.assertEqual(review["reshoot_capture_id"], second_capture)

    async def test_first_reshoot_keeps_its_row_and_photo(self):
        """正常单次回拍（含后续验收）路径不受影响。"""
        ticket = await self._open()
        reshoot = await self.work.reshoot_fix(self.shooter, ticket["id"], self._live(FIRST_COLOR))
        rows = await self._reshoot_rows(ticket["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["capture_id"], reshoot["reshoot_capture_id"])
        self.assertEqual(int(await self._pending_reshoot_id(ticket["id"])), rows[0]["id"])
        self.assertTrue(await self.captures.exists_async(reshoot["reshoot_capture_id"]))
        self.assertTrue(await self.captures.exists_async(ticket["capture_id"]))

        accepted = await self.work.accept_fix(self.opener, ticket["id"])
        self.assertEqual(accepted["status"], "已通过")
        self.assertTrue(await self.captures.exists_async(reshoot["reshoot_capture_id"]))

    async def test_rejected_reshoot_survives_the_next_reshoot(self):
        """被打回的那张是驳回证据：下一轮回拍不许把它当"重复回拍"删掉。"""
        ticket = await self._open()
        first = await self.work.reshoot_fix(self.shooter, ticket["id"], self._live(FIRST_COLOR))
        rejected = await self.work.reject_fix(self.opener, ticket["id"], reason="角度不对")
        self.assertEqual(rejected["status"], "待回拍")

        second = await self.work.reshoot_fix(
            self.shooter, ticket["id"], self._live(SECOND_COLOR)
        )
        rows = await self._reshoot_rows(ticket["id"])
        self.assertEqual(len(rows), 2, "驳回证据必须留着")
        self.assertEqual(
            [row["capture_id"] for row in rows],
            [first["reshoot_capture_id"], second["reshoot_capture_id"]],
        )
        self.assertTrue(await self.captures.exists_async(first["reshoot_capture_id"]))
        self.assertEqual(int(await self._pending_reshoot_id(ticket["id"])), rows[1]["id"])

        accepted = await self.work.accept_fix(self.opener, ticket["id"])
        self.assertEqual(accepted["status"], "已通过")
        with self.assertRaises(HygieneWorkError) as err:
            await self.work.reshoot_fix(self.shooter, ticket["id"], self._live())
        self.assertEqual(err.exception.code, "already_accepted")

    async def test_replaced_photo_cleanup_runs_outside_the_write_lock(self):
        """删旧照片是文件 IO（每张一次 unlink），不能占着全局写锁（§3.4）。"""
        ticket = await self._open()
        await self.work.reshoot_fix(self.shooter, ticket["id"], self._live(FIRST_COLOR))

        observed = []
        original = self.work._delete_capture_files

        async def probe(capture_ids):
            observed.append((self.work._write_lock.locked(), list(capture_ids)))
            return await original(capture_ids)

        with mock.patch.object(self.work, "_delete_capture_files", probe):
            await self.work.reshoot_fix(self.shooter, ticket["id"], self._live(SECOND_COLOR))

        self.assertEqual(len(observed), 1, "只该删被顶替的那一张")
        locked, capture_ids = observed[0]
        self.assertFalse(locked, "删旧照片时不应持有写锁")
        self.assertEqual(len(capture_ids), 1 + len(CAPTURE_VARIANTS))
