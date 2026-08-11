#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时订阅 Hub：`/ws/realtime` 连接的订阅状态管理与 nudge 派发。

只做"有变"通知（nudge：`{type, topic, scope}`，不带数据），不做 delta / 全局
seq / 跳号重订阅 / snapshot 缓存——客户端收到 nudge 后自行复用现有 HTTP API
拉取最新数据。
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

VALID_TOPICS = {"orders", "tables", "scraper", "dashboard", "logs", "admin"}

_VALID_ACTIONS = {"subscribe", "unsubscribe", "ping"}

# dashboard 汇总接口开销较大，orders/tables 变更后合并该窗口内的多次
# 变更为一次 `dashboard` nudge，而非每条变更都触发一次。
DASHBOARD_DEBOUNCE_SECONDS = 0.3

# 只有这些 topic 的变更需要顺带触发 dashboard 的 debounce 汇总；
# "dashboard" 本身不在其中——避免 dashboard 的 nudge 又反过来触发一次新的
# debounce（递归/无限循环）。
_DASHBOARD_DEBOUNCE_TOPICS = {"orders", "tables"}


@dataclass
class Subscription:
    id: str
    topics: set
    filters: dict


@dataclass
class ConnectionState:
    websocket: Any
    auth: str
    subscriptions: dict = field(default_factory=dict)

    def has_topic(self, topic: str) -> bool:
        return any(topic in sub.topics for sub in self.subscriptions.values())

    def subscriptions_for_topic(self, topic: str):
        return [sub for sub in self.subscriptions.values() if topic in sub.topics]


def _scope_matches_filters(scope: dict, filters: dict) -> bool:
    """filters 为空 = 接收该 topic 全部 nudge。否则对 filters 里指定的每个字段，
    仅当 scope 里同名字段存在且不相等时判定为不匹配；scope 未指定该字段（或
    filters 未指定该字段）都算匹配——即"订阅的 filter 是我只关心这个档口/日期，
    scope 是这次变更属于哪个档口/日期"。
    """
    for key, wanted in filters.items():
        if wanted is None:
            continue
        actual = scope.get(key)
        if actual is not None and actual != wanted:
            return False
    return True


class RealtimeHub:
    """管理所有 WS 连接的订阅状态，并把 nudge 精确推给匹配的订阅方。"""

    def __init__(self, dashboard_debounce_seconds: float = DASHBOARD_DEBOUNCE_SECONDS):
        self._connections: dict = {}
        self._dashboard_debounce_seconds = dashboard_debounce_seconds
        self._dashboard_debounce_task: Optional[asyncio.Task] = None

    async def register(self, websocket, auth: str) -> ConnectionState:
        """加入连接集合（不 accept，accept 由端点在调用本方法前完成一次）。"""
        state = ConnectionState(websocket=websocket, auth=auth)
        self._connections[websocket] = state
        return state

    def unregister(self, websocket) -> None:
        self._connections.pop(websocket, None)

    async def _send(self, websocket, payload: dict) -> bool:
        try:
            await websocket.send_json(payload)
            return True
        except Exception as exc:
            logger.debug("WS 推送失败，标记为死连接: %s", exc)
            return False

    async def _send_error(self, websocket, code: str, message: str) -> None:
        await self._send(websocket, {"type": "error", "code": code, "message": message})

    async def handle_message(self, websocket, raw: str) -> None:
        """解析客户端一条消息并分派 subscribe/unsubscribe/ping；非法消息回
        `{type:error}`，不断开连接。"""
        state = self._connections.get(websocket)
        if state is None:
            return

        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            await self._send_error(websocket, "INVALID_SUBSCRIBE", "消息必须是合法 JSON")
            return
        if not isinstance(msg, dict):
            await self._send_error(websocket, "INVALID_SUBSCRIBE", "消息必须是 JSON 对象")
            return

        action = msg.get("action")
        if action not in _VALID_ACTIONS:
            await self._send_error(websocket, "INVALID_SUBSCRIBE", f"未知 action: {action!r}")
            return

        if action == "ping":
            await self._send(websocket, {"type": "pong"})
            return

        sub_id = msg.get("id")
        if not sub_id or not isinstance(sub_id, str):
            await self._send_error(websocket, "INVALID_SUBSCRIBE", "缺少合法的 id")
            return

        if action == "unsubscribe":
            state.subscriptions.pop(sub_id, None)
            return

        topics = msg.get("topics")
        if not isinstance(topics, list) or not topics:
            await self._send_error(websocket, "INVALID_SUBSCRIBE", "topics 必须是非空数组")
            return
        invalid_topics = set(topics) - VALID_TOPICS
        if invalid_topics:
            await self._send_error(websocket, "INVALID_SUBSCRIBE", f"不支持的 topic: {sorted(invalid_topics)}")
            return

        filters = msg.get("filters") or {}
        if not isinstance(filters, dict):
            await self._send_error(websocket, "INVALID_SUBSCRIBE", "filters 必须是对象")
            return

        state.subscriptions[sub_id] = Subscription(id=sub_id, topics=set(topics), filters=filters)
        await self._send(websocket, {"type": "subscribed", "id": sub_id})

    async def broadcast_nudge(self, topic: str, scope: Optional[dict] = None) -> None:
        """向订阅了 `topic` 且过滤匹配的连接推 `{type:nudge, topic, scope}`；
        推送失败的死连接会被清理，不影响其它连接。"""
        scope = scope or {}
        message = {"type": "nudge", "topic": topic, "scope": scope}
        dead = []
        for websocket, state in list(self._connections.items()):
            matched = any(
                _scope_matches_filters(scope, sub.filters)
                for sub in state.subscriptions_for_topic(topic)
            )
            if not matched:
                continue
            if not await self._send(websocket, message):
                dead.append(websocket)
        for websocket in dead:
            self.unregister(websocket)

        if topic in _DASHBOARD_DEBOUNCE_TOPICS:
            self.schedule_dashboard_nudge()

    def schedule_dashboard_nudge(self) -> None:
        """在 `_dashboard_debounce_seconds` 窗口内合并多次调用为一次
        `dashboard` nudge：若已有等待中的定时任务则不重复创建。"""
        if self._dashboard_debounce_task is not None and not self._dashboard_debounce_task.done():
            return
        self._dashboard_debounce_task = asyncio.create_task(self._debounced_dashboard_nudge())

    async def _debounced_dashboard_nudge(self) -> None:
        await asyncio.sleep(self._dashboard_debounce_seconds)
        self._dashboard_debounce_task = None
        await self.broadcast_nudge("dashboard")


realtime_hub = RealtimeHub()
