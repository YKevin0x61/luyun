#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班日格（ADR 0138）：`DayGrid.cells` / `leave_days` 的读口径。

主缝 = 真库 + 直插行：日格只回答「表里那几行是什么」，所以这里不经过写口（覆盖的
规则是写口那组用例的事），直接摆出 `staff_assignments` / `scheduling_overrides` 的
行来验读。行序、补格、班次名这些**投影**外壳不在这里验（它们仍归各投影）。
"""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.identity import EmployeeAccounts
from services.scheduling.day_grid import (
    SOURCE_OVERRIDE,
    SOURCE_RULE,
    DayCell,
    DayGrid,
    is_half_rest,
)

PASSWORD = "password123"


class DayGridTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
        self.accounts = EmployeeAccounts(self.db, now=lambda: self.fixed_now)
        self.grid = DayGrid(self.db, now=lambda: self.fixed_now)
        self.stamp = self.fixed_now.isoformat()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _employee(self, phone="13800138000", name="张三", *, approve=True):
        employee = await self.accounts.register(phone, PASSWORD, name)
        if approve:
            await self.accounts.approve(employee["id"])
        return int(employee["id"])

    async def _assignment(
        self,
        employee_id,
        day,
        shift_id=None,
        *,
        half_rest=0,
        source=SOURCE_RULE,
        zone_id=None,
    ):
        await self.db._conn.execute(
            """INSERT INTO staff_assignments
               (employee_id, business_date, shift_id, zone_id, source, half_rest,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (employee_id, day, shift_id, zone_id, source, half_rest, self.stamp, self.stamp),
        )
        await self.db._conn.commit()

    async def _leave(self, employee_id, day):
        await self.db._conn.execute(
            """INSERT INTO scheduling_overrides
               (employee_id, business_date, shift_id, zone_id, kind, created_at, updated_at)
               VALUES (?, ?, NULL, NULL, 'leave', ?, ?)""",
            (employee_id, day, self.stamp, self.stamp),
        )
        await self.db._conn.commit()

    async def test_cells_returns_only_rows_that_exist(self):
        """没铺到的日子不补格：区间里只有落过行的两天有格子。"""
        employee = await self._employee()
        await self._assignment(employee, "2026-09-01", 7)
        await self._assignment(employee, "2026-09-03", 7)

        cells = await self.grid.cells("2026-09-01", "2026-09-05")

        self.assertEqual([cell.business_date for cell in cells], ["2026-09-01", "2026-09-03"])

    async def test_rest_work_and_override_flags(self):
        """休行（shift 空）与上班行分开，`overridden` 只看 source。"""
        employee = await self._employee()
        await self._assignment(employee, "2026-09-01", 7, zone_id=3)
        await self._assignment(employee, "2026-09-02", None, source=SOURCE_OVERRIDE)

        cells = {cell.business_date: cell for cell in await self.grid.cells("2026-09-01", "2026-09-02")}

        work = cells["2026-09-01"]
        self.assertEqual(work.shift_id, 7)
        self.assertEqual(work.zone_id, 3)
        self.assertFalse(work.overridden)
        self.assertFalse(work.half_rest)

        rest = cells["2026-09-02"]
        self.assertIsNone(rest.shift_id)
        self.assertIsNone(rest.zone_id)
        self.assertTrue(rest.overridden)

    async def test_half_rest_requires_a_shift(self):
        """半天休只对上班行作数；休行上的标志读成 False。"""
        employee = await self._employee()
        await self._assignment(employee, "2026-09-01", None, half_rest=1)
        await self._assignment(employee, "2026-09-02", 7, half_rest=1)

        cells = {cell.business_date: cell for cell in await self.grid.cells("2026-09-01", "2026-09-02")}

        self.assertFalse(cells["2026-09-01"].half_rest)
        self.assertTrue(cells["2026-09-02"].half_rest)
        self.assertFalse(is_half_rest(None, 1))
        self.assertTrue(is_half_rest(7, 1))
        self.assertFalse(is_half_rest(7, 0))

    async def test_leave_marks_only_rest_rows(self):
        """请假只落在休行上；上班行即使表里有 kind=leave 也不算请假。"""
        employee = await self._employee()
        await self._assignment(employee, "2026-09-01", None)
        await self._assignment(employee, "2026-09-02", 7)
        await self._leave(employee, "2026-09-01")
        await self._leave(employee, "2026-09-02")

        cells = {cell.business_date: cell for cell in await self.grid.cells("2026-09-01", "2026-09-02")}

        self.assertTrue(cells["2026-09-01"].leave)
        self.assertFalse(cells["2026-09-02"].leave)

    async def test_roster_only_filters_pending_and_resigned(self):
        """`roster_only=True` 只留在册：待批准与已离职都不出现；False 时都读。"""
        approved = await self._employee("13800138001", "在册")
        pending = await self._employee("13800138002", "待批准", approve=False)
        resigned = await self._employee("13800138003", "离职")
        await self.accounts.resign(resigned, "2026-09-01")
        for employee in (approved, pending, resigned):
            await self._assignment(employee, "2026-09-01", 7)

        on_roster = await self.grid.cells("2026-09-01", "2026-09-01")
        everyone = await self.grid.cells("2026-09-01", "2026-09-01", roster_only=False)

        self.assertEqual({cell.employee_id for cell in on_roster}, {approved})
        self.assertEqual({cell.employee_id for cell in everyone}, {approved, pending, resigned})

    async def test_roster_uses_the_injected_clock(self):
        """在册判断的「今天」由构造注入：同一个离职日期，时钟在它之前就还不算离职。"""
        employee = await self._employee("13800138004", "将离职")
        await self.accounts.resign(employee, "2026-09-01")
        await self._assignment(employee, "2026-08-01", 7)

        before = DayGrid(
            self.db, now=lambda: datetime(2026, 8, 1, 10, 0, tzinfo=CHINA_TZ)
        )
        after = DayGrid(
            self.db, now=lambda: datetime(2026, 9, 2, 10, 0, tzinfo=CHINA_TZ)
        )

        self.assertEqual(len(await before.cells("2026-08-01", "2026-08-01")), 1)
        self.assertEqual(await after.cells("2026-08-01", "2026-08-01"), [])

    async def test_employee_ids_narrows_and_empty_returns_nothing(self):
        """`employee_ids` 限定到人；空名单不查库、直接空。"""
        first = await self._employee("13800138001", "甲")
        second = await self._employee("13800138002", "乙")
        await self._assignment(first, "2026-09-01", 7)
        await self._assignment(second, "2026-09-01", 7)

        only_first = await self.grid.cells("2026-09-01", "2026-09-01", employee_ids=[first])
        none = await self.grid.cells("2026-09-01", "2026-09-01", employee_ids=[])

        self.assertEqual([cell.employee_id for cell in only_first], [first])
        self.assertEqual(none, [])

    async def test_leave_days_includes_leave_without_an_assignment_row(self):
        """请了假但还没铺到结果行：`leave_days` 照样给，`cells` 不补格。"""
        employee = await self._employee()
        await self._leave(employee, "2026-09-10")

        cells = await self.grid.cells("2026-09-01", "2026-09-30")
        leave_days = await self.grid.leave_days([employee], "2026-09-01", "2026-09-30")

        self.assertEqual(cells, [])
        self.assertEqual(leave_days, {employee: {"2026-09-10"}})
        self.assertEqual(await self.grid.leave_days([], "2026-09-01", "2026-09-30"), {})

    async def test_cells_range_is_inclusive_at_both_ends(self):
        """`first` / `last` 两端都含；区间外的行不读。"""
        employee = await self._employee()
        for day in ("2026-08-31", "2026-09-01", "2026-09-30", "2026-10-01"):
            await self._assignment(employee, day, 7)

        cells = await self.grid.cells("2026-09-01", "2026-09-30")

        self.assertEqual(
            [cell.business_date for cell in cells], ["2026-09-01", "2026-09-30"]
        )

    async def test_cells_are_sorted_by_employee_then_date(self):
        """顺序稳定：按员工号、再按日期，跟插入顺序无关。"""
        first = await self._employee("13800138001", "甲")
        second = await self._employee("13800138002", "乙")
        await self._assignment(second, "2026-09-02", 7)
        await self._assignment(first, "2026-09-02", 7)
        await self._assignment(second, "2026-09-01", 7)

        cells = await self.grid.cells("2026-09-01", "2026-09-02")

        self.assertEqual(
            [(cell.employee_id, cell.business_date) for cell in cells],
            [(first, "2026-09-02"), (second, "2026-09-01"), (second, "2026-09-02")],
        )

    async def test_accepts_a_raw_connection(self):
        """构造约定与 `DutyRoster` 一致：给连接或给 DatabaseManager 都行。"""
        employee = await self._employee()
        await self._assignment(employee, "2026-09-01", 7)

        cells = await DayGrid(self.db._conn, now=lambda: self.fixed_now).cells(
            "2026-09-01", "2026-09-01"
        )

        self.assertEqual(len(cells), 1)
        self.assertIsInstance(cells[0], DayCell)

    async def test_requires_an_open_connection(self):
        with self.assertRaises(RuntimeError):
            DayGrid(None)


if __name__ == "__main__":
    unittest.main()
