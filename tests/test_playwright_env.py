#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Playwright 浏览器缺失判定与补装。"""

import tempfile
import unittest
from pathlib import Path

from services.playwright_env import (
    ensure_chromium_installed,
    ensure_chromium_installed_sync,
    is_browser_missing_error,
)

# 现场原始报错（headless 启动走 chromium_headless_shell，不是完整 chromium）。
SITE_ERROR = (
    "BrowserType.launch: Executable doesn't exist at /ms-playwright/"
    "chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell"
)


def _fake_python(directory: Path, exit_code: int) -> str:
    path = directory / f"fake-python-{exit_code}"
    path.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


class BrowserMissingErrorTest(unittest.TestCase):
    def test_matches_site_error(self):
        self.assertTrue(is_browser_missing_error(Exception(SITE_ERROR)))

    def test_matches_sync_api_wording(self):
        self.assertTrue(
            is_browser_missing_error(
                Exception("Executable does not exist at /ms-playwright/x")
            )
        )

    def test_does_not_match_disk_full(self):
        self.assertFalse(
            is_browser_missing_error(Exception("database or disk is full"))
        )

    def test_does_not_match_unrelated_launch_failure(self):
        self.assertFalse(is_browser_missing_error(Exception("Target closed")))


class EnsureInstalledSyncTest(unittest.TestCase):
    def test_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = ensure_chromium_installed_sync(
                python_bin=_fake_python(Path(tmp), 0), timeout_seconds=30
            )
        self.assertTrue(ok)

    def test_nonzero_exit_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = ensure_chromium_installed_sync(
                python_bin=_fake_python(Path(tmp), 1), timeout_seconds=30
            )
        self.assertFalse(ok)

    def test_missing_interpreter_returns_false(self):
        ok = ensure_chromium_installed_sync(
            python_bin="/definitely/not/a/real/python", timeout_seconds=5
        )
        self.assertFalse(ok)


class EnsureInstalledAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = await ensure_chromium_installed(
                python_bin=_fake_python(Path(tmp), 0), timeout_seconds=30
            )
        self.assertTrue(ok)

    async def test_nonzero_exit_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = await ensure_chromium_installed(
                python_bin=_fake_python(Path(tmp), 2), timeout_seconds=30
            )
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
