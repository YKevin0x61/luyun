#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生测试的公用夹具：把「某人今天在哪个班、哪个区」**用排班造出来**（票 10）。

票 10 之前，卫生测试是这样造前置数据的：

    await accounts.pick_assignment(employee["id"], "白班", zone_id)

那调的是「员工当天自己选」。票 10 接上排班之后，员工当天的班次与责任区由
`staff_assignments` 决定（员工端不再有自选入口），所以前置数据要改成**配排班**：
给这个人一条固定班次的轮转规则 +（可选）一个固定责任区，展开出今天那一行。

几行 SQL 收在这一个模块里，免得九个测试文件各写一份、哪天排班的口径一动就要改九处。

用法：

    from tests.hygiene_duty import assign_duty

    zone = await self._zone("案板")
    await assign_duty(self.db, employee["id"], zone_id=zone)          # 白班档 + 案板
    await assign_duty(self.db, employee["id"], slot="night")          # 夜班档
    await assign_duty(self.db, employee["id"], slot=None)             # 没标档位 → 没有日常

**注意**：测试里的时钟是注入的，排班的「今天」跟卫生的营业日必须是同一个 —— 所以
`now` 要传测试那个固定时刻（一个 `datetime`，这个模块负责包成排班要的可调用时钟），
别让它落到真实时间上。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from services.scheduling.store import SchedulingStore

__all__ = ["assign_duty"]


async def assign_duty(
    db,
    employee_id: int,
    *,
    slot: Optional[str] = "day",
    zone_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> int:
    """让某个人从今天起固定上「白班档 / 夜班档」，并（可选）固定一个责任区。

    `slot` 是**卫生档位**（`staff_shifts.duty_slot`）：`'day'`、`'night'`，或者 `None`
    表示配一条没标档位的班次 —— 那种情况下员工今天**没有日常可交**（票 10 的口径：
    没说是哪一档就不替他决定），正是「今天没有要交的日常」那几条用例要的前置。

    `now` 是这个测试的固定时刻（`datetime`）；不给就用真实时间（排班自己那个时钟）。
    排班的窗口与营业日都由它决定，所以拨过钟的用例一定要传。

    返回那条班次的 id（要断言「哪一天在哪个班」的用例会用到）。班次表空的时候会先
    走一次 `prepare()`（它放默认的白班/夜班两条，测试库每个用例前都会 TRUNCATE）。
    """
    store = SchedulingStore(db, now=(lambda: now) if now is not None else None)
    await store.prepare()
    shift_id = None
    for shift in await store.list_shifts(include_inactive=True):
        if (shift.get("duty_slot") or None) == slot:
            shift_id = shift["id"]
            break
    if shift_id is None:
        # 这个档位还没有班次（`slot=None` 时总会走到这里）：建一条。
        created = await store.create_shift(f"{slot or 'dutyless'}-shift", None, slot)
        shift_id = created["id"]
    if zone_id is not None:
        await store.set_zone_default(int(employee_id), shift_id, int(zone_id))
    await store.set_rule(int(employee_id), [shift_id])
    return shift_id
