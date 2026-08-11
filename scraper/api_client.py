#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Playwright API 请求重试封装。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)


async def post_form_with_retry(
    page,
    url: str,
    *,
    headers: dict,
    data: str,
    timeout_ms: int,
    max_retries: int,
    retry_backoff_s: float,
    context_label: str = "",
) -> Any:
    """POST form-urlencoded，失败时指数退避重试。"""
    last_error: Optional[Exception] = None
    label = context_label or url
    for attempt in range(1, max_retries + 1):
        try:
            response = await page.request.post(
                url,
                headers=headers,
                data=data,
                timeout=timeout_ms,
            )
            if response.status == 200:
                return response
            last_error = RuntimeError(f"HTTP {response.status}")
            logger.warning(
                "%s 请求失败 attempt=%s/%s status=%s",
                label,
                attempt,
                max_retries,
                response.status,
            )
        except PlaywrightTimeoutError as exc:
            last_error = exc
            logger.warning(
                "%s 超时 attempt=%s/%s timeout_ms=%s: %s",
                label,
                attempt,
                max_retries,
                timeout_ms,
                exc,
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "%s 异常 attempt=%s/%s: %s",
                label,
                attempt,
                max_retries,
                exc,
            )
        if attempt < max_retries:
            await asyncio.sleep(retry_backoff_s * (2 ** (attempt - 1)))
    raise last_error if last_error else RuntimeError(f"{label} 请求失败")
