#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scraper launch 自愈：浏览器缺失时补装一次并重试。"""

import unittest
from unittest.mock import AsyncMock, patch

from config import settings
from scraper.pos_session import PosSession

SITE_ERROR = (
    "BrowserType.launch: Executable doesn't exist at /ms-playwright/"
    "chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell"
)


class _FakeChromium:
    def __init__(self, errors):
        self._errors = list(errors)
        self.calls = 0

    async def launch(self, **kwargs):
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return "browser"


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


class _FakeLogger:
    def warning(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


def _session(chromium) -> PosSession:
    # 绕开 __init__：_launch_browser 只依赖 playwright / logger。
    session = object.__new__(PosSession)
    session.playwright = _FakePlaywright(chromium)
    session.logger = _FakeLogger()
    return session


class LaunchBrowserHealTest(unittest.IsolatedAsyncioTestCase):
    async def test_heals_once_after_missing_executable(self):
        chromium = _FakeChromium([Exception(SITE_ERROR)])
        session = _session(chromium)

        with patch.object(settings, "SCRAPER_BROWSER_AUTO_INSTALL", True), patch(
            "scraper.pos_session.ensure_chromium_installed",
            new=AsyncMock(return_value=True),
        ) as install:
            browser = await session._launch_browser(headless=True)

        self.assertEqual(browser, "browser")
        self.assertEqual(chromium.calls, 2, "must retry launch exactly once")
        install.assert_awaited_once()

    async def test_raises_when_install_fails(self):
        chromium = _FakeChromium([Exception(SITE_ERROR)])
        session = _session(chromium)

        with patch.object(settings, "SCRAPER_BROWSER_AUTO_INSTALL", True), patch(
            "scraper.pos_session.ensure_chromium_installed",
            new=AsyncMock(return_value=False),
        ):
            with self.assertRaises(Exception):
                await session._launch_browser(headless=True)

        self.assertEqual(chromium.calls, 1)

    async def test_unrelated_error_is_not_healed(self):
        chromium = _FakeChromium([Exception("Target closed")])
        session = _session(chromium)

        with patch.object(settings, "SCRAPER_BROWSER_AUTO_INSTALL", True), patch(
            "scraper.pos_session.ensure_chromium_installed",
            new=AsyncMock(return_value=True),
        ) as install:
            with self.assertRaises(Exception):
                await session._launch_browser(headless=True)

        install.assert_not_awaited()

    async def test_auto_install_can_be_disabled(self):
        chromium = _FakeChromium([Exception(SITE_ERROR)])
        session = _session(chromium)

        with patch.object(settings, "SCRAPER_BROWSER_AUTO_INSTALL", False), patch(
            "scraper.pos_session.ensure_chromium_installed",
            new=AsyncMock(return_value=True),
        ) as install:
            with self.assertRaises(Exception):
                await session._launch_browser(headless=True)

        install.assert_not_awaited()
        self.assertEqual(chromium.calls, 1)


if __name__ == "__main__":
    unittest.main()
