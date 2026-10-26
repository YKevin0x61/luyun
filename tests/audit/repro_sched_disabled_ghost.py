#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缺陷：**已停用（离职）的人仍被算进月历 / 当日名单的「上班人数」，周表却把他排除**。

同一份 `staff_assignments`，三处读口两套口径：

* `month_calendar` / `day_detail` / `my_day`：直接数 `staff_assignments` 行，**不看花名册状态**
  —— 停用的人照样在「白班 3 人」「当日名单」里，人数因此虚高；
* `week_grid`：先过滤 `disabled` / 未批准，并在 `excluded` 里报「另有 N 人已停用」。

停用一个已经铺过 90 天的员工（`EmployeeAccounts.disable` 不会删 `staff_assignments`
行，排班层也没有任何停用判据），于是：
- 店长在月历上看到的人数包含一个已经离职的人 → 以为人手够；
- 员工端 `/api/scheduling/me/day`（`my_day` 直读 `day_detail`）看到已离职的同事「明天上班」。

预期：同一份排班在三处读口对「谁在上班」用同一个名单判据（周表那一套），或者停用时
把未来的结果行一起清掉（产品决定，但当前是**没有任何一处**处理）。

确认后修哪里：`services/scheduling/store.py` 的 `month_calendar` / `day_detail`（及其分叉
`my_day`）按花名册过滤，或 `services/identity` 停用员工时清未来行。

跑法：`.venv/bin/python -m pytest tests/audit/repro_sched_disabled_ghost.py -x -q`
"""

import asyncio
import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.accounts import EmployeeAccounts
from services.scheduling.store import SchedulingStore

PASSWORD = "password123"
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
DAY = "2026-09-28"


class DisabledGhostTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.accounts = EmployeeAccounts(self.db, now=lambda: FIXED_NOW)
        self.store = SchedulingStore(self.db, now=lambda: FIXED_NOW)
        await self.store.prepare()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _employee(self, phone: str, name: str) -> int:
        employee = await self.accounts.register(phone, PASSWORD, name)
        await self.accounts.approve(employee["id"])
        return int(employee["id"])

    async def test_disabled_employee_still_counts_in_calendar_but_not_in_week_grid(self):
        stay = await self._employee("13800138000", "张三")
        leave = await self._employee("13800138001", "李四")
        shift_id = (await self.store.list_shifts())[0]["id"]
        await self.store.set_rule(stay, [shift_id])
        await self.store.set_rule(leave, [shift_id])
        await self.store.expand()

        # 李四离职：停用只改花名册，排班结果行照旧躺在 90 天窗口里。
        await self.accounts.disable(leave)

        calendar = await self.store.month_calendar("2026-09")
        day = [item for item in calendar["days"] if item["business_date"] == DAY][0]
        detail = await self.store.day_detail(DAY)
        week = await self.store.week_grid("2026-09-28")

        listed_in_week = {person["id"] for person in week["employees"]}
        on_duty_in_detail = {
            person["id"] for group in detail["groups"] for person in group["people"]
        }
        self.assertNotIn(leave, listed_in_week)  # 周表：已停用的人不在表里

        self.assertEqual(
            day["total"],
            1,
            "月历把已停用的人也算成「上班」：这天 total="
            f"{day['total']}（实际在职 1 人），当日名单里有 "
            f"{sorted(on_duty_in_detail)}（周表只有 {sorted(listed_in_week)}）",
        )


if __name__ == "__main__":
    unittest.main()
