#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加班与补钟台账（`CONTEXT.md` 的「加班与补钟登记」，决策见 `docs/adr/0100`）。

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
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

import asyncpg

from db_core.utils import CHINA_TZ
from services.identity import EmployeeAccounts, serialized_write

logger = logging.getLogger(__name__)

__all__ = [
    "HALF_HOURS_MAX",
    "KIND_MAKEUP",
    "KIND_OVERTIME",
    "MAX_REASON",
    "EntryActor",
    "OvertimeError",
    "OvertimeLedger",
    "STATUS_APPROVED",
    "STATUS_CANCELLED",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "STATUS_VOIDED",
]

# 状态机（`docs/adr/0100`）：本票只用得到 `pending` 与 `cancelled`，另外三个一次到位 ——
# 状态取值是数据面契约，票 02 的审批不该再改一次列的定义。
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"
STATUS_VOIDED = "voided"

# 读的时候按符号派生：正数是加班、负数是补钟。它不是库里的一列。
KIND_OVERTIME = "overtime"
KIND_MAKEUP = "makeup"

# 界面上的步长是 0.5 小时，库里存它的整数倍（`half_hours`）。单条上限 ±12 小时 ——
# 防的是手滑打成 120，不是替业务定政策。
HALF_HOURS_MAX = 24

# 事由限长：它是登记卡上的一行话，不是留言板。超了报错而不是悄悄截断（同排班的
# `MAX_REQUEST_NOTE`：员工写下的东西不该被系统改掉）。
MAX_REASON = 50

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

# 读登记时选的列：员工页与管理端待办共用一份，免得两处列名漂移。
_ENTRY_COLUMNS = (
    "id, employee_id, entry_date, half_hours, reason, status, reject_reason,"
    " created_by, decided_by, decided_at, created_at, updated_at"
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


class OvertimeError(ValueError):
    """台账层的输入错误。``code`` 是给 API 层查文案用的稳定标识，不是给员工看的文案。"""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


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


def _require_month(value: Any) -> str:
    text = str(value or "").strip()
    if not _MONTH_RE.match(text):
        raise OvertimeError("invalid_month", "invalid_month")
    try:
        date.fromisoformat(f"{text}-01")
    except ValueError:
        raise OvertimeError("invalid_month", "invalid_month")
    return text


def _month_summary(rows: list[dict], month: str) -> dict:
    """一个月的合计，**待审批与已批准分开**（员工端那张月度卡的口径）。

    只有这两种状态算进「这个月加了多少」：已驳回 / 已撤回 / 已作废的登记不进合计
    —— 它们没换来钱，也不该让员工以为自己加过（票 03 的全店统计同一条口径）。

    净时长 = 加班与补钟**正负相抵**（`CONTEXT.md` 的「净时长」）。负数照实给：员工端
    要看得见「这个月是净欠的」，扣不扣钱是票 03 的事。
    """
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
        bucket["net_half_hours"] = (
            bucket["overtime_half_hours"] - bucket["makeup_half_hours"]
        )
        for name in ("overtime", "makeup", "net"):
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
        self._local_write_lock = None

    @property
    def _write_lock(self):
        shared = getattr(self._write_lock_owner, "_write_lock", None)
        if shared is not None:
            return shared
        if self._local_write_lock is None:
            import asyncio

            self._local_write_lock = asyncio.Lock()
        return self._local_write_lock

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
        与卫生日常的口径（`CONTEXT.md` 的「加班与补钟登记」_Avoid_ 点名了这一条）。
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
        return {
            "today": self.today(),
            "yesterday": self.yesterday(),
            "month": target,
            "summary": _month_summary(rows, target),
            "entries": rows,
        }

    async def _claim(
        self, entry_id: int, status: str, expect: tuple[str, ...], stamp: str
    ) -> None:
        """把这一笔从 `expect` 里的某个状态改成 `status`；没抢到就报「已经处理过了」。

        **状态谓词落在写锁里**：调用方那次「读出来是待审批」的判断在锁外，双击、两个
        标签页、网关重试会双双通过那道判断。到这儿再比一次状态，只有第一个改得动这一行。
        不提交：调用方决定这一条 UPDATE 与它后面的读怎么收尾。

        判「抢没抢到」用 `rowcount` 而不是 `... RETURNING id`：方言层对 UPDATE 只解析
        asyncpg 的 command tag，RETURNING 的行取不出来（同排班 `_claim_request`）。
        """
        marks = ", ".join("?" for _ in expect)
        cur = await self._conn.execute(
            f"""UPDATE overtime_entries
                SET status = ?, decided_at = ?, updated_at = ?
                WHERE id = ? AND status IN ({marks})""",
            (status, stamp, stamp, int(entry_id), *expect),
        )
        if cur.rowcount != 1:
            # 0 = 状态已经被人改了；-1 = command tag 没解析出来。两种都按「没抢到」办 ——
            # 宁可让调用方刷新一次，也不能重复写一遍。
            raise OvertimeError("entry_not_pending", "entry_not_pending")

    @serialized_write
    async def _decide(self, entry_id: int, status: str, expect: tuple[str, ...]) -> dict:
        """把这一笔从 `expect` 置成 `status` 并记下时间（撤回与票 02 的批 / 驳共用）。"""
        stamp = self._now_iso()
        await self._claim(entry_id, status, expect, stamp)
        await self._conn.commit()
        entry = await self._entry_by_id(int(entry_id))
        assert entry is not None  # 刚改过的那一行
        return entry

    @_needs_migration
    async def cancel(self, employee_id: int, entry_id: int) -> dict:
        """员工撤回自己**还没被审批**的那一笔。

        撤回只动这一行：台账是流水，撤回不是删除（记录还在、状态是 `cancelled`）。
        管理端的待办按 `pending` 读，所以它自己就不在那儿了 —— 票 02 兑现那一半。

        别人的登记一律当作「不存在」：不告诉调用方「它在，但不是你的」。
        """
        row = await self._entry_by_id(entry_id)
        if row is None or int(row["employee_id"]) != int(employee_id):
            raise OvertimeError("unknown_entry", "unknown_entry")
        if row["status"] != STATUS_PENDING:
            # 批完 / 驳完 / 已撤回 / 已作废都不再动：状态机只往前走。
            raise OvertimeError("entry_not_pending", "entry_not_pending")
        return await self._decide(int(entry_id), STATUS_CANCELLED, (STATUS_PENDING,))
