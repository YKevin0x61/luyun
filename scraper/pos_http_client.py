#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PosHttpClient：POS form POST、header/cookie、会话失效恢复与失败计数。

浏览器登录 / page 生命周期仍由 PosSession 负责；本模块通过回调取当前 page 与 recover()。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from scraper._common import ScraperSessionError, pos_session_lost
from scraper.api_client import post_form_with_retry

GetPage = Callable[[], Any]
RecoverFn = Callable[[], Awaitable[bool]]
RequestFn = Callable[[], Awaitable[Tuple[int, Any]]]

_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)
_ORIGIN = "https://cy7mm.wuuxiang.com"


class PosHttpClient:
    def __init__(
        self,
        *,
        get_page: GetPage,
        recover: RecoverFn,
        timeout_ms: int,
        max_retries: int,
        retry_backoff_s: float,
        logger_: Optional[logging.Logger] = None,
    ):
        self._get_page = get_page
        self._recover = recover
        self._timeout_ms = timeout_ms
        self._max_retries = max_retries
        self._retry_backoff_s = retry_backoff_s
        self.logger = logger_ or logging.getLogger(__name__)
        self.api_failures = 0

    def _require_page(self):
        page = self._get_page()
        if page is None or page.is_closed():
            raise RuntimeError("浏览器页面未就绪")
        return page

    async def build_headers(
        self,
        *,
        referer: str = "",
        content_type: str = "application/x-www-form-urlencoded",
        user_agent: str = _DEFAULT_UA,
    ) -> Dict[str, str]:
        """Build common POS headers from the current page cookies."""
        page = self._require_page()
        cookies = await page.context.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": content_type,
            "Cookie": cookie_str,
            "Origin": _ORIGIN,
            "Referer": referer or "",
            "User-Agent": user_agent,
        }

    async def post_form(
        self,
        url: str,
        *,
        headers: dict,
        encoded_data: str,
        context_label: str,
    ):
        """POST form-urlencoded via the current page; bump api_failures on hard failure."""
        page = self._require_page()
        try:
            return await post_form_with_retry(
                page,
                url,
                headers=headers,
                data=encoded_data,
                timeout_ms=self._timeout_ms,
                max_retries=self._max_retries,
                retry_backoff_s=self._retry_backoff_s,
                context_label=context_label,
            )
        except Exception:
            self.api_failures += 1
            raise

    async def request_with_recovery(
        self,
        request_fn: RequestFn,
        *,
        context_label: str,
    ) -> Tuple[int, Any]:
        """Run request_fn; on session-lost, recover() once and retry.

        ``request_fn`` must be a zero-arg coroutine factory that re-reads the
        current page (do not close over a stale page object).
        """
        status, body = await request_fn()
        if not pos_session_lost(status, body):
            return status, body

        if not await self._recover():
            raise ScraperSessionError(f"{context_label}：会话失效且重新登录失败")

        self.logger.info("✅ 重新登录成功，重试 %s", context_label)
        return await request_fn()
