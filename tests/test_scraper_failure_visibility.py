#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tempfile
import unittest

from config import settings
from services.scraper_failure_tracker import (
    ScraperFailureTracker,
    build_scraper_failure_alert_message,
    should_alert_scraper_failure,
)
from services.scraper_health import read_health


class FailingScrape:
    """模拟持续抛异常的假抓取函数，不触碰 Playwright/网络。"""

    def __init__(self, message_prefix: str = "boom"):
        self.calls = 0
        self._message_prefix = message_prefix

    async def __call__(self):
        self.calls += 1
        raise RuntimeError(f"{self._message_prefix}-{self.calls}")


class RecordingAlertSender:
    """记录已发送的告警文本，代替真实企微 webhook 通道。"""

    def __init__(self):
        self.messages = []

    async def __call__(self, message: str) -> None:
        self.messages.append(message)


class ScraperFailureTrackerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_dir = settings.DATABASE_DIR
        settings.DATABASE_DIR = self._tmpdir.name
        self._old_threshold = settings.SCRAPER_ALERT_FAILURE_THRESHOLD

    def tearDown(self):
        settings.DATABASE_DIR = self._old_dir
        settings.SCRAPER_ALERT_FAILURE_THRESHOLD = self._old_threshold
        self._tmpdir.cleanup()

    async def test_consecutive_failures_written_to_health_as_error(self):
        threshold = 3
        settings.SCRAPER_ALERT_FAILURE_THRESHOLD = threshold
        tracker = ScraperFailureTracker(alert_sender=RecordingAlertSender())
        failing_scrape = FailingScrape()

        for _ in range(threshold):
            with self.assertRaises(RuntimeError):
                await tracker.run_once(failing_scrape)

        health = read_health()
        self.assertGreaterEqual(health["consecutive_failures"], threshold)
        self.assertEqual(health["status"], "error")
        self.assertEqual(tracker.consecutive_failures, threshold)

    async def test_success_after_failures_resets_count_and_status(self):
        settings.SCRAPER_ALERT_FAILURE_THRESHOLD = 3
        tracker = ScraperFailureTracker(alert_sender=None)
        failing_scrape = FailingScrape()

        for _ in range(2):
            with self.assertRaises(RuntimeError):
                await tracker.run_once(failing_scrape)
        self.assertEqual(tracker.consecutive_failures, 2)

        async def ok_scrape():
            return "ok"

        result = await tracker.run_once(ok_scrape)
        self.assertEqual(result, "ok")
        self.assertEqual(tracker.consecutive_failures, 0)

        health = read_health()
        self.assertEqual(health["consecutive_failures"], 0)
        self.assertEqual(health["status"], "ok")

    async def test_alert_sent_exactly_once_when_first_crossing_threshold(self):
        threshold = 3
        settings.SCRAPER_ALERT_FAILURE_THRESHOLD = threshold
        alert_sender = RecordingAlertSender()
        tracker = ScraperFailureTracker(alert_sender=alert_sender)
        failing_scrape = FailingScrape()

        for _ in range(threshold - 1):
            with self.assertRaises(RuntimeError):
                await tracker.run_once(failing_scrape)
        self.assertEqual(len(alert_sender.messages), 0, "未达阈值前不应告警")

        with self.assertRaises(RuntimeError):
            await tracker.run_once(failing_scrape)
        self.assertEqual(len(alert_sender.messages), 1, "刚跨过阈值应告警一次")

    async def test_alert_debounced_and_repeats_every_threshold_multiple(self):
        threshold = 2
        settings.SCRAPER_ALERT_FAILURE_THRESHOLD = threshold
        alert_sender = RecordingAlertSender()
        tracker = ScraperFailureTracker(alert_sender=alert_sender)
        failing_scrape = FailingScrape()

        for _ in range(threshold * 2):
            with self.assertRaises(RuntimeError):
                await tracker.run_once(failing_scrape)

        # 阈值=2：第2次、第4次失败各告警一次，中间不刷屏
        self.assertEqual(len(alert_sender.messages), 2)

    def test_should_alert_scraper_failure_threshold_boundaries(self):
        self.assertFalse(should_alert_scraper_failure(0, threshold=3))
        self.assertFalse(should_alert_scraper_failure(2, threshold=3))
        self.assertTrue(should_alert_scraper_failure(3, threshold=3))
        self.assertFalse(should_alert_scraper_failure(4, threshold=3))
        self.assertFalse(should_alert_scraper_failure(5, threshold=3))
        self.assertTrue(should_alert_scraper_failure(6, threshold=3))

    def test_build_scraper_failure_alert_message_contains_key_info(self):
        message = build_scraper_failure_alert_message(5, "Timeout waiting for selector")
        self.assertIn("5", message)
        self.assertIn("Timeout waiting for selector", message)


if __name__ == "__main__":
    unittest.main()
