#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作区名单的**只读入口**（公共层）。

「有哪些区」是店里共享的一份名单：排班拿它给每个人配固定区，卫生拿它做当天的分工。
表的 DDL 是卫生那条线建的（`hygiene_zones`，`migrations/pg/0001_initial_schema.sql`），
但读这张表的那段代码放在公共层 —— 排班从这里取名单，不去 import 卫生的任何东西
（`spec.md` 的「分层」：两边各读各的，谁都不认识对方的业务）。

这里出 `id` / `name`，以及 `day_shift` / `night_shift` 两个开关（「这个区跑不跑
白班 / 夜班」）。排班做「要顶的档」需要知道这个区跑不跑这一档：馅档只跑夜市，把
一个人配到「馅档 × 早班」时那一行不该出现（见 `.scratch/week-cover/spec.md`）。

两个开关的**语义由卫生定义**（卫生自己读，`HygieneWork.list_zones`）：排班不解释、
也不另立一套「这个区跑哪几档」的说法，只是把这两个开关**原样下发**给前端，当前端判断
「这个区跑不跑这一档」的判据。对应关系就是排班班次的 `duty_slot` 那两个词：`day` ↔
`day_shift`、`night` ↔ `night_shift`（`admin-web/src/utils/weekCoverSlots.js` 的
`ZONE_SLOT_FLAG`）。排班自己的班次是数据（`staff_shifts`），跟卫生的「白 / 夜」两个字
不是一回事；开关本身不搬进排班，也不在排班这边重命名。
"""

from __future__ import annotations

from typing import Any

__all__ = ["ZoneDirectory"]


class ZoneDirectory:
    """工作区名单（只读）。

    构造抄 `EmployeeAccounts`：接受 DatabaseManager 或已经打开的连接。写锁不在这里
    要 —— 这个类没有写操作。
    """

    def __init__(self, conn_or_db: Any):
        self._conn = getattr(conn_or_db, "_conn", conn_or_db)

    async def list_zones(self) -> list[dict]:
        """全部工作区，按 id（= 建区的先后），带两个班次开关（0 / 1）。"""
        cur = await self._conn.execute(
            """SELECT id, name, day_shift, night_shift
               FROM hygiene_zones ORDER BY id ASC"""
        )
        rows = await cur.fetchall()
        return [
            {
                "id": int(dict(row)["id"]),
                "name": dict(row)["name"],
                # 列是 BIGINT NOT NULL DEFAULT 1；`or 0` 跟卫生侧的归一化同一条口径，
                # 人工 SQL 写进来的 NULL 不会被读成「跑这一档」。
                "day_shift": int(dict(row)["day_shift"] or 0),
                "night_shift": int(dict(row)["night_shift"] or 0),
            }
            for row in rows
        ]
