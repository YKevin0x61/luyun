#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scripts/start.py` 的解释器漂移守卫。

背景（`.scratch/project-review-2026-09-22` PERF-04）：开发实例用系统 `python3`
（3.9.6 / playwright 1.40.0 / redis 5.0.1）跑 `scripts/start.py`，而测试与生产是
仓库 `.venv`（3.11.15 / playwright 1.63.0 / redis 8.1.0）。在开发机上观测到的
三方库行为（Redis 重连、Playwright 行为）**不能**当生产证据。

守卫只提示、不阻断：裸 `python3 scripts/start.py` 仍照常起服务（既有习惯），
但在「解释器不在仓库 `.venv` 内」或「playwright 版本 != `requirements.txt`
钉死值」时打印醒目告警。本文件覆盖三段：

1. `requirements.txt` 钉死值解析（含读不到文件 / 未钉死时的降级：不抛异常）；
2. 解释器判定（`.venv/bin/python` 本身是 symlink，判定**不能**先把路径解析掉）；
3. 告警文案与"何时静默"（`.venv` 内且版本一致必须一声不响）。

seam：`scripts.start` 的模块级函数（下面 import 的名字）。测试不碰 uvicorn，
也不真的启动服务。
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import scripts.start as start_guard

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_FILE = REPO_ROOT / "requirements.txt"
VENV_DIR = REPO_ROOT / ".venv"
VENV_PYTHON = str(VENV_DIR / "bin" / "python")
# 系统解释器的典型路径（本项目开发机上就是它）。只用来构造"不在 .venv 内"的输入，
# 不要求它真的存在。
SYSTEM_PYTHON = "/Library/Developer/CommandLineTools/usr/bin/python3"


class ParsePinnedPlaywrightTest(unittest.TestCase):
    """`requirements.txt` 里 playwright 的钉死版本解析。"""

    def _write(self, tmpdir: str, text: str) -> Path:
        path = Path(tmpdir) / "requirements.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_reads_double_equals_pin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(
                tmpdir,
                "fastapi==0.104.1\nplaywright==1.63.0\nbeautifulsoup4==4.12.2\n",
            )
            self.assertEqual(start_guard.parse_pinned_playwright(path), "1.63.0")

    def test_ignores_comments_and_inline_comment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(
                tmpdir,
                "# playwright==9.9.9 只是注释里的例子\n"
                "playwright==1.63.0  # 钉死：lib 与 chromium build 强绑定\n",
            )
            self.assertEqual(start_guard.parse_pinned_playwright(path), "1.63.0")

    def test_other_packages_do_not_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, "playwright-stealth==1.0.0\nhttpx==0.25.2\n")
            self.assertIsNone(start_guard.parse_pinned_playwright(path))

    def test_unpinned_range_degrades_to_none(self):
        # 改成范围约束就没有"钉死值"可比对：降级为不判定，而不是猜一个版本。
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, "playwright>=1.40,<2\n")
            self.assertIsNone(start_guard.parse_pinned_playwright(path))

    def test_missing_file_degrades_to_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "nope.txt"
            self.assertIsNone(start_guard.parse_pinned_playwright(missing))

    def test_real_requirements_still_pins_a_version(self):
        # 不写死具体版本号：升级 playwright 时这条不该跟着改，只保证"仍然钉死着"。
        pinned = start_guard.parse_pinned_playwright(REQUIREMENTS_FILE)
        self.assertIsNotNone(pinned, "requirements.txt 里应有一行 playwright==<版本>")
        self.assertRegex(pinned, r"^\d+(\.\d+)+$")


class IsRepoVenvTest(unittest.TestCase):
    """`sys.executable` 是否落在仓库 `.venv` 内。"""

    def test_venv_python_counts_as_inside(self):
        self.assertTrue(start_guard.is_repo_venv(VENV_PYTHON, VENV_DIR))

    def test_system_python_is_outside(self):
        self.assertFalse(start_guard.is_repo_venv(SYSTEM_PYTHON, VENV_DIR))

    def test_symlinked_venv_python_still_counts_as_inside(self):
        # venv 的 bin/python 本身就是指向真实解释器的 symlink（macOS 与
        # uv 建的 venv 都是）。判定若先 resolve()，这里会误判成"不在 venv 内"。
        with tempfile.TemporaryDirectory() as tmpdir:
            venv = Path(tmpdir) / ".venv"
            (venv / "bin").mkdir(parents=True)
            link = venv / "bin" / "python"
            os.symlink(sys.executable, link)
            self.assertTrue(start_guard.is_repo_venv(str(link), venv))


class FormatInterpreterWarningTest(unittest.TestCase):
    """纯文案/判定函数：什么时候静默、什么时候告警、告警里写了什么。"""

    def _format(self, *, executable: str = SYSTEM_PYTHON, pinned="1.63.0", installed="1.40.0", redis="5.0.1"):
        return start_guard.format_interpreter_warning(
            executable=executable,
            venv_dir=VENV_DIR,
            python_version="3.9.6",
            pinned_playwright=pinned,
            installed_playwright=installed,
            installed_redis=redis,
        )

    def test_venv_with_matching_playwright_is_silent(self):
        self.assertIsNone(self._format(executable=VENV_PYTHON, installed="1.63.0"))

    def test_venv_with_unresolvable_pin_is_silent(self):
        # requirements 解析失败（pinned=None）时无法判定版本：不因此告警。
        self.assertIsNone(self._format(executable=VENV_PYTHON, pinned=None, installed="1.63.0"))

    def test_system_python_warns_even_when_versions_match(self):
        message = self._format(installed="1.63.0")
        self.assertIsNotNone(message)
        self.assertIn("改用 .venv/bin/python scripts/start.py", message)

    def test_system_python_warning_names_interpreter_and_versions(self):
        message = self._format()
        self.assertIn(SYSTEM_PYTHON, message)
        self.assertIn("3.9.6", message)
        self.assertIn("1.40.0", message)
        self.assertIn("1.63.0", message)
        self.assertIn("5.0.1", message)

    def test_venv_with_version_drift_warns(self):
        message = self._format(executable=VENV_PYTHON, installed="1.40.0")
        self.assertIsNotNone(message)
        self.assertIn("1.40.0", message)
        self.assertIn("1.63.0", message)
        self.assertIn("改用 .venv/bin/python scripts/start.py", message)

    def test_venv_with_missing_playwright_warns(self):
        message = self._format(executable=VENV_PYTHON, installed=None)
        self.assertIsNotNone(message)
        self.assertIn("未安装", message)


class InterpreterDriftWarningTest(unittest.TestCase):
    """真实取值入口：默认参数走 `sys.executable` + 仓库 `requirements.txt`。"""

    def test_matches_current_environment(self):
        # 自洽断言：任何解释器下都成立——在仓库 .venv 内且版本一致时必须静默，
        # 否则必须告警并给出改用指引（避免写死"本机当前跑在哪个解释器"）。
        message = start_guard.interpreter_drift_warning()
        inside = start_guard.is_repo_venv(
            sys.executable, start_guard.VENV_DIR
        )
        if inside:
            self.assertIsNone(message)
        else:
            self.assertIsNotNone(message)
            self.assertIn("改用 .venv/bin/python scripts/start.py", message)

    def test_warn_prints_only_when_drifting(self):
        stream = io.StringIO()
        with redirect_stderr(stream):
            warned = start_guard.warn_interpreter_drift()
        self.assertEqual(warned, start_guard.interpreter_drift_warning() is not None)
        if warned:
            self.assertIn("改用 .venv/bin/python scripts/start.py", stream.getvalue())
        else:
            self.assertEqual(stream.getvalue(), "", "venv 内必须静默，不能刷屏")

    def test_warn_is_silent_for_explicit_venv_state(self):
        stream = io.StringIO()
        with redirect_stderr(stream):
            warned = start_guard.warn_interpreter_drift(
                executable=VENV_PYTHON, installed_playwright="1.63.0"
            )
        self.assertFalse(warned)
        self.assertEqual(stream.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
