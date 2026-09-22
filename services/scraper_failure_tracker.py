#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爬虫主循环连续失败计数与健康告警（不依赖 Playwright/网络，可独立单测）。

`run_restaurant_scraper()` 的主循环把每轮抓取委托给
`ScraperFailureTracker.run_once()`：异常时计数 +1、写入 `scraper_health`，
并按「次数门槛 + 最小间隔」两道门去抖后经企微通道推送一次健康告警；
成功一轮则清零计数。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Optional

from config import settings
from services.scraper_health import record_scraper_failure, record_scraper_success

logger = logging.getLogger(__name__)

AlertSender = Callable[[str], Awaitable[Any]]


def should_alert_scraper_failure(
    consecutive_failures: int,
    threshold: Optional[int] = None,
    *,
    last_alert_at: Optional[float] = None,
    now: Optional[float] = None,
    min_interval_seconds: Optional[int] = None,
) -> bool:
    """连续失败次数是否应触发一次企微告警。

    两道门：

    1. **次数门槛**（`SCRAPER_ALERT_FAILURE_THRESHOLD`，默认 3）：偶发一两轮失败不打扰，
       连续失败到这个数才开始告警；
    2. **最小间隔**（`SCRAPER_ALERT_MIN_INTERVAL_SECONDS`，默认 3600）：同一场故障里
       最多每小时一条。只按次数去抖是不够的——失败轮次间隔约 60s，阈值 3 会变成
       每 3 分钟一条，持续挂死一晚上就是几百条。

    `last_alert_at` / `now` 用同一时间基准（`time.monotonic()`），便于单测注入。
    """
    effective_threshold = settings.SCRAPER_ALERT_FAILURE_THRESHOLD if threshold is None else threshold
    if effective_threshold <= 0 or consecutive_failures < effective_threshold:
        return False
    if last_alert_at is None:
        return True
    interval = (
        settings.SCRAPER_ALERT_MIN_INTERVAL_SECONDS
        if min_interval_seconds is None
        else min_interval_seconds
    )
    if interval <= 0:
        return True
    current = time.monotonic() if now is None else now
    return (current - last_alert_at) >= interval


def build_scraper_failure_alert_message(consecutive_failures: int, error: str) -> str:
    return (
        f"【爬虫健康告警】连续失败 {consecutive_failures} 次\n"
        f"最近错误: {error}\n"
        f"请检查 POS 登录凭据 / 网络连通性 / Playwright 运行状态"
    )


class ScraperFailureTracker:
    """维护爬虫主循环的连续失败计数，写健康状态并按阈值去抖发送企微告警。

    调用方注入实际抓取逻辑（`scrape_fn`）与告警发送回调（`alert_sender`），
    因此单测可用抛异常的假抓取函数与假告警通道，无需真实 Playwright/网络。
    """

    def __init__(
        self,
        *,
        alert_sender: Optional[AlertSender] = None,
        threshold: Optional[int] = None,
        min_interval_seconds: Optional[int] = None,
    ) -> None:
        self._alert_sender = alert_sender
        self._threshold = settings.SCRAPER_ALERT_FAILURE_THRESHOLD if threshold is None else threshold
        self._min_interval_seconds = (
            settings.SCRAPER_ALERT_MIN_INTERVAL_SECONDS
            if min_interval_seconds is None
            else min_interval_seconds
        )
        self.consecutive_failures = 0
        # 上一次告警的时间（time.monotonic）；成功一轮会连同计数一起复位，
        # 这样「好了又坏」是新的一场故障，可以立刻再提醒一次。
        self.last_alert_at: Optional[float] = None

    async def run_once(self, scrape_fn: Callable[[], Awaitable[Any]]) -> Any:
        """执行一轮抓取。

        成功：清零连续失败计数并写入健康状态为 "ok"，返回 `scrape_fn` 的结果。
        异常：连续失败计数 +1，写入健康状态为 "error"，过了「次数门槛 + 最小间隔」
        才推送企微告警，随后重新抛出异常，交由调用方（主循环）保持原有的日志与
        sleep 重试逻辑。
        """
        try:
            result = await scrape_fn()
        except Exception as exc:
            self.consecutive_failures += 1
            record_scraper_failure(self.consecutive_failures, str(exc))
            if should_alert_scraper_failure(
                self.consecutive_failures,
                self._threshold,
                last_alert_at=self.last_alert_at,
                min_interval_seconds=self._min_interval_seconds,
            ):
                self.last_alert_at = time.monotonic()
                await self._send_alert(self.consecutive_failures, str(exc))
            raise
        else:
            self.consecutive_failures = 0
            self.last_alert_at = None
            record_scraper_success()
            return result

    async def _send_alert(self, consecutive_failures: int, error: str) -> None:
        if self._alert_sender is None:
            return
        message = build_scraper_failure_alert_message(consecutive_failures, error)
        try:
            await self._alert_sender(message)
        except Exception as exc:
            logger.error("发送爬虫健康告警失败: %s", exc)
