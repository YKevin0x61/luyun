#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班日格（Day Cell）：`staff_assignments` 的**唯一只读入口**（ADR 0138）。

一张排班结果行就是「某个人某一天」的那一格：有班次是上班、`shift_id` 空是休；那天批过
请假就是休 + 覆盖记录里的 `kind='leave'`。周表、月历、当日名单、员工端的两条、考勤表
以前各写各的 SQL、各解释一遍列名，于是同一格在六个地方能说出六种话。现在它们都从这里拿
`DayCell`，判据只有一份。

**只读**：不自建连接、不持写锁、不写库，也不自己读系统时钟（「今天」是各投影自己的
事；在册判断用的时钟可由调用方注入，不传就用 `EmployeeAccounts` 的真实时钟）。
「那天有没有行」由 cell 存不存在表示 —— 不补格：没有行就是「还没铺到」，补出来的空格会
把它和「那天休」塌成同一件事。请假另走 `leave_days()`：批了假的人可能根本没有结果行
（比如离职前批的、或规则还没铺到的那天），只有覆盖记录知道他请假了。

分层上这里只 import 公共层的员工花名册（`services.identity.accounts`），不 import 排班
自己的 store，也不认识卫生 —— 考勤 → 这里、排班 store → 这里是单向的。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Optional

import asyncpg

from services.identity.accounts import EmployeeAccounts

__all__ = [
    "DayCell",
    "DayGrid",
    "KIND_LEAVE",
    "SOURCE_OVERRIDE",
    "SOURCE_RULE",
    "is_half_rest",
]

logger = logging.getLogger(__name__)

# 排班结果这一行是谁写的（`staff_assignments.source`）。
SOURCE_RULE = "rule"
SOURCE_OVERRIDE = "override"

# 覆盖记录的 kind（`scheduling_overrides.kind`）：请假的那一种。读（这里）与写
# （store 的批假 / 删覆盖）共用同一个 DB 值，定义放这里、store 从这里 import。
KIND_LEAVE = "leave"

# 请假标记读不出来时的降级文案（0007 没应用时所有人那天本来就只是「休」，不该把
# 整页变成 503）。
_LEAVE_WARNING = "请假标记读不了（0007 未应用？），这天按「休」显示: %s"


def _normalize_ids(employee_ids: Iterable[int]) -> list[int]:
    """员工号去重排序；空输入回空列表（「空 = 不限」还是「空 = 不查」由调用方决定）。"""
    return sorted({int(item) for item in employee_ids})


def is_half_rest(shift_id: Any, value: Any) -> bool:
    """这一行是不是「休半天」（票 07 / ADR 0114）：**那天得有班次**，标志才作数。

    `half_rest` 是 BIGINT 0/1（仓库口径的布尔，同 `staff_shifts.is_active`）。写路径
    （`_write_day_row`）把「改成休」那天的标志清成 0，所以正常情况下不会出现
    「`shift_id` 空 + 标志为 1」的行；这里对读到的行再挡一次 —— 手工 SQL / 历史数据里
    那种行不该在页面上变成「休半天」（`休 + 休半天` 是自相矛盾的一句话）。
    """
    if shift_id is None:
        return False
    return bool(int(value or 0))


@dataclass(frozen=True)
class DayCell:
    """某人某天的一格排班结果（只读快照）。

    `shift_id is None` 就是休；`leave` 只在休的那一格上可能为 `True`（上班的人请假是
    另一件事，不在这张表里）。`source` 是 `rule` / `override`，`overridden` 只是它的
    一种读法，别在调用方各写一遍 `== "override"`。
    """

    employee_id: int
    business_date: str
    shift_id: Optional[int]
    zone_id: Optional[int]
    source: Optional[str]
    half_rest: bool
    leave: bool

    @property
    def overridden(self) -> bool:
        """这一行是单日覆盖写的（不是轮转规则算出来的）。"""
        return self.source == SOURCE_OVERRIDE


class DayGrid:
    """按日期区间读排班日格（只读）。构造抄 `DutyRoster`：接受 DatabaseManager 或连接。"""

    def __init__(
        self,
        conn_or_db: Any,
        now: Optional[Callable[[], datetime]] = None,
    ):
        self._conn = getattr(conn_or_db, "_conn", conn_or_db)
        if self._conn is None:
            raise RuntimeError("DayGrid requires an open database connection")
        self._write_lock_owner = conn_or_db
        # 只给「在册」判断用（花名册的 `resigned` 是相对今天算的）；日格自己不读时钟。
        self._now = now

    async def _rollback_quietly(self) -> None:
        try:
            await self._conn.rollback()
        except Exception:  # pragma: no cover - 回滚失败不该盖住原始错误
            logger.debug("回滚失败（忽略）", exc_info=True)

    async def on_roster_ids(self) -> set[int]:
        """在册的人：花名册里**已批准且未停用**的那些（`list_roster()` 的同一份判据）。

        停用（离职）只改花名册，结果行照旧躺在 `staff_assignments` 里；「谁在上班」不能
        只数结果行，否则同一天会给店长和员工两个人数。
        """
        roster = await EmployeeAccounts(
            self._write_lock_owner, now=self._now
        ).list_roster()
        return {
            employee["id"]
            for employee in roster
            if employee["approved"] and not employee["resigned"]
        }

    async def cells(
        self,
        first: str,
        last: str,
        *,
        employee_ids: Optional[Iterable[int]] = None,
        roster_only: bool = True,
    ) -> list[DayCell]:
        """`[first, last]`（两端都含）里**存在**的结果行，按 (员工号, 营业日) 排。

        * `employee_ids` 给了就只读这几个人；空列表直接回空（不是「不限」）。
        * `roster_only=True` 只留在册的人（`on_roster_ids`）；空名单直接回空。
        * 不补格：没有结果行的那天不出现在返回值里，调用方自己按窗口补「还没排到」。
        * `half_rest` 在这里归一：`shift_id` 空恒为 `False`（见 `is_half_rest`）。
        * `leave` 只在休行上查一次 `scheduling_overrides`（`kind='leave'`），上班行恒为
          `False`。缺 0007 那张表 / 列时降级成「都不算请假」，并回滚掉失败的事务 ——
          读口不该因为一次没应用的迁移整页 503。

        `staff_assignments` 缺表时**照常抛**（由调用方的 `_needs_migration` 统一换成
        「去应用 0005」那个错）：这张表是日格的本体，读不到就不是「今天没人上班」。
        """
        ids: Optional[list[int]] = None
        if employee_ids is not None:
            ids = _normalize_ids(employee_ids)
            if not ids:
                return []

        roster: Optional[set[int]] = None
        if roster_only:
            roster = await self.on_roster_ids()
            if not roster:
                return []

        where = ["business_date >= ?", "business_date <= ?"]
        params: list[Any] = [str(first), str(last)]
        if ids is not None:
            where.append(f"employee_id IN ({', '.join('?' for _ in ids)})")
            params.extend(ids)
        cur = await self._conn.execute(
            f"""SELECT employee_id, business_date, shift_id, zone_id, source, half_rest
                FROM staff_assignments
                WHERE {' AND '.join(where)}""",
            tuple(params),
        )
        # 先攒「扁」的行：请假要等这一批读完才知道该问哪些人（且只问有休行的人）。
        rows: list[tuple[int, str, Optional[int], Optional[int], Optional[str], bool]] = []
        for row in await cur.fetchall():
            mapping = dict(row)
            employee_id = int(mapping["employee_id"])
            if roster is not None and employee_id not in roster:
                continue
            shift_id = None if mapping["shift_id"] is None else int(mapping["shift_id"])
            zone_id = None if mapping["zone_id"] is None else int(mapping["zone_id"])
            rows.append(
                (
                    employee_id,
                    str(mapping["business_date"]),
                    shift_id,
                    zone_id,
                    mapping["source"],
                    is_half_rest(shift_id, mapping["half_rest"]),
                )
            )

        rest_ids = {employee_id for employee_id, _day, shift_id, *_rest in rows if shift_id is None}
        leave_map: dict[int, set[str]] = {}
        if rest_ids:
            leave_map = await self._leave_map(rest_ids, str(first), str(last))

        cells = [
            DayCell(
                employee_id=employee_id,
                business_date=day,
                shift_id=shift_id,
                zone_id=zone_id,
                source=source,
                half_rest=half_rest,
                leave=(
                    shift_id is None
                    and day in leave_map.get(employee_id, ())
                ),
            )
            for employee_id, day, shift_id, zone_id, source, half_rest in rows
        ]
        cells.sort(key=lambda cell: (cell.employee_id, cell.business_date))
        return cells

    async def leave_days(
        self, employee_ids: Iterable[int], first: str, last: str
    ) -> dict[int, set[str]]:
        """这几个人 `[first, last]` 里批了请假的那几天（两端都含）。

        给「请假了但可能没有结果行」的名单用：员工月历/今天页要标出批过的假，而结果行
        里不一定有那一格（比如规则还没铺到、或离职前批的）。空输入直接回空字典；缺 0007
        时与 `cells` 同一个降级（空字典 + 一次 warning）。
        """
        ids = _normalize_ids(employee_ids)
        if not ids:
            return {}
        return await self._leave_map(ids, str(first), str(last))

    async def _leave_map(
        self, employee_ids: Iterable[int], first: str, last: str
    ) -> dict[int, set[str]]:
        """`scheduling_overrides` 里 `kind='leave'` 的 `{员工号: {营业日}}`。

        `cells` 与 `leave_days` 共用这一条查询：日期区间两端含，员工号给了就加 `IN`。
        0007 还没应用时回滚 + 一次 warning，回空字典 —— 与 store 旧的 `_leave_ids` /
        `_leave_days` 同一条降级口径。

        `employee_id IN (...)` 写在最前：0007 的 `idx_scheduling_overrides_employee`
        前导列就是 `employee_id`（票 07 复核 F7 的结论），别写成先按 `business_date`
        筛的形态。日期区间仍由索引的第二列兜住。
        """
        ids = _normalize_ids(employee_ids)
        if not ids:
            return {}
        marks = ", ".join("?" for _ in ids)
        try:
            cur = await self._conn.execute(
                f"""SELECT employee_id, business_date FROM scheduling_overrides
                    WHERE employee_id IN ({marks}) AND business_date >= ?
                      AND business_date <= ? AND kind = ?""",
                (*ids, str(first), str(last), KIND_LEAVE),
            )
            rows = await cur.fetchall()
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            logger.warning(_LEAVE_WARNING, exc)
            return {}

        out: dict[int, set[str]] = {}
        for row in rows:
            mapping = dict(row)
            out.setdefault(int(mapping["employee_id"]), set()).add(
                str(mapping["business_date"])
            )
        return out
