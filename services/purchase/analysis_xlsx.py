#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购分析导出：概览、近 7 日、异常各占一张表。

只做「把已经算好的字典铺成表格」，不查库、不判权限；数据口径全在
services/purchase/service.py，避免导出与页面两套算法。
"""

from __future__ import annotations

import io
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="F0EDE4")
HEADER_FONT = Font(bold=True)
TITLE_FONT = Font(bold=True, size=14)

ANOMALY_SHEETS = (
    ("no_order", "连续没下单"),
    ("over", "采购超量"),
    ("under", "采购偏低"),
    ("zero_stock", "零库存"),
)


def _header(sheet, row: int, labels: Iterable[str], widths: Iterable[int]) -> None:
    for index, (label, width) in enumerate(zip(labels, widths), start=1):
        cell = sheet.cell(row=row, column=index, value=label)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
        sheet.column_dimensions[get_column_letter(index)].width = width


def _write_overview(sheet, overview: dict) -> None:
    sheet["A1"] = "厨房采购分析"
    sheet["A1"].font = TITLE_FONT
    sheet["A2"] = "日期"
    sheet["B2"] = overview.get("date") or ""
    stats = (
        ("采购单", overview.get("order_count")),
        ("采购量", overview.get("item_count")),
        ("商品数", overview.get("sku_count")),
        ("下单人", overview.get("submitter_count")),
    )
    for offset, (label, value) in enumerate(stats):
        sheet.cell(row=3 + offset, column=1, value=label).font = HEADER_FONT
        sheet.cell(row=3 + offset, column=2, value=value)
    start_row = 9
    sheet.cell(row=start_row, column=1, value="近 7 日采购量").font = HEADER_FONT
    _header(sheet, start_row + 1, ("日期", "采购单", "采购量"), (14, 10, 12))
    for index, point in enumerate(overview.get("trend") or [], start=start_row + 2):
        sheet.cell(row=index, column=1, value=point.get("date"))
        sheet.cell(row=index, column=2, value=point.get("order_count"))
        sheet.cell(row=index, column=3, value=point.get("quantity"))


def _write_anomalies(sheet, rows: list, kind: str) -> None:
    labels = ["严重度", "商品编码", "商品", "单位"]
    widths = [10, 14, 24, 8]
    if kind == "no_order":
        labels += ["最近下单", "间隔天数"]
        widths += [14, 12]
    elif kind in ("over", "under"):
        labels += ["当天采购", "参考值", "倍数"]
        widths += [12, 12, 10]
    else:
        labels += ["盘点日期", "盘点人"]
        widths += [14, 16]
    _header(sheet, 1, labels, widths)
    for row_index, item in enumerate(rows, start=2):
        values = [
            item.get("severity"),
            item.get("product_code"),
            item.get("name"),
            item.get("unit"),
        ]
        if kind == "no_order":
            values += [item.get("last_ordered"), item.get("days")]
        elif kind in ("over", "under"):
            values += [item.get("quantity"), item.get("reference"), item.get("ratio")]
        else:
            values += [item.get("count_date"), item.get("counted_by")]
        for column, value in enumerate(values, start=1):
            sheet.cell(row=row_index, column=column, value=value)
    if not rows:
        sheet.cell(row=2, column=1, value="当天没有这一类异常")


def build_analysis_workbook(overview: dict, anomalies: dict) -> bytes:
    """把两个分析接口的返回铺成三张表：概览 / 近 7 日 / 异常明细。"""
    workbook = Workbook()
    overview_sheet = workbook.active
    overview_sheet.title = "概览"
    _write_overview(overview_sheet, overview or {})

    trend_sheet = workbook.create_sheet("近7日")
    _header(trend_sheet, 1, ("日期", "采购单", "采购量"), (14, 10, 12))
    for index, point in enumerate((overview or {}).get("trend") or [], start=2):
        trend_sheet.cell(row=index, column=1, value=point.get("date"))
        trend_sheet.cell(row=index, column=2, value=point.get("order_count"))
        trend_sheet.cell(row=index, column=3, value=point.get("quantity"))

    anomaly_sheet = workbook.create_sheet("异常")
    row_cursor = 1
    for kind, label in ANOMALY_SHEETS:
        anomaly_sheet.cell(row=row_cursor, column=1, value=label).font = HEADER_FONT
        items = (anomalies or {}).get(kind) or []
        sub = workbook.create_sheet(label[:31])
        _write_anomalies(sub, items, kind)
        anomaly_sheet.cell(row=row_cursor, column=2, value=len(items))
        row_cursor += 1

    options = (anomalies or {}).get("parameters") or {}
    anomaly_sheet.cell(row=row_cursor + 1, column=1, value="参数").font = HEADER_FONT
    for offset, (key, value) in enumerate(options.items(), start=1):
        anomaly_sheet.cell(row=row_cursor + 1 + offset, column=1, value=key)
        anomaly_sheet.cell(row=row_cursor + 1 + offset, column=2, value=value)

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def analysis_filename(day: str) -> str:
    return "purchase-analysis-" + str(day or "") + ".xlsx"
