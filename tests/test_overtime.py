#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加班与补钟登记·员工那一面（票 01）：提交、看自己的记录、撤回。

主缝 = 服务层 + 真库 + 注入时钟（`.scratch/overtime-and-reminders/spec.md` 的
「Testing Decisions」）：申报窗口由**自然日**决定（不是 06:00 的营业日），所以时钟
必须能拨；HTTP 面另有一小段薄缝，只回答「谁能打、打进去会怎样」。

术语与口径见 `CONTEXT.md` 的「加班与补钟登记」「净时长」，决策理由见
`docs/adr/0100-overtime-and-makeup-ledger.md`。
"""

import asyncio
import tempfile
import unittest
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.admin as admin_module
import api.auth as auth_module
import api.overtime as overtime_module
import main as main_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from db_core.schema import ADMIN_READ_ONLY_TABLES, OVERTIME_TABLES
from services.app_runtime import AppRuntime, set_runtime
from services.business_day import current_business_date
from services.hygiene.accounts import EmployeeAccounts
from services.overtime.ledger import (
    MAX_REASON,
    EntryActor,
    OvertimeError,
    OvertimeLedger,
)

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
# 2026-09-24 10:00（北京时）→ 自然日的今天 = 9/24、昨天 = 9/23。
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-24"
YESTERDAY = "2026-09-23"


class OvertimeLedgerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.accounts = EmployeeAccounts(self.db, now=lambda: FIXED_NOW)
        self.ledger = OvertimeLedger(self.db, now=lambda: FIXED_NOW)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _employee(self, phone="13800138000", name="张三", salary=5100):
        """一个已批准的在职员工。

        票 03 起**默认补上底薪**：审批会写该月的计费底薪快照，而底薪待补的人批不了
        （`docs/adr/0100` 的后果、票 03 的验收 3）—— 所以测试里的「普通员工」就该是
        档案齐全的那个。要造「待补」的人传 `salary=None`。
        """
        employee = await self.accounts.register(phone, PASSWORD, name)
        await self.accounts.approve(employee["id"])
        if salary is not None:
            await self.accounts.update_fields(
                employee["id"], base_salary=salary, hire_date="2024-03-01"
            )
        return employee

    async def test_employee_submits_overtime_and_sees_it_pending(self):
        """验收 1：提交一笔加班，立刻在「我的记录」里看到，状态是待审批。"""
        employee = await self._employee()
        actor = EntryActor(employee_id=employee["id"])

        created = await self.ledger.submit(actor, TODAY, 13, "中秋加班")

        self.assertEqual(created["status"], "pending")
        self.assertEqual(created["entry_date"], TODAY)
        self.assertEqual(created["half_hours"], 13)
        self.assertEqual(created["kind"], "overtime")
        self.assertEqual(created["reason"], "中秋加班")

        mine = await self.ledger.list_mine(employee["id"])
        self.assertEqual([row["id"] for row in mine["entries"]], [created["id"]])
        self.assertEqual(mine["today"], TODAY)
        self.assertEqual(mine["yesterday"], YESTERDAY)

    async def test_makeup_is_the_same_ledger_with_a_negative_hours(self):
        """验收 1：补钟记负数 —— 加班与补钟是同一张台账，**符号就是类型**（不存 kind）。"""
        employee = await self._employee()
        actor = EntryActor(employee_id=employee["id"])

        overtime = await self.ledger.submit(actor, TODAY, 13, "中秋加班")
        makeup = await self.ledger.submit(actor, YESTERDAY, -4, "前天早退两小时")

        self.assertEqual(overtime["kind"], "overtime")
        self.assertEqual(overtime["hours"], 6.5)
        self.assertEqual(makeup["kind"], "makeup")
        self.assertEqual(makeup["half_hours"], -4)
        self.assertEqual(makeup["hours"], -2)

        mine = await self.ledger.list_mine(employee["id"])
        # 新的在前：今天那条排在昨天那条前面。
        self.assertEqual(
            [row["id"] for row in mine["entries"]], [overtime["id"], makeup["id"]]
        )

    async def test_employee_can_only_report_today_and_yesterday(self):
        """验收 3：员工只能报今天与昨天（自然日），前天或更早被拒。"""
        employee = await self._employee()
        actor = EntryActor(employee_id=employee["id"])

        self.assertEqual(
            (await self.ledger.submit(actor, YESTERDAY, 2, "昨天加班"))["entry_date"],
            YESTERDAY,
        )
        for early in ("2026-09-22", "2026-08-01"):
            with self.assertRaises(OvertimeError) as caught:
                await self.ledger.submit(actor, early, 2, "前天加班")
            self.assertEqual(caught.exception.code, "past_window")

    async def test_window_is_a_calendar_day_not_the_six_am_business_day(self):
        """验收 3 的口径：窗口按**自然日**（零点切），不是 06:00 的营业日。

        凌晨两点提交时，营业日还停在前一天 —— 窗口要是误用了营业日，「今天」那一笔
        会被当成明天而拒掉（`docs/adr/0100` 的 _Avoid_ 点名了这一条）。所以这一条
        把两个日期**并排**验一次：自然日的今天是 9/24，营业日仍然是 9/23。
        """
        employee = await self._employee()
        night = datetime(2026, 9, 24, 2, 0, tzinfo=CHINA_TZ)
        ledger = OvertimeLedger(self.db, now=lambda: night)
        actor = EntryActor(employee_id=employee["id"])

        self.assertEqual(current_business_date(night), "2026-09-23")  # 营业日还在前一天
        self.assertEqual(ledger.today(), "2026-09-24")  # 自然日已经是这一天
        self.assertEqual(ledger.yesterday(), "2026-09-23")

        entry = await ledger.submit(actor, "2026-09-24", 2, "凌晨那笔加班")
        self.assertEqual(entry["entry_date"], "2026-09-24")

    async def test_backfill_window_follows_the_submitter(self):
        """窗口按**提交人**判：有能力的店长与超管可补录任意过去日期，未来日期谁都收。

        这一条只走服务层：票 01 的 HTTP 面还没有店长那条路（票 04 才开），但判据
        必须一次写对 —— 它是同一个 `submit()` 里的一个分支。
        """
        employee = await self._employee()
        manager = await self._employee(phone="13800138001", name="李四")
        backfiller = EntryActor(employee_id=manager["id"], can_backfill=True)

        backfilled = await self.ledger.submit(
            backfiller, "2026-08-01", 8, "上月忘登的加班", target_employee_id=employee["id"]
        )
        self.assertEqual(backfilled["entry_date"], "2026-08-01")
        self.assertEqual(backfilled["employee_id"], employee["id"])
        # 代录记的是**代录人**，不是被代录的那个人。
        self.assertEqual(backfilled["created_by"], f"staff:{manager['id']}")

        by_super = await self.ledger.submit(
            EntryActor(), "2026-07-15", -3, "上月补钟", target_employee_id=employee["id"]
        )
        self.assertEqual(by_super["created_by"], "super")

        for actor in (EntryActor(employee_id=employee["id"]), backfiller, EntryActor()):
            with self.assertRaises(OvertimeError) as caught:
                await self.ledger.submit(
                    actor, "2026-09-25", 2, "明天加班", target_employee_id=employee["id"]
                )
            self.assertEqual(caught.exception.code, "future_day")

    async def test_mine_shows_only_my_own_entries(self):
        """验收 5 的前半：员工会话读到的只有自己那几笔。"""
        me = await self._employee()
        other = await self._employee(phone="13800138001", name="李四")

        mine = await self.ledger.submit(EntryActor(employee_id=me["id"]), TODAY, 3, "我加班")
        await self.ledger.submit(EntryActor(employee_id=other["id"]), TODAY, 5, "别人加班")

        rows = (await self.ledger.list_mine(me["id"]))["entries"]
        self.assertEqual([row["id"] for row in rows], [mine["id"]])

    async def test_hours_shape_is_half_hour_steps_within_the_cap(self):
        """验收 2：时长按 0.5 小时加减；0 拒、超过 ±12 小时拒、事由必填、日期要真日期。"""
        employee = await self._employee()
        actor = EntryActor(employee_id=employee["id"])

        # 0.5 的整数倍都收：1 = 半小时、3 = 1.5 小时、±24 = ±12 小时（上限本身合法）。
        for half_hours in (1, 3, -1, 13, 24, -24):
            entry = await self.ledger.submit(actor, TODAY, half_hours, "半步也算加班")
            self.assertEqual(entry["half_hours"], half_hours)

        for bad, code in (
            (0, "zero_half_hours"),
            (25, "hours_too_large"),
            (-25, "hours_too_large"),
            (2.5, "invalid_half_hours"),  # 1.25 小时不是 0.5 的整数倍
            ("abc", "invalid_half_hours"),
            (None, "invalid_half_hours"),
        ):
            with self.assertRaises(OvertimeError) as caught:
                await self.ledger.submit(actor, TODAY, bad, "半步也算加班")
            self.assertEqual(caught.exception.code, code, bad)

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.submit(actor, TODAY, 2, "   ")
        self.assertEqual(caught.exception.code, "missing_reason")

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.submit(actor, TODAY, 2, "字" * (MAX_REASON + 1))
        self.assertEqual(caught.exception.code, "reason_too_long")

        for bad_day in ("2026-9-24", "20260924", "", None, "2026-02-30"):
            with self.assertRaises(OvertimeError) as caught:
                await self.ledger.submit(actor, bad_day, 2, "日期打错了")
            self.assertEqual(caught.exception.code, "invalid_date", bad_day)

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.submit(
                EntryActor(can_backfill=True), TODAY, 2, "查无此人", target_employee_id=999999
            )
        self.assertEqual(caught.exception.code, "unknown_employee")

    async def test_employee_cancels_own_pending_entry(self):
        """验收 4：撤回自己待审批的那笔 —— 状态变已撤回，管理端的待办里就没有它了。"""
        employee = await self._employee()
        entry = await self.ledger.submit(
            EntryActor(employee_id=employee["id"]), TODAY, 4, "临时加班"
        )

        cancelled = await self.ledger.cancel(employee["id"], entry["id"])

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNotNone(cancelled["decided_at"])
        # 撤回不是删除：记录还在，员工自己的列表里看得见它已经撤回了。
        rows = (await self.ledger.list_mine(employee["id"]))["entries"]
        self.assertEqual(
            [(row["id"], row["status"]) for row in rows], [(entry["id"], "cancelled")]
        )

    async def test_cancel_only_works_on_my_own_pending_entry(self):
        """验收 5：拿别人的 id 撤回必须失败；撤过一次的不能再撤。"""
        me = await self._employee()
        other = await self._employee(phone="13800138001", name="李四")
        mine = await self.ledger.submit(EntryActor(employee_id=me["id"]), TODAY, 4, "我的加班")

        # 别人的登记一律当作「不存在」：不告诉调用方「它在，但不是你的」。
        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.cancel(other["id"], mine["id"])
        self.assertEqual(caught.exception.code, "unknown_entry")

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.cancel(me["id"], 999999)
        self.assertEqual(caught.exception.code, "unknown_entry")

        # 状态机只往前走：撤过一次就不能再撤。
        await self.ledger.cancel(me["id"], mine["id"])
        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.cancel(me["id"], mine["id"])
        self.assertEqual(caught.exception.code, "entry_not_pending")

    async def _approve_raw(self, entry_id: int) -> None:
        """直接把状态标成已批准 —— 票 02 才有点头的地方，这一票只借它的结果造前置状态。"""
        stamp = FIXED_NOW.isoformat()
        await self.db._conn.execute(
            "UPDATE overtime_entries SET status = 'approved', decided_at = ?, updated_at = ?"
            " WHERE id = ?",
            (stamp, stamp, int(entry_id)),
        )
        await self.db._conn.commit()

    async def test_monthly_summary_splits_pending_from_approved(self):
        """员工端「每个月的加班时长」：净时长 = 加班与补钟相抵，待审批与已批准分开。"""
        employee = await self._employee()
        actor = EntryActor(employee_id=employee["id"])
        approved = await self.ledger.submit(actor, YESTERDAY, 13, "已批的加班")
        await self._approve_raw(approved["id"])
        await self.ledger.submit(actor, TODAY, -4, "待批的补钟")
        # 上个月那笔只能由管理端补录（员工本人的窗口够不着它）—— 顺带钉住
        # 「代录的登记进被代录那个人的月度合计」。
        await self.ledger.submit(
            EntryActor(), "2026-08-31", 6, "上个月那笔", target_employee_id=employee["id"]
        )

        mine = await self.ledger.list_mine(employee["id"])
        summary = mine["summary"]
        self.assertEqual(summary["month"], "2026-09")
        self.assertEqual(summary["approved"]["overtime_half_hours"], 13)
        self.assertEqual(summary["approved"]["makeup_half_hours"], 0)
        self.assertEqual(summary["approved"]["net_half_hours"], 13)
        self.assertEqual(summary["pending"]["net_half_hours"], -4)
        # 上个月那笔不混进本月，但换个月份问得出来。
        august = (await self.ledger.list_mine(employee["id"], "2026-08"))["summary"]
        self.assertEqual(august["month"], "2026-08")
        self.assertEqual(august["pending"]["net_half_hours"], 6)
        self.assertEqual(august["approved"]["net_half_hours"], 0)

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.list_mine(employee["id"], "2026-9")
        self.assertEqual(caught.exception.code, "invalid_month")

    # ── 票 02：审批（批准 / 驳回 / 作废）与全店待办 ─────────────────────────

    async def test_approve_moves_pending_to_approved_and_stamps_the_reviewer(self):
        """验收 1：待审批能被批准；判的人与判的时间落在这一行上。"""
        employee = await self._employee()
        entry = await self.ledger.submit(
            EntryActor(employee_id=employee["id"]), TODAY, 4, "下午加班"
        )

        approved = await self.ledger.approve(EntryActor(), entry["id"])

        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["decided_by"], "super")
        self.assertIsNotNone(approved["decided_at"])
        self.assertEqual(approved["half_hours"], 4)

    async def test_reject_needs_a_reason_and_keeps_it(self):
        """验收 1：驳回不写理由被拒；写了就存在那一行上，员工看得到为什么。"""
        employee = await self._employee()
        entry = await self.ledger.submit(
            EntryActor(employee_id=employee["id"]), TODAY, 3, "加班"
        )

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.reject(EntryActor(), entry["id"], "   ")
        self.assertEqual(caught.exception.code, "missing_reject_reason")
        # 被拒的那一次一个字都没写：它还在待审批里等下一次判。
        mine = (await self.ledger.list_mine(employee["id"]))["entries"]
        self.assertEqual((mine[0]["status"], mine[0]["reject_reason"]), ("pending", None))

        rejected = await self.ledger.reject(EntryActor(), entry["id"], "那天没有排班")
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["reject_reason"], "那天没有排班")
        self.assertEqual(rejected["decided_by"], "super")

        # 状态机只往前走：已经驳过的不再动。
        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.reject(EntryActor(), entry["id"], "再驳一次")
        self.assertEqual(caught.exception.code, "entry_not_pending")

    async def test_void_only_applies_to_approved_entries(self):
        """验收 3：只有已批准的能作废；作废后记录不消失、看得出被作废过。"""
        employee = await self._employee()
        entry = await self.ledger.submit(
            EntryActor(employee_id=employee["id"]), TODAY, 5, "加班"
        )

        # 还没批的作废不了：作废撤销的是「已经点过的头」，不是「别急」。
        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.void(EntryActor(), entry["id"])
        self.assertEqual(caught.exception.code, "entry_not_approved")

        await self.ledger.approve(EntryActor(), entry["id"])
        voided = await self.ledger.void(EntryActor(), entry["id"])

        self.assertEqual(voided["status"], "voided")
        self.assertEqual(voided["decided_by"], "super")
        self.assertIsNotNone(voided["decided_at"])
        # 作废不是删除：那一行还在员工的列表里，状态是已作废。
        rows = (await self.ledger.list_mine(employee["id"]))["entries"]
        self.assertEqual(
            [(row["id"], row["status"]) for row in rows], [(entry["id"], "voided")]
        )

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.void(EntryActor(), entry["id"])
        self.assertEqual(caught.exception.code, "entry_not_approved")

    # ── 票 03：底薪快照（该月第一次审批定音）────────────────────────────────

    async def test_approve_writes_the_month_snapshot_once(self):
        """验收 1 + 2：该月第一次审批写下快照；月中调薪不改已经算出的那一版。"""
        employee = await self._employee(salary=5100)
        actor = EntryActor(employee_id=employee["id"])
        first = await self.ledger.submit(actor, TODAY, 13, "中秋加班")
        second = await self.ledger.submit(actor, TODAY, 4, "又来一笔")

        await self.ledger.approve(EntryActor(), first["id"])
        self.assertEqual(
            await self.ledger.salary_snapshot(employee["id"], "2026-09"), 5100
        )

        # 月中调薪：第二笔审批不覆盖快照 —— 这个月的钱按第一次点头那一刻的底薪算。
        await self.accounts.update_fields(employee["id"], base_salary=6000)
        await self.ledger.approve(EntryActor(), second["id"])
        self.assertEqual(
            await self.ledger.salary_snapshot(employee["id"], "2026-09"), 5100
        )

    async def test_approve_is_blocked_while_the_base_salary_is_missing(self):
        """验收 3：底薪待补的人批不了（先补档案），且不写快照、不改状态。"""
        employee = await self._employee(salary=None)
        entry = await self.ledger.submit(
            EntryActor(employee_id=employee["id"]), TODAY, 4, "加班"
        )

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.approve(EntryActor(), entry["id"])
        self.assertEqual(caught.exception.code, "salary_missing")

        rows = (await self.ledger.list_mine(employee["id"]))["entries"]
        self.assertEqual(
            [(row["id"], row["status"]) for row in rows], [(entry["id"], "pending")]
        )
        self.assertIsNone(
            await self.ledger.salary_snapshot(employee["id"], "2026-09")
        )

    async def test_snapshot_follows_the_entry_month_not_the_approval_month(self):
        """验收 4：8 月 31 日的登记 9 月才批，快照落在 8 月那一版上。"""
        employee = await self._employee(salary=5100)
        august = await self.ledger.submit(
            EntryActor(), "2026-08-31", 6, "上月末加班", target_employee_id=employee["id"]
        )

        await self.ledger.approve(EntryActor(), august["id"])

        self.assertEqual(
            await self.ledger.salary_snapshot(employee["id"], "2026-08"), 5100
        )
        self.assertIsNone(
            await self.ledger.salary_snapshot(employee["id"], "2026-09")
        )

    # ── 票 03：按月统计与加班费 ─────────────────────────────────────────────

    async def test_monthly_stats_pay_per_person_and_the_total_row(self):
        """验收 6 + 8：金额 =（快照底薪 ÷ 该月天数 ÷ 8.5）× 净时长，每人取整到元；合计是各行相加。

        9 月 30 天：5100 元的时薪 = 5100 / 30 / 8.5 = 20 元，净 5 小时 → 100 元；
        8500 元 = 33.333… 元，净 2 小时 = 66.67 → **67 元**（四舍五入成整数元）；
        合计 167 元 —— 各人取整后再相加，不是拿全店小时数重算一遍。
        """
        me = await self._employee(salary=5100)
        other = await self._employee(phone="13800138001", name="李四", salary=8500)
        first = await self.ledger.submit(
            EntryActor(employee_id=me["id"]), YESTERDAY, 13, "加班"
        )
        second = await self.ledger.submit(
            EntryActor(employee_id=me["id"]), TODAY, -3, "补钟"
        )
        his = await self.ledger.submit(
            EntryActor(employee_id=other["id"]), TODAY, 4, "加班"
        )
        for entry in (first, second, his):
            await self.ledger.approve(EntryActor(), entry["id"])

        stats = await self.ledger.monthly_stats("2026-09")

        by_id = {row["employee_id"]: row for row in stats["items"]}
        self.assertEqual(stats["month"], "2026-09")
        self.assertEqual(stats["days"], 30)
        self.assertEqual(stats["hours_per_day"], 8.5)
        self.assertEqual(by_id[me["id"]]["overtime_half_hours"], 13)
        self.assertEqual(by_id[me["id"]]["makeup_half_hours"], 3)
        self.assertEqual(by_id[me["id"]]["net_half_hours"], 10)
        self.assertEqual(by_id[me["id"]]["net_hours"], 5.0)
        self.assertEqual(by_id[me["id"]]["base_salary"], 5100)
        self.assertEqual(by_id[me["id"]]["amount"], 100)
        self.assertEqual(by_id[me["id"]]["employee_name"], "张三")
        self.assertEqual(by_id[other["id"]]["amount"], 67)
        self.assertEqual(stats["count"], 2)
        self.assertEqual(stats["total"]["amount"], 167)
        self.assertEqual(stats["total"]["net_half_hours"], 14)
        self.assertEqual(stats["total"]["unpriced"], 0)

    async def test_monthly_stats_counts_approved_only_and_never_goes_negative(self):
        """验收 5 + 7：净额为负算 0 元（不倒扣、不带下月）；另外四种状态都不进统计。"""
        employee = await self._employee(salary=5100)
        actor = EntryActor(employee_id=employee["id"])
        overtime = await self.ledger.submit(actor, TODAY, 2, "加班")
        makeup = await self.ledger.submit(actor, TODAY, -13, "补钟")
        await self.ledger.approve(EntryActor(), overtime["id"])
        await self.ledger.approve(EntryActor(), makeup["id"])
        # 待审批、已驳回、已撤回、已作废：一笔都不该出现在这个月的账上。
        await self.ledger.submit(actor, TODAY, 8, "还没批")
        rejected = await self.ledger.submit(actor, TODAY, 6, "被驳")
        await self.ledger.reject(EntryActor(), rejected["id"], "不算")
        withdrawn = await self.ledger.submit(actor, TODAY, 5, "撤回")
        await self.ledger.cancel(employee["id"], withdrawn["id"])
        voided = await self.ledger.submit(actor, TODAY, 4, "批了又作废")
        await self.ledger.approve(EntryActor(), voided["id"])
        await self.ledger.void(EntryActor(), voided["id"])

        stats = await self.ledger.monthly_stats("2026-09")

        row = stats["items"][0]
        self.assertEqual(row["overtime_half_hours"], 2)
        self.assertEqual(row["makeup_half_hours"], 13)
        self.assertEqual(row["net_half_hours"], -11)
        self.assertEqual(row["net_hours"], -5.5)
        # 净欠 5.5 小时：这个月没有加班费，也不倒扣工资、不带进下个月。
        self.assertEqual(row["amount"], 0)
        self.assertEqual(stats["total"]["amount"], 0)

    async def test_monthly_stats_leaves_the_amount_blank_without_a_snapshot(self):
        """没有快照 = 算不出来 → 金额留空，而不是显示成 0 元（同「待补」的诚实口径）。"""
        employee = await self._employee(salary=5100)
        entry = await self.ledger.submit(
            EntryActor(employee_id=employee["id"]), TODAY, 8, "加班"
        )
        # 绕过审批直接标成已批准：造一行「有已批准、却没有快照」的历史数据。
        await self._approve_raw(entry["id"])

        stats = await self.ledger.monthly_stats("2026-09")

        self.assertEqual(stats["items"][0]["amount"], None)
        self.assertEqual(stats["total"]["amount"], 0)
        self.assertEqual(stats["total"]["unpriced"], 1)

    async def test_monthly_stats_rejects_a_bad_month(self):
        """月份形状不对就说清楚，别拿它去 LIKE 出一堆奇怪的行。"""
        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.monthly_stats("2026-9")
        self.assertEqual(caught.exception.code, "invalid_month")

    async def test_pending_queue_is_shop_wide_with_names(self):
        """验收 1：管理端的待办是全店的 —— 谁提的、哪一天、多少小时、为什么。"""
        me = await self._employee()
        other = await self._employee(phone="13800138001", name="李四")
        first = await self.ledger.submit(
            EntryActor(employee_id=me["id"]), YESTERDAY, 3, "我加班"
        )
        second = await self.ledger.submit(
            EntryActor(employee_id=other["id"]), TODAY, -2, "他补钟"
        )
        # 撤回的、已经批过的都不在队列里：待办回答的是「还等着谁点头」。
        withdrawn = await self.ledger.submit(
            EntryActor(employee_id=me["id"]), TODAY, 1, "撤回掉"
        )
        await self.ledger.cancel(me["id"], withdrawn["id"])
        approved = await self.ledger.submit(
            EntryActor(employee_id=other["id"]), TODAY, 4, "批过的"
        )
        await self.ledger.approve(EntryActor(), approved["id"])

        pending = await self.ledger.list_pending()

        self.assertEqual(pending["count"], 2)
        # 旧的在前：待办是先来先处理，不是「最新动态」（同排班 inbox 的口径）。
        self.assertEqual(
            [row["id"] for row in pending["entries"]], [first["id"], second["id"]]
        )
        self.assertEqual(
            [row["employee_name"] for row in pending["entries"]], ["张三", "李四"]
        )
        self.assertEqual(pending["entries"][1]["kind"], "makeup")
        self.assertEqual(pending["today"], TODAY)

    async def test_admin_list_shows_everyone_newest_first(self):
        """验收 3 的前置：管理端要看得见**已经批过的**那几笔，作废才有地方点。"""
        me = await self._employee()
        other = await self._employee(phone="13800138001", name="李四")
        august = await self.ledger.submit(
            EntryActor(), "2026-08-31", 6, "上月忘登的加班", target_employee_id=me["id"]
        )
        september = await self.ledger.submit(
            EntryActor(employee_id=other["id"]), TODAY, -3, "迟到补钟"
        )
        approved = await self.ledger.submit(
            EntryActor(employee_id=me["id"]), TODAY, 4, "已批的加班"
        )
        await self.ledger.approve(EntryActor(), approved["id"])

        listing = await self.ledger.list_entries()

        self.assertEqual(listing["count"], 3)
        # 新的在前：管理端这一页是查账（队列那一页才是先来先处理）。
        self.assertEqual(
            [row["id"] for row in listing["entries"]],
            [approved["id"], september["id"], august["id"]],
        )
        self.assertEqual(listing["entries"][0]["employee_name"], "张三")
        self.assertEqual(listing["entries"][0]["status"], "approved")

        # 按月筛（自然月，同净时长的归月口径）：8 月只有代录的那一笔。
        august_only = await self.ledger.list_entries(month="2026-08")
        self.assertEqual([row["id"] for row in august_only["entries"]], [august["id"]])
        # 按状态筛：还没批的两笔（李四的补钟在前、张三那笔代录的在后，按日期倒序）。
        pending_only = await self.ledger.list_entries(status="pending")
        self.assertEqual(
            [row["id"] for row in pending_only["entries"]],
            [september["id"], august["id"]],
        )
        # 按人筛：张三两笔（8 月那笔代录的也算在他头上）。
        mine_only = await self.ledger.list_entries(employee_id=me["id"])
        self.assertEqual(
            [row["id"] for row in mine_only["entries"]], [approved["id"], august["id"]]
        )

        with self.assertRaises(OvertimeError) as caught:
            await self.ledger.list_entries(month="2026-8")
        self.assertEqual(caught.exception.code, "invalid_month")


class OvertimeTableContractTest(unittest.TestCase):
    """验收 7：新表进表名清单，Admin 的数据浏览器里只读（写入口在 HTTP 那一侧钉）。"""

    def test_new_tables_are_registered_read_only(self):
        # 断言的是**「加班台账这张表在里面」，不是「清单里只有它」**：这张清单是这一批
        # 票共享的（票 03 的底薪快照、票 05 的工龄奖留痕各自追加自己的表），写死全集
        # 会让别人加一张表就红在这里 —— 那不是契约，那是排期。
        self.assertIn("overtime_entries", OVERTIME_TABLES)
        for table in OVERTIME_TABLES:
            self.assertIn(table, ADMIN_READ_ONLY_TABLES)

        from api.admin import _admin_catalog

        catalog = _admin_catalog()
        for table in OVERTIME_TABLES:
            self.assertIn(table, catalog["tables"])
            self.assertEqual(catalog["table_meta"][table]["group"], "overtime")
            self.assertTrue(catalog["table_meta"][table]["read_only"])
        groups = {group["key"]: group for group in catalog["groups"]}
        self.assertEqual(set(groups["overtime"]["tables"]), set(OVERTIME_TABLES))
        self.assertEqual(groups["overtime"]["label"], "人事")


# ── HTTP 薄缝：谁能打、打进去会怎样 ──────────────────────────────────────────
#
# 一条真实请求链路（照 `tests/test_scheduling_api.py` 的薄缝）：路由里现造的
# `OvertimeLedger(db)` 用默认时钟，而这一份断言全围着 2026-09-24 写，所以把
# `__init__` 的 `now=` 钉住 —— 真钟一走过零点，写死的今天 / 昨天就整片错位。


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


@pytest.fixture
def overtime_http(tmp_path, monkeypatch):
    real_init = OvertimeLedger.__init__

    def _pinned_init(self, *args, **kwargs):
        kwargs.setdefault("now", lambda: FIXED_NOW)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(OvertimeLedger, "__init__", _pinned_init)

    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    # `require_staff_session` 从 `main` 取员工账号服务（同 `tests/test_scheduling_api.py`）。
    monkeypatch.setattr(main_module, "employee_accounts", accounts)

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(admin_module.router)
    app.include_router(overtime_module.router)
    with TestClient(app) as client:
        init = client.post("/api/auth/init", json=ADMIN)
        assert init.status_code == 200, init.text
        login = client.post(
            "/api/auth/login",
            json={"username": ADMIN["username"], "password": ADMIN["password"]},
        )
        assert login.status_code == 200, login.text
        yield client, db, accounts
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _employee_id(accounts, phone=PHONE, name=NAME, salary=5100):
    """同 `OvertimeLedgerTest._employee`：默认档案齐全（票 03 起审批要底薪）。"""
    employee = _run(accounts.register(phone, PASSWORD, name))
    _run(accounts.approve(employee["id"]))
    if salary is not None:
        _run(
            accounts.update_fields(
                employee["id"], base_salary=salary, hire_date="2024-03-01"
            )
        )
    return employee["id"]


def _staff_cookie(client, accounts, phone=PHONE):
    """换成员工那个 cookie（跟管理端是两个名字），登录用的是同一套账号。"""
    session_id = _run(accounts.login(phone, PASSWORD))["session_id"]
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, session_id)
    return session_id


def test_every_endpoint_needs_a_staff_session(overtime_http):
    """两扇门各认各的 cookie：匿名一律 401，管理端会话也不是员工那扇门的钥匙。"""
    client, _db, _accounts = overtime_http
    body = {"entry_date": TODAY, "half_hours": 2, "reason": "加班"}

    # fixture 里此刻是管理端会话。
    assert client.get("/api/overtime/me").status_code == 401
    assert client.post("/api/overtime/me", json=body).status_code == 401
    assert client.delete("/api/overtime/me/1").status_code == 401

    client.cookies.clear()
    assert client.get("/api/overtime/me").status_code == 401
    assert client.post("/api/overtime/me", json=body).status_code == 401
    assert client.delete("/api/overtime/me/1").status_code == 401


def test_staff_submits_sees_and_cancels(overtime_http):
    """验收 1/4/6：提交两种登记 → 我的记录里看到 → 撤回自己待审批的那笔。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    overtime = client.post(
        "/api/overtime/me",
        json={"entry_date": YESTERDAY, "half_hours": 13, "reason": "中秋加班"},
    )
    assert overtime.status_code == 200, overtime.text
    entry = overtime.json()["entry"]
    assert (
        entry["entry_date"],
        entry["half_hours"],
        entry["kind"],
        entry["status"],
    ) == (YESTERDAY, 13, "overtime", "pending")

    makeup = client.post(
        "/api/overtime/me",
        json={"entry_date": TODAY, "half_hours": -3, "reason": "迟到半小时"},
    )
    assert makeup.status_code == 200, makeup.text
    assert makeup.json()["entry"]["kind"] == "makeup"

    mine = client.get("/api/overtime/me")
    assert mine.status_code == 200, mine.text
    payload = mine.json()
    # 新的在前（今天那笔排在昨天那笔前面），每笔带日期 / 带符号时长 / 事由 / 状态。
    assert [row["id"] for row in payload["entries"]] == [
        makeup.json()["entry"]["id"],
        entry["id"],
    ]
    assert payload["entries"][0]["reason"] == "迟到半小时"
    assert payload["summary"]["pending"]["net_half_hours"] == 10

    cancelled = client.delete(f"/api/overtime/me/{entry['id']}")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["entry"]["status"] == "cancelled"
    assert client.delete("/api/overtime/me/999999").status_code == 404


def test_staff_responses_carry_no_money(overtime_http):
    """验收 5 的边界：员工门任何响应里都没有底薪与金额字段（0098 那条的延伸）。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    created = client.post(
        "/api/overtime/me",
        json={"entry_date": TODAY, "half_hours": 4, "reason": "下午加班"},
    )
    assert created.status_code == 200, created.text

    for response in (client.get("/api/overtime/me"), created):
        for forbidden in ("base_salary", "salary", "amount", "money", "pay"):
            assert forbidden not in response.text, f"员工门不得下发 {forbidden}"


def test_rejections_are_chinese_400(overtime_http):
    """窗口与形状被拒时是 400 + 一句能照做的中文（不是英文 422）。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    def submit(**overrides):
        body = {"entry_date": TODAY, "half_hours": 2, "reason": "加班"}
        body.update(overrides)
        return client.post("/api/overtime/me", json=body)

    too_early = submit(entry_date="2026-09-22")
    assert too_early.status_code == 400
    assert too_early.json()["detail"] == "只能登记今天和昨天的加班：更早的请找店长补录"

    zero = submit(half_hours=0)
    assert zero.status_code == 400
    assert zero.json()["detail"] == "时长不能是 0：请用 + / − 按钮调出这笔的时长"

    too_big = submit(half_hours=25)
    assert too_big.status_code == 400
    assert too_big.json()["detail"] == "单笔最多 12 小时：超过了请分成两笔"

    no_reason = submit(reason="   ")
    assert no_reason.status_code == 400
    assert no_reason.json()["detail"] == "请写一句事由：这笔加班 / 补钟是干什么的"

    tomorrow = submit(entry_date="2026-09-25")
    assert tomorrow.status_code == 400
    assert tomorrow.json()["detail"] == "还没到的日子登不了：加班记的是已经发生的事"


def test_generic_admin_write_path_rejects_the_new_table(overtime_http):
    """验收 7 的后半：通用写入口对这张表 403（只读不是只写在页面上）。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    created = client.post(
        "/api/overtime/me",
        json={"entry_date": TODAY, "half_hours": 2, "reason": "加班"},
    )
    assert created.status_code == 200, created.text
    entry_id = created.json()["entry"]["id"]

    login = client.post(
        "/api/auth/login",
        json={"username": ADMIN["username"], "password": ADMIN["password"]},
    )
    assert login.status_code == 200, login.text
    # 登录本身就把管理端 cookie 放进了 cookie jar（员工那个也还在，两套并存是正常的）。

    blocked = client.delete(f"/api/admin/tables/overtime_entries/rows/{entry_id}")
    assert blocked.status_code == 403, blocked.text


# ── 票 02：管理端的审批台 ────────────────────────────────────────────────────


def _submit(client, entry_date, half_hours, reason):
    response = client.post(
        "/api/overtime/me",
        json={"entry_date": entry_date, "half_hours": half_hours, "reason": reason},
    )
    assert response.status_code == 200, response.text
    return response.json()["entry"]


def _admin_login(client):
    """切回管理端那一套 cookie（两套 cookie 名字不同，本来就允许并存；这里清干净再登）。"""
    client.cookies.clear()
    response = client.post(
        "/api/auth/login",
        json={"username": ADMIN["username"], "password": ADMIN["password"]},
    )
    assert response.status_code == 200, response.text


def test_admin_decides_pending_entries(overtime_http):
    """验收 1/2：管理端读得到全店待办，能批准、能驳回（驳回必须写理由）。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    first = _submit(client, YESTERDAY, 13, "中秋加班")
    second = _submit(client, TODAY, -4, "迟到补钟")
    _admin_login(client)

    pending = client.get("/api/overtime/admin/pending")
    assert pending.status_code == 200, pending.text
    queue = pending.json()
    assert queue["count"] == 2
    # 旧的在前：先来先处理。
    assert [row["id"] for row in queue["entries"]] == [first["id"], second["id"]]
    assert queue["entries"][0]["employee_name"] == NAME

    approved = client.post(f"/api/overtime/admin/entries/{first['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["entry"]["status"] == "approved"

    no_reason = client.post(f"/api/overtime/admin/entries/{second['id']}/reject", json={})
    assert no_reason.status_code == 400
    assert "理由" in no_reason.json()["detail"]

    rejected = client.post(
        f"/api/overtime/admin/entries/{second['id']}/reject",
        json={"reason": "那天没有排班"},
    )
    assert rejected.status_code == 200, rejected.text
    decided = rejected.json()["entry"]
    assert (decided["status"], decided["reject_reason"]) == ("rejected", "那天没有排班")

    # 状态机只往前走：已经批过 / 驳过的再来一次都是 400（不是 500，也不是静默成功）。
    assert client.post(f"/api/overtime/admin/entries/{first['id']}/approve").status_code == 400
    assert client.post(f"/api/overtime/admin/entries/{second['id']}/approve").status_code == 400
    # 队列空了，台账里两条都还在（驳回不是删除）。
    assert client.get("/api/overtime/admin/pending").json()["count"] == 0
    listing = client.get("/api/overtime/admin/entries").json()
    assert [row["status"] for row in listing["entries"]] == ["rejected", "approved"]


def test_admin_endpoints_need_an_admin_session(overtime_http):
    """两扇门各认各的 cookie：匿名与员工会话都打不进管理端那几条。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)

    client.cookies.clear()
    assert client.get("/api/overtime/admin/pending").status_code == 401
    assert client.get("/api/overtime/admin/entries").status_code == 401
    assert client.post("/api/overtime/admin/entries/1/approve").status_code == 401
    assert client.post("/api/overtime/admin/entries/1/void").status_code == 401

    # 员工会话是员工那扇门的钥匙，不是管理端的。
    _staff_cookie(client, accounts)
    assert client.get("/api/overtime/admin/pending").status_code == 401
    assert client.post("/api/overtime/admin/entries/1/approve").status_code == 401


def test_admin_lists_employees_for_backfill(overtime_http):
    """代录要选人：这一条只回员工号 / 姓名 / 停用与否。

    **不带身份证与底薪** —— 加班页只需要「选谁」，没必要把花名册整行搬进这个页面；
    这一条也不读 `overtime_entries`，所以缺 0021 时它照样能用。
    """
    client, _db, accounts = overtime_http
    employee = _employee_id(accounts)
    _admin_login(client)

    listing = client.get("/api/overtime/admin/employees")
    assert listing.status_code == 200, listing.text
    people = {row["id"]: row for row in listing.json()["employees"]}
    assert people[employee]["name"] == NAME
    assert set(people[employee]) == {"id", "name", "disabled"}

    client.cookies.clear()
    assert client.get("/api/overtime/admin/employees").status_code == 401


def test_admin_lists_carry_the_limits(overtime_http):
    """管理端那两条列表也把上限随响应下发：页面照它设输入，不在前端再写死一份。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _admin_login(client)

    for path in ("/api/overtime/admin/pending", "/api/overtime/admin/entries"):
        payload = client.get(path).json()
        assert payload["max_half_hours"] == 24, path
        assert payload["max_reason"] == 50, path
        assert payload["max_reject_reason"] == 100, path


def test_admin_backfills_any_past_date(overtime_http):
    """验收 4：超管能代员工补录任意过去日期，台账上认得出是代录的。"""
    client, _db, accounts = overtime_http
    employee = _employee_id(accounts)
    _admin_login(client)

    created = client.post(
        "/api/overtime/admin/entries",
        json={
            "employee_id": employee,
            "entry_date": "2026-08-01",
            "half_hours": 8,
            "reason": "上月忘登的加班",
        },
    )
    assert created.status_code == 200, created.text
    entry = created.json()["entry"]
    assert (entry["created_by"], entry["employee_id"], entry["status"]) == (
        "super",
        employee,
        "pending",
    )

    # 这笔算在被代录那个人的头上：他自己手机上看得见。
    _staff_cookie(client, accounts)
    mine = client.get("/api/overtime/me").json()
    assert [row["id"] for row in mine["entries"]] == [entry["id"]]

    _admin_login(client)
    # 窗口放宽的是「过去」，不是「还没发生」；不指定算谁的也收不了（超管没有员工号）。
    future = client.post(
        "/api/overtime/admin/entries",
        json={
            "employee_id": employee,
            "entry_date": "2026-09-25",
            "half_hours": 2,
            "reason": "明天加班",
        },
    )
    assert future.status_code == 400
    nobody = client.post(
        "/api/overtime/admin/entries",
        json={"entry_date": "2026-08-01", "half_hours": 2, "reason": "忘了选人"},
    )
    assert nobody.status_code == 404


def test_admin_voids_an_approved_entry_and_it_stays_on_the_ledger(overtime_http):
    """验收 3/5：作废只对已批准的生效；员工端看到「已作废」而不是「不见了」。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    entry = _submit(client, TODAY, 4, "下午加班")
    _admin_login(client)

    # 还没批的作废不了。
    assert client.post(f"/api/overtime/admin/entries/{entry['id']}/void").status_code == 400

    assert (
        client.post(f"/api/overtime/admin/entries/{entry['id']}/approve").status_code
        == 200
    )
    voided = client.post(f"/api/overtime/admin/entries/{entry['id']}/void")
    assert voided.status_code == 200, voided.text
    assert voided.json()["entry"]["status"] == "voided"
    assert voided.json()["entry"]["decided_by"] == "super"
    # 作废过的不能再作废。
    assert client.post(f"/api/overtime/admin/entries/{entry['id']}/void").status_code == 400

    _staff_cookie(client, accounts)
    rows = client.get("/api/overtime/me").json()["entries"]
    assert [(row["id"], row["status"]) for row in rows] == [(entry["id"], "voided")]


def test_employee_sees_all_five_statuses_with_the_reject_reason(overtime_http):
    """验收 5：员工端五种状态都显示得出来，被驳回的那笔带着理由。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    pending = _submit(client, TODAY, 1, "待审批的")
    approved = _submit(client, TODAY, 2, "批了的")
    rejected = _submit(client, TODAY, 3, "驳了的")
    cancelled = _submit(client, TODAY, 4, "撤了的")
    voided = _submit(client, TODAY, 5, "批完又作废的")

    client.delete(f"/api/overtime/me/{cancelled['id']}")
    _admin_login(client)
    # 批两笔（其中一笔稍后作废），另一笔直接驳回 —— 驳回的是「待审批」，不是「已批准」。
    for entry in (approved, voided):
        client.post(f"/api/overtime/admin/entries/{entry['id']}/approve")
    client.post(
        f"/api/overtime/admin/entries/{rejected['id']}/reject",
        json={"reason": "那天没有排班"},
    )
    client.post(f"/api/overtime/admin/entries/{voided['id']}/void")

    _staff_cookie(client, accounts)
    rows = client.get("/api/overtime/me").json()["entries"]
    by_id = {row["id"]: row for row in rows}
    assert by_id[pending["id"]]["status"] == "pending"
    assert by_id[approved["id"]]["status"] == "approved"
    assert by_id[cancelled["id"]]["status"] == "cancelled"
    assert by_id[voided["id"]]["status"] == "voided"
    assert by_id[rejected["id"]]["status"] == "rejected"
    assert by_id[rejected["id"]]["reject_reason"] == "那天没有排班"


def test_staff_may_subscribe_to_the_overtime_topic():
    """员工端页面订得上新的 topic —— 订不上，nudge 发出去了也没人听。"""
    from services.realtime.hub import STAFF_ALLOWED_TOPICS, VALID_TOPICS

    assert "overtime" in VALID_TOPICS
    assert "overtime" in STAFF_ALLOWED_TOPICS


def test_every_move_broadcasts_one_data_less_nudge_to_the_owner(
    overtime_http, monkeypatch
):
    """验收 7：提交、撤回、批准、驳回各按一人一条广播给对应员工（nudge 不带数据）。"""
    client, _db, accounts = overtime_http
    employee = _employee_id(accounts)
    _staff_cookie(client, accounts)
    broadcast = AsyncMock()
    monkeypatch.setattr(overtime_module.realtime_hub, "broadcast_nudge", broadcast)

    def calls():
        return [call.args for call in broadcast.await_args_list]

    entry = _submit(client, TODAY, 4, "下午加班")
    assert ("overtime", {"reason": "entry_submitted", "employee_id": employee}) in calls()

    broadcast.reset_mock()
    client.delete(f"/api/overtime/me/{entry['id']}")
    assert ("overtime", {"reason": "entry_cancelled", "employee_id": employee}) in calls()

    second = _submit(client, TODAY, 2, "再一笔")
    _admin_login(client)

    broadcast.reset_mock()
    approved = client.post(f"/api/overtime/admin/entries/{second['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert ("overtime", {"reason": "entry_approved", "employee_id": employee}) in calls()

    third = _submit_for(client, accounts, TODAY, -2, "补钟一笔")
    broadcast.reset_mock()
    rejected = client.post(
        f"/api/overtime/admin/entries/{third['id']}/reject",
        json={"reason": "那天没有排班"},
    )
    assert rejected.status_code == 200, rejected.text
    assert ("overtime", {"reason": "entry_rejected", "employee_id": employee}) in calls()

    broadcast.reset_mock()
    voided = client.post(f"/api/overtime/admin/entries/{second['id']}/void")
    assert voided.status_code == 200, voided.text
    assert ("overtime", {"reason": "entry_voided", "employee_id": employee}) in calls()


def test_admin_month_stats_carry_the_pay_and_stay_admin_only(overtime_http):
    """验收 6：管理端按月看到加班费；员工门拿不到它 —— 金额只在管理端。

    5100 元的底薪、9 月 30 天、净 6.5 小时：5100 / 30 / 8.5 = 20 元/小时 → 130 元。
    """
    client, _db, accounts = overtime_http
    employee_id = _employee_id(accounts)
    entry = _submit_for(client, accounts, TODAY, 13, "中秋加班")
    approved = client.post(f"/api/overtime/admin/entries/{entry['id']}/approve")
    assert approved.status_code == 200, approved.text

    response = client.get("/api/overtime/admin/month", params={"month": "2026-09"})
    assert response.status_code == 200, response.text
    stats = response.json()["stats"]
    assert stats["month"] == "2026-09"
    assert stats["days"] == 30
    row = stats["items"][0]
    assert row["employee_id"] == employee_id
    assert row["employee_name"] == NAME
    assert row["net_hours"] == 6.5
    assert row["base_salary"] == 5100
    assert row["amount"] == 130
    assert stats["total"]["amount"] == 130

    # 员工门拿不到这一页：金额只在管理端（ADR 0098 那条边界的延伸）。
    _staff_cookie(client, accounts)
    assert client.get("/api/overtime/admin/month").status_code == 401


def test_admin_approve_is_blocked_without_a_base_salary(overtime_http):
    """验收 3：底薪待补时管理端批不了，报的是「先去补档案」而不是 500。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts, salary=None)
    entry = _submit_for(client, accounts, TODAY, 4, "加班")

    response = client.post(f"/api/overtime/admin/entries/{entry['id']}/approve")

    assert response.status_code == 400, response.text
    assert "底薪" in response.json()["detail"]
    # 一个字都没写进去：这一笔还在待审批里等补完档案。
    listing = client.get("/api/overtime/admin/entries").json()["entries"]
    assert [row["status"] for row in listing] == ["pending"]


def _submit_for(client, accounts, entry_date, half_hours, reason):
    """以员工身份提一笔（供管理端上下文里造数据用：切回员工 → 提 → 再切回管理端）。"""
    _staff_cookie(client, accounts)
    entry = _submit(client, entry_date, half_hours, reason)
    _admin_login(client)
    return entry


# ── 票 04：店长在手机上审批（第 11 项能力键 `overtime`）──────────────────────
#
# 店长**没有管理端账号**：他就是花名册上的一个真人 + 员工会话。所以这一面走员工门
# （`require_staff_session`）**加上**能力判据（`has_cap(caps, CAP_OVERTIME)`），形状与
# 卫生那三项（管理员在员工手机端验收）完全一样，见 `docs/adr/0103`。
#
# 判据一律看 `admin_caps`：`permission` 那一列只是人话标签，拿它判会放行没给的那件事。

SUPERVISOR_PHONE = "13800138011"
SUPERVISOR_NAME = "李四"
COLLEAGUE_PHONE = "13800138012"
COLLEAGUE_NAME = "王五"


def _with_caps(accounts, phone, name, caps):
    """建一个员工并给他一组管理能力开关，返回员工号。"""
    employee_id = _employee_id(accounts, phone=phone, name=name)
    _run(accounts.set_admin_caps(employee_id, list(caps)))
    return employee_id


def _supervisor(accounts):
    """开好「加班与补钟审批」的店长 —— 仍然是员工会话，没有管理端 cookie。"""
    return _with_caps(accounts, SUPERVISOR_PHONE, SUPERVISOR_NAME, ["overtime"])


def test_me_says_whether_i_can_review(overtime_http):
    """`can_review` 由服务端按开关算：前端不把 caps 判据再写一遍。"""
    client, _db, accounts = overtime_http
    _employee_id(accounts)

    _staff_cookie(client, accounts)
    assert client.get("/api/overtime/me").json()["can_review"] is False

    _supervisor(accounts)
    _staff_cookie(client, accounts, phone=SUPERVISOR_PHONE)
    assert client.get("/api/overtime/me").json()["can_review"] is True


def test_without_the_cap_the_review_face_is_closed(overtime_http):
    """验收 2/3：没勾这项的人直接调接口被拒。

    被拒是 **403**（「你没这个权限」），不是 404 —— 后者会把「权限不够」说成
    「这一笔不存在」，店长会以为是数据没了。
    """
    client, _db, accounts = overtime_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    assert client.get("/api/overtime/review/pending").status_code == 403
    assert client.get("/api/overtime/review/employees").status_code == 403
    assert client.post("/api/overtime/review/1/approve").status_code == 403
    assert (
        client.post("/api/overtime/review/1/reject", json={"reason": "不算"}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/overtime/review/entries",
            json={
                "employee_id": 1,
                "entry_date": TODAY,
                "half_hours": 2,
                "reason": "代录",
            },
        ).status_code
        == 403
    )


def test_supervisor_reviews_a_colleagues_entry_on_the_phone(overtime_http):
    """验收 2/4：勾了能力的店长看到**全店**待审批，能批准、能驳回（理由必填）。"""
    client, _db, accounts = overtime_http
    colleague = _employee_id(accounts, phone=COLLEAGUE_PHONE, name=COLLEAGUE_NAME)
    _staff_cookie(client, accounts, phone=COLLEAGUE_PHONE)
    first = _submit(client, TODAY, 4, "晚市加班")
    second = _submit(client, YESTERDAY, -2, "早退补钟")

    supervisor_id = _supervisor(accounts)
    _staff_cookie(client, accounts, phone=SUPERVISOR_PHONE)

    queue = client.get("/api/overtime/review/pending")
    assert queue.status_code == 200, queue.text
    payload = queue.json()
    assert payload["count"] == 2
    # 队列上要写得出「谁 · 哪天 · 多少 · 干什么」：只给员工号等于让店长自己查名单。
    assert {row["employee_name"] for row in payload["entries"]} == {COLLEAGUE_NAME}
    # 旧的在前：待办是先来先处理的队列，不是「最新动态」。
    assert [row["id"] for row in payload["entries"]] == [first["id"], second["id"]]

    approved = client.post(f"/api/overtime/review/{first['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["entry"]["status"] == "approved"
    # 谁判的记在台账上：是这位店长的员工号，不是 `super`（两拨人不能混成一条记录）。
    assert approved.json()["entry"]["decided_by"] == f"staff:{supervisor_id}"

    # 驳回**必须写理由** —— 理由比「驳回」这个结果本身更重要。
    no_reason = client.post(f"/api/overtime/review/{second['id']}/reject", json={})
    assert no_reason.status_code == 400
    rejected = client.post(
        f"/api/overtime/review/{second['id']}/reject",
        json={"reason": "那天你排的是休"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["entry"]["reject_reason"] == "那天你排的是休"

    # 员工那一侧立刻看到结果（票面「员工立刻看到结果」）。
    _staff_cookie(client, accounts, phone=COLLEAGUE_PHONE)
    mine = client.get("/api/overtime/me").json()
    by_id = {row["id"]: row for row in mine["entries"]}
    assert by_id[first["id"]]["status"] == "approved"
    assert by_id[second["id"]]["status"] == "rejected"
    assert by_id[second["id"]]["reject_reason"] == "那天你排的是休"
    assert by_id[first["id"]]["employee_id"] == colleague


def test_supervisor_backfills_any_past_date_while_staff_cannot(overtime_http):
    """验收 5：店长能代录任意过去日期；员工自己仍受今天 / 昨天限制。"""
    client, _db, accounts = overtime_http
    colleague = _employee_id(accounts, phone=COLLEAGUE_PHONE, name=COLLEAGUE_NAME)
    _supervisor(accounts)
    _staff_cookie(client, accounts, phone=SUPERVISOR_PHONE)

    backfilled = client.post(
        "/api/overtime/review/entries",
        json={
            "employee_id": colleague,
            "entry_date": "2026-08-01",
            "half_hours": 6,
            "reason": "上个月月中加班，忘了登",
        },
    )
    assert backfilled.status_code == 200, backfilled.text
    entry = backfilled.json()["entry"]
    assert entry["entry_date"] == "2026-08-01"
    assert entry["employee_id"] == colleague
    # 代录人是店长自己：台账上分得清「他自己提的」与「别人替他补的」。
    assert entry["created_by"].startswith("staff:")

    # 员工自己提前天（更别说上个月）：窗口还是那条。
    _staff_cookie(client, accounts, phone=COLLEAGUE_PHONE)
    refused = client.post(
        "/api/overtime/me",
        json={"entry_date": "2026-08-01", "half_hours": 6, "reason": "补登"},
    )
    assert refused.status_code == 400
    assert "店长" in refused.json()["detail"]

    # 但店长**给自己**登也放开了窗口（能力跟人走，不跟「替谁登」走）。
    _staff_cookie(client, accounts, phone=SUPERVISOR_PHONE)
    own = client.post(
        "/api/overtime/me",
        json={"entry_date": "2026-08-02", "half_hours": 2, "reason": "自己也补一笔"},
    )
    assert own.status_code == 200, own.text


def test_supervisor_side_carries_no_money(overtime_http):
    """验收 6：店长这一侧的响应里没有底薪、也没有任何金额字段。

    他用的就是员工会话，而底薪按 `docs/adr/0098` 不下发员工端；代录要选人，所以
    选人那条名单是**新开的窄口**（只回员工号 / 姓名 / 停用与否），不借花名册那个
    带身份证号与底薪的端点。
    """
    client, _db, accounts = overtime_http
    colleague = _employee_id(accounts, phone=COLLEAGUE_PHONE, name=COLLEAGUE_NAME)
    _run(accounts.update_fields(colleague, base_salary=5200, hire_date="2024-09-18"))
    _supervisor(accounts)
    _staff_cookie(client, accounts, phone=SUPERVISOR_PHONE)
    _submit(client, TODAY, 2, "加班")

    responses = [
        client.get("/api/overtime/me"),
        client.get("/api/overtime/review/pending"),
        client.get("/api/overtime/review/employees"),
    ]
    for response in responses:
        assert response.status_code == 200, response.text
        for forbidden in ("base_salary", "salary", "amount", "money", "pay", "id_card"):
            assert forbidden not in response.text, f"店长侧不得下发 {forbidden}"

    names = client.get("/api/overtime/review/employees").json()["employees"]
    assert any(person["id"] == colleague for person in names)


def test_the_cap_takes_effect_immediately(overtime_http):
    """验收 7：取消勾选后**立即**失效，不需要重新登录。

    判据每次请求现读 `admin_caps`（那张表就是权限本身），所以收回能力与给他能力
    一样，下一次请求就生效 —— 没有第二份「登录时快照的权限」。
    """
    client, _db, accounts = overtime_http
    supervisor_id = _supervisor(accounts)
    _staff_cookie(client, accounts, phone=SUPERVISOR_PHONE)
    assert client.get("/api/overtime/review/pending").status_code == 200

    _run(accounts.set_admin_caps(supervisor_id, []))
    assert client.get("/api/overtime/review/pending").status_code == 403
    # 同一条 cookie、没有重登：被收回的是能力，不是会话。
    assert client.get("/api/overtime/me").status_code == 200

    _run(accounts.set_admin_caps(supervisor_id, ["overtime"]))
    assert client.get("/api/overtime/review/pending").status_code == 200
