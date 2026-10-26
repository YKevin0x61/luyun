#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缺陷：**加班审批的「底薪快照」写入在写锁之外，与批准不是同一个事务**。

`OvertimeLedger.approve()` 的顺序是：
1. 读该月快照 → 读档案底薪 → `_write_snapshot(...)`（INSERT，**此时还没拿写锁**）；
2. 才进 `_decide()`（`@serialized_write` 拿全局写锁 → `_claim` 改状态 → commit）。

`_write_snapshot` 之前那句 docstring 说「调用方把这一条 INSERT 与状态变更收在同一个事务
里」—— 事务确实还没提交，但**锁还没拿到**。全局写锁存在的意义就是让同一个连接上的写
互不交错（`db_core/connection.py`：`_write_lock`）；这一段落在锁外，于是这条未提交的
INSERT 会被**任何并发写请求的收尾**带上：
- 并发请求成功 commit → 这一笔「还没批准」的底薪快照被提前提交（孤儿快照）；
- 并发请求失败 rollback（`serialized_write` 在异常时无条件 `rollback()`）→ 这一笔快照
  被丢掉，随后 `_decide` 的 UPDATE 照旧提交 → **批准成功、快照消失**。

第二种是钱上的错：`monthly_stats()` 对没有快照的人给 `amount=None` 并计入 `unpriced`
（「算不出来」），这一笔已经批准的加班费就此算不出来；而且该月之后第一次审批会用
**那时**的底薪补写快照 —— 月中调薪会把前面那笔按新底薪算。

下面的用例把「并发请求在拿到锁之前回滚」这一步显式做出来（`_write_snapshot` 之后挂起，
测试里执行一次 `rollback()` —— 正是 `services/identity/accounts.py` 的
`serialized_write` 在异常时做的事），然后放行。**没有改产品代码**。

确认后修哪里：`services/overtime/ledger.py` 的 `approve()` —— 把快照读/写与 `_decide`
一起放进同一个 `@serialized_write` 里（或者把 `_write_snapshot` 挪到 `_decide` 的锁内）。

跑法：`.venv/bin/python -m pytest tests/audit/repro_sched_snapshot_race.py -x -q`
"""

import asyncio
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.accounts import EmployeeAccounts
from services.overtime.ledger import EntryActor, OvertimeLedger

PASSWORD = "password123"
PHONE = "13800138000"
NAME = "张三"
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-24"


class SnapshotRaceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.accounts = EmployeeAccounts(self.db, now=lambda: FIXED_NOW)
        self.ledger = OvertimeLedger(self.db, now=lambda: FIXED_NOW)
        employee = await self.accounts.register(PHONE, PASSWORD, NAME)
        await self.accounts.approve(employee["id"])
        await self.accounts.update_fields(
            employee["id"], base_salary=5100, hire_date="2024-03-01"
        )
        self.employee_id = int(employee["id"])

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_approval_and_salary_snapshot_must_be_one_transaction(self):
        entry = await self.ledger.submit(
            EntryActor(employee_id=self.employee_id), TODAY, 4, "加班"
        )

        real_write_snapshot = OvertimeLedger._write_snapshot
        inserted = asyncio.Event()
        proceed = asyncio.Event()

        async def gated_write_snapshot(ledger_self, employee_id, month, base_salary):
            await real_write_snapshot(ledger_self, employee_id, month, base_salary)
            # 插完了、但还没拿写锁 —— 让并发请求先把它的事务收掉。
            inserted.set()
            await proceed.wait()

        with patch.object(OvertimeLedger, "_write_snapshot", gated_write_snapshot):
            approving = asyncio.create_task(
                self.ledger.approve(EntryActor(), entry["id"])
            )
            await asyncio.wait_for(inserted.wait(), timeout=5)
            # 并发写请求失败时的收尾（`serialized_write` 的 except 分支）：无条件 rollback。
            await self.db._conn.rollback()
            proceed.set()
            approved = await asyncio.wait_for(approving, timeout=5)

        self.assertEqual(approved["status"], "approved")
        snapshot = await self.ledger.salary_snapshot(self.employee_id, TODAY[:7])
        stats = await self.ledger.monthly_stats(TODAY[:7])
        unpriced = [item for item in stats["items"] if item["amount"] is None]
        self.assertIsNotNone(
            snapshot,
            "批准已提交，但那一笔计费底薪快照被并发回滚吞掉了："
            f"salary_snapshot={snapshot}，monthly_stats 里 {len(unpriced)} 人 amount=None"
            f"（unpriced={stats['total']['unpriced']}）—— 这笔已批准的加班费算不出来",
        )


if __name__ == "__main__":
    unittest.main()
