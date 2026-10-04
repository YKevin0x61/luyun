#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生侧的员工身份扩展：当日分工（班次与工作区）。

身份本身在公共层 `services.identity.accounts`；这里只放卫生业务，并把同一个类名
继续暴露给卫生端既有的调用方。

票 10 起「今天在哪个班、哪个区」由**排班结果**决定（经公共层只读入口 `DutyRoster`，
卫生不 import 排班模块）：员工端不再有自选的入口，管理员改派也落到排班的单日覆盖上。
卫生内部只认「白班 / 夜班」两档，档位取自排班班次行上的 `duty_slot`（按班次 id 认，
不按名字）。`hygiene_shift_picks` 那张表从票 10 起不再被读写，历史数据留着。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from services.business_day import business_date_of
from services.identity.accounts import (  # noqa: F401  转发给卫生端既有调用方
    ALLOWED_PERMISSIONS,
    FORBIDDEN_SUPER_PERMISSION,
    LAST_SEEN_REFRESH_SECONDS,
    MAX_NAME_LENGTH,
    PERMISSION_ADMIN,
    PERMISSION_STAFF,
    EmployeeAccountsError,
    EmployeeAccounts as _EmployeeIdentity,
    hash_session_id,
    normalize_phone,
    serialized_write,
)
from services.identity.duty import DutyRoster

logger = logging.getLogger(__name__)


def hygiene_business_date(now: datetime) -> str:
    """营业日 YYYY-MM-DD，06:00 切（与 POS 同一规则）——卫生端的名字。"""
    return business_date_of(now)


# 卫生自己的班次：员工当天能交哪份日常检查由它决定。排班系统有它自己的一套。
SHIFT_DAY = "白班"
SHIFT_NIGHT = "夜班"
ALLOWED_SHIFTS = frozenset({SHIFT_DAY, SHIFT_NIGHT})


def _slot_shift(slot: Optional[str]) -> Optional[str]:
    """卫生档位 → 卫生认的班次名（`day` → 白班、`night` → 夜班，其余 None）。"""
    if slot == "day":
        return SHIFT_DAY
    if slot == "night":
        return SHIFT_NIGHT
    return None


def _duty_shift(duty: dict) -> Optional[str]:
    """排班给的那一行 → 卫生认的班次名（票 10）。

    卫生内部只认「白班 / 夜班」两个值（日常检查就分这两档），而排班的班次是**数据**：
    店长能改名、能加第三个（票 11）。所以认的是班次行上的 `duty_slot`（`0010` 那一列），
    **不是名字** —— 把「夜班」改成「晚班」，这一档仍然是夜班档。

    返回 `None` = 今天没有日常可交，四种情况在这一层收敛成同一个答案：没排到他 /
    那天休 / 那条班次没标档位 / 排班表还没建好（`available=False`）。员工端那句话一样
    （「今天没有要交的日常」），分开说只会让下游多四个分支。
    """
    if not duty or not duty.get("scheduled"):
        return None
    return _slot_shift(duty.get("duty_slot"))


class HygieneEmployeeAccounts(_EmployeeIdentity):
    """身份 + 卫生的当日分工（班次 / 工作区）。"""

    def _business_date(self) -> str:
        return hygiene_business_date(self._now_dt())

    def _assignment(
        self,
        employee_id: int,
        business_date: str,
        shift: Optional[str],
        zone_id: Optional[int] = None,
        zone_name: Optional[str] = None,
        zone_shifts=None,
    ) -> dict:
        return {
            "employee_id": int(employee_id),
            "business_date": business_date,
            "shift": shift,
            "zone_id": None if zone_id is None else int(zone_id),
            "zone_name": zone_name,
            "zone_shifts": list(zone_shifts or []),
        }

    def _normalize_shift(self, shift: str) -> str:
        value = (shift or "").strip()
        if value not in ALLOWED_SHIFTS:
            raise EmployeeAccountsError("invalid_shift", "invalid_shift")
        return value

    async def current_shift(self, employee_id: int) -> Optional[str]:
        assignment = await self.current_assignment(employee_id)
        return assignment["shift"]

    async def current_assignment(self, employee_id: int) -> dict:
        """今天的班次与工作区 —— 由**排班结果**决定（票 10）。

        票 10 之前这里是读 `hygiene_shift_picks`（员工当天自己在手机上选的）。现在上游
        是排班：店长配的轮转规则 + 单日覆盖说了算，员工端不再有自选的入口。读的就是
        `staff_assignments` 那一行（经公共层的只读入口 `DutyRoster`，卫生不 import
        排班模块），所以店长把某天的区改掉之后，这边当天立刻跟着变，不用谁去同步。

        没有班次时连工作区也不给：日常检查是「班次 × 区」两个一起筛的，留一个没有班次的
        区只会让下游多一种要判的组合。
        """
        duty = await DutyRoster(self._write_lock_owner).duty_for(
            int(employee_id), self._business_date()
        )
        shift = _duty_shift(duty)
        if shift is None:
            return self._assignment(employee_id, self._business_date(), None)
        zone_id = duty.get("zone_id")
        zone_name = duty.get("zone_name")
        if zone_id is not None and zone_name is None:
            # 那个区被删了：排班那张表的 `zone_id` 没有外键，也不追着卫生的删区动作改历史
            # （CONTEXT.md 的「固定工作区」那条），所以这里按**没配区**渲染 —— 跟排班页
            # 同一个口径。不回一个已经不存在、页面又翻译不出名字的 id。
            zone_id = None
        return self._assignment(
            employee_id,
            self._business_date(),
            shift,
            zone_id,
            zone_name,
            await self._zone_shifts_for(zone_id),
        )

    async def _zone_shifts_for(self, zone_id: Optional[int]) -> list[str]:
        """这个工作区开着哪几档日常（页面照它判「排班给的班次跟这个区对不上」）。

        区名单只有一份（卫生这张表），排班那边只记 `zone_id` —— 所以这一步在卫生这边做，
        不要求排班认识「白班档 / 夜班档」是怎么开的。区被删掉时给空列表：页面对空列表
        是容忍的（不判「对不上」），不会因为一个删了的区把人挡在门外。
        """
        if zone_id is None:
            return []
        zone = await self._fetch_zone(int(zone_id))
        return [] if zone is None else self._zone_shift_list(zone)

    async def _fetch_zone(self, zone_id: int):
        cur = await self._conn.execute(
            """SELECT id, name, day_shift, night_shift
               FROM hygiene_zones WHERE id = ?""",
            (int(zone_id),),
        )
        return await cur.fetchone()

    @staticmethod
    def _zone_allows_shift(zone, shift: str) -> bool:
        mapping = dict(zone)
        flag = mapping.get("day_shift") if shift == SHIFT_DAY else mapping.get("night_shift")
        return bool(int(flag or 0))

    @staticmethod
    def _zone_shift_list(zone) -> list[str]:
        mapping = dict(zone)
        shifts = []
        if int(mapping.get("day_shift", 1) or 0):
            shifts.append(SHIFT_DAY)
        if int(mapping.get("night_shift", 1) or 0):
            shifts.append(SHIFT_NIGHT)
        return shifts

    async def pick_assignment(self, employee_id: int, shift: str, zone_id: int) -> dict:
        """**票 10 起不再可用**：今天在哪个班、哪个区由排班说了算，员工不自己选。

        留着这个方法（而不是删掉）是为了让「谁还在调它」变成一句明确的错，而不是
        `AttributeError` 或者静默失效。写入口已经没了 —— 要改某人今天在哪，去排班页
        （店长）改那一天，或者让管理员在台账上改派。
        """
        raise EmployeeAccountsError("assignment_from_schedule", "assignment_from_schedule")

    async def pick_shift(self, employee_id: int, shift: str) -> dict:
        """**票 10 起不再可用**：同上，班次由排班决定。"""
        raise EmployeeAccountsError("shift_from_schedule", "shift_from_schedule")

    async def super_set_shift(self, employee_id: int, shift: str) -> dict:
        """**票 10 起不再从卫生这一侧改派**：改某人今天在哪 = 改排班那一天。

        卫生不 import 排班（DESIGN 决定 3：两边代码不互相 import，「谁是上游」是数据上的
        事实），所以这里不再自己写一张表。管理员的「改派」落到**排班的单日覆盖**上，
        由前端直接调 `/api/scheduling/overrides/{employee_id}/{date}`（同一个管理员会话，
        两扇门都在）。这一层留一句明确的错，别让调用方以为改成功了。
        """
        raise EmployeeAccountsError("assignment_from_schedule", "assignment_from_schedule")

    async def super_set_assignment(self, employee_id: int, shift: str, zone_id: int) -> dict:
        """**票 10 起不再从卫生这一侧改派**：同上，写的是排班的单日覆盖。"""
        raise EmployeeAccountsError("assignment_from_schedule", "assignment_from_schedule")

    async def _roster_extras(self, employees: list[dict]) -> None:
        """花名册每行补上今天的班次和工作区（一次查询，不按人循环）。

        票 10 起读的是**排班结果**（经公共层的批量只读入口 `duty_map`），跟员工自己
        那一份同源：台账上写「他今天在白班 · 案板」，就该是员工手机上看到的那个答案。
        没排到、那天休、或者班次没标档位的人，两项都空。
        """
        if not employees:
            return
        duty_map = await DutyRoster(self._write_lock_owner).duty_map(self._business_date())
        for employee in employees:
            duty = duty_map.get(int(employee["id"])) or {}
            shift = _duty_shift(duty)
            zone_id = duty.get("zone_id")
            zone_name = duty.get("zone_name")
            if zone_id is not None and zone_name is None:
                # 区被删了：按「没配区」渲染（口径同 `current_assignment`）。
                zone_id = None
            employee["shift"] = shift
            employee["zone_id"] = zone_id if shift else None
            employee["zone_name"] = zone_name if shift else None

    async def _session_extras(self, employee: dict) -> dict:
        return await self.current_assignment(employee["id"])


# 卫生端既有调用方（api/hygiene.py、main.py、services/hygiene/*、21 个测试）用的还是这个名字。
EmployeeAccounts = HygieneEmployeeAccounts

__all__ = [
    "ALLOWED_PERMISSIONS",
    "ALLOWED_SHIFTS",
    "FORBIDDEN_SUPER_PERMISSION",
    "LAST_SEEN_REFRESH_SECONDS",
    "MAX_NAME_LENGTH",
    "PERMISSION_ADMIN",
    "PERMISSION_STAFF",
    "EmployeeAccounts",
    "EmployeeAccountsError",
    "HygieneEmployeeAccounts",
    "SHIFT_DAY",
    "SHIFT_NIGHT",
    "hash_session_id",
    "hygiene_business_date",
    "normalize_phone",
    "serialized_write",
]
