#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爬虫主循环连续失败计数与健康告警（不依赖 Playwright/网络，可独立单测）。

`run_restaurant_scraper()` 的主循环把每轮抓取委托给
`ScraperFailureTracker.run_once()`：异常时计数 +1、写入 `scraper_health`，
并按阈值去抖经企微通道推送一次健康告警；成功一轮则清零计数。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from config import settings
from services.scraper_health import record_scraper_failure, record_scraper_success

logger = logging.getLogger(__name__)

AlertSender = Callable[[str], Awaitable[Any]]


def should_alert_scraper_failure(consecutive_failures: int, threshold: Optional[int] = None) -> bool:
    """连续失败次数是否应触发一次企微告警。

    去抖策略：仅在 `consecutive_failures` 恰好等于阈值的整数倍时触发——
    即刚跨过阈值时告警一次，此后每再累计满一个阈值的失败次数才再告警一次，
    避免在持续故障期间每轮（默认 5~20 秒一次）都刷屏，
    同时仍能在长时间挂死时周期性提醒，而不是只提醒一次后彻底沉默。
    """
    effective_threshold = settings.SCRAPER_ALERT_FAILURE_THRESHOLD if threshold is None else threshold
    if effective_threshold <= 0 or consecutive_failures < effective_threshold:
        return False
    return consecutive_failures % effective_threshold == 0


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
    ) -> None:
        self._alert_sender = alert_sender
        self._threshold = settings.SCRAPER_ALERT_FAILURE_THRESHOLD if threshold is None else threshold
        self.consecutive_failures = 0

    async def run_once(self, scrape_fn: Callable[[], Awaitable[Any]]) -> Any:
        """执行一轮抓取。

        成功：清零连续失败计数并写入健康状态为 "ok"，返回 `scrape_fn` 的结果。
        异常：连续失败计数 +1，写入健康状态为 "error"，达到告警阈值时推送企微告警，
        随后重新抛出异常，交由调用方（主循环）保持原有的日志与 sleep 重试逻辑。
        """
        try:
            result = await scrape_fn()
        except Exception as exc:
            self.consecutive_failures += 1
            record_scraper_failure(self.consecutive_failures, str(exc))
            if should_alert_scraper_failure(self.consecutive_failures, self._threshold):
                await self._send_alert(self.consecutive_failures, str(exc))
            raise
        else:
            self.consecutive_failures = 0
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
