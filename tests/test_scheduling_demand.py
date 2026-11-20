#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班「每日需求人数」（0061）：默认值 / 按天覆盖 / fallback 中位数 / 缺口。

用户裁定的口径（不要在这里重新论证）：
- 目标按「营业日 × 班次」配，默认值分平时（weekday）/ 周末（weekend）两套，
  特殊日子按天覆盖（scheduling_demand_days）；
- 都没配的日子回落「近 4 周同星期几该班次上班人数的中位数」，响应里
  source = fallback（前端显示「参考」，灰的）；一次样本都没有的新店是 source = none；
- 缺口 = 实到 - 目标，负数是缺；
- month_calendar 只加字段，旧的 counts / total / overridden / half_rest 一个不破。

主缝跟 tests/test_scheduling.py 一样：服务层 + 真库 + 注入时钟（工作台日 00:00 切）。
历史班表直接用 SQL 写进 staff_assignments —— fallback 的证据只能是「过去真排过」，
走规则展开反而绕：规则是今天起往后铺，回不到过去。
"""

import tempfile
import unittest
from datetime import datetime, timedelta

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.accounts import EmployeeAccounts
from services.scheduling.store import (
    DEMAND_FALLBACK_WEEKS,
    DEMAND_SOURCE_CONFIGURED,
    DEMAND_SOURCE_FALLBACK,
    DEMAND_SOURCE_NONE,
    MAX_DEMAND_HEADCOUNT,
    SOURCE_RULE,
    SchedulingError,
    SchedulingStore,
)

PASSWORD = "password123"
TODAY = "2026-09-24"  # 周四
SATURDAY = "2026-09-26"
NEXT_THURSDAY = "2026-10-01"


class SchedulingDemandTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
        self.accounts = EmployeeAccounts(self.db, now=lambda: self.fixed_now)
        self.store = SchedulingStore(self.db, now=lambda: self.fixed_now)
        await self.store.prepare()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _employees(self, count):
        """建 count 个已批准的在册员工（fallback 只数在册的人）。"""
        people = []
        for index in range(count):
            employee = await self.accounts.register(
                f"1380013{index:04d}", PASSWORD, f"员工{index}"
            )
            await self.accounts.approve(employee["id"])
            people.append(employee)
        return people

    async def _shift_id(self, name):
        for shift in await self.store.list_shifts():
            if shift["name"] == name:
                return shift["id"]
        raise AssertionError(f"没有班次 {name}")

    async def _assign(self, employee_ids, day, shift_id):
        """直接往 staff_assignments 写历史行（绕过规则展开：要的是过去的班表）。"""
        stamp = self.fixed_now.isoformat()
        for employee_id in employee_ids:
            await self.db._conn.execute(
                """INSERT INTO staff_assignments
                   (employee_id, business_date, shift_id, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (employee_id, day, shift_id, SOURCE_RULE, stamp, stamp),
            )
        await self.db._conn.commit()

    async def _seed_thursday_history(self):
        """过去 4 个周四的班表：白班 8/10/12/14 人，夜班恒定 4 人 → 中位 11 / 4。"""
        day_shift = await self._shift_id("白班")
        night_shift = await self._shift_id("夜班")
        # 一个人同一天只能排一个班（staff_assignments 的唯一键），所以夜班那 4 个人
        # 从白班人数最多的 14 人之外再拿。
        people = await self._employees(18)
        thursdays = [
            (datetime.fromisoformat(TODAY) - timedelta(days=7 * step)).date().isoformat()
            for step in range(1, 5)
        ]
        for day, day_count in zip(thursdays, (8, 10, 12, 14)):
            await self._assign([p["id"] for p in people[:day_count]], day, day_shift)
            await self._assign([p["id"] for p in people[14:18]], day, night_shift)
        return day_shift, night_shift

    async def test_defaults_round_trip(self):
        """默认值：平时 / 周末两组各存各的，读回来的形状给前端弹窗用。"""
        day_shift = await self._shift_id("白班")
        night_shift = await self._shift_id("夜班")
        await self.store.set_demand_defaults(
            {str(day_shift): 10, str(night_shift): 5}, {str(day_shift): 8}
        )

        data = await self.store.demand_defaults()
        self.assertEqual(data["weekday"], {str(day_shift): 10, str(night_shift): 5})
        self.assertEqual(data["weekend"], {str(day_shift): 8})
        self.assertEqual(data["fallback_weeks"], DEMAND_FALLBACK_WEEKS)
        self.assertEqual(data["max_headcount"], MAX_DEMAND_HEADCOUNT)
        self.assertEqual(
            [shift["id"] for shift in data["shifts"]], [day_shift, night_shift]
        )

        weekday = await self.store.demand_target(TODAY)
        self.assertEqual(weekday["targets"], {str(day_shift): 10, str(night_shift): 5})
        self.assertEqual(weekday["source"], DEMAND_SOURCE_CONFIGURED)
        self.assertFalse(weekday["weekend"])

        weekend = await self.store.demand_target(SATURDAY)
        self.assertTrue(weekend["weekend"])
        self.assertEqual(weekend["targets"][str(day_shift)], 8)
        self.assertEqual(weekend["sources"][str(day_shift)], DEMAND_SOURCE_CONFIGURED)

    async def test_day_override_wins_over_default(self):
        """按天覆盖优先于默认值；传 null / 空删掉覆盖后回默认值。"""
        day_shift = await self._shift_id("白班")
        night_shift = await self._shift_id("夜班")
        await self.store.set_demand_defaults({str(day_shift): 10, str(night_shift): 5})

        await self.store.set_demand_day(TODAY, {str(day_shift): 12})
        data = await self.store.demand_target(TODAY)
        self.assertEqual(data["targets"][str(day_shift)], 12)
        self.assertEqual(data["sources"][str(day_shift)], DEMAND_SOURCE_CONFIGURED)
        # 按天覆盖是整组：没写到的班次仍走默认值。
        self.assertEqual(data["targets"][str(night_shift)], 5)
        self.assertEqual(data["sources"][str(night_shift)], DEMAND_SOURCE_CONFIGURED)

        await self.store.set_demand_day(TODAY, None)
        cleared = await self.store.demand_target(TODAY)
        self.assertEqual(cleared["targets"][str(day_shift)], 10)
        self.assertEqual(cleared["sources"][str(day_shift)], DEMAND_SOURCE_CONFIGURED)

    async def test_fallback_uses_median_of_same_weekday(self):
        """没配置时按近 4 周同星期几的中位数，响应标 fallback；没有样本回 none/0。"""
        day_shift, night_shift = await self._seed_thursday_history()

        data = await self.store.demand_target(NEXT_THURSDAY)
        self.assertEqual(data["targets"], {str(day_shift): 11, str(night_shift): 4})
        self.assertEqual(
            data["sources"],
            {
                str(day_shift): DEMAND_SOURCE_FALLBACK,
                str(night_shift): DEMAND_SOURCE_FALLBACK,
            },
        )
        self.assertEqual(data["source"], DEMAND_SOURCE_FALLBACK)
        self.assertEqual(data["overrides"], {})

        # 周一一次历史都没有（新店）→ 0 人 + none，而不是猜一个数。
        fresh = await self.store.demand_target("2026-09-28")
        self.assertEqual(fresh["targets"], {str(day_shift): 0, str(night_shift): 0})
        self.assertEqual(fresh["source"], DEMAND_SOURCE_NONE)

    async def test_gap_is_count_minus_target(self):
        """缺口 = 实到 - 目标：月历与当天快照都带正负号。"""
        day_shift = await self._shift_id("白班")
        night_shift = await self._shift_id("夜班")
        await self.store.set_demand_defaults({str(day_shift): 2, str(night_shift): 5})
        people = await self._employees(8)
        await self._assign([people[0]["id"]], TODAY, day_shift)
        await self._assign([p["id"] for p in people[1:8]], TODAY, night_shift)

        day = await self.store.demand_day(TODAY)
        by_shift = {item["id"]: item for item in day["shifts"]}
        self.assertEqual(by_shift[day_shift]["count"], 1)
        self.assertEqual(by_shift[day_shift]["target"], 2)
        self.assertEqual(by_shift[day_shift]["gap"], -1)
        self.assertEqual(by_shift[night_shift]["count"], 7)
        self.assertEqual(by_shift[night_shift]["gap"], 2)
        self.assertEqual(day["total"], 8)
        self.assertEqual(day["target_total"], 7)

        calendar = await self.store.month_calendar("2026-09")
        cells = {cell["business_date"]: cell for cell in calendar["days"]}
        cell = cells[TODAY]
        self.assertEqual(cell["counts"], {str(day_shift): 1, str(night_shift): 7})
        self.assertEqual(cell["gaps"], {str(day_shift): -1, str(night_shift): 2})
        self.assertEqual(cell["targets"], {str(day_shift): 2, str(night_shift): 5})
        self.assertEqual(cell["sources"][str(day_shift)], DEMAND_SOURCE_CONFIGURED)
        self.assertEqual(cell["target_source"], DEMAND_SOURCE_CONFIGURED)

    async def test_month_calendar_keeps_old_fields(self):
        """月历只加字段：老前端读的 counts / total / overridden / half_rest 全在。"""
        calendar = await self.store.month_calendar("2026-09")
        for key in (
            "month",
            "first_date",
            "today",
            "lead",
            "window_end",
            "days",
            "next_day_locked",
            "leave_lead_days",
            "shifts",
        ):
            self.assertIn(key, calendar)
        self.assertEqual(calendar["month"], "2026-09")
        self.assertEqual(len(calendar["days"]), 30)
        for day in calendar["days"]:
            for key in (
                "business_date",
                "day",
                "is_today",
                "counts",
                "total",
                "overridden",
                "half_rest",
                "targets",
                "gaps",
                "sources",
                "target_source",
            ):
                self.assertIn(key, day)

    async def test_inbox_days_carry_targets(self):
        """待办里的请假卡带当天目标：前端才能算「批了会缺几个人」。"""
        employee = (await self._employees(1))[0]
        day_shift = await self._shift_id("白班")
        await self.store.set_demand_defaults({str(day_shift): 3})
        await self._assign([employee["id"]], TODAY, day_shift)
        await self.store.submit_leave(employee["id"], TODAY, None, "家里有事")

        inbox = await self.store.inbox()
        self.assertTrue(inbox["requests"])
        day = inbox["requests"][0]["days"][0]
        self.assertEqual(day["targets"][str(day_shift)], 3)
        self.assertEqual(day["sources"][str(day_shift)], DEMAND_SOURCE_CONFIGURED)
        self.assertEqual(day["counts"][str(day_shift)], 1)
        self.assertEqual(day["target_source"], DEMAND_SOURCE_CONFIGURED)

    async def test_invalid_inputs_use_service_layer_codes(self):
        """人数越界 / 未知班次 / 坏日期都由服务层那道闸挡（前端拿到中文 400）。"""
        day_shift = await self._shift_id("白班")
        cases = [
            ({str(day_shift): -1}, "invalid_headcount"),
            ({str(day_shift): MAX_DEMAND_HEADCOUNT + 1}, "invalid_headcount"),
            ({str(day_shift): True}, "invalid_headcount"),
            ({"999999": 3}, "unknown_shift_id"),
        ]
        for headcounts, code in cases:
            with self.assertRaises(SchedulingError) as caught:
                await self.store.set_demand_defaults(headcounts)
            self.assertEqual(caught.exception.code, code)

        with self.assertRaises(SchedulingError) as caught:
            await self.store.set_demand_day("2026-13-01", None)
        self.assertEqual(caught.exception.code, "invalid_business_date")

        with self.assertRaises(SchedulingError) as caught:
            await self.store.demand_day("not-a-date")
        self.assertEqual(caught.exception.code, "invalid_business_date")
