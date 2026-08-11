#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""经营分析：趋势、退菜等"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import logging

from database import DatabaseManager, get_db

logger = logging.getLogger(__name__)
CHINA_TZ = timezone(timedelta(hours=8))

router = APIRouter(prefix="/api/analytics", tags=["经营分析"])


@router.get("/sales-trend")
async def sales_trend(
    granularity: str = Query("day", description="hour | day | week | month"),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    station: Optional[str] = Query(None),
    db: DatabaseManager = Depends(get_db),
):
    """按小时/日/周/月聚合营业额与订单量趋势。"""
    try:
        if granularity not in ("hour", "day", "week", "month"):
            raise HTTPException(status_code=400, detail="granularity 须为 hour/day/week/month")
        now = datetime.now(CHINA_TZ)
        end = end_date or now.strftime("%Y-%m-%d")
        if not start_date:
            if granularity == "month":
                start = (now.replace(day=1) - timedelta(days=180)).strftime("%Y-%m-%d")
            elif granularity == "week":
                start = (now - timedelta(days=56)).strftime("%Y-%m-%d")
            else:
                start = (now - timedelta(days=13)).strftime("%Y-%m-%d")
        else:
            start = start_date
        series = await db.reports.aggregate_sales_trend(start, end, granularity, station)
        return {
            "success": True,
            "granularity": granularity,
            "date_range": {"start": start, "end": end},
            "series": series,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("销售趋势失败: %s", exc)
        raise HTTPException(status_code=500, detail="销售趋势查询失败")


@router.get("/refunds")
async def refund_stats(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    station: Optional[str] = Query(None),
    db: DatabaseManager = Depends(get_db),
):
    """退菜/负数量订单统计。"""
    try:
        now = datetime.now(CHINA_TZ)
        end = end_date or now.strftime("%Y-%m-%d")
        start = start_date or end
        data = await db.reports.aggregate_refund_stats(start, end, station)
        return {"success": True, **data}
    except Exception as exc:
        logger.error("退菜统计失败: %s", exc)
        raise HTTPException(status_code=500, detail="退菜统计失败")
