#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试用的 PostgreSQL 探针。

SQLite 退场后（ADR 0089），测试里原先直接 `sqlite3.connect(settings.APP_DB_PATH)`
查结构/数据的写法没有对应物。这里提供最小的一组替代，让用例继续「查一下库」：

- ``table_columns(table)``  ← ``PRAGMA table_info(table)``
- ``list_tables()``         ← ``SELECT name FROM sqlite_master WHERE type='table'``
- ``list_indexes(table=None)`` ← 同上 ``type='index'``
- ``fetch_all`` / ``fetch_one``：只读查询，SQL 里照旧写 ``?`` 占位符

连接走项目的 DSN 解析（``db_core.backend.pg.dsn_from_env``），因此测试进程里一定
指向 conftest 钉死的 ``luyun_test``。每次调用新建短连接：这些探针是低频调用，
不值得为跨事件循环复用连接付出复杂度。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional, Sequence


def _to_dollar(sql: str) -> str:
    """``?`` → ``$1..$n``：测试里保持项目 SQL 的写法。"""
    out = []
    idx = 0
    for ch in sql:
        if ch == "?":
            idx += 1
            out.append(f"${idx}")
        else:
            out.append(ch)
    return "".join(out)


async def _fetch(sql: str, params: Sequence[Any]) -> list[tuple]:
    import asyncpg

    from db_core.backend.pg import dsn_from_env

    conn = await asyncpg.connect(dsn_from_env())
    try:
        rows = await conn.fetch(_to_dollar(sql), *params)
        return [tuple(row.values()) for row in rows]
    finally:
        await conn.close()


def fetch_all(sql: str, params: Sequence[Any] = ()) -> list[tuple]:
    """同步只读查询（内部自建事件循环）。"""
    return asyncio.run(_fetch(sql, params))


async def fetch_all_async(sql: str, params: Sequence[Any] = ()) -> list[tuple]:
    """给已经在事件循环里的用例用。"""
    return await _fetch(sql, params)


def fetch_one(sql: str, params: Sequence[Any] = ()) -> Optional[tuple]:
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params: Sequence[Any] = ()) -> Any:
    row = fetch_one(sql, params)
    return row[0] if row else None


def table_columns(table: str) -> list[str]:
    """表列名，按定义顺序——替代 ``PRAGMA table_info``。"""
    rows = fetch_all(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = ? "
        "ORDER BY ordinal_position",
        (table,),
    )
    return [row[0] for row in rows]


def list_tables() -> list[str]:
    rows = fetch_all(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    )
    return [row[0] for row in rows]


def list_indexes(table: Optional[str] = None) -> list[str]:
    if table:
        rows = fetch_all(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = ? ORDER BY indexname",
            (table,),
        )
    else:
        rows = fetch_all(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' ORDER BY indexname"
        )
    return [row[0] for row in rows]
