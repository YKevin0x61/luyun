#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仪容仪表的照片得活得下去，也要导得出来。

两件事都跟「照片的生命周期」有关：

1. **不能被孤儿清理收走**（这条最要紧）。`HygieneWork.sweep_capture_orphans` 按
   `_referenced_capture_ids` 判断「这张图还有没有人要」，不在集合里的**直接删文件**，
   而 `capture_maintenance_loop` 每小时跑一次。仪容仪表的两张表（实拍 + 标准图）当初
   没进那个 UNION —— 员工刚交的那张，一个整点之后就没了，店长点开验收看到的是
   「图片不存在」。这里既钉住它，也**反方向钉一次**：清扫本身没坏，真孤儿照样收走。

2. **归档要认得它**。「数据与照片」那一页与「一键保存今天的照片」都走
   `HygieneDataArchive`：六类业务记录里原来没有仪容仪表，照片既看不到也导不出
   （`photo_path` 会回一句 `capture_unknown`）。
"""

import io
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.archive import (
    ARCHIVE_KINDS,
    KIND_ATTIRE,
    KIND_DAILY,
    HygieneDataArchive,
    normalize_kinds,
    write_ledger_zip,
)
from services.hygiene.attire import HygieneAttire
from services.hygiene.captures import FileCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork
from tests.hygiene_duty import assign_duty

DAY_PHONE = "13800000001"
ZHANGSAN = 11
DAY = "2026-09-13"
SHOT_COLOR = (30, 90, 120)
STANDARD_COLOR = (200, 200, 40)


def _jpeg(color=(30, 90, 120)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (120, 90), color).save(output, format="JPEG", quality=90)
    return output.getvalue()


def _live(color=SHOT_COLOR) -> dict:
    return {"bytes": _jpeg(color), "content_type": "image/jpeg", "live": True, "markup": []}


def _standard(color=STANDARD_COLOR) -> dict:
    return {"bytes": _jpeg(color), "content_type": "image/jpeg", "markup": []}


class AttireCaptureLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.now_value = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
        self.captures = FileCaptureStore(Path(self._tmpdir.name) / "hygiene-captures")
        # 这里**不**给 work 注入时钟：清扫比的是「文件 mtime 距今多久」，用的是
        # 真实当下；注入一个固定的过去时刻会让两者差出十几天，门槛怎么给都不对
        # （要么全删要么全留，测试就失去意义）。需要固定营业日的是业务那一侧 ——
        # 下面 `HygieneAttire` 与排班各自注入同一个时刻。
        self.work = HygieneWork(
            self.db,
            captures=self.captures,
            image_variants=ImageVariantGenerator(),
        )
        await self.work.prepare()
        self.attire = HygieneAttire(self.work, now=lambda: self.now_value)
        self.archive = HygieneDataArchive(self.db, self.captures)

        now_iso = self.now_value.isoformat()
        await self.db._conn.execute(
            """INSERT INTO hygiene_employees
                   (id, phone, name, password_hash, permission, approved,
                    created_at, updated_at)
               VALUES (?, ?, ?, '', '普通员工', 1, ?, ?)""",
            (ZHANGSAN, DAY_PHONE, "张三", now_iso, now_iso),
        )
        await self.db._conn.commit()
        # 今天排到班次（这是「要拍」的判据）。
        await assign_duty(self.db, ZHANGSAN, slot="day", now=self.now_value)
        self.assertEqual(self.attire.today(), DAY)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _submit_one(self) -> tuple[str, str]:
        """传标准图 + 交一张实拍，返回两张图的 capture_id。"""
        standard = await self.attire.set_standard(_standard())
        await self.attire.submit(ZHANGSAN, _live())
        shot_id = await self.attire.pending_capture(ZHANGSAN)
        self.assertTrue(shot_id)
        return standard["capture_id"], shot_id

    # ── 1. 不能被杀 ─────────────────────────────────────────────────────

    async def test_attire_photos_survive_the_orphan_sweep(self):
        """**这一条最要紧**：实拍与标准图都在引用集合里，清扫之后文件还在。

        没进集合的话，`capture_maintenance_loop` 每小时跑一次就把它们当孤儿删了 ——
        照片没了，验收也就无从谈起（而且删的是文件，库里那一行还留着）。
        """
        standard_id, shot_id = await self._submit_one()

        referenced = await self.work._referenced_capture_ids()
        self.assertIn(standard_id, referenced)
        self.assertIn(shot_id, referenced)

        # 门槛给负数才真的会删：判据是 `now - modified < max_age_seconds`，给 0 时
        # 刚落的文件会卡在 mtime 与 now 的浮点边界上被跳过 —— 那样这条测试就是假绿
        # （清扫其实一张都没动）。负数把"年龄"这一关彻底让开，只剩"有没有人引用"。
        await self.work.sweep_capture_orphans(max_age_seconds=-1)
        alive = set(await self.captures.list_ids_async())
        self.assertIn(shot_id, alive)
        self.assertIn(standard_id, alive)

    async def test_sweep_still_collects_a_real_orphan(self):
        """反方向：清扫本身没坏 —— 没有任何业务行引用的图照样被收走。

        少了这一条，上面那条用「sweep 根本没生效」也能过。
        """
        orphan_id, _generated = await self.work.store_capture(
            _jpeg((10, 10, 10)), "image/jpeg", require_image=True
        )
        await self.work.sweep_capture_orphans(max_age_seconds=-1)
        alive = set(await self.captures.list_ids_async())
        self.assertNotIn(orphan_id, alive)

    # ── 2. 导得出来 ─────────────────────────────────────────────────────

    async def test_archive_lists_the_attire_record(self):
        standard_id, shot_id = await self._submit_one()
        page = await self.archive.list_records(
            kinds=[KIND_ATTIRE], date_from=DAY, date_to=DAY
        )
        self.assertEqual(page["total"], 1)
        item = page["items"][0]
        self.assertEqual(item["kind"], KIND_ATTIRE)
        self.assertEqual(item["kind_label"], "仪容仪表")
        self.assertEqual(item["business_date"], DAY)
        self.assertEqual(item["title"], "仪容仪表")
        self.assertEqual(item["status"], "等验收")
        self.assertEqual(item["submitter_name"], "张三")
        # 仪容仪表不挂工作区：那一列是空的，不是「未分类」。
        self.assertEqual(item["zone_name"], "")
        self.assertIsNone(item["zone_id"])
        self.assertEqual([photo["capture_id"] for photo in item["photos"]], [shot_id])
        self.assertNotEqual(shot_id, standard_id)

    async def test_archive_zip_carries_the_attire_photo(self):
        """打包出来真的有一张图，而且按「照片/仪容仪表/」归档。"""
        _standard_id, _shot_id = await self._submit_one()
        records = await self.archive.iter_records(
            kinds=[KIND_ATTIRE], date_from=DAY, date_to=DAY
        )
        entries = []
        for record in records:
            photos = []
            for photo in record["photos"]:
                view = await self.archive.photo_path(photo["capture_id"], "original")
                photos.append({**photo, "path": view["path"]})
            entries.append({"record": record, "photos": photos})

        target = Path(self._tmpdir.name) / "today.zip"
        written, skipped = write_ledger_zip(entries, target)
        self.assertEqual((written, skipped), (1, 0))
        with zipfile.ZipFile(target) as bundle:
            names = bundle.namelist()
        self.assertIn("记录.csv", names)
        self.assertTrue(
            any(name.startswith("照片/仪容仪表/") for name in names),
            names,
        )
        # 台账那一行也得写上营业日与提交人（它是给人工核对用的）。
        with zipfile.ZipFile(target) as bundle:
            ledger = bundle.read("记录.csv").decode("utf-8")
        self.assertIn("仪容仪表", ledger)
        self.assertIn("张三", ledger)

    async def test_photo_path_knows_attire_captures(self):
        """`photo_path` 靠 `_capture_meta` 认图：没接上就回 `capture_unknown`（导不出）。"""
        standard_id, shot_id = await self._submit_one()
        for capture_id in (standard_id, shot_id):
            view = await self.archive.photo_path(capture_id, "original")
            self.assertTrue(Path(view["path"]).is_file())
            self.assertTrue(str(view["content_type"]).startswith("image/"))

    async def test_attire_is_empty_when_filtering_by_zone(self):
        """按工作区筛就没有仪容仪表 —— 它按人，不挂在任何区上（跟专项同一条口径）。"""
        await self._submit_one()
        page = await self.archive.list_records(
            kinds=[KIND_ATTIRE], date_from=DAY, date_to=DAY, zone_id=1
        )
        self.assertEqual(page["items"], [])
        self.assertEqual(page["total"], 0)

    async def test_one_click_covers_both_daily_and_attire(self):
        """「一键保存今天的照片」不带 kinds = 全部类型：用户点名的两类都在里面。"""
        selected = normalize_kinds(None)
        self.assertEqual(set(selected), set(ARCHIVE_KINDS))
        self.assertIn(KIND_DAILY, selected)
        self.assertIn(KIND_ATTIRE, selected)
