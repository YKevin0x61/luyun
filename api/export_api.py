#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报表导出（CSV）"""

import csv
import io
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
import logging

from database import DatabaseManager, get_db

logger = logging.getLogger(__name__)
CHINA_TZ = timezone(timedelta(hours=8))

router = APIRouter(prefix="/api/export", tags=["导出"])


@router.get("/sales-report.csv")
async def export_sales_report_csv(
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    station: Optional[str] = Query(None),
    db: DatabaseManager = Depends(get_db),
):
    """导出销售报表 CSV（菜品 + 半成品用量）。"""
    try:
        report = await db.reports.compute_sales_report(start_date, end_date, station)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["类型", "名称", "档口/岗位", "数量", "金额", "单位", "子分类"])
        for dish in report.get("dish_sales", []):
            writer.writerow([
                "菜品",
                dish.get("dish_name", ""),
                dish.get("station", ""),
                dish.get("qty", 0),
                dish.get("total_amount", 0),
                "",
                "",
            ])
        for block in report.get("semi_finished", []):
            pos = block.get("position", "")
            for item in block.get("items", []):
                writer.writerow([
                    "半成品",
                    item.get("semi_name", ""),
                    pos,
                    item.get("qty", 0),
                    "",
                    item.get("unit", ""),
                    item.get("sub_category", "") or item.get("category", ""),
                ])
        buffer.seek(0)
        filename = f"sales_{start_date}_{end_date}.csv"
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        logger.error("导出 CSV 失败: %s", exc)
        raise HTTPException(status_code=500, detail="导出 CSV 失败")
