#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务读接口鉴权（SEC-02）。

登录墙以前只护 HTML 外壳：``HtmlAuthMiddleware`` 只拦「看起来像页面」的请求
（``accept: text/html``），``/api/orders/``、``/api/dashboard/summary``、
``/api/tables/live`` 这些读接口的依赖树里只有 ``get_db``。结果是门店局域网里
任何未登录设备（访客手机、没配 Token 的 KDS 平板）都能拉走订单明细、营业额、
桌台占用、备货计划与配方内容。

本文件钉三条契约：

1. **业务读面**：无凭据 401（且拒绝必须来自鉴权门，不是业务逻辑碰巧报错），
   带会话 cookie 或 ``X-Admin-Token`` 时 200 并且真的拿到数据；
2. **公开面不被误伤**：``/api/healthz`` 探针、登录入口、卫生员工端入口、
   扫码即看的配方阅读面（``/recipe*`` 页面读的接口）无凭据照样可用；
3. **公开面清单与实现一致**：``main.PUBLIC_API_SURFACE`` 是唯一的例外登记处，
   清单之外的 ``/api`` 路由必须带守卫（管理员凭据 / require_session /
   require_staff_session），防止以后又长出新的裸读接口。
"""

from datetime import datetime

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from config import settings
from database import CHINA_TZ

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "password123"

# 业务读面：无凭据必须 401。前四条是本票的验收点，其余是审查脚本命中的同根因接口。
BUSINESS_READ_PATHS = (
    "/api/orders/",
    "/api/orders/quick-stats",
    "/api/dashboard/summary",
    "/api/tables/live",
    "/api/orders/search",
    "/api/dishes/merged",
    "/api/dishes/stats/overview",
    "/api/dish-stations/stats",
    "/api/semi-rules/",
    "/api/report-dishes/",
    "/api/prep-plan/current",
    "/api/stations",
    "/api/system/status",
    "/api/system/scraper-health",
    "/api/export/sales-report.csv",
    "/api/recipes/stations/changfen/recipes",
    "/api/recipes/stations/changfen/export",
    "/api/recipes/recipes/1/history",
)

# 公开面：无凭据必须「可达」，即拒绝原因绝不能是鉴权门的 401「未授权」。
PUBLIC_PATHS = (
    "/api/healthz",
    "/api/system/health",
    "/api/scraper/status",
    "/api/auth/status",
    "/api/recipes/stations",
    "/api/recipes/search",
)

# 鉴权门的 401 body。用它把「鉴权拒绝」和业务自己的 401（如员工登录密码错误）区分开。
AUTH_GATE_BODY = {"detail": "未授权"}

# 这里曾有一份 `UNGUARDED_OWNED_BY_OTHER_TICKETS` 豁免清单：SEC-02 期间
# `/api/hygiene/admin/standards-export/jobs/{job_id}` 与它的 `/download` 还没有守卫
# （属别的票 SEC-03，当时靠 122bit job_id 能力 URL 兜底）。SEC-03 已给两条加上
# `require_session`，豁免随之失效——继续留着它们，下面那条契约就会**跳过**这两条
# 路由，等于给未来的"裸读接口"留了后门。整份清单删除：现在「清单外 /api 必须带
# 守卫」对全部 /api 路由生效。

# 认得的守卫：管理员凭据、管理端会话、员工会话、卫生标准图缓存会话。
GUARD_CALLABLES = {
    "verify_admin_token",
    "require_session",
    "require_staff_session",
    "require_standard_cache_session",
}


class ApiAccess:
    """真实 ``main.app`` 客户端 + 三种身份：匿名 / 会话 cookie / API Token。"""

    def __init__(self, client: TestClient, session_id: str, token: str) -> None:
        self.client = client
        self.session_id = session_id
        self.token = token

    def _send(self, method: str, path: str, *, cookie=False, token=False, **kwargs):
        self.client.cookies.clear()
        headers = dict(kwargs.pop("headers", None) or {})
        if cookie:
            self.client.cookies.set(settings.SESSION_COOKIE_NAME, self.session_id)
        if token:
            headers["X-Admin-Token"] = self.token
        return self.client.request(method, path, headers=headers, **kwargs)

    def anonymous(self, method: str, path: str, **kwargs):
        return self._send(method, path, **kwargs)

    def as_cookie(self, method: str, path: str, **kwargs):
        return self._send(method, path, cookie=True, **kwargs)

    def as_token(self, method: str, path: str, **kwargs):
        return self._send(method, path, token=True, **kwargs)


@pytest.fixture
def api(tmp_path):
    """启动真实 app（含中间件与依赖树），先建管理员账号，再清掉 cookie 当匿名客户端用。"""
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    import main as main_module

    with TestClient(main_module.app) as client:
        init = client.post(
            "/api/auth/init",
            json={
                "username": ADMIN_USERNAME,
                "password": ADMIN_PASSWORD,
                "confirm_password": ADMIN_PASSWORD,
            },
        )
        assert init.status_code == 200, init.text
        login = client.post(
            "/api/auth/login",
            json={
                "username": ADMIN_USERNAME,
                "password": ADMIN_PASSWORD,
                "remember": False,
                "issue_api_token": True,
            },
        )
        assert login.status_code == 200, login.text
        session_id = client.cookies.get(settings.SESSION_COOKIE_NAME)
        assert session_id, "登录必须下发会话 cookie"
        yield ApiAccess(client, session_id, login.json()["api_token"])
    settings.DATABASE_DIR = old


def _insert_row(api: ApiAccess, table: str, values: dict) -> None:
    """通过管理端写接口落数据（读接口的断言需要真实数据，不只看状态码）。"""
    resp = api.as_token(
        "POST", f"/api/admin/tables/{table}/rows", json={"values": values}
    )
    assert resp.status_code == 200, resp.text


def _seed_order(api: ApiAccess) -> None:
    _insert_row(
        api,
        "orders",
        {
            "business_flow_id": "sec02-001",
            "table_number": "8",
            "dish_name": "虾饺",
            "quantity": 2,
            "price": 12.5,
            "total_amount": 25.0,
            "order_time": datetime.now(CHINA_TZ).isoformat(),
            "station": "shulong",
            "status": "未结",
        },
    )


# ---------------------------------------------------------------------------
# 1. 业务读面：无凭据 401
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", BUSINESS_READ_PATHS)
def test_business_read_endpoints_reject_anonymous_requests(api, path):
    resp = api.anonymous("GET", path)

    assert resp.status_code == 401, f"{path} 无凭据居然 {resp.status_code}: {resp.text}"
    # 拒绝必须来自鉴权门：body 就是「未授权」，没有夹带任何业务字段。
    assert resp.json() == AUTH_GATE_BODY, resp.text


def test_anonymous_orders_read_leaks_no_revenue(api):
    """票面证据里的具体危害：无凭据能读到营业额字段。"""
    _seed_order(api)

    resp = api.anonymous("GET", "/api/orders/quick-stats")

    assert resp.status_code == 401, resp.text
    assert "total_revenue" not in resp.text
    assert "25.0" not in resp.text


def test_bogus_token_is_not_a_credential(api):
    """带头不等于有凭据：伪造的 X-Admin-Token 仍是 401。"""
    resp = api.anonymous(
        "GET", "/api/orders/", headers={"X-Admin-Token": "bogus-token"}
    )
    assert resp.status_code == 401, resp.text
    assert resp.json() == AUTH_GATE_BODY


# ---------------------------------------------------------------------------
# 2. 业务读面：带凭据 200 且真的拿到数据
# ---------------------------------------------------------------------------
def test_orders_read_with_session_cookie_returns_rows(api):
    _seed_order(api)

    resp = api.as_cookie("GET", "/api/orders/")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert [row["dish_name"] for row in body["data"]] == ["虾饺"]


def test_quick_stats_with_session_cookie_returns_revenue(api):
    _seed_order(api)

    resp = api.as_cookie("GET", "/api/orders/quick-stats")

    assert resp.status_code == 200, resp.text
    stats = resp.json()["data"]
    assert stats["total_orders"] == 1
    assert stats["total_quantity"] == 2
    assert stats["total_revenue"] == 25.0


def test_dashboard_summary_with_api_token_returns_revenue(api):
    """KDS / 脚本走的是 X-Admin-Token，这条通路必须一并可用。"""
    _seed_order(api)

    resp = api.as_token("GET", "/api/dashboard/summary")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["orders"]["total_revenue"] == 25.0


def test_tables_live_and_stations_with_api_token(api):
    """非浏览器客户端（KDS / 脚本）走 X-Admin-Token，这条通路也要一起通。"""
    live = api.as_token("GET", "/api/tables/live")
    assert live.status_code == 200, live.text
    body = live.json()
    assert body["success"] is True
    assert isinstance(body["tables"], list)
    assert "total_occupied" in body

    stations = api.as_token("GET", "/api/stations")
    assert stations.status_code == 200, stations.text
    assert {s["id"] for s in stations.json()} >= {"shulong"}


# ---------------------------------------------------------------------------
# 3. 公开面：不得因为本票而 401
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_public_endpoints_stay_reachable_without_credentials(api, path):
    resp = api.anonymous("GET", path)

    assert resp.status_code != 401 or resp.json() != AUTH_GATE_BODY, (
        f"{path} 被鉴权门拦住了：{resp.status_code} {resp.text}"
    )


def test_healthz_probe_reports_db_and_disk_without_credentials(api):
    """Docker HEALTHCHECK / 反代探针契约：免鉴权探针，且必须给出 db 与 disk。"""
    resp = api.anonymous("GET", "/api/healthz")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["db"] == "healthy"
    assert body["status"] == "ok"
    assert "level" in body["disk"]


def test_login_endpoints_work_without_credentials(api):
    """登录墙的入口自身不能被登录墙挡住，且错误凭据的 401 来自口令校验。"""
    status = api.anonymous("GET", "/api/auth/status")
    assert status.status_code == 200, status.text
    assert status.json()["initialized"] is True
    assert status.json()["logged_in"] is False

    relogin = api.anonymous(
        "POST",
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert relogin.status_code == 200, relogin.text
    assert relogin.json()["success"] is True

    bad = api.anonymous(
        "POST",
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": "wrong-password"},
    )
    assert bad.status_code == 401, bad.text
    assert bad.json() == {"detail": "用户名或密码错误"}

    already = api.anonymous(
        "POST",
        "/api/auth/init",
        json={
            "username": "someone",
            "password": ADMIN_PASSWORD,
            "confirm_password": ADMIN_PASSWORD,
        },
    )
    assert already.status_code == 409, already.text


def test_hygiene_staff_entrypoints_work_without_credentials(api):
    """卫生员工端入口保持开放（已有按 IP / 手机号的限流兜底）。"""
    bad_login = api.anonymous(
        "POST",
        "/api/hygiene/staff/login",
        json={"phone": "13800000000", "password": "nope"},
    )
    assert bad_login.status_code == 401, bad_login.text
    assert bad_login.json()["detail"] != AUTH_GATE_BODY["detail"], bad_login.text

    invalid_register = api.anonymous("POST", "/api/hygiene/staff/register", json={})
    assert invalid_register.status_code == 422, invalid_register.text


def _seed_recipe_station(api: ApiAccess) -> str:
    slug = "changfen"
    created = api.as_token(
        "POST", "/api/recipes/stations", json={"slug": slug, "title": "肠粉档"}
    )
    assert created.status_code == 200, created.text
    recipe = api.as_token(
        "POST",
        f"/api/recipes/stations/{slug}/recipes",
        json={
            "section": "配方",
            "recipe_name": "鲜虾肠粉",
            "body": "米浆要现磨",
            "is_new": False,
            "steps": ["大火蒸 3 分钟"],
        },
    )
    assert recipe.status_code == 200, recipe.text
    return slug


def test_recipe_reader_surface_is_readable_without_credentials(api):
    """``/recipe*`` 是扫码即看的公开页面，无凭据也要能读到岗位与配方内容。"""
    slug = _seed_recipe_station(api)

    stations = api.anonymous("GET", "/api/recipes/stations")
    assert stations.status_code == 200, stations.text
    assert slug in [s["slug"] for s in stations.json()["stations"]]

    detail = api.anonymous("GET", f"/api/recipes/stations/{slug}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["title"] == "肠粉档"
    assert "鲜虾肠粉" in body["content_html"]
    assert "大火蒸 3 分钟" in body["content_html"]

    search = api.anonymous("GET", "/api/recipes/search", params={"q": "虾"})
    assert search.status_code == 200, search.text
    assert "鲜虾肠粉" in search.text


def test_recipe_management_reads_require_credentials(api):
    """同一模块里的管理面读接口（导出 / 全量行 / 历史）不在公开阅读面内。"""
    slug = _seed_recipe_station(api)

    for path in (
        f"/api/recipes/stations/{slug}/recipes",
        f"/api/recipes/stations/{slug}/export",
        f"/api/recipes/stations/{slug}/docx",
    ):
        resp = api.anonymous("GET", path)
        assert resp.status_code == 401, f"{path} -> {resp.status_code} {resp.text}"
        assert resp.json() == AUTH_GATE_BODY

    authorized = api.as_cookie("GET", f"/api/recipes/stations/{slug}/recipes")
    assert authorized.status_code == 200, authorized.text
    assert "鲜虾肠粉" in [r["recipe_name"] for r in authorized.json()["recipes"]]


# ---------------------------------------------------------------------------
# 4. 公开面清单必须与实现一致（防以后又长出裸读接口）
# ---------------------------------------------------------------------------
def _effective_api_routes(app):
    """``/api`` 路由表，按 (method, path) 去重并保留**先注册**的那条。

    Starlette 按注册顺序匹配，重复路径由先注册者生效（SEC-05 的
    ``/api/logs/recent`` 就是被 api/logs.py 里带鉴权的那条遮蔽掉的）。
    """
    routes = {}
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
            continue
        for method in route.methods:
            routes.setdefault((method, route.path), route)
    return routes


def _guard_names(route) -> set:
    names = set()

    def walk(dependant) -> None:
        for dependency in dependant.dependencies:
            if dependency.call is not None:
                names.add(getattr(dependency.call, "__name__", str(dependency.call)))
            walk(dependency)

    walk(route.dependant)
    return names


def test_every_api_route_is_guarded_or_declared_public():
    import main as main_module

    declared_public = set(main_module.PUBLIC_API_SURFACE)
    unguarded = sorted(
        f"{method} {path}"
        for (method, path), route in _effective_api_routes(main_module.app).items()
        if (method, path) not in declared_public
        and not (_guard_names(route) & GUARD_CALLABLES)
    )

    assert unguarded == [], (
        "以下 /api 路由既没有守卫也不在 main.PUBLIC_API_SURFACE 清单里：\n"
        + "\n".join(unguarded)
    )


def test_public_api_surface_registry_matches_real_routes():
    """清单里不能有笔误：每条都必须是真实注册的 (method, path)。"""
    import main as main_module

    real = set(_effective_api_routes(main_module.app))
    missing = sorted(set(main_module.PUBLIC_API_SURFACE) - real)

    assert missing == [], f"PUBLIC_API_SURFACE 里这些条目没有对应路由：{missing}"
