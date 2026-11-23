#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""厨房采购的 HTTP 适配层（spec: .scratch/worktool2-import/spec.md）。

业务规则都在 services/purchase/service.py；这里只做身份转换、参数收集与错误翻译。
三条 router：

- reader_router：任一身份进门，服务层再判「管理端或花名册勾了厨房采购」。员工手机
  与管理端电脑共用这一面。
- manager_router：任一身份进门，服务层判管理员 / 提交者本人。**不能挂
  verify_admin_token** —— 员工要拿到 403「只有管理端能……」，不是 401「没登录」。
- public_router：**免登录**，只放今日汇总（小程序/看板读）。没有身份判断，所以
  只挂只读聚合，且只返回汇总口径的字段，「没人登录」是它的契约而不是漏配。

页面清单与权限键（purchase）见 admin-web/src/router/pageRoutes.json 与
services/identity/capabilities.py。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from api.security import require_any_identity_session
from database import get_db
from services.export_jobs import (
    KIND_PURCHASE_ANALYSIS,
    ExportJobBusy,
    ExportJobError,
    ExportJobFailed,
    ExportJobGone,
    ExportJobNotFound,
    ExportJobNotReady,
    JobHandle,
    JobResult,
    export_jobs,
)
from services.printer import service as printer_service
from services.printer.config import (
    describe as describe_printer,
    load_config as load_printer_config,
    save_config as save_printer_config,
)
from services.printer.service import PrinterError
from services.purchase.analysis_xlsx import (
    analysis_filename,
    build_analysis_workbook,
)
from services.purchase.catalog_xlsx import (
    TEMPLATE_FILENAME,
    build_catalog_workbook,
    export_filename,
    parse_catalog_workbook,
)
from services.purchase.migration import (
    MAX_UPLOAD_BYTES as MAX_MIGRATION_UPLOAD_BYTES,
    MigrationError,
    migration_manager,
)
from services.purchase.service import (
    PurchaseActor,
    PurchaseError,
    purchase_service,
)

logger = logging.getLogger(__name__)

reader_router = APIRouter(
    prefix="/api/purchase",
    tags=["厨房采购"],
    dependencies=[Depends(require_any_identity_session)],
)
manager_router = APIRouter(
    prefix="/api/purchase",
    tags=["厨房采购"],
    dependencies=[Depends(require_any_identity_session)],
)
#: 对外公开面：**故意不挂身份依赖**。只放今日汇总这一条只读聚合，供小程序/看板
#: 免登录读取；新增任何写接口或明细字段都不该挂到这里（见模块 docstring）。
public_router = APIRouter(prefix="/api/purchase/public", tags=["厨房采购（公开）"])

_NOT_MIGRATED_DETAIL = (
    "厨房采购表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
    "migrations/pg/0062_kitchen_purchase.sql，然后刷新本页"
)

_ERROR_STATUS = {
    "forbidden": 403,
    "order_not_found": 404,
    "product_not_found": 404,
    "supplier_not_found": 404,
    "category_not_found": 404,
    "position_not_found": 404,
    "not_pending": 409,
    "duplicate_supplier": 409,
    "supplier_in_use": 409,
    "duplicate_category": 409,
    "category_in_use": 409,
    "duplicate_position": 409,
    "duplicate_product": 409,
    "code_exhausted": 409,
    "bad_request": 400,
    "bad_date": 400,
    "bad_tag": 400,
    "bad_weekday": 400,
    "bad_position": 400,
    "bad_items": 400,
    "bad_code": 400,
    "not_migrated": 503,
}

_XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
_MAX_WORKBOOK_BYTES = 10 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _read_upload_bounded(
    file: UploadFile,
    label: str = "采购目录文件",
    *,
    max_bytes: int = _MAX_WORKBOOK_BYTES,
) -> bytes:
    """分块读上传文件，超过上限直接 413，不把整份大表吃进内存。"""
    limit_mb = max(1, (max_bytes + 1024 * 1024 - 1) // (1024 * 1024))
    declared = getattr(file, "size", None)
    if declared is not None and declared > max_bytes:
        raise HTTPException(status_code=413, detail=f"{label}不能超过 {limit_mb} MB")
    data = bytearray()
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail=f"{label}不能超过 {limit_mb} MB")
    return bytes(data)


def _xlsx_download(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _actor(identity: dict) -> PurchaseActor:
    if identity.get("kind") == "staff":
        employee = identity.get("employee") or {}
        employee_id = employee.get("id")
        return PurchaseActor(
            kind="staff",
            employee_id=int(employee_id) if employee_id is not None else None,
            name=str(employee.get("name") or ""),
            caps=frozenset(employee.get("admin_caps") or ()),
        )
    return PurchaseActor(kind="admin", name="管理端")


def _ensure_admin(identity: dict) -> None:
    try:
        purchase_service.ensure_admin(_actor(identity))
    except PurchaseError as exc:
        raise _http_error(exc) from exc


def _http_error(exc: PurchaseError) -> HTTPException:
    if exc.code == "not_migrated":
        logger.warning("厨房采购表还没迁移: %s", exc)
        return HTTPException(status_code=503, detail=_NOT_MIGRATED_DETAIL)
    logger.info("厨房采购请求被拒: code=%s", exc.code)
    return HTTPException(
        status_code=_ERROR_STATUS.get(exc.code, 400),
        detail=str(exc) or "请求不合法",
        headers={"X-Error-Code": exc.code},
    )


async def _run(call, *args, **kwargs):
    try:
        return await call(*args, **kwargs)
    except PurchaseError as exc:
        raise _http_error(exc) from exc


def _parse_ids(raw: Optional[str]) -> list[int]:
    if not raw:
        return []
    values: list[int] = []
    for chunk in str(raw).replace("，", ",").split(","):
        text = chunk.strip()
        if text:
            try:
                values.append(int(text))
            except ValueError:
                continue
    return values


def _export_job_http_error(exc: ExportJobError) -> HTTPException:
    if isinstance(exc, ExportJobBusy):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, ExportJobNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ExportJobGone):
        return HTTPException(status_code=410, detail=str(exc))
    if isinstance(exc, ExportJobNotReady):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ExportJobFailed):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc) or "导出失败")


_MIGRATION_STATUS = {
    "not_found": 404,
    "expired": 410,
    "busy": 429,
    "preview_required": 409,
    "preview_not_done": 409,
    "report_not_ready": 409,
    "no_files": 400,
    "too_many_files": 400,
    "too_large": 413,
    "bad_archive": 400,
    "bad_file": 400,
    "no_collections": 400,
    "ambiguous_collection": 400,
    "bad_request": 400,
    "not_migrated": 503,
    "bad_source": 400,
    "storage": 500,
}


def _migration_http_error(exc: MigrationError) -> HTTPException:
    logger.info("厨房采购迁移请求被拒: code=%s detail=%s", exc.code, exc)
    return HTTPException(
        status_code=_MIGRATION_STATUS.get(exc.code, 400),
        detail=str(exc) or "迁移请求不合法",
        headers={"X-Error-Code": exc.code},
    )


# ── 读：下单页 / 目录 / 单张单 ──────────────────────────────────


@reader_router.get("/meta")
async def purchase_meta(
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(purchase_service.meta, db, _actor(identity))


@reader_router.get("/products")
async def purchase_products(
    position_id: Optional[int] = Query(None),
    q: str = Query(""),
    include_inactive: bool = Query(False),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.products,
        db,
        _actor(identity),
        position_id=position_id,
        q=q,
        include_inactive=include_inactive,
    )


@reader_router.get("/reference")
async def purchase_reference(
    position_id: Optional[int] = Query(None),
    day: Optional[str] = Query(None),
    days: Optional[int] = Query(None),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.reference,
        db,
        _actor(identity),
        position_id=position_id,
        day=day,
        days=days,
    )


@reader_router.get("/weather")
async def purchase_weather(
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(purchase_service.weather, db, _actor(identity))


@reader_router.get("/stock")
async def purchase_stock(
    day: Optional[str] = Query(None),
    position_id: Optional[int] = Query(None),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.stock,
        db,
        _actor(identity),
        day=day,
        position_id=position_id,
    )


@reader_router.get("/orders")
async def purchase_orders(
    day_from: Optional[str] = Query(None),
    day_to: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    position_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.list_orders,
        db,
        _actor(identity),
        day_from=day_from,
        day_to=day_to,
        status=status,
        position_id=position_id,
        page=page,
        page_size=page_size,
    )


@reader_router.get("/orders/{order_id}")
async def purchase_order_detail(
    order_id: int,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.order_detail, db, _actor(identity), order_id
    )


@reader_router.get("/summary")
async def purchase_summary(
    day: Optional[str] = Query(None),
    position_ids: Optional[str] = Query(None),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.summary,
        db,
        _actor(identity),
        day=day,
        position_ids=_parse_ids(position_ids),
    )


@reader_router.get("/summary/export")
async def purchase_summary_export(
    day: Optional[str] = Query(None),
    position_ids: Optional[str] = Query(None),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> Response:
    data = await _run(
        purchase_service.summary,
        db,
        _actor(identity),
        day=day,
        position_ids=_parse_ids(position_ids),
    )
    filename = "purchase-summary-" + str(data["date"]) + ".xlsx"
    return Response(
        content=_summary_workbook(data),
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="' + filename + '"'},
    )


# ── 小票打印（商鹏云云打印） ─────────────────────────────────
#
# 只打「今日汇总」这一张票：页面点按钮才发，不做定时任务。配置存 app_settings
# 的 purchase_printer，密钥加密；GET 对员工开放但只回「有没有填」，不回原值。
#
# 打印走 reader（员工在采购页也能给自己打一张当天的汇总），改配置与发测试票
# 走 manager —— 前者动的是全店的出票口，后者会真出纸。

_PRINTER_ERROR_STATUS = {
    "not_configured": 400,
    "disabled": 409,
    "empty": 409,
    "bind_failed": 502,
    "print_failed": 502,
    "status_failed": 502,
}


def _printer_http_error(exc: PrinterError) -> HTTPException:
    logger.info("采购小票打印失败: code=%s", exc.code)
    return HTTPException(
        status_code=_PRINTER_ERROR_STATUS.get(exc.code, 400),
        detail=exc.message or "打印失败",
        headers={"X-Error-Code": exc.code},
    )


async def _run_printer(call, *args, **kwargs):
    try:
        return await call(*args, **kwargs)
    except PrinterError as exc:
        raise _printer_http_error(exc) from exc


async def _summary_for_print(db, identity: dict, day, position_ids) -> dict:
    return await _run(
        purchase_service.summary,
        db,
        _actor(identity),
        day=day,
        position_ids=_parse_ids(position_ids),
    )


@reader_router.get("/printer")
async def purchase_printer_config(
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    """当前打印配置（密钥脱敏）。前端用 enabled/configured 决定按钮状态。"""
    config = await load_printer_config(db)
    return config.public()


@manager_router.put("/printer")
async def purchase_printer_save(
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    """保存配置。密钥字段留空=保留原值；clear_secret=true 才清空。"""
    _ensure_admin(identity)
    config = await save_printer_config(db, payload)
    logger.info("采购小票打印配置已更新: %s", describe_printer(config))
    return config.public()


@manager_router.get("/printer/info")
async def purchase_printer_info(
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    """设备在线状态 / 待打张数 / 型号。设置页点「检查设备」时调。"""
    _ensure_admin(identity)
    return await _run_printer(printer_service.device_status, db)


@manager_router.post("/printer/test")
async def purchase_printer_test(
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    """打一张固定内容的测试票，用来先撞出签名/绑定/在线问题。"""
    _ensure_admin(identity)
    return await _run_printer(printer_service.send_test_ticket, db)


@reader_router.get("/summary/ticket/preview")
async def purchase_summary_ticket_preview(
    day: Optional[str] = Query(None),
    position_ids: Optional[str] = Query(None),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    """只排版不发送：返回会打出去的原文与统计，设置页的预览用。"""
    data = await _summary_for_print(db, identity, day, position_ids)
    config = await load_printer_config(db)
    # 预览必须和真打的一致：票尾的次日员工餐走同一个取数函数。
    staff_meal = await printer_service.next_day_staff_meal(db, data)
    return {
        "date": data.get("date"),
        **printer_service.preview_ticket(data, config, staff_meal),
    }


@reader_router.post("/summary/ticket")
async def purchase_summary_ticket(
    day: Optional[str] = Query(None),
    position_ids: Optional[str] = Query(None),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    """把今日汇总推到打印机。**每次调用都会真出纸**，前端按钮需防连点。"""
    data = await _summary_for_print(db, identity, day, position_ids)
    return await _run_printer(printer_service.print_summary, db, data)


# ── 公开：今日汇总（免登录） ──────────────────────────────────


@public_router.get("/summary")
async def purchase_public_summary(
    day: Optional[str] = Query(None),
    position_ids: Optional[str] = Query(None),
    db=Depends(get_db),
) -> dict:
    """今日汇总的免登录版本；口径与工作台 `/api/purchase/summary` 一致。

    返回供应商 → 分类 → 商品的聚合（数量、下单人员），不含订单明细、参考值、
    盘点或任何写入口。小程序只想看「今天买什么」时不必先做员工登录。
    """
    return await _run(
        purchase_service.public_summary,
        db,
        day=day,
        position_ids=_parse_ids(position_ids),
    )


@manager_router.put("/stock")
async def purchase_set_stock(
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.set_stock, db, _actor(identity), payload
    )


# ── 写：下单 / 改单 / 状态 ─────────────────────────────────────


@manager_router.post("/orders")
async def purchase_submit_order(
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.submit_order, db, _actor(identity), payload
    )


@manager_router.patch("/orders/{order_id}/items")
async def purchase_update_items(
    order_id: int,
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.update_items, db, _actor(identity), order_id, payload
    )


@manager_router.post("/orders/{order_id}/complete")
async def purchase_complete_order(
    order_id: int,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.complete_order, db, _actor(identity), order_id
    )


@manager_router.post("/orders/{order_id}/cancel")
async def purchase_cancel_order(
    order_id: int,
    payload: Optional[dict[str, Any]] = Body(None),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.cancel_order, db, _actor(identity), order_id, payload
    )


@manager_router.delete("/orders/{order_id}")
async def purchase_delete_order(
    order_id: int,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.delete_order, db, _actor(identity), order_id
    )


# ── 分析 ─────────────────────────────────────────────────────


@manager_router.get("/analysis/overview")
async def purchase_analysis_overview(
    day: Optional[str] = Query(None),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.analysis_overview, db, _actor(identity), day=day
    )


@manager_router.get("/analysis/anomalies")
async def purchase_analysis_anomalies(
    day: Optional[str] = Query(None),
    no_order_days: int = Query(3, ge=1, le=90),
    over_ratio: float = Query(1.5, gt=0),
    under_ratio: float = Query(0.5, gt=0),
    history_days: int = Query(21, ge=7, le=120),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.analysis_anomalies,
        db,
        _actor(identity),
        day=day,
        no_order_days=no_order_days,
        over_ratio=over_ratio,
        under_ratio=under_ratio,
        history_days=history_days,
    )


@manager_router.post("/analysis/export")
async def start_purchase_analysis_export(
    day: Optional[str] = Query(None),
    no_order_days: int = Query(3, ge=1, le=90),
    over_ratio: float = Query(1.5, gt=0),
    under_ratio: float = Query(0.5, gt=0),
    history_days: int = Query(21, ge=7, le=120),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    """起一个分析导出任务：概览先算好，近 7 日与异常在后台算并落 xlsx。"""
    actor = _actor(identity)
    overview = await _run(purchase_service.analysis_overview, db, actor, day=day)

    async def build(handle: JobHandle) -> JobResult:
        anomalies = await _run(
            purchase_service.analysis_anomalies,
            db,
            actor,
            day=day,
            no_order_days=no_order_days,
            over_ratio=over_ratio,
            under_ratio=under_ratio,
            history_days=history_days,
        )
        handle.progress(done=0, total=1, stage="rendering")
        content = await asyncio.to_thread(build_analysis_workbook, overview, anomalies)
        filename = analysis_filename(overview.get("date") or "")
        path = Path(handle.workdir) / filename
        path.write_bytes(content)
        handle.progress(done=1, total=1, stage="done")
        return JobResult(artifact=path, filename=filename)

    try:
        job_id = export_jobs.start(
            KIND_PURCHASE_ANALYSIS, build, max_running=2
        )
    except ExportJobError as exc:
        raise _export_job_http_error(exc) from exc
    return {"job_id": job_id, "state": "running"}


@manager_router.get("/analysis/export/{job_id}")
async def read_purchase_analysis_export(
    job_id: str,
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    _ensure_admin(identity)
    try:
        snapshot = export_jobs.state(job_id)
    except ExportJobError as exc:
        raise _export_job_http_error(exc) from exc
    return {
        "state": snapshot["state"],
        "done": snapshot["done"],
        "total": snapshot["total"],
        "error": snapshot["error"],
        "filename": snapshot.get("filename"),
        "bytes": snapshot.get("bytes"),
    }


@manager_router.get("/analysis/export/{job_id}/download")
async def download_purchase_analysis_export(
    job_id: str,
    identity: dict = Depends(require_any_identity_session),
) -> FileResponse:
    _ensure_admin(identity)
    try:
        snapshot = export_jobs.state(job_id)
        path = export_jobs.path(job_id)
    except ExportJobError as exc:
        raise _export_job_http_error(exc) from exc
    return FileResponse(
        path,
        media_type=_XLSX_MEDIA_TYPE,
        filename=snapshot.get("filename") or path.name,
    )


@manager_router.get("/analysis/products/{product_id}/history")
async def purchase_product_history(
    product_id: int,
    days: int = Query(21, ge=7, le=180),
    day: Optional[str] = Query(None),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.product_history,
        db,
        _actor(identity),
        product_id,
        days=days,
        day=day,
    )


# ── 目录维护（服务层只认管理端）────────────────────────────────


@manager_router.get("/admin/products")
async def purchase_admin_products(
    position_id: Optional[int] = Query(None),
    q: str = Query(""),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.products,
        db,
        _actor(identity),
        position_id=position_id,
        q=q,
        include_inactive=True,
    )


@manager_router.get("/admin/products/export")
async def purchase_export_products(
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> Response:
    """导出整份目录（含停用商品）。重新导入时按商品编码更新。"""
    data = await _run(
        purchase_service.products,
        db,
        _actor(identity),
        include_inactive=True,
    )
    content = build_catalog_workbook(data.get("products") or [])
    return _xlsx_download(content, export_filename())


@manager_router.get("/admin/products/template")
async def purchase_products_template(
    identity: dict = Depends(require_any_identity_session),
) -> Response:
    """空白模板：表头 + 填写说明，不带示例行，避免手滑把示例导进目录。"""
    return _xlsx_download(build_catalog_workbook(template=True), TEMPLATE_FILENAME)


@manager_router.post("/admin/products/import")
async def purchase_import_products(
    file: UploadFile = File(..., description="采购商品目录 .xlsx"),
    mode: str = Form("upsert", description="add=只新增；upsert=按商品编码新增或更新"),
    dry_run: bool = Form(True, description="只预览不写库"),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    """导入目录：先 dry_run 预览，确认后再写。逐行处理，失败行带原因返回。"""
    raw = await _read_upload_bounded(file)
    try:
        rows = parse_catalog_workbook(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _run(
        purchase_service.import_products,
        db,
        _actor(identity),
        rows,
        mode=mode,
        dry_run=dry_run,
    )


@manager_router.get("/admin/date-tags")
async def purchase_admin_date_tags(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.date_tags, db, _actor(identity), start=start, end=end
    )


@manager_router.post("/admin/suppliers")
async def purchase_create_supplier(
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.create_supplier, db, _actor(identity), payload
    )


@manager_router.patch("/admin/suppliers/{supplier_id}")
async def purchase_update_supplier(
    supplier_id: int,
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.update_supplier,
        db,
        _actor(identity),
        supplier_id,
        payload,
    )


@manager_router.post("/admin/categories")
async def purchase_create_category(
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.create_category, db, _actor(identity), payload
    )


@manager_router.patch("/admin/categories/{category_id}")
async def purchase_update_category(
    category_id: int,
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.update_category,
        db,
        _actor(identity),
        category_id,
        payload,
    )


@manager_router.post("/admin/positions")
async def purchase_create_position(
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.create_position, db, _actor(identity), payload
    )


@manager_router.patch("/admin/positions/{position_id}")
async def purchase_update_position(
    position_id: int,
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.update_position,
        db,
        _actor(identity),
        position_id,
        payload,
    )


@manager_router.post("/admin/products")
async def purchase_create_product(
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.create_product, db, _actor(identity), payload
    )


@manager_router.patch("/admin/products/{product_id}")
async def purchase_update_product(
    product_id: int,
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.update_product,
        db,
        _actor(identity),
        product_id,
        payload,
    )


@manager_router.put("/admin/date-tags")
async def purchase_set_date_tag(
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.set_date_tag, db, _actor(identity), payload
    )


@manager_router.put("/admin/date-tags/bulk")
async def purchase_set_date_tags(
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.set_date_tags, db, _actor(identity), payload
    )


@manager_router.delete("/admin/date-tags")
async def purchase_clear_date_tags(
    start: str = Query(...),
    end: str = Query(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.clear_date_tags, db, _actor(identity), start, end
    )


@manager_router.delete("/admin/date-tags/{tag_date}")
async def purchase_delete_date_tag(
    tag_date: str,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.delete_date_tag, db, _actor(identity), tag_date
    )


@manager_router.put("/admin/weekday-defaults")
async def purchase_set_weekday_default(
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    return await _run(
        purchase_service.set_weekday_default, db, _actor(identity), payload
    )


# ── 网页全量迁移 ─────────────────────────────────────────────


@manager_router.post("/admin/migrations")
async def purchase_start_migration(
    files: list[UploadFile] = File(...),
    sync_catalog: bool = Form(False),
    skip_orders: bool = Form(False),
    skip_stock: bool = Form(False),
    limit: int = Form(0),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """上传 8 个集合或一个 zip，暂存后自动跑 dry-run 预览。"""
    _ensure_admin(identity)
    uploads: list[tuple[str, bytes]] = []
    total = 0
    for upload in files:
        name = Path(str(upload.filename or "文件")).name or "文件"
        remaining = MAX_MIGRATION_UPLOAD_BYTES - total
        if remaining <= 0:
            raise HTTPException(
                status_code=413,
                detail=f"单次上传合计不能超过 {MAX_MIGRATION_UPLOAD_BYTES // (1024 * 1024)} MB",
            )
        data = await _read_upload_bounded(upload, name, max_bytes=remaining)
        total += len(data)
        uploads.append((name, data))
    try:
        migration = await migration_manager.stage(
            uploads,
            {
                "sync_catalog": sync_catalog,
                "skip_orders": skip_orders,
                "skip_stock": skip_stock,
                "limit": limit,
            },
        )
        try:
            migration_manager.start_preview(migration.id)
        except MigrationError:
            migration_manager.discard(migration.id)
            raise
        return {"migration": migration_manager.public(migration.id)}
    except MigrationError as exc:
        raise _migration_http_error(exc) from exc


@manager_router.get("/admin/migrations/{migration_id}")
async def purchase_get_migration(
    migration_id: str,
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    _ensure_admin(identity)
    try:
        return {"migration": migration_manager.public(migration_id)}
    except MigrationError as exc:
        raise _migration_http_error(exc) from exc


@manager_router.post("/admin/migrations/{migration_id}/preview")
async def purchase_preview_migration(
    migration_id: str,
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    _ensure_admin(identity)
    try:
        migration_manager.start_preview(migration_id)
        return {"migration": migration_manager.public(migration_id)}
    except MigrationError as exc:
        raise _migration_http_error(exc) from exc


@manager_router.post("/admin/migrations/{migration_id}/apply")
async def purchase_apply_migration(
    migration_id: str,
    payload: dict[str, Any] = Body(...),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    _ensure_admin(identity)
    if payload.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="写库前需要确认（confirm=true）")
    try:
        migration_manager.start_apply(migration_id)
        return {"migration": migration_manager.public(migration_id)}
    except MigrationError as exc:
        raise _migration_http_error(exc) from exc


@manager_router.delete("/admin/migrations/{migration_id}")
async def purchase_discard_migration(
    migration_id: str,
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    _ensure_admin(identity)
    try:
        migration_manager.discard(migration_id)
        return {"discarded": True}
    except MigrationError as exc:
        raise _migration_http_error(exc) from exc


@manager_router.get("/admin/migrations/{migration_id}/jobs/{job_id}/report")
async def purchase_migration_report(
    migration_id: str,
    job_id: str,
    identity: dict = Depends(require_any_identity_session),
) -> Response:
    _ensure_admin(identity)
    try:
        job = migration_manager.job(migration_id, job_id)
        if job.state != "done" or job.report is None:
            raise MigrationError("report_not_ready", "任务还没成功结束，报告还没生成")
        content = json.dumps(job.report, ensure_ascii=False, indent=2).encode("utf-8")
        filename = f"purchase-migration-{job.kind}-{migration_id[:8]}.json"
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except MigrationError as exc:
        raise _migration_http_error(exc) from exc


# ── xlsx ─────────────────────────────────────────────────────


def _summary_workbook(summary: dict) -> bytes:
    workbook = Workbook()
    ws = workbook.active
    ws.title = "今日采购汇总"
    ws["A1"] = "厨房采购汇总 · " + str(summary.get("date") or "")
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells("A1:J1")
    headers = [
        "供应商", "分类", "商品编码", "商品", "规格", "单位", "数量", "岗位",
        "库存快照", "下单人",
    ]
    for index, label in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=index, value=label)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDEBF7")
        cell.alignment = Alignment(horizontal="center")
    row = 4
    for supplier in summary.get("suppliers") or []:
        for category in supplier.get("categories") or []:
            for product in category.get("products") or []:
                positions = " / ".join(
                    str(item.get("position_name") or "")
                    for item in product.get("positions") or []
                )
                values = [
                    supplier.get("supplier_name"),
                    category.get("category_name"),
                    product.get("product_code"),
                    product.get("name"),
                    product.get("specification"),
                    product.get("unit"),
                    product.get("quantity"),
                    positions,
                    product.get("stock_qty"),
                    "、".join(product.get("submitters") or []),
                ]
                for index, value in enumerate(values, start=1):
                    ws.cell(row=row, column=index, value=value)
                row += 1
    ws.cell(row=row + 1, column=1, value="合计").font = Font(bold=True)
    ws.cell(row=row + 1, column=7, value=summary.get("total_quantity"))
    widths = [18, 14, 12, 22, 16, 8, 10, 18, 12, 16]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.freeze_panes = "A4"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
