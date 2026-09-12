#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HygieneWork: 卫生责任区, 日常检查项, current 标准图."""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.work import HygieneWork, HygieneWorkError

SEED_ZONE_NAMES = ["案板", "馅档", "熟笼", "肠粉", "西饼", "明档1", "明档2", "煎炸"]
SUPER = {"kind": "super"}
STAFF = {"kind": "staff", "id": 1, "permission": "普通员工"}
STAFF_ADMIN = {"kind": "staff", "id": 2, "permission": "管理员"}
OLD_BYTES = b"OLD-STANDARD-JPEG-LITERAL"
NEW_BYTES = b"NEW-STANDARD-JPEG-LITERAL"
ZONE_A_BYTES = b"ZONE-A-STANDARD-BYTES"


class HygieneWorkTest(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_seeded_eight_zones_and_create_adds_another(self):
        zones = await self.work.list_zones()
        names = [zone["name"] for zone in zones]
        self.assertEqual(names, SEED_ZONE_NAMES)
        extra = await self.work.create_zone(SUPER, "卫生间")
        self.assertEqual(extra["name"], "卫生间")
        self.assertTrue(extra["id"])
        again = [zone["name"] for zone in await self.work.list_zones()]
        self.assertEqual(again, SEED_ZONE_NAMES + ["卫生间"])
        from config import KITCHEN_STATIONS
        station_names = {meta["name"] for meta in KITCHEN_STATIONS.values()}
        self.assertNotEqual(set(SEED_ZONE_NAMES), station_names)
        self.assertNotIn("卫生间", station_names)

    def _zone(self, zones, name):
        return next(zone for zone in zones if zone["name"] == name)

    def _capture(self, data):
        return {"bytes": data, "content_type": "image/jpeg", "markup": []}

    async def test_item_without_standard_not_on_staff_catalog_and_lists_are_per_zone(self):
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")
        xian = self._zone(zones, "馅档")
        with self.assertRaises(HygieneWorkError) as raised:
            await self.work.add_daily_item(SUPER, anban["id"], "案板表面", None)
        self.assertEqual(raised.exception.code, "standard_required")
        catalog = await self.work.list_staff_daily_items()
        self.assertEqual([zone["name"] for zone in catalog], SEED_ZONE_NAMES)
        self.assertEqual(self._zone(catalog, "案板")["items"], [])
        self.assertEqual(self._zone(catalog, "馅档")["items"], [])

        added = await self.work.add_daily_item(
            SUPER, anban["id"], "案板表面", self._capture(ZONE_A_BYTES)
        )
        self.assertEqual(added["name"], "案板表面")
        self.assertEqual(added["zone_id"], anban["id"])
        self.assertTrue(added["current_standard_id"])
        catalog = await self.work.list_staff_daily_items()
        anban_items = self._zone(catalog, "案板")["items"]
        self.assertEqual([item["name"] for item in anban_items], ["案板表面"])
        self.assertEqual(self._zone(catalog, "馅档")["items"], [])
        self.assertEqual(self.captures.get(anban_items[0]["capture_id"]), ZONE_A_BYTES)

    async def test_replace_standard_new_view_uses_new_capture_not_old(self):
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")
        item = await self.work.add_daily_item(
            SUPER, anban["id"], "案板表面", self._capture(OLD_BYTES)
        )
        old = await self.work.current_standard(item["id"])
        self.assertEqual(self.captures.get(old["capture_id"]), OLD_BYTES)
        self.assertEqual(old["id"], item["current_standard_id"])

        replaced = await self.work.replace_standard(
            SUPER, item["id"], self._capture(NEW_BYTES)
        )
        self.assertNotEqual(replaced["current_standard_id"], old["id"])
        current = await self.work.current_standard(item["id"])
        self.assertEqual(current["id"], replaced["current_standard_id"])
        self.assertEqual(self.captures.get(current["capture_id"]), NEW_BYTES)
        self.assertNotEqual(current["capture_id"], old["capture_id"])
        self.assertEqual(self.captures.get(old["capture_id"]), OLD_BYTES)

        catalog = await self.work.list_staff_daily_items()
        listed = self._zone(catalog, "案板")["items"][0]
        self.assertEqual(listed["current_standard_id"], current["id"])
        self.assertEqual(self.captures.get(listed["capture_id"]), NEW_BYTES)

    async def test_staff_actor_cannot_write_config_super_can(self):
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")
        with self.assertRaises(HygieneWorkError) as created:
            await self.work.create_zone(STAFF, "卫生间")
        self.assertEqual(created.exception.code, "forbidden")
        with self.assertRaises(HygieneWorkError) as manager_created:
            await self.work.create_zone(STAFF_ADMIN, "卫生间")
        self.assertEqual(manager_created.exception.code, "forbidden")
        self.assertEqual(
            [zone["name"] for zone in await self.work.list_zones()],
            SEED_ZONE_NAMES,
        )
        with self.assertRaises(HygieneWorkError) as added:
            await self.work.add_daily_item(
                STAFF, anban["id"], "案板表面", self._capture(ZONE_A_BYTES)
            )
        self.assertEqual(added.exception.code, "forbidden")
        item = await self.work.add_daily_item(
            SUPER, anban["id"], "案板表面", self._capture(OLD_BYTES)
        )
        with self.assertRaises(HygieneWorkError) as replaced:
            await self.work.replace_standard(STAFF, item["id"], self._capture(NEW_BYTES))
        self.assertEqual(replaced.exception.code, "forbidden")
        current = await self.work.current_standard(item["id"])
        self.assertEqual(self.captures.get(current["capture_id"]), OLD_BYTES)
        zone = await self.work.create_zone(SUPER, "卫生间")
        self.assertEqual(zone["name"], "卫生间")

    async def test_staff_catalog_is_same_for_day_and_night_shift(self):
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")
        await self.work.add_daily_item(
            SUPER, anban["id"], "案板表面", self._capture(ZONE_A_BYTES)
        )
        catalog = await self.work.list_staff_daily_items()
        again = await self.work.list_staff_daily_items()
        self.assertEqual(catalog, again)
        names = [item["name"] for item in self._zone(catalog, "案板")["items"]]
        self.assertEqual(names, ["案板表面"])
        self.assertNotIn("shift", catalog[0])
        self.assertNotIn("shift", self._zone(catalog, "案板")["items"][0])

    async def test_standard_keeps_circle_arrow_caption_markup(self):
        markup = [
            {"kind": "circle", "x": 0.4, "y": 0.3, "r": 0.08},
            {"kind": "arrow", "x1": 0.2, "y1": 0.8, "x2": 0.5, "y2": 0.4},
            {"kind": "caption", "x": 0.5, "y": 0.9, "text": "擦干净"},
        ]
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")
        item = await self.work.add_daily_item(
            SUPER,
            anban["id"],
            "案板表面",
            {"bytes": ZONE_A_BYTES, "content_type": "image/jpeg", "markup": markup},
        )
        current = await self.work.current_standard(item["id"])
        self.assertEqual(current["markup"], markup)
        catalog = await self.work.list_staff_daily_items()
        self.assertEqual(self._zone(catalog, "案板")["items"][0]["markup"], markup)


SHOT_A = b"SHOT-A"
SHOT_B = b"SHOT-B"
DAY_PHONE = "13800138010"
NIGHT_PHONE = "13800138011"
ADMIN_PHONE = "13800138013"
OTHER_ADMIN_PHONE = "13800138014"


def _staff(employee_id, phone, shift, permission="普通员工"):
    return {
        "kind": "staff",
        "id": employee_id,
        "permission": permission,
        "phone": phone,
        "shift": shift,
    }


class HygieneDailySubmitTest(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def _zone(self, zones, name):
        return next(zone for zone in zones if zone["name"] == name)

    def _capture(self, data):
        return {"bytes": data, "content_type": "image/jpeg", "markup": []}

    def _live(self, data):
        return {"bytes": data, "content_type": "image/jpeg", "live": True}

    def _inbox_row(self, inbox, item_id, shift):
        return next(
            row
            for row in inbox
            if row["item_id"] == item_id and row["shift"] == shift
        )

    async def _two_items(self):
        zones = await self.work.list_zones()
        anban = await self.work.add_daily_item(
            SUPER, self._zone(zones, "案板")["id"], "案板表面", self._capture(OLD_BYTES)
        )
        xian = await self.work.add_daily_item(
            SUPER, self._zone(zones, "馅档")["id"], "馅档台面", self._capture(ZONE_A_BYTES)
        )
        return anban, xian

    async def test_night_cannot_submit_day_instance_but_day_can_shoot_other_zone(self):
        anban, xian = await self._two_items()
        night = _staff(11, NIGHT_PHONE, "夜班")
        day = _staff(10, DAY_PHONE, "白班")
        with self.assertRaises(HygieneWorkError) as raised:
            await self.work.submit_daily(
                night, anban["id"], self._live(SHOT_A), shift="白班"
            )
        self.assertEqual(raised.exception.code, "shift_mismatch")
        submitted = await self.work.submit_daily(day, xian["id"], self._live(SHOT_A))
        self.assertEqual(submitted["status"], "待验收")
        self.assertEqual(submitted["shift"], "白班")
        self.assertEqual(submitted["zone_name"], "馅档")
        inbox = await self.work.list_daily_work(day)
        row = self._inbox_row(inbox, xian["id"], "白班")
        self.assertEqual(row["status"], "待验收")
        self.assertEqual(row["zone_name"], "馅档")
        self.assertEqual(self.captures.get(row["capture_id"]), SHOT_A)

    async def test_later_submit_replaces_pending_capture(self):
        _anban, xian = await self._two_items()
        day = _staff(10, DAY_PHONE, "白班")
        first = await self.work.submit_daily(day, xian["id"], self._live(SHOT_A))
        second = await self.work.submit_daily(day, xian["id"], self._live(SHOT_B))
        self.assertEqual(second["status"], "待验收")
        self.assertNotEqual(second["capture_id"], first["capture_id"])
        review = await self.work.get_daily_review(xian["id"], "白班")
        self.assertEqual(self.captures.get(review["capture_id"]), SHOT_B)
        self.assertNotEqual(self.captures.get(review["capture_id"]), SHOT_A)
        inbox = await self.work.list_daily_work(day)
        row = self._inbox_row(inbox, xian["id"], "白班")
        self.assertEqual(self.captures.get(row["capture_id"]), SHOT_B)

    async def test_submitter_cannot_accept_other_admin_or_super_can(self):
        anban, xian = await self._two_items()
        submitter = _staff(20, ADMIN_PHONE, "白班", "管理员")
        other = _staff(21, OTHER_ADMIN_PHONE, "白班", "管理员")
        await self.work.submit_daily(submitter, anban["id"], self._live(SHOT_A))
        with self.assertRaises(HygieneWorkError) as self_accept:
            await self.work.accept_daily(submitter, anban["id"], "白班")
        self.assertEqual(self_accept.exception.code, "cannot_self_accept")
        regular = _staff(10, DAY_PHONE, "白班")
        with self.assertRaises(HygieneWorkError) as staff_accept:
            await self.work.accept_daily(regular, anban["id"], "白班")
        self.assertEqual(staff_accept.exception.code, "forbidden")
        passed = await self.work.accept_daily(other, anban["id"], "白班")
        self.assertEqual(passed["status"], "已通过")
        self.assertEqual(self._inbox_row(await self.work.list_daily_work(other), anban["id"], "白班")["status"], "已通过")
        await self.work.submit_daily(submitter, xian["id"], self._live(SHOT_B))
        rejected = await self.work.reject_daily(SUPER, xian["id"], "白班")
        self.assertEqual(rejected["status"], "待拍")

    async def test_accept_uses_standard_frozen_at_submit(self):
        anban, _xian = await self._two_items()
        day = _staff(10, DAY_PHONE, "白班")
        submitted = await self.work.submit_daily(day, anban["id"], self._live(SHOT_A))
        frozen_id = submitted["frozen_standard_id"]
        old = await self.work.standard_by_id(frozen_id)
        self.assertEqual(self.captures.get(old["capture_id"]), OLD_BYTES)
        replaced = await self.work.replace_standard(SUPER, anban["id"], self._capture(NEW_BYTES))
        self.assertNotEqual(replaced["current_standard_id"], frozen_id)
        review = await self.work.get_daily_review(anban["id"], "白班")
        self.assertEqual(review["frozen_standard_id"], frozen_id)
        frozen = await self.work.standard_by_id(review["frozen_standard_id"])
        self.assertEqual(self.captures.get(frozen["capture_id"]), OLD_BYTES)
        current = await self.work.current_standard(anban["id"])
        self.assertEqual(self.captures.get(current["capture_id"]), NEW_BYTES)
        passed = await self.work.accept_daily(SUPER, anban["id"], "白班")
        self.assertEqual(passed["status"], "已通过")
        still = await self.work.standard_by_id(frozen_id)
        self.assertEqual(self.captures.get(still["capture_id"]), OLD_BYTES)

    async def test_reject_returns_instance_to_todo_without_pending_capture(self):
        anban, _xian = await self._two_items()
        day = _staff(10, DAY_PHONE, "白班")
        reviewer = _staff(21, OTHER_ADMIN_PHONE, "白班", "管理员")
        await self.work.submit_daily(day, anban["id"], self._live(SHOT_A))
        rejected = await self.work.reject_daily(reviewer, anban["id"], "白班")
        self.assertEqual(rejected["status"], "待拍")
        inbox = await self.work.list_daily_work(day)
        row = self._inbox_row(inbox, anban["id"], "白班")
        self.assertEqual(row["status"], "待拍")
        self.assertIsNone(row["capture_id"])
        self.assertIsNone(row["frozen_standard_id"])
        self.assertIsNone(row["watermark"])
        with self.assertRaises(HygieneWorkError) as raised:
            await self.work.get_daily_review(anban["id"], "白班")
        self.assertEqual(raised.exception.code, "not_pending")
        again = await self.work.submit_daily(day, anban["id"], self._live(SHOT_B))
        self.assertEqual(again["status"], "待验收")
        self.assertEqual(self.captures.get(again["capture_id"]), SHOT_B)

    async def test_watermark_fields_are_injected_now_zone_and_photographer(self):
        _anban, xian = await self._two_items()
        day = _staff(10, DAY_PHONE, "白班")
        with self.assertRaises(HygieneWorkError) as album:
            await self.work.submit_daily(
                day,
                xian["id"],
                {"bytes": SHOT_A, "content_type": "image/jpeg", "live": False},
            )
        self.assertEqual(album.exception.code, "live_required")
        with self.assertRaises(HygieneWorkError) as no_shift:
            await self.work.submit_daily(
                {**day, "shift": None}, xian["id"], self._live(SHOT_A)
            )
        self.assertEqual(no_shift.exception.code, "shift_required")
        submitted = await self.work.submit_daily(day, xian["id"], self._live(SHOT_A))
        self.assertEqual(
            submitted["watermark"],
            {
                "time": "2026-09-13T10:00:00+08:00",
                "zone": "馅档",
                "photographer": DAY_PHONE,
            },
        )
        review = await self.work.get_daily_review(xian["id"], "白班")
        self.assertEqual(review["watermark"]["time"], "2026-09-13T10:00:00+08:00")
        self.assertEqual(review["watermark"]["zone"], "馅档")
        self.assertEqual(review["watermark"]["photographer"], DAY_PHONE)

    async def test_inbox_is_shop_wide_not_filtered_by_zone(self):
        anban, xian = await self._two_items()
        day = _staff(10, DAY_PHONE, "白班")
        await self.work.submit_daily(day, xian["id"], self._live(SHOT_A))
        inbox = await self.work.list_daily_work(day)
        seen = {
            (row["zone_name"], row["item_name"], row["shift"], row["status"])
            for row in inbox
        }
        self.assertIn(("案板", "案板表面", "白班", "待拍"), seen)
        self.assertIn(("案板", "案板表面", "夜班", "待拍"), seen)
        self.assertIn(("馅档", "馅档台面", "白班", "待验收"), seen)
        self.assertIn(("馅档", "馅档台面", "夜班", "待拍"), seen)
        night = _staff(11, NIGHT_PHONE, "夜班")
        night_inbox = await self.work.list_daily_work(night)
        self.assertEqual(
            {(row["zone_name"], row["shift"], row["status"]) for row in night_inbox},
            {(row["zone_name"], row["shift"], row["status"]) for row in inbox},
        )



