#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前后端 SPA 页面路由契约：vue-router 注册的页面路径必须都能被后端命中。

缺陷背景（`.scratch/project-review-2026-09-22` DOC-01）：`/hygiene-data`（票 05 起是
`/hygiene/data`）只在 `admin-web/src/router/index.js` 注册，`main.py` 的页面路由清单漏了
它，而 `main.py` 没有 catch-all。直连 uvicorn（Docker 场景 `deploy/docker-entrypoint.sh`、
运维 curl 排查）硬导航或刷新该路径就是 404 —— 管理端里点得进去，一刷新就 404，
很容易被误判成反代故障。

**为什么不能靠反代兜底**：`deploy/nginx.conf` 的
`location ~ ^/(admin|sales-report|logs|wecom-push|workbench)(/.*)?$` 与
`deploy/Caddyfile` 的 `@spa path` 是同样这五个前缀（票 11 起：工作台进来了，
搬走的 `/prep-plan`、`/recipe*` 清出去了），`try_files … /index.html` 只写在这两个
白名单块里；其余页面路由（`/login`、`/register`、`/settings` 等）落到兜底的
`reverse_proxy 127.0.0.1:8000`。而 `main.py` 没有 catch-all —— 每一条页面都必须在这里
逐条注册，否则直连 uvicorn（Docker 场景 `deploy/docker-entrypoint.sh`、运维 curl 排查）
就是 404。工作台进了反代白名单**也不改变这一点**：白名单只让经反代的那条路拿到静态壳，
直连后端那条路仍然逐条注册，本文件按这个前提断言。

**票 01 起页面清单只有一份来源**：`admin-web/src/router/pageRoutes.json`
（Vite 直接 `import`，本文件 `json.load`）。本文件因此同时钉三件事：

1. 表本身合法 —— 六个字段（路径 / 页面标题 / 所属组 / 允许的身份 / 是否独立外壳 /
   是否公开）齐全、取值在枚举内、路径唯一且为绝对路径；
2. vue-router 注册的路径集合与表**逐条相等**（双向；服务端别名不是页面，不参与）；
3. 后端 `main.SPA_PAGE_ROUTES` 与两张页面豁免表（`HTML_AUTH_EXACT` /
   `HTML_AUTH_PUBLIC_PAGES`）由表派生 —— 清单相等，「是否公开」再拿**真实请求**
   复核一遍（未登录硬导航 200 / 302 到 `/login`），而不是把名单在测试里再抄一遍。

方向：**前端有的，后端必须有**（本票的缺陷方向）。反向（后端有、前端无）只剩表里的
服务端别名（`aliases`：`/index.html`、`/admin/`）—— 不再有第二份例外清单。
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_JS = REPO_ROOT / "admin-web" / "src" / "router" / "index.js"
# 页面清单的唯一来源。加一页、改标题、改分组、改身份都只改这里（见文件头注释）。
PAGE_ROUTES_JSON = REPO_ROOT / "admin-web" / "src" / "router" / "pageRoutes.json"

# 表里的取值域。`home` 是工作台首页那一组（票 06 才有行），先在这里登记，免得以后
# 加一页时两边枚举对不上。
PAGE_GROUPS = {"home", "hr", "floor", "kitchen", "me", "system", "entry"}
PAGE_AUDIENCES = {"admin", "staff", "both"}

# 免墙表里**不属于任何页面**的壳层条目，逐条写明理由与归属。表里每一条要么对应清单里
# 的一页（或它的服务端别名），要么在这里留痕 —— 不许有清单之外的页面悄悄免墙。
#
# 票 03 起 `/staff` 那条不在了：员工三页搬进工作台（`/workbench/me/*`），页面壳的放行
# 根据是「任一会话有效」（`HTML_AUTH_EXACT` 收不了这个），旧的裸条目与 `/staff/` 前缀
# 一起删掉 —— `tests/test_auth.py` 有反向断言盯着它们不许回来。
NON_PAGE_AUTH_ENTRIES = {
    "/login.html": "SPA 外壳的静态文件名；同一页面在 vue-router 里叫 '/login'。",
}


def _page_rows() -> list[dict]:
    """读页面清单。文件被删/被抽空都当场报错，不许静默降级成空表。"""
    source = PAGE_ROUTES_JSON.read_text(encoding="utf-8")
    rows = json.loads(source)["pages"]
    if not rows:
        raise AssertionError(
            f"{PAGE_ROUTES_JSON} 的 pages 是空的 —— 页面清单是唯一来源，抽空它"
            "前后端就都失去契约了。"
        )
    return rows


def _table_page_paths() -> set[str]:
    return {row["path"] for row in _page_rows()}


def _table_alias_paths() -> set[str]:
    """服务端额外注册的路径变体（同一页面的别名，不是独立路由）。"""
    return {alias for row in _page_rows() for alias in row.get("aliases", [])}


# ── vue-router 源码解析（票 01 放宽解析面）──────────────────────────────────
# 认三种字面量（'…' / "…" / 无插值模板 `…`）与任意 `<名字>Page('路径', …)` 工厂。
# **认不出来的写法一律报错，绝不静默漏页**：漏掉的一页就是「直连 uvicorn 硬导航 404」，
# 而静默漏页正是这条契约最坏的失效方式（空集 ⊆ 任何集合）。
_QUOTED_PATH = r"""(?:'([^']+)'|"([^"]+)"|`([^`$]+)`)"""
_PATH_LITERAL_RE = re.compile(rf"\bpath:\s*{_QUOTED_PATH}")
_PAGE_FACTORY_CALL_RE = re.compile(rf"\b([A-Za-z_$][\w$]*Page)\(\s*{_QUOTED_PATH}")
_PAGE_FACTORY_NAME_RE = re.compile(r"\b([A-Za-z_$][\w$]*Page)\(")
_INTERPOLATED_PATH_RE = re.compile(r"\bpath:\s*`[^`]*\$\{")
_INTERPOLATED_FACTORY_RE = re.compile(r"\b[A-Za-z_$][\w$]*Page\(\s*`[^`]*\$\{")


def _routes_block(source: str) -> str:
    """取出 router 源码里 `const routes = [...]` 这一段（参数化，便于用合成源码测解析面）。"""
    match = re.search(r"const routes = \[(.*?)\n\]", source, re.S)
    if not match:
        raise AssertionError(
            f"{ROUTER_JS} 里找不到 `const routes = [...]` 数组 —— "
            "如果 router 改了结构，这条契约测试的解析规则也要跟着改。"
        )
    return match.group(1)


def _first_group(match: re.Match, start: int = 0) -> str:
    for group in match.groups()[start:]:
        if group:
            return group
    raise AssertionError(f"正则匹配里没有捕获到路径：{match.group(0)!r}")


def parse_page_paths(source: str) -> dict[str, list[str]]:
    """按注册写法分组解析页面路径（合成源码也能跑，见 `RouterParseSurfaceTest`）。

    - `path: '/x'` / `path: "/x"` / ``path: `/x` ``：直接写的路由对象；
    - `<名字>Page('/x', …)`：套壳工厂（今天叫 `hygieneAdminPage` / `hygieneStaffAuthPage`，
      名字以后换了也照样认，只要第一个参数是路径字面量）。

    `path: ''` 那种子记录的相对空路径**不算**一条页面路径（父路径本身就是那一页）。
    """
    block = _routes_block(source)
    if _INTERPOLATED_PATH_RE.search(block) or _INTERPOLATED_FACTORY_RE.search(block):
        raise AssertionError(
            "router 的 routes 数组里出现了插值模板路径（`` `/x/${slug}` ``）："
            "静态解析不出来，这条契约就没法保证后端也注册了它。"
            "要么写成字面量，要么把解析规则一起改掉 —— 不许让它静默漏过去。"
        )

    by_form: dict[str, list[str]] = {
        "path:": [_first_group(m) for m in _PATH_LITERAL_RE.finditer(block)]
    }

    factory_calls: dict[str, list[str]] = {}
    for match in _PAGE_FACTORY_CALL_RE.finditer(block):
        factory_calls.setdefault(match.group(1), []).append(_first_group(match, 1))
    by_form.update(factory_calls)

    # 工厂调用必须每一个都解析出路径：只认字面量的解析面遇上变量参数会「少一页」，
    # 那正是这条契约要拦的（旧写法同理 —— router 换了注册方式就得来改解析规则）。
    unresolved = sorted(set(_PAGE_FACTORY_NAME_RE.findall(block)) - set(factory_calls))
    if unresolved:
        raise AssertionError(
            f"这些路由工厂调用没能解析出路径：{unresolved}；"
            "解析规则可能跟不上 router 的新写法（变量路径、插值模板、新的包装函数）。"
        )
    return by_form


def _router_page_paths_by_form() -> dict[str, list[str]]:
    return parse_page_paths(ROUTER_JS.read_text(encoding="utf-8"))


def _router_page_paths() -> set[str]:
    by_form = _router_page_paths_by_form()
    paths: set[str] = set()
    for form, found in by_form.items():
        # 自检（票 01 保留）：解析必须覆盖到每一种写法，否则 router 改格式后这条测试会
        # 「空集 ⊆ 任何集合」静默变绿。
        if not found:
            raise AssertionError(
                f"没能从 {ROUTER_JS} 的 routes 数组里解析出 `{form}` 写法的路径；"
                "解析规则可能已被 router 的改写打破。"
            )
        paths.update(found)
    return paths


def _registered_get_paths() -> set[str]:
    """main.py 上真正注册成功的 GET 路径集合（不是常量，是路由表）。"""
    import main as main_module

    return {
        route.path
        for route in main_module.app.routes
        if isinstance(route, APIRoute) and "GET" in (route.methods or set())
    }


@contextmanager
def _spa_shell_index() -> Iterator[None]:
    """把 SPA 外壳临时指到一个可读的 index.html。

    `admin-web/dist` 是构建产物（本机可能没构建），而这里要断的是登录墙放不放行，
    不是构建产物在不在。
    """
    import main as main_module

    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index.html"
        index.write_text("<!doctype html><title>禄云管理后台</title>", encoding="utf-8")
        original = main_module.spa_index_path
        main_module.spa_index_path = str(index)
        try:
            yield
        finally:
            main_module.spa_index_path = original


class SpaPageRouteContractTest(unittest.TestCase):
    def test_router_registers_workbench_data(self):
        """缺陷锚点：前端确实注册了 `/workbench/floor/data`（票 05 前的 `/hygiene-data`，
        票 04 起在 `/workbench/*`，票 05 起落进「现场」组）。"""
        self.assertIn("/workbench/floor/data", _router_page_paths())

    def test_router_parse_covers_every_registration_form(self):
        """解析面按**写法**分组，每一组都得有东西（票 01 放宽后仍然如此）。"""
        by_form = _router_page_paths_by_form()
        self.assertGreaterEqual(len(by_form["path:"]), 10)
        # 票 05：人事组四页与现场组七页各走一条工厂；花名册（人事）与数据页（现场）
        # 分属两边 —— 工厂名换了，解析面照样要认得出路径。
        self.assertIn("/workbench/hr/roster", by_form["workbenchHrPage"])
        self.assertIn("/workbench/floor/data", by_form["hygieneAdminPage"])
        self.assertEqual(
            {"hygieneStaffAuthPage": sorted(by_form["hygieneStaffAuthPage"])},
            {"hygieneStaffAuthPage": ["/register"]},
        )

    def test_every_router_page_is_registered_on_the_backend(self):
        """前端有的，后端必须有 —— 缺一条就是硬导航/刷新 404。"""
        missing = sorted(_router_page_paths() - _registered_get_paths())
        self.assertEqual(
            missing,
            [],
            f"vue-router 有、main.py 未注册页面路由：{missing}；"
            "直连 uvicorn（无反代兜底）会 404。",
        )

    def test_only_the_workbench_prefix_is_registered(self):
        """票 05（收口）：工作台只剩「人事 / 现场」两组加员工那半边，旧前缀与**旧的平铺
        地址**一条都不留。

        两个方向都查：新前缀一条都不能少（少了 → 手机直连/刷新 404），旧地址一条都不能
        留（留一半最坏 —— "点得进去、刷新 404"）。前端的字面清单在
        `admin-web/src/router/__tests__/workbenchRoutes.test.js` 里也钉了一遍。

        票 05 之前 `/workbench/roster`、`/workbench/daily` 这些**平铺**地址是正式地址；
        这一票把它们按组重排到 `/workbench/hr/*` 与 `/workbench/floor/*` 之后，平铺那批
        就是旧地址了（自然 404、不留别名、不做重定向）—— 所以它们从 new_paths 挪进了
        old_paths，两个方向都查。

        票 07 把配方五页（列表 / 沉浸阅读 / 打印 / 印码 / 管理）搬进「后勤」组
        （`/workbench/kitchen/recipe*`）：那一批新地址进 new_paths，旧的 `/recipe*`
        跟着 `/staff/*` 一起进 old_paths（不留别名、不做重定向）。

        票 08 把备货计划从管理后台的 `/prep-plan` 搬进同一组
        （`/workbench/kitchen/prep-plan`）：新地址进 new_paths，旧地址进 old_paths。
        """
        import main as main_module

        new_paths = {
            # 子应用根：票 06 之前仍渲染排班月历（清单里 group 标 home 的那一行），
            # 不许 404。
            "/workbench",
            # 人事组（票 05）。
            "/workbench/hr/calendar",
            "/workbench/hr/inbox",
            "/workbench/hr/shifts",
            "/workbench/hr/roster",
            # 现场组（票 05）。
            "/workbench/floor/zones",
            "/workbench/floor/daily",
            "/workbench/floor/deep-clean",
            "/workbench/floor/fix",
            "/workbench/floor/boards",
            "/workbench/floor/attire",
            "/workbench/floor/data",
            # 票 03：员工三页搬进「我的」组，越权落点也是工作台里的一页。
            "/workbench/me/today",
            "/workbench/me/month",
            "/workbench/me/clean",
            "/workbench/forbidden",
            # 后勤组（票 07）：配方五页；（票 08）备货计划一并搬进来。
            "/workbench/kitchen/recipe",
            "/workbench/kitchen/recipe/detail",
            "/workbench/kitchen/recipe/print",
            "/workbench/kitchen/recipe/qr",
            "/workbench/kitchen/recipe/manage",
            "/workbench/kitchen/prep-plan",
        }
        old_paths = {
            "/scheduling",
            "/scheduling/inbox",
            "/scheduling/shifts",
            "/hygiene/roster",
            "/hygiene/zones",
            "/hygiene/daily",
            "/hygiene/deep-clean",
            "/hygiene/fix",
            "/hygiene/boards",
            "/hygiene/data",
            "/hygiene/attire",
            # 票 03：员工三页的旧址（含裸前缀那条「免墙却没页面」的条目，audit 条目 15）。
            "/staff",
            "/staff/today",
            "/staff/month",
            "/staff/clean",
            # 票 05：工作台内部的**平铺**地址（重排进 hr / floor 两组之前的正式地址）。
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
            # 票 07：配方五页的旧址（独立域 `/recipe*` 时代的正式地址）。
            "/recipe",
            "/recipe/detail",
            "/recipe/print",
            "/recipe/qr",
            "/recipe/manage",
            # 票 08：备货计划的老地址（管理后台的 `/prep-plan`）—— 搬进「后勤」组之后
            # 从 vue-router 与 `SPA_PAGE_ROUTES` 一起删掉，自然 404、不留别名。
            "/prep-plan",
        }
        router_paths = _router_page_paths()
        self.assertEqual(sorted(new_paths - router_paths), [], "vue-router 少了工作台页面")
        self.assertEqual(sorted(old_paths & router_paths), [], "旧前缀还留在 vue-router 里")
        self.assertEqual(
            sorted(new_paths - set(main_module.SPA_PAGE_ROUTES)), [], "main.py 清单少了工作台页面"
        )
        self.assertEqual(
            sorted(old_paths & set(main_module.SPA_PAGE_ROUTES)), [], "旧前缀还留在 main.py 清单里"
        )
        self.assertEqual(
            sorted(new_paths - _registered_get_paths()), [], "工作台页面没真正注册成 GET 路由"
        )
        self.assertEqual(
            sorted(old_paths & _registered_get_paths()), [], "旧前缀还注册着 GET 路由"
        )

    def test_workbench_data_page_route_exists(self):
        import main as main_module

        self.assertIn("/workbench/floor/data", main_module.SPA_PAGE_ROUTES)
        self.assertIn("/workbench/floor/data", _registered_get_paths())

    def test_spa_page_routes_constant_matches_registration(self):
        """常量是注册与测试共用的唯一清单：改了常量却漏注册（或反过来）要红。"""
        import main as main_module

        routes = list(main_module.SPA_PAGE_ROUTES)
        self.assertEqual(len(routes), len(set(routes)), f"SPA_PAGE_ROUTES 有重复项：{routes}")
        unregistered = sorted(set(routes) - _registered_get_paths())
        self.assertEqual(unregistered, [], f"SPA_PAGE_ROUTES 里没注册成 GET 的路径：{unregistered}")

    def test_workbench_data_serves_the_spa_shell_instead_of_404(self):
        """直连 uvicorn 时 /workbench/floor/data 必须返回 SPA 外壳（而不是 404）。"""
        import main as main_module

        with _spa_shell_index():
            client = TestClient(main_module.app)
            # 非页面请求（curl / 探针：Accept 不是 text/html）不经过登录墙，
            # 直接命中页面路由 —— 修复前这条就是 404。
            response = client.get("/workbench/floor/data")
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/html", response.headers["content-type"])
            self.assertIn("禄云管理后台", response.text)

            # 浏览器硬导航：未登录由 HtmlAuthMiddleware 302 到 /login，也不是 404。
            navigation = client.get(
                "/workbench/floor/data",
                headers={"accept": "text/html"},
                follow_redirects=False,
            )
            self.assertEqual(navigation.status_code, 302)
            self.assertEqual(
                navigation.headers["location"],
                "/login?next=%2Fworkbench%2Ffloor%2Fdata",
            )


class RouterParseSurfaceTest(unittest.TestCase):
    """解析面（票 01 放宽）：认得模板字符串与新的工厂名，但抽不到东西时必须红。

    合成源码跑的是 `parse_page_paths` 本身 —— 用真实 router 测不出「换成别的写法还认
    不认」，而重排路径那几张票要的正是这个保证。
    """

    def test_reads_single_double_and_template_literals(self):
        source = (
            "const routes = [\n"
            "  { path: '/a', name: 'a' },\n"
            "  { path: \"/b\", name: 'b' },\n"
            "  { path: `/c`, name: 'c' },\n"
            "]"
        )
        self.assertEqual(sorted(parse_page_paths(source)["path:"]), ["/a", "/b", "/c"])

    def test_reads_any_page_factory_name(self):
        source = (
            "const routes = [\n"
            "  workbenchPage('/workbench/hr/roster', 'roster', () => import('x')),\n"
            "  staffPage(`/workbench/me/today`, 'today', () => import('y')),\n"
            "]"
        )
        by_form = parse_page_paths(source)
        self.assertEqual(by_form["workbenchPage"], ["/workbench/hr/roster"])
        self.assertEqual(by_form["staffPage"], ["/workbench/me/today"])

    def test_interpolated_template_path_is_rejected_loudly(self):
        source = "const routes = [\n  { path: `/workbench/${slug}`, name: 'x' },\n]"
        with self.assertRaises(AssertionError):
            parse_page_paths(source)

    def test_interpolated_factory_path_is_rejected_loudly(self):
        source = "const routes = [\n  adminPage(`/workbench/${slug}`, 'x'),\n]"
        with self.assertRaises(AssertionError):
            parse_page_paths(source)

    def test_factory_call_without_a_literal_path_is_rejected_loudly(self):
        """新工厂名认得了，但它没解析出路径时仍要红 —— 这就是「抽空即失败」的现行版本。"""
        source = "const routes = [\n  adminPage(VIEW_PATH, 'x'),\n]"
        with self.assertRaises(AssertionError):
            parse_page_paths(source)

    def test_missing_routes_array_is_rejected_loudly(self):
        with self.assertRaises(AssertionError):
            parse_page_paths("export default []")


class PageInventorySingleSourceTest(unittest.TestCase):
    """页面清单是唯一来源：前端 vue-router 与后端三张表都对着它断言。"""

    def test_rows_carry_all_six_fields(self):
        for row in _page_rows():
            with self.subTest(path=row.get("path")):
                for field in ("path", "title", "group", "audience", "standalone", "public"):
                    self.assertIn(field, row, f"{row.get('path')} 少了 `{field}` 字段")
                self.assertTrue(str(row["title"]).strip(), f"{row['path']} 的标题是空的")

    def test_values_are_within_the_declared_domains(self):
        for row in _page_rows():
            with self.subTest(path=row["path"]):
                self.assertTrue(row["path"].startswith("/"), "页面路径必须是绝对路径")
                self.assertIn(row["group"], PAGE_GROUPS, f"{row['path']} 的分组不认识")
                self.assertIn(row["audience"], PAGE_AUDIENCES, f"{row['path']} 的身份不认识")
                self.assertIsInstance(row["standalone"], bool)
                self.assertIsInstance(row["public"], bool)

    def test_paths_are_unique_including_aliases(self):
        paths = [row["path"] for row in _page_rows()]
        self.assertEqual(len(paths), len(set(paths)), f"清单里有重复路径：{paths}")
        duplicated = sorted(set(paths) & _table_alias_paths())
        self.assertEqual(duplicated, [], f"别名与页面路径撞了：{duplicated}")

    def test_frontend_router_matches_the_inventory(self):
        """双向：vue-router 有的表里有，表里有的 vue-router 有。别名不是页面，不参与。"""
        self.assertEqual(sorted(_router_page_paths() ^ _table_page_paths()), [])

    def test_backend_page_routes_match_the_inventory(self):
        """`SPA_PAGE_ROUTES` == 清单里的页面 + 它们的服务端别名（不再是手抄的一份）。"""
        import main as main_module

        routes = list(main_module.SPA_PAGE_ROUTES)
        self.assertEqual(len(routes), len(set(routes)), f"SPA_PAGE_ROUTES 有重复项：{routes}")
        self.assertEqual(
            set(routes),
            _table_page_paths() | _table_alias_paths(),
            "main.py 的页面路由清单与 pageRoutes.json 对不上：清单里加一页就要注册一条，"
            "漏一条就是硬导航 404。",
        )
        self.assertEqual(sorted(set(routes) - _registered_get_paths()), [])

    def test_exemption_tables_match_the_inventory(self):
        """两张页面豁免表里每一条都要有出处：清单里的页、它的别名，或已登记的壳层条目。"""
        import main as main_module

        declared = set(main_module.HTML_AUTH_EXACT) | set(main_module.HTML_AUTH_PUBLIC_PAGES)
        accounted = _table_page_paths() | _table_alias_paths()
        self.assertEqual(
            sorted(declared - accounted),
            sorted(NON_PAGE_AUTH_ENTRIES),
            "页面豁免表里出现了清单之外的条目：要么它是某一页（补进 pageRoutes.json），"
            "要么不属于任何页面（写进 NON_PAGE_AUTH_ENTRIES 并说明理由）。",
        )

    def test_every_public_page_is_exempt_by_a_named_mechanism(self):
        """标了 public 的页，要么在豁免表里逐条登记，要么被免墙前缀罩住。"""
        import main as main_module

        declared = set(main_module.HTML_AUTH_EXACT) | set(main_module.HTML_AUTH_PUBLIC_PAGES)
        for row in _page_rows():
            if not row["public"] or row["path"] in declared:
                continue
            self.assertTrue(
                any(row["path"].startswith(prefix) for prefix in main_module.HTML_AUTH_PREFIXES),
                f"{row['path']} 标了 public，却既不在两张豁免表里、也不在免墙前缀下。",
            )

    def test_public_flag_matches_the_real_login_wall(self):
        """「是否公开」拿真实请求复核：未登录硬导航，公开页 200、其余 302 到 `/login`。

        这一条不看名单常量，看中间件实际怎么判 —— 名单写对了但前缀表把一页捎带放行，
        同样要红。
        """
        import main as main_module

        with _spa_shell_index():
            client = TestClient(main_module.app)
            for row in _page_rows():
                with self.subTest(path=row["path"]):
                    response = client.get(
                        row["path"], headers={"accept": "text/html"}, follow_redirects=False
                    )
                    if row["public"]:
                        self.assertEqual(
                            response.status_code,
                            200,
                            f"{row['path']} 在清单里是公开页，未登录硬导航却被拦了"
                            f"（{response.status_code} {response.headers.get('location')}）。",
                        )
                    else:
                        self.assertEqual(
                            response.status_code,
                            302,
                            f"{row['path']} 不是公开页，未登录硬导航却拿到了页面壳。",
                        )
                        self.assertTrue(
                            response.headers["location"].startswith("/login"),
                            response.headers["location"],
                        )


if __name__ == "__main__":
    unittest.main()
