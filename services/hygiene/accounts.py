#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工花名册、手机号+密码登录、批准、停用、职位、卫生权限。

Does not know 日常检查 / 专项卫生 / 整改单. 班次 pick is ticket 02.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from config import settings
from database import CHINA_TZ
from services import password_hash

logger = logging.getLogger(__name__)

PERMISSION_STAFF = "普通员工"
PERMISSION_ADMIN = "管理员"
ALLOWED_PERMISSIONS = frozenset({PERMISSION_STAFF, PERMISSION_ADMIN})
FORBIDDEN_SUPER_PERMISSION = "超级管理员"

# Mainland China mobile: 11 digits, 1[3-9]...
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


class EmployeeAccountsError(ValueError):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def _as_bool(value: Any) -> bool:
    return bool(int(value or 0))


class EmployeeAccounts:
    def __init__(self, conn_or_db, now: Optional[Callable[[], datetime]] = None):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("EmployeeAccounts requires an open database connection")
        self._conn = conn
        self._now = now or (lambda: datetime.now(CHINA_TZ))

    def _now_dt(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value

    def _now_iso(self) -> str:
        return self._now_dt().isoformat()

    def _normalize_phone(self, phone: str) -> str:
        digits = re.sub(r"[\s\-]", "", (phone or "").strip())
        if digits.startswith("+86"):
            digits = digits[3:]
        if digits.startswith("86") and len(digits) == 13:
            digits = digits[2:]
        if not _PHONE_RE.match(digits):
            raise EmployeeAccountsError("invalid_phone", "invalid_phone")
        return digits

    def _employee_from_row(self, row) -> dict:
        mapping = dict(row)
        return {
            "id": int(mapping["id"]),
            "phone": mapping["phone"],
            "job_title": mapping.get("job_title") or "",
            "permission": mapping["permission"],
            "approved": _as_bool(mapping["approved"]),
            "disabled": _as_bool(mapping["disabled"]),
        }

    async def _fetch_employee(self, employee_id: int):
        cur = await self._conn.execute(
            """SELECT id, phone, job_title, permission, approved, disabled
               FROM hygiene_employees WHERE id = ?""",
            (employee_id,),
        )
        return await cur.fetchone()

    async def _fetch_employee_by_phone(self, phone: str):
        cur = await self._conn.execute(
            """SELECT id, phone, password_hash, job_title, permission, approved, disabled
               FROM hygiene_employees WHERE phone = ?""",
            (phone,),
        )
        return await cur.fetchone()

    async def register(self, phone: str, password: str) -> dict:
        normalized = self._normalize_phone(phone)
        password_hash.validate_password(password)
        existing = await self._fetch_employee_by_phone(normalized)
        if existing is not None:
            raise EmployeeAccountsError("duplicate_phone", "duplicate_phone")
        now = self._now_iso()
        hashed = password_hash.hash_password(password)
        cur = await self._conn.execute(
            """INSERT INTO hygiene_employees
               (phone, password_hash, job_title, permission, approved, disabled, created_at, updated_at)
               VALUES (?, ?, '', ?, 0, 0, ?, ?)""",
            (normalized, hashed, PERMISSION_STAFF, now, now),
        )
        await self._conn.commit()
        logger.info("hygiene employee registered id=%s", cur.lastrowid)
        row = await self._fetch_employee(cur.lastrowid)
        return self._employee_from_row(row)

    async def login(self, phone: str, password: str) -> Optional[dict]:
        try:
            normalized = self._normalize_phone(phone)
        except EmployeeAccountsError:
            return None
        row = await self._fetch_employee_by_phone(normalized)
        if row is None:
            return None
        mapping = dict(row)
        if not password_hash.verify_password(password, mapping["password_hash"]):
            return None
        if not _as_bool(mapping["approved"]) or _as_bool(mapping["disabled"]):
            return None
        session_id = secrets.token_urlsafe(32)
        now_dt = self._now_dt()
        expires_dt = now_dt + timedelta(hours=settings.SESSION_TTL_HOURS)
        now = now_dt.isoformat()
        await self._conn.execute(
            """INSERT INTO hygiene_staff_sessions
               (session_id, employee_id, expires_at, created_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, mapping["id"], expires_dt.isoformat(), now, now),
        )
        await self._conn.commit()
        employee = self._employee_from_row(mapping)
        return {"session_id": session_id, "employee": employee}

    async def approve(self, employee_id: int) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET approved = 1, updated_at = ? WHERE id = ?",
            (now, employee_id),
        )
        await self._conn.commit()
        logger.info("hygiene employee approved id=%s", employee_id)
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    async def disable(self, employee_id: int) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET disabled = 1, updated_at = ? WHERE id = ?",
            (now, employee_id),
        )
        await self._conn.execute(
            "DELETE FROM hygiene_staff_sessions WHERE employee_id = ?",
            (employee_id,),
        )
        await self._conn.commit()
        logger.info("hygiene employee disabled id=%s", employee_id)
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    async def list_roster(self) -> list[dict]:
        cur = await self._conn.execute(
            """SELECT id, phone, job_title, permission, approved, disabled
               FROM hygiene_employees
               ORDER BY disabled ASC, approved ASC, id ASC"""
        )
        rows = await cur.fetchall()
        return [self._employee_from_row(row) for row in rows]

    async def set_job_title(self, employee_id: int, title: str) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        cleaned = (title or "").strip()
        if len(cleaned) > 40:
            raise EmployeeAccountsError("invalid_job_title", "invalid_job_title")
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET job_title = ?, updated_at = ? WHERE id = ?",
            (cleaned, now, employee_id),
        )
        await self._conn.commit()
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    async def set_permission(self, employee_id: int, permission: str) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        value = (permission or "").strip()
        if value == FORBIDDEN_SUPER_PERMISSION or value not in ALLOWED_PERMISSIONS:
            raise EmployeeAccountsError("invalid_permission", "invalid_permission")
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET permission = ?, updated_at = ? WHERE id = ?",
            (value, now, employee_id),
        )
        await self._conn.commit()
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    async def get_staff_session(self, session_id: Optional[str]) -> Optional[dict]:
        if not session_id:
            return None
        cur = await self._conn.execute(
            """SELECT s.session_id, s.expires_at,
                      e.id, e.phone, e.job_title, e.permission, e.approved, e.disabled
               FROM hygiene_staff_sessions s
               JOIN hygiene_employees e ON e.id = s.employee_id
               WHERE s.session_id = ?""",
            (session_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        mapping = dict(row)
        expires_at = datetime.fromisoformat(mapping["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=CHINA_TZ)
        if self._now_dt() >= expires_at:
            await self.logout(session_id)
            return None
        if not _as_bool(mapping["approved"]) or _as_bool(mapping["disabled"]):
            return None
        return self._employee_from_row(mapping)

    async def logout(self, session_id: str) -> None:
        await self._conn.execute(
            "DELETE FROM hygiene_staff_sessions WHERE session_id = ?",
            (session_id,),
        )
        await self._conn.commit()
