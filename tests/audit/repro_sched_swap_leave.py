#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缺陷：**换班批准会把对方「已批准的请假」直接抹掉**（批量清除 / 周表反而拒绝动它）。

`services/scheduling/store.py` 里「覆盖记录 `kind='leave'` 就是那条批过的假」这条规矩，
只在两处被认：批量刷格子的 `leave_approved` 与前端周表的落笔筛选。换班这条链路一次都
没查过 `_leave_ids`：

- `submit_swap` 只判申请人那天「有没有一行」（`_day_shift(...)[0]`），请假那天的结果
  行也在（`shift_id` 为空），所以**请假中的那天可以拿去跟同事换**；
- `_approve_swap` 在批准时拿两人**当下**的班直接对调，用 `kind='swap'` 覆盖两人的
  覆盖记录与结果行 —— 对方那天如果批过假，那条 `kind='leave'` 就被 `kind='swap'` 盖掉，
  对方从「请假」变成「来上申请人的班」。

预期：与批量清除同一口径 —— 覆盖记录 `kind='leave'` 的那天不许被换班改写
（`leave_approved`），且申请人那天应当**真有班次**才谈得上换。

确认后修哪里：`submit_swap` 的 `_day_shift` 判据收紧成「有班次」；
`_approve_swap` 写之前用 `_leave_ids([employee_id, peer_id], day)` 拦一次。

跑法：`.venv/bin/python -m pytest tests/audit/repro_sched_swap_leave.py -x -q`
"""

import asyncio
import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.accounts import EmployeeAccounts
from services.scheduling.store import SchedulingError, SchedulingStore

PASSWORD = "password123"
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
DAY = "2026-09-28"  # 下周一：两人规则都铺到，且员工能提申请


class SwapOverLeaveTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_a_swap_must_not_overwrite_the_peers_approved_leave(self):
        a = await self._employee("13800138000", "张三")
        b = await self._employee("13800138001", "李四")
        shift_id = (await self.store.list_shifts())[0]["id"]
        await self.store.set_rule(a, [shift_id])
        await self.store.set_rule(b, [shift_id])
        await self.store.expand()

        # 李四那天请了假并被批准（覆盖记录 kind=leave、没有班次）。
        leave = await self.store.submit_leave(b, DAY, DAY, "家里有事")
        await self.store.approve_request(leave["id"])
        detail = await self.store.day_detail(DAY)
        self.assertEqual(
            [person["id"] for person in detail["off_people"] if person["leave"]], [b]
        )

        # 张三拿这一天去跟李四换班：李四同意、店长批准（全链路都是正常操作）。
        swap = await self.store.submit_swap(a, b, DAY)
        await self.store.answer_swap(b, swap["id"], True)
        approved = await self.store.approve_request(swap["id"])
        self.assertEqual(approved["status"], "approved")

        after = await self.store.day_detail(DAY)
        still_on_leave = [person["id"] for person in after["off_people"] if person["leave"]]
        working = [
            person["id"]
            for group in after["groups"]
            for person in group["people"]
            if person["id"] == b
        ]
        self.assertIn(
            b,
            still_on_leave,
            "换班批准把李四已批准的假抹掉了：他现在 "
            f"{'上白班' if working else '不在上班组'}（working={working}），"
            f"请假组={still_on_leave}；scheduling_requests 里那条假仍是 approved —— "
            "已批的假被排班事实覆盖",
        )


if __name__ == "__main__":
    unittest.main()
