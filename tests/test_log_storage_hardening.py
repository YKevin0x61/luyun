#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志存储抗故障硬化（PostgreSQL）：磁盘满只丢当批、连接类错误判定、断连重连补写、
运行期维护。

背景：这套契约原先写在 `logs.db`（SQLite WAL）上，包含损坏隔离与 quarantine 副本
的用例。SQLite 退场后（ADR 0089）日志进 PG，页损坏不再存在，剩下的是「写不进去」的
两类可恢复故障：磁盘满与连接不可用。
"""

import time
import unittest
from datetime import datetime, timedelta

import asyncpg

from services.log_storage import CHINA_TZ, LogStorage


class _RaisingConn:
    """只实现 _flush 用到的最小接口，用来注入指定错误。"""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.commits = 0
        self.rollbacks = 0

    async def executemany(self, *args, **kwargs):
        raise self.error

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def close(self) -> None:
        pass


class _HealthyConn:
    """记住写入行的假连接：用来断言「重连后补写成功」。"""

    def __init__(self) -> None:
        self.rows = []
        self.commits = 0
        self.rollbacks = 0

    async def executemany(self, sql, rows) -> None:
        self.rows.extend(rows)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def close(self) -> None:
        pass


class _ReconnectStorage(LogStorage):
    """把 _reconnect 换成「立即换一条好连接」，确定性测重连补写。"""

    def __init__(self, healthy: _HealthyConn) -> None:
        super().__init__()
        self._healthy = healthy
        self.reconnects = 0

    async def _reconnect(self) -> bool:
        self.reconnects += 1
        self._conn = self._healthy
        return True


class _DeadReconnectStorage(LogStorage):
    """重连也失败：这一批应当被丢弃并计入 queue_dropped。"""

    def __init__(self) -> None:
        super().__init__()
        self.reconnects = 0

    async def _reconnect(self) -> bool:
        self.reconnects += 1
        self._degraded = True
        return False


def _record(message: str = "hello", ts_epoch: float | None = None) -> dict:
    return {
        "ts": datetime.now(CHINA_TZ).isoformat(),
        "ts_epoch": time.time() if ts_epoch is None else ts_epoch,
        "level": "INFO",
        "logger": "tests",
        "message": message,
        "exception": None,
    }


class LogStorageErrorPredicateTest(unittest.TestCase):
    def test_disk_full_predicate_does_not_match_connection_errors(self):
        self.assertTrue(
            LogStorage.is_disk_full_error(
                asyncpg.DiskFullError("could not extend file: No space left on device")
            )
        )
        self.assertTrue(
            LogStorage.is_disk_full_error(Exception("database or disk is full"))
        )
        self.assertTrue(LogStorage.is_disk_full_error(Exception("disk I/O error")))
        self.assertFalse(
            LogStorage.is_disk_full_error(
                asyncpg.ConnectionDoesNotExistError("unexpected connection loss")
            )
        )

    def test_corruption_predicate_is_connection_class_only(self):
        """PG 没有页损坏；这里判的是「连不上 / 连接已断 / 表还没有」。"""
        self.assertTrue(
            LogStorage.is_corruption_error(
                asyncpg.InterfaceError("connection is closed")
            )
        )
        self.assertTrue(
            LogStorage.is_corruption_error(
                asyncpg.ConnectionDoesNotExistError("unexpected connection loss")
            )
        )
        self.assertTrue(
            LogStorage.is_corruption_error(
                asyncpg.UndefinedTableError('relation "logs" does not exist')
            )
        )
        self.assertTrue(
            LogStorage.is_corruption_error(ConnectionRefusedError("connection refused"))
        )
        # 磁盘满是可恢复的「写不进去」，不能被当成存储不可用。
        self.assertFalse(
            LogStorage.is_corruption_error(
                asyncpg.DiskFullError("could not extend file: No space left on device")
            )
        )
        self.assertFalse(LogStorage.is_corruption_error(RuntimeError("boom")))


class LogStorageWriteFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_disk_full_drops_batch_without_degrading(self):
        storage = LogStorage()
        fake = _RaisingConn(asyncpg.DiskFullError("No space left on device"))
        storage._conn = fake

        await storage._flush([_record()])

        self.assertEqual(fake.rollbacks, 1, "must roll back the half-open transaction")
        self.assertEqual(storage._dropped, 1)
        self.assertFalse(storage._degraded, "满盘是可恢复的，不该判存储不可用")

    async def test_connection_error_reconnects_and_rewrites_batch(self):
        healthy = _HealthyConn()
        storage = _ReconnectStorage(healthy)
        storage._conn = _RaisingConn(asyncpg.InterfaceError("connection is closed"))

        await storage._flush([_record(), _record("second")])

        self.assertEqual(storage.reconnects, 1)
        self.assertEqual(len(healthy.rows), 2, "重连后必须把这一批补写完")
        self.assertEqual(storage._write_total, 2)
        self.assertEqual(storage._dropped, 0)
        self.assertFalse(storage._degraded)

    async def test_failed_reconnect_drops_batch_and_degrades(self):
        storage = _DeadReconnectStorage()
        storage._conn = _RaisingConn(asyncpg.InterfaceError("connection is closed"))

        await storage._flush([_record()])

        self.assertEqual(storage._dropped, 1)
        self.assertTrue(storage._degraded)

    async def test_reconnect_is_throttled_after_failure(self):
        """PG 不可用期间不能每个批次都新开连接（冷却窗口内的批次直接丢弃计数）。"""
        storage = _DeadReconnectStorage()
        storage._started = True
        storage._conn = None

        await storage._flush([_record()])
        await storage._flush([_record()])

        self.assertEqual(storage.reconnects, 1, "冷却期内不该再尝试新连接")
        self.assertEqual(storage._dropped, 2)
        self.assertTrue(storage._degraded)

    async def test_recovering_connection_is_reused_after_a_lost_one(self):
        """连接被丢弃后，下一批应当（按冷却窗口）重连并把日志补写进去。"""
        healthy = _HealthyConn()
        storage = _ReconnectStorage(healthy)
        storage._started = True
        storage._conn = None

        await storage._flush([_record()])

        self.assertEqual(storage.reconnects, 1)
        self.assertEqual(len(healthy.rows), 1)
        self.assertEqual(storage._dropped, 0)

    async def test_missing_table_does_not_reconnect(self):
        """缺表（迁移没应用）也是「存储不可用」，但重连一万次也还是不可用。"""
        healthy = _HealthyConn()
        storage = _ReconnectStorage(healthy)
        storage._conn = _RaisingConn(
            asyncpg.UndefinedTableError('relation "logs" does not exist')
        )

        await storage._flush([_record()])

        self.assertEqual(storage.reconnects, 0, "缺表不该制造无谓的新连接")
        self.assertEqual(storage._dropped, 1)
        self.assertTrue(storage._degraded)
        self.assertEqual(healthy.rows, [])

    async def test_unwritable_without_connection_counts_dropped(self):
        """没起来（start 失败 / 已 stop）的存储不许偷偷连库，只计入 queue_dropped。"""
        storage = LogStorage()

        await storage._flush([_record()])

        self.assertEqual(storage._dropped, 1)
        self.assertEqual(storage._write_total, 0)


class LogStorageMaintenanceTest(unittest.IsolatedAsyncioTestCase):
    """运行期维护走真实测试库（conftest 已把后端钉死为 PG 测试库）。"""

    async def asyncSetUp(self):
        self.storage = LogStorage()
        self.assertTrue(await self.storage.start(), "日志存储应能连上 PG 测试库")

    async def asyncTearDown(self):
        await self.storage.stop()

    async def test_maintenance_deletes_expired_rows(self):
        old_epoch = (datetime.now(CHINA_TZ) - timedelta(days=30)).timestamp()
        await self.storage._flush([_record("expired", old_epoch), _record("fresh")])

        await self.storage._run_maintenance()

        _, total = await self.storage.query()
        self.assertEqual(total, 1)
        items = await self.storage.latest()
        self.assertEqual(items[0]["message"], "fresh")


if __name__ == "__main__":
    unittest.main()
