#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""库不可用要有「体面出口」：503 + 可重试，而不是被压成 500（DATA-02）。

2026-09-23 12:06 整库恢复：``_reconnect_raw()`` 把 ``_raw`` 摘空的那段窗口里，
并发业务请求命中 ``RuntimeError("PostgreSQL 连接正在重连，请稍后重试")``。它不是
``HTTPException``，于是被 ``main.py`` 的全局处理器压成 500「服务器内部错误」——
同一时刻 ``/api/healthz`` 还在正确地报 503，探针说「不健康」、用户看到「服务器崩了」。

修复后的契约分三层，各自钉一个用例：

1. 驱动层统一抛领域异常：``DatabaseUnavailable``（不可用）/ ``DatabaseReconnecting``
   （重连窗口、事务被打断）/ ``DatabaseBusy``（写锁排队超时），三种形态在 HTTP 层
   只该有一种出路；
2. HTTP 层映射 503 + ``Retry-After`` + ``retryable: true``，业务路由（读和写）
   在窗口内快速失败，窗口关掉后自动恢复 200；
3. 同一窗口只留一条结构化日志，不再逐请求打 traceback。
"""

import asyncio
import contextlib
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import main as main_module
from database import DatabaseManager
from db_core.backend import pg as pg_backend
from db_core.backend.pg import (
    DatabaseBusy,
    DatabaseReconnecting,
    DatabaseUnavailable,
)

# 免鉴权的配方阅读面：窗口内它必须 503，而不是 500、也不是白屏（RECIPE_READER_META.public）。
BUSINESS_ROUTE = "/api/recipes/stations"
# 真实存在的写路由（业务面统一挂 verify_admin_token）：窗口内必须在中间件就被拦下。
WRITE_ROUTE = "/api/dish-stations/"
# 把 asyncpg.connect 拖慢，好让「重连窗口」稳定地张开足够长的时间给 HTTP 请求撞进去。
SLOW_CONNECT_SECONDS = 0.6


class DatabaseUnavailableContractTest(unittest.IsolatedAsyncioTestCase):
    """驱动层：三种「库不可用」形态统一成领域异常。"""

    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect(), "测试库连接失败")
        self.pg = self.db._connection._pg

    async def asyncTearDown(self):
        # 用例把连接置成了窗口态，先恢复再关，免得 close() 撞见摘空的 _raw。
        self.pg._reconnecting = False
        with contextlib.suppress(Exception):
            await self.db.close()

    async def test_window_state_raises_database_reconnecting_everywhere(self):
        """窗口内：``execute`` / ``ensure_transaction`` / ``executemany`` 一个口径。"""
        saved_raw = self.pg._raw
        self.pg._raw = None
        self.pg._reconnecting = True
        try:
            self.assertFalse(self.pg.alive(), "重连窗口里连接不算可用")

            with self.assertRaises(DatabaseReconnecting) as cursor_ctx:
                await self.pg.execute("SELECT 1")
            with self.assertRaises(DatabaseReconnecting):
                await self.pg.ensure_transaction()  # 写事务入口同样给领域异常
            with self.assertRaises(DatabaseReconnecting):
                await self.pg.executemany("SELECT 1", [()])

            exc = cursor_ctx.exception
            self.assertIsInstance(exc, DatabaseUnavailable, "领域异常同一棵树")
            self.assertEqual(exc.reason, "reconnecting")
            self.assertTrue(exc.retryable, "可重试语义要能直接读出来")
        finally:
            self.pg._raw = saved_raw
            self.pg._reconnecting = False

    async def test_closed_connection_is_database_unavailable_not_interface_error(self):
        """``InterfaceError: connection is closed`` 也要变成领域异常。

        修复前它原样冒到路由层：不是 ``HTTPException`` → 500。现在 ``execute``
        自己把它映射成 ``DatabaseUnavailable``（可重试），而不是让调用方去认
        asyncpg 的异常类型。
        """
        raw = self.pg._raw
        await raw.close()
        with self.assertRaises(DatabaseUnavailable) as ctx:
            await self.pg.execute("SELECT 1")
        self.assertIsInstance(ctx.exception, DatabaseUnavailable)
        self.assertNotIsInstance(ctx.exception, DatabaseReconnecting)
        self.assertEqual(ctx.exception.reason, "unavailable")
        self.assertIn("未执行", str(ctx.exception), "要说清「这次没执行」")

    def test_exception_tree_is_documented(self):
        """异常层级是公开契约，别在重构里悄悄改掉。"""
        self.assertTrue(issubclass(DatabaseReconnecting, DatabaseUnavailable))
        self.assertTrue(issubclass(DatabaseBusy, DatabaseUnavailable))
        # 历史调用方里的 ``except RuntimeError`` 兜底语义不变。
        self.assertTrue(issubclass(DatabaseUnavailable, RuntimeError))


class DatabaseUnavailableHttpTest(unittest.TestCase):
    """HTTP 层：窗口内业务路由 503 + retryable，窗口关掉后自动恢复。"""

    def test_window_returns_503_with_retryable_then_recovers(self):
        with TestClient(main_module.app) as client:
            db = main_module.db_manager
            self.assertIsNotNone(db, "lifespan 应当已经建好 db_manager")
            pg = db._connection._pg

            # 前置：两条路由平时都是「真的能走」的（写路由至少能走到鉴权）。
            baseline = client.get(BUSINESS_ROUTE)
            self.assertEqual(baseline.status_code, 200, baseline.text)
            unauthenticated = client.post(
                WRITE_ROUTE, json={"dish_name": "探针", "station": "探针"}
            )
            self.assertIn(
                unauthenticated.status_code,
                (401, 403),
                f"写路由应当存在且要鉴权，实际 {unauthenticated.status_code}",
            )

            loop = pg._loop
            original_connect = pg_backend.asyncpg.connect

            async def _slow_connect(*args, **kwargs):
                await asyncio.sleep(SLOW_CONNECT_SECONDS)
                return await original_connect(*args, **kwargs)

            with mock.patch.object(pg_backend.asyncpg, "connect", _slow_connect):
                future = asyncio.run_coroutine_threadsafe(
                    pg._reconnect_raw(loop, "测试用重连窗口"), loop
                )
                try:
                    deadline = time.monotonic() + 3
                    while not pg.reconnecting and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(pg.reconnecting, "重连窗口应当已经打开")

                    with self.assertLogs(
                        main_module.logger, level="WARNING"
                    ) as captured:
                        read_in_window = client.get(BUSINESS_ROUTE)
                        read_again = client.get(BUSINESS_ROUTE)
                        write_in_window = client.post(
                            WRITE_ROUTE, json={"dish_name": "断", "station": "试试"}
                        )
                        healthz = client.get("/api/healthz")

                    for response in (read_in_window, read_again, write_in_window):
                        self.assertEqual(
                            response.status_code,
                            503,
                            f"窗口内业务路由应当是 503，不是 500：{response.text}",
                        )
                        body = response.json()
                        self.assertTrue(body.get("retryable"), body)
                        self.assertEqual(response.headers.get("retry-after"), "1")

                    # 就绪/健康探针复用同一判定：窗口内报 reconnecting，而不是「健康」。
                    self.assertEqual(healthz.status_code, 503, healthz.text)
                    self.assertEqual(healthz.json().get("db"), "reconnecting")
                finally:
                    future.result(timeout=10)

            window_records = [
                record
                for record in captured.records
                if "数据库不可用" in record.getMessage()
            ]
            self.assertEqual(
                len(window_records),
                1,
                "同一窗口只该记一条结构化记录，不该逐请求打 traceback",
            )
            self.assertIn("reconnecting", window_records[0].getMessage())

            # 窗口关掉后自动恢复：不需要重启、不需要人工介入。
            recovered = client.get(BUSINESS_ROUTE)
            self.assertEqual(recovered.status_code, 200, recovered.text)


class IndependentUnavailableExitTest(unittest.TestCase):
    """独立验证（verifier-db）：补齐实现者用例没覆盖的两个出口角度。

    实现者走的是「重连窗口 + 中间件」；这里补：

    * 写锁排队超时（``DatabaseBusy``）必须也出 503 + ``retryable``——它不走中间件，
      走的是 ``main.py`` 注册的领域异常处理器，而那条注册关系正是容易漏的地方；
    * 窗口内 ``/api/system/health``（免鉴权探针）与 ``/api/healthz`` 判据一致：
      两边都不许说「健康」。
    """

    BUSY_PROBE_PATH = "/__verify_t1_busy_probe"

    def test_database_busy_maps_to_503_retryable_in_the_production_app(self):
        """DatabaseBusy → 503 + Retry-After + retryable + reason=write_lock_timeout。"""
        from fastapi.routing import APIRoute

        async def _busy_probe():
            raise DatabaseBusy()

        probe_route = APIRoute(self.BUSY_PROBE_PATH, _busy_probe, methods=["GET"])
        main_module.app.router.routes.append(probe_route)
        try:
            self.assertIs(
                main_module.app.exception_handlers.get(DatabaseUnavailable),
                main_module.database_unavailable_handler,
                "生产 app 必须把 DatabaseUnavailable（含 DatabaseBusy / "
                "DatabaseReconnecting 子类）注册到领域异常处理器上",
            )
            with TestClient(main_module.app) as client:
                response = client.get(self.BUSY_PROBE_PATH)

            self.assertEqual(response.status_code, 503, response.text)
            body = response.json()
            self.assertTrue(body.get("retryable"), body)
            self.assertEqual(body.get("reason"), "write_lock_timeout", body)
            self.assertEqual(response.headers.get("retry-after"), "1")
            self.assertNotIn("服务器内部错误", response.text)
        finally:
            main_module.app.router.routes.remove(probe_route)

    def test_probe_endpoints_agree_inside_a_reconnect_window(self):
        """窗口内两条探针一致：``/api/healthz`` 503 + reconnecting，就绪面 unhealthy。"""
        with TestClient(main_module.app) as client:
            db = main_module.db_manager
            self.assertIsNotNone(db, "lifespan 应当已经建好 db_manager")
            pg = db._connection._pg
            before = client.get("/api/system/health")
            self.assertEqual(before.status_code, 200, before.text)

            loop = pg._loop
            original_connect = pg_backend.asyncpg.connect

            async def _slow_connect(*args, **kwargs):
                await asyncio.sleep(SLOW_CONNECT_SECONDS)
                return await original_connect(*args, **kwargs)

            with mock.patch.object(pg_backend.asyncpg, "connect", _slow_connect):
                future = asyncio.run_coroutine_threadsafe(
                    pg._reconnect_raw(loop, "独立验证重连窗口"), loop
                )
                try:
                    deadline = time.monotonic() + 3
                    while not pg.reconnecting and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(pg.reconnecting, "重连窗口应当已经打开")

                    in_window = client.get("/api/system/health")
                    healthz = client.get("/api/healthz")
                finally:
                    future.result(timeout=10)

            self.assertEqual(
                healthz.status_code, 503, f"窗口内 healthz 应当 503：{healthz.text}"
            )
            self.assertEqual(healthz.json().get("db"), "reconnecting")
            # KDS 约定：就绪探针恒 200，但结论必须与 healthz 一致（不许说健康）。
            self.assertEqual(in_window.status_code, 200, in_window.text)
            body = in_window.json()
            self.assertEqual(body.get("status"), "unhealthy", body)
            self.assertFalse(body.get("ready"), body)
            self.assertFalse(body.get("db_connected"), body)
            self.assertFalse(body.get("migrations_complete"), body)

            # 窗口关掉后回到窗口前的结论（不依赖此刻迁移是否全部已应用）。
            after = client.get("/api/system/health")
            self.assertEqual(after.status_code, 200, after.text)
            self.assertEqual(
                after.json().get("status"), before.json().get("status"), after.text
            )


if __name__ == "__main__":
    unittest.main()
