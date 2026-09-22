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

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Sequence

import asyncpg

from db_core.backend.dialect import translate

logger = logging.getLogger(__name__)

DEFAULT_DSN = "postgresql://localhost:5432/luyun"

_INSERT_TABLE_RE = re.compile(
    r"INSERT\s+INTO\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE
)
_INSERT_RE = re.compile(r"^\s*INSERT\b", re.IGNORECASE)
# CTE 也是只读查询的形态（`WITH x AS (...) SELECT ...`）。只认 `^SELECT` 会把整条
# CTE 判成 UPDATE/DDL、走 raw.execute——那只拿得到 command tag，结果集被丢掉，
# fetchall() 恒为空且不报错。卫生端的员工端日常清单（services/hygiene/work.py 的
# list_daily_work）正是这种写法：PG 后端下它永远返回空，员工端一项都看不到。
_SELECT_RE = re.compile(r"^\s*(?:SELECT|WITH)\b", re.IGNORECASE)
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


_TABLE_RE = re.compile(
    r"\b(?:FROM|UPDATE|INTO|JOIN)\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE
)
# 行标识列：有 id 列就是 id，否则取主键第一列（sessions→session_id、
# api_tokens→token_hash、sop_stations→slug...）。
_PRIMARY_KEY_SQL = """
    SELECT a.attname
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY (i.indkey)
    WHERE i.indisprimary
      AND c.relname = $1
      AND n.nspname = ANY (current_schemas(false))
    ORDER BY array_position(i.indkey, a.attnum)
    LIMIT 1
"""

def first_table_name(sql: str) -> Optional[str]:
    """取 SQL 里第一个表名（admin 的通用表格语句都是单表）。"""
    match = _TABLE_RE.search(sql)
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
    async def _translate(self, sql: str) -> str:
        """翻译方言；含 rowid 时按目标表的行标识列解析。"""
        if "rowid" not in sql.lower():
            return translate(sql)
        table = first_table_name(sql)
        column = await self._connection.row_key_column(table) if table else "id"
        return translate(sql, rowid_column=column)

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
        # 整条语句在串行锁内执行：方言解析也要读 raw（rowid → 行标识列），
        # 外层不加锁的话同一条连接仍会被并发使用。
        async with self._connection.guard():
            try:
                translated = await self._translate(sql)
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
            except asyncpg.PostgresError:
                # 服务端报错会让事务进入 aborted 并占住串行锁，立刻回滚释放。
                await self._connection.discard_aborted_transaction()
                raise

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
        async with self._connection.guard():
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

    @property
    def description(self):
        """DB-API 风格的列信息。

        admin 的表格列表用 ``[d[0] for d in cursor.description]`` 取列名；asyncpg
        的 Record 没有这个接口，这里从 keys 重建。空结果集返回 None（没有行要
        渲染，与 aiosqlite 的行为差异不影响调用方）。
        """
        if not self._rows:
            return None
        return [
            (name, None, None, None, None, None, None)
            for name in self._rows[0].keys()
        ]

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self) -> PgRow:
        if self._index >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._index]
        self._index += 1
        return row


class _TaskGuard:
    """按 asyncio 任务可重入的串行锁。

    asyncpg 的**单条连接不允许并发操作**（``InterfaceError: another operation
    is in progress``），而调用方——FastAPI 请求与后台调度器——天然并发；SQLite
    后端由 aiosqlite 在内部串行化，所以这个差异只有 PG 会暴露。

    可重入是必需的：一条写路径会嵌套获取同一把锁（``PgCursor.execute`` →
    ``ensure_transaction`` → ``PgConnection.execute``）。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: Optional[asyncio.Task] = None
        self._depth = 0

    async def acquire(self) -> bool:
        """取得锁；返回值表示是否强制接管了「已结束的持有者」留下的锁。"""
        task = asyncio.current_task()
        if self._owner is task:
            self._depth += 1
            return False
        stole = False
        # 持有者已结束却没归还（写路径异常退出）：强制接管，否则后续所有
        # 数据库操作都会永久卡在这把锁上。
        if self._owner is not None and self._owner.done():
            logger.warning("PG 连接锁的持有者已结束但未释放，强制接管")
            self._reset()
            stole = True
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        return stole

    def release(self) -> None:
        if self._depth <= 0:
            return
        self._depth -= 1
        if self._depth == 0:
            self._reset()

    @property
    def depth(self) -> int:
        return self._depth

    def force_release(self) -> None:
        """连接关闭时的兜底：未归还的锁不能拖死后续操作。"""
        if self._depth > 0:
            logger.warning("PG 连接关闭时强制释放未归还的连接锁 (depth=%d)", self._depth)
        self._reset()

    def _reset(self) -> None:
        self._owner = None
        self._depth = 0
        if self._lock.locked():
            self._lock.release()


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
        # 单连接的串行锁：asyncpg 不允许一条连接并发操作，见 _TaskGuard。
        self._guard = _TaskGuard()
        # 事务期间是否由本连接持有锁（开始事务时取得，commit/rollback 时归还）
        self._tx_guard_held = False
        # 表名 → 是否有 id 列。有 id 才能用 RETURNING id 支撑 lastrowid；
        # 探测结果缓存起来，避免每条 INSERT 都试错。
        self._table_has_id: Dict[str, bool] = {}
        # 表名 → 行标识列（rowid 的 PG 等价物）
        self._row_keys: Dict[str, str] = {}
        self.stats_queries = 0

    async def row_key_column(self, table: str) -> str:
        """该表的「行标识列」——SQLite ``rowid`` 的 PG 等价物。

        SQLite 的 rowid 对任何表都存在，PG 只有显式列，所以按「有 id 用 id，
        否则用主键第一列」解析。结果缓存，避免每条 SQL 都查一次目录。
        """
        if table in self._row_keys:
            return self._row_keys[table]
        column = "id"
        try:
            row = await self._raw.fetchrow(_PRIMARY_KEY_SQL, table)
            if row:
                column = row["attname"]
        except Exception:
            logger.debug("解析行标识列失败，回退 id: %s", table, exc_info=True)
        self._row_keys[table] = column
        return column

    @property
    def raw(self) -> asyncpg.Connection:
        return self._raw

    def bump_query_count(self) -> None:
        self.stats_queries += 1

    def in_transaction(self) -> bool:
        return self._tx is not None

    async def ensure_transaction(self) -> None:
        """写操作前调用：没有活动事务就开一个。

        事务期间**一直持有串行锁**，直到 commit/rollback：否则其他任务的语句会
        落进别人的事务，跟着一起被提交或一起被回滚（静默丢数据）。
        """
        if self._tx is not None:
            return
        await self._guard.acquire()
        self._tx_guard_held = True
        self._tx = self._raw.transaction()
        try:
            await self._tx.start()
        except Exception:
            self._tx = None
            self._release_tx_guard()
            raise

    def _release_tx_guard(self) -> None:
        if self._tx_guard_held:
            self._tx_guard_held = False
            self._guard.release()

    async def discard_aborted_transaction(self) -> None:
        """语句报错后丢弃已 aborted 的事务，并把事务持有的锁还回去。

        PG 的事务一旦有语句报错就进入 aborted：除 ROLLBACK 外任何语句都失败。
        此时若不回滚，aborted 事务会一直占着串行锁——其他任务全部被挡在锁外
        （等于全站卡死），持有者自己的后续语句也只会不断报
        ``InFailedSQLTransactionError``。所以这里立刻回滚，异常仍照原样抛给调用方。
        """
        if self._tx is None:
            return
        logger.warning("PG 事务内语句报错，已回滚该事务以避免 aborted 状态占住连接")
        try:
            await self.rollback()
        except Exception:
            logger.debug("回滚 aborted 事务失败", exc_info=True)
            self._tx = None
            self._release_tx_guard()

    async def _discard_stale_transaction(self) -> None:
        """清理上一个（已结束的）任务留下的悬挂事务。

        锁被强制接管时，连接上可能还挂着别人的事务；不回滚的话，当前任务会误以为
        自己已在事务里，语句会落进那个陈旧事务。这里**不动当前任务的锁**。
        """
        if self._tx is None:
            return
        logger.warning("接管陈旧连接锁时发现悬挂事务，已回滚")
        tx, self._tx = self._tx, None
        self._tx_guard_held = False  # 陈旧标记清零，不能把当前任务的锁带走
        try:
            await tx.rollback()
        except Exception:
            logger.debug("回滚悬挂事务失败", exc_info=True)

    @classmethod
    def is_write_sql(cls, translated: str) -> bool:
        return translated.lstrip()[:8].upper().startswith(cls._WRITE_PREFIXES)

    def table_has_id(self, table: str) -> Optional[bool]:
        """该表是否有 ``id`` 列；``None`` 表示尚未探测过。"""
        return self._table_has_id.get(table)

    def remember_table_has_id(self, table: str, has_id: bool) -> None:
        self._table_has_id[table] = has_id

    # -- 并发串行化 ------------------------------------------------------
    @asynccontextmanager
    async def guard(self) -> AsyncIterator[None]:
        """把一次连接操作放进串行锁内；同一任务内嵌套获取是安全的。"""
        stole = await self._guard.acquire()
        if stole:
            await self._discard_stale_transaction()
        try:
            yield
        finally:
            self._guard.release()

    # -- 兼容两种调用姿势 ------------------------------------------------
    def cursor(self) -> PgCursor:
        return PgCursor(self)

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> PgCursor:
        cur = PgCursor(self)
        await cur.execute(sql, params)
        return cur

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        translated = translate(sql)
        async with self.guard():
            if self.is_write_sql(translated):
                await self.ensure_transaction()
            await self._raw.executemany(translated, [tuple(p) for p in seq])

    async def commit(self) -> None:
        if self._tx is None:
            return
        tx, self._tx = self._tx, None
        try:
            await tx.commit()
        finally:
            self._release_tx_guard()

    async def rollback(self) -> None:
        if self._tx is None:
            return
        tx, self._tx = self._tx, None
        try:
            await tx.rollback()
        finally:
            self._release_tx_guard()

    async def close(self) -> None:
        if self._tx is not None:
            # 未提交的事务不能静默丢弃：回滚并留线索，避免「以为写进去了」。
            logger.warning("PG 连接关闭时存在未提交事务，已回滚")
            try:
                await self.rollback()
            except Exception:
                logger.debug("关闭前回滚失败", exc_info=True)
        self._guard.force_release()
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
    """建立一条 PG 连接并包成 aiosqlite 形态。

    ``LUYUN_PG_STATEMENT_TIMEOUT_MS`` 设置时给这条连接加 statement_timeout：
    测试用它把「等锁等成挂起」变成「超时失败」（测试库上用例之间共享连接，
    一个没提交的事务就能让下一条语句永久等待）。
    """
    target = dsn or dsn_from_env()
    connect_kwargs: dict = {}
    timeout_ms = os.environ.get("LUYUN_PG_STATEMENT_TIMEOUT_MS")
    if timeout_ms:
        connect_kwargs["server_settings"] = {"statement_timeout": str(int(timeout_ms))}
    raw = await asyncpg.connect(target, **connect_kwargs)
    logger.info("🐘 已连接 PostgreSQL: %s", target.rsplit("@", 1)[-1])
    return PgConnection(raw)
