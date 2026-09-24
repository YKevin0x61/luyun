#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from config import settings
from database import CHINA_TZ, DatabaseManager


class AuthSchemaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name

    async def asyncTearDown(self):
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_auth_tables_live_in_the_business_database(self):
        """auth 三张表与业务表同库（PG 唯一后端，ADR 0089 起没有独立的 auth .db）。"""
        import pg_probe

        db = DatabaseManager()
        self.assertTrue(await db.connect())
        try:
            rows = await pg_probe.fetch_all_async(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            tables = {row[0] for row in rows}
        finally:
            await db.close()
        self.assertIn("admin_user", tables)
        self.assertIn("sessions", tables)
        self.assertIn("api_tokens", tables)


import asyncio
import re
from datetime import datetime, timedelta

from services import auth_service
from services.app_runtime import AppRuntime, set_runtime

# 员工端路径名单前端那一份（服务端那两张表照它对表，见本文件末尾的契约用例）。
STAFF_PATHS_JS = (
    Path(__file__).resolve().parents[1] / "admin-web" / "src" / "utils" / "staffPaths.js"
)


def _run(coro):
    return asyncio.run(coro)


def _cookie_max_age(resp) -> int:
    """Set-Cookie 里的 Max-Age——httpx 的 resp.cookies 不暴露它。"""
    header = resp.headers["set-cookie"]
    match = re.search(r"max-age=(\d+)", header, flags=re.IGNORECASE)
    assert match, header
    return int(match.group(1))


async def _session_expires_at(db: DatabaseManager, session_id: str) -> datetime:
    async with db.table("auth").conn.cursor() as cursor:
        await cursor.execute(
            "SELECT expires_at FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
    assert row is not None, "session 未落库"
    expires_at = datetime.fromisoformat(row["expires_at"])
    return expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=CHINA_TZ)


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
from fastapi import Depends, FastAPI
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


def test_login_remember_extends_cookie_and_session(auth_client):
    """「记住密码，自动登录」= 30 天会话 + 同长度 cookie；不勾选仍是班次级 TTL。"""
    client, db = auth_client
    client.post("/api/auth/init", json={
        "username": "admin",
        "password": "password123",
        "confirm_password": "password123",
    })
    client.cookies.clear()

    remembered = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "password123",
        "remember": True,
    })
    assert remembered.status_code == 200
    assert _cookie_max_age(remembered) == settings.SESSION_REMEMBER_DAYS * 86400
    remembered_expires = _run(
        _session_expires_at(db, remembered.cookies.get(settings.SESSION_COOKIE_NAME))
    )
    assert remembered_expires - datetime.now(CHINA_TZ) > timedelta(
        days=settings.SESSION_REMEMBER_DAYS - 1
    )

    client.cookies.clear()
    session_only = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "password123",
        "remember": False,
    })
    assert session_only.status_code == 200
    assert _cookie_max_age(session_only) == settings.SESSION_TTL_HOURS * 3600
    session_expires = _run(
        _session_expires_at(db, session_only.cookies.get(settings.SESSION_COOKIE_NAME))
    )
    assert session_expires - datetime.now(CHINA_TZ) <= timedelta(
        hours=settings.SESSION_TTL_HOURS
    )


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
def auth_app_client(tmp_path, monkeypatch):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    import main as main_module
    stub = tmp_path / "spa-index.html"
    stub.write_text("<!doctype html><title>spa stub</title>", encoding="utf-8")
    monkeypatch.setattr(main_module, "spa_index_path", str(stub))
    with TestClient(main_module.app) as client:
        yield client, main_module.db_manager
    settings.DATABASE_DIR = old


def _html_headers():
    return {"Accept": "text/html"}


def test_unauthenticated_root_redirects(auth_app_client):
    client, _ = auth_app_client
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")


def test_login_page_accessible_without_session(auth_app_client):
    client, _ = auth_app_client
    resp = client.get("/login")
    assert resp.status_code == 200


def test_spa_index_missing_returns_404(tmp_path, monkeypatch):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    import main as main_module
    monkeypatch.setattr(
        main_module, "spa_index_path", str(tmp_path / "missing-index.html")
    )
    try:
        with TestClient(main_module.app) as client:
            resp = client.get("/login")
        assert resp.status_code == 404
    finally:
        settings.DATABASE_DIR = old


def test_static_asset_accessible_without_session(auth_app_client):
    # 静态资源（.css/.js 等）应被 HtmlAuthMiddleware 按后缀豁免，未登录也能加载，
    # 否则 SPA 登录页自身的样式/脚本会被 302 重定向而无法渲染。
    client, _ = auth_app_client
    resp = client.get("/recipe.css")
    assert resp.status_code == 200
    hygiene_css = client.get("/hygiene-admin.css")
    assert hygiene_css.status_code == 200


def test_recipe_reader_pages_accessible_without_session(auth_app_client):
    client, _ = auth_app_client
    for path in ("/recipe", "/recipe/detail", "/recipe/print", "/recipe/qr"):
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        loc = resp.headers.get("location", "")
        assert resp.status_code != 302 or "/login" not in loc, path


def test_recipe_manage_html_requires_session(auth_app_client):
    client, _ = auth_app_client
    resp = client.get("/recipe/manage", headers=_html_headers(), follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")


def test_setup_html_requires_session(auth_app_client):
    client, _ = auth_app_client
    resp = client.get("/setup", headers=_html_headers(), follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")


def test_kds_html_not_redirected_to_login(auth_app_client):
    client, _ = auth_app_client
    resp = client.get("/kds", headers=_html_headers(), follow_redirects=False)
    loc = resp.headers.get("location", "")
    assert "/login" not in loc


def test_staff_phone_pages_accessible_without_admin_session(auth_app_client):
    # 员工手机上那两块的页（票 05 起落点是排班的 /today，票 06 多了 /today/month）：
    # 没有管理端会话也不许被甩到 /login —— 管理端登录表单只认管理端账号，员工在那儿登不进去。
    # `/today/`、`/today/month` 都不是精确表里的条目：前者靠尾斜杠、后者靠 `/today/` 前缀
    # （书签/外链/手输都可能带来）。
    client, _ = auth_app_client
    for path in (
        "/hygiene",
        "/hygiene/login",
        "/hygiene/register",
        "/today",
        "/today/",
        "/today/month",
        "/today/month/",
    ):
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        loc = resp.headers.get("location", "")
        assert resp.status_code != 302 or "/login" not in loc, path


def test_staff_path_list_matches_the_frontend_copy():
    """员工端路径名单前端也有一份：两边必须对得上。

    名单的唯一一份判据在 `admin-web/src/utils/staffPaths.js`（路由守卫、401 白名单、
    PWA 清单归属都从它出发）。服务端这边少登记一条，员工手机硬导航那一页就被 302 到
    管理端 `/login`（票 05 的 `/today` 正是这么漏过一次）；前端多一条，管理端页面就被
    当成员工页。所以这里按 JS 那份逐条对 `main.py` 的两张表，而不是各写一份清单。
    """
    import main as main_module

    source = STAFF_PATHS_JS.read_text(encoding="utf-8")
    block = re.search(r"STAFF_PHONE_PREFIXES = \[(.*?)\]", source, re.S)
    assert block, STAFF_PATHS_JS
    prefixes = re.findall(r"'([^']+)'", block.group(1))
    assert prefixes, "名单是空的：解析规则或文件结构变了，这条契约要跟着改"

    for prefix in prefixes:
        assert prefix in main_module.HTML_AUTH_EXACT, prefix
        # 尾斜杠变体走前缀表（书签/外链/手输带来的 `/today/`）。
        assert f"{prefix}/" in main_module.HTML_AUTH_PREFIXES, prefix


def test_hygiene_roster_html_requires_admin_session(auth_app_client):
    client, _ = auth_app_client
    for path in (
        "/hygiene-roster",
        "/hygiene-zones",
        "/hygiene-daily",
        "/hygiene-deep-clean",
        "/hygiene-fix",
        "/hygiene-boards",
    ):
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 302, path
        assert resp.headers["location"].startswith("/login"), path


def test_html_auth_preserves_query_in_next(auth_app_client):
    client, _ = auth_app_client
    resp = client.get(
        "/admin",
        params={"tab": "orders"},
        headers=_html_headers(),
        follow_redirects=False,
    )
    assert resp.status_code == 302
    from urllib.parse import parse_qs, urlparse

    next_val = parse_qs(urlparse(resp.headers["location"]).query).get("next", [""])[0]
    assert next_val == "/admin?tab=orders"


import api.orders as orders_module
from datetime import datetime
from database import CHINA_TZ
from api.security import verify_admin_token


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
    # 按 main.py 的注册方式挂载：业务面（含读接口）在 include_router 处统一挂凭据，
    # 见 SEC-02 / tests/test_api_read_auth.py。
    app.include_router(
        orders_module.router, dependencies=[Depends(verify_admin_token)]
    )

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


def test_orders_get_without_auth_is_rejected(orders_client):
    """SEC-02：订单列表是业务数据，无凭据必须 401（以前这里断言的是 200）。"""
    client, db, _ = orders_client
    _seed_pending(db)
    resp = client.get("/api/orders/")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "未授权"}


def test_orders_get_with_token_still_works(orders_client):
    client, db, admin_headers = orders_client
    _seed_pending(db)
    resp = client.get(
        "/api/orders/",
        params={"start_time": "2026-06-30", "end_time": "2026-06-30"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert [row["dish_name"] for row in resp.json()["data"]] == ["虾饺"]
