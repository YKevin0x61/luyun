#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作区名单的**只读入口**（公共层）。

「有哪些区」是店里共享的一份名单：排班拿它给每个人配固定区，卫生拿它做当天的分工。
表的 DDL 是卫生那条线建的（`hygiene_zones`，`migrations/pg/0001_initial_schema.sql`），
但读这张表的那段代码放在公共层 —— 排班从这里取名单，不去 import 卫生的任何东西
（`spec.md` 的「分层」：两边各读各的，谁都不认识对方的业务）。

这里只出 `id` / `name`：`day_shift` / `night_shift` 两个开关的含义是「这个区跑不跑
白班 / 夜班」，那是卫生那两段固定班次的说法，卫生自己读（`HygieneWork.list_zones`），
排班不关心 —— 排班的班次是数据（`staff_shifts`），跟卫生的「白 / 夜」两个字不是一回事。
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
        """全部工作区，按 id（= 建区的先后）。"""
        cur = await self._conn.execute(
            "SELECT id, name FROM hygiene_zones ORDER BY id ASC"
        )
        rows = await cur.fetchall()
        return [{"id": int(dict(row)["id"]), "name": dict(row)["name"]} for row in rows]
