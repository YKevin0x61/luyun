#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同步日志 handler → 异步 `logs` nudge 的安全桥接。

`logging.Handler.emit()` 是同步方法，可能在任意线程/无事件循环的上下文被调用
（例如启动早期，或非 asyncio 线程）。`LogsNudgeScheduler` 把"有新日志"这件事
安全地调度到已捕获的事件循环上，并用两道防线避免风暴/自激：

1. **debounce**：`notify()` 的多次调用在 `debounce_seconds` 窗口内合并为一次
   `broadcast_nudge("logs")`（写法仿照 `hub.py` 里 dashboard 的可注入延时 debounce）。
2. **重入保护**：广播 `logs` nudge 期间（`_broadcasting=True`）产生的新日志——
   包括 hub 内部 `_send` 失败时的 `logger.debug`——绝不会再触发新的调度，
   从根上掐断"广播 → 产生日志 → 又广播"的递归链路。
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 合并窗口：这段时间内的多次新日志只触发一次 logs nudge，避免日志密集时
# 对每条日志都触发一次广播（客户端收到后会拉取 `/api/logs/recent`）。
LOGS_NUDGE_DEBOUNCE_SECONDS = 0.5


class LogsNudgeScheduler:
    """把 `emit()` 里的"通知 logs 变更"请求桥接到事件循环，并做 debounce + 防递归。"""

    def __init__(self, hub, debounce_seconds: float = LOGS_NUDGE_DEBOUNCE_SECONDS):
        self._hub = hub
        self._debounce_seconds = debounce_seconds
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # 是否已有一次尚未触发的 debounce 定时器在排队。
        self._pending = False
        # 重入保护：True 表示正处于"调度回调已触发 → 正在广播"的窗口内，
        # 此时产生的新日志绝不再安排新的调度。
        self._broadcasting = False
        # 当前排队/运行中的 debounce 任务引用；仅用于防止 asyncio 只持
        # 弱引用导致的提前 GC（对齐 hub.py `_dashboard_debounce_task` 的
        # 做法），不参与 `_pending`/`_broadcasting` 的判定逻辑。
        self._debounce_task: Optional[asyncio.Task] = None

    def bind_loop(self, loop: Optional[asyncio.AbstractEventLoop]) -> None:
        """在 app 启动（lifespan）时调用，捕获运行中的事件循环引用；
        传入 `None` 可在关闭时解绑，避免持有已关闭的 loop。"""
        self._loop = loop

    def notify(self) -> None:
        """由 `emit()` 同步调用，可能来自任意线程。安全跳过（不抛错）：
        没有可用 loop、已有等待中的 debounce、或正在广播期间都直接返回。"""
        if self._loop is None or self._broadcasting or self._pending:
            return
        try:
            self._pending = True
            self._loop.call_soon_threadsafe(self._schedule)
        except Exception:
            self._pending = False

    def _schedule(self) -> None:
        """运行在事件循环线程上：创建 debounce 定时任务并保存引用（asyncio
        对任务只持弱引用，不保存引用可能被提前 GC；写法对齐 `hub.py`
        `schedule_dashboard_nudge` 保存 `_dashboard_debounce_task` 的做法）。"""
        try:
            self._debounce_task = asyncio.create_task(self._debounced_broadcast())
        except Exception:
            self._pending = False

    async def _debounced_broadcast(self) -> None:
        try:
            try:
                await asyncio.sleep(self._debounce_seconds)
            except BaseException:
                # sleep 期间被取消/异常：尚未进入广播阶段，直接复位
                # `_pending` 即可（不存在"广播期间自激"的风险）。
                self._pending = False
                raise
            # 关键顺序：先置位 `_broadcasting`，再清 `_pending`。两者之间
            # 没有 `await`，但跨 OS 线程调用的 `notify()` 仍可能在这两条
            # 语句之间被 GIL 调度执行——若先清 `_pending` 再置位
            # `_broadcasting`（旧写法），会出现"两者皆 False"的窗口，
            # 使并发 `notify()` 误判为空闲并再排一次调度，导致一次突发
            # 对应 2 次广播。调换顺序后，`notify()` 在这两条语句之间读到
            # 的必然是 `_broadcasting=True`（`_pending` 是否已清无关紧要，
            # 因为 `_broadcasting` 已经能挡住它），彻底消除该窗口。
            self._broadcasting = True
            self._pending = False
            try:
                await self._hub.broadcast_nudge("logs")
            except Exception:
                logger.debug("logs nudge 广播失败，已忽略", exc_info=True)
            finally:
                self._broadcasting = False
        finally:
            self._debounce_task = None
