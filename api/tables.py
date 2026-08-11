#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""餐桌运营分析 API"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import logging

from database import DatabaseManager, get_db, ensure_beijing_datetime

logger = logging.getLogger(__name__)
CHINA_TZ = timezone(timedelta(hours=8))

router = APIRouter(prefix="/api/tables", tags=["餐桌分析"])


@router.get("/snapshot")
async def get_tables_snapshot(db: DatabaseManager = Depends(get_db)):
    """当前餐桌快照：占用率、在席人数与金额。"""
    try:
        data = await db.get_table_snapshot_stats()
        return {"success": True, **data}
    except Exception as exc:
        logger.error("获取餐桌快照失败: %s", exc)
        raise HTTPException(status_code=500, detail="获取餐桌快照失败")


@router.get("/live")
async def get_tables_live(db: DatabaseManager = Depends(get_db)):
    """当前占用桌台列表，供仪表盘桌台实况面板使用。"""
    try:
        data = await db.get_table_live_list()
        return {"success": True, **data}
    except Exception as exc:
        logger.error("获取桌台实况失败: %s", exc)
        raise HTTPException(status_code=500, detail="获取桌台实况失败")


@router.get("/operations")
async def get_table_operations(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD，默认今天"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD，默认今天"),
    db: DatabaseManager = Depends(get_db),
):
    """翻台与客单分析（基于 tables 快照历史字段聚合）。"""
    try:
        now = datetime.now(CHINA_TZ)
        start = start_date or now.strftime("%Y-%m-%d")
        end = end_date or start
        data = await db.reports.aggregate_table_operations(start, end)
        return {"success": True, **data}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("餐桌运营分析失败: %s", exc)
        raise HTTPException(status_code=500, detail="餐桌运营分析失败")
