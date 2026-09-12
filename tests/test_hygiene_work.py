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



