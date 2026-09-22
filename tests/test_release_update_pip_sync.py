#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PipDepsSyncAdapter — stream log + cooperative cancel."""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from services.release_update.job_adapters import PipDepsSyncAdapter


class PipDepsSyncAdapterTest(unittest.TestCase):
    def test_streams_pip_output_to_log_and_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("unused\n", encoding="utf-8")
            log_path = root / "update_job.log"
            wrapper = root / "pywrap"
            wrapper.write_text(
                textwrap.dedent(
                    f"""\
                    #!{sys.executable}
                    print("Collecting demo==1.0")
                    print("Successfully installed demo-1.0")
                    """
                ),
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            adapter = PipDepsSyncAdapter(
                root,
                python_bin=str(wrapper),
                is_cancelled=lambda: False,
                log_path=log_path,
                timeout_seconds=30,
            )
            adapter.sync()
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("--- pip sync ---", text)
            self.assertIn("Collecting demo==1.0", text)
            self.assertIn("Successfully installed demo-1.0", text)

    def test_cancel_during_hanging_pip_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("unused\n", encoding="utf-8")
            log_path = root / "update_job.log"
            wrapper = root / "pywrap"
            wrapper.write_text(
                textwrap.dedent(
                    f"""\
                    #!{sys.executable}
                    import time
                    print("starting slow install", flush=True)
                    time.sleep(60)
                    """
                ),
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            cancelled = {"n": 0}

            def is_cancelled() -> bool:
                # 等子进程真把那一行打出来再取消：只看调用次数的话，机器一忙
                # 取消会抢在 fake pip 输出之前，日志里就没有 "starting slow install"
                # 这行，断言会变成偶发假红（实测本机跑全量时踩到过）。
                cancelled["n"] += 1
                try:
                    if "starting slow install" in log_path.read_text(encoding="utf-8"):
                        return True
                except OSError:
                    pass
                return cancelled["n"] >= 50  # 兜底：万一输出一直没来也别拖到超时

            adapter = PipDepsSyncAdapter(
                root,
                python_bin=str(wrapper),
                is_cancelled=is_cancelled,
                log_path=log_path,
                timeout_seconds=30,
            )
            with self.assertRaises(RuntimeError) as ctx:
                adapter.sync()
            self.assertIn("cancelled by operator", str(ctx.exception))
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("starting slow install", text)
            self.assertIn("cancel requested", text)
