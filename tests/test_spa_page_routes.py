#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前后端 SPA 页面路由契约：vue-router 注册的页面路径必须都能被后端命中。

缺陷背景（`.scratch/project-review-2026-09-22` DOC-01）：`/hygiene-data`（票 05 起是
`/hygiene/data`）只在 `admin-web/src/router/index.js` 注册，`main.py` 的页面路由清单漏了
它，而 `main.py` 没有 catch-all。直连 uvicorn（Docker 场景 `deploy/docker-entrypoint.sh`、
运维 curl 排查）硬导航或刷新该路径就是 404 —— 管理端里点得进去，一刷新就 404，
很容易被误判成反代故障。

**为什么不能靠反代兜底**：`deploy/nginx.conf:92` 的
`location ~ ^/(admin|sales-report|logs|prep-plan|wecom-push|recipe)(/.*)?$` 与
`deploy/Caddyfile:81-82` 的 `@spa path` 是同样六个前缀，`try_files … /index.html`
只写在这两个白名单块里；`/hygiene/*`、`/login`、`/settings` 全部落到兜底的
`reverse_proxy 127.0.0.1:8000`。所以 hygiene 页面必须在 `main.py` 里逐条注册，
本文件按这个前提断言。

方向：**前端有的，后端必须有**（本票的缺陷方向）。反向（后端有、前端无）目前
只剩三条已知例外，用显式清单表达 —— 见 `BACKEND_ONLY_PAGE_EXCEPTIONS`。
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_JS = REPO_ROOT / "admin-web" / "src" / "router" / "index.js"

# 反向例外（后端页面路由有、vue-router 没有）。每项都必须写明理由与归属，
# 新增例外要在这里留痕 —— 不许悄悄放过一条后端孤例。
BACKEND_ONLY_PAGE_EXCEPTIONS = {
    "/index.html": "SPA 外壳的静态文件名；同一页面在 vue-router 里叫 '/'，不是独立路由。",
    "/admin/": "尾斜杠变体；vue-router 只注册 '/admin'（strict 默认 false，'/admin/' 照样匹配）。",
}


def _router_routes_block() -> str:
    """取出 index.js 里 `const routes = [...]` 这一段的源码。"""
    source = ROUTER_JS.read_text(encoding="utf-8")
    match = re.search(r"const routes = \[(.*?)\n\]", source, re.S)
    if not match:
        raise AssertionError(
            f"{ROUTER_JS} 里找不到 `const routes = [...]` 数组 —— "
            "如果 router 改了结构，这条契约测试的解析规则也要跟着改。"
        )
    return match.group(1)


def _router_page_paths_by_form() -> dict[str, list[str]]:
    """按三种注册写法分别解析页面路径（不硬编码清单，从 router 源码里读）。

    - `path: '/x'`：直接写的路由对象（含 `/`、`/admin`、`/hygiene` 等）；
    - `hygieneAdminPage('/x', …)`：卫生管理端页面（套 HygieneAdminLayout）；
    - `hygieneStaffAuthPage('/x', …)`：员工手机端页面（票 03 起只剩 `/register`；
      员工登录页已并入 `/login`）。
    """
    block = _router_routes_block()
    return {
        "path:": re.findall(r"path:\s*'([^']+)'", block),
        "hygieneAdminPage": re.findall(r"hygieneAdminPage\(\s*'([^']+)'", block),
        "hygieneStaffAuthPage": re.findall(r"hygieneStaffAuthPage\(\s*'([^']+)'", block),
    }


def _router_page_paths() -> set[str]:
    by_form = _router_page_paths_by_form()
    paths: set[str] = set()
    for form, found in by_form.items():
        # 解析必须覆盖到每一种写法：否则 router 改格式后这条测试会「空集 ⊆ 任何集合」
        # 静默变绿。
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


class SpaPageRouteContractTest(unittest.TestCase):
    def test_router_registers_hygiene_data(self):
        """本票的缺陷锚点：前端确实注册了 /hygiene/data（票 05 前的 /hygiene-data）。"""
        self.assertIn("/hygiene/data", _router_page_paths())

    def test_router_parse_covers_all_three_registration_forms(self):
        by_form = _router_page_paths_by_form()
        self.assertGreaterEqual(len(by_form["path:"]), 10)
        self.assertIn("/hygiene/roster", by_form["hygieneAdminPage"])
        self.assertIn("/hygiene/data", by_form["hygieneAdminPage"])
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

    def test_hygiene_data_page_route_exists(self):
        import main as main_module

        self.assertIn("/hygiene/data", main_module.SPA_PAGE_ROUTES)
        self.assertIn("/hygiene/data", _registered_get_paths())

    def test_spa_page_routes_constant_matches_registration(self):
        """常量是注册与测试共用的唯一清单：改了常量却漏注册（或反过来）要红。"""
        import main as main_module

        routes = list(main_module.SPA_PAGE_ROUTES)
        self.assertEqual(len(routes), len(set(routes)), f"SPA_PAGE_ROUTES 有重复项：{routes}")
        unregistered = sorted(set(routes) - _registered_get_paths())
        self.assertEqual(unregistered, [], f"SPA_PAGE_ROUTES 里没注册成 GET 的路径：{unregistered}")

    def test_backend_only_page_routes_are_the_documented_exceptions(self):
        """反向：后端有、前端无 —— 只允许显式例外清单里的三条。"""
        import main as main_module

        backend_only = set(main_module.SPA_PAGE_ROUTES) - _router_page_paths()
        self.assertEqual(
            backend_only,
            set(BACKEND_ONLY_PAGE_EXCEPTIONS),
            "后端页面路由与 vue-router 的差集变了：新增孤例要么补前端页面，"
            "要么在 BACKEND_ONLY_PAGE_EXCEPTIONS 里写明理由。",
        )

    def test_hygiene_data_serves_the_spa_shell_instead_of_404(self):
        """直连 uvicorn 时 /hygiene/data 必须返回 SPA 外壳（而不是 404）。"""
        import main as main_module

        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "index.html"
            index.write_text("<!doctype html><title>禄云管理后台</title>", encoding="utf-8")
            original = main_module.spa_index_path
            main_module.spa_index_path = str(index)
            try:
                client = TestClient(main_module.app)
                # 非页面请求（curl / 探针：Accept 不是 text/html）不经过登录墙，
                # 直接命中页面路由 —— 修复前这条就是 404。
                response = client.get("/hygiene/data")
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertIn("禄云管理后台", response.text)

                # 浏览器硬导航：未登录由 HtmlAuthMiddleware 302 到 /login，也不是 404。
                navigation = client.get(
                    "/hygiene/data",
                    headers={"accept": "text/html"},
                    follow_redirects=False,
                )
                self.assertEqual(navigation.status_code, 302)
                self.assertEqual(
                    navigation.headers["location"],
                    "/login?next=%2Fhygiene%2Fdata",
                )
            finally:
                main_module.spa_index_path = original


if __name__ == "__main__":
    unittest.main()
