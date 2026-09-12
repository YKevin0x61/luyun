#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Staff-phone and Admin SPA roster HTTP adapter. Rules live in EmployeeAccounts."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from api.security import require_session
from config import settings
from services.hygiene.accounts import EmployeeAccounts, EmployeeAccountsError

router = APIRouter(prefix="/api/hygiene", tags=["hygiene"])

_ERROR_DETAILS = {
    "invalid_phone": "请输入有效的中国大陆手机号",
    "password_too_short": f"密码至少 {settings.AUTH_MIN_PASSWORD_LENGTH} 位",
    "password_too_long": f"密码过长（最多 {settings.AUTH_MAX_PASSWORD_BYTES} 字节）",
    "duplicate_phone": "该手机号已注册",
    "invalid_permission": "卫生权限只能是普通员工或管理员",
    "invalid_job_title": "职位过长",
    "employee_not_found": "员工不存在",
}


def _get_accounts() -> EmployeeAccounts:
    from main import employee_accounts

    if employee_accounts is None:
        raise HTTPException(status_code=500, detail="员工账号未初始化")
    return employee_accounts


def _http_error(exc: EmployeeAccountsError) -> HTTPException:
    status = 404 if exc.code == "employee_not_found" else 400
    if exc.code == "duplicate_phone":
        status = 409
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
    phone: str
    password: str


class StaffLoginIn(BaseModel):
    phone: str
    password: str


class RosterPatchIn(BaseModel):
    job_title: Optional[str] = None
    permission: Optional[str] = None


@router.post("/staff/register")
async def staff_register(
    body: StaffRegisterIn,
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    try:
        employee = await accounts.register(body.phone, body.password)
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
async def staff_me(staff=Depends(require_staff_session)) -> Dict[str, Any]:
    return {"employee": staff["employee"]}


@router.post("/staff/logout")
async def staff_logout(
    response: Response,
    staff=Depends(require_staff_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, bool]:
    await accounts.logout(staff["session_id"])
    _clear_staff_cookie(response)
    return {"success": True}


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


@router.patch("/admin/roster/{employee_id}")
async def admin_patch_roster(
    employee_id: int,
    body: RosterPatchIn,
    _session_id: str = Depends(require_session),
    accounts: EmployeeAccounts = Depends(_get_accounts),
) -> Dict[str, Any]:
    if body.job_title is None and body.permission is None:
        raise HTTPException(status_code=400, detail="请提供职位或卫生权限")
    try:
        employee = None
        if body.job_title is not None:
            employee = await accounts.set_job_title(employee_id, body.job_title)
        if body.permission is not None:
            employee = await accounts.set_permission(employee_id, body.permission)
    except EmployeeAccountsError as exc:
        raise _http_error(exc) from exc
    return {"employee": employee}
