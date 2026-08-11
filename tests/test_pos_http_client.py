#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PosHttpClient header + failure-counter tests."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from scraper.pos_http_client import PosHttpClient


class PosHttpClientHeadersTest(unittest.IsolatedAsyncioTestCase):
    async def test_build_headers_joins_cookies_and_referer(self):
        page = MagicMock()
        page.is_closed.return_value = False
        page.context.cookies = AsyncMock(
            return_value=[
                {"name": "a", "value": "1"},
                {"name": "b", "value": "2"},
            ]
        )

        async def recover():
            return True

        client = PosHttpClient(
            get_page=lambda: page,
            recover=recover,
            timeout_ms=1000,
            max_retries=1,
            retry_backoff_s=0.01,
        )
        headers = await client.build_headers(referer="https://example.com/ref")
        self.assertEqual(headers["Cookie"], "a=1; b=2")
        self.assertEqual(headers["Referer"], "https://example.com/ref")
        self.assertEqual(headers["Content-Type"], "application/x-www-form-urlencoded")

    async def test_post_form_increments_api_failures_on_error(self):
        page = MagicMock()
        page.is_closed.return_value = False

        async def recover():
            return True

        client = PosHttpClient(
            get_page=lambda: page,
            recover=recover,
            timeout_ms=1000,
            max_retries=1,
            retry_backoff_s=0.01,
        )

        async def boom(*args, **kwargs):
            raise RuntimeError("network down")

        import scraper.pos_http_client as mod

        original = mod.post_form_with_retry
        mod.post_form_with_retry = boom
        try:
            with self.assertRaises(RuntimeError):
                await client.post_form(
                    "https://example.com",
                    headers={},
                    encoded_data="x=1",
                    context_label="probe",
                )
            self.assertEqual(client.api_failures, 1)
        finally:
            mod.post_form_with_retry = original


if __name__ == "__main__":
    unittest.main()
