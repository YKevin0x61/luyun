#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工餐采购的 HTTP 适配层（ADR 0135，票 03）。

业务规则都在 `services/staff_meal/service.py`，这里只做三件事：把身份换成 actor、
把上传/JSON 参数收下来、把 `StaffMealError.code` 翻成能照着做的中文。

**三条 router，读者与写者分开**（形态照 `api/prep_plan.py`）：

- `router` —— 管理端。注册处（`main.py`）整体挂 `verify_admin_token`：导入整月菜单、
  手改某天菜单、删草稿单。员工会话在这一侧拿到的是 401（门都没过）。
- `reader_router` —— **任一身份**：正常读月历 / 单日、取自历史食材名。员工手机与
  管理端电脑共用这一面。
- `manager_router` —— **任一身份进门、服务层再判归属**：保存草稿、提交（含改后重推）、
  推送重试。这里不能挂 `verify_admin_token` —— 员工要拿到业务层的 403「不是你的单」
  而不是 401「没登录」，得先过「任一身份」门拿到 `kind` 才分得清（与
  `api/prep_plan.py` 的 `manager_router` 同一条理由）。

**权限口径**（ADR 0135 / 2026-11-11 用户裁定）：这一域**不设能力开关** —— 读 / 填 =
任何员工（员工餐是全员都能参与的活）；改已提交单 = 提交者本人或管理员；导入 / 改菜单 /
删单 = 只有管理员。身份与归属**每次请求现读**，不在登录时快照。
"""

from __future__ import annotations

import calendar
import io
import logging
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from pydantic import BaseModel

from api.security import require_any_identity_session
from database import get_db
from services.staff_meal.service import (
    MAX_ITEM_NAME,
    MAX_ITEMS,
    MAX_QUANTITY,
    MAX_REMARK,
    MAX_UNIT,
    MenuConflictError,
    StaffMealActor,
    StaffMealError,
    staff_meal_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/staff-meal", tags=["员工餐采购"])
reader_router = APIRouter(
    prefix="/api/staff-meal",
    tags=["员工餐采购"],
    dependencies=[Depends(require_any_identity_session)],
)
manager_router = APIRouter(
    prefix="/api/staff-meal",
    tags=["员工餐采购"],
    dependencies=[Depends(require_any_identity_session)],
)

# 一个月份表的 xlsx 实测只有十几 KB；给 10 MB 是给「有人贴了一堆截图进去」留的余量，
# 与损耗照片那 20 MB 不是同一类东西。超限回 413，页面提示重新导出一份干净的模板。
MAX_WORKBOOK_BYTES = 10 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024

#: 空模板（整月 31 天，表头与 `services/staff_meal/menu_import.py` 的解析规则一致）。
#: 与 KDS 等静态资源同住 `public/`：发行包按 `git archive HEAD` 打包，不提交就没有。
STAFF_MEAL_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "public"
    / "staff-meal"
    / "staff-meal-menu-template.xlsx"
)
STAFF_MEAL_TEMPLATE_FILENAME = "staff-meal-menu-template.xlsx"
STAFF_MEAL_TEMPLATE_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

#: 动态模板接受的年份范围，与导入参数校验（`_check_year_month`）保持一致。
TEMPLATE_MIN_YEAR = 2020
TEMPLATE_MAX_YEAR = 2100

_TEMPLATE_DAY_FILL = PatternFill("solid", fgColor="DDEBF7")
_TEMPLATE_LABEL_FILL = PatternFill("solid", fgColor="F2F2F2")


def _template_blocks(days: int) -> list[int]:
    """把当月天数切成周块（每块最多 7 天）：31 → 7/7/7/7/3，30 → 7/7/7/7/2。"""
    blocks: list[int] = []
    remaining = days
    while remaining > 0:
        blocks.append(min(7, remaining))
        remaining -= 7
    return blocks


def _write_template_menu_sheet(ws, year: int, month: int) -> None:
    """写「菜单」表：表头占位与静态 31 天模板一致，只是日期列数按当月天数变。"""
    days = calendar.monthrange(year, month)[1]
    ws.title = "菜单"
    ws["A1"] = f"{year}年{month:02d}月员工餐饮菜单（空白模板）"
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=8)

    day = 1
    row = 2
    for block_days in _template_blocks(days):
        # 时间行：A 列「时间」，B 列起是日号（按当月实际天数生成，用户不用删列）。
        ws.cell(row=row, column=1, value="时间")
        for col in range(2, block_days + 2):
            cell = ws.cell(row=row, column=col, value=day)
            cell.alignment = Alignment(horizontal="center")
            cell.fill = _TEMPLATE_DAY_FILL
            cell.font = Font(bold=True)
            day += 1
        # 星期行留空：每月星期不同，解析时留空 = None，可以正常导入。
        ws.cell(row=row + 1, column=1, value="星期")
        ws.cell(row=row + 2, column=1, value="午餐")
        ws.cell(row=row + 2, column=2, value="主菜")
        ws.cell(row=row + 3, column=2, value="副菜")
        ws.cell(row=row + 4, column=2, value="副菜")
        ws.cell(row=row + 5, column=1, value="饮品/水果")
        ws.cell(row=row + 6, column=1, value="晚餐")
        ws.cell(row=row + 6, column=2, value="主菜")
        ws.cell(row=row + 7, column=2, value="副菜")
        ws.cell(row=row + 8, column=2, value="副菜")
        for r in range(row, row + 9):
            ws.cell(row=r, column=1).fill = _TEMPLATE_LABEL_FILL
            ws.cell(row=r, column=1).font = Font(bold=True)
            ws.cell(row=r, column=1).alignment = Alignment(horizontal="center")
            ws.cell(row=r, column=2).alignment = Alignment(horizontal="center")
        row += 9

    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 10
    for col in range(3, 9):
        ws.column_dimensions[get_column_letter(col)].width = 14
    ws.freeze_panes = "B2"


def _write_template_help_sheet(ws, year: int, month: int) -> None:
    """写「使用说明」表；按月份生成的模板日期列已齐全，不需要用户删列。"""
    days = calendar.monthrange(year, month)[1]
    ws.title = "使用说明"
    lines = [
        "员工餐采购菜单 · 空白模板使用说明",
        "",
        f"1. 本模板按 {year} 年 {month:02d} 月生成（{days} 天），日期列已齐全，不需要增删。",
        "2. 在「菜单」工作表里填当月菜单：午餐 / 晚餐每列最多 3 道菜（主菜 1 道、副菜 2 道）。",
        "3. 「时间」行左侧是日号，不要改；「星期」行可以留空（系统不强制）。",
        "4. 空单元格 = 那道菜没定；填 / 表示当天没有这道菜；填「自主发挥」表示厨师自由发挥。",
        "5. 填完在管理端「导入菜单」上传本文件即可：系统按你选择的年月生成每天一张采购草稿单。",
    ]
    for index, text in enumerate(lines, start=1):
        ws.cell(row=index, column=1, value=text)
    ws["A1"].font = Font(bold=True, size=13)
    ws.column_dimensions["A"].width = 90


def _build_menu_template(year: int, month: int) -> bytes:
    """按月份现场生成整月空模板（openpyxl），不依赖 `public/` 下的静态文件。"""
    workbook = Workbook()
    _write_template_menu_sheet(workbook.active, year, month)
    _write_template_help_sheet(workbook.create_sheet("使用说明"), year, month)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


#: 页面上的单位快捷项。库里 `unit` 是自由文本（Q7），这里只是常用项；手输照收。
UNITS = (
    "斤",
    "公斤",
    "只",
    "个",
    "根",
    "盒",
    "包",
    "瓶",
    "袋",
    "把",
    "条",
    "块",
    "件",
    "桶",
    "提",
    "片",
    "箱",
)

_NOT_MIGRATED_DETAIL = (
    "员工餐采购表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
    "migrations/pg/0053_staff_meal.sql，然后刷新本页"
)

#: 能照着改的文案：`not_migrated` 不是「参数写错了」，是这台机器还没升级完。
_ERROR_STATUS = {
    "forbidden": 403,
    "order_not_found": 404,
    "menu_day_not_found": 404,
    "day_not_found": 404,
    "no_items": 409,
    "already_submitted": 409,
    "not_submitted": 409,
    "menu_exists": 409,
    "day_expired": 409,
    "conflict": 409,
    "bad_workbook": 400,
    "bad_menu": 400,
    "bad_remark": 400,
    "item_invalid": 400,
    "bad_month": 400,
    "bad_date": 400,
    "push_failed": 502,
    "not_migrated": 503,
}

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MenuDayUpdate(BaseModel):
    """手改某天菜单：只传要改的字段（`exclude_unset` 才分得清「没传」与「清空」）。"""

    lunch_main: Optional[str] = None
    lunch_side1: Optional[str] = None
    lunch_side2: Optional[str] = None
    dinner_main: Optional[str] = None
    dinner_side1: Optional[str] = None
    dinner_side2: Optional[str] = None
    drink: Optional[str] = None
    freeform: Optional[bool] = None


class OrderSaveRequest(BaseModel):
    """保存草稿：`items` 是整张单的条目数组（页面每次提交全量，不做增量）。

    `expected_updated_at` 是页面上读到的版本（乐观锁，F14）：传了就要求与库里一致，
    两人同时改同一张单时后到的一次会拿到 409 而不是静默覆盖；不传保持旧行为。
    """

    items: list[dict[str, Any]] = []
    remark: Optional[str] = None
    expected_updated_at: Optional[str] = None


def _actor(identity: dict) -> StaffMealActor:
    """两种会话 → 服务层 actor（这一域不设能力开关，员工身份即可写）。"""
    if identity.get("kind") == "staff":
        employee = identity.get("employee") or {}
        employee_id = employee.get("id")
        return StaffMealActor(
            kind="staff",
            employee_id=int(employee_id) if employee_id is not None else None,
            name=str(employee.get("name") or ""),
        )
    return StaffMealActor(kind="admin", name="管理端")


def _admin_actor() -> StaffMealActor:
    """管理端写面（注册处已挂 `verify_admin_token`，走到这里就是管理员）。"""
    return StaffMealActor(kind="admin", name="管理端")


def _http_error(exc: StaffMealError) -> HTTPException:
    if isinstance(exc, MenuConflictError):
        logger.info("员工餐导入撞上已有单据 month=%s", exc.payload.get("month"))
        return HTTPException(
            status_code=409,
            detail={
                "code": "menu_exists",
                "message": "这个月已经有采购单了：确认后覆盖菜单，已提交的采购单会保留不动",
                **exc.payload,
            },
        )
    if exc.code == "not_migrated":
        logger.warning("员工餐表还没迁移: %s", exc)
        return HTTPException(status_code=503, detail=_NOT_MIGRATED_DETAIL)
    logger.info("员工餐请求被拒: code=%s", exc.code)
    return HTTPException(
        status_code=_ERROR_STATUS.get(exc.code, 400), detail=str(exc) or "请求不合法"
    )


def _check_month(year_month: str) -> str:
    if not _MONTH_RE.match(year_month or ""):
        raise HTTPException(status_code=400, detail="月份格式应该是 YYYY-MM")
    return year_month


def _check_date(order_date: str) -> str:
    if not _DATE_RE.match(order_date or ""):
        raise HTTPException(status_code=400, detail="日期格式应该是 YYYY-MM-DD")
    return order_date


def _check_year_month(year: int, month: int) -> None:
    if not 2020 <= int(year) <= 2100:
        raise HTTPException(status_code=400, detail="导入年份不合法")
    if not 1 <= int(month) <= 12:
        raise HTTPException(status_code=400, detail="导入月份不合法")


async def _read_upload_bounded(file: UploadFile, label: str = "菜单文件") -> bytes:
    limit_mb = MAX_WORKBOOK_BYTES // (1024 * 1024)
    declared = getattr(file, "size", None)
    if declared is not None and declared > MAX_WORKBOOK_BYTES:
        raise HTTPException(status_code=413, detail=f"{label}不能超过 {limit_mb} MB")
    data = bytearray()
    while True:
        chunk = await file.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_WORKBOOK_BYTES:
            raise HTTPException(status_code=413, detail=f"{label}不能超过 {limit_mb} MB")
    return bytes(data)


# ── 管理端：导入与维护 ────────────────────────────────────────────


@router.post("/menu/import/preview")
async def preview_import_menu(
    file: UploadFile = File(..., description="月度员工餐菜单 .xlsx"),
    year: int = Form(..., description="目标年份，如 2026"),
    month: int = Form(..., description="目标月份 1-12"),
    db=Depends(get_db),
) -> dict:
    """只解析不写库：上传前告诉管理员这份表拆出多少天、哪些天已有单。"""
    _check_year_month(year, month)
    data = await _read_upload_bounded(file)
    if not data:
        raise HTTPException(status_code=400, detail="上传文件是空的：请重新选择")
    try:
        return await staff_meal_service.preview_import(
            db, _admin_actor(), data, year, month
        )
    except StaffMealError as exc:
        raise _http_error(exc) from exc


@router.post("/menu/import")
async def import_menu(
    file: UploadFile = File(..., description="月度员工餐菜单 .xlsx"),
    year: int = Form(..., description="目标年份，如 2026"),
    month: int = Form(..., description="目标月份 1-12"),
    confirm: bool = Form(False, description="同月已有单据时是否确认覆盖菜单"),
    db=Depends(get_db),
) -> dict:
    """导入整月菜单，并给每天建一张草稿采购单（已提交的单保留不动）。"""
    _check_year_month(year, month)
    data = await _read_upload_bounded(file)
    if not data:
        raise HTTPException(status_code=400, detail="上传文件是空的：请重新选择")
    try:
        return await staff_meal_service.import_month(
            db, _admin_actor(), data, year, month, confirm=confirm
        )
    except StaffMealError as exc:
        raise _http_error(exc) from exc


@router.get("/menu/template")
async def download_menu_template(
    year: Optional[int] = Query(
        None, description="目标年份（2020–2100）；与 month 一起传时按该月天数现场生成"
    ),
    month: Optional[int] = Query(None, description="目标月份（1–12）"),
) -> Response:
    """下载空白的整月菜单模板（管理端）。

    不传参数：返回 `public/` 里的静态 31 天模板（旧行为）。
    传 year + month：按该月实际天数（28/29/30/31）现场生成，文件名带年月 ——
    30 天月份 / 2 月不用再让用户自己在 Excel 里删日期列。
    """
    if year is None and month is None:
        if not STAFF_MEAL_TEMPLATE_PATH.is_file():
            logger.error("员工餐模板文件缺失: %s", STAFF_MEAL_TEMPLATE_PATH)
            raise HTTPException(
                status_code=404,
                detail="员工餐菜单模板还没准备好：请更新到最新版本后再试",
            )
        return FileResponse(
            STAFF_MEAL_TEMPLATE_PATH,
            media_type=STAFF_MEAL_TEMPLATE_MEDIA_TYPE,
            filename=STAFF_MEAL_TEMPLATE_FILENAME,
        )

    if year is None or month is None:
        raise HTTPException(
            status_code=400,
            detail="year 和 month 要一起传：只传一个没法确定模板月份",
        )
    if not TEMPLATE_MIN_YEAR <= int(year) <= TEMPLATE_MAX_YEAR:
        raise HTTPException(
            status_code=400,
            detail=f"模板年份要在 {TEMPLATE_MIN_YEAR}–{TEMPLATE_MAX_YEAR} 之间",
        )
    if not 1 <= int(month) <= 12:
        raise HTTPException(status_code=400, detail="模板月份要在 1–12 之间")

    content = _build_menu_template(int(year), int(month))
    filename = f"员工餐菜单模板-{int(year):04d}-{int(month):02d}.xlsx"
    logger.info("按月份生成员工餐菜单模板: %s (%d bytes)", filename, len(content))
    return Response(
        content=content,
        media_type=STAFF_MEAL_TEMPLATE_MEDIA_TYPE,
        headers={
            "Content-Disposition": "attachment; "
            + f"filename*=utf-8''{quote(filename)}"
        },
    )


@router.put("/menu/days/{order_date}")
async def update_menu_day(
    order_date: str,
    payload: MenuDayUpdate,
    db=Depends(get_db),
) -> dict:
    """手改某天菜单（导入后临时换菜；模板与表格不一致时的兜底）。"""
    _check_date(order_date)
    fields = payload.model_dump(exclude_unset=True)
    try:
        return await staff_meal_service.update_menu_day(
            db, _admin_actor(), order_date, fields
        )
    except StaffMealError as exc:
        raise _http_error(exc) from exc


@router.delete("/orders/{order_id}")
async def delete_draft_order(order_id: int, db=Depends(get_db)) -> dict:
    """删一张草稿单（已提交的单不删 —— 单据是已发生的事，见 ADR 0135）。"""
    try:
        return await staff_meal_service.delete_draft(
            db, _admin_actor(), int(order_id)
        )
    except StaffMealError as exc:
        raise _http_error(exc) from exc


# ── 任一身份：读 ─────────────────────────────────────────────────


@reader_router.get("/months/{year_month}")
async def list_month(
    year_month: str,
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """月历：每天一行菜单 + 那天的单据状态（未导入的月份返回空 days）。"""
    _check_month(year_month)
    try:
        return await staff_meal_service.list_month(db, _actor(identity), year_month)
    except StaffMealError as exc:
        raise _http_error(exc) from exc


@reader_router.get("/days/{order_date}")
async def get_day(
    order_date: str,
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """单日：菜单 + 采购单（含菜品、条目、汇总）。"""
    _check_date(order_date)
    try:
        return await staff_meal_service.get_day(db, _actor(identity), order_date)
    except StaffMealError as exc:
        raise _http_error(exc) from exc


@reader_router.get("/suggestions")
async def dish_item_suggestions(
    dish: str = Query(..., min_length=1, description="菜名"),
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """同名菜历史上买过的食材名（只带名字与常用单位，不带数量 —— Q9）。"""
    try:
        return await staff_meal_service.suggestions(db, _actor(identity), dish)
    except StaffMealError as exc:
        raise _http_error(exc) from exc


@reader_router.get("/meta")
async def get_meta(
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """页面用的常量（单位快捷项与几个上限），避免前后端各写一份。"""
    return {
        "units": list(UNITS),
        "max_items": MAX_ITEMS,
        "max_item_name": MAX_ITEM_NAME,
        "max_unit": MAX_UNIT,
        "max_quantity": float(MAX_QUANTITY),
        "max_remark": MAX_REMARK,
    }


# ── 任一身份：写（服务层判能力 / 归属） ──────────────────────────


@manager_router.put("/days/{order_date}/order")
async def save_order(
    order_date: str,
    payload: OrderSaveRequest,
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """保存整张单（草稿；改已提交单只有提交者本人与管理员能做）。"""
    _check_date(order_date)
    try:
        return await staff_meal_service.save_order(
            db,
            _actor(identity),
            order_date,
            {
                "items": payload.items,
                "remark": payload.remark,
                "expected_updated_at": payload.expected_updated_at,
            },
        )
    except StaffMealError as exc:
        raise _http_error(exc) from exc


@manager_router.post("/days/{order_date}/order/submit")
async def submit_order(
    order_date: str,
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """提交（或改后重推）一张单：至少一项有数量，推完整清单到核心群。"""
    _check_date(order_date)
    try:
        return await staff_meal_service.submit_order(
            db, _actor(identity), order_date
        )
    except StaffMealError as exc:
        raise _http_error(exc) from exc


@manager_router.post("/orders/{order_id}/retry-push")
async def retry_push(
    order_id: int,
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """推送失败后重试（同一份清单再发一版，标题带「（更新）」）。"""
    try:
        return await staff_meal_service.retry_push(
            db, _actor(identity), int(order_id)
        )
    except StaffMealError as exc:
        raise _http_error(exc) from exc
