#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HygieneWork: 卫生工作区, 日常检查项, current 标准图."""

import tempfile
import unittest
from datetime import datetime, timedelta

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.notifier import FakeNotifier
from services.hygiene.work import (
    BOARD_PERSON,
    BOARD_ZONE,
    EVENT_FIRST_PASS,
    EVENT_MISSED_DAILY,
    EVENT_REJECT,
    OPENER_STAFF,
    HygieneWork,
    HygieneWorkError,
    hygiene_business_date,
    hygiene_week_start,
)
from tests.hygiene_duty import assign_duty

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

    async def test_zone_shifts_default_both_and_can_be_day_only(self):
        zones = await self.work.list_zones()
        self.assertTrue(all(zone["shifts"] == ["白班", "夜班"] for zone in zones))

        extra = await self.work.create_zone(SUPER, "卫生间", ["白班"])
        self.assertEqual(extra["shifts"], ["白班"])

        updated = await self.work.set_zone_shifts(SUPER, extra["id"], ["夜班"])
        self.assertEqual(updated["shifts"], ["夜班"])
        listed = self._zone(await self.work.list_zones(), "卫生间")
        self.assertEqual(listed["shifts"], ["夜班"])

        staff = {"kind": "staff", "id": 1, "permission": "管理员"}
        with self.assertRaises(HygieneWorkError) as forbidden:
            await self.work.set_zone_shifts(staff, extra["id"], ["白班"])
        self.assertEqual(forbidden.exception.code, "forbidden")

        with self.assertRaises(HygieneWorkError) as empty:
            await self.work.create_zone(SUPER, "库房", [])
        self.assertEqual(empty.exception.code, "zone_shift_required")

        with self.assertRaises(HygieneWorkError) as bad:
            await self.work.create_zone(SUPER, "库房", ["早班"])
        self.assertEqual(bad.exception.code, "invalid_shift")

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

    async def test_staff_zone_assignment_limits_daily_work_and_submit(self):
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")
        xian = self._zone(zones, "馅档")
        anban_item = await self.work.add_daily_item(
            SUPER, anban["id"], "案板表面", self._capture(b"ANBAN")
        )
        xian_item = await self.work.add_daily_item(
            SUPER, xian["id"], "馅料盆", self._capture(b"XIAN")
        )
        actor = {
            "kind": "staff",
            "id": 1,
            "permission": "普通员工",
            "name": "张三",
            "phone": DAY_PHONE,
            "shift": "白班",
            "zone_id": anban["id"],
        }

        inbox = await self.work.list_daily_work(actor)
        self.assertEqual({row["zone_id"] for row in inbox}, {anban["id"]})
        self.assertEqual(inbox[0]["item_id"], anban_item["id"])
        with self.assertRaises(HygieneWorkError) as raised:
            await self.work.submit_daily(
                actor,
                xian_item["id"],
                {"bytes": b"SHOT", "content_type": "image/jpeg", "live": True},
            )
        self.assertEqual(raised.exception.code, "zone_mismatch")

    async def test_day_only_zone_has_no_night_work_and_rejects_night_submit(self):
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")
        await self.work.set_zone_shifts(SUPER, anban["id"], ["白班"])
        item = await self.work.add_daily_item(
            SUPER, anban["id"], "案板表面", self._capture(OLD_BYTES)
        )
        day = _staff(10, DAY_PHONE, "白班")
        night = _staff(11, NIGHT_PHONE, "夜班")
        day["zone_id"] = anban["id"]
        night["zone_id"] = anban["id"]
        live = {"bytes": SHOT_A, "content_type": "image/jpeg", "live": True}

        inbox = await self.work.list_daily_work(SUPER)
        shifts = {row["shift"] for row in inbox if row["item_id"] == item["id"]}
        self.assertEqual(shifts, {"白班"})

        self.assertEqual(await self.work.list_daily_work(night), [])
        with self.assertRaises(HygieneWorkError) as raised:
            await self.work.submit_daily(night, item["id"], live)
        self.assertEqual(raised.exception.code, "zone_shift_mismatch")

        submitted = await self.work.submit_daily(day, item["id"], live)
        self.assertEqual(submitted["shift"], "白班")

    async def test_staff_zone_assignment_limits_fix_tickets(self):
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")
        xian = self._zone(zones, "馅档")
        live = {"bytes": b"FIX", "content_type": "image/jpeg", "live": True}
        anban_ticket = await self.work.open_fix(
            SUPER,
            anban["id"],
            "卫生",
            "案板脏",
            timedelta(hours=2),
            live,
        )
        xian_ticket = await self.work.open_fix(
            SUPER,
            xian["id"],
            "卫生",
            "馅档脏",
            timedelta(hours=2),
            live,
        )
        actor = {
            "kind": "staff",
            "id": 1,
            "permission": "普通员工",
            "name": "张三",
            "phone": DAY_PHONE,
            "shift": "白班",
            "zone_id": anban["id"],
        }

        tickets = await self.work.list_fix_tickets(actor)
        self.assertEqual([row["id"] for row in tickets], [anban_ticket["id"]])
        with self.assertRaises(HygieneWorkError) as raised:
            await self.work.get_fix_ticket(xian_ticket["id"], actor=actor)
        self.assertEqual(raised.exception.code, "zone_mismatch")

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


def _staff(employee_id, phone, shift, permission="普通员工", name="", caps=None):
    """造一个员工 actor。

    `caps` 是**管理权限开关**（2026-10-05 起判据看它，不看 `permission`）。不给就按迁移
    `0015` 的回填规则推：升级前那个「管理员」档位等于 `daily_review + deep_review + fix`
    三项 —— 于是升级前写的用例一字不改仍然表达同一个意思。
    """
    if caps is None:
        caps = (
            ("daily_review", "deep_review", "fix") if permission == "管理员" else ()
        )
    return {
        "kind": "staff",
        "id": employee_id,
        "permission": permission,
        "caps": list(caps),
        "name": name,
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

    async def test_pending_review_queue_is_shop_wide_for_admins(self):
        """「待我验收」那一档：管理员的验收权是全店的，但默认队列锁在他自己的区里。

        2026-10-05 用户确认「员工账号的管理员也同样有权限验收」。后端判据一直是全店的
        （`_require_reviewer` 只看 `daily_review` 那一项能力开关、不看区），缺的是**入口**：默认队列按
        「本区 + 本班次」过滤（`_inbox_filter`），今天别的区有人交了，他一条也看不到。
        `pending_review_only=True` 就是那条入口 —— 两个班次、全部区、只要待验收、
        且不含他自己交的那条（`cannot_self_accept`）。普通员工拿不到（forbidden）。
        """
        anban, xian = await self._two_items()
        zones = await self.work.list_zones()
        anban_zone = self._zone(zones, "案板")["id"]
        xian_zone = self._zone(zones, "馅档")["id"]

        # 同事在**馅档**交了活，等的就是管理员那一眼。
        mate = dict(_staff(12, ADMIN_PHONE, "白班", name="同事"), zone_id=xian_zone)
        await self.work.submit_daily(mate, xian["id"], self._live(SHOT_A))

        # 管理员今天排的是**案板**。
        manager = dict(
            _staff(13, OTHER_ADMIN_PHONE, "白班", permission="管理员", name="复核"),
            zone_id=anban_zone,
        )

        # 默认队列锁在本区：馅档那条看不见 —— 这正是"有权限、没入口"的现场。
        mine = await self.work.list_daily_work(manager)
        self.assertNotIn(xian["id"], [row["item_id"] for row in mine])

        # 「待我验收」跨区看得见（权限本来就是全店的）。
        review = await self.work.list_daily_work(manager, pending_review_only=True)
        rows = [row for row in review if row["item_id"] == xian["id"]]
        self.assertEqual(len(rows), 1, "馅档那条待验收应当出现在「待我验收」里")
        self.assertEqual(rows[0]["status"], "待验收")
        self.assertEqual(rows[0]["zone_name"], "馅档")

        # 自己交的不能自己验收：管理员在案板也交一条，它**不**出现在待验收那一档。
        await self.work.submit_daily(manager, anban["id"], self._live(SHOT_A))
        again = await self.work.list_daily_work(manager, pending_review_only=True)
        self.assertNotIn(anban["id"], [row["item_id"] for row in again])

        # 普通员工拿不到这一档（前端也不会问他）。
        plain = dict(_staff(14, DAY_PHONE, "白班"), zone_id=anban_zone)
        with self.assertRaises(HygieneWorkError) as raised:
            await self.work.list_daily_work(plain, pending_review_only=True)
        self.assertEqual(raised.exception.code, "forbidden")

    async def test_admin_caps_are_per_item_not_all_or_nothing(self):
        """管理权限是**逐项开关**（2026-10-05 用户裁定）：只勾了「日常验收」的人判不了专项。

        升级前只有一个「管理员」档位 —— 勾上就是三项全给，表达不出"他该判日常、不该判专项"。
        现在判据是 `_require_reviewer(actor, submitter_id, cap)`，这里直接压它：
        有那一项就放行、没有就 forbidden；自己交的仍然不能自己判（那条老规矩没动）。
        """
        mate_id = 12
        only_daily = _staff(
            13, OTHER_ADMIN_PHONE, "白班", permission="管理员", caps=("daily_review",)
        )
        # 有这一项：放行（不抛就是过）。
        self.work._require_reviewer(only_daily, mate_id, "daily_review")
        # 没有那一项：拒。
        with self.assertRaises(HygieneWorkError) as raised:
            self.work._require_reviewer(only_daily, mate_id, "deep_review")
        self.assertEqual(raised.exception.code, "forbidden")
        # 自己交的不能自己判 —— 哪怕他有那一项。
        with self.assertRaises(HygieneWorkError) as raised_self:
            self.work._require_reviewer(only_daily, 13, "daily_review")
        self.assertEqual(raised_self.exception.code, "cannot_self_accept")
        # 一个开关都没有 = 什么都判不了（普通员工那一档）。
        plain = _staff(14, DAY_PHONE, "白班")
        with self.assertRaises(HygieneWorkError) as raised_plain:
            self.work._require_reviewer(plain, mate_id, "daily_review")
        self.assertEqual(raised_plain.exception.code, "forbidden")
        # 超级管理员不受开关限制（他没走员工会话这条路）。
        self.work._require_reviewer({"kind": "super"}, mate_id, "deep_review")

    async def test_my_score_reads_the_same_events_as_the_board(self):
        """「我的成绩」与红黑榜是**同一份记录**，不另起计数。

        2026-10-05 用户裁定做这一项：员工端原来只有"今天要做什么"，看不到自己干得怎么样。
        口径是 `一次通过 /（一次通过 + 驳回）`，数据源就是"人的榜"那张事件表 ——
        如果哪天有人另起一套计数，员工看到的数字就会和店长看到的榜对不上，那种账最难查。
        """
        today = hygiene_business_date(self.work._now_dt())
        me = 7
        other = 8
        # 三条一次通过、一条驳回（带原因）、一条同事的（不该算进我的）
        for _ in range(3):
            await self.work._insert_board_event(
                BOARD_PERSON, EVENT_FIRST_PASS, employee_id=me, business_date=today
            )
        await self.work._insert_board_event(
            BOARD_PERSON,
            EVENT_REJECT,
            employee_id=me,
            business_date=today,
            reason="有水渍",
        )
        await self.work._insert_board_event(
            BOARD_PERSON, EVENT_FIRST_PASS, employee_id=other, business_date=today
        )

        score = await self.work.staff_score(me, days=7)
        self.assertEqual(score["first_pass"], 3)
        self.assertEqual(score["rejected"], 1)
        # 3/4 —— 同事那一条不能算进来。
        self.assertAlmostEqual(score["pass_rate"], 0.75)
        self.assertEqual(score["reasons"], [{"reason": "有水渍", "count": 1}])

        # 一次都没交过的人：pass_rate 是 None（不是 0）—— 前端据此显示"还没开始"，
        # 显示 0% 会让新人看着像已经被扣分了。
        empty = await self.work.staff_score(999, days=7)
        self.assertEqual(empty["first_pass"], 0)
        self.assertIsNone(empty["pass_rate"])
        self.assertEqual(empty["reasons"], [])

        # 窗口之外的事件不算（把那条驳回挪到 30 天前）。
        old_date = (datetime.fromisoformat(today) - timedelta(days=30)).date().isoformat()
        await self.work._conn.execute(
            "UPDATE hygiene_board_events SET business_date = ? WHERE employee_id = ? AND event_type = ?",
            (old_date, me, EVENT_REJECT),
        )
        await self.work._conn.commit()
        week = await self.work.staff_score(me, days=7)
        self.assertEqual(week["rejected"], 0)
        self.assertEqual(week["pass_rate"], 1.0)

    async def test_reject_requires_a_reason(self):
        """驳回**必须**写原因（2026-10-05 用户裁定）—— 这条在 API 层拦，服务层不动。

        服务层的 `reason` 仍然是可选的（管理端回看、脚本、历史数据都可能没有原因），
        把关放在七个 HTTP 驳回入口上（`_require_reject_reason`），那里才是人点出来的。
        """
        from api.hygiene import _require_reject_reason
        from fastapi import HTTPException

        self.assertEqual(_require_reject_reason("  有水渍  "), "有水渍")
        self.assertEqual(len(_require_reject_reason("x" * 500)), 200)
        for blank in (None, "", "   "):
            with self.assertRaises(HTTPException) as raised:
                _require_reject_reason(blank)
            self.assertEqual(raised.exception.status_code, 400)
            # 与仪容那条既有文案一字不差（七个入口一句话）—— 改文案要一起改测试。
            self.assertIn("写明", raised.exception.detail)

    async def test_trend_aggregates_days_zones_and_reasons(self):
        """趋势三个切面：按天 / 按区 / 按驳回原因（2026-10-05 用户裁定）。

        店长要能回答"这周比上周好还是差" —— 现场七页只有"当下"，没有一处能给这个答案。
        口径必须与榜、与员工端那张成绩卡**一致**（都从同一张事件表来），
        所以这里连"按区那次也按同一口径"一起钉住：逾期是另一条轴，不塞进分母。
        """
        today = hygiene_business_date(self.work._now_dt())
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")["id"]
        xian = self._zone(zones, "馅档")["id"]

        # 今天：案板 2 次一次通过 + 1 次驳回（带原因）；馅档 1 次一次通过。
        for _ in range(2):
            await self.work._insert_board_event(
                BOARD_PERSON, EVENT_FIRST_PASS, zone_id=anban, employee_id=11, business_date=today
            )
        await self.work._insert_board_event(
            BOARD_PERSON, EVENT_REJECT, zone_id=anban, employee_id=11,
            business_date=today, reason="有水渍",
        )
        await self.work._insert_board_event(
            BOARD_PERSON, EVENT_FIRST_PASS, zone_id=xian, employee_id=12, business_date=today
        )
        # 区的榜：案板逾期 3 次（另一条轴）。
        for _ in range(3):
            await self.work._insert_board_event(
                BOARD_ZONE, EVENT_MISSED_DAILY, zone_id=anban, business_date=today
            )
        # 30 天前的一条：不该进 7 天窗口。
        old = (datetime.fromisoformat(today) - timedelta(days=30)).date().isoformat()
        await self.work._insert_board_event(
            BOARD_PERSON, EVENT_REJECT, zone_id=xian, employee_id=12,
            business_date=old, reason="旧账",
        )
        await self.work._conn.commit()

        trend = await self.work.hygiene_trend(days=7)
        day = next(row for row in trend["daily"] if row["business_date"] == today)
        self.assertEqual(day["first_pass"], 3)
        self.assertEqual(day["rejected"], 1)
        self.assertEqual(day["missed"], 3)
        self.assertAlmostEqual(day["pass_rate"], 0.75)
        # 旧账不在窗口里。
        self.assertNotIn("旧账", [row["reason"] for row in trend["reasons"]])
        self.assertEqual(trend["totals"]["first_pass"], 3)
        self.assertEqual(trend["totals"]["rejected"], 1)

        # 按区：与按天**同一口径**（一次通过 /（一次通过 + 驳回）），逾期单独一列。
        by_zone = {row["zone_id"]: row for row in trend["zones"]}
        self.assertEqual(by_zone[anban]["first_pass"], 2)
        self.assertEqual(by_zone[anban]["rejected"], 1)
        self.assertEqual(by_zone[anban]["missed"], 3)
        self.assertAlmostEqual(by_zone[anban]["pass_rate"], 2 / 3)
        self.assertEqual(by_zone[xian]["pass_rate"], 1.0)
        # 驳回多的区排前面（店长先看问题区）。
        self.assertEqual(trend["zones"][0]["zone_id"], anban)

        # 一条事件都没有的窗口：不编数字（pass_rate 是 None，不是 0）。
        empty = await self.work.hygiene_trend(days=1)
        self.assertIsInstance(empty["daily"], list)

    async def test_later_submit_replaces_pending_capture(self):
        _anban, xian = await self._two_items()
        day = _staff(10, DAY_PHONE, "白班")
        first = await self.work.submit_daily(day, xian["id"], self._live(SHOT_A))
        second = await self.work.submit_daily(day, xian["id"], self._live(SHOT_B))
        self.assertEqual(second["status"], "待验收")
        self.assertNotEqual(second["capture_id"], first["capture_id"])
        review = await self.work.get_daily_review(xian["id"], "白班", actor=SUPER)
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
        review = await self.work.get_daily_review(anban["id"], "白班", actor=SUPER)
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
            await self.work.get_daily_review(anban["id"], "白班", actor=SUPER)
        self.assertEqual(raised.exception.code, "not_pending")
        again = await self.work.submit_daily(day, anban["id"], self._live(SHOT_B))
        self.assertEqual(again["status"], "待验收")
        self.assertEqual(self.captures.get(again["capture_id"]), SHOT_B)

    async def test_watermark_fields_are_injected_now_zone_and_photographer(self):
        _anban, xian = await self._two_items()
        day = _staff(10, DAY_PHONE, "白班", name="张三")
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
                "photographer": "张三",
            },
        )
        review = await self.work.get_daily_review(xian["id"], "白班", actor=SUPER)
        self.assertEqual(review["watermark"]["time"], "2026-09-13T10:00:00+08:00")
        self.assertEqual(review["watermark"]["zone"], "馅档")
        self.assertEqual(review["watermark"]["photographer"], "张三")

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
        self.assertIn(("馅档", "馅档台面", "白班", "待验收"), seen)
        self.assertFalse(any(row["shift"] == "夜班" for row in inbox))
        night = _staff(11, NIGHT_PHONE, "夜班")
        night_inbox = await self.work.list_daily_work(night)
        night_seen = {
            (row["zone_name"], row["item_name"], row["shift"], row["status"])
            for row in night_inbox
        }
        self.assertIn(("案板", "案板表面", "夜班", "待拍"), night_seen)
        self.assertIn(("馅档", "馅档台面", "夜班", "待拍"), night_seen)
        self.assertFalse(any(row["shift"] == "白班" for row in night_inbox))
        super_inbox = await self.work.list_daily_work(SUPER)
        super_seen = {
            (row["zone_name"], row["item_name"], row["shift"], row["status"])
            for row in super_inbox
        }
        self.assertIn(("案板", "案板表面", "白班", "待拍"), super_seen)
        self.assertIn(("案板", "案板表面", "夜班", "待拍"), super_seen)
        self.assertIn(("馅档", "馅档台面", "白班", "待验收"), super_seen)
        self.assertIn(("馅档", "馅档台面", "夜班", "待拍"), super_seen)


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

    async def test_overdue_sweep_skips_shift_a_zone_does_not_run(self):
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")
        await self.work.set_zone_shifts(SUPER, anban["id"], ["白班"])
        await self.work.add_daily_item(
            SUPER, anban["id"], "案板表面", self._capture(OLD_BYTES)
        )
        await self.work.set_daily_overdue_clocks(SUPER, "09:00", "09:30")

        notified = await self.work.sweep_overdue()
        shifts = {row["shift"] for row in notified if row.get("item_id")}
        self.assertEqual(shifts, {"白班"})
        self.assertEqual(len(self.notifier.texts), 1)
        self.assertIn("白班", self.notifier.texts[0])
        self.assertNotIn("夜班", self.notifier.texts[0])

    async def test_pre_six_clock_waits_until_next_calendar_morning(self):
        await self._anban_item()
        await self.work.set_daily_overdue_clocks(SUPER, "05:00", "21:30")
        self.fixed_now = datetime(2026, 9, 13, 6, 0, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.assertEqual(self.notifier.texts, [])
        self.fixed_now = datetime(2026, 9, 14, 4, 59, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.assertEqual(len(self.notifier.texts), 1)
        self.assertIn("夜班", self.notifier.texts[0])
        self.assertNotIn("白班", self.notifier.texts[0])
        self.fixed_now = datetime(2026, 9, 14, 5, 0, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.assertEqual(len(self.notifier.texts), 2)
        self.assertTrue(any("白班" in text for text in self.notifier.texts))

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

    async def test_missed_daily_increments_zone_board_not_the_person_on_duty(self):
        """漏交的日常记在**工作区**上，不记在当天当班的人头上。

        票 10 之前这里的前置是「员工自己选了白班」；自选入口撤了之后，同一个人改成
        **排班排到白班**（`assign_duty`，店长还没给他配工作区）—— 要守的仍然是同一件事：
        `sweep_overdue` 只往工作区看板写一条 `逾期`（`employee_id` 为空），不会因为今天
        有谁在班就把这次漏交算到谁名下（个人看板是提交/驳回时才动的）。
        """
        from services.hygiene.accounts import EmployeeAccounts

        accounts = EmployeeAccounts(self.db, now=lambda: self.fixed_now)
        on_duty = await accounts.register("13800138010", "password123", "张三")
        await accounts.approve(on_duty["id"])
        await assign_duty(self.db, on_duty["id"], slot="day", now=self.fixed_now)
        self.assertEqual(await accounts.current_shift(on_duty["id"]), "白班")
        item = await self._anban_item()
        await self.work.set_daily_overdue_clocks(SUPER, "15:00", "21:30")
        self.fixed_now = datetime(2026, 9, 13, 15, 0, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        zone_events = await self.work.list_zone_board_events(item["zone_id"])
        self.assertEqual(len(zone_events), 1)
        self.assertEqual(zone_events[0]["event_type"], "逾期")
        self.assertEqual(zone_events[0]["zone_id"], item["zone_id"])
        self.assertEqual(zone_events[0]["shift"], "白班")
        self.assertIsNone(zone_events[0]["employee_id"])
        person_events = await self.work.list_person_board_events(on_duty["id"])
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

    async def test_deep_clean_list_is_shop_wide_on_purpose(self):
        """专项清单对**所有**员工会话一视同仁（全店一起干，见 ADR 0054 / 0059 / 0075）。

        2026-10-05 审查 F-07 把"专项不按提交人过滤、同事能看到别人待验收的照片与姓名"
        标成「存疑，需产品确认」。核到的结论是**设计如此**：专项清单只有 `(weekday, name)`，
        没有区/组/人可判 —— 与日常那侧（按本区 + 本班次过滤）不对称是口径不同，不是漏判。

        这条用例是**特征测试**：将来有人要给专项补 `WHERE submitter_id = ?`，它会红 ——
        那时请先改产品口径（ADR）与页面文案，别在服务层悄悄加一片横向隔离。
        """
        first, _second = await self._two_sunday_items()
        submitter = _staff(10, DAY_PHONE, "白班")
        colleague = _staff(11, NIGHT_PHONE, "夜班")
        await self.work.submit_deep_clean_pair(
            submitter, first["id"], self._live(BEFORE_A), self._live(AFTER_A)
        )
        for actor in (submitter, colleague, _staff(12, ADMIN_PHONE, "白班", "管理员")):
            row = self._pair_row(await self.work.list_deep_clean_work(actor), first["id"])
            self.assertEqual(row["status"], "待验收")
            # 提交人姓名 + 照片 id 一起下发：这是"全店可见"的具体含义（字段名叫
            # submitter_phone，实际存的是姓名 —— 水印取的是同一个值）。
            self.assertEqual(row["submitter_id"], 10)
            self.assertIsNotNone(row["before_capture_id"])
            self.assertIsNotNone(row["after_capture_id"])

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

    async def test_pending_pairs_before_clock_are_not_overdue(self):
        first, second = await self._two_sunday_items()
        await self.work.set_deep_clean_overdue_clock(SUPER, "20:00")
        day = _staff(10, DAY_PHONE, "白班")
        await self.work.submit_deep_clean_pair(
            day, first["id"], self._live(BEFORE_A), self._live(AFTER_A)
        )
        await self.work.submit_deep_clean_pair(
            day, second["id"], self._live(BEFORE_B), self._live(AFTER_B)
        )
        self.fixed_now = datetime(2026, 9, 13, 20, 0, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.assertEqual(self.notifier.texts, [])
        calendar = await self.work.list_deep_clean_calendar("2026-09-13", "2026-09-13")
        self.assertEqual(calendar[0]["status"], "待办")


OPEN_BYTES = b"FIX-OPEN-JPEG"
RESHOOT_A = b"FIX-RESHOOT-A"
RESHOOT_B = b"FIX-RESHOOT-B"
TWO_HOURS = timedelta(hours=2)
OPEN_AT = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
DEADLINE_AT = datetime(2026, 9, 13, 12, 0, tzinfo=CHINA_TZ)
CIRCLES = [{"kind": "circle", "x": 0.4, "y": 0.3, "r": 0.08}]


class HygieneFixTicketTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = OPEN_AT
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

    async def _zone_id(self, name):
        zones = await self.work.list_zones()
        return next(zone["id"] for zone in zones if zone["name"] == name)

    def _live(self, data, markup=None):
        capture = {"bytes": data, "content_type": "image/jpeg", "live": True}
        if markup is not None:
            capture["markup"] = markup
        return capture

    async def _open_on(self, actor, zone_name="案板", ticket_type="卫生", body="案板有油，用热水擦干净"):
        zone_id = await self._zone_id(zone_name)
        return await self.work.open_fix(
            actor,
            zone_id,
            ticket_type,
            body,
            TWO_HOURS,
            self._live(OPEN_BYTES, CIRCLES),
        )

    async def test_staff_cannot_open_admin_can_super_needs_live_and_notifies_text(self):
        zone_id = await self._zone_id("案板")
        staff = _staff(10, DAY_PHONE, "白班")
        admin = _staff(20, ADMIN_PHONE, "白班", "管理员")
        with self.assertRaises(HygieneWorkError) as staff_open:
            await self.work.open_fix(
                staff,
                zone_id,
                "卫生",
                "案板有油，用热水擦干净",
                TWO_HOURS,
                self._live(OPEN_BYTES),
            )
        self.assertEqual(staff_open.exception.code, "forbidden")
        self.assertEqual(self.notifier.texts, [])

        opened = await self.work.open_fix(
            admin,
            zone_id,
            "卫生",
            "案板有油，用热水擦干净",
            TWO_HOURS,
            self._live(OPEN_BYTES, CIRCLES),
        )
        self.assertEqual(opened["status"], "待回拍")
        self.assertEqual(opened["ticket_type"], "卫生")
        self.assertEqual(opened["body_text"], "案板有油，用热水擦干净")
        self.assertEqual(opened["deadline"], "2026-09-13T12:00:00+08:00")
        self.assertEqual(opened["markup"], CIRCLES)
        self.assertEqual(self.captures.get(opened["capture_id"]), OPEN_BYTES)
        self.assertEqual(len(self.notifier.texts), 1)
        text = self.notifier.texts[0]
        self.assertIsInstance(text, str)
        self.assertIn("案板", text)
        self.assertIn("卫生", text)
        self.assertNotIn("image", text.lower())
        self.assertNotIn("FIX-OPEN-JPEG", text)
        self.assertNotIn("@", text)

        with self.assertRaises(HygieneWorkError) as no_camera:
            await self.work.open_fix(
                SUPER,
                zone_id,
                "摆放",
                "盘子叠歪了，重新摆齐",
                TWO_HOURS,
                {"bytes": OPEN_BYTES, "content_type": "image/jpeg", "live": False},
            )
        self.assertEqual(no_camera.exception.code, "live_required")
        self.assertEqual(len(self.notifier.texts), 1)

        super_opened = await self.work.open_fix(
            SUPER,
            zone_id,
            "摆放",
            "盘子叠歪了，重新摆齐",
            TWO_HOURS,
            self._live(OPEN_BYTES),
        )
        self.assertEqual(super_opened["status"], "待回拍")
        self.assertEqual(super_opened["opener_kind"], "super")
        self.assertEqual(
            opened["watermark"],
            {
                "time": "2026-09-13T10:00:00+08:00",
                "zone": "案板",
                "photographer": ADMIN_PHONE,
            },
        )
        self.assertEqual(
            super_opened["watermark"],
            {
                "time": "2026-09-13T10:00:00+08:00",
                "zone": "案板",
                "photographer": "超级管理员",
            },
        )
        self.assertEqual(len(self.notifier.texts), 2)
        self.assertNotIn("FIX-OPEN-JPEG", self.notifier.texts[1])
        self.assertNotIn("image", self.notifier.texts[1].lower())

    async def test_fix_opener_needs_permission_and_a_zone_not_a_shift(self):
        """开整改单要的是**档位 + 工作区**，不是"今天排到班"（2026-10-05 审查 F-05）。

        actor 按 `api/hygiene.py` 的 `_staff_actor` 那个形状造：**`zone_id` 键始终在**
        （今天没排到区时值是 None）。本文件其它用例用的 `_staff()` helper 不带这个键，
        而 `_actor_zone_id` 对"键都没有"和"键在但是空"是两种处理 —— 拿它测这一条会漏掉
        真实的 400。
        """
        anban = await self._zone_id("案板")
        xian = await self._zone_id("馅档")
        live = self._live(OPEN_BYTES)

        def actor(employee_id, permission, zone_id):
            # 2026-10-05 起判据看 `caps`（管理权限开关），`permission` 只是人话标签 ——
            # 这里按迁移 `0015` 的回填规则推：老「管理员」= daily_review + deep_review + fix。
            caps = (
                ["daily_review", "deep_review", "fix"] if permission == "管理员" else []
            )
            return {
                "kind": "staff",
                "id": employee_id,
                "permission": permission,
                "caps": caps,
                "name": "",
                "phone": ADMIN_PHONE,
                "shift": "白班" if zone_id else None,
                "zone_id": zone_id,
            }

        admin_with_zone = actor(20, "管理员", anban)
        admin_no_zone = actor(21, "管理员", None)
        body = "案板有油，用热水擦干净"

        # 普通员工：403 不变，而且在工作区判断**之前**就被挡下（两种形状都一样）。
        for worker in (actor(30, "普通员工", anban), actor(31, "普通员工", None)):
            with self.assertRaises(HygieneWorkError) as forbidden:
                await self.work.open_fix(worker, anban, "卫生", body, TWO_HOURS, live)
            self.assertEqual(forbidden.exception.code, "forbidden")
        self.assertEqual(self.notifier.texts, [])

        # 管理员 + 今天排到了区：能开，但只能开在自己那个区上。
        opened = await self.work.open_fix(
            admin_with_zone, anban, "卫生", body, TWO_HOURS, live
        )
        self.assertEqual(opened["status"], "待回拍")
        self.assertEqual(opened["opener_kind"], OPENER_STAFF)
        with self.assertRaises(HygieneWorkError) as mismatch:
            await self.work.open_fix(admin_with_zone, xian, "卫生", body, TWO_HOURS, live)
        self.assertEqual(mismatch.exception.code, "zone_mismatch")

        # 管理员 + 今天没排到区：拒的是"没有可开的区"（zone_required），不是"没有班"。
        with self.assertRaises(HygieneWorkError) as no_zone:
            await self.work.open_fix(admin_no_zone, anban, "卫生", body, TWO_HOURS, live)
        self.assertEqual(no_zone.exception.code, "zone_required")

        # 三次被拒都没建单：只有成功那一次落了库。
        tickets = await self.work.list_fix_tickets(admin_with_zone)
        self.assertEqual([ticket["id"] for ticket in tickets], [opened["id"]])
        # 也只有那一次发了群文字。
        self.assertEqual(len(self.notifier.texts), 1)
        self.assertIn("案板", self.notifier.texts[0])

    async def test_night_staff_can_reshoot_day_opened_ticket(self):
        opener = _staff(20, ADMIN_PHONE, "白班", "管理员")
        night = _staff(11, NIGHT_PHONE, "夜班")
        opened = await self._open_on(opener)
        listed = await self.work.list_fix_tickets(night)
        row = next(ticket for ticket in listed if ticket["id"] == opened["id"])
        self.assertEqual(row["markup"], CIRCLES)
        self.assertEqual(self.captures.get(row["capture_id"]), OPEN_BYTES)
        self.assertIsNone(row["reshoot_capture_id"])

        reshot = await self.work.reshoot_fix(night, opened["id"], self._live(RESHOOT_A))
        self.assertEqual(reshot["status"], "待验收")
        self.assertEqual(self.captures.get(reshot["reshoot_capture_id"]), RESHOOT_A)
        self.assertEqual(
            reshot["watermark"],
            {
                "time": "2026-09-13T10:00:00+08:00",
                "zone": "案板",
                "photographer": NIGHT_PHONE,
            },
        )
        self.assertEqual(
            reshot["open_watermark"],
            {
                "time": "2026-09-13T10:00:00+08:00",
                "zone": "案板",
                "photographer": ADMIN_PHONE,
            },
        )
        after = next(
            ticket
            for ticket in await self.work.list_fix_tickets(night)
            if ticket["id"] == opened["id"]
        )
        self.assertEqual(self.captures.get(after["capture_id"]), OPEN_BYTES)
        self.assertEqual(after["markup"], CIRCLES)
        self.assertEqual(self.captures.get(after["reshoot_capture_id"]), RESHOOT_A)

    async def test_other_admin_cannot_accept_before_deadline_can_after(self):
        opener = _staff(20, ADMIN_PHONE, "白班", "管理员")
        other = _staff(21, OTHER_ADMIN_PHONE, "白班", "管理员")
        regular = _staff(10, DAY_PHONE, "白班")
        opened = await self._open_on(opener)
        later = await self._open_on(
            opener, zone_name="馅档", ticket_type="标签", body="日期贴掉了，重贴"
        )
        await self.work.reshoot_fix(regular, opened["id"], self._live(RESHOOT_A))
        await self.work.reshoot_fix(regular, later["id"], self._live(RESHOOT_B))

        self.fixed_now = datetime(2026, 9, 13, 11, 59, tzinfo=CHINA_TZ)
        with self.assertRaises(HygieneWorkError) as too_soon:
            await self.work.accept_fix(other, opened["id"])
        self.assertEqual(too_soon.exception.code, "forbidden")
        with self.assertRaises(HygieneWorkError) as super_too_soon:
            await self.work.accept_fix(SUPER, opened["id"])
        self.assertEqual(super_too_soon.exception.code, "forbidden")
        passed_by_opener = await self.work.accept_fix(opener, opened["id"])
        self.assertEqual(passed_by_opener["status"], "已通过")

        self.fixed_now = DEADLINE_AT
        with self.assertRaises(HygieneWorkError) as staff_accept:
            await self.work.accept_fix(regular, later["id"])
        self.assertEqual(staff_accept.exception.code, "forbidden")
        passed = await self.work.accept_fix(other, later["id"])
        self.assertEqual(passed["status"], "已通过")

    async def test_disabled_opener_still_blocks_others_until_deadline(self):
        from services.hygiene.accounts import EmployeeAccounts

        accounts = EmployeeAccounts(self.db, now=lambda: self.fixed_now)
        registered = await accounts.register(ADMIN_PHONE, "password123", "李四")
        await accounts.approve(registered["id"])
        opener_row = await accounts.set_permission(registered["id"], "管理员")
        opener = _staff(opener_row["id"], ADMIN_PHONE, "白班", "管理员")
        other = _staff(21, OTHER_ADMIN_PHONE, "白班", "管理员")
        opened = await self._open_on(opener)
        await self.work.reshoot_fix(
            _staff(10, DAY_PHONE, "白班"), opened["id"], self._live(RESHOOT_A)
        )
        await accounts.disable(opener_row["id"])

        listed = await self.work.list_fix_tickets(other)
        still_open = next(ticket for ticket in listed if ticket["id"] == opened["id"])
        self.assertEqual(still_open["status"], "待验收")
        self.assertEqual(still_open["opener_id"], opener_row["id"])
        roster = await accounts.list_roster()
        self.assertEqual(len(roster), 1)
        self.assertTrue(roster[0]["disabled"])
        self.assertEqual(roster[0]["phone"], ADMIN_PHONE)

        self.fixed_now = datetime(2026, 9, 13, 11, 0, tzinfo=CHINA_TZ)
        with self.assertRaises(HygieneWorkError) as still_waiting:
            await self.work.accept_fix(other, opened["id"])
        self.assertEqual(still_waiting.exception.code, "forbidden")
        with self.assertRaises(HygieneWorkError) as super_waiting:
            await self.work.accept_fix(SUPER, opened["id"])
        self.assertEqual(super_waiting.exception.code, "forbidden")

        self.fixed_now = DEADLINE_AT
        passed = await self.work.accept_fix(other, opened["id"])
        self.assertEqual(passed["status"], "已通过")

    async def test_reject_restarts_same_two_hour_deadline_from_reject_now(self):
        opener = _staff(20, ADMIN_PHONE, "白班", "管理员")
        opened = await self._open_on(opener)
        self.assertEqual(opened["deadline"], "2026-09-13T12:00:00+08:00")
        await self.work.reshoot_fix(
            _staff(10, DAY_PHONE, "白班"), opened["id"], self._live(RESHOOT_A)
        )
        reject_at = datetime(2026, 9, 13, 11, 0, tzinfo=CHINA_TZ)
        self.fixed_now = reject_at
        rejected = await self.work.reject_fix(opener, opened["id"])
        self.assertEqual(rejected["status"], "待回拍")
        self.assertEqual(rejected["deadline"], "2026-09-13T13:00:00+08:00")
        listed = next(
            ticket
            for ticket in await self.work.list_fix_tickets(opener)
            if ticket["id"] == opened["id"]
        )
        self.assertEqual(listed["deadline"], "2026-09-13T13:00:00+08:00")
        self.assertIsNone(listed["reshoot_capture_id"])

    async def test_later_reshoot_replaces_pending_capture_bytes(self):
        opener = _staff(20, ADMIN_PHONE, "白班", "管理员")
        night = _staff(11, NIGHT_PHONE, "夜班")
        opened = await self._open_on(opener)
        first = await self.work.reshoot_fix(night, opened["id"], self._live(RESHOOT_A))
        second = await self.work.reshoot_fix(
            _staff(10, DAY_PHONE, "白班"), opened["id"], self._live(RESHOOT_B)
        )
        self.assertEqual(second["status"], "待验收")
        self.assertNotEqual(second["reshoot_capture_id"], first["reshoot_capture_id"])
        listed = next(
            ticket
            for ticket in await self.work.list_fix_tickets(night)
            if ticket["id"] == opened["id"]
        )
        self.assertEqual(self.captures.get(listed["reshoot_capture_id"]), RESHOOT_B)
        self.assertNotEqual(self.captures.get(listed["reshoot_capture_id"]), RESHOOT_A)
        self.assertEqual(self.captures.get(listed["capture_id"]), OPEN_BYTES)

    async def test_super_deletes_fix_ticket_with_captures_staff_admin_cannot(self):
        opener = _staff(20, ADMIN_PHONE, "白班", "管理员")
        opened = await self._open_on(opener)
        await self.work.reshoot_fix(
            _staff(10, DAY_PHONE, "白班"), opened["id"], self._live(RESHOOT_A)
        )
        listed = next(
            ticket
            for ticket in await self.work.list_fix_tickets(opener)
            if ticket["id"] == opened["id"]
        )
        capture_ids = [listed["capture_id"], listed["reshoot_capture_id"]]

        with self.assertRaises(HygieneWorkError) as forbidden:
            await self.work.delete_fix_ticket(opener, opened["id"])
        self.assertEqual(forbidden.exception.code, "forbidden")
        self.assertTrue(await self.captures.exists_async(capture_ids[0]))

        removed = await self.work.delete_fix_ticket(SUPER, opened["id"])
        self.assertEqual(removed["id"], opened["id"])
        self.assertNotIn(
            opened["id"],
            [ticket["id"] for ticket in await self.work.list_fix_tickets(SUPER)],
        )
        with self.assertRaises(HygieneWorkError) as missing:
            await self.work.get_fix_ticket(opened["id"])
        self.assertEqual(missing.exception.code, "ticket_not_found")
        for capture_id in capture_ids:
            self.assertFalse(await self.captures.exists_async(capture_id))

    async def test_sweep_no_reshoot_reddens_zone_only_reshoot_counts_photographer(self):
        opener = _staff(20, ADMIN_PHONE, "白班", "管理员")
        night = _staff(11, NIGHT_PHONE, "夜班")
        missed = await self._open_on(opener, zone_name="案板")
        shot = await self._open_on(opener, zone_name="馅档", ticket_type="摆放", body="托盘乱，收整齐")
        await self.work.reshoot_fix(night, shot["id"], self._live(RESHOOT_A))
        await self.work.reshoot_fix(
            _staff(10, DAY_PHONE, "白班"), shot["id"], self._live(RESHOOT_B)
        )
        open_texts = list(self.notifier.texts)

        self.fixed_now = datetime(2026, 9, 13, 11, 59, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.assertEqual(self.notifier.texts, open_texts)

        self.fixed_now = DEADLINE_AT
        await self.work.sweep_overdue()
        self.assertEqual(len(self.notifier.texts), len(open_texts) + 2)
        for text in self.notifier.texts[len(open_texts) :]:
            self.assertIsInstance(text, str)
            self.assertNotIn("image", text.lower())
            self.assertNotIn("FIX-OPEN-JPEG", text)
            self.assertNotIn("@", text)

        await self.work.sweep_overdue()
        self.assertEqual(len(self.notifier.texts), len(open_texts) + 2)

        anban_zone = [
            event
            for event in await self.work.list_zone_board_events(missed["zone_id"])
            if event["event_type"] == "逾期"
        ]
        xian_zone = [
            event
            for event in await self.work.list_zone_board_events(shot["zone_id"])
            if event["event_type"] == "逾期"
        ]
        self.assertEqual(len(anban_zone), 1)
        self.assertIsNone(anban_zone[0]["employee_id"])
        self.assertEqual(len(xian_zone), 1)

        self.assertEqual(await self.work.list_person_board_events(opener["id"]), [])
        self.assertEqual(await self.work.list_person_board_events(night["id"]), [])
        last_shooter = await self.work.list_person_board_events(10)
        self.assertEqual([event["event_type"] for event in last_shooter], ["逾期"])
        self.assertEqual(last_shooter[0]["employee_id"], 10)


class HygieneWeekStartTest(unittest.TestCase):
    def test_monday_0600_opens_new_week_0559_stays_previous(self):
        self.assertEqual(
            hygiene_week_start(datetime(2026, 9, 14, 5, 59, tzinfo=CHINA_TZ)),
            datetime(2026, 9, 7, 6, 0, tzinfo=CHINA_TZ),
        )
        self.assertEqual(
            hygiene_week_start(datetime(2026, 9, 14, 6, 0, tzinfo=CHINA_TZ)),
            datetime(2026, 9, 14, 6, 0, tzinfo=CHINA_TZ),
        )
        self.assertEqual(
            hygiene_week_start(datetime(2026, 9, 13, 5, 59, tzinfo=CHINA_TZ)),
            datetime(2026, 9, 7, 6, 0, tzinfo=CHINA_TZ),
        )
        self.assertEqual(
            hygiene_week_start(datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)),
            datetime(2026, 9, 7, 6, 0, tzinfo=CHINA_TZ),
        )


def _no_score_keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            text = str(key)
            if "score" in text.lower() or "points" in text.lower() or "分" in text:
                raise AssertionError(f"score-like key {key!r}")
            _no_score_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            _no_score_keys(item)


def _person_row(boards, employee_id):
    return next(row for row in boards["people"] if row["employee_id"] == employee_id)


def _zone_row(boards, zone_id):
    return next(row for row in boards["zones"] if row["zone_id"] == zone_id)


class HygieneBoardsAndTeachingTest(unittest.IsolatedAsyncioTestCase):
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

    async def _item(self, zone_name, item_name, photo=OLD_BYTES):
        zones = await self.work.list_zones()
        return await self.work.add_daily_item(
            SUPER, self._zone(zones, zone_name)["id"], item_name, self._capture(photo)
        )

    async def test_events_at_monday_0559_and_0600_land_in_different_weeks(self):
        anban = await self._item("案板", "案板表面")
        xian = await self._item("馅档", "馅档台面", ZONE_A_BYTES)
        day = _staff(10, DAY_PHONE, "白班")

        self.fixed_now = datetime(2026, 9, 14, 5, 59, tzinfo=CHINA_TZ)
        await self.work.submit_daily(day, anban["id"], self._live(SHOT_A))
        before_cut = await self.work.list_boards(self.fixed_now)
        self.assertEqual(before_cut["week_start"], "2026-09-07T06:00:00+08:00")
        self.assertEqual(_person_row(before_cut, 10)["实拍"], 1)
        _no_score_keys(before_cut)

        self.fixed_now = datetime(2026, 9, 14, 6, 0, tzinfo=CHINA_TZ)
        await self.work.submit_daily(day, xian["id"], self._live(SHOT_B))
        after_cut = await self.work.list_boards(self.fixed_now)
        self.assertEqual(after_cut["week_start"], "2026-09-14T06:00:00+08:00")
        self.assertEqual(_person_row(after_cut, 10)["实拍"], 1)
        prior = await self.work.list_boards(datetime(2026, 9, 14, 5, 59, tzinfo=CHINA_TZ))
        self.assertEqual(prior["week_start"], "2026-09-07T06:00:00+08:00")
        self.assertEqual(_person_row(prior, 10)["实拍"], 1)
        self.assertEqual(_person_row(prior, 10)["驳回"], 0)
        _no_score_keys(after_cut)
        _no_score_keys(prior)

    async def test_miss_kinds_split_across_zone_person_and_calendar(self):
        missed_daily = await self._item("馅档", "馅档台面", ZONE_A_BYTES)
        first_pass = await self._item("熟笼", "蒸笼内壁")
        shot = await self._item("案板", "案板表面")
        day = _staff(10, DAY_PHONE, "白班")
        reviewer = _staff(21, OTHER_ADMIN_PHONE, "白班", "管理员")
        opener = _staff(20, ADMIN_PHONE, "白班", "管理员")
        await self.work.set_daily_overdue_clocks(SUPER, "15:00", "21:30")
        await self.work.set_deep_clean_overdue_clock(SUPER, "20:00")
        await self.work.add_deep_clean_item(SUPER, 6, FRIDGE_A)
        await self.work.add_deep_clean_item(SUPER, 6, FRIDGE_B)

        await self.work.submit_daily(day, shot["id"], self._live(SHOT_A))
        await self.work.reject_daily(reviewer, shot["id"], "白班")
        await self.work.submit_daily(day, shot["id"], self._live(SHOT_A))
        await self.work.submit_daily(day, first_pass["id"], self._live(SHOT_B))
        await self.work.accept_daily(reviewer, first_pass["id"], "白班")

        zones = await self.work.list_zones()
        xipi_id = self._zone(zones, "西饼")["id"]
        ming_id = self._zone(zones, "明档1")["id"]
        reshot = await self.work.open_fix(
            opener,
            xipi_id,
            "摆放",
            "西饼乱，收整齐",
            TWO_HOURS,
            self._live(OPEN_BYTES),
        )
        await self.work.reshoot_fix(day, reshot["id"], self._live(RESHOOT_A))
        await self.work.open_fix(
            opener,
            ming_id,
            "标签",
            "日期贴掉了，重贴",
            TWO_HOURS,
            self._live(OPEN_BYTES),
        )

        self.fixed_now = datetime(2026, 9, 13, 12, 0, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.fixed_now = datetime(2026, 9, 13, 15, 0, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()
        self.fixed_now = datetime(2026, 9, 13, 20, 0, tzinfo=CHINA_TZ)
        await self.work.sweep_overdue()

        boards = await self.work.list_boards()
        _no_score_keys(boards)
        person = _person_row(boards, 10)
        self.assertEqual(person["实拍"], 3)
        self.assertEqual(person["驳回"], 1)
        self.assertEqual(person["一次通过"], 1)
        self.assertEqual(person["逾期"], 1)
        self.assertFalse(any(row["employee_id"] == 20 for row in boards["people"]))
        self.assertEqual(_zone_row(boards, missed_daily["zone_id"])["逾期"], 1)
        self.assertEqual(_zone_row(boards, ming_id)["逾期"], 1)
        self.assertEqual(_zone_row(boards, xipi_id)["逾期"], 1)
        self.assertFalse(any(row.get("employee_id") for row in boards["zones"]))
        calendar = await self.work.list_deep_clean_calendar("2026-09-13", "2026-09-13")
        self.assertEqual(calendar[0]["status"], "未完成")
        self.assertEqual(len(calendar), 1)
        self.assertNotIn("考核分", str(boards))
        self.assertNotIn("score", str(boards).lower())
        self.assertNotIn("points", str(boards).lower())

    async def test_accept_does_not_auto_fill_teaching_super_marks_then_list(self):
        item = await self._item("案板", "案板表面")
        day = _staff(10, DAY_PHONE, "白班")
        reviewer = _staff(21, OTHER_ADMIN_PHONE, "白班", "管理员")
        await self.work.submit_daily(day, item["id"], self._live(SHOT_A))
        with self.assertRaises(HygieneWorkError) as pending:
            await self.work.mark_teaching(
                SUPER, {"kind": "daily", "item_id": item["id"], "shift": "白班"}
            )
        self.assertEqual(pending.exception.code, "not_passed")
        await self.work.accept_daily(reviewer, item["id"], "白班")
        self.assertEqual(await self.work.list_teaching(), [])
        with self.assertRaises(HygieneWorkError) as staff_mark:
            await self.work.mark_teaching(
                day, {"kind": "daily", "item_id": item["id"], "shift": "白班"}
            )
        self.assertEqual(staff_mark.exception.code, "forbidden")
        with self.assertRaises(HygieneWorkError) as admin_mark:
            await self.work.mark_teaching(
                reviewer, {"kind": "daily", "item_id": item["id"], "shift": "白班"}
            )
        self.assertEqual(admin_mark.exception.code, "forbidden")
        marked = await self.work.mark_teaching(
            SUPER, {"kind": "daily", "item_id": item["id"], "shift": "白班"}
        )
        self.assertEqual(marked["kind"], "daily")
        self.assertEqual(marked["title"], "案板 · 案板表面")
        self.assertEqual(marked["left_label"], "标准图")
        self.assertEqual(marked["right_label"], "实拍")
        listed = await self.work.list_teaching()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], marked["id"])
        self.assertEqual(self.captures.get(listed[0]["left_capture_id"]), OLD_BYTES)
        self.assertEqual(self.captures.get(listed[0]["right_capture_id"]), SHOT_A)
        opened = await self.work.get_teaching(listed[0]["id"])
        self.assertEqual(opened["id"], marked["id"])
        _no_score_keys(marked)
        _no_score_keys(listed)
        self.assertNotIn("考核分", str(listed))

        fridge = await self.work.add_deep_clean_item(SUPER, 6, FRIDGE_A)
        await self.work.submit_deep_clean_pair(
            day, fridge["id"], self._live(BEFORE_A), self._live(AFTER_A)
        )
        await self.work.accept_deep_clean_pair(reviewer, fridge["id"])
        self.assertEqual(len(await self.work.list_teaching()), 1)
        deep = await self.work.mark_teaching(
            SUPER, {"kind": "deep_clean", "item_id": fridge["id"]}
        )
        self.assertEqual(deep["kind"], "deep_clean")
        self.assertEqual(deep["title"], FRIDGE_A)
        self.assertEqual(deep["left_label"], "清理前")
        self.assertEqual(deep["right_label"], "清理后")
        self.assertEqual(len(await self.work.list_teaching()), 2)
        self.assertEqual(self.captures.get(deep["left_capture_id"]), BEFORE_A)
        self.assertEqual(self.captures.get(deep["right_capture_id"]), AFTER_A)
        _no_score_keys(deep)


class HygieneDeleteDropsWorkTest(unittest.IsolatedAsyncioTestCase):
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
            notifier=FakeNotifier(),
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

    async def test_delete_zone_drops_pending_daily_and_open_fix_other_zone_stays(self):
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")
        xian = self._zone(zones, "馅档")
        anban_item = await self.work.add_daily_item(
            SUPER, anban["id"], "案板表面", self._capture(OLD_BYTES)
        )
        xian_item = await self.work.add_daily_item(
            SUPER, xian["id"], "馅档台面", self._capture(ZONE_A_BYTES)
        )
        day = _staff(10, DAY_PHONE, "白班")
        opener = _staff(20, ADMIN_PHONE, "白班", "管理员")
        await self.work.submit_daily(day, anban_item["id"], self._live(SHOT_A))
        await self.work.submit_daily(day, xian_item["id"], self._live(SHOT_B))
        anban_fix = await self.work.open_fix(
            opener,
            anban["id"],
            "卫生",
            "案板有油，用热水擦干净",
            TWO_HOURS,
            self._live(OPEN_BYTES),
        )
        xian_fix = await self.work.open_fix(
            opener,
            xian["id"],
            "摆放",
            "托盘乱，收整齐",
            TWO_HOURS,
            self._live(OPEN_BYTES),
        )

        deleted = await self.work.delete_zone(SUPER, anban["id"])
        self.assertEqual(deleted["name"], "案板")

        names = [zone["name"] for zone in await self.work.list_zones()]
        self.assertNotIn("案板", names)
        self.assertIn("馅档", names)
        catalog = await self.work.list_staff_daily_items()
        self.assertNotIn("案板", [zone["name"] for zone in catalog])
        xian_catalog = self._zone(catalog, "馅档")["items"]
        self.assertEqual([item["name"] for item in xian_catalog], ["馅档台面"])

        inbox = await self.work.list_daily_work(day)
        self.assertFalse(any(row["zone_name"] == "案板" for row in inbox))
        self.assertFalse(any(row["item_id"] == anban_item["id"] for row in inbox))
        remaining = next(
            row
            for row in inbox
            if row["item_id"] == xian_item["id"] and row["shift"] == "白班"
        )
        self.assertEqual(remaining["status"], "待验收")
        self.assertEqual(remaining["zone_name"], "馅档")

        tickets = await self.work.list_fix_tickets(day)
        self.assertFalse(any(ticket["id"] == anban_fix["id"] for ticket in tickets))
        self.assertFalse(any(ticket["zone_name"] == "案板" for ticket in tickets))
        kept = next(ticket for ticket in tickets if ticket["id"] == xian_fix["id"])
        self.assertEqual(kept["zone_name"], "馅档")
        self.assertEqual(kept["status"], "待回拍")

    async def test_delete_daily_item_drops_only_that_item_pending_work(self):
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")
        surface = await self.work.add_daily_item(
            SUPER, anban["id"], "案板表面", self._capture(OLD_BYTES)
        )
        edge = await self.work.add_daily_item(
            SUPER, anban["id"], "案板边缝", self._capture(ZONE_A_BYTES)
        )
        opener = _staff(20, ADMIN_PHONE, "白班", "管理员")
        zone_fix = await self.work.open_fix(
            opener,
            anban["id"],
            "卫生",
            "案板有油，用热水擦干净",
            TWO_HOURS,
            self._live(OPEN_BYTES),
        )
        day = _staff(10, DAY_PHONE, "白班")
        await self.work.submit_daily(day, surface["id"], self._live(SHOT_A))
        await self.work.submit_daily(day, edge["id"], self._live(SHOT_B))

        deleted = await self.work.delete_daily_item(SUPER, surface["id"])
        self.assertEqual(deleted["name"], "案板表面")

        catalog = self._zone(await self.work.list_staff_daily_items(), "案板")["items"]
        self.assertEqual([item["name"] for item in catalog], ["案板边缝"])
        inbox = await self.work.list_daily_work(day)
        self.assertFalse(any(row["item_id"] == surface["id"] for row in inbox))
        kept = next(
            row for row in inbox if row["item_id"] == edge["id"] and row["shift"] == "白班"
        )
        self.assertEqual(kept["status"], "待验收")
        self.assertEqual(kept["item_name"], "案板边缝")
        tickets = await self.work.list_fix_tickets(day)
        self.assertEqual([ticket["id"] for ticket in tickets], [zone_fix["id"]])
        self.assertEqual(tickets[0]["zone_name"], "案板")
        self.assertEqual(tickets[0]["status"], "待回拍")

    async def test_staff_cannot_delete_zone_or_daily_item(self):
        zones = await self.work.list_zones()
        anban = self._zone(zones, "案板")
        item = await self.work.add_daily_item(
            SUPER, anban["id"], "案板表面", self._capture(OLD_BYTES)
        )
        for actor in (STAFF, STAFF_ADMIN):
            with self.assertRaises(HygieneWorkError) as zone_err:
                await self.work.delete_zone(actor, anban["id"])
            self.assertEqual(zone_err.exception.code, "forbidden")
            with self.assertRaises(HygieneWorkError) as item_err:
                await self.work.delete_daily_item(actor, item["id"])
            self.assertEqual(item_err.exception.code, "forbidden")
        names = [zone["name"] for zone in await self.work.list_zones()]
        self.assertIn("案板", names)
        catalog = self._zone(await self.work.list_staff_daily_items(), "案板")["items"]
        self.assertEqual([row["name"] for row in catalog], ["案板表面"])
