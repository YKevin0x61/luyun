#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dashboard debounce nudge 单测：orders/tables 在 debounce 窗口内的多次变更
合并为一次 `dashboard` nudge；窗口结束后再来一次变更会再触发一次。

不启动完整 main.app（无 lifespan/真实 POS/Playwright），只用最小 fake
websocket 直测 `services.realtime.hub.RealtimeHub`（同 `tests/test_ws_hub.py`）。
debounce 延时通过构造参数注入极小值，避免脆弱的时序竞态。
"""

import asyncio
import json
import unittest

from services.realtime.hub import DASHBOARD_DEBOUNCE_SECONDS, RealtimeHub


class _FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


async def _subscribe(hub, ws, sub_id, topics, filters=None):
    await hub.handle_message(
        ws,
        json.dumps({
            "action": "subscribe",
            "id": sub_id,
            "topics": topics,
            "filters": filters or {},
        }),
    )


def _dashboard_nudges(ws):
    return [m for m in ws.sent if m.get("type") == "nudge" and m.get("topic") == "dashboard"]


class DashboardDebounceTest(unittest.IsolatedAsyncioTestCase):
    DEBOUNCE = 0.02

    async def asyncSetUp(self):
        self.hub = RealtimeHub(dashboard_debounce_seconds=self.DEBOUNCE)
        self.ws = _FakeWebSocket()
        await self.hub.register(self.ws, "session")
        await _subscribe(self.hub, self.ws, "sub-dash", ["dashboard"])

    async def _await_window(self, factor=3):
        await asyncio.sleep(self.DEBOUNCE * factor)

    async def test_default_debounce_constant_is_300ms(self):
        self.assertEqual(DASHBOARD_DEBOUNCE_SECONDS, 0.3)

    async def test_multiple_orders_nudges_in_window_collapse_to_one_dashboard_nudge(self):
        await self.hub.broadcast_nudge("orders", {"station": "a"})
        await self.hub.broadcast_nudge("orders", {"station": "b"})
        await self.hub.broadcast_nudge("orders", {"station": "c"})

        await self._await_window()

        self.assertEqual(len(_dashboard_nudges(self.ws)), 1)

    async def test_tables_nudge_also_schedules_debounced_dashboard_nudge(self):
        await self.hub.broadcast_nudge("tables", {})

        await self._await_window()

        self.assertEqual(len(_dashboard_nudges(self.ws)), 1)

    async def test_mixed_orders_and_tables_nudges_collapse_to_one(self):
        await self.hub.broadcast_nudge("orders", {})
        await self.hub.broadcast_nudge("tables", {})
        await self.hub.broadcast_nudge("orders", {})

        await self._await_window()

        self.assertEqual(len(_dashboard_nudges(self.ws)), 1)

    async def test_change_after_window_triggers_another_dashboard_nudge(self):
        await self.hub.broadcast_nudge("orders", {})
        await self._await_window()
        self.assertEqual(len(_dashboard_nudges(self.ws)), 1)

        await self.hub.broadcast_nudge("orders", {})
        await self._await_window()
        self.assertEqual(len(_dashboard_nudges(self.ws)), 2)

    async def test_dashboard_nudge_itself_does_not_retrigger_debounce(self):
        """直接广播 dashboard nudge 不应再次安排 debounce（防递归）。"""
        await self.hub.broadcast_nudge("dashboard", {})

        await self._await_window()

        # 只有一开始那一次直接广播，没有额外由 debounce 触发的第二次。
        self.assertEqual(len(_dashboard_nudges(self.ws)), 1)
        self.assertIsNone(self.hub._dashboard_debounce_task)

    async def test_other_topics_do_not_schedule_dashboard_nudge(self):
        await self.hub.broadcast_nudge("scraper", {})
        await self.hub.broadcast_nudge("logs", {})
        await self.hub.broadcast_nudge("admin", {})

        await self._await_window()

        self.assertEqual(_dashboard_nudges(self.ws), [])


if __name__ == "__main__":
    unittest.main()
