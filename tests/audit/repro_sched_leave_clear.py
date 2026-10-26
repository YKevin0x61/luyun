#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缺陷：**单格撤销覆盖会把「已批的请假」抹掉，而批量清除会拒绝同一件事**。

预期（`services/scheduling/store.py` 的 `set_overrides_batch` 口径，前端
`admin-web/src/utils/schedulingWeekPick.js` 也有同名判据）：覆盖记录 `kind=leave` 就是
那条被批准的假，任何「清除 / 退回规则」都不许删掉它 —— 批量清除对同一格返回
`skipped: leave_approved`。

实际（`SchedulingStore.clear_override` / `DELETE /api/scheduling/overrides/{员工}/{营业日}`）：
没有这道判据，`_delete_override_row` 直接删掉那条 `kind=leave` 的记录，再用**现在的
规则**把那天重写成上班（或删成「还没铺到」）。结果：
- 员工手机上那天从「请假」变回「上班 / 休」（`my_days` 的 `leave` 变 False）；
- 当日名单（`day_detail`）不再把这个人列进「请假」组；
- 而 `scheduling_requests` 里那条申请仍然是 `approved` —— 审批记录与排班事实互相矛盾。

这个缺陷本轮被放大：`19b9bf9` 放开「过去的日子也能清除」后，可补记窗口内的历史请假
（过去 30 天）同样能被这样抹掉。

确认后修哪里：`services/scheduling/store.py` 的 `clear_override`（以及它复用的
`_clear_override`）在删之前查一次 `_leave_ids([employee_id], day)`，命中就抛
`SchedulingError("leave_approved", ...)`，与批量端点同一口径、同一词表。

跑法：`.venv/bin/python -m pytest tests/audit/repro_sched_leave_clear.py -x -q`
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
PHONE = "13800138000"
NAME = "张三"
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
# 明天：审批后写覆盖行，本轮放开后它同样落在「可补记窗口」里（也顺带覆盖过去的日子）。
TOMORROW = "2026-09-25"


class LeaveClearTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.accounts = EmployeeAccounts(self.db, now=lambda: FIXED_NOW)
        self.store = SchedulingStore(self.db, now=lambda: FIXED_NOW)
        await self.store.prepare()
        self.employee_id = await self._employee()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _employee(self) -> int:
        employee = await self.accounts.register(PHONE, PASSWORD, NAME)
        await self.accounts.approve(employee["id"])
        await self.accounts.update_fields(employee["id"], base_salary=5100)
        return int(employee["id"])

    async def test_single_cell_clear_must_not_erase_an_approved_leave(self):
        shift_id = (await self.store.list_shifts())[0]["id"]
        await self.store.set_rule(self.employee_id, [shift_id])

        # 员工请假 → 店长批准（覆盖记录 kind=leave，那天没有班次）。
        request = await self.store.submit_leave(
            self.employee_id, TOMORROW, TOMORROW, "家里有事"
        )
        approved = await self.store.approve_request(request["id"])
        self.assertEqual(approved["applied_days"], [TOMORROW])

        detail = await self.store.day_detail(TOMORROW)
        leavers = [person for person in detail["off_people"] if person["leave"]]
        self.assertEqual([person["id"] for person in leavers], [self.employee_id])

        # 对照组：批量「清除班次」明确拒绝动这一天（这才是全仓的口径）。
        batch = await self.store.set_overrides_batch(
            "clear", items=[{"employee_id": self.employee_id, "business_date": TOMORROW}]
        )
        self.assertEqual(batch["applied"], 0)
        self.assertEqual(
            [(item["employee_id"], item["business_date"], item["reason"]) for item in batch["skipped"]],
            [(self.employee_id, TOMORROW, "leave_approved")],
        )

        # 缺陷：单格撤销不做同一条判断，把已批的假抹掉。
        raised = None
        try:
            await self.store.clear_override(self.employee_id, TOMORROW)
        except SchedulingError as exc:  # 预期走这里（与批量同一条闸）
            raised = exc
        if raised is None:
            after = await self.store.day_detail(TOMORROW)
            on_duty = [
                person
                for group in after["groups"]
                for person in group["people"]
                if person["id"] == self.employee_id
            ]
            still_leave = [
                person for person in after["off_people"] if person["leave"]
            ]
            request_status = [
                row["status"]
                for row in (await self.store.my_requests(self.employee_id))["requests"]
                if int(row["id"]) == int(request["id"])
            ]
        self.assertIsNotNone(
            raised,
            "单格撤销没有拒绝抹掉已批的请假：覆盖记录被删，那天被重写成规则班次 "
            f"（改动后仍在上班组：{bool(on_duty)}，请假组：{still_leave}），"
            f"而 scheduling_requests 仍是 {request_status} —— 审批记录与排班事实矛盾",
        )
        self.assertEqual(raised.code, "leave_approved")


if __name__ == "__main__":
    unittest.main()
