#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HygieneWork: 卫生责任区, 日常检查项, current 标准图."""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.notifier import FakeNotifier
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
        with self.assertRaises(HygieneWorkError) as clocks:
            await self.work.set_daily_overdue_clocks(STAFF_ADMIN, "16:00", "22:00")
        self.assertEqual(clocks.exception.code, "forbidden")
        with self.assertRaises(HygieneWorkError) as deep_item:
            await self.work.add_deep_clean_item(STAFF_ADMIN, 6, "冷柜一号")
        self.assertEqual(deep_item.exception.code, "forbidden")
        with self.assertRaises(HygieneWorkError) as deep_clock:
            await self.work.set_deep_clean_overdue_clock(STAFF, "20:00")
        self.assertEqual(deep_clock.exception.code, "forbidden")
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


class HygieneDailyOverdueTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
        self.captures = FakeCaptureStore()
        self.notifier = FakeNotifier()
        self.work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: self.fixed_now,
            notifier=self.notifier,
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

    async def _anban_item(self):
        zones = await self.work.list_zones()
        return await self.work.add_daily_item(
            SUPER,
            self._zone(zones, "案板")["id"],
            "案板表面",
            self._capture(OLD_BYTES),
        )

    async def test_sweep_notifies_once_after_configured_day_clock(self):
        item = await self._anban_item()
        clocks = await self.work.set_daily_overdue_clocks(SUPER, "15:00", "21:30")
        self.assertEqual(clocks["day_hhmm"], "15:00")
        self.assertEqual(clocks["night_hhmm"], "21:30")

        self.fixed_now = datetime(2026, 9, 13, 14, 59, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.assertEqual(self.notifier.texts, [])

        self.fixed_now = datetime(2026, 9, 13, 15, 0, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.assertEqual(len(self.notifier.texts), 1)
        text = self.notifier.texts[0]
        self.assertIsInstance(text, str)
        self.assertIn("白班", text)
        self.assertIn("案板", text)
        self.assertIn("案板表面", text)
        self.assertNotIn(DAY_PHONE, text)
        self.assertNotIn("@", text)
        self.assertNotIn("image", text.lower())
        self.assertNotIn(str(item["id"]) + ".jpg", text)

        await self.work.sweep_overdue()
        self.assertEqual(len(self.notifier.texts), 1)

    async def test_pending_accept_before_clock_is_not_overdue(self):
        item = await self._anban_item()
        await self.work.set_daily_overdue_clocks(SUPER, "15:00", "21:30")
        day = _staff(10, DAY_PHONE, "白班")
        self.fixed_now = datetime(2026, 9, 13, 14, 50, tzinfo=CHINA_TZ)
        submitted = await self.work.submit_daily(day, item["id"], self._live(SHOT_A))
        self.assertEqual(submitted["status"], "待验收")

        self.fixed_now = datetime(2026, 9, 13, 15, 1, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.assertEqual(self.notifier.texts, [])
        zone_events = await self.work.list_zone_board_events()
        self.assertEqual(zone_events, [])

    async def test_changed_clocks_are_read_not_hardcoded_1500(self):
        await self._anban_item()
        await self.work.set_daily_overdue_clocks(SUPER, "16:00", "21:30")
        self.fixed_now = datetime(2026, 9, 13, 15, 30, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.assertEqual(self.notifier.texts, [])
        clocks = await self.work.get_daily_overdue_clocks()
        self.assertEqual(clocks["day_hhmm"], "16:00")
        self.assertEqual(clocks["night_hhmm"], "21:30")

    async def test_missed_daily_increments_zone_board_not_shift_picker(self):
        from services.hygiene.accounts import EmployeeAccounts

        accounts = EmployeeAccounts(self.db, now=lambda: self.fixed_now)
        picker = await accounts.register("13800138010", "password123")
        await accounts.approve(picker["id"])
        await accounts.pick_shift(picker["id"], "白班")
        item = await self._anban_item()
        await self.work.set_daily_overdue_clocks(SUPER, "15:00", "21:30")
        self.fixed_now = datetime(2026, 9, 13, 15, 0, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        zone_events = await self.work.list_zone_board_events(item["zone_id"])
        self.assertEqual(len(zone_events), 1)
        self.assertEqual(zone_events[0]["event_type"], "逾期")
        self.assertEqual(zone_events[0]["zone_id"], item["zone_id"])
        self.assertIsNone(zone_events[0]["employee_id"])
        person_events = await self.work.list_person_board_events(picker["id"])
        self.assertEqual(person_events, [])

    async def test_submit_and_reject_record_person_board_missed_sibling_zone_only(self):
        zones = await self.work.list_zones()
        anban = await self.work.add_daily_item(
            SUPER,
            self._zone(zones, "案板")["id"],
            "案板表面",
            self._capture(OLD_BYTES),
        )
        xian = await self.work.add_daily_item(
            SUPER,
            self._zone(zones, "馅档")["id"],
            "馅档台面",
            self._capture(ZONE_A_BYTES),
        )
        day = _staff(10, DAY_PHONE, "白班")
        reviewer = _staff(21, OTHER_ADMIN_PHONE, "白班", "管理员")
        await self.work.set_daily_overdue_clocks(SUPER, "15:00", "21:30")
        self.fixed_now = datetime(2026, 9, 13, 14, 40, tzinfo=CHINA_TZ)
        await self.work.submit_daily(day, anban["id"], self._live(SHOT_A))
        await self.work.reject_daily(reviewer, anban["id"], "白班")
        await self.work.submit_daily(day, anban["id"], self._live(SHOT_B))
        person_events = await self.work.list_person_board_events(day["id"])
        types = [event["event_type"] for event in person_events]
        self.assertEqual(types, ["实拍", "驳回", "实拍"])
        self.assertTrue(all(event["employee_id"] == day["id"] for event in person_events))
        self.assertEqual(await self.work.list_person_board_events(reviewer["id"]), [])

        self.fixed_now = datetime(2026, 9, 13, 15, 0, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        anban_missed = [
            event
            for event in await self.work.list_zone_board_events(anban["zone_id"])
            if event["event_type"] == "逾期"
        ]
        xian_missed = [
            event
            for event in await self.work.list_zone_board_events(xian["zone_id"])
            if event["event_type"] == "逾期"
        ]
        self.assertEqual(anban_missed, [])
        self.assertEqual(len(xian_missed), 1)
        self.assertEqual(xian_missed[0]["zone_id"], xian["zone_id"])
        still_person = await self.work.list_person_board_events(day["id"])
        self.assertEqual(
            [event["event_type"] for event in still_person], ["实拍", "驳回", "实拍"]
        )
        self.assertFalse(any(event["event_type"] == "逾期" for event in still_person))


SUNDAY = 6
FRIDGE_A = "冷柜一号"
FRIDGE_B = "冷柜二号"
BEFORE_A = b"BEFORE-A"
AFTER_A = b"AFTER-A"
BEFORE_B = b"BEFORE-B"
AFTER_B = b"AFTER-B"


class HygieneDeepCleanTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
        self.captures = FakeCaptureStore()
        self.notifier = FakeNotifier()
        self.work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: self.fixed_now,
            notifier=self.notifier,
        )
        await self.work.prepare()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def _live(self, data):
        return {"bytes": data, "content_type": "image/jpeg", "live": True}

    async def test_add_deep_clean_item_without_standard_appears_for_staff(self):
        added = await self.work.add_deep_clean_item(SUPER, SUNDAY, FRIDGE_A)
        self.assertEqual(added["name"], FRIDGE_A)
        self.assertEqual(added["weekday"], SUNDAY)
        self.assertNotIn("current_standard_id", added)
        self.assertNotIn("capture_id", added)
        listed = await self.work.list_deep_clean_work(_staff(10, DAY_PHONE, "白班"))
        names = [row["item_name"] for row in listed["items"]]
        self.assertEqual(names, [FRIDGE_A])
        self.assertEqual(listed["items"][0]["status"], "待拍")
        self.assertIsNone(listed["items"][0].get("zone_name"))

    async def _two_sunday_items(self):
        first = await self.work.add_deep_clean_item(SUPER, SUNDAY, FRIDGE_A)
        second = await self.work.add_deep_clean_item(SUPER, SUNDAY, FRIDGE_B)
        return first, second

    def _pair_row(self, listed, item_id):
        return next(row for row in listed["items"] if row["item_id"] == item_id)

    async def test_one_pair_does_not_complete_until_every_pair_accepted(self):
        first, second = await self._two_sunday_items()
        day = _staff(10, DAY_PHONE, "白班")
        reviewer = _staff(21, OTHER_ADMIN_PHONE, "白班", "管理员")
        await self.work.submit_deep_clean_pair(
            day, first["id"], self._live(BEFORE_A), self._live(AFTER_A)
        )
        listed = await self.work.list_deep_clean_work(day)
        self.assertEqual(listed["status"], "待办")
        self.assertEqual(self._pair_row(listed, first["id"])["status"], "待验收")
        self.assertEqual(self._pair_row(listed, second["id"])["status"], "待拍")
        await self.work.accept_deep_clean_pair(reviewer, first["id"])
        still = await self.work.list_deep_clean_work(day)
        self.assertEqual(still["status"], "待办")
        self.assertEqual(self._pair_row(still, first["id"])["status"], "已通过")
        self.assertEqual(self._pair_row(still, second["id"])["status"], "待拍")
        await self.work.submit_deep_clean_pair(
            day, second["id"], self._live(BEFORE_B), self._live(AFTER_B)
        )
        await self.work.accept_deep_clean_pair(reviewer, second["id"])
        done = await self.work.list_deep_clean_work(day)
        self.assertEqual(done["status"], "已完成")
        self.assertEqual(self._pair_row(done, first["id"])["status"], "已通过")
        self.assertEqual(self._pair_row(done, second["id"])["status"], "已通过")

    async def test_day_and_night_staff_can_both_submit_same_weekday_task(self):
        first, second = await self._two_sunday_items()
        day = _staff(10, DAY_PHONE, "白班")
        night = _staff(11, NIGHT_PHONE, "夜班")
        day_shot = await self.work.submit_deep_clean_pair(
            day, first["id"], self._live(BEFORE_A), self._live(AFTER_A)
        )
        night_shot = await self.work.submit_deep_clean_pair(
            night, second["id"], self._live(BEFORE_B), self._live(AFTER_B)
        )
        self.assertEqual(day_shot["status"], "待验收")
        self.assertEqual(night_shot["status"], "待验收")
        listed = await self.work.list_deep_clean_work(night)
        self.assertEqual(self._pair_row(listed, first["id"])["submitter_phone"], DAY_PHONE)
        self.assertEqual(self._pair_row(listed, second["id"])["submitter_phone"], NIGHT_PHONE)

    async def test_submitter_cannot_accept_own_pair_other_admin_or_super_can(self):
        first, second = await self._two_sunday_items()
        submitter = _staff(20, ADMIN_PHONE, "夜班", "管理员")
        other = _staff(21, OTHER_ADMIN_PHONE, "白班", "管理员")
        await self.work.submit_deep_clean_pair(
            submitter, first["id"], self._live(BEFORE_A), self._live(AFTER_A)
        )
        with self.assertRaises(HygieneWorkError) as self_accept:
            await self.work.accept_deep_clean_pair(submitter, first["id"])
        self.assertEqual(self_accept.exception.code, "cannot_self_accept")
        regular = _staff(10, DAY_PHONE, "白班")
        with self.assertRaises(HygieneWorkError) as staff_accept:
            await self.work.accept_deep_clean_pair(regular, first["id"])
        self.assertEqual(staff_accept.exception.code, "forbidden")
        passed = await self.work.accept_deep_clean_pair(other, first["id"])
        self.assertEqual(passed["status"], "已通过")
        await self.work.submit_deep_clean_pair(
            submitter, second["id"], self._live(BEFORE_B), self._live(AFTER_B)
        )
        rejected = await self.work.reject_deep_clean_pair(SUPER, second["id"])
        self.assertEqual(rejected["status"], "待拍")
        inbox = await self.work.list_deep_clean_work(submitter)
        row = self._pair_row(inbox, second["id"])
        self.assertEqual(row["status"], "待拍")
        self.assertIsNone(row["before_capture_id"])
        self.assertIsNone(row["after_capture_id"])

    async def test_watermark_has_item_name_not_zone_name(self):
        first, _second = await self._two_sunday_items()
        day = _staff(10, DAY_PHONE, "白班")
        with self.assertRaises(HygieneWorkError) as album:
            await self.work.submit_deep_clean_pair(
                day,
                first["id"],
                {"bytes": BEFORE_A, "content_type": "image/jpeg", "live": False},
                self._live(AFTER_A),
            )
        self.assertEqual(album.exception.code, "live_required")
        submitted = await self.work.submit_deep_clean_pair(
            day, first["id"], self._live(BEFORE_A), self._live(AFTER_A)
        )
        expected = {
            "time": "2026-09-13T10:00:00+08:00",
            "item_name": FRIDGE_A,
            "photographer": DAY_PHONE,
        }
        self.assertEqual(submitted["watermark"], expected)
        self.assertNotIn("zone", submitted["watermark"])
        self.assertEqual(submitted["before_watermark"]["item_name"], FRIDGE_A)
        self.assertEqual(submitted["after_watermark"]["item_name"], FRIDGE_A)
        review = await self.work.get_deep_clean_review(first["id"])
        self.assertEqual(review["watermark"]["item_name"], FRIDGE_A)
        self.assertNotIn("zone", review["watermark"])

    async def test_later_pair_submit_replaces_pending_before_and_after(self):
        first, _second = await self._two_sunday_items()
        day = _staff(10, DAY_PHONE, "白班")
        first_shot = await self.work.submit_deep_clean_pair(
            day, first["id"], self._live(BEFORE_A), self._live(AFTER_A)
        )
        second_shot = await self.work.submit_deep_clean_pair(
            day, first["id"], self._live(BEFORE_B), self._live(AFTER_B)
        )
        self.assertEqual(second_shot["status"], "待验收")
        self.assertNotEqual(second_shot["before_capture_id"], first_shot["before_capture_id"])
        self.assertNotEqual(second_shot["after_capture_id"], first_shot["after_capture_id"])
        review = await self.work.get_deep_clean_review(first["id"])
        self.assertEqual(self.captures.get(review["before_capture_id"]), BEFORE_B)
        self.assertEqual(self.captures.get(review["after_capture_id"]), AFTER_B)
        self.assertNotEqual(self.captures.get(review["before_capture_id"]), BEFORE_A)
        listed = await self.work.list_deep_clean_work(day)
        row = self._pair_row(listed, first["id"])
        self.assertEqual(self.captures.get(row["before_capture_id"]), BEFORE_B)
        self.assertEqual(self.captures.get(row["after_capture_id"]), AFTER_B)

    async def test_overdue_incomplete_notifies_once_calendar_miss_not_boards(self):
        first, second = await self._two_sunday_items()
        clocks = await self.work.set_deep_clean_overdue_clock(SUPER, "20:00")
        self.assertEqual(clocks["hhmm"], "20:00")
        day = _staff(10, DAY_PHONE, "白班")
        await self.work.submit_deep_clean_pair(
            day, first["id"], self._live(BEFORE_A), self._live(AFTER_A)
        )
        await self.work.accept_deep_clean_pair(SUPER, first["id"])

        self.fixed_now = datetime(2026, 9, 13, 19, 59, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.assertEqual(self.notifier.texts, [])

        self.fixed_now = datetime(2026, 9, 13, 20, 0, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.assertEqual(len(self.notifier.texts), 1)
        text = self.notifier.texts[0]
        self.assertIsInstance(text, str)
        self.assertIn("专项卫生", text)
        self.assertIn("2026-09-13", text)
        self.assertNotIn(DAY_PHONE, text)
        self.assertNotIn("@", text)
        self.assertNotIn("image", text.lower())
        self.assertNotIn("案板", text)

        await self.work.sweep_overdue()
        self.assertEqual(len(self.notifier.texts), 1)
        self.assertEqual(await self.work.list_zone_board_events(), [])
        self.assertEqual(await self.work.list_person_board_events(), [])
        calendar = await self.work.list_deep_clean_calendar("2026-09-13", "2026-09-13")
        self.assertEqual(len(calendar), 1)
        self.assertEqual(calendar[0]["business_date"], "2026-09-13")
        self.assertEqual(calendar[0]["status"], "未完成")
        self.assertEqual(calendar[0]["weekday"], SUNDAY)



