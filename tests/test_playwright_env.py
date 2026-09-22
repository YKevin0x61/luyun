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


class InstallLockIsLoopedTest(unittest.TestCase):
    """补装锁必须**每个事件循环一把**（DOC-09）。

    模块级 `asyncio.Lock()` 在**发生竞争**时绑定首个使用它的 loop，之后在别的 loop 上
    一竞争就抛 "is bound to a different event loop"。同一形状项目自己已经判过缺陷并
    改掉（`services/db_migrations.py` 的迁移应用锁把锁挂在 db 对象上、在 loop 内创建）。

    但**只做惰性创建是不够的**：锁一旦创建就常驻，第二个 loop 上照样撞同一句话（本用例
    的探针就是这条证据）。所以这里钉的是"按 loop 分桶"，不是"模块级变量改成 None"。

    用子进程：要造"两个 loop 上真的竞争"必须在两个独立的 `asyncio.run` 里跑，而进程内
    的锁可能已经被别的用例用过（那时它已经绑定了那个 loop）。子进程让这一条用例的结论
    只取决于当前代码。
    """

    def test_lock_is_per_loop_and_still_serializes_within_one_loop(self):
        import os
        import subprocess
        import sys

        repo_root = Path(__file__).resolve().parents[1]
        probe = (
            "import asyncio, sys\n"
            "sys.path.insert(0, %r)\n"
            "from services import playwright_env as pe\n"
            "assert not pe._install_locks, 'import 时就创建了锁'\n"
            "in_flight = 0\n"
            "peak = 0\n"
            "async def hit():\n"
            "    global in_flight, peak\n"
            "    async with pe._get_install_lock():\n"
            "        in_flight += 1\n"
            "        peak = max(peak, in_flight)\n"
            "        await asyncio.sleep(0)\n"
            "        in_flight -= 1\n"
            "async def main():\n"
            "    await asyncio.gather(*[hit() for _ in range(4)])\n"
            "asyncio.run(main())\n"
            "asyncio.run(main())\n"
            "assert peak == 1, '同一个 loop 内的并发调用没有被串行化: peak=%%d' %% peak\n"
            "print('OK')\n"
        ) % (str(repo_root),)
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(repo_root),
            env={**os.environ, "DISABLE_BACKGROUND_TASKS": "true"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
