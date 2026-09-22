#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准图导出的状态 / 下载接口必须同样要管理员会话（SEC-03）。

缺陷背景（`.scratch/project-review-2026-09-22` SEC-03）：同一能力的三个接口鉴权不一致
——`POST /admin/standards-export/jobs` 要 `Depends(require_session)`，两个 GET
（状态与下载）的依赖树是空的。下载返回的是打包好的**卫生标准原图 zip**。

危害被随机性压住：`job_id = uuid4().hex`（122 bit）不可枚举。风险在于这类 URL 会进
浏览器历史、代理日志与聊天记录——泄露即等于任意人可下载该次导出的全部原图。

前端不依赖无 cookie 直链：`admin-web/src/views/hygiene/HygieneZonesView.vue` 的
`exportStandards()` 走 `api.download()`（`admin-web/src/api/client.js` 的
`fetch(path, { credentials: 'include' })`），所以加门不需要配套改造。

本文件只钉鉴权（403/404/409 那些业务分支在 `test_hygiene_standards_export_http.py`，
那边的 conftest 会把 `require_session` 覆写成已登录）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.hygiene as hygiene_module

EXPORT_JOBS = "/api/hygiene/admin/standards-export/jobs"


@pytest.fixture
def anonymous_client(monkeypatch):
    """不覆写 `require_session`，并让会话校验恒为失败 —— 就是一个无凭据客户端。"""
    from services import auth_service

    async def _no_session(_session_id):
        return False

    monkeypatch.setattr(auth_service, "validate_session_id", _no_session)
    app = FastAPI()
    app.include_router(hygiene_module.router)
    with TestClient(app) as client:
        yield client


def test_status_endpoint_requires_admin_session(anonymous_client):
    response = anonymous_client.get(f"{EXPORT_JOBS}/whatever")
    assert response.status_code == 401, response.text


def test_download_endpoint_requires_admin_session(anonymous_client):
    response = anonymous_client.get(f"{EXPORT_JOBS}/whatever/download")
    assert response.status_code == 401, response.text


def test_create_endpoint_still_requires_admin_session(anonymous_client):
    """对照：创建端本来就是 401，别在改动里把它弄丢。"""
    response = anonymous_client.post(EXPORT_JOBS)
    assert response.status_code == 401, response.text


def test_export_routes_declare_the_session_dependency():
    """依赖树层面的直测：两个 GET 的依赖里必须有 require_session。

    只看状态码不够——将来有人用别的方式"让它 401"（比如路由级中间件）也过得去，
    但这条契约问的是"这个接口有没有挂管理员会话门"。
    """
    from fastapi.routing import APIRoute

    checked = {}
    for route in hygiene_module.router.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith(f"{hygiene_module.router.prefix}/admin/standards-export/jobs"):
            continue
        names = set()

        def walk(dependant) -> None:
            for dependency in dependant.dependencies:
                if dependency.call is not None:
                    names.add(getattr(dependency.call, "__name__", str(dependency.call)))
                walk(dependency)

        walk(route.dependant)
        for method in sorted(route.methods or set()):
            checked[(method, route.path)] = names

    assert checked, "没找到 standards-export 的路由"
    for key, names in checked.items():
        assert "require_session" in names, f"{key} 的依赖树里没有 require_session：{sorted(names)}"
