#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Update Job oneshot script contract tests (no live systemd / GitHub)."""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_JOB = REPO_ROOT / "scripts" / "run_update_job.py"
UNIT_FILE = REPO_ROOT / "deploy" / "luyun-update.service"


class UpdateJobContractTest(unittest.TestCase):
    def test_dry_run_contract_prints_bundle_stages_and_paths(self):
        env = os.environ.copy()
        completed = subprocess.run(
            ["python3", str(RUN_JOB), "--dry-run-contract"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env=env,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        out = completed.stdout
        self.assertIn("UPDATE_JOB_CONTRACT", out)
        self.assertIn("backup_before_swap=1", out)
        self.assertIn("preserve_data=1", out)
        self.assertIn("backing_up", out)
        self.assertIn("fetching_bundle", out)
        self.assertIn("installing", out)
        self.assertIn("syncing_deps", out)
        self.assertIn("restarting", out)
        self.assertIn("luyun-release-bundle.tar.gz", out)
        self.assertIn("SHA256SUMS", out)
        self.assertNotIn("admin-web-dist.tar.gz", out)
        self.assertNotIn("kds-dist.tar.gz", out)
        self.assertIn("update_job.json", out)
        self.assertIn("update_job.log", out)
        self.assertIn("out_of_process=1", out)

    def test_systemd_unit_is_oneshot_and_points_at_script(self):
        text = UNIT_FILE.read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", text)
        self.assertIn("scripts/run_update_job.py", text)
        self.assertIn("EnvironmentFile=", text)
        self.assertNotIn("npm", text.lower())
        self.assertNotIn("build_kds", text)

    def test_oneshot_starters_target_run_update_job_script(self):
        """Out-of-process starters must invoke scripts/run_update_job.py."""
        from services.release_update import oneshot as oneshot_mod

        script = oneshot_mod._update_job_script()
        self.assertEqual(script.resolve(), RUN_JOB.resolve())
        self.assertTrue(script.is_file())
        unit = UNIT_FILE.read_text(encoding="utf-8")
        self.assertIn(str(Path("scripts") / "run_update_job.py"), unit.replace("\\", "/"))
