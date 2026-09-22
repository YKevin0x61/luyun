#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""realtime nudge 的跨进程广播总线（Redis pub/sub）。

`/ws/realtime` 的订阅状态在每个进程的内存里，nudge 也只在本进程内派发。要让
scraper 所在进程的变更通知到别的 worker 上的 WS 连接，就需要一条进程外的广播
通道——这里用 Redis pub/sub，消息体就是 nudge 协议本身（`topic` + `scope`）加上
发送方实例 id，**不带**数据、不带序号。

设计约束（可降级是第一要求）：

- `REDIS_URL` / `LUYUN_REDIS_URL` 未配置 → `enabled is False`，所有方法退化成
  no-op，行为与"没有这个模块"完全一致；
- 连不上、连上后断开、发不出去 → 只记日志 + 退避重连（1s 起、翻倍、封顶 30s），
  **绝不**抛给调用方，也**绝不**影响应用启动与请求；
- 本地派发不经过本模块（见 `hub.RealtimeHub._dispatch_local`），所以总线坏掉只
  影响跨进程传播，不丢本进程的 nudge。

依赖 `redis.asyncio`（`requirements.txt` 的 `redis>=5,<9`），惰性 import：没装
这个包时按"总线不可用"降级，而不是让应用起不来。
"""

import asyncio
import inspect
import json
import logging
import os
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 频道命名留扩展位：将来要按 topic 分流可以加 `luyun:nudge:<topic>` 这类前缀频道。
# 本阶段只有一个频道——消息里的 `topic` 字段已经够用，加了反而多一份要同步的映射。
NUDGE_CHANNEL = "luyun:nudge"

# 断线重连退避：别热循环。1s → 2s → 4s……封顶 30s。
RECONNECT_BACKOFF_INITIAL_SECONDS = 1.0
RECONNECT_BACKOFF_MAX_SECONDS = 30.0

# 单条命令的 socket 超时。`publish()` 在请求路径上被 await，Redis "半死"（TCP
# 连上了但不回包）时不能把业务请求拖住。
SOCKET_TIMEOUT_SECONDS = 2.0

# handler 签名：`async def handler(topic: str, scope: dict) -> None`
NudgeHandler = Callable[[str, dict], Any]


def redis_url_from_env(config: Any = None) -> str:
    """Redis 连接串：环境变量 ``LUYUN_REDIS_URL`` 优先，其次 ``settings.REDIS_URL``。

    写法与 ``db_core/backend/pg.py::dsn_from_env`` 一致（env 优先于 .env / 配置）。
    返回空字符串表示不启用跨进程总线。
    """
    override = os.environ.get("LUYUN_REDIS_URL")
    if override:
        return override
    if config is None:
        from config import settings

        config = settings
    return getattr(config, "REDIS_URL", "") or ""


def encode_nudge(topic: str, scope: Optional[dict] = None, origin: str = "") -> str:
    """把 nudge 编码成总线消息（JSON）。

    字段与发给客户端的 nudge 一致（`type` / `topic` / `scope`），另加一个 `origin`
    ——发送方进程实例 id，供订阅方过滤掉自己发的消息。
    """
    return json.dumps(
        {"type": "nudge", "topic": topic, "scope": scope or {}, "origin": origin},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_nudge(raw: Any) -> Optional[dict]:
    """解析总线消息，返回 ``{"topic", "scope", "origin"}``；非法形状返回 None。

    容错是刻意的：频道上可能出现别的进程（或将来别的版本）写的东西，坏消息只该
    被丢掉，不该让订阅循环出错。
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str):
        return None
    try:
        msg = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("type") != "nudge":
        return None
    topic = msg.get("topic")
    if not isinstance(topic, str) or not topic:
        return None
    scope = msg.get("scope")
    if scope is None:
        scope = {}
    if not isinstance(scope, dict):
        return None
    origin = msg.get("origin")
    return {
        "topic": topic,
        "scope": scope,
        "origin": origin if isinstance(origin, str) else "",
    }


def _redact_url(url: str) -> str:
    """日志里去掉 URL 的 userinfo（可能含密码），只留 host/port/db。"""
    if "@" not in url:
        return url
    scheme, sep, rest = url.partition("://")
    tail = rest.rsplit("@", 1)[-1] if sep else url.rsplit("@", 1)[-1]
    return f"{scheme}{sep}***@{tail}" if sep else f"***@{tail}"


async def _close_quietly(target: Any) -> None:
    """关闭 redis 客户端 / pubsub：兼容 `aclose()`（redis>=5.0.1）与 `close()`，
    失败只记 debug——收摊阶段的异常不该冒泡到 lifespan。"""
    if target is None:
        return
    closer = getattr(target, "aclose", None) or getattr(target, "close", None)
    if closer is None:
        return
    try:
        result = closer()
        if inspect.isawaitable(result):
            await result
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("关闭 Redis 资源失败（忽略）: %s", exc)


class RedisBus:
    """Redis pub/sub 薄封装：一条连接 + 一个订阅任务 + 退避重连。

    用法：构造时传 `handler=`，或之后 `bus.subscribe(handler)`，把"收到别的进程
    的 nudge"交给它；`await bus.start()` 起后台订阅任务，`await bus.stop()` 收摊。
    `publish()` 只负责把 nudge 发到频道——本地派发由调用方（hub）自己完成。
    """

    def __init__(
        self,
        config: Any = None,
        handler: Optional[NudgeHandler] = None,
        url: Optional[str] = None,
        client_factory: Optional[Callable[[], Any]] = None,
        backoff_initial: float = RECONNECT_BACKOFF_INITIAL_SECONDS,
        backoff_max: float = RECONNECT_BACKOFF_MAX_SECONDS,
        socket_timeout: float = SOCKET_TIMEOUT_SECONDS,
    ):
        # 进程实例 id：订阅回调据此跳过自己发的消息，避免"自己派发一次 + 订阅
        # 回来再派发一次"的重复。
        self.instance_id = uuid.uuid4().hex
        self._config = config
        self._handler = handler
        self._url = (url if url is not None else redis_url_from_env(config)).strip()
        # 测试 seam：注入的 client 工厂替掉真实的 redis.asyncio.from_url。
        self._client_factory = client_factory
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._socket_timeout = socket_timeout
        self._task: Optional[asyncio.Task] = None
        self._client: Any = None
        self._connected = False

    @property
    def enabled(self) -> bool:
        """是否配置了 Redis（配置层语义，不代表当前连着）。"""
        return bool(self._url)

    @property
    def connected(self) -> bool:
        """当前是否已连接并订阅成功（退避重连期间为 False）。"""
        return self._connected

    def subscribe(self, handler: Optional[NudgeHandler]) -> None:
        """设置收到跨进程 nudge 时的回调（后设覆盖先设）。"""
        self._handler = handler

    async def start(self) -> None:
        """起后台订阅任务：连接、订阅、重连都在任务里做。

        这里刻意**不** await 连接：Redis 不可达时不能拖住或打断应用启动，连接
        失败由后台任务退避重试。未配置 URL 时是 no-op。
        """
        if not self.enabled:
            logger.info("ℹ️ 未配置 REDIS_URL，realtime nudge 只在进程内派发")
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="realtime-redis-bus")

    async def stop(self) -> None:
        """取消订阅任务并断开连接；未启用 / 未启动 / 重复调用都是 no-op。"""
        task, self._task = self._task, None
        self._connected = False
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # 关闭路径上的异常不该影响 lifespan
            logger.debug("Redis nudge 总线任务退出时异常: %s", exc)

    async def publish(self, topic: str, scope: Optional[dict] = None) -> bool:
        """把 nudge 发到频道，返回是否真的发出去了。

        未启用 / 未连接 / 发送失败都只记日志：调用方（hub）已经完成本地派发，
        总线只负责把它捎给别的进程，Redis 抖动不该让业务请求报错。
        """
        client = self._client
        if not self.enabled or client is None:
            logger.debug("Redis nudge 总线未就绪，跳过跨进程广播（topic=%s）", topic)
            return False
        try:
            await client.publish(
                NUDGE_CHANNEL, encode_nudge(topic, scope, self.instance_id)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("⚠️ Redis nudge 总线发送失败（topic=%s）: %s", topic, exc)
            return False
        return True

    def _create_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        # 惰性 import：没装 redis 包时降级为"总线不可用"，而不是让应用起不来。
        from redis import asyncio as redis_asyncio

        return redis_asyncio.from_url(
            self._url,
            decode_responses=True,
            socket_connect_timeout=self._socket_timeout,
            socket_timeout=self._socket_timeout,
        )

    async def _run(self) -> None:
        """订阅循环：连上就收消息，断了就退避重连；每次连上都重新订阅一次。"""
        backoff = self._backoff_initial
        while True:
            client = None
            pubsub = None
            try:
                client = self._create_client()
                await client.ping()
                pubsub = client.pubsub()
                await pubsub.subscribe(NUDGE_CHANNEL)
                self._client = client
                self._connected = True
                backoff = self._backoff_initial
                logger.info(
                    "✅ realtime nudge 总线已连接：%s（频道 %s）",
                    _redact_url(self._url),
                    NUDGE_CHANNEL,
                )
                async for message in pubsub.listen():
                    if not isinstance(message, dict):
                        continue
                    if message.get("type") != "message":
                        continue  # subscribe/unsubscribe 之类的确认帧
                    await self._handle_message(message.get("data"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "⚠️ Redis nudge 总线不可用（%s），%.1fs 后重连：%s",
                    _redact_url(self._url),
                    backoff,
                    exc,
                )
            finally:
                self._connected = False
                self._client = None
                await _close_quietly(pubsub)
                await _close_quietly(client)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._backoff_max)

    async def _handle_message(self, raw: Any) -> None:
        """处理一条总线消息：自己发的跳过（本地已经派发过），其余交给 handler。"""
        msg = decode_nudge(raw)
        if msg is None:
            logger.debug("忽略无法解析的 realtime 总线消息: %r", raw)
            return
        if msg["origin"] == self.instance_id:
            return
        handler = self._handler
        if handler is None:
            return
        try:
            await handler(msg["topic"], msg["scope"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("处理跨进程 nudge 失败（topic=%s）: %s", msg["topic"], exc)
