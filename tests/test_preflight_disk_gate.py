#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Update Preflight 的磁盘余量门禁。"""

import unittest

from services.release_update import (
    PREFLIGHT_DISK_SPACE,
    PREFLIGHT_REDIS,
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


class PreflightRedisGateTest(unittest.TestCase):
    """Redis 是部署必需组件：**没配** REDIS_URL 拦更新，配了连不上只提示。

    没配就更新，等于把「应用起不来」的新代码推上去（ADR 0090 的硬切）；而配了但
    此刻连不上不该拦——应用会退避重连，一次抖动挡更新没有道理。
    """

    def _check(self, preflight):
        return next(c for c in preflight.checks if c.code == PREFLIGHT_REDIS)

    def test_missing_redis_url_blocks_apply(self):
        preflight = _build_preflight(
            _env(redis_configured=False, redis_detail="REDIS_URL 未配置：…"),
            job_idle=True,
        )

        self.assertFalse(preflight.apply_allowed)
        self.assertFalse(preflight.discard_local_changes_allowed)
        check = self._check(preflight)
        self.assertFalse(check.ok)
        self.assertIn("REDIS_URL", check.message)

    def test_configured_but_unreachable_only_warns(self):
        preflight = _build_preflight(
            _env(redis_configured=True, redis_reachable=False, redis_detail="Redis 不可达"),
            job_idle=True,
        )

        self.assertTrue(preflight.apply_allowed)
        check = self._check(preflight)
        self.assertFalse(check.ok)
        self.assertEqual(check.message, "Redis 不可达")

    def test_reachable_redis_is_green(self):
        preflight = _build_preflight(
            _env(redis_configured=True, redis_reachable=True), job_idle=True
        )

        self.assertTrue(preflight.apply_allowed)
        self.assertTrue(self._check(preflight).ok)

    def test_default_env_does_not_gate_on_redis(self):
        """默认值（未探测）不能让既有调用方突然被判红。"""
        preflight = _build_preflight(_default_preflight_env(), job_idle=True)
        self.assertTrue(preflight.apply_allowed)
        self.assertTrue(self._check(preflight).ok)


if __name__ == "__main__":
    unittest.main()
