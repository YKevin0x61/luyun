#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报表导出（CSV）"""

import csv
import io
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
import logging

from database import DatabaseManager, get_db

logger = logging.getLogger(__name__)
CHINA_TZ = timezone(timedelta(hours=8))

router = APIRouter(prefix="/api/export", tags=["导出"])

# SEC-09：日期参数先按形状挡（不合法 → 422），再按日历挡（2026-02-30 → 400）。
# 以前参数只是裸 str，任何字符串都直接进报表层，非法输入最终变成
# 500「导出 CSV 失败」——用户看到"系统坏了"，而不是"参数写错了"。
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
# 档口名是自由文本（配置里写什么就是什么），只封长度上限，不做字符白名单：
# 值始终以绑定参数进 SQL，不参与拼接。
_STATION_MAX_LENGTH = 64


def _require_valid_date(value: str, name: str) -> None:
    """形状已由 Query(pattern=...) 挡过；这里挡"形状对但日历上不存在"的日期。"""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"参数 {name} 不是合法日期（YYYY-MM-DD）：{value!r}",
        )


@router.get("/sales-report.csv")
async def export_sales_report_csv(
    start_date: str = Query(..., pattern=_DATE_PATTERN, description="YYYY-MM-DD"),
    end_date: str = Query(..., pattern=_DATE_PATTERN, description="YYYY-MM-DD"),
    station: Optional[str] = Query(None, max_length=_STATION_MAX_LENGTH),
    db: DatabaseManager = Depends(get_db),
):
    """导出销售报表 CSV（菜品 + 半成品用量）。"""
    _require_valid_date(start_date, "start_date")
    _require_valid_date(end_date, "end_date")
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
        # 文件名要进响应头：即便参数已校验过，也按 RFC 3986 转义一次，
        # 免得将来放宽参数校验就把引号/换行带进 Content-Disposition（SEC-09）。
        disposition = f'attachment; filename="{quote(filename)}"'
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv; charset=utf-8",
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
