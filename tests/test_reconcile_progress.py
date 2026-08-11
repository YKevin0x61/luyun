#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对账进度状态（内存态，供前端轮询/nudge 展示）的测试。"""

import unittest

import services.reconcile_job as reconcile_job
from services.reconcile_job import execute_reconcile, get_reconcile_progress, is_reconcile_running


class _StubUninitializedAdapter:
    """仅用于驱动 execute_reconcile 的早退分支（爬虫未初始化）。"""

    async def ensure_ready(self, *, ignore_pause=False):
        return False


class SetStageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 每个用例前重置内存态，避免用例间串状态（全局单例）。
        reconcile_job._reconcile_running = False
        await reconcile_job._set_stage("2026-07-20", "matching_bills", current=2, total=5)

    async def test_set_stage_updates_progress_snapshot(self):
        progress = get_reconcile_progress()
        self.assertEqual(progress["biz_date"], "2026-07-20")
        self.assertEqual(progress["stage"], "matching_bills")
        self.assertEqual(progress["stage_label"], "逐单核对")
        self.assertEqual(progress["current"], 2)
        self.assertEqual(progress["total"], 5)
        self.assertTrue(progress["running"])
        self.assertIsNone(progress["error"])

    async def test_get_reconcile_progress_returns_copy(self):
        progress = get_reconcile_progress()
        progress["stage"] = "tampered"
        self.assertNotEqual(get_reconcile_progress()["stage"], "tampered")


class ExecuteReconcileErrorStageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reconcile_job._reconcile_running = False

    async def test_uninitialized_adapter_sets_error_stage(self):
        result = await execute_reconcile(None, _StubUninitializedAdapter(), "2026-07-20")

        self.assertFalse(result["success"])
        self.assertIn("爬虫未初始化", result["error"])

        progress = get_reconcile_progress()
        self.assertEqual(progress["stage"], "error")
        self.assertFalse(progress["running"])
        self.assertIn("爬虫未初始化", progress["error"])
        # finally 分支必须复位运行标志，否则后续对账永远返回「正在运行中」。
        self.assertFalse(is_reconcile_running())

    async def test_already_running_short_circuits_without_touching_progress(self):
        reconcile_job._reconcile_running = True
        try:
            result = await execute_reconcile(None, _StubUninitializedAdapter(), "2026-07-20")
        finally:
            reconcile_job._reconcile_running = False

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "对账任务正在运行中")


if __name__ == "__main__":
    unittest.main()
