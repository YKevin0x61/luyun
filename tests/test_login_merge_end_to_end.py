#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""票 09：登录面板整合的端到端收口（真 app + TestClient）。

本文件补的是「没有任何单张票能覆盖」的那几条端到端路径 —— 单张票各自只断言了自己
那一半，跨票的交界处没人钉。所有断言都走真 `main.app`（lifespan 建连、真路由、
真中间件），不写连库的临时脚本（本机 `.env` 指向真库，只有 `tests/conftest.py`
把 DSN 钉到测试库）。

对应关系（票面冒烟逐条）：

- 旧地址不做兼容（ADR 0091）：非页面请求 404（没有页面、也没有兼容跳转）；浏览器
  硬导航只是被**通用**登录墙 302 到 `/login?next=<旧地址>` —— 它和任何一个未登录的
  受保护页待遇相同，登录后回到的仍是那个 404 的旧地址，不构成兼容。
- 员工在 `/login` 员工栏登录后的落点（`/staff/today`）：服务端确实注册了这三页，
  且员工前缀免墙拿到 SPA 外壳。前端「员工栏登录成功落到 `STAFF_ENTRY_PATH`」的
  组件级证据在 `admin-web/src/views/__tests__/LoginView.test.js`（「没带 ?next 时
  落到员工默认落点（今天页）」），这里补「那个地址服务端真的服务、且不被墙拦」。
- `/hygiene/*` 管理端页未登录：302 到 `?next=`，**不是**放行 SPA 外壳。
- 同一浏览器同时持两套 cookie（合并面板后从罕见变成顺手可得）：两套会话在服务端
  各认各的、互不顶掉（WS 那半边见
  `tests/test_ws_auth.py::DeclaredIdentityScopeTest`）。

刻意不重复的既有证据（避免同一件事两处断言、改一处忘一处）：

- 八个 `/hygiene/*` 未登录 302 与反向断言（免墙前缀里没有 `/hygiene/`）：
  `tests/test_auth.py::test_hygiene_login_page_is_gone_and_the_hygiene_prefix_no_longer_skips_the_wall`
- `/staff/*`、`/register` 免墙拿到壳：`tests/test_auth.py::test_staff_phone_pages_accessible_without_admin_session`
- 前端路由清单 ⊆ 服务端页面清单：`tests/test_spa_page_routes.py`
- 员工端名单两份语义（PWA 归属含 `/register`、落点白名单不含）：
  `tests/test_auth.py::test_staff_path_list_matches_the_frontend_copy`
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
STAFF_PATHS_JS = REPO_ROOT / "admin-web" / "src" / "utils" / "staffPaths.js"

HTML = {"Accept": "text/html"}

PHONE = "13800138000"
PASSWORD = "password123"
NAME = "张三"
ADMIN_INIT = {
    "username": "admin",
    "password": "password123",
    "confirm_password": "password123",
}

# 旧地址（ADR 0091 的「不做兼容」清单）：`/today*`、`/hygiene*`（含本路径与员工登录/注册）、
# `/setup`。都在页面注册清单与两张豁免表之外。
OLD_URLS = (
    "/today",
    "/today/month",
    "/hygiene",
    # 票 03：员工三页搬进工作台的「我的」组，`/staff/*`（含裸前缀那条「免墙却没页面」的
    # 条目，audit 条目 15）与更早那批一样作废 —— 不加 302、不留别名、不再免墙。
    "/staff",
    "/staff/today",
    "/staff/month",
    "/staff/clean",
    "/workbench/login",
    "/workbench/register",
    # 票 05：工作台里的**平铺**地址（票 04 的正式地址）随分组重排一起作废 ——
    # 不加 302、不留别名、不再免墙，跟上面那批一个待遇。
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
    "/hygiene-roster",
    "/hygiene-zones",
    "/hygiene-daily",
    "/hygiene-deep-clean",
    "/hygiene-fix",
    "/hygiene-boards",
    "/hygiene-data",
    "/hygiene-attire",
    "/setup",
)

STAFF_PAGES = ("/workbench/me/today", "/workbench/me/month", "/workbench/me/clean")

# 票 05：现场七页按分组落在 `/workbench/floor/*`；花名册是人事页（`/workbench/hr/*`），
# 但它的服务端待遇与现场页一样（工作台的页面壳对任一会话放行，未登录 302 到 `/login`）。
HYGIENE_ADMIN_PAGES = (
    "/workbench/floor/zones",
    "/workbench/floor/daily",
    "/workbench/floor/deep-clean",
    "/workbench/floor/fix",
    "/workbench/floor/boards",
    "/workbench/floor/data",
    "/workbench/floor/attire",
)

WORKBENCH_HR_PAGES = (
    "/workbench/hr/calendar",
    "/workbench/hr/inbox",
    "/workbench/hr/shifts",
    "/workbench/hr/roster",
)


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """真 `main.app` + 打桩的 SPA 外壳（与 `tests/test_auth.py` 同一套夹具口径）。"""
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    import main as main_module

    stub = tmp_path / "spa-index.html"
    stub.write_text("<!doctype html><title>spa stub</title>", encoding="utf-8")
    monkeypatch.setattr(main_module, "spa_index_path", str(stub))
    with TestClient(main_module.app) as client:
        yield client, main_module
    settings.DATABASE_DIR = old


def test_old_urls_are_404_for_non_page_requests_and_wall_302_with_next_for_browsers(
    app_client,
):
    """票面第 3 条：旧地址既不是页面，也不是兼容跳转。

    两条请求方式分别对应两种真实来路：

    - 非页面请求（curl / 探针 / SW 预取，`Accept` 不是 `text/html`）：绕开登录墙直接
      看路由表 —— 旧地址没有页面，就是 404；**不是** 200 空壳，也**不是** 302 兼容。
    - 浏览器硬导航（`Accept: text/html`）：和任何一个未登录的受保护页一样，被通用
      登录墙 302 到 `/login?next=<旧地址>`。这条 302 是「拦」，不是「兼容」—— 登录
      之后回到的仍是那个 404 的旧地址（ADR 0091 认下这个代价，转成门店一次运维）。
    """
    client, main_module = app_client

    for old in OLD_URLS:
        assert old not in main_module.SPA_PAGE_ROUTES, old
        assert old not in main_module.HTML_AUTH_EXACT, old
        assert not any(
            old.startswith(prefix) for prefix in main_module.HTML_AUTH_PREFIXES
        ), f"{old} 落在免墙前缀下，旧地址成了员工页"

        # 非页面请求：没有页面 → 404（不跟着重定向，直接看服务端返回了什么）。
        bare = client.get(old, follow_redirects=False)
        assert bare.status_code == 404, (old, bare.status_code)

        # 浏览器硬导航：通用登录墙 302，next 是编码后的字面量。
        nav = client.get(old, headers=HTML, follow_redirects=False)
        assert nav.status_code == 302, (old, nav.status_code)
        assert nav.headers["location"] == f"/login?next={quote(old, safe='')}", old


def test_hygiene_admin_pages_are_walled_with_next_not_served_as_the_spa_shell(app_client):
    """票面第 4 条：未登录直连卫生管理页得到 302 到 `/login`，不是放行 SPA 外壳。

    用带 `next` 的完整字面量断言（既有用例只断言 `location.startswith('/login')`）：
    `/hygiene/` 从免墙前缀里删掉之后，这八页对未登录访客与 `/settings` 一个待遇。
    """
    client, _main_module = app_client

    for path in HYGIENE_ADMIN_PAGES + WORKBENCH_HR_PAGES + ("/settings",):
        resp = client.get(path, headers=HTML, follow_redirects=False)
        assert resp.status_code == 302, (path, resp.status_code)
        assert resp.headers["location"] == f"/login?next={quote(path, safe='')}", path

    # 半页也不行：`/hygiene` 本路径没有页面，未登录时同样只是被墙拦（不是 200 空壳）。
    bare = client.get("/hygiene", headers=HTML, follow_redirects=False)
    assert bare.status_code == 302
    assert bare.headers["location"] == "/login?next=%2Fhygiene"


def test_staff_entry_landing_is_a_served_page_behind_the_workbench_wall(app_client):
    """票面第 1 条的服务端半边：员工登录后的落点确实可达 —— 但它现在是**要登录**的页。

    落点值来自前端唯一那份常量（`STAFF_ENTRY_PATH`）：员工栏登录成功、花名册页二维码、
    员工清单的 `start_url` 三处共用它。这里把「前端默认落点」与「服务端真的服务这个
    地址」接起来 —— 常量漂到一条没注册的路径上，员工登进去就是 404/白屏。

    票 03 的口径变化：三页搬进工作台之后**不再靠路径免墙**（旧的 `/staff/` 前缀删掉
    了），未登录硬导航跟工作台别的页一样 302 到 `/login?next=<原地址>`；带员工会话拿到
    外壳的那半边由 `tests/test_auth.py::test_workbench_page_wall_accepts_a_staff_session`
    盯着（这里不重复建员工账号）。
    """
    from urllib.parse import quote

    client, main_module = app_client

    source = STAFF_PATHS_JS.read_text(encoding="utf-8")
    match = re.search(r"STAFF_ENTRY_PATH\s*=\s*'([^']+)'", source)
    assert match, f"{STAFF_PATHS_JS} 里找不到 STAFF_ENTRY_PATH"
    entry = match.group(1)

    assert entry == "/workbench/me/today"
    assert entry in main_module.SPA_PAGE_ROUTES, entry

    # 员工三页都可达（在页面注册清单里），但未登录不再放行 —— 工作台是「进去要登录」的。
    for path in STAFF_PAGES:
        assert path in main_module.SPA_PAGE_ROUTES, path
        resp = client.get(path, headers=HTML, follow_redirects=False)
        assert resp.status_code == 302, (path, resp.status_code)
        assert resp.headers["location"] == f"/login?next={quote(path, safe='')}", path


def test_two_cookie_browser_keeps_both_sessions_alive(app_client):
    """合并面板后的新常态：同一浏览器同时持两套 cookie，两套会话各认各的。

    服务端一个字的优先级都没改（两张表、两个 cookie、两条守卫链），所以这条断言的
    价值是钉住「两套 cookie 并存时互不顶掉」这个前提 —— WS 身份那半边
    （`tests/test_ws_auth.py::DeclaredIdentityScopeTest`）依赖它。

    员工在 `/login` 员工栏登录（HTTP 链路）→ 员工页免墙拿到外壳；同一个 client 里
    管理端会话仍然有效、员工会话也仍然有效。落点由前端决定（组件级用例在
    `admin-web/src/views/__tests__/LoginView.test.js`），这里只断言服务端给了什么。
    """
    client, main_module = app_client

    init = client.post("/api/auth/init", json=ADMIN_INIT)
    assert init.status_code == 200, init.text
    assert client.cookies.get(settings.SESSION_COOKIE_NAME)

    registered = client.post(
        "/api/hygiene/staff/register",
        json={"name": NAME, "phone": PHONE, "password": PASSWORD},
    )
    assert registered.status_code == 200, registered.text
    employee = registered.json()["employee"]

    approved = client.post(f"/api/hygiene/admin/roster/{employee['id']}/approve")
    assert approved.status_code == 200, approved.text

    login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD, "remember": True},
    )
    assert login.status_code == 200, login.text

    # 登录成功这一刻，同一浏览器上两套 cookie 同时在（两个 cookie 的 path 都是 `/`）。
    assert client.cookies.get(settings.SESSION_COOKIE_NAME)
    assert client.cookies.get(settings.STAFF_SESSION_COOKIE_NAME)

    # 工作台页面壳对任一会话都放行（票 02）：两套都带着，员工三页同样 200 外壳。
    for path in STAFF_PAGES:
        resp = client.get(path, headers=HTML, follow_redirects=False)
        assert resp.status_code == 200, path
        assert "text/html" in resp.headers["content-type"], path

    # 两套会话在 API 层互不顶掉：管理端状态照报已登录，员工身份照认自己。
    assert client.get("/api/auth/status").json()["logged_in"] is True
    me = client.get("/api/hygiene/staff/me")
    assert me.status_code == 200, me.text
    assert me.json()["employee"]["phone"] == PHONE


def test_realtime_endpoint_over_the_real_app_keeps_staff_identity_with_two_cookies(app_client):
    """票面第 6 条：同一浏览器两套 cookie 时，员工页那条连接**真的**被认成员工。

    上半段（前端）由 `admin-web/src/__tests__/App.realtime.test.js` 断言 —— 员工端页面
    建出的连接 URL 带 `?identity=staff`；下半段（端点函数）由
    `tests/test_ws_auth.py::DeclaredIdentityScopeTest` 断言 —— 两套 cookie + 声明 staff
    时订不到 `orders`、收不到同事的 nudge。这里补最后一厘米：**跨过真实 ASGI 端点**
    （TestClient 的真 WebSocket，不是 fake）走一遍，把前端写出的那个查询串喂给后端，
    确认服务端就是按员工身份接的。
    """
    client, _main_module = app_client

    init = client.post("/api/auth/init", json=ADMIN_INIT)
    assert init.status_code == 200, init.text
    registered = client.post(
        "/api/hygiene/staff/register",
        json={"name": NAME, "phone": PHONE, "password": PASSWORD},
    )
    assert registered.status_code == 200, registered.text
    employee = registered.json()["employee"]
    assert client.post(
        f"/api/hygiene/admin/roster/{employee['id']}/approve"
    ).status_code == 200
    assert client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    ).status_code == 200
    # 两套 cookie 都在这个 client 的 jar 里（path 都是 `/`）。
    assert client.cookies.get(settings.SESSION_COOKIE_NAME)
    assert client.cookies.get(settings.STAFF_SESSION_COOKIE_NAME)

    # `?identity=staff` 就是 `App.vue` 给员工页拼的那一段（字面量）。
    with client.websocket_connect("/ws/realtime?identity=staff") as ws:
        assert ws.receive_json() == {"type": "connected"}

        # 经营主题订不到：身份是员工，管理端 cookie 在场也不放宽。
        ws.send_json({"action": "subscribe", "id": "orders-1", "topics": ["orders"]})
        denied = ws.receive_json()
        assert denied["type"] == "error", denied

        # 员工自己的主题订得到（订阅被换成他自己的 scope，见 hub）。
        ws.send_json({"action": "subscribe", "id": "hygiene-1", "topics": ["hygiene"]})
        assert ws.receive_json() == {"type": "subscribed", "id": "hygiene-1"}


def test_exemption_tables_hold_no_leftover_old_addresses(app_client):
    """收口：免墙两张表里没有一条旧地址，`/settings` 与 `/hygiene/` 也不在里面。

    逐条断言旧地址而不是钉死整张表的内容：`HTML_AUTH_EXACT` 以后合法地多一个公开页
    时不该假红（新增公开页有它自己的对表用例）。
    """
    _client, main_module = app_client

    assert "/settings" not in main_module.HTML_AUTH_EXACT
    assert not any(
        "/settings".startswith(prefix) for prefix in main_module.HTML_AUTH_PREFIXES
    )
    assert "/workbench/" not in main_module.HTML_AUTH_PREFIXES

    for old in OLD_URLS:
        assert old not in main_module.HTML_AUTH_EXACT, old
        assert old not in main_module.HTML_AUTH_PREFIXES, old
