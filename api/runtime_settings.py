#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爬虫运行配置 API 路由。

暴露营业时段 / 轮询间隔 / 浏览器选项的读取与保存。保存后会立即通知
运行中的 scraper 热重载，无需重启服务。敏感凭据不在此处理。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import DatabaseManager, get_db
from api.security import verify_admin_token
from services import runtime_settings

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/runtime-settings",
    tags=["runtime-settings"],
    dependencies=[Depends(verify_admin_token)],
)


class RuntimeSettingsIn(BaseModel):
    work_start: str = Field(..., description="营业开始时间 HH:MM")
    work_end: str = Field(..., description="营业结束时间 HH:MM")
    interval_min: int = Field(..., description="轮询间隔下限（秒）")
    interval_max: int = Field(..., description="轮询间隔上限（秒）")
    headless: bool = Field(True, description="浏览器无头模式")
    retry_count: int = Field(3, description="失败重试次数")
    timeout_ms: int = Field(30000, description="页面/接口超时（毫秒）")
    delivery_cancel_miss_threshold: int = Field(
        3, description="外卖取消判定次数：连续缺席多少次判为取消"
    )


@router.get("")
async def get_runtime_settings(db: DatabaseManager = Depends(get_db)) -> dict:
    """读取当前运行配置（含默认值补齐）与其最后更新时间。"""
    data = await runtime_settings.load_runtime_settings(db)
    updated_at = await db.settings_updated_at(runtime_settings.RUNTIME_SETTINGS_KEY)
    return {
        "success": True,
        "settings": data,
        "defaults": runtime_settings.DEFAULT_RUNTIME_SETTINGS,
        "updated_at": updated_at,
    }


@router.put("")
async def update_runtime_settings(
    payload: RuntimeSettingsIn,
    db: DatabaseManager = Depends(get_db),
) -> dict:
    """校验并保存运行配置，随后通知 scraper 热重载。"""
    try:
        validated = await runtime_settings.save_runtime_settings(db, payload.dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("保存运行配置失败: %s", exc)
        raise HTTPException(status_code=500, detail="保存运行配置失败")

    await _notify_scraper_reload_runtime(db)
    logger.info(
        "🛠️ 运行配置已更新：营业 %s-%s，轮询 %s~%ss",
        validated["work_start"],
        validated["work_end"],
        validated["interval_min"],
        validated["interval_max"],
    )
    return {"success": True, "settings": validated}


async def _notify_scraper_reload_runtime(db: DatabaseManager) -> None:
    """让运行中的 scraper 立即重新读取运行配置。"""
    try:
        from main import restaurant_scraper

        if restaurant_scraper and hasattr(restaurant_scraper, "reload_runtime_settings"):
            await restaurant_scraper.reload_runtime_settings(db)
    except Exception as exc:
        logger.warning("通知 scraper 重新加载运行配置失败: %s", exc)
