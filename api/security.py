#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理接口鉴权（Session Cookie、API Token、过渡期 ADMIN_API_KEY）。"""

import logging
from typing import Optional

from fastapi import Header, HTTPException, Request

from config import settings
from services import auth_service

logger = logging.getLogger(__name__)
_warned_open_admin = False


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

    return None


async def require_session(request: Request) -> str:
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not await auth_service.validate_session_id(session_id):
        raise HTTPException(status_code=401, detail="需要登录")
    return session_id
