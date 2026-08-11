#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP 鉴权加固回归测试（合并 Task 1.1 + 1.2 + 1.3）。

- Task 1.1：消除"未初始化即开放写接口"后门。
- Task 1.2：`/api/logs` 只读接口纳入 admin 鉴权。
- Task 1.3：`/api/recipes` 写操作纳入 admin 鉴权。

注：为避免触发 `main.app` 完整 lifespan（会启动真实爬虫适配器并可能连接生产 POS
系统），这里沿用 `tests/test_kds_orders.py` / `tests/test_recipe_api.py` 的做法——
构建只挂载目标路由的最小 FastAPI 应用，手动接管 `auth_service` 的数据库依赖。
"""

import asyncio
import sqlite3
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import settings
from database import DatabaseManager
from services import auth_service
from services.app_runtime import AppRuntime, set_runtime


def _run(coro):
    return asyncio.run(coro)


class TestUninitializedBackdoor(unittest.TestCase):
    """Task 1.1：未初始化 + 无 ADMIN_API_KEY + 非本机来源 → 写接口必须 401。"""

    def setUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        _run(self.db.connect())
        set_runtime(AppRuntime(db=self.db))  # 未调用 init_user，保持“未初始化”状态

        import api.logs as logs_module
        self.app = FastAPI()
        self.app.include_router(logs_module.router)

    def tearDown(self):
        _run(self.db.close())
        set_runtime(None)
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def test_remote_write_rejected_when_uninitialized(self):
        # 未初始化 + 无 ADMIN_API_KEY + 非本机来源 → 写接口必须 401。
        # TestClient 的 request.client.host 默认为 "testclient"（非 127.0.0.1/::1），
        # 因此无论是否携带 x-forwarded-for，都应视为非本机来源。
        with TestClient(self.app) as client:
            resp = client.post(
                "/api/logs/cleanup?days=7",
                headers={"x-forwarded-for": "203.0.113.9"},
            )
        self.assertEqual(resp.status_code, 401)


class TestLogsReadRequiresAuth(unittest.TestCase):
    """Task 1.2：/api/logs 只读接口需要鉴权。"""

    def setUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        _run(self.db.connect())
        set_runtime(AppRuntime(db=self.db))
        _run(auth_service.init_user("admin", "password123"))

        import api.logs as logs_module
        self.app = FastAPI()
        self.app.include_router(logs_module.router)

    def tearDown(self):
        _run(self.db.close())
        set_runtime(None)
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def test_logs_get_requires_auth(self):
        with TestClient(self.app) as client:
            resp = client.get("/api/logs?limit=1")
        self.assertEqual(resp.status_code, 401)


def _seed_recipe_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sop_stations (slug TEXT PRIMARY KEY, title TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE sop_recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, station_slug TEXT NOT NULL, section TEXT NOT NULL,
            recipe_name TEXT NOT NULL, body_markdown TEXT NOT NULL, sort_order INTEGER NOT NULL,
            is_new INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
        INSERT INTO sop_stations VALUES ('changfen','肠粉档','2026-01-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()


class TestRecipesWriteRequiresAuth(unittest.TestCase):
    """Task 1.3：/api/recipes 写操作需要鉴权，只读路由保持公开。"""

    def setUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        _run(self.db.connect())
        set_runtime(AppRuntime(db=self.db))
        _run(auth_service.init_user("admin", "password123"))

        from services.recipes.store import RecipeStore
        import api.recipes as recipes_module
        self.recipes_module = recipes_module

        self._recipe_db_path = f"{self._tmpdir.name}/recipes.db"
        _seed_recipe_db(self._recipe_db_path)
        self.store = RecipeStore(self._recipe_db_path)
        _run(self.store.connect())

        self.app = FastAPI()
        self.app.include_router(recipes_module.router)
        self.app.dependency_overrides[recipes_module._get_recipe_store] = lambda: self.store

    def tearDown(self):
        _run(self.store.close())
        _run(self.db.close())
        set_runtime(None)
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def test_create_recipe_requires_auth(self):
        with TestClient(self.app) as client:
            resp = client.post(
                "/api/recipes/stations/changfen/recipes",
                json={"section": "配方", "recipe_name": "新", "body": "x", "is_new": False},
            )
        self.assertEqual(resp.status_code, 401)

    def test_delete_station_requires_auth(self):
        with TestClient(self.app) as client:
            resp = client.delete("/api/recipes/stations/changfen")
        self.assertEqual(resp.status_code, 401)

    def test_list_stations_stays_public(self):
        with TestClient(self.app) as client:
            resp = client.get("/api/recipes/stations")
        self.assertEqual(resp.status_code, 200)

    def test_create_recipe_succeeds_with_session_cookie(self):
        session_id, _expires_at = _run(auth_service.create_session(remember=False))
        with TestClient(self.app) as client:
            client.cookies.set(settings.SESSION_COOKIE_NAME, session_id)
            resp = client.post(
                "/api/recipes/stations/changfen/recipes",
                json={"section": "配方", "recipe_name": "新", "body": "x", "is_new": False},
            )
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
