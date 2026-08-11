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

    # ── 生命周期 ─────────────────────────────────

    async def start(self) -> bool:
        """建表 + 启动后台消费者协程。"""
        try:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.executescript(_LOGS_SCHEMA)
            for idx_sql in _LOG_INDEXES:
                await self._conn.execute(idx_sql)
            await self._conn.commit()
            logger.info(f"✅ 日志数据库已就绪: {self._db_path}")

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
            self._consumer_task = asyncio.create_task(self._consume_loop())
            return True
        except Exception as exc:
            logger.error(f"❌ 启动日志存储失败: {exc}")
            return False

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
            logger.error(f"批量写入日志失败: {exc}")

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

        # 总数
        count_sql = f"SELECT COUNT(*) AS c FROM logs{where}"
        async with self._conn.execute(count_sql, params) as cur:
            row = await cur.fetchone()
        total = int(row["c"]) if row else 0

        # 分页
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
