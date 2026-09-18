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

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
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


async def _restart_after_response() -> None:
    """响应发出去之后再重启。

    原先重启同步跑在请求处理里：容器一重启，反代立刻返回 502，浏览器拿到的是
    openresty 的 HTML 错误页 —— 用户既看不到结果，也分不清重置到底成没成。
    放到 BackgroundTasks 里（Starlette 在响应发送后才执行）就没有这个窗口。
    """
    from services.release_update.job_adapters import build_main_service_adapter

    await asyncio.sleep(1.0)  # 给响应留出 flush 时间
    try:
        await asyncio.to_thread(build_main_service_adapter().restart)
    except Exception as exc:  # noqa: BLE001 - 密码已写好，重启失败只需人工补一次
        logger.error("数据库密码已更新，但自动重启失败，请手动重启应用: %s", exc)


@router.get("")
async def get_db_credentials() -> dict:
    return credentials_status()


@router.post("/reset")
async def reset_db_credentials(payload: ResetRequest, background: BackgroundTasks) -> dict:
    username = await auth_service.get_admin_username()
    if not username:
        raise HTTPException(status_code=400, detail="后台尚未初始化，无法二次确认")
    if not await auth_service.authenticate(username, payload.confirm_password):
        logger.warning("数据库密码重置被拒绝：二次确认失败")
        raise HTTPException(status_code=403, detail="后台密码不正确，已拒绝本次重置")
    try:
        result = await reset_database_password(actor=username, restart=False)
    except PasswordResetError as exc:
        logger.error("数据库密码重置失败 code=%s: %s", exc.code, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background.add_task(_restart_after_response)
    result["restart_scheduled"] = True
    return result
