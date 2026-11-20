#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写口：进程内**唯一**同时持有写锁、并给事务收尾的地方（ADR 0152）。

两个出口，领域 service 按需要选：

* ``async with write_unit(holder):`` —— 一段编排要「一起成、一起不成」时用
  （离职 = 删排班 + 落日期；仪容判定 = 读状态 + 改状态）。块内 ``commit()`` 只表示
  「这一步写完了」，落库在出口；块内异常整块回滚。
* ``@serialized_write`` —— 单个写方法的原口径：取锁、提交归方法自己、异常回滚。
  在写单元里它**退化成被动**（不另取锁、不提前提交），所以把两个既有方法编排进一个
  单元不用改它们一行。

锁与事务的细节都在 :class:`db_core.database_connection.DatabaseConnection`；
这一层只做「拿谁当连接」的判断 —— 假连接（测试里的 AsyncMock）不认，按老口径走，
否则 ``getattr`` 会把任意属性都当成真的。
"""

from __future__ import annotations

import functools
from contextlib import asynccontextmanager
from typing import Any

from db_core.database_connection import DatabaseConnection


def _connection_of(holder: Any) -> Any:
    """服务对象 → 连接（``DatabaseManager`` 与服务都长这个样子）。"""
    return getattr(holder, "_conn", holder)


@asynccontextmanager
async def write_unit(holder: Any):
    """写单元：块内是一个事务，出口提交 / 异常回滚。"""
    conn = _connection_of(holder)
    if isinstance(conn, DatabaseConnection):
        async with conn.write():
            yield conn
    else:
        # 没接上库（测试假连接、探针）：没有事务可管，原样放行。
        yield conn


def serialized_write(method):
    """写方法串行化：同一时刻只有一个写方法在跑，异常不留半截事务。"""

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        conn = _connection_of(self)
        if isinstance(conn, DatabaseConnection):
            async with conn.write_scope():
                return await method(self, *args, **kwargs)
        # 老口径（假连接 / 还没接上库）：有锁就串行，异常回滚。
        lock = getattr(self, "_write_lock", None)
        if lock is None:
            return await method(self, *args, **kwargs)
        async with lock:
            try:
                return await method(self, *args, **kwargs)
            except Exception:
                if conn is not None and hasattr(conn, "rollback"):
                    await conn.rollback()
                raise

    return wrapper
