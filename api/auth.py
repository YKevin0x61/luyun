#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门店共享账号登录、Session 与 API Token 管理。"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, field_validator, model_validator

from api.security import require_session, verify_admin_token
from config import settings
from services import auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_fail_attempts: Dict[str, List[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 5


def _password_error_detail(code: str) -> str:
    lowered = code.lower()
    if "72 bytes" in lowered or "truncate manually" in lowered:
        return "密码过长，请缩短后重试"
    if code == "password_too_short":
        return f"密码至少 {settings.AUTH_MIN_PASSWORD_LENGTH} 位"
    if code == "password_too_long":
        return f"密码过长（最多 {settings.AUTH_MAX_PASSWORD_BYTES} 字节）"
    if code == "passwords_do_not_match":
        return "两次输入的密码不一致"
    return code


class InitIn(BaseModel):
    username: str
    password: str
    confirm_password: str

    @model_validator(mode="after")
    def validate_passwords(self) -> "InitIn":
        if self.password != self.confirm_password:
            raise ValueError("passwords_do_not_match")
        try:
            auth_service.validate_password(self.password)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class LoginIn(BaseModel):
    username: str
    password: str
    remember: bool = False
    issue_api_token: bool = False


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        try:
            auth_service.validate_password(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return value


class TokenIn(BaseModel):
    label: str = ""


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    recent = [ts for ts in _fail_attempts[ip] if now - ts < _RATE_LIMIT_WINDOW]
    _fail_attempts[ip] = recent
    if len(recent) >= _RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")


def _record_failure(ip: str) -> None:
    _fail_attempts[ip].append(time.time())


def _clear_failures(ip: str) -> None:
    _fail_attempts.pop(ip, None)


def _set_session_cookie(response: Response, session_id: str, remember: bool) -> None:
    max_age = (
        settings.SESSION_REMEMBER_DAYS * 86400
        if remember
        else settings.SESSION_TTL_HOURS * 3600
    )
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=max_age,
        secure=not settings.DEBUG,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")


@router.get("/status")
async def auth_status(request: Request) -> Dict[str, Any]:
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    logged_in = await auth_service.validate_session_id(session_id)
    username = await auth_service.get_admin_username() if logged_in else None
    return {
        "initialized": await auth_service.is_initialized(),
        "logged_in": logged_in,
        "username": username,
    }


@router.post("/init")
async def auth_init(body: InitIn, request: Request, response: Response) -> Dict[str, bool]:
    ip = _client_ip(request)
    _check_rate_limit(ip)

    if await auth_service.is_initialized():
        _record_failure(ip)
        raise HTTPException(status_code=409, detail="系统已初始化")

    try:
        await auth_service.init_user(body.username, body.password)
    except ValueError as exc:
        _record_failure(ip)
        code = str(exc)
        if code == "already_initialized":
            raise HTTPException(status_code=409, detail="系统已初始化") from exc
        raise HTTPException(status_code=400, detail=_password_error_detail(code)) from exc
    except Exception as exc:
        _record_failure(ip)
        logger.exception("Auth init failed")
        raise HTTPException(
            status_code=400,
            detail=_password_error_detail(str(exc)),
        ) from exc

    session_id, _expires_at = await auth_service.create_session(remember=False)
    _set_session_cookie(response, session_id, remember=False)
    _clear_failures(ip)
    logger.info("Auth initialized via API from ip=%s user=%s", ip, body.username.strip())
    return {"success": True}


@router.post("/login")
async def auth_login(body: LoginIn, request: Request, response: Response) -> Dict[str, Any]:
    ip = _client_ip(request)
    _check_rate_limit(ip)

    user = await auth_service.authenticate(body.username, body.password)
    if not user:
        _record_failure(ip)
        logger.warning("Login failed from ip=%s username=%s", ip, body.username.strip())
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    session_id, _expires_at = await auth_service.create_session(remember=body.remember)
    _set_session_cookie(response, session_id, remember=body.remember)
    _clear_failures(ip)
    logger.info("Login success from ip=%s user=%s", ip, user["username"])

    result: Dict[str, Any] = {"success": True}
    if body.issue_api_token:
        plain, _meta = await auth_service.issue_api_token(label="login")
        result["api_token"] = plain
    return result


@router.post("/logout")
async def auth_logout(
    response: Response,
    session_id: str = Depends(require_session),
) -> Dict[str, bool]:
    await auth_service.delete_session(session_id)
    _clear_session_cookie(response)
    return {"success": True}


@router.post("/change-password")
async def auth_change_password(
    body: ChangePasswordIn,
    _session_id: str = Depends(require_session),
) -> Dict[str, bool]:
    try:
        await auth_service.change_password(body.old_password, body.new_password)
    except ValueError as exc:
        code = str(exc)
        if code == "invalid_password":
            raise HTTPException(status_code=401, detail="旧密码错误") from exc
        raise HTTPException(status_code=400, detail=_password_error_detail(code)) from exc
    logger.info("Password changed via API")
    return {"success": True}


@router.post("/token")
async def auth_create_token(
    body: TokenIn,
    _session_id: str = Depends(require_session),
) -> Dict[str, Any]:
    plain, meta = await auth_service.issue_api_token(label=body.label)
    logger.info("API token issued label=%s", body.label or "")
    return {"success": True, "api_token": plain, **meta}


@router.get("/tokens")
async def auth_list_tokens(
    _session_id: str = Depends(require_session),
) -> Dict[str, List[Dict[str, Any]]]:
    tokens = await auth_service.list_api_tokens()
    safe_tokens = []
    for token in tokens:
        safe_tokens.append({
            "token_hash_prefix": token["token_hash"][:8],
            "label": token.get("label") or "",
            "expires_at": token.get("expires_at"),
            "created_at": token.get("created_at"),
            "revoked_at": token.get("revoked_at"),
        })
    return {"tokens": safe_tokens}


@router.delete("/token/{token_hash_prefix}")
async def auth_revoke_token(
    token_hash_prefix: str,
    _session_id: str = Depends(require_session),
) -> Dict[str, bool]:
    tokens = await auth_service.list_api_tokens()
    matches = [
        token
        for token in tokens
        if token["token_hash"].startswith(token_hash_prefix)
        and not token.get("revoked_at")
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="Token 不存在")
    if len(matches) > 1:
        raise HTTPException(status_code=400, detail="前缀不唯一，请提供更长的 hash 前缀")
    revoked = await auth_service.revoke_api_token(matches[0]["token_hash"])
    if not revoked:
        raise HTTPException(status_code=404, detail="Token 不存在")
    logger.info("API token revoked prefix=%s", token_hash_prefix)
    return {"success": True}


@router.get("/verify", dependencies=[Depends(verify_admin_token)])
async def verify_token() -> Dict[str, bool]:
    return {"ok": True}
