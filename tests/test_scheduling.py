#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班服务层（票 02）：配固定班次 → 展开 90 天 → 月历数得出来。

主缝 = 服务层 + 真库 + 注入时钟（见 `.scratch/scheduling/spec.md` 的「测试缝」）：
排班的窗口、相位、过去不改都由**营业日**决定，所以时钟必须能拨。
"""

import ast
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from config import settings
from database import CHINA_TZ, DatabaseManager
from db_core.schema import ADMIN_READ_ONLY_TABLES, SCHEDULING_TABLES
from services.hygiene.accounts import EmployeeAccounts
from services.scheduling.store import (
    EXPANSION_DAYS,
    SchedulingError,
    SchedulingStore,
)

PASSWORD = "password123"
REPO_ROOT = Path(__file__).resolve().parents[1]

# 2026-09-24 是周四，10:00 已经过了 06:00 的切日点 → 营业日就是 9/24。
TODAY = "2026-09-24"
LAST_DAY = "2026-12-22"  # 今天 + 89 天


class SchedulingStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
        self.accounts = EmployeeAccounts(self.db, now=lambda: self.fixed_now)
        self.store = SchedulingStore(self.db, now=lambda: self.fixed_now)
        # 迁移里那两条默认班次在每个用例前被 TRUNCATE 掉了（见 conftest），
        # 所以这里走一次启动期的那段维护。
        await self.store.prepare()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _employee(self, phone="13800138000", name="张三"):
        employee = await self.accounts.register(phone, PASSWORD, name)
        await self.accounts.approve(employee["id"])
        return employee

    async def _shift_id(self, name):
        for shift in await self.store.list_shifts():
            if shift["name"] == name:
                return shift["id"]
        raise AssertionError(f"没有班次 {name}")

    async def _rows(self, employee_id):
        cur = await self.db._conn.execute(
            """SELECT business_date, shift_id, source FROM staff_assignments
               WHERE employee_id = ? ORDER BY business_date ASC""",
            (employee_id,),
        )
        return [dict(row) for row in await cur.fetchall()]

    async def _zone(self, name="案板"):
        """建一个责任区。表是卫生那条线建的（`hygiene_zones`），这里只当数据用 ——
        排班读它要走公共层的 `ZoneDirectory`，测试里也不假装是排班自己的表。"""
        now = self.fixed_now.isoformat()
        cur = await self.db._conn.execute(
            """INSERT INTO hygiene_zones (name, day_shift, night_shift, created_at, updated_at)
               VALUES (?, 1, 1, ?, ?)""",
            (name, now, now),
        )
        await self.db._conn.commit()
        return int(cur.lastrowid)

    async def _zone_ids(self, employee_id):
        cur = await self.db._conn.execute(
            "SELECT business_date, zone_id FROM staff_assignments WHERE employee_id = ?",
            (employee_id,),
        )
        return {dict(row)["business_date"]: dict(row)["zone_id"] for row in await cur.fetchall()}

    async def _full_rows(self, employee_id):
        """整行快照（`business_date → 全字段`）：用来证明「过去一个字都没动」。

        `id` 也在里面：删掉再插一行、字段看着一样时，换过行这件事只有它记得
        （时钟是注入的固定值，`created_at`/`updated_at` 分不出来）。
        """
        cur = await self.db._conn.execute(
            """SELECT id, business_date, shift_id, zone_id, source, created_at, updated_at
               FROM staff_assignments WHERE employee_id = ?""",
            (employee_id,),
        )
        return {dict(row)["business_date"]: dict(row) for row in await cur.fetchall()}

    # ── 验收 1：配「固定白班」，从今天起每天都算进白班 ──────────────────

    async def test_fixed_day_shift_covers_every_day_from_today(self):
        employee = await self._employee()
        day = await self._shift_id("白班")

        await self.store.set_rule(employee["id"], [day])

        rows = await self._rows(employee["id"])
        self.assertEqual(len(rows), EXPANSION_DAYS)
        self.assertEqual(rows[0]["business_date"], TODAY)
        self.assertEqual(rows[-1]["business_date"], LAST_DAY)
        self.assertEqual({row["shift_id"] for row in rows}, {day})
        self.assertEqual({row["source"] for row in rows}, {"rule"})

    async def test_cycle_phase_follows_anchor_date(self):
        """周期的相位由起点算：「白夜」两天一轮、起点是昨天 → 今天上夜班。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")

        await self.store.set_rule(employee["id"], [day, night], anchor_date="2026-09-23")

        rows = await self._rows(employee["id"])
        self.assertEqual(rows[0]["shift_id"], night)  # 9/24
        self.assertEqual(rows[1]["shift_id"], day)  # 9/25

    async def test_rest_day_is_written_as_no_shift(self):
        """周期里的「休」就是那天不排班：行还在（那天这个人归排班管），班次为空。"""
        employee = await self._employee()
        day = await self._shift_id("白班")

        await self.store.set_rule(employee["id"], [day, None])

        rows = await self._rows(employee["id"])
        self.assertEqual(rows[0]["shift_id"], day)
        self.assertIsNone(rows[1]["shift_id"])

        detail = await self.store.day_detail("2026-09-25")
        self.assertEqual(detail["total"], 0)
        self.assertEqual(detail["off_count"], 1)

    # ── 票 04：轮转周期「白白白夜夜休休」────────────────────────────────

    async def test_seven_day_cycle_wraps_on_the_eighth_day(self):
        """第 8 天回到周期第 1 格；再往后每一轮都对得齐（错位最容易在这儿露出来）。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        cycle = [day, day, day, night, night, None, None]

        await self.store.set_rule(employee["id"], cycle)

        rows = await self._rows(employee["id"])
        self.assertEqual([row["shift_id"] for row in rows[:7]], cycle)
        self.assertEqual(rows[7]["shift_id"], day)  # 第 8 天 = 周期第 1 格
        self.assertEqual([row["shift_id"] for row in rows[7:14]], cycle)

    async def test_rest_day_disappears_from_every_shift_count(self):
        """周期里的「休」：那天不进任何班次的人数，只进 `off_count`。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")

        await self.store.set_rule(employee["id"], [day, None, night])

        calendar = await self.store.month_calendar("2026-09")
        days = {item["business_date"]: item for item in calendar["days"]}
        self.assertEqual(days["2026-09-25"]["counts"].get(str(day), 0), 0)
        self.assertEqual(days["2026-09-25"]["counts"].get(str(night), 0), 0)
        self.assertEqual(days["2026-09-26"]["counts"][str(night)], 1)

        detail = await self.store.day_detail("2026-09-25")
        self.assertEqual(detail["total"], 0)
        self.assertEqual(detail["off_count"], 1)
        self.assertEqual([group for group in detail["groups"] if group["count"]], [])

    async def test_cycle_of_one_is_the_same_rule_as_the_fixed_shift(self):
        """长度 1 的周期就是「固定某个班」：老写法（`cycle: [shift]`）不用改数据。"""
        employee = await self._employee()
        day = await self._shift_id("白班")

        await self.store.set_rule(employee["id"], [day])

        rows = await self._rows(employee["id"])
        self.assertEqual(len(rows), EXPANSION_DAYS)
        self.assertEqual({row["shift_id"] for row in rows}, {day})
        rules = await self.store.list_rules()
        self.assertEqual(rules[employee["id"]]["cycle"], [day])
        self.assertEqual(rules[employee["id"]]["anchor_date"], TODAY)  # 不传起点 = 今天

    async def test_dirty_anchor_date_is_an_input_error_not_a_crash(self):
        """人工 SQL 把起点写成垃圾：报一条输入错误，而不是让整个月历 500。

        月历/当天两个接口都是先 `expand()` 再读（`api/scheduling.py:89,104`），所以
        这里照着调 `expand()`。
        """
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])
        await self.db._conn.execute(
            "UPDATE scheduling_rules SET anchor_date = ? WHERE employee_id = ?",
            ("昨天", employee["id"]),
        )
        await self.db._conn.commit()

        with self.assertRaises(SchedulingError) as caught:
            await self.store.expand()
        self.assertEqual(caught.exception.code, "invalid_anchor")

    async def test_unknown_shift_in_a_cycle_says_which_slot(self):
        """周期里引用了不存在的班次：错误要说清是第几格（规则页照着这句话报错）。"""
        employee = await self._employee()
        day = await self._shift_id("白班")

        with self.assertRaises(SchedulingError) as caught:
            await self.store.set_rule(employee["id"], [day, 999999, day])

        self.assertEqual(caught.exception.code, "unknown_shift_in_cycle")
        self.assertEqual(caught.exception.args[0], "2")
        self.assertEqual(await self._rows(employee["id"]), [])  # 半个周期都不落库

    # ── 验收 2：月历每格显示当天白班、夜班各几人 ────────────────────────

    async def test_month_calendar_counts_per_shift(self):
        first = await self._employee("13800138000", "张三")
        second = await self._employee("13800138001", "李四")
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(first["id"], [day])
        await self.store.set_rule(second["id"], [night])

        calendar = await self.store.month_calendar("2026-09")

        self.assertEqual(calendar["month"], "2026-09")
        self.assertEqual(calendar["first_date"], "2026-09-01")
        self.assertEqual(calendar["lead"], 2)  # 2026-09-01 是周二
        self.assertEqual(len(calendar["days"]), 30)
        self.assertEqual(calendar["today"], TODAY)
        self.assertEqual(calendar["window_end"], LAST_DAY)
        by_date = {day_["business_date"]: day_ for day_ in calendar["days"]}
        today_cell = by_date[TODAY]
        self.assertTrue(today_cell["is_today"])
        self.assertEqual(today_cell["counts"][str(day)], 1)
        self.assertEqual(today_cell["counts"][str(night)], 1)
        self.assertEqual(today_cell["total"], 2)
        # 排班从今天开始，昨天那格没人。
        self.assertEqual(by_date["2026-09-23"]["total"], 0)

    async def test_day_detail_lists_names_per_shift(self):
        first = await self._employee("13800138000", "张三")
        day = await self._shift_id("白班")
        await self.store.set_rule(first["id"], [day])

        detail = await self.store.day_detail(TODAY)

        self.assertEqual(detail["total"], 1)
        group = [item for item in detail["groups"] if item["shift"]["name"] == "白班"][0]
        self.assertEqual([person["name"] for person in group["people"]], ["张三"])
        # 没配区的人照样出现在名单里，只是区是空的（票 03 的验收项）。
        self.assertIsNone(group["people"][0]["zone"])

    # ── 票 05：员工自己的「今天」 ────────────────────────────────────────

    async def test_my_days_carries_today_shift_and_zone(self):
        """验收 2/3/5：员工看到的那一行跟店长配的是同一件事，日期是营业日。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        zone = await self._zone("案板")
        await self.store.set_zone_default(employee["id"], day, zone)
        await self.store.set_rule(employee["id"], [day])

        mine = await self.store.my_days(employee["id"])

        self.assertEqual(mine["today"], TODAY)
        self.assertEqual(
            [item["business_date"] for item in mine["days"]],
            ["2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27"],
        )
        self.assertEqual(
            [
                (item["is_today"], item["scheduled"], item["shift_name"], item["zone_name"])
                for item in mine["days"]
            ],
            [(True, True, "白班", "案板")] + [(False, True, "白班", "案板")] * 3,
        )

    async def test_my_days_distinguishes_a_rest_day_from_no_roster(self):
        """休和「还没排」不是一回事：休那天有行（`scheduled`）但没有班次。"""
        employee = await self._employee()
        idle = await self._employee("13800138001", "李四")
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day, None])

        days = (await self.store.my_days(employee["id"]))["days"]
        self.assertEqual([item["scheduled"] for item in days], [True] * 4)
        self.assertEqual(
            [item["shift_name"] for item in days], ["白班", None, "白班", None]
        )

        # 一条规则都没配的人：四天都在，只是都还没排 —— 不是报错，也不是空列表。
        empty = (await self.store.my_days(idle["id"]))["days"]
        self.assertEqual([item["scheduled"] for item in empty], [False] * 4)
        self.assertEqual([item["shift_name"] for item in empty], [None] * 4)
        self.assertEqual([item["business_date"] for item in empty], [
            "2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27",
        ])

    async def test_my_days_fills_the_window_for_this_person_only(self):
        """员工页自己会补齐：店长配完规则、月历还没人打开过，这一眼也得是对的。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])
        await self.db._conn.execute(
            "DELETE FROM staff_assignments WHERE employee_id = ?", (employee["id"],)
        )
        await self.db._conn.commit()
        self.assertEqual(await self._rows(employee["id"]), [])

        days = (await self.store.my_days(employee["id"]))["days"]

        self.assertEqual(days[0]["shift_name"], "白班")
        # 只铺这一人：窗口 90 天，不是「今天 + 4 天」那几行。
        self.assertEqual(len(await self._rows(employee["id"])), EXPANSION_DAYS)

    async def test_my_days_reads_back_a_changed_zone_default(self):
        """验收 2 的另一半：店长换了默认责任区，员工这一眼跟着变（不是只在月历里变）。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        board = await self._zone("案板")
        steamer = await self._zone("蒸柜")
        await self.store.set_zone_default(employee["id"], day, board)
        await self.store.set_rule(employee["id"], [day])
        self.assertEqual(
            (await self.store.my_days(employee["id"]))["days"][0]["zone_name"], "案板"
        )

        await self.store.set_zone_default(employee["id"], day, steamer)

        self.assertEqual(
            (await self.store.my_days(employee["id"]))["days"][0]["zone_name"], "蒸柜"
        )

    async def test_my_days_gives_no_name_when_the_shift_row_is_gone(self):
        """班次/责任区被硬删（票 11 与卫生那边管这两张表）不该冒 500：
        行还在、名字取不到就给 None，页面自己翻成「班次已调整」——不许假装那天是休。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        zone = await self._zone("案板")
        await self.store.set_zone_default(employee["id"], day, zone)
        await self.store.set_rule(employee["id"], [day])
        await self.store.my_days(employee["id"])  # 先把行铺出来

        await self.db._conn.execute("DELETE FROM hygiene_zones WHERE id = ?", (zone,))
        await self.db._conn.commit()
        no_zone = (await self.store.my_days(employee["id"]))["days"][0]
        self.assertTrue(no_zone["scheduled"])
        self.assertEqual(no_zone["shift_name"], "白班")
        self.assertIsNone(no_zone["zone_name"])

        await self.db._conn.execute("DELETE FROM staff_shifts WHERE id = ?", (day,))
        await self.db._conn.commit()
        no_shift = (await self.store.my_days(employee["id"]))["days"][0]
        self.assertTrue(no_shift["scheduled"])
        self.assertIsNone(no_shift["shift_name"])

    # ── 票 06：员工自己的整月 ──────────────────────────────────────────

    async def test_my_month_covers_every_day_of_that_month(self):
        """验收 1/2/5：一个月一天一格，休的那天一眼能分辨（`scheduled` 真、班名空）。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [day, night, None])

        month = await self.store.my_month(employee["id"], "2026-09")

        self.assertEqual(month["month"], "2026-09")
        self.assertEqual(month["today"], TODAY)
        self.assertEqual([item["day"] for item in month["days"]], list(range(1, 31)))
        self.assertEqual(month["days"][0]["business_date"], "2026-09-01")
        # 9/1 是周二：表头周日开头，第一格前空两格。
        self.assertEqual(month["lead"], 2)
        # 相位：配规则那天（营业日 9/24）是 cycle[0]（白班），9/25 夜班，9/26 休……
        today_index = [item["day"] for item in month["days"]].index(24)
        self.assertEqual(
            [(item["scheduled"], item["shift_name"]) for item in month["days"][today_index : today_index + 4]],
            [(True, "白班"), (True, "夜班"), (True, None), (True, "白班")],
        )
        today_row = [item for item in month["days"] if item["is_today"]]
        self.assertEqual(len(today_row), 1)
        self.assertEqual(today_row[0]["business_date"], TODAY)
        # 本月已经过去的日子：这一台是新装机（规则刚配），过去那些天没有结果行 ——
        # 展开只往今天以后铺，不回头补（见 `test_my_month_past_days_show_what_was_written`）。
        self.assertEqual(
            [item["scheduled"] for item in month["days"][:today_index]], [False] * today_index
        )

    async def test_my_month_defaults_to_this_business_month(self):
        """不填月份就是本营业月（凌晨一点看到的和店长看到的是同一个月）。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])

        month = await self.store.my_month(employee["id"])

        self.assertEqual(month["month"], TODAY[:7])
        self.assertEqual([item["day"] for item in month["days"]], list(range(1, 31)))

    async def test_my_month_shows_no_shift_not_an_error_for_a_ruleless_person(self):
        """一条规则都没配的人：整月都在，全是「还没排」（`scheduled` 假），不报错。"""
        idle = await self._employee("13800138001", "李四")

        month = await self.store.my_month(idle["id"], "2026-09")

        self.assertEqual(len(month["days"]), 30)
        self.assertEqual([item["scheduled"] for item in month["days"]], [False] * 30)
        self.assertEqual([item["shift_name"] for item in month["days"]], [None] * 30)

    async def test_my_month_is_this_person_only(self):
        """验收 4：同一段日子，两个人的格子各是各的。"""
        first = await self._employee()
        second = await self._employee("13800138001", "李四")
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(first["id"], [day])
        await self.store.set_rule(second["id"], [night])

        mine = await self.store.my_month(first["id"], "2026-09")
        theirs = await self.store.my_month(second["id"], "2026-09")

        # 只看有结果行的那几天（本月过去的日子是空的，见上一个用例的说明）。
        self.assertEqual(
            {item["shift_name"] for item in mine["days"] if item["scheduled"]}, {"白班"}
        )
        self.assertEqual(
            {item["shift_name"] for item in theirs["days"] if item["scheduled"]}, {"夜班"}
        )

    async def test_my_month_past_days_show_what_was_written(self):
        """翻到过去的月份：只显示已经铺过的日子（展开不回头补），不报错。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])

        past = await self.store.my_month(employee["id"], "2026-08")

        self.assertEqual(past["month"], "2026-08")
        self.assertEqual(len(past["days"]), 31)
        self.assertEqual([item["scheduled"] for item in past["days"]], [False] * 31)

    async def test_my_month_rejects_a_bad_month(self):
        """月份写错是输入错误（`invalid_month`），不是 500。"""
        employee = await self._employee()
        for bad in ("2026-9", "2026-13", "九月", "", "2026/09"):
            with self.subTest(month=bad):
                with self.assertRaises(SchedulingError) as ctx:
                    await self.store.my_month(employee["id"], bad)
                self.assertEqual(ctx.exception.code, "invalid_month")

    async def test_my_month_after_the_window_is_all_unscheduled(self):
        """窗口（今天 + 90 天）之外的月份整月都是「还没排」，不是「休」。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])

        window_end = (await self.store.my_month(employee["id"], "2026-09"))["window_end"]
        self.assertEqual(window_end, LAST_DAY)  # 今天 + 89 天
        far = await self.store.my_month(employee["id"], "2027-01")
        self.assertEqual([item["scheduled"] for item in far["days"]], [False] * 31)

    async def test_my_month_gives_no_name_when_the_shift_row_is_gone(self):
        """班次被硬删（票 11 管那张表）：行还在、名字给 None，不 500。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])
        await self.store.my_month(employee["id"], "2026-09")  # 先把行铺出来

        await self.db._conn.execute("DELETE FROM staff_shifts WHERE id = ?", (day,))
        await self.db._conn.commit()
        row = (await self.store.my_month(employee["id"], "2026-09"))["days"][23]

        self.assertEqual(row["business_date"], TODAY)
        self.assertTrue(row["scheduled"])
        self.assertIsNone(row["shift_name"])

    # ── 票 03：每人每班次一个固定责任区 ────────────────────────────────

    async def test_zone_default_is_written_into_new_rows(self):
        employee = await self._employee()
        day = await self._shift_id("白班")
        zone = await self._zone("案板")

        await self.store.set_zone_default(employee["id"], day, zone)
        await self.store.set_rule(employee["id"], [day])

        self.assertEqual(set((await self._zone_ids(employee["id"])).values()), {zone})

    async def test_setting_a_zone_updates_today_and_later_only(self):
        employee = await self._employee()
        day = await self._shift_id("白班")
        first = await self._zone("案板")
        second = await self._zone("熟笼")
        await self.store.set_zone_default(employee["id"], day, first)
        await self.store.set_rule(employee["id"], [day])

        # 两天后再改区：已经写下来的那两个营业日不动，今天及以后跟着走。
        self.store._now = lambda: self.fixed_now + timedelta(days=2)
        await self.store.set_zone_default(employee["id"], day, second)

        zones = await self._zone_ids(employee["id"])
        self.assertEqual(zones["2026-09-24"], first)
        self.assertEqual(zones["2026-09-25"], first)
        self.assertEqual(zones["2026-09-26"], second)
        self.assertEqual(zones[LAST_DAY], second)

    async def test_past_days_keep_the_zone_they_were_written_with(self):
        """读的那一侧也守住「过去不改」：那天写下来是什么区，点开就是什么区。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        first = await self._zone("案板")
        second = await self._zone("熟笼")
        await self.store.set_zone_default(employee["id"], day, first)
        await self.store.set_rule(employee["id"], [day])

        self.store._now = lambda: self.fixed_now + timedelta(days=2)
        await self.store.set_zone_default(employee["id"], day, second)

        def zone_of(detail):
            return [
                item for item in detail["groups"] if item["shift"]["name"] == "白班"
            ][0]["people"][0]["zone"]

        self.assertEqual(zone_of(await self.store.day_detail("2026-09-24")), "案板")
        self.assertEqual(zone_of(await self.store.day_detail("2026-09-26")), "熟笼")

    async def test_zone_follows_the_shift_when_the_rule_changes(self):
        """白班换夜班：区跟着新班次走 —— 区是挂在「班次」上的，不是挂在人身上。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        day_zone = await self._zone("案板")
        night_zone = await self._zone("熟笼")
        await self.store.set_zone_default(employee["id"], day, day_zone)
        await self.store.set_zone_default(employee["id"], night, night_zone)
        await self.store.set_rule(employee["id"], [day])
        self.assertEqual(set((await self._zone_ids(employee["id"])).values()), {day_zone})

        await self.store.set_rule(employee["id"], [night])

        self.assertEqual(set((await self._zone_ids(employee["id"])).values()), {night_zone})

    async def test_zone_default_can_be_cleared(self):
        employee = await self._employee()
        day = await self._shift_id("白班")
        zone = await self._zone()
        await self.store.set_zone_default(employee["id"], day, zone)
        await self.store.set_rule(employee["id"], [day])

        await self.store.set_zone_default(employee["id"], day, None)

        self.assertEqual(await self.store.zone_defaults(), {})
        self.assertEqual(set((await self._zone_ids(employee["id"])).values()), {None})

    async def test_unknown_zone_is_rejected(self):
        employee = await self._employee()
        day = await self._shift_id("白班")

        with self.assertRaises(SchedulingError) as ctx:
            await self.store.set_zone_default(employee["id"], day, 987654)

        self.assertEqual(ctx.exception.code, "unknown_zone")

    async def test_zone_default_rejects_unknown_shift_and_employee(self):
        employee = await self._employee()
        zone = await self._zone()
        day = await self._shift_id("白班")

        with self.assertRaises(SchedulingError) as ctx:
            await self.store.set_zone_default(employee["id"], 987654, zone)
        self.assertEqual(ctx.exception.code, "unknown_shift")

        with self.assertRaises(SchedulingError) as ctx:
            await self.store.set_zone_default(987654, day, zone)
        self.assertEqual(ctx.exception.code, "unknown_employee")

    async def test_roster_carries_zones_and_each_persons_default(self):
        employee = await self._employee()
        day = await self._shift_id("白班")
        zone = await self._zone("熟笼")
        await self.store.set_zone_default(employee["id"], day, zone)

        roster = await self.store.roster_with_rules()

        self.assertIn({"id": zone, "name": "熟笼"}, roster["zones"])
        self.assertEqual(roster["employees"][0]["zone_defaults"], {day: zone})

    async def test_zone_created_in_hygiene_is_pickable_at_once(self):
        """名单只有一份（卫生建的那张表），排班经公共层读它 —— 新建完不用重启。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        before = await self.store.roster_with_rules()

        zone = await self._zone("肠粉")

        after = await self.store.roster_with_rules()
        self.assertNotIn(zone, [item["id"] for item in before["zones"]])
        self.assertIn({"id": zone, "name": "肠粉"}, after["zones"])
        await self.store.set_zone_default(employee["id"], day, zone)
        self.assertEqual((await self.store.zone_defaults())[employee["id"]], {day: zone})

    # ── 验收 3：只铺 90 天，已写下的行不重算 ────────────────────────────

    async def test_expansion_stops_at_ninety_days(self):
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])

        cur = await self.db._conn.execute(
            """SELECT COUNT(*) AS n FROM staff_assignments
               WHERE employee_id = ? AND business_date > ?""",
            (employee["id"], LAST_DAY),
        )
        row = await cur.fetchone()
        self.assertEqual(int(dict(row)["n"]), 0)

    async def test_prepare_survives_a_missing_table(self):
        """0005 还没应用时，启动期这一段不许把应用拦死。

        应用起不来就进不了 Admin「系统更新 → 数据库迁移」面板，而那是补 0005 的唯一
        入口 —— 会绕成死循环。所以缺表只记日志、返回 False（口径同 `services/log_storage.py`
        对 0004 的处理）。
        """
        await self.store._conn.execute("ALTER TABLE staff_shifts RENAME TO staff_shifts_tmp")
        await self.store._conn.commit()
        try:
            missing = SchedulingStore(self.db, now=lambda: self.fixed_now)
            self.assertFalse(await missing.prepare())
            # 真用到排班时也不给 500 traceback：翻成一个有话说、能照做的 code。
            with self.assertRaises(SchedulingError) as caught:
                await missing.list_shifts()
            self.assertEqual(caught.exception.code, "not_migrated")
            # 失败的事务已经回滚，这条连接后面的查询照常能跑。
            self.assertEqual(await missing.list_rules(), {})
        finally:
            await self.store._conn.execute("ALTER TABLE staff_shifts_tmp RENAME TO staff_shifts")
            await self.store._conn.commit()

    async def test_window_follows_the_business_day_cut(self):
        """06:00 才切日：凌晨 5:30 还算是前一天的营业日，窗口也跟着往前一天。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        before_cut = SchedulingStore(
            self.db, now=lambda: datetime(2026, 9, 24, 5, 30, tzinfo=CHINA_TZ)
        )

        await before_cut.set_rule(employee["id"], [day])

        self.assertEqual(before_cut.today(), "2026-09-23")
        rows = await self._rows(employee["id"])
        self.assertEqual(rows[0]["business_date"], "2026-09-23")
        self.assertEqual(rows[-1]["business_date"], "2026-12-21")  # 9/23 + 89

    async def test_month_past_the_window_says_where_it_ends(self):
        """翻到窗口尽头那个月：12/22 之前有人，之后是空的 —— 接口得说出边界在哪，
        否则前端只能把「还没铺到」画成「那天没人上班」。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])

        calendar = await self.store.month_calendar("2026-12")

        self.assertEqual(calendar["window_end"], LAST_DAY)
        cells = {day_["business_date"]: day_ for day_ in calendar["days"]}
        self.assertEqual(len(calendar["days"]), 31)
        self.assertEqual(cells[LAST_DAY]["total"], 1)
        self.assertEqual(cells["2026-12-23"]["total"], 0)

    async def test_existing_rows_are_not_recomputed(self):
        """展开是「补齐缺的天」，不是「重铺窗口」：已经写下的行原样留着。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [day])

        # 模拟别处改过这一行（票 07 的单日覆盖就是这么写的）。
        await self.db._conn.execute(
            """UPDATE staff_assignments SET shift_id = ?
               WHERE employee_id = ? AND business_date = ?""",
            (night, employee["id"], "2026-09-28"),
        )
        await self.db._conn.commit()
        cur = await self.db._conn.execute(
            """SELECT created_at FROM staff_assignments
               WHERE employee_id = ? AND business_date = ?""",
            (employee["id"], "2026-09-28"),
        )
        stamp_before = dict(await cur.fetchone())["created_at"]

        written = await self.store.expand(employee["id"])

        self.assertEqual(written, 0)
        rows = {row["business_date"]: row for row in await self._rows(employee["id"])}
        self.assertEqual(rows["2026-09-28"]["shift_id"], night)
        cur = await self.db._conn.execute(
            """SELECT created_at FROM staff_assignments
               WHERE employee_id = ? AND business_date = ?""",
            (employee["id"], "2026-09-28"),
        )
        self.assertEqual(dict(await cur.fetchone())["created_at"], stamp_before)

    async def test_rule_change_rewrites_only_today_and_later(self):
        """改规则只影响今天以后：之前写下的行**整行**不动（「过去不改」是做法本身）。

        比的是整行（含 `created_at` / `updated_at`），不只是 `shift_id` —— 否则哪天有人
        把铺行改成 upsert、或把删除条件的 `>=` 写成 `>`，这条例题照样绿。
        """
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [day])
        before = await self._full_rows(employee["id"])

        later = SchedulingStore(
            self.db, now=lambda: self.fixed_now + timedelta(days=10)
        )  # 2026-10-04
        await later.set_rule(employee["id"], [night])

        rows = await self._rows(employee["id"])
        past = [row for row in rows if row["business_date"] < "2026-10-04"]
        future = [row for row in rows if row["business_date"] >= "2026-10-04"]
        self.assertEqual(past[0]["business_date"], TODAY)
        self.assertEqual(past[-1]["business_date"], "2026-10-03")
        self.assertEqual({row["shift_id"] for row in past}, {day})
        self.assertEqual({row["shift_id"] for row in future}, {night})

        after = await self._full_rows(employee["id"])
        untouched = {date: row for date, row in before.items() if date < "2026-10-04"}
        self.assertEqual(
            {date: after[date] for date in untouched},
            untouched,
            "今天以前的行被改写了",
        )
        # 今天以后的每一行都换过了（说明上面那份快照不是「什么都没发生」）
        self.assertTrue(
            any(after[date] != row for date, row in before.items() if date >= "2026-10-04")
        )

    # ── 验收 5：没配规则的人不出现，也不报错 ────────────────────────────

    async def test_employee_without_rule_has_no_assignments(self):
        configured = await self._employee("13800138000", "张三")
        idle = await self._employee("13800138001", "李四")
        day = await self._shift_id("白班")
        await self.store.set_rule(configured["id"], [day])

        self.assertEqual(await self._rows(idle["id"]), [])
        calendar = await self.store.month_calendar("2026-09")
        by_date = {day_["business_date"]: day_ for day_ in calendar["days"]}
        self.assertEqual(by_date[TODAY]["total"], 1)

    async def test_roster_lists_everyone_with_rule_state(self):
        configured = await self._employee("13800138000", "张三")
        pending = await self.accounts.register("13800138001", PASSWORD, "李四")
        day = await self._shift_id("白班")
        await self.store.set_rule(configured["id"], [day])

        roster = await self.store.roster_with_rules()

        by_id = {employee["id"]: employee for employee in roster["employees"]}
        self.assertEqual(set(by_id), {configured["id"], pending["id"]})
        self.assertEqual(by_id[configured["id"]]["rule"]["cycle"], [day])
        self.assertIsNone(by_id[pending["id"]]["rule"])
        self.assertEqual([shift["name"] for shift in roster["shifts"]], ["白班", "夜班"])

    # ── 规则的增删改 ────────────────────────────────────────────────────

    async def test_clear_rule_removes_today_and_later(self):
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])

        await self.store.clear_rule(employee["id"])

        self.assertEqual(await self._rows(employee["id"]), [])
        self.assertEqual(await self.store.list_rules(), {})

    async def test_rule_change_replaces_previous_cycle(self):
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [day])
        await self.store.set_rule(employee["id"], [night])

        rules = await self.store.list_rules()
        self.assertEqual(rules[employee["id"]]["cycle"], [night])
        rows = await self._rows(employee["id"])
        self.assertEqual(len(rows), EXPANSION_DAYS)
        self.assertEqual({row["shift_id"] for row in rows}, {night})

    async def test_unknown_shift_is_rejected(self):
        """整条周期都是不存在的班次：第 1 格就报错（`unknown_shift` 现在只留给
        责任区那边——「配区时选了个不存在的班次」）。"""
        employee = await self._employee()
        with self.assertRaises(SchedulingError) as caught:
            await self.store.set_rule(employee["id"], [999])
        self.assertEqual(caught.exception.code, "unknown_shift_in_cycle")
        self.assertEqual(caught.exception.args[0], "1")
        self.assertEqual(await self._rows(employee["id"]), [])

    async def test_unknown_employee_is_rejected(self):
        day = await self._shift_id("白班")
        with self.assertRaises(SchedulingError) as caught:
            await self.store.set_rule(9999, [day])
        self.assertEqual(caught.exception.code, "unknown_employee")

    async def test_empty_cycle_is_rejected(self):
        employee = await self._employee()
        with self.assertRaises(SchedulingError) as caught:
            await self.store.set_rule(employee["id"], [])
        self.assertEqual(caught.exception.code, "invalid_cycle")

    async def test_bad_month_is_rejected(self):
        with self.assertRaises(SchedulingError) as caught:
            await self.store.month_calendar("2026-9")
        self.assertEqual(caught.exception.code, "invalid_month")

    # ── 验收：单日覆盖（票 07）──────────────────────────────────────────
    #
    # 覆盖是「整天的快照」：这一天的班次与责任区由那次改动定下来，规则以后怎么变都不再
    # 动它（想回去就撤掉覆盖）。所以下面既要断言「改的那天变了」，也要断言
    # 「别的时候没变」。

    async def test_override_changes_only_that_one_day(self):
        """验收 1：改某天的某人，只影响那一天。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [day])

        await self.store.set_override(employee["id"], "2026-09-25", shift_id=night)

        rows = {row["business_date"]: row for row in await self._rows(employee["id"])}
        self.assertEqual(len(rows), EXPANSION_DAYS)  # 不多不少，还是这个窗口
        self.assertEqual(
            (rows["2026-09-24"]["shift_id"], rows["2026-09-24"]["source"]), (day, "rule")
        )
        self.assertEqual(
            (rows["2026-09-25"]["shift_id"], rows["2026-09-25"]["source"]), (night, "override")
        )
        self.assertEqual(
            (rows["2026-09-26"]["shift_id"], rows["2026-09-26"]["source"]), (day, "rule")
        )
        # 今天也能改：只有**过去**不行。把 `day < self.today()` 写成 `<=` 就会红在这里。
        saved = await self.store.set_override(employee["id"], TODAY, shift_id=night)
        self.assertEqual(saved["business_date"], TODAY)
        rows = {row["business_date"]: row for row in await self._rows(employee["id"])}
        self.assertEqual((rows[TODAY]["shift_id"], rows[TODAY]["source"]), (night, "override"))

    async def test_override_to_rest_takes_the_person_out_of_the_count(self):
        """验收 2：改成休 —— 那天少一个人、多一个「休」，别的时候不受影响。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])
        before = await self.store.day_detail("2026-09-25")
        self.assertEqual((before["total"], before["off_count"]), (1, 0))

        saved = await self.store.set_override(employee["id"], "2026-09-25", is_rest=True)

        self.assertEqual((saved["shift_id"], saved["is_rest"]), (None, True))
        after = await self.store.day_detail("2026-09-25")
        self.assertEqual((after["total"], after["off_count"]), (0, 1))
        tomorrow = await self.store.day_detail("2026-09-26")
        self.assertEqual((tomorrow["total"], tomorrow["off_count"]), (1, 0))
        # 休的那天人是「那天休」，不是「没排到」：行还在，班次为空。
        self.assertEqual(
            (await self._rows(employee["id"]))[1]["shift_id"],
            None,
        )

    async def test_override_can_move_only_the_zone(self):
        """验收 3：只换责任区 —— 班次还是白班，那天换到别的区，固定区配置不动。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        zone = await self._zone("案板")
        other = await self._zone("凉菜")
        await self.store.set_rule(employee["id"], [day])
        await self.store.set_zone_default(employee["id"], day, zone)

        await self.store.set_override(employee["id"], "2026-09-25", shift_id=day, zone_id=other)

        detail = await self.store.day_detail("2026-09-25")
        self.assertEqual(detail["groups"][0]["shift"]["name"], "白班")
        self.assertEqual(
            detail["groups"][0]["people"],
            [
                {
                    "id": employee["id"],
                    "name": "张三",
                    "zone": "凉菜",
                    # 区也按 id 给一份：编辑器要按 id 预填下拉（名字没有唯一约束）。
                    "zone_id": other,
                    "overridden": True,
                }
            ],
        )
        # 「固定区」是配置，覆盖只是那一天：第二天照旧回到案板。
        self.assertEqual(
            (await self.store.day_detail("2026-09-26"))["groups"][0]["people"][0]["zone"],
            "案板",
        )

    async def test_override_without_a_zone_follows_the_shifts_fixed_zone(self):
        """不给区 = 「跟这个班次的固定区」—— 跟展开时是同一条口径。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        zone = await self._zone("案板")
        await self.store.set_rule(employee["id"], [day])
        await self.store.set_zone_default(employee["id"], day, zone)

        saved = await self.store.set_override(employee["id"], "2026-09-25", shift_id=day)

        self.assertEqual(saved["zone_id"], zone)
        self.assertEqual((await self._zone_ids(employee["id"]))["2026-09-25"], zone)

    async def test_month_calendar_marks_the_overridden_day(self):
        """验收 4：被覆盖过的天在月历上有标记（`overridden`），别人别的天没有。"""
        employee = await self._employee()
        other = await self._employee(phone="13800138001", name="李四")
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])
        await self.store.set_rule(other["id"], [day])

        await self.store.set_override(employee["id"], "2026-09-25", is_rest=True)

        days = (await self.store.month_calendar("2026-09"))["days"]
        marked = {item["business_date"]: item for item in days}
        self.assertEqual(marked["2026-09-25"]["overridden"], 1)
        self.assertEqual(marked["2026-09-24"]["overridden"], 0)
        self.assertEqual(marked["2026-09-26"]["overridden"], 0)
        # 「被改成休」在人数里看不出来（那天他本来就不占白班的名额），标记里看得出来。
        self.assertEqual(marked["2026-09-25"]["total"], 1)  # 只剩李四
        # 标记读的是**结果行的 `source`**，所以被改成休的人也带着它 —— 而且休的人
        # 不能只给一个数：他还得能被点开改回来（`off_people`）。
        detail = await self.store.day_detail("2026-09-25")
        self.assertEqual(
            [(item["name"], item["overridden"]) for item in detail["groups"][0]["people"]],
            [("李四", False)],
        )
        self.assertEqual(
            [(item["name"], item["zone_id"], item["overridden"]) for item in detail["off_people"]],
            [("张三", None, True)],
        )
        self.assertEqual((detail["total"], detail["off_count"]), (1, 1))

    async def test_clearing_an_override_puts_the_rule_back(self):
        """验收 5：撤掉覆盖 → 那天回到规则铺出来的样子（连责任区一起）。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        zone = await self._zone("案板")
        await self.store.set_rule(employee["id"], [day])
        await self.store.set_zone_default(employee["id"], day, zone)
        await self.store.set_override(employee["id"], "2026-09-25", is_rest=True)

        await self.store.clear_override(employee["id"], "2026-09-25")

        row = (await self._full_rows(employee["id"]))["2026-09-25"]
        self.assertEqual((row["shift_id"], row["zone_id"], row["source"]), (day, zone, "rule"))
        detail = await self.store.day_detail("2026-09-25")
        self.assertEqual(detail["total"], 1)
        self.assertEqual(detail["groups"][0]["people"][0]["overridden"], False)
        days = (await self.store.month_calendar("2026-09"))["days"]
        self.assertEqual([d["overridden"] for d in days if d["business_date"] == "2026-09-25"], [0])

    async def test_clearing_a_past_override_keeps_the_history(self):
        """撤销只对今天以后生效：过去那天的结果行一个字都不改（记录还是会删）。

        这是「过去不改」在撤销这条路的样子。店长不该看到一个点了没反应的按钮 ——
        所以 `day_detail` 顺手给出 `undoable`，前端据此把撤销收起来。
        """
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [day])
        await self.store.set_override(employee["id"], "2026-09-25", shift_id=night)
        stamp = (await self._full_rows(employee["id"]))["2026-09-25"]
        self.assertEqual(stamp["source"], "override")
        self.assertEqual((await self.store.day_detail("2026-09-25"))["undoable"], True)

        # 时钟推到那天之后：9/25 成了过去（把 `if day >= self.today()` 改成 `if True` 会红）。
        self.fixed_now = datetime(2026, 9, 28, 10, 0, tzinfo=CHINA_TZ)
        self.assertEqual((await self.store.day_detail("2026-09-25"))["undoable"], False)

        await self.store.clear_override(employee["id"], "2026-09-25")

        self.assertEqual((await self._full_rows(employee["id"]))["2026-09-25"], stamp)
        cur = await self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM scheduling_overrides WHERE employee_id = ?",
            (employee["id"],),
        )
        self.assertEqual(dict(await cur.fetchone())["n"], 0)

    async def test_rule_change_does_not_wipe_an_overridden_day(self):
        """验收 6：改规则时已被覆盖的那天不被冲掉（一个字都不动，含 `updated_at`）。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [day])
        await self.store.set_override(employee["id"], "2026-09-25", is_rest=True)
        stamp = (await self._full_rows(employee["id"]))["2026-09-25"]

        await self.store.set_rule(employee["id"], [night])  # 新规则：那天本该上夜班

        self.assertEqual((await self._full_rows(employee["id"]))["2026-09-25"], stamp)
        rows = {row["business_date"]: row for row in await self._rows(employee["id"])}
        self.assertEqual(rows["2026-09-24"]["shift_id"], night)
        self.assertEqual(rows["2026-09-25"]["shift_id"], None)
        self.assertEqual(rows["2026-09-26"]["shift_id"], night)

    async def test_clearing_the_rule_does_not_wipe_an_overridden_day(self):
        """验收 6（第二种规则变动）：删掉规则时，手改过的那天照样留着。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [day])
        await self.store.set_override(employee["id"], "2026-09-25", shift_id=night)
        stamp = (await self._full_rows(employee["id"]))["2026-09-25"]

        await self.store.clear_rule(employee["id"])

        rows = await self._full_rows(employee["id"])
        # 规则铺的全撤了（今天及以后），只剩手改的那天。
        self.assertEqual(sorted(rows), ["2026-09-25"])
        self.assertEqual(rows["2026-09-25"], stamp)
        detail = await self.store.day_detail("2026-09-25")
        # 挂在夜班那组：`day_detail` 把所有在用的班次都列出来，`groups[0]` 可能是空组。
        people = {
            item["name"]: item
            for group in detail["groups"]
            for item in group["people"]
        }
        self.assertEqual(people["张三"]["overridden"], True)

    async def test_changing_the_fixed_zone_does_not_wipe_an_overridden_day(self):
        """验收 6（第三种规则变动）：改固定区时，手改过的那天也不动。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        zone = await self._zone("案板")
        other = await self._zone("凉菜")
        third = await self._zone("面点")
        await self.store.set_rule(employee["id"], [day])
        await self.store.set_zone_default(employee["id"], day, zone)
        await self.store.set_override(employee["id"], "2026-09-25", shift_id=day, zone_id=other)
        stamp = (await self._full_rows(employee["id"]))["2026-09-25"]

        await self.store.set_zone_default(employee["id"], day, third)

        # 手改那天照旧是「凉菜」（改的那一刻定下的），别的日子跟新的固定区走。
        self.assertEqual((await self._full_rows(employee["id"]))["2026-09-25"], stamp)
        zone_ids = await self._zone_ids(employee["id"])
        self.assertEqual((zone_ids["2026-09-25"], zone_ids["2026-09-24"]), (other, third))

    async def test_override_refuses_a_past_day_and_a_day_past_the_window(self):
        """验收 7：只影响今天以后 —— 昨天不给改（`past_day`），窗口外不给改。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])
        before = await self._full_rows(employee["id"])

        with self.assertRaises(SchedulingError) as past:
            await self.store.set_override(employee["id"], "2026-09-23", shift_id=day)
        self.assertEqual(past.exception.code, "past_day")
        with self.assertRaises(SchedulingError) as beyond:
            await self.store.set_override(employee["id"], "2026-12-23", shift_id=day)
        self.assertEqual(beyond.exception.code, "beyond_window")
        with self.assertRaises(SchedulingError) as bad_date:
            await self.store.set_override(employee["id"], "9/25", shift_id=day)
        self.assertEqual(bad_date.exception.code, "invalid_business_date")

        self.assertEqual(await self._full_rows(employee["id"]), before)
        # 窗口最后一天还是能改的（边界是含的，跟展开的窗口同一口径）。
        saved = await self.store.set_override(employee["id"], LAST_DAY, is_rest=True)
        self.assertEqual(saved["business_date"], LAST_DAY)

    async def test_override_validates_its_own_payload(self):
        """「改成休」不能顺带带班次或区；改班次必须给班次；人、班次、区都得存在。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        zone = await self._zone("案板")
        await self.store.set_rule(employee["id"], [day])

        cases = [
            ({"is_rest": True, "shift_id": day}, "rest_with_details"),
            ({"is_rest": True, "zone_id": zone}, "rest_with_details"),
            ({}, "missing_shift"),
            ({"shift_id": 999}, "unknown_shift"),
            ({"shift_id": day, "zone_id": 999}, "unknown_zone"),
        ]
        for payload, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(SchedulingError) as caught:
                    await self.store.set_override(employee["id"], "2026-09-25", **payload)
                self.assertEqual(caught.exception.code, code)
        with self.assertRaises(SchedulingError) as caught:
            await self.store.set_override(9999, "2026-09-25", shift_id=day)
        self.assertEqual(caught.exception.code, "unknown_employee")

        # 一次都没落盘：那天还是规则铺的，覆盖记录一条都没有。
        detail = await self.store.day_detail("2026-09-25")
        self.assertEqual(detail["groups"][0]["people"][0]["overridden"], False)
        days = (await self.store.month_calendar("2026-09"))["days"]
        self.assertEqual([d["overridden"] for d in days if d["business_date"] == "2026-09-25"], [0])

    async def test_a_ruleless_person_can_be_given_a_day_off_and_undo_leaves_nothing(self):
        """没有规则的人也能单改一天；撤掉之后那天回到「还没铺到」，不留空行。"""
        employee = await self._employee()
        self.assertEqual(await self._rows(employee["id"]), [])

        await self.store.set_override(employee["id"], "2026-09-25", is_rest=True)

        self.assertEqual(
            [
                (row["business_date"], row["shift_id"], row["source"])
                for row in await self._rows(employee["id"])
            ],
            [("2026-09-25", None, "override")],
        )
        await self.store.clear_override(employee["id"], "2026-09-25")
        self.assertEqual(await self._rows(employee["id"]), [])

    async def test_expand_brings_a_lost_override_row_back_from_the_record(self):
        """结果行丢了也照覆盖记录补回来 —— 记录才是「那天被改成什么」的真源。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [day])
        await self.store.set_override(employee["id"], "2026-09-25", shift_id=night)
        await self.db._conn.execute(
            "DELETE FROM staff_assignments WHERE employee_id = ? AND business_date = ?",
            (employee["id"], "2026-09-25"),
        )
        await self.db._conn.commit()

        await self.store.expand(employee["id"])

        rows = {row["business_date"]: row for row in await self._rows(employee["id"])}
        self.assertEqual(
            (rows["2026-09-25"]["shift_id"], rows["2026-09-25"]["source"]), (night, "override")
        )
        self.assertEqual(rows["2026-09-26"]["shift_id"], day)  # 别的天照旧按规则

    # ── 启动期：班次表只在空的时候放默认值 ──────────────────────────────

    async def test_prepare_keeps_renamed_shifts(self):
        await self.db._conn.execute("UPDATE staff_shifts SET name = '早班' WHERE name = '白班'")
        await self.db._conn.commit()

        await self.store.prepare()

        names = [shift["name"] for shift in await self.store.list_shifts()]
        self.assertEqual(names, ["早班", "夜班"])

    async def test_connection_knows_the_new_tables(self):
        """新表也要进连接的 TableView 清单，否则 `db.table("staff_shifts")` 抛「未知表」。"""
        for table in SCHEDULING_TABLES:
            self.assertIsNotNone(self.db.table(table))


class SchedulingLayeringTest(unittest.TestCase):
    """验收 6/7：排班是独立系统 —— 不 import 卫生，也不动员工端。"""

    def test_scheduling_never_imports_hygiene(self):
        # 服务层和它的 HTTP 面都扫：票 03 起公共层多了 `services/identity/zones.py`，
        # 排班侧真正碰责任区的调用点在 `store.py` 与 `api/scheduling.py` 两处。
        paths = sorted((REPO_ROOT / "services" / "scheduling").glob("*.py"))
        paths.append(REPO_ROOT / "api" / "scheduling.py")
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
            offenders = sorted(name for name in imported if name.startswith("services.hygiene"))
            self.assertEqual(offenders, [], f"{path.name} 不该 import 卫生")

    def test_scheduling_http_doors_are_separate(self):
        """票 05：排班的 HTTP 面有两扇门，员工那扇只读自己。

        票 02 时这里断言「一条员工路由都没有」；票 05 开了 `/me`、票 06 又开了
        `/me/month` 之后，边界从「零条」变成「两扇门互不通用」—— 店长那九条（七条路径）
        只认管理端会话，员工那两条只认手机端 cookie。

        断言路由表本身，不是源码文本 —— 文本比对会被注释或文档字符串误伤
        （写一句「这里不用 require_staff_session」就红了）。
        """
        from api.scheduling import router

        staff_paths = {"/api/scheduling/me", "/api/scheduling/me/month"}
        manager_paths = {
            "/api/scheduling/shifts",
            "/api/scheduling/calendar",
            "/api/scheduling/day",
            "/api/scheduling/roster",
            "/api/scheduling/rules/{employee_id}",
            "/api/scheduling/zone-defaults/{employee_id}",
            # 票 07：单日覆盖的改与撤。
            "/api/scheduling/overrides/{employee_id}/{business_date}",
        }
        self.assertEqual(
            {route.path for route in router.routes}, staff_paths | manager_paths
        )

        def dependency_names(dependant) -> set[str]:
            names = set()
            for sub in dependant.dependencies:
                names.add(getattr(sub.call, "__name__", ""))
                names |= dependency_names(sub)
            return names

        for route in router.routes:
            names = dependency_names(route.dependant)
            if route.path in staff_paths:
                self.assertIn("require_staff_session", names, route.path)
                self.assertNotIn("require_session", names, route.path)
            else:
                self.assertIn("require_session", names, route.path)
                self.assertNotIn("require_staff_session", names, route.path)

        # 员工那两条门上都没有路径参数：读谁不由调用方说了算。
        for route in router.routes:
            if route.path in staff_paths:
                self.assertNotIn("{", route.path)

    def test_new_tables_are_registered_read_only(self):
        """验收 4：新表进表名清单，Admin 的数据浏览器只读能看到。"""
        self.assertEqual(
            set(SCHEDULING_TABLES),
            {
                "staff_shifts",
                "staff_assignments",
                "scheduling_rules",
                "scheduling_zone_defaults",
                # 票 07：单日覆盖记在这张表里（`source='override'` 的结果行由它解释）。
                "scheduling_overrides",
            },
        )
        for table in SCHEDULING_TABLES:
            self.assertIn(table, ADMIN_READ_ONLY_TABLES)
        from api.admin import _admin_catalog

        catalog = _admin_catalog()
        for table in SCHEDULING_TABLES:
            self.assertIn(table, catalog["tables"])
            self.assertEqual(catalog["table_meta"][table]["group"], "scheduling")
            self.assertTrue(catalog["table_meta"][table]["read_only"])
        groups = {group["key"]: group for group in catalog["groups"]}
        self.assertEqual(set(groups["scheduling"]["tables"]), set(SCHEDULING_TABLES))
