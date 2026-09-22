#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`/api/healthz` 的 realtime 总线（Redis）状态段（CORR-04）。

Redis 自 ADR 0090 起是部署必需组件，但运行期掉线以前没有任何对外可见面：
healthz body 只有 status/db/disk，`RedisBus.connected` 全仓没有消费方，探针看不出
「必需组件掉线了」。这里钉住的三条契约：

1. **一定有 redis 段且是布尔**：`{"configured": bool, "connected": bool}`——探针要能
   直接判断，不必去猜字段类型；
2. **只读两个现成属性**：`RedisBus.enabled` / `RedisBus.connected`。healthz 是免鉴权、
   面向前端反代与 Docker 的轻量探针，**不做主动 ping、不建连接、不阻塞**（用只允许读
   这两个属性的假 bus 钉住）；
3. **掉线不改状态码**：总线未配置 / 未连上时 `connected=False`，但 healthz 仍返回
   200——本地派发不经过总线，重启容器腾不出 Redis，重启循环比降级本身更糟（与
   「磁盘水位高不改状态码」同一条设计）。

三种总线状态下的取值（`services/realtime/redis_bus.py`）：

- 未配 `REDIS_URL`：生产启动在 `_require_startup_config()` fail-fast，根本到不了运行期；
  `DISABLE_BACKGROUND_TASKS=true`（测试 / 一次性工具）时 `realtime_hub.bus` 一直是
  None → `configured=False, connected=False`；
- 配了但连不上（Redis 停 / 地址错 / 连接后掉线）：`enabled=True`、退避重连期间
  `_connected=False` → `configured=True, connected=False`；
- 已连上（PING + SUBSCRIBE 成功）：`_connected=True` → `configured=True, connected=True`。

这些用例一律用假 bus 注入状态，绝不连真 Redis——生产 Redis 进程不该被测试碰。
"""

import contextlib

import pytest
from fastapi.testclient import TestClient


class _ReadOnlyBus:
    """只允许读 `enabled` / `connected` 的假 bus。

    healthz 一旦碰别的（`ping()` / `publish()` / 真连接）就抛 AssertionError 变红——
    探针必须轻量只读这条约束靠它钉住，而不是靠读实现。
    """

    def __init__(self, *, enabled: bool, connected: bool) -> None:
        self.enabled = enabled
        self.connected = connected

    def __getattr__(self, name):
        raise AssertionError(
            f"healthz 只能读 bus.enabled / bus.connected，这里访问了 {name!r}"
            "（免鉴权探针不得主动 ping / 建连接）"
        )


@pytest.fixture
def client():
    """真实 app（含中间件与依赖树）的匿名客户端；不带任何凭据。"""
    import main as main_module

    with TestClient(main_module.app) as test_client:
        yield test_client


@contextlib.contextmanager
def _injected_bus(monkeypatch, *, enabled: bool, connected: bool):
    """用例内临时换上假 bus，退出前恢复原值。

    用 `monkeypatch.context()` 而不是整场 monkeypatch：lifespan 关闭路径会调
    `stop_bus()`（那是它该做的），假对象不必为关闭路径兜底，探针用例也不该把
    「只读两个属性」的约束放宽到 `stop`。
    """
    import main as main_module

    with monkeypatch.context() as patch:
        patch.setattr(
            main_module.realtime_hub,
            "_bus",
            _ReadOnlyBus(enabled=enabled, connected=connected),
        )
        yield


def _redis_state(client):
    resp = client.get("/api/healthz")
    assert resp.status_code == 200, (
        f"healthz 必须 200（总线状态不改状态码）：{resp.status_code} {resp.text}"
    )
    return resp.json()["redis"]


def test_healthz_reports_configured_but_disconnected_bus(client, monkeypatch):
    """配了 Redis 但没连上：探针能看出掉线，且 healthz 仍是 200。"""
    with _injected_bus(monkeypatch, enabled=True, connected=False):
        redis = _redis_state(client)

    assert redis["configured"] is True
    assert redis["connected"] is False


def test_healthz_reports_connected_bus(client, monkeypatch):
    """连上了就报连上：不能永远 False（否则这个出口等于没有）。"""
    with _injected_bus(monkeypatch, enabled=True, connected=True):
        redis = _redis_state(client)

    assert redis["configured"] is True
    assert redis["connected"] is True


def test_healthz_reports_missing_bus_as_not_configured(client):
    """总线没起来（未配 URL 的降级形态 / DISABLE_BACKGROUND_TASKS）时两字段都是 False。"""
    import main as main_module

    assert main_module.realtime_hub.bus is None, "本用例的前提是总线未启动"

    redis = _redis_state(client)

    assert redis["configured"] is False
    assert redis["connected"] is False


def test_healthz_redis_fields_are_booleans_and_probe_stays_anonymous(client, monkeypatch):
    """字段类型是布尔（探针直接判断），且无 cookie / 无凭据也能读到。"""
    assert not client.cookies, "本用例刻意不带任何凭据"

    with _injected_bus(monkeypatch, enabled=True, connected=True):
        resp = client.get("/api/healthz")

    assert resp.status_code == 200, resp.text
    redis = resp.json()["redis"]
    assert isinstance(redis["configured"], bool), redis
    assert isinstance(redis["connected"], bool), redis
