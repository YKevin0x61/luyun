#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作台「任务」的 HTTP 适配层。

票 01（地基）先立骨架：照片上传 / 读取是真的，任务发布与验收的端点先过门、
回 501（下一张票 02/04 接实现）。这样「谁能进这扇门」从第一天起就按真判据跑，
后面的票只往门后填业务，不重写权限。

**两种身份走同一个面**（`require_any_identity_session`）：

- 管理端共享账号（`admin_user`）：在电脑上发布、验收、改派；**不提交照片** ——
  提交只认员工会话（ADR 0132：执行人只能是花名册里的真人）。
- 员工会话：在员工端「我的任务」里做任务、拍照提交；花名册上勾了
  「任务管理」（`admin_caps` 里的 `task`）的人还能发布 / 验收 / 改派。

**发布 / 验收 / 改派 / 停用的门** `_require_task_manager` 与排班、损耗那两道同形：
管理端会话直接放行；员工看 `admin_caps` 里有没有 `CAP_TASK` 现读一场，没有就 403。
页面那一侧有同一份能力判据（`admin-web/src/utils/adminCaps.js` 的 `staffSide`），
但**页面能不能打开与接口能不能调是两件事**，两边都判（与 `staffCap` 的既有口径一致）。

**照片两步**（同损耗 / 卫生）：`POST /images` 落盘并写一行台账，返回 `capture_id`；
提交任务时再把这些 id 绑到提交上（票 03）。完成照必须带 `live=true`（页面的实时
取景框才会带）；服务端没有设备指纹，这个标记**只是客户端声明**，挡的是「页面里
明明在拍照却从相册选」那类误操作，不是安全边界（spec 的「诚实的风险」那一段）。
参考图（发布任务时选的那张）允许相册。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)

from api.security import require_any_identity_session, require_staff_session
from config import settings
from database import get_db
from services.hygiene.captures import FileCaptureStore
from services.hygiene.images import sniff_image_content_type
from services.identity.capabilities import CAP_TASK, has_cap
from services.realtime.hub import realtime_hub
from pydantic import BaseModel

from services.tasks.service import (
    TaskConflictError,
    TaskNotFoundError,
    TaskPermissionError,
    TaskService,
    TaskValidationError,
)
from services.tasks.store import (
    PURPOSE_SUBMISSION,
    PURPOSES,
    TaskActor,
    TaskStore,
    TaskStoreError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["工作任务"])

# 单张照片上限：与卫生现场照、损耗照同一把尺（同一个反向代理、同一类手机相机）。
MAX_IMAGE_BYTES = 20 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024

#: 任务表还没迁移时的提示：点名脚本，店员能照着找「系统更新 → 数据库迁移」。
_NOT_MIGRATED_DETAIL = (
    "任务表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
    "migrations/pg/0054_work_tasks.sql，然后刷新本页"
)

_ERROR_DETAILS = {
    "not_migrated": _NOT_MIGRATED_DETAIL,
    "images_unavailable": "照片服务还没就绪：请稍后重试，或先不带照片提交",
}

_capture_store: Optional[FileCaptureStore] = None


def _captures() -> FileCaptureStore:
    """照片落盘目录与卫生那批分开（`data/task-captures`）：清理、备份、迁移互不牵连。"""
    global _capture_store
    if _capture_store is None:
        _capture_store = FileCaptureStore(Path(settings.DATABASE_DIR) / "task-captures")
    return _capture_store


def _store(db) -> TaskStore:
    return TaskStore(db, captures=_captures())


def _actor(identity: dict) -> TaskActor:
    """两种会话 → 操作人。管理端共享账号没有员工号，名字写「管理端」。"""
    if identity.get("kind") == "staff":
        employee = identity.get("employee") or {}
        return TaskActor(
            kind="staff",
            employee_id=int(employee["id"]),
            name=str(employee.get("name") or ""),
        )
    return TaskActor(kind="admin", employee_id=None, name="管理端")


def _require_task_manager(identity: dict = Depends(require_any_identity_session)) -> dict:
    """发布 / 验收 / 改派 / 停用的门：管理端，或开了「任务管理」能力的员工。

    员工拿到 403「你没这个权限」，不是 404 —— 后者会把「权限不够」说成「任务没了」。
    能力每次请求现读，收回后下一次请求就 403。
    """
    if identity.get("kind") == "admin":
        return identity
    employee = identity.get("employee") or {}
    if not has_cap(employee.get("admin_caps"), CAP_TASK):
        raise HTTPException(
            status_code=403,
            detail="只有开了「任务管理」权限的人能发布或验收任务：请找超级管理员在花名册里开通",
        )
    return identity


def _http_error(exc: TaskStoreError) -> HTTPException:
    status = 503 if exc.code in {"not_migrated", "images_unavailable"} else 400
    return HTTPException(
        status_code=status,
        detail=_ERROR_DETAILS.get(exc.code, "任务操作没成功：请刷新重试"),
    )


def _task_http_error(exc: Exception) -> HTTPException:
    """服务层的四类异常 → HTTP。文案已经在服务层写成人话，这里不再改。"""
    if isinstance(exc, TaskValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, TaskConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, TaskPermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, TaskNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, TaskStoreError):
        return _http_error(exc)
    return HTTPException(status_code=400, detail="任务操作没成功：请刷新重试")


def _service(db) -> TaskService:
    return TaskService(db)


def _too_large_detail() -> str:
    return f"照片不能超过 {max(1, MAX_IMAGE_BYTES // (1024 * 1024))} MB"


async def _read_upload_bounded(file: UploadFile) -> bytes:
    declared = getattr(file, "size", None)
    if declared is not None and declared > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=_too_large_detail())
    data = bytearray()
    while True:
        chunk = await file.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail=_too_large_detail())
    return bytes(data)


# iPhone 相机默认存 HEIC / HEIF（`....ftyp<brand>`），网页存不了、PIL 也解不了。
# 单独认出来、单独说一句能照做的话（用页面里的相机拍，出来的就是 JPEG）。
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


# ── 入参 ────────────────────────────────────────────────────────────────────
class TaskAssigneeIn(BaseModel):
    kind: str = ""
    employee_ids: list[int] = []
    pool_kind: str | None = None
    zone_id: int | None = None


class TaskDefinitionIn(BaseModel):
    kind: str = "once"
    title: str = ""
    body: str = ""
    photo_count: int = 1
    reference_capture_id: str | None = None
    assignee: TaskAssigneeIn = TaskAssigneeIn()
    due_date: str = ""
    deadline_hhmm: str = ""
    frequency_kind: str = ""
    weekday: int | None = None
    month_day: int | None = None
    start_date: str = ""
    end_date: str | None = None
    wecom_group_push: bool = False


class TaskSubmitIn(BaseModel):
    capture_ids: list[str] = []


class TaskRejectIn(BaseModel):
    reason: str = ""


# ── 照片 ────────────────────────────────────────────────────────────────────
async def _task_nudge(reason: str) -> None:
    """任务动了就敲一下（nudge 不带数据，客户端自己重拉）。

    统一发**全店级**（scope 不带 employee_id）：发布 / 验收要让执行人、发布者、验收人
    都看到；员工连接对全店级事件是放行的（ADR 0131 已裁定这层可接受 —— 任务本身不加密，
    员工端也只渲染跟自己有关的那几件）。
    """
    await realtime_hub.broadcast_nudge("tasks", {"reason": reason})


@router.post("/images")
async def upload_task_image(
    file: UploadFile = File(...),
    purpose: str = Form(PURPOSE_SUBMISSION),
    live: bool = Form(False),
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> dict:
    """传一张任务照片，返回 `capture_id`（提交 / 发布时再带上）。

    `purpose=submission`（完成照）要求 `live=true`：页面里的实时取景框拍照时才带，
    从相册选的会被页面自己拦住，服务端这一道兜误用。`purpose=reference`（参考图）
    允许相册。类型按**字节内容**识别，不信客户端 `Content-Type`（防 SVG 存储型 XSS，
    同卫生 / 损耗那条判据）。
    """
    purpose = str(purpose or "").strip().lower()
    if purpose not in PURPOSES:
        raise HTTPException(
            status_code=400,
            detail="照片用途不对：只接受 submission（完成照）或 reference（参考图）",
        )
    if purpose == PURPOSE_SUBMISSION and not live:
        raise HTTPException(
            status_code=400,
            detail="完成照必须现场拍：请用页面里的相机拍一张（不能从相册选）",
        )
    data = await _read_upload_bounded(file)
    if not data:
        raise HTTPException(status_code=400, detail="照片是空的：请重拍一张")
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
    try:
        image = await _store(db).add_image(_actor(identity), data, content_type, purpose)
    except TaskStoreError as exc:
        raise _http_error(exc) from exc
    return {"image": image}


@router.get("/images/{capture_id}")
async def read_task_image(
    capture_id: str,
    db=Depends(get_db),
    identity: dict = Depends(require_any_identity_session),
) -> Response:
    """读一张任务照片。

    已绑定到任务上的（完成照 / 参考图）任何有会话的人都能看；未绑定的只有上传者
    自己能看（上传后预览、提交失败重试）。读不到一律 404，不区分「没有」与「不是你的」。
    """
    try:
        found = await _store(db).image_bytes(_actor(identity), capture_id)
    except TaskStoreError as exc:
        raise _http_error(exc) from exc
    if found is None:
        raise HTTPException(status_code=404, detail="照片不存在")
    data, content_type = found
    return Response(
        content=data,
        media_type=content_type,
        headers={
            # 内容按 capture_id 寻址，可以缓存一会儿；`private` 是因为它带鉴权。
            "Cache-Control": "private, max-age=86400",
        },
    )


# ── 发布表单的数据源 ────────────────────────────────────────────────────────
@router.get("/assignees")
async def read_task_assignees(
    identity: dict = Depends(_require_task_manager),
    db=Depends(get_db),
) -> dict:
    """发布表单用：在职员工 + 工作区名单 + 今天的日期（前端按它校验截止）。"""
    try:
        return await _service(db).assignees_catalog()
    except TaskStoreError as exc:
        raise _http_error(exc) from exc


# ── 任务定义 / 板 ──────────────────────────────────────────────────────────
@router.get("/definitions")
async def list_task_definitions(
    identity: dict = Depends(_require_task_manager),
    db=Depends(get_db),
) -> dict:
    """定期任务列表（管理端「定期任务」页用，带近 7 期完成情况）。"""
    try:
        return await _service(db).recurring_definitions()
    except TaskStoreError as exc:
        raise _http_error(exc) from exc


@router.post("/definitions")
async def publish_task_definition(
    payload: TaskDefinitionIn,
    identity: dict = Depends(_require_task_manager),
    db=Depends(get_db),
) -> dict:
    """发布任务：即时任务写定义 + 当期实例快照；定期任务写定义并物化近期实例。"""
    try:
        result = await _service(db).create_definition(_actor(identity), payload.model_dump())
    except (TaskValidationError, TaskConflictError, TaskPermissionError, TaskNotFoundError, TaskStoreError) as exc:
        raise _task_http_error(exc) from exc
    await _task_nudge("published")
    return result


@router.patch("/definitions/{definition_id}")
async def update_task_definition(
    definition_id: int,
    payload: TaskDefinitionIn,
    identity: dict = Depends(_require_task_manager),
    db=Depends(get_db),
) -> dict:
    """改任务：即时任务同步当期实例快照（已有人提交 → 409）；定期任务只改定义，不动已生成实例。"""
    try:
        result = await _service(db).update_definition(
            _actor(identity), definition_id, payload.model_dump()
        )
    except (TaskValidationError, TaskConflictError, TaskPermissionError, TaskNotFoundError, TaskStoreError) as exc:
        raise _task_http_error(exc) from exc
    await _task_nudge("updated")
    return result


@router.post("/definitions/{definition_id}/stop")
async def stop_task_definition(
    definition_id: int,
    identity: dict = Depends(_require_task_manager),
    db=Depends(get_db),
) -> dict:
    """停用定期任务：以后不再生成新期，历史实例保留。"""
    try:
        result = await _service(db).stop_definition(definition_id)
    except (TaskValidationError, TaskConflictError, TaskPermissionError, TaskNotFoundError, TaskStoreError) as exc:
        raise _task_http_error(exc) from exc
    await _task_nudge("stopped")
    return result


@router.get("/board")
async def read_task_board(
    counts_only: bool = Query(False),
    identity: dict = Depends(_require_task_manager),
    db=Depends(get_db),
) -> dict:
    """管理端「任务」板：进行中 / 待验收 / 已结束三段 + 计数。

    `counts_only=true` 只回计数（首页卡片 / 底栏角标用），省掉整页序列化。
    """
    try:
        return await _service(db).board(_actor(identity), counts_only=counts_only)
    except TaskStoreError as exc:
        raise _http_error(exc) from exc


@router.get("/mine")
async def read_my_tasks(
    employee: dict = Depends(require_staff_session),
    db=Depends(get_db),
) -> dict:
    """员工端「我的任务」：待我做 / 我发布的（+ 有 `task` 能力时的待我验收）。"""
    include_review = has_cap(employee.get("admin_caps"), CAP_TASK)
    try:
        return await _service(db).mine(employee, include_review=include_review)
    except TaskStoreError as exc:
        raise _http_error(exc) from exc


@router.get("/instances/{instance_id}")
async def read_task_instance(
    instance_id: int,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
) -> dict:
    """任务详情 + 提交历史；无权看的人一律 404（不拿 403 探别人有什么）。"""
    employee = identity.get("employee") or {}
    can_manage = identity.get("kind") == "admin" or has_cap(employee.get("admin_caps"), CAP_TASK)
    try:
        return await _service(db).instance_detail(_actor(identity), instance_id, can_manage=can_manage)
    except (TaskValidationError, TaskConflictError, TaskPermissionError, TaskNotFoundError, TaskStoreError) as exc:
        raise _task_http_error(exc) from exc


@router.post("/instances/{instance_id}/submit")
async def submit_task_instance(
    instance_id: int,
    payload: TaskSubmitIn,
    employee: dict = Depends(require_staff_session),
    db=Depends(get_db),
) -> dict:
    """员工提交完成照（组 / 池任务先交为准，逾期仍可交并标 late）。"""
    try:
        result = await _service(db).submit_instance(employee, instance_id, payload.capture_ids)
    except (TaskValidationError, TaskConflictError, TaskPermissionError, TaskNotFoundError, TaskStoreError) as exc:
        raise _task_http_error(exc) from exc
    await _task_nudge("submitted")
    return result


@router.post("/instances/{instance_id}/accept")
async def accept_task_instance(
    instance_id: int,
    identity: dict = Depends(_require_task_manager),
    db=Depends(get_db),
) -> dict:
    """验收通过（不能验自己提交的）。"""
    try:
        result = await _service(db).accept_instance(_actor(identity), instance_id)
    except (TaskValidationError, TaskConflictError, TaskPermissionError, TaskNotFoundError, TaskStoreError) as exc:
        raise _task_http_error(exc) from exc
    await _task_nudge("accepted")
    return result


@router.post("/instances/{instance_id}/reject")
async def reject_task_instance(
    instance_id: int,
    payload: TaskRejectIn,
    identity: dict = Depends(_require_task_manager),
    db=Depends(get_db),
) -> dict:
    """驳回（理由必填，回待交、保留本次提交历史）。"""
    try:
        result = await _service(db).reject_instance(_actor(identity), instance_id, payload.reason)
    except (TaskValidationError, TaskConflictError, TaskPermissionError, TaskNotFoundError, TaskStoreError) as exc:
        raise _task_http_error(exc) from exc
    await _task_nudge("rejected")
    return result


@router.post("/instances/{instance_id}/reassign")
async def reassign_task_instance(
    instance_id: int,
    payload: TaskAssigneeIn,
    identity: dict = Depends(_require_task_manager),
    db=Depends(get_db),
) -> dict:
    """改派一期待交的任务（票 08）：换小组或换池，提交/验收历史不动。"""
    try:
        result = await _service(db).reassign_instance(
            _actor(identity), instance_id, {"assignee": payload.model_dump()}
        )
    except (TaskValidationError, TaskConflictError, TaskPermissionError, TaskNotFoundError, TaskStoreError) as exc:
        raise _task_http_error(exc) from exc
    await _task_nudge("reassigned")
    return result


@router.post("/instances/{instance_id}/cancel")
async def cancel_task_instance(
    instance_id: int,
    identity: dict = Depends(_require_task_manager),
    db=Depends(get_db),
) -> dict:
    """撤回即时任务：发布者、且还没人提交。撤回后移到「已结束」并标「已撤回」。"""
    try:
        result = await _service(db).cancel_instance(_actor(identity), instance_id)
    except (TaskValidationError, TaskConflictError, TaskPermissionError, TaskNotFoundError, TaskStoreError) as exc:
        raise _task_http_error(exc) from exc
    await _task_nudge("cancelled")
    return result
