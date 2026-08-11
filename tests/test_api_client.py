#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from scraper.api_client import post_form_with_retry


class _FakeResponse:
    def __init__(self, status: int):
        self.status = status


class ApiClientRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_on_first_attempt(self):
        page = MagicMock()
        ok = _FakeResponse(200)
        page.request.post = AsyncMock(return_value=ok)

        result = await post_form_with_retry(
            page,
            "http://example.test/api",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data="a=1",
            timeout_ms=1000,
            max_retries=3,
            retry_backoff_s=0.01,
            context_label="test",
        )
        self.assertIs(result, ok)
        self.assertEqual(page.request.post.await_count, 1)

    async def test_retries_then_succeeds(self):
        page = MagicMock()
        page.request.post = AsyncMock(
            side_effect=[
                PlaywrightTimeoutError("timeout"),
                _FakeResponse(500),
                _FakeResponse(200),
            ]
        )

        result = await post_form_with_retry(
            page,
            "http://example.test/api",
            headers={},
            data="",
            timeout_ms=1000,
            max_retries=3,
            retry_backoff_s=0.01,
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(page.request.post.await_count, 3)

    async def test_raises_after_max_retries(self):
        page = MagicMock()
        page.request.post = AsyncMock(side_effect=PlaywrightTimeoutError("timeout"))

        with self.assertRaises(PlaywrightTimeoutError):
            await post_form_with_retry(
                page,
                "http://example.test/api",
                headers={},
                data="",
                timeout_ms=1000,
                max_retries=2,
                retry_backoff_s=0.01,
            )
        self.assertEqual(page.request.post.await_count, 2)


if __name__ == "__main__":
    unittest.main()
