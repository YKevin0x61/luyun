#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志持久化存储（PostgreSQL）
============================

- 日志表 `logs` 与业务表同库：表结构由 `migrations/pg/0004_logs.sql` 建立，
  不再有独立的 `data/logs.db`，也没有 WAL / `quick_check` / 损坏隔离 /
  forensics —— SQLite 已退场（ADR 0089），PG 不产生页损坏。
- 写入通过 `queue.Queue`，后台协程批量落库，避免阻塞事件循环
- 表结构：`logs(id, ts, ts_epoch, level, logger, message, exception)`
- 启动时按 `LOG_RETENTION_DAYS` 清理过期记录
"""

from __future__ import annotations

import asyncio
import logging
import queue
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from config import settings
from db_core.backend import pg as pg_backend
from db_core.backend.pg import PgConnection

logger = logging.getLogger(__name__)

CHINA_TZ = timezone(timedelta(hours=8))

# SQL 沿用 SQLite 方言的 `?` 占位符：PgConnection 在驱动边界经
# db_core/backend/dialect.py 重写成 `$n`（与 db_core 下所有 repo 同一约定）。
_INSERT_SQL = (
    "INSERT INTO logs (ts, ts_epoch, level, logger, message, exception) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

# 连接已断 / 建立不起来：这时「换一条连接」是唯一可能恢复的动作。
_CONNECTION_EXC_TYPES = (
    asyncpg.InterfaceError,  # 客户端侧：连接已关闭、同一条连接被并发使用
    asyncpg.PostgresConnectionError,  # 服务端侧：连接丢失
    asyncpg.TooManyConnectionsError,
    asyncpg.CannotConnectNowError,  # 服务端正在启动/关闭
    ConnectionError,
    OSError,  # DNS / 网络层，如 ConnectionRefusedError
)
_CONNECTION_MARKERS = (
    "connection was closed",
    "connection is closed",
    "not connected",
    "connection refused",
    "connection reset",
    "server closed the connection",
    "terminating connection",
    "the database system is",
    "another operation is in progress",
)

# 「日志存储此刻不可用」：连接之外还包括 DSN/迁移层面的不可用。api/logs.py 据此把
# 异常映射成 503 而不是 500。这些重连也没用，所以不参与 _flush 的重连补偿。
_UNAVAILABLE_EXC_TYPES = _CONNECTION_EXC_TYPES + (
    asyncpg.InvalidCatalogNameError,  # DSN 指向的库不存在
    asyncpg.InvalidPasswordError,
    # logs 表不存在（迁移没应用）同样是写不进去，而且指向一个可执行的动作。
    asyncpg.UndefinedTableError,
)

# 重连失败后的冷却时间：PG 长时间不可用时，每个批次都新开连接会把「日志写不进去」
# 放大成连接风暴。冷却期内直接丢当批日志（计入 queue_dropped），到点再试一次。
_RECONNECT_BACKOFF_SECONDS = 30.0


class LogStorage:
    """日志持久化服务（单例）。"""

    def __init__(self) -> None:
        self._conn: Optional[PgConnection] = None
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=10000)
        self._consumer_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._write_total: int = 0
        self._dropped: int = 0
        self._last_error: Optional[str] = None
        self._last_flush_at: float = 0.0
        self._degraded: bool = False
        self._last_maintenance_at: float = 0.0
        # start() 成功后才允许写：没起来（或已 stop）的存储不该在 _flush 里偷偷连库。
        self._started: bool = False
        self._next_reconnect_at: float = 0.0

    # ── 生命周期 ─────────────────────────────────

    async def start(self) -> bool:
        """连接 PostgreSQL + 启动后台消费者协程。

        不在启动期建表：PG 的结构变更要可追溯，一律走 `migrations/pg/0004_logs.sql`
        （见 ADR 0089）。缺表只报一条带动作的日志，不阻塞应用启动——门店在 Admin
        「数据库迁移」里补上即可，不需要重启。
        """
        try:
            await self._connect()
            await self._warn_if_table_missing()

            # 清理过期日志
            if settings.LOG_RETENTION_DAYS > 0:
                try:
                    deleted = await self.cleanup_older_than(settings.LOG_RETENTION_DAYS)
                    if deleted > 0:
                        logger.info(
                            f"🧹 清理 {deleted} 条 {settings.LOG_RETENTION_DAYS} 天前的日志"
                        )
                except Exception as exc:  # 启动期清理失败不阻塞
                    logger.warning(f"⚠️ 清理过期日志失败: {exc}")

            # 后台消费者
            self._loop = asyncio.get_running_loop()
            self._stop_event = asyncio.Event()
            self._last_maintenance_at = time.monotonic()
            self._consumer_task = asyncio.create_task(self._consume_loop())
            self._started = True
            logger.info("✅ 日志存储已就绪 (PostgreSQL logs 表)")
            return True
        except Exception as exc:
            self._last_error = str(exc)
            logger.error(f"❌ 启动日志存储失败: {exc}")
            return False

    async def _connect(self) -> None:
        """建立 PG 连接（复用 db_core 的设施：占位符重写 + 单连接串行锁）。

        直连一条专用连接而不走业务库那一条：日志写入是高频后台路径，和业务查询
        挤在同一条连接上会互相排队。
        """
        self._conn = await pg_backend.connect()
        self._degraded = False
        self._last_error = None
        self._next_reconnect_at = 0.0

    async def _close_conn(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            await conn.close()
        except Exception:
            logger.debug("关闭日志存储连接失败", exc_info=True)

    async def _reconnect(self) -> bool:
        """换一条连接：PG 的坏连接救不回来，重连是唯一的恢复手段。"""
        await self._close_conn()
        try:
            await self._connect()
            return True
        except Exception as exc:
            self._last_error = str(exc)
            self._degraded = True
            logger.error(f"❌ 日志存储重连失败: {exc}")
            return False

    async def _try_reconnect(self) -> bool:
        """带冷却的重连；冷却期内直接返回 False（不新建连接）。"""
        if time.monotonic() < self._next_reconnect_at:
            return False
        if await self._reconnect():
            self._next_reconnect_at = 0.0
            return True
        self._next_reconnect_at = time.monotonic() + _RECONNECT_BACKOFF_SECONDS
        return False

    async def _warn_if_table_missing(self) -> None:
        if self._conn is None:
            return
        try:
            # 只做解析，不取数据：表不存在时 PG 在计划阶段就报 undefined_table。
            await self._conn.execute("SELECT 1 FROM logs LIMIT 1")
        except asyncpg.UndefinedTableError:
            self._last_error = "logs 表不存在"
            self._degraded = True
            logger.error(
                "❌ logs 表不存在：请在 Admin「系统更新 → 数据库迁移」应用 "
                "migrations/pg/0004_logs.sql（PG 不在启动期改结构）"
            )

    async def _rollback_quietly(self) -> None:
        """丢弃未提交/aborted 事务；失败不影响调用方（连接可能已经不可用）。"""
        if self._conn is None:
            return
        try:
            await self._conn.rollback()
        except Exception:
            pass

    @staticmethod
    def is_disk_full_error(exc: Exception) -> bool:
        """是否属于「磁盘写不进去」这类可恢复错误。

        PG 侧对应 SQLSTATE 53100，asyncpg 抛 ``DiskFullError``；底层也可能是
        ENOSPC 文案。这类错误只丢当批日志并计入 ``queue_dropped``，**不改变**
        存储可用性判断——满盘是会恢复的，日志连接不该因此被判死。
        """
        if isinstance(exc, asyncpg.DiskFullError):
            return True
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "database or disk is full",
                "disk i/o error",
                "disk full",
                "no space left",
                "enospc",
            )
        )

    @staticmethod
    def is_corruption_error(exc: Exception) -> bool:
        """日志存储此刻是否不可用（连接类错误判定）。

        名字沿用历史对外契约（``api/logs.py`` 用它把「存储不可用」映射成 503）；
        PG 不产生页损坏，所以这里判的是可达性：连不上、连接已断、库/表不存在。
        """
        if isinstance(exc, _UNAVAILABLE_EXC_TYPES):
            return True
        message = str(exc).lower()
        return any(marker in message for marker in _CONNECTION_MARKERS)

    @staticmethod
    def _is_connection_error(exc: Exception) -> bool:
        """是否属于「连接没了」——只有这类才值得换一条连接重试。

        与 :meth:`is_corruption_error` 的区别：缺表 / 口令错 / 库不存在也是「存储
        不可用」，但重连一万次也还是不可用，重连只会白白制造连接。
        """
        if isinstance(exc, _CONNECTION_EXC_TYPES):
            return True
        message = str(exc).lower()
        return any(marker in message for marker in _CONNECTION_MARKERS)

    def _note_unavailable(self, exc: Exception) -> None:
        """查询路径失败时记一笔；不可用错误同时标记降级（stats 里可见）。"""
        self._last_error = str(exc)
        if self.is_corruption_error(exc):
            self._degraded = True

    async def stop(self) -> None:
        """停止消费者并 flush 残余日志。"""
        if self._consumer_task and not self._consumer_task.done():
            assert self._stop_event is not None
            self._stop_event.set()
            try:
                await asyncio.wait_for(self._consumer_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._consumer_task.cancel()
        # 兜底：把队列里残留的也写完（_started 在这之后才置 False，收尾写入照常）
        await self._flush_remaining()
        await self._close_conn()
        self._started = False

    # ── 写入（线程/协程安全） ─────────────────

    def enqueue(self, record: Dict[str, Any]) -> bool:
        """线程安全的入队。logging handler 在工作线程中调用。"""
        try:
            self._queue.put_nowait(record)
            return True
        except queue.Full:
            self._dropped += 1
            return False

    async def _consume_loop(self) -> None:
        assert self._loop is not None
        assert self._stop_event is not None
        batch: List[Dict[str, Any]] = []
        while not self._stop_event.is_set():
            try:
                # 阻塞式取一条，但加超时以响应 stop
                record = await self._loop.run_in_executor(
                    None, self._get_with_timeout, 0.5
                )
                if record is not None:
                    batch.append(record)
            except Exception as exc:
                self._last_error = str(exc)
                logger.debug(f"日志队列消费异常: {exc}")

            # 达到批量阈值或距上次刷新过久
            now = time.monotonic()
            should_flush = (
                len(batch) >= settings.LOG_QUEUE_BATCH_SIZE
                or (batch and now - self._last_flush_at >= settings.LOG_QUEUE_FLUSH_INTERVAL)
            )
            if should_flush:
                await self._flush(batch)
                batch.clear()
                self._last_flush_at = now

            # 运行期维护（清理过期日志）。启动期只清一次，长期不重启的实例必须靠
            # 这里把保留天数落到实处，否则保留配置形同虚设。
            if (
                now - self._last_maintenance_at
                >= settings.LOG_MAINTENANCE_INTERVAL_SECONDS
            ):
                self._last_maintenance_at = now
                await self._run_maintenance()

        # 退出前再 flush 一次
        if batch:
            await self._flush(batch)

    def _get_with_timeout(self, timeout: float) -> Optional[Dict[str, Any]]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @staticmethod
    def _to_rows(batch: List[Dict[str, Any]]) -> List[Tuple[Any, ...]]:
        return [
            (
                r["ts"],
                r["ts_epoch"],
                r["level"],
                r["logger"],
                r["message"],
                r.get("exception"),
            )
            for r in batch
        ]

    async def _insert_rows(self, rows: List[Tuple[Any, ...]]) -> None:
        """写一批日志。异常向上抛，由 `_flush` 决定是丢还是重连后补写。"""
        assert self._conn is not None
        await self._conn.executemany(_INSERT_SQL, rows)
        await self._conn.commit()

    async def _flush(self, batch: List[Dict[str, Any]]) -> None:
        if not batch:
            return
        if self._conn is None and not (self._started and await self._try_reconnect()):
            # 启动失败 / 已 stop / 上次重连失败：写不出去，计入丢弃
            # （冷却期内的批次在这里安静地计数，只有真正尝试重连时才打日志）。
            self._dropped += len(batch)
            self._last_error = self._last_error or "日志存储未连接"
            return

        rows = self._to_rows(batch)
        try:
            await self._insert_rows(rows)
            self._write_total += len(batch)
            # 写入恢复正常就不再算降级（_last_error 保留最后一次错误供排查）。
            self._degraded = False
            return
        except Exception as exc:
            # 先 rollback：否则连接停在未提交/aborted 事务上，后续所有读写都会
            # 连带失败（PG 的 aborted 事务会一直占着连接）。
            await self._rollback_quietly()
            self._last_error = str(exc)

            if self.is_disk_full_error(exc):
                self._dropped += len(batch)
                logger.warning(
                    f"⚠️ 日志写入遇磁盘空间不足，丢弃 {len(batch)} 条日志: {exc}"
                )
                return

            # 连接断了：换一条连接补写这一批；仍失败才丢。只在「连接没了」时重连
            # ——缺表 / 口令错这类不可用重连也没用，而真的连不上时由 _try_reconnect
            # 的冷却窗口兜住，不会每个批次都新开连接。
            if self._is_connection_error(exc) and await self._try_reconnect():
                try:
                    await self._insert_rows(rows)
                    self._write_total += len(batch)
                    self._degraded = False
                    logger.info("🔁 日志存储重连后补写 %s 条日志", len(batch))
                    return
                except Exception as retry_exc:
                    await self._rollback_quietly()
                    self._last_error = str(retry_exc)
                    exc = retry_exc

            self._dropped += len(batch)
            self._degraded = True
            logger.error(f"批量写入日志失败，丢弃 {len(batch)} 条: {exc}")

    async def _run_maintenance(self) -> None:
        """运行期日志维护：按保留天数清理过期记录。

        SQLite 时代这里还要 PASSIVE checkpoint 回收 WAL；PG 的空间回收交给
        autovacuum，维护循环只负责保留天数这一件事。
        """
        if self._conn is None:
            return
        try:
            if settings.LOG_RETENTION_DAYS > 0:
                deleted = await self.cleanup_older_than(settings.LOG_RETENTION_DAYS)
                if deleted:
                    logger.info(
                        f"🧹 运行期清理 {deleted} 条 "
                        f"{settings.LOG_RETENTION_DAYS} 天前的日志"
                    )
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning(f"⚠️ 运行期清理过期日志失败: {exc}")
            await self._rollback_quietly()

    async def _flush_remaining(self) -> None:
        """stop 时把队列里所有记录写完。"""
        batch: List[Dict[str, Any]] = []
        while True:
            try:
                record = self._queue.get_nowait()
            except queue.Empty:
                break
            batch.append(record)
        if batch:
            await self._flush(batch)

    # ── 查询 ─────────────────────────────────────

    async def query(
        self,
        level: Optional[str] = None,
        logger_name: Optional[str] = None,
        q: Optional[str] = None,
        since_epoch: Optional[float] = None,
        until_epoch: Optional[float] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """条件查询日志。返回 (rows, total_count)。"""
        assert self._conn is not None
        where, params = self._build_where(
            level=level,
            logger_name=logger_name,
            q=q,
            since_epoch=since_epoch,
            until_epoch=until_epoch,
        )
        try:
            return await self._query_rows(where, params, limit=limit, offset=offset)
        except Exception as exc:
            self._note_unavailable(exc)
            raise

    async def _query_rows(
        self,
        where: str,
        params: list,
        *,
        limit: int,
        offset: int,
    ) -> Tuple[List[Dict[str, Any]], int]:
        assert self._conn is not None
        count_sql = f"SELECT COUNT(*) AS c FROM logs{where}"
        cur = await self._conn.execute(count_sql, params)
        row = await cur.fetchone()
        total = int(row["c"]) if row else 0

        sql = (
            "SELECT id, ts, ts_epoch, level, logger, message, exception "
            f"FROM logs{where} "
            "ORDER BY id DESC LIMIT ? OFFSET ?"
        )
        cur = await self._conn.execute(sql, (*params, limit, offset))
        rows = await cur.fetchall()
        items = [
            {
                "id": r["id"],
                "timestamp": r["ts"],
                "ts_epoch": r["ts_epoch"],
                "level": r["level"],
                "logger": r["logger"],
                "message": r["message"],
                "exception": r["exception"],
            }
            for r in rows
        ]
        return items, total

    async def latest(self, limit: int = 50) -> List[Dict[str, Any]]:
        items, _ = await self.query(limit=limit)
        return items

    async def facets(self) -> Dict[str, List[Dict[str, Any]]]:
        """返回可选的 level / logger 维度及各自计数。"""
        assert self._conn is not None
        try:
            return await self._facets_rows()
        except Exception as exc:
            self._note_unavailable(exc)
            raise

    async def _facets_rows(self) -> Dict[str, List[Dict[str, Any]]]:
        assert self._conn is not None
        result: Dict[str, List[Dict[str, Any]]] = {"levels": [], "loggers": []}
        cur = await self._conn.execute(
            "SELECT level, COUNT(*) AS c FROM logs GROUP BY level ORDER BY c DESC"
        )
        result["levels"] = [
            {"value": r["level"], "count": r["c"]} for r in await cur.fetchall()
        ]
        cur = await self._conn.execute(
            "SELECT logger AS name, COUNT(*) AS c FROM logs "
            "GROUP BY logger ORDER BY c DESC LIMIT 50"
        )
        result["loggers"] = [
            {"value": r["name"], "count": r["c"]} for r in await cur.fetchall()
        ]
        return result

    async def stats(self) -> Dict[str, Any]:
        assert self._conn is not None
        try:
            return await self._stats_rows()
        except Exception as exc:
            self._note_unavailable(exc)
            raise

    async def _stats_rows(self) -> Dict[str, Any]:
        assert self._conn is not None
        cur = await self._conn.execute("SELECT COUNT(*) AS c FROM logs")
        row = await cur.fetchone()
        total = int(row["c"]) if row else 0

        cur = await self._conn.execute(
            "SELECT level, COUNT(*) AS c FROM logs "
            "WHERE ts_epoch > ? GROUP BY level",
            ((datetime.now(CHINA_TZ) - timedelta(hours=1)).timestamp(),),
        )
        last_hour = {r["level"]: r["c"] for r in await cur.fetchall()}

        # 最早/最晚一条的时间
        cur = await self._conn.execute(
            "SELECT MIN(ts_epoch) AS m, MAX(ts_epoch) AS x FROM logs"
        )
        row = await cur.fetchone()

        def _iso(epoch: Optional[float]) -> Optional[str]:
            if not epoch:
                return None
            return datetime.fromtimestamp(epoch, tz=CHINA_TZ).isoformat()

        return {
            "total": total,
            "last_hour": last_hour,
            "earliest": _iso(row["m"] if row else None),
            "latest": _iso(row["x"] if row else None),
            # 日志已与业务表同库（PG），不再有独立的库文件路径。
            "backend": "postgresql",
            "write_total": self._write_total,
            "queue_dropped": self._dropped,
            "queue_size": self._queue.qsize(),
            "last_error": self._last_error,
            "degraded": self._degraded,
            "retention_days": settings.LOG_RETENTION_DAYS,
        }

    async def cleanup_older_than(self, days: int) -> int:
        if days <= 0:
            return 0
        assert self._conn is not None
        cutoff = (datetime.now(CHINA_TZ) - timedelta(days=days)).timestamp()
        try:
            cur = await self._conn.execute(
                "DELETE FROM logs WHERE ts_epoch < ?", (cutoff,)
            )
            deleted = cur.rowcount or 0
            await self._conn.commit()
        except Exception as exc:
            self._note_unavailable(exc)
            raise
        return int(deleted)

    @staticmethod
    def _build_where(
        level: Optional[str],
        logger_name: Optional[str],
        q: Optional[str],
        since_epoch: Optional[float],
        until_epoch: Optional[float],
    ) -> Tuple[str, list]:
        clauses: list[str] = []
        params: list[Any] = []
        if level and level.upper() != "ALL":
            clauses.append("level = ?")
            params.append(level.upper())
        if logger_name:
            clauses.append("logger = ?")
            params.append(logger_name)
        if q:
            clauses.append("(message LIKE ? OR exception LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])
        if since_epoch is not None:
            clauses.append("ts_epoch >= ?")
            params.append(float(since_epoch))
        if until_epoch is not None:
            clauses.append("ts_epoch <= ?")
            params.append(float(until_epoch))
        if not clauses:
            return "", params
        return " WHERE " + " AND ".join(clauses), params


# ─────────────────────────────────────────────
#  logging Handler：把日志投递给 LogStorage
# ─────────────────────────────────────────────

class LogStorageHandler(logging.Handler):
    """logging → LogStorage 队列 handler（线程安全，不阻塞调用方）。"""

    def __init__(self, storage: LogStorage, level: int = logging.INFO):
        super().__init__(level=level)
        self._storage = storage
        self.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            timestamp = datetime.fromtimestamp(record.created, tz=CHINA_TZ)
            exception_text = None
            if record.exc_info:
                try:
                    exception_text = self.format_exception(record.exc_info)
                except Exception:
                    exception_text = None
            payload = {
                "ts": timestamp.isoformat(),
                "ts_epoch": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
                "exception": exception_text,
            }
            self._storage.enqueue(payload)
        except Exception:
            self.handleError(record)

    @staticmethod
    def format_exception(exc_info) -> str:
        import traceback

        return "".join(traceback.format_exception(*exc_info))


# 全局单例
log_storage = LogStorage()
