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


def test_staff_phone_pages_accessible_without_admin_session(auth_app_client):
    # 员工手机上那三页（票 04 起整体住在 `/staff/*`：今天 /staff/today、整月 /staff/month、
    # 卫生首页 /staff/clean）：没有管理端会话也必须拿到 SPA 外壳 —— 管理端登录表单只认
    # 管理端账号，服务端把员工拦下来他就永远进不去（员工会话由客户端守卫查那个 cookie）。
    # 三页都不是精确表里的条目：它们靠 `/staff/` 前缀放行（书签/外链/手输都可能带尾斜杠）。
    # 员工注册页（票 02）在员工前缀之外，只能逐条精确放行。
    client, _ = auth_app_client
    for path in ("/staff/today", "/staff/month", "/staff/clean", "/register"):
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 200, (path, resp.headers.get("location"))
        assert "text/html" in resp.headers["content-type"], path

    # `/staff` 与前缀本身没有页面（票面明确 `/staff` 不进 SPA_PAGE_ROUTES）：免墙、
    # 但确实没有这一页 —— 404，而不是被甩到 /login。
    for path in ("/staff", "/staff/"):
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 404, (path, resp.headers.get("location"))


def test_old_staff_paths_are_gone(auth_app_client):
    """票 04：员工三页搬进 `/staff/*`，旧的 `/today`、`/today/month`、`/hygiene` 一律删掉。

    这是 ADR 0091 明知代价的取舍：不加 302、不留前端别名。浏览器硬导航到旧地址既不是
    员工页也不是兼容跳转 —— 用非页面请求（curl / 探针：Accept 不是 text/html）绕开
    登录墙，直接看路由表：确实是 404。
    """
    import main as main_module

    client, _ = auth_app_client
    for stale in ("/today", "/today/month", "/hygiene"):
        assert stale not in main_module.SPA_PAGE_ROUTES, stale
        assert stale not in main_module.HTML_AUTH_EXACT, stale
        # 旧路径不再是一个页面（服务端没有 catch-all，也没给它们留兼容跳转）。
        assert client.get(stale, follow_redirects=False).status_code == 404, stale

    # 新落点反过来都在：三页进页面注册清单，`/staff` 本身只进免墙精确名单。
    for fresh in ("/staff/today", "/staff/month", "/staff/clean"):
        assert fresh in main_module.SPA_PAGE_ROUTES, fresh
    assert "/staff" in main_module.HTML_AUTH_EXACT
    assert "/staff" not in main_module.SPA_PAGE_ROUTES
    assert "/staff/" in main_module.HTML_AUTH_PREFIXES


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

    # 正向：八个卫生管理页收进 `/hygiene/*`（票 05），未登录访问任意一个，服务端仍必须
    # 302 到 `/login`（而不是放行 SPA 壳 —— 放行之后前端守卫兜不住，直接就是未授权页面）。
    # 非页面请求（Accept 不是 text/html）绕开登录墙直接看路由表：新路径必须是页面。
    for path in (
        "/workbench/roster",
        "/workbench/zones",
        "/workbench/daily",
        "/workbench/deep-clean",
        "/workbench/fix",
        "/workbench/boards",
        "/workbench/data",
        "/workbench/attire",
    ):
        assert path in main_module.SPA_PAGE_ROUTES, path
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 302, path
        assert resp.headers["location"].startswith("/login"), path

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


def test_staff_path_list_matches_the_frontend_copy():
    """员工端路径名单前端也有一份：两边必须对得上。

    名单的唯一一份判据在 `admin-web/src/utils/staffPaths.js`（路由守卫、401 白名单、
    PWA 清单归属都从它出发）。服务端这边少登记一条，员工手机硬导航那一页就被 302 到
    管理端 `/login`（票 05 的 `/today` 正是这么漏过一次）；前端多一条，管理端页面就被
    当成员工页。所以这里按 JS 那份逐条对 `main.py` 的两张表，而不是各写一份清单。

    票 02 起 JS 那份拆成两层，这里也要分别对：
    - 前缀名单（`STAFF_PHONE_PREFIXES`）＝ 员工侧界面与登录后落点；
    - 精确名单（`STAFF_PHONE_EXACT`）＝ 只属于员工侧界面、**不是**落点（`/register`）。
    两层都进 `HTML_AUTH_EXACT`，精确名单里的条目还必须不被前缀覆盖 —— 被覆盖就说明
    这一层是白写的，两层语义已经塌回一层。

    票 04 起只有 `/staff` 一条前缀（三个员工页都在它下面），前缀的子树也全是员工页，
    所以「前缀本身」与「前缀 + `/`」两条都在免墙名单里。反向断言（`/hygiene/` 不在
    免墙前缀里）在 `test_hygiene_login_page_is_gone_and_the_hygiene_prefix_no_longer_skips_the_wall`。
    """
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

    for prefix in prefixes:
        assert prefix in main_module.HTML_AUTH_EXACT, prefix
        # 尾斜杠变体走前缀表（书签/外链/手输带来的 `/staff/today/`）。
        assert f"{prefix}/" in main_module.HTML_AUTH_PREFIXES, prefix

    for exact in exact_paths:
        assert exact in main_module.HTML_AUTH_EXACT, exact
        assert not any(
            exact == prefix or exact.startswith(f"{prefix}/") for prefix in prefixes
        ), f"{exact} 已被前缀名单罩住，不该再进精确名单（两层语义塌回一层）"


def test_hygiene_roster_html_requires_admin_session(auth_app_client):
    client, _ = auth_app_client
    for path in (
        "/workbench/roster",
        "/workbench/zones",
        "/workbench/daily",
        "/workbench/deep-clean",
        "/workbench/fix",
        "/workbench/boards",
    ):
        resp = client.get(path, headers=_html_headers(), follow_redirects=False)
        assert resp.status_code == 302, path
        assert resp.headers["location"].startswith("/login"), path


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
        "/workbench/roster",
        "/workbench/zones",
        "/workbench/daily",
        "/workbench/deep-clean",
        "/workbench/fix",
        "/workbench/boards",
        "/workbench/data",
        "/workbench/attire",
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
