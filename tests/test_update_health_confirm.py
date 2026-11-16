#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新健康确认（P0-3）：服务端自己把「重启中」推到终态。

缺陷背景：更新作业写出 restarting 后就退出了，而健康的最终结论原来只在
`GET /api/release-update/job` 的轮询里推进——操作人确认更新后一关页面，作业就
永远停在「重启中」，回滚入口与下一次应用更新都被锁住。`run_update_health_confirm()`
把这一步搬到启动后的常驻任务里。

这里钉三件事：
1. 启动时作业不在 restarting → 什么都不做（不写状态、不 sleep）；
2. 在 restarting → 调一次确认，落终态即返回；
3. 宽限期用尽仍未落定 → 用 `force=True` 再确认一次，确保不会留下永久重启中。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch


class _FakeReleaseUpdate:
    """只实现确认流程用到的两个方法，不碰真实作业文件与发行包。"""

    def __init__(self, *, stage: str, confirm_stages: list[str]):
        self.stage = stage
        self._confirm_stages = list(confirm_stages)
        self.confirm_calls: list[bool] = []

    def job_status(self):
        return SimpleNamespace(stage=self.stage)

    async def confirm_health(self, *, force: bool = False):
        self.confirm_calls.append(force)
        stage = (
            self._confirm_stages.pop(0)
            if len(self._confirm_stages) > 1
            else self._confirm_stages[0]
        )
        return SimpleNamespace(
            stage=stage,
            message="ok",
            health_detail="db / migrations / tables ready",
        )


class UpdateHealthConfirmTest(unittest.IsolatedAsyncioTestCase):
    async def test_noop_when_job_is_not_restarting(self):
        import main as main_module
        from services.release_update import factory as factory_module

        fake = _FakeReleaseUpdate(stage="succeeded", confirm_stages=["succeeded"])
        with patch.object(factory_module, "build_release_update", return_value=fake):
            await main_module.run_update_health_confirm()

        self.assertEqual(fake.confirm_calls, [])

    async def test_returns_once_terminal_stage_is_reached(self):
        import main as main_module
        from services.release_update import factory as factory_module

        fake = _FakeReleaseUpdate(
            stage="restarting", confirm_stages=["succeeded"]
        )
        with patch.object(factory_module, "build_release_update", return_value=fake):
            await main_module.run_update_health_confirm()

        self.assertEqual(fake.confirm_calls, [False])

    async def test_forced_final_confirmation_unlocks_stuck_restarting(self):
        """宽限期用尽时不能只记一条日志就走：作业必须被推到终态。"""
        import main as main_module
        from services.release_update import factory as factory_module

        fake = _FakeReleaseUpdate(
            stage="restarting",
            confirm_stages=["restarting", "succeeded_but_unhealthy"],
        )
        with (
            patch.object(factory_module, "build_release_update", return_value=fake),
            patch.object(main_module, "UPDATE_HEALTH_CONFIRM_TIMEOUT_SECONDS", 0.0),
            patch.object(main_module, "UPDATE_HEALTH_CONFIRM_POLL_SECONDS", 0.0),
        ):
            await main_module.run_update_health_confirm()

        self.assertEqual(fake.confirm_calls, [False, True])
