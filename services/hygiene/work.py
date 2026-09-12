#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生责任区、日常检查项、当前标准图、日常提交与验收、专项卫生、整改单、逾期群通知、红黑榜、卫生教材。

Does not hash passwords or issue staff sessions. Captures stay off SQLite WAL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Callable, Optional

from database import CHINA_TZ
from services.hygiene.accounts import (
    BUSINESS_DAY_CUT_HOUR,
    PERMISSION_ADMIN,
    SHIFT_DAY,
    SHIFT_NIGHT,
    hygiene_business_date,
)

logger = logging.getLogger(__name__)

SEED_ZONE_NAMES = ("案板", "馅档", "熟笼", "肠粉", "西饼", "明档1", "明档2", "煎炸")
STATUS_TODO = "待拍"
STATUS_PENDING = "待验收"
STATUS_PASSED = "已通过"
STATUS_FIX_TODO = "待回拍"
FIX_TYPES = ("卫生", "摆放", "标签")
OPENER_STAFF = "staff"
OPENER_SUPER = "super"
DAILY_SHIFTS = (SHIFT_DAY, SHIFT_NIGHT)
DEFAULT_DAY_OVERDUE_HHMM = "15:00"
DEFAULT_NIGHT_OVERDUE_HHMM = "21:30"
SETTING_DAY_OVERDUE = "daily_overdue_day_hhmm"
SETTING_NIGHT_OVERDUE = "daily_overdue_night_hhmm"
SETTING_DEEP_CLEAN_OVERDUE = "deep_clean_overdue_hhmm"
DEFAULT_DEEP_CLEAN_OVERDUE_HHMM = "21:30"
WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
CALENDAR_TODO = "待办"
CALENDAR_DONE = "已完成"
CALENDAR_MISSED = "未完成"
OVERDUE_SWEEP_INTERVAL_SECONDS = 30
BOARD_ZONE = "zone"
BOARD_PERSON = "person"
EVENT_MISSED_DAILY = "逾期"
EVENT_CAPTURE = "实拍"
EVENT_REJECT = "驳回"
EVENT_FIRST_PASS = "一次通过"
PERSON_COUNT_KEYS = ("实拍", "驳回", "一次通过", "逾期")
ZONE_COUNT_KEYS = ("逾期",)
TEACHING_DAILY = "daily"
TEACHING_DEEP_CLEAN = "deep_clean"
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def hygiene_week_start(now: datetime) -> datetime:
    """Monday 06:00 China of the hygiene week containing `now`."""
    if now.tzinfo is None:
        local = now.replace(tzinfo=CHINA_TZ)
    else:
        local = now.astimezone(CHINA_TZ)
    if local.hour < BUSINESS_DAY_CUT_HOUR:
        local = local - timedelta(days=1)
    monday = local.date() - timedelta(days=local.weekday())
    return datetime(
        monday.year,
        monday.month,
        monday.day,
        BUSINESS_DAY_CUT_HOUR,
        0,
        tzinfo=CHINA_TZ,
    )


class HygieneWorkError(ValueError):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


class HygieneWork:
    def __init__(
        self,
        conn_or_db,
        captures,
        now: Optional[Callable[[], datetime]] = None,
        notifier=None,
    ):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("HygieneWork requires an open database connection")
        if captures is None:
            raise RuntimeError("HygieneWork requires a capture store")
        self._conn = conn
        self._captures = captures
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._notifier = notifier

    def _now_dt(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value

    def _now_iso(self) -> str:
        return self._now_dt().isoformat()

    def _require_super(self, actor: dict) -> None:
        if not actor or actor.get("kind") != "super":
            raise HygieneWorkError("forbidden", "forbidden")

    async def prepare(self) -> None:
        await self._seed_zones_if_empty()
        await self._seed_overdue_clocks_if_empty()

    async def _seed_zones_if_empty(self) -> None:
        cur = await self._conn.execute("SELECT COUNT(*) AS n FROM hygiene_zones")
        row = await cur.fetchone()
        if int(dict(row)["n"]) > 0:
            return
        now = self._now_iso()
        await self._conn.executemany(
            "INSERT INTO hygiene_zones (name, created_at, updated_at) VALUES (?, ?, ?)",
            [(name, now, now) for name in SEED_ZONE_NAMES],
        )
        await self._conn.commit()
        logger.info("hygiene zones seeded count=%s", len(SEED_ZONE_NAMES))

    def _zone_from_row(self, row) -> dict:
        mapping = dict(row)
        return {"id": int(mapping["id"]), "name": mapping["name"]}

    async def list_zones(self) -> list[dict]:
        cur = await self._conn.execute(
            "SELECT id, name FROM hygiene_zones ORDER BY id ASC"
        )
        rows = await cur.fetchall()
        return [self._zone_from_row(row) for row in rows]

    async def create_zone(self, actor: dict, name: str) -> dict:
        self._require_super(actor)
        cleaned = (name or "").strip()
        if not cleaned:
            raise HygieneWorkError("invalid_zone_name", "invalid_zone_name")
        now = self._now_iso()
        try:
            cur = await self._conn.execute(
                "INSERT INTO hygiene_zones (name, created_at, updated_at) VALUES (?, ?, ?)",
                (cleaned, now, now),
            )
            await self._conn.commit()
        except sqlite3.IntegrityError as exc:
            await self._conn.rollback()
            raise HygieneWorkError("duplicate_zone", "duplicate_zone") from exc
        logger.info("hygiene zone created id=%s name=%s", cur.lastrowid, cleaned)
        return {"id": int(cur.lastrowid), "name": cleaned}

    async def _item_ids_for_zone(self, zone_id: int) -> list[int]:
        cur = await self._conn.execute(
            "SELECT id FROM hygiene_daily_items WHERE zone_id = ?",
            (int(zone_id),),
        )
        return [int(dict(row)["id"]) for row in await cur.fetchall()]

    async def _drop_daily_items(self, item_ids: list[int]) -> None:
        if not item_ids:
            return
        placeholders = ",".join("?" * len(item_ids))
        inst_cur = await self._conn.execute(
            f"SELECT id FROM hygiene_daily_instances WHERE item_id IN ({placeholders})",
            item_ids,
        )
        instance_ids = [int(dict(row)["id"]) for row in await inst_cur.fetchall()]
        if instance_ids:
            inst_ph = ",".join("?" * len(instance_ids))
            await self._conn.execute(
                f"UPDATE hygiene_daily_instances SET pending_submission_id = NULL "
                f"WHERE id IN ({inst_ph})",
                instance_ids,
            )
            await self._conn.execute(
                f"DELETE FROM hygiene_daily_submissions WHERE instance_id IN ({inst_ph})",
                instance_ids,
            )
            await self._conn.execute(
                f"DELETE FROM hygiene_daily_instances WHERE id IN ({inst_ph})",
                instance_ids,
            )
        await self._conn.execute(
            f"DELETE FROM hygiene_overdue_notices WHERE item_id IN ({placeholders})",
            item_ids,
        )
        await self._conn.execute(
            f"UPDATE hygiene_daily_items SET current_standard_id = NULL "
            f"WHERE id IN ({placeholders})",
            item_ids,
        )
        await self._conn.execute(
            f"DELETE FROM hygiene_standards WHERE item_id IN ({placeholders})",
            item_ids,
        )
        await self._conn.execute(
            f"DELETE FROM hygiene_daily_items WHERE id IN ({placeholders})",
            item_ids,
        )

    async def _drop_zone_fix_tickets(self, zone_id: int) -> None:
        cur = await self._conn.execute(
            "SELECT id FROM hygiene_fix_tickets WHERE zone_id = ?",
            (int(zone_id),),
        )
        ticket_ids = [int(dict(row)["id"]) for row in await cur.fetchall()]
        if not ticket_ids:
            return
        placeholders = ",".join("?" * len(ticket_ids))
        await self._conn.execute(
            f"UPDATE hygiene_fix_tickets SET pending_reshoot_id = NULL "
            f"WHERE id IN ({placeholders})",
            ticket_ids,
        )
        await self._conn.execute(
            f"DELETE FROM hygiene_fix_reshoots WHERE ticket_id IN ({placeholders})",
            ticket_ids,
        )
        await self._conn.execute(
            f"DELETE FROM hygiene_fix_overdue_notices WHERE ticket_id IN ({placeholders})",
            ticket_ids,
        )
        await self._conn.execute(
            f"DELETE FROM hygiene_fix_tickets WHERE id IN ({placeholders})",
            ticket_ids,
        )

    async def _drop_board_events(self, zone_id=None, item_ids=None) -> None:
        clauses = []
        params: list = []
        if zone_id is not None:
            clauses.append("zone_id = ?")
            params.append(int(zone_id))
        if item_ids:
            placeholders = ",".join("?" * len(item_ids))
            clauses.append(f"item_id IN ({placeholders})")
            params.extend(item_ids)
        if not clauses:
            return
        await self._conn.execute(
            f"DELETE FROM hygiene_board_events WHERE {' OR '.join(clauses)}",
            params,
        )

    async def delete_zone(self, actor: dict, zone_id: int) -> dict:
        self._require_super(actor)
        zone = await self._fetch_zone(zone_id)
        if zone is None:
            raise HygieneWorkError("zone_not_found", "zone_not_found")
        mapping = dict(zone)
        try:
            item_ids = await self._item_ids_for_zone(int(zone_id))
            await self._drop_daily_items(item_ids)
            await self._drop_zone_fix_tickets(int(zone_id))
            await self._drop_board_events(zone_id=int(zone_id), item_ids=item_ids)
            await self._conn.execute(
                "DELETE FROM hygiene_zones WHERE id = ?",
                (int(zone_id),),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        logger.info("hygiene zone deleted id=%s name=%s", mapping["id"], mapping["name"])
        return {"id": int(mapping["id"]), "name": mapping["name"]}

    async def delete_daily_item(self, actor: dict, item_id: int) -> dict:
        self._require_super(actor)
        item = await self._fetch_item(item_id)
        if item is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        mapping = dict(item)
        try:
            ids = [int(item_id)]
            await self._drop_daily_items(ids)
            await self._drop_board_events(item_ids=ids)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        logger.info(
            "hygiene daily item deleted id=%s zone=%s",
            mapping["id"],
            mapping["zone_id"],
        )
        return {
            "id": int(mapping["id"]),
            "zone_id": int(mapping["zone_id"]),
            "name": mapping["name"],
        }

    def _require_capture(self, capture) -> bytes:
        data = None if capture is None else capture.get("bytes")
        if not data:
            raise HygieneWorkError("standard_required", "standard_required")
        return data

    def _markup_json(self, capture) -> str:
        markup = [] if capture is None else capture.get("markup")
        if not isinstance(markup, list):
            markup = []
        return json.dumps(markup, ensure_ascii=False)

    def _parse_markup(self, raw: str):
        try:
            value = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        return value if isinstance(value, list) else []

    async def _fetch_zone(self, zone_id: int):
        cur = await self._conn.execute(
            "SELECT id, name FROM hygiene_zones WHERE id = ?",
            (zone_id,),
        )
        return await cur.fetchone()

    async def add_daily_item(self, actor: dict, zone_id: int, name: str, capture) -> dict:
        self._require_super(actor)
        data = self._require_capture(capture)
        cleaned = (name or "").strip()
        if not cleaned:
            raise HygieneWorkError("invalid_item_name", "invalid_item_name")
        zone = await self._fetch_zone(zone_id)
        if zone is None:
            raise HygieneWorkError("zone_not_found", "zone_not_found")
        content_type = (capture.get("content_type") or "image/jpeg").strip()
        capture_id = self._captures.put(data, content_type=content_type)
        now = self._now_iso()
        try:
            item_cur = await self._conn.execute(
                """INSERT INTO hygiene_daily_items
                   (zone_id, name, current_standard_id, created_at, updated_at)
                   VALUES (?, ?, NULL, ?, ?)""",
                (zone_id, cleaned, now, now),
            )
            item_id = int(item_cur.lastrowid)
            std_cur = await self._conn.execute(
                """INSERT INTO hygiene_standards
                   (item_id, capture_id, content_type, markup_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (item_id, capture_id, content_type, self._markup_json(capture), now),
            )
            standard_id = int(std_cur.lastrowid)
            await self._conn.execute(
                """UPDATE hygiene_daily_items
                   SET current_standard_id = ?, updated_at = ?
                   WHERE id = ?""",
                (standard_id, now, item_id),
            )
            await self._conn.commit()
        except sqlite3.IntegrityError as exc:
            await self._conn.rollback()
            raise HygieneWorkError("duplicate_item", "duplicate_item") from exc
        logger.info(
            "hygiene daily item created id=%s zone=%s standard=%s",
            item_id,
            zone_id,
            standard_id,
        )
        return {
            "id": item_id,
            "zone_id": int(zone_id),
            "name": cleaned,
            "current_standard_id": standard_id,
            "capture_id": capture_id,
        }

    def _item_from_join(self, mapping: dict) -> dict:
        return {
            "id": int(mapping["id"]),
            "zone_id": int(mapping["zone_id"]),
            "name": mapping["name"],
            "current_standard_id": int(mapping["current_standard_id"]),
            "capture_id": mapping["capture_id"],
            "markup": self._parse_markup(mapping.get("markup_json") or "[]"),
        }

    async def list_staff_daily_items(self) -> list[dict]:
        zones = await self.list_zones()
        cur = await self._conn.execute(
            """SELECT i.id, i.zone_id, i.name, i.current_standard_id,
                      s.capture_id, s.markup_json
               FROM hygiene_daily_items i
               JOIN hygiene_standards s ON s.id = i.current_standard_id
               WHERE i.current_standard_id IS NOT NULL
               ORDER BY i.id ASC"""
        )
        rows = await cur.fetchall()
        by_zone: dict[int, list] = {}
        for row in rows:
            item = self._item_from_join(dict(row))
            by_zone.setdefault(item["zone_id"], []).append(item)
        return [
            {"id": zone["id"], "name": zone["name"], "items": by_zone.get(zone["id"], [])}
            for zone in zones
        ]

    async def _fetch_item(self, item_id: int):
        cur = await self._conn.execute(
            """SELECT id, zone_id, name, current_standard_id
               FROM hygiene_daily_items WHERE id = ?""",
            (item_id,),
        )
        return await cur.fetchone()

    async def current_standard(self, item_id: int) -> dict:
        item = await self._fetch_item(item_id)
        if item is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        mapping = dict(item)
        standard_id = mapping.get("current_standard_id")
        if not standard_id:
            raise HygieneWorkError("standard_required", "standard_required")
        return await self.standard_by_id(int(standard_id))

    async def standard_by_id(self, standard_id: int) -> dict:
        cur = await self._conn.execute(
            """SELECT id, item_id, capture_id, content_type, markup_json, created_at
               FROM hygiene_standards WHERE id = ?""",
            (int(standard_id),),
        )
        row = await cur.fetchone()
        if row is None:
            raise HygieneWorkError("standard_required", "standard_required")
        std = dict(row)
        return {
            "id": int(std["id"]),
            "item_id": int(std["item_id"]),
            "capture_id": std["capture_id"],
            "content_type": std["content_type"],
            "markup": self._parse_markup(std.get("markup_json") or "[]"),
            "created_at": std["created_at"],
        }

    def capture_bytes(self, capture_id: str) -> bytes:
        return self._captures.get(capture_id)

    async def replace_standard(self, actor: dict, item_id: int, capture) -> dict:
        self._require_super(actor)
        data = self._require_capture(capture)
        item = await self._fetch_item(item_id)
        if item is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        content_type = (capture.get("content_type") or "image/jpeg").strip()
        capture_id = self._captures.put(data, content_type=content_type)
        now = self._now_iso()
        std_cur = await self._conn.execute(
            """INSERT INTO hygiene_standards
               (item_id, capture_id, content_type, markup_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (item_id, capture_id, content_type, self._markup_json(capture), now),
        )
        standard_id = int(std_cur.lastrowid)
        await self._conn.execute(
            """UPDATE hygiene_daily_items
               SET current_standard_id = ?, updated_at = ?
               WHERE id = ?""",
            (standard_id, now, item_id),
        )
        await self._conn.commit()
        logger.info(
            "hygiene standard replaced item=%s standard=%s",
            item_id,
            standard_id,
        )
        return {
            "id": int(item_id),
            "zone_id": int(dict(item)["zone_id"]),
            "name": dict(item)["name"],
            "current_standard_id": standard_id,
            "capture_id": capture_id,
        }

    def _actor_shift(self, actor: dict, requested: Optional[str] = None) -> str:
        if not actor or actor.get("kind") != "staff":
            raise HygieneWorkError("forbidden", "forbidden")
        picked = actor.get("shift")
        if not picked:
            raise HygieneWorkError("shift_required", "shift_required")
        target = (requested or picked).strip()
        if target not in DAILY_SHIFTS:
            raise HygieneWorkError("shift_mismatch", "shift_mismatch")
        if picked != target:
            raise HygieneWorkError("shift_mismatch", "shift_mismatch")
        return target

    def _require_live_capture(self, capture) -> bytes:
        if not capture or capture.get("live") is not True:
            raise HygieneWorkError("live_required", "live_required")
        data = capture.get("bytes")
        if not data:
            raise HygieneWorkError("capture_required", "capture_required")
        return data

    def _photographer(self, actor: dict) -> str:
        return (actor.get("phone") or actor.get("display_name") or "").strip()

    def _watermark(self, captured_at: str, zone_name: str, photographer: str) -> dict:
        return {
            "time": captured_at,
            "zone": zone_name,
            "photographer": photographer,
        }

    async def _fetch_item_with_zone(self, item_id: int):
        cur = await self._conn.execute(
            """SELECT i.id, i.zone_id, i.name, i.current_standard_id, z.name AS zone_name
               FROM hygiene_daily_items i
               JOIN hygiene_zones z ON z.id = i.zone_id
               WHERE i.id = ?""",
            (item_id,),
        )
        return await cur.fetchone()

    async def _fetch_instance(self, business_date: str, shift: str, item_id: int):
        cur = await self._conn.execute(
            """SELECT id, business_date, shift, item_id, status, pending_submission_id
               FROM hygiene_daily_instances
               WHERE business_date = ? AND shift = ? AND item_id = ?""",
            (business_date, shift, item_id),
        )
        return await cur.fetchone()

    async def _ensure_instance(self, business_date: str, shift: str, item_id: int) -> dict:
        existing = await self._fetch_instance(business_date, shift, item_id)
        if existing is not None:
            return dict(existing)
        now = self._now_iso()
        cur = await self._conn.execute(
            """INSERT INTO hygiene_daily_instances
               (business_date, shift, item_id, status, pending_submission_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, NULL, ?, ?)""",
            (business_date, shift, item_id, STATUS_TODO, now, now),
        )
        await self._conn.commit()
        return {
            "id": int(cur.lastrowid),
            "business_date": business_date,
            "shift": shift,
            "item_id": item_id,
            "status": STATUS_TODO,
            "pending_submission_id": None,
        }

    async def _fetch_submission(self, submission_id: Optional[int]):
        if not submission_id:
            return None
        cur = await self._conn.execute(
            """SELECT id, instance_id, capture_id, content_type, frozen_standard_id,
                      submitter_id, submitter_phone, zone_name, captured_at
               FROM hygiene_daily_submissions WHERE id = ?""",
            (int(submission_id),),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    def _inbox_from_parts(self, item: dict, shift: str, business_date: str, instance, submission) -> dict:
        status = STATUS_TODO
        if instance is not None:
            status = instance["status"]
        watermark = None
        if submission is not None:
            watermark = self._watermark(
                submission["captured_at"],
                submission["zone_name"],
                submission["submitter_phone"],
            )
        return {
            "item_id": int(item["id"]),
            "item_name": item["name"],
            "zone_id": int(item["zone_id"]),
            "zone_name": item["zone_name"],
            "shift": shift,
            "business_date": business_date,
            "status": status,
            "submitter_id": None if submission is None else int(submission["submitter_id"]),
            "submitter_phone": None if submission is None else submission["submitter_phone"],
            "capture_id": None if submission is None else submission["capture_id"],
            "frozen_standard_id": None if submission is None else int(submission["frozen_standard_id"]),
            "current_standard_id": int(item["current_standard_id"])
            if item.get("current_standard_id")
            else None,
            "markup": self._parse_markup(item.get("markup_json") or "[]"),
            "watermark": watermark,
        }

    async def submit_daily(
        self,
        actor: dict,
        item_id: int,
        capture,
        shift: Optional[str] = None,
    ) -> dict:
        target_shift = self._actor_shift(actor, shift)
        data = self._require_live_capture(capture)
        item_row = await self._fetch_item_with_zone(item_id)
        if item_row is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        item = dict(item_row)
        standard_id = item.get("current_standard_id")
        if not standard_id:
            raise HygieneWorkError("standard_required", "standard_required")
        photographer = self._photographer(actor)
        if not photographer:
            raise HygieneWorkError("photographer_required", "photographer_required")
        business_date = hygiene_business_date(self._now_dt())
        instance = await self._ensure_instance(business_date, target_shift, item_id)
        if instance["status"] == STATUS_PASSED:
            raise HygieneWorkError("already_accepted", "already_accepted")
        content_type = (capture.get("content_type") or "image/jpeg").strip()
        capture_id = self._captures.put(data, content_type=content_type)
        now = self._now_iso()
        cur = await self._conn.execute(
            """INSERT INTO hygiene_daily_submissions
               (instance_id, capture_id, content_type, frozen_standard_id,
                submitter_id, submitter_phone, zone_name, captured_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                instance["id"],
                capture_id,
                content_type,
                int(standard_id),
                int(actor["id"]),
                photographer,
                item["zone_name"],
                now,
                now,
            ),
        )
        submission_id = int(cur.lastrowid)
        await self._conn.execute(
            """UPDATE hygiene_daily_instances
               SET status = ?, pending_submission_id = ?, updated_at = ?
               WHERE id = ?""",
            (STATUS_PENDING, submission_id, now, instance["id"]),
        )
        await self._insert_board_event(
            BOARD_PERSON,
            EVENT_CAPTURE,
            zone_id=int(item["zone_id"]),
            employee_id=int(actor["id"]),
            item_id=int(item_id),
            shift=target_shift,
            business_date=business_date,
        )
        await self._conn.commit()
        logger.info(
            "hygiene daily submitted item=%s shift=%s capture=%s",
            item_id,
            target_shift,
            capture_id,
        )
        return {
            "item_id": int(item_id),
            "shift": target_shift,
            "business_date": business_date,
            "status": STATUS_PENDING,
            "zone_name": item["zone_name"],
            "capture_id": capture_id,
            "frozen_standard_id": int(standard_id),
            "submitter_id": int(actor["id"]),
            "submitter_phone": photographer,
            "watermark": self._watermark(now, item["zone_name"], photographer),
        }

    async def list_daily_work(self, actor: dict) -> list[dict]:
        del actor  # shop-wide; zone membership is not an inbox filter
        business_date = hygiene_business_date(self._now_dt())
        cur = await self._conn.execute(
            """SELECT i.id, i.zone_id, i.name, i.current_standard_id, z.name AS zone_name,
                      s.markup_json
               FROM hygiene_daily_items i
               JOIN hygiene_zones z ON z.id = i.zone_id
               JOIN hygiene_standards s ON s.id = i.current_standard_id
               WHERE i.current_standard_id IS NOT NULL
               ORDER BY i.id ASC"""
        )
        items = [dict(row) for row in await cur.fetchall()]
        inst_cur = await self._conn.execute(
            """SELECT id, business_date, shift, item_id, status, pending_submission_id
               FROM hygiene_daily_instances
               WHERE business_date = ?""",
            (business_date,),
        )
        instances = {}
        for row in await inst_cur.fetchall():
            mapping = dict(row)
            instances[(int(mapping["item_id"]), mapping["shift"])] = mapping
        inbox = []
        for item in items:
            for shift in DAILY_SHIFTS:
                instance = instances.get((int(item["id"]), shift))
                submission = None
                if instance is not None:
                    submission = await self._fetch_submission(
                        instance.get("pending_submission_id")
                    )
                    if submission is None and instance["status"] == STATUS_PASSED:
                        sub_cur = await self._conn.execute(
                            """SELECT id, instance_id, capture_id, content_type,
                                      frozen_standard_id, submitter_id, submitter_phone,
                                      zone_name, captured_at
                               FROM hygiene_daily_submissions
                               WHERE instance_id = ?
                               ORDER BY id DESC LIMIT 1""",
                            (instance["id"],),
                        )
                        sub_row = await sub_cur.fetchone()
                        submission = None if sub_row is None else dict(sub_row)
                inbox.append(
                    self._inbox_from_parts(item, shift, business_date, instance, submission)
                )
        return inbox

    async def get_daily_review(self, item_id: int, shift: str) -> dict:
        if shift not in DAILY_SHIFTS:
            raise HygieneWorkError("shift_mismatch", "shift_mismatch")
        business_date = hygiene_business_date(self._now_dt())
        instance_row = await self._fetch_instance(business_date, shift, item_id)
        if instance_row is None:
            raise HygieneWorkError("not_pending", "not_pending")
        instance = dict(instance_row)
        if instance["status"] != STATUS_PENDING:
            raise HygieneWorkError("not_pending", "not_pending")
        submission = await self._fetch_submission(instance.get("pending_submission_id"))
        if submission is None:
            raise HygieneWorkError("not_pending", "not_pending")
        standard = await self.standard_by_id(int(submission["frozen_standard_id"]))
        return {
            "item_id": int(item_id),
            "shift": shift,
            "business_date": business_date,
            "status": STATUS_PENDING,
            "capture_id": submission["capture_id"],
            "content_type": submission["content_type"],
            "frozen_standard_id": int(submission["frozen_standard_id"]),
            "frozen_markup": standard["markup"],
            "submitter_id": int(submission["submitter_id"]),
            "submitter_phone": submission["submitter_phone"],
            "zone_name": submission["zone_name"],
            "watermark": self._watermark(
                submission["captured_at"],
                submission["zone_name"],
                submission["submitter_phone"],
            ),
        }

    def _require_reviewer(self, actor: dict, submitter_id: int) -> None:
        if not actor:
            raise HygieneWorkError("forbidden", "forbidden")
        if actor.get("kind") == "super":
            return
        if actor.get("kind") != "staff" or actor.get("permission") != PERMISSION_ADMIN:
            raise HygieneWorkError("forbidden", "forbidden")
        if int(actor.get("id") or 0) == int(submitter_id):
            raise HygieneWorkError("cannot_self_accept", "cannot_self_accept")

    async def _pending_instance(self, item_id: int, shift: str) -> tuple[dict, dict]:
        if shift not in DAILY_SHIFTS:
            raise HygieneWorkError("shift_mismatch", "shift_mismatch")
        business_date = hygiene_business_date(self._now_dt())
        instance_row = await self._fetch_instance(business_date, shift, item_id)
        if instance_row is None:
            raise HygieneWorkError("not_pending", "not_pending")
        instance = dict(instance_row)
        if instance["status"] != STATUS_PENDING:
            raise HygieneWorkError("not_pending", "not_pending")
        submission = await self._fetch_submission(instance.get("pending_submission_id"))
        if submission is None:
            raise HygieneWorkError("not_pending", "not_pending")
        return instance, submission

    async def _daily_was_rejected(
        self, item_id: int, shift: str, business_date: str
    ) -> bool:
        cur = await self._conn.execute(
            """SELECT 1 FROM hygiene_board_events
               WHERE board = ? AND event_type = ? AND item_id = ?
                 AND shift = ? AND business_date = ? LIMIT 1""",
            (BOARD_PERSON, EVENT_REJECT, int(item_id), shift, business_date),
        )
        return await cur.fetchone() is not None

    async def accept_daily(self, actor: dict, item_id: int, shift: str) -> dict:
        instance, submission = await self._pending_instance(item_id, shift)
        self._require_reviewer(actor, submission["submitter_id"])
        now = self._now_iso()
        await self._conn.execute(
            """UPDATE hygiene_daily_instances
               SET status = ?, updated_at = ?
               WHERE id = ?""",
            (STATUS_PASSED, now, instance["id"]),
        )
        if not await self._daily_was_rejected(
            int(item_id), shift, instance["business_date"]
        ):
            item_row = await self._fetch_item(item_id)
            zone_id = None if item_row is None else int(dict(item_row)["zone_id"])
            await self._insert_board_event(
                BOARD_PERSON,
                EVENT_FIRST_PASS,
                zone_id=zone_id,
                employee_id=int(submission["submitter_id"]),
                item_id=int(item_id),
                shift=shift,
                business_date=instance["business_date"],
            )
        await self._conn.commit()
        logger.info("hygiene daily accepted item=%s shift=%s", item_id, shift)
        return {
            "item_id": int(item_id),
            "shift": shift,
            "business_date": instance["business_date"],
            "status": STATUS_PASSED,
        }

    async def reject_daily(self, actor: dict, item_id: int, shift: str) -> dict:
        instance, submission = await self._pending_instance(item_id, shift)
        self._require_reviewer(actor, submission["submitter_id"])
        now = self._now_iso()
        await self._conn.execute(
            """UPDATE hygiene_daily_instances
               SET status = ?, pending_submission_id = NULL, updated_at = ?
               WHERE id = ?""",
            (STATUS_TODO, now, instance["id"]),
        )
        item_row = await self._fetch_item(item_id)
        zone_id = None if item_row is None else int(dict(item_row)["zone_id"])
        await self._insert_board_event(
            BOARD_PERSON,
            EVENT_REJECT,
            zone_id=zone_id,
            employee_id=int(submission["submitter_id"]),
            item_id=int(item_id),
            shift=shift,
            business_date=instance["business_date"],
        )
        await self._conn.commit()
        logger.info("hygiene daily rejected item=%s shift=%s", item_id, shift)
        return {
            "item_id": int(item_id),
            "shift": shift,
            "business_date": instance["business_date"],
            "status": STATUS_TODO,
        }

    def _parse_hhmm(self, raw: str) -> str:
        cleaned = (raw or "").strip()
        if not _HHMM_RE.fullmatch(cleaned):
            raise HygieneWorkError("invalid_clock", "invalid_clock")
        return cleaned

    async def _setting(self, key: str) -> Optional[str]:
        cur = await self._conn.execute(
            "SELECT value FROM hygiene_settings WHERE key = ?",
            (key,),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)["value"]

    async def _upsert_setting(self, key: str, value: str) -> None:
        now = self._now_iso()
        await self._conn.execute(
            """INSERT INTO hygiene_settings (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                   updated_at = excluded.updated_at""",
            (key, value, now),
        )

    async def _seed_overdue_clocks_if_empty(self) -> None:
        day = await self._setting(SETTING_DAY_OVERDUE)
        night = await self._setting(SETTING_NIGHT_OVERDUE)
        deep = await self._setting(SETTING_DEEP_CLEAN_OVERDUE)
        if day and night and deep:
            return
        if not day:
            await self._upsert_setting(SETTING_DAY_OVERDUE, DEFAULT_DAY_OVERDUE_HHMM)
        if not night:
            await self._upsert_setting(SETTING_NIGHT_OVERDUE, DEFAULT_NIGHT_OVERDUE_HHMM)
        if not deep:
            await self._upsert_setting(
                SETTING_DEEP_CLEAN_OVERDUE, DEFAULT_DEEP_CLEAN_OVERDUE_HHMM
            )
        await self._conn.commit()

    async def get_daily_overdue_clocks(self) -> dict:
        day = await self._setting(SETTING_DAY_OVERDUE) or DEFAULT_DAY_OVERDUE_HHMM
        night = await self._setting(SETTING_NIGHT_OVERDUE) or DEFAULT_NIGHT_OVERDUE_HHMM
        return {"day_hhmm": day, "night_hhmm": night}

    async def set_daily_overdue_clocks(
        self, actor: dict, day_hhmm: str, night_hhmm: str
    ) -> dict:
        self._require_super(actor)
        day = self._parse_hhmm(day_hhmm)
        night = self._parse_hhmm(night_hhmm)
        await self._upsert_setting(SETTING_DAY_OVERDUE, day)
        await self._upsert_setting(SETTING_NIGHT_OVERDUE, night)
        await self._conn.commit()
        logger.info("hygiene overdue clocks day=%s night=%s", day, night)
        return {"day_hhmm": day, "night_hhmm": night}

    async def get_deep_clean_overdue_clock(self) -> dict:
        hhmm = await self._setting(SETTING_DEEP_CLEAN_OVERDUE) or DEFAULT_DEEP_CLEAN_OVERDUE_HHMM
        return {"hhmm": hhmm}

    async def set_deep_clean_overdue_clock(self, actor: dict, hhmm: str) -> dict:
        self._require_super(actor)
        cleaned = self._parse_hhmm(hhmm)
        await self._upsert_setting(SETTING_DEEP_CLEAN_OVERDUE, cleaned)
        await self._conn.commit()
        logger.info("hygiene deep-clean overdue clock=%s", cleaned)
        return {"hhmm": cleaned}

    def _clock_reached(self, now: datetime, hhmm: str) -> bool:
        local = now.astimezone(CHINA_TZ) if now.tzinfo else now.replace(tzinfo=CHINA_TZ)
        hour, minute = hhmm.split(":")
        return local.hour * 60 + local.minute >= int(hour) * 60 + int(minute)

    async def _catalog_daily_items(self) -> list:
        cur = await self._conn.execute(
            """SELECT i.id, i.zone_id, i.name, z.name AS zone_name
               FROM hygiene_daily_items i
               JOIN hygiene_zones z ON z.id = i.zone_id
               WHERE i.current_standard_id IS NOT NULL
               ORDER BY i.id ASC"""
        )
        return [dict(row) for row in await cur.fetchall()]

    async def _notice_exists(self, business_date: str, shift: str, item_id: int) -> bool:
        cur = await self._conn.execute(
            """SELECT 1 FROM hygiene_overdue_notices
               WHERE business_date = ? AND shift = ? AND item_id = ?""",
            (business_date, shift, item_id),
        )
        return await cur.fetchone() is not None

    def _overdue_group_text(self, business_date: str, shift: str, item: dict) -> str:
        return (
            f"【卫生逾期】{business_date} {shift} {item['zone_name']}「{item['name']}」"
            "仍未提交，请到员工卫生入口补拍。"
        )

    async def _record_overdue_notice(
        self, business_date: str, shift: str, item: dict
    ) -> None:
        now = self._now_iso()
        await self._conn.execute(
            """INSERT INTO hygiene_overdue_notices
               (business_date, shift, item_id, zone_id, notified_at)
               VALUES (?, ?, ?, ?, ?)""",
            (business_date, shift, int(item["id"]), int(item["zone_id"]), now),
        )
        await self._insert_board_event(
            BOARD_ZONE,
            EVENT_MISSED_DAILY,
            zone_id=int(item["zone_id"]),
            item_id=int(item["id"]),
            shift=shift,
            business_date=business_date,
        )
        await self._conn.commit()

    def _event_from_row(self, row) -> dict:
        mapping = dict(row)
        zone_id = mapping.get("zone_id")
        employee_id = mapping.get("employee_id")
        item_id = mapping.get("item_id")
        return {
            "id": int(mapping["id"]),
            "board": mapping["board"],
            "event_type": mapping["event_type"],
            "zone_id": None if zone_id is None else int(zone_id),
            "employee_id": None if employee_id is None else int(employee_id),
            "item_id": None if item_id is None else int(item_id),
            "shift": mapping.get("shift"),
            "business_date": mapping.get("business_date"),
            "occurred_at": mapping["occurred_at"],
        }

    async def _insert_board_event(
        self,
        board: str,
        event_type: str,
        *,
        zone_id=None,
        employee_id=None,
        item_id=None,
        shift=None,
        business_date=None,
    ) -> None:
        await self._conn.execute(
            """INSERT INTO hygiene_board_events
               (board, event_type, zone_id, employee_id, item_id, shift,
                business_date, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                board,
                event_type,
                zone_id,
                employee_id,
                item_id,
                shift,
                business_date,
                self._now_iso(),
            ),
        )

    async def list_zone_board_events(self, zone_id=None) -> list:
        sql = """SELECT id, board, event_type, zone_id, employee_id, item_id,
                        shift, business_date, occurred_at
                 FROM hygiene_board_events
                 WHERE board = ?"""
        params = [BOARD_ZONE]
        if zone_id is not None:
            sql += " AND zone_id = ?"
            params.append(int(zone_id))
        sql += " ORDER BY id ASC"
        cur = await self._conn.execute(sql, params)
        return [self._event_from_row(row) for row in await cur.fetchall()]

    async def list_person_board_events(self, employee_id=None) -> list:
        sql = """SELECT id, board, event_type, zone_id, employee_id, item_id,
                        shift, business_date, occurred_at
                 FROM hygiene_board_events
                 WHERE board = ?"""
        params = [BOARD_PERSON]
        if employee_id is not None:
            sql += " AND employee_id = ?"
            params.append(int(employee_id))
        sql += " ORDER BY id ASC"
        cur = await self._conn.execute(sql, params)
        return [self._event_from_row(row) for row in await cur.fetchall()]

    def _week_bounds(self, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
        clock = now or self._now_dt()
        start = hygiene_week_start(clock)
        return start, start + timedelta(days=7)

    async def _events_in_week(self, board: str, now: Optional[datetime] = None) -> list:
        start, end = self._week_bounds(now)
        cur = await self._conn.execute(
            """SELECT id, board, event_type, zone_id, employee_id, item_id,
                      shift, business_date, occurred_at
               FROM hygiene_board_events
               WHERE board = ? AND occurred_at >= ? AND occurred_at < ?
               ORDER BY id ASC""",
            (board, start.isoformat(), end.isoformat()),
        )
        return [self._event_from_row(row) for row in await cur.fetchall()]

    async def person_board(self, now: Optional[datetime] = None) -> list:
        events = await self._events_in_week(BOARD_PERSON, now)
        by_id = {}
        for event in events:
            employee_id = event.get("employee_id")
            if employee_id is None:
                continue
            row = by_id.setdefault(
                int(employee_id),
                {
                    "employee_id": int(employee_id),
                    "phone": None,
                    **{key: 0 for key in PERSON_COUNT_KEYS},
                },
            )
            kind = event["event_type"]
            if kind in PERSON_COUNT_KEYS:
                row[kind] += 1
        if by_id:
            placeholders = ",".join("?" * len(by_id))
            cur = await self._conn.execute(
                f"SELECT id, phone FROM hygiene_employees WHERE id IN ({placeholders})",
                list(by_id),
            )
            for row in await cur.fetchall():
                mapping = dict(row)
                by_id[int(mapping["id"])]["phone"] = mapping["phone"]
        people = list(by_id.values())
        people.sort(key=lambda row: (-row["逾期"], -row["驳回"], row["employee_id"]))
        return people

    async def zone_board(self, now: Optional[datetime] = None) -> list:
        events = await self._events_in_week(BOARD_ZONE, now)
        by_id = {}
        for event in events:
            zone_id = event.get("zone_id")
            if zone_id is None:
                continue
            row = by_id.setdefault(
                int(zone_id),
                {
                    "zone_id": int(zone_id),
                    "zone_name": None,
                    **{key: 0 for key in ZONE_COUNT_KEYS},
                },
            )
            kind = event["event_type"]
            if kind in ZONE_COUNT_KEYS:
                row[kind] += 1
        if by_id:
            placeholders = ",".join("?" * len(by_id))
            cur = await self._conn.execute(
                f"SELECT id, name FROM hygiene_zones WHERE id IN ({placeholders})",
                list(by_id),
            )
            for row in await cur.fetchall():
                mapping = dict(row)
                by_id[int(mapping["id"])]["zone_name"] = mapping["name"]
        zones = list(by_id.values())
        zones.sort(key=lambda row: (-row["逾期"], row["zone_id"]))
        return zones

    async def list_boards(self, now: Optional[datetime] = None) -> dict:
        start, end = self._week_bounds(now)
        return {
            "week_start": start.isoformat(),
            "week_end": end.isoformat(),
            "people": await self.person_board(now),
            "zones": await self.zone_board(now),
        }

    async def sweep_overdue(self) -> list:
        now = self._now_dt()
        business_date = hygiene_business_date(now)
        clocks = await self.get_daily_overdue_clocks()
        shift_clocks = (
            (SHIFT_DAY, clocks["day_hhmm"]),
            (SHIFT_NIGHT, clocks["night_hhmm"]),
        )
        items = await self._catalog_daily_items()
        notified = []
        for shift, hhmm in shift_clocks:
            if not self._clock_reached(now, hhmm):
                continue
            for item in items:
                instance = await self._fetch_instance(
                    business_date, shift, int(item["id"])
                )
                status = STATUS_TODO if instance is None else dict(instance)["status"]
                if status != STATUS_TODO:
                    continue
                if await self._notice_exists(business_date, shift, int(item["id"])):
                    continue
                text = self._overdue_group_text(business_date, shift, item)
                if self._notifier is not None:
                    await self._notifier.notify_group_text(text)
                await self._record_overdue_notice(business_date, shift, item)
                notified.append(
                    {
                        "business_date": business_date,
                        "shift": shift,
                        "item_id": int(item["id"]),
                        "zone_id": int(item["zone_id"]),
                        "text": text,
                    }
                )
        deep_notice = await self._sweep_deep_clean_overdue(now, business_date)
        if deep_notice is not None:
            notified.append(deep_notice)
        notified.extend(await self._sweep_fix_overdue(now))
        return notified

    async def _deep_clean_complete(self, business_date: str) -> bool:
        weekday = self._weekday_of(business_date)
        items = await self._catalog_deep_clean_items(weekday)
        if not items:
            return True
        for item in items:
            instance = await self._fetch_deep_clean_instance(business_date, int(item["id"]))
            if instance is None or instance["status"] != STATUS_PASSED:
                return False
        return True

    async def _deep_clean_notice_exists(self, business_date: str) -> bool:
        cur = await self._conn.execute(
            """SELECT 1 FROM hygiene_deep_clean_overdue_notices
               WHERE business_date = ?""",
            (business_date,),
        )
        return await cur.fetchone() is not None

    def _deep_clean_overdue_text(self, business_date: str) -> str:
        return f"【卫生逾期】{business_date} 专项卫生仍未完成，请到员工卫生入口补拍。"

    async def _record_deep_clean_overdue(self, business_date: str) -> None:
        now = self._now_iso()
        await self._conn.execute(
            """INSERT INTO hygiene_deep_clean_overdue_notices
               (business_date, notified_at) VALUES (?, ?)""",
            (business_date, now),
        )
        await self._conn.commit()

    async def _sweep_deep_clean_overdue(self, now: datetime, business_date: str):
        items = await self._catalog_deep_clean_items(self._weekday_of(business_date))
        if not items:
            return None
        clock = await self.get_deep_clean_overdue_clock()
        if not self._clock_reached(now, clock["hhmm"]):
            return None
        if await self._deep_clean_complete(business_date):
            return None
        if await self._deep_clean_notice_exists(business_date):
            return None
        text = self._deep_clean_overdue_text(business_date)
        if self._notifier is not None:
            await self._notifier.notify_group_text(text)
        await self._record_deep_clean_overdue(business_date)
        return {
            "business_date": business_date,
            "kind": "专项卫生",
            "text": text,
        }

    async def list_deep_clean_calendar(self, from_date: str, to_date: str) -> list:
        start = datetime.strptime(from_date, "%Y-%m-%d").date()
        end = datetime.strptime(to_date, "%Y-%m-%d").date()
        if end < start:
            return []
        days = []
        cursor = start
        while cursor <= end:
            business_date = cursor.isoformat()
            weekday = cursor.weekday()
            items = await self._catalog_deep_clean_items(weekday)
            if items:
                complete = await self._deep_clean_complete(business_date)
                missed = await self._deep_clean_notice_exists(business_date)
                if complete:
                    status = CALENDAR_DONE
                elif missed:
                    status = CALENDAR_MISSED
                else:
                    status = CALENDAR_TODO
                days.append(
                    {
                        "business_date": business_date,
                        "weekday": weekday,
                        "weekday_name": WEEKDAY_NAMES[weekday],
                        "status": status,
                        "item_count": len(items),
                    }
                )
            cursor = cursor + timedelta(days=1)
        return days

    def _parse_weekday(self, weekday) -> int:
        if isinstance(weekday, str) and weekday in WEEKDAY_NAMES:
            return WEEKDAY_NAMES.index(weekday)
        try:
            value = int(weekday)
        except (TypeError, ValueError) as exc:
            raise HygieneWorkError("invalid_weekday", "invalid_weekday") from exc
        if value < 0 or value > 6:
            raise HygieneWorkError("invalid_weekday", "invalid_weekday")
        return value

    def _weekday_of(self, business_date: str) -> int:
        return datetime.strptime(business_date, "%Y-%m-%d").weekday()

    def _deep_clean_watermark(
        self, captured_at: str, item_name: str, photographer: str
    ) -> dict:
        return {
            "time": captured_at,
            "item_name": item_name,
            "photographer": photographer,
        }

    async def add_deep_clean_item(self, actor: dict, weekday, name: str) -> dict:
        self._require_super(actor)
        day = self._parse_weekday(weekday)
        cleaned = (name or "").strip()
        if not cleaned:
            raise HygieneWorkError("invalid_item_name", "invalid_item_name")
        now = self._now_iso()
        try:
            cur = await self._conn.execute(
                """INSERT INTO hygiene_deep_clean_items
                   (weekday, name, created_at, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (day, cleaned, now, now),
            )
            await self._conn.commit()
        except sqlite3.IntegrityError as exc:
            await self._conn.rollback()
            raise HygieneWorkError("duplicate_item", "duplicate_item") from exc
        logger.info(
            "hygiene deep-clean item created id=%s weekday=%s name=%s",
            cur.lastrowid,
            day,
            cleaned,
        )
        return {
            "id": int(cur.lastrowid),
            "weekday": day,
            "weekday_name": WEEKDAY_NAMES[day],
            "name": cleaned,
        }

    async def remove_deep_clean_item(self, actor: dict, item_id: int) -> dict:
        self._require_super(actor)
        cur = await self._conn.execute(
            "SELECT id, weekday, name FROM hygiene_deep_clean_items WHERE id = ?",
            (int(item_id),),
        )
        row = await cur.fetchone()
        if row is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        mapping = dict(row)
        await self._conn.execute(
            "DELETE FROM hygiene_deep_clean_items WHERE id = ?",
            (int(item_id),),
        )
        await self._conn.commit()
        return {
            "id": int(mapping["id"]),
            "weekday": int(mapping["weekday"]),
            "name": mapping["name"],
        }

    async def list_deep_clean_items(self, weekday=None) -> list:
        sql = """SELECT id, weekday, name FROM hygiene_deep_clean_items"""
        params: list = []
        if weekday is not None:
            sql += " WHERE weekday = ?"
            params.append(self._parse_weekday(weekday))
        sql += " ORDER BY weekday ASC, id ASC"
        cur = await self._conn.execute(sql, params)
        rows = await cur.fetchall()
        return [
            {
                "id": int(mapping["id"]),
                "weekday": int(mapping["weekday"]),
                "weekday_name": WEEKDAY_NAMES[int(mapping["weekday"])],
                "name": mapping["name"],
            }
            for mapping in (dict(row) for row in rows)
        ]

    async def _catalog_deep_clean_items(self, weekday: int) -> list:
        return await self.list_deep_clean_items(weekday)

    async def _fetch_deep_clean_item(self, item_id: int):
        cur = await self._conn.execute(
            "SELECT id, weekday, name FROM hygiene_deep_clean_items WHERE id = ?",
            (int(item_id),),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def _fetch_deep_clean_instance(self, business_date: str, item_id: int):
        cur = await self._conn.execute(
            """SELECT id, business_date, item_id, status, pending_submission_id
               FROM hygiene_deep_clean_instances
               WHERE business_date = ? AND item_id = ?""",
            (business_date, int(item_id)),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def _fetch_deep_clean_submission(self, submission_id):
        if not submission_id:
            return None
        cur = await self._conn.execute(
            """SELECT id, instance_id, before_capture_id, after_capture_id,
                      before_content_type, after_content_type, submitter_id,
                      submitter_phone, item_name, before_captured_at,
                      after_captured_at
               FROM hygiene_deep_clean_submissions WHERE id = ?""",
            (int(submission_id),),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    def _deep_clean_row(self, item: dict, business_date: str, instance, submission) -> dict:
        status = STATUS_TODO
        if instance is not None:
            status = instance["status"]
        before_wm = None
        after_wm = None
        if submission is not None:
            before_wm = self._deep_clean_watermark(
                submission["before_captured_at"],
                submission["item_name"],
                submission["submitter_phone"],
            )
            after_wm = self._deep_clean_watermark(
                submission["after_captured_at"],
                submission["item_name"],
                submission["submitter_phone"],
            )
        return {
            "item_id": int(item["id"]),
            "item_name": item["name"],
            "weekday": int(item["weekday"]),
            "business_date": business_date,
            "status": status,
            "submitter_id": None if submission is None else int(submission["submitter_id"]),
            "submitter_phone": None if submission is None else submission["submitter_phone"],
            "before_capture_id": None if submission is None else submission["before_capture_id"],
            "after_capture_id": None if submission is None else submission["after_capture_id"],
            "before_watermark": before_wm,
            "after_watermark": after_wm,
            "watermark": after_wm,
        }

    async def list_deep_clean_work(self, actor: dict) -> dict:
        del actor  # shop-wide; 班次 does not gate 专项卫生
        business_date = hygiene_business_date(self._now_dt())
        weekday = self._weekday_of(business_date)
        items = await self._catalog_deep_clean_items(weekday)
        rows = []
        for item in items:
            instance = await self._fetch_deep_clean_instance(business_date, int(item["id"]))
            submission = None
            if instance is not None:
                submission = await self._fetch_deep_clean_submission(
                    instance.get("pending_submission_id")
                )
                if submission is None and instance["status"] == STATUS_PASSED:
                    sub_cur = await self._conn.execute(
                        """SELECT id, instance_id, before_capture_id, after_capture_id,
                                  before_content_type, after_content_type, submitter_id,
                                  submitter_phone, item_name, before_captured_at,
                                  after_captured_at
                           FROM hygiene_deep_clean_submissions
                           WHERE instance_id = ?
                           ORDER BY id DESC LIMIT 1""",
                        (instance["id"],),
                    )
                    sub_row = await sub_cur.fetchone()
                    submission = None if sub_row is None else dict(sub_row)
            rows.append(self._deep_clean_row(item, business_date, instance, submission))
        status = CALENDAR_DONE if rows and all(
            row["status"] == STATUS_PASSED for row in rows
        ) else CALENDAR_TODO
        if not rows:
            status = "无专项"
        return {
            "business_date": business_date,
            "weekday": weekday,
            "weekday_name": WEEKDAY_NAMES[weekday],
            "status": status,
            "items": rows,
        }

    def _require_staff_submitter(self, actor: dict) -> None:
        if not actor or actor.get("kind") != "staff":
            raise HygieneWorkError("forbidden", "forbidden")

    async def _ensure_deep_clean_instance(self, business_date: str, item_id: int) -> dict:
        existing = await self._fetch_deep_clean_instance(business_date, item_id)
        if existing is not None:
            return existing
        now = self._now_iso()
        cur = await self._conn.execute(
            """INSERT INTO hygiene_deep_clean_instances
               (business_date, item_id, status, pending_submission_id, created_at, updated_at)
               VALUES (?, ?, ?, NULL, ?, ?)""",
            (business_date, int(item_id), STATUS_TODO, now, now),
        )
        await self._conn.commit()
        return {
            "id": int(cur.lastrowid),
            "business_date": business_date,
            "item_id": int(item_id),
            "status": STATUS_TODO,
            "pending_submission_id": None,
        }

    async def submit_deep_clean_pair(self, actor: dict, item_id: int, before, after) -> dict:
        """Submit a live before/after pair. Last writer of the complete pair is the submitter."""
        self._require_staff_submitter(actor)
        before_data = self._require_live_capture(before)
        after_data = self._require_live_capture(after)
        item = await self._fetch_deep_clean_item(item_id)
        if item is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        photographer = self._photographer(actor)
        if not photographer:
            raise HygieneWorkError("photographer_required", "photographer_required")
        business_date = hygiene_business_date(self._now_dt())
        weekday = self._weekday_of(business_date)
        if int(item["weekday"]) != weekday:
            raise HygieneWorkError("item_not_found", "item_not_found")
        instance = await self._ensure_deep_clean_instance(business_date, int(item_id))
        if instance["status"] == STATUS_PASSED:
            raise HygieneWorkError("already_accepted", "already_accepted")
        before_type = (before.get("content_type") or "image/jpeg").strip()
        after_type = (after.get("content_type") or "image/jpeg").strip()
        before_id = self._captures.put(before_data, content_type=before_type)
        after_id = self._captures.put(after_data, content_type=after_type)
        now = self._now_iso()
        cur = await self._conn.execute(
            """INSERT INTO hygiene_deep_clean_submissions
               (instance_id, before_capture_id, after_capture_id, before_content_type,
                after_content_type, submitter_id, submitter_phone, item_name,
                before_captured_at, after_captured_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                instance["id"],
                before_id,
                after_id,
                before_type,
                after_type,
                int(actor["id"]),
                photographer,
                item["name"],
                now,
                now,
                now,
            ),
        )
        submission_id = int(cur.lastrowid)
        await self._conn.execute(
            """UPDATE hygiene_deep_clean_instances
               SET status = ?, pending_submission_id = ?, updated_at = ?
               WHERE id = ?""",
            (STATUS_PENDING, submission_id, now, instance["id"]),
        )
        await self._conn.commit()
        logger.info(
            "hygiene deep-clean submitted item=%s before=%s after=%s",
            item_id,
            before_id,
            after_id,
        )
        after_wm = self._deep_clean_watermark(now, item["name"], photographer)
        return {
            "item_id": int(item_id),
            "item_name": item["name"],
            "business_date": business_date,
            "status": STATUS_PENDING,
            "submitter_id": int(actor["id"]),
            "submitter_phone": photographer,
            "before_capture_id": before_id,
            "after_capture_id": after_id,
            "before_watermark": self._deep_clean_watermark(now, item["name"], photographer),
            "after_watermark": after_wm,
            "watermark": after_wm,
        }

    async def _pending_deep_clean(self, item_id: int) -> tuple:
        business_date = hygiene_business_date(self._now_dt())
        instance = await self._fetch_deep_clean_instance(business_date, int(item_id))
        if instance is None or instance["status"] != STATUS_PENDING:
            raise HygieneWorkError("not_pending", "not_pending")
        submission = await self._fetch_deep_clean_submission(
            instance.get("pending_submission_id")
        )
        if submission is None:
            raise HygieneWorkError("not_pending", "not_pending")
        return instance, submission

    async def get_deep_clean_review(self, item_id: int) -> dict:
        instance, submission = await self._pending_deep_clean(item_id)
        item = await self._fetch_deep_clean_item(item_id)
        name = submission["item_name"] if item is None else item["name"]
        before_wm = self._deep_clean_watermark(
            submission["before_captured_at"],
            name,
            submission["submitter_phone"],
        )
        after_wm = self._deep_clean_watermark(
            submission["after_captured_at"],
            name,
            submission["submitter_phone"],
        )
        return {
            "item_id": int(item_id),
            "item_name": name,
            "business_date": instance["business_date"],
            "status": STATUS_PENDING,
            "submitter_id": int(submission["submitter_id"]),
            "submitter_phone": submission["submitter_phone"],
            "before_capture_id": submission["before_capture_id"],
            "after_capture_id": submission["after_capture_id"],
            "before_content_type": submission["before_content_type"],
            "after_content_type": submission["after_content_type"],
            "before_watermark": before_wm,
            "after_watermark": after_wm,
            "watermark": after_wm,
        }

    async def accept_deep_clean_pair(self, actor: dict, item_id: int) -> dict:
        instance, submission = await self._pending_deep_clean(item_id)
        self._require_reviewer(actor, submission["submitter_id"])
        now = self._now_iso()
        await self._conn.execute(
            """UPDATE hygiene_deep_clean_instances
               SET status = ?, updated_at = ?
               WHERE id = ?""",
            (STATUS_PASSED, now, instance["id"]),
        )
        await self._conn.commit()
        logger.info("hygiene deep-clean accepted item=%s", item_id)
        work = await self.list_deep_clean_work(actor)
        return {
            "item_id": int(item_id),
            "business_date": instance["business_date"],
            "status": STATUS_PASSED,
            "task_status": work["status"],
        }

    async def reject_deep_clean_pair(self, actor: dict, item_id: int) -> dict:
        instance, submission = await self._pending_deep_clean(item_id)
        self._require_reviewer(actor, submission["submitter_id"])
        now = self._now_iso()
        await self._conn.execute(
            """UPDATE hygiene_deep_clean_instances
               SET status = ?, pending_submission_id = NULL, updated_at = ?
               WHERE id = ?""",
            (STATUS_TODO, now, instance["id"]),
        )
        await self._conn.commit()
        logger.info("hygiene deep-clean rejected item=%s", item_id)
        return {
            "item_id": int(item_id),
            "business_date": instance["business_date"],
            "status": STATUS_TODO,
        }

    def _as_duration(self, duration) -> timedelta:
        if isinstance(duration, timedelta):
            if duration.total_seconds() <= 0:
                raise HygieneWorkError("invalid_duration", "invalid_duration")
            return duration
        raise HygieneWorkError("invalid_duration", "invalid_duration")

    def _parse_iso(self, raw: str) -> datetime:
        value = datetime.fromisoformat(raw)
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value

    def _duration_label(self, seconds: int) -> str:
        hours = seconds / 3600
        if hours == int(hours):
            return f"{int(hours)} 小时"
        return f"{hours:g} 小时"

    def _require_fix_opener(self, actor: dict) -> None:
        if actor and actor.get("kind") == "super":
            return
        if (
            actor
            and actor.get("kind") == "staff"
            and actor.get("permission") == PERMISSION_ADMIN
        ):
            return
        raise HygieneWorkError("forbidden", "forbidden")

    def _is_fix_opener(self, actor: dict, ticket: dict) -> bool:
        if not actor:
            return False
        if ticket["opener_kind"] == OPENER_SUPER:
            return actor.get("kind") == "super"
        if actor.get("kind") != "staff":
            return False
        return int(actor.get("id") or 0) == int(ticket["opener_id"] or 0)

    def _require_fix_reviewer(self, actor: dict, ticket: dict) -> None:
        if self._is_fix_opener(actor, ticket):
            return
        deadline = self._parse_iso(ticket["deadline"])
        if self._now_dt() < deadline:
            raise HygieneWorkError("forbidden", "forbidden")
        if actor and actor.get("kind") == "super":
            return
        if (
            actor
            and actor.get("kind") == "staff"
            and actor.get("permission") == PERMISSION_ADMIN
        ):
            return
        raise HygieneWorkError("forbidden", "forbidden")

    def _fix_open_text(self, zone_name: str, ticket_type: str, body: str, seconds: int) -> str:
        return (
            f"【整改单】{zone_name} · {ticket_type}：{body}"
            f" 时限 {self._duration_label(seconds)}。"
            "请到员工卫生入口回拍。"
        )

    def _fix_overdue_text(self, ticket: dict) -> str:
        return (
            f"【卫生逾期】整改单 {ticket['zone_name']} · {ticket['ticket_type']}"
            "已到时限仍未完成，请到员工卫生入口处理。"
        )

    def _fix_row(self, ticket: dict, reshoot=None) -> dict:
        opener_id = ticket.get("opener_id")
        watermark = None
        reshoot_capture_id = None
        if reshoot is not None:
            reshoot_capture_id = reshoot["capture_id"]
            watermark = self._watermark(
                reshoot["captured_at"],
                reshoot["zone_name"],
                reshoot["photographer_phone"],
            )
        return {
            "id": int(ticket["id"]),
            "zone_id": int(ticket["zone_id"]),
            "zone_name": ticket["zone_name"],
            "ticket_type": ticket["ticket_type"],
            "body_text": ticket["body_text"],
            "deadline": ticket["deadline"],
            "opener_kind": ticket["opener_kind"],
            "opener_id": None if opener_id is None else int(opener_id),
            "status": ticket["status"],
            "capture_id": ticket["capture_id"],
            "content_type": ticket.get("content_type"),
            "markup": self._parse_markup(ticket.get("markup_json") or "[]"),
            "reshoot_capture_id": reshoot_capture_id,
            "reshoot_content_type": None if reshoot is None else reshoot.get("content_type"),
            "watermark": watermark,
        }

    async def _fetch_fix_ticket(self, ticket_id: int):
        cur = await self._conn.execute(
            """SELECT t.id, t.zone_id, t.ticket_type, t.body_text, t.duration_seconds,
                      t.deadline, t.opener_kind, t.opener_id, t.opener_phone, t.status,
                      t.capture_id, t.content_type, t.markup_json, t.pending_reshoot_id,
                      z.name AS zone_name
               FROM hygiene_fix_tickets t
               JOIN hygiene_zones z ON z.id = t.zone_id
               WHERE t.id = ?""",
            (int(ticket_id),),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def _fetch_fix_reshoot(self, reshoot_id):
        if not reshoot_id:
            return None
        cur = await self._conn.execute(
            """SELECT id, ticket_id, capture_id, content_type, photographer_id,
                      photographer_phone, zone_name, captured_at
               FROM hygiene_fix_reshoots WHERE id = ?""",
            (int(reshoot_id),),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def _latest_fix_reshoot(self, ticket_id: int):
        cur = await self._conn.execute(
            """SELECT id, ticket_id, capture_id, content_type, photographer_id,
                      photographer_phone, zone_name, captured_at
               FROM hygiene_fix_reshoots
               WHERE ticket_id = ?
               ORDER BY id DESC LIMIT 1""",
            (int(ticket_id),),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def open_fix(
        self,
        actor: dict,
        zone_id: int,
        ticket_type: str,
        body_text: str,
        duration,
        live_capture,
        markup=None,
    ) -> dict:
        self._require_fix_opener(actor)
        data = self._require_live_capture(live_capture)
        cleaned_type = (ticket_type or "").strip()
        if cleaned_type not in FIX_TYPES:
            raise HygieneWorkError("invalid_type", "invalid_type")
        cleaned_body = (body_text or "").strip()
        if not cleaned_body:
            raise HygieneWorkError("invalid_body", "invalid_body")
        span = self._as_duration(duration)
        zone = await self._fetch_zone(zone_id)
        if zone is None:
            raise HygieneWorkError("zone_not_found", "zone_not_found")
        zone_name = dict(zone)["name"]
        if actor.get("kind") == "super":
            opener_kind = OPENER_SUPER
            opener_id = None
            opener_phone = self._photographer(actor) or "超级管理员"
        else:
            opener_kind = OPENER_STAFF
            opener_id = int(actor["id"])
            opener_phone = self._photographer(actor)
            if not opener_phone:
                raise HygieneWorkError("photographer_required", "photographer_required")
        marks = markup
        if marks is None and live_capture is not None:
            marks = live_capture.get("markup")
        content_type = (live_capture.get("content_type") or "image/jpeg").strip()
        capture_id = self._captures.put(data, content_type=content_type)
        now = self._now_iso()
        duration_seconds = int(span.total_seconds())
        deadline = (self._now_dt() + span).isoformat()
        cur = await self._conn.execute(
            """INSERT INTO hygiene_fix_tickets
               (zone_id, ticket_type, body_text, duration_seconds, deadline,
                opener_kind, opener_id, opener_phone, status, capture_id,
                content_type, markup_json, pending_reshoot_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
            (
                int(zone_id),
                cleaned_type,
                cleaned_body,
                duration_seconds,
                deadline,
                opener_kind,
                opener_id,
                opener_phone,
                STATUS_FIX_TODO,
                capture_id,
                content_type,
                self._markup_json({"markup": marks or []}),
                now,
                now,
            ),
        )
        ticket_id = int(cur.lastrowid)
        await self._conn.commit()
        text = self._fix_open_text(zone_name, cleaned_type, cleaned_body, duration_seconds)
        if self._notifier is not None:
            await self._notifier.notify_group_text(text)
        logger.info(
            "hygiene fix opened id=%s zone=%s type=%s",
            ticket_id,
            zone_id,
            cleaned_type,
        )
        return {
            "id": ticket_id,
            "zone_id": int(zone_id),
            "zone_name": zone_name,
            "ticket_type": cleaned_type,
            "body_text": cleaned_body,
            "deadline": deadline,
            "opener_kind": opener_kind,
            "opener_id": opener_id,
            "status": STATUS_FIX_TODO,
            "capture_id": capture_id,
            "markup": marks or [],
            "reshoot_capture_id": None,
            "watermark": None,
        }

    async def list_fix_tickets(self, actor: dict) -> list:
        del actor  # shop-wide; 班次 does not gate 整改单
        cur = await self._conn.execute(
            """SELECT t.id, t.zone_id, t.ticket_type, t.body_text, t.duration_seconds,
                      t.deadline, t.opener_kind, t.opener_id, t.opener_phone, t.status,
                      t.capture_id, t.content_type, t.markup_json, t.pending_reshoot_id,
                      z.name AS zone_name
               FROM hygiene_fix_tickets t
               JOIN hygiene_zones z ON z.id = t.zone_id
               WHERE t.status != ?
               ORDER BY t.id ASC""",
            (STATUS_PASSED,),
        )
        rows = []
        for row in await cur.fetchall():
            ticket = dict(row)
            reshoot = await self._fetch_fix_reshoot(ticket.get("pending_reshoot_id"))
            rows.append(self._fix_row(ticket, reshoot))
        return rows

    async def get_fix_ticket(self, ticket_id: int) -> dict:
        ticket = await self._fetch_fix_ticket(ticket_id)
        if ticket is None:
            raise HygieneWorkError("ticket_not_found", "ticket_not_found")
        reshoot = await self._fetch_fix_reshoot(ticket.get("pending_reshoot_id"))
        return self._fix_row(ticket, reshoot)

    async def get_fix_review(self, ticket_id: int) -> dict:
        ticket = await self._fetch_fix_ticket(ticket_id)
        if ticket is None:
            raise HygieneWorkError("ticket_not_found", "ticket_not_found")
        if ticket["status"] != STATUS_PENDING:
            raise HygieneWorkError("not_pending", "not_pending")
        reshoot = await self._fetch_fix_reshoot(ticket.get("pending_reshoot_id"))
        if reshoot is None:
            raise HygieneWorkError("not_pending", "not_pending")
        return self._fix_row(ticket, reshoot)

    async def reshoot_fix(self, actor: dict, ticket_id: int, live_capture) -> dict:
        self._require_staff_submitter(actor)
        data = self._require_live_capture(live_capture)
        ticket = await self._fetch_fix_ticket(ticket_id)
        if ticket is None:
            raise HygieneWorkError("ticket_not_found", "ticket_not_found")
        if ticket["status"] == STATUS_PASSED:
            raise HygieneWorkError("already_accepted", "already_accepted")
        photographer = self._photographer(actor)
        if not photographer:
            raise HygieneWorkError("photographer_required", "photographer_required")
        content_type = (live_capture.get("content_type") or "image/jpeg").strip()
        capture_id = self._captures.put(data, content_type=content_type)
        now = self._now_iso()
        cur = await self._conn.execute(
            """INSERT INTO hygiene_fix_reshoots
               (ticket_id, capture_id, content_type, photographer_id,
                photographer_phone, zone_name, captured_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(ticket_id),
                capture_id,
                content_type,
                int(actor["id"]),
                photographer,
                ticket["zone_name"],
                now,
                now,
            ),
        )
        reshoot_id = int(cur.lastrowid)
        await self._conn.execute(
            """UPDATE hygiene_fix_tickets
               SET status = ?, pending_reshoot_id = ?, updated_at = ?
               WHERE id = ?""",
            (STATUS_PENDING, reshoot_id, now, int(ticket_id)),
        )
        await self._conn.commit()
        logger.info(
            "hygiene fix reshot ticket=%s capture=%s",
            ticket_id,
            capture_id,
        )
        ticket["status"] = STATUS_PENDING
        ticket["pending_reshoot_id"] = reshoot_id
        reshoot = {
            "capture_id": capture_id,
            "content_type": content_type,
            "photographer_phone": photographer,
            "zone_name": ticket["zone_name"],
            "captured_at": now,
        }
        return self._fix_row(ticket, reshoot)

    async def _pending_fix(self, ticket_id: int) -> tuple:
        ticket = await self._fetch_fix_ticket(ticket_id)
        if ticket is None:
            raise HygieneWorkError("ticket_not_found", "ticket_not_found")
        if ticket["status"] != STATUS_PENDING:
            raise HygieneWorkError("not_pending", "not_pending")
        reshoot = await self._fetch_fix_reshoot(ticket.get("pending_reshoot_id"))
        if reshoot is None:
            raise HygieneWorkError("not_pending", "not_pending")
        return ticket, reshoot

    async def accept_fix(self, actor: dict, ticket_id: int) -> dict:
        ticket, _reshoot = await self._pending_fix(ticket_id)
        self._require_fix_reviewer(actor, ticket)
        now = self._now_iso()
        await self._conn.execute(
            """UPDATE hygiene_fix_tickets
               SET status = ?, updated_at = ?
               WHERE id = ?""",
            (STATUS_PASSED, now, int(ticket_id)),
        )
        await self._conn.commit()
        logger.info("hygiene fix accepted ticket=%s", ticket_id)
        return {
            "id": int(ticket_id),
            "status": STATUS_PASSED,
            "deadline": ticket["deadline"],
        }

    async def reject_fix(self, actor: dict, ticket_id: int) -> dict:
        ticket, _reshoot = await self._pending_fix(ticket_id)
        self._require_fix_reviewer(actor, ticket)
        now_dt = self._now_dt()
        now = now_dt.isoformat()
        deadline = (now_dt + timedelta(seconds=int(ticket["duration_seconds"]))).isoformat()
        await self._conn.execute(
            """UPDATE hygiene_fix_tickets
               SET status = ?, pending_reshoot_id = NULL, deadline = ?, updated_at = ?
               WHERE id = ?""",
            (STATUS_FIX_TODO, deadline, now, int(ticket_id)),
        )
        await self._conn.commit()
        logger.info("hygiene fix rejected ticket=%s deadline=%s", ticket_id, deadline)
        return {
            "id": int(ticket_id),
            "status": STATUS_FIX_TODO,
            "deadline": deadline,
            "reshoot_capture_id": None,
        }

    async def _fix_notice_exists(self, ticket_id: int) -> bool:
        cur = await self._conn.execute(
            "SELECT 1 FROM hygiene_fix_overdue_notices WHERE ticket_id = ?",
            (int(ticket_id),),
        )
        return await cur.fetchone() is not None

    async def _record_fix_overdue(self, ticket: dict) -> None:
        now = self._now_iso()
        ticket_id = int(ticket["id"])
        zone_id = int(ticket["zone_id"])
        await self._conn.execute(
            """INSERT INTO hygiene_fix_overdue_notices (ticket_id, notified_at)
               VALUES (?, ?)""",
            (ticket_id, now),
        )
        await self._insert_board_event(
            BOARD_ZONE,
            EVENT_MISSED_DAILY,
            zone_id=zone_id,
        )
        last = await self._latest_fix_reshoot(ticket_id)
        if last is not None:
            await self._insert_board_event(
                BOARD_PERSON,
                EVENT_MISSED_DAILY,
                zone_id=zone_id,
                employee_id=int(last["photographer_id"]),
            )
        await self._conn.commit()

    async def _sweep_fix_overdue(self, now: datetime) -> list:
        cur = await self._conn.execute(
            """SELECT t.id, t.zone_id, t.ticket_type, t.body_text, t.deadline, t.status,
                      z.name AS zone_name
               FROM hygiene_fix_tickets t
               JOIN hygiene_zones z ON z.id = t.zone_id
               WHERE t.status != ?""",
            (STATUS_PASSED,),
        )
        notified = []
        for row in await cur.fetchall():
            ticket = dict(row)
            if self._parse_iso(ticket["deadline"]) > now:
                continue
            if await self._fix_notice_exists(int(ticket["id"])):
                continue
            text = self._fix_overdue_text(ticket)
            if self._notifier is not None:
                await self._notifier.notify_group_text(text)
            await self._record_fix_overdue(ticket)
            notified.append(
                {
                    "ticket_id": int(ticket["id"]),
                    "zone_id": int(ticket["zone_id"]),
                    "kind": "整改单",
                    "text": text,
                }
            )
        return notified

    async def overdue_scheduler_loop(self) -> None:
        logger.info("卫生逾期调度器已启动")
        while True:
            try:
                await self.sweep_overdue()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("卫生逾期调度异常: %s", exc)
            await asyncio.sleep(OVERDUE_SWEEP_INTERVAL_SECONDS)

    def _teaching_from_row(self, row) -> dict:
        mapping = dict(row)
        return {
            "id": int(mapping["id"]),
            "kind": mapping["kind"],
            "title": mapping["title"],
            "left_label": mapping["left_label"],
            "right_label": mapping["right_label"],
            "left_capture_id": mapping["left_capture_id"],
            "right_capture_id": mapping["right_capture_id"],
            "left_content_type": mapping["left_content_type"],
            "right_content_type": mapping["right_content_type"],
            "left_markup": self._parse_markup(mapping.get("left_markup_json") or "[]"),
            "item_id": None if mapping.get("item_id") is None else int(mapping["item_id"]),
            "shift": mapping.get("shift"),
            "created_at": mapping["created_at"],
        }

    async def _insert_teaching(
        self,
        *,
        kind: str,
        title: str,
        left_label: str,
        right_label: str,
        left_capture_id: str,
        right_capture_id: str,
        left_content_type: str,
        right_content_type: str,
        left_markup,
        item_id=None,
        shift=None,
    ) -> dict:
        cur = await self._conn.execute(
            """SELECT id, kind, title, left_label, right_label, left_capture_id,
                      right_capture_id, left_content_type, right_content_type,
                      left_markup_json, item_id, shift, created_at
               FROM hygiene_teaching_examples
               WHERE kind = ? AND left_capture_id = ? AND right_capture_id = ?""",
            (kind, left_capture_id, right_capture_id),
        )
        existing = await cur.fetchone()
        if existing is not None:
            return self._teaching_from_row(existing)
        now = self._now_iso()
        markup_json = json.dumps(left_markup or [], ensure_ascii=False)
        cur = await self._conn.execute(
            """INSERT INTO hygiene_teaching_examples
               (kind, title, left_label, right_label, left_capture_id,
                right_capture_id, left_content_type, right_content_type,
                left_markup_json, item_id, shift, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kind,
                title,
                left_label,
                right_label,
                left_capture_id,
                right_capture_id,
                left_content_type,
                right_content_type,
                markup_json,
                item_id,
                shift,
                now,
            ),
        )
        await self._conn.commit()
        return {
            "id": int(cur.lastrowid),
            "kind": kind,
            "title": title,
            "left_label": left_label,
            "right_label": right_label,
            "left_capture_id": left_capture_id,
            "right_capture_id": right_capture_id,
            "left_content_type": left_content_type,
            "right_content_type": right_content_type,
            "left_markup": left_markup or [],
            "item_id": None if item_id is None else int(item_id),
            "shift": shift,
            "created_at": now,
        }

    async def _passed_daily_source(self, item_id: int, shift: str) -> dict:
        if shift not in DAILY_SHIFTS:
            raise HygieneWorkError("shift_mismatch", "shift_mismatch")
        item_row = await self._fetch_item_with_zone(item_id)
        if item_row is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        item = dict(item_row)
        business_date = hygiene_business_date(self._now_dt())
        instance_row = await self._fetch_instance(business_date, shift, item_id)
        if instance_row is None:
            raise HygieneWorkError("not_passed", "not_passed")
        instance = dict(instance_row)
        if instance["status"] != STATUS_PASSED:
            raise HygieneWorkError("not_passed", "not_passed")
        submission = await self._fetch_submission(instance.get("pending_submission_id"))
        if submission is None:
            raise HygieneWorkError("not_passed", "not_passed")
        standard = await self.standard_by_id(int(submission["frozen_standard_id"]))
        return {
            "kind": TEACHING_DAILY,
            "title": f"{item['zone_name']} · {item['name']}",
            "left_label": "标准图",
            "right_label": "实拍",
            "left_capture_id": standard["capture_id"],
            "right_capture_id": submission["capture_id"],
            "left_content_type": standard["content_type"],
            "right_content_type": submission["content_type"],
            "left_markup": standard["markup"],
            "item_id": int(item_id),
            "shift": shift,
        }

    async def _passed_deep_clean_source(self, item_id: int) -> dict:
        item = await self._fetch_deep_clean_item(item_id)
        if item is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        business_date = hygiene_business_date(self._now_dt())
        instance = await self._fetch_deep_clean_instance(business_date, item_id)
        if instance is None or instance["status"] != STATUS_PASSED:
            raise HygieneWorkError("not_passed", "not_passed")
        submission = await self._fetch_deep_clean_submission(
            instance.get("pending_submission_id")
        )
        if submission is None:
            raise HygieneWorkError("not_passed", "not_passed")
        return {
            "kind": TEACHING_DEEP_CLEAN,
            "title": item["name"],
            "left_label": "清理前",
            "right_label": "清理后",
            "left_capture_id": submission["before_capture_id"],
            "right_capture_id": submission["after_capture_id"],
            "left_content_type": submission["before_content_type"],
            "right_content_type": submission["after_content_type"],
            "left_markup": [],
            "item_id": int(item_id),
            "shift": None,
        }

    async def mark_teaching(self, actor: dict, source: dict) -> dict:
        self._require_super(actor)
        payload = source or {}
        kind = (payload.get("kind") or "").strip()
        if kind == TEACHING_DAILY:
            row = await self._passed_daily_source(
                int(payload.get("item_id") or 0),
                (payload.get("shift") or "").strip(),
            )
        elif kind == TEACHING_DEEP_CLEAN:
            row = await self._passed_deep_clean_source(int(payload.get("item_id") or 0))
        else:
            raise HygieneWorkError("invalid_teaching", "invalid_teaching")
        example = await self._insert_teaching(**row)
        logger.info("hygiene teaching marked id=%s kind=%s", example["id"], kind)
        return example

    async def list_teaching(self) -> list:
        cur = await self._conn.execute(
            """SELECT id, kind, title, left_label, right_label, left_capture_id,
                      right_capture_id, left_content_type, right_content_type,
                      left_markup_json, item_id, shift, created_at
               FROM hygiene_teaching_examples
               ORDER BY id DESC"""
        )
        return [self._teaching_from_row(row) for row in await cur.fetchall()]

    async def get_teaching(self, example_id: int) -> dict:
        cur = await self._conn.execute(
            """SELECT id, kind, title, left_label, right_label, left_capture_id,
                      right_capture_id, left_content_type, right_content_type,
                      left_markup_json, item_id, shift, created_at
               FROM hygiene_teaching_examples WHERE id = ?""",
            (int(example_id),),
        )
        row = await cur.fetchone()
        if row is None:
            raise HygieneWorkError("teaching_not_found", "teaching_not_found")
        return self._teaching_from_row(row)


