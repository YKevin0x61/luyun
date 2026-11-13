#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""经营分析：趋势、退菜等"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import logging

from database import DatabaseManager, get_db
from api.date_params import DATE_PATTERN, require_valid_date

logger = logging.getLogger(__name__)
CHINA_TZ = timezone(timedelta(hours=8))

router = APIRouter(prefix="/api/analytics", tags=["经营分析"])


@router.get("/sales-trend")
async def sales_trend(
    granularity: str = Query("day", description="hour | day | week | month"),
    start_date: Optional[str] = Query(None, pattern=DATE_PATTERN, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, pattern=DATE_PATTERN, description="YYYY-MM-DD"),
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
        require_valid_date(start, "start_date")
        require_valid_date(end, "end_date")
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
    start_date: Optional[str] = Query(None, pattern=DATE_PATTERN),
    end_date: Optional[str] = Query(None, pattern=DATE_PATTERN),
    station: Optional[str] = Query(None),
    db: DatabaseManager = Depends(get_db),
):
    """退菜/负数量订单统计。"""
    try:
        now = datetime.now(CHINA_TZ)
        end = end_date or now.strftime("%Y-%m-%d")
        start = start_date or end
        require_valid_date(start, "start_date")
        require_valid_date(end, "end_date")
        data = await db.reports.aggregate_refund_stats(start, end, station)
        return {"success": True, **data}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("退菜统计失败: %s", exc)
        raise HTTPException(status_code=500, detail="退菜统计失败")


@router.get("/refunds/detail")
async def refund_details(
    start_date: Optional[str] = Query(None, pattern=DATE_PATTERN, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, pattern=DATE_PATTERN, description="YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    kind: Optional[str] = Query(None, description="co=退单 present=赠单，空=两类都要"),
    db: DatabaseManager = Depends(get_db),
):
    """退单 / 赠单明细（商龙云「异常汇总」）。

    与 ``/refunds`` 是两套口径：那边按 ``orders`` 里数量为负或状态含「退」的行
    汇总（爬虫差分推断，份数在软取消时归零）；这里读 ``refund_records``，是
    cy7mm 接口落下来的一手明细，每行都带份数、原因、操作人。``kind`` 区分
    退单（``co``）与赠单（``present``）。

    没有 ``station`` 参数：接口只返回桌台名（``point_name``），拿不到档口。
    两类汇总一起返回，页面顶部不用再发一次请求。
    """
    if kind and kind not in ("co", "present"):
        raise HTTPException(status_code=400, detail="kind 只能是 co 或 present")
    try:
        now = datetime.now(CHINA_TZ)
        end = end_date or now.strftime("%Y-%m-%d")
        start = start_date or end
        require_valid_date(start, "start_date")
        require_valid_date(end, "end_date")
        detail = await db.refund_records_detail(
            start_date=start, end_date=end, page=page, page_size=page_size, kind=kind
        )
        summaries = {
            each: await db.refund_records_summary(start_date=start, end_date=end, kind=each)
            for each in ("co", "present")
        }
        return {
            "success": True,
            "start_date": start,
            "end_date": end,
            "kind": kind or "",
            **detail,
            "summary": summaries,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("退单/赠单明细查询失败: %s", exc)
        raise HTTPException(status_code=500, detail="退单/赠单明细查询失败")
