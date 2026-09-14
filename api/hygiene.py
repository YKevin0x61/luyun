#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Staff-phone and Admin SPA hygiene HTTP adapter. Rules live in the modules."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from api.security import require_session
from config import settings
from services.hygiene.accounts import EmployeeAccounts, EmployeeAccountsError
from services.hygiene.work import HygieneWork, HygieneWorkError

router = APIRouter(prefix="/api/hygiene", tags=["hygiene"])

SUPER_ACTOR = {"kind": "super"}

_ERROR_DETAILS = {
    "invalid_phone": "请输入有效的中国大陆手机号",
    "password_too_short": f"密码至少 {settings.AUTH_MIN_PASSWORD_LENGTH} 位",
    "password_too_long": f"密码过长（最多 {settings.AUTH_MAX_PASSWORD_BYTES} 字节）",
    "duplicate_phone": "该手机号已注册",
    "invalid_name": "请填写员工姓名",
    "invalid_permission": "卫生权限只能是普通员工或管理员",
    "invalid_job_title": "职位过长",
    "employee_not_found": "员工不存在",
    "invalid_shift": "班次只能是白班或夜班",
    "shift_already_picked": "当天班次已选定，不能自己改",
    "forbidden": "没有权限做这一步",
    "standard_required": "没有标准图不能上架日常检查项",
    "invalid_zone_name": "请填写卫生责任区名称",
    "invalid_item_name": "请填写日常检查项名称",
    "zone_not_found": "卫生责任区不存在",
    "item_not_found": "日常检查项不存在",
    "duplicate_zone": "已有同名卫生责任区",
    "duplicate_item": "该卫生责任区已有同名检查项",
    "shift_required": "请先选择当天班次",
    "shift_mismatch": "只能交自己班次的日常检查",
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
    if exc.code == "duplicate_phone" or exc.code == "shift_already_picked":
        status = 409
    return HTTPException(status_code=status, detail=_ERROR_DETAILS.get(exc.code, exc.code))


def _work_http_error(exc: HygieneWorkError) -> HTTPException:
    if exc.code in ("forbidden", "cannot_self_accept"):
        status = 403
    elif exc.code in ("zone_not_found", "item_not_found", "ticket_not_found", "teaching_not_found"):
        status = 404
    elif exc.code in ("duplicate_zone", "duplicate_item"):
        status = 409
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


class StaffRegisterIn(BaseModel):
    name: str
    phone: str
    password: str


class StaffLoginIn(BaseModel):
    phone: str
    password: str


class RosterPatchIn(BaseModel):
    name: Optional[str] = None
    job_title: Optional[str] = None
    permission: Optional[str] = None


class ShiftIn(BaseModel):
    shift: str


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


async def _daily_capture_response(work: HygieneWork, item_id: int, shift: str) -> Response:
    try:
        review = await work.get_daily_review(item_id, shift)
        body = work.capture_bytes(review["capture_id"])
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="实拍不存在") from exc
    return Response(
        content=body,
        media_type=review.get("content_type") or "image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


async def _frozen_standard_response(work: HygieneWork, item_id: int, shift: str) -> Response:
    try:
        review = await work.get_daily_review(item_id, shift)
        standard = await work.standard_by_id(review["frozen_standard_id"])
        body = work.capture_bytes(standard["capture_id"])
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="标准图不存在") from exc
    return Response(
        content=body,
        media_type=standard.get("content_type") or "image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


async def _standard_image_response(work: HygieneWork, item_id: int) -> Response:
    try:
        standard = await work.current_standard(item_id)
        body = work.capture_bytes(standard["capture_id"])
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="标准图不存在") from exc
    return Response(
        content=body,
        media_type=standard["content_type"] or "image/jpeg",
        headers={"Cache-Control": "no-store"},
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


@router.post("/staff/logout")
async def staff_logout(
    response: Response,
    staff=Depends(require_staff_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, bool]:
    await accounts.logout(staff["session_id"])
    _clear_staff_cookie(response)
    return {"success": True}


@router.post("/staff/shift")
async def staff_pick_shift(
    body: ShiftIn,
    staff=Depends(require_staff_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    try:
        picked = await accounts.pick_shift(staff["employee"]["id"], body.shift)
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    return picked


@router.get("/admin/roster")
async def admin_list_roster(
    _session_id: str = Depends(require_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    return {"employees": await accounts.list_roster()}


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
        employee = None
        if body.name is not None:
            employee = await accounts.set_name(employee_id, body.name)
        if body.job_title is not None:
            employee = await accounts.set_job_title(employee_id, body.job_title)
        if body.permission is not None:
            employee = await accounts.set_permission(employee_id, body.permission)
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    return {"employee": employee}


@router.post("/admin/roster/{employee_id}/shift")
async def admin_set_shift(
    employee_id: int,
    body: ShiftIn,
    _session_id: str = Depends(require_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    try:
        picked = await accounts.super_set_shift(employee_id, body.shift)
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
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
        return await work.set_daily_overdue_clocks(
            SUPER_ACTOR, body.day_hhmm, body.night_hhmm
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


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
    return {"item": item}


@router.get("/admin/items/{item_id}/standard")
async def admin_current_standard_image(
    item_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _standard_image_response(work, item_id)


@router.get("/staff/daily-catalog")
async def staff_daily_catalog(
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    return {"zones": await work.list_staff_daily_items()}


@router.get("/staff/items/{item_id}/standard")
async def staff_current_standard_image(
    item_id: int,
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _standard_image_response(work, item_id)


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
    return submitted


@router.post("/staff/daily/{item_id}/accept")
async def staff_accept_daily(
    item_id: int,
    body: ShiftIn,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.accept_daily(
            _staff_actor(staff["employee"]), item_id, body.shift
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.post("/staff/daily/{item_id}/reject")
async def staff_reject_daily(
    item_id: int,
    body: ShiftIn,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.reject_daily(
            _staff_actor(staff["employee"]), item_id, body.shift
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.get("/staff/daily/{item_id}/capture")
async def staff_daily_capture(
    item_id: int,
    shift: str,
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _daily_capture_response(work, item_id, shift)


@router.get("/staff/daily/{item_id}/frozen-standard")
async def staff_daily_frozen_standard(
    item_id: int,
    shift: str,
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _frozen_standard_response(work, item_id, shift)


@router.get("/staff/daily/{item_id}/review")
async def staff_daily_review(
    item_id: int,
    shift: str,
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.get_daily_review(item_id, shift)
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
        return await work.accept_daily(SUPER_ACTOR, item_id, body.shift)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.post("/admin/daily/{item_id}/reject")
async def admin_reject_daily(
    item_id: int,
    body: ShiftIn,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.reject_daily(SUPER_ACTOR, item_id, body.shift)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.get("/admin/daily/{item_id}/capture")
async def admin_daily_capture(
    item_id: int,
    shift: str,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _daily_capture_response(work, item_id, shift)


@router.get("/admin/daily/{item_id}/frozen-standard")
async def admin_daily_frozen_standard(
    item_id: int,
    shift: str,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _frozen_standard_response(work, item_id, shift)


@router.get("/admin/daily/{item_id}/review")
async def admin_daily_review(
    item_id: int,
    shift: str,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.get_daily_review(item_id, shift)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


async def _deep_clean_shot_response(work: HygieneWork, item_id: int, which: str) -> Response:
    try:
        review = await work.get_deep_clean_review(item_id)
        capture_id = review["before_capture_id"] if which == "before" else review["after_capture_id"]
        content_type = (
            review["before_content_type"] if which == "before" else review["after_content_type"]
        )
        body = work.capture_bytes(capture_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="实拍不存在") from exc
    return Response(
        content=body,
        media_type=content_type or "image/jpeg",
        headers={"Cache-Control": "no-store"},
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
        return await work.submit_deep_clean_pair(
            _staff_actor(staff["employee"]),
            item_id,
            before_capture,
            after_capture,
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.post("/staff/deep-clean/{item_id}/accept")
async def staff_accept_deep_clean(
    item_id: int,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.accept_deep_clean_pair(_staff_actor(staff["employee"]), item_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.post("/staff/deep-clean/{item_id}/reject")
async def staff_reject_deep_clean(
    item_id: int,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.reject_deep_clean_pair(_staff_actor(staff["employee"]), item_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


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
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _deep_clean_shot_response(work, item_id, "before")


@router.get("/staff/deep-clean/{item_id}/after")
async def staff_deep_clean_after(
    item_id: int,
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _deep_clean_shot_response(work, item_id, "after")


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
    return {"item": item}


@router.delete("/admin/deep-clean/items/{item_id}")
async def admin_remove_deep_clean_item(
    item_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.remove_deep_clean_item(SUPER_ACTOR, item_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


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
        return await work.set_deep_clean_overdue_clock(SUPER_ACTOR, body.hhmm)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


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
        return await work.accept_deep_clean_pair(SUPER_ACTOR, item_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.post("/admin/deep-clean/{item_id}/reject")
async def admin_reject_deep_clean(
    item_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.reject_deep_clean_pair(SUPER_ACTOR, item_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


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
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _deep_clean_shot_response(work, item_id, "before")


@router.get("/admin/deep-clean/{item_id}/after")
async def admin_deep_clean_after(
    item_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _deep_clean_shot_response(work, item_id, "after")


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
        return await work.open_fix(
            actor,
            zone_id,
            ticket_type,
            body_text,
            _parse_duration_hours(duration_hours),
            capture,
        )
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


async def _fix_shot_response(work: HygieneWork, ticket_id: int, which: str) -> Response:
    try:
        ticket = await work.get_fix_ticket(ticket_id)
        if which == "original":
            capture_id = ticket["capture_id"]
            content_type = ticket.get("content_type") or "image/jpeg"
        else:
            capture_id = ticket.get("reshoot_capture_id")
            content_type = ticket.get("reshoot_content_type") or "image/jpeg"
            if not capture_id:
                raise HygieneWorkError("not_pending", "not_pending")
        body = work.capture_bytes(capture_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="实拍不存在") from exc
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "no-store"},
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
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.get_fix_ticket(ticket_id)
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
        return await work.reshoot_fix(_staff_actor(staff["employee"]), ticket_id, capture)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.post("/staff/fix/{ticket_id}/accept")
async def staff_accept_fix(
    ticket_id: int,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.accept_fix(_staff_actor(staff["employee"]), ticket_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.post("/staff/fix/{ticket_id}/reject")
async def staff_reject_fix(
    ticket_id: int,
    staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.reject_fix(_staff_actor(staff["employee"]), ticket_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.get("/staff/fix/{ticket_id}/original")
async def staff_fix_original(
    ticket_id: int,
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _fix_shot_response(work, ticket_id, "original")


@router.get("/staff/fix/{ticket_id}/reshoot")
async def staff_fix_reshoot_image(
    ticket_id: int,
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _fix_shot_response(work, ticket_id, "reshoot")


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
        return await work.get_fix_ticket(ticket_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.post("/admin/fix/{ticket_id}/accept")
async def admin_accept_fix(
    ticket_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.accept_fix(SUPER_ACTOR, ticket_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.post("/admin/fix/{ticket_id}/reject")
async def admin_reject_fix(
    ticket_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Dict[str, Any]:
    try:
        return await work.reject_fix(SUPER_ACTOR, ticket_id)
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc


@router.get("/admin/fix/{ticket_id}/original")
async def admin_fix_original(
    ticket_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _fix_shot_response(work, ticket_id, "original")


@router.get("/admin/fix/{ticket_id}/reshoot")
async def admin_fix_reshoot_image(
    ticket_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _fix_shot_response(work, ticket_id, "reshoot")


def _teaching_source(body: TeachingMarkIn) -> dict:
    source = {"kind": body.kind, "item_id": body.item_id}
    if body.shift:
        source["shift"] = body.shift
    return source


async def _teaching_shot_response(work: HygieneWork, example_id: int, which: str) -> Response:
    try:
        example = await work.get_teaching(example_id)
        key = "left" if which == "left" else "right"
        body = work.capture_bytes(example[f"{key}_capture_id"])
    except HygieneWorkError as exc:
        raise _work_http_error(exc) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="教材图片不存在") from exc
    media = example.get(f"{key}_content_type") or "image/jpeg"
    return Response(content=body, media_type=media, headers={"Cache-Control": "no-store"})


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
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _teaching_shot_response(work, example_id, "left")


@router.get("/staff/teaching/{example_id}/right")
async def staff_teaching_right(
    example_id: int,
    _staff=Depends(require_staff_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _teaching_shot_response(work, example_id, "right")


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
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _teaching_shot_response(work, example_id, "left")


@router.get("/admin/teaching/{example_id}/right")
async def admin_teaching_right(
    example_id: int,
    _session_id: str = Depends(require_session),
    work: HygieneWork = Depends(_get_work),
) -> Response:
    return await _teaching_shot_response(work, example_id, "right")
