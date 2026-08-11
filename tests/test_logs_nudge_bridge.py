#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""logs nudge 的 sync→async 桥接单测：`LogsNudgeScheduler.notify()`（同步，可能
来自任意线程）经 debounce 合并为有限次 `broadcast_nudge("logs")`，且广播期间
产生的新日志（重入）绝不会再触发新的调度——防止风暴/无限递归。

不启动完整 main.app lifespan：只用注入的 fake hub + 真实事件循环直测
`services.realtime.logs_bridge.LogsNudgeScheduler`（同 `tests/test_ws_hub.py`
`tests/test_ws_dashboard_debounce.py` 的注入式写法）。debounce 延时通过构造
参数注入极小值，避免脆弱的时序竞态。
"""

import asyncio
import threading
import unittest

from services.realtime.logs_bridge import LOGS_NUDGE_DEBOUNCE_SECONDS, LogsNudgeScheduler


class _ProbeScheduler(LogsNudgeScheduler):
    """测试专用子类：一旦 `arm()`，之后每次 `_pending` 被写为 `False`，都会
    同步记录当时的 `_broadcasting` 值，并立即触发一次 `notify()`——用来确定性
    地模拟"另一个 OS 线程恰好在 `_pending` 刚变为 False 的瞬间并发调用
    `notify()`"这一评审所述的跨线程窗口，而不依赖真实、不可靠的 GIL 时序竞态。

    `arm()` 之前（覆盖 `__init__` 内部自身对 `_pending` 的初始化赋值）不生效，
    避免构造期尚未就绪时触发。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._armed = False
        self.pending_false_broadcasting_snapshots = []

    def arm(self) -> None:
        self._armed = True

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name == "_pending" and value is False and getattr(self, "_armed", False):
            self.pending_false_broadcasting_snapshots.append(self._broadcasting)
            self.notify()


class _FakeHub:
    """fake hub：`broadcast_nudge` 记录调用次数，并可选地模拟"广播期间产生了
    新日志"（调用方传入的 `scheduler` 在广播时反过来调用一次 `notify()`）。"""

    def __init__(self):
        self.calls = []
        self.scheduler = None

    async def broadcast_nudge(self, topic, scope=None):
        self.calls.append((topic, scope or {}))
        if self.scheduler is not None:
            # 模拟广播路径内部再产生日志（如 hub `_send` 失败时的
            # `logger.debug`）触发 `emit()` → `notify()`。
            self.scheduler.notify()


class LogsNudgeSchedulerTest(unittest.IsolatedAsyncioTestCase):
    DEBOUNCE = 0.02

    async def asyncSetUp(self):
        self.hub = _FakeHub()
        self.scheduler = LogsNudgeScheduler(self.hub, debounce_seconds=self.DEBOUNCE)
        self.hub.scheduler = self.scheduler
        self.scheduler.bind_loop(asyncio.get_running_loop())

    async def _await_window(self, factor=4):
        await asyncio.sleep(self.DEBOUNCE * factor)

    async def test_default_debounce_constant_is_500ms(self):
        self.assertEqual(LOGS_NUDGE_DEBOUNCE_SECONDS, 0.5)

    async def test_multiple_notify_calls_collapse_to_one_broadcast(self):
        for _ in range(50):
            self.scheduler.notify()

        await self._await_window()

        self.assertEqual(len(self.hub.calls), 1)
        self.assertEqual(self.hub.calls[0], ("logs", {}))

    async def test_notify_without_bound_loop_is_safely_skipped(self):
        scheduler = LogsNudgeScheduler(self.hub, debounce_seconds=self.DEBOUNCE)

        scheduler.notify()  # 未绑定 loop（如启动早期），不应抛错
        await self._await_window()

        self.assertEqual(self.hub.calls, [])

    async def test_broadcast_triggered_logging_does_not_reschedule(self):
        """核心防递归断言：fake hub 在广播时反过来调用一次 notify()（模拟广播
        路径自身产生的新日志），不应导致第二次广播——否则就是自激/风暴。"""
        self.scheduler.notify()

        await self._await_window()

        self.assertEqual(len(self.hub.calls), 1)
        self.assertFalse(self.scheduler._broadcasting)
        self.assertFalse(self.scheduler._pending)

    async def test_notify_after_broadcast_completes_schedules_again(self):
        self.scheduler.notify()
        await self._await_window()
        self.assertEqual(len(self.hub.calls), 1)

        self.scheduler.notify()
        await self._await_window()
        self.assertEqual(len(self.hub.calls), 2)

    async def test_notify_from_worker_thread_is_bridged_via_call_soon_threadsafe(self):
        """`emit()` 可能在非事件循环线程被调用；notify() 必须能安全跨线程调度。"""
        thread = threading.Thread(target=self.scheduler.notify)
        thread.start()
        thread.join()

        await self._await_window()

        self.assertEqual(len(self.hub.calls), 1)

    async def test_sustained_reentrant_logging_never_grows_unbounded(self):
        """极端情况：让 fake hub 在广播期间反复调用 notify() 多次（模拟日志风暴），
        重入保护应确保总广播次数仍然有限（此处应恰为 1 次），而非随日志数量增长。"""

        class _StormHub(_FakeHub):
            async def broadcast_nudge(self, topic, scope=None):
                self.calls.append((topic, scope or {}))
                for _ in range(20):
                    self.scheduler.notify()

        storm_hub = _StormHub()
        scheduler = LogsNudgeScheduler(storm_hub, debounce_seconds=self.DEBOUNCE)
        storm_hub.scheduler = scheduler
        scheduler.bind_loop(asyncio.get_running_loop())

        scheduler.notify()
        await self._await_window()

        self.assertEqual(len(storm_hub.calls), 1)

    async def test_cross_thread_notify_at_pending_clear_moment_collapses_to_one_broadcast(self):
        """确定性复现评审所述的跨线程窗口：`_debounced_broadcast()` 在
        `await asyncio.sleep(...)` 之后，把 `_pending` 清为 `False` 的瞬间，
        用 `_ProbeScheduler` 同步触发一次"另一路径"的 `notify()`（模拟另一
        OS 线程在该确切时刻并发调用）。

        修复前：`_pending` 先被清空、`_broadcasting` 随后才置位，此刻
        `notify()` 会读到两者皆 `False`，误判为空闲并再排一次调度，一次突发
        产生 2 次广播。
        修复后：`_broadcasting` 已先于 `_pending` 被置位，`notify()` 在此刻
        必然读到 `_broadcasting=True` 而直接跳过——一次突发收敛为恰好 1 次
        广播，且不破坏"广播期间产生的日志不自激"的红线。
        """
        scheduler = _ProbeScheduler(self.hub, debounce_seconds=self.DEBOUNCE)
        self.hub.scheduler = scheduler
        scheduler.bind_loop(asyncio.get_running_loop())
        scheduler.arm()

        scheduler.notify()
        # 等待原始 debounce 窗口、"模拟并发 notify()"可能引发的第二次调度、
        # 以及所有广播都完整走完。
        await self._await_window(factor=12)

        # 核心断言：`_pending` 变为 False 的瞬间，`_broadcasting` 必须已经
        # 是 True——即不存在"两者皆 False"的窗口。
        self.assertTrue(
            scheduler.pending_false_broadcasting_snapshots,
            "探针未捕获到任何 _pending→False 的瞬间，测试未生效",
        )
        self.assertTrue(
            all(scheduler.pending_false_broadcasting_snapshots),
            "存在 _pending 与 _broadcasting 同时为 False 的窗口：" f"{scheduler.pending_false_broadcasting_snapshots}",
        )
        # 收敛断言：即便在该瞬间发生了并发 notify()，最终整个突发也只产生
        # 1 次广播（而不是 2 次）。
        self.assertEqual(len(self.hub.calls), 1)
        self.assertFalse(scheduler._broadcasting)
        self.assertFalse(scheduler._pending)

    async def test_debounce_task_reference_is_retained_and_cleared_after_completion(self):
        """修复 2：`_schedule()` 保存 `asyncio.create_task(...)` 的引用（对齐
        `hub.py` `_dashboard_debounce_task` 的做法），避免 asyncio 只持弱引用
        导致任务被提前 GC；任务完成后引用应被清空。"""
        self.assertIsNone(self.scheduler._debounce_task)

        self.scheduler.notify()
        await asyncio.sleep(0)  # 让 call_soon_threadsafe 排的 _schedule() 有机会跑一次
        self.assertIsNotNone(self.scheduler._debounce_task)
        self.assertFalse(self.scheduler._debounce_task.done())

        await self._await_window()

        self.assertEqual(len(self.hub.calls), 1)
        self.assertIsNone(self.scheduler._debounce_task)


if __name__ == "__main__":
    unittest.main()
