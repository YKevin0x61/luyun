#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工花名册、手机号+密码登录、批准、停用/启用、职位、卫生权限、班次与责任区。

Does not know 日常检查 / 专项卫生 / 整改单.
"""

from __future__ import annotations

import logging
import asyncio
import functools
import re
import secrets
import sqlite3
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

BUSINESS_DAY_CUT_HOUR = 6
SHIFT_DAY = "白班"
SHIFT_NIGHT = "夜班"
ALLOWED_SHIFTS = frozenset({SHIFT_DAY, SHIFT_NIGHT})
MAX_NAME_LENGTH = 40


def serialized_write(method):
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        async with self._write_lock:
            return await method(self, *args, **kwargs)

    return wrapper


def hygiene_business_date(now: datetime) -> str:
    """营业日 YYYY-MM-DD. Cuts at 06:00 China time, same idea as POS."""
    if now.tzinfo is None:
        local = now.replace(tzinfo=CHINA_TZ)
    else:
        local = now.astimezone(CHINA_TZ)
    if local.hour < BUSINESS_DAY_CUT_HOUR:
        local = local - timedelta(days=1)
    return local.date().isoformat()


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
        self._write_lock_owner = conn_or_db
        self._local_write_lock = None

    @property
    def _write_lock(self):
        shared = getattr(self._write_lock_owner, "_write_lock", None)
        if shared is not None:
            return shared
        if self._local_write_lock is None:
            self._local_write_lock = asyncio.Lock()
        return self._local_write_lock

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

    def _normalize_name(self, name: str) -> str:
        cleaned = " ".join((name or "").strip().split())
        if not cleaned:
            raise EmployeeAccountsError("invalid_name", "invalid_name")
        if len(cleaned) > MAX_NAME_LENGTH:
            raise EmployeeAccountsError("invalid_name", "invalid_name")
        return cleaned

    def _employee_from_row(self, row) -> dict:
        mapping = dict(row)
        return {
            "id": int(mapping["id"]),
            "phone": mapping["phone"],
            "name": mapping.get("name") or "",
            "job_title": mapping.get("job_title") or "",
            "permission": mapping["permission"],
            "approved": _as_bool(mapping["approved"]),
            "disabled": _as_bool(mapping["disabled"]),
        }

    async def _fetch_employee(self, employee_id: int):
        cur = await self._conn.execute(
            """SELECT id, phone, name, job_title, permission, approved, disabled
               FROM hygiene_employees WHERE id = ?""",
            (employee_id,),
        )
        return await cur.fetchone()

    async def _fetch_employee_by_phone(self, phone: str):
        cur = await self._conn.execute(
            """SELECT id, phone, name, password_hash, job_title, permission, approved, disabled
               FROM hygiene_employees WHERE phone = ?""",
            (phone,),
        )
        return await cur.fetchone()

    @serialized_write
    async def register(self, phone: str, password: str, name: str) -> dict:
        normalized = self._normalize_phone(phone)
        employee_name = self._normalize_name(name)
        password_hash.validate_password(password)
        existing = await self._fetch_employee_by_phone(normalized)
        if existing is not None:
            raise EmployeeAccountsError("duplicate_phone", "duplicate_phone")
        now = self._now_iso()
        hashed = await password_hash.hash_password_async(password)
        try:
            cur = await self._conn.execute(
                """INSERT INTO hygiene_employees
                   (phone, name, password_hash, job_title, permission, approved, disabled,
                    created_at, updated_at)
                   VALUES (?, ?, ?, '', ?, 0, 0, ?, ?)""",
                (normalized, employee_name, hashed, PERMISSION_STAFF, now, now),
            )
            await self._conn.commit()
        except sqlite3.IntegrityError as exc:
            await self._conn.rollback()
            raise EmployeeAccountsError("duplicate_phone", "duplicate_phone") from exc
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
        if not await password_hash.verify_password_async(password, mapping["password_hash"]):
            return None
        if not _as_bool(mapping["approved"]) or _as_bool(mapping["disabled"]):
            return None
        if password_hash.needs_rehash(mapping["password_hash"]):
            # legacy 无前缀哈希：验证通过后按现行格式重写，下次登录只需一次
            # bcrypt。老密码可能不满足当前长度策略，此时放弃升级但不阻断登录。
            # 与下面的 session 写入共用同一次 commit。
            try:
                upgraded = await password_hash.hash_password_async(password)
            except ValueError as exc:
                logger.warning(
                    "hygiene legacy hash upgrade skipped id=%s: %s", mapping["id"], exc
                )
            else:
                await self._conn.execute(
                    "UPDATE hygiene_employees SET password_hash = ?, updated_at = ? WHERE id = ?",
                    (upgraded, self._now_iso(), int(mapping["id"])),
                )
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

    async def enable(self, employee_id: int) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET disabled = 0, updated_at = ? WHERE id = ?",
            (now, employee_id),
        )
        await self._conn.commit()
        logger.info("hygiene employee enabled id=%s", employee_id)
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    async def list_roster(self) -> list[dict]:
        business_date = self._business_date()
        cur = await self._conn.execute(
            """SELECT e.id, e.phone, e.name, e.job_title, e.permission,
                      e.approved, e.disabled,
                      p.shift AS shift, p.zone_id AS zone_id,
                      z.name AS zone_name
               FROM hygiene_employees e
               LEFT JOIN hygiene_shift_picks p
                 ON p.employee_id = e.id AND p.business_date = ?
               LEFT JOIN hygiene_zones z ON z.id = p.zone_id
               ORDER BY e.disabled ASC, e.approved ASC, e.id ASC""",
            (business_date,),
        )
        rows = await cur.fetchall()
        employees = []
        for row in rows:
            employee = self._employee_from_row(row)
            employee["shift"] = dict(row).get("shift")
            employee["zone_id"] = dict(row).get("zone_id")
            employee["zone_name"] = dict(row).get("zone_name")
            employees.append(employee)
        return employees

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

    async def set_name(self, employee_id: int, name: str) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        cleaned = self._normalize_name(name)
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET name = ?, updated_at = ? WHERE id = ?",
            (cleaned, now, employee_id),
        )
        await self._conn.commit()
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    @serialized_write
    async def update_profile(
        self,
        employee_id: int,
        *,
        name: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        mapping = dict(row)
        fields = []
        params: list = []
        if name is not None:
            fields.append("name = ?")
            params.append(self._normalize_name(name))
        if phone is not None:
            normalized = self._normalize_phone(phone)
            if normalized != mapping["phone"]:
                existing = await self._fetch_employee_by_phone(normalized)
                if existing is not None and int(dict(existing)["id"]) != int(employee_id):
                    raise EmployeeAccountsError("duplicate_phone", "duplicate_phone")
            fields.append("phone = ?")
            params.append(normalized)
        if not fields:
            raise EmployeeAccountsError("invalid_profile", "invalid_profile")
        fields.append("updated_at = ?")
        params.append(self._now_iso())
        params.append(employee_id)
        try:
            await self._conn.execute(
                f"UPDATE hygiene_employees SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            await self._conn.commit()
        except sqlite3.IntegrityError as exc:
            await self._conn.rollback()
            raise EmployeeAccountsError("duplicate_phone", "duplicate_phone") from exc
        logger.info("hygiene employee profile updated id=%s", employee_id)
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    async def change_password(
        self,
        employee_id: int,
        current_password: str,
        new_password: str,
        keep_session_id: Optional[str] = None,
    ) -> None:
        cur = await self._conn.execute(
            "SELECT password_hash FROM hygiene_employees WHERE id = ?",
            (int(employee_id),),
        )
        row = await cur.fetchone()
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        if not await password_hash.verify_password_async(current_password, dict(row)["password_hash"]):
            raise EmployeeAccountsError(
                "invalid_current_password",
                "invalid_current_password",
            )
        password_hash.validate_password(new_password)
        hashed = await password_hash.hash_password_async(new_password)
        await self._conn.execute(
            "UPDATE hygiene_employees SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hashed, self._now_iso(), int(employee_id)),
        )
        if keep_session_id:
            await self._conn.execute(
                """DELETE FROM hygiene_staff_sessions
                   WHERE employee_id = ? AND session_id != ?""",
                (int(employee_id), keep_session_id),
            )
        else:
            await self._conn.execute(
                "DELETE FROM hygiene_staff_sessions WHERE employee_id = ?",
                (int(employee_id),),
            )
        await self._conn.commit()
        logger.info("hygiene employee password changed id=%s", employee_id)

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

    @serialized_write
    async def update_fields(
        self,
        employee_id: int,
        *,
        name: Optional[str] = None,
        job_title: Optional[str] = None,
        permission: Optional[str] = None,
    ) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        fields = []
        params: list = []
        if name is not None:
            fields.append("name = ?")
            params.append(self._normalize_name(name))
        if job_title is not None:
            cleaned_title = (job_title or "").strip()
            if len(cleaned_title) > 40:
                raise EmployeeAccountsError("invalid_job_title", "invalid_job_title")
            fields.append("job_title = ?")
            params.append(cleaned_title)
        if permission is not None:
            value = (permission or "").strip()
            if value == FORBIDDEN_SUPER_PERMISSION or value not in ALLOWED_PERMISSIONS:
                raise EmployeeAccountsError("invalid_permission", "invalid_permission")
            fields.append("permission = ?")
            params.append(value)
        if not fields:
            return self._employee_from_row(row)
        fields.append("updated_at = ?")
        params.append(self._now_iso())
        params.append(employee_id)
        await self._conn.execute(
            f"UPDATE hygiene_employees SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        await self._conn.commit()
        refreshed = await self._fetch_employee(employee_id)
        return self._employee_from_row(refreshed)

    async def get_staff_session(self, session_id: Optional[str]) -> Optional[dict]:
        if not session_id:
            return None
        cur = await self._conn.execute(
            """SELECT s.session_id, s.expires_at,
                      e.id, e.phone, e.name, e.job_title, e.permission,
                      e.approved, e.disabled
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
        employee = self._employee_from_row(mapping)
        employee.update(await self.current_assignment(employee["id"]))
        return employee

    def _business_date(self) -> str:
        return hygiene_business_date(self._now_dt())

    def _assignment(
        self,
        employee_id: int,
        business_date: str,
        shift: Optional[str],
        zone_id: Optional[int] = None,
        zone_name: Optional[str] = None,
        zone_shifts=None,
    ) -> dict:
        return {
            "employee_id": int(employee_id),
            "business_date": business_date,
            "shift": shift,
            "zone_id": None if zone_id is None else int(zone_id),
            "zone_name": zone_name,
            "zone_shifts": list(zone_shifts or []),
        }

    def _normalize_shift(self, shift: str) -> str:
        value = (shift or "").strip()
        if value not in ALLOWED_SHIFTS:
            raise EmployeeAccountsError("invalid_shift", "invalid_shift")
        return value

    async def current_shift(self, employee_id: int) -> Optional[str]:
        assignment = await self.current_assignment(employee_id)
        return assignment["shift"]

    async def current_assignment(self, employee_id: int) -> dict:
        cur = await self._conn.execute(
            """SELECT p.shift, p.zone_id, z.name AS zone_name,
                      z.day_shift, z.night_shift
               FROM hygiene_shift_picks p
               LEFT JOIN hygiene_zones z ON z.id = p.zone_id
               WHERE p.employee_id = ? AND p.business_date = ?""",
            (employee_id, self._business_date()),
        )
        row = await cur.fetchone()
        if row is None:
            return self._assignment(
                employee_id,
                self._business_date(),
                None,
            )
        mapping = dict(row)
        return self._assignment(
            employee_id,
            self._business_date(),
            mapping["shift"],
            mapping.get("zone_id"),
            mapping.get("zone_name"),
            self._zone_shift_list(mapping) if mapping.get("zone_id") else [],
        )

    async def _fetch_zone(self, zone_id: int):
        cur = await self._conn.execute(
            """SELECT id, name, day_shift, night_shift
               FROM hygiene_zones WHERE id = ?""",
            (int(zone_id),),
        )
        return await cur.fetchone()

    @staticmethod
    def _zone_allows_shift(zone, shift: str) -> bool:
        mapping = dict(zone)
        flag = mapping.get("day_shift") if shift == SHIFT_DAY else mapping.get("night_shift")
        return bool(int(flag or 0))

    @staticmethod
    def _zone_shift_list(zone) -> list[str]:
        mapping = dict(zone)
        shifts = []
        if int(mapping.get("day_shift", 1) or 0):
            shifts.append(SHIFT_DAY)
        if int(mapping.get("night_shift", 1) or 0):
            shifts.append(SHIFT_NIGHT)
        return shifts

    @serialized_write
    async def pick_assignment(self, employee_id: int, shift: str, zone_id: int) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        shift = self._normalize_shift(shift)
        zone = await self._fetch_zone(zone_id)
        if zone is None:
            raise EmployeeAccountsError("zone_not_found", "zone_not_found")
        if not self._zone_allows_shift(zone, shift):
            raise EmployeeAccountsError("zone_shift_mismatch", "zone_shift_mismatch")
        business_date = self._business_date()
        now = self._now_iso()
        await self._conn.execute(
            """INSERT INTO hygiene_shift_picks
               (employee_id, business_date, shift, zone_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(employee_id, business_date) DO UPDATE SET
                 shift = excluded.shift,
                 zone_id = excluded.zone_id,
                 updated_at = excluded.updated_at""",
            (employee_id, business_date, shift, int(zone_id), now, now),
        )
        await self._conn.commit()
        zone_mapping = dict(zone)
        logger.info(
            "hygiene assignment picked employee=%s date=%s shift=%s zone=%s",
            employee_id,
            business_date,
            shift,
            zone_id,
        )
        return self._assignment(
            employee_id,
            business_date,
            shift,
            zone_id,
            zone_mapping["name"],
            self._zone_shift_list(zone),
        )

    async def pick_shift(self, employee_id: int, shift: str) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        shift = self._normalize_shift(shift)
        business_date = self._business_date()
        if await self.current_shift(employee_id) is not None:
            raise EmployeeAccountsError("shift_already_picked", "shift_already_picked")
        now = self._now_iso()
        try:
            await self._conn.execute(
                """INSERT INTO hygiene_shift_picks
                   (employee_id, business_date, shift, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (employee_id, business_date, shift, now, now),
            )
            await self._conn.commit()
        except sqlite3.IntegrityError as exc:
            await self._conn.rollback()
            raise EmployeeAccountsError(
                "shift_already_picked", "shift_already_picked"
            ) from exc
        logger.info(
            "hygiene shift picked employee=%s date=%s shift=%s",
            employee_id,
            business_date,
            shift,
        )
        return self._assignment(employee_id, business_date, shift)

    async def super_set_shift(self, employee_id: int, shift: str) -> dict:
        current = await self.current_assignment(employee_id)
        if current["zone_id"] is not None:
            return await self.super_set_assignment(
                employee_id,
                shift,
                current["zone_id"],
            )
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        shift = self._normalize_shift(shift)
        business_date = self._business_date()
        now = self._now_iso()
        await self._conn.execute(
            """INSERT INTO hygiene_shift_picks
               (employee_id, business_date, shift, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(employee_id, business_date) DO UPDATE SET
                 shift = excluded.shift,
                 updated_at = excluded.updated_at""",
            (employee_id, business_date, shift, now, now),
        )
        await self._conn.commit()
        return self._assignment(employee_id, business_date, shift)

    async def super_set_assignment(self, employee_id: int, shift: str, zone_id: int) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        shift = self._normalize_shift(shift)
        zone = await self._fetch_zone(zone_id)
        if zone is None:
            raise EmployeeAccountsError("zone_not_found", "zone_not_found")
        if not self._zone_allows_shift(zone, shift):
            raise EmployeeAccountsError("zone_shift_mismatch", "zone_shift_mismatch")
        business_date = self._business_date()
        now = self._now_iso()
        await self._conn.execute(
            """INSERT INTO hygiene_shift_picks
               (employee_id, business_date, shift, zone_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(employee_id, business_date) DO UPDATE SET
                 shift = excluded.shift,
                 zone_id = excluded.zone_id,
                 updated_at = excluded.updated_at""",
            (employee_id, business_date, shift, int(zone_id), now, now),
        )
        await self._conn.commit()
        logger.info(
            "hygiene assignment super-set employee=%s date=%s shift=%s zone=%s",
            employee_id,
            business_date,
            shift,
            zone_id,
        )
        zone_mapping = dict(zone)
        return self._assignment(
            employee_id,
            business_date,
            shift,
            zone_id,
            zone_mapping["name"],
            self._zone_shift_list(zone),
        )

    async def logout(self, session_id: str) -> None:
        await self._conn.execute(
            "DELETE FROM hygiene_staff_sessions WHERE session_id = ?",
            (session_id,),
        )
        await self._conn.commit()
