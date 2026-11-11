#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存假台账只读 module（ADR 0139）：行、批量读、抵扣上限与缺表降级。

主缝 = 真库 + 直插行（同 `tests/test_scheduling_day_grid.py`）：这一层只回答「表里
那些行算出来是多少」，所以不经过写口，直接摆出排班 / 节假日 / 台账 / 抵扣 / 加班
的行来验读。时钟固定注入，不依赖机器当天日期。
"""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.holiday_calendar import HolidayCalendar
from services.identity import EmployeeAccounts
from services.identity.profile import id_card_check_digit
from services.leave_ledger import LeaveLedger

PASSWORD = "password123"
# 2026-09-24 10:00（北京时）：9 月还没过完，8 月已经过完。
NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-24"
SHIFT = 7

LEAVE_KEYS = {
    "actual_days",
    "monthly_rest",
    "holiday_makeup",
    "birthday_leave",
    "annual_leave",
    "previous_balance",
    "pool",
    "month_days",
    "deducted_days",
    "month_attendance_days",
    "balance",
    "balance_computed",
    "balance_is_manual",
}

CAPACITY_KEYS = {
    "month",
    "makeup_half_hours",
    "makeup_hours",
    "deducted_days",
    "deducted_half_hours",
    "deducted_hours",
    "available_half_hours",
    "available_hours",
    "leave_balance",
    "max_days_by_makeup",
    "max_days_by_leave",
    "max_days",
    "sealed",
    "half_hours_per_day",
    "hours_per_day",
}


def _id_card(birthday: str) -> str:
    """造一个校验位正确的 18 位身份证号（生日在 7–14 位）。"""
    body = f"110101{birthday}001"
    return body + id_card_check_digit(body)


class LeaveLedgerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.stamp = NOW.isoformat()
        self.accounts = EmployeeAccounts(self.db, now=lambda: NOW)
        self.holidays = HolidayCalendar(self.db, now=lambda: NOW)
        self.ledger = LeaveLedger(self.db, now=lambda: NOW)
        self._phone_seq = 0

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    # ── 造数 ───────────────────────────────────────────────────────────────

    async def _employee(
        self,
        name="张三",
        *,
        hire_date="2024-03-01",
        id_card_no=None,
        admin_caps=None,
        seniority_bonus=None,
        resigned_at=None,
    ):
        self._phone_seq += 1
        phone = f"1380013{self._phone_seq:04d}"
        employee = await self.accounts.register(
            phone,
            PASSWORD,
            name,
            id_card_no=id_card_no,
            hire_date=hire_date,
        )
        employee_id = int(employee["id"])
        await self.accounts.approve(employee_id)
        if admin_caps is not None:
            await self.accounts.set_admin_caps(employee_id, admin_caps)
        if seniority_bonus is not None:
            await self.accounts.update_fields(
                employee_id, seniority_bonus=seniority_bonus
            )
        if resigned_at is not None:
            await self.db._conn.execute(
                "UPDATE hygiene_employees SET resigned_at = ? WHERE id = ?",
                (resigned_at, employee_id),
            )
            await self.db._conn.commit()
        return employee_id

    async def _work(self, employee_id, days, *, half_rest_days=()):
        """排上班（`shift_id` 非空）；`half_rest_days` 里的那天标「休半天」。"""
        half_rest_days = set(half_rest_days)
        for day in days:
            await self.db._conn.execute(
                """INSERT INTO staff_assignments
                   (employee_id, business_date, shift_id, zone_id, source, half_rest,
                    created_at, updated_at)
                   VALUES (?, ?, ?, NULL, 'rule', ?, ?, ?)""",
                (
                    employee_id,
                    day,
                    SHIFT,
                    1 if day in half_rest_days else 0,
                    self.stamp,
                    self.stamp,
                ),
            )
        await self.db._conn.commit()

    async def _ledger(
        self, employee_id, month, *, opening=None, closing=None, sealed_at=None
    ):
        await self.db._conn.execute(
            """INSERT INTO attendance_ledger
               (tenant_id, employee_id, "month", opening_balance, closing_balance,
                sealed_at, created_at, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (tenant_id, employee_id, "month") DO UPDATE SET
                 opening_balance = excluded.opening_balance,
                 closing_balance = excluded.closing_balance,
                 sealed_at = excluded.sealed_at""",
            (
                employee_id,
                month,
                opening,
                closing,
                sealed_at,
                self.stamp,
                self.stamp,
            ),
        )
        await self.db._conn.commit()

    async def _deduction(self, employee_id, month, days, *, status="approved"):
        await self.db._conn.execute(
            """INSERT INTO overtime_leave_deductions
               (tenant_id, employee_id, "month", days, half_hours, status,
                created_by, created_at, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, 'tester', ?, ?)""",
            (
                employee_id,
                month,
                days,
                days * 17,
                status,
                self.stamp,
                self.stamp,
            ),
        )
        await self.db._conn.commit()

    async def _makeup(self, employee_id, day, hours):
        """造一笔**已批准**的补钟；台账里存的是半小时数，补钟取负。"""
        half_hours = -int(round(float(hours) * 2))
        await self.db._conn.execute(
            """INSERT INTO overtime_entries
               (tenant_id, employee_id, entry_date, half_hours, reason, status,
                created_by, created_at, updated_at)
               VALUES (1, ?, ?, ?, '补钟', 'approved', 'tester', ?, ?)""",
            (employee_id, day, half_hours, self.stamp, self.stamp),
        )
        await self.db._conn.commit()

    # ── 行与算式 ───────────────────────────────────────────────────────────

    async def test_row_counts_the_whole_formula(self):
        """九项 + balance：整月出勤、例休、节假日补休、生日假、上月结转。"""
        employee = await self._employee(id_card_no=_id_card("19900915"))
        days = [f"2026-09-{day:02d}" for day in range(1, 11)]
        await self._work(employee, days)
        await self.holidays.mark("2026-09-15", "中秋节")
        await self._work(employee, ["2026-09-15"])
        # 8 月封过账：9 月结转取封下的 2 天（9 月这一行没有期初）。
        await self._ledger(
            employee, "2026-08", closing=2.0, sealed_at="2026-09-01T09:00:00+08:00"
        )

        row = await self.ledger.ledger_row(employee, "2026-09")

        self.assertEqual(row["employee_id"], employee)
        self.assertEqual(row["month"], "2026-09")
        self.assertEqual(row["today"], TODAY)
        self.assertEqual(row["as_of"], TODAY)
        self.assertEqual(row["days_in_month"], 30)
        self.assertIsNone(row["sealed_at"])
        self.assertTrue(row["provisional"])
        self.assertEqual(
            row["previous_month"],
            {"month": "2026-08", "sealed_at": "2026-09-01T09:00:00+08:00"},
        )

        leave = row["leave"]
        self.assertEqual(set(leave), LEAVE_KEYS)
        self.assertEqual(leave["actual_days"], 11.0)
        self.assertEqual(leave["monthly_rest"], 1)
        self.assertEqual(leave["holiday_makeup"], 1)
        self.assertEqual(leave["birthday_leave"], 1)
        self.assertEqual(leave["annual_leave"], 0)
        self.assertEqual(leave["previous_balance"], 2.0)
        self.assertEqual(leave["pool"], 16.0)
        self.assertEqual(leave["month_days"], 30)
        self.assertEqual(leave["deducted_days"], 0.0)
        self.assertEqual(leave["month_attendance_days"], 16.0)
        self.assertEqual(leave["balance"], -14.0)
        self.assertEqual(leave["balance_computed"], -14.0)
        self.assertFalse(leave["balance_is_manual"])

    async def test_hire_month_and_resigned_month_use_on_duty_days(self):
        """入职当月从入职日算、离职月截到离职日；老员工仍是整月。"""
        hired = await self._employee(hire_date="2026-09-16")
        left = await self._employee(hire_date="2024-03-01", resigned_at="2026-09-15")
        old = await self._employee(hire_date="2024-03-01")
        for employee in (hired, left, old):
            await self._ledger(employee, "2026-09", opening=30.0)

        for employee, month_days in ((hired, 15), (left, 15), (old, 30)):
            row = await self.ledger.ledger_row(employee, "2026-09")
            self.assertEqual(row["leave"]["month_days"], month_days)
            self.assertEqual(row["leave"]["balance"], 30.0 - month_days)

    async def test_half_rest_counts_half_a_day(self):
        """标了「休半天」的那天算出勤 0.5 天。"""
        employee = await self._employee()
        days = [f"2026-09-{day:02d}" for day in range(1, 7)]
        await self._work(employee, days, half_rest_days={"2026-09-03"})

        leave = (await self.ledger.ledger_row(employee, "2026-09"))["leave"]

        self.assertEqual(leave["actual_days"], 5.5)
        self.assertEqual(leave["monthly_rest"], 0)  # 5.5 < 6
        self.assertEqual(leave["balance"], 5.5 - 30)

    async def test_birthday_leave_comes_from_id_card(self):
        """生日落在当月 +1 天；不是当月 0 天。"""
        born = await self._employee(id_card_no=_id_card("19900915"))
        other = await self._employee(id_card_no=_id_card("19901015"))
        await self._ledger(born, "2026-09", opening=30.0)
        await self._ledger(other, "2026-09", opening=30.0)

        landed = (await self.ledger.ledger_row(born, "2026-09"))["leave"]
        missed = (await self.ledger.ledger_row(other, "2026-09"))["leave"]

        self.assertEqual(landed["birthday_leave"], 1)
        self.assertEqual(landed["balance"], 1.0)
        self.assertEqual(missed["birthday_leave"], 0)
        self.assertEqual(missed["balance"], 0.0)

    async def test_annual_leave_only_in_seniority_due_month(self):
        """年假只在工龄奖调整月给；管理层 5 天、其他 3 天。"""
        staff = await self._employee(
            hire_date="2023-09-01", seniority_bonus=300
        )
        manager = await self._employee(
            hire_date="2023-09-01",
            seniority_bonus=300,
            admin_caps=["schedule"],
        )

        self.assertEqual(
            (await self.ledger.ledger_row(staff, "2026-09"))["leave"]["annual_leave"], 3
        )
        self.assertEqual(
            (await self.ledger.ledger_row(manager, "2026-09"))["leave"]["annual_leave"],
            5,
        )
        # 8 月不是工龄奖调整月（also 不是 settled 的调整月），一律 0。
        self.assertEqual(
            (await self.ledger.ledger_row(staff, "2026-08"))["leave"]["annual_leave"], 0
        )

    async def test_manual_closing_balance_overrides_the_formula(self):
        """手填期末存假是有效值，公式值仍留在 balance_computed 里。"""
        employee = await self._employee()
        await self._ledger(employee, "2026-09", closing=12.5)

        leave = (await self.ledger.ledger_row(employee, "2026-09"))["leave"]

        self.assertEqual(leave["balance"], 12.5)
        self.assertEqual(leave["balance_computed"], -30.0)
        self.assertTrue(leave["balance_is_manual"])

    async def test_previous_balance_three_paths(self):
        """上月已封账取结余；未封账退回本月期初；都没有 0。"""
        sealed = await self._employee()
        unsealed = await self._employee()
        empty = await self._employee()
        await self._ledger(
            sealed,
            "2026-08",
            closing=7.0,
            sealed_at="2026-09-01T09:00:00+08:00",
        )
        await self._ledger(sealed, "2026-09", opening=99.0)
        # 上月结转按**这个人**的上一月行判；整月封账口径只用在 `sealed_at` 展示上。
        # （下面会断言：8 月被另一个员工封过，所以整月的 `sealed_at` 非空，但这一行的
        # 结转仍退回本月期初 3.0。）
        await self._ledger(unsealed, "2026-07", closing=7.0)
        await self._ledger(unsealed, "2026-08", opening=3.0)

        first = await self.ledger.ledger_row(sealed, "2026-09")
        second = await self.ledger.ledger_row(unsealed, "2026-08")
        third = await self.ledger.ledger_row(empty, "2026-09")

        self.assertEqual(first["leave"]["previous_balance"], 7.0)
        self.assertEqual(second["leave"]["previous_balance"], 3.0)
        self.assertEqual(third["leave"]["previous_balance"], 0.0)
        self.assertEqual(
            first["previous_month"]["sealed_at"], "2026-09-01T09:00:00+08:00"
        )
        self.assertIsNone(second["previous_month"]["sealed_at"])
        # 整月口径：另一个人把 8 月封过，这个人的 8 月也按「已封账」展示。
        self.assertIsNotNone(second["sealed_at"])
        self.assertFalse(second["provisional"])

    async def test_approved_deduction_only_touches_balance(self):
        """已批抵扣只从余额减；池与明细不动，待批不算。"""
        employee = await self._employee()
        await self._ledger(employee, "2026-09", opening=32.0)
        await self._deduction(employee, "2026-09", 2)
        await self._deduction(employee, "2026-09", 5, status="pending")

        leave = (await self.ledger.ledger_row(employee, "2026-09"))["leave"]

        self.assertEqual(leave["deducted_days"], 2.0)
        self.assertEqual(leave["balance"], 0.0)
        self.assertEqual(leave["pool"], 32.0)
        self.assertEqual(leave["actual_days"], 0.0)
        self.assertEqual(leave["monthly_rest"], 0)
        self.assertEqual(leave["holiday_makeup"], 0)

    async def test_sealed_month_is_not_provisional_and_capacity_is_zero(self):
        """封账月：sealed_at 有值、provisional=False、capacity.max_days=0，其余照算。"""
        employee = await self._employee()
        await self._ledger(
            employee,
            "2026-08",
            opening=31.0,
            closing=1.0,
            sealed_at="2026-09-01T09:00:00+08:00",
        )
        await self._makeup(employee, "2026-08-10", 34.0)

        row = await self.ledger.ledger_row(employee, "2026-08")
        capacity = await self.ledger.capacity(employee, "2026-08")

        self.assertEqual(row["sealed_at"], "2026-09-01T09:00:00+08:00")
        self.assertFalse(row["provisional"])
        self.assertEqual(row["leave"]["balance"], 1.0)
        self.assertTrue(capacity["sealed"])
        self.assertEqual(capacity["max_days"], 0)
        # 其余字段照算：补钟 4 天、存假 1 天。
        self.assertEqual(capacity["max_days_by_makeup"], 4)
        self.assertEqual(capacity["max_days_by_leave"], 1)
        self.assertEqual(capacity["leave_balance"], 1.0)

    async def test_as_of_clamps_to_month_end_or_today(self):
        """过去月顶到月末，当月 / 未来月都取今天。"""
        employee = await self._employee()

        august = await self.ledger.ledger_row(employee, "2026-08")
        september = await self.ledger.ledger_row(employee, "2026-09")
        october = await self.ledger.ledger_row(employee, "2026-10")

        self.assertEqual(august["as_of"], "2026-08-31")
        self.assertEqual(august["days_in_month"], 31)
        self.assertEqual(september["as_of"], TODAY)
        self.assertEqual(october["as_of"], TODAY)
        self.assertEqual(october["days_in_month"], 31)

    async def test_ledger_rows_matches_ledger_row_and_scopes_the_roster(self):
        """批量与单人同值；省略名单 = 花名册全员（含离职）；显式名单过滤。"""
        first = await self._employee(name="甲")
        second = await self._employee(name="乙")
        resigned = await self._employee(name="丙", resigned_at="2026-09-10")

        rows = await self.ledger.ledger_rows("2026-09")

        self.assertEqual(set(rows), {first, second, resigned})
        for employee_id in (first, second, resigned):
            self.assertEqual(
                rows[employee_id],
                await self.ledger.ledger_row(employee_id, "2026-09"),
            )
        self.assertEqual(
            await self.ledger.ledger_rows("2026-09", [first]), {first: rows[first]}
        )
        # 显式名单里认不出的人直接跳过；空名单直接返回 {} —— 但节假日仍先读
        # （缺 0026 时要报 503，不能因为名单空就静默成功）。
        self.assertEqual(
            await self.ledger.ledger_rows("2026-09", [first, 999999]),
            {first: rows[first]},
        )
        self.assertEqual(await self.ledger.ledger_rows("2026-09", []), {})
        self.assertIsNone(await self.ledger.ledger_row(999999, "2026-09"))

    async def test_missing_ledger_and_deduction_tables_degrade(self):
        """0027 / 0042 缺表：未封账、期初 0、抵扣 0 —— 不把只读投影打成 503。"""
        employee = await self._employee()
        await self._ledger(employee, "2026-09", opening=5.0, sealed_at=self.stamp)
        await self._deduction(employee, "2026-09", 2)

        await self.db._conn.execute(
            "ALTER TABLE attendance_ledger RENAME TO attendance_ledger_gone"
        )
        await self.db._conn.commit()
        try:
            row = await self.ledger.ledger_row(employee, "2026-09")
            self.assertIsNone(row["sealed_at"])
            self.assertTrue(row["provisional"])
            self.assertEqual(row["leave"]["previous_balance"], 0.0)
            # 期初 5 天没了，但 2 天已批抵扣还在：0 − 30 − 2。
            self.assertEqual(row["leave"]["balance"], -32.0)
        finally:
            await self.db._conn.execute(
                "ALTER TABLE attendance_ledger_gone RENAME TO attendance_ledger"
            )
            await self.db._conn.commit()

        await self.db._conn.execute(
            "ALTER TABLE overtime_leave_deductions RENAME TO overtime_leave_deductions_gone"
        )
        await self.db._conn.commit()
        try:
            row = await self.ledger.ledger_row(employee, "2026-09")
            self.assertEqual(row["leave"]["deducted_days"], 0.0)
            self.assertEqual(row["leave"]["balance"], 5.0 - 30.0)
        finally:
            await self.db._conn.execute(
                "ALTER TABLE overtime_leave_deductions_gone RENAME TO overtime_leave_deductions"
            )
            await self.db._conn.commit()

    # ── 抵扣上限 ───────────────────────────────────────────────────────────

    async def test_capacity_keys_and_both_caps(self):
        """返回键与旧 `deduction_capacity` 一致；补钟侧 / 存假侧各封各的顶。"""
        employee = await self._employee()
        await self._ledger(employee, "2026-09", opening=32.0)  # 余额 2 天
        await self._makeup(employee, TODAY, 34.0)  # 4 天

        capacity = await self.ledger.capacity(employee, "2026-09")

        self.assertEqual(set(capacity), CAPACITY_KEYS)
        self.assertEqual(capacity["month"], "2026-09")
        self.assertEqual(capacity["makeup_half_hours"], 68)
        self.assertEqual(capacity["makeup_hours"], 34.0)
        self.assertEqual(capacity["deducted_days"], 0)
        self.assertEqual(capacity["deducted_half_hours"], 0)
        self.assertEqual(capacity["deducted_hours"], 0.0)
        self.assertEqual(capacity["available_half_hours"], 68)
        self.assertEqual(capacity["available_hours"], 34.0)
        self.assertEqual(capacity["leave_balance"], 2.0)
        self.assertEqual(capacity["max_days_by_makeup"], 4)
        self.assertEqual(capacity["max_days_by_leave"], 2)
        self.assertEqual(capacity["max_days"], 2)
        self.assertFalse(capacity["sealed"])
        self.assertEqual(capacity["half_hours_per_day"], 17)
        self.assertEqual(capacity["hours_per_day"], 8.5)

    async def test_capacity_makeup_cap_and_leave_cap_are_floor(self):
        """补钟不够整天 / 存假只有 0.5 天时各自回 0；已批抵扣从补钟池里减。"""
        short_makeup = await self._employee()
        await self._ledger(short_makeup, "2026-09", opening=35.0)  # 余额 5 天
        await self._makeup(short_makeup, TODAY, 6.0)  # 12 个半小时 < 17

        half_leave = await self._employee()
        await self._ledger(half_leave, "2026-09", opening=30.5)  # 余额 0.5 天
        await self._makeup(half_leave, TODAY, 17.0)

        deducted = await self._employee()
        await self._ledger(deducted, "2026-09", opening=35.0)
        await self._makeup(deducted, TODAY, 34.0)  # 4 天
        await self._deduction(deducted, "2026-09", 2)  # 已抵 2 天 → 只剩 2 天补钟

        none = await self.ledger.capacity(short_makeup, "2026-09")
        half = await self.ledger.capacity(half_leave, "2026-09")
        after = await self.ledger.capacity(deducted, "2026-09")

        self.assertEqual(none["max_days_by_makeup"], 0)
        self.assertEqual(none["max_days"], 0)
        self.assertEqual(half["max_days_by_leave"], 0)
        self.assertEqual(half["max_days"], 0)
        self.assertEqual(after["deducted_days"], 2)
        self.assertEqual(after["deducted_half_hours"], 34)
        self.assertEqual(after["available_half_hours"], 34)
        self.assertEqual(after["max_days_by_makeup"], 2)
        self.assertEqual(after["max_days"], 2)

        self.assertIsNone(await self.ledger.capacity(999999, "2026-09"))
