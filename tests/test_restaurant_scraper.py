#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RestaurantScraper composition-root orchestration tests."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from scraper.restaurant_scraper import RestaurantScraper


class RestaurantScraperCycleTests(unittest.IsolatedAsyncioTestCase):
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
