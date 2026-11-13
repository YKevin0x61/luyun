#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报表导出（CSV）"""

import csv
import io
from datetime import timezone, timedelta
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
import logging

from api.date_params import DATE_PATTERN, require_valid_date
from database import DatabaseManager, get_db

logger = logging.getLogger(__name__)
CHINA_TZ = timezone(timedelta(hours=8))

router = APIRouter(prefix="/api/export", tags=["导出"])

# 档口名是自由文本（配置里写什么就是什么），只封长度上限，不做字符白名单：
# 值始终以绑定参数进 SQL，不参与拼接。
_STATION_MAX_LENGTH = 64


def _csv_cell(value: Any) -> str:
    """公式注入防护：以 = + - @ 开头的文本在 Excel 里会被当成公式执行（审查 R42/L1）。"""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@router.get("/sales-report.csv")
async def export_sales_report_csv(
    start_date: str = Query(..., pattern=DATE_PATTERN, description="YYYY-MM-DD"),
    end_date: str = Query(..., pattern=DATE_PATTERN, description="YYYY-MM-DD"),
    station: Optional[str] = Query(None, max_length=_STATION_MAX_LENGTH),
    db: DatabaseManager = Depends(get_db),
):
    """导出销售报表 CSV（菜品 + 半成品用量）。"""
    require_valid_date(start_date, "start_date")
    require_valid_date(end_date, "end_date")
    try:
        report = await db.reports.compute_sales_report(start_date, end_date, station)
        buffer = io.StringIO()
        # Excel 打开 UTF-8 CSV 需要 BOM，否则中文列名整行乱码（审查 M4）。
        buffer.write("\ufeff")
        writer = csv.writer(buffer)
        writer.writerow(["类型", "名称", "档口/岗位", "数量", "金额", "单位", "子分类"])
        for dish in report.get("dish_sales", []):
            writer.writerow([
                "菜品",
                _csv_cell(dish.get("dish_name", "")),
                _csv_cell(dish.get("station", "")),
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
                    _csv_cell(item.get("semi_name", "")),
                    _csv_cell(pos),
                    item.get("qty", 0),
                    "",
                    _csv_cell(item.get("unit", "")),
                    _csv_cell(item.get("sub_category", "") or item.get("category", "")),
                ])
        buffer.seek(0)
        filename = f"sales_{start_date}_{end_date}.csv"
        # 文件名要进响应头：即便参数已校验过，也按 RFC 3986 转义一次，
        # 免得将来放宽参数校验就把引号/换行带进 Content-Disposition（SEC-09）。
        disposition = f'attachment; filename="{quote(filename)}"'
        return StreamingResponse(
            iter([buffer.getvalue()]),
            # 只给 text/csv，让 Starlette 自己补一次 charset；以前显式写 charset 会和它拼成
            # `text/csv; charset=utf-8; charset=utf-8`（审查 N1）。
            media_type="text/csv",
            headers={"Content-Disposition": disposition},
        )
    except HTTPException:
        raise
    except ValueError as exc:
        logger.error("导出参数不合法: %s", exc)
        raise HTTPException(status_code=400, detail=f"导出参数不合法: {exc}")
    except Exception as exc:
        logger.error("导出 CSV 失败: %s", exc)
        raise HTTPException(status_code=500, detail="导出 CSV 失败")
