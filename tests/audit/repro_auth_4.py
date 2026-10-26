#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现（切片 A · 权限/身份）：CSRF 纵深防御可被一个自定义请求头绕过。

## 缺陷

`api/security.py:48-50` 的 `csrf_origin_rejected`：带会话 cookie 的跨站写请求，
只要请求里有 `Authorization` 或 `X-Admin-Token` 头就**直接放行**。理由写在
`_CSRF_EXEMPT_HEADERS` 上方：

    带这些头的调用不是「浏览器自动附带凭据」，不构成 CSRF 面。

这个前提对**浏览器里的脚本**不成立：跨站 `fetch(..., {credentials: 'include',
headers: {'X-Admin-Token': 'x'}})` 只要 CORS 预检通过，浏览器就会把自定义头带上、
并把 cookie 一起发出去（`credentials: 'include'`）。而 `main.py` 的 CORS 配置是
`allow_origins=["*"] + allow_credentials=False + allow_headers=["*"]`（`main.py:839-845`），
预检对任意来源、任意请求头都放行 —— 下面第 2 部分用同一份配置的真实预检回包证明。

于是这一层「纵深」在浏览器面前是 0：攻击页只要多带一个头，`csrf_origin_rejected`
就返回 False。当前真正挡住它的是会话 cookie 的 `SameSite=Lax`（跨站 POST 不带 cookie），
所以**今天不可直接利用**；但这一层的存在理由恰恰是「哪天为了让员工页嵌进别的站点把
cookie 放宽成 SameSite=None」（见同一函数的 docstring）—— 那一刻它会静默失效。

## 预期 vs 实际

- 预期：跨站写请求（带会话 cookie、`Sec-Fetch-Site: cross-site`）一律拒绝，
  与是否带自定义头无关；要放过 KDS / 脚本那类非浏览器客户端，判据应该是
  「没有 cookie 才不看」或「Origin 与 Host 一致」，不是「带了某个头」。
- 实际：`csrf_origin_rejected == False`（放行）。

## 修的方向（不写完整补丁）

`Sec-Fetch-Site` 存在时以它为准（浏览器的请求一定有它，且攻击者改不了它），
把「自定义头豁免」限制在 `Sec-Fetch-Site` **缺失**的那条回退分支里 —— 非浏览器客户端
本来就不发 `Sec-Fetch-Site`，KDS / curl / 脚本不受影响。

## 跑法

    .venv/bin/python -m pytest tests/audit/repro_auth_4.py -x -q

红 = 缺陷存在。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.requests import Request

from api.security import csrf_origin_rejected
from config import settings

STAFF_COOKIE = f"{settings.STAFF_SESSION_COOKIE_NAME}=staff-session-value"
ADMIN_COOKIE = f"{settings.SESSION_COOKIE_NAME}=admin-session-value"
HOST = "luyun.example.com:8000"


def _request(method="POST", cookie=None, headers=None):
    raw_headers = [
        (key.lower().encode("latin-1"), str(value).encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    if cookie:
        raw_headers.append((b"cookie", cookie.encode("latin-1")))
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/wastage/records",
            "headers": raw_headers,
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def test_cross_site_write_with_bogus_token_header_is_still_rejected():
    """浏览器能造的跨站写请求（自定义头 + cookie + cross-site）：必须拒。"""
    for header in ("x-admin-token", "authorization"):
        request = _request(
            "POST",
            cookie=ADMIN_COOKIE,
            headers={
                "sec-fetch-site": "cross-site",
                "host": HOST,
                header: "anything",  # 值不重要：守卫根本不看它的值
            },
        )
        assert csrf_origin_rejected(request) is True, (
            f"带一个假 {header} 头的跨站写请求被放行了 —— "
            "攻击页只要多带这个头就能穿过 CSRF 纵深（当前只剩 SameSite=Lax 一层）"
        )


def test_cors_preflight_lets_an_attacker_attach_that_header():
    """同一份 CORS 配置（main.py:839-845）放行任意来源 + 任意请求头。"""
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/api/wastage/records")
    async def write():  # pragma: no cover - 只为预检
        return {"ok": True}

    with TestClient(app) as client:
        preflight = client.options(
            "/api/wastage/records",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-admin-token,content-type",
            },
        )
    assert preflight.status_code == 200, preflight.text
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert "x-admin-token" in preflight.headers["access-control-allow-headers"].lower(), (
        "预检必须放行这个头，上面那条「浏览器能造出来」才成立"
    )
