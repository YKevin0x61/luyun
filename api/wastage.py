#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日常损耗登记的 HTTP 适配层。台账在 `services/wastage/store.py`，这里只管翻译。

**两个身份走同一个面**（边界层的 `identity_actor`：管理端会话或员工会话，任一有效
即放行）：

- 员工手机端（`/workbench/me/loss`）：登记、看自己的记录、存自己的模板；
- 管理端工作台（`/workbench/kitchen/loss`）：看全店、按月统计、按条件翻明细；
- **开了「损耗分析」能力（`admin_caps` 里的 `wastage`）的员工**：与店长同一面 ——
  全店统计与含「谁记的」的明细都看得到（2026-10-14 用户裁定）。没开的那一档照旧
  只看自己记的，统计 403。

区别不在路径上，而在**归属**：员工那几档的读写都带 `employee_id` 过滤（服务层的
`_scope_clause` 一处判），所以接口上根本没有「看别人的」这个参数。有能力看全店的
员工由 `WastageActor.sees_all` 带出去（边界层按能力解析，服务层只认这个标记）。
统计是另一道门：`wastage_view_all` 动作（管理端直接放行，员工要那一项能力）。

**共用电脑上两套 cookie 都在时，员工页用 `?identity=staff` 显式声明**（管理端页声明
`admin`）：后端那个「任一身份」门的默认优先级是管理端优先，不声明的话员工在员工页
记的一笔会归到共用账号名下、明细还会拿到全店。声明只选凭据、不放宽校验（形状同 WS
的 `identify_ws`），页面侧见 `WastageRegisterView.vue`；不带声明时优先级逐字不变。

**照片上传与登记是两步**（同源项目：先 `uploadFile` 拿到 fileID 数组，提交时再带上）：
`POST /images` 落盘并记一行元数据，返回 `capture_id`；提交记录时把它放进 `image_ids`。
未绑定的图是孤儿行（`record_id IS NULL`），留着可查、可清。

**更正**：`PATCH /records/{id}` 改字段、`DELETE /records/{id}` 撤回，都只对「本人 24
小时内记的那一笔」生效（归属与窗口在服务层判，这里只翻译 403 / 409）；照片不可改不可
增删，PATCH body 里的 `image_ids` 被忽略。

**不发实时 nudge**：损耗是「记一笔、事后看」的事，没有「另一台设备上有人等着它」的
那种紧迫；工作台那个 topic 白名单（`services/realtime/hub.py` 的 `VALID_TOPICS` 与员工
那三档）也不该为它多开一格。页面自己提交后刷新，别人记的那几笔靠页面上的刷新。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel

from api.workbench_boundary import (
    WorkbenchActor,
    WorkbenchErrors,
    identity_actor,
    require_action,
)
from config import settings
from database import get_db
from db_core.utils import CHINA_TZ
from services.hygiene.images import sniff_image_content_type
from services.wastage.store import (
    AMEND_WINDOW_HOURS,
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
        "migrations/pg/ 下还没应用的脚本（0029_wastage.sql / "
        "0046_wastage_amendments.sql / 0049_prep_review_signals.sql），然后刷新本页"
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
    "invalid_prep_item": "备货品选得不对：刷新页面重选，或留空不选",
    "unknown_prep_item": "这个备货品不存在：刷新页面重选，或留空不选",
    "images_unavailable": "照片服务还没就绪：请稍后重试，或先不带照片提交",
    "invalid_month": "月份格式应该是 YYYY-MM",
    "missing_range": "统计要给出月份或起止日期",
    "invalid_range": "结束日期不能早于开始日期",
    "too_many_templates": "最多存 {} 个常用模板：先删掉不用的再来存",
    "template_exists": "这个模板已经存过了",
    "unknown_template": "这个模板不存在：可能已经被删掉了，刷新看看",
    "unknown_record": "找不到这条登记：可能已经撤回了",
    "not_owner": "只能改自己记的那几条",
    "amend_window_closed": "超过 24 小时了，这一笔不能再改；如果记错了，另记一笔说明",
}

_ERROR_STATUS = {
    "unknown_template": 404,
    "unknown_image": 404,
    # 撤回过的登记对读接口就是「不存在」，改它也是同一个 404。
    "unknown_record": 404,
    # 共享例外表把 `unknown_*` 通配成 404；这个 code 在损耗这边一直是 **400** —— 错的
    # 是「选了一个不存在的备货品」，不是「要改的那条记录没了」。
    "unknown_prep_item": 400,
    # `not_owner`（403）、`amend_window_closed`（409）、`not_migrated` 与
    # `images_unavailable`（503）由共享例外表提供，本域不再复写。
}

# 本域唯一的业务错误出口（票 04）：文案表与状态覆盖仍是原来那两份数据，边界只多做
# 一件事 —— 每个由这里翻出来的拒绝都带 `X-Error-Code: <code>`。
_WASTAGE_ERRORS = WorkbenchErrors(
    domain="损耗登记请求",
    texts=_ERROR_DETAILS,
    status_overrides=_ERROR_STATUS,
    default_text="损耗登记参数不合法",
)

# 12 条里唯一判动作的那条（ADR 0134）：管理端放行，员工要「损耗分析」能力。
_wastage_view_all = require_action("wastage_view_all")


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
    # 可选的「对应备货品」：挂了之后复盘算多备代价时按 id 对齐（见 0049 的注释）。
    prep_item_id: Optional[int] = None


class TemplateRequest(BaseModel):
    item_name: Optional[str] = None
    unit: Optional[str] = None
    reason: Optional[str] = None


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
        # 更正窗口由服务端给：前端文案里的「24 小时」不该是另一份写死的数字。
        "amend_window_hours": AMEND_WINDOW_HOURS,
    }


@router.get("/meta")
async def read_meta(
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(identity_actor),
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
    actor: WorkbenchActor = Depends(identity_actor),
) -> dict:
    """登记一笔损耗。谁登记的就记在谁名下（员工看得到自己的，管理端看全店）。"""
    store = _store(db)
    try:
        record = await store.add_record(
            WastageActor.from_actor(actor),
            item_name=payload.item_name,
            quantity=payload.quantity,
            unit=payload.unit,
            reason=payload.reason,
            remark=payload.remark,
            wastage_date=payload.wastage_date,
            image_ids=payload.image_ids,
            prep_item_id=payload.prep_item_id,
        )
    except WastageError as exc:
        raise _WASTAGE_ERRORS.http(exc) from exc
    return {"record": record}


@router.patch("/records/{record_id}")
async def amend_record(
    record_id: int,
    payload: RecordRequest,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(identity_actor),
) -> dict:
    """更正本人 24 小时内记的那一笔：只改传了值的字段，照片不参与（body 里的
    `image_ids` 会被忽略 —— 照片不可换，见服务层）。

    归属与窗口都在服务层判：不是本人的 403、过了 24 小时的 409。这两个错分开，
    前端才知道该提示「找管理员」还是「另记一笔」。响应带回整条记录（新值 +
    `can_amend` / `amend_deadline_at`）。
    """
    store = _store(db)
    try:
        record = await store.amend_record(
            WastageActor.from_actor(actor),
            record_id,
            item_name=payload.item_name,
            quantity=payload.quantity,
            unit=payload.unit,
            reason=payload.reason,
            remark=payload.remark,
            wastage_date=payload.wastage_date,
        )
    except WastageError as exc:
        raise _WASTAGE_ERRORS.http(exc) from exc
    return {"record": record}


@router.delete("/records/{record_id}")
async def revoke_record(
    record_id: int,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(identity_actor),
) -> dict:
    """撤回本人 24 小时内记的那一笔（软删）：读接口从此看不到它，修订里留整行快照。"""
    store = _store(db)
    try:
        record = await store.revoke_record(WastageActor.from_actor(actor), record_id)
    except WastageError as exc:
        raise _WASTAGE_ERRORS.http(exc) from exc
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
    actor: WorkbenchActor = Depends(identity_actor),
) -> dict:
    """明细，新的在前。员工拿得到自己那几笔；开了「损耗分析」能力的员工与店长一样看全店
    （归属过滤在服务层的 `_scope_clause` 一处判）。

    响应另带 `counts`（归属口径的今天 / 昨天笔数，与筛选无关）与每条的 `edit_count`
    （改过几次）—— 登记页那行状态与明细上的「改」角标直接读，不在前端另算。
    """
    store = _store(db)
    try:
        data = await store.list_records(
            WastageActor.from_actor(actor),
            start_date=start_date,
            end_date=end_date,
            reason=reason,
            keyword=keyword,
            offset=offset,
            limit=limit,
        )
    except WastageError as exc:
        raise _WASTAGE_ERRORS.http(exc) from exc
    return data


@router.get("/statistics")
async def read_statistics(
    month: Optional[str] = Query(None, description="YYYY-MM；不给就看 start/end"),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(_wastage_view_all),
) -> dict:
    """按月（或一段日期）的汇总：总笔数、物料种类、原因分布、物料排行、逐日趋势，
    外加 `revoked_count` / `edited_count`（范围内撤回过、改过的笔数，空范围是 0 不是 None）。

    门是 `wastage_view_all`：管理端放行，员工要开了「损耗分析」能力。
    """
    store = _store(db)
    try:
        data = await store.statistics(
            month=month, start_date=start_date, end_date=end_date
        )
    except WastageError as exc:
        raise _WASTAGE_ERRORS.http(exc) from exc
    return {**data, "month": month or None, "meta": _meta()}


@router.get("/suggestions")
async def read_suggestions(
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(identity_actor),
) -> dict:
    """物料名称的历史建议（填名字时的补全）。员工看自己近 30 天的，管理端看全店。"""
    store = _store(db)
    return {"suggestions": await store.suggestions(WastageActor.from_actor(actor))}


@router.get("/templates")
async def read_templates(
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(identity_actor),
) -> dict:
    """常用模板：我存过的 + 我最近常这么记的组合。"""
    store = _store(db)
    data = await store.templates(WastageActor.from_actor(actor))
    return {**data, "max_templates": MAX_TEMPLATES}


@router.post("/templates")
async def create_template(
    payload: TemplateRequest,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(identity_actor),
) -> dict:
    """把「物料 + 单位 + 原因」存成常用模板（数量留空，登记时再填）。"""
    store = _store(db)
    try:
        template = await store.save_template(
            WastageActor.from_actor(actor),
            item_name=payload.item_name,
            unit=payload.unit,
            reason=payload.reason,
        )
    except WastageError as exc:
        raise _WASTAGE_ERRORS.http(exc) from exc
    return {"template": template}


@router.delete("/templates/{template_id}")
async def remove_template(
    template_id: int,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(identity_actor),
) -> dict:
    """删一个自己存的模板（别人的模板对你是「不存在」）。"""
    store = _store(db)
    try:
        await store.delete_template(WastageActor.from_actor(actor), template_id)
    except WastageError as exc:
        raise _WASTAGE_ERRORS.http(exc) from exc
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


# iPhone 相机默认存 HEIC / HEIF（`....ftyp<brand>`），PIL 解码不了、除 Safari 外的浏览器
# 也显示不了。它不是「坏图」，而是这台机器**存不了**的格式 —— 所以单独认出来、单独说一句
# 能照做的话（用页面里的相机拍，出来的就是 JPEG）；笼统的「换个 JPG」对 iPhone 用户等于
# 没说（2026-10-17 真机撞的就是这个）。
_HEIF_BRANDS = (
    b"heic",
    b"heix",
    b"hevc",
    b"heim",
    b"heis",
    b"hevm",
    b"hevs",
    b"mif1",
    b"msf1",
    b"avif",
)


def _looks_like_heif(data: bytes) -> bool:
    return len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS


@router.post("/images")
async def upload_image(
    file: UploadFile = File(...),
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(identity_actor),
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
        if _looks_like_heif(data):
            raise HTTPException(
                status_code=400,
                detail=(
                    "这是 iPhone 相机默认的 HEIC 格式，网页这边存不了："
                    "请点页面里的「拍一张」直接拍（拍好的就是 JPG）"
                ),
            )
        raise HTTPException(status_code=400, detail="这不是一张能用的图片：请重新拍一张")
    store = _store(db)
    try:
        image = await store.add_image(WastageActor.from_actor(actor), data, content_type)
    except WastageError as exc:
        raise _WASTAGE_ERRORS.http(exc) from exc
    return {"image": image}


@router.get("/images/{capture_id}")
async def read_image(
    capture_id: str,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(identity_actor),
) -> Response:
    """读一张照片。员工只读得到自己传的（开了「损耗分析」能力的员工看全部）；
    读不到一律 404（不区分「没有」与「不是你的」）。"""
    store = _store(db)
    found = await store.image_bytes(WastageActor.from_actor(actor), capture_id)
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
