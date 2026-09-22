#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`services.realtime.redis_bus` 单测——**不依赖真实 Redis**。

覆盖四件事：

1. 降级路径（默认部署的主路径）：未配置 `REDIS_URL` 时 `enabled is False`、
   `publish()` 是 no-op、hub 的本地派发照常；
2. 跨进程：两个 hub 各带一个 `RedisBus`，共享一个进程内假 Redis——A 发的 nudge
   能被 B 的订阅回调派发给 B 的本地订阅者，且 A 不会因为"自己的消息被回投"而
   重复派发（origin 自过滤）；
3. 重连：连不上时退避重试（不热循环），恢复后自动重新订阅并继续派发；
4. 编解码：topic + scope 往返一致，坏消息被丢弃而不是抛错。

假 Redis 只实现总线用到的那一小撮命令（`ping` / `pubsub` / `publish` / `close`），
通过 `RedisBus(client_factory=...)` 注入，所以被测的仍是真实的 `RedisBus` 代码
路径（订阅循环、退避、自过滤都在里面），而不是一个仿造的 bus。
"""

import asyncio
import json
import os
import unittest

from config import settings
from services.realtime.hub import RealtimeHub
from services.realtime.redis_bus import (
    NUDGE_CHANNEL,
    RECONNECT_BACKOFF_INITIAL_SECONDS,
    REDIS_REQUIRED_HINT,
    RedisBus,
    decode_nudge,
    encode_nudge,
    redis_url_from_env,
    require_redis_url,
)


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


def _nudges(ws):
    return [m for m in ws.sent if m.get("type") == "nudge"]


def _sink_handler(sink):
    async def handler(topic, scope):
        sink.append((topic, scope))

    return handler


async def _wait_for(predicate, timeout=2.0, interval=0.005):
    """轮询等待条件成立：假 Redis 的投递经过订阅任务的协程调度，靠 sleep(0) 猜
    轮数会脆，这里给一个明确上限。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())


class _FakePubSub:
    """假 pubsub：`listen()` 是一个由假服务器投喂的异步生成器。"""

    def __init__(self, server):
        self._server = server
        self._queue: asyncio.Queue = asyncio.Queue()
        self.channels = set()
        self.closed = False

    async def subscribe(self, channel):
        self.channels.add(channel)
        self._server.attach(channel, self)

    async def listen(self):
        while True:
            message = await self._queue.get()
            yield message

    async def aclose(self):
        self.closed = True
        self._server.detach(self)

    close = aclose  # redis<5.0.1 的名字，覆盖 aclose/close 两条关闭路径


class _FakeRedis:
    """假 redis 客户端：`fail_connect` 由假服务器控制，用来测退避重连。"""

    def __init__(self, server):
        self._server = server
        self.closed = False

    async def ping(self):
        if self._server.fail_connect:
            raise ConnectionError("模拟 Redis 不可达")
        return True

    def pubsub(self):
        return _FakePubSub(self._server)

    async def publish(self, channel, data):
        self._server.published.append((channel, data))
        # 真 Redis 会把消息也投回给发送者自己，这里照做——正是它让"自过滤"这条
        # 被测出来（不过滤的话发送方会收到两条 nudge）。
        return self._server.deliver(channel, data)

    async def aclose(self):
        self.closed = True

    close = aclose


class _FakeRedisServer:
    """进程内假 Redis：只有一个频道的 pub/sub 语义。"""

    def __init__(self):
        self.subscribers = {}
        self.published = []
        self.fail_connect = False

    def client(self):
        return _FakeRedis(self)

    def attach(self, channel, pubsub):
        self.subscribers.setdefault(channel, []).append(pubsub)

    def detach(self, pubsub):
        for subs in self.subscribers.values():
            if pubsub in subs:
                subs.remove(pubsub)

    def deliver(self, channel, data):
        targets = list(self.subscribers.get(channel, ()))
        for pubsub in targets:
            pubsub._queue.put_nowait({"type": "message", "channel": channel, "data": data})
        return len(targets)


class _NoRedisConfigCase:
    """把 REDIS_URL 钉成空：既测降级路径，也保证这些用例（本机 .env 配了 Redis
    也一样）绝不会去连真实 Redis。"""

    def setUp(self):
        self._orig_redis_url = settings.REDIS_URL
        self._orig_env = os.environ.pop("LUYUN_REDIS_URL", None)
        settings.REDIS_URL = ""
        self.addCleanup(self._restore_redis_config)

    def _restore_redis_config(self):
        settings.REDIS_URL = self._orig_redis_url
        if self._orig_env is None:
            os.environ.pop("LUYUN_REDIS_URL", None)
        else:
            os.environ["LUYUN_REDIS_URL"] = self._orig_env


class RedisBusDegradedTest(_NoRedisConfigCase, unittest.IsolatedAsyncioTestCase):
    """未配置 REDIS_URL：总线整体 no-op，本地派发与现在逐字一致。"""

    async def test_not_enabled_and_start_is_noop(self):
        bus = RedisBus()
        self.assertFalse(bus.enabled)
        self.assertFalse(bus.connected)

        await bus.start()
        self.assertIsNone(bus._task)  # 没起订阅任务 = 没去连 Redis

        self.assertFalse(await bus.publish("orders", {"station": "A"}))

        await bus.stop()  # 未启动时 stop 也是 no-op

    async def test_publish_is_noop_and_never_raises(self):
        sink = []
        bus = RedisBus(handler=_sink_handler(sink))
        self.assertFalse(await bus.publish("orders", {"station": "A"}))
        self.assertEqual(sink, [])
        await bus.publish("dashboard")
        await bus.publish("hygiene", {"resource": "daily"})

    async def test_whitespace_url_is_disabled(self):
        self.assertFalse(RedisBus(url="   ").enabled)

    async def test_hub_start_bus_without_redis_keeps_local_dispatch(self):
        hub = RealtimeHub()
        await hub.start_bus()

        self.assertIsNotNone(hub.bus)
        self.assertFalse(hub.bus.enabled)

        ws = _FakeWebSocket()
        await hub.register(ws, "session")
        await _subscribe(hub, ws, "sub-1", ["orders"])

        await hub.broadcast_nudge("orders", {"station": "A"})

        self.assertEqual(
            _nudges(ws),
            [{"type": "nudge", "topic": "orders", "scope": {"station": "A"}}],
        )
        await hub.stop_bus()

    async def test_hub_without_start_bus_is_purely_local(self):
        """没调 start_bus（DISABLE_BACKGROUND_TASKS 下的测试形态）：bus 为 None，
        broadcast 只有本地派发。"""
        hub = RealtimeHub()
        self.assertIsNone(hub.bus)

        ws = _FakeWebSocket()
        await hub.register(ws, "session")
        await _subscribe(hub, ws, "sub-1", ["tables"])

        await hub.broadcast_nudge("tables", {})

        self.assertEqual(_nudges(ws), [{"type": "nudge", "topic": "tables", "scope": {}}])
        await hub.stop_bus()

    def test_url_priority_env_then_settings_then_explicit_config(self):
        settings.REDIS_URL = "redis://from-settings:6379/0"
        self.assertEqual(redis_url_from_env(), "redis://from-settings:6379/0")

        os.environ["LUYUN_REDIS_URL"] = "redis://from-env:6379/1"
        self.assertEqual(redis_url_from_env(), "redis://from-env:6379/1")

        class _Cfg:
            REDIS_URL = "redis://from-config:6379/2"

        # env 优先于任何显式传入的配置对象
        self.assertEqual(redis_url_from_env(_Cfg), "redis://from-env:6379/1")
        os.environ.pop("LUYUN_REDIS_URL")
        self.assertEqual(redis_url_from_env(_Cfg), "redis://from-config:6379/2")

    def test_reconnect_backoff_is_not_a_hot_loop(self):
        self.assertGreaterEqual(RECONNECT_BACKOFF_INITIAL_SECONDS, 1.0)

    def test_redis_url_default_is_empty(self):
        """新增配置项的默认值：空字符串 = 不启用（compose 的 redis 默认不起）。"""
        self.assertEqual(type(settings).model_fields["REDIS_URL"].default, "")


class RedisBusCrossProcessTest(unittest.IsolatedAsyncioTestCase):
    """两个实例共用一个假 Redis：验证跨进程广播与自过滤。"""

    async def asyncSetUp(self):
        self.server = _FakeRedisServer()
        self.hub_a = RealtimeHub(
            bus=RedisBus(url="redis://fake:6379/0", client_factory=self.server.client)
        )
        self.hub_b = RealtimeHub(
            bus=RedisBus(url="redis://fake:6379/0", client_factory=self.server.client)
        )
        await self.hub_a.start_bus()
        await self.hub_b.start_bus()
        self.assertTrue(
            await _wait_for(lambda: self.hub_a.bus.connected and self.hub_b.bus.connected)
        )

    async def asyncTearDown(self):
        await self.hub_a.stop_bus()
        await self.hub_b.stop_bus()

    async def _register(self, hub, ws, sub_id, topics, filters=None):
        await hub.register(ws, "session")
        await _subscribe(hub, ws, sub_id, topics, filters)

    async def test_nudge_reaches_other_instance_and_is_not_dispatched_twice(self):
        ws_a, ws_b = _FakeWebSocket(), _FakeWebSocket()
        await self._register(self.hub_a, ws_a, "sub-a", ["orders"])
        await self._register(self.hub_b, ws_b, "sub-b", ["orders"])

        await self.hub_a.broadcast_nudge("orders", {"station": "A"})
        self.assertTrue(await _wait_for(lambda: _nudges(ws_b)))

        expected = {"type": "nudge", "topic": "orders", "scope": {"station": "A"}}
        # A 自己只有本地派发那一条：消息被假 Redis 回投给自己时按 origin 跳过
        self.assertEqual(_nudges(ws_a), [expected])
        self.assertEqual(_nudges(ws_b), [expected])

        # 再多等几轮，确认没有回音/来回弹
        await asyncio.sleep(0.05)
        self.assertEqual(len(_nudges(ws_a)), 1)
        self.assertEqual(len(_nudges(ws_b)), 1)

    async def test_published_message_carries_channel_topic_scope_and_origin(self):
        await self.hub_a.broadcast_nudge("hygiene", {"resource": "daily"})
        self.assertTrue(await _wait_for(lambda: self.server.published))

        channel, raw = self.server.published[-1]
        self.assertEqual(channel, NUDGE_CHANNEL)
        msg = decode_nudge(raw)
        self.assertEqual(msg["topic"], "hygiene")
        self.assertEqual(msg["scope"], {"resource": "daily"})
        self.assertEqual(msg["origin"], self.hub_a.bus.instance_id)

    async def test_two_instances_have_different_origin(self):
        self.assertNotEqual(self.hub_a.bus.instance_id, self.hub_b.bus.instance_id)

    async def test_cross_process_nudge_respects_local_filters(self):
        ws_b = _FakeWebSocket()
        await self._register(self.hub_b, ws_b, "sub-b", ["orders"], {"station": "B"})

        await self.hub_a.broadcast_nudge("orders", {"station": "A"})
        self.assertTrue(await _wait_for(lambda: self.server.published))
        await asyncio.sleep(0.05)
        self.assertEqual(_nudges(ws_b), [])

        await self.hub_a.broadcast_nudge("orders", {"station": "B"})
        self.assertTrue(await _wait_for(lambda: _nudges(ws_b)))
        self.assertEqual(_nudges(ws_b)[0]["scope"], {"station": "B"})

    async def test_local_dispatch_happens_even_if_publish_fails(self):
        """总线坏了也不能丢本地 nudge：publish 用会抛错的 client。"""

        class _BrokenRedis(_FakeRedis):
            async def publish(self, channel, data):
                raise ConnectionError("模拟发送失败")

        hub = RealtimeHub(bus=RedisBus(url="redis://fake", client_factory=lambda: _BrokenRedis(self.server)))
        await hub.start_bus()
        try:
            self.assertTrue(await _wait_for(lambda: hub.bus.connected))
            ws = _FakeWebSocket()
            await self._register(hub, ws, "sub-c", ["orders"])

            await hub.broadcast_nudge("orders", {"station": "C"})

            self.assertEqual(len(_nudges(ws)), 1)
            self.assertEqual(_nudges(ws)[0]["scope"], {"station": "C"})
        finally:
            await hub.stop_bus()

    async def test_handle_message_filters_own_origin(self):
        sink = []
        bus = RedisBus(
            url="redis://fake",
            handler=_sink_handler(sink),
            client_factory=self.server.client,
        )
        await bus._handle_message(encode_nudge("orders", {"station": "A"}, bus.instance_id))
        self.assertEqual(sink, [])
        await bus._handle_message(encode_nudge("orders", {"station": "A"}, "other-instance"))
        self.assertEqual(sink, [("orders", {"station": "A"})])
        await bus._handle_message("不是 JSON")
        self.assertEqual(sink, [("orders", {"station": "A"})])


class RedisBusReconnectTest(unittest.IsolatedAsyncioTestCase):
    """连不上 → 退避重试 → 恢复后自动重新订阅。"""

    async def test_reconnects_resubscribes_after_redis_comes_back(self):
        server = _FakeRedisServer()
        server.fail_connect = True
        received = []
        bus = RedisBus(
            url="redis://fake:6379/0",
            handler=_sink_handler(received),
            client_factory=server.client,
            backoff_initial=0.01,
            backoff_max=0.05,
        )
        await bus.start()
        try:
            self.assertFalse(bus.connected)
            await asyncio.sleep(0.05)  # 跑过若干轮退避，全程连不上也不抛错
            self.assertFalse(bus.connected)
            self.assertTrue(bus.enabled)
            # 连不上时 publish 只记日志、不抛错
            self.assertFalse(await bus.publish("orders", {"station": "A"}))

            server.fail_connect = False
            self.assertTrue(await _wait_for(lambda: bus.connected))

            # 恢复后是**重新订阅**过的：另一个实例的 nudge 能到达 handler
            other = RedisBus(
                url="redis://fake:6379/0",
                client_factory=server.client,
                backoff_initial=0.01,
            )
            await other.start()
            try:
                self.assertTrue(await _wait_for(lambda: other.connected))
                self.assertTrue(await other.publish("orders", {"station": "Z"}))
                self.assertTrue(await _wait_for(lambda: received))
                self.assertEqual(received, [("orders", {"station": "Z"})])
            finally:
                await other.stop()
        finally:
            await bus.stop()

        self.assertFalse(bus.connected)
        self.assertFalse(await bus.publish("orders", {}))  # stop 之后不再发送

    async def test_start_and_stop_are_idempotent(self):
        server = _FakeRedisServer()
        bus = RedisBus(url="redis://fake", client_factory=server.client, backoff_initial=0.01)
        await bus.start()
        first_task = bus._task
        await bus.start()
        self.assertIs(bus._task, first_task)
        self.assertTrue(await _wait_for(lambda: bus.connected))

        await bus.stop()
        await bus.stop()
        self.assertFalse(bus.connected)
        self.assertIsNone(bus._task)


class NudgeCodecTest(unittest.TestCase):
    """消息编解码：只有 nudge 协议本身 + 自我过滤用的 origin。"""

    def test_roundtrip_topic_and_scope(self):
        raw = encode_nudge("orders", {"station": "A", "nested": {"x": 1}}, origin="inst-1")
        self.assertEqual(
            decode_nudge(raw),
            {
                "topic": "orders",
                "scope": {"station": "A", "nested": {"x": 1}},
                "origin": "inst-1",
            },
        )

    def test_scope_defaults_to_empty_dict(self):
        self.assertEqual(decode_nudge(encode_nudge("dashboard"))["scope"], {})

    def test_bytes_payload_is_accepted(self):
        """decode_responses 不是唯一可能：bytes 也要能解析。"""
        self.assertEqual(decode_nudge(encode_nudge("logs").encode("utf-8"))["topic"], "logs")

    def test_payload_has_no_data_or_sequence_fields(self):
        msg = json.loads(encode_nudge("orders", {"station": "A"}, origin="i"))
        self.assertEqual(set(msg), {"type", "topic", "scope", "origin"})

    def test_bad_payloads_are_dropped(self):
        bad = [
            "不是 JSON",
            "[1,2]",
            '{"type":"nudge"}',
            '{"type":"nudge","topic":""}',
            '{"type":"other","topic":"orders"}',
            '{"type":"nudge","topic":"orders","scope":[]}',
            b"\xff\xfe",
            None,
            42,
        ]
        for raw in bad:
            self.assertIsNone(decode_nudge(raw), repr(raw))


class RedisRequiredTest(_NoRedisConfigCase, unittest.TestCase):
    """部署契约：Redis 是必需组件——没配 `REDIS_URL` 就是部署没做完。

    fail-fast 落在**启动路径**上（`main._require_startup_config`），不是落在
    `RedisBus` 上：配置问题判死，运行期连不上只退避重连。
    """

    def test_require_redis_url_raises_with_install_guidance(self):
        with self.assertRaises(RuntimeError) as ctx:
            require_redis_url()
        message = str(ctx.exception)
        self.assertIs(message, REDIS_REQUIRED_HINT)
        # 指引必须给全：装什么、配哪里、怎么临时跳过
        for hint in ("redis-server", "REDIS_URL", "env.production", "DISABLE_BACKGROUND_TASKS"):
            self.assertIn(hint, message)

    def test_whitespace_url_counts_as_not_configured(self):
        settings.REDIS_URL = "   "
        with self.assertRaises(RuntimeError):
            require_redis_url()
        self.assertFalse(RedisBus(handler=None).enabled)

    def test_require_redis_url_returns_configured_url(self):
        settings.REDIS_URL = "redis://127.0.0.1:6379/0"
        self.assertEqual(require_redis_url(), "redis://127.0.0.1:6379/0")

    def test_env_override_satisfies_the_contract(self):
        os.environ["LUYUN_REDIS_URL"] = "redis://from-env:6379/1"
        self.assertEqual(require_redis_url(), "redis://from-env:6379/1")


class StartupRequiresRedisTest(_NoRedisConfigCase, unittest.TestCase):
    """启动期硬前置：`main._require_startup_config()`。

    刻意只调这个薄函数、不进 lifespan —— 真的进 lifespan 会拉起爬虫去连真实 POS。
    代价是"lifespan 里确实调了它"只靠代码可见性保证（一行调用，评审看得见）。
    """

    def setUp(self):
        super().setUp()
        self._orig_bg = settings.DISABLE_BACKGROUND_TASKS
        settings.DISABLE_BACKGROUND_TASKS = False
        self.addCleanup(self._restore_bg)

    def _restore_bg(self):
        settings.DISABLE_BACKGROUND_TASKS = self._orig_bg

    def test_missing_redis_url_fails_startup(self):
        import main

        with self.assertRaises(RuntimeError) as ctx:
            main._require_startup_config()
        self.assertIs(str(ctx.exception), REDIS_REQUIRED_HINT)

    def test_configured_redis_url_passes(self):
        import main

        settings.REDIS_URL = "redis://127.0.0.1:6379/0"
        main._require_startup_config()  # 不抛即通过

    def test_disabled_background_tasks_skips_the_requirement(self):
        import main

        settings.DISABLE_BACKGROUND_TASKS = True
        main._require_startup_config()  # 测试 / 一次性工具不需要 Redis


class RedisTimeoutContractTest(unittest.IsolatedAsyncioTestCase):
    """超时只兜「连接期 + 发送」，**绝不**兜订阅的读。

    2026-09 现场踩过：redis-py 5.0.x 把客户端的 `socket_timeout` 作用到 pubsub 的
    空闲读上，安静的频道每 N 秒就抛一次 `TimeoutError: Timeout reading from ...`，
    订阅循环跟着重连——日志刷屏，而且重连窗口内的 nudge 直接丢（pub/sub 没有重放）。
    所以这里把「不给 client 设 socket_timeout」钉成契约。
    """

    def test_client_is_created_without_socket_timeout(self):
        from unittest import mock

        from redis import asyncio as redis_asyncio

        from services.realtime.redis_bus import OP_TIMEOUT_SECONDS

        bus = RedisBus(url="redis://127.0.0.1:6379/0")
        with mock.patch.object(
            redis_asyncio, "from_url", return_value=object()
        ) as from_url:
            bus._create_client()

        kwargs = from_url.call_args.kwargs
        self.assertNotIn("socket_timeout", kwargs)
        self.assertEqual(kwargs["socket_connect_timeout"], OP_TIMEOUT_SECONDS)
        self.assertTrue(kwargs["decode_responses"])

    async def test_publish_timeout_does_not_hang_the_request(self):
        server = _FakeRedisServer()

        class _HangingPublishRedis(_FakeRedis):
            async def publish(self, channel, data):
                await asyncio.sleep(3600)  # 模拟"半死"：TCP 通、不回包

        bus = RedisBus(
            url="redis://fake",
            client_factory=lambda: _HangingPublishRedis(server),
            op_timeout=0.05,
            backoff_initial=0.01,
        )
        await bus.start()
        try:
            self.assertTrue(await _wait_for(lambda: bus.connected))
            started = asyncio.get_running_loop().time()
            self.assertFalse(await bus.publish("orders", {"station": "A"}))
            self.assertLess(asyncio.get_running_loop().time() - started, 1.0)
        finally:
            await bus.stop()

    async def test_connect_probe_timeout_keeps_retrying(self):
        """连接期探测也要有超时，否则半死连接会把订阅循环永久挂住。"""
        server = _FakeRedisServer()

        class _HangingPingRedis(_FakeRedis):
            async def ping(self):
                await asyncio.sleep(3600)

        bus = RedisBus(
            url="redis://fake",
            client_factory=lambda: _HangingPingRedis(server),
            op_timeout=0.05,
            backoff_initial=0.01,
        )
        await bus.start()
        try:
            await asyncio.sleep(0.2)
            self.assertFalse(bus.connected)  # 连不上，但循环还活着（没被挂死）
            self.assertFalse(bus._task.done())
        finally:
            await bus.stop()
        self.assertFalse(bus.connected)


if __name__ == "__main__":
    unittest.main()
