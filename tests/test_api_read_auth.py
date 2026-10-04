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
2. **公开面不被误伤**：``/api/healthz`` 探针、登录入口、卫生员工端入口无凭据照样可用；
3. **公开面清单与实现一致**：``main.PUBLIC_API_SURFACE`` 是唯一的例外登记处，
   清单之外的 ``/api`` 路由必须带守卫（管理员凭据 / require_session /
   require_staff_session / require_any_identity_session），防止以后又长出新的裸读接口。

票 07 起**配方阅读面不再是公开面**：三条读接口（搜索 / 岗位列表 / 岗位阅读）改成
「任一身份」门（``require_any_identity_session``，ADR 0092）—— 扫码看配方保留，但
扫码的人先登录。这一条把「零鉴权、谁都能拉（连停用配方都能拉）」那个既有缺陷关掉，
所以下面第 2 节里不再有配方，第 1 节里多出三条。

票 08 把**备货计划的读端点**也换成同一条门（它随页搬进工作台的「后勤」组，后厨要看得见
今天要备什么），而**写端点一个都没动**：员工 cookie 打写接口仍必须 401、管理端照常
（第 3b 节两条），ADR 0092 那句「进工作台只是换位置与统一导航，不扩权」在测试里就是这个
正反两面。
"""

from datetime import datetime

import pytest
from fastapi import Depends, FastAPI
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
    "/api/stations",
    "/api/system/status",
    "/api/system/scraper-health",
    "/api/export/sales-report.csv",
    "/api/recipes/stations/changfen/recipes",
    "/api/recipes/stations/changfen/export",
    "/api/recipes/recipes/1/history",
    # 配方阅读面（票 07）：扫码即看的三条读接口从「零鉴权」改成「任一身份」门 ——
    # 无凭据必须 401，拒绝同样来自鉴权门（body 是「未授权」）。
    "/api/recipes/search",
    "/api/recipes/stations",
    "/api/recipes/stations/changfen",
)

# 备货计划的读面（票 08）：搬进工作台的「后勤」组之后，读端点从「仅管理端」改成
# 「任一身份」门（ADR 0092 那张契约表的「备货计划读端点」一行）。单独列一份是因为
# 除了「无凭据 401」，它还有两条自己的断言（员工会话 200 拿到同一份内容、写端点仍 401）——
# 见下面的 `test_prep_plan_reads_accept_any_identity_session`。
PREP_PLAN_READ_PATHS = (
    "/api/prep-plan/current",
    "/api/prep-plan/forecast",
    "/api/prep-plan/movements",
    "/api/prep-plan/expiring",
)

# 备货计划的写面（票 08 不动的部分）：**仍只认管理端**。
PREP_PLAN_WRITE_PATHS = (
    ("POST", "/api/prep-plan/generate", {"method": "weighted_history"}),
    ("POST", "/api/prep-plan/init-items-from-rules", None),
    ("POST", "/api/prep-plan/batches", {
        "item_name": "不存在的备货品", "unit": "份", "produced_qty": 1,
    }),
)


# 公开面：无凭据必须「可达」，即拒绝原因绝不能是鉴权门的 401「未授权」。
PUBLIC_PATHS = (
    "/api/healthz",
    "/api/system/health",
    "/api/scraper/status",
    "/api/auth/status",
)

# 鉴权门的 401 body。用它把「鉴权拒绝」和业务自己的 401（如员工登录密码错误）区分开。
AUTH_GATE_BODY = {"detail": "未授权"}

# 这里曾有一份 `UNGUARDED_OWNED_BY_OTHER_TICKETS` 豁免清单：SEC-02 期间
# `/api/hygiene/admin/standards-export/jobs/{job_id}` 与它的 `/download` 还没有守卫
# （属别的票 SEC-03，当时靠 122bit job_id 能力 URL 兜底）。SEC-03 已给两条加上
# `require_session`，豁免随之失效——继续留着它们，下面那条契约就会**跳过**这两条
# 路由，等于给未来的"裸读接口"留了后门。整份清单删除：现在「清单外 /api 必须带
# 守卫」对全部 /api 路由生效。

# 认得的守卫：管理员凭据、管理端会话、员工会话、卫生标准图缓存会话、任一身份门。
# 新守卫要加进这里，否则挂上它的路由在下面那条契约里会被当成「裸接口」（票 07/08
# 把「任一身份」门接到配方 / 备货计划读接口时会撞上）。
GUARD_CALLABLES = {
    "verify_admin_token",
    "require_session",
    "require_staff_session",
    "require_standard_cache_session",
    "require_any_identity_session",
}


class ApiAccess:
    """真实 ``main.app`` 客户端 + 四种身份：匿名 / 管理端会话 / 员工会话 / API Token。

    员工会话（票 07 起配方阅读面的第二把钥匙）在用到时才建：建它要先走一遍
    「注册 → 批准 → 登录」，而批准那一步本身要管理端会话。
    """

    def __init__(self, client: TestClient, session_id: str, token: str) -> None:
        self.client = client
        self.session_id = session_id
        self.token = token
        self.staff_session_id = None

    def _send(self, method: str, path: str, *, cookie=False, staff=False, token=False, **kwargs):
        self.client.cookies.clear()
        headers = dict(kwargs.pop("headers", None) or {})
        if cookie:
            self.client.cookies.set(settings.SESSION_COOKIE_NAME, self.session_id)
        if staff:
            assert self.staff_session_id, "先调 _staff_reader_session(api) 建员工会话"
            self.client.cookies.set(
                settings.STAFF_SESSION_COOKIE_NAME, self.staff_session_id
            )
        if token:
            headers["X-Admin-Token"] = self.token
        return self.client.request(method, path, headers=headers, **kwargs)

    def anonymous(self, method: str, path: str, **kwargs):
        return self._send(method, path, **kwargs)

    def as_cookie(self, method: str, path: str, **kwargs):
        return self._send(method, path, cookie=True, **kwargs)

    def as_staff(self, method: str, path: str, **kwargs):
        return self._send(method, path, staff=True, **kwargs)

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


@pytest.mark.parametrize("path", PREP_PLAN_READ_PATHS)
def test_prep_plan_read_endpoints_reject_anonymous_requests(api, path):
    """票 08：备货计划读面从「仅管理端」改成「任一身份」门 —— 无凭据仍必须 401。

    改成「任一身份」不等于放开：拒绝必须来自鉴权门（body 是「未授权」），而不是业务
    逻辑碰巧报错。
    """
    resp = api.anonymous("GET", path)
    assert resp.status_code == 401, f"{path} 无凭据居然 {resp.status_code}: {resp.text}"
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


def _staff_reader_session(api: ApiAccess) -> str:
    """走完员工链路，返回员工会话 cookie 原文（配方阅读面的第二把钥匙）。

    `_staff_session_cookie` 里那一步「批准注册」要管理端会话，而 `ApiAccess` 每次请求
    都会先清空 cookie 罐 —— 这里先把管理端会话放回去，再走员工注册 / 登录。
    """
    api.client.cookies.set(settings.SESSION_COOKIE_NAME, api.session_id)
    api.staff_session_id = _staff_session_cookie(api.client)
    return api.staff_session_id


def test_recipe_reader_surface_requires_any_identity_session(api):
    """票 07：配方阅读面从「零鉴权」改成「任一身份」门（ADR 0092）。

    ``/workbench/kitchen/recipe*`` 是工作台里的页面，三条读接口跟着换成
    ``require_any_identity_session``：无凭据 401（拒绝来自鉴权门），管理端会话与
    员工会话都能读到**同样的**内容 —— 扫码的厨师登录之后看的就是这一份。
    """
    slug = _seed_recipe_station(api)
    reader_paths = (
        "/api/recipes/stations",
        f"/api/recipes/stations/{slug}",
        "/api/recipes/search?q=虾",
    )

    for path in reader_paths:
        resp = api.anonymous("GET", path)
        assert resp.status_code == 401, f"{path} 无凭据居然 {resp.status_code}: {resp.text}"
        assert resp.json() == AUTH_GATE_BODY, resp.text

    _staff_reader_session(api)

    stations = api.as_staff("GET", "/api/recipes/stations")
    assert stations.status_code == 200, stations.text
    assert slug in [s["slug"] for s in stations.json()["stations"]]

    detail = api.as_staff("GET", f"/api/recipes/stations/{slug}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["title"] == "肠粉档"
    assert "鲜虾肠粉" in body["content_html"]

    search = api.as_staff("GET", "/api/recipes/search", params={"q": "虾"})
    assert search.status_code == 200, search.text
    assert "鲜虾肠粉" in search.text

    # 管理端会话走的是同一条门（先管理端、再员工），拿到的内容一样。
    as_admin = api.as_cookie("GET", f"/api/recipes/stations/{slug}")
    assert as_admin.status_code == 200, as_admin.text
    assert "鲜虾肠粉" in as_admin.json()["content_html"]


def test_recipe_include_inactive_flag_is_admin_only(api):
    """票 07：「含停用」只对管理端生效 —— 员工与未登录者都拉不到停用配方。

    这是既有缺陷的正面口径：免登录时代 ``include_inactive=1`` 对谁都生效，
    于是「停用」这个管理动作在公开面上等于没做。
    """
    slug = _seed_recipe_station(api)
    created = api.as_token(
        "POST",
        f"/api/recipes/stations/{slug}/recipes",
        json={"section": "配方", "recipe_name": "停售品", "body": "下架了", "is_new": False},
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    assert api.as_token("POST", f"/api/recipes/recipes/{rid}/toggle-active").status_code == 200

    _staff_reader_session(api)

    # 员工会话：显式要 include_inactive 也不算数。
    staff_detail = api.as_staff(
        "GET", f"/api/recipes/stations/{slug}", params={"include_inactive": 1}
    )
    assert staff_detail.status_code == 200, staff_detail.text
    assert "停售品" not in staff_detail.json()["content_html"]

    staff_search = api.as_staff(
        "GET", "/api/recipes/search", params={"q": "停售品", "include_inactive": 1}
    )
    assert staff_search.status_code == 200, staff_search.text
    assert staff_search.json() == {"groups": []}

    # 管理端会话：这个开关照旧生效（阅读面与打印面都是管理端在用的）。
    admin_detail = api.as_cookie(
        "GET", f"/api/recipes/stations/{slug}", params={"include_inactive": 1}
    )
    assert admin_detail.status_code == 200, admin_detail.text
    assert "停售品" in admin_detail.json()["content_html"]
    assert "recipe-card--inactive" in admin_detail.json()["content_html"]

    admin_search = api.as_cookie(
        "GET", "/api/recipes/search", params={"q": "停售品", "include_inactive": 1}
    )
    assert admin_search.status_code == 200, admin_search.text
    assert "停售品" in admin_search.text

    # 不带这个开关时两边都看不到（默认就是「只看启用」）。
    for client_call in (
        api.as_cookie("GET", f"/api/recipes/stations/{slug}"),
        api.as_staff("GET", f"/api/recipes/stations/{slug}"),
    ):
        assert client_call.status_code == 200, client_call.text
        assert "停售品" not in client_call.json()["content_html"]


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
# 3b. 备货计划（票 08）：读门「任一身份」，写门仍只认管理端
# ---------------------------------------------------------------------------
def _seed_semi_finished_rule(api: ApiAccess) -> None:
    """落一条半成品规则 —— 写路由的正面断言要它才看得出「真的写进去了」。"""
    _insert_row(
        api,
        "semi_finished_rules",
        {
            "dish_name": "鲜虾饺",
            "semi_name": "虾饺馅",
            "position": "馅档",
            "factor": 1,
            "unit": "份",
            "category": "",
            "notes": "",
            "created_at": datetime.now(CHINA_TZ).isoformat(),
            "updated_at": datetime.now(CHINA_TZ).isoformat(),
        },
    )


def test_prep_plan_reads_accept_any_identity_session(api):
    """票 08：备货计划的读端点从「仅管理端」改成「任一身份」门（ADR 0092）。

    ``/workbench/kitchen/prep-plan`` 是工作台「后勤」组里的一页，两种身份都进得去 ——
    读端点跟着换成 ``require_any_identity_session``：无凭据 401（拒绝来自鉴权门），
    管理端会话与员工会话都能读到**同样的**内容。**写端点不在这一档**（下一条钉住）。
    """
    for path in PREP_PLAN_READ_PATHS:
        anonymous = api.anonymous("GET", path)
        assert anonymous.status_code == 401, f"{path} 无凭据居然 {anonymous.status_code}"
        assert anonymous.json() == AUTH_GATE_BODY, anonymous.text

        as_admin = api.as_cookie("GET", path)
        assert as_admin.status_code == 200, f"{path} 管理端会话 {as_admin.status_code}: {as_admin.text}"

    _staff_reader_session(api)
    for path in PREP_PLAN_READ_PATHS:
        as_staff = api.as_staff("GET", path)
        assert as_staff.status_code == 200, f"{path} 员工会话 {as_staff.status_code}: {as_staff.text}"

    # 两把钥匙拿到的是同一份内容（读面不分身份裁剪）。
    as_admin = api.as_cookie("GET", "/api/prep-plan/current")
    as_staff = api.as_staff("GET", "/api/prep-plan/current")
    assert as_admin.json() == as_staff.json()


def test_prep_plan_writes_still_require_admin(api):
    """票 08：进工作台只是换位置，**不扩权** —— 写端点仍只认管理端。

    员工 cookie 打写接口必须 401（拒绝来自鉴权门），管理端 cookie 打同一条接口照常
    —— 正反两条一起钉，免得「加了读门」顺手把写门也放宽。
    """
    _staff_reader_session(api)

    for method, path, payload in PREP_PLAN_WRITE_PATHS:
        staff_resp = api.as_staff(method, path, json=payload)
        assert staff_resp.status_code == 401, f"{method} {path} 员工居然 {staff_resp.status_code}"
        assert staff_resp.json() == AUTH_GATE_BODY, staff_resp.text

    # 管理端照常：`init-items-from-rules` 是一条不依赖业务状态的写路由（空库也是 200），
    # 拿它证明写面本身没被这一票弄坏 —— 而且真的写进去了（created 计数从 0 变 1）。
    _seed_semi_finished_rule(api)
    admin_resp = api.as_cookie("POST", "/api/prep-plan/init-items-from-rules")
    assert admin_resp.status_code == 200, admin_resp.text
    assert admin_resp.json()["created"] == 1, admin_resp.text


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


# ---------------------------------------------------------------------------
# 5. 「任一身份」门（票 02 建依赖本身；接线到配方 / 备货计划是 07 / 08 的事）
# ---------------------------------------------------------------------------
# 工作台是第一个两种身份都能进的地方，配方与备货计划的读接口随后要共用同一条门
# （ADR 0092）。本票不改任何业务路由，所以这里用一条探针 app 打它 —— 依赖必须真的
# 可用：真 `main.app` 的 lifespan 建库与员工账号服务，管理端会话走 `/api/auth/login`，
# 员工会话走 `/api/hygiene/staff/login`，两个都是真 cookie。
PROBE_PATH = "/probe"


def _staff_session_cookie(client) -> str:
    """在真 app 上走完员工链路，返回员工会话的 cookie 原文（库里存的是它的 sha256）。"""
    registered = client.post(
        "/api/hygiene/staff/register",
        json={"name": "张三", "phone": "13800138000", "password": ADMIN_PASSWORD},
    )
    assert registered.status_code == 200, registered.text
    employee = registered.json()["employee"]
    approved = client.post(f"/api/hygiene/admin/roster/{employee['id']}/approve")
    assert approved.status_code == 200, approved.text
    login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": "13800138000", "password": ADMIN_PASSWORD},
    )
    assert login.status_code == 200, login.text
    session_id = client.cookies.get(settings.STAFF_SESSION_COOKIE_NAME)
    assert session_id, "员工登录必须下发员工会话 cookie"
    return session_id


@pytest.fixture
def any_identity_gate(tmp_path):
    """一条只挂「任一身份」门的探针 app + 真 app 的装配（runtime / 员工账号服务）。"""
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    import main as main_module
    from api.security import require_any_identity_session

    probe = FastAPI()

    @probe.get(PROBE_PATH)
    async def _probe(identity=Depends(require_any_identity_session)):
        return {"kind": identity["kind"]}

    try:
        with TestClient(main_module.app) as client:
            yield client, TestClient(probe)
    finally:
        settings.DATABASE_DIR = old


def test_any_identity_gate_is_registered_as_a_known_guard():
    """名字必须与契约清单里那一条对得上 —— 重命名要一起改，否则挂上它的接口变「裸接口」。"""
    from api.security import require_any_identity_session

    assert require_any_identity_session.__name__ in GUARD_CALLABLES


def test_any_identity_gate_accepts_either_session(any_identity_gate):
    client, probe = any_identity_gate

    # 无凭据：401，body 与别的鉴权门一致（契约靠它把「鉴权拒绝」与业务 401 分开）。
    anonymous = probe.get(PROBE_PATH)
    assert anonymous.status_code == 401, anonymous.text
    assert anonymous.json() == AUTH_GATE_BODY

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
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert login.status_code == 200, login.text
    admin_session = client.cookies.get(settings.SESSION_COOKIE_NAME)
    assert admin_session

    staff_session = _staff_session_cookie(client)

    probe.cookies.set(settings.SESSION_COOKIE_NAME, admin_session)
    as_admin = probe.get(PROBE_PATH)
    assert as_admin.status_code == 200, as_admin.text
    assert as_admin.json() == {"kind": "admin"}

    probe.cookies.clear()
    probe.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, staff_session)
    as_staff = probe.get(PROBE_PATH)
    assert as_staff.status_code == 200, as_staff.text
    assert as_staff.json() == {"kind": "staff"}


def test_any_identity_gate_prefers_the_admin_session(any_identity_gate):
    """同浏览器两套 cookie：管理端那一支先判（与卫生的标准图门同一顺序）。"""
    client, probe = any_identity_gate

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
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert login.status_code == 200, login.text
    staff_session = _staff_session_cookie(client)

    probe.cookies.set(
        settings.SESSION_COOKIE_NAME, client.cookies.get(settings.SESSION_COOKIE_NAME)
    )
    probe.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, staff_session)

    resp = probe.get(PROBE_PATH)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"kind": "admin"}


def test_any_identity_gate_rejects_bogus_cookies(any_identity_gate):
    """有 cookie 不等于有会话：伪造 / 过期的凭据一律 401。"""
    _client, probe = any_identity_gate

    probe.cookies.set(settings.SESSION_COOKIE_NAME, "bogus-admin-session")
    probe.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, "bogus-staff-session")

    resp = probe.get(PROBE_PATH)

    assert resp.status_code == 401, resp.text
    assert resp.json() == AUTH_GATE_BODY
