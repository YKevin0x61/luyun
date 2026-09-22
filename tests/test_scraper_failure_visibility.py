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


class SoftFailingScrape:
    """模拟 `run_cycle` 把内部失败吞掉的那一轮：api_failures +1，但不抛异常。

    现场路径：餐桌明细 POST 硬失败 → `pos_http_client.post_form()` 计数 +1 并抛出 →
    `pos_session.fetch_table_orders()` 捕获后返回 None → 整轮照常跑完。
    """

    def __init__(self, counter: dict, result: str = "cycle-done"):
        self._counter = counter
        self._result = result

    async def __call__(self):
        self._counter["api_failures"] += 1
        return self._result


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

    async def test_round_with_api_failures_but_no_exception_is_not_recorded_as_success(self):
        """本轮有硬失败（api_failures 增量）但不抛异常时，不许记成成功那一轮。

        CORR-03：`run_cycle` 吞掉明细接口失败后照常返回，旧实现只看「有没有抛异常」，
        于是写 `status="ok"`、清零连续失败——看板全绿、企微告警不响。
        """
        settings.SCRAPER_ALERT_FAILURE_THRESHOLD = 3
        counter = {"api_failures": 0}
        tracker = ScraperFailureTracker(
            alert_sender=None,
            api_failures_fn=lambda: counter["api_failures"],
        )

        with self.assertRaises(RuntimeError):
            await tracker.run_once(FailingScrape())
        self.assertEqual(tracker.consecutive_failures, 1)

        result = await tracker.run_once(SoftFailingScrape(counter))

        self.assertEqual(result, "cycle-done")
        self.assertEqual(tracker.consecutive_failures, 2, "连续失败计数不许被清零")
        health = read_health()
        self.assertNotEqual(health["status"], "ok")
        self.assertEqual(health["consecutive_failures"], 2)
        self.assertIn("API 失败", health["last_error"])

    async def test_clean_round_records_success_even_with_nonzero_cumulative_failures(self):
        """干净一轮仍写 status=ok：判据是本轮增量，而不是进程累计值。

        `api_failures` 是进程启动以来的累计计数（PosHttpClient），早先几轮的失败
        不该把之后每一轮都判成失败。
        """
        counter = {"api_failures": 5}
        tracker = ScraperFailureTracker(
            alert_sender=None,
            api_failures_fn=lambda: counter["api_failures"],
        )

        async def ok_scrape():
            return "ok"

        result = await tracker.run_once(ok_scrape)

        self.assertEqual(result, "ok")
        self.assertEqual(tracker.consecutive_failures, 0)
        health = read_health()
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["consecutive_failures"], 0)

    async def test_soft_failure_rounds_reach_alert_threshold(self):
        """软失败轮同样把连续失败推到门槛并触发企微告警（CORR-03 的核心影响）。"""
        settings.SCRAPER_ALERT_FAILURE_THRESHOLD = 2
        counter = {"api_failures": 0}
        alert_sender = RecordingAlertSender()
        tracker = ScraperFailureTracker(
            alert_sender=alert_sender,
            api_failures_fn=lambda: counter["api_failures"],
            min_interval_seconds=0,
        )
        soft_failing_scrape = SoftFailingScrape(counter)

        await tracker.run_once(soft_failing_scrape)
        self.assertEqual(len(alert_sender.messages), 0, "未达阈值前不应告警")

        await tracker.run_once(soft_failing_scrape)
        self.assertEqual(len(alert_sender.messages), 1, "软失败达阈值也应告警一次")
        self.assertIn("2", alert_sender.messages[0])
        self.assertIn("API 失败", alert_sender.messages[0])

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

    async def test_alert_rate_limited_to_one_per_interval(self):
        """持续故障：过了阈值先告警一次，之后同一最小间隔内不再重复。

        失败轮次间隔约 60s，只按次数去抖（阈值 3）会变成每 3 分钟一条；这里锁住
        「每小时最多一条」这条产品约定。
        """
        settings.SCRAPER_ALERT_FAILURE_THRESHOLD = 3
        alert_sender = RecordingAlertSender()
        tracker = ScraperFailureTracker(alert_sender=alert_sender, min_interval_seconds=3600)
        failing_scrape = FailingScrape()

        for _ in range(10):
            with self.assertRaises(RuntimeError):
                await tracker.run_once(failing_scrape)

        self.assertEqual(len(alert_sender.messages), 1, "同一小时内最多一条告警")
        self.assertIsNotNone(tracker.last_alert_at)

    async def test_alert_resumes_after_interval_and_after_recovery(self):
        """间隔为 0（不限流）时每轮达标都提醒；成功一轮后计数与限流一并复位。"""
        settings.SCRAPER_ALERT_FAILURE_THRESHOLD = 1
        alert_sender = RecordingAlertSender()
        tracker = ScraperFailureTracker(alert_sender=alert_sender, min_interval_seconds=0)
        failing_scrape = FailingScrape()

        for _ in range(3):
            with self.assertRaises(RuntimeError):
                await tracker.run_once(failing_scrape)
        self.assertEqual(len(alert_sender.messages), 3)

        async def ok_scrape():
            return "ok"

        await tracker.run_once(ok_scrape)
        self.assertEqual(tracker.consecutive_failures, 0)
        self.assertIsNone(tracker.last_alert_at, "恢复后限流窗口应复位")

        with self.assertRaises(RuntimeError):
            await tracker.run_once(failing_scrape)
        self.assertEqual(len(alert_sender.messages), 4, "新一场故障可以立刻提醒")

    def test_should_alert_scraper_failure_threshold_and_interval(self):
        # 次数门槛：达阈值即可告警，之后次数继续增长不再受次数限制
        self.assertFalse(should_alert_scraper_failure(0, threshold=3))
        self.assertFalse(should_alert_scraper_failure(2, threshold=3))
        self.assertTrue(should_alert_scraper_failure(3, threshold=3))
        self.assertTrue(should_alert_scraper_failure(4, threshold=3))
        self.assertTrue(should_alert_scraper_failure(9, threshold=3))
        # 最小间隔：距上次告警不足一小时不再发，满一小时才发
        self.assertFalse(
            should_alert_scraper_failure(
                9, threshold=3, last_alert_at=1000.0, now=1060.0, min_interval_seconds=3600
            )
        )
        self.assertTrue(
            should_alert_scraper_failure(
                9, threshold=3, last_alert_at=1000.0, now=4600.0, min_interval_seconds=3600
            )
        )
        # 间隔 <= 0 表示不限流（测试与排障用）
        self.assertTrue(
            should_alert_scraper_failure(
                9, threshold=3, last_alert_at=1000.0, now=1000.1, min_interval_seconds=0
            )
        )

    def test_build_scraper_failure_alert_message_contains_key_info(self):
        message = build_scraper_failure_alert_message(5, "Timeout waiting for selector")
        self.assertIn("5", message)
        self.assertIn("Timeout waiting for selector", message)


if __name__ == "__main__":
    unittest.main()
