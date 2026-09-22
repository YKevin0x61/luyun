#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时订阅 Hub：`/ws/realtime` 连接的订阅状态管理与 nudge 派发。

只做"有变"通知（nudge：`{type, topic, scope}`，不带数据），不做 delta / 全局
seq / 跳号重订阅 / snapshot 缓存——客户端收到 nudge 后自行复用现有 HTTP API
拉取最新数据。

订阅状态还是进程内的，但 nudge 的**跨进程传播**交给了
`services.realtime.redis_bus.RedisBus`：配了 `REDIS_URL` 时 `broadcast_nudge()`
会先把 nudge 发一份到 Redis 频道，别的进程收到后只做本地派发（不再转发，否则
消息会在实例间来回弹）。未配置或 Redis 不可用时总线整体退化成 no-op，本模块的
行为与纯进程内派发逐字一致。

**单 worker 约束仍未解除**：nudge 能跨进程了，但那 7 个常驻后台循环还没有分布式
选主，开 `--workers > 1` 仍会重复采集 / 重复推送（见 deploy/README.md）。
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from services.realtime.redis_bus import RedisBus

logger = logging.getLogger(__name__)

VALID_TOPICS = {
    "orders",
    "tables",
    "scraper",
    "dashboard",
    "logs",
    "admin",
    "hygiene",
}

_VALID_ACTIONS = {"subscribe", "unsubscribe", "ping"}

# 员工（staff）会话只允许订阅卫生主题。nudge 不带数据，但 orders/tables/logs/admin
# 的**时序**本身就是门店经营信息（几点来了几单、什么时候在改档口），而员工端没有
# 任何页面需要它们。管理员会话不受限制。
STAFF_ALLOWED_TOPICS = frozenset({"hygiene"})


def allowed_topics(auth: str):
    """该鉴权方式能订阅的 topic 集合。"""
    if auth == "staff":
        return STAFF_ALLOWED_TOPICS
    return VALID_TOPICS

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
    """管理所有 WS 连接的订阅状态，并把 nudge 精确推给匹配的订阅方。

    `bus`（Redis 跨进程总线）可以注入：不注入时由 `start_bus()` 按配置建一个；
    没调用 `start_bus()` 就一直为 None，此时行为与没有 Redis 的版本一致。
    """

    def __init__(
        self,
        dashboard_debounce_seconds: float = DASHBOARD_DEBOUNCE_SECONDS,
        bus: Optional[RedisBus] = None,
    ):
        self._connections: dict = {}
        self._dashboard_debounce_seconds = dashboard_debounce_seconds
        self._dashboard_debounce_task: Optional[asyncio.Task] = None
        self._bus = bus

    @property
    def bus(self) -> Optional[RedisBus]:
        """跨进程 nudge 总线；未配置 / 未启动时为 None。"""
        return self._bus

    async def start_bus(self) -> None:
        """启动跨进程 nudge 总线（Redis pub/sub）。

        `REDIS_URL` 未配置或 Redis 不可用时整体退化为 no-op：只记日志，不影响
        启动，也不影响本地派发。
        """
        if self._bus is None:
            self._bus = RedisBus(handler=self._dispatch_from_bus)
        else:
            self._bus.subscribe(self._dispatch_from_bus)
        await self._bus.start()

    async def stop_bus(self) -> None:
        """停止跨进程 nudge 总线；未启动 / 未配置时是 no-op。"""
        if self._bus is None:
            return
        await self._bus.stop()

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
        invalid_topics = set(topics) - allowed_topics(state.auth)
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
        """本地派发 + 跨进程广播。

        本地派发永远先做（总线坏掉也不影响本进程的订阅者）；Redis 只是把同一个
        nudge 捎给别的 worker 上的订阅者。
        """
        scope = scope or {}
        await self._dispatch_local(topic, scope)
        bus = self._bus
        if bus is not None:
            await bus.publish(topic, scope)

    async def _dispatch_from_bus(self, topic: str, scope: Optional[dict] = None) -> None:
        """收到**别的进程**发来的 nudge：只做本地派发。

        这里刻意不再 publish——转发会让同一条消息在实例间来回弹。
        """
        await self._dispatch_local(topic, scope)

    async def _dispatch_local(self, topic: str, scope: Optional[dict] = None) -> None:
        """向订阅了 `topic` 且过滤匹配的**本进程**连接推
        `{type:nudge, topic, scope}`；推送失败的死连接会被清理，不影响其它连接。
        """
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
        # debounce 是**本地时钟**上的合并：产物只在本地派发。若在这里回 publish，
        # 每个实例都会把自己合并出来的 dashboard 再广播一遍，别的实例就收到重复
        # 的 dashboard nudge（它们的 debounce 定时器本来也会各自触发一次）。
        await self._dispatch_local("dashboard")


realtime_hub = RealtimeHub()
