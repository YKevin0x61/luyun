#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加班与补钟台账（`GLOSSARY.md` 的「加班与补钟登记」，决策见 `docs/adr/0100`）。

一条记录 = 一个**自然日** + 一个**带符号的半小时数** + 必填事由：加班为正、补钟为负。
符号就是类型，表里不存 `kind` —— 读的时候按符号派生（`overtime` / `makeup`）。

**它不是考勤**：不记上下班时刻、不判定迟到早退、不跟排班班次比对工时（本店没有考勤
系统可对接，`docs/adr/0038`）。这一层只做「申报 → 审批」的前半截：审批在票 02、
底薪快照与加班费在票 03。

窗口判据只有一处（:meth:`OvertimeLedger.submit`）：员工只能报**今天与昨天**（自然日
零点切，不是 06:00 的营业日），超级管理员或持有 `overtime` 能力的店长可以补录任意
过去日期。它按「提交人是谁」判，不按接口路径判 —— 三种身份走的是同一个方法。
"""

from __future__ import annotations

import functools
import logging
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Optional

import asyncpg

from db_core.utils import CHINA_TZ
from services.identity import EmployeeAccounts, serialized_write
from services.identity.capabilities import CAP_SCHEDULE
from services.leave_ledger import LeaveLedger
from services.overtime_contract import (
    HALF_HOURS_PER_LEAVE_DAY,
    HOURS_PER_DAY,
    MAX_DEDUCTION_DAYS,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_VOIDED,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ACTION_APPROVE",
    "ACTION_REJECT",
    "ACTION_VOID",
    "BOOK_DEDUCTION",
    "BOOK_ENTRY",
    "HALF_HOURS_MAX",
    "HALF_HOURS_PER_LEAVE_DAY",
    "HOURS_PER_DAY",
    "KIND_LEAVE_DEDUCTION",
    "KIND_MAKEUP",
    "KIND_OVERTIME",
    "MAX_DEDUCTION_DAYS",
    "MAX_REASON",
    "MAX_REJECT_REASON",
    "EntryActor",
    "OvertimeError",
    "OvertimeLedger",
    "STATUS_APPROVED",
    "STATUS_CANCELLED",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "STATUS_VOIDED",
]

# 状态机（`docs/adr/0100`）、一个工作日的半小时数与「一次最多抵几天」都搬到了
# `services/overtime_contract.py`（零 import 的叶子，ADR 0139）：考勤那边只需要念
# 两个状态字符串，不该为此 import 整个加班域。名字仍从本模块导出（`__all__` 不变）。

# 读的时候按符号派生：正数是加班、负数是补钟。它不是库里的一列。
KIND_OVERTIME = "overtime"
KIND_MAKEUP = "makeup"
# 补钟用存假抵扣（ADR 0124）：它不是加班登记的一种，是那张 `overtime_leave_deductions`
# 表上的一行 —— 但列表接口把两种记录并排下发时，前端要靠这个字段分渲染。
KIND_LEAVE_DEDUCTION = "leave_deduction"

# 两本账共用一台状态机（ADR 0153）：`book` 选表与错误码，`action` 选目标状态。
# 名字不叫 `kind`：那个词已经被加班 / 补钟 / 抵扣三类记录占了。
BOOK_ENTRY = "entry"
BOOK_DEDUCTION = "deduction"
ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
ACTION_VOID = "void"

# 界面上的步长是 0.5 小时，库里存它的整数倍（`half_hours`）。单条上限 ±12 小时 ——
# 防的是手滑打成 120，不是替业务定政策。
HALF_HOURS_MAX = 24

# 事由限长：它是登记卡上的一行话，不是留言板。超了报错而不是悄悄截断（同排班的
# `MAX_REQUEST_NOTE`：员工写下的东西不该被系统改掉）。
MAX_REASON = 50

# 驳回理由限长。它比事由宽一倍：事由是「这笔是干什么的」，驳回理由是「为什么不算」
# ——后者常常要说清依据（哪天的排班、哪条规矩），一句话往往不够。
MAX_REJECT_REASON = 100

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

# 读登记时选的列：员工页与管理端待办共用一份，免得两处列名漂移。
_ENTRY_COLUMNS = (
    "id, employee_id, entry_date, half_hours, reason, status, reject_reason,"
    " created_by, decided_by, decided_at, created_at, updated_at"
)

# 抵扣单读出来选的列：与 `_ENTRY_COLUMNS` 同一个理由（两处读口不各写一份列名）。
_DEDUCTION_COLUMNS = (
    'id, employee_id, "month", days, half_hours, status, created_by,'
    " decided_by, decided_at, reject_reason, created_at, updated_at"
)


def _needs_migration(method):
    """表还没建（0021 没应用）时，把 asyncpg 的缺表异常换成一句能照做的错。

    照排班 `store.py` 的同一个装饰器：真正用到加班登记的那一刻不该是一个 500
    traceback，店长要看到的是「去 Admin『系统更新 → 数据库迁移』应用 0021」。
    顺带把失败的事务回滚掉，否则这条连接后面的查询会全跟着报
    `current transaction is aborted`。
    """

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            raise OvertimeError("not_migrated", str(exc)) from exc

    return wrapper


@dataclass(frozen=True)
class EntryActor:
    """谁在提这笔登记 —— 决定申报窗口与 `created_by` 怎么写。

    `employee_id` 为空就是管理端那个共享账号（超管）：他没有员工号，所以代录时必须
    指定这笔算谁的；`can_backfill` 是店长那一档的「加班与补钟审批」能力（票 04 起
    由 `admin_caps` 给），两者都让窗口放宽到「任意过去日期」。
    """

    employee_id: Optional[int] = None
    can_backfill: bool = False

    @property
    def is_super(self) -> bool:
        return self.employee_id is None

    @property
    def can_backdate(self) -> bool:
        return self.is_super or self.can_backfill

    @property
    def stamp(self) -> str:
        """写进 `created_by` 的形状：`super` 或 `staff:<员工号>`。"""
        return "super" if self.is_super else f"staff:{int(self.employee_id)}"

    @classmethod
    def from_actor(cls, actor: Any) -> "EntryActor":
        """边界 actor → 台账 actor（鸭子类型；本层不认识 API 的 `WorkbenchActor`）。

        管理端（超管）没有员工号，`created_by` 写 `super`、代录时另给「这笔算谁的」；
        员工按自己的能力键决定能不能补录任意过去日期 —— 能力跟人走，不跟路径走
        （`docs/adr/0100` / `0103`）。员工端那扇门已保证 `employee_id` 存在。
        """
        if getattr(actor, "kind", None) == "admin":
            return cls()
        return cls(
            employee_id=int(actor.employee_id),
            can_backfill=bool(actor.has_cap(CAP_SCHEDULE)),
        )


class OvertimeError(ValueError):
    """台账层的输入错误。``code`` 是给 API 层查文案用的稳定标识，不是给员工看的文案。"""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class _Action:
    """一条裁决动作：目标状态、期望的当下状态、失败时用哪本账的错误码。"""

    status: str
    expect: str
    error_attr: str
    requires_reason: bool = False


_ACTIONS = {
    ACTION_APPROVE: _Action(STATUS_APPROVED, STATUS_PENDING, "not_pending"),
    ACTION_REJECT: _Action(
        STATUS_REJECTED, STATUS_PENDING, "not_pending", requires_reason=True
    ),
    ACTION_VOID: _Action(STATUS_VOIDED, STATUS_APPROVED, "not_approved"),
}


@dataclass(frozen=True)
class _Book:
    """一本账的适配器：读哪张表、报哪个 code、批之前还要过哪道守卫。"""

    name: str
    table: str
    unknown: str
    not_pending: str
    not_approved: str
    read: Callable[[int], Any]
    prepare: Optional[Callable[[], Any]]
    before: dict[str, Callable[..., Any]]


def _require_date(value: Any) -> str:
    text = str(value or "").strip()
    if not _DATE_RE.match(text):
        raise OvertimeError("invalid_date", "invalid_date")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        # 形状对、日历上没有这一天（2026-02-30）。
        raise OvertimeError("invalid_date", "invalid_date")


def _require_half_hours(value: Any) -> int:
    """时长的合法形状：非零整数（半小时为单位）、绝对值不超过单条上限。

    「0.5 的整数倍」这条口径的判据就是「它是个整数」—— 库里存的就是半小时数，
    界面的 `+/−` 每次动 1。`0` 单独报一句：它不是「没填」，是「白登一笔」。
    """
    if isinstance(value, bool):
        raise OvertimeError("invalid_half_hours", "invalid_half_hours")
    if isinstance(value, float):
        if value != int(value):
            raise OvertimeError("invalid_half_hours", "invalid_half_hours")
        value = int(value)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise OvertimeError("invalid_half_hours", "invalid_half_hours")
    if number == 0:
        raise OvertimeError("zero_half_hours", "zero_half_hours")
    if abs(number) > HALF_HOURS_MAX:
        # 第二参填进文案的 `{}`：报真实的上限（别把 12 写死在文案里）。
        raise OvertimeError("hours_too_large", str(HALF_HOURS_MAX // 2))
    return number


def _clean_reason(value: Any) -> str:
    """事由：必填、限长。不悄悄截断（同排班 `_clean_note` 的理由）。"""
    text = str(value or "").strip()
    if not text:
        raise OvertimeError("missing_reason", "missing_reason")
    if len(text) > MAX_REASON:
        raise OvertimeError("reason_too_long", str(MAX_REASON))
    return text


def _clean_reject_reason(value: Any) -> str:
    """驳回理由：必填、限长。

    它比事由更严一档的地方只有「必填」这一条 —— 员工看到的「已驳回」本身不解释任何
    事，没有理由他就只能来问人；下一笔还会照原样提上来（`docs/adr/0100` 把它定成
    比请假先例更严的那一条）。
    """
    text = str(value or "").strip()
    if not text:
        raise OvertimeError("missing_reject_reason", "missing_reject_reason")
    if len(text) > MAX_REJECT_REASON:
        raise OvertimeError("reject_reason_too_long", str(MAX_REJECT_REASON))
    return text


def _row_to_entry(row: Any) -> dict:
    """一行 → 员工端/管理端要的形状：加 `kind`、加 `hours`（半小时数换成小时）。

    两者都是派生值，库里没有它们 —— 派生只在这一处，两个页面不会算出两个答案。
    """
    entry = dict(row)
    half_hours = int(entry.get("half_hours") or 0)
    entry["half_hours"] = half_hours
    entry["hours"] = half_hours / 2
    entry["kind"] = KIND_OVERTIME if half_hours > 0 else KIND_MAKEUP
    return entry


def _row_to_deduction(row: Any) -> dict:
    """一行抵扣单 → 前端要的形状：加 `hours`、`kind`（派生值，库里没有）。

    `half_hours` 是「这笔抵扣注销掉多少补钟小时」——整天的话就是 `days × 17`。它刻意
    与 `days` 一起给：页面上写「抵 1 天（8.5 小时）」两个数都要，账上对得起来。
    """
    item = dict(row)
    days = int(item.get("days") or 0)
    item["days"] = days
    item["half_hours"] = int(item.get("half_hours") or days * HALF_HOURS_PER_LEAVE_DAY)
    item["hours"] = item["half_hours"] / 2
    item["kind"] = KIND_LEAVE_DEDUCTION
    return item


def _require_days(value: Any) -> int:
    """抵扣天数：正整数、不超过一个月（31）—— 界面上的步长是「整天」。"""
    if isinstance(value, bool):
        raise OvertimeError("invalid_days", "invalid_days")
    if isinstance(value, float):
        if value != int(value):
            raise OvertimeError("invalid_days", "invalid_days")
        value = int(value)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise OvertimeError("invalid_days", "invalid_days")
    if number < 1:
        raise OvertimeError("invalid_days", "invalid_days")
    if number > MAX_DEDUCTION_DAYS:
        raise OvertimeError("days_too_large", str(MAX_DEDUCTION_DAYS))
    return number


def _require_month(value: Any) -> str:
    text = str(value or "").strip()
    if not _MONTH_RE.match(text):
        raise OvertimeError("invalid_month", "invalid_month")
    try:
        date.fromisoformat(f"{text}-01")
    except ValueError:
        raise OvertimeError("invalid_month", "invalid_month")
    return text


def _days_in_month(month: str) -> int:
    """``YYYY-MM`` → 该月日历天数（加班费公式的分母之一，票 03）。

    用**自然月天数**而不是门店的应出勤天数：用户给的公式字面就是「当月天数」，
    而且这样不用另维护一张每月应出勤表（`docs/adr/0100` 的 Considered options）。
    """
    year, mon = int(month[:4]), int(month[5:7])
    return monthrange(year, mon)[1]


def _pay_for(salary: int, days: int, net_hours: float) -> int:
    """一笔月账的加班费（元）：`salary ÷ days ÷ 8.5 × net_hours`，四舍五入到元。

    走 `Decimal` 而不是 `round()`：Python 的 `round` 是**银行家舍入**（`round(2.5) == 2`），
    而工钱这件事上「四舍五入」四个字就是它字面的意思。净时长 ≤ 0 回 0 —— 不倒扣工资
    （倒扣是线下的事），也不把欠的钟带到下个月。
    """
    if net_hours <= 0:
        return 0
    hourly = Decimal(int(salary)) / Decimal(int(days)) / Decimal(str(HOURS_PER_DAY))
    amount = hourly * Decimal(str(net_hours))
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _month_summary(rows: list[dict], month: str, *, deducted_days: int = 0) -> dict:
    """一个月的合计，**待审批与已批准分开**（员工端那张月度卡的口径）。

    只有这两种状态算进「这个月加了多少」：已驳回 / 已撤回 / 已作废的登记不进合计
    —— 它们没换来钱，也不该让员工以为自己加过（票 03 的全店统计同一条口径）。

    净时长 = 加班与补钟**正负相抵**（`GLOSSARY.md` 的「净时长」）。负数照实给：员工端
    要看得见「这个月是净欠的」，扣不扣钱是票 03 的事。

    ``deducted_days`` 是那人那个月**已经批下来**的存假抵扣天数（ADR 0124）：被抵掉的
    那部分补钟从净时长里注销（净时长 = 加班 − 补钟 + 抵扣小时），所以它只进**已批准**
    那一桶 —— 待审批的抵扣还没生效，一分都不算。
    """
    deducted_half_hours = int(deducted_days) * HALF_HOURS_PER_LEAVE_DAY
    buckets = {
        status: {"overtime_half_hours": 0, "makeup_half_hours": 0}
        for status in (STATUS_PENDING, STATUS_APPROVED)
    }
    for row in rows:
        bucket = buckets.get(row["status"])
        if bucket is None or not str(row["entry_date"]).startswith(month):
            continue
        half_hours = int(row["half_hours"])
        key = "overtime_half_hours" if half_hours > 0 else "makeup_half_hours"
        bucket[key] += abs(half_hours)
    summary: dict[str, Any] = {"month": month}
    for status, bucket in buckets.items():
        offset = deducted_half_hours if status == STATUS_APPROVED else 0
        bucket["deducted_half_hours"] = offset
        bucket["deducted_days"] = int(deducted_days) if status == STATUS_APPROVED else 0
        bucket["net_half_hours"] = (
            bucket["overtime_half_hours"] - bucket["makeup_half_hours"] + offset
        )
        for name in ("overtime", "makeup", "net", "deducted"):
            bucket[f"{name}_hours"] = bucket[f"{name}_half_hours"] / 2
        summary[status] = bucket
    return summary


class OvertimeLedger:
    """加班与补钟台账的读写口。构造要一个已打开的库连接（同 `SchedulingStore` 的口径）。"""

    def __init__(self, conn_or_db, now: Optional[Callable[[], datetime]] = None):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("OvertimeLedger requires an open database connection")
        self._conn = conn
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._write_lock_owner = conn_or_db
        # 两本账只差「读哪张表、报哪个 code、批之前多过哪道守卫」；状态机不复制。
        self._books = {
            BOOK_ENTRY: _Book(
                name=BOOK_ENTRY,
                table="overtime_entries",
                unknown="unknown_entry",
                not_pending="entry_not_pending",
                not_approved="entry_not_approved",
                read=self._entry_by_id,
                prepare=None,
                before={
                    ACTION_APPROVE: self._before_entry_approve,
                    ACTION_VOID: self._before_entry_void,
                },
            ),
            BOOK_DEDUCTION: _Book(
                name=BOOK_DEDUCTION,
                table="overtime_leave_deductions",
                unknown="unknown_deduction",
                not_pending="deduction_not_pending",
                not_approved="deduction_not_approved",
                read=self._deduction_by_id,
                prepare=self._require_deduction_table,
                before={
                    ACTION_APPROVE: self._before_deduction_approve,
                    ACTION_VOID: self._before_deduction_void,
                },
            ),
        }

    def _now_dt(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value

    def _now_iso(self) -> str:
        return self._now_dt().isoformat()

    def today(self) -> str:
        """今天 —— **自然日**（北京时零点切），不是 06:00 的营业日。

        加班记的是「哪一天加的班」，日历日才是员工嘴里的那天；06:00 那个切点是 POS
        与卫生日常的口径（`GLOSSARY.md` 的「加班与补钟登记」_Avoid_ 点名了这一条）。
        """
        return self._now_dt().date().isoformat()

    def yesterday(self) -> str:
        """昨天 —— 员工能自己登记的最早一天。"""
        return (self._now_dt().date() - timedelta(days=1)).isoformat()

    async def _rollback_quietly(self) -> None:
        try:
            await self._conn.rollback()
        except Exception:  # pragma: no cover - 回滚失败不该盖住原始错误
            logger.debug("回滚失败（忽略）", exc_info=True)

    async def _rows(self, clause: str, params: tuple, order: str) -> list[dict]:
        cur = await self._conn.execute(
            f"SELECT {_ENTRY_COLUMNS} FROM overtime_entries{clause}"
            f"{f' ORDER BY {order}' if order else ''}",
            params,
        )
        return [_row_to_entry(row) for row in await cur.fetchall()]

    async def _entry_by_id(self, entry_id: int) -> Optional[dict]:
        rows = await self._rows(" WHERE id = ?", (int(entry_id),), "")
        return rows[0] if rows else None

    @_needs_migration
    @serialized_write
    async def submit(
        self,
        actor: EntryActor,
        entry_date: str,
        half_hours: Any,
        reason: Any,
        *,
        target_employee_id: Optional[int] = None,
    ) -> dict:
        """提一笔加班（正数）或补钟（负数）。

        三种身份走同一条路：员工给自己提、店长给自己或代同事提、超管代任何人提。
        **窗口按提交人判**（`actor.can_backdate`）：员工只能报今天与昨天，超管与有
        `overtime` 能力的店长可以补录任意过去日期；未来日期谁的都不收。
        """
        employee_id = (
            actor.employee_id if target_employee_id is None else int(target_employee_id)
        )
        if employee_id is None:
            # 超管没有员工号：不指定「这笔算谁的」就没法落库。这条在 API 层就该校验，
            # 这里兜一句稳定的 code，免得写成 NULL 再被库拒。
            raise OvertimeError("unknown_employee", "unknown_employee")
        employee_id = int(employee_id)
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        if employee_id not in {employee["id"] for employee in roster}:
            raise OvertimeError("unknown_employee", "unknown_employee")

        day = _require_date(entry_date)
        hours = _require_half_hours(half_hours)
        text = _clean_reason(reason)

        today = self.today()
        if day > today:
            raise OvertimeError("future_day", "future_day")
        if not actor.can_backdate and day < self.yesterday():
            # 窗口是纪律，逃生口留给管理端（超管与有能力的店长可补录）——
            # 员工忘了登的代价不该是「这笔钱永久消失」（`docs/adr/0100`）。
            raise OvertimeError("past_window", "past_window")

        stamp = self._now_iso()
        cur = await self._conn.execute(
            "INSERT INTO overtime_entries"
            " (employee_id, entry_date, half_hours, reason, status, created_by,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (employee_id, day, hours, text, STATUS_PENDING, actor.stamp, stamp, stamp),
        )
        created = await cur.fetchone()
        await self._conn.commit()
        entry = await self._entry_by_id(int(created["id"]))
        assert entry is not None  # 刚写进去的那一行
        return entry

    @_needs_migration
    async def list_mine(self, employee_id: int, month: Optional[str] = None) -> dict:
        """我自己的登记，新的在前；外加**某一个月**的合计（员工端那一页读的就是它）。

        接口形状里**没有金额、没有别人的记录**：员工会话读得到的只有自己这几笔
        （`docs/adr/0098` 那条边界的延伸，见票 01 的验收 5）。`month` 只决定月度卡看
        哪个月（不给就是本月），决定不了看谁 —— 同排班 `my_month` 的口径。

        `entries` 给的是**全部历史**（员工要能翻自己提过什么），月度合计只在 `summary`
        里按 `month` 算：一张卡回答「这个月加了多少」，一张列表回答「我都提过什么」。
        """
        rows = await self._rows(
            " WHERE employee_id = ?", (int(employee_id),), "entry_date DESC, id DESC"
        )
        target = self.today()[:7] if month in (None, "") else _require_month(month)
        deducted = await self._deduction_days(int(employee_id), target)
        return {
            "today": self.today(),
            "yesterday": self.yesterday(),
            "month": target,
            "summary": _month_summary(rows, target, deducted_days=deducted),
            "entries": rows,
            "deductions": await self._my_deduction_rows(int(employee_id)),
            "deduction_capacity": await self.deduction_capacity(int(employee_id), target),
        }

    async def _name_index(self) -> dict[int, str]:
        """员工号 → 姓名。管理端的列表要写「谁提的」，而表里只有员工号。

        名单从公共层取（同排班 `store.inbox()` 的做法）：这一层不 import 卫生，也不
        自己查 `hygiene_employees` —— 那两件事都会让「员工是谁」多出第二个出处。
        """
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        return {
            int(employee["id"]): (employee.get("name") or "")
            for employee in roster
        }

    @_needs_migration
    async def list_pending(self, *, exclude_employee_id: Optional[int] = None) -> dict:
        """全店**等审批**的登记（管理端那一页的队列）。

        **旧的在前**：待办是先来先处理的队列，不是「最新动态」（同排班 `inbox`）。
        已撤回 / 已批 / 已驳 / 已作废的都不在这儿 —— 它们已经有人点过头了。

        每条带 `employee_name`：队列上写成「李四 · 9/24 · +6.5 小时 · 中秋加班」，
        只给员工号等于让店长自己去查名单。

        `exclude_employee_id` 只给店长那一面（`/review/pending`）用：**自己提的那几笔
        不进队列** —— 他不能批自己的登记（`decide(..., ACTION_APPROVE, ...)` 的
        `cannot_self_accept`），摆在
        队列里就是一颗点了必被拒的「批准」。管理端的超管没有员工号，不传这个参数，
        队列照旧是全店的。
        """
        clause = " WHERE status = ?"
        params: list[Any] = [STATUS_PENDING]
        if exclude_employee_id is not None:
            clause += " AND employee_id <> ?"
            params.append(int(exclude_employee_id))
        rows = await self._rows(clause, tuple(params), "created_at, id")
        names = await self._name_index()
        for row in rows:
            row["employee_name"] = names.get(int(row["employee_id"]), "")
        return {"today": self.today(), "count": len(rows), "entries": rows}

    @_needs_migration
    async def list_entries(
        self,
        *,
        month: Optional[str] = None,
        employee_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> dict:
        """全店登记（管理端）：可按月 / 按人 / 按状态筛，**新的在前**。

        管理端拿它做两件事：找一条**已经批过**的来作废（待办队列里只有待审批的，
        而作废要找的正是已批准的那些），以及代录之后回头确认写对了没有。
        归月口径与净时长同一条：按 `entry_date` 的自然月，与审批时间无关。
        """
        where: list[str] = []
        params: list[Any] = []
        if month not in (None, ""):
            where.append("entry_date LIKE ?")
            params.append(f"{_require_month(month)}%")
        if employee_id is not None:
            where.append("employee_id = ?")
            params.append(int(employee_id))
        if status not in (None, ""):
            where.append("status = ?")
            params.append(str(status))
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = await self._rows(clause, tuple(params), "entry_date DESC, id DESC")
        names = await self._name_index()
        for row in rows:
            row["employee_name"] = names.get(int(row["employee_id"]), "")
        return {"today": self.today(), "count": len(rows), "entries": rows}

    @_needs_migration
    @serialized_write
    async def decide(
        self,
        book: str,
        target_id: int,
        action: str,
        actor: EntryActor,
        reason: Any = None,
    ) -> dict:
        """两本账共用的一台状态机：批 / 驳 / 作废。

        book 选表与错误码（entry / deduction），action 选目标状态
        （approve / reject / void）。两本账只在「读哪张表、批之前多过哪道守卫」上不同，
        那两件事由 self._books 里的适配器提供，状态转移只有这一份。

        判的顺序是**状态 → 身份 → 理由**：先回读并比期望状态（已经处理过就报
        *_not_pending，不覆盖别人的决定），再判自己 / 超管，最后才校验驳回理由。
        于是重复点击得到的是「已经处理过了」，而不是在陈旧行上写快照或算容量。

        @serialized_write 把这一整段（含守卫里的写快照 / 算容量）与那次 UPDATE 收在
        同一个写单元里：快照、容量、状态一起成或一起败。
        """
        spec = self._books.get(book)
        if spec is None:
            raise ValueError(f"unknown overtime book: {book!r}")
        rule = _ACTIONS.get(action)
        if rule is None:
            raise ValueError(f"unknown overtime action: {action!r}")
        if spec.prepare is not None:
            await spec.prepare()
        row = await spec.read(int(target_id))
        if row is None:
            raise OvertimeError(spec.unknown, spec.unknown)
        if row.get("status") != rule.expect:
            code = getattr(spec, rule.error_attr)
            raise OvertimeError(code, code)
        self._require_actor(action, actor, row)
        reject_reason = _clean_reject_reason(reason) if rule.requires_reason else None
        guard = spec.before.get(action)
        if guard is not None:
            await guard(actor, row)
        return await self._transition(
            spec,
            int(target_id),
            rule.status,
            (rule.expect,),
            actor.stamp,
            reject_reason,
            getattr(spec, rule.error_attr),
        )

    @_needs_migration
    @serialized_write
    async def withdraw(self, book: str, employee_id: int, target_id: int) -> dict:
        """员工撤回自己还没被审批的那一笔；别人的一律当作「不存在」。

        撤回也是判决（判的人是他自己），所以同样落 decided_by=staff:<id>；
        但不校验驳回理由，也不走批 / 驳的身份规则 —— 这是唯一允许提交人自己动的动作。
        """
        spec = self._books.get(book)
        if spec is None:
            raise ValueError(f"unknown overtime book: {book!r}")
        if spec.prepare is not None:
            await spec.prepare()
        row = await spec.read(int(target_id))
        if row is None or int(row["employee_id"]) != int(employee_id):
            raise OvertimeError(spec.unknown, spec.unknown)
        if row.get("status") != STATUS_PENDING:
            raise OvertimeError(spec.not_pending, spec.not_pending)
        return await self._transition(
            spec,
            int(target_id),
            STATUS_CANCELLED,
            (STATUS_PENDING,),
            f"staff:{int(employee_id)}",
            None,
            spec.not_pending,
        )

    def _require_actor(self, action: str, actor: EntryActor, row: dict) -> None:
        """作废只认超管；批 / 驳不许提交人自己点头。

        身份判据看「这一笔算谁的」（employee_id），不看 created_by —— 店长代别人
        补录的那一笔，批的仍然是他，允许。
        """
        if action == ACTION_VOID:
            if not actor.is_super:
                raise OvertimeError("forbidden", "forbidden")
            return
        if actor.employee_id is not None and int(actor.employee_id) == int(
            row["employee_id"]
        ):
            raise OvertimeError("cannot_self_accept", "cannot_self_accept")

    async def _transition(
        self,
        spec: _Book,
        target_id: int,
        status: str,
        expect: tuple[str, ...],
        decided_by: str,
        reject_reason: Optional[str],
        error_code: str,
    ) -> dict:
        """唯一一条 SET status：条件 UPDATE → 提交 → 回读。

        **状态谓词落在写锁里**：调用方那次「读出来是待审批」的判断在锁外，双击、两个
        标签页、网关重试会双双通过那道判断。到这儿再比一次状态，只有第一个改得动这一行。
        判「抢没抢到」用 rowcount 而不是 RETURNING（方言层只解析 command tag）。
        """
        stamp = self._now_iso()
        marks = ", ".join("?" for _ in expect)
        cur = await self._conn.execute(
            f"""UPDATE {spec.table}
                SET status = ?, decided_at = ?, decided_by = ?, reject_reason = ?,
                    updated_at = ?
                WHERE id = ? AND status IN ({marks})""",
            (status, stamp, decided_by, reject_reason, stamp, int(target_id), *expect),
        )
        if cur.rowcount != 1:
            # 0 = 状态已经被人改了；-1 = command tag 没解析出来。两种都按「没抢到」办。
            raise OvertimeError(error_code, error_code)
        await self._conn.commit()
        row = await spec.read(int(target_id))
        assert row is not None  # 刚改过的那一行
        row["employee_name"] = (await self._name_index()).get(
            int(row["employee_id"]), ""
        )
        return row

    # ── 补钟用存假抵扣（ADR 0124，2026-10-31 用户裁定）────────────────────────
    #
    # 一条抵扣单 = 员工 × **补钟登记月** × 天数（整天，`days × 17` 个半小时）。
    # 它不动补钟台账那几行本身（**不追溯**「抵的是哪几笔」：整天粒度本来就不对齐
    # 单笔，一笔可能是 6 小时），只在算净时长与存假余额时按月度汇总扣减。

    async def _deduction_rows(
        self, clause: str, params: tuple, order: str = ""
    ) -> list[dict]:
        """读抵扣单。**缺 0042 时回空**（读路径不 503）：没这张表就等于没抵过 ——
        加班页的登记与记录不该因为一次还没做的迁移整页挂掉。写路径另有一道
        `_require_deduction_table` 会点名要应用哪个脚本。"""
        try:
            cur = await self._conn.execute(
                f"SELECT {_DEDUCTION_COLUMNS} FROM overtime_leave_deductions{clause}"
                f"{f' ORDER BY {order}' if order else ''}",
                params,
            )
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            await self._rollback_quietly()
            return []
        return [_row_to_deduction(row) for row in await cur.fetchall()]

    async def _deduction_by_id(self, deduction_id: int) -> Optional[dict]:
        rows = await self._deduction_rows(" WHERE id = ?", (int(deduction_id),))
        return rows[0] if rows else None

    async def _my_deduction_rows(self, employee_id: int) -> list[dict]:
        """我自己提过的抵扣单（全部历史，新的在前）——员工端列表里那一块。"""
        return await self._deduction_rows(
            " WHERE employee_id = ?", (int(employee_id),), '"month" DESC, id DESC'
        )

    async def _deduction_days(self, employee_id: int, month: str) -> int:
        """那个人那个月**已批准**的抵扣天数合计（0 = 没抵过 / 缺 0042）。"""
        try:
            cur = await self._conn.execute(
                """SELECT COALESCE(SUM(days), 0) AS days FROM overtime_leave_deductions
                   WHERE employee_id = ? AND "month" = ? AND status = ?""",
                (int(employee_id), month, STATUS_APPROVED),
            )
            row = await cur.fetchone()
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            await self._rollback_quietly()
            return 0
        return int(dict(row)["days"] or 0) if row is not None else 0

    async def _deduction_days_by_employee(self, month: str) -> dict[int, int]:
        """那个月全店已批准的抵扣天数：``{员工号: 天数}``（月结账单用；缺表回空）。"""
        try:
            cur = await self._conn.execute(
                """SELECT employee_id, COALESCE(SUM(days), 0) AS days
                   FROM overtime_leave_deductions
                   WHERE "month" = ? AND status = ? GROUP BY employee_id""",
                (month, STATUS_APPROVED),
            )
            rows = await cur.fetchall()
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            await self._rollback_quietly()
            return {}
        return {
            int(dict(row)["employee_id"]): int(dict(row)["days"] or 0) for row in rows
        }

    async def _approved_makeup_half_hours(self, employee_id: int, month: str) -> int:
        """那个人那个月**已批准**的补钟小时（正数、半小时单位）—— 可抵的池子。"""
        cur = await self._conn.execute(
            """SELECT COALESCE(SUM(-half_hours), 0) AS half_hours FROM overtime_entries
               WHERE employee_id = ? AND entry_date LIKE ? AND status = ?
                 AND half_hours < 0""",
            (int(employee_id), f"{month}%", STATUS_APPROVED),
        )
        row = await cur.fetchone()
        return int(dict(row)["half_hours"] or 0) if row is not None else 0

    async def _month_sealed(self, month: str) -> bool:
        """那个月**整月**封过账没有 —— 封过就锁死，不能再抵、也不能再撤销。

        封账是整月操作：`AttendanceStore.seal()` 一次写全在册员工的行，
        `ensure_day_editable()` 也按整月挡。这里跟同一口径，避免「甲封了、乙还能撤」
        （AD-0139 决定 3）。
        缺表时回 `False`（老库先跑着，真正写时会由那张表自己的缺表错误兜住）。
        """
        try:
            cur = await self._conn.execute(
                """SELECT 1 FROM attendance_ledger
                   WHERE "month" = ? AND sealed_at IS NOT NULL LIMIT 1""",
                (month,),
            )
            row = await cur.fetchone()
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            await self._rollback_quietly()
            return False
        return row is not None

    async def deduction_capacity(self, employee_id: int, month: str) -> dict:
        """那个人那个月**还能抵几天**：补钟与存假两头都封顶（用户裁定）。

        * 补钟侧：已批补钟小时 − 已抵小时，够 `days × 8.5` 才抵得动；
        * 存假侧：当前有效余额（已扣掉已批抵扣）够 `days` 天才抵得动 —— **不能把存假
          抵成负数**（欠班本来就该欠着，拿不存在的假去抵是另一笔账）；
        * 已封账的月 `max_days` 直接 0（「封过账就不能再抵」的唯一判据）。

        这个数同时是员工端「申请抵扣」弹窗的上限与店长审批时的那道闸 —— 只有一份实现，
        两边不会对不上（弹出「可抵 2 天」、点下去却被拒是最糟的一种）。
        """
        # 月格式仍在这里拦：这条是 HTTP 读口，非法月份以前报 `invalid_month`（400），
        # 不能因为换了实现就变成 500（ADR 0139 的兼容底线）。
        target = _require_month(month)
        capacity = await LeaveLedger(self._write_lock_owner, now=self._now).capacity(
            int(employee_id), target
        )
        if capacity is None:
            # 旧路径经 `AttendanceStore.leave_balance_days()` 抛考勤那一层的
            # `unknown_employee`；原样复现（函数内 import：只在这一条错误支路用到）。
            from services.attendance.store import AttendanceError

            raise AttendanceError("unknown_employee", "unknown_employee")
        return capacity

    async def _require_deduction_table(self) -> None:
        """写路径的那道闸：缺 0042 时给一句能照做的 503，而不是一句业务错误。"""
        try:
            await self._conn.execute("SELECT 1 FROM overtime_leave_deductions LIMIT 1")
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            raise OvertimeError("not_migrated", "overtime_leave_deductions") from exc

    async def _capacity_for_decision(self, employee_id: int, month: str, days: int) -> dict:
        """提交/审批共用的那道闸：天数必须同时不超过补钟与存假（已在锁内调用）。"""
        capacity = await self.deduction_capacity(employee_id, month)
        if capacity["sealed"]:
            raise OvertimeError("month_sealed", month)
        if capacity["available_half_hours"] < days * HALF_HOURS_PER_LEAVE_DAY:
            raise OvertimeError(
                "deduction_exceeds_makeup", str(capacity["max_days_by_makeup"])
            )
        if capacity["leave_balance"] < days:
            raise OvertimeError(
                "deduction_exceeds_leave", str(capacity["max_days_by_leave"])
            )
        return capacity

    @_needs_migration
    @serialized_write
    async def submit_deduction(self, actor: EntryActor, month: Any, days: Any) -> dict:
        """员工提一笔「用存假抵补钟」：归月 = 补钟登记月，只能整天。

        三道闸（全在写锁里）：那个月**没封账**、补钟够抵、存假够扣。离职的人不能再提
        （他的账该由超管在离职时结掉）；别人的账提不了 —— `actor.employee_id` 是谁就
        提谁的，接口上也没有 `employee_id` 可填。
        """
        await self._require_deduction_table()
        if actor.employee_id is None:
            raise OvertimeError("unknown_employee", "unknown_employee")
        employee_id = int(actor.employee_id)
        target = _require_month(month)
        count = _require_days(days)
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        me = next(
            (item for item in roster if int(item["id"]) == employee_id), None
        )
        if me is None:
            raise OvertimeError("unknown_employee", "unknown_employee")
        if me.get("resigned"):
            raise OvertimeError("employee_resigned", "employee_resigned")
        await self._capacity_for_decision(employee_id, target, count)

        stamp = self._now_iso()
        cur = await self._conn.execute(
            "INSERT INTO overtime_leave_deductions"
            " (employee_id, \"month\", days, half_hours, status, created_by,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                employee_id,
                target,
                count,
                count * HALF_HOURS_PER_LEAVE_DAY,
                STATUS_PENDING,
                actor.stamp,
                stamp,
                stamp,
            ),
        )
        created = await cur.fetchone()
        await self._conn.commit()
        deduction = await self._deduction_by_id(int(created["id"]))
        assert deduction is not None  # 刚写进去的那一行
        return deduction

    @_needs_migration
    async def list_pending_deductions(
        self, *, exclude_employee_id: Optional[int] = None
    ) -> dict:
        """全店**等审批**的抵扣单（旧的在前，同加班登记那条队列的口径）。

        `exclude_employee_id` 给店长那一面用：自己提的抵扣不能自己批（同
        `decide(..., ACTION_APPROVE, ...)` 的 `cannot_self_accept`），摆在队列里就是一颗点了
        必被拒的钥匙。
        """
        clause = " WHERE status = ?"
        params: list[Any] = [STATUS_PENDING]
        if exclude_employee_id is not None:
            clause += " AND employee_id <> ?"
            params.append(int(exclude_employee_id))
        rows = await self._deduction_rows(clause, tuple(params), "created_at, id")
        names = await self._name_index()
        for row in rows:
            row["employee_name"] = names.get(int(row["employee_id"]), "")
        return {"today": self.today(), "count": len(rows), "deductions": rows}

    @_needs_migration
    async def list_deductions(
        self,
        *,
        month: Optional[str] = None,
        employee_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> dict:
        """全店抵扣单（管理端）：可按月 / 按人 / 按状态筛，新的在前。"""
        where: list[str] = []
        params: list[Any] = []
        if month not in (None, ""):
            where.append('"month" = ?')
            params.append(_require_month(month))
        if employee_id is not None:
            where.append("employee_id = ?")
            params.append(int(employee_id))
        if status not in (None, ""):
            where.append("status = ?")
            params.append(str(status))
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = await self._deduction_rows(
            clause, tuple(params), '"month" DESC, id DESC'
        )
        names = await self._name_index()
        for row in rows:
            row["employee_name"] = names.get(int(row["employee_id"]), "")
        return {"today": self.today(), "count": len(rows), "deductions": rows}

    async def _before_deduction_approve(self, actor: EntryActor, row: dict) -> None:
        """批抵扣前的额外守卫：那个月剩下的补钟还够不够扣。

        容量在锁内、状态回读之后算 —— 两个管理员同时批两张单时，第二张看到的是第一张
        已经占掉的额度，不会两张都过。
        """
        await self._capacity_for_decision(
            int(row["employee_id"]), str(row["month"]), int(row["days"])
        )

    async def _before_deduction_void(self, actor: EntryActor, row: dict) -> None:
        """作废抵扣前的额外守卫：封账的月份不许再动（ADR 0124）。"""
        month = str(row["month"])
        if await self._month_sealed(month):
            raise OvertimeError("month_sealed", month)

    # ── 按月统计与加班费（票 03）────────────────────────────────────────────

    @_needs_migration
    async def monthly_stats(self, month: str) -> dict:
        """全店某个月的加班费账单（管理端）：每人一行 + 合计行。

        只算**已批准**的登记 —— 待审批 / 已驳回 / 已撤回 / 已作废的没换来钱，不进账
        （与员工端月度卡同一条口径）。归月按 `entry_date` 的**自然月**，与什么时候审批
        无关：8 月 31 日的单 9 月才批，算在 8 月。

        金额 =（该月**快照**底薪 ÷ 该月日历天数 ÷ 8.5）× 该月净时长，**每人各自取整
        到元**再加总 —— 所以合计行是各行相加。拿全店小时数重算一遍会因为取整位置不同
        而差几块钱，那是两本账。

        没有快照的人（绕过审批写进来的历史数据）金额留 `None`，并在合计里计进
        `unpriced`：「算不出来」与「算出来是 0 元」是两件事，界面要分得开。
        """
        target = _require_month(month)
        rows = await self._rows(
            " WHERE status = ? AND entry_date LIKE ?",
            (STATUS_APPROVED, f"{target}%"),
            "entry_date ASC, id ASC",
        )

        snapshots: dict[int, int] = {}
        cur = await self._conn.execute(
            "SELECT employee_id, base_salary FROM overtime_salary_snapshots"
            " WHERE month = ?",
            (target,),
        )
        for row in await cur.fetchall():
            snapshots[int(row["employee_id"])] = int(row["base_salary"])

        days = _days_in_month(target)
        names = await self._name_index()
        # 那个月全店已批准的补钟抵扣（ADR 0124）：净时长里被抵掉的补钟加回来。
        deductions = await self._deduction_days_by_employee(target)
        buckets: dict[int, dict] = {}
        for row in rows:
            employee_id = int(row["employee_id"])
            bucket = buckets.setdefault(
                employee_id, {"overtime_half_hours": 0, "makeup_half_hours": 0}
            )
            half_hours = int(row["half_hours"])
            key = "overtime_half_hours" if half_hours > 0 else "makeup_half_hours"
            bucket[key] += abs(half_hours)

        items: list[dict] = []
        for employee_id in sorted(buckets):
            bucket = buckets[employee_id]
            overtime = bucket["overtime_half_hours"]
            makeup = bucket["makeup_half_hours"]
            deducted_days = deductions.get(employee_id, 0)
            deducted_half_hours = deducted_days * HALF_HOURS_PER_LEAVE_DAY
            # 净时长 = 加班 − 补钟 + 已用存假抵掉的补钟（那部分时间按休假算，不再欠班）。
            net_half_hours = overtime - makeup + deducted_half_hours
            net_hours = net_half_hours / 2
            salary = snapshots.get(employee_id)
            items.append(
                {
                    "employee_id": employee_id,
                    "employee_name": names.get(employee_id, ""),
                    "overtime_half_hours": overtime,
                    "overtime_hours": overtime / 2,
                    "makeup_half_hours": makeup,
                    "makeup_hours": makeup / 2,
                    "deducted_days": deducted_days,
                    "deducted_half_hours": deducted_half_hours,
                    "deducted_hours": deducted_half_hours / 2,
                    "net_half_hours": net_half_hours,
                    "net_hours": net_hours,
                    "base_salary": salary,
                    "amount": (
                        None if salary is None else _pay_for(salary, days, net_hours)
                    ),
                }
            )

        total = {
            "overtime_half_hours": sum(item["overtime_half_hours"] for item in items),
            "makeup_half_hours": sum(item["makeup_half_hours"] for item in items),
            "deducted_half_hours": sum(item["deducted_half_hours"] for item in items),
            "net_half_hours": sum(item["net_half_hours"] for item in items),
            "amount": sum(item["amount"] or 0 for item in items),
            "unpriced": sum(1 for item in items if item["amount"] is None),
        }
        total["overtime_hours"] = total["overtime_half_hours"] / 2
        total["makeup_hours"] = total["makeup_half_hours"] / 2
        total["deducted_hours"] = total["deducted_half_hours"] / 2
        total["net_hours"] = total["net_half_hours"] / 2
        return {
            "month": target,
            "days": days,
            "hours_per_day": HOURS_PER_DAY,
            "count": len(items),
            "items": items,
            "total": total,
        }

    # ── 计费底薪快照（票 03）────────────────────────────────────────────────

    @_needs_migration
    async def salary_snapshot(self, employee_id: int, month: str) -> Optional[int]:
        """某个人某个月的**计费底薪**；该月还没批过任何一笔就回 `None`。

        它是「这个月的钱按哪一版底薪算」的唯一答案（`GLOSSARY.md` 的「底薪快照」）：
        写下之后不再变，所以月中调薪不会把已经算出来的数改掉。
        """
        cur = await self._conn.execute(
            "SELECT base_salary FROM overtime_salary_snapshots"
            " WHERE employee_id = ? AND month = ?",
            (int(employee_id), _require_month(month)),
        )
        row = await cur.fetchone()
        return None if row is None else int(row["base_salary"])

    async def _write_snapshot(
        self, employee_id: int, month: str, base_salary: int
    ) -> None:
        """写下这个月的计费底薪 —— **已经有的不覆盖**（该月第一次审批定音）。

        不提交，而且**调用方必须已经持有写锁**（`_before_entry_approve` 在 `decide` 的
        `@serialized_write` 里调用它）：锁保证这一条 INSERT 与它后面那次状态变更之间
        不会有别的写请求收事务，于是「批了却没记底薪」与「记了底薪却没批」两种半截账
        都不会留在库里。
        """
        await self._conn.execute(
            "INSERT INTO overtime_salary_snapshots"
            " (employee_id, month, base_salary, created_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (tenant_id, employee_id, month) DO NOTHING",
            (int(employee_id), _require_month(month), int(base_salary), self._now_iso()),
        )

    async def _base_salary(self, employee_id: int) -> Optional[int]:
        """档案里的底薪（元/月）；没填回 `None` —— 「待补」是审批的拦路虎，不是 0。

        `0` 是合法值（不待补，同 `services/identity/profile.py` 的 `is_blank`），
        所以这里判的是「没这一格」而不是假值。
        """
        row = await EmployeeAccounts(self._write_lock_owner).get_profile_row(
            int(employee_id)
        )
        value = row.get("base_salary")
        return None if value is None or value == "" else int(value)

    @_needs_migration
    @serialized_write
    async def _before_entry_approve(self, actor: EntryActor, row: dict) -> None:
        """批登记前的额外守卫：定下这个月的计费底薪（票 03）。

        **底薪待补的人批不了**：钱算不出来，先让超管去花名册补档案。快照按**登记
        日期**那个月写（8 月的单 9 月才批，写的还是 8 月），而且只在该月还没有快照
        时才写 —— 「该月第一次点头」那一刻的底薪说了算。
        """
        employee_id = int(row["employee_id"])
        month = str(row["entry_date"])[:7]
        if await self.salary_snapshot(employee_id, month) is None:
            salary = await self._base_salary(employee_id)
            if salary is None:
                raise OvertimeError("salary_missing", "salary_missing")
            await self._write_snapshot(employee_id, month, salary)

    async def _before_entry_void(self, actor: EntryActor, row: dict) -> None:
        """作废补钟前的额外守卫：已经拿去抵扣的小时不能凭空消失。

        判据是「作废后，那个月剩下的已批补钟还够不够抵已批的抵扣天数」——不够就拒
        （makeup_in_use），提示先作废抵扣单。
        """
        if int(row["half_hours"]) >= 0:
            return
        employee_id = int(row["employee_id"])
        month = str(row["entry_date"])[:7]
        makeup = await self._approved_makeup_half_hours(employee_id, month)
        deducted = (await self._deduction_days(employee_id, month)) * HALF_HOURS_PER_LEAVE_DAY
        remaining = makeup - abs(int(row["half_hours"]))
        if remaining < deducted:
            raise OvertimeError("makeup_in_use", month)
