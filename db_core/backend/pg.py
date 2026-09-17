#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PostgreSQL 后端：在 asyncpg 之上模拟既有 aiosqlite 接口。

现有 db_core 代码全部按 aiosqlite 的形态书写：

    async with tdb.conn.cursor() as cursor:      # cursor() 直接当上下文管理器
        await cursor.execute(sql, params)
        rows = await cursor.fetchall()

    cursor = await self._conn.cursor()           # 同一 API 也有 await 用法

两种写法在仓库里同时存在，所以本模块的 ``cursor()`` 必须**既能 await 又能
async with``，且 :class:`PgCursor` 要提供 ``lastrowid``（PG 侧靠自动追加
``RETURNING id`` 实现）。

SQL 文本在进入 asyncpg 前统一过 :func:`db_core.backend.dialect.translate`，
因此调用方仍旧写 SQLite 方言。
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, Iterable, List, Optional, Sequence

import asyncpg

from db_core.backend.dialect import translate

logger = logging.getLogger(__name__)

DEFAULT_DSN = "postgresql://localhost:5432/luyun"


def dsn_from_env() -> str:
    """PG 连接串：环境变量 ``LUYUN_POSTGRES_DSN`` 优先，其次 ``settings.POSTGRES_DSN``。"""
    override = os.environ.get("LUYUN_POSTGRES_DSN")
    if override:
        return override
    from config import settings

    return getattr(settings, "POSTGRES_DSN", "") or DEFAULT_DSN


def _normalize_value(value):
    """把 PG 侧类型收敛到 SQLite 等价形态。

    ``SUM(bigint)`` 在 PG 上返回 numeric，asyncpg 给的是 Decimal，而 SQLite 返回
    int/float——不收敛的话 JSON 里会出现字符串数字（"101" 而不是 101）。
    """
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


class PgRow:
    """同时支持 ``row["col"]`` 与 ``row[0]`` 的行对象。

    现有代码三种取向都有：``dict(row)``、``row[0]``（reports 里的 COUNT）、
    ``for a, b, c in rows`` 解包。asyncpg 的 Record 不支持整数下标，所以这里
    统一包一层。``keys()`` 的存在让 ``dict(row)`` 走映射协议，而 ``__iter__``
    仍按值迭代以支持解包。
    """

    __slots__ = ("_cols", "_values", "_map")

    def __init__(self, record):
        self._cols = list(record.keys())
        self._values = [_normalize_value(v) for v in record.values()]
        self._map = dict(zip(self._cols, self._values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._map[key]

    def __contains__(self, key):
        return key in self._map

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._cols)

    def keys(self):
        return list(self._cols)

    def values(self):
        return list(self._values)

    def items(self):
        return list(zip(self._cols, self._values))

    def get(self, key, default=None):
        return self._map.get(key, default)

    def __repr__(self):
        return f"PgRow({self._map!r})"


class PgCursor:
    """模拟 aiosqlite.Cursor 的最小接口。"""

    def __init__(self, connection: "PgConnection"):
        self._connection = connection
        self._rows: List[PgRow] = []
        self._row: Optional[PgRow] = None
        self._index = 0
        self.lastrowid: Optional[int] = None
        self.rowcount = -1

    # -- 兼容两种调用姿势 ------------------------------------------------
    def __await__(self):
        async def _self() -> "PgCursor":
            return self
        return _self().__await__()

    async def __aenter__(self) -> "PgCursor":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def close(self) -> None:
        return None

    # -- 执行 ------------------------------------------------------------
    async def execute(self, sql: str, params: Sequence[Any] = ()) -> "PgCursor":
        # 同一个 cursor 会被连续 execute 多次（如 reports.aggregate_dashboard_extras
        # 连查三条 COUNT），游标位置必须重置，否则第二次 fetchone() 会返回 None。
        self._index = 0
        translated = translate(sql)
        raw = self._connection.raw
        self._connection.bump_query_count()

        is_insert = translated.lstrip()[:6].upper() == "INSERT"
        needs_returning = is_insert and "RETURNING" not in translated.upper()
        if needs_returning:
            # lastrowid 语义：PG 用 RETURNING 取回自增主键。没有 id 列的表
            # （如 sop_stations 以 slug 为主键）会报 UndefinedColumn，此时
            # 退回普通执行。
            try:
                row = await raw.fetchrow(
                    translated.rstrip().rstrip(";") + " RETURNING id", *params
                )
            except asyncpg.UndefinedColumnError:
                await raw.execute(translated, *params)
                self.rowcount = 0
                return self
            self.lastrowid = int(row["id"]) if row and row.get("id") is not None else None
            self.rowcount = 1 if row else 0
            return self

        if translated.lstrip()[:6].upper() == "SELECT":
            self._rows = [PgRow(r) for r in await raw.fetch(translated, *params)]
            self.rowcount = len(self._rows)
            return self

        await raw.execute(translated, *params)
        self.rowcount = 0
        return self

    async def executescript(self, script: str) -> "PgCursor":
        await self._connection.raw.execute(script)
        return self

    # -- 取数 ------------------------------------------------------------
    async def fetchone(self) -> Optional[PgRow]:
        if self._rows:
            if self._index < len(self._rows):
                row = self._rows[self._index]
                self._index += 1
                return row
            return None
        return self._row

    async def fetchall(self) -> List[PgRow]:
        if self._rows:
            remaining = self._rows[self._index:]
            self._index = len(self._rows)
            return remaining
        return []

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self) -> PgRow:
        if self._index >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._index]
        self._index += 1
        return row


class PgConnection:
    """模拟 aiosqlite.Connection 的最小接口（autocommit 语义）。"""

    def __init__(self, raw: asyncpg.Connection):
        self._raw = raw
        self.stats_queries = 0

    @property
    def raw(self) -> asyncpg.Connection:
        return self._raw

    def bump_query_count(self) -> None:
        self.stats_queries += 1

    # -- 兼容两种调用姿势 ------------------------------------------------
    def cursor(self) -> PgCursor:
        return PgCursor(self)

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> PgCursor:
        cur = PgCursor(self)
        await cur.execute(sql, params)
        return cur

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        translated = translate(sql)
        await self._raw.executemany(translated, [tuple(p) for p in seq])

    async def commit(self) -> None:
        # asyncpg 默认 autocommit；显式事务支持见模块文档的已知限制。
        return None

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        try:
            await self._raw.close()
        except Exception:  # 已关闭 / 连接丢失
            logger.debug("PG 连接关闭时忽略异常", exc_info=True)

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, _value) -> None:
        # aiosqlite 用它切换 Row 类型；asyncpg 恒返回 Record（可 dict()）
        return None


async def connect(dsn: Optional[str] = None) -> PgConnection:
    """建立一条 PG 连接并包成 aiosqlite 形态。"""
    target = dsn or dsn_from_env()
    raw = await asyncpg.connect(target)
    logger.info("🐘 已连接 PostgreSQL: %s", target.rsplit("@", 1)[-1])
    return PgConnection(raw)
