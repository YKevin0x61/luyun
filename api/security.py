#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理接口鉴权（Session Cookie、API Token、过渡期 ADMIN_API_KEY）。"""

import logging
from dataclasses import dataclass
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


@dataclass(frozen=True)
class WsIdentity:
    """一条 WebSocket 连接的身份：鉴权方式 +（员工会话才有的）员工 id。"""

    auth: str
    employee_id: Optional[int] = None


# 连接可以用 `?identity=` 显式声明自己带的是哪套凭据。取值只有这两个——声明
# **只决定拿哪份凭据去验**，绝不放宽校验本身：声明 staff 时管理端 cookie / token
# 一律不看，没有有效员工会话就拒绝连接（fail-closed），反之亦然。认不出的值也拒绝，
# 不静默退回旧优先级——那会让拼错的声明悄悄拿到管理端身份。
WS_IDENTITY_STAFF = "staff"
WS_IDENTITY_ADMIN = "admin"
_WS_IDENTITY_PARAM = "identity"


async def _admin_ws_identity(websocket) -> Optional[WsIdentity]:
    """管理端凭据：Session Cookie 优先，其次 `?token=` 的 API Token。"""
    session_id = websocket.cookies.get(settings.SESSION_COOKIE_NAME)
    if await auth_service.validate_session_id(session_id):
        return WsIdentity(auth="session")

    token = websocket.query_params.get("token")
    if token and await auth_service.validate_api_token(token):
        return WsIdentity(auth="api_token")

    return None


async def _staff_ws_identity(websocket) -> Optional[WsIdentity]:
    """员工（卫生/排班手机端）Session Cookie。认不出返回 None。"""
    staff_session_id = websocket.cookies.get(settings.STAFF_SESSION_COOKIE_NAME)
    if not staff_session_id:
        return None
    try:
        from main import employee_accounts
    except ImportError:
        employee_accounts = None
    if employee_accounts is None:
        return None
    employee = await employee_accounts.get_staff_session(staff_session_id)
    if employee is None:
        return None
    # 取不到 id 也照样算 staff（fail-closed：hub 侧对没有 employee_id 的
    # 员工连接不派发任何 scope 带 employee_id 的 nudge，只给全店级事件）。
    return WsIdentity(auth="staff", employee_id=employee.get("id"))


async def identify_ws(websocket) -> Optional[WsIdentity]:
    """校验 WebSocket 连接并解析出「这条连接是谁」：鉴权方式 + 员工 id。

    优先级：**显式声明 > Session Cookie > `?token=` API Token > 员工 Session Cookie**。

    显式声明（`?identity=staff|admin`）是「这台浏览器同时持两套 cookie 时用哪一份」的
    答案：员工端页面的连接声明自己是员工，于是不会被管理端 cookie 顶掉（那会让
    `allowed_topics` 全开、`_staff_owns_scope` 失效）。声明**只选凭据、不给身份**——
    声明的那一支验不过就拒绝连接，不回落到另一支。不带声明时优先级一个字不变。

    员工那一支必须把 employee_id 一起带出来：实时 hub 靠它做归属隔离（只给本人派发
    scope 里带 employee_id 的 nudge），而身份只能在这里——连接建立时、cookie 验过
    之后——定下来，绝不能听客户端在 subscribe 里自报。

    认不出来返回 None（端点据此 4401 关闭）。
    """
    declared = websocket.query_params.get(_WS_IDENTITY_PARAM)
    if declared is not None:
        declared = declared.strip().lower()
        if declared == WS_IDENTITY_STAFF:
            return await _staff_ws_identity(websocket)
        if declared == WS_IDENTITY_ADMIN:
            return await _admin_ws_identity(websocket)
        return None

    identity = await _admin_ws_identity(websocket)
    if identity is not None:
        return identity
    return await _staff_ws_identity(websocket)


async def authenticate_ws(websocket) -> Optional[str]:
    """校验 WebSocket 连接：显式声明（`?identity=`）优先，其余按 Session Cookie →
    `?token=` API Token → 员工 Session Cookie 的优先级（见 `identify_ws`）。

    返回鉴权方式（"session" / "api_token" / "staff"），两者皆失败返回 None。

    只关心鉴权方式时用它；需要员工的 employee_id 做归属隔离时用 `identify_ws`
    ——两者解析路径是同一份代码，不会各查一次库、更不会给出不一致的身份。
    """
    identity = await identify_ws(websocket)
    return None if identity is None else identity.auth


async def require_session(request: Request) -> str:
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not await auth_service.validate_session_id(session_id):
        raise HTTPException(status_code=401, detail="需要登录")
    return session_id


def _staff_accounts():
    """员工账号服务：`main` 启动时装配（测试里也会替换它）。

    真正会撞上「还没装配好」的是**服务刚起的那几秒**：那时候 `main.employee_accounts`
    还是 None，员工手机上第一条请求就落在这一支。这里给 503（稍后再试）而不是 500 ——
    这不是代码错。`ImportError` 那一支才是「没经由 main 起」的场景（命令行脚本）。
    """
    try:
        from main import employee_accounts
    except ImportError:  # pragma: no cover - 只在不经由 main 起的脚本里发生
        employee_accounts = None
    if employee_accounts is None:
        raise HTTPException(status_code=503, detail="员工账号服务未就绪（服务正在启动）")
    return employee_accounts


async def require_staff_session(request: Request) -> dict:
    """员工会话（手机端那个 cookie），卫生与排班两个 HTTP 面共用。

    身份本身在公共层（`services.identity.accounts`）：排班不该 import 卫生的路由模块
    去蹭它那个同名依赖 —— `tests/test_scheduling.py` 的分层测试盯的就是这条边界。
    """
    accounts = _staff_accounts()
    employee = await accounts.get_staff_session(
        request.cookies.get(settings.STAFF_SESSION_COOKIE_NAME)
    )
    if employee is None:
        raise HTTPException(status_code=401, detail="需要员工登录")
    return employee
