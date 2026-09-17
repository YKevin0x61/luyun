#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新作业的浏览器同步 adapter：解释器选择 + 失败不阻断更新。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.release_update.job_adapters import (
    PlaywrightBrowserSyncAdapter,
    resolve_deploy_python,
)


class ResolveDeployPythonTest(unittest.TestCase):
    def test_prefers_venv_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv_python = Path(tmp) / ".venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
            self.assertEqual(
                resolve_deploy_python(Path(tmp)), str(venv_python)
            )

    def test_falls_back_to_python3(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(resolve_deploy_python(Path(tmp)), "python3")

    def test_explicit_override_wins(self):
        self.assertEqual(
            resolve_deploy_python(Path("/nope"), "/custom/python"), "/custom/python"
        )


class PlaywrightBrowserSyncAdapterTest(unittest.TestCase):
    def test_success_path_targets_deploy_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = PlaywrightBrowserSyncAdapter(Path(tmp), timeout_seconds=10)
            with patch(
                "services.release_update.job_adapters.ensure_chromium_installed_sync",
                return_value=True,
            ) as install:
                adapter.sync()
            self.assertEqual(install.call_args.kwargs["python_bin"], "python3")
            self.assertEqual(install.call_args.kwargs["timeout_seconds"], 10)

    def test_failure_does_not_raise(self):
        """浏览器同步失败不能卡住更新：主服务启动时会自愈重试。"""
        with tempfile.TemporaryDirectory() as tmp:
            adapter = PlaywrightBrowserSyncAdapter(Path(tmp), timeout_seconds=10)
            with patch(
                "services.release_update.job_adapters.ensure_chromium_installed_sync",
                return_value=False,
            ):
                adapter.sync()


if __name__ == "__main__":
    unittest.main()
