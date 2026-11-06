#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货复盘的 90 天保留策略（票 07）。

钉住四件事：

1. `prep_plan_snapshots` 近 90 天保留全部小时行，更早的只留 07:00 主口径一条；
2. `prep_plan_events` 90 天前的行删除；
3. `prep_plan_actuals` 一行不动（复盘证据与快照同期限）；
4. 重复跑无副作用，失败只记日志不抛。

测试库内直接写 SQL 造 100 天数据（票里允许），不走 `write_snapshot` 的重计算路径。
"""

import unittest
from datetime import datetime, timedelta

from database import CHINA_TZ, DatabaseManager
from services.log_storage import LogStorage
from services.prep_review.retention import PrepReviewRetention

# 固定在零点，让「90 天前」正好落在自然日边界上，边界断言不依赖跑测试的时刻。
NOW = datetime(2026, 11, 8, 0, 0, tzinfo=CHINA_TZ)
CUTOFF = NOW - timedelta(days=90)
PRIMARY_CLOCK = "07:00"

SNAPSHOT_INSERT = """
INSERT INTO prep_plan_snapshots (
    snapshot_at, window_start, window_end, plan_day,
    prep_item_id, item_name, unit, station,
    suggested_qty, demand_qty, store_factor, quantile,
    basis_json, rules_fingerprint, created_at
) VALUES (?, ?, ?, ?, 1, '虾饺馅', '份', 'shulong', 10, 10, 1, 0.5, '{}', '', ?)
"""

ACTUAL_INSERT = """
INSERT INTO prep_plan_actuals (
    window_start, window_end, prep_item_id, item_name, unit, station,
    sold_qty, rules_fingerprint, computed_at
) VALUES (?, ?, 1, '虾饺馅', '份', 'shulong', 10, '', ?)
"""

EVENT_INSERT = """
INSERT INTO prep_plan_events (
    at, plan_day, prep_item_id, kind, meta_json
) VALUES (?, ?, 1, 'window_end', '{}')
"""


def _at(day_offset: int, hour: int = 0) -> datetime:
    """相对 NOW 往前 `day_offset` 天的钟点（固定 +08:00）。"""
    day = NOW - timedelta(days=day_offset)
    return day.replace(hour=hour)


class PrepReviewRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.retention = PrepReviewRetention()

    async def asyncTearDown(self):
        await self.db.close()

    # ── 造数据 ─────────────────────────────────

    async def _seed_snapshots(self, *, days: int = 100) -> None:
        """最近 `days` 天，每天 24 条整点快照。"""
        rows = []
        for offset in range(1, days + 1):
            for hour in range(24):
                stamp = _at(offset, hour)
                rows.append(
                    (
                        stamp.isoformat(),
                        stamp.isoformat(),
                        (stamp + timedelta(hours=24)).isoformat(),
                        stamp.date().isoformat(),
                        stamp.isoformat(),
                    )
                )
        conn = self.db._conn
        await conn.executemany(SNAPSHOT_INSERT, rows)
        await conn.commit()

    async def _seed_actuals(self, offsets) -> None:
        rows = []
        for offset in offsets:
            stamp = _at(offset)
            rows.append(
                (
                    stamp.isoformat(),
                    (stamp + timedelta(hours=24)).isoformat(),
                    stamp.isoformat(),
                )
            )
        conn = self.db._conn
        await conn.executemany(ACTUAL_INSERT, rows)
        await conn.commit()

    async def _seed_events(self, offsets) -> None:
        rows = []
        for offset in offsets:
            stamp = _at(offset, 9)
            rows.append((stamp.isoformat(), stamp.date().isoformat()))
        conn = self.db._conn
        await conn.executemany(EVENT_INSERT, rows)
        await conn.commit()

    async def _count(self, table_name: str) -> int:
        conn = self.db._conn
        async with conn.cursor() as cursor:
            await cursor.execute(f"SELECT COUNT(*) AS n FROM {table_name}")
            return int((await cursor.fetchone())["n"])

    async def _snapshot_clocks(self, before: datetime):
        conn = self.db._conn
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT snapshot_at FROM prep_plan_snapshots "
                "WHERE snapshot_at < ? ORDER BY snapshot_at",
                (before.isoformat(),),
            )
            return [row["snapshot_at"][11:16] for row in await cursor.fetchall()]

    # ── 快照降采样 ─────────────────────────────

    async def test_purge_downsamples_old_snapshots_to_primary_clock(self):
        await self._seed_snapshots(days=100)

        result = await self.retention.purge(self.db, NOW)

        self.assertTrue(result["success"])
        # 90 天（含边界当天）保留全天 24 条，再往前 10 天每天只留 07:00。
        self.assertEqual(result["snapshots_deleted"], 10 * 23)
        self.assertEqual(await self._count("prep_plan_snapshots"), 90 * 24 + 10)
        self.assertEqual(
            await self._snapshot_clocks(CUTOFF), [PRIMARY_CLOCK] * 10
        )

    async def test_recent_snapshots_are_kept_in_full(self):
        await self._seed_snapshots(days=100)
        await self.retention.purge(self.db, NOW)

        conn = self.db._conn
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT COUNT(*) AS n FROM prep_plan_snapshots WHERE snapshot_at >= ?",
                (CUTOFF.isoformat(),),
            )
            self.assertEqual(int((await cursor.fetchone())["n"]), 90 * 24)

    # ── 事件删除 ───────────────────────────────

    async def test_purge_deletes_events_older_than_90_days(self):
        # 121 天前、91 天前、正好 90 天边界、10 天前。
        await self._seed_events([121, 91, 90, 10])

        result = await self.retention.purge(self.db, NOW)

        self.assertEqual(result["events_deleted"], 2)
        self.assertEqual(await self._count("prep_plan_events"), 2)

    # ── 实际销量不动 ───────────────────────────

    async def test_purge_leaves_actuals_untouched(self):
        await self._seed_actuals([120, 91, 89, 10])
        before = await self._count("prep_plan_actuals")

        await self.retention.purge(self.db, NOW)

        self.assertEqual(await self._count("prep_plan_actuals"), before)

    # ── 幂等 ───────────────────────────────────

    async def test_purge_is_idempotent(self):
        await self._seed_snapshots(days=100)
        await self._seed_events([121, 10])
        await self.retention.purge(self.db, NOW)
        snapshot_count = await self._count("prep_plan_snapshots")

        again = await self.retention.purge(self.db, NOW)

        self.assertTrue(again["success"])
        self.assertEqual(again["snapshots_deleted"], 0)
        self.assertEqual(again["events_deleted"], 0)
        self.assertEqual(await self._count("prep_plan_snapshots"), snapshot_count)

    # ── 失败隔离 ───────────────────────────────

    async def test_failure_is_reported_without_raising(self):
        class _BrokenDB:
            @property
            def _conn(self):
                raise RuntimeError("database unavailable")

        result = await self.retention.purge(_BrokenDB(), NOW)

        self.assertFalse(result["success"])

    # ── 每日一次 ───────────────────────────────

    async def test_run_daily_runs_at_most_once_per_local_day(self):
        await self._seed_events([121])

        first = await self.retention.run_daily(self.db, NOW)
        second = await self.retention.run_daily(
            self.db, NOW + timedelta(hours=6)
        )
        next_day = await self.retention.run_daily(
            self.db, NOW + timedelta(days=1)
        )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(next_day)
        self.assertEqual(await self._count("prep_plan_events"), 0)


class LogMaintenanceHookTests(unittest.IsolatedAsyncioTestCase):
    """清理挂在日志维护节拍上，且不依赖日志自己的连接（ADR 0130）。"""

    async def test_registered_hook_runs_even_without_log_connection(self):
        storage = LogStorage()  # 不 start：_conn 为 None，模拟日志库暂时连不上
        calls = []

        async def hook():
            calls.append("ran")

        storage.add_maintenance_hook(hook)
        await storage._run_maintenance()

        self.assertEqual(calls, ["ran"])

    async def test_one_failing_hook_does_not_block_the_next(self):
        storage = LogStorage()
        calls = []

        async def broken():
            raise RuntimeError("boom")

        async def healthy():
            calls.append("ran")

        storage.add_maintenance_hook(broken)
        storage.add_maintenance_hook(healthy)
        await storage._run_maintenance()  # 不应抛

        self.assertEqual(calls, ["ran"])
