#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Update Preflight 的磁盘余量门禁。"""

import unittest

from services.release_update import (
    PREFLIGHT_DISK_SPACE,
    PreflightEnv,
    _build_preflight,
    _default_preflight_env,
)


def _env(**overrides) -> PreflightEnv:
    base = {
        "restart_ready": True,
        "credentials_ready": True,
        "dirty_tree": False,
    }
    base.update(overrides)
    return PreflightEnv(**base)


class PreflightDiskGateTest(unittest.TestCase):
    def test_low_disk_blocks_apply(self):
        preflight = _build_preflight(
            _env(disk_ok=False, disk_free_mb=120), job_idle=True
        )

        self.assertFalse(preflight.apply_allowed)
        self.assertFalse(preflight.discard_local_changes_allowed)
        check = next(c for c in preflight.checks if c.code == PREFLIGHT_DISK_SPACE)
        self.assertFalse(check.ok)
        self.assertIn("120", check.message)

    def test_enough_disk_allows_apply(self):
        preflight = _build_preflight(
            _env(disk_ok=True, disk_free_mb=8000), job_idle=True
        )

        self.assertTrue(preflight.apply_allowed)
        check = next(c for c in preflight.checks if c.code == PREFLIGHT_DISK_SPACE)
        self.assertTrue(check.ok)

    def test_unknown_disk_does_not_block(self):
        """读不到用量时不能挡住更新（探测失败不等于磁盘满）。"""
        preflight = _build_preflight(_env(disk_ok=True), job_idle=True)
        self.assertTrue(preflight.apply_allowed)

    def test_default_env_stays_backward_compatible(self):
        preflight = _build_preflight(_default_preflight_env(), job_idle=True)
        self.assertTrue(preflight.apply_allowed)


if __name__ == "__main__":
    unittest.main()
