#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志持久化存储
==============

- 独立的 `data/logs.db`，不与业务表混用
- 写入通过 `queue.Queue`，后台协程批量落库，避免阻塞事件循环
- 表结构：`logs(id, ts, level, logger, message, exception)`
- 启动时按 `LOG_RETENTION_DAYS` 清理过期记录
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import re
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from config import settings

logger = logging.getLogger(__name__)

CHINA_TZ = timezone(timedelta(hours=8))

# ─────────────────────────────────────────────
#  表结构
# ─────────────────────────────────────────────

_LOGS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        ts_epoch REAL NOT NULL,
        level TEXT NOT NULL,
        logger TEXT NOT NULL,
        message TEXT NOT NULL,
        exception TEXT
    )
"""

_LOG_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_logs_ts_epoch ON logs(ts_epoch DESC)",
    "CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)",
    "CREATE INDEX IF NOT EXISTS idx_logs_logger ON logs(logger)",
]

_BUSY_TIMEOUT_MS = 5000

# 损坏副本命名：<db>.corrupt.<YYYYmmdd_HHMMSS>[-wal|-shm|.forensics.txt]
_CORRUPT_SUFFIX_RE = re.compile(r"^(.+)\.corrupt\.(\d{8}_\d{6})(.*)$")


def _write_text_file(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class LogStorage:
    """日志持久化服务（单例）。"""

    def __init__(self) -> None:
        self._db_path: str = settings.DATABASE_PATHS["logs"]
        self._conn: Optional[aiosqlite.Connection] = None
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=10000)
        self._consumer_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._write_total: int = 0
        self._dropped: int = 0
        self._last_error: Optional[str] = None
        self._last_flush_at: float = 0.0
        self._recovery_attempted: bool = False
        self._degraded: bool = False
        self._last_maintenance_at: float = 0.0

    # ── 生命周期 ─────────────────────────────────

    async def start(self) -> bool:
        """建表 + 启动后台消费者协程。"""
        try:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            try:
                await self._open_and_prepare()
            except Exception as exc:
                if not await self._rebuild_after_corruption(exc):
                    raise
            else:
                # 启动体检：quick_check 只做页级校验，比 integrity_check 快得多
                # （220MB 库上后者要全表扫描并阻塞启动）。损坏的日志库没有抢救
                # 价值，直接隔离重建；但必须留档，才能事后判断是满盘还是真损坏。
                if settings.SQLITE_QUICK_CHECK_ON_START:
                    problem = await self._quick_check()
                    if problem:
                        logger.error("❌ logs.db quick_check 未通过: %s", problem)
                        if not await self._rebuild_after_corruption(
                            RuntimeError(problem), force=True
                        ):
                            raise RuntimeError(f"logs.db unusable: {problem}")
            logger.info(f"✅ 日志数据库已就绪: {self._db_path}")
            self._degraded = False

            # 清理过期日志
            if settings.LOG_RETENTION_DAYS > 0:
                try:
                    cutoff = datetime.now(CHINA_TZ) - timedelta(days=settings.LOG_RETENTION_DAYS)
                    cursor = await self._conn.execute(
                        "DELETE FROM logs WHERE ts_epoch < ?", (cutoff.timestamp(),)
                    )
                    deleted = cursor.rowcount or 0
                    await self._conn.commit()
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
            return True
        except Exception as exc:
            logger.error(f"❌ 启动日志存储失败: {exc}")
            return False

    async def _open_and_prepare(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        # logs.db 此前一直是默认 rollback journal 模式。改 WAL 是写入侧的关键
        # 优化：批量 append 不再反复重写日志文件，配合运行期 PASSIVE checkpoint
        # 回收空间。个别文件系统（NFS / 部分 overlay）不支持 WAL，失败时退回
        # 默认模式即可——不值得让日志存储整个起不来。
        try:
            await self._conn.execute("PRAGMA journal_mode=WAL")
        except Exception as exc:
            logger.warning("⚠️ logs.db 启用 WAL 失败，退回默认 journal 模式: %s", exc)
        # logs.db 是可丢弃的运行数据：WAL + synchronous=NORMAL 是 SQLite 官方
        # 推荐组合（进程崩溃安全，只牺牲断电瞬间最后几条记录），换来更少 fsync。
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        await self._conn.executescript(_LOGS_SCHEMA)
        for idx_sql in _LOG_INDEXES:
            await self._conn.execute(idx_sql)
        await self._conn.commit()

    async def _rebuild_after_corruption(
        self, exc: Exception, *, force: bool = False
    ) -> bool:
        """Quarantine a malformed logs.db and start a fresh one.

        Logs are non-critical operational data. A corrupt store must not take
        down the app or pin every health check in a retry loop, so rebuild it
        once and keep the damaged file for offline inspection.

        ``force`` 供启动期 quick_check 使用：它报出的问题文本不一定含
        "malformed"，但体检既然不通过，同样按损坏处理。
        """
        if self._recovery_attempted or not (force or self._is_corruption(exc)):
            return False
        self._recovery_attempted = True
        logger.error(
            "logs.db appears malformed; quarantining it and starting a fresh log store: %s",
            exc,
        )
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

        stamp = datetime.now(CHINA_TZ).strftime("%Y%m%d_%H%M%S")
        corrupt_path = f"{self._db_path}.corrupt.{stamp}"
        # 先移 -wal/-shm 再移主库：中间态宁可是「主库在、WAL 已移走」，也不能
        # 出现「主库已移走、旧 WAL 还在」——后者会让新建的库读到旧 WAL。
        move_errors: List[str] = []
        for suffix in ("-wal", "-shm", ""):
            src = f"{self._db_path}{suffix}"
            if not os.path.exists(src):
                continue
            dst = f"{corrupt_path}{suffix}"
            try:
                await asyncio.to_thread(os.replace, src, dst)
            except OSError as move_exc:
                move_errors.append(f"{suffix or '<db>'}: {move_exc}")
                logger.exception("failed to quarantine logs database %s", src)

        if os.path.exists(self._db_path):
            # 主库搬不走就不能建新库（会往损坏文件上写），保持降级等下次重启再试。
            self._degraded = True
            self._last_error = f"quarantine failed: {'; '.join(move_errors)}"
            logger.error("logs.db 无法隔离，日志存储保持降级: %s", self._last_error)
            return False

        try:
            await self._write_forensics(corrupt_path, exc, move_errors)
        except Exception:
            logger.exception("failed to write logs.db forensics sidecar")

        try:
            await self._open_and_prepare()
        except Exception:
            logger.exception("failed to recreate logs database after quarantine")
            return False
        self._last_error = None
        self._degraded = False
        try:
            self._prune_quarantine_copies(settings.LOG_CORRUPT_KEEP)
        except Exception:
            logger.exception("failed to prune old logs.db quarantine copies")
        logger.warning("fresh logs database created; damaged copy kept at %s", corrupt_path)
        return True

    async def _quick_check(self) -> Optional[str]:
        """``PRAGMA quick_check``：通过返回 None，否则返回问题描述。"""
        if self._conn is None:
            return None
        try:
            async with self._conn.execute("PRAGMA quick_check(1)") as cur:
                rows = await cur.fetchall()
        except Exception as exc:
            return str(exc)
        problems = [str(r[0]) for r in rows if str(r[0]).strip().lower() != "ok"]
        return "; ".join(problems) if problems else None

    async def _write_forensics(
        self, corrupt_path: str, exc: Exception, move_errors: List[str]
    ) -> None:
        """把现场状态写进 sidecar：判断「满盘导致」还是「真损坏」全靠它。

        没有这份记录就只能靠猜——现场报告把满盘直接当成 fsync 半写，
        但 SQLite 在 ENOSPC 下的正常行为是回滚并返回 SQLITE_FULL，不该损坏库。
        """
        lines = [
            f"time={datetime.now(CHINA_TZ).isoformat()}",
            f"db_path={self._db_path}",
            f"error={exc}",
        ]
        if move_errors:
            lines.append("move_errors=" + "; ".join(move_errors))
        try:
            usage = shutil.disk_usage(os.path.dirname(self._db_path) or ".")
            lines.append(
                "disk_total_mb={:.0f} disk_used_mb={:.0f} disk_free_mb={:.0f}".format(
                    usage.total / (1024 * 1024),
                    usage.used / (1024 * 1024),
                    usage.free / (1024 * 1024),
                )
            )
        except OSError as usage_exc:
            lines.append(f"disk_usage_error={usage_exc}")
        for suffix in ("", "-wal", "-shm"):
            try:
                size = os.path.getsize(f"{corrupt_path}{suffix}")
            except OSError:
                continue
            lines.append(f"size{suffix or '<db>'}={size}")
        text = "\n".join(lines) + "\n"
        await asyncio.to_thread(_write_text_file, f"{corrupt_path}.forensics.txt", text)

    def _prune_quarantine_copies(self, keep: int) -> int:
        """只保留最近 ``keep`` 份损坏副本（<=0 表示不限制）。

        每份是几百 MB 的快照，无上限保留会反过来加剧磁盘满，形成
        「满盘 → 损坏 → 再满盘」的循环。
        """
        if keep <= 0:
            return 0
        parent = os.path.dirname(self._db_path) or "."
        base = os.path.basename(self._db_path)
        groups: Dict[str, List[str]] = {}
        try:
            names = os.listdir(parent)
        except OSError:
            return 0
        for name in names:
            if not name.startswith(base + ".corrupt."):
                continue
            match = _CORRUPT_SUFFIX_RE.match(name)
            if not match:
                continue
            groups.setdefault(match.group(2), []).append(name)
        removed = 0
        for stamp in sorted(groups, reverse=True)[keep:]:
            for name in groups[stamp]:
                try:
                    os.remove(os.path.join(parent, name))
                    removed += 1
                except OSError:
                    logger.warning("无法删除过期的损坏日志副本: %s", name)
        if removed:
            logger.info("🧹 清理了 %s 个过期的 logs.db 损坏副本", removed)
        return removed

    async def _rollback_quietly(self) -> None:
        """丢弃未提交事务；失败不影响调用方（连接可能已经不可用）。"""
        if self._conn is None:
            return
        try:
            await self._conn.rollback()
        except Exception:
            pass

    @staticmethod
    def is_disk_full_error(exc: Exception) -> bool:
        """是否属于「磁盘写不进去」这类可恢复错误。

        这类错误绝不能拿去隔离数据库：满盘是可恢复的，而隔离会把全部历史
        日志永久搬走（现场就是这样丢掉 220MB）。
        """
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
    def _is_corruption(exc: Exception) -> bool:
        return LogStorage.is_corruption_error(exc)

    @staticmethod
    def is_corruption_error(exc: Exception) -> bool:
        """Whether an exception indicates a damaged SQLite log store."""
        message = str(exc).lower()
        return "malformed" in message or "not a database" in message

    async def _recover_once(self, exc: Exception) -> bool:
        """Handle corruption discovered after startup without retrying forever."""
        if not self._is_corruption(exc):
            return False
        recovered = await self._rebuild_after_corruption(exc)
        if not recovered:
            self._degraded = True
            self._last_error = str(exc)
            logger.warning("log database is degraded; persistent log writes are unavailable")
        return recovered

    async def stop(self) -> None:
        """停止消费者并 flush 残余日志。"""
        if self._consumer_task and not self._consumer_task.done():
            assert self._stop_event is not None
            self._stop_event.set()
            try:
                await asyncio.wait_for(self._consumer_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._consumer_task.cancel()
        # 兜底：把队列里残留的也写完
        await self._flush_remaining()
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                pass

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

            # 运行期维护（清理过期日志 + 回收 WAL）。启动期只清一次，长期不重启
            # 的实例必须靠这里把 logs.db 控制住，否则保留天数形同虚设。
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

    async def _flush(self, batch: List[Dict[str, Any]]) -> None:
        if not batch or not self._conn:
            return
        try:
            await self._conn.executemany(
                """INSERT INTO logs (ts, ts_epoch, level, logger, message, exception)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        r["ts"],
                        r["ts_epoch"],
                        r["level"],
                        r["logger"],
                        r["message"],
                        r.get("exception"),
                    )
                    for r in batch
                ],
            )
            await self._conn.commit()
            self._write_total += len(batch)
        except Exception as exc:
            self._last_error = str(exc)
            # 先 rollback：否则连接停在未结束的事务上，后续所有读写都会连带失败
            # （现场日志里的 "Cannot operate on a closed database." 正是这种连锁）。
            await self._rollback_quietly()
            # 磁盘满是可恢复错误：丢这一批日志并降级，绝不能隔离数据库——
            # 隔离会把全部历史日志永久搬走，而满盘本身是会恢复的。
            if self.is_disk_full_error(exc):
                self._dropped += len(batch)
                logger.warning(
                    f"⚠️ 日志写入遇磁盘空间不足，丢弃 {len(batch)} 条日志: {exc}"
                )
                return
            if not await self._recover_once(exc):
                logger.error(f"批量写入日志失败: {exc}")

    async def _run_maintenance(self) -> None:
        """运行期日志维护：清理过期记录 + PASSIVE 回收 WAL。

        只做「不阻塞写入」的被动 checkpoint；磁盘满时这里的失败必须被吞掉，
        不能反过来影响日志写入本身。
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
        try:
            await self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception as exc:
            # 非 WAL 模式（文件系统不支持时退回）会在这里报错，忽略即可。
            logger.debug("日志 WAL checkpoint 跳过: %s", exc)

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
            if not await self._recover_once(exc):
                raise
            return await self._query_rows(where, params, limit=limit, offset=offset)

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
        async with self._conn.execute(count_sql, params) as cur:
            row = await cur.fetchone()
        total = int(row["c"]) if row else 0

        sql = (
            "SELECT id, ts, ts_epoch, level, logger, message, exception "
            f"FROM logs{where} "
            "ORDER BY id DESC LIMIT ? OFFSET ?"
        )
        async with self._conn.execute(sql, (*params, limit, offset)) as cur:
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
        result: Dict[str, List[Dict[str, Any]]] = {"levels": [], "loggers": []}
        try:
            return await self._facets_rows()
        except Exception as exc:
            if not await self._recover_once(exc):
                raise
            return await self._facets_rows()

    async def _facets_rows(self) -> Dict[str, List[Dict[str, Any]]]:
        assert self._conn is not None
        result: Dict[str, List[Dict[str, Any]]] = {"levels": [], "loggers": []}
        async with self._conn.execute(
            "SELECT level, COUNT(*) AS c FROM logs GROUP BY level ORDER BY c DESC"
        ) as cur:
            result["levels"] = [
                {"value": r["level"], "count": r["c"]} async for r in cur
            ]
        async with self._conn.execute(
            "SELECT logger AS name, COUNT(*) AS c FROM logs GROUP BY logger ORDER BY c DESC LIMIT 50"
        ) as cur:
            result["loggers"] = [
                {"value": r["name"], "count": r["c"]} async for r in cur
            ]
        return result

    async def stats(self) -> Dict[str, Any]:
        assert self._conn is not None
        try:
            return await self._stats_rows()
        except Exception as exc:
            if not await self._recover_once(exc):
                raise
            return await self._stats_rows()

    async def _stats_rows(self) -> Dict[str, Any]:
        assert self._conn is not None
        async with self._conn.execute("SELECT COUNT(*) AS c FROM logs") as cur:
            row = await cur.fetchone()
        total = int(row["c"]) if row else 0

        async with self._conn.execute(
            "SELECT level, COUNT(*) AS c FROM logs "
            "WHERE ts_epoch > ? GROUP BY level",
            ((datetime.now(CHINA_TZ) - timedelta(hours=1)).timestamp(),),
        ) as cur:
            last_hour = {r["level"]: r["c"] async for r in cur}

        # 最早一条的时间
        async with self._conn.execute(
            "SELECT MIN(ts_epoch) AS m, MAX(ts_epoch) AS x FROM logs"
        ) as cur:
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
            "db_path": self._db_path,
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
        async with self._conn.execute(
            "DELETE FROM logs WHERE ts_epoch < ?", (cutoff,)
        ) as cur:
            deleted = cur.rowcount or 0
        await self._conn.commit()
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
