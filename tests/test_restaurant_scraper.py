#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RestaurantScraper composition-root orchestration tests."""

import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from config import settings
from scraper.restaurant_scraper import RestaurantScraper
from services.scraper_health import read_health


class _ApiFailureCounter:
    """代替 PosHttpClient 的进程内累计失败计数（`settled_api_failures` 的来源）。"""

    def __init__(self, value: int = 0):
        self.api_failures = value


class RestaurantScraperCycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # 健康状态落在 settings.DATABASE_DIR：指到临时目录，别污染仓库 data/
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_dir = settings.DATABASE_DIR
        settings.DATABASE_DIR = self._tmpdir.name

    def tearDown(self):
        settings.DATABASE_DIR = self._old_dir
        self._tmpdir.cleanup()

    def _build_scraper(self) -> RestaurantScraper:
        scraper = RestaurantScraper.__new__(RestaurantScraper)
        scraper.logger = MagicMock()
        scraper.dish_catalog = None
        scraper.session = MagicMock()
        scraper.session.consecutive_login_failures = 0
        scraper.session.http = _ApiFailureCounter()
        scraper.tables = MagicMock()
        scraper.delivery = MagicMock()
        scraper.delivery.last_delivery_cancel_count = 0
        scraper.ensure_ready = AsyncMock(return_value=True)
        scraper.scrape_delivery_orders = AsyncMock(return_value=[])
        return scraper

    async def test_run_cycle_writes_round_api_failure_delta_not_cumulative(self):
        """健康状态里的 api_failures 是本轮增量，不是进程启动以来的累计值。

        CORR-03：`api_failures` 原先写的是累计值，现场因此看不出「本轮又失败了」。
        """
        scraper = self._build_scraper()
        db = MagicMock()
        db.save_table_data = AsyncMock()
        db.orders = MagicMock()
        db.orders.save_orders = AsyncMock()

        async def table_data_with_one_failure():
            # 本轮一次明细接口硬失败（被 run_cycle 吞掉，整轮照常结束）
            scraper.session.http.api_failures += 1
            return []

        scraper.scrape_table_data = AsyncMock(side_effect=table_data_with_one_failure)

        with patch(
            "services.realtime.hub.realtime_hub.broadcast_nudge", new_callable=AsyncMock
        ):
            await scraper.run_cycle(db)
            self.assertEqual(read_health()["api_failures"], 1, "第一轮增量应为 1")

            await scraper.run_cycle(db)
            self.assertEqual(
                read_health()["api_failures"], 1, "第二轮增量仍是 1，不得累加成 2"
            )

    async def test_run_cycle_fans_out_tables_orders_delivery(self):
        scraper = RestaurantScraper.__new__(RestaurantScraper)
        scraper.logger = MagicMock()
        scraper.dish_catalog = None
        scraper.session = MagicMock()
        scraper.session.consecutive_login_failures = 0
        scraper.session.http = MagicMock(api_failures=0)
        scraper.tables = MagicMock()
        scraper.delivery = MagicMock()
        scraper.delivery.last_delivery_cancel_count = 0

        scraper.ensure_ready = AsyncMock(return_value=True)
        scraper.scrape_table_data = AsyncMock(return_value=[{"table": "A1"}])
        scraper.monitor_table_orders = AsyncMock(
            return_value=[{"dish_name": "虾饺", "quantity": 1}]
        )
        scraper.scrape_delivery_orders = AsyncMock(return_value=[])

        db = MagicMock()
        db.save_table_data = AsyncMock()
        db.orders = MagicMock()
        db.orders.save_orders = AsyncMock()

        with patch(
            "services.realtime.hub.realtime_hub.broadcast_nudge", new_callable=AsyncMock
        ) as nudge, patch(
            "services.scraper_health.update_runtime_health"
        ) as health:
            await scraper.run_cycle(db)

        db.save_table_data.assert_awaited_once()
        db.orders.save_orders.assert_awaited_once()
        self.assertGreaterEqual(nudge.await_count, 2)
        health.assert_called_once()

    async def test_run_cycle_raises_when_login_failures_exceed_threshold(self):
        from config import settings
        from scraper._common import ScraperSessionError

        scraper = RestaurantScraper.__new__(RestaurantScraper)
        scraper.logger = MagicMock()
        scraper.session = MagicMock()
        scraper.session.consecutive_login_failures = (
            settings.SCRAPER_ALERT_FAILURE_THRESHOLD
        )
        scraper.ensure_ready = AsyncMock(return_value=False)

        with self.assertRaises(ScraperSessionError):
            await scraper.run_cycle(MagicMock())


if __name__ == "__main__":
    unittest.main()
