#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSRF 纵深：跨站写请求拦截。

主要屏障是会话 cookie 的 ``SameSite=Lax``；这里测的是第二层——判定优先看
``Sec-Fetch-Site``（现代浏览器都发，且不受反向代理改写 Host 的影响），缺失时
退回比较 ``Origin`` 与 ``Host``，两者都缺则放行（curl / 脚本 / 老浏览器）。
"""

import unittest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from api.security import csrf_origin_rejected
from config import settings

STAFF_COOKIE = f"{settings.STAFF_SESSION_COOKIE_NAME}=staff-session-value"
ADMIN_COOKIE = f"{settings.SESSION_COOKIE_NAME}=admin-session-value"
HOST = "luyun.example.com:8000"


def make_request(method="POST", cookie=None, headers=None):
    raw_headers = [
        (key.lower().encode("latin-1"), str(value).encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    if cookie:
        raw_headers.append((b"cookie", cookie.encode("latin-1")))
    return StarletteRequest({
        "type": "http",
        "method": method,
        "path": "/api/hygiene/staff/me",
        "headers": raw_headers,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
    })


class CsrfOriginRejectedTest(unittest.TestCase):
    def test_cross_site_write_with_session_cookie_is_rejected(self):
        for cookie in (STAFF_COOKIE, ADMIN_COOKIE):
            request = make_request(
                "POST",
                cookie=cookie,
                headers={"sec-fetch-site": "cross-site", "host": HOST},
            )
            self.assertTrue(csrf_origin_rejected(request), cookie)

    def test_same_origin_and_same_site_are_allowed(self):
        for value in ("same-origin", "same-site", "none"):
            request = make_request(
                "POST",
                cookie=STAFF_COOKIE,
                headers={"sec-fetch-site": value, "host": HOST},
            )
            self.assertFalse(csrf_origin_rejected(request), value)

    def test_write_without_session_cookie_is_not_our_business(self):
        """没有 cookie 就没有 CSRF 面——KDS 那类 ?token= 客户端不受影响。"""
        request = make_request(
            "POST",
            headers={"sec-fetch-site": "cross-site", "host": HOST},
        )
        self.assertFalse(csrf_origin_rejected(request))

    def test_explicit_token_header_is_exempt(self):
        for header in ("authorization", "x-admin-token"):
            request = make_request(
                "POST",
                cookie=STAFF_COOKIE,
                headers={
                    "sec-fetch-site": "cross-site",
                    "host": HOST,
                    header: "Bearer whatever",
                },
            )
            self.assertFalse(csrf_origin_rejected(request), header)

    def test_safe_methods_are_never_checked(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            request = make_request(
                method,
                cookie=STAFF_COOKIE,
                headers={"sec-fetch-site": "cross-site", "host": HOST},
            )
            self.assertFalse(csrf_origin_rejected(request), method)

    def test_origin_fallback_matches_host(self):
        allowed = make_request(
            "POST",
            cookie=STAFF_COOKIE,
            headers={"origin": f"http://{HOST}", "host": HOST},
        )
        self.assertFalse(csrf_origin_rejected(allowed))

        rejected = make_request(
            "POST",
            cookie=STAFF_COOKIE,
            headers={"origin": "http://evil.example.com", "host": HOST},
        )
        self.assertTrue(csrf_origin_rejected(rejected))

    def test_origin_fallback_is_case_insensitive(self):
        request = make_request(
            "POST",
            cookie=STAFF_COOKIE,
            headers={"origin": f"http://{HOST.upper()}", "host": HOST},
        )
        self.assertFalse(csrf_origin_rejected(request))

    def test_sec_fetch_site_wins_over_mismatched_origin(self):
        """Vite 开发代理是 changeOrigin: true，Origin 与 Host 必然不等；
        浏览器发的 Sec-Fetch-Site 才是准的——否则本机开发全被拦掉。"""
        request = make_request(
            "POST",
            cookie=STAFF_COOKIE,
            headers={
                "sec-fetch-site": "same-origin",
                "origin": "http://localhost:5173",
                "host": "localhost:8000",
            },
        )
        self.assertFalse(csrf_origin_rejected(request))

    def test_missing_both_signals_is_allowed(self):
        request = make_request("POST", cookie=STAFF_COOKIE, headers={"host": HOST})
        self.assertFalse(csrf_origin_rejected(request))
        request = make_request("POST", cookie=STAFF_COOKIE)
        self.assertFalse(csrf_origin_rejected(request))

    def test_null_origin_is_rejected(self):
        """沙箱 iframe / file:// 发的是字面量 "null"，不是「没有 Origin」。"""
        request = make_request(
            "POST",
            cookie=STAFF_COOKIE,
            headers={"origin": "null", "host": HOST},
        )
        self.assertTrue(csrf_origin_rejected(request))


class CsrfMiddlewareContractTest(unittest.TestCase):
    """main.py 里那段中间件的行为契约：403 + 中文 detail。"""

    def setUp(self):
        app = FastAPI()

        @app.middleware("http")
        async def guard(request: Request, call_next):
            if csrf_origin_rejected(request):
                return JSONResponse(status_code=403, content={"detail": "跨站请求被拒绝"})
            return await call_next(request)

        @app.post("/write")
        async def write():
            return {"ok": True}

        self.client = TestClient(app)

    def test_cross_site_write_is_blocked_with_readable_detail(self):
        resp = self.client.post(
            "/write",
            headers={"sec-fetch-site": "cross-site", "cookie": STAFF_COOKIE},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"], "跨站请求被拒绝")

    def test_same_origin_write_passes(self):
        resp = self.client.post(
            "/write",
            headers={"sec-fetch-site": "same-origin", "cookie": STAFF_COOKIE},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
