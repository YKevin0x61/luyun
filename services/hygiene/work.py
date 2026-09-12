#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生责任区、日常检查项、当前标准图、日常提交与验收。

Does not hash passwords or issue staff sessions. Captures stay off SQLite WAL.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Callable, Optional

from database import CHINA_TZ
from services.hygiene.accounts import (
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
DAILY_SHIFTS = (SHIFT_DAY, SHIFT_NIGHT)


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
        await self._conn.commit()
        logger.info("hygiene daily rejected item=%s shift=%s", item_id, shift)
        return {
            "item_id": int(item_id),
            "shift": shift,
            "business_date": instance["business_date"],
            "status": STATUS_TODO,
        }


