#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Staff-phone and Admin SPA hygiene HTTP adapter. Rules live in the modules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote
import asyncio
import json
import logging
import sqlite3
import tempfile
import time
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.security import require_session
from config import settings
from services import auth_service
from services.hygiene.accounts import (
    EmployeeAccounts,
    EmployeeAccountsError,
    normalize_phone,
)
from services.hygiene.images import sniff_image_content_type
from services.hygiene.standards_export import write_archive
from services.hygiene.work import (
    BOARD_EVENT_DEFAULT_LIMIT,
    BOARD_EVENT_MAX_LIMIT,
    MAX_STANDARD_BYTES,
    HygieneWork,
    HygieneWorkError,
)
from services.realtime.hub import realtime_hub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hygiene", tags=["hygiene"])

SUPER_ACTOR = {"kind": "super"}
MAX_UPLOAD_BYTES = MAX_STANDARD_BYTES
UPLOAD_CHUNK_BYTES = 1024 * 1024
# 员工端离线缓存标准图时用的变体：页面里这些图最大显示到 440px 高，没必要下发原图。
STANDARD_CACHE_VARIANT = "preview"


def _upload_too_large_detail(label: str) -> str:
    limit_mb = max(1, MAX_UPLOAD_BYTES // (1024 * 1024))
    return f"{label}不能超过 {limit_mb} MB"


async def _read_upload_bounded(file: UploadFile, label: str) -> bytes:
    declared_size = getattr(file, "size", None)
    if declared_size is not None and declared_size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_upload_too_large_detail(label))

    data = bytearray()
    while True:
        chunk = await file.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=_upload_too_large_detail(label))
    return bytes(data)


async def _hygiene_nudge(resource: str, action: str, **scope) -> None:
    await realtime_hub.broadcast_nudge(
        "hygiene",
        {"resource": resource, "action": action, **scope},
    )

_ERROR_DETAILS = {
    "invalid_phone": "请输入有效的中国大陆手机号",
    "password_too_short": f"密码至少 {settings.AUTH_MIN_PASSWORD_LENGTH} 位",
    "password_too_long": f"密码过长（最多 {settings.AUTH_MAX_PASSWORD_BYTES} 字节）",
    "invalid_current_password": "当前密码错误",
    "passwords_do_not_match": "两次输入的新密码不一致",
    "duplicate_phone": "该手机号已注册",
    "invalid_name": "请填写员工姓名",
    "invalid_profile": "没有需要修改的资料",
    "invalid_permission": "卫生权限只能是普通员工或管理员",
    "invalid_job_title": "职位过长",
    "employee_not_found": "员工不存在",
    "invalid_shift": "班次只能是白班或夜班",
    "shift_already_picked": "当天班次已选定，不能自己改",
    "forbidden": "没有权限做这一步",
    "standard_required": "没有标准图不能上架日常检查项",
    "standard_not_found": "标准图版本不存在",
    "standard_too_large": _upload_too_large_detail("标准图"),
    "capture_too_large": _upload_too_large_detail("照片"),
    "invalid_zone_name": "请填写卫生责任区名称",
    "zone_shift_required": "卫生责任区至少要有一个班次",
    "zone_shift_mismatch": "这个责任区没有该班次，请换一个责任区或班次",
    "invalid_item_name": "请填写日常检查项名称",
    "zone_not_found": "卫生责任区不存在",
    "item_not_found": "日常检查项不存在",
    "duplicate_zone": "已有同名卫生责任区",
    "duplicate_item": "该卫生责任区已有同名检查项",
    "shift_required": "请先选择当天班次",
    "shift_mismatch": "只能交自己班次的日常检查",
    "zone_required": "请先选择今天的卫生责任区",
    "zone_mismatch": "只能查看和提交所选卫生责任区的任务",
    "live_required": "必须现场拍摄，不能从相册选图",
    "capture_required": "请拍摄日常检查照片",
    "cannot_self_accept": "交这张的人不能自己验收",
    "already_accepted": "这项已经通过，不能再交",
    "not_pending": "没有待验收的实拍",
    "photographer_required": "拍摄人未知",
    "invalid_clock": "逾期点须为 HH:MM，例如 15:00",
    "invalid_weekday": "请选择周一到周日",
    "invalid_type": "整改类型只能是卫生、摆放或标签",
    "invalid_body": "请写明哪里脏、怎么改",
    "invalid_duration": "请填写整改时限",
    "invalid_image": "图片无法读取，请换一张有效的照片",
    "invalid_variant": "图片尺寸类型不支持",
    "ticket_not_found": "整改单不存在",
    "not_passed": "只有已通过的对照才能标成卫生教材",
    "invalid_teaching": "请选择已通过的日常或专项对照",
    "teaching_not_found": "卫生教材不存在",
}


def _get_accounts() -> EmployeeAccounts:
    from main import employee_accounts

    if employee_accounts is None:
        raise HTTPException(status_code=500, detail="员工账号未初始化")
    return employee_accounts


def _get_work() -> HygieneWork:
    from main import hygiene_work

    if hygiene_work is None:
        raise HTTPException(status_code=500, detail="卫生待办未初始化")
    return hygiene_work


def _error_detail(code: str) -> str:
    """错误码 → 给客户端看的中文文案。

    缺映射时**不回显原始 code**：code 是内部标识，一旦哪天有人把异常信息拼进
    code（``f"bad_x: {value}"``），回显就等于把内部细节漏给客户端。原 code 只进
    日志，客户端拿固定文案。
    """
    mapped = _ERROR_DETAILS.get(code)
    if mapped is not None:
        return mapped
    logger.warning("卫生接口出现未映射的错误码：%s", code)
    return "操作失败，请稍后重试"


def _http_error(exc: EmployeeAccountsError) -> HTTPException:
    status = 404 if exc.code == "employee_not_found" else 400
    if exc.code == "zone_not_found":
        status = 404
    if exc.code == "duplicate_phone" or exc.code == "shift_already_picked":
        status = 409
    return HTTPException(status_code=status, detail=_error_detail(exc.code))


def _work_http_error(exc: HygieneWorkError) -> HTTPException:
    if exc.code in ("forbidden", "cannot_self_accept", "zone_mismatch"):
        status = 403
    elif exc.code in (
        "zone_not_found",
        "item_not_found",
        "ticket_not_found",
        "teaching_not_found",
        "standard_not_found",
    ):
        status = 404
    elif exc.code in ("duplicate_zone", "duplicate_item"):
        status = 409
    elif exc.code == "standard_too_large":
        status = 413
    elif exc.code == "capture_too_large":
        status = 413
    else:
        status = 400
    return HTTPException(status_code=status, detail=_error_detail(exc.code))


async def _delete_or_conflict(coro, detail: str):
    """删除类端点的统一兜底：还有没清干净的外键引用时给 409 而不是 500。

    正常路径已经在服务层按依赖顺序清了子行，这里是防止将来漏掉一张子表就
    把 500「服务器内部错误」抛给管理员。注意 PG 后端的完整性异常目前没有映射到
    ``sqlite3.IntegrityError``，所以这条兜底只在 SQLite 下生效。
    """
    try:
        return await coro
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    except sqlite3.IntegrityError as exc:
        logger.warning("hygiene delete blocked by integrity error: %s", exc)
        raise HTTPException(status_code=409, detail=detail) from exc


def _set_staff_cookie(response: Response, session_id: str, remember: bool = False) -> None:
    # 勾了「记住密码，自动登录」就把 cookie 延到 30 天，与服务端会话有效期保持一致；
    # 否则维持班次级（SESSION_TTL_HOURS）会话。
    max_age = (
        settings.SESSION_REMEMBER_DAYS * 86400
        if remember
        else settings.SESSION_TTL_HOURS * 3600
    )
    response.set_cookie(
        key=settings.STAFF_SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=max_age,
        secure=settings.session_cookie_secure,
    )


def _clear_staff_cookie(response: Response) -> None:
    response.delete_cookie(settings.STAFF_SESSION_COOKIE_NAME, path="/")


async def require_staff_session(
    request: Request,
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    session_id = request.cookies.get(settings.STAFF_SESSION_COOKIE_NAME)
    employee = await accounts.get_staff_session(session_id)
    if employee is None:
        raise HTTPException(status_code=401, detail="需要员工登录")
    return {"session_id": session_id, "employee": employee}


async def require_standard_cache_session(
    request: Request,
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    admin_session = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if await auth_service.validate_session_id(admin_session):
        return {"kind": "admin", "session_id": admin_session}
    employee = await accounts.get_staff_session(
        request.cookies.get(settings.STAFF_SESSION_COOKIE_NAME)
    )
    if employee is not None:
        return {"kind": "staff", "employee": employee}
    raise HTTPException(status_code=401, detail="需要登录")


class StaffRegisterIn(BaseModel):
    name: str
    phone: str
    password: str


class StaffLoginIn(BaseModel):
    phone: str
    password: str
    remember: bool = False


class StaffProfileIn(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None


class StaffPasswordIn(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


class RosterPatchIn(BaseModel):
    name: Optional[str] = None
    job_title: Optional[str] = None
    permission: Optional[str] = None


class ShiftIn(BaseModel):
    shift: str
    # 驳回时可附一句原因：员工端会显示出来，否则他只能原样重拍。
    reason: Optional[str] = None


class RejectIn(BaseModel):
    """专项/整改驳回的可选说明；不传就是只驳回、不给原因。"""

    reason: Optional[str] = None


class AssignmentIn(BaseModel):
    shift: str
    zone_id: int


class ZoneIn(BaseModel):
    name: str
    shifts: Optional[list[str]] = None


class ZoneShiftsIn(BaseModel):
    shifts: list[str]


class OverdueClocksIn(BaseModel):
    day_hhmm: str
    night_hhmm: str


class DeepCleanItemIn(BaseModel):
    weekday: int
    name: str


class DeepCleanClockIn(BaseModel):
    hhmm: str


class TeachingMarkIn(BaseModel):
    kind: str
    item_id: int
    shift: Optional[str] = None


class StandardMarkupIn(BaseModel):
    """只改标注、不换图时的请求体（形状由服务层归一化，这里不重复校验）。"""

    markup: List[Dict[str, Any]] = Field(default_factory=list)


def _parse_markup_field(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="标注格式不对") from exc
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="标注格式不对")
    return value


async def _capture_from_upload(file: UploadFile, markup_raw: Optional[str]) -> dict:
    data = await _read_upload_bounded(file, "标准图")
    if not data:
        raise HTTPException(status_code=400, detail="请上传标准图")
    return {
        "bytes": data,
        "content_type": _stored_content_type(data),
        "markup": _parse_markup_field(markup_raw),
    }


def _stored_content_type(data: bytes) -> str:
    """落库用的图片类型：由内容决定，不用客户端声明的 Content-Type。

    只信客户端的话，``image/svg+xml`` 会被原样存下来、再以同源
    ``Content-Type: image/svg+xml`` 发回去，里面的 ``<script>`` 就成了存储型
    XSS。认不出来的一律按二进制流存，浏览器不会当文档渲染它。
    """
    return sniff_image_content_type(data) or "application/octet-stream"


def _staff_actor(employee: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "staff",
        "id": employee["id"],
        "permission": employee["permission"],
        "name": employee.get("name") or "",
        "phone": employee["phone"],
        "shift": employee.get("shift"),
        "zone_id": employee.get("zone_id"),
        "zone_name": employee.get("zone_name"),
    }


def _live_flag(raw: Optional[str]) -> bool:
    return (raw or "").strip().lower() in {"true", "1", "yes"}


async def _live_capture_from_upload(file: UploadFile, live_raw: Optional[str]) -> dict:
    data = await _read_upload_bounded(file, "照片")
    return {
        "bytes": data,
        "content_type": _stored_content_type(data),
        "live": _live_flag(live_raw),
    }


async def _daily_capture_response(
    work: HygieneWork,
    item_id: int,
    shift: str,
    actor: Dict[str, Any],
    variant: str = "original",
    request: Optional[Request] = None,
) -> Response:
    try:
        review = await work.get_daily_review(item_id, shift, actor=actor)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    return await _image_response(
        work,
        review["capture_id"],
        variant=variant,
        request=request,
    )


async def _frozen_standard_response(
    work: HygieneWork,
    item_id: int,
    shift: str,
    actor: Dict[str, Any],
    variant: str = "original",
    request: Optional[Request] = None,
) -> Response:
    try:
        review = await work.get_daily_review(item_id, shift, actor=actor)
        standard = await work.standard_by_id(review["frozen_standard_id"])
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    return await _image_response(
        work,
        standard["capture_id"],
        variant=variant,
        request=request,
    )


async def _standard_image_response(
    work: HygieneWork,
    item_id: int,
    variant: str = "original",
    request: Optional[Request] = None,
) -> Response:
    try:
        standard = await work.current_standard(item_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    return await _image_response(
        work,
        standard["capture_id"],
        variant=variant,
        request=request,
    )


def _etag_matches(request: Optional[Request], etag: Optional[str]) -> bool:
    if request is None or not etag:
        return False
    raw = request.headers.get("if-none-match") or ""
    return etag in {part.strip() for part in raw.split(",")}


async def _image_response(
    work: HygieneWork,
    capture_id: str,
    *,
    variant: str = "original",
    request: Optional[Request] = None,
    cache_control: str = "no-store",
) -> Response:
    try:
        view = await work.capture_view(capture_id, variant)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="图片不存在") from exc
    headers = {
        "Cache-Control": cache_control,
        "Content-Encoding": "identity",
        # 类型已经由内容裁决（见 _stored_content_type），这里禁止浏览器再自行
        # 嗅探成可执行文档 —— 这是 SVG/HTML 伪装成图片那条路的第二道闸。
        "X-Content-Type-Options": "nosniff",
    }
    if view.get("sha256"):
        headers["ETag"] = f'"{view["sha256"]}"'
    if view.get("fallback"):
        headers["X-Hygiene-Variant-Fallback"] = "original"
    if _etag_matches(request, headers.get("ETag")):
        return Response(status_code=304, headers=headers)
    content_type = view.get("content_type") or "image/jpeg"
    # 认不出内容的字节按附件下发：浏览器不会在页面上下文里渲染它。
    headers["Content-Disposition"] = (
        'attachment; filename="capture.bin"'
        if content_type == "application/octet-stream"
        else 'inline; filename="capture.jpg"'
    )
    if view.get("path") is not None:
        # Let FileResponse derive Content-Length from the file itself. The
        # stored byte_size can lag behind a restored/copied capture and a
        # stale value makes uvicorn abort the stream mid-response.
        return FileResponse(
            path=view["path"],
            media_type=content_type,
            headers=headers,
        )
    body = await work.capture_bytes(view["capture_id"])
    return Response(content=body, media_type=content_type, headers=headers)


def _standard_cache_actor(identity: Dict[str, Any]) -> Dict[str, Any]:
    """把 require_standard_cache_session 的身份翻成 HygieneWork 的 actor。

    管理员会话不限责任区；员工会话按当天所选责任区切片，否则这份「可整包离线
    缓存」的清单就等于把全店标准图发给每个员工。
    """
    if isinstance(identity, dict) and identity.get("kind") == "staff":
        return _staff_actor(identity["employee"])
    return SUPER_ACTOR


@router.get("/standard-manifest")
async def standard_manifest(
    response: Response,
    identity=Depends(require_standard_cache_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    # 清单声明的必须是**实际下发的这一份**（preview 变体）的字节数与摘要：
    # 声明原图、下发变体的话，员工端按清单校验必然判「图片大小不一致」。
    manifest = await work.standard_manifest(
        _standard_cache_actor(identity),
        variant=STANDARD_CACHE_VARIANT,
    )
    for entry in manifest["standards"]:
        # 员工端会把整份清单离线缓存下来，而页面上这些图最大只显示到 440px 高：
        # 缓存 1600px 的 preview（250–450KB）而不是原图（2.5–4MB）。缺变体的老图
        # 由 _image_response 回落到原图，不会拿不到。
        entry["image_url"] = (
            f"/api/hygiene/standards/{int(entry['standard_id'])}/image"
            f"?variant={STANDARD_CACHE_VARIANT}"
        )
    # 每次切卫生路由都会拉这份清单：30 秒内让浏览器直接用缓存，标准图换版是低频操作。
    response.headers["Cache-Control"] = "private, max-age=30"
    response.headers["ETag"] = f'"{manifest["version"]}"'
    return manifest


@router.get("/standards/{standard_id}/image")
async def standard_version_image(
    standard_id: int,
    request: Request,
    variant: str = "original",
    identity=Depends(require_standard_cache_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    try:
        standard = await work.standard_version(
            standard_id,
            _standard_cache_actor(identity),
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    return await _image_response(
        work,
        standard["capture_id"],
        variant=variant,
        request=request,
        cache_control="private, max-age=31536000, immutable",
    )


@router.post("/staff/register")
async def staff_register(
    body: StaffRegisterIn,
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    try:
        employee = await accounts.register(body.phone, body.password, body.name)
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=_error_detail(str(exc)),
        ) from exc
    await _hygiene_nudge("roster", "registered")
    return {"employee": employee}


# 员工登录限流。管理员登录早就有一把（api/auth.py 的 _check_rate_limit），员工侧
# 一直是可以无限次猜的。两把尺子都要有：只按 IP 会被分布式绕过，只按手机号会被
# 人拿来锁死同事的账号。
_STAFF_LOGIN_WINDOW_SECONDS = 60
_STAFF_LOGIN_MAX_PER_IP = 10
_STAFF_LOGIN_MAX_PER_PHONE = 5
# 限流表的键数上限。模块级 dict 没有上限的话，攻击者用大量不同 IP / 手机号各失败
# 一次就能把它撑起来（每键约 200 字节，百万级就是几百 MB），而单 worker 进程扛不住。
_STAFF_LOGIN_MAX_TRACKED_KEYS = 5000
_staff_login_failures: Dict[str, list] = defaultdict(list)


def _prune_staff_login_failures(now: float) -> None:
    """表太大时清掉窗口外的键；仍然过大就按最旧淘汰到 80% 水位。

    淘汰而不是整体清空：清空会把正在被限流的桶（包括受害者账号）一起重置，
    等于给攻击者一次重置全场计数的机会。
    """
    if len(_staff_login_failures) < _STAFF_LOGIN_MAX_TRACKED_KEYS:
        return
    stale = [
        key
        for key, stamps in _staff_login_failures.items()
        if not any(now - ts < _STAFF_LOGIN_WINDOW_SECONDS for ts in stamps)
    ]
    for key in stale:
        _staff_login_failures.pop(key, None)
    if len(_staff_login_failures) >= _STAFF_LOGIN_MAX_TRACKED_KEYS:
        # 仍然超限：按"最近一次失败"从旧到新淘汰到 80% 水位。
        # 不用 clear()：那会把正在被限流的桶（包括受害者账号）一起清掉，等于给攻击者
        # 一次重置全场计数的机会，比按最旧淘汰更弱。
        ordered = sorted(
            _staff_login_failures.items(),
            key=lambda item: max(item[1]) if item[1] else 0.0,
        )
        target = int(_STAFF_LOGIN_MAX_TRACKED_KEYS * 0.8)
        for key, _stamps in ordered[: max(0, len(ordered) - target)]:
            _staff_login_failures.pop(key, None)
        logger.warning(
            "员工登录限流表超过 %s 个键，已按最旧淘汰到 %s 个",
            _STAFF_LOGIN_MAX_TRACKED_KEYS,
            len(_staff_login_failures),
        )


def _staff_login_keys(request: Request, phone: str) -> list:
    ip = request.client.host if request.client else "unknown"
    keys = [f"ip:{ip}"]
    # 必须归一化：`+8613800138000`、`138-0013-8000`、`8613800138000` 打的是同一个
    # 账号，按原样当键的话每种写法各占一个桶，这一维（防分布式撞库）就白设了。
    normalized = normalize_phone(phone)
    if normalized:
        keys.append(f"phone:{normalized}")
    return keys


def _check_staff_login_limit(keys: list) -> None:
    now = time.time()
    _prune_staff_login_failures(now)
    for key in keys:
        recent = [
            ts for ts in _staff_login_failures.get(key, [])
            if now - ts < _STAFF_LOGIN_WINDOW_SECONDS
        ]
        if recent:
            _staff_login_failures[key] = recent
        else:
            _staff_login_failures.pop(key, None)
        limit = (
            _STAFF_LOGIN_MAX_PER_PHONE
            if key.startswith("phone:")
            else _STAFF_LOGIN_MAX_PER_IP
        )
        if len(recent) >= limit:
            raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试")


def _record_staff_login_failure(keys: list) -> None:
    now = time.time()
    for key in keys:
        _staff_login_failures[key].append(now)


def _clear_staff_login_failures(keys: list) -> None:
    for key in keys:
        _staff_login_failures.pop(key, None)


@router.post("/staff/login")
async def staff_login(
    body: StaffLoginIn,
    request: Request,
    response: Response,
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    keys = _staff_login_keys(request, body.phone)
    _check_staff_login_limit(keys)
    result = await accounts.login(body.phone, body.password, remember=body.remember)
    if result is None:
        _record_staff_login_failure(keys)
        raise HTTPException(status_code=401, detail="手机号或密码错误，或账号未批准、已停用")
    _clear_staff_login_failures(keys)
    _set_staff_cookie(response, result["session_id"], remember=body.remember)
    return {"success": True, "employee": result["employee"]}


@router.get("/staff/me")
async def staff_me(
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {
        "employee": staff["employee"],
        "daily_clocks": await work.get_daily_overdue_clocks(),
        "deep_clock": await work.get_deep_clean_overdue_clock(),
    }


@router.patch("/staff/me")
async def staff_update_me(
    body: StaffProfileIn,
    staff=Depends(require_staff_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    if body.name is None and body.phone is None:
        raise HTTPException(status_code=400, detail="请提供姓名或手机号")
    try:
        employee = await accounts.update_profile(
            staff["employee"]["id"],
            name=body.name,
            phone=body.phone,
        )
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=_error_detail(str(exc)),
        ) from exc
    await _hygiene_nudge("roster", "profile_updated")
    return {"employee": employee}


@router.patch("/staff/password")
async def staff_change_password(
    body: StaffPasswordIn,
    staff=Depends(require_staff_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, bool]:
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail=_ERROR_DETAILS["passwords_do_not_match"])
    try:
        await accounts.change_password(
            staff["employee"]["id"],
            body.current_password,
            body.new_password,
            keep_session_id=staff["session_id"],
        )
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=_error_detail(str(exc)),
        ) from exc
    await _hygiene_nudge("roster", "password_changed")
    return {"success": True}


@router.post("/staff/logout")
async def staff_logout(
    response: Response,
    staff=Depends(require_staff_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, bool]:
    await accounts.logout(staff["session_id"])
    _clear_staff_cookie(response)
    return {"success": True}


async def _record_zone_switch(
    work: HygieneWork,
    employee: Dict[str, Any],
    picked: Dict[str, Any],
) -> bool:
    """员工当天自己换责任区时留痕。

    首次选择（previous 为空）与同区重选都不算换区；只有"本来在 A、现在改成 B"
    才记一条换区事件，用于红黑榜个人榜与事件流追溯。管理员改派不走这里。

    "改之前"用 `pick_assignment` 在写锁内读到的 `previous_zone_id`，而不是在调用
    之前预读——两个并发选班请求预读会拿到同一个旧值，后完成的那次就可能漏记。
    """
    previous_zone = picked.get("previous_zone_id")
    current_zone = picked.get("zone_id")
    if previous_zone is None or current_zone is None:
        return False
    if int(previous_zone) == int(current_zone):
        return False
    await work.record_zone_switch(
        employee,
        from_zone_id=int(previous_zone),
        from_zone_name=picked.get("previous_zone_name") or "",
        to_zone_id=int(current_zone),
        to_zone_name=picked.get("zone_name") or "",
    )
    return True


async def _pick_assignment(
    body: AssignmentIn,
    staff=Depends(require_staff_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    employee_id = staff["employee"]["id"]
    try:
        picked = await accounts.pick_assignment(
            employee_id,
            body.shift,
            body.zone_id,
        )
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    if await _record_zone_switch(work, staff["employee"], picked):
        # 换区后个人榜的实拍归属需要重新解释，让看板刷新。
        await _hygiene_nudge("boards", "changed")
    return picked


@router.post("/staff/assignment")
async def staff_pick_assignment(
    body: AssignmentIn,
    staff=Depends(require_staff_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    picked = await _pick_assignment(body, staff, accounts, work)
    await _hygiene_nudge("assignment", "changed", employee_id=staff["employee"]["id"])
    return picked


@router.post("/staff/shift")
async def staff_pick_shift(
    body: AssignmentIn,
    staff=Depends(require_staff_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    picked = await _pick_assignment(body, staff, accounts, work)
    await _hygiene_nudge("assignment", "changed", employee_id=staff["employee"]["id"])
    return picked


@router.get("/admin/roster")
async def admin_list_roster(
    _session_id: str = Depends(require_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {
        "employees": await accounts.list_roster(),
        "zones": await work.list_zones(),
    }


@router.post("/admin/roster/{employee_id}/approve")
async def admin_approve(
    employee_id: int,
    _session_id: str = Depends(require_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    try:
        employee = await accounts.approve(employee_id)
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    await _hygiene_nudge("roster", "approved", employee_id=employee_id)
    return {"employee": employee}


@router.post("/admin/roster/{employee_id}/disable")
async def admin_disable(
    employee_id: int,
    _session_id: str = Depends(require_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    try:
        employee = await accounts.disable(employee_id)
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    await _hygiene_nudge("roster", "disabled", employee_id=employee_id)
    return {"employee": employee}


@router.post("/admin/roster/{employee_id}/enable")
async def admin_enable(
    employee_id: int,
    _session_id: str = Depends(require_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    try:
        employee = await accounts.enable(employee_id)
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    await _hygiene_nudge("roster", "enabled", employee_id=employee_id)
    return {"employee": employee}


@router.patch("/admin/roster/{employee_id}")
async def admin_patch_roster(
    employee_id: int,
    body: RosterPatchIn,
    _session_id: str = Depends(require_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    if body.name is None and body.job_title is None and body.permission is None:
        raise HTTPException(status_code=400, detail="请提供姓名、职位或卫生权限")
    try:
        employee = await accounts.update_fields(
            employee_id,
            name=body.name,
            job_title=body.job_title,
            permission=body.permission,
        )
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    await _hygiene_nudge("roster", "updated", employee_id=employee_id)
    return {"employee": employee}


async def _admin_set_assignment(
    employee_id: int,
    body: AssignmentIn,
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    try:
        picked = await accounts.super_set_assignment(
            employee_id,
            body.shift,
            body.zone_id,
        )
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    return picked


@router.post("/admin/roster/{employee_id}/assignment")
async def admin_set_assignment(
    employee_id: int,
    body: AssignmentIn,
    _session_id: str = Depends(require_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    picked = await _admin_set_assignment(employee_id, body, accounts)
    await _hygiene_nudge("assignment", "changed", employee_id=employee_id)
    return picked


@router.post("/admin/roster/{employee_id}/shift")
async def admin_set_shift(
    employee_id: int,
    body: AssignmentIn,
    _session_id: str = Depends(require_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    picked = await _admin_set_assignment(employee_id, body, accounts)
    await _hygiene_nudge("assignment", "changed", employee_id=employee_id)
    return picked


async def _standard_export_entries(work: HygieneWork, variant: str) -> list:
    """收集 (责任区, 检查项, 图片路径, 标注)，只取各检查项的当前标准图。

    给的是路径不是字节：门店标准图上百张时，一次性把原图读进内存就是几百 MB，
    烘焙在线程里逐张读盘更稳。
    """
    entries: list = []
    for zone in await work.list_staff_daily_items():
        for item in zone.get("items") or []:
            capture_id = item.get("capture_id")
            if not capture_id:
                continue
            try:
                view = await work.capture_view(capture_id, variant)
            except (HygieneWorkError, FileNotFoundError) as exc:
                logger.warning(
                    "导出标准图：取不到这张图，已跳过 item=%s: %s", item.get("id"), exc
                )
                continue
            path = view.get("path")
            if path is None:
                continue
            entries.append(
                (zone["name"], item["name"], Path(path), item.get("markup") or [])
            )
    return entries


# 导出任务表：单 worker 进程内存在内存里（部署约束就是一个 worker），重启即丢——
# 导出是"点一下、等几秒、下载"的操作，丢了重来即可，不值得落库。
_EXPORT_JOBS: Dict[str, Dict[str, Any]] = {}
_EXPORT_JOB_TTL_SECONDS = 30 * 60
_EXPORT_MAX_RUNNING = 2


def _prune_export_jobs(now: float) -> None:
    for job_id, job in list(_EXPORT_JOBS.items()):
        if now - job["started_at"] < _EXPORT_JOB_TTL_SECONDS:
            continue
        path = job.get("path")
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.debug("清理导出临时文件失败 job=%s", job_id, exc_info=True)
        _EXPORT_JOBS.pop(job_id, None)


async def _run_export_job(job_id: str, work: HygieneWork, size: str) -> None:
    """后台把标准图烘焙进图片并按责任区打包。进度写在任务表里，前端轮询。"""
    job = _EXPORT_JOBS[job_id]
    archive_path: Optional[Path] = None
    try:
        entries = await _standard_export_entries(work, size)
        if not entries:
            job.update(state="failed", error="还没有带标准图的日常检查项")
            return
        job["total"] = len(entries)
        with tempfile.NamedTemporaryFile(
            prefix="hygiene-standards-", suffix=".zip", delete=False
        ) as handle:
            archive_path = Path(handle.name)

        def report(done: int, total: int) -> None:
            job["done"] = done
            job["total"] = total

        written, failed = await asyncio.to_thread(
            write_archive, entries, archive_path, report
        )
        if not written:
            archive_path.unlink(missing_ok=True)
            job.update(state="failed", error="标准图文件读不出来，无法导出")
            return
        stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
        job.update(
            state="done",
            path=archive_path,
            count=written,
            skipped=failed,
            filename=f"标准图-{stamp}.zip",
            bytes=archive_path.stat().st_size,
        )
        logger.info(
            "📦 [审计] 导出标准图 %s 张（跳过 %s 张，变体=%s，%.1f MB）",
            written, failed, size, job["bytes"] / 1024 / 1024,
        )
    except Exception as exc:  # noqa: BLE001 —— 后台任务必须自己收口，否则任务永远 running
        logger.exception("导出标准图失败 job=%s", job_id)
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)
        job.update(state="failed", error=f"导出失败：{exc}")


@router.post("/admin/standards-export/jobs")
async def start_standards_export(
    size: str = Query("preview", pattern="^(original|preview)$"),
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    """起一个导出任务，立刻返回 job_id；打包进度由状态接口轮询。

    同步返回会让浏览器干等十几秒（几十张原图要解码重编码 + 下载几十 MB），
    期间界面上没有任何反馈。这里改成任务：POST 立刻返回，前端按 done/total 显示
    进度，完成后再去下载。
    """
    now = time.time()
    _prune_export_jobs(now)
    running = sum(1 for job in _EXPORT_JOBS.values() if job["state"] == "running")
    if running >= _EXPORT_MAX_RUNNING:
        raise HTTPException(status_code=429, detail="已有一个导出任务在跑，请稍候")
    job_id = uuid.uuid4().hex
    _EXPORT_JOBS[job_id] = {
        "state": "running",
        "done": 0,
        "total": 0,
        "error": "",
        "started_at": now,
        "path": None,
        "size": size,
    }
    asyncio.create_task(_run_export_job(job_id, work, size))
    return {"job_id": job_id, "state": "running"}


@router.get("/admin/standards-export/jobs/{job_id}")
async def read_standards_export(job_id: str) -> Dict[str, Any]:
    job = _EXPORT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="导出任务不存在或已过期")
    return {
        "state": job["state"],
        "done": job["done"],
        "total": job["total"],
        "error": job["error"],
        "count": job.get("count"),
        "bytes": job.get("bytes"),
    }


@router.get("/admin/standards-export/jobs/{job_id}/download")
async def download_standards_export(job_id: str) -> Response:
    job = _EXPORT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="导出任务不存在或已过期")
    if job["state"] == "failed":
        raise HTTPException(status_code=409, detail=job["error"] or "导出失败")
    if job["state"] != "done":
        raise HTTPException(status_code=409, detail="还在打包，请稍候")
    path = job["path"]
    if path is None or not Path(path).exists():
        raise HTTPException(status_code=410, detail="导出文件已被清理，请重新导出")
    filename = job.get("filename") or "hygiene-standards.zip"
    return FileResponse(
        path,
        media_type="application/zip",
        headers={
            # ASCII 名兜底老浏览器；filename* 让前端拿到中文文件名。
            "Content-Disposition": (
                'attachment; filename="hygiene-standards.zip"; '
                f"filename*=UTF-8''{quote(filename)}"
            ),
            "X-Hygiene-Export-Count": str(job.get("count") or 0),
        },
    )


@router.get("/admin/zones")
async def admin_list_zones(
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {"zones": await work.list_staff_daily_items()}


@router.post("/admin/zones")
async def admin_create_zone(
    body: ZoneIn,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        zone = await work.create_zone(SUPER_ACTOR, body.name, body.shifts)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("zones", "created", zone_id=zone["id"])
    return {"zone": zone}


@router.patch("/admin/zones/{zone_id}")
async def admin_update_zone(
    zone_id: int,
    body: ZoneShiftsIn,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        zone = await work.set_zone_shifts(SUPER_ACTOR, zone_id, body.shifts)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("zones", "updated", zone_id=zone_id)
    return {"zone": zone}


@router.delete("/admin/zones/{zone_id}")
async def admin_delete_zone(
    zone_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    zone = await _delete_or_conflict(
        work.delete_zone(SUPER_ACTOR, zone_id),
        "该卫生责任区还有关联数据没清干净，暂时不能删",
    )
    await _hygiene_nudge("zones", "deleted", zone_id=zone_id)
    return {"zone": zone}


@router.get("/admin/overdue-clocks")
async def admin_get_overdue_clocks(
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return await work.get_daily_overdue_clocks()


@router.patch("/admin/overdue-clocks")
async def admin_set_overdue_clocks(
    body: OverdueClocksIn,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        clocks = await work.set_daily_overdue_clocks(
            SUPER_ACTOR, body.day_hhmm, body.night_hhmm
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("settings", "updated")
    return clocks


@router.get("/admin/board-events")
async def admin_board_events(
    since: Optional[str] = Query(None, description="只返回该时间之后的事件（ISO 时间戳）"),
    limit: int = Query(
        BOARD_EVENT_DEFAULT_LIMIT,
        ge=1,
        le=BOARD_EVENT_MAX_LIMIT,
        description="每个榜最多返回多少条",
    ),
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    # 事件表只增不减（一年几十万行），整包吐出去就是 O(历史) 响应体，所以带上
    # 时间窗与条数上限；不传时给一个保守的默认值。
    return {
        "zones": await work.list_zone_board_events(
            since=since,
            limit=limit,
        ),
        "people": await work.list_person_board_events(
            since=since,
            limit=limit,
        ),
    }


@router.get("/admin/boards")
async def admin_boards(
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return await work.list_boards()


@router.get("/staff/boards")
async def staff_boards(
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return await work.list_boards()


@router.post("/admin/zones/{zone_id}/items")
async def admin_add_daily_item(
    zone_id: int,
    name: str = Form(...),
    markup: Optional[str] = Form(None),
    file: UploadFile = File(...),
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    capture = await _capture_from_upload(file, markup)
    try:
        item = await work.add_daily_item(SUPER_ACTOR, zone_id, name, capture)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("zones", "created", zone_id=item["zone_id"])
    return {"item": item}


@router.delete("/admin/items/{item_id}")
async def admin_delete_daily_item(
    item_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    item = await _delete_or_conflict(
        work.delete_daily_item(SUPER_ACTOR, item_id),
        "该检查项还有关联数据没清干净，暂时不能删",
    )
    await _hygiene_nudge("zones", "deleted", item_id=item["id"])
    return {"item": item}


@router.post("/admin/items/{item_id}/standard")
async def admin_replace_standard(
    item_id: int,
    markup: Optional[str] = Form(None),
    file: UploadFile = File(...),
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    capture = await _capture_from_upload(file, markup)
    try:
        item = await work.replace_standard(SUPER_ACTOR, item_id, capture)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("zones", "standard_updated", item_id=item["id"])
    return {"item": item}


@router.get("/admin/items/{item_id}/standard")
async def admin_current_standard_image(
    item_id: int,
    request: Request,
    variant: str = "original",
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _standard_image_response(work, item_id, variant, request)


@router.patch("/admin/items/{item_id}/standard/markup")
async def admin_update_standard_markup(
    item_id: int,
    body: StandardMarkupIn,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    """只改当前标准图的标注，不重新上传图片。

    原来的唯一入口是 ``POST /admin/items/{id}/standard``（``file`` 必填），想补一个
    圈就得把同一张图再传一遍。这里复用同一份图片、只换 markup，服务层会新插一版
    标准并把 current_standard_id 指过去（历史冻结标准不动）。
    """
    try:
        item = await work.update_standard_markup(SUPER_ACTOR, item_id, body.markup)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("zones", "standard_updated", item_id=item["id"])
    return {"item": item}


@router.get("/staff/daily-catalog")
async def staff_daily_catalog(
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {"zones": await work.list_staff_daily_items(_staff_actor(staff["employee"]))}


@router.get("/staff/items/{item_id}/standard")
async def staff_current_standard_image(
    item_id: int,
    request: Request,
    variant: str = "original",
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    try:
        await work.require_daily_item_access(_staff_actor(staff["employee"]), item_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    return await _standard_image_response(work, item_id, variant, request)


@router.get("/staff/daily-work")
async def staff_daily_work(
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {"items": await work.list_daily_work(_staff_actor(staff["employee"]))}


@router.post("/staff/daily/{item_id}/submit")
async def staff_submit_daily(
    item_id: int,
    live: Optional[str] = Form(None),
    shift: Optional[str] = Form(None),
    file: UploadFile = File(...),
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    capture = await _live_capture_from_upload(file, live)
    try:
        submitted = await work.submit_daily(
            _staff_actor(staff["employee"]),
            item_id,
            capture,
            shift=shift,
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("daily", "submitted", item_id=item_id)
    await _hygiene_nudge("boards", "changed")
    return submitted


@router.post("/staff/daily/{item_id}/accept")
async def staff_accept_daily(
    item_id: int,
    body: ShiftIn,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        accepted = await work.accept_daily(
            _staff_actor(staff["employee"]), item_id, body.shift
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("daily", "accepted", item_id=item_id)
    await _hygiene_nudge("boards", "changed")
    return accepted


@router.post("/staff/daily/{item_id}/reject")
async def staff_reject_daily(
    item_id: int,
    body: ShiftIn,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        rejected = await work.reject_daily(
            _staff_actor(staff["employee"]), item_id, body.shift, reason=body.reason
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("daily", "rejected", item_id=item_id)
    await _hygiene_nudge("boards", "changed")
    return rejected


@router.get("/staff/daily/{item_id}/capture")
async def staff_daily_capture(
    item_id: int,
    shift: str,
    request: Request,
    variant: str = "original",
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _daily_capture_response(
        work,
        item_id,
        shift,
        _staff_actor(staff["employee"]),
        variant,
        request,
    )


@router.get("/staff/daily/{item_id}/frozen-standard")
async def staff_daily_frozen_standard(
    item_id: int,
    shift: str,
    request: Request,
    variant: str = "original",
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _frozen_standard_response(
        work,
        item_id,
        shift,
        _staff_actor(staff["employee"]),
        variant,
        request,
    )


@router.get("/staff/daily/{item_id}/review")
async def staff_daily_review(
    item_id: int,
    shift: str,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.get_daily_review(
            item_id,
            shift,
            actor=_staff_actor(staff["employee"]),
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.get("/admin/daily-queue")
async def admin_daily_queue(
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    items = await work.list_daily_work(SUPER_ACTOR)
    pending = [row for row in items if row["status"] == "待验收"]
    return {"items": pending}


@router.post("/admin/daily/{item_id}/accept")
async def admin_accept_daily(
    item_id: int,
    body: ShiftIn,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        accepted = await work.accept_daily(SUPER_ACTOR, item_id, body.shift)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("daily", "accepted", item_id=item_id)
    await _hygiene_nudge("boards", "changed")
    return accepted


@router.post("/admin/daily/{item_id}/reject")
async def admin_reject_daily(
    item_id: int,
    body: ShiftIn,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        rejected = await work.reject_daily(
            SUPER_ACTOR, item_id, body.shift, reason=body.reason
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("daily", "rejected", item_id=item_id)
    await _hygiene_nudge("boards", "changed")
    return rejected


@router.get("/admin/daily/{item_id}/capture")
async def admin_daily_capture(
    item_id: int,
    shift: str,
    request: Request,
    variant: str = "original",
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _daily_capture_response(
        work,
        item_id,
        shift,
        SUPER_ACTOR,
        variant,
        request,
    )


@router.get("/admin/daily/{item_id}/frozen-standard")
async def admin_daily_frozen_standard(
    item_id: int,
    shift: str,
    request: Request,
    variant: str = "original",
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _frozen_standard_response(
        work,
        item_id,
        shift,
        SUPER_ACTOR,
        variant,
        request,
    )


@router.get("/admin/daily/{item_id}/review")
async def admin_daily_review(
    item_id: int,
    shift: str,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.get_daily_review(item_id, shift, actor=SUPER_ACTOR)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


async def _deep_clean_shot_response(
    work: HygieneWork,
    item_id: int,
    which: str,
    variant: str = "original",
    request: Optional[Request] = None,
) -> Response:
    try:
        review = await work.get_deep_clean_review(item_id)
        capture_id = review["before_capture_id"] if which == "before" else review["after_capture_id"]
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    return await _image_response(
        work,
        capture_id,
        variant=variant,
        request=request,
    )


@router.get("/staff/deep-clean")
async def staff_deep_clean_work(
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return await work.list_deep_clean_work(_staff_actor(staff["employee"]))


@router.post("/staff/deep-clean/{item_id}/submit")
async def staff_submit_deep_clean(
    item_id: int,
    live: Optional[str] = Form(None),
    before: UploadFile = File(...),
    after: UploadFile = File(...),
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    before_capture = await _live_capture_from_upload(before, live)
    after_capture = await _live_capture_from_upload(after, live)
    try:
        submitted = await work.submit_deep_clean_pair(
            _staff_actor(staff["employee"]),
            item_id,
            before_capture,
            after_capture,
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("deep", "submitted", item_id=item_id)
    return submitted


@router.post("/staff/deep-clean/{item_id}/accept")
async def staff_accept_deep_clean(
    item_id: int,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        accepted = await work.accept_deep_clean_pair(
            _staff_actor(staff["employee"]), item_id
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("deep", "accepted", item_id=item_id)
    return accepted


@router.post("/staff/deep-clean/{item_id}/reject")
async def staff_reject_deep_clean(
    item_id: int,
    body: Optional[RejectIn] = None,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        rejected = await work.reject_deep_clean_pair(
            _staff_actor(staff["employee"]),
            item_id,
            reason=(body.reason if body else None),
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("deep", "rejected", item_id=item_id)
    return rejected


@router.get("/staff/deep-clean/{item_id}/review")
async def staff_deep_clean_review(
    item_id: int,
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.get_deep_clean_review(item_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.get("/staff/deep-clean/{item_id}/before")
async def staff_deep_clean_before(
    item_id: int,
    request: Request,
    variant: str = "original",
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _deep_clean_shot_response(work, item_id, "before", variant, request)


@router.get("/staff/deep-clean/{item_id}/after")
async def staff_deep_clean_after(
    item_id: int,
    request: Request,
    variant: str = "original",
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _deep_clean_shot_response(work, item_id, "after", variant, request)


@router.get("/admin/deep-clean/items")
async def admin_list_deep_clean_items(
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {"items": await work.list_deep_clean_items()}


@router.post("/admin/deep-clean/items")
async def admin_add_deep_clean_item(
    body: DeepCleanItemIn,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        item = await work.add_deep_clean_item(SUPER_ACTOR, body.weekday, body.name)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("deep", "configured", weekday=body.weekday)
    return {"item": item}


@router.delete("/admin/deep-clean/items/{item_id}")
async def admin_remove_deep_clean_item(
    item_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    item = await _delete_or_conflict(
        work.remove_deep_clean_item(SUPER_ACTOR, item_id),
        "该专项卫生项还有关联数据没清干净，暂时不能删",
    )
    await _hygiene_nudge("deep", "configured", weekday=item["weekday"])
    return item


@router.get("/admin/deep-clean/clock")
async def admin_get_deep_clean_clock(
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return await work.get_deep_clean_overdue_clock()


@router.patch("/admin/deep-clean/clock")
async def admin_set_deep_clean_clock(
    body: DeepCleanClockIn,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        clock = await work.set_deep_clean_overdue_clock(SUPER_ACTOR, body.hhmm)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("settings", "updated")
    return clock


@router.get("/admin/deep-clean/calendar")
async def admin_deep_clean_calendar(
    from_date: str,
    to_date: str,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {"days": await work.list_deep_clean_calendar(from_date, to_date)}


@router.get("/admin/deep-clean/queue")
async def admin_deep_clean_queue(
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    listed = await work.list_deep_clean_work(SUPER_ACTOR)
    pending = [row for row in listed["items"] if row["status"] == "待验收"]
    return {"items": pending, "status": listed["status"]}


@router.post("/admin/deep-clean/{item_id}/accept")
async def admin_accept_deep_clean(
    item_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        accepted = await work.accept_deep_clean_pair(SUPER_ACTOR, item_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("deep", "accepted", item_id=item_id)
    return accepted


@router.post("/admin/deep-clean/{item_id}/reject")
async def admin_reject_deep_clean(
    item_id: int,
    body: Optional[RejectIn] = None,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        rejected = await work.reject_deep_clean_pair(
            SUPER_ACTOR, item_id, reason=(body.reason if body else None)
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("deep", "rejected", item_id=item_id)
    return rejected


@router.get("/admin/deep-clean/{item_id}/review")
async def admin_deep_clean_review(
    item_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.get_deep_clean_review(item_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.get("/admin/deep-clean/{item_id}/before")
async def admin_deep_clean_before(
    item_id: int,
    request: Request,
    variant: str = "original",
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _deep_clean_shot_response(work, item_id, "before", variant, request)


@router.get("/admin/deep-clean/{item_id}/after")
async def admin_deep_clean_after(
    item_id: int,
    request: Request,
    variant: str = "original",
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _deep_clean_shot_response(work, item_id, "after", variant, request)


def _parse_duration_hours(raw: Optional[str]) -> timedelta:
    try:
        hours = float((raw or "").strip())
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=_ERROR_DETAILS["invalid_duration"]) from exc
    if hours <= 0:
        raise HTTPException(status_code=400, detail=_ERROR_DETAILS["invalid_duration"])
    return timedelta(hours=hours)


async def _open_fix_from_form(
    actor: dict,
    zone_id: int,
    ticket_type: str,
    body_text: str,
    duration_hours: Optional[str],
    live: Optional[str],
    file: UploadFile,
    markup: Optional[str],
    work: HygieneWork,
) -> Dict[str, Any]:
    capture = await _live_capture_from_upload(file, live)
    capture["markup"] = _parse_markup_field(markup)
    try:
        result = await work.open_fix(
            actor,
            zone_id,
            ticket_type,
            body_text,
            _parse_duration_hours(duration_hours),
            capture,
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge(
        "fix",
        "opened",
        ticket_id=result["id"],
        zone_id=result["zone_id"],
    )
    return result


async def _fix_shot_response(
    work: HygieneWork,
    ticket_id: int,
    which: str,
    actor: Dict[str, Any],
    variant: str = "original",
    request: Optional[Request] = None,
) -> Response:
    try:
        ticket = await work.get_fix_ticket(ticket_id, actor=actor)
        if which == "original":
            capture_id = ticket["capture_id"]
        else:
            capture_id = ticket.get("reshoot_capture_id")
            if not capture_id:
                raise HygieneWorkError("not_pending", "not_pending")
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    return await _image_response(
        work,
        capture_id,
        variant=variant,
        request=request,
    )


@router.get("/staff/fix")
async def staff_list_fix(
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {"items": await work.list_fix_tickets(_staff_actor(staff["employee"]))}


@router.post("/staff/fix")
async def staff_open_fix(
    zone_id: int = Form(...),
    ticket_type: str = Form(...),
    body_text: str = Form(...),
    duration_hours: Optional[str] = Form(None),
    live: Optional[str] = Form(None),
    markup: Optional[str] = Form(None),
    file: UploadFile = File(...),
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return await _open_fix_from_form(
        _staff_actor(staff["employee"]),
        zone_id,
        ticket_type,
        body_text,
        duration_hours,
        live,
        file,
        markup,
        work,
    )


@router.get("/staff/fix/{ticket_id}")
async def staff_get_fix(
    ticket_id: int,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.get_fix_ticket(
            ticket_id,
            actor=_staff_actor(staff["employee"]),
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.post("/staff/fix/{ticket_id}/reshoot")
async def staff_reshoot_fix(
    ticket_id: int,
    live: Optional[str] = Form(None),
    file: UploadFile = File(...),
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    capture = await _live_capture_from_upload(file, live)
    try:
        reshoot = await work.reshoot_fix(
            _staff_actor(staff["employee"]), ticket_id, capture
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("fix", "reshot", ticket_id=ticket_id)
    return reshoot


@router.post("/staff/fix/{ticket_id}/accept")
async def staff_accept_fix(
    ticket_id: int,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        accepted = await work.accept_fix(_staff_actor(staff["employee"]), ticket_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("fix", "accepted", ticket_id=ticket_id)
    return accepted


@router.post("/staff/fix/{ticket_id}/reject")
async def staff_reject_fix(
    ticket_id: int,
    body: Optional[RejectIn] = None,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        rejected = await work.reject_fix(
            _staff_actor(staff["employee"]), ticket_id, reason=(body.reason if body else None)
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("fix", "rejected", ticket_id=ticket_id)
    return rejected


@router.get("/staff/fix/{ticket_id}/original")
async def staff_fix_original(
    ticket_id: int,
    request: Request,
    variant: str = "original",
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _fix_shot_response(
        work,
        ticket_id,
        "original",
        _staff_actor(staff["employee"]),
        variant,
        request,
    )


@router.get("/staff/fix/{ticket_id}/reshoot")
async def staff_fix_reshoot_image(
    ticket_id: int,
    request: Request,
    variant: str = "original",
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _fix_shot_response(
        work,
        ticket_id,
        "reshoot",
        _staff_actor(staff["employee"]),
        variant,
        request,
    )


@router.get("/admin/fix")
async def admin_list_fix(
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {"items": await work.list_fix_tickets(SUPER_ACTOR)}


@router.post("/admin/fix")
async def admin_open_fix(
    zone_id: int = Form(...),
    ticket_type: str = Form(...),
    body_text: str = Form(...),
    duration_hours: Optional[str] = Form(None),
    live: Optional[str] = Form(None),
    markup: Optional[str] = Form(None),
    file: UploadFile = File(...),
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return await _open_fix_from_form(
        SUPER_ACTOR,
        zone_id,
        ticket_type,
        body_text,
        duration_hours,
        live,
        file,
        markup,
        work,
    )


@router.get("/admin/fix/{ticket_id}")
async def admin_get_fix(
    ticket_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.get_fix_ticket(ticket_id, actor=SUPER_ACTOR)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.post("/admin/fix/{ticket_id}/accept")
async def admin_accept_fix(
    ticket_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        accepted = await work.accept_fix(SUPER_ACTOR, ticket_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("fix", "accepted", ticket_id=ticket_id)
    return accepted


@router.post("/admin/fix/{ticket_id}/reject")
async def admin_reject_fix(
    ticket_id: int,
    body: Optional[RejectIn] = None,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        rejected = await work.reject_fix(
            SUPER_ACTOR, ticket_id, reason=(body.reason if body else None)
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("fix", "rejected", ticket_id=ticket_id)
    return rejected


@router.delete("/admin/fix/{ticket_id}")
async def admin_delete_fix(
    ticket_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        ticket = await work.delete_fix_ticket(SUPER_ACTOR, ticket_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("fix", "deleted", ticket_id=ticket_id)
    return {"ticket": ticket}


@router.get("/admin/fix/{ticket_id}/original")
async def admin_fix_original(
    ticket_id: int,
    request: Request,
    variant: str = "original",
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _fix_shot_response(
        work,
        ticket_id,
        "original",
        SUPER_ACTOR,
        variant,
        request,
    )


@router.get("/admin/fix/{ticket_id}/reshoot")
async def admin_fix_reshoot_image(
    ticket_id: int,
    request: Request,
    variant: str = "original",
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _fix_shot_response(
        work,
        ticket_id,
        "reshoot",
        SUPER_ACTOR,
        variant,
        request,
    )


def _teaching_source(body: TeachingMarkIn) -> dict:
    source = {"kind": body.kind, "item_id": body.item_id}
    if body.shift:
        source["shift"] = body.shift
    return source


async def _teaching_shot_response(
    work: HygieneWork,
    example_id: int,
    which: str,
    variant: str = "original",
    request: Optional[Request] = None,
) -> Response:
    try:
        example = await work.get_teaching(example_id)
        key = "left" if which == "left" else "right"
        capture_id = example[f"{key}_capture_id"]
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    return await _image_response(
        work,
        capture_id,
        variant=variant,
        request=request,
    )


@router.get("/staff/teaching")
async def staff_list_teaching(
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {"items": await work.list_teaching()}


@router.get("/staff/teaching/{example_id}")
async def staff_get_teaching(
    example_id: int,
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.get_teaching(example_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.get("/staff/teaching/{example_id}/left")
async def staff_teaching_left(
    example_id: int,
    request: Request,
    variant: str = "original",
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _teaching_shot_response(work, example_id, "left", variant, request)


@router.get("/staff/teaching/{example_id}/right")
async def staff_teaching_right(
    example_id: int,
    request: Request,
    variant: str = "original",
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _teaching_shot_response(work, example_id, "right", variant, request)


@router.get("/admin/teaching")
async def admin_list_teaching(
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {"items": await work.list_teaching()}


@router.post("/admin/teaching")
async def admin_mark_teaching(
    body: TeachingMarkIn,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        example = await work.mark_teaching(SUPER_ACTOR, _teaching_source(body))
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("teaching", "marked", example_id=example["id"])
    return {"example": example}


@router.get("/admin/teaching/{example_id}")
async def admin_get_teaching(
    example_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.get_teaching(example_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.get("/admin/teaching/{example_id}/left")
async def admin_teaching_left(
    example_id: int,
    request: Request,
    variant: str = "original",
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _teaching_shot_response(work, example_id, "left", variant, request)


@router.get("/admin/teaching/{example_id}/right")
async def admin_teaching_right(
    example_id: int,
    request: Request,
    variant: str = "original",
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _teaching_shot_response(work, example_id, "right", variant, request)
