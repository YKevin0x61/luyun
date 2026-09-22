#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志存储没起来时的对外契约（CORR-02）。

`LogStorage.start()` 失败（PG 启动期不可达、口令错、缺表……）后 `_conn is None`，
此前的查询入口抛的是 `AssertionError('')`：它既不在「存储不可用」的异常白名单里，
空 message 也匹配不到任何连接标记，于是 `/api/logs` 走的是 500 那条分支，detail
被拼成 `"查询日志失败: "`——门店现场拿到 500 + 空白原因，契约里该有的 503
「日志存储不可用」信号丢了。

本文件钉三条契约：

1. `/api/logs`、`/api/logs/stats`、`/api/logs/facets`（以及同一根因的
   `/api/logs/persisted/recent`、`/api/logs/cleanup`）在存储不可用时返回 **503**，
   且 detail 非空、能读出「日志存储不可用」；
2. 非「不可用」的普通失败仍是 500，但 detail 绝不空着（兜底成异常类型名）；
3. 正常 start 之后这些接口仍然 200（回归）。

503 那几条不启动完整 app lifespan（同 tests/test_logs_nudge_bridge.py 的注入式
写法）：用一个只挂 logs_router 的最小 app，把路由模块引用的单例换成「没起来的
LogStorage」，这样断言的是路由的映射分支本身，不需要真的把 PG 打挂。鉴权门
（`verify_admin_token`，已由 tests/test_security_regression.py 覆盖）用
`dependency_overrides` 放行。
"""

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import logs as logs_api
from api.security import verify_admin_token
from config import settings
from db_core.backend import pg as pg_backend
from services.log_storage import LogStorage, LogStorageUnavailable

# 存储不可用时的对外文案（与 stats / facets 既有设计一致）。
UNAVAILABLE_DETAIL = "日志存储不可用"

# 同一根因（入口发现 `_conn is None`）的五个查询 / 维护入口。
UNAVAILABLE_REQUESTS = (
    ("GET", "/api/logs?limit=1"),
    ("GET", "/api/logs/stats"),
    ("GET", "/api/logs/facets"),
    ("GET", "/api/logs/persisted/recent?limit=1"),
    ("POST", "/api/logs/cleanup?days=7"),
)


def _client() -> TestClient:
    """只挂 logs_router 的最小 app（不用 with，故不触发任何 lifespan）。

    鉴权门 `verify_admin_token` 是 router 级依赖，在 `@router.get` 时就固化进了每条
    route 的 `dependencies`（`include_router` 只是把它再复制一遍），所以这里用
    `dependency_overrides` 覆盖掉它 —— 绝不改共享 route 对象上的 `dependencies`
    （那是模块级共享状态，会在同一进程的后续用例里继续生效）。鉴权本身由
    tests/test_security_regression.py 覆盖，本文件只钉状态码映射。
    """
    app = FastAPI()
    app.include_router(logs_api.router)
    app.dependency_overrides[verify_admin_token] = lambda: True
    return TestClient(app)


async def _refuse_connect():
    raise ConnectionRefusedError("connection refused")


class LogStorageUnavailableErrorTest(unittest.IsolatedAsyncioTestCase):
    """入口在 `_conn is None` 时抛专用异常，而不是把断言当控制流。"""

    async def test_query_entry_points_raise_dedicated_unavailable_error(self):
        storage = LogStorage()
        self.assertIsNone(storage._conn)

        for make_coro in (
            lambda: storage.query(limit=1),
            lambda: storage.stats(),
            lambda: storage.facets(),
            lambda: storage.latest(limit=1),
            lambda: storage.cleanup_older_than(7),
        ):
            with self.assertRaises(LogStorageUnavailable):
                await make_coro()

    async def test_failed_start_leaves_the_same_unavailable_state(self):
        """PG 启动期不可达：start() 返回 False，后续查询仍抛专用异常。"""
        storage = LogStorage()
        with mock.patch.object(pg_backend, "connect", _refuse_connect):
            self.assertFalse(await storage.start())

        self.assertIsNone(storage._conn)
        with self.assertRaises(LogStorageUnavailable):
            await storage.query(limit=1)

    def test_dedicated_error_is_treated_as_storage_unavailable(self):
        """503 分支靠 is_corruption_error 判定，专用异常必须命中。"""
        self.assertTrue(issubclass(LogStorageUnavailable, RuntimeError))

        error = LogStorageUnavailable("日志存储未连接")

        self.assertEqual(str(error), "日志存储未连接")
        self.assertTrue(LogStorage.is_corruption_error(error))


class LogsApiUnavailableMappingTest(unittest.IsolatedAsyncioTestCase):
    """路由分支：存储不可用 → 503 + 可读 detail；正常 start → 200。"""

    async def asyncSetUp(self):
        self.storage = LogStorage()
        patcher = mock.patch.object(logs_api, "log_storage", self.storage)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.storage.stop()

    def _assert_unavailable(self, method: str, path: str) -> None:
        response = _client().request(method, path)

        self.assertEqual(response.status_code, 503, response.text)
        detail = response.json()["detail"]
        self.assertTrue(detail.strip(), "503 的 detail 不能是空的")
        self.assertIn(UNAVAILABLE_DETAIL, detail)

    async def test_not_started_storage_maps_to_503_with_readable_detail(self):
        for method, path in UNAVAILABLE_REQUESTS:
            with self.subTest(path=path):
                self._assert_unavailable(method, path)

    async def test_failed_start_maps_to_503_with_readable_detail(self):
        with mock.patch.object(pg_backend, "connect", _refuse_connect):
            self.assertFalse(await self.storage.start())
        self.assertIsNone(self.storage._conn)

        for method, path in UNAVAILABLE_REQUESTS:
            with self.subTest(path=path):
                self._assert_unavailable(method, path)

    async def test_plain_failure_detail_is_never_empty(self):
        """非「不可用」的普通失败仍是 500，但 detail 要能读出原因。"""

        async def _boom(*args, **kwargs):
            raise RuntimeError("")

        with mock.patch.object(self.storage, "query", _boom):
            response = _client().get("/api/logs?limit=1")

        self.assertEqual(response.status_code, 500, response.text)
        detail = response.json()["detail"]
        self.assertTrue(detail.strip(), "500 的 detail 不能是空的——现场只能猜")
        self.assertIn("RuntimeError", detail)

    async def test_storage_stays_available_after_normal_start(self):
        """回归：正常 start 之后五个入口照常可用（真实 PG 测试库）。

        测试进程里 `DISABLE_BACKGROUND_TASKS=true`，lifespan 不会去起日志存储，
        所以这里自己 start / stop —— 走的就是「启动成功」的那条状态。
        """
        import main as main_module

        with mock.patch.object(main_module, "log_storage", self.storage), mock.patch.object(
            settings, "ADMIN_API_KEY", "test-admin-key"
        ):
            headers = {"X-Admin-Token": "test-admin-key"}
            with TestClient(main_module.app) as client:
                self.assertTrue(
                    await self.storage.start(), "日志存储应能连上 PG 测试库"
                )
                try:
                    body = client.get("/api/logs?limit=1", headers=headers)
                    self.assertEqual(body.status_code, 200, body.text)
                    self.assertTrue(body.json()["success"])

                    stats = client.get("/api/logs/stats", headers=headers)
                    self.assertEqual(stats.status_code, 200, stats.text)
                    self.assertEqual(stats.json()["backend"], "postgresql")

                    facets = client.get("/api/logs/facets", headers=headers)
                    self.assertEqual(facets.status_code, 200, facets.text)
                    self.assertIn("levels", facets.json())

                    recent = client.get(
                        "/api/logs/persisted/recent?limit=1", headers=headers
                    )
                    self.assertEqual(recent.status_code, 200, recent.text)

                    cleanup = client.post("/api/logs/cleanup?days=7", headers=headers)
                    self.assertEqual(cleanup.status_code, 200, cleanup.text)
                finally:
                    await self.storage.stop()


if __name__ == "__main__":
    unittest.main()
