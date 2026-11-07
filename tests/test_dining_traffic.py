#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""客流 / 翻台按小时落库（票 10）。

钉住四件事：

1. 同一小时重复采集**不新增行**、只刷新数值（一小时一行的唯一性在库里）；
2. `tables_data` 为 `None` / `[]` 时不写、不报错；
3. 汇总口径：在座人数 = 各行人数之和，开台数 = 有客的桌数，总桌数 = 行数；
4. 90 天清理与既有保留策略同一套门闩。

表来自 `migrations/pg/0050_dining_traffic.sql`（conftest 已全量应用）。
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from database import CHINA_TZ, DatabaseManager
from services.dining_traffic import record_hourly
from services.prep_review.retention import PrepReviewRetention

# 固定在「某天 19:23:45」：既验证截断到整点，也不依赖跑测试的时刻。
NOW = datetime(2026, 11, 7, 19, 23, 45, tzinfo=CHINA_TZ)

# 一轮真实的桌台快照形状（`pos_session._parse_table_item`）：A1 有客、A2 空桌、
# A3 有客。`amount` 为 0 / 有人数都算开台，这里 A2 两者都是 0。
TABLES = [
    {"table_number": "A1", "amount": 120.0, "people": 3, "duration": 40},
    {"table_number": "A2", "amount": 0.0, "people": 0, "duration": 0},
    {"table_number": "A3", "amount": 88.0, "people": 2, "duration": 25},
]


class DiningTrafficRecordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()

    async def _rows(self):
        conn = self.db._conn
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT snapshot_at, plan_day, in_seat_people, open_tables, "
                "total_tables, created_at FROM dining_traffic "
                "ORDER BY snapshot_at"
            )
            return [dict(row) for row in await cursor.fetchall()]

    # ── 正常一轮 ───────────────────────────────

    async def test_records_one_row_truncated_to_the_hour(self):
        await record_hourly(self.db, TABLES, now=NOW)

        rows = await self._rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # 同一小时内多次采集共用一个键，所以钟点必须截断（分秒位不可留）。
        self.assertEqual(row["snapshot_at"], "2026-11-07T19:00:00+08:00")
        self.assertEqual(row["plan_day"], "2026-11-07")

    async def test_summarises_people_open_and_total_tables(self):
        await record_hourly(self.db, TABLES, now=NOW)

        row = (await self._rows())[0]
        self.assertEqual(row["in_seat_people"], 5, "3 + 2 人")
        self.assertEqual(row["open_tables"], 2, "A2 是空桌，不算开台")
        self.assertEqual(row["total_tables"], 3, "总桌数 = 本轮快照行数")

    # ── 同小时幂等 ─────────────────────────────

    async def test_same_hour_refreshes_numbers_without_adding_a_row(self):
        await record_hourly(self.db, TABLES, now=NOW)
        first = (await self._rows())[0]

        # 同一小时的下一轮采集：又来了两位客人，多开一张桌。
        later = NOW.replace(minute=48, second=10)
        await record_hourly(
            self.db,
            [
                {"table_number": "A1", "amount": 168.0, "people": 4},
                {"table_number": "A2", "amount": 66.0, "people": 1},
                {"table_number": "A3", "amount": 88.0, "people": 2},
            ],
            now=later,
        )

        rows = await self._rows()
        self.assertEqual(len(rows), 1, "同一小时只能有一行")
        self.assertEqual(rows[0]["snapshot_at"], first["snapshot_at"])
        self.assertEqual(rows[0]["in_seat_people"], 7, "刷新成最新一轮的 4+1+2")
        self.assertEqual(rows[0]["open_tables"], 3)
        self.assertEqual(rows[0]["total_tables"], 3)
        self.assertEqual(
            rows[0]["created_at"], first["created_at"], "刷新数值不改首次写入时间"
        )

    async def test_a_different_hour_appends_a_row(self):
        await record_hourly(self.db, TABLES, now=NOW)
        await record_hourly(self.db, TABLES, now=NOW + timedelta(hours=1))

        rows = await self._rows()
        self.assertEqual([row["snapshot_at"] for row in rows], [
            "2026-11-07T19:00:00+08:00",
            "2026-11-07T20:00:00+08:00",
        ])

    # ── 空数据 ─────────────────────────────────

    async def test_none_or_empty_tables_write_nothing(self):
        self.assertIsNone(await record_hourly(self.db, None, now=NOW))
        self.assertIsNone(await record_hourly(self.db, [], now=NOW))

        self.assertEqual(await self._rows(), [])

    # ── 移动银台的字段差异 ─────────────────────

    async def test_counts_people_from_the_yintai_people_qty_field(self):
        """移动银台只给 `people_qty`（`scraper/yintai_session.py`），没有 `people`。"""
        await record_hourly(
            self.db,
            [
                {"table_number": "A1", "amount": 98.0, "people_qty": 4},
                {"table_number": "A2", "amount": 0.0, "people_qty": 0},
                # 混源时 `people` 是 None 也要退回 `people_qty`，别把在座人数算成 0。
                {"table_number": "A3", "amount": 50.0, "people": None, "people_qty": 2},
            ],
            now=NOW,
        )

        row = (await self._rows())[0]
        self.assertEqual(row["in_seat_people"], 6)
        self.assertEqual(row["open_tables"], 2)
        self.assertEqual(row["total_tables"], 3)


class DiningTrafficRetentionTests(unittest.IsolatedAsyncioTestCase):
    """90 天清理：与快照 / 事件同一套门闩与失败隔离。"""

    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.retention = PrepReviewRetention()

    async def asyncTearDown(self):
        await self.db.close()

    async def _seed(self, offsets) -> None:
        rows = []
        for offset in offsets:
            stamp = (NOW - timedelta(days=offset)).replace(
                minute=0, second=0, microsecond=0
            )
            rows.append(
                (stamp.isoformat(), stamp.date().isoformat(), stamp.isoformat())
            )
        conn = self.db._conn
        await conn.executemany(
            "INSERT INTO dining_traffic "
            "(snapshot_at, plan_day, created_at) VALUES (?, ?, ?)",
            rows,
        )
        await conn.commit()

    async def _count(self) -> int:
        conn = self.db._conn
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) AS n FROM dining_traffic")
            return int((await cursor.fetchone())["n"])

    async def test_purge_deletes_rows_older_than_90_days(self):
        # 121 天前、91 天前都在窗口外；10 天前保留。
        await self._seed([121, 91, 10])

        result = await self.retention.purge(self.db, NOW)

        self.assertTrue(result["success"])
        self.assertEqual(result["dining_traffic_deleted"], 2)
        self.assertEqual(await self._count(), 1)

        again = await self.retention.purge(self.db, NOW)
        self.assertEqual(again["dining_traffic_deleted"], 0, "重复跑删 0 行")


class RunCycleWiringTests(unittest.IsolatedAsyncioTestCase):
    """接线：`run_cycle` 在保存桌台之后落客流，且失败绝不能中断采集。"""

    def _build_scraper(self):
        from scraper.restaurant_scraper import RestaurantScraper

        scraper = RestaurantScraper.__new__(RestaurantScraper)
        scraper.logger = MagicMock()
        scraper.dish_catalog = None
        scraper.session = MagicMock()
        scraper.session.consecutive_login_failures = 0
        scraper.session.http = MagicMock(api_failures=0)
        scraper.tables = MagicMock()
        scraper.tables.last_dine_in_port_updates = 0
        scraper.delivery = MagicMock()
        scraper.delivery.last_delivery_cancel_count = 0
        scraper.ensure_ready = AsyncMock(return_value=True)
        scraper.scrape_table_data = AsyncMock(return_value=TABLES)
        scraper.monitor_table_orders = AsyncMock(
            return_value=[{"dish_name": "虾饺", "quantity": 1}]
        )
        scraper.scrape_delivery_orders = AsyncMock(return_value=[])
        return scraper

    def _db(self):
        db = MagicMock()
        db.save_table_data = AsyncMock()
        db.orders = MagicMock()
        db.orders.save_orders = AsyncMock()
        return db

    async def test_run_cycle_records_hourly_traffic_after_saving_tables(self):
        scraper = self._build_scraper()
        db = self._db()

        with patch(
            "services.dining_traffic.record_hourly", new_callable=AsyncMock
        ) as record, patch(
            "services.realtime.hub.realtime_hub.broadcast_nudge", new_callable=AsyncMock
        ), patch("services.scraper_health.update_runtime_health"):
            await scraper.run_cycle(db)

        record.assert_awaited_once()
        self.assertIs(record.await_args.args[1], TABLES)
        db.save_table_data.assert_awaited_once()

    async def test_recording_failure_does_not_interrupt_the_cycle(self):
        scraper = self._build_scraper()
        db = self._db()

        with patch(
            "services.dining_traffic.record_hourly",
            new_callable=AsyncMock,
            side_effect=RuntimeError("dining_traffic 表还没迁移"),
        ), patch(
            "services.realtime.hub.realtime_hub.broadcast_nudge", new_callable=AsyncMock
        ), patch("services.scraper_health.update_runtime_health"):
            await scraper.run_cycle(db)  # 不应抛

        db.orders.save_orders.assert_awaited_once()
        warnings = " ".join(
            str(call.args[0]) for call in scraper.logger.warning.call_args_list
            if call.args
        )
        self.assertIn("客流", warnings)
