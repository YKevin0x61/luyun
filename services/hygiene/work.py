#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生责任区、日常检查项、当前标准图。

Does not hash passwords or issue staff sessions. Captures stay off SQLite WAL.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Callable, Optional

from database import CHINA_TZ

logger = logging.getLogger(__name__)

SEED_ZONE_NAMES = ("案板", "馅档", "熟笼", "肠粉", "西饼", "明档1", "明档2", "煎炸")


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


