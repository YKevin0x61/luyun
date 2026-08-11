#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RealtimeHub 订阅/派发单测：不启动完整 main.app（避免加载真实 POS 凭据 +
启动 Playwright），只用最小 fake websocket 直接测 `services.realtime.hub.RealtimeHub`。
"""

import json
import unittest

from services.realtime.hub import RealtimeHub


class _FakeWebSocket:
    """最小 fake websocket：只提供 hub 需要的 send_json，记录收到的消息。"""

    def __init__(self, fail_send=False):
        self.sent = []
        self._fail_send = fail_send

    async def send_json(self, payload):
        if self._fail_send:
            raise RuntimeError("模拟推送失败：死连接")
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


class RealtimeHubTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = RealtimeHub()

    async def test_subscribe_with_filter_matches_scope(self):
        ws = _FakeWebSocket()
        await self.hub.register(ws, "session")
        await _subscribe(self.hub, ws, "sub-1", ["orders"], {"station": "A"})

        await self.hub.broadcast_nudge("orders", {"station": "A"})

        nudges = [m for m in ws.sent if m.get("type") == "nudge"]
        self.assertEqual(len(nudges), 1)
        self.assertEqual(nudges[0], {"type": "nudge", "topic": "orders", "scope": {"station": "A"}})

    async def test_subscribe_with_filter_does_not_match_other_scope(self):
        ws = _FakeWebSocket()
        await self.hub.register(ws, "session")
        await _subscribe(self.hub, ws, "sub-1", ["orders"], {"station": "A"})

        await self.hub.broadcast_nudge("orders", {"station": "B"})

        nudges = [m for m in ws.sent if m.get("type") == "nudge"]
        self.assertEqual(nudges, [])

    async def test_subscribe_without_filters_receives_any_scope(self):
        ws = _FakeWebSocket()
        await self.hub.register(ws, "session")
        await _subscribe(self.hub, ws, "sub-1", ["orders"])

        await self.hub.broadcast_nudge("orders", {"station": "A"})
        await self.hub.broadcast_nudge("orders", {"station": "B"})

        nudges = [m for m in ws.sent if m.get("type") == "nudge"]
        self.assertEqual(len(nudges), 2)

    async def test_orders_subscription_does_not_receive_tables_nudge(self):
        ws = _FakeWebSocket()
        await self.hub.register(ws, "session")
        await _subscribe(self.hub, ws, "sub-1", ["orders"])

        await self.hub.broadcast_nudge("tables", {})

        nudges = [m for m in ws.sent if m.get("type") == "nudge"]
        self.assertEqual(nudges, [])

    async def test_ping_returns_pong(self):
        ws = _FakeWebSocket()
        await self.hub.register(ws, "session")

        await self.hub.handle_message(ws, json.dumps({"action": "ping"}))

        self.assertEqual(ws.sent, [{"type": "pong"}])

    async def test_unsubscribe_stops_nudges(self):
        ws = _FakeWebSocket()
        await self.hub.register(ws, "session")
        await _subscribe(self.hub, ws, "sub-1", ["orders"])

        await self.hub.handle_message(ws, json.dumps({"action": "unsubscribe", "id": "sub-1"}))
        await self.hub.broadcast_nudge("orders", {})

        nudges = [m for m in ws.sent if m.get("type") == "nudge"]
        self.assertEqual(nudges, [])

    async def test_invalid_action_returns_error_and_keeps_connection(self):
        ws = _FakeWebSocket()
        await self.hub.register(ws, "session")

        await self.hub.handle_message(ws, json.dumps({"action": "not-a-real-action"}))

        self.assertEqual(len(ws.sent), 1)
        self.assertEqual(ws.sent[0]["type"], "error")
        self.assertIn(ws, self.hub._connections)

    async def test_invalid_json_returns_error_and_keeps_connection(self):
        ws = _FakeWebSocket()
        await self.hub.register(ws, "session")

        await self.hub.handle_message(ws, "not valid json{{{")

        self.assertEqual(len(ws.sent), 1)
        self.assertEqual(ws.sent[0]["type"], "error")
        self.assertIn(ws, self.hub._connections)

    async def test_invalid_topic_in_subscribe_returns_error(self):
        ws = _FakeWebSocket()
        await self.hub.register(ws, "session")

        await self.hub.handle_message(
            ws,
            json.dumps({"action": "subscribe", "id": "sub-1", "topics": ["not-a-topic"]}),
        )

        self.assertEqual(len(ws.sent), 1)
        self.assertEqual(ws.sent[0]["type"], "error")

    async def test_dead_connection_cleaned_up_during_broadcast(self):
        alive_ws = _FakeWebSocket()
        dead_ws = _FakeWebSocket(fail_send=True)
        await self.hub.register(alive_ws, "session")
        await self.hub.register(dead_ws, "session")
        await _subscribe(self.hub, alive_ws, "sub-1", ["orders"])
        await _subscribe(self.hub, dead_ws, "sub-1", ["orders"])

        await self.hub.broadcast_nudge("orders", {})

        nudges = [m for m in alive_ws.sent if m.get("type") == "nudge"]
        self.assertEqual(len(nudges), 1)
        self.assertNotIn(dead_ws, self.hub._connections)
        self.assertIn(alive_ws, self.hub._connections)


if __name__ == "__main__":
    unittest.main()
