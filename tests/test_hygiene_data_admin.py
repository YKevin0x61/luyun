#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生数据与照片的管理端：查询、存储概况、导出、删除、历史回看（ADR-0087/0088）。

用 ``FileCaptureStore`` 而不是 ``FakeCaptureStore``：存储概况与导出都是按文件路径
和文件大小算的，假存储测不出真实链路。
"""

import io
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
import tempfile

from PIL import Image

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.archive import (
    HygieneDataArchive,
    ArchiveQueryError,
    KIND_DAILY,
    KIND_DEEP,
    KIND_FIX_RESHOOT,
    KIND_STANDARD,
    KIND_TEACHING,
    parse_archive_range,
    write_ledger_zip,
)
from services.hygiene.captures import FileCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork, HygieneWorkError

SUPER = {"kind": "super"}
DAY_PHONE = "13800000001"
NIGHT_PHONE = "13800000002"


def _staff(employee_id, phone, shift, permission="普通员工", name=""):
    return {
        "kind": "staff",
        "id": employee_id,
        "permission": permission,
        "name": name,
        "phone": phone,
        "shift": shift,
    }


def _jpeg(color=(30, 90, 120)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (120, 90), color).save(output, format="JPEG", quality=90)
    return output.getvalue()


class HygieneDataAdminTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        # now 走可变对象：历史回看要靠"往后推几天"把已写下的数据变成过去
        # （营业日按 06:00 切，推一天就够，但推两天更不容易踩边界）。
        self.now_value = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
        self.captures = FileCaptureStore(Path(self._tmpdir.name) / "hygiene-captures")
        self.work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: self.now_value,
            image_variants=ImageVariantGenerator(),
        )
        await self.work.prepare()
        self.archive = HygieneDataArchive(self.db, captures=self.captures)
        await self._employee(11, DAY_PHONE, "张三")

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def _advance_days(self, days: int):
        """把"现在"往后推，让已经写下的数据变成历史营业日。"""
        self.now_value = self.now_value + timedelta(days=days)

    async def _employee(self, employee_id, phone, name):
        now = self.now_value.isoformat()
        await self.db._conn.execute(
            """INSERT INTO hygiene_employees
                   (id, phone, name, password_hash, permission, approved, created_at, updated_at)
               VALUES (?, ?, ?, '', '普通员工', 1, ?, ?)""",
            (employee_id, phone, name, now, now),
        )
        await self.db._conn.commit()

    def _live(self, data=None, markup=None):
        capture = {"bytes": data or _jpeg(), "content_type": "image/jpeg", "live": True}
        if markup is not None:
            capture["markup"] = markup
        return capture

    def _standard(self, data=None, markup=None):
        capture = {"bytes": data or _jpeg(), "content_type": "image/jpeg"}
        if markup is not None:
            capture["markup"] = markup
        return capture

    async def _one(self, sql, params=()):
        cur = await self.db._conn.execute(sql, params)
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def _submission_id(self, capture_id):
        row = await self._one(
            "SELECT id FROM hygiene_daily_submissions WHERE capture_id = ?", (capture_id,)
        )
        return int(row["id"])

    async def _zone(self, name="案板"):
        zones = await self.work.list_zones()
        return next(zone for zone in zones if zone["name"] == name)

    async def _daily_setup(self, item_name="案板表面"):
        zone = await self._zone()
        item = await self.work.add_daily_item(SUPER, zone["id"], item_name, self._standard())
        day = _staff(11, DAY_PHONE, "白班")
        submitted = await self.work.submit_daily(day, item["id"], self._live())
        submitted["id"] = await self._submission_id(submitted["capture_id"])
        return zone, item, submitted

    # ---- 查询与概况 -------------------------------------------------------

    async def test_records_normalize_every_kind(self):
        zone, item, submitted = await self._daily_setup()

        deep_item = await self.work.add_deep_clean_item(
            SUPER, self.now_value.weekday(), "冷柜一号"
        )
        await self.work.submit_deep_clean_pair(
            _staff(11, DAY_PHONE, "白班"), deep_item["id"], self._live(), self._live()
        )

        ticket = await self.work.open_fix(
            _staff(11, DAY_PHONE, "白班", permission="管理员"),
            zone["id"],
            "卫生",
            "案板有油",
            timedelta(hours=2),
            self._live(),
        )
        await self.work.reshoot_fix(_staff(11, DAY_PHONE, "白班"), ticket["id"], self._live())

        passed = await self.work.accept_daily(SUPER, item["id"], "白班")
        self.assertEqual(passed["status"], "已通过")
        await self.work.mark_teaching(SUPER, {"kind": "daily", "item_id": item["id"], "shift": "白班"})

        listed = await self.archive.list_records(date_from="2026-09-13", date_to="2026-09-13")
        kinds = sorted({row["kind"] for row in listed["items"]})
        self.assertEqual(
            kinds,
            sorted([KIND_DAILY, KIND_DEEP, "fix", KIND_FIX_RESHOOT, KIND_TEACHING, KIND_STANDARD]),
        )
        daily = next(row for row in listed["items"] if row["kind"] == KIND_DAILY)
        self.assertEqual(daily["title"], "案板表面")
        self.assertEqual(daily["zone_name"], "案板")
        self.assertEqual(daily["submitter_phone"], DAY_PHONE)
        self.assertEqual(daily["submitter_name"], "张三")
        self.assertEqual(daily["status"], "已通过")
        self.assertEqual(daily["business_date"], "2026-09-13")
        self.assertEqual([photo["role"] for photo in daily["photos"]], ["实拍"])
        deep = next(row for row in listed["items"] if row["kind"] == KIND_DEEP)
        self.assertEqual([photo["role"] for photo in deep["photos"]], ["清理前", "清理后"])
        teaching = next(row for row in listed["items"] if row["kind"] == KIND_TEACHING)
        self.assertEqual([photo["role"] for photo in teaching["photos"]], ["左图", "右图"])

    async def test_records_filter_by_kind_zone_and_range(self):
        zone, _item, _submitted = await self._daily_setup()
        other = await self._zone("馅档")
        await self.work.add_daily_item(SUPER, other["id"], "馅盆", self._standard())

        only_daily = await self.archive.list_records(
            kinds=[KIND_DAILY], date_from="2026-09-13", date_to="2026-09-13"
        )
        self.assertTrue(all(row["kind"] == KIND_DAILY for row in only_daily["items"]))

        anban_only = await self.archive.list_records(
            date_from="2026-09-13", date_to="2026-09-13", zone_id=zone["id"]
        )
        self.assertTrue(anban_only["items"])
        self.assertTrue(all(row["zone_id"] == zone["id"] for row in anban_only["items"]))

        empty = await self.archive.list_records(date_from="2026-08-01", date_to="2026-08-02")
        self.assertEqual(empty["total"], 0)

    async def test_records_paginate_and_report_total(self):
        zone = await self._zone()
        for index in range(3):
            item = await self.work.add_daily_item(
                SUPER, zone["id"], f"台面{index}", self._standard()
            )
            await self.work.submit_daily(_staff(11, DAY_PHONE, "白班"), item["id"], self._live())
        page = await self.archive.list_records(
            kinds=[KIND_DAILY], date_from="2026-09-13", date_to="2026-09-13", page=2, page_size=2
        )
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["page"], 2)
        self.assertEqual(len(page["items"]), 1)

    async def test_storage_summary_counts_photos_and_bytes(self):
        await self._daily_setup()
        summary = await self.archive.storage_summary()
        daily = next(bucket for bucket in summary["kinds"] if bucket["kind"] == KIND_DAILY)
        standard = next(
            bucket for bucket in summary["kinds"] if bucket["kind"] == KIND_STANDARD
        )
        self.assertEqual(daily["count"], 1)  # 实拍
        self.assertEqual(standard["count"], 1)  # 当前标准图
        self.assertGreater(daily["bytes"], 0)
        self.assertEqual(summary["total"]["count"], 2)
        # 缩略图与 preview 变体也算占用，但不算"照片张数"。
        self.assertGreater(summary["total"]["files"], summary["total"]["count"])
        self.assertEqual(summary["missing_files"], 0)
        self.assertEqual(summary["orphan_files"]["count"], 0)

    async def test_photo_path_falls_back_to_original(self):
        _zone, _item, submitted = await self._daily_setup()
        capture_id = submitted["capture_id"]
        original = await self.archive.photo_path(capture_id, "original")
        self.assertTrue(original["path"].is_file())
        thumb = await self.archive.photo_path(capture_id, "thumb")
        self.assertEqual(thumb["variant"], "thumb")
        # 变体文件丢了以后必须回落原图，而不是报"照片不存在"。
        variant_row = await self._one(
            """SELECT capture_id FROM hygiene_capture_variants
               WHERE source_capture_id = ? AND variant = 'thumb'""",
            (capture_id,),
        )
        await self.captures.delete_async(variant_row["capture_id"])
        fallback = await self.archive.photo_path(capture_id, "thumb")
        self.assertEqual(fallback["path"], original["path"])
        with self.assertRaises(ArchiveQueryError) as unknown:
            await self.archive.photo_path("does-not-exist", "original")
        self.assertEqual(unknown.exception.code, "capture_unknown")

    # ---- 删除 -------------------------------------------------------------

    async def test_delete_daily_submission_resets_instance_and_files(self):
        _zone, item, submitted = await self._daily_setup()
        capture_id = submitted["capture_id"]
        self.assertTrue(await self.captures.exists_async(capture_id))

        result = await self.work.delete_archive_record(SUPER, KIND_DAILY, submitted["id"])
        self.assertEqual(result["records"], 1)
        self.assertEqual(result["photos"], 1)
        self.assertEqual(result["status_reset"], 1)
        self.assertFalse(await self.captures.exists_async(capture_id))

        inbox = await self.work.list_daily_work(SUPER)
        row = next(entry for entry in inbox if entry["item_id"] == item["id"])
        self.assertEqual(row["status"], "待拍")
        remaining = await self.archive.list_records(
            kinds=[KIND_DAILY], date_from="2026-09-13", date_to="2026-09-13"
        )
        self.assertEqual(remaining["total"], 0)

    async def test_delete_needs_super_and_existing_record(self):
        _zone, _item, submitted = await self._daily_setup()
        with self.assertRaises(HygieneWorkError) as forbidden:
            await self.work.delete_archive_record(
                _staff(11, DAY_PHONE, "白班", permission="管理员"), KIND_DAILY, submitted["id"]
            )
        self.assertEqual(forbidden.exception.code, "forbidden")

        with self.assertRaises(HygieneWorkError) as missing:
            await self.work.delete_archive_record(SUPER, KIND_DAILY, 999999)
        self.assertEqual(missing.exception.code, "record_not_found")

        with self.assertRaises(HygieneWorkError) as bad_kind:
            await self.work.delete_archive_record(SUPER, "fix", 1)
        self.assertEqual(bad_kind.exception.code, "bad_kind")

    async def test_delete_standard_version_rules(self):
        zone = await self._zone()
        item = await self.work.add_daily_item(SUPER, zone["id"], "案板表面", self._standard())
        current = item["current_standard_id"]
        with self.assertRaises(HygieneWorkError) as blocked:
            await self.work.delete_archive_record(SUPER, KIND_STANDARD, current)
        self.assertEqual(blocked.exception.code, "standard_in_use")

        # 换一版标准图：旧版没有待验收对照它，可以删。
        replaced = await self.work.replace_standard(SUPER, item["id"], self._standard())
        old_id = current
        new_id = replaced["current_standard_id"]
        self.assertNotEqual(old_id, new_id)
        result = await self.work.delete_archive_record(SUPER, KIND_STANDARD, old_id)
        self.assertEqual(result["records"], 1)

        # 新版本是当前版本，仍然不能删。
        with self.assertRaises(HygieneWorkError):
            await self.work.delete_archive_record(SUPER, KIND_STANDARD, new_id)

    async def test_delete_standard_frozen_by_pending_is_blocked(self):
        zone = await self._zone()
        item = await self.work.add_daily_item(SUPER, zone["id"], "案板表面", self._standard())
        frozen = item["current_standard_id"]
        await self.work.submit_daily(_staff(11, DAY_PHONE, "白班"), item["id"], self._live())
        # 换版后旧版仍被这次待验收冻结（ADR-0069），删它就等于抽掉验收依据。
        await self.work.replace_standard(SUPER, item["id"], self._standard())
        with self.assertRaises(HygieneWorkError) as frozen_error:
            await self.work.delete_archive_record(SUPER, KIND_STANDARD, frozen)
        self.assertEqual(frozen_error.exception.code, "standard_in_use")

    async def test_delete_fix_reshoot_resets_ticket(self):
        zone = await self._zone()
        ticket = await self.work.open_fix(
            _staff(11, DAY_PHONE, "白班", permission="管理员"),
            zone["id"],
            "卫生",
            "案板有油",
            timedelta(hours=2),
            self._live(),
        )
        await self.work.reshoot_fix(_staff(11, DAY_PHONE, "白班"), ticket["id"], self._live())
        reshoot_row = await self._one(
            "SELECT id FROM hygiene_fix_reshoots WHERE ticket_id = ? ORDER BY id DESC",
            (ticket["id"],),
        )
        result = await self.work.delete_archive_record(
            SUPER, KIND_FIX_RESHOOT, int(reshoot_row["id"])
        )
        self.assertEqual(result["status_reset"], 1)
        after = await self.work.get_fix_ticket(ticket["id"], actor=SUPER)
        self.assertEqual(after["status"], "待回拍")

    async def test_purge_by_range_removes_records_and_photos_but_keeps_tickets(self):
        zone, _item, submitted = await self._daily_setup()
        ticket = await self.work.open_fix(
            _staff(11, DAY_PHONE, "白班", permission="管理员"),
            zone["id"],
            "卫生",
            "案板有油",
            timedelta(hours=2),
            self._live(),
        )
        deep_item = await self.work.add_deep_clean_item(
            SUPER, self.now_value.weekday(), "冷柜一号"
        )
        await self.work.submit_deep_clean_pair(
            _staff(11, DAY_PHONE, "白班"), deep_item["id"], self._live(), self._live()
        )

        self.assertTrue(await self.captures.exists_async(submitted["capture_id"]))
        result = await self.work.purge_archive_records(
            SUPER, [KIND_DAILY, KIND_DEEP], "2026-09-13", "2026-09-13"
        )
        self.assertEqual(result["records"], 2)
        self.assertEqual(result["photos"], 3)  # 日常 1 张 + 专项前后 2 张
        self.assertGreater(result["files"], result["photos"])
        self.assertFalse(await self.captures.exists_async(submitted["capture_id"]))

        remaining = await self.archive.list_records(date_from="2026-09-13", date_to="2026-09-13")
        kinds = {row["kind"] for row in remaining["items"]}
        # 整改单整单不参与范围清理（ADR-0087），它的原图还在。
        self.assertNotIn(KIND_DAILY, kinds)
        self.assertNotIn(KIND_DEEP, kinds)
        self.assertIn("fix", kinds)
        still_there = await self.work.get_fix_ticket(ticket["id"], actor=SUPER)
        self.assertEqual(still_there["id"], ticket["id"])

    async def test_purge_does_not_touch_board_events(self):
        _zone, item, _submitted = await self._daily_setup()
        before = await self.db._conn.execute("SELECT COUNT(*) AS n FROM hygiene_board_events")
        count_before = int(dict(await before.fetchone())["n"])
        self.assertGreaterEqual(count_before, 1)
        await self.work.purge_archive_records(SUPER, [KIND_DAILY], "2026-09-13", "2026-09-13")
        after = await self.db._conn.execute("SELECT COUNT(*) AS n FROM hygiene_board_events")
        self.assertEqual(int(dict(await after.fetchone())["n"]), count_before)

    # ---- 导出 -------------------------------------------------------------

    async def test_ledger_zip_contains_csv_and_named_photos(self):
        _zone, _item, _submitted = await self._daily_setup()
        records = await self.archive.iter_records(
            date_from="2026-09-13", date_to="2026-09-13", kinds=[KIND_DAILY]
        )
        entries = []
        for record in records:
            photos = []
            for photo in record["photos"]:
                view = await self.archive.photo_path(photo["capture_id"], "original")
                photos.append({**photo, "path": view["path"]})
            entries.append({"record": record, "photos": photos})
        target = Path(self._tmpdir.name) / "ledger.zip"
        written, skipped = write_ledger_zip(entries, target)
        self.assertEqual((written, skipped), (1, 0))
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            self.assertIn("记录.csv", names)
            photo_names = [name for name in names if name.startswith("照片/")]
            self.assertEqual(len(photo_names), 1)
            self.assertIn("日常实拍", photo_names[0])
            self.assertIn("案板表面", photo_names[0])
            self.assertIn("张三", photo_names[0])
            csv_text = archive.read("记录.csv").decode("utf-8-sig")
            self.assertIn("日常实拍", csv_text)
            self.assertIn("案板表面", csv_text)
            self.assertIn(photo_names[0].split("/", 1)[1], csv_text)

    async def test_ledger_zip_skips_missing_files_without_failing(self):
        records = [
            {
                "kind": KIND_DAILY,
                "kind_label": "日常实拍",
                "record_id": 1,
                "business_date": "2026-09-13",
                "occurred_at": "2026-09-13T10:00:00+08:00",
                "zone_name": "案板",
                "title": "案板表面",
                "subtitle": "白班",
                "status": "待验收",
                "submitter_name": "张三",
                "submitter_phone": DAY_PHONE,
                "photos": [],
            }
        ]
        entries = [
            {
                "record": records[0],
                "photos": [
                    {
                        "role": "实拍",
                        "capture_id": "missing",
                        "content_type": "image/jpeg",
                        "path": Path(self._tmpdir.name) / "nope.jpg",
                    }
                ],
            }
        ]
        target = Path(self._tmpdir.name) / "broken.zip"
        written, skipped = write_ledger_zip(entries, target)
        self.assertEqual((written, skipped), (0, 1))
        with zipfile.ZipFile(target) as archive:
            self.assertIn("记录.csv", archive.namelist())

    # ---- 区间解析 ---------------------------------------------------------

    async def test_range_rules(self):
        start, end = parse_archive_range(None, "2026-09-13")
        self.assertEqual(end, "2026-09-13")
        self.assertEqual(start, "2026-08-15")
        with self.assertRaises(ArchiveQueryError) as bad:
            parse_archive_range("2026-13-01", "2026-09-13")
        self.assertEqual(bad.exception.code, "bad_date")
        with self.assertRaises(ArchiveQueryError) as reversed_range:
            parse_archive_range("2026-09-13", "2026-09-01")
        self.assertEqual(reversed_range.exception.code, "bad_range")
        with self.assertRaises(ArchiveQueryError) as wide:
            parse_archive_range("2020-01-01", "2026-09-13")
        self.assertEqual(wide.exception.code, "range_too_wide")

    # ---- 历史回看（ADR-0088）---------------------------------------------

    async def test_history_lists_every_item_with_status(self):
        _zone, item, _submitted = await self._daily_setup()
        await self.work.accept_daily(SUPER, item["id"], "白班")

        today = await self.work.list_daily_work(SUPER)
        self.assertIn("已通过", {row["status"] for row in today})
        self.assertEqual({row["business_date"] for row in today}, {"2026-09-13"})

        # 换一个营业日：新的一天没有实例，全是待拍；回看 09-13 才看得到已通过。
        self._advance_days(2)
        fresh = await self.work.list_daily_work(SUPER)
        self.assertEqual({row["status"] for row in fresh}, {"待拍"})
        history = await self.work.list_daily_work(SUPER, "2026-09-13")
        self.assertIn("已通过", {row["status"] for row in history})
        self.assertEqual({row["business_date"] for row in history}, {"2026-09-13"})

    async def test_history_review_reads_passed_submission_and_flags_missing(self):
        _zone, item, submitted = await self._daily_setup()
        await self.work.accept_daily(SUPER, item["id"], "白班")
        self._advance_days(2)

        history = await self.work.get_daily_review(
            item["id"], "白班", actor=SUPER, business_date="2026-09-13"
        )
        self.assertEqual(history["status"], "已通过")
        self.assertTrue(history["historical"])
        self.assertEqual(history["capture_id"], submitted["capture_id"])
        self.assertTrue(history["capture_available"])
        self.assertTrue(history["standard_available"])

        with self.assertRaises(HygieneWorkError) as empty:
            await self.work.get_daily_review(
                item["id"], "夜班", actor=SUPER, business_date="2026-09-13"
            )
        self.assertEqual(empty.exception.code, "not_pending")

    async def test_today_review_still_requires_pending(self):
        _zone, item, _submitted = await self._daily_setup()
        await self.work.accept_daily(SUPER, item["id"], "白班")
        with self.assertRaises(HygieneWorkError) as not_pending:
            await self.work.get_daily_review(item["id"], "白班", actor=SUPER)
        self.assertEqual(not_pending.exception.code, "not_pending")

    async def test_history_review_sees_latest_submission_after_reject(self):
        _zone, item, first = await self._daily_setup()
        await self.work.reject_daily(SUPER, item["id"], "白班", "台面还有油")
        second = await self.work.submit_daily(
            _staff(11, DAY_PHONE, "白班"), item["id"], self._live()
        )
        self._advance_days(2)
        review = await self.work.get_daily_review(
            item["id"], "白班", actor=SUPER, business_date="2026-09-13"
        )
        self.assertEqual(review["capture_id"], second["capture_id"])
        self.assertNotEqual(review["capture_id"], first["capture_id"])
        self.assertEqual(review["status"], "待验收")

    async def test_standard_used_by_a_submission_cannot_be_deleted(self):
        _zone, item, _submitted = await self._daily_setup()
        frozen = item["current_standard_id"]
        await self.work.accept_daily(SUPER, item["id"], "白班")
        await self.work.replace_standard(SUPER, item["id"], self._standard())
        # 换过图也不行：那一版已经被这次提交冻结成对照，删了历史回看就缺一半。
        with self.assertRaises(HygieneWorkError) as in_use:
            await self.work.delete_archive_record(SUPER, KIND_STANDARD, frozen)
        self.assertEqual(in_use.exception.code, "standard_in_use")
        self._advance_days(2)
        review = await self.work.get_daily_review(
            item["id"], "白班", actor=SUPER, business_date="2026-09-13"
        )
        self.assertTrue(review["standard_available"])

    async def test_history_review_of_deleted_submission_is_empty(self):
        _zone, item, submitted = await self._daily_setup()
        await self.work.accept_daily(SUPER, item["id"], "白班")
        await self.work.delete_archive_record(SUPER, KIND_DAILY, submitted["id"])
        self._advance_days(2)
        with self.assertRaises(HygieneWorkError) as gone:
            await self.work.get_daily_review(
                item["id"], "白班", actor=SUPER, business_date="2026-09-13"
            )
        self.assertEqual(gone.exception.code, "not_pending")


if __name__ == "__main__":
    unittest.main()
