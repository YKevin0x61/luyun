#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Admin 的「数据库迁移」面板后端：看待应用清单、点一下应用。

SQLite 部署不需要它（启动时自愈），PG 部署用它替掉「升级后记得敲 psql」这件
靠人记住的事。判断与执行都在 services/db_migrations.py 里。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.security import require_session
from database import DatabaseManager, get_db
from services.db_migrations import apply_pending_migrations, migration_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/db-migrations", tags=["db-migrations"])


class ApplyMigrationsIn(BaseModel):
    """只应用指定版本；不传就全部待应用的按序应用。"""

    only: Optional[list] = None


@router.get("")
async def list_migrations(
    _session_id: str = Depends(require_session),
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    status = await migration_status(db)
    return {"success": True, **status.as_dict()}


@router.post("/apply")
async def apply_migrations(
    payload: Optional[ApplyMigrationsIn] = None,
    _session_id: str = Depends(require_session),
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = await apply_pending_migrations(
            db,
            only=payload.only if payload else None,
        )
    except Exception as exc:
        logger.error("应用数据库迁移失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"应用迁移失败：{exc}") from exc
    if not result.ok:
        # 失败的那一条已经回滚、后续不再继续。detail 给可读字符串：前端的 api client
        # 对对象型 detail 只会退化成「请求失败 (409)」。已成功的部分让界面重新拉状态。
        failed = result.failed[0] if result.failed else {}
        detail = (
            f"迁移 {failed.get('filename', '')} 执行失败并已回滚："
            f"{failed.get('error', '未知错误')}"
        )
        if len(result.failed) > 1:
            detail += f"（另有 {len(result.failed) - 1} 条未执行）"
        raise HTTPException(status_code=409, detail=detail)
    return {"success": True, **result.as_dict()}
