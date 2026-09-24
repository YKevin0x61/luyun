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
        self.assertEqual(group["names"], ["张三"])

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
        """改规则只影响今天以后：之前写下的行一行不动（「过去不改」是做法本身）。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [day])

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
        employee = await self._employee()
        with self.assertRaises(SchedulingError) as caught:
            await self.store.set_rule(employee["id"], [999])
        self.assertEqual(caught.exception.code, "unknown_shift")
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
        for path in sorted((REPO_ROOT / "services" / "scheduling").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
            offenders = sorted(name for name in imported if name.startswith("services.hygiene"))
            self.assertEqual(offenders, [], f"{path.name} 不该 import 卫生")

    def test_scheduling_api_has_no_staff_route(self):
        """员工端这一票一个字不改：排班的 HTTP 面只有管理端会话。

        断言路由表本身，不是源码文本 —— 文本比对会被注释或文档字符串误伤
        （写一句「这里不用 require_staff_session」就红了）。
        """
        from api.scheduling import router

        self.assertEqual(
            {route.path for route in router.routes},
            {
                "/api/scheduling/shifts",
                "/api/scheduling/calendar",
                "/api/scheduling/day",
                "/api/scheduling/roster",
                "/api/scheduling/rules/{employee_id}",
            },
        )

        def dependency_names(dependant) -> set[str]:
            names = set()
            for sub in dependant.dependencies:
                names.add(getattr(sub.call, "__name__", ""))
                names |= dependency_names(sub)
            return names

        for route in router.routes:
            names = dependency_names(route.dependant)
            self.assertIn("require_session", names, route.path)
            self.assertNotIn("require_staff_session", names, route.path)

    def test_new_tables_are_registered_read_only(self):
        """验收 4：新表进表名清单，Admin 的数据浏览器只读能看到。"""
        self.assertEqual(
            set(SCHEDULING_TABLES),
            {"staff_shifts", "staff_assignments", "scheduling_rules"},
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
