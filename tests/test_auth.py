#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

import aiosqlite
from config import settings
from database import DatabaseManager


class AuthSchemaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name

    async def asyncTearDown(self):
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_auth_db_has_required_tables(self):
        db = DatabaseManager()
        self.assertTrue(await db.connect())
        auth_path = settings.DATABASE_PATHS["auth"]
        self.assertTrue(os.path.isfile(auth_path))
        async with aiosqlite.connect(auth_path) as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in await cursor.fetchall()}
        self.assertIn("admin_user", tables)
        self.assertIn("sessions", tables)
        self.assertIn("api_tokens", tables)
        await db.close()


import asyncio
from services import auth_service
from services.app_runtime import AppRuntime, set_runtime


def _run(coro):
    return asyncio.run(coro)


class AuthServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        set_runtime(AppRuntime(db=self.db))

    async def asyncTearDown(self):
        await self.db.close()
        set_runtime(None)
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_init_and_login_flow(self):
        self.assertFalse(await auth_service.is_initialized())
        await auth_service.init_user("admin", "password123")
        self.assertTrue(await auth_service.is_initialized())
        user = await auth_service.authenticate("admin", "password123")
        self.assertIsNotNone(user)
        self.assertIsNone(await auth_service.authenticate("admin", "wrong"))

    async def test_session_lifecycle(self):
        await auth_service.init_user("admin", "password123")
        session_id, expires_at = await auth_service.create_session(remember=False)
        self.assertTrue(await auth_service.validate_session_id(session_id))
        await auth_service.delete_session(session_id)
        self.assertFalse(await auth_service.validate_session_id(session_id))

    async def test_api_token_issue_and_validate(self):
        await auth_service.init_user("admin", "password123")
        plain, meta = await auth_service.issue_api_token(label="kds-1")
        self.assertTrue(await auth_service.validate_api_token(plain))
        await auth_service.revoke_api_token(meta["token_hash"])
        self.assertFalse(await auth_service.validate_api_token(plain))

    async def test_long_password_over_72_bytes(self):
        long_password = "密" * 30 + "x" * 50
        self.assertGreater(len(long_password.encode("utf-8")), 72)
        await auth_service.init_user("admin", long_password)
        user = await auth_service.authenticate("admin", long_password)
        self.assertIsNotNone(user)

    async def test_min_length_password_accepted(self):
        password = "12345678"
        self.assertEqual(len(password), settings.AUTH_MIN_PASSWORD_LENGTH)
        await auth_service.init_user("admin", password)
        self.assertIsNotNone(await auth_service.authenticate("admin", password))


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
from services import auth_service


@pytest.fixture
def auth_client(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    app = FastAPI()
    app.include_router(auth_module.router)
    with TestClient(app) as client:
        yield client, db
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def test_auth_init_and_status(auth_client):
    client, _db = auth_client
    status = client.get("/api/auth/status").json()
    assert status["initialized"] is False
    assert status["logged_in"] is False
    resp = client.post("/api/auth/init", json={
        "username": "admin",
        "password": "password123",
        "confirm_password": "password123",
    })
    assert resp.status_code == 200
    assert resp.cookies.get(settings.SESSION_COOKIE_NAME)
    status2 = client.get("/api/auth/status").json()
    assert status2["initialized"] is True
    assert status2["logged_in"] is True


def test_auth_init_twice_conflict(auth_client):
    client, _ = auth_client
    client.post("/api/auth/init", json={
        "username": "admin",
        "password": "password123",
        "confirm_password": "password123",
    })
    resp = client.post("/api/auth/init", json={
        "username": "admin2",
        "password": "password1234",
        "confirm_password": "password1234",
    })
    assert resp.status_code == 409


def test_auth_login_issue_api_token(auth_client):
    client, _ = auth_client
    client.post("/api/auth/init", json={
        "username": "admin",
        "password": "password123",
        "confirm_password": "password123",
    })
    client.cookies.clear()
    resp = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "password123",
        "remember": False,
        "issue_api_token": True,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body.get("api_token")


def test_verify_admin_token_accepts_session_cookie(auth_client):
    client, _ = auth_client
    client.post("/api/auth/init", json={
        "username": "admin",
        "password": "password123",
        "confirm_password": "password123",
    })
    from fastapi import Depends, FastAPI
    from api.security import verify_admin_token

    app = FastAPI()

    @app.get("/protected")
    async def protected(_=Depends(verify_admin_token)):
        return {"ok": True}

    with TestClient(app) as c:
        c.cookies.set(settings.SESSION_COOKIE_NAME, client.cookies.get(settings.SESSION_COOKIE_NAME))
        resp = c.get("/protected")
        assert resp.status_code == 200


def test_verify_admin_token_accepts_api_token(auth_client):
    client, _ = auth_client
    client.post("/api/auth/init", json={
        "username": "admin",
        "password": "password123",
        "confirm_password": "password123",
    })
    login = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "password123",
        "issue_api_token": True,
    }).json()
    token = login["api_token"]

    from fastapi import Depends, FastAPI
    from api.security import verify_admin_token

    app = FastAPI()

    @app.get("/protected")
    async def protected(_=Depends(verify_admin_token)):
        return {"ok": True}

    with TestClient(app) as c:
        resp = c.get("/protected", headers={"X-Admin-Token": token})
        assert resp.status_code == 200
        resp2 = c.get("/protected")
        assert resp2.status_code == 401


@pytest.fixture
def auth_app_client(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    import main as main_module
    with TestClient(main_module.app) as client:
        yield client, main_module.db_manager
    settings.DATABASE_DIR = old


def test_unauthenticated_root_redirects(auth_app_client):
    client, _ = auth_app_client
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")


def test_login_page_accessible_without_session(auth_app_client):
    client, _ = auth_app_client
    resp = client.get("/login")
    assert resp.status_code == 200


def test_static_asset_accessible_without_session(auth_app_client):
    # 静态资源（.css/.js 等）应被 HtmlAuthMiddleware 按后缀豁免，未登录也能加载，
    # 否则 SPA 登录页自身的样式/脚本会被 302 重定向而无法渲染。
    client, _ = auth_app_client
    resp = client.get("/recipe.css")
    assert resp.status_code == 200


import api.orders as orders_module
from datetime import datetime
from database import CHINA_TZ


@pytest.fixture
def orders_client(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    _run(auth_service.init_user("admin", "password123"))
    plain, _ = _run(auth_service.issue_api_token(label="kds-test"))
    admin_headers = {"X-Admin-Token": plain}

    app = FastAPI()
    app.include_router(orders_module.router)

    async def _get_db():
        return db

    app.dependency_overrides[orders_module.get_db] = _get_db
    with TestClient(app) as client:
        yield client, db, admin_headers
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _seed_pending(db):
    _run(db.batch_insert_orders([{
        "business_flow_id": "cook-001",
        "table_number": "B2",
        "dish_name": "虾饺",
        "quantity": 3,
        "order_time": datetime(2026, 6, 30, 11, 0, tzinfo=CHINA_TZ),
        "station": "shulong",
        "status": "未结",
    }]))


def test_complete_cooking_requires_auth(orders_client):
    client, db, _admin_headers = orders_client
    _seed_pending(db)
    row = _run(db.get_orders(limit=1))[0]
    resp = client.post("/api/orders/complete-cooking", json={
        "dish_name": "虾饺",
        "station": "shulong",
        "complete_quantity": 3,
        "orders": [{
            "order_id": row["_id"],
            "table_number": "B2",
            "complete_quantity": 3,
            "original_quantity": 3,
        }],
    })
    assert resp.status_code == 401


def test_orders_get_without_auth(orders_client):
    client, db, _ = orders_client
    _seed_pending(db)
    resp = client.get("/api/orders/")
    assert resp.status_code == 200
