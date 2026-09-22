#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`/api/logs/recent` 必须只有一条 GET，且必须带管理员凭据门。

缺陷背景（`.scratch/project-review-2026-09-22` SEC-05）：`api/logs.py` 的 router 级
`dependencies=[Depends(verify_admin_token)]` 与一条 `main.py` 的无鉴权 handler 注册**同一
路径**。FastAPI 按声明顺序匹配，因此当前生效的是带鉴权的那条（`include_router` 早于
`main.py` 的那段），无凭据请求返回 401——**今天没有越权**。风险是"改错文件"与静默降级：
把 `@app.get` 提到 `include_router` 之前，同一 URL 会突然变成匿名可读，而没有任何测试
会红。

本文件把那条契约钉死：路径唯一 + 唯一的那条依赖树里有 `verify_admin_token`。
"""

from __future__ import annotations

from fastapi.routing import APIRoute

LOGS_RECENT = "/api/logs/recent"


def _recent_get_routes():
    import main as main_module

    return [
        route
        for route in main_module.app.routes
        if isinstance(route, APIRoute) and route.path == LOGS_RECENT and "GET" in (route.methods or set())
    ]


def _guard_names(route) -> set:
    names = set()

    def walk(dependant) -> None:
        for dependency in dependant.dependencies:
            if dependency.call is not None:
                names.add(getattr(dependency.call, "__name__", str(dependency.call)))
            walk(dependency)

    walk(route.dependant)
    return names


def test_only_one_handler_is_registered_for_logs_recent():
    """同路径两条 handler = 生效者取决于注册顺序，必须只剩一条。"""
    routes = _recent_get_routes()
    assert len(routes) == 1, (
        f"{LOGS_RECENT} 注册了 {len(routes)} 条 GET："
        f"{[r.endpoint.__module__ + '.' + r.endpoint.__name__ for r in routes]}；"
        "重复注册时生效者由声明顺序决定，顺序一变鉴权就可能静默消失。"
    )


def test_the_surviving_handler_requires_admin_credentials():
    routes = _recent_get_routes()
    assert routes, f"{LOGS_RECENT} 没有任何 GET handler"
    guards = _guard_names(routes[0])
    assert "verify_admin_token" in guards, (
        f"{LOGS_RECENT} 的 handler "
        f"({routes[0].endpoint.__module__}.{routes[0].endpoint.__name__}) "
        f"依赖树里没有 verify_admin_token：{sorted(guards)}"
    )


def test_unauthenticated_request_is_401():
    """端到端再确认一次：无凭据拿不到内存日志。"""
    from fastapi.testclient import TestClient

    import main as main_module

    with TestClient(main_module.app) as client:
        response = client.get(LOGS_RECENT)
    assert response.status_code == 401, response.text
