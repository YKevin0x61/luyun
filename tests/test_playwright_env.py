#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Playwright 浏览器缺失判定、补装，以及 lib ↔ 浏览器 build 一致性守卫（票 10）。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.playwright_env import (
    DEGRADED_ALERT_PREFIX,
    DEGRADED_PLAYWRIGHT_BROWSER_MISSING,
    REASON_BROWSER_MISSING,
    REASON_CACHE_UNREADABLE,
    REASON_LIB_UNAVAILABLE,
    REASON_OK,
    STRICT_ENV,
    BrowserCacheStatus,
    PlaywrightBrowserDriftError,
    browser_cache_status,
    browser_directory_name,
    browsers_cache_dir,
    cached_browser_dirs,
    ensure_chromium_installed,
    ensure_chromium_installed_sync,
    expected_browser_builds,
    format_degraded_alert,
    is_browser_missing_error,
    repair_hint,
    strict_mode_enabled,
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


def _fake_package_dir(directory: Path, builds) -> Path:
    """造一个「已安装 playwright 包」的最小目录（只放 browsers.json）。"""
    package = Path(directory) / "playwright"
    target = package / "driver" / "package"
    target.mkdir(parents=True, exist_ok=True)
    (target / "browsers.json").write_text(
        json.dumps(
            {
                "browsers": [
                    {"name": name, "revision": revision} for name, revision in builds
                ]
            }
        ),
        encoding="utf-8",
    )
    return package


def _make_cache_dirs(cache_dir: Path, dirs) -> Path:
    for name in dirs:
        (cache_dir / name).mkdir(parents=True, exist_ok=True)
    return cache_dir


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


class ExpectedBrowserBuildsTest(unittest.TestCase):
    """期望 build 的**唯一事实来源**是 lib 自带的 browsers.json（票 10）。"""

    def test_reads_the_installed_lib_table(self):
        builds = expected_browser_builds()
        self.assertEqual(sorted(builds), ["chromium", "chromium-headless-shell"])
        self.assertTrue(all(str(rev).isdigit() for rev in builds.values()))

    def test_reads_a_given_package_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = _fake_package_dir(
                tmp, [("chromium", "1111"), ("chromium-headless-shell", "1111")]
            )
            builds = expected_browser_builds(package_dir=package)
        self.assertEqual(
            builds, {"chromium": "1111", "chromium-headless-shell": "1111"}
        )

    def test_accepts_driver_and_underscore_spellings(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = _fake_package_dir(
                tmp, [("chromium", "1111"), ("chromium-headless-shell", "1111")]
            )
            builds = expected_browser_builds(
                ("chromium", "chromium_headless_shell"), package_dir=package
            )
        self.assertEqual(builds["chromium-headless-shell"], "1111")

    def test_entry_missing_from_table_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = _fake_package_dir(tmp, [("chromium", "1111")])
            with self.assertRaises(ValueError):
                expected_browser_builds(package_dir=package)

    def test_absent_table_raises_file_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                expected_browser_builds(package_dir=Path(tmp))


class BrowserDirectoryNameTest(unittest.TestCase):
    def test_matches_driver_naming_rule(self):
        self.assertEqual(browser_directory_name("chromium", 1243), "chromium-1243")
        self.assertEqual(
            browser_directory_name("chromium-headless-shell", "1243"),
            "chromium_headless_shell-1243",
        )


class BrowsersCacheDirTest(unittest.TestCase):
    """缓存根解析与 playwright driver 的 computeDefaultCacheDirectory 对齐。"""

    def test_env_override_wins(self):
        self.assertEqual(
            browsers_cache_dir(env={"PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright"}),
            Path("/ms-playwright"),
        )

    def test_zero_means_local_browsers_beside_the_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                browsers_cache_dir(
                    package_dir=Path(tmp), env={"PLAYWRIGHT_BROWSERS_PATH": "0"}
                ),
                Path(tmp) / ".local-browsers",
            )

    def test_darwin_default(self):
        self.assertEqual(
            browsers_cache_dir(env={}, platform="darwin", home="/Users/op"),
            Path("/Users/op/Library/Caches/ms-playwright"),
        )

    def test_linux_default_prefers_xdg_cache_home(self):
        self.assertEqual(
            browsers_cache_dir(
                env={"XDG_CACHE_HOME": "/var/cache/op"},
                platform="linux",
                home="/home/op",
            ),
            Path("/var/cache/op/ms-playwright"),
        )
        self.assertEqual(
            browsers_cache_dir(env={}, platform="linux", home="/home/op"),
            Path("/home/op/.cache/ms-playwright"),
        )

    def test_windows_default_uses_localappdata(self):
        self.assertEqual(
            browsers_cache_dir(
                env={"LOCALAPPDATA": "/tmp/AppData/Local"},
                platform="win32",
                home="/tmp/home",
            ),
            Path("/tmp/AppData/Local/ms-playwright"),
        )


class BrowserCacheStatusTest(unittest.TestCase):
    def test_reports_the_build_the_lib_expects_but_the_cache_lacks(self):
        """票 10 的现场形状：lib 期望 1243，缓存只到 1234。"""
        with tempfile.TemporaryDirectory() as tmp:
            package = _fake_package_dir(
                tmp, [("chromium", "1243"), ("chromium-headless-shell", "1243")]
            )
            cache = _make_cache_dirs(
                Path(tmp) / "cache",
                ["chromium-1091", "chromium-1234", "chromium_headless_shell-1234"],
            )
            status = browser_cache_status(package_dir=package, cache_dir=cache)
        self.assertFalse(status.ok)
        self.assertEqual(status.reason, REASON_BROWSER_MISSING)
        self.assertEqual(
            status.missing, ("chromium-1243", "chromium_headless_shell-1243")
        )
        self.assertEqual(
            status.present, ("chromium-1091", "chromium-1234", "chromium_headless_shell-1234")
        )
        self.assertIn("browser_missing", status.describe())
        self.assertIn("chromium-1243", status.describe())
        self.assertEqual(status.as_alert()["degraded"], DEGRADED_PLAYWRIGHT_BROWSER_MISSING)

    def test_ok_when_every_expected_dir_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = _fake_package_dir(
                tmp, [("chromium", "1243"), ("chromium-headless-shell", "1243")]
            )
            cache = _make_cache_dirs(
                Path(tmp) / "cache",
                ["chromium-1243", "chromium_headless_shell-1243"],
            )
            status = browser_cache_status(package_dir=package, cache_dir=cache)
        self.assertTrue(status.ok)
        self.assertEqual(status.reason, REASON_OK)
        self.assertEqual(status.missing, ())
        self.assertIn("一致", status.describe())
        alert = status.as_alert()
        self.assertTrue(alert["ok"])
        self.assertIsNone(
            alert["degraded"],
            "健康机（ok=True）不该带降级标记 R-T6-01",
        )

    def test_empty_cache_dir_lists_everything_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = _fake_package_dir(
                tmp, [("chromium", "1243"), ("chromium-headless-shell", "1243")]
            )
            status = browser_cache_status(
                package_dir=package, cache_dir=Path(tmp) / "cache"
            )
        self.assertFalse(status.ok)
        self.assertEqual(status.reason, REASON_BROWSER_MISSING)
        self.assertEqual(status.present, ())
        self.assertEqual(
            status.missing, ("chromium-1243", "chromium_headless_shell-1243")
        )

    def test_lib_unavailable_when_the_table_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = browser_cache_status(
                package_dir=Path(tmp), cache_dir=Path(tmp) / "cache"
            )
        self.assertFalse(status.ok)
        self.assertEqual(status.reason, REASON_LIB_UNAVAILABLE)
        self.assertIn("browsers.json", status.detail)

    def test_cache_unreadable_when_a_file_blocks_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = _fake_package_dir(
                tmp, [("chromium", "1243"), ("chromium-headless-shell", "1243")]
            )
            blocker = Path(tmp) / "ms-playwright"
            blocker.write_text("not a directory", encoding="utf-8")
            status = browser_cache_status(package_dir=package, cache_dir=blocker)
        self.assertFalse(status.ok)
        self.assertEqual(status.reason, REASON_CACHE_UNREADABLE)
        self.assertEqual(status.missing, ())

    def test_installed_lib_against_the_real_cache_is_self_consistent(self):
        """不写死机器状态：只钉「结论与两个来源一致」。"""
        status = browser_cache_status()
        cache_dir = browsers_cache_dir()
        dirs = cached_browser_dirs(cache_dir) or ()
        self.assertEqual(status.cache_dir, str(cache_dir))
        self.assertEqual(set(status.missing), set(status.expected_dirs) - set(dirs))
        self.assertEqual(status.ok, not status.missing)


class DegradedAlertTest(unittest.TestCase):
    def _status(self) -> BrowserCacheStatus:
        return BrowserCacheStatus(
            ok=False,
            reason=REASON_BROWSER_MISSING,
            cache_dir="/ms-playwright",
            expected={"chromium": "1243", "chromium-headless-shell": "1243"},
            present=("chromium-1234",),
            missing=("chromium-1243", "chromium_headless_shell-1243"),
            detail="",
        )

    def test_alert_is_one_line_of_json_with_stable_fields(self):
        line = format_degraded_alert(
            self._status(), phase="playwright_browser_sync", python_bin="/d/python"
        )
        self.assertNotIn("\n", line)
        self.assertTrue(line.startswith(DEGRADED_ALERT_PREFIX + " "))
        payload = json.loads(line[len(DEGRADED_ALERT_PREFIX) + 1 :])
        self.assertEqual(payload["degraded"], DEGRADED_PLAYWRIGHT_BROWSER_MISSING)
        self.assertEqual(payload["reason"], REASON_BROWSER_MISSING)
        self.assertEqual(payload["cache_dir"], "/ms-playwright")
        self.assertEqual(
            payload["missing"], ["chromium-1243", "chromium_headless_shell-1243"]
        )
        self.assertEqual(
            payload["expected_dirs"], ["chromium-1243", "chromium_headless_shell-1243"]
        )
        self.assertEqual(payload["phase"], "playwright_browser_sync")
        self.assertEqual(payload["python_bin"], "/d/python")

    def test_repair_hint_names_the_interpreter(self):
        self.assertEqual(
            repair_hint("/srv/app/.venv/bin/python"),
            "/srv/app/.venv/bin/python -m playwright install chromium",
        )


class StrictModeTest(unittest.TestCase):
    def test_default_is_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(STRICT_ENV, None)
            self.assertFalse(strict_mode_enabled())

    def test_truthy_values_enable_it(self):
        for value in ("1", "true", "YES", "on"):
            with patch.dict(os.environ, {STRICT_ENV: value}):
                self.assertTrue(strict_mode_enabled(), value)

    def test_falsy_values_keep_it_off(self):
        for value in ("0", "no", "off", ""):
            with patch.dict(os.environ, {STRICT_ENV: value}):
                self.assertFalse(strict_mode_enabled(), value)


class GuardCliTest(unittest.TestCase):
    """命令行守卫：缺 build 时非零退出（bootstrap/CI/手工排查都能用）。"""

    def _run(self, browsers_path: Path) -> subprocess.CompletedProcess:
        repo_root = Path(__file__).resolve().parents[1]
        env = {
            **os.environ,
            "PLAYWRIGHT_BROWSERS_PATH": str(browsers_path),
            "DISABLE_BACKGROUND_TASKS": "true",
        }
        return subprocess.run(
            [sys.executable, "-m", "services.playwright_env", "--check", "--json"],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_exit_zero_when_the_cache_matches_the_lib(self):
        expected = expected_browser_builds()
        with tempfile.TemporaryDirectory() as tmp:
            _make_cache_dirs(
                Path(tmp),
                [browser_directory_name(name, rev) for name, rev in expected.items()],
            )
            proc = self._run(Path(tmp))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["reason"], REASON_OK)
        self.assertIsNone(
            payload["degraded"],
            "健康机的 --check --json 不该带降级标记（R-T6-01）",
        )

    def test_exit_nonzero_and_alert_when_the_build_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(Path(tmp))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], REASON_BROWSER_MISSING)
        self.assertEqual(payload["degraded"], DEGRADED_PLAYWRIGHT_BROWSER_MISSING)
        self.assertEqual(
            payload["missing"],
            sorted(
                browser_directory_name(name, rev)
                for name, rev in payload["expected"].items()
            ),
        )
        self.assertIn(
            "playwright install chromium", payload["repair"]
        )


class BrowserSyncAdapterAlertTest(unittest.TestCase):
    """更新作业里浏览器同步失败的**可见性**（票 10）。

    既有决策「不让一次网络抖动卡死整台店」保持不变：补装命令失败仍然不抛，但必须
    在更新作业日志里留下结构化降级告警（进而出现在 Admin「系统更新」面板）；
    「补装自称成功、缓存里仍然缺 build」这种确定性漂移可在严格模式下硬失败。
    """

    def _missing(self) -> BrowserCacheStatus:
        return BrowserCacheStatus(
            ok=False,
            reason=REASON_BROWSER_MISSING,
            cache_dir="/ms-playwright",
            expected={"chromium": "1243", "chromium-headless-shell": "1243"},
            present=("chromium-1234",),
            missing=("chromium-1243", "chromium_headless_shell-1243"),
            detail="",
        )

    def _ok(self) -> BrowserCacheStatus:
        return BrowserCacheStatus(
            ok=True,
            reason=REASON_OK,
            cache_dir="/ms-playwright",
            expected={"chromium": "1243", "chromium-headless-shell": "1243"},
            present=("chromium-1243", "chromium_headless_shell-1243"),
        )

    def _sync(self, tmp: str, *, install_ok: bool, status: BrowserCacheStatus, strict: bool):
        from services.release_update.job_adapters import PlaywrightBrowserSyncAdapter

        log_path = Path(tmp) / "update_job.log"
        adapter = PlaywrightBrowserSyncAdapter(
            Path(tmp), timeout_seconds=10, log_path=log_path, strict=strict
        )
        with patch(
            "services.release_update.job_adapters.ensure_chromium_installed_sync",
            return_value=install_ok,
        ), patch(
            "services.release_update.job_adapters.browser_cache_status",
            return_value=status,
        ):
            adapter.sync()
        return adapter, log_path

    def _alert_line(self, log_path: Path) -> dict:
        lines = [
            line
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.startswith(DEGRADED_ALERT_PREFIX)
        ]
        self.assertEqual(len(lines), 1, lines)
        return json.loads(lines[0][len(DEGRADED_ALERT_PREFIX) + 1 :])

    def test_install_failure_writes_degraded_alert_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter, log_path = self._sync(
                tmp, install_ok=False, status=self._missing(), strict=False
            )
            payload = self._alert_line(log_path)
        self.assertEqual(payload["degraded"], DEGRADED_PLAYWRIGHT_BROWSER_MISSING)
        self.assertFalse(payload["install_ok"])
        self.assertEqual(payload["phase"], "playwright_browser_sync")
        self.assertIn("playwright install chromium", payload["repair"])
        self.assertFalse(adapter.last_status.ok)

    def test_install_ok_but_build_still_missing_only_alerts_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter, log_path = self._sync(
                tmp, install_ok=True, status=self._missing(), strict=False
            )
            payload = self._alert_line(log_path)
        self.assertTrue(payload["install_ok"])
        self.assertFalse(payload["strict"])
        self.assertEqual(
            payload["missing"], ["chromium-1243", "chromium_headless_shell-1243"]
        )
        self.assertFalse(adapter.last_status.ok)

    def test_strict_mode_turns_a_deterministic_drift_into_a_hard_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlaywrightBrowserDriftError) as ctx:
                self._sync(tmp, install_ok=True, status=self._missing(), strict=True)
            payload = self._alert_line(Path(tmp) / "update_job.log")
        self.assertFalse(ctx.exception.status.ok)
        self.assertTrue(payload["strict"])

    def test_no_alert_when_the_cache_already_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter, log_path = self._sync(
                tmp, install_ok=True, status=self._ok(), strict=True
            )
            logged = log_path.exists()
        self.assertTrue(adapter.last_status.ok)
        self.assertFalse(logged)
        self.assertIsNone(
            adapter.last_status.as_alert()["degraded"],
            "健康机（ok=True）不该带降级标记 R-T6-01",
        )


if __name__ == "__main__":
    unittest.main()
