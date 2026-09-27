#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「今天谁在哪」的**只读入口**（公共层）。

排班把「谁在哪天、上什么班、在哪个区」物化进 `staff_assignments`；卫生据此决定员工今天
能交哪份日常检查。那张表的 DDL 是排班那条线建的（`migrations/pg/0005_scheduling.sql`），
但读它的代码放在公共层 —— 卫生从这里取当天的班次与责任区，**不去 import 排班的任何东西**
（`spec.md` 的「分层」：排班 → 卫生 是数据上的上下游，不是调用关系）。

**班次给的是 id 与名字**（`staff_shifts.name`），不是卫生写死的「白班 / 夜班」：排班的班次
是数据（票 11 能改名、能加第三个），按名字当外键会静默错配。卫生认不认一个新班次，是它自己
那份名单（`hygiene_zones.day_shift / night_shift` 与逾期钟点）的事。

读的是**当下的结果行**，所以店长把某天的区改掉之后这里立刻跟着变（票 10 的验收项：
卫生那边不用手工同步）。
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

__all__ = ["DutyRoster"]

logger = logging.getLogger(__name__)


def _blank_duty(available: bool) -> dict:
    return {
        "available": available,
        "scheduled": False,
        "shift_id": None,
        "shift_name": None,
        "zone_id": None,
        "zone_name": None,
        "duty_slot": None,
        "source": None,
    }


class DutyRoster:
    """当天的班次与责任区（只读）。构造抄 `ZoneDirectory`：接受 DatabaseManager 或连接。"""

    def __init__(self, conn_or_db: Any):
        self._conn = getattr(conn_or_db, "_conn", conn_or_db)

    async def duty_for(self, employee_id: int, business_date: str) -> dict:
        """某人某天排班给的那一行。

        三种「今天没有班」分得开，调用方不用猜：

        - `available=False`：排班的表还没建（0005 没应用）—— 是「还没接上」，不是「今天休」。
          这一支顺带把失败的事务回滚掉（否则这条连接后面的查询全报 aborted），所以
          **只在读路径上用它**（卫生那边是「今天能交哪些日常检查」）。
        - `scheduled=False`：那天没排到他（新人还没配规则）。
        - `scheduled=True` 而 `shift_id=None`：那天休息。

        `shift_name` / `zone_name` 是左连接出来的：班次行被手工删掉（`staff_assignments.shift_id`
        没有外键）时给 `None`，不是报错 —— 员工页显示「班次已删」比 500 好。

        `duty_slot`（票 10）是这条班次挂在卫生的哪一档日常检查上（`day` / `night` / `None`）：
        卫生按**班次 id** 认档位，不按名字 —— 店长把「夜班」改成「晚班」不该让夜班的人
        当天交不了日常。这一列在 `staff_shifts` 上（`0010_shift_duty_slot.sql`），
        班次行不在时同样是 `None`（= 今天没有日常可交）。
        """
        try:
            cur = await self._conn.execute(
                """SELECT a.shift_id, s.name AS shift_name, s.duty_slot, a.zone_id,
                          z.name AS zone_name, a.source
                     FROM staff_assignments a
                     LEFT JOIN staff_shifts s ON s.id = a.shift_id
                     LEFT JOIN hygiene_zones z ON z.id = a.zone_id
                    WHERE a.employee_id = ? AND a.business_date = ?""",
                (int(employee_id), str(business_date)),
            )
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            logger.warning(
                "排班结果表读不了（0005/0006 未应用？），卫生按「今天没有排班」处理: %s", exc
            )
            return _blank_duty(available=False)
        row = await cur.fetchone()
        if row is None:
            return _blank_duty(available=True)
        return self._duty_from_row(dict(row))

    @staticmethod
    def _duty_from_row(mapping: dict) -> dict:
        """结果行 → 调用方拿到的那份（`duty_for` 与 `duty_map` 共用一份列名解释）。"""
        shift_id = mapping["shift_id"]
        zone_id = mapping["zone_id"]
        return {
            "available": True,
            "scheduled": True,
            "shift_id": None if shift_id is None else int(shift_id),
            "shift_name": mapping["shift_name"],
            "zone_id": None if zone_id is None else int(zone_id),
            "zone_name": mapping["zone_name"],
            "duty_slot": mapping.get("duty_slot"),
            "source": mapping["source"],
        }

    async def duty_map(self, business_date: str) -> dict[int, dict]:
        """那一天**所有人**的班次与责任区（花名册台账那种「一次问一批」的读法）。

        返回 `{employee_id: duty}`，每一份的形状跟 `duty_for` 一样（`available` 恒为
        `True`：整批读不出来时返回**空字典**，调用方按「谁都没排班」渲染 —— 那时候
        逐个人说「还没接上」没有意义，页面上是一张空表）。
        """
        try:
            cur = await self._conn.execute(
                """SELECT a.employee_id, a.shift_id, s.name AS shift_name, s.duty_slot,
                          a.zone_id, z.name AS zone_name, a.source
                     FROM staff_assignments a
                     LEFT JOIN staff_shifts s ON s.id = a.shift_id
                     LEFT JOIN hygiene_zones z ON z.id = a.zone_id
                    WHERE a.business_date = ?""",
                (str(business_date),),
            )
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            logger.warning(
                "排班结果表读不了（0005/0010 未应用？），花名册按「谁都没排班」渲染: %s", exc
            )
            return {}
        out: dict[int, dict] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            out[int(mapping["employee_id"])] = self._duty_from_row(mapping)
        return out

    async def _rollback_quietly(self) -> None:
        """丢弃 aborted 的事务；失败不影响调用方（连接可能已经不可用）。"""
        try:
            await self._conn.rollback()
        except Exception:  # pragma: no cover - 连接已经坏了的话，没别的可做
            pass
