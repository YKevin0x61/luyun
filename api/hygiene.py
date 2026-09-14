#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Staff-phone and Admin SPA hygiene HTTP adapter. Rules live in the modules."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.security import require_session
from config import settings
from services import auth_service
from services.hygiene.accounts import EmployeeAccounts, EmployeeAccountsError
from services.hygiene.work import HygieneWork, HygieneWorkError
from services.realtime.hub import realtime_hub

router = APIRouter(prefix="/api/hygiene", tags=["hygiene"])

SUPER_ACTOR = {"kind": "super"}


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
    "standard_too_large": "标准图不能超过 20 MB",
    "invalid_zone_name": "请填写卫生责任区名称",
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


def _http_error(exc: EmployeeAccountsError) -> HTTPException:
    status = 404 if exc.code == "employee_not_found" else 400
    if exc.code == "zone_not_found":
        status = 404
    if exc.code == "duplicate_phone" or exc.code == "shift_already_picked":
        status = 409
    return HTTPException(status_code=status, detail=_ERROR_DETAILS.get(exc.code, exc.code))


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
    else:
        status = 400
    return HTTPException(status_code=status, detail=_ERROR_DETAILS.get(exc.code, exc.code))


def _set_staff_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=settings.STAFF_SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=settings.SESSION_TTL_HOURS * 3600,
        secure=not settings.DEBUG,
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


class AssignmentIn(BaseModel):
    shift: str
    zone_id: int


class ZoneIn(BaseModel):
    name: str


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
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="请上传标准图")
    content_type = (file.content_type or "image/jpeg").split(";")[0].strip()
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"
    return {
        "bytes": data,
        "content_type": content_type,
        "markup": _parse_markup_field(markup_raw),
    }


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
    data = await file.read()
    content_type = (file.content_type or "image/jpeg").split(";")[0].strip()
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"
    return {
        "bytes": data,
        "content_type": content_type,
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
    }
    if view.get("byte_size") is not None:
        headers["Content-Length"] = str(int(view["byte_size"]))
    if view.get("sha256"):
        headers["ETag"] = f'"{view["sha256"]}"'
    if view.get("fallback"):
        headers["X-Hygiene-Variant-Fallback"] = "original"
    if _etag_matches(request, headers.get("ETag")):
        return Response(status_code=304, headers=headers)
    content_type = view.get("content_type") or "image/jpeg"
    if view.get("path") is not None:
        return FileResponse(
            path=view["path"],
            media_type=content_type,
            headers=headers,
        )
    body = await work.capture_bytes(view["capture_id"])
    return Response(content=body, media_type=content_type, headers=headers)


@router.get("/standard-manifest")
async def standard_manifest(
    _identity=Depends(require_standard_cache_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    manifest = await work.standard_manifest()
    for entry in manifest["standards"]:
        entry["image_url"] = (
            f"/api/hygiene/standards/{int(entry['standard_id'])}/image"
        )
    return manifest


@router.get("/standards/{standard_id}/image")
async def standard_version_image(
    standard_id: int,
    request: Request,
    variant: str = "original",
    _identity=Depends(require_standard_cache_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    try:
        standard = await work.standard_version(standard_id)
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
        code = str(exc)
        raise HTTPException(
            status_code=400,
            detail=_ERROR_DETAILS.get(code, code),
        ) from exc
    await _hygiene_nudge("roster", "registered")
    return {"employee": employee}


@router.post("/staff/login")
async def staff_login(
    body: StaffLoginIn,
    response: Response,
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    result = await accounts.login(body.phone, body.password)
    if result is None:
        raise HTTPException(status_code=401, detail="手机号或密码错误，或账号未批准、已停用")
    _set_staff_cookie(response, result["session_id"])
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
        code = str(exc)
        raise HTTPException(
            status_code=400,
            detail=_ERROR_DETAILS.get(code, code),
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
        code = str(exc)
        raise HTTPException(
            status_code=400,
            detail=_ERROR_DETAILS.get(code, code),
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


async def _pick_assignment(
    body: AssignmentIn,
    staff=Depends(require_staff_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    try:
        picked = await accounts.pick_assignment(
            staff["employee"]["id"],
            body.shift,
            body.zone_id,
        )
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    return picked


@router.post("/staff/assignment")
async def staff_pick_assignment(
    body: AssignmentIn,
    staff=Depends(require_staff_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    picked = await _pick_assignment(body, staff, accounts)
    await _hygiene_nudge("assignment", "changed", employee_id=staff["employee"]["id"])
    return picked


@router.post("/staff/shift")
async def staff_pick_shift(
    body: AssignmentIn,
    staff=Depends(require_staff_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    picked = await _pick_assignment(body, staff, accounts)
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
        zone = await work.create_zone(SUPER_ACTOR, body.name)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("zones", "created", zone_id=zone["id"])
    return {"zone": zone}


@router.delete("/admin/zones/{zone_id}")
async def admin_delete_zone(
    zone_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        zone = await work.delete_zone(SUPER_ACTOR, zone_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
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
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {
        "zones": await work.list_zone_board_events(),
        "people": await work.list_person_board_events(),
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
    try:
        item = await work.delete_daily_item(SUPER_ACTOR, item_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
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
            _staff_actor(staff["employee"]), item_id, body.shift
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
        rejected = await work.reject_daily(SUPER_ACTOR, item_id, body.shift)
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
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        rejected = await work.reject_deep_clean_pair(
            _staff_actor(staff["employee"]), item_id
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
    try:
        item = await work.remove_deep_clean_item(SUPER_ACTOR, item_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
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
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        rejected = await work.reject_deep_clean_pair(SUPER_ACTOR, item_id)
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
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        rejected = await work.reject_fix(_staff_actor(staff["employee"]), ticket_id)
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
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        rejected = await work.reject_fix(SUPER_ACTOR, ticket_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    await _hygiene_nudge("fix", "rejected", ticket_id=ticket_id)
    return rejected


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
