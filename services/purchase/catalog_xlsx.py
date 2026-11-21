#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购目录的 Excel 读写（商品与供应商页的导入 / 导出 / 模板）。

表头是中文、按列名解析，不依赖列顺序；导入只认目录字段，供应商编码 / 分类编码
用来把行挂到已有目录上，不做「导入时顺手新建供应商」——目录的父子关系由人先确认，
否则一张表就能凭错别字造出一堆重复供应商。

解析失败的行不抛异常，而是带回 row + message，由服务层逐行处理并汇报；结构性错误
（不是 xlsx、没有表头、缺必需列）才抛 ValueError，由 API 翻成 400。
"""

from __future__ import annotations

import io
from datetime import date
from typing import Any, Iterable, Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SHEET_TITLE = "商品目录"
TEMPLATE_HELP_SHEET = "填写说明"

# (字段名, 表头, 列宽)；顺序就是导出顺序，导入按表头名字认列。
COLUMNS = (
    ("supplier_code", "供应商编码", 10),
    ("supplier_name", "供应商", 18),
    ("category_code", "分类编码", 10),
    ("category_name", "分类", 16),
    ("product_code", "商品编码", 12),
    ("name", "商品名称", 20),
    ("specification", "规格", 16),
    ("unit", "单位", 8),
    ("positions", "岗位", 18),
    ("order_step", "下单步长", 10),
    ("safety_factor", "安全系数", 10),
    ("need_stock_check", "需盘点", 8),
    ("is_long_cycle", "长周期", 8),
    ("active", "启用", 8),
    ("remark", "备注", 24),
)

HEADER_LABELS = {field: label for field, label, _ in COLUMNS}
REQUIRED_HEADERS = ("供应商", "分类", "商品名称", "岗位")
_HEADER_ALIASES = {
    "供应商名称": "供应商",
    "分类名称": "分类",
    # 小程序 exportProducts 的 18 列表头是「商品ID」（= 2+2+3 编码），没有
    # 「商品编码」；认这个别名，老表才能按编码更新而不是新建一份重名商品。
    "商品ID": "商品编码",
    "商品": "商品名称",
    "采购岗位": "岗位",
    "步长": "下单步长",
    "系数": "安全系数",
    "是否盘点": "需盘点",
    "是否长周期": "长周期",
    "是否启用": "启用",
    "备注说明": "备注",
}

_BOOL_TRUE = {"是", "1", "true", "yes", "y", "需要", "启用"}
_BOOL_FALSE = {"否", "0", "false", "no", "n", "不需要", "停用", ""}

_HEADER_FILL = PatternFill("solid", fgColor="F0EDE4")
_HEADER_FONT = Font(bold=True)
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center")


def _norm_header(value: Any) -> str:
    text = str(value or "").replace("\u3000", " ").strip()
    text = "".join(text.split())
    return _HEADER_ALIASES.get(text, text)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        # 编码列被 Excel 存成数字时别带小数点；文本列的数字也按整数/小数正常转。
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return str(value).strip()


def _code(value: Any, width: int) -> str:
    text = _text(value)
    if not text:
        return ""
    if text.isdigit() and len(text) < width:
        return text.zfill(width)
    return text


def _number(value: Any, label: str) -> Optional[float]:
    text = _text(value).replace(",", "").replace("，", "")
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是数字") from exc
    if number <= 0:
        raise ValueError(f"{label}必须大于 0")
    return number


def _bool_value(value: Any, label: str) -> Optional[bool]:
    if value is None:
        return None
    text = _text(value).lower()
    if text in _BOOL_TRUE:
        return True
    if text in _BOOL_FALSE:
        return False
    raise ValueError(f"{label}只能填是 / 否")


def _split_positions(value: Any) -> list[str]:
    text = _text(value)
    if not text:
        return []
    for sep in ("、", "，", ",", "/", "／", ";", "；", "|"):
        text = text.replace(sep, "\n")
    return [part.strip() for part in text.split("\n") if part.strip()]


def parse_catalog_workbook(data: bytes) -> list[dict]:
    """把上传的 xlsx 解析成行；每行带 row / error（解析成功时 error 为空）。"""
    if not data:
        raise ValueError("上传文件是空的：请重新选择")
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl 的异常类型随版本变，统一翻成可读文案
        raise ValueError("这个文件不是有效的 .xlsx：请用模板另存后再上传") from exc
    try:
        sheet = workbook.active
        if sheet is None:
            raise ValueError("工作簿里没有工作表")
        header_row = None
        header_index = 0
        for index, row in enumerate(sheet.iter_rows(values_only=True)):
            if index > 20:
                break
            if any(_text(cell) for cell in row):
                header_row = row
                header_index = index
                break
        if header_row is None:
            raise ValueError("表头是空的：第一行请写供应商 / 分类 / 商品名称 / 岗位等列名")
        index_by_header: dict[str, int] = {}
        for column, value in enumerate(header_row):
            label = _norm_header(value)
            if label and label not in index_by_header:
                index_by_header[label] = column
        missing = [label for label in REQUIRED_HEADERS if label not in index_by_header]
        if missing:
            raise ValueError("表头缺少这些列：" + "、".join(missing))
        rows: list[dict] = []
        for offset, row in enumerate(sheet.iter_rows(min_row=header_index + 2, values_only=True)):
            row_number = header_index + offset + 2
            if not any(_text(cell) for cell in row):
                continue

            def cell(label: str) -> Any:
                column = index_by_header.get(label)
                return row[column] if column is not None and column < len(row) else None

            item: dict[str, Any] = {
                "row": row_number,
                "supplier_code": _code(cell("供应商编码"), 2),
                "supplier_name": _text(cell("供应商")),
                "category_code": _code(cell("分类编码"), 2),
                "category_name": _text(cell("分类")),
                "product_code": _code(cell("商品编码"), 7),
                "name": _text(cell("商品名称")),
                "specification": _text(cell("规格")),
                "unit": _text(cell("单位")),
                "positions": _split_positions(cell("岗位")),
                "remark": _text(cell("备注")),
                "order_step": None,
                "safety_factor": None,
                "need_stock_check": None,
                "is_long_cycle": None,
                "active": None,
                "error": "",
            }
            try:
                item["order_step"] = _number(cell("下单步长"), "下单步长")
                item["safety_factor"] = _number(cell("安全系数"), "安全系数")
                item["need_stock_check"] = _bool_value(cell("需盘点"), "需盘点")
                item["is_long_cycle"] = _bool_value(cell("长周期"), "长周期")
                item["active"] = _bool_value(cell("启用"), "启用")
                if not item["supplier_code"] and not item["supplier_name"]:
                    raise ValueError("供应商编码与供应商名称至少填一个")
                if not item["category_code"] and not item["category_name"]:
                    raise ValueError("分类编码与分类名称至少填一个")
                if not item["name"]:
                    raise ValueError("商品名称不能为空")
                if not item["positions"]:
                    raise ValueError("岗位不能为空，多个岗位用、隔开")
            except ValueError as exc:
                item["error"] = str(exc)
            rows.append(item)
        return rows
    finally:
        workbook.close()


def _write_sheet_header(sheet) -> None:
    sheet.append([label for _, label, _ in COLUMNS])
    for column, (_, _, width) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=1, column=column)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _HEADER_ALIGN
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}1"


def _write_help_sheet(sheet) -> None:
    sheet.column_dimensions["A"].width = 18
    sheet.column_dimensions["B"].width = 64
    lines = (
        ("列", "怎么填"),
        ("供应商编码 / 供应商", "至少填一个；编码或名称必须能在目录里找到（不会自动新建供应商）。"),
        ("分类编码 / 分类", "至少填一个；分类必须属于这一行的供应商。"),
        ("商品编码", "7 位数字，留空则按供应商 2 位 + 分类 2 位 + 序号自动生成。"),
        ("商品名称", "必填，最多 60 个字。"),
        ("岗位", "必填，可写多个，用「、」隔开；岗位名称必须已在目录里建好。"),
        ("下单步长 / 安全系数", "留空按 1 / 1.2；必须大于 0。"),
        ("需盘点 / 长周期 / 启用", "填「是」或「否」；留空时新建按否 / 否 / 是，更新保持原值。"),
        ("导入方式", "「新增」遇到已有商品编码会报错；「新增或更新」按商品编码覆盖可编辑字段。"),
    )
    for row in lines:
        sheet.append(list(row))
    for cell in sheet[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL


def build_catalog_workbook(products: Iterable[dict] = (), *, template: bool = False) -> bytes:
    """导出目录；template=True 时只写表头与填写说明。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_TITLE
    _write_sheet_header(sheet)
    if template:
        _write_help_sheet(workbook.create_sheet(TEMPLATE_HELP_SHEET))
    else:
        for product in products:
            positions = product.get("positions") or []
            sheet.append(
                [
                    _code(product.get("supplier_code"), 2),
                    _text(product.get("supplier_name")),
                    _code(product.get("category_code"), 2),
                    _text(product.get("category_name")),
                    _code(product.get("product_code"), 7),
                    _text(product.get("name")),
                    _text(product.get("specification")),
                    _text(product.get("unit")),
                    "、".join(_text(row.get("name")) for row in positions if row.get("name")),
                    _text(product.get("order_step")),
                    _text(product.get("safety_factor")),
                    "是" if product.get("need_stock_check") else "否",
                    "是" if product.get("is_long_cycle") else "否",
                    "是" if product.get("active", True) else "否",
                    _text(product.get("remark")),
                ]
            )
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def export_filename(today: Optional[date] = None) -> str:
    return f"purchase-catalog-{(today or date.today()).isoformat()}.xlsx"


TEMPLATE_FILENAME = "purchase-catalog-template.xlsx"
