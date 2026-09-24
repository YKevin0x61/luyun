#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生侧的员工身份扩展：当日分工（班次与责任区）。

身份本身在公共层 `services.identity.accounts`；这里只放卫生业务，并把同一个类名
继续暴露给卫生端既有的调用方。班次常量仍是卫生自己的——排班有自己的班次表。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from db_core.errors import is_integrity_violation
from services.business_day import business_date_of
from services.identity.accounts import (  # noqa: F401  转发给卫生端既有调用方
    ALLOWED_PERMISSIONS,
    FORBIDDEN_SUPER_PERMISSION,
    LAST_SEEN_REFRESH_SECONDS,
    MAX_NAME_LENGTH,
    PERMISSION_ADMIN,
    PERMISSION_STAFF,
    EmployeeAccountsError,
    EmployeeAccounts as _EmployeeIdentity,
    hash_session_id,
    normalize_phone,
    serialized_write,
)

logger = logging.getLogger(__name__)


def hygiene_business_date(now: datetime) -> str:
    """营业日 YYYY-MM-DD，06:00 切（与 POS 同一规则）——卫生端的名字。"""
    return business_date_of(now)


def shift_pick_conflict_target() -> str:
    """hygiene_shift_picks 的 upsert 冲突目标，必须与唯一索引一致。

    PostgreSQL schema 的唯一索引是 (tenant_id, employee_id, business_date)。
    写错会报「there is no unique or exclusion constraint matching the ON CONFLICT
    specification」——现场 0.6.0 + PG 上就是这么炸的。SQLite 时代这里按后端分支
    （那张表没有 tenant_id），ADR 0089 之后只剩 PG 一种目标。
    """
    return "tenant_id, employee_id, business_date"


def shift_pick_upsert_sql(with_zone: bool) -> str:
    """hygiene_shift_picks 的 upsert 语句（冲突目标随后端，见上）。"""
    target = shift_pick_conflict_target()
    if with_zone:
        return (
            "INSERT INTO hygiene_shift_picks "
            "(employee_id, business_date, shift, zone_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            f"ON CONFLICT({target}) DO UPDATE SET "
            "shift = excluded.shift, zone_id = excluded.zone_id, updated_at = excluded.updated_at"
        )
    return (
        "INSERT INTO hygiene_shift_picks "
        "(employee_id, business_date, shift, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        f"ON CONFLICT({target}) DO UPDATE SET "
        "shift = excluded.shift, updated_at = excluded.updated_at"
    )


# 卫生自己的班次：员工当天能交哪份日常检查由它决定。排班系统有它自己的一套。
SHIFT_DAY = "白班"
SHIFT_NIGHT = "夜班"
ALLOWED_SHIFTS = frozenset({SHIFT_DAY, SHIFT_NIGHT})


class HygieneEmployeeAccounts(_EmployeeIdentity):
    """身份 + 卫生的当日分工（班次 / 责任区）。"""

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
        # 在锁内读"改之前是哪个区"：调用方要用它判断这次是不是换区（留痕）。放在锁外
        # 预读的话，两个并发选班请求会读到同一个旧值，后完成的那次就可能漏记。
        previous = await self.current_assignment(employee_id)
        await self._conn.execute(
            shift_pick_upsert_sql(with_zone=True),
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
        picked = self._assignment(
            employee_id,
            business_date,
            shift,
            zone_id,
            zone_mapping["name"],
            self._zone_shift_list(zone),
        )
        picked["previous_zone_id"] = previous.get("zone_id")
        picked["previous_zone_name"] = previous.get("zone_name")
        return picked

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
        except Exception as exc:
            if not is_integrity_violation(exc):
                raise
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
            shift_pick_upsert_sql(with_zone=False),
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
            shift_pick_upsert_sql(with_zone=True),
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

    async def _roster_extras(self, employees: list[dict]) -> None:
        """花名册每行补上今天的班次和责任区（一次查询，不按人循环）。"""
        if not employees:
            return
        cur = await self._conn.execute(
            """SELECT p.employee_id AS employee_id,
                      p.shift AS shift,
                      p.zone_id AS zone_id,
                      z.name AS zone_name
               FROM hygiene_shift_picks p
               LEFT JOIN hygiene_zones z ON z.id = p.zone_id
               WHERE p.business_date = ?""",
            (self._business_date(),),
        )
        picks = {row["employee_id"]: dict(row) for row in await cur.fetchall()}
        for employee in employees:
            pick = picks.get(employee["id"]) or {}
            employee["shift"] = pick.get("shift")
            employee["zone_id"] = pick.get("zone_id")
            employee["zone_name"] = pick.get("zone_name")

    async def _session_extras(self, employee: dict) -> dict:
        return await self.current_assignment(employee["id"])


# 卫生端既有调用方（api/hygiene.py、main.py、services/hygiene/*、21 个测试）用的还是这个名字。
EmployeeAccounts = HygieneEmployeeAccounts

__all__ = [
    "ALLOWED_PERMISSIONS",
    "ALLOWED_SHIFTS",
    "FORBIDDEN_SUPER_PERMISSION",
    "LAST_SEEN_REFRESH_SECONDS",
    "MAX_NAME_LENGTH",
    "PERMISSION_ADMIN",
    "PERMISSION_STAFF",
    "EmployeeAccounts",
    "EmployeeAccountsError",
    "HygieneEmployeeAccounts",
    "SHIFT_DAY",
    "SHIFT_NIGHT",
    "hash_session_id",
    "hygiene_business_date",
    "normalize_phone",
    "serialized_write",
    "shift_pick_conflict_target",
    "shift_pick_upsert_sql",
]
