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
import re
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence

import asyncpg

from db_core.backend.dialect import translate

logger = logging.getLogger(__name__)

DEFAULT_DSN = "postgresql://localhost:5432/luyun"

_INSERT_TABLE_RE = re.compile(
    r"INSERT\s+INTO\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE
)
_INSERT_RE = re.compile(r"^\s*INSERT\b", re.IGNORECASE)
_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_ARG_INDEX_RE = re.compile(r"query argument \$(\d+)")


def coerce_numeric_args(exc: Exception, params: Sequence[Any]) -> Optional[list]:
    """把 asyncpg 明确要求整数、却收到数字字符串的参数收敛为 int。

    SQLite 会隐式把 ``'205139'`` 当整数比较，PG + asyncpg 严格拒绝。仓库里
    ``row_to_dict`` 把 ``id`` 转成了字符串（``_id = str(id)``），所以调用方
    传字符串 id 是常态。这里只在 asyncpg 真的报「需要整数」时才动参数——
    text 列不会触发这个错误，因此不会误伤 ``table_number='6'`` 这类值。
    """
    indexes = {int(m) for m in _ARG_INDEX_RE.findall(str(exc))}
    if not indexes:
        return None
    coerced = list(params)
    changed = False
    for index in indexes:
        position = index - 1
        if not (0 <= position < len(coerced)):
            continue
        value = coerced[position]
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            coerced[position] = int(value)
            changed = True
    return coerced if changed else None


def insert_target_table(translated: str) -> Optional[str]:
    """从 INSERT 语句里取出目标表名；解析不到返回 None。"""
    match = _INSERT_TABLE_RE.search(translated)
    return match.group(1) if match else None


def rowcount_from_status(status: str) -> int:
    """解析 asyncpg 的 command tag：'UPDATE 2' / 'DELETE 0' / 'INSERT 0 1'。"""
    parts = (status or "").split()
    if not parts:
        return -1
    if parts[0].upper() == "INSERT":
        return int(parts[-1]) if parts[-1].isdigit() else 0
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return -1


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
    async def _run(self, runner, translated: str, params: Sequence[Any]):
        """执行，并在 asyncpg 抱怨参数类型时按需收敛后重试。"""
        try:
            return await runner(translated, *params)
        except (TypeError, asyncpg.DataError) as exc:
            coerced = coerce_numeric_args(exc, params)
            if coerced is None:
                raise
            logger.debug("PG 参数类型收敛后重试: %s", translated[:60])
            return await runner(translated, *coerced)

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> "PgCursor":
        # 同一个 cursor 会被连续 execute 多次（如 reports.aggregate_dashboard_extras
        # 连查三条 COUNT），游标位置必须重置，否则第二次 fetchone() 会返回 None。
        self._index = 0
        self.lastrowid = None
        translated = translate(sql)
        raw = self._connection.raw
        self._connection.bump_query_count()

        # 写操作进入显式事务（对齐 aiosqlite 语义），commit/rollback 由调用方决定
        if self._connection.is_write_sql(translated):
            await self._connection.ensure_transaction()

        if _INSERT_RE.match(translated):
            return await self._execute_insert(raw, translated, params)

        if _SELECT_RE.match(translated):
            self._rows = [PgRow(r) for r in await self._run(raw.fetch, translated, params)]
            self.rowcount = len(self._rows)
            return self

        # UPDATE / DELETE / DDL：asyncpg 返回 command tag，需解析出真实行数
        status = await self._run(raw.execute, translated, params)
        self.rowcount = rowcount_from_status(status)
        return self

    async def _execute_insert(self, raw, translated: str, params) -> "PgCursor":
        """INSERT：按需追加 ``RETURNING id`` 以支撑 ``lastrowid``。

        不能在事务里靠「试错 + 回退」探测表结构——PG 的事务一旦有语句报错就进入
        aborted 状态，后续语句全部失败（InFailedSQLTransactionError）。所以先用
        缓存判断，未知表用 SAVEPOINT 隔离探测，结果记入缓存。
        """
        if "RETURNING" in translated.upper():
            self._rows = [PgRow(r) for r in await self._run(raw.fetch, translated, params)]
            self.rowcount = len(self._rows)
            return self

        table = insert_target_table(translated)
        known = self._connection.table_has_id(table) if table else False
        statement = translated.rstrip().rstrip(";")

        if known is False:
            status = await self._run(raw.execute, translated, params)
            self.rowcount = rowcount_from_status(status)
            return self

        if known is True:
            row = await self._run(raw.fetchrow, statement + " RETURNING id", params)
            self.lastrowid = int(row["id"]) if row and row.get("id") is not None else None
            self.rowcount = 1 if row else 0
            return self

        # 未知表：SAVEPOINT 隔离探测，失败只回滚到存档点，不拖垮整个事务
        await raw.execute("SAVEPOINT pg_probe_id")
        try:
            row = await self._run(raw.fetchrow, statement + " RETURNING id", params)
        except asyncpg.UndefinedColumnError:
            await raw.execute("ROLLBACK TO SAVEPOINT pg_probe_id")
            await raw.execute("RELEASE SAVEPOINT pg_probe_id")
            if table:
                self._connection.remember_table_has_id(table, False)
            status = await self._run(raw.execute, translated, params)
            self.rowcount = rowcount_from_status(status)
            return self
        await raw.execute("RELEASE SAVEPOINT pg_probe_id")
        if table:
            self._connection.remember_table_has_id(table, True)
        self.lastrowid = int(row["id"]) if row and row.get("id") is not None else None
        self.rowcount = 1 if row else 0
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
    """模拟 aiosqlite.Connection，含**显式事务语义**。

    aiosqlite 的写操作会隐式开启事务、需要 ``commit()`` 才落库；现有 repo 全部
    按这个模式书写（每个写路径后面都跟 commit）。asyncpg 默认 autocommit，直接
    照搬会让「先删后插」这类操作失去原子性——中途失败就留下半截数据。所以这里
    按 aiosqlite 的语义实现：首次写操作开启事务，commit/rollback 结束它。
    """

    _WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE")

    def __init__(self, raw: asyncpg.Connection):
        self._raw = raw
        self._tx = None
        # 表名 → 是否有 id 列。有 id 才能用 RETURNING id 支撑 lastrowid；
        # 探测结果缓存起来，避免每条 INSERT 都试错。
        self._table_has_id: Dict[str, bool] = {}
        self.stats_queries = 0

    @property
    def raw(self) -> asyncpg.Connection:
        return self._raw

    def bump_query_count(self) -> None:
        self.stats_queries += 1

    def in_transaction(self) -> bool:
        return self._tx is not None

    async def ensure_transaction(self) -> None:
        """写操作前调用：没有活动事务就开一个。"""
        if self._tx is None:
            self._tx = self._raw.transaction()
            await self._tx.start()

    @classmethod
    def is_write_sql(cls, translated: str) -> bool:
        return translated.lstrip()[:8].upper().startswith(cls._WRITE_PREFIXES)

    def table_has_id(self, table: str) -> Optional[bool]:
        """该表是否有 ``id`` 列；``None`` 表示尚未探测过。"""
        return self._table_has_id.get(table)

    def remember_table_has_id(self, table: str, has_id: bool) -> None:
        self._table_has_id[table] = has_id

    # -- 兼容两种调用姿势 ------------------------------------------------
    def cursor(self) -> PgCursor:
        return PgCursor(self)

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> PgCursor:
        cur = PgCursor(self)
        await cur.execute(sql, params)
        return cur

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        translated = translate(sql)
        if self.is_write_sql(translated):
            await self.ensure_transaction()
        await self._raw.executemany(translated, [tuple(p) for p in seq])

    async def commit(self) -> None:
        if self._tx is None:
            return
        tx, self._tx = self._tx, None
        await tx.commit()

    async def rollback(self) -> None:
        if self._tx is None:
            return
        tx, self._tx = self._tx, None
        await tx.rollback()

    async def close(self) -> None:
        if self._tx is not None:
            # 未提交的事务不能静默丢弃：回滚并留线索，避免「以为写进去了」。
            logger.warning("PG 连接关闭时存在未提交事务，已回滚")
            try:
                await self.rollback()
            except Exception:
                logger.debug("关闭前回滚失败", exc_info=True)
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
