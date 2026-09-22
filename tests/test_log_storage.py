#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志存储契约（PostgreSQL）：入队 → 后台批量落库 → 查询 / facets / stats / 清理。

写入走公开路径（`enqueue()` + 后台消费者协程），读回也走公开查询方法，确保断言的是
「日志真的进了 PG」，而不是进程内状态。
"""

import asyncio
import logging
import time
import unittest
from datetime import datetime, timedelta

from config import settings
from services.log_storage import CHINA_TZ, LogStorage, LogStorageHandler


class LogStoragePostgresTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = LogStorage()
        self.assertTrue(await self.storage.start(), "日志存储应能连上 PG 测试库")

    async def asyncTearDown(self):
        await self.storage.stop()

    @staticmethod
    def _record(
        message: str,
        *,
        level: str = "INFO",
        logger_name: str = "tests",
        ts_epoch: float | None = None,
        exception: str | None = None,
    ) -> dict:
        epoch = time.time() if ts_epoch is None else ts_epoch
        return {
            "ts": datetime.fromtimestamp(epoch, tz=CHINA_TZ).isoformat(),
            "ts_epoch": epoch,
            "level": level,
            "logger": logger_name,
            "message": message,
            "exception": exception,
        }

    async def _wait_for_rows(self, expected: int, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, total = await self.storage.query(limit=1)
            if total >= expected:
                return
            await asyncio.sleep(0.05)
        self.fail(f"后台消费者未在 {timeout}s 内把 {expected} 条日志写入 PG")

    async def _persist(self, *records: dict) -> None:
        """公开写入路径：入队，然后等后台消费者批量落库。"""
        for record in records:
            self.assertTrue(self.storage.enqueue(record))
        await self._wait_for_rows(len(records))

    async def test_enqueued_records_are_persisted_and_queryable(self):
        await self._persist(
            self._record("first"),
            self._record("second", level="WARNING", logger_name="scraper"),
            self._record("third", exception="Traceback: boom"),
        )

        items, total = await self.storage.query()

        self.assertEqual(total, 3)
        self.assertEqual(len(items), 3)
        # ORDER BY id DESC：最新的一条在前
        self.assertEqual(items[0]["message"], "third")
        self.assertEqual(items[0]["exception"], "Traceback: boom")
        self.assertEqual(items[1]["level"], "WARNING")
        self.assertEqual(items[1]["logger"], "scraper")
        self.assertIsInstance(items[0]["id"], int)
        self.assertGreater(items[0]["ts_epoch"], 0)

    async def test_query_filters_by_level_logger_text_and_window(self):
        now = time.time()
        await self._persist(
            self._record("plain info", ts_epoch=now - 3600),
            self._record("needle in message", level="ERROR", logger_name="scraper",
                         ts_epoch=now - 60),
            self._record("other", exception="needle in exception", level="ERROR",
                         ts_epoch=now - 30),
            self._record("ancient", ts_epoch=now - 86400 * 30),
        )

        _, by_level = await self.storage.query(level="error")
        self.assertEqual(by_level, 2, "level 过滤应当不区分大小写地归一")

        _, by_logger = await self.storage.query(logger_name="scraper")
        self.assertEqual(by_logger, 1)

        items, by_text = await self.storage.query(q="needle")
        self.assertEqual(by_text, 2, "message 与 exception 都要参与模糊搜索")
        self.assertEqual(
            sorted(item["message"] for item in items), ["needle in message", "other"]
        )

        _, in_window = await self.storage.query(since_epoch=now - 7200, until_epoch=now)
        self.assertEqual(in_window, 3, "时间窗外的 ancient 不应命中")

    async def test_latest_respects_limit(self):
        await self._persist(*[self._record(f"m{i}") for i in range(5)])

        items = await self.storage.latest(limit=2)

        self.assertEqual([item["message"] for item in items], ["m4", "m3"])

    async def test_facets_count_levels_and_loggers(self):
        await self._persist(
            self._record("a", level="INFO"),
            self._record("b", level="INFO"),
            self._record("c", level="ERROR", logger_name="scraper"),
        )

        facets = await self.storage.facets()

        self.assertEqual(facets["levels"][0], {"value": "INFO", "count": 2})
        self.assertEqual(
            sorted(item["value"] for item in facets["loggers"]),
            ["scraper", "tests"],
        )

    async def test_stats_reports_postgres_backend_and_counters(self):
        await self._persist(self._record("one"))

        stats = await self.storage.stats()

        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["backend"], "postgresql")
        self.assertNotIn("db_path", stats, "日志不再有独立的库文件路径")
        self.assertEqual(stats["queue_dropped"], 0)
        self.assertEqual(stats["queue_size"], 0)
        self.assertFalse(stats["degraded"])
        self.assertEqual(stats["retention_days"], settings.LOG_RETENTION_DAYS)
        self.assertIsNotNone(stats["earliest"])
        self.assertIsNotNone(stats["latest"])

    async def test_cleanup_older_than_deletes_only_expired(self):
        old_epoch = (datetime.now(CHINA_TZ) - timedelta(days=10)).timestamp()
        await self._persist(self._record("old", ts_epoch=old_epoch), self._record("new"))

        deleted = await self.storage.cleanup_older_than(7)

        self.assertEqual(deleted, 1)
        _, total = await self.storage.query()
        self.assertEqual(total, 1)
        self.assertEqual(await self.storage.cleanup_older_than(0), 0, "0 天不清理")

    async def test_handler_emit_reaches_postgres(self):
        """LogStorageHandler → 队列 → PG：换一个实例读回，证明数据真落库了。"""
        handler = LogStorageHandler(self.storage)
        handler.emit(
            logging.LogRecord(
                name="tests",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg="handler-message",
                args=(),
                exc_info=None,
            )
        )
        await self._wait_for_rows(1)

        reader = LogStorage()
        self.assertTrue(await reader.start())
        try:
            items = await reader.latest(limit=5)
        finally:
            await reader.stop()

        self.assertEqual(len(items), 1)
        self.assertIn("handler-message", items[0]["message"])
        self.assertEqual(items[0]["logger"], "tests")


if __name__ == "__main__":
    unittest.main()
