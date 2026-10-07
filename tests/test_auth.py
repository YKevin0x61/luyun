#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import json
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
# 节流窗口与员工端同一个值（`services/identity/accounts.py`）—— 票 10 的对齐要求之一。
from services.identity.accounts import LAST_SEEN_REFRESH_SECONDS

REPO_ROOT = Path(__file__).resolve().parents[1]

# 员工端路径名单前端那一份（服务端那两张表照它对表，见本文件末尾的契约用例）。
STAFF_PATHS_JS = REPO_ROOT / "admin-web" / "src" / "utils" / "staffPaths.js"


def _run(coro):
    return asyncio.run(coro)


def _cookie_max_age(resp) -> int:
    """Set-Cookie 里的 Max-Age——httpx 的 resp.cookies 不暴露它。"""
    header = resp.headers["set-cookie"]
    match = re.search(r"max-age=(\d+)", header, flags=re.IGNORECASE)
    assert match, header
    return int(match.group(1))


async def _session_row(db: DatabaseManager, session_id: str):
    """会话行（被删掉时返回 None）。"""
    async with db.table("auth").conn.cursor() as cursor:
        await cursor.execute(
            "SELECT expires_at, last_seen_at FROM sessions WHERE session_id = ?", (session_id,)
        )
        return await cursor.fetchone()


async def _session_expires_at(db: DatabaseManager, session_id: str) -> datetime:
    row = await _session_row(db, session_id)
    assert row is not None, "session 未落库"
    expires_at = datetime.fromisoformat(row["expires_at"])
    return expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=CHINA_TZ)


async def _set_last_seen(db: DatabaseManager, session_id: str, when: datetime) -> None:
    """把「这个会话最后一次被用到」的时间挪到过去 —— 闲置上限判定的唯一输入。"""
    async with db.table("auth").conn.cursor() as cursor:
        await cursor.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE session_id = ?",
            (when.isoformat(), session_id),
        )
    await db.table("auth").conn.commit()


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


# 票面口径：管理端复用员工端那一个配置项（`SESSION_IDLE_HOURS`），默认 14 天。
# 下面的边界用例从这条字面量算「超限/未超限」，不拿被测代码读的那个值反推期望值。
IDLE_DAYS = 14


class AdminSessionIdleLimitTest(unittest.IsolatedAsyncioTestCase):
    """管理端会话的闲置上限（票 10，spec 故事 43 / 44）。

    行为与员工端一致：**先看绝对有效期，再看闲置上限**；在用的会话按
    `LAST_SEEN_REFRESH_SECONDS` 节流刷新 `last_seen_at`（闲置窗口随活动滑动），
    但**绝不延长 `expires_at`**（「记住我 30 天 / 否则 8 小时」两档一个字不改）。
    """

    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._old_idle_hours = settings.SESSION_IDLE_HOURS
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        set_runtime(AppRuntime(db=self.db))
        await auth_service.init_user("admin", "password123")

    async def asyncTearDown(self):
        settings.SESSION_IDLE_HOURS = self._old_idle_hours
        await self.db.close()
        set_runtime(None)
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_idle_limit_reuses_the_staff_setting_and_defaults_to_fourteen_days(self):
        """同一个配置项、同一个默认值：两套会话不该让人猜为什么这次要重登。"""
        self.assertEqual(settings.SESSION_IDLE_HOURS, IDLE_DAYS * 24)

    async def test_session_within_the_idle_limit_still_passes(self):
        """未超限放行：勾了「记住我」（绝对有效期 30 天）的会话闲置 13 天仍然有效。"""
        settings.SESSION_IDLE_HOURS = IDLE_DAYS * 24
        session_id, _ = await auth_service.create_session(remember=True)
        await _set_last_seen(
            self.db, session_id, datetime.now(CHINA_TZ) - timedelta(days=IDLE_DAYS - 1)
        )

        self.assertTrue(await auth_service.validate_session_id(session_id))
        self.assertIsNotNone(await _session_row(self.db, session_id), "放行的会话不该被删")

    async def test_session_over_the_idle_limit_is_logged_out(self):
        """超限登出：闲置 15 天（绝对有效期还没到）→ 拒绝，并把行删掉。"""
        settings.SESSION_IDLE_HOURS = IDLE_DAYS * 24
        session_id, _ = await auth_service.create_session(remember=True)
        await _set_last_seen(
            self.db, session_id, datetime.now(CHINA_TZ) - timedelta(days=IDLE_DAYS, hours=1)
        )

        self.assertFalse(await auth_service.validate_session_id(session_id))
        self.assertIsNone(
            await _session_row(self.db, session_id), "闲置超时要删行，而不是只拒绝这一次"
        )

    async def test_last_seen_refreshes_only_after_the_throttle_window(self):
        """节流刷新与员工端同值：窗口内不写库，过了窗口才把 last_seen_at 推到现在。"""
        session_id, _ = await auth_service.create_session(remember=True)

        before = (await _session_row(self.db, session_id))["last_seen_at"]
        self.assertTrue(await auth_service.validate_session_id(session_id))
        self.assertEqual(
            (await _session_row(self.db, session_id))["last_seen_at"],
            before,
            "节流窗口内的请求不该写库",
        )

        stale = datetime.now(CHINA_TZ) - timedelta(seconds=LAST_SEEN_REFRESH_SECONDS + 60)
        await _set_last_seen(self.db, session_id, stale)
        self.assertTrue(await auth_service.validate_session_id(session_id))
        refreshed = (await _session_row(self.db, session_id))["last_seen_at"]
        self.assertNotEqual(refreshed, stale.isoformat())
        self.assertGreater(datetime.fromisoformat(refreshed), stale)

    async def test_idle_limit_never_extends_the_absolute_expiry(self):
        """不加滑动续期：怎么用都不改 `expires_at`（那是「记住我 / 不记住」两档说了算的）。"""
        session_id, expires_at = await auth_service.create_session(remember=False)
        await _set_last_seen(
            self.db, session_id, datetime.now(CHINA_TZ) - timedelta(hours=2)
        )

        self.assertTrue(await auth_service.validate_session_id(session_id))
        self.assertEqual((await _session_row(self.db, session_id))["expires_at"], expires_at)


import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
from services import auth_service
from tests.hygiene_profile import approve_body, register_body


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


def test_idle_admin_session_is_rejected_on_a_protected_endpoint(auth_client):
    """闲置超限的管理端会话：受保护端点 401，且会话行被删掉（真实请求，不是内部调用）。"""
    client, db = auth_client
    client.post("/api/auth/init", json={
        "username": "admin",
        "password": "password123",
        "confirm_password": "password123",
    })
    client.cookies.clear()
    login = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "password123",
        "remember": True,
    })
    assert login.status_code == 200
    session_id = login.cookies.get(settings.SESSION_COOKIE_NAME)
    _run(_set_last_seen(
        db, session_id, datetime.now(CHINA_TZ) - timedelta(days=IDLE_DAYS, hours=1)
    ))

    resp = client.get("/api/auth/tokens")

    assert resp.status_code == 401
    assert _run(_session_row(db, session_id)) is None


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


def test_recipe_reader_pages_require_a_session(auth_app_client):
    """票 07：配方阅读面从**免登录**改成要登录（本仓唯一一次推翻既有刻意设计）。

    旧口径是「扫码即看」：`/recipe*` 四页在 `HTML_AUTH_PUBLIC_PAGES` 里、未登录也拿到
    外壳（那也正是「零鉴权、谁都能拉、连停用配方都能拉」那个缺陷的入口）。配方搬进工作台
    的「后勤」组之后，阅读面与工作台别的页一个待遇：未登录硬导航 302 到
    `/login?next=<原地址>` —— **原地址要能原样回来（含 query）**，扫码那条链路
    （扫码 → 未登录 → 登录 → 回到那条配方）就压在这上面。
    """
    from urllib.parse import quote

    import main as main_module

    client, _ = auth_app_client
    for path in (
        "/workbench/kitchen/recipe",
        "/workbench/kitchen/recipe/detail",
        "/workbench/kitchen/recipe/print",
        "/workbench/kitchen/recipe/qr",
        "/workbench/kitchen/recipe/manage",
    ):
        assert path in main_module.SPA_PAGE_ROUTES, path
        assert path not in main_module.HTML_AUTH_PUBLIC_PAGES, path
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 302, (path, resp.status_code)
        assert resp.headers["location"] == f"/login?next={quote(path, safe='')}", path

    # 扫码进来的是带 slug 的那一条：query 必须一起回来，否则登录后回到的是列表页。
    scanned = "/workbench/kitchen/recipe/detail?slug=changfen"
    resp = client.get(scanned, headers=_html_headers(), follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == f"/login?next={quote(scanned, safe='')}"

    # 反向：旧的四条 `/recipe*` 不许留在页面豁免表里（留着它们，改门就只改了一半）。
    for stale in ("/recipe", "/recipe/detail", "/recipe/print", "/recipe/qr"):
        assert stale not in main_module.HTML_AUTH_PUBLIC_PAGES, stale


def test_recipe_pages_open_for_a_logged_in_admin(auth_app_client):
    """另一半：有会话就拿到外壳（登录墙放行的根据是会话，不是路径）。

    未登录被拦不等于页面作废 —— 登录之后每一条都得能开，否则「登录后回到那条配方」
    会落在一个 404 上。
    """
    from urllib.parse import quote

    client, _ = auth_app_client
    init = client.post("/api/auth/init", json=ADMIN_INIT)
    assert init.status_code == 200, init.text
    assert client.cookies.get(settings.SESSION_COOKIE_NAME)

    for path in (
        "/workbench/kitchen/recipe",
        "/workbench/kitchen/recipe/detail?slug=changfen",
        "/workbench/kitchen/recipe/print?slug=changfen",
        "/workbench/kitchen/recipe/qr",
        "/workbench/kitchen/recipe/manage",
    ):
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 200, (path, resp.status_code, quote(resp.text, safe=""))
        assert "text/html" in resp.headers["content-type"], path


def test_old_recipe_paths_are_gone(auth_app_client):
    """票 07：旧 `/recipe*` 随搬家作废 —— 不留别名、不留重定向、不再免墙。

    跟 `/staff/*`、`/hygiene/*`、`/scheduling*` 一个待遇（ADR 0092）：浏览器硬导航到旧
    地址跟任何一个未登录的受保护页一样被 302；非页面请求直接看路由表，确实是 404。
    `?next=` 里可能还存着旧地址，那由前端 `loginNext` 在入口换成新地址。
    """
    from urllib.parse import quote

    import main as main_module

    client, _ = auth_app_client
    for stale in ("/recipe", "/recipe/detail", "/recipe/print", "/recipe/qr", "/recipe/manage"):
        assert stale not in main_module.SPA_PAGE_ROUTES, stale
        assert stale not in main_module.HTML_AUTH_PUBLIC_PAGES, stale
        assert stale not in main_module.HTML_AUTH_EXACT, stale
        assert client.get(stale, follow_redirects=False).status_code == 404, stale
        nav = client.get(stale, headers=_html_headers(), follow_redirects=False)
        assert nav.status_code == 302, stale
        assert nav.headers["location"] == f"/login?next={quote(stale, safe='')}", stale


def test_recipe_reader_pages_open_for_a_staff_session(auth_app_client):
    """扫码的厨师：员工会话（没有管理端会话）硬导航阅读页 → 200 页面壳。

    这是「扫码 → 登录 → 回到那条配方」在服务端的另一半：登录之后落回来的是工作台里的
    一页，页面墙对 `/workbench/*` 认任一会话（`_has_staff_session`）。
    """
    client, _ = auth_app_client
    _staff_only_client(client)

    for path in (
        "/workbench/kitchen/recipe",
        "/workbench/kitchen/recipe/detail?slug=changfen",
        "/workbench/kitchen/recipe/print",
    ):
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 200, (path, resp.status_code)
        assert "text/html" in resp.headers["content-type"], path

    # 管理端那两页（配方管理、印码）：员工会话进得去壳（工作台认任一会话），页面级权限由
    # 前端守卫 `audience: admin` 与接口自己的 401 兜住 —— 这一条与服务端页面墙的分工一致。
    # 票 04 起印码页（`/workbench/kitchen/recipe/qr`）也归这一档：前端守卫会把员工送到
    # `/workbench/forbidden`（admin-web 的 `loginGuard.test.js` 压着那一条），服务端页面墙
    # 一个字没动 —— 页面能不能打开与接口能不能读是两件事。
    for path in ("/workbench/kitchen/recipe/manage", "/workbench/kitchen/recipe/qr"):
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 200, (path, resp.status_code)
        assert "text/html" in resp.headers["content-type"], path


def test_settings_html_requires_session(auth_app_client):
    """票 06：系统配置页从 `/setup` 改名 `/settings`，它需要登录 —— 从来不是初始化页。

    首次初始化（创建管理员账号）在 `/login` 的管理员栏里。配置页在免登录墙的**两张表
    之外**，未登录的浏览器硬导航必须被 302 到 `/login`，而不是拿到 SPA 壳；已登录的
    超级管理员直连则拿到壳（登录墙放行的根据是会话，不是路径）。
    """
    import main as main_module

    client, _ = auth_app_client

    resp = client.get("/settings", headers=_html_headers(), follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")

    # 反向断言：`/settings` 不许出现在免登录墙的两张表里 —— 放行它就是让未登录访客
    # 进配置页（POS 凭据 / 数据库凭据 / 账号与 API Token）。
    assert "/settings" not in main_module.HTML_AUTH_EXACT
    assert not any(
        "/settings".startswith(prefix) for prefix in main_module.HTML_AUTH_PREFIXES
    )

    # 另一半：已登录直连拿到 SPA 壳（页面确实注册成了路由，不是 404）。
    init = client.post(
        "/api/auth/init",
        json={
            "username": "admin",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    assert init.status_code == 200, init.text
    assert client.cookies.get(settings.SESSION_COOKIE_NAME)
    logged_in = client.get("/settings", headers=_html_headers(), follow_redirects=False)
    assert logged_in.status_code == 200
    assert "text/html" in logged_in.headers["content-type"]


def test_old_setup_path_is_gone(auth_app_client):
    """票 06：`/setup` 不再注册成页面（旧地址不做兼容），`/settings` 顶替它在清单里的位置。

    非页面请求（accept 不是 text/html）绕开登录墙直接看路由表：旧地址确实没有页面了。
    """
    import main as main_module

    client, _ = auth_app_client
    assert "/setup" not in main_module.SPA_PAGE_ROUTES
    assert "/settings" in main_module.SPA_PAGE_ROUTES
    assert client.get("/setup", follow_redirects=False).status_code == 404


def test_kds_html_not_redirected_to_login(auth_app_client):
    client, _ = auth_app_client
    resp = client.get("/kds", headers=_html_headers(), follow_redirects=False)
    loc = resp.headers.get("location", "")
    assert "/login" not in loc


def test_staff_pages_are_walled_workbench_pages_now(auth_app_client):
    """票 03：员工三页搬进工作台（`/workbench/me/*`），**不再靠路径免墙**。

    旧口径（票 04）是「手机上没有管理端会话，所以 `/staff/*` 必须免墙，否则员工永远进
    不去」——页面壳的放行只看管理端 cookie。票 02 把工作台改成「任一会话有效」之后，
    员工自己的会话就是那把钥匙：路径免墙那条口子不但不必要，而且有害（工作台是「进去
    要登录」的页面区，留着它未登录访客也能拿到壳）。

    所以这里断的是新的行为：三页未登录硬导航一律 302 到 `/login?next=<原地址>`，
    带员工会话才 200（后一条在 `test_workbench_page_wall_accepts_a_staff_session`）。
    """
    from urllib.parse import quote

    import main as main_module

    client, _ = auth_app_client
    for path in ("/workbench/me/today", "/workbench/me/month", "/workbench/me/clean"):
        assert path in main_module.SPA_PAGE_ROUTES, path
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 302, (path, resp.status_code)
        assert resp.headers["location"] == f"/login?next={quote(path, safe='')}", path

    # 员工注册页（票 02）在工作台之外，照旧逐条精确放行：新员工还没有任何会话。
    resp = client.get("/register", headers=_html_headers(), follow_redirects=False)
    assert resp.status_code == 200, resp.headers.get("location")
    assert "text/html" in resp.headers["content-type"]

    # 反向：旧的裸条目 `/staff` 与免墙前缀 `/staff/` 一条都不许回来（audit 条目 15 记的
    # 正是「`/staff` 免墙却无页面」这个坑；留着前缀等于把 `/staff/*` 整段对未登录访客放行）。
    assert "/staff" not in main_module.HTML_AUTH_EXACT
    assert "/staff/" not in main_module.HTML_AUTH_PREFIXES


def test_old_staff_paths_are_gone(auth_app_client):
    """票 03：`/staff/*` 随员工三页搬走而作废 —— 不留别名、不留重定向、不再免墙。

    这是 ADR 0091/0092 明知代价的取舍：不加 302、不留前端别名。浏览器硬导航到旧地址
    跟任何一个未登录的受保护页一样被登录墙 302（`?next=` 里存着旧地址也不是兼容跳转）；
    用非页面请求（curl / 探针：Accept 不是 text/html）绕开登录墙直接看路由表：确实是 404。
    """
    from urllib.parse import quote

    import main as main_module

    client, _ = auth_app_client
    for stale in ("/staff", "/staff/", "/staff/today", "/staff/month", "/staff/clean"):
        assert stale not in main_module.SPA_PAGE_ROUTES, stale
        assert stale not in main_module.HTML_AUTH_EXACT, stale
        # 不再落在任何免墙前缀下（票 03 删掉了 `/staff/`）。
        assert not any(
            stale.startswith(prefix) for prefix in main_module.HTML_AUTH_PREFIXES
        ), stale
        # 非页面请求：没有页面 → 404；浏览器硬导航：被登录墙 302 到 `/login?next=<旧地址>`。
        assert client.get(stale, follow_redirects=False).status_code == 404, stale
        nav = client.get(stale, headers=_html_headers(), follow_redirects=False)
        assert nav.status_code == 302, stale
        assert nav.headers["location"] == f"/login?next={quote(stale, safe='')}", stale

    # 更早那一批旧地址（票 04）同样不再是一个页面。
    for older in ("/today", "/today/month", "/hygiene"):
        assert older not in main_module.SPA_PAGE_ROUTES, older
        assert client.get(older, follow_redirects=False).status_code == 404, older

    # 新落点反过来都在：三页进页面注册清单（工作台里的一页）。
    for fresh in ("/workbench/me/today", "/workbench/me/month", "/workbench/me/clean"):
        assert fresh in main_module.SPA_PAGE_ROUTES, fresh


def test_register_page_is_public_and_the_old_hygiene_path_is_gone(auth_app_client):
    """注册页在顶层 `/register`：无会话、无 cookie 也拿到页面（不 302 到 /login）。

    旧路径 `/hygiene/register` 从页面注册清单里删掉，不做兼容跳转（spec：旧地址不做兼容）。
    只断言外部行为：拿到的状态码与响应体，不是名单常量长什么样。
    """
    import main as main_module

    client, _ = auth_app_client
    assert "/workbench/register" not in main_module.SPA_PAGE_ROUTES

    resp = client.get("/register", headers=_html_headers(), follow_redirects=False)
    assert resp.status_code == 200, resp.headers.get("location")
    assert "text/html" in resp.headers["content-type"]

    # 票 03 起 `/hygiene/` 不再免墙，浏览器的硬导航会被登录墙 302 到 `/login` —— 那也
    # 不是「兼容跳转」。这一条要断的是「旧路径不再是一个页面」，所以用非页面请求
    # （curl / 探针：Accept 不是 text/html）绕开登录墙，直接看路由表：确实是 404。
    stale = client.get("/workbench/register", follow_redirects=False)
    assert stale.status_code == 404, stale.headers.get("location")


def test_hygiene_login_page_is_gone_and_the_hygiene_prefix_no_longer_skips_the_wall(
    auth_app_client,
):
    """票 03：员工登录页并入 `/login`，`/hygiene/` 那段免墙前缀必须一起删掉。

    这是本次唯一会造成未授权访问的地方：`/hygiene/` 以前是员工登录页的免墙前缀，
    改完之后它底下已经没有任何员工页 —— 留着它，票 05 把八个卫生管理页搬进
    `/hygiene/*` 之后，它们就对未登录访客放行了。所以反向（前缀名单里没有它）与
    正向（八个卫生管理页仍未登录 302 到 `/login`）两条都要断言。
    """
    import main as main_module

    client, _ = auth_app_client

    # 反向：免墙前缀名单里不再有 `/hygiene/`，那条旧路径两张表里都不在（它从前是靠
    # `/hygiene/` 前缀免墙的，不是精确条目）。票 05 把八个管理端卫生页搬进 `/hygiene/*`
    # 之后这条仍是安全要害：前缀回来了，八页就一起对未登录访客放行。
    assert "/workbench/" not in main_module.HTML_AUTH_PREFIXES
    assert "/hygiene" not in main_module.HTML_AUTH_EXACT
    assert "/hygiene" not in main_module.SPA_PAGE_ROUTES
    assert "/workbench/login" not in main_module.HTML_AUTH_EXACT
    assert "/workbench/login" not in main_module.HTML_AUTH_PREFIXES

    # `/hygiene/login` 从页面注册清单里删掉：非页面请求直接 404，不做兼容跳转；
    # 浏览器硬导航则跟别的受保护页一样被登录墙 302（不再是员工页的免墙待遇）。
    assert "/workbench/login" not in main_module.SPA_PAGE_ROUTES
    stale = client.get("/workbench/login", follow_redirects=False)
    assert stale.status_code == 404, stale.headers.get("location")
    walled = client.get("/workbench/login", headers=_html_headers(), follow_redirects=False)
    assert walled.status_code == 302, walled.headers.get("location")
    assert walled.headers["location"].startswith("/login")

    # 正向：现场七页（票 05 起按分组落在 `/workbench/floor/*`）未登录访问任意一个，
    # 服务端仍必须 302 到 `/login`（而不是放行 SPA 壳 —— 放行之后前端守卫兜不住，
    # 直接就是未授权页面）。非页面请求（Accept 不是 text/html）绕开登录墙直接看路由表：
    # 新路径必须是页面。
    for path in (
        "/workbench/floor/zones",
        "/workbench/floor/daily",
        "/workbench/floor/deep-clean",
        "/workbench/floor/fix",
        "/workbench/floor/boards",
        "/workbench/floor/data",
        "/workbench/floor/attire",
        # 花名册票 05 起是人事页（`/workbench/hr/*`），它照样要登录。
        "/workbench/hr/roster",
    ):
        assert path in main_module.SPA_PAGE_ROUTES, path
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 302, path
        assert resp.headers["location"].startswith("/login"), path

    # 票 05：工作台里那批**平铺**地址（票 04 的正式地址）随分组重排一起作废 ——
    # 不留别名、不做重定向、非页面请求自然 404（跟更早的 `/hygiene-*` 一个待遇）。
    for flat in (
        "/workbench/inbox",
        "/workbench/shifts",
        "/workbench/roster",
        "/workbench/zones",
        "/workbench/daily",
        "/workbench/deep-clean",
        "/workbench/fix",
        "/workbench/boards",
        "/workbench/data",
        "/workbench/attire",
    ):
        assert flat not in main_module.SPA_PAGE_ROUTES, flat
        assert flat not in main_module.HTML_AUTH_EXACT, flat
        assert client.get(flat, follow_redirects=False).status_code == 404, flat

    # 旧连字符路径一律删掉（spec：旧地址不做兼容）：既不进页面注册清单，也不在两张
    # 豁免表里；非页面请求直接 404 —— 不是 200，也不是 302 兼容跳转。
    for legacy in (
        "/hygiene-roster",
        "/hygiene-zones",
        "/hygiene-daily",
        "/hygiene-deep-clean",
        "/hygiene-fix",
        "/hygiene-boards",
        "/hygiene-data",
        "/hygiene-attire",
    ):
        assert legacy not in main_module.SPA_PAGE_ROUTES, legacy
        assert legacy not in main_module.HTML_AUTH_EXACT, legacy
        assert client.get(legacy, follow_redirects=False).status_code == 404, legacy


def test_staff_path_list_matches_the_frontend_copy(auth_app_client):
    """员工端路径名单前后端对表（票 03 改写）：**服务端不再为它开免墙口子**。

    名单的唯一一份判据仍在 `admin-web/src/utils/staffPaths.js`（路由守卫、401 白名单、
    PWA 清单归属都从它出发）。票 02/04 那两版断的是「JS 那份逐条等于 `main.py` 两张
    免墙表里的条目」—— 员工页当时靠路径免墙（手机上没有管理端会话）。

    票 03 把三页搬进工作台之后，页面壳的放行根据换成会话（`_is_workbench_page` +
    `_has_staff_session`，票 02），路径免墙那条口子必须消失：留着 `/workbench/me/`
    前缀，整段员工页就对未登录访客放行了 —— 工作台是「进去要登录」的页面区。

    所以这里断三件事，判据仍然只有 JS 那一份 + 页面清单那一份：

    - 反向（安全要害）：前缀本身与前缀下的页都不在免墙表里，未登录硬导航一律 302；
    - 正向：前缀罩住的每一页都在 `SPA_PAGE_ROUTES` 里（少一条 = 手机硬导航 404）。
      名单从页面清单的 `me` 组取，不在这儿再抄一遍；
    - 精确名单（`/register`）照旧逐条免墙 —— 它在工作台之外，新员工还没有任何会话。
    """
    from urllib.parse import quote

    import main as main_module

    source = STAFF_PATHS_JS.read_text(encoding="utf-8")
    block = re.search(r"STAFF_PHONE_PREFIXES = \[(.*?)\]", source, re.S)
    assert block, STAFF_PATHS_JS
    prefixes = re.findall(r"'([^']+)'", block.group(1))
    assert prefixes, "前缀名单是空的：解析规则或文件结构变了，这条契约要跟着改"

    exact_block = re.search(r"STAFF_PHONE_EXACT = \[(.*?)\]", source, re.S)
    assert exact_block, f"{STAFF_PATHS_JS} 里找不到 STAFF_PHONE_EXACT"
    exact_paths = re.findall(r"'([^']+)'", exact_block.group(1))
    assert exact_paths, "精确名单是空的：解析规则或文件结构变了，这条契约要跟着改"

    client, _ = auth_app_client

    # 「我的」那一组就是前端名单罩住的那一段：清单是唯一来源，不在这儿再列一遍路径。
    inventory = json.loads(
        (REPO_ROOT / "admin-web" / "src" / "router" / "pageRoutes.json").read_text(
            encoding="utf-8"
        )
    )["pages"]
    me_pages = [row["path"] for row in inventory if row["group"] == "me"]
    assert me_pages, "页面清单里「我的」一页都没有：名单或清单结构变了"

    for prefix in prefixes:
        # 反向：前缀本身不在精确表里（裸条目免墙却无页面正是 audit 条目 15 那个坑）。
        assert prefix not in main_module.HTML_AUTH_EXACT, prefix
        # 反向：前缀也不在免墙前缀表里 —— 在的话下面那些页整段对未登录访客放行。
        assert f"{prefix}/" not in main_module.HTML_AUTH_PREFIXES, prefix
        assert not any(prefix.startswith(item) for item in main_module.HTML_AUTH_EXACT), prefix

    for path in me_pages:
        assert any(path.startswith(f"{prefix}/") for prefix in prefixes), (
            f"{path} 是「我的」组的一页，却不在前端那份员工前缀名单里（{prefixes}）"
        )
        assert path in main_module.SPA_PAGE_ROUTES, path
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 302, (path, resp.status_code)
        assert resp.headers["location"] == f"/login?next={quote(path, safe='')}", path

    for exact in exact_paths:
        assert exact in main_module.HTML_AUTH_EXACT, exact
        assert not any(
            exact == prefix or exact.startswith(f"{prefix}/") for prefix in prefixes
        ), f"{exact} 已被前缀名单罩住，不该再进精确名单（两层语义塌回一层）"


def test_hygiene_roster_html_requires_a_session(auth_app_client):
    """未登录（两套会话都没有）硬导航工作台页面：302 到 `/login?next=`，不是 200 外壳。

    票 02 之后这一条的含义收窄成「没有**任何**会话」——工作台页面壳对管理端会话与
    员工会话都放行（下面那三条用例分别盯着放行、系统管理面只认管理端、假 cookie 不放行）。
    """
    client, _ = auth_app_client
    for path in (
        "/workbench",
        "/workbench/hr/calendar",
        "/workbench/hr/roster",
        "/workbench/floor/zones",
        "/workbench/floor/daily",
        "/workbench/floor/deep-clean",
        "/workbench/floor/fix",
        "/workbench/floor/boards",
    ):
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 302, path
        assert resp.headers["location"].startswith("/login"), path


# ── 票 02：「任一身份」门 ─────────────────────────────────────────────────────
# 工作台（ADR 0092）是第一个两种身份都能进的页面区：页面壳的放行条件是「管理端会话
# 或员工会话至少有一个有效」，哪一页谁能看交给前端路由 meta 与各接口自己的 401。
# 系统管理面（仪表盘 / 数据管理 / 销售报表 / 备货计划 / 企微推送 / 日志 / 设置）留在
# 工作台外面，继续只认管理端会话。
WORKBENCH_PAGES = (
    "/workbench",
    # 票 05：人事四页与现场七页各在自己的组里（平铺那批地址已作废）。
    "/workbench/hr/calendar",
    "/workbench/hr/inbox",
    "/workbench/hr/shifts",
    "/workbench/hr/roster",
    "/workbench/floor/zones",
    "/workbench/floor/daily",
    "/workbench/floor/deep-clean",
    "/workbench/floor/fix",
    "/workbench/floor/boards",
    "/workbench/floor/data",
    "/workbench/floor/attire",
    # 票 03：员工三页搬进「我的」组，越权落点也是工作台里的一页 —— 服务端对它们一视
    # 同仁（任一会话有效即可拿到壳，页面级权限在前端守卫与各接口 401）。
    "/workbench/me/today",
    "/workbench/me/month",
    "/workbench/me/clean",
    "/workbench/forbidden",
    # 票 07：配方五页搬进「后勤」组（阅读四页 + 管理页）—— 服务端同样一视同仁。
    "/workbench/kitchen/recipe",
    "/workbench/kitchen/recipe/detail",
    "/workbench/kitchen/recipe/print",
    "/workbench/kitchen/recipe/qr",
    "/workbench/kitchen/recipe/manage",
    # 票 08：备货计划从管理后台搬进「后勤」组（`audience: both`）—— 服务端对工作台
    # 前缀一视同仁；它不再是系统管理面的一页。
    "/workbench/kitchen/prep-plan",
)
SYSTEM_PAGES = (
    "/",
    "/admin",
    "/sales-report",
    "/wecom-push",
    "/logs",
    "/settings",
)
STAFF_PHONE = "13800138000"
STAFF_PASSWORD = "password123"
ADMIN_INIT = {
    "username": "admin",
    "password": "password123",
    "confirm_password": "password123",
}


def _staff_only_client(client):
    """把客户端变成「只有员工会话」：走过完整的员工链路，最后清掉管理端 cookie。

    管理端会话只在审批花名册那一步用（`/api/hygiene/admin/roster/{id}/approve`），
    审批完就删掉 —— 这条用例的前提正是「手机上只有员工 cookie、没有管理端会话」。
    """
    init = client.post("/api/auth/init", json=ADMIN_INIT)
    assert init.status_code == 200, init.text
    registered = client.post(
        "/api/hygiene/staff/register",
        json=register_body(name="张三", phone=STAFF_PHONE, password=STAFF_PASSWORD),
    )
    assert registered.status_code == 200, registered.text
    employee = registered.json()["employee"]
    # 批准门槛（2026-10-07）：先补底薪与入职日期，才批得下来。
    seeded = client.patch(
        f"/api/hygiene/admin/roster/{employee['id']}", json=approve_body()
    )
    assert seeded.status_code == 200, seeded.text
    approved = client.post(f"/api/hygiene/admin/roster/{employee['id']}/approve")
    assert approved.status_code == 200, approved.text
    login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": STAFF_PHONE, "password": STAFF_PASSWORD},
    )
    assert login.status_code == 200, login.text
    assert client.cookies.get(settings.STAFF_SESSION_COOKIE_NAME)
    client.cookies.delete(settings.SESSION_COOKIE_NAME)
    assert client.cookies.get(settings.SESSION_COOKIE_NAME) is None


def test_workbench_page_wall_accepts_a_staff_session(auth_app_client):
    """员工会话（没有管理端会话）硬导航工作台每一页 → 200 页面壳。

    这就是「工作台包含员工端」在服务端成立的那一半：以前页面墙只读管理端 cookie，
    员工对自己的那半边（票 03 起是 `/workbench/me/*`）也永远进不去。票 03 之前员工页
    是靠路径免墙（`/staff/*`）绕过去的；现在它们跟工作台别的页一个待遇。
    """
    client, _ = auth_app_client
    _staff_only_client(client)

    for path in WORKBENCH_PAGES:
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 200, (path, resp.status_code)
        assert "text/html" in resp.headers["content-type"], path


def test_system_management_surface_rejects_a_staff_session(auth_app_client):
    """系统管理面留在工作台外面：同一个员工会话对它任何一页都无效，仍然 302 登录页。"""
    from urllib.parse import quote

    client, _ = auth_app_client
    _staff_only_client(client)

    for path in SYSTEM_PAGES:
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 302, (path, resp.status_code)
        assert resp.headers["location"] == f"/login?next={quote(path, safe='')}", path


def test_workbench_page_wall_fails_closed_before_staff_accounts_are_wired(
    auth_app_client, monkeypatch
):
    """员工账号服务还没装配（服务刚起的那几秒）：不放行，也不炸 500。"""
    import main as main_module

    client, _ = auth_app_client
    monkeypatch.setattr(main_module, "employee_accounts", None)
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, "some-staff-session")

    resp = client.get(
        "/workbench/hr/roster", headers=_html_headers(), follow_redirects=False
    )

    assert resp.status_code == 302, resp.text
    assert resp.headers["location"] == "/login?next=%2Fworkbench%2Fhr%2Froster"


def test_workbench_page_wall_rejects_a_bogus_staff_cookie(auth_app_client):
    """有那个 cookie 不等于会话有效：假 / 过期 / 别人的员工 cookie 都不放行。"""
    client, _ = auth_app_client
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, "bogus-staff-session")

    resp = client.get(
        "/workbench/hr/roster", headers=_html_headers(), follow_redirects=False
    )

    assert resp.status_code == 302, resp.text
    assert resp.headers["location"] == "/login?next=%2Fworkbench%2Fhr%2Froster"


def test_logged_in_super_admin_gets_the_spa_shell_on_every_hygiene_page(auth_app_client):
    """票 05 验收的另一半：八页未登录被拦，已登录的超级管理员直连每一页拿到 SPA 壳。

    `/hygiene/*` 不在任何豁免表里，登录墙放行的根据是会话而不是路径 —— 所以这里走真实
    登录流程（`/api/auth/init` 发 cookie）再逐页硬导航：登录了还 404 就说明页面路由没登记
    （反过来没登录还 200 就是未授权，上一条用例盯着）。
    """
    client, _ = auth_app_client
    init = client.post(
        "/api/auth/init",
        json={
            "username": "admin",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    assert init.status_code == 200, init.text
    assert client.cookies.get(settings.SESSION_COOKIE_NAME)

    for path in (
        "/workbench/hr/calendar",
        "/workbench/hr/roster",
        "/workbench/floor/zones",
        "/workbench/floor/daily",
        "/workbench/floor/deep-clean",
        "/workbench/floor/fix",
        "/workbench/floor/boards",
        "/workbench/floor/data",
        "/workbench/floor/attire",
    ):
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 200, path
        assert "text/html" in resp.headers["content-type"], path


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
