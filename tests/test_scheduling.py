#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班服务层（票 02）：配固定班次 → 展开 90 天 → 月历数得出来。

主缝 = 服务层 + 真库 + 注入时钟（见 `.scratch/scheduling/spec.md` 的「测试缝」）：
排班的窗口、相位、过去不改都由**营业日**决定，所以时钟必须能拨。
"""

import ast
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from config import settings
from database import CHINA_TZ, DatabaseManager
from db_core.schema import ADMIN_READ_ONLY_TABLES, SCHEDULING_TABLES
from services.business_day import current_business_date
from services.hygiene.accounts import EmployeeAccounts
from services.scheduling.store import (
    EXPANSION_DAYS,
    MAX_REQUEST_NOTE,
    SOURCE_RULE,
    SchedulingError,
    SchedulingStore,
)

PASSWORD = "password123"
REPO_ROOT = Path(__file__).resolve().parents[1]

# 2026-09-24 是周四，10:00 已经过了 06:00 的切日点 → 营业日就是 9/24。
TODAY = "2026-09-24"
LAST_DAY = "2026-12-22"  # 今天 + 89 天


class AdvancingClock:
    """一个会推进的假时钟：前 `early_reads` 次读数是切日**之前**的时刻，之后是之后的。

    06:00 是营业日的切日点（`services/business_day.py`）：同一个写请求里前几次读钟
    算出来的营业日是 9/23、后面的变成 9/24 —— 正好复现「删的时候还是 9/23、铺的时候
    已经是 9/24」那个竞态（见 `test_a_write_request_reads_the_clock_once_across_the_cut`）。
    """

    def __init__(self, early: datetime, late: datetime, early_reads: int = 3):
        self.early = early
        self.late = late
        self.early_reads = early_reads
        self.reads = 0

    def __call__(self) -> datetime:
        self.reads += 1
        return self.early if self.reads <= self.early_reads else self.late


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

    async def _raw_rule(self, employee_id, cycle, anchor_date=TODAY):
        """手工 SQL 写一条规则（票面的「人工 SQL 写坏 cycle / anchor_date」就是这个来源）。

        不走 `set_rule`：那一道闸会把脏数据挡在外面，而缺陷 1 说的正是绕过闸之后写进去的
        那一行 —— 以前它能让全店的月历、当天、待办和名单一起 400。
        """
        stamp = self.fixed_now.isoformat()
        await self.db._conn.execute(
            """INSERT INTO scheduling_rules (employee_id, cycle, anchor_date, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (employee_id, cycle, anchor_date, stamp, stamp),
        )
        await self.db._conn.commit()

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

    async def _requests(self):
        """申请表的整表快照：排序固定，方便整体比对（新用例大多只落一条）。"""
        cur = await self.db._conn.execute(
            """SELECT id, employee_id, kind, start_date, end_date, status, note, decided_at
               FROM scheduling_requests ORDER BY id ASC""",
        )
        return [dict(row) for row in await cur.fetchall()]

    async def _overrides(self):
        """覆盖表的整表快照：`kind` 是「手改 / 请假」的判据（票 08 用它分辨不是「休」）。"""
        cur = await self.db._conn.execute(
            """SELECT employee_id, business_date, shift_id, zone_id, kind
               FROM scheduling_overrides ORDER BY employee_id ASC, business_date ASC""",
        )
        return [dict(row) for row in await cur.fetchall()]

    async def _peer_of(self, request_id):
        """某条申请「想跟谁换」（0009 加的那一列）。

        单独一条查询而不是塞进 `_requests()`：那份快照是票 08 定下的形状，两边一起改会
        让票 08 的用例跟着变红（它们整字典比对）。
        """
        cur = await self.db._conn.execute(
            "SELECT peer_employee_id FROM scheduling_requests WHERE id = ?", (request_id,)
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)["peer_employee_id"]

    async def _day_shift(self, employee_id, business_date=TODAY):
        """某人某天那一行的班次：没有那一行就是 `None`（那天还没排到他）。"""
        cur = await self.db._conn.execute(
            """SELECT shift_id FROM staff_assignments
               WHERE employee_id = ? AND business_date = ?""",
            (int(employee_id), business_date),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)["shift_id"]

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

    async def test_dirty_anchor_date_skips_that_person_instead_of_the_whole_store(self):
        """人工 SQL 把起点写成垃圾：那个人被跳过，名单里标成「读不出来」。

        起点是**算相位时**才算的（`_rule_anchor`），所以 `list_rules()` 照旧把它原样带
        出来、不在这里抛；会被全店读到的两条路 —— `expand()`（`/calendar`、`/day` 的
        第一步）与 `roster_with_rules()`（`/roster`，唯一能重配周期的入口）—— 都不许
        跟着 400。
        """
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])
        await self.db._conn.execute(
            "UPDATE scheduling_rules SET anchor_date = ? WHERE employee_id = ?",
            ("昨天", employee["id"]),
        )
        await self.db._conn.commit()

        # 内部读拿得到那一行（`cycle` 是好的、`anchor_date` 原样带出来）—— 它一被算就报错。
        self.assertEqual(
            (await self.store.list_rules())[employee["id"]]["anchor_date"], "昨天"
        )

        # 展开跳过这个人：把已经铺好的行清掉再展开，一行都不写（不是照脏起点硬铺）。
        await self.db._conn.execute("DELETE FROM staff_assignments")
        await self.db._conn.commit()
        self.assertEqual(await self.store.expand(), 0)
        self.assertEqual(await self._rows(employee["id"]), [])

        roster = await self.store.roster_with_rules()
        rule = {item["id"]: item for item in roster["employees"]}[employee["id"]]["rule"]
        self.assertEqual(rule, {"cycle": None, "anchor_date": None, "invalid": True})

    # ── 缺陷 1：一条脏规则不该打死全店（人工 SQL 会碰 `scheduling_rules`）────

    async def test_a_broken_cycle_does_not_stop_the_rest_of_the_store(self):
        """某人的 `cycle` 是非法 JSON：别人照常铺，月历与名单都出得来。

        `/calendar` 与 `/day` 都是 `expand()` + 读，`/inbox` 也先 `expand()`；名单面板
        （`roster_with_rules()`）是唯一能重配周期的入口，它跟着 400 就等于店长自锁 ——
        只能进库改。所以坏规则要**显示出来**，不是抛错。
        """
        broken = await self._employee("13800138000", "张三")
        healthy = await self._employee("13800138001", "李四")
        day = await self._shift_id("白班")
        await self.store.set_rule(healthy["id"], [day])
        await self._raw_rule(broken["id"], "白班/夜班")  # 人工 SQL：cycle 根本不是 JSON

        # 清掉李四已经铺好的行：接下来这次展开该把他的 90 天补齐，而张三那条坏规则
        # 一行都不写（跳过）—— 于是「写了几行」这个数就说明了别人没被连坐。
        await self.db._conn.execute("DELETE FROM staff_assignments")
        await self.db._conn.commit()
        written = await self.store.expand()  # `/calendar`、`/day`、`/inbox` 的第一步
        calendar = await self.store.month_calendar("2026-09")

        # 别人那天照常排出来：只铺了李四那 90 天，张三一行都没有（被跳过）。
        self.assertEqual(written, EXPANSION_DAYS)
        self.assertEqual(len(await self._rows(healthy["id"])), EXPANSION_DAYS)
        self.assertEqual(await self._rows(broken["id"]), [])
        cells = {item["business_date"]: item for item in calendar["days"]}
        self.assertEqual(cells[TODAY]["counts"][str(day)], 1)
        self.assertEqual(cells[TODAY]["total"], 1)

        # 名单：坏规则显示出来，好规则一个字没变（前端 `ruleLabel` 照这个形状渲染）。
        roster = await self.store.roster_with_rules()
        by_id = {item["id"]: item for item in roster["employees"]}
        self.assertEqual(
            by_id[broken["id"]]["rule"],
            {"cycle": None, "anchor_date": None, "invalid": True},
        )
        self.assertEqual(by_id[healthy["id"]]["rule"], {"cycle": [day], "anchor_date": TODAY})

        # 内部读的口径一个字没改：`list_rules()` 遇到读不出来的 `cycle` 照旧抛
        # （要降级的是调用方，见那个 docstring）。
        with self.assertRaises(SchedulingError) as caught:
            await self.store.list_rules()
        self.assertEqual(caught.exception.code, "invalid_cycle")

        # `/inbox` 那条路也走得通：坏规则的人算「配过规则」，不进「还没配规则」名单。
        inbox = await self.store.inbox()
        self.assertEqual(inbox["requests"], [])
        self.assertEqual([item["id"] for item in inbox["without_rule"]], [])

    async def test_a_cycle_pointing_at_a_missing_shift_writes_no_ghost_rows(self):
        """周期里引用一个不存在的班次：跳过这个人，也不铺 90 行指向幽灵班次的行。

        `unknown_shift_in_cycle` 本来只在 `set_rule` 那道闸上（票 04），手工 SQL 绕得过去；
        照那种规则展开，结果行的 `shift_id` 指向一个查不到的班次 —— 月历会多出一列
        没有名字的计数，清理还得进库。
        """
        broken = await self._employee("13800138000", "张三")
        healthy = await self._employee("13800138001", "李四")
        day = await self._shift_id("白班")
        await self.store.set_rule(healthy["id"], [day])
        await self._raw_rule(broken["id"], "[9999]")

        # 同上：清掉李四的行，让这次展开真的写一遍。
        await self.db._conn.execute("DELETE FROM staff_assignments")
        await self.db._conn.commit()
        written = await self.store.expand()

        self.assertEqual(written, EXPANSION_DAYS)
        self.assertEqual(len(await self._rows(healthy["id"])), EXPANSION_DAYS)
        self.assertEqual(await self._rows(broken["id"]), [])
        cells = {
            item["business_date"]: item
            for item in (await self.store.month_calendar("2026-09"))["days"]
        }
        self.assertEqual(cells[TODAY]["counts"][str(day)], 1)
        self.assertNotIn("9999", cells[TODAY]["counts"])  # 没有那一列幽灵班次
        self.assertEqual(cells[TODAY]["total"], 1)

        roster = await self.store.roster_with_rules()
        by_id = {item["id"]: item for item in roster["employees"]}
        self.assertEqual(
            by_id[broken["id"]]["rule"],
            {"cycle": None, "anchor_date": None, "invalid": True},
        )

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

    async def test_a_write_request_reads_the_clock_once_across_the_cut(self):
        """写请求正好跨过 06:00：删与铺必须是**同一个营业日**。

        钟在同一个请求里从 9/24 05:59:59.999（还属于营业日 9/23）推进到 9/24 06:00:00.001
        （营业日已经是 9/24）。修之前 `_delete_future_rule_rows` 与 `_expand_rows` 各读
        一次钟 —— 删的是 ≥ 9/23 的行、铺的是 [9/24, …]，刚结束的那个营业日（9/23）被删掉
        而且**没人补**：月历和员工页上那天从「有班」变成「还没排到」。修之后 `set_rule`
        在开头取一次 today 往下传。

        `early_reads=3` 是按修之前那条链取的：起点、规则行的 stamp、删除各读一次钟，
        第 4 次（重铺）已经在切日之后 —— 于是缺口在那一次请求里真的成立。
        """
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        # 先在营业日 9/23 把规则配好（凌晨 5:30 还算是 9/23 的营业日）：
        # 那个营业日本来就有一行（窗口从 9/23 起）。
        before_cut = SchedulingStore(
            self.db, now=lambda: datetime(2026, 9, 24, 5, 30, tzinfo=CHINA_TZ)
        )
        await before_cut.set_rule(employee["id"], [day])
        self.assertEqual(before_cut.today(), "2026-09-23")
        self.assertEqual((await self._rows(employee["id"]))[0]["business_date"], "2026-09-23")

        clock = AdvancingClock(
            datetime(2026, 9, 24, 5, 59, 59, 999000, tzinfo=CHINA_TZ),
            datetime(2026, 9, 24, 6, 0, 0, 1000, tzinfo=CHINA_TZ),
        )
        # 前提：这两个读数算出来的营业日不一样（05:59 还是 9/23、06:00 已是 9/24）。
        self.assertEqual(current_business_date(clock.early), "2026-09-23")
        self.assertEqual(current_business_date(clock.late), "2026-09-24")

        await SchedulingStore(self.db, now=clock).set_rule(employee["id"], [night])
        self.assertGreater(clock.reads, 1)  # 假钟真的被读了不止一次

        rows = {row["business_date"]: row for row in await self._rows(employee["id"])}
        # 删与铺用的是同一个营业日（9/23）：那一行是新规则重铺出来的，不是缺口。
        self.assertIn("2026-09-23", rows)
        self.assertEqual(rows["2026-09-23"]["shift_id"], night)
        self.assertEqual(rows["2026-09-23"]["source"], SOURCE_RULE)
        # 整段窗口一起挪：末日跟着起点走（12/22 是「按 9/24 算」那一版才有的行）。
        self.assertEqual((min(rows), max(rows)), ("2026-09-23", "2026-12-21"))
        self.assertEqual(len(rows), EXPANSION_DAYS)

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
                    # 票 08：那天在上班，`leave` 是「批了请假的没班」那个标记。
                    "leave": False,
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

    # ── 验收 5：请假申请（票 08） ────────────────────────────────────────

    async def test_a_pending_leave_request_touches_no_roster(self):
        """验收 1：员工能提一天或一段日期 —— 提的时候排班一个字不动。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])
        before = await self._full_rows(employee["id"])

        request = await self.store.submit_leave(
            employee["id"], "2026-09-28", "2026-09-29", "家里有事"
        )

        self.assertEqual(request["kind"], "leave")
        self.assertEqual(request["status"], "pending_manager")
        self.assertEqual(
            (
                request["start_date"],
                request["end_date"],
                request["note"],
                request["decided_at"],
            ),
            ("2026-09-28", "2026-09-29", "家里有事", None),
        )
        self.assertGreater(request["id"], 0)
        # 待批的申请不写排班：结果行快照（含 id）逐字节不变，也没有覆盖记录。
        self.assertEqual(await self._full_rows(employee["id"]), before)
        self.assertEqual(await self._overrides(), [])
        self.assertEqual(
            [row["status"] for row in await self._requests()], ["pending_manager"]
        )

    async def test_a_single_day_request_is_one_day_long(self):
        """验收 1 的前半句：「一天」就是 start = end，不是「从这天起一直请」。"""
        employee = await self._employee()

        request = await self.store.submit_leave(employee["id"], "2026-09-28")

        self.assertEqual(
            (request["start_date"], request["end_date"]),
            ("2026-09-28", "2026-09-28"),
        )

    async def test_a_leave_request_validates_its_dates_and_note(self):
        """四道闸（格式 / 顺序 / 过去 / 窗口外）+ 事由长度；被拒的申请不留半行。"""
        employee = await self._employee()
        cases = [
            (("",), "missing_day"),
            (("2026-9-28",), "invalid_business_date"),
            (("2026-09-29", "2026-09-28"), "bad_range"),
            (("2026-09-23",), "past_leave"),
            ((LAST_DAY, "2026-12-23"), "beyond_leave"),
        ]
        for args, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(SchedulingError) as caught:
                    await self.store.submit_leave(employee["id"], *args)
                self.assertEqual(caught.exception.code, code)
        # 窗口外那句话报**真实的窗口末日**（票 07 复核 P4 的口径），不写死 90 天。
        with self.assertRaises(SchedulingError) as beyond:
            await self.store.submit_leave(employee["id"], "2026-12-23")
        self.assertEqual(beyond.exception.args[0], LAST_DAY)
        with self.assertRaises(SchedulingError) as long_note:
            await self.store.submit_leave(employee["id"], "2026-09-28", note="家" * 51)
        self.assertEqual(long_note.exception.code, "note_too_long")
        with self.assertRaises(SchedulingError) as stranger:
            await self.store.submit_leave(987654, "2026-09-28")
        self.assertEqual(stranger.exception.code, "unknown_employee")
        self.assertEqual(await self._requests(), [])

        # 事由正好 50 字是收的；空白事由当没写（不给一句「 」的备注）。
        full = await self.store.submit_leave(employee["id"], "2026-09-28", note="家" * 50)
        self.assertEqual(len(full["note"]), 50)
        blank = await self.store.submit_leave(employee["id"], "2026-09-29", note="   ")
        self.assertIsNone(blank["note"])

    async def test_a_range_that_starts_in_the_past_can_still_be_submitted(self):
        """「过去请不了假」的基准是**末日**：一段从昨天跨到今天的申请提得了。

        批的时候只把今天以后那几天写进去，已经过去的那天进 `skipped_days`（票 07 的
        「过去不改」）。这条钉住 `submit_leave` 里那道闸的基准 —— 改成 `first < today`
        时既有用例全绿（它们都是单日，first == last），这段行为就没人守着。
        """
        employee = await self._employee()
        await self.store.set_rule(employee["id"], [await self._shift_id("白班")])

        request = await self.store.submit_leave(employee["id"], "2026-09-23", "2026-09-25")
        result = await self.store.approve_request(request["id"])

        self.assertEqual(result["applied_days"], ["2026-09-24", "2026-09-25"])
        self.assertEqual(result["skipped_days"], ["2026-09-23"])

    async def test_the_owner_can_take_back_a_request_that_is_not_decided(self):
        """验收 1 的后半句：撤回自己还没被批的申请；别人的、批过的都撤不动。"""
        owner = await self._employee("13800138001", "李四")
        other = await self._employee("13800138002", "王五")
        request = await self.store.submit_leave(owner["id"], "2026-09-28")

        # 别人的申请一律当作「不存在」：不告诉调用方「这条在，但不是你的」。
        with self.assertRaises(SchedulingError) as stranger:
            await self.store.cancel_request(other["id"], request["id"])
        self.assertEqual(stranger.exception.code, "unknown_request")

        cancelled = await self.store.cancel_request(owner["id"], request["id"])

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNotNone(cancelled["decided_at"])
        self.assertEqual([row["status"] for row in await self._requests()], ["cancelled"])
        # 状态机只往前走：撤回过的撤不了、也批不了。
        with self.assertRaises(SchedulingError) as again:
            await self.store.cancel_request(owner["id"], request["id"])
        self.assertEqual(again.exception.code, "request_not_pending")
        with self.assertRaises(SchedulingError) as late:
            await self.store.approve_request(request["id"])
        self.assertEqual(late.exception.code, "request_not_pending")
        with self.assertRaises(SchedulingError) as unknown:
            await self.store.approve_request(987654)
        self.assertEqual(unknown.exception.code, "unknown_request")

    async def test_the_inbox_previews_who_is_left_on_each_shift(self):
        """验收 2/3：待办列着等他批的请假，并摊开「批了以后每个班次还剩几个人」。"""
        leaver = await self._employee("13800138001", "李四")
        stayer = await self._employee("13800138002", "王五")
        await self._employee("13800138003", "赵六")  # 没配规则 → 待办里的新人
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(leaver["id"], [day])
        await self.store.set_rule(stayer["id"], [day])
        await self.store.submit_leave(leaver["id"], "2026-09-28", "2026-09-29", "家里有事")

        inbox = await self.store.inbox()

        self.assertEqual(inbox["today"], TODAY)
        self.assertEqual([person["name"] for person in inbox["without_rule"]], ["赵六"])
        self.assertEqual(len(inbox["requests"]), 1)
        card = inbox["requests"][0]
        self.assertEqual(
            (card["employee_name"], card["note"], card["start_date"], card["end_date"]),
            ("李四", "家里有事", "2026-09-28", "2026-09-29"),
        )
        self.assertEqual(
            [item["business_date"] for item in card["days"]],
            ["2026-09-28", "2026-09-29"],
        )
        for item in card["days"]:
            with self.subTest(day=item["business_date"]):
                self.assertEqual(
                    (item["scheduled"], item["past"], item["current_shift_name"]),
                    (True, False, "白班"),
                )
                # 那天本来只有白班有人：批了李四之后白班只剩王五；
                # 夜班那天一个人都没有，就不往卡上堆一行「0 人」。
                self.assertEqual(
                    item["after"],
                    [{"shift_id": day, "shift_name": "白班", "count": 1}],
                )
                self.assertNotIn(
                    night, [shift["shift_id"] for shift in item["after"]]
                )

    async def test_a_ruleless_person_can_ask_for_leave(self):
        """没配规则的人也能请假：那天本来就「还没排到」，批了照样记成请假。"""
        newcomer = await self._employee()
        request = await self.store.submit_leave(newcomer["id"], "2026-09-28")

        card = (await self.store.inbox())["requests"][0]
        self.assertEqual((card["days"][0]["scheduled"], card["days"][0]["after"]), (False, []))

        await self.store.approve_request(request["id"])

        detail = await self.store.day_detail("2026-09-28")
        self.assertEqual(
            [(person["name"], person["leave"]) for person in detail["off_people"]],
            [("张三", True)],
        )

    async def test_approving_a_leave_turns_those_days_into_leave(self):
        """验收 5：批了以后那几天变成请假，和本来就休「看得出区别」。"""
        leaver = await self._employee("13800138001", "李四")
        rester = await self._employee("13800138002", "王五")
        stayer = await self._employee("13800138003", "赵六")
        day = await self._shift_id("白班")
        await self.store.set_rule(leaver["id"], [day])
        await self.store.set_rule(rester["id"], [day, None])  # 9/25 本来就休
        await self.store.set_rule(stayer["id"], [day])
        request = await self.store.submit_leave(
            leaver["id"], "2026-09-25", "2026-09-26", "家里有事"
        )
        rester_before = await self._full_rows(rester["id"])

        approved = await self.store.approve_request(request["id"])

        self.assertEqual(
            (approved["status"], approved["applied_days"], approved["skipped_days"]),
            ("approved", ["2026-09-25", "2026-09-26"], []),
        )
        self.assertIsNotNone(approved["decided_at"])
        # 覆盖记录是「这天为什么没有班」的唯一判据：kind=leave、班次与区都是空。
        self.assertEqual(
            await self._overrides(),
            [
                {
                    "employee_id": leaver["id"],
                    "business_date": day_key,
                    "shift_id": None,
                    "zone_id": None,
                    "kind": "leave",
                }
                for day_key in ("2026-09-25", "2026-09-26")
            ],
        )
        # 结果行照旧走票 07 那条路：没有班次，来源是覆盖。
        self.assertEqual(
            [
                (row["shift_id"], row["source"])
                for row in await self._rows(leaver["id"])
                if row["business_date"] in ("2026-09-25", "2026-09-26")
            ],
            [(None, "override"), (None, "override")],
        )
        first = await self.store.day_detail("2026-09-25")
        self.assertEqual([person["name"] for person in first["groups"][0]["people"]], ["赵六"])
        self.assertEqual((first["total"], first["off_count"]), (1, 2))
        # 两位「这天没有班」的人：请假的 leave=True，本来就休的 leave=False。
        self.assertEqual(
            {person["name"]: (person["leave"], person["overridden"]) for person in first["off_people"]},
            {"李四": (True, True), "王五": (False, False)},
        )
        # 第二天王五本来就上班：请假只盖住申请的那几天，不碰别人、也不碰别的日子。
        second = await self.store.day_detail("2026-09-26")
        self.assertEqual(
            [person["name"] for person in second["groups"][0]["people"]], ["王五", "赵六"]
        )
        self.assertEqual(await self._full_rows(rester["id"]), rester_before)
        # 批过的不能再批。
        with self.assertRaises(SchedulingError) as again:
            await self.store.approve_request(request["id"])
        self.assertEqual(again.exception.code, "request_not_pending")
        # 员工页与员工月历上，那天是「请假」而不是「休」。
        mine = await self.store.my_days(leaver["id"])
        by_date = {item["business_date"]: item for item in mine["days"]}
        self.assertEqual(
            (
                by_date["2026-09-25"]["leave"],
                by_date["2026-09-25"]["shift_id"],
                by_date["2026-09-25"]["scheduled"],
            ),
            (True, None, True),
        )
        month = await self.store.my_month(leaver["id"], "2026-09")
        cell = {item["business_date"]: item for item in month["days"]}["2026-09-25"]
        self.assertTrue(cell["leave"])

    async def test_rejecting_a_leave_leaves_every_row_untouched(self):
        """验收 6：驳回后排班一个字不变。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])
        before = await self._full_rows(employee["id"])
        request = await self.store.submit_leave(employee["id"], "2026-09-28", "2026-09-29")

        rejected = await self.store.reject_request(request["id"])

        self.assertEqual(rejected["status"], "rejected")
        self.assertIsNotNone(rejected["decided_at"])
        self.assertEqual(await self._full_rows(employee["id"]), before)
        self.assertEqual(await self._overrides(), [])
        self.assertEqual([row["status"] for row in await self._requests()], ["rejected"])
        with self.assertRaises(SchedulingError) as late:
            await self.store.approve_request(request["id"])
        self.assertEqual(late.exception.code, "request_not_pending")

    async def test_the_owner_sees_where_each_request_stands(self):
        """验收 8：员工看得到自己每条申请到哪一步 —— 而且只看得到自己的。"""
        owner = await self._employee("13800138001", "李四")
        other = await self._employee("13800138002", "王五")
        first = await self.store.submit_leave(owner["id"], "2026-09-28")
        second = await self.store.submit_leave(owner["id"], "2026-09-29", "2026-09-30")
        await self.store.reject_request(second["id"])
        self.fixed_now = datetime(2026, 9, 25, 10, 0, tzinfo=CHINA_TZ)
        await self.store.approve_request(first["id"])

        mine = await self.store.my_requests(owner["id"])

        self.assertEqual(mine["today"], "2026-09-25")
        # 新的在前：后提的那条（已驳回）排在上面；时间戳是同一条时钟，靠 id 定序。
        self.assertEqual(
            [(row["id"], row["status"]) for row in mine["requests"]],
            [(second["id"], "rejected"), (first["id"], "approved")],
        )
        self.assertTrue(all(row["decided_at"] for row in mine["requests"]))
        self.assertEqual((await self.store.my_requests(other["id"]))["requests"], [])

        pending = await self.store.submit_leave(other["id"], "2026-10-01")

        self.assertEqual(pending["status"], "pending_manager")
        self.assertEqual(
            [
                (row["status"], row["decided_at"])
                for row in (await self.store.my_requests(other["id"]))["requests"]
            ],
            [("pending_manager", None)],
        )

    async def test_a_late_approval_does_not_rewrite_past_days(self):
        """批得晚了：过去那几天不重写（票 07 的「过去不改」），申请照样记成已批准。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])
        request = await self.store.submit_leave(employee["id"], TODAY, "2026-09-25")
        before = await self._full_rows(employee["id"])

        self.fixed_now = datetime(2026, 9, 26, 10, 0, tzinfo=CHINA_TZ)
        approved = await self.store.approve_request(request["id"])

        self.assertEqual(
            (approved["status"], approved["applied_days"], approved["skipped_days"]),
            ("approved", [], ["2026-09-24", "2026-09-25"]),
        )
        self.assertEqual(await self._full_rows(employee["id"]), before)
        self.assertEqual(await self._overrides(), [])
        # 那两天他是上班的：「批了」只记在申请上，不假装改过历史。
        detail = await self.store.day_detail("2026-09-25")
        self.assertEqual(
            [person["name"] for person in detail["groups"][0]["people"]], ["张三"]
        )

    async def test_leave_marks_fall_back_to_rest_when_overrides_are_missing(self):
        """0007 没应用时：请假的标记读不出来，当日分工与员工页照常（那天按「休」显示）。"""
        employee = await self._employee()
        request = await self.store.submit_leave(employee["id"], "2026-09-28")
        await self.store.approve_request(request["id"])

        await self.db._conn.execute(
            "ALTER TABLE scheduling_overrides RENAME TO scheduling_overrides_tmp"
        )
        await self.db._conn.commit()
        try:
            detail = await self.store.day_detail("2026-09-28")
            self.assertEqual(
                [(person["name"], person["leave"]) for person in detail["off_people"]],
                [("张三", False)],
            )
            mine = await self.store.my_days(employee["id"])
            self.assertFalse(any(item["leave"] for item in mine["days"]))
        finally:
            await self.db._conn.execute(
                "ALTER TABLE scheduling_overrides_tmp RENAME TO scheduling_overrides"
            )
            await self.db._conn.commit()

    # ── 验收 5：换班申请（票 09）────────────────────────────────────────
    #
    # 换班比请假多一道门：**先过对方**。所以下面既断言状态机（等对方 → 等店长 → 批），
    # 也断言「对方还没点的时候店长看不到这条」，还要断言批了以后两个人的班对调、
    # 责任区跟着各自的新班次走。

    async def _two_people(self):
        """两个人：张三白班、李四夜班（都从今天起每天）。"""
        first = await self._employee()
        second = await self._employee(phone="13800138001", name="李四")
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(first["id"], [day])
        await self.store.set_rule(second["id"], [night])
        return first, second, day, night

    async def test_a_swap_request_waits_for_the_other_side(self):
        """验收 1/3：提一条换班先落在「等对方」；对方没点之前店长待办里看不到。"""
        first, second, day, night = await self._two_people()
        before_first = await self._full_rows(first["id"])
        before_second = await self._full_rows(second["id"])

        request = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")

        self.assertEqual(
            (request["kind"], request["status"], request["start_date"], request["end_date"]),
            ("swap", "pending_peer", "2026-09-25", "2026-09-25"),
        )
        self.assertIsNone(request["decided_at"])
        self.assertEqual(await self._peer_of(request["id"]), second["id"])
        # 申请本身不碰排班：两个人的结果行一个字不变，覆盖表还是空的。
        self.assertEqual(await self._full_rows(first["id"]), before_first)
        self.assertEqual(await self._full_rows(second["id"]), before_second)
        self.assertEqual(await self._overrides(), [])
        # 验收 3：对方还没点，店长的待办里没有这条。
        self.assertEqual((await self.store.inbox())["requests"], [])

    async def test_the_other_side_agrees_and_then_the_manager_sees_it(self):
        """验收 4 的前半步：对方同意 → 进店长待办，卡上摊开两个人那天的班。"""
        first, second, day, night = await self._two_people()
        request = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")

        answered = await self.store.answer_swap(second["id"], request["id"], True)

        self.assertEqual(answered["status"], "pending_manager")
        cards = (await self.store.inbox())["requests"]
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(
            (card["kind"], card["employee_name"], card["peer_name"]),
            ("swap", "张三", "李四"),
        )
        self.assertEqual(
            [
                (
                    day_card["business_date"],
                    day_card["scheduled"],
                    day_card["current_shift_name"],
                    day_card["peer_scheduled"],
                    day_card["peer_shift_name"],
                )
                for day_card in card["days"]
            ],
            [("2026-09-25", True, "白班", True, "夜班")],
        )
        # 验收 7：申请人自己看得到这条卡在谁那里（对方点头之后就轮到店长）。
        mine = (await self.store.my_requests(first["id"]))["requests"][0]
        self.assertEqual((mine["status"], mine["peer_name"]), ("pending_manager", "李四"))

    async def test_the_other_side_sees_the_request_with_both_shifts(self):
        """验收 2/7：对方在手机上看到「谁、哪天、他那天什么班、我那天什么班」。"""
        first, second, day, night = await self._two_people()
        request = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")

        card = (await self.store.my_requests(second["id"]))["incoming"][0]

        self.assertEqual(
            (
                card["id"],
                card["employee_name"],
                card["their_scheduled"],
                card["their_shift_name"],
                card["my_scheduled"],
                card["my_shift_name"],
            ),
            (request["id"], "张三", True, "白班", True, "夜班"),
        )
        # 申请人自己那边没有「等我回应」；对方回过之后，那条也从「等我回应」里出去。
        self.assertEqual((await self.store.my_requests(first["id"]))["incoming"], [])
        await self.store.answer_swap(second["id"], request["id"], True)
        self.assertEqual((await self.store.my_requests(second["id"]))["incoming"], [])

    async def test_a_refusal_ends_it_without_the_manager(self):
        """验收 2：对方拒绝 → 这件事到此为止，店长看不到、排班一个字不改。"""
        first, second, day, night = await self._two_people()
        request = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
        before_first = await self._full_rows(first["id"])
        before_second = await self._full_rows(second["id"])

        refused = await self.store.answer_swap(second["id"], request["id"], False)

        self.assertEqual(refused["status"], "rejected")
        self.assertIsNotNone(refused["decided_at"])
        self.assertEqual((await self.store.inbox())["requests"], [])
        self.assertEqual(await self._full_rows(first["id"]), before_first)
        self.assertEqual(await self._full_rows(second["id"]), before_second)
        self.assertEqual(await self._overrides(), [])
        # 回过的不能再回；不是问我的当作不存在（申请人自己去「同意」也不行）。
        with self.assertRaises(SchedulingError) as again:
            await self.store.answer_swap(second["id"], request["id"], True)
        self.assertEqual(again.exception.code, "request_not_pending")
        with self.assertRaises(SchedulingError) as stranger:
            await self.store.answer_swap(first["id"], request["id"], True)
        self.assertEqual(stranger.exception.code, "unknown_request")
        # 店长也批不动它：它从来没进过待批那一档。
        with self.assertRaises(SchedulingError) as denied:
            await self.store.approve_request(request["id"])
        self.assertEqual(denied.exception.code, "request_not_pending")

    async def test_approval_swaps_the_two_shifts_and_the_zones_follow(self):
        """验收 4/5：批了两个人那天的班对调，责任区跟着各自的**新**班次走。"""
        first, second, day, night = await self._two_people()
        board = await self._zone("案板")
        cold = await self._zone("凉菜")
        pastry = await self._zone("面点")
        # 每人两个班次各配一个区：换完之后取的是新班次那个区，不是原来那个。
        await self.store.set_zone_default(first["id"], day, board)
        await self.store.set_zone_default(first["id"], night, cold)
        await self.store.set_zone_default(second["id"], day, pastry)
        await self.store.set_zone_default(second["id"], night, board)
        await self.store.set_rule(first["id"], [day])
        await self.store.set_rule(second["id"], [night])

        request = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
        await self.store.answer_swap(second["id"], request["id"], True)
        approved = await self.store.approve_request(request["id"])

        self.assertEqual(
            (approved["status"], approved["applied_days"], approved["skipped_days"]),
            ("approved", ["2026-09-25"], []),
        )
        mine = {row["business_date"]: row for row in await self._rows(first["id"])}
        theirs = {row["business_date"]: row for row in await self._rows(second["id"])}
        self.assertEqual(
            (mine["2026-09-25"]["shift_id"], mine["2026-09-25"]["source"]), (night, "override")
        )
        self.assertEqual(
            (theirs["2026-09-25"]["shift_id"], theirs["2026-09-25"]["source"]),
            (day, "override"),
        )
        # 换的是那一天：前后两天照旧。
        self.assertEqual((mine["2026-09-24"]["shift_id"], mine["2026-09-26"]["shift_id"]), (day, day))
        self.assertEqual(
            (theirs["2026-09-24"]["shift_id"], theirs["2026-09-26"]["shift_id"]), (night, night)
        )
        # 覆盖记的是 `swap`：跟店长手改（`manual`）、请假（`leave`）在同一张表里分得开。
        self.assertEqual(
            await self._overrides(),
            [
                {
                    "employee_id": first["id"],
                    "business_date": "2026-09-25",
                    "shift_id": night,
                    "zone_id": cold,
                    "kind": "swap",
                },
                {
                    "employee_id": second["id"],
                    "business_date": "2026-09-25",
                    "shift_id": day,
                    "zone_id": pastry,
                    "kind": "swap",
                },
            ],
        )
        zones_mine = await self._zone_ids(first["id"])
        zones_theirs = await self._zone_ids(second["id"])
        self.assertEqual((zones_mine["2026-09-25"], zones_mine["2026-09-24"]), (cold, board))
        self.assertEqual(
            (zones_theirs["2026-09-25"], zones_theirs["2026-09-24"]), (pastry, board)
        )
        # 当日分工也跟着换：那天白班上的是李四（在他白班那个区里）。
        self.assertEqual(
            [
                (person["name"], person["zone"])
                for person in (await self.store.day_detail("2026-09-25"))["groups"][0]["people"]
            ],
            [("李四", "面点")],
        )

    async def test_a_swap_with_someone_who_is_off_that_day(self):
        """对方那天本来就休：批了他接走这个班，提的人那天空出来（这也是一种换班）。"""
        first = await self._employee()
        second = await self._employee(phone="13800138001", name="李四")
        day = await self._shift_id("白班")
        await self.store.set_rule(first["id"], [day])
        await self.store.set_rule(second["id"], [None])  # 李四天天休
        pastry = await self._zone("面点")
        await self.store.set_zone_default(second["id"], day, pastry)

        request = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
        await self.store.answer_swap(second["id"], request["id"], True)
        await self.store.approve_request(request["id"])

        mine = {row["business_date"]: row for row in await self._rows(first["id"])}
        theirs = {row["business_date"]: row for row in await self._rows(second["id"])}
        self.assertEqual(
            (mine["2026-09-25"]["shift_id"], mine["2026-09-25"]["source"]), (None, "override")
        )
        self.assertEqual(
            (theirs["2026-09-25"]["shift_id"], theirs["2026-09-25"]["source"]),
            (day, "override"),
        )
        # 李四接的是白班 → 区按他白班的固定区（他接过来的班次），不是「休」那个空值。
        self.assertEqual((await self._zone_ids(second["id"]))["2026-09-25"], pastry)
        detail = await self.store.day_detail("2026-09-25")
        self.assertEqual([person["name"] for person in detail["groups"][0]["people"]], ["李四"])

    async def test_a_swap_validates_its_people_and_its_day(self):
        """每道闸各说各的：跟自己换、缺人、人不在了、没选日期、过去、太远、那天没班。"""
        first, second, day, night = await self._two_people()
        stranger = await self._employee(phone="13800138009", name="赵六")
        pending = await self.accounts.register("13800138008", PASSWORD, "钱七")

        cases = [
            ((first["id"], first["id"], "2026-09-25"), "swap_with_self"),
            ((first["id"], None, "2026-09-25"), "missing_peer"),
            ((first["id"], 987654, "2026-09-25"), "unknown_peer"),
            ((first["id"], pending["id"], "2026-09-25"), "peer_unavailable"),
            ((first["id"], second["id"], ""), "missing_swap_day"),
            ((first["id"], second["id"], "2026-9-25"), "invalid_business_date"),
            ((first["id"], second["id"], "2026-09-23"), "past_swap"),
            ((stranger["id"], first["id"], "2026-09-25"), "nothing_to_swap"),
        ]
        for args, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(SchedulingError) as caught:
                    await self.store.submit_swap(*args)
                self.assertEqual(caught.exception.code, code)
        # 窗口末日之后：报的是真实的末日（不把 90 天写死进文案）。
        with self.assertRaises(SchedulingError) as far:
            await self.store.submit_swap(first["id"], second["id"], "2026-12-23")
        self.assertEqual((far.exception.code, far.exception.args[0]), ("beyond_swap", LAST_DAY))
        # 事由跟请假共用一条上限。
        with self.assertRaises(SchedulingError) as long_note:
            await self.store.submit_swap(
                first["id"], second["id"], "2026-09-26", "长" * (MAX_REQUEST_NOTE + 1)
            )
        self.assertEqual(long_note.exception.code, "note_too_long")
        # 被挡下来的都没落库。
        self.assertEqual(await self._requests(), [])

    async def test_asking_the_same_thing_twice_is_refused(self):
        """同一天同一个人只挂一条：对方手机上不该出现两条一样的请求。"""
        first, second, day, night = await self._two_people()
        other = await self._employee(phone="13800138002", name="王五")
        await self.store.set_rule(other["id"], [night])
        request = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")

        with self.assertRaises(SchedulingError) as caught:
            await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
        self.assertEqual(caught.exception.code, "already_asked")
        # 换一天、换个人都还能提（挡的是「同时挂着两条一样的」）。
        self.assertEqual(
            (await self.store.submit_swap(first["id"], other["id"], "2026-09-25"))["status"],
            "pending_peer",
        )
        self.assertEqual(
            (await self.store.submit_swap(first["id"], second["id"], "2026-09-26"))["status"],
            "pending_peer",
        )
        # 撤掉之后可以再提同一条：挡的是「同时挂着」，不是「这辈子只能提一次」。
        await self.store.cancel_request(first["id"], request["id"])
        self.assertEqual(
            (await self.store.submit_swap(first["id"], second["id"], "2026-09-25"))["status"],
            "pending_peer",
        )

    async def test_asking_again_is_refused_while_the_first_one_is_still_queued(self):
        """对方点头之后那条还挂在店长那儿：再提一条同人同日，店长会看到两张同一天的卡。"""
        first, second, day, night = await self._two_people()
        request = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
        await self.store.answer_swap(second["id"], request["id"], True)

        with self.assertRaises(SchedulingError) as caught:
            await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
        self.assertEqual(caught.exception.code, "already_asked")
        # 撤掉之后同一对同一天可以再提：挡的是「还挂着」，不是「提过」。
        await self.store.cancel_request(first["id"], request["id"])
        self.assertEqual(
            (await self.store.submit_swap(first["id"], second["id"], "2026-09-25"))["status"],
            "pending_peer",
        )

    async def test_the_database_is_the_last_gate_against_a_double_submit(self):
        """那次扫描在写锁外（`submit_swap` 只有 `@_needs_migration`）：把它挡掉，
        同一瞬间挤进来的第二条必须被 0009 的局部唯一索引拦下，而且报同一句人话。"""
        first, second, day, night = await self._two_people()
        await self.store.submit_swap(first["id"], second["id"], "2026-09-25")

        async def nothing(self, **_kwargs):
            return []

        with mock.patch.object(SchedulingStore, "_request_rows", nothing):
            with self.assertRaises(SchedulingError) as caught:
                await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
        self.assertEqual(caught.exception.code, "already_asked")

    async def test_the_scan_stands_on_its_own_when_the_index_is_missing(self):
        """0009 只应用了一半（列在、索引没建）：落库那道闸不在时，服务层那次扫描
        就是唯一的闸 —— 得自己站得住，不能全靠索引兜。"""
        first, second, _day, _night = await self._two_people()
        await self.db._conn.execute("DROP INDEX IF EXISTS idx_scheduling_requests_swap_once")
        await self.db._conn.commit()
        try:
            await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
            with self.assertRaises(SchedulingError) as caught:
                await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
            self.assertEqual(caught.exception.code, "already_asked")

            # 对方点头之后那条还挂在店长那儿：索引不在时这一条只能靠扫描挡。
            request = (await self.store.my_requests(first["id"]))["requests"][0]
            await self.store.answer_swap(second["id"], request["id"], True)
            with self.assertRaises(SchedulingError) as caught:
                await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
            self.assertEqual(caught.exception.code, "already_asked")
        finally:
            # 补回来的定义要与 0009 逐字相同：后面的用例还靠这条索引当最后一道闸。
            await self.db._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduling_requests_swap_once "
                "ON scheduling_requests (employee_id, peer_employee_id, start_date) "
                "WHERE kind = 'swap' AND status IN ('pending_peer', 'pending_manager')"
            )
            await self.db._conn.commit()

    async def test_the_owner_can_take_back_a_swap_at_either_step(self):
        """验收 6：申请人撤回 —— 对方点头前后都能撤，撤回后别人排班一个字不变。"""
        first, second, day, night = await self._two_people()
        before_first = await self._full_rows(first["id"])
        before_second = await self._full_rows(second["id"])

        early = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
        self.assertEqual(
            (await self.store.cancel_request(first["id"], early["id"]))["status"], "cancelled"
        )
        # 撤了之后对方那边看不到、也回不了。
        self.assertEqual((await self.store.my_requests(second["id"]))["incoming"], [])
        with self.assertRaises(SchedulingError) as late:
            await self.store.answer_swap(second["id"], early["id"], True)
        self.assertEqual(late.exception.code, "request_not_pending")

        # 对方已经点头、还在等店长的时候也能撤：撤回只动申请这一行。
        later = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
        await self.store.answer_swap(second["id"], later["id"], True)
        self.assertEqual(
            (await self.store.cancel_request(first["id"], later["id"]))["status"], "cancelled"
        )
        self.assertEqual((await self.store.inbox())["requests"], [])
        self.assertEqual(await self._full_rows(first["id"]), before_first)
        self.assertEqual(await self._full_rows(second["id"]), before_second)
        self.assertEqual(await self._overrides(), [])

    async def test_a_late_swap_approval_does_not_rewrite_the_past(self):
        """批得晚了：那天已经过去，两个人的班都不动，申请照样记成已批准。"""
        first, second, day, night = await self._two_people()
        request = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
        await self.store.answer_swap(second["id"], request["id"], True)
        before_first = await self._full_rows(first["id"])
        before_second = await self._full_rows(second["id"])

        self.fixed_now = datetime(2026, 9, 26, 10, 0, tzinfo=CHINA_TZ)
        approved = await self.store.approve_request(request["id"])

        self.assertEqual(
            (approved["status"], approved["applied_days"], approved["skipped_days"]),
            ("approved", [], ["2026-09-25"]),
        )
        self.assertEqual(await self._full_rows(first["id"]), before_first)
        self.assertEqual(await self._full_rows(second["id"]), before_second)
        self.assertEqual(await self._overrides(), [])
        # 那天他还是白班（过去怎么排就怎么留着）。
        self.assertEqual(
            [row["shift_id"] for row in await self._rows(first["id"]) if row["business_date"] == "2026-09-25"],
            [day],
        )

    async def test_colleagues_lists_only_the_people_at_work(self):
        """换班名单：不给自己、不给停用的、不给还没批准的，也不给手机号。"""
        first, second, day, night = await self._two_people()
        disabled = await self._employee(phone="13800138003", name="王五")
        await self.accounts.disable(disabled["id"])
        await self.accounts.register("13800138004", PASSWORD, "钱七")

        people = await self.store.colleagues(first["id"])

        self.assertEqual([person["name"] for person in people], ["李四"])
        self.assertEqual(set(people[0]), {"id", "name", "job_title"})

    async def test_swap_reads_fall_back_when_the_peer_column_is_missing(self):
        """0009 没应用：请假那几条照常读（「等我回应」当作空），再提换班明说去应用它。"""
        first, second, day, night = await self._two_people()
        request = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
        await self.store.answer_swap(second["id"], request["id"], True)

        await self.db._conn.execute(
            "ALTER TABLE scheduling_requests DROP COLUMN peer_employee_id"
        )
        await self.db._conn.commit()
        try:
            mine = await self.store.my_requests(first["id"])
            self.assertEqual([row["status"] for row in mine["requests"]], ["pending_manager"])
            self.assertIsNone(mine["requests"][0]["peer_employee_id"])
            self.assertEqual(mine["incoming"], [])
            # 请假不靠这一列：0009 缺着也能照常提、照常读（换班才非得有它）。
            leave = await self.store.submit_leave(first["id"], "2026-09-26", note="家里有事")
            self.assertEqual((leave["kind"], leave["status"]), ("leave", "pending_manager"))
            self.assertEqual(
                [row["id"] for row in (await self.store.my_requests(first["id"]))["requests"]],
                [leave["id"], request["id"]],
            )
            # 写的那条路仍然要明说缺哪一支迁移（不是 500，也不是悄悄成功）。
            with self.assertRaises(SchedulingError) as caught:
                await self.store.submit_swap(first["id"], second["id"], "2026-09-26")
            self.assertEqual(caught.exception.code, "not_migrated")
            # 已经排上队（对方已同意）的那条：渲染不出来，也不能批 —— 报缺哪一支迁移。
            # 不许印出一句「对方那天休」的假话，更不许在 `int(None)` 上抛成清不掉的 500。
            with self.assertRaises(SchedulingError) as caught:
                await self.store.inbox()
            self.assertEqual(caught.exception.code, "not_migrated")
            with self.assertRaises(SchedulingError) as caught:
                await self.store.approve_request(request["id"])
            self.assertEqual(caught.exception.code, "not_migrated")
            # 批准没落地：两条都还挂在「等店长」，迁移补回来就能照常批。
            self.assertEqual(
                {
                    row["id"]: row["status"]
                    for row in (await self.store.my_requests(first["id"]))["requests"]
                },
                {leave["id"]: "pending_manager", request["id"]: "pending_manager"},
            )
        finally:
            # 索引也要补回来：`DROP COLUMN` 把它们一起带走了（补列不补索引 = 少一道闸）。
            for statement in (
                "ALTER TABLE scheduling_requests ADD COLUMN IF NOT EXISTS peer_employee_id BIGINT",
                "CREATE INDEX IF NOT EXISTS idx_scheduling_requests_peer "
                "ON scheduling_requests (peer_employee_id, status)",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduling_requests_swap_once "
                "ON scheduling_requests (employee_id, peer_employee_id, start_date) "
                "WHERE kind = 'swap' AND status IN ('pending_peer', 'pending_manager')",
            ):
                await self.db._conn.execute(statement)
            await self.db._conn.commit()

    async def test_a_swap_that_lost_its_peer_is_skipped_not_fatal(self):
        """列在、这一行的对方却是空的（0009 被撤掉又补回来，`DROP COLUMN` 把值带走了）：
        那一行摆不出来、也批不了 —— 跳过它，别让一张残骸把整页待办拖成 503
        （同一队列里别人的请假还等着批）。"""
        first, second, _day, _night = await self._two_people()
        request = await self.store.submit_swap(first["id"], second["id"], "2026-09-25")
        await self.store.answer_swap(second["id"], request["id"], True)
        leave = await self.store.submit_leave(second["id"], "2026-09-26", note="家里有事")
        await self.db._conn.execute(
            "UPDATE scheduling_requests SET peer_employee_id = NULL WHERE id = ?",
            (request["id"],),
        )
        await self.db._conn.commit()

        cards = (await self.store.inbox())["requests"]
        self.assertEqual([card["id"] for card in cards], [leave["id"]])

        # 直接批那一行仍旧明说是哪一支迁移的事：绝不拿 NULL 当「对方」写进排班。
        with self.assertRaises(SchedulingError) as caught:
            await self.store.approve_request(request["id"])
        self.assertEqual(caught.exception.code, "not_migrated")
        self.assertEqual(
            {
                row["id"]: row["status"]
                for row in await self._requests()
                if row["kind"] == "swap"
            },
            {request["id"]: "pending_manager"},
        )

    async def test_prepare_keeps_renamed_shifts(self):
        await self.db._conn.execute("UPDATE staff_shifts SET name = '早班' WHERE name = '白班'")
        await self.db._conn.commit()

        await self.store.prepare()

        names = [shift["name"] for shift in await self.store.list_shifts()]
        self.assertEqual(names, ["早班", "夜班"])

    # ── 班次表的编辑（票 11）────────────────────────────────────────────

    async def _past_row(self, employee_id, day, shift_id):
        """手写一行「历史」：展开窗口从今天起，跑不出过去的日子。

        停用/改名那几条验收说的是「**已经写下的**行照旧显示」，所以这里直接落一行 ——
        它跟三个月前展开出来的那行在数据上长得一样（`source='rule'`）。
        """
        stamp = self.fixed_now.isoformat()
        await self.db._conn.execute(
            """INSERT INTO staff_assignments
                   (employee_id, business_date, shift_id, zone_id, source, created_at, updated_at)
               VALUES (?, ?, ?, NULL, 'rule', ?, ?)""",
            (employee_id, day, shift_id, stamp, stamp),
        )
        await self.db._conn.commit()

    async def test_renaming_a_shift_does_not_touch_a_single_row(self):
        """验收 1：改名字安全 —— 别处引用的都是 id（`staff_assignments.shift_id` 没有外键）。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])
        before = await self._full_rows(employee["id"])

        renamed = await self.store.update_shift(day, name="早班")

        self.assertEqual(
            (renamed["id"], renamed["name"], renamed["sort_order"], renamed["is_active"]),
            (day, "早班", 10, True),
        )
        self.assertEqual(await self._full_rows(employee["id"]), before)
        self.assertEqual((await self.store.my_days(employee["id"]))["days"][0]["shift_name"], "早班")
        # 月历与当天名单按 id 翻译名字：历史行跟着新名字显示，不用回填一行数据。
        calendar = await self.store.month_calendar("2026-09")
        self.assertEqual([shift["name"] for shift in calendar["shifts"]], ["早班", "夜班"])

    async def test_shift_names_are_validated_with_readable_reasons(self):
        await self._employee()
        day = await self._shift_id("白班")
        middle = await self.store.create_shift("中班")
        self.assertEqual((middle["name"], middle["sort_order"], middle["is_active"]), ("中班", 30, True))
        # 显式给排序位也认（新班次插在两条中间）。
        self.assertEqual((await self.store.create_shift("打烊班", sort_order=25))["sort_order"], 25)

        for payload in ("", "   ", None):
            with self.assertRaises(SchedulingError) as caught:
                await self.store.create_shift(payload)
            self.assertEqual(caught.exception.code, "missing_shift_name")
        with self.assertRaises(SchedulingError) as caught:
            await self.store.create_shift("早班早班早班早班早班早班早")
        self.assertEqual((caught.exception.code, caught.exception.args[0]), ("shift_name_too_long", "12"))
        # 两头的空白不算名字的一部分：`" 中班 "` 跟已有的「中班」是同一条。
        with self.assertRaises(SchedulingError) as caught:
            await self.store.create_shift(" 中班 ")
        self.assertEqual(caught.exception.code, "shift_name_taken")
        with self.assertRaises(SchedulingError) as caught:
            await self.store.update_shift(day, name="中班")
        self.assertEqual(caught.exception.code, "shift_name_taken")
        # 改成自己原来的名字不算重名（否则「只调顺序」也会被自己拦下来）。
        self.assertEqual((await self.store.update_shift(day, name="白班"))["name"], "白班")
        with self.assertRaises(SchedulingError) as caught:
            await self.store.update_shift(day, sort_order="靠前一点")
        self.assertEqual(caught.exception.code, "invalid_shift_order")
        for call in (lambda: self.store.update_shift(987654, name="没有这个班次"),
                     lambda: self.store.delete_shift(987654)):
            with self.assertRaises(SchedulingError) as caught:
                await call()
            self.assertEqual(caught.exception.code, "unknown_shift_id")
        # 停用的班次照样能改（改名、重新启用）—— 「不存在」与「已停用」是两件事。
        await self.store.update_shift(middle["id"], is_active=False)
        self.assertEqual((await self.store.update_shift(middle["id"], name="晚班"))["name"], "晚班")

    async def test_reordering_is_the_whole_table_at_once(self):
        """验收 1：调显示顺序 —— 一次给全，落成 10、20、30…（不留半个状态）。"""
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        middle = await self.store.create_shift("中班")

        ordered = await self.store.reorder_shifts([middle["id"], night, day])

        self.assertEqual(
            [(shift["name"], shift["sort_order"]) for shift in ordered],
            [("中班", 10), ("夜班", 20), ("白班", 30)],
        )
        self.assertEqual(
            [shift["name"] for shift in await self.store.list_shifts()],
            ["中班", "夜班", "白班"],
        )
        # 少给、多给、给重、给个不存在的：一律「刷新一下再调」，不猜剩下的排哪儿。
        for payload in ([day, night], [day, night, middle["id"], 999], [day, day, night],
                        ["白班"], "白班", ["白班", "夜班", "中班"]):
            with self.assertRaises(SchedulingError) as caught:
                await self.store.reorder_shifts(payload)
            self.assertEqual(caught.exception.code, "shift_order_mismatch")
        # 顺序没被那几次失败的调用改掉。
        self.assertEqual(
            [shift["name"] for shift in await self.store.list_shifts()],
            ["中班", "夜班", "白班"],
        )

    async def test_disabling_a_shift_needs_nobody_on_it(self):
        """验收 2 + 3：还有人排着它就不给停用（并说清人数）；停用后配规则挑不到它。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [day])

        with self.assertRaises(SchedulingError) as caught:
            await self.store.update_shift(day, is_active=False)
        self.assertEqual((caught.exception.code, caught.exception.args[0]), ("shift_in_use", "1"))
        self.assertTrue((await self.store.list_shifts(include_inactive=True))[0]["is_active"])

        # 把规则换掉（新排班的来源）之后白班没人上了 —— 这时才给停用。
        await self.store.set_rule(employee["id"], [night])
        stopped = await self.store.update_shift(day, is_active=False)
        self.assertFalse(stopped["is_active"])
        self.assertEqual([shift["name"] for shift in await self.store.list_shifts()], ["夜班"])
        # 编辑页要能看见它（给它改名、重新启用），配规则却挑不到它。
        self.assertEqual(
            [shift["name"] for shift in await self.store.list_shifts(include_inactive=True)],
            ["白班", "夜班"],
        )
        with self.assertRaises(SchedulingError) as caught:
            await self.store.set_rule(employee["id"], [day])
        self.assertEqual(caught.exception.code, "unknown_shift_in_cycle")

    async def test_a_stopped_shift_still_shows_the_rows_already_written(self):
        """验收 2：停用**不等于**把历史抹掉 —— 已经写下的行照旧显示那个班次。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [night])
        await self._past_row(employee["id"], "2026-09-23", day)

        await self.store.update_shift(day, is_active=False)

        calendar = await self.store.month_calendar("2026-09")
        # 启用的排前面，停用但这段里有行的跟在后面（`_shifts_for_display` 的老口径）。
        self.assertEqual([shift["name"] for shift in calendar["shifts"]], ["夜班", "白班"])
        cells = {item["business_date"]: item for item in calendar["days"]}
        self.assertEqual(cells["2026-09-23"]["counts"][str(day)], 1)
        # 今天往后白班一个人都没有：停用后新排班不再用它（规则一展开也不再写回来）；
        # 唯一一行白班还是 9/23 那行历史，没被谁拿掉。
        self.assertEqual(cells[TODAY]["counts"][str(day)], 0)
        rows = await self._rows(employee["id"])
        self.assertEqual(
            [
                row["shift_id"] for row in rows
                if row["business_date"] >= TODAY and row["shift_id"] == day
            ],
            [],
        )
        self.assertEqual(
            [(row["business_date"], row["shift_id"]) for row in rows if row["shift_id"] == day],
            [("2026-09-23", day)],
        )
        groups = {
            group["shift"]["name"]: group
            for group in (await self.store.day_detail("2026-09-23"))["groups"]
        }
        self.assertEqual([person["name"] for person in groups["白班"]["people"]], ["张三"])
        # 一个从来没排过班的停用班次不白占一列（「带上它」的条件是那个范围里真有行）。
        middle = await self.store.create_shift("中班")
        await self.store.update_shift(middle["id"], is_active=False)
        self.assertEqual(
            [shift["name"] for shift in (await self.store.month_calendar("2026-09"))["shifts"]],
            ["夜班", "白班"],
        )

    async def test_reactivating_a_shift_keeps_the_rows_it_already_had(self):
        """验收 6：停用再启用，之前的排班不受影响（一行都没被谁动过）。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [night])
        # 这天他本来上夜班，店长把**那一天**改成白班（单日覆盖）：白班于是有了一行，
        # 但没有谁的**轮转**里排着它，所以停得掉 —— 停用看的是「还有人在上」。
        await self.store.set_override(employee["id"], "2026-09-25", shift_id=day)
        before = await self._full_rows(employee["id"])

        await self.store.update_shift(day, is_active=False)
        # 停用期间：那天的覆盖照旧显示（它是已经写下的行），但月历图例里不出白班 ——
        # 白班在 9 月确实还有一行（9/25 的覆盖），所以它照样带上。
        during = await self.store.day_detail("2026-09-25")
        self.assertEqual(
            [group["shift"]["name"] for group in during["groups"] if group["count"]],
            ["白班"],
        )

        back = await self.store.update_shift(day, is_active=True)

        self.assertTrue(back["is_active"])
        self.assertEqual(await self._full_rows(employee["id"]), before)
        self.assertEqual(
            [shift["name"] for shift in await self.store.list_shifts()], ["白班", "夜班"]
        )

    async def test_used_shifts_cannot_be_deleted(self):
        """验收 5：排过班的删不掉（那句错话里说清为什么）；刚建错的才删得掉。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        mistake = await self.store.create_shift("中班")

        gone = await self.store.delete_shift(mistake["id"])
        self.assertEqual(gone["name"], "中班")
        self.assertEqual(
            [shift["name"] for shift in await self.store.list_shifts()], ["白班", "夜班"]
        )

        await self.store.set_rule(employee["id"], [day])
        with self.assertRaises(SchedulingError) as caught:
            await self.store.delete_shift(day)
        self.assertEqual(caught.exception.code, "shift_used")
        self.assertIn(f"已经排过 {EXPANSION_DAYS} 天班", caught.exception.args[0])
        # 那条规则还在（拒绝 = 什么都没做）。
        self.assertEqual(
            [row["shift_id"] for row in await self._rows(employee["id"])][:1], [day]
        )
        # 只在别人的轮转里、一天班都还没排的，也是「用过」：理由说的是人不是天数。
        idle = await self._employee("13800138001", "李四")
        stamp = self.fixed_now.isoformat()
        await self.db._conn.execute(
            """INSERT INTO scheduling_rules (employee_id, cycle, anchor_date, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (idle["id"], f"[{night}]", TODAY, stamp, stamp),
        )
        await self.db._conn.commit()
        with self.assertRaises(SchedulingError) as caught:
            await self.store.delete_shift(night)
        self.assertEqual(caught.exception.code, "shift_used")
        self.assertIn("还有 1 个人的轮转里排着它", caught.exception.args[0])

    async def test_the_last_usable_shift_cannot_be_removed(self):
        """停用或删掉最后一个**在用**的班次都拦下来：不然谁都没班可排。"""
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.update_shift(day, is_active=False)
        await self.store.delete_shift(day)

        with self.assertRaises(SchedulingError) as caught:
            await self.store.update_shift(night, is_active=False)
        self.assertEqual(caught.exception.code, "last_active_shift")
        with self.assertRaises(SchedulingError) as caught:
            await self.store.delete_shift(night)
        self.assertEqual(caught.exception.code, "last_active_shift")
        # 那次拒绝什么都没删掉。
        self.assertEqual(
            [shift["name"] for shift in await self.store.list_shifts()], ["夜班"]
        )

    async def test_deleting_the_only_active_shift_is_refused_even_if_others_are_stopped(self):
        """两道闸得同一个口径：先停一条、再删掉唯一在用的那条，也拦得住。

        删的那条自己「没人用过」，但删完就一条在用的都不剩了 —— 数「一共还剩几条」
        会放它过去（真出现过：删完 `/shifts` 是空的，配规则直接报没有可用班次）。
        """
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.update_shift(night, is_active=False)  # 白班是唯一在用的

        with self.assertRaises(SchedulingError) as caught:
            await self.store.delete_shift(day)
        self.assertEqual(caught.exception.code, "last_active_shift")
        self.assertEqual(
            [(shift["name"], shift["is_active"]) for shift in await self.store.list_shifts(include_inactive=True)],
            [("白班", True), ("夜班", False)],
        )
        # 停用的那条删得掉（删它不影响「还有没有人能排班」）。
        self.assertEqual((await self.store.delete_shift(night))["name"], "夜班")
        self.assertEqual([shift["name"] for shift in await self.store.list_shifts()], ["白班"])

    async def test_a_shift_cannot_be_stopped_while_a_rule_is_being_written(self):
        """配规则与停用赛跑：不许出现「班次已停用、轮转里还排着它」。

        交错点要卡准：**对方已经在写锁里、规则还没写下去**的那一刻，锁外读到的用量还是
        0 —— 停用闸如果不在锁里就会放行，接着规则也写成，坏状态成立。所以这里把
        `_write_rule_row` 按住（Event），等停用那边把「读用量 - 判」跑完再放它继续。

        同时起飞（`asyncio.gather`）测不出这件事：`update_shift` 常常先抢到锁、把班次
        停掉，`set_rule` 随即在锁里复验拒绝，规则根本没写下去 —— 坏状态没机会成形
        （复核实测：把停用闸整段搬出写锁，gather 那版用例照样全绿）。
        """
        employee = await self._employee()
        await self.store.create_shift("中班")
        middle = [shift for shift in await self.store.list_shifts() if shift["name"] == "中班"][0]

        hold = asyncio.Event()
        real_write_rule = SchedulingStore._write_rule_row

        async def held_write_rule(self, employee_id, cycle, anchor_date):
            # 停在「锁已经拿到、规则还没写」这一刻。
            await hold.wait()
            return await real_write_rule(self, employee_id, cycle, anchor_date)

        with mock.patch.object(SchedulingStore, "_write_rule_row", held_write_rule):
            rule_task = asyncio.create_task(self.store.set_rule(employee["id"], [middle["id"]]))
            await asyncio.sleep(0.05)  # set_rule 已经拿着写锁、停在写规则那一步
            stop_task = asyncio.create_task(
                self.store.update_shift(middle["id"], is_active=False)
            )
            await asyncio.sleep(0.05)  # 让停用那边把它的「读用量 - 判」跑完
            hold.set()
            results = await asyncio.gather(rule_task, stop_task, return_exceptions=True)

        # 两种交错都合法，但**只能成一件**：要么规则写不进去（班次先被停用），
        # 要么停用被拦住（规则已经排着它）。两件都成就是坏状态。
        done = [result for result in results if not isinstance(result, Exception)]
        refused = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(done), 1, f"两件都成了：{results}")
        self.assertEqual(len(refused), 1, f"两件都被拒：{results}")
        self.assertIn(refused[0].code, {"unknown_shift_in_cycle", "shift_in_use"})

        stopped = [
            shift for shift in await self.store.list_shifts(include_inactive=True)
            if shift["id"] == middle["id"]
        ][0]
        rules = await self.store.list_rules()
        in_rule = middle["id"] in rules.get(employee["id"], {}).get("cycle", [])
        self.assertFalse(
            not stopped["is_active"] and in_rule,
            f"坏状态：中班已停用，规则里还排着它（结果 {results}）",
        )

    async def test_shift_names_cover_the_boundary_and_the_reserved_words(self):
        """恰好到上限的名字要收（前端 `maxlength` 就让它敲得出来），保留词一律不收。"""
        day = await self._shift_id("白班")

        # 12 个字：收（上限就是 12）；13 个字：不收。
        self.assertEqual((await self.store.create_shift("一二三四五六七八九十一二"))["name"], "一二三四五六七八九十一二")
        with self.assertRaises(SchedulingError) as caught:
            await self.store.create_shift("一二三四五六七八九十一二三")
        self.assertEqual((caught.exception.code, caught.exception.args[0]), ("shift_name_too_long", "12"))
        # 改名同一道闸：恰好 12 个字也收。
        renamed = await self.store.update_shift(day, name="早班早班早班早班早班早班")
        self.assertEqual(len(renamed["name"]), 12)

        # 「休」这一族留给「那天休息」（月历的周期编辑先认班次名、认不出才当休息）：
        # 建与改都不许用，且把那两个词回给接口层，好让文案点名。
        for word in ("休", "休息", "空", "x", "-"):
            with self.assertRaises(SchedulingError) as caught:
                await self.store.create_shift(word)
            self.assertEqual((caught.exception.code, caught.exception.args[0]), ("shift_name_reserved", word))
        with self.assertRaises(SchedulingError) as caught:
            await self.store.update_shift(day, name="休")
        self.assertEqual((caught.exception.code, caught.exception.args[0]), ("shift_name_reserved", "休"))
        # 带空白的「 休 」也是同一个词（先去两头空白再判）。
        with self.assertRaises(SchedulingError) as caught:
            await self.store.create_shift(" 休 ")
        self.assertEqual(caught.exception.code, "shift_name_reserved")
        # 名字里含「休」不算（「周末休班」是名字，不是那个保留词）。
        self.assertEqual((await self.store.create_shift("周末休班"))["name"], "周末休班")

    async def test_shift_names_cannot_contain_cycle_separators(self):
        """班次名里不许有空白与 `, ， 、 · /`：周期编辑器按这些字符切词。

        `SchedulingCalendarView.vue` 的 `parseCycle` 用 `/[\\s,，、·/]+/` 切词，所以名字里
        带了它们，这条班次在界面上**永远写不进周期**；而「白班/夜班」正好切出两个合法
        班次名，编辑器还会把「固定上这个班」静默读成两班轮转。建与改名同一道闸。
        """
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")

        for name, offending in (
            ("白班/夜班", "/"),
            ("早 班", "空格"),
            ("早\u3000班", "空格"),  # 全角空格也是空白（JS 的 `\s` 认它）
            ("A,B", ","),
            ("甲，乙", "，"),
            ("甲、乙", "、"),
            ("甲·乙", "·"),
        ):
            with self.assertRaises(SchedulingError) as caught:
                await self.store.create_shift(name)
            self.assertEqual(
                (caught.exception.code, caught.exception.args[0]),
                ("shift_name_charset", offending),
            )

        # 改名同一道闸：改成一个带分隔符的名字也要被拒。
        for name in ("白班/夜班", "早 班", "A,B"):
            with self.assertRaises(SchedulingError) as caught:
                await self.store.update_shift(day, name=name)
            self.assertEqual(caught.exception.code, "shift_name_charset")
        # 拒绝 = 一个字都没改：两条班次还在原名上。
        self.assertEqual(
            [(shift["name"], shift["id"]) for shift in await self.store.list_shifts()],
            [("白班", day), ("夜班", night)],
        )

        # 正常名字照常收：中文、数字、字母、连字符、下划线、括号都不在禁用集里。
        for name in ("早班", "中班2", "A班", "早-班", "早_班", "早(补)"):
            self.assertEqual((await self.store.create_shift(name))["name"], name)

    async def test_shift_name_charset_error_says_which_characters_are_not_allowed(self):
        """错误码翻成中文：那句话要说全不能用的字符，并点名这次撞上的那个。

        这条特意穿到 HTTP 适配层 —— 错误码是服务层的，店长读到的那句话是接口层的，
        两边都对才算数（`_bad_request` 就是所有排班路由共用的那个出口）。
        """
        from api.scheduling import _bad_request

        exc = _bad_request(SchedulingError("shift_name_charset", "/"))
        self.assertEqual(exc.status_code, 400)
        self.assertIn("空格", exc.detail)
        for char in (",", "，", "、", "·", "/"):
            self.assertIn(char, exc.detail)
        # 点名撞上的那个字符；空白那一种不给店长看一个看不见的东西。
        self.assertIn("「/」", exc.detail)
        self.assertIn("「空格」", _bad_request(SchedulingError("shift_name_charset", "空格")).detail)
        self.assertNotIn("{}", exc.detail)

    async def test_usage_counts_people_in_the_rotation_and_days_written(self):
        """编辑页要看到的两个数：几个人的轮转里排着它、已经排过多少天班。"""
        first = await self._employee("13800138000", "张三")
        second = await self._employee("13800138001", "李四")
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(first["id"], [day])
        await self.store.set_rule(second["id"], [day, day, night])

        usage = await self.store.shift_usage()

        # 李四的周期是「白白夜」：90 天里白班 60 天、夜班 30 天。
        self.assertEqual(usage[day], {"people": 2, "days": EXPANSION_DAYS + 60})
        self.assertEqual(usage[night], {"people": 1, "days": 30})
        # 每个班次都有一项（页面照着自己的班次列表取值，不用补默认值）。
        middle = await self.store.create_shift("中班")
        self.assertEqual((await self.store.shift_usage())[middle["id"]], {"people": 0, "days": 0})

    async def test_a_third_shift_flows_through_calendar_and_staff_card(self):
        """验收 4：加第三个班次之后，月历与员工端卡片自动多出一种班别（不改代码）。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        middle = await self.store.create_shift("中班")
        await self.store.set_rule(employee["id"], [middle["id"]])

        calendar = await self.store.month_calendar("2026-09")
        # 月历图例按班次列表渲染：新班次自己就多出一列（前端是 `v-for`，没有写死两个）。
        self.assertEqual(
            [shift["name"] for shift in calendar["shifts"]], ["白班", "夜班", "中班"]
        )
        cells = {item["business_date"]: item for item in calendar["days"]}
        self.assertEqual(cells[TODAY]["counts"][str(middle["id"])], 1)
        self.assertEqual(cells[TODAY]["counts"][str(day)], 0)
        # 员工端那张卡：班次名由结果行的 id 翻译出来，一个新班次不需要谁认识它。
        self.assertEqual((await self.store.my_days(employee["id"]))["days"][0]["shift_name"], "中班")
        groups = {
            group["shift"]["name"]: group
            for group in (await self.store.day_detail(TODAY))["groups"]
        }
        self.assertEqual([person["name"] for person in groups["中班"]["people"]], ["张三"])

    async def test_connection_knows_the_new_tables(self):
        """新表也要进连接的 TableView 清单，否则 `db.table("staff_shifts")` 抛「未知表」。"""
        for table in SCHEDULING_TABLES:
            self.assertIsNotNone(self.db.table(table))

    # ── 交错：读在锁外、写在锁里的两处（并发用例） ──────────────────────
    #
    # 两处的形状一样 —— 先在锁外读一次（拿到「有哪些人 / 这条是什么状态」），再拿写锁
    # 去写。两个请求在这中间交错时，锁外那次读的结论已经过期了。下面两个用例把交错点
    # 钉死在「读完、还没拿锁」的那一刻，断言的是**结果**：已删的规则不许被铺回来，
    # 同一条换班不许被换两遍。

    async def test_clearing_a_rule_mid_expand_does_not_resurrect_it(self):
        """展开读到规则之后、拿到写锁之前规则被清掉：不许照旧快照把他铺回来。

        `expand()` 是读接口顺手做的（票 02 的落地口径）：先读全店规则，再逐人拿锁写。
        `clear_rule` 会删规则行、连今天以后的规则行一起删；展开要是还拿着旧快照写，
        这个人就成了「名单上没配规则、月历上却有 90 天班」—— 而且不会自愈
        （`expand()` 只补缺的天，被铺回来的行再没别的路径去删）。

        交错点钉在展开**读名单**那一刻：名单走的是 `_rule_rows()`（原样行、不解码），
        这样一个人一条脏规则不会把别人一起带走（缺陷 1）。
        """
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])
        # 清掉结果行：让接下来这次展开必须真的写（否则它只是「一天都不缺」）
        await self.db._conn.execute("DELETE FROM staff_assignments")
        await self.db._conn.commit()

        original = SchedulingStore._rule_rows
        snapshot_taken = asyncio.Event()
        resume = asyncio.Event()

        async def gated(self, *args, **kwargs):
            rules = await original(self, *args, **kwargs)
            snapshot_taken.set()
            await resume.wait()
            return rules

        SchedulingStore._rule_rows = gated
        try:
            task = asyncio.create_task(self.store.expand())
            await asyncio.wait_for(snapshot_taken.wait(), timeout=5)
            # 就在「读完规则」与「拿写锁」之间：店长清掉了他的规则。
            await self.store.clear_rule(employee["id"])
            resume.set()
            await asyncio.wait_for(task, timeout=5)
        finally:
            SchedulingStore._rule_rows = original

        cur = await self.db._conn.execute("SELECT COUNT(*) AS n FROM scheduling_rules")
        self.assertEqual(int(dict(await cur.fetchone())["n"]), 0)
        self.assertEqual(await self._rows(employee["id"]), [])

    async def test_a_swap_cannot_be_approved_twice(self):
        """同一条换班被「批准」两次：第二次不许再换一遍。

        两个标签页、双击、网关重试都会走到这儿。「读出来是待批」那道判断在锁外，
        所以只有把状态谓词放进锁里那条 UPDATE 才挡得住 —— 第二次要是照样执行
        `_approve_swap`，它会重读两个人当天的班（已经是换完的样子）再换一次：
        两次都回「批了」，班却回到原样。
        """
        first = await self._employee("13800138001", "甲")
        second = await self._employee("13800138002", "乙")
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(first["id"], [day])
        await self.store.set_rule(second["id"], [night])

        request = await self.store.submit_swap(first["id"], second["id"], TODAY, None)
        await self.store.answer_swap(second["id"], request["id"], True)

        original = SchedulingStore._request_by_id
        gate = asyncio.Event()
        readers = []

        async def gated(self, request_id):
            row = await original(self, request_id)
            readers.append(1)
            if len(readers) >= 2:
                gate.set()
            await asyncio.wait_for(gate.wait(), timeout=5)
            return row

        SchedulingStore._request_by_id = gated
        try:
            results = await asyncio.gather(
                SchedulingStore(self.db, now=lambda: self.fixed_now).approve_request(request["id"]),
                SchedulingStore(self.db, now=lambda: self.fixed_now).approve_request(request["id"]),
                return_exceptions=True,
            )
        finally:
            SchedulingStore._request_by_id = original

        succeeded = [result for result in results if not isinstance(result, Exception)]
        refused = [result for result in results if isinstance(result, SchedulingError)]
        self.assertEqual(len(succeeded), 1, results)
        self.assertEqual(len(refused), 1, results)
        self.assertEqual(refused[0].code, "request_not_pending")
        # 换了一次就停：甲那天是夜班、乙那天是白班（不是又换回去）。
        self.assertEqual(await self._day_shift(first["id"]), night)
        self.assertEqual(await self._day_shift(second["id"]), day)


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
        `/me/month` 之后，边界从「零条」变成「两扇门互不通用」—— 店长那几条只认
        管理端会话，员工那几条只认手机端 cookie。票 08 在两边各加了请假申请的路由
        （员工提/撤回、店长批/驳），票 09 又加了换班那几条（找谁换、提、对方同意/拒绝），
        票 11 再给店长那扇门加了班次表的编辑（增、改、删、调顺序），员工那扇门仍然全挂在
        `/me` 底下。

        断言路由表本身，不是源码文本 —— 文本比对会被注释或文档字符串误伤
        （写一句「这里不用 require_staff_session」就红了）。
        """
        from api.scheduling import router

        staff_paths = {
            "/api/scheduling/me",
            "/api/scheduling/me/month",
            # 票 08：我的请假申请（提、看、撤回）。
            "/api/scheduling/me/requests",
            "/api/scheduling/me/requests/{request_id}",
            # 票 09：换班 —— 能找谁换、提一条、对方同意 / 拒绝。
            "/api/scheduling/me/colleagues",
            "/api/scheduling/me/swaps",
            "/api/scheduling/me/swaps/{request_id}/accept",
            "/api/scheduling/me/swaps/{request_id}/reject",
        }
        manager_paths = {
            "/api/scheduling/shifts",
            # 票 11：班次表的编辑（看含停用的全表 + 用量、增、改、删、整表调顺序）。
            "/api/scheduling/shifts/manage",
            "/api/scheduling/shifts/order",
            "/api/scheduling/shifts/{shift_id}",
            "/api/scheduling/calendar",
            "/api/scheduling/day",
            "/api/scheduling/roster",
            "/api/scheduling/rules/{employee_id}",
            "/api/scheduling/zone-defaults/{employee_id}",
            # 票 07：单日覆盖的改与撤。
            "/api/scheduling/overrides/{employee_id}/{business_date}",
            # 票 08：待办（申请进来，批或驳出去）；票 09 的换班批完在对调两个人那天的班。
            "/api/scheduling/inbox",
            "/api/scheduling/inbox/{request_id}/approve",
            "/api/scheduling/inbox/{request_id}/reject",
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

        # 员工那扇门全挂在 `/me` 底下：读谁、动谁的申请都由会话决定，路径里不许出现
        # 员工号（票 08 的撤回带的是申请号 —— 申请号不是身份，拿别人的号也改不动）。
        for route in router.routes:
            if route.path in staff_paths:
                self.assertTrue(route.path.startswith("/api/scheduling/me"), route.path)
                self.assertNotIn("employee_id", route.path)

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
                # 票 08：请假申请（批准时才写上面那张覆盖表）。
                "scheduling_requests",
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

