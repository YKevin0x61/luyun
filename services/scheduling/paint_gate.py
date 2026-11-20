#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班落笔闸：一格里「能不能写」只在这里判一次。

为什么要一道门：月历的单格、考勤表的逐日改格、清除、休半天、周表的批量落笔，
五条路原来各写一份判据，于是同一格在不同入口结论不一样 —— 批量端把「未批准」
与「已离职但还没到离职日」的人整块拒掉，单格端照写；可补记的 30 天、批量的
200 格又各在前端抄了一份（前端那份还不认识在册与半天休）。判据写两遍就会漂，
这一道门是唯一的一份。

分工：

* **判据**（本模块）：理由词表、动作表、窗口、判据顺序、读哪几张表；
* **读层**（read_...）：先把那一批格要用的花名册 / 请假 / 班次行一次读齐，
  判据本身是纯函数 —— 表驱动用例不用起库；
* **传输语义**（调用方）：单格口把 skip 翻成 SchedulingError（single_error），
  批量口原样塞进 skipped[]。判据不抛异常，因为它要同时服务两种口。

动作表是数据不是代码：新增一种落笔，只加一行规则；新增一道判据，只加一个函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from services.business_day import shift_business_date
from services.identity.accounts import EmployeeAccounts
from services.scheduling.day_grid import DayGrid

#: 单日覆盖与批量端点共用的可补记天数（一处定义，两处都读它）。
BACKFILL_DAYS = 30
#: 批量落笔一次最多几格：超过就整批 400，不做一半。
MAX_BATCH_CELLS = 200

# —— 判据理由 ——
# 前四个是批量口的 skipped 理由；单格口经 single_error 翻成错误码。
OUT_OF_ROSTER = "out_of_roster"
RESIGNED_EMPLOYEE = "resigned_employee"
PAST_DAY = "past_day"
BEYOND_WINDOW = "beyond_window"
LEAVE_APPROVED = "leave_approved"
HALF_REST_NEEDS_SHIFT = "half_rest_needs_shift"
HALF_REST_NEEDS_SHIFT_MESSAGE = (
    "那天没有班次（休 / 还没排到），标不上「休半天」：先给那天排一个班次"
)

# —— 落笔动作 ——
# 名字是「写什么」，不是「哪个端点」：批量口的 restore 也是 clear，void 是另一种清除。
PAINT_SHIFT = "shift"
PAINT_REST = "rest"
PAINT_CLEAR = "clear"
PAINT_VOID = "void"
PAINT_HALF_REST = "half_rest"
PAINT_ACTIONS = (PAINT_SHIFT, PAINT_REST, PAINT_CLEAR, PAINT_VOID, PAINT_HALF_REST)

__all__ = [
    "BACKFILL_DAYS",
    "MAX_BATCH_CELLS",
    "BEYOND_WINDOW",
    "HALF_REST_NEEDS_SHIFT",
    "HALF_REST_NEEDS_SHIFT_MESSAGE",
    "LEAVE_APPROVED",
    "OUT_OF_ROSTER",
    "PAINT_ACTIONS",
    "PAINT_CLEAR",
    "PAINT_HALF_REST",
    "PAINT_REST",
    "PAINT_SHIFT",
    "PAINT_VOID",
    "PAST_DAY",
    "RESIGNED_EMPLOYEE",
    "Limits",
    "Person",
    "Reads",
    "Skip",
    "backfill_floor",
    "judge",
    "judge_all",
    "limits_for",
    "read",
    "single_error",
]


@dataclass(frozen=True)
class Limits:
    """某一天看出去的那扇窗：今天、最早可补记、最晚已铺到。"""

    today: str
    first_day: str
    last_day: str


@dataclass(frozen=True)
class Person:
    """花名册那一行里判据要的两个字段。

    left 是离职日（YYYY-MM-DD）；None = 在职，空串 = 已离职但没填日期
    （这种人在任何一天都不该再被写）。
    """

    approved: bool
    left: Optional[str]


@dataclass(frozen=True)
class Reads:
    """一批格要用的三条读：谁在册、哪天请假、哪天有班次（只有半休要看）。"""

    roster: dict[int, Person]
    leave: dict[int, set[str]]
    shift_days: set[tuple[int, str]]


@dataclass(frozen=True)
class Skip:
    """判据的结论：None 才是可写。detail 是给文案填空的那个参数。"""

    reason: str
    detail: Optional[str] = None


def backfill_floor(today: str) -> str:
    """最早可补记的那一天（含）。"""
    return shift_business_date(today, -BACKFILL_DAYS)


def limits_for(today: str, last_day: str) -> Limits:
    """把「今天 + 已铺到的末日」收成一份判据用的窗。

    last_day 由调用方给（展开窗口的末日，store 的 _window_from 算）；
    最早可补记那天在这里派生，所以没有第二个地方能发明另一个 30 天。
    """
    return Limits(today=today, first_day=backfill_floor(today), last_day=last_day)


# —— 五道判据：都是纯函数，返回值是「拒的理由」或 None ——


def _check_roster(employee_id: int, day: str, reads: Reads, limits: Limits) -> Optional[Skip]:
    person = reads.roster.get(employee_id)
    if person is None or not person.approved:
        return Skip(OUT_OF_ROSTER)
    if person.left is not None and (person.left == "" or day > person.left):
        return Skip(RESIGNED_EMPLOYEE, person.left)
    return None


def _check_window(employee_id: int, day: str, reads: Reads, limits: Limits) -> Optional[Skip]:
    if day > limits.last_day:
        return Skip(BEYOND_WINDOW, limits.last_day)
    return None


def _check_past(employee_id: int, day: str, reads: Reads, limits: Limits) -> Optional[Skip]:
    if day < limits.first_day:
        return Skip(PAST_DAY, limits.first_day)
    return None


def _check_leave(employee_id: int, day: str, reads: Reads, limits: Limits) -> Optional[Skip]:
    if day in reads.leave.get(employee_id, ()):
        return Skip(LEAVE_APPROVED)
    return None


def _check_shift_on_day(
    employee_id: int, day: str, reads: Reads, limits: Limits
) -> Optional[Skip]:
    if (employee_id, day) not in reads.shift_days:
        return Skip(HALF_REST_NEEDS_SHIFT)
    return None


_CHECKS = {
    "roster": _check_roster,
    "window": _check_window,
    "past": _check_past,
    "leave": _check_leave,
    "shift_on_day": _check_shift_on_day,
}

#: 每个动作要过哪几道，按顺序。顺序是行为的一部分：先看在册再看窗口，所以
#: 一个不在册的人落在很旧的日子里，报的是「不在册」而不是「太旧了」。
_DAY_RULES = ("roster", "window", "past", "leave")
_RULES = {
    PAINT_SHIFT: _DAY_RULES,
    PAINT_REST: _DAY_RULES,
    PAINT_CLEAR: _DAY_RULES,
    PAINT_VOID: _DAY_RULES,
    # 半天休多一道：那天本来就没班次（休 / 还没排到）就标不上。
    PAINT_HALF_REST: _DAY_RULES + ("shift_on_day",),
}


def judge(action: str, cell: tuple[int, str], reads: Reads, limits: Limits) -> Optional[Skip]:
    """判一格。cell = (employee_id, "YYYY-MM-DD")；可写返回 None。"""
    try:
        checks = _RULES[action]
    except KeyError:
        raise ValueError(f"unknown paint action: {action!r}") from None
    employee_id, day = int(cell[0]), str(cell[1])
    for name in checks:
        skip = _CHECKS[name](employee_id, day, reads, limits)
        if skip is not None:
            return skip
    return None


def judge_all(
    action: str, cells: Iterable[tuple[int, str]], reads: Reads, limits: Limits
) -> list[Optional[Skip]]:
    """批量口用：结论与 cells 一一对应、同序。"""
    return [judge(action, cell, reads, limits) for cell in cells]


#: 单格口的翻译表：只有「不在册」换了名字。批量口不用它，直接把 skip.reason
#: 塞进 skipped[]。单格口除了月历单格还有考勤 PUT，两者共用一个 404 文案。
_SINGLE_ERRORS = {OUT_OF_ROSTER: "unknown_employee"}


def single_error(skip: Skip) -> tuple[str, str]:
    """单格口的 raise 参数。detail 缺省时用错误码填 args（没有 {} 的文案不看它）。"""
    code = _SINGLE_ERRORS.get(skip.reason, skip.reason)
    return code, skip.detail or code


# —— 读层：把这一批格要用的三条读一次读齐 ——


def _left_of(row: dict) -> Optional[str]:
    value = row.get("resigned_at")
    if value is None:
        return None
    return str(value)[:10]


async def read(
    conn_or_db: Any,
    cells: Iterable[tuple[int, str]],
    *,
    need_shift: bool = False,
    need_roster: bool = True,
    now: Any = None,
) -> Reads:
    """读判据要的东西。cells 里出现的员工与日子就是全部范围，不多读一天。

    只读（不自建连接、不持写锁）：调用方拿写锁时用它也没问题。
    """
    ids = sorted({int(cell[0]) for cell in cells})
    days = sorted({str(cell[1]) for cell in cells})
    if not ids or not days:
        return Reads(roster={}, leave={}, shift_days=set())
    wanted = set(ids)
    roster: dict[int, Person] = {}
    if need_roster:
        for row in await EmployeeAccounts(conn_or_db, now=now).list_roster():
            employee_id = int(row["id"])
            if employee_id in wanted:
                roster[employee_id] = Person(
                    approved=bool(row["approved"]), left=_left_of(row)
                )
    grid = DayGrid(conn_or_db, now=now)
    leave = await grid.leave_days(ids, days[0], days[-1])
    shift_days: set[tuple[int, str]] = set()
    if need_shift:
        for cell in await grid.cells(
            days[0], days[-1], employee_ids=ids, roster_only=False
        ):
            if cell.shift_id is not None:
                shift_days.add((int(cell.employee_id), str(cell.business_date)))
    return Reads(roster=roster, leave=leave, shift_days=shift_days)
