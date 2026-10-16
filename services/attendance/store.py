#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当月考勤表：一人一张卡，逐日三态 + 卡片上那几列（`GLOSSARY.md` 的「考勤表」）。

**这是投影，不是打卡系统**（`docs/adr/0112`）：不记上下班时刻、不判迟到早退、不算
实际工时。三个输入是排班结果（`staff_assignments`）、净时长（`overtime_entries` 里
**已批准**的那几笔，正负相抵）与花名册（公共层 `services.identity`）。这一层**只读**，
而且只算当月 —— 不提供历史月份的参数，选不到过去。

口径（票 01；假期那一套是票 03 的事）：

* 一天三态：排了班 = `work`、有排班行但班次为空 = `rest`、**压根没有排班行** =
  `todo`（轮转规则没铺到，「待补」）。今天之后的日期 `state` 是 `None` —— 那是「还没
  到」，不是「待补」。
* 名单 = 在职员工（**已批准且未停用**，同排班周表的口径）+ 当月有排班或加班记录的
  人（含当月离职的）。没有当月记录的**历史**离职人员不进表。
* 卡片列：职位 / 底薪 / 年限（工龄奖）/ 是否新入职 / 当月净时长。入职日期为空的人
  `new_hire` 是 `None`（留空），不是 `False`（「算不出来」与「不是新来的」是两件事）。

「今天」取**营业日**（06:00 切，同排班）：考勤表的格子就是
`staff_assignments.business_date`，两边的「哪一天」必须是同一个词。
"""

from __future__ import annotations

import functools
import logging
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

import asyncpg

from db_core.utils import CHINA_TZ
from services.business_day import current_business_date
from services.identity import EmployeeAccounts
from services.overtime import STATUS_APPROVED

logger = logging.getLogger(__name__)

__all__ = [
    "STATE_REST",
    "STATE_TODO",
    "STATE_WORK",
    "AttendanceError",
    "AttendanceStore",
]

# 一天的三态。第四态是「空」—— 今天之后的日子 `state` 为 `None`。
STATE_WORK = "work"  # 那天排了班
STATE_REST = "rest"  # 那天有排班行、班次为空（休）
STATE_TODO = "todo"  # 那天压根没有排班行（轮转规则没铺到，「待补」）


def _needs_migration(method):
    """要读的表还没建（0005 / 0021 没应用）时，换成一个能照做的错。

    照排班 `store.py` 的同一个装饰器：整页不该是一个 500 traceback，店长要看到的是
    「去 Admin『系统更新 → 数据库迁移』应用哪个脚本」。顺带把失败的事务回滚掉，否则
    这条连接后面的查询会全跟着报 `current transaction is aborted`。
    """

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            raise AttendanceError("not_migrated", str(exc)) from exc

    return wrapper


class AttendanceError(ValueError):
    """考勤这层的错误。``code`` 是给 API 层查文案用的稳定标识，不是给店长看的文案。"""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def _next_month(start: date) -> date:
    """下月一号 —— 区间一律**右开**（`>= 本月一号 AND < 下月一号`），不自己数月份长度。"""
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


class AttendanceStore:
    """当月考勤表的读口。构造要一个已打开的库连接（同 `SchedulingStore` 的口径）。"""

    def __init__(self, conn_or_db, now: Optional[Callable[[], datetime]] = None):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("AttendanceStore requires an open database connection")
        self._conn = conn
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._write_lock_owner = conn_or_db

    def _now_dt(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value

    def today(self) -> str:
        """当前营业日（06:00 切，北京时）—— 与 `staff_assignments.business_date` 同一个词。"""
        return current_business_date(self._now_dt())

    async def _rollback_quietly(self) -> None:
        try:
            await self._conn.rollback()
        except Exception:  # pragma: no cover - 回滚失败不该盖住原始错误
            logger.debug("回滚失败（忽略）", exc_info=True)

    async def _assignment_rows(self, first: str, next_month: str) -> dict[int, dict[str, Any]]:
        """当月每天每人的排班行：``{员工号: {营业日: 班次 id 或 None}}``。

        **有行**就说明那天被铺过（`None` = 休）；没有这一格就是「待补」，所以这里不
        过滤班次为空的行 —— 过滤掉的话「休」会与「没铺到」塌成同一件事。
        """
        cur = await self._conn.execute(
            """SELECT employee_id, business_date, shift_id FROM staff_assignments
               WHERE business_date >= ? AND business_date < ?""",
            (first, next_month),
        )
        rows: dict[int, dict[str, Any]] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            rows.setdefault(int(mapping["employee_id"]), {})[mapping["business_date"]] = (
                None if mapping["shift_id"] is None else int(mapping["shift_id"])
            )
        return rows

    async def _month_entries(self, first: str, next_month: str) -> tuple[dict[int, int], set[int]]:
        """当月加班登记：``(已批准的半小时数合计, 这个月有登记的人)``。

        两个答案一次查出来，但**判据不一样**：

        * 净时长只算**已批准**的那几笔（票 01 验收 7）—— 待审批 / 已驳回 / 已撤回 /
          已作废的登记一分都不算（「有单子等着批」是待办队列里的事，不在这个数里）；
        * 「这个人当月有没有加班记录」算**全部状态**（票 01 验收 5）—— 一笔还没批的
          加班也是「他那个月上过班」的证据，离职的人靠它进表。

        归月按 `entry_date` 的自然月，与什么时候审批无关（8/31 的单 9 月才批，算 8 月）。
        """
        cur = await self._conn.execute(
            """SELECT employee_id, half_hours, status FROM overtime_entries
               WHERE entry_date >= ? AND entry_date < ?""",
            (first, next_month),
        )
        totals: dict[int, int] = {}
        recorded: set[int] = set()
        for row in await cur.fetchall():
            mapping = dict(row)
            employee_id = int(mapping["employee_id"])
            recorded.add(employee_id)
            if mapping["status"] == STATUS_APPROVED:
                totals[employee_id] = totals.get(employee_id, 0) + int(mapping["half_hours"])
        return totals, recorded

    @_needs_migration
    async def sheet(self) -> dict:
        """当月那一张考勤表：月框 + 一人一张卡。**唯一的读口**，没有月份参数。"""
        today = self.today()
        month = today[:7]
        start = date.fromisoformat(f"{month}-01")
        next_month = _next_month(start)
        # 区间右开：`[本月一号, 下月一号)` —— 不自己数月份长度（2 月与 31 天的月各一次坑）。
        first, until = start.isoformat(), next_month.isoformat()

        rows = await self._assignment_rows(first, until)
        totals, recorded = await self._month_entries(first, until)
        # 当月有排班或加班记录的人：离职的人靠它进表（票 01 验收 5）。
        on_record = set(rows) | recorded
        # 员工是谁只从公共层取（同排班的路子）：职位 / 底薪 / 工龄奖 / 入职日期都在
        # 那一份投影里。时钟跟着本服务的钟走，派生字段才与「今天」同一个。
        roster = await EmployeeAccounts(self._write_lock_owner, now=self._now).list_roster()

        # 月框：当月每一天一条（`day` 是日期号，前端拿它当表头）。`is_future` 是
        # 「还没到」的判据 —— 今天之后留空，不是「待补」（票 01 验收 4）。
        days = []
        for offset in range(monthrange(start.year, start.month)[1]):
            day = start + timedelta(days=offset)
            key = day.isoformat()
            days.append(
                {
                    "day": day.day,
                    "business_date": key,
                    "is_today": key == today,
                    "is_future": key > today,
                }
            )

        employees = []
        for employee in roster:
            employee_id = int(employee["id"])
            if not _is_on_the_sheet(employee, employee_id in on_record):
                continue
            theirs = rows.get(employee_id, {})
            hire_date = employee.get("hire_date") or None
            employees.append(
                {
                    "id": employee_id,
                    "name": employee.get("name") or "",
                    "job_title": employee.get("job_title") or "",
                    "base_salary": employee.get("base_salary"),
                    "seniority_bonus": employee.get("seniority_bonus"),
                    # 入职日期落在当月 = 新入职；没填入职日期 = 留空（`None`），不是「不是新来的」。
                    "new_hire": None if not hire_date else str(hire_date)[:7] == month,
                    "net_hours": totals.get(employee_id, 0) / 2,
                    "days": [
                        {
                            "business_date": day["business_date"],
                            "state": _day_state(today, day["business_date"], theirs),
                        }
                        for day in days
                    ],
                }
            )

        return {
            "month": month,
            "today": today,
            "days": days,
            "employees": employees,
        }


def _is_on_the_sheet(employee: dict, on_record: bool) -> bool:
    """这个人进不进当月的表。

    在职（已批准、未停用）的一律在；此外**当月有排班或加班记录**的也在 —— 当月离职的
    人那天还有班，漏掉他就漏结。反过来，一个早已停用、当月又一条记录都没有的人不在表上。
    """
    return on_record or (bool(employee.get("approved")) and not employee.get("disabled"))


def _day_state(today: str, business_date: str, theirs: dict[str, Any]) -> Optional[str]:
    """那天这个人是什么状态；今天之后是 `None`（留空，不是「待补」）。"""
    if business_date > today:
        return None
    if business_date not in theirs:
        return STATE_TODO
    return STATE_REST if theirs[business_date] is None else STATE_WORK
