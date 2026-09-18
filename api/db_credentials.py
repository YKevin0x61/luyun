#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后台「数据库凭据」：查看 PostgreSQL 连接信息、重置业务角色密码。

安全约定：
- 与后台其它管理接口同一鉴权（``verify_admin_token``）；
- 重置必须二次确认当前后台管理员密码；
- 响应里**不含**密码明文——新密码只落 ``deploy/env.production``；
- 审计日志只记谁、改了哪个角色，不记密码。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.security import verify_admin_token
from services import auth_service
from services.pg_credentials import (
    PasswordResetError,
    credentials_status,
    reset_database_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/db-credentials",
    tags=["admin"],
    dependencies=[Depends(verify_admin_token)],
)


class ResetRequest(BaseModel):
    # 二次确认：避免会话被顺手利用就在生产库上换掉凭据
    confirm_password: str = Field(..., min_length=1, max_length=1024)


@router.get("")
async def get_db_credentials() -> dict:
    return credentials_status()


@router.post("/reset")
async def reset_db_credentials(payload: ResetRequest) -> dict:
    username = await auth_service.get_admin_username()
    if not username:
        raise HTTPException(status_code=400, detail="后台尚未初始化，无法二次确认")
    if not await auth_service.authenticate(username, payload.confirm_password):
        logger.warning("数据库密码重置被拒绝：二次确认失败")
        raise HTTPException(status_code=403, detail="后台密码不正确，已拒绝本次重置")
    try:
        return await reset_database_password(actor=username)
    except PasswordResetError as exc:
        logger.error("数据库密码重置失败 code=%s: %s", exc.code, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
