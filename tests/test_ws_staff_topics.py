#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WS 订阅按鉴权方式分级：员工（staff）只能订卫生主题。

nudge 本身不带数据，但 orders/tables/logs/admin 的**时序**就是门店经营信息
（几点来了几单、什么时候在改档口），员工端没有任何页面需要它们。
"""

import json
import unittest

from services.realtime.hub import STAFF_ALLOWED_TOPICS, RealtimeHub, allowed_topics
from tests.test_ws_hub import _FakeWebSocket, _subscribe


class StaffTopicScopeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = RealtimeHub()

    async def test_staff_may_subscribe_to_hygiene(self):
        ws = _FakeWebSocket()
        await self.hub.register(ws, "staff")

        await _subscribe(self.hub, ws, "sub-1", ["hygiene"])

        self.assertEqual(ws.sent[-1], {"type": "subscribed", "id": "sub-1"})
        await self.hub.broadcast_nudge("hygiene", {"resource": "daily"})
        self.assertEqual(len([m for m in ws.sent if m.get("type") == "nudge"]), 1)

    async def test_staff_cannot_subscribe_to_business_topics(self):
        for topic in ("orders", "tables", "logs", "admin", "scraper", "dashboard"):
            ws = _FakeWebSocket()
            await self.hub.register(ws, "staff")

            await _subscribe(self.hub, ws, "sub-1", [topic])

            self.assertEqual(ws.sent[-1]["type"], "error", topic)
            self.assertEqual(ws.sent[-1]["code"], "INVALID_SUBSCRIBE", topic)
            # 拒绝之后不能留下任何订阅。
            self.assertFalse(self.hub._connections[ws].has_topic(topic))
            await self.hub.broadcast_nudge(topic, {})
            self.assertEqual([m for m in ws.sent if m.get("type") == "nudge"], [], topic)

    async def test_staff_mixed_topics_are_rejected_whole(self):
        """一条订阅里混了越权 topic：整条拒绝，不能只放行 hygiene。"""
        ws = _FakeWebSocket()
        await self.hub.register(ws, "staff")

        await _subscribe(self.hub, ws, "sub-1", ["hygiene", "orders"])

        self.assertEqual(ws.sent[-1]["type"], "error")
        self.assertFalse(self.hub._connections[ws].has_topic("hygiene"))

    async def test_session_and_api_token_keep_full_topic_set(self):
        for auth in ("session", "api_token"):
            ws = _FakeWebSocket()
            await self.hub.register(ws, auth)

            await _subscribe(self.hub, ws, "sub-1", ["orders", "logs"])

            self.assertEqual(ws.sent[-1], {"type": "subscribed", "id": "sub-1"}, auth)
            await self.hub.broadcast_nudge("orders", {})
            self.assertEqual(
                len([m for m in ws.sent if m.get("type") == "nudge"]), 1, auth
            )

    async def test_allowed_topics_helper(self):
        self.assertEqual(allowed_topics("staff"), STAFF_ALLOWED_TOPICS)
        self.assertIn("orders", allowed_topics("session"))
        self.assertNotIn("orders", allowed_topics("staff"))

    async def test_staff_ping_still_works(self):
        """分级只影响 subscribe，不能顺手把心跳也挡了。"""
        ws = _FakeWebSocket()
        await self.hub.register(ws, "staff")

        await self.hub.handle_message(ws, json.dumps({"action": "ping"}))

        self.assertEqual(ws.sent[-1], {"type": "pong"})


if __name__ == "__main__":
    unittest.main()
