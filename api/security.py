#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理接口鉴权（Session Cookie、API Token、过渡期 ADMIN_API_KEY）。"""

import logging
from typing import Optional
from urllib.parse import urlparse

from fastapi import Header, HTTPException, Request

from config import settings
from services import auth_service

logger = logging.getLogger(__name__)
_warned_open_admin = False

# 会改状态的方法。GET/HEAD/OPTIONS 不在其中（OPTIONS 还要留给 CORS 预检）。
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# 带这些头的调用不是「浏览器自动附带凭据」，不构成 CSRF 面。
_CSRF_EXEMPT_HEADERS = ("authorization", "x-admin-token")


def csrf_origin_rejected(request: Request) -> bool:
    """跨站写请求判定（CSRF 纵深防御）。

    只对**浏览器会自动附带凭据**的请求生效：带会话 cookie、且没有显式 token 头。

    判定优先看 `Sec-Fetch-Site`——现代浏览器都发，而且**不受反向代理改写 Host 的
    影响**（部署里的 nginx/Caddy 都转发原 Host，但 Vite 开发代理是
    ``changeOrigin: true``，用 Origin/Host 比较会把本机开发全拦掉）：

    * ``cross-site`` → 拒绝；
    * ``same-origin`` / ``same-site`` / ``none`` → 放行；
    * 缺失（老浏览器、curl、脚本）→ 退回比较 ``Origin`` 与 ``Host``；两者都缺则放行。

    `SameSite=Lax` 仍是主要屏障，这里是纵深：一旦将来为了让员工页嵌进别的站点而把
    cookie 放宽成 ``SameSite=None``，写接口不会被跨站表单直接打穿。
    """
    if request.method not in _UNSAFE_METHODS:
        return False
    cookies = request.cookies
    if not (
        cookies.get(settings.SESSION_COOKIE_NAME)
        or cookies.get(settings.STAFF_SESSION_COOKIE_NAME)
    ):
        return False
    for header in _CSRF_EXEMPT_HEADERS:
        if request.headers.get(header):
            return False

    fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site:
        return fetch_site == "cross-site"

    origin = (request.headers.get("origin") or "").strip()
    host = (request.headers.get("host") or "").strip()
    if not origin or not host:
        return False
    return urlparse(origin).netloc.lower() != host.lower()


def warn_if_admin_open() -> None:
    """未初始化认证时在启动日志中告警。"""
    global _warned_open_admin
    if settings.ADMIN_API_KEY or _warned_open_admin:
        return
    _warned_open_admin = True
    logger.warning(
        "⚠️ 认证未初始化：请访问 /login 完成首次设置。"
        "在初始化前，写接口仍对局域网开放（若无 ADMIN_API_KEY）。"
    )


async def verify_admin_token(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
) -> bool:
    """校验 Session Cookie、API Token 或过渡期 ADMIN_API_KEY。"""
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if await auth_service.validate_session_id(session_id):
        return True

    bearer_token = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()
    provided = x_admin_token or bearer_token
    if provided and await auth_service.validate_api_token(provided):
        return True
    if settings.ADMIN_API_KEY and provided == settings.ADMIN_API_KEY:
        logger.warning("ADMIN_API_KEY 已弃用，请改用 Web 登录或 api_token")
        return True

    if (
        not await auth_service.is_initialized()
        and not settings.ADMIN_API_KEY
        and settings.ALLOW_UNAUTH_SETUP_FROM_LOCALHOST
        and request.client is not None
        and request.client.host in ("127.0.0.1", "::1")
    ):
        return True

    raise HTTPException(status_code=401, detail="未授权")


async def authenticate_ws(websocket) -> Optional[str]:
    """校验 WebSocket 连接：Session Cookie 优先，其次 ?token= 携带的 API Token。

    返回鉴权方式（"session" / "api_token"），两者皆失败返回 None。
    """
    session_id = websocket.cookies.get(settings.SESSION_COOKIE_NAME)
    if await auth_service.validate_session_id(session_id):
        return "session"

    token = websocket.query_params.get("token")
    if token and await auth_service.validate_api_token(token):
        return "api_token"

    staff_session_id = websocket.cookies.get(settings.STAFF_SESSION_COOKIE_NAME)
    if staff_session_id:
        try:
            from main import employee_accounts
        except ImportError:
            employee_accounts = None
        if employee_accounts is not None:
            employee = await employee_accounts.get_staff_session(staff_session_id)
            if employee is not None:
                return "staff"

    return None


async def require_session(request: Request) -> str:
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not await auth_service.validate_session_id(session_id):
        raise HTTPException(status_code=401, detail="需要登录")
    return session_id
