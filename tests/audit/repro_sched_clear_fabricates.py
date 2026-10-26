#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缺陷：**「清除班次（退回规则）」在从未铺过的过去日子里会凭空写出一行「上班」**。

`19b9bf9` 放开了「过去的日子也能清除」（`clear_override` 的下界从 `today` 改成
`_backfill_floor`）。但清除的实现是「删覆盖记录 + 按**现在的规则**重算那天」
（`_restore_day`）：

* 那天有规则 → `shift_id = _cycle_shift(rule, day)`，然后 `_write_day_row(...)`
  **有则改、无则插**；
* 展开窗口是 `[今天, 今天+89]`，**过去的日子从来没有被铺过**（除非它当年是「今天」），
  所以「从没排过班」的过去日子在 `staff_assignments` 里本来没有行。

于是：店长在周表上选一片过去的日子点「退回规则」（批量 `clear`，最多 200 格；
单格 `DELETE /api/scheduling/overrides/{员工}/{营业日}` 同理），对那些**既没有覆盖、
也没有结果行**的日子，服务层不是「什么都不做」，而是按现在的轮转规则**补写一行上班**。
后果：
- 考勤表那天的状态从「待补」变成「上班」，`_leave_terms` 的**实际出勤天数 +1**，
  月例休 / 存假跟着变（当月是暂算值，改的是全店的数）；
- 月历/当日名单把那天显示成「按规则上的班」，`source='rule'`、没有覆盖标记 —— 事后
  看不出这是被一次「清除」造出来的；
- 批量回执把这种格子计入 `applied`（「已清 N 格」），店长以为清掉了什么。

预期：「清除 / 退回规则」在没有覆盖记录、也没有结果行的日子上应当是 **no-op**
（不新增行）；过去的日子要补记，走 `set_override`（「改成某个班」）那条明示的路。

确认后修哪里：`services/scheduling/store.py` 的 `_restore_day` —— 先看那天原本有没有
结果行 / 覆盖记录，两者都没有就不要 INSERT（或让 `clear_override` 在 day < today 且
没有覆盖时直接返回）。

跑法：`.venv/bin/python -m pytest tests/audit/repro_sched_clear_fabricates.py -x -q`
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
PHONE = "13800138000"
NAME = "张三"
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
# 过去 23 天（≥ 可补记窗口下界 2026-08-25），且系统从没铺过这一天。
PAST_DAY = "2026-09-01"


class ClearFabricatesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.accounts = EmployeeAccounts(self.db, now=lambda: FIXED_NOW)
        self.store = SchedulingStore(self.db, now=lambda: FIXED_NOW)
        await self.store.prepare()
        employee = await self.accounts.register(PHONE, PASSWORD, NAME)
        await self.accounts.approve(employee["id"])
        self.employee_id = int(employee["id"])

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_clear_on_a_day_that_was_never_scheduled_must_be_a_no_op(self):
        shift_id = (await self.store.list_shifts())[0]["id"]
        await self.store.set_rule(self.employee_id, [shift_id])
        await self.store.expand()

        before = await self.store.day_detail(PAST_DAY)
        self.assertEqual((before["total"], before["off_count"]), (0, 0))
        # 这一天本来没有结果行（过去的日子从来没被展开过）。
        cur = await self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM staff_assignments WHERE business_date = ?",
            (PAST_DAY,),
        )
        self.assertEqual(int(dict(await cur.fetchone())["n"]), 0)

        # 店长在周表上对这一片过去的日子点「退回规则」（批量 clear）—— 这里取一格。
        result = await self.store.set_overrides_batch(
            "clear", items=[{"employee_id": self.employee_id, "business_date": PAST_DAY}]
        )

        after = await self.store.day_detail(PAST_DAY)
        fabricated = [
            person
            for group in after["groups"]
            for person in group["people"]
            if person["id"] == self.employee_id
        ]
        cur = await self.db._conn.execute(
            "SELECT source, shift_id FROM staff_assignments WHERE business_date = ?",
            (PAST_DAY,),
        )
        row = await cur.fetchone()
        self.assertEqual(
            (after["total"], after["off_count"]),
            (0, 0),
            "「清除班次」在从没铺过的过去日子里凭空造了一行班："
            f"applied={result['applied']}、skipped={result['skipped']}；"
            f"那天现在 total={after['total']}（{fabricated}），结果行={None if row is None else dict(row)}"
            " —— 考勤表会把这天算成「上班」，实际出勤天数与存假跟着变，"
            "而且 source='rule' 看不出是被清除动作造出来的",
        )


if __name__ == "__main__":
    unittest.main()
