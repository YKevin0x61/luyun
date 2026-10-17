#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日常损耗登记的 HTTP 适配层。台账在 `services/wastage/store.py`，这里只管翻译。

**两个身份走同一个面**（`require_any_identity_session`：管理端会话或员工会话）：

- 员工手机端（`/workbench/me/loss`）：登记、看自己的记录、存自己的模板；
- 管理端工作台（`/workbench/kitchen/loss`）：看全店、按月统计、按条件翻明细。

区别不在路径上，而在**归属**：员工那几档的读写都带 `employee_id` 过滤（服务层的
`_scope_clause` 一处判），所以接口上根本没有「看别人的」这个参数。统计是唯一的例外
——它只给管理端（`403`），因为「这个月店里损耗了多少」不是员工要关心的数字。

**照片上传与登记是两步**（同源项目：先 `uploadFile` 拿到 fileID 数组，提交时再带上）：
`POST /images` 落盘并记一行元数据，返回 `capture_id`；提交记录时把它放进 `image_ids`。
未绑定的图是孤儿行（`record_id IS NULL`），留着可查、可清。

**不发实时 nudge**：损耗是「记一笔、事后看」的事，没有「另一台设备上有人等着它」的
那种紧迫；工作台那个 topic 白名单（`services/realtime/hub.py` 的 `VALID_TOPICS` 与员工
那三档）也不该为它多开一格。页面自己提交后刷新，别人记的那几笔靠页面上的刷新。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel

from api.security import require_any_identity_session
from config import settings
from database import get_db
from db_core.utils import CHINA_TZ
from services.hygiene.images import sniff_image_content_type
from services.wastage.store import (
    KIND_ADMIN,
    KIND_STAFF,
    MAX_IMAGES,
    MAX_ITEM_NAME,
    MAX_QUANTITY,
    MAX_REMARK,
    MAX_TEMPLATES,
    MAX_UNIT,
    REASONS,
    REASON_ORDER,
    UNITS,
    WastageActor,
    WastageError,
    WastageStore,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wastage", tags=["损耗登记"])

# 单张照片上限。与卫生现场照片同一把尺（`services/hygiene/work.py` 的
# `MAX_STANDARD_BYTES`）：两个模块各写一个数就会漂，而它们受的是同一个反向代理
# （`client_max_body_size 512m`）与同一类手机相机。
MAX_IMAGE_BYTES = 20 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024

# 服务层的 `WastageError.code` 是稳定标识，不是给店员看的文案。每个 code 翻成一句
# 能照着改的话 —— 「参数不合法」会把「数量打错了」和「这张图不是你的」说成同一件事。
_ERROR_DETAILS = {
    "not_migrated": (
        "损耗登记表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
        "migrations/pg/0029_wastage.sql，然后刷新本页"
    ),
    "invalid_date": "日期格式应该是 YYYY-MM-DD：请重新选一天",
    "future_day": "还没到的日子登不了：损耗记的是已经发生的事",
    "missing_item_name": "请填物料名称：这样的是什么坏了",
    "item_name_too_long": "物料名称最多 {} 个字：请缩短一点",
    "missing_unit": "请选或填一个单位：数量是多少「份 / kg / 个」",
    "unit_too_long": "单位最多 {} 个字",
    "invalid_reason": "请选一个损耗原因",
    "invalid_quantity": "数量要填数字：例如 1、0.5、2.25",
    "quantity_not_positive": "数量要大于 0：没坏东西就不用记这一笔",
    "quantity_too_precise": "数量最多三位小数：例如 0.5、1.25",
    "quantity_too_large": "数量最多 {}：超过了请分成两笔",
    "missing_remark": "选了「其他」要写一句说明：不然统计里看不出是什么情况",
    "remark_too_long": "备注最多 {} 个字：请缩短一点",
    "too_many_images": "现场照片最多 {} 张",
    "unknown_image": "有一张照片没传上去：请删掉它重新上传",
    "images_unavailable": "照片服务还没就绪：请稍后重试，或先不带照片提交",
    "invalid_month": "月份格式应该是 YYYY-MM",
    "missing_range": "统计要给出月份或起止日期",
    "invalid_range": "结束日期不能早于开始日期",
    "too_many_templates": "最多存 {} 个常用模板：先删掉不用的再来存",
    "template_exists": "这个模板已经存过了",
    "unknown_template": "这个模板不存在：可能已经被删掉了，刷新看看",
}

_ERROR_STATUS = {
    "unknown_template": 404,
    "unknown_image": 404,
    # `not_migrated` 不是「参数写错了」，是这台机器还没升级完 —— 503 比 400 诚实。
    "not_migrated": 503,
    "images_unavailable": 503,
}


class RecordRequest(BaseModel):
    """登记一笔。字段都给了默认值：缺字段落到服务层那道闸上、回一句中文 400，
    而不是 pydantic 的英文 422（同加班的 `EntryRequest`）。
    """

    item_name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    reason: Optional[str] = None
    remark: Optional[str] = None
    wastage_date: Optional[str] = None
    # `image_ids` 收的是 `POST /images` 回来的 `capture_id`，不是自增主键 ——
    # 文件名才是磁盘上真正的那份东西。
    image_ids: Optional[list] = None


class TemplateRequest(BaseModel):
    item_name: Optional[str] = None
    unit: Optional[str] = None
    reason: Optional[str] = None


def _bad_request(exc: WastageError) -> HTTPException:
    detail = _ERROR_DETAILS.get(exc.code, "损耗登记参数不合法")
    if "{}" in detail:
        raw = exc.args[0] if exc.args else ""
        if not raw or raw == exc.code:
            logger.warning("损耗登记错误缺细节，用「？」占位: code=%s", exc.code)
        detail = detail.replace("{}", raw if raw and raw != exc.code else "？")
    logger.info("损耗登记请求被拒: code=%s", exc.code)
    return HTTPException(status_code=_ERROR_STATUS.get(exc.code, 400), detail=detail)


def _actor(identity: dict) -> WastageActor:
    """两种会话 → 台账层的 actor。归属（谁能看谁记的）由它带出去。"""
    if identity.get("kind") == "staff":
        employee = identity.get("employee") or {}
        return WastageActor(
            kind=KIND_STAFF,
            employee_id=int(employee["id"]),
            name=str(employee.get("name") or ""),
        )
    # 管理端那台共享账号：没有员工号，名字写「管理端」（明细里「谁登记的」那一列）。
    return WastageActor(kind=KIND_ADMIN, employee_id=None, name="管理端")


def _require_admin(identity: dict) -> None:
    """统计只给管理端。员工拿到的是 403「你没这个权限」，不是 404 —— 后者会把
    「权限不够」说成「数据没了」。"""
    if identity.get("kind") != "admin":
        raise HTTPException(status_code=403, detail="只有店长能看损耗统计")


_capture_store = None


def _captures():
    """照片的落盘目录（第一次用时才建目录）。与卫生那批分开放，清理时互不牵连。"""
    global _capture_store
    if _capture_store is None:
        from services.hygiene.captures import FileCaptureStore

        _capture_store = FileCaptureStore(
            Path(settings.DATABASE_DIR) / "wastage-captures"
        )
    return _capture_store


def _store(db) -> WastageStore:
    return WastageStore(db, captures=_captures())


def _meta() -> dict:
    return {
        "reasons": [{"value": key, "label": REASONS[key]} for key in REASON_ORDER],
        "units": list(UNITS),
        "max_images": MAX_IMAGES,
        "max_item_name": MAX_ITEM_NAME,
        "max_unit": MAX_UNIT,
        "max_remark": MAX_REMARK,
        "max_quantity": float(MAX_QUANTITY),
        "max_templates": MAX_TEMPLATES,
        "max_image_bytes": MAX_IMAGE_BYTES,
    }


@router.get("/meta")
async def read_meta(
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """登记页要的那几份清单与上限：原因六选一、常用单位、今天与上限。

    一次给全，前端不再各写一份 —— 上限（50 / 200 / 3 / 20）在服务端是唯一出处，
    页面文案里出现「最多 3 张」时那个 3 是从这里来的。`today` 走台账那一刻钟
    （不是路由里另取一次系统时间）：日期选择器的上界与「未来日期不收」必须是同一天。
    """
    return {"meta": _meta(), "today": WastageStore(db).today()}


@router.post("/records")
async def create_record(
    payload: RecordRequest,
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """登记一笔损耗。谁登记的就记在谁名下（员工看得到自己的，管理端看全店）。"""
    store = _store(db)
    try:
        record = await store.add_record(
            _actor(identity),
            item_name=payload.item_name,
            quantity=payload.quantity,
            unit=payload.unit,
            reason=payload.reason,
            remark=payload.remark,
            wastage_date=payload.wastage_date,
            image_ids=payload.image_ids,
        )
    except WastageError as exc:
        raise _bad_request(exc) from exc
    return {"record": record}


@router.get("/records")
async def list_records(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    reason: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """明细，新的在前。员工只拿得到自己那几笔（归属过滤在服务层）。"""
    store = _store(db)
    try:
        data = await store.list_records(
            _actor(identity),
            start_date=start_date,
            end_date=end_date,
            reason=reason,
            keyword=keyword,
            offset=offset,
            limit=limit,
        )
    except WastageError as exc:
        raise _bad_request(exc) from exc
    return data


@router.get("/statistics")
async def read_statistics(
    month: Optional[str] = Query(None, description="YYYY-MM；不给就看 start/end"),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """按月（或一段日期）的汇总：总笔数、物料种类、原因分布、物料排行、逐日趋势。"""
    _require_admin(identity)
    store = _store(db)
    try:
        data = await store.statistics(
            month=month, start_date=start_date, end_date=end_date
        )
    except WastageError as exc:
        raise _bad_request(exc) from exc
    return {**data, "month": month or None, "meta": _meta()}


@router.get("/suggestions")
async def read_suggestions(
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """物料名称的历史建议（填名字时的补全）。员工看自己近 30 天的，管理端看全店。"""
    store = _store(db)
    return {"suggestions": await store.suggestions(_actor(identity))}


@router.get("/templates")
async def read_templates(
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """常用模板：我存过的 + 我最近常这么记的组合。"""
    store = _store(db)
    data = await store.templates(_actor(identity))
    return {**data, "max_templates": MAX_TEMPLATES}


@router.post("/templates")
async def create_template(
    payload: TemplateRequest,
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """把「物料 + 单位 + 原因」存成常用模板（数量留空，登记时再填）。"""
    store = _store(db)
    try:
        template = await store.save_template(
            _actor(identity),
            item_name=payload.item_name,
            unit=payload.unit,
            reason=payload.reason,
        )
    except WastageError as exc:
        raise _bad_request(exc) from exc
    return {"template": template}


@router.delete("/templates/{template_id}")
async def remove_template(
    template_id: int,
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """删一个自己存的模板（别人的模板对你是「不存在」）。"""
    store = _store(db)
    try:
        await store.delete_template(_actor(identity), template_id)
    except WastageError as exc:
        raise _bad_request(exc) from exc
    return {"ok": True}


async def _read_upload_bounded(file: UploadFile, label: str) -> bytes:
    declared = getattr(file, "size", None)
    if declared is not None and declared > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=_too_large_detail(label))
    data = bytearray()
    while True:
        chunk = await file.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail=_too_large_detail(label))
    return bytes(data)


def _too_large_detail(label: str) -> str:
    limit_mb = max(1, MAX_IMAGE_BYTES // (1024 * 1024))
    return f"{label}不能超过 {limit_mb} MB"


@router.post("/images")
async def upload_image(
    file: UploadFile = File(...),
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """传一张现场照片，返回它的 `capture_id`（提交记录时带上）。

    类型按**字节内容**识别，不信客户端的 Content-Type：只信它，`image/svg+xml` 里的
    `<script>` 就会跟着同源响应头执行（存储型 XSS），同卫生那条的判据。
    """
    data = await _read_upload_bounded(file, "照片")
    if not data:
        raise HTTPException(status_code=400, detail="照片是空的：请重拍或重选一张")
    content_type = sniff_image_content_type(data)
    if content_type is None:
        raise HTTPException(status_code=400, detail="这不是一张能用的图片：请换 JPG / PNG 再试")
    store = _store(db)
    try:
        image = await store.add_image(_actor(identity), data, content_type)
    except WastageError as exc:
        raise _bad_request(exc) from exc
    return {"image": image}


@router.get("/images/{capture_id}")
async def read_image(
    capture_id: str,
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> Response:
    """读一张照片。员工只读得到自己传的；读不到一律 404（不区分「没有」与「不是你的」）。"""
    store = _store(db)
    found = await store.image_bytes(_actor(identity), capture_id)
    if found is None:
        raise HTTPException(status_code=404, detail="照片不存在")
    data, content_type = found
    return Response(
        content=data,
        media_type=content_type,
        headers={
            # 内容是内容寻址的（capture_id 唯一），可以放心让浏览器缓存一会儿；
            # `private` 是因为它带鉴权，不该进任何共享缓存。
            "Cache-Control": "private, max-age=86400",
        },
    )
