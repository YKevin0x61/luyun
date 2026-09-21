#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生责任区、日常检查项、当前标准图、日常提交与验收、专项卫生、整改单、逾期群通知、红黑榜、卫生教材。

Does not hash passwords or issue staff sessions. Captures stay off SQLite WAL.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import re
import sqlite3
import time
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
from services.hygiene.images import GeneratedVariant, InvalidImageError
from services.hygiene.archive import (
    ARCHIVE_DELETE_KINDS,
    KIND_DAILY,
    KIND_DEEP,
    KIND_FIX_RESHOOT,
    KIND_STANDARD,
    KIND_TEACHING,
    created_range,
    normalize_kinds,
    parse_archive_range,
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
MAX_STANDARD_BYTES = 20 * 1024 * 1024
MAX_CAPTURE_BYTES = MAX_STANDARD_BYTES
# 标注（圆圈/箭头/批注）。形状与 admin-web/src/utils/hygieneMarkup.js 一致，
# 上限对齐前端：批注 40 字（input maxlength）、条数给足余量。
MARKUP_KINDS = ("circle", "arrow", "caption")
MAX_MARKUP_MARKS = 50
MAX_CAPTION_LENGTH = 40
MIN_CIRCLE_RADIUS = 0.02
MAX_CIRCLE_RADIUS = 0.4


def _clamp01(value) -> float:
    """坐标夹到 0–1（图片内的比例位置）。非数字按 0 处理。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return min(1.0, max(0.0, number))


def _clamp_radius(value) -> float:
    """圆圈半径夹到前后端约定的区间。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.08
    if number != number:  # NaN
        return 0.08
    return min(MAX_CIRCLE_RADIUS, max(MIN_CIRCLE_RADIUS, number))


CAPTURE_VARIANTS = ("thumb", "preview")
ORPHAN_CAPTURE_MIN_AGE_SECONDS = 24 * 60 * 60
# Keep every `IN (...)` well under SQLITE_MAX_VARIABLE_NUMBER (999 on old builds,
# 250000 on the SQLite shipped with macOS Command Line Tools).
SQL_ID_CHUNK_SIZE = 500
WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
CALENDAR_TODO = "待办"
CALENDAR_DONE = "已完成"
CALENDAR_MISSED = "未完成"
OVERDUE_SWEEP_INTERVAL_SECONDS = 30
# 标准图清单里"文件是否还在"的核对间隔：文件消失是罕见事件，不值得每个请求扫一遍目录。
STANDARD_MANIFEST_FILE_TTL_SECONDS = 60
# 看板事件只增不减（每次提交/驳回/逾期一条，一年几十万行）。红黑榜按周读取，
# 留 12 周足够覆盖"本周 + 上季"的追溯，超出部分定期清掉。
BOARD_EVENT_RETENTION_DAYS = 84
BOARD_EVENT_PURGE_INTERVAL_SECONDS = 6 * 3600
# 看板事件流的默认返回条数：接口目前没有前端消费者，但一旦接上就是 O(历史) 响应体。
BOARD_EVENT_DEFAULT_LIMIT = 200
BOARD_EVENT_MAX_LIMIT = 1000
BOARD_ZONE = "zone"
BOARD_PERSON = "person"
EVENT_MISSED_DAILY = "逾期"
EVENT_CAPTURE = "实拍"
EVENT_REJECT = "驳回"
EVENT_FIRST_PASS = "一次通过"
# 员工当天自行换责任区的留痕事件。责任区是分工而不是安全边界（员工可以自由换区，
# 见 .scratch/hygiene-audit/2026-09-20-review.md §1.1），所以不阻止换区，但要把
# "谁越了区"记进个人榜，让红黑榜的实拍数字可解释。
EVENT_ZONE_SWITCH = "换区"
PERSON_COUNT_KEYS = ("实拍", "驳回", "一次通过", "逾期", "换区")
ZONE_COUNT_KEYS = ("逾期",)
TEACHING_DAILY = "daily"
TEACHING_DEEP_CLEAN = "deep_clean"
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def serialized_write(method):
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        async with self._write_lock:
            try:
                return await method(self, *args, **kwargs)
            except Exception:
                # 兜底回滚：aiosqlite 是隐式事务，方法里任何未处理异常（磁盘满、
                # 外键冲突、图片解码失败…）都会把半截写留在连接上——下一个协程的
                # commit 会把它一起提交，别人的 rollback 会回滚掉无关的写。
                # 无事务时 rollback 是 no-op，所以这里无条件调用是安全的。
                await self._conn.rollback()
                raise

    return wrapper


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


def _archive_result(
    kind: str,
    records: int,
    photos: int,
    status_reset: int = 0,
    blocked: int = 0,
    files: Optional[int] = None,
) -> dict:
    """删除/清理的返回形状。

    ``photos`` 是原图张数——用户嘴里的"照片"；``files`` 是实际删掉的文件数（含
    缩略图与 preview 变体），给日志和"释放了多少空间"用。``blocked`` 目前只有标准图
    版本用得上：被占用而没删掉的条数。
    """
    return {
        "kind": kind,
        "records": records,
        "photos": photos,
        "files": photos if files is None else files,
        "status_reset": status_reset,
        "blocked": blocked,
    }


def _chunked(items, size: int):
    """Yield bounded slices so a single statement never binds too many variables."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


MAX_REJECT_REASON_LENGTH = 120


def _clean_reject_reason(reason) -> Optional[str]:
    """驳回原因归一化：去空白、限长；没填就是 None（不写空串进库）。"""
    text = (reason or "").strip()
    if not text:
        return None
    return text[:MAX_REJECT_REASON_LENGTH]


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
        image_variants=None,
        on_change=None,
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
        self._image_variants = image_variants
        self._on_change = on_change
        self._write_lock_owner = conn_or_db
        self._local_write_lock = None
        # capture 文件集合的 TTL 缓存，见 _readable_capture_ids。
        self._readable_capture_cache = None
        # 看板事件清理的上次执行时间（monotonic），见 _maybe_purge_board_events。
        self._last_board_purge = 0.0
        # hygiene_board_events 的两个扩展列是否存在（PG 既有库可能只跑了其中一次迁移）。
        # 分开缓存：只应用了 0002（reason）而没应用 0003（ticket_id）时，
        # 驳回原因不该跟着一起降级。
        self._has_event_reason: Optional[bool] = None
        self._has_event_ticket: Optional[bool] = None

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

    def business_date(self) -> str:
        """当前营业日（06:00 切）。

        管理端要判断"某天是不是今天"时必须问这里，不要各自取一次 now：两处时钟一旦
        不同源（测试注入、将来的多店时钟），"今天"就会漂移，而漂移的后果是历史回看
        被当成可验收。
        """
        return hygiene_business_date(self._now_dt())

    def _require_super(self, actor: dict) -> None:
        if not actor or actor.get("kind") != "super":
            raise HygieneWorkError("forbidden", "forbidden")

    async def prepare(self) -> None:
        # 先探扩展列是否存在（此时没有任何写事务，探测失败回滚是无害的）。
        # 不能留到写路径里去探：PG 缺列时那次探测必须 rollback，会把同一事务里已经
        # 写好的状态更新一起回滚掉——驳回就成了"记了一笔事件、状态却没变"。
        await self._event_reason_supported()
        await self._event_ticket_supported()
        await self._backfill_standard_metadata()
        await self._seed_zones_if_empty()
        await self._seed_overdue_clocks_if_empty()

    async def _store_capture(
        self,
        data: bytes,
        content_type: str,
        *,
        require_image: bool = False,
    ) -> tuple[str, dict[str, tuple[str, GeneratedVariant]]]:
        capture_id = await self._captures.put_async(data, content_type=content_type)
        generated: dict[str, tuple[str, GeneratedVariant]] = {}
        if self._image_variants is None:
            self._remember_capture_ids(capture_id)
            return capture_id, generated
        try:
            variants = await asyncio.to_thread(self._image_variants.generate, data)
        except InvalidImageError:
            if require_image:
                await self._captures.delete_async(capture_id)
                raise HygieneWorkError("invalid_image", "invalid_image")
            logger.warning("hygiene capture is not decodable capture=%s", capture_id)
            self._remember_capture_ids(capture_id)
            return capture_id, generated
        except Exception:
            logger.exception("hygiene derivative generation failed capture=%s", capture_id)
            if require_image:
                await self._captures.delete_async(capture_id)
                raise
            self._remember_capture_ids(capture_id)
            return capture_id, generated
        try:
            for variant_name, variant in variants.items():
                derivative_id = await self._captures.put_async(
                    variant.data,
                    content_type="image/jpeg",
                )
                generated[variant_name] = (derivative_id, variant)
        except Exception:
            await self._delete_capture_files(
                [capture_id, *(item[0] for item in generated.values())]
            )
            raise
        self._remember_capture_ids(
            capture_id, *(item[0] for item in generated.values())
        )
        return capture_id, generated

    async def _insert_variants(
        self,
        source_capture_id: str,
        generated: dict[str, tuple[str, GeneratedVariant]],
    ) -> None:
        if not generated:
            return
        now = self._now_iso()
        await self._conn.executemany(
            """INSERT INTO hygiene_capture_variants
               (source_capture_id, variant, capture_id, content_type,
                width, height, byte_size, content_sha256, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_capture_id, variant) DO NOTHING""",
            [
                (
                    source_capture_id,
                    variant_name,
                    capture_id,
                    "image/jpeg",
                    variant.width,
                    variant.height,
                    len(variant.data),
                    hashlib.sha256(variant.data).hexdigest(),
                    now,
                )
                for variant_name, (capture_id, variant) in generated.items()
            ],
        )

    async def _delete_capture_files(self, capture_ids) -> None:
        for capture_id in {item for item in capture_ids if item}:
            try:
                await self._captures.delete_async(capture_id)
            except Exception:
                logger.warning(
                    "hygiene capture cleanup failed capture=%s",
                    capture_id,
                    exc_info=True,
                )

    async def _backfill_standard_metadata(self) -> None:
        cur = await self._conn.execute(
            """SELECT id, capture_id, byte_size, content_sha256
               FROM hygiene_standards
               WHERE byte_size IS NULL OR content_sha256 IS NULL"""
        )
        rows = await cur.fetchall()
        changed = False
        for row in rows:
            mapping = dict(row)
            try:
                if not await self._captures.exists_async(mapping["capture_id"]):
                    raise FileNotFoundError(mapping["capture_id"])
                data = await self._captures.get_async(mapping["capture_id"])
            except (FileNotFoundError, KeyError):
                logger.warning(
                    "hygiene standard capture missing standard=%s",
                    mapping["id"],
                )
                continue
            await self._conn.execute(
                """UPDATE hygiene_standards
                   SET byte_size = ?, content_sha256 = ?
                   WHERE id = ?""",
                (len(data), hashlib.sha256(data).hexdigest(), int(mapping["id"])),
            )
            changed = True
        if changed:
            await self._conn.commit()

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
        return {
            "id": int(mapping["id"]),
            "name": mapping["name"],
            "shifts": self._zone_shifts_from_flags(mapping),
        }

    @staticmethod
    def _zone_shifts_from_flags(mapping: dict) -> list[str]:
        shifts = []
        if int(mapping.get("day_shift", 1) or 0):
            shifts.append(SHIFT_DAY)
        if int(mapping.get("night_shift", 1) or 0):
            shifts.append(SHIFT_NIGHT)
        return shifts

    @staticmethod
    def _zone_shift_enabled(mapping: dict, shift: str) -> bool:
        if shift == SHIFT_DAY:
            return bool(int(mapping.get("day_shift", 1) or 0))
        if shift == SHIFT_NIGHT:
            return bool(int(mapping.get("night_shift", 1) or 0))
        return False

    @staticmethod
    def _normalize_zone_shifts(shifts) -> tuple[int, int]:
        """Return (day_shift, night_shift) flags for a zone's enabled shifts."""
        if shifts is None:
            return 1, 1
        if isinstance(shifts, str):
            cleaned = [part.strip() for part in shifts.split(",") if part.strip()]
        else:
            cleaned = [str(part).strip() for part in shifts]
        if any(part not in DAILY_SHIFTS for part in cleaned):
            raise HygieneWorkError("invalid_shift", "invalid_shift")
        if not cleaned:
            raise HygieneWorkError("zone_shift_required", "zone_shift_required")
        return int(SHIFT_DAY in cleaned), int(SHIFT_NIGHT in cleaned)

    async def list_zones(self) -> list[dict]:
        cur = await self._conn.execute(
            """SELECT id, name, day_shift, night_shift
               FROM hygiene_zones ORDER BY id ASC"""
        )
        rows = await cur.fetchall()
        return [self._zone_from_row(row) for row in rows]

    async def create_zone(self, actor: dict, name: str, shifts=None) -> dict:
        self._require_super(actor)
        cleaned = (name or "").strip()
        if not cleaned:
            raise HygieneWorkError("invalid_zone_name", "invalid_zone_name")
        day_shift, night_shift = self._normalize_zone_shifts(shifts)
        now = self._now_iso()
        try:
            cur = await self._conn.execute(
                """INSERT INTO hygiene_zones
                   (name, day_shift, night_shift, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (cleaned, day_shift, night_shift, now, now),
            )
            await self._conn.commit()
        except sqlite3.IntegrityError as exc:
            await self._conn.rollback()
            raise HygieneWorkError("duplicate_zone", "duplicate_zone") from exc
        shifts_enabled = self._zone_shifts_from_flags(
            {"day_shift": day_shift, "night_shift": night_shift}
        )
        logger.info(
            "hygiene zone created id=%s name=%s shifts=%s",
            cur.lastrowid,
            cleaned,
            ",".join(shifts_enabled),
        )
        return {
            "id": int(cur.lastrowid),
            "name": cleaned,
            "shifts": shifts_enabled,
        }

    @serialized_write
    async def set_zone_shifts(self, actor: dict, zone_id: int, shifts) -> dict:
        """超级管理员设置责任区跑哪些班次。"""
        self._require_super(actor)
        zone = await self._fetch_zone(zone_id)
        if zone is None:
            raise HygieneWorkError("zone_not_found", "zone_not_found")
        day_shift, night_shift = self._normalize_zone_shifts(shifts)
        await self._conn.execute(
            """UPDATE hygiene_zones
               SET day_shift = ?, night_shift = ?, updated_at = ?
               WHERE id = ?""",
            (day_shift, night_shift, self._now_iso(), int(zone_id)),
        )
        await self._conn.commit()
        mapping = dict(zone)
        shifts_enabled = self._zone_shifts_from_flags(
            {"day_shift": day_shift, "night_shift": night_shift}
        )
        logger.info(
            "hygiene zone shifts updated id=%s shifts=%s",
            zone_id,
            ",".join(shifts_enabled),
        )
        return {
            "id": int(mapping["id"]),
            "name": mapping["name"],
            "shifts": shifts_enabled,
        }

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
        await self._drop_teaching_examples(TEACHING_DAILY, item_ids)

    async def _drop_teaching_examples(self, kind: str, item_ids: list[int]) -> None:
        """清掉引用被删检查项的卫生教材。

        教材的左右图就是这些检查项的 capture：留着行会让教材页 404，而且
        ``_referenced_capture_ids`` 会把它们继续算作"被引用"，文件永远不回收。
        capture 文件本身交给常规孤儿清理，避免误删仍被别处引用的图。
        """
        ids = [int(item_id) for item_id in item_ids if item_id is not None]
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        await self._conn.execute(
            f"DELETE FROM hygiene_teaching_examples "
            f"WHERE kind = ? AND item_id IN ({placeholders})",
            [kind, *ids],
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

    @serialized_write
    async def delete_zone(self, actor: dict, zone_id: int) -> dict:
        self._require_super(actor)
        zone = await self._fetch_zone(zone_id)
        if zone is None:
            raise HygieneWorkError("zone_not_found", "zone_not_found")
        mapping = dict(zone)
        try:
            item_ids = await self._item_ids_for_zone(int(zone_id))
            # hygiene_shift_picks.zone_id 有外键指向 hygiene_zones，先解绑再删区，
            # 否则只要当天有人选过这个区就必然 IntegrityError（现场老库没这条外键，
            # 只有从源码新建的库会踩到，所以新门店/CI 一删就 500）。
            await self._conn.execute(
                "UPDATE hygiene_shift_picks SET zone_id = NULL, updated_at = ? "
                "WHERE zone_id = ?",
                (self._now_iso(), int(zone_id)),
            )
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

    @serialized_write
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
        if len(data) > MAX_STANDARD_BYTES:
            raise HygieneWorkError("standard_too_large", "standard_too_large")
        return data

    def _normalize_markup(self, raw) -> list:
        """标注入库前的归一化。

        前端 `hygieneMarkup.js` 已经保证形状（坐标 0–1、批注 ≤40 字、kind 三选一），
        但标注会渲染在员工手机上，而管理员可以直接 POST/PATCH 这两条写路径——所以
        在服务端再兜一层：丢掉认不出的 kind、夹住坐标、截断批注、限制条数。

        只做「前端本来就保证的事」，不改真实流量的语义。
        """
        if not isinstance(raw, list):
            return []
        marks = []
        for item in raw:
            if len(marks) >= MAX_MARKUP_MARKS:
                break
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            if kind not in MARKUP_KINDS:
                continue
            if kind == "circle":
                marks.append({
                    "kind": "circle",
                    "x": _clamp01(item.get("x")),
                    "y": _clamp01(item.get("y")),
                    "r": _clamp_radius(item.get("r")),
                })
            elif kind == "arrow":
                marks.append({
                    "kind": "arrow",
                    "x1": _clamp01(item.get("x1")),
                    "y1": _clamp01(item.get("y1")),
                    "x2": _clamp01(item.get("x2")),
                    "y2": _clamp01(item.get("y2")),
                })
            else:
                text = str(item.get("text") or "").strip()[:MAX_CAPTION_LENGTH]
                marks.append({
                    "kind": "caption",
                    "x": _clamp01(item.get("x")),
                    "y": _clamp01(item.get("y")),
                    "text": text,
                })
        return marks

    def _markup_json(self, capture) -> str:
        markup = [] if capture is None else capture.get("markup")
        return json.dumps(self._normalize_markup(markup), ensure_ascii=False)

    def _parse_markup(self, raw: str):
        try:
            value = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        return value if isinstance(value, list) else []

    async def _fetch_zone(self, zone_id: int):
        cur = await self._conn.execute(
            """SELECT id, name, day_shift, night_shift
               FROM hygiene_zones WHERE id = ?""",
            (zone_id,),
        )
        return await cur.fetchone()

    @serialized_write
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
        capture_id, generated = await self._store_capture(
            data,
            content_type,
            require_image=True,
        )
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
                   (item_id, capture_id, content_type, byte_size,
                    content_sha256, markup_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id,
                    capture_id,
                    content_type,
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                    self._markup_json(capture),
                    now,
                ),
            )
            standard_id = int(std_cur.lastrowid)
            await self._conn.execute(
                """UPDATE hygiene_daily_items
                   SET current_standard_id = ?, updated_at = ?
                   WHERE id = ?""",
                (standard_id, now, item_id),
            )
            await self._insert_variants(capture_id, generated)
            await self._conn.commit()
        except sqlite3.IntegrityError as exc:
            await self._conn.rollback()
            await self._delete_capture_files(
                [capture_id, *(item[0] for item in generated.values())]
            )
            raise HygieneWorkError("duplicate_item", "duplicate_item") from exc
        except Exception:
            await self._conn.rollback()
            await self._delete_capture_files(
                [capture_id, *(item[0] for item in generated.values())]
            )
            raise
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

    async def list_staff_daily_items(self, actor: Optional[dict] = None) -> list[dict]:
        zones = await self.list_zones()
        sql = """SELECT i.id, i.zone_id, i.name, i.current_standard_id,
                        s.capture_id, s.markup_json
                 FROM hygiene_daily_items i
                 JOIN hygiene_standards s ON s.id = i.current_standard_id
                 WHERE i.current_standard_id IS NOT NULL"""
        params: list = []
        try:
            zone_id = self._actor_zone_id(actor or {})
        except HygieneWorkError:
            zone_id = None
        if zone_id is not None:
            sql += " AND i.zone_id = ?"
            params.append(zone_id)
        sql += " ORDER BY i.id ASC"
        cur = await self._conn.execute(sql, params)
        rows = await cur.fetchall()
        by_zone: dict[int, list] = {}
        for row in rows:
            item = self._item_from_join(dict(row))
            by_zone.setdefault(item["zone_id"], []).append(item)
        return [
            {
                "id": zone["id"],
                "name": zone["name"],
                "shifts": zone.get("shifts") or list(DAILY_SHIFTS),
                "items": by_zone.get(zone["id"], []),
            }
            for zone in zones
        ]

    async def _fetch_item(self, item_id: int):
        cur = await self._conn.execute(
            """SELECT id, zone_id, name, current_standard_id
               FROM hygiene_daily_items WHERE id = ?""",
            (item_id,),
        )
        return await cur.fetchone()

    async def require_daily_item_access(self, actor: dict, item_id: int) -> None:
        item = await self._fetch_item(item_id)
        if item is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        self._require_zone_access(actor, dict(item)["zone_id"])

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
            """SELECT id, item_id, capture_id, content_type, byte_size,
                      content_sha256, markup_json, created_at
               FROM hygiene_standards WHERE id = ?""",
            (int(standard_id),),
        )
        row = await cur.fetchone()
        if row is None:
            raise HygieneWorkError("standard_not_found", "standard_not_found")
        std = dict(row)
        return {
            "id": int(std["id"]),
            "item_id": int(std["item_id"]),
            "capture_id": std["capture_id"],
            "content_type": std["content_type"],
            "byte_size": int(std["byte_size"]) if std.get("byte_size") is not None else None,
            "sha256": std.get("content_sha256"),
            "markup": self._parse_markup(std.get("markup_json") or "[]"),
            "created_at": std["created_at"],
        }

    async def standard_version(self, standard_id: int, actor: Optional[dict] = None) -> dict:
        """单张标准图。传入员工 actor 时按该员工的责任区校验归属。

        同一条数据在 ``/staff/items/{item_id}/standard`` 上是有责任区校验的，
        这里不能成为绕过它的第二扇门（员工可以按 id 遍历下载全店标准图）。
        """
        standard = await self.standard_by_id(standard_id)
        if actor is not None:
            self._require_zone_access(actor, await self._item_zone_id(standard["item_id"]))
        return standard

    async def _item_zone_id(self, item_id: int) -> int:
        item = await self._fetch_item(item_id)
        if item is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        return int(dict(item)["zone_id"])

    async def standard_manifest(
        self,
        actor: Optional[dict] = None,
        variant: str = "original",
    ) -> dict:
        """当前标准图清单（员工端离线缓存用）。

        传入员工 actor 时只返回该员工当天责任区的项：这份清单会被整包离线缓存，
        「缓存即越权」——不做切片就等于把全店标准图发给每个员工。未选责任区时
        返回空清单而不是报错，避免刚打开页面就被 400 挡住。

        ``variant`` 决定清单声明的是哪一份字节：员工端下的是 ``?variant=preview``
        的缩放图，客户端拿字节后会按清单里的 ``byte_size``/``sha256`` 校验。声明原图
        的摘要却下发变体，那边必然判「图片大小不一致」——缓存永远建立不起来，而
        且每次重试都一样。所以这里必须跟着实际会下发的那一份走：变体记录存在且
        文件还在就用变体的元数据，否则退回原图（服务端此时也会回落到原图）。
        """
        zone_id: Optional[int] = None
        if actor is not None and actor.get("kind") == "staff":
            picked = actor.get("zone_id")
            if not picked:
                return self._empty_standard_manifest()
            zone_id = int(picked)
        want_variant = (variant or "original").strip().lower()
        if want_variant != "original":
            sql = """SELECT i.id AS item_id, i.name AS item_name,
                          i.current_standard_id AS standard_id,
                          s.capture_id, s.byte_size, s.content_sha256, s.content_type,
                          v.capture_id AS variant_capture_id,
                          v.byte_size AS variant_byte_size,
                          v.content_sha256 AS variant_sha256,
                          v.content_type AS variant_content_type
                   FROM hygiene_daily_items i
                   JOIN hygiene_standards s ON s.id = i.current_standard_id
                   LEFT JOIN hygiene_capture_variants v
                     ON v.source_capture_id = s.capture_id AND v.variant = ?
                   WHERE i.current_standard_id IS NOT NULL
                     AND s.byte_size IS NOT NULL
                     AND s.content_sha256 IS NOT NULL"""
            params: list = [want_variant]
        else:
            sql = """SELECT i.id AS item_id, i.name AS item_name,
                          i.current_standard_id AS standard_id,
                          s.capture_id, s.byte_size, s.content_sha256, s.content_type,
                          NULL AS variant_capture_id,
                          NULL AS variant_byte_size,
                          NULL AS variant_sha256,
                          NULL AS variant_content_type
                   FROM hygiene_daily_items i
                   JOIN hygiene_standards s ON s.id = i.current_standard_id
                   WHERE i.current_standard_id IS NOT NULL
                     AND s.byte_size IS NOT NULL
                     AND s.content_sha256 IS NOT NULL"""
            params = []
        if zone_id is not None:
            sql += " AND i.zone_id = ?"
            params.append(zone_id)
        sql += " ORDER BY s.id ASC"
        cur = await self._conn.execute(sql, params)
        rows = [dict(row) for row in await cur.fetchall()]
        readable_ids = set()
        if rows:
            readable_ids = await self._readable_capture_ids()
        standards = []
        for mapping in rows:
            capture_id = str(mapping["capture_id"])
            byte_size = mapping["byte_size"]
            sha256 = mapping["content_sha256"]
            content_type = mapping["content_type"]
            variant_id = mapping.get("variant_capture_id")
            # 变体记录可能指向已经被清理掉的文件：那种情况服务端会回落到原图，
            # 清单也得跟着声明原图，否则又对不上。
            if variant_id and str(variant_id) in readable_ids:
                capture_id = str(variant_id)
                byte_size = mapping["variant_byte_size"]
                sha256 = mapping["variant_sha256"]
                content_type = mapping.get("variant_content_type") or content_type
            if capture_id not in readable_ids:
                logger.warning(
                    "hygiene current standard capture missing standard=%s",
                    mapping["standard_id"],
                )
                continue
            standard_id = int(mapping["standard_id"])
            standards.append({
                "item_id": int(mapping["item_id"]),
                "item_name": mapping["item_name"],
                "standard_id": standard_id,
                "byte_size": int(byte_size),
                "sha256": sha256,
                "content_type": content_type,
            })
        return self._standard_manifest_payload(standards)

    def _standard_manifest_payload(self, standards: list) -> dict:
        digest_input = json.dumps(
            standards,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "version": hashlib.sha256(digest_input).hexdigest(),
            "generated_at": self._now_iso(),
            "standards": standards,
        }

    def _empty_standard_manifest(self) -> dict:
        """还没选责任区的员工拿到空清单（有稳定的 version，不是错误）。"""
        return self._standard_manifest_payload([])

    async def _readable_capture_ids(self) -> set:
        """磁盘上实际存在的 capture 文件集合，带 TTL 缓存。

        manifest 要判断"标准图文件还在不在"，原来是每个请求 iterdir + 逐文件 stat：
        文件上到几万时单次请求就是几百毫秒，而这份清单在每次卫生路由切换时都会被拉。
        文件消失属于罕见事件（磁盘故障、人工清理），缓存 60 秒足够。
        """
        now = time.monotonic()
        cached = self._readable_capture_cache
        if cached is not None and now - cached[0] < STANDARD_MANIFEST_FILE_TTL_SECONDS:
            return cached[1]
        ids = {str(capture_id) for capture_id in await self._captures.list_ids_async()}
        self._readable_capture_cache = (now, ids)
        return ids

    def _remember_capture_ids(self, *capture_ids) -> None:
        """新写入的 capture 立刻并进 TTL 缓存。

        不这么做的话，TTL 窗口内 manifest 认不出刚换版的标准图，员工端会短暂看到
        旧图或空清单（``test_manifest_lists_current_version_and_keeps_old_version_addressable``
        就是这样抓到的）。删除方向不需要特殊处理：文件消失是罕见事件，等 TTL 过期。
        """
        cached = self._readable_capture_cache
        if cached is None:
            return
        stamp, ids = cached
        merged = set(ids)
        for capture_id in capture_ids:
            if capture_id:
                merged.add(str(capture_id))
        self._readable_capture_cache = (stamp, merged)

    async def capture_bytes(self, capture_id: str) -> bytes:
        return await self._captures.get_async(capture_id)

    async def _original_capture_meta(self, capture_id: str) -> dict:
        cur = await self._conn.execute(
            """SELECT capture_id, content_type, byte_size, content_sha256
               FROM hygiene_standards WHERE capture_id = ?
               UNION ALL
               SELECT capture_id, content_type, NULL, NULL
               FROM hygiene_daily_submissions WHERE capture_id = ?
               UNION ALL
               SELECT before_capture_id AS capture_id,
                      before_content_type AS content_type, NULL, NULL
               FROM hygiene_deep_clean_submissions WHERE before_capture_id = ?
               UNION ALL
               SELECT after_capture_id AS capture_id,
                      after_content_type AS content_type, NULL, NULL
               FROM hygiene_deep_clean_submissions WHERE after_capture_id = ?
               UNION ALL
               SELECT capture_id, content_type, NULL, NULL
               FROM hygiene_fix_tickets WHERE capture_id = ?
               UNION ALL
               SELECT capture_id, content_type, NULL, NULL
               FROM hygiene_fix_reshoots WHERE capture_id = ?
               LIMIT 1""",
            (capture_id, capture_id, capture_id, capture_id, capture_id, capture_id),
        )
        row = await cur.fetchone()
        mapping = {} if row is None else dict(row)
        return {
            "capture_id": capture_id,
            "content_type": mapping.get("content_type") or "image/jpeg",
            "byte_size": (
                int(mapping["byte_size"])
                if mapping.get("byte_size") is not None
                else None
            ),
            "sha256": mapping.get("content_sha256"),
        }

    async def capture_view(self, capture_id: str, variant: str = "original") -> dict:
        requested = (variant or "original").strip().lower()
        if requested not in {"original", *CAPTURE_VARIANTS}:
            raise HygieneWorkError("invalid_variant", "invalid_variant")
        if requested != "original":
            cur = await self._conn.execute(
                """SELECT capture_id, content_type, byte_size, content_sha256
                   FROM hygiene_capture_variants
                   WHERE source_capture_id = ? AND variant = ?""",
                (capture_id, requested),
            )
            row = await cur.fetchone()
            if row is not None:
                mapping = dict(row)
                if await self._captures.exists_async(mapping["capture_id"]):
                    return {
                        "capture_id": mapping["capture_id"],
                        "content_type": mapping["content_type"],
                        "byte_size": int(mapping["byte_size"]),
                        "sha256": mapping["content_sha256"],
                        "variant": requested,
                        "fallback": False,
                        "path": await self._captures.path_async(mapping["capture_id"]),
                    }
        if not await self._captures.exists_async(capture_id):
            raise FileNotFoundError(capture_id)
        meta = await self._original_capture_meta(capture_id)
        return {
            **meta,
            "variant": "original",
            "fallback": requested != "original",
            "requested_variant": requested,
            "path": await self._captures.path_async(capture_id),
        }

    async def _referenced_capture_ids(self) -> set[str]:
        """Capture ids owned by hygiene business tables (roots of the variant tree)."""
        cur = await self._conn.execute(
            """SELECT capture_id FROM hygiene_standards
               UNION
               SELECT capture_id FROM hygiene_daily_submissions
               UNION
               SELECT before_capture_id FROM hygiene_deep_clean_submissions
               UNION
               SELECT after_capture_id FROM hygiene_deep_clean_submissions
               UNION
               SELECT capture_id FROM hygiene_fix_tickets
               UNION
               SELECT capture_id FROM hygiene_fix_reshoots
               UNION
               SELECT left_capture_id FROM hygiene_teaching_examples
               UNION
               SELECT right_capture_id FROM hygiene_teaching_examples"""
        )
        return {str(dict(row)["capture_id"]) for row in await cur.fetchall()}

    async def _variant_capture_ids(self) -> set[str]:
        """Derivative capture ids. These are never variant sources, or thumbs recurse."""
        cur = await self._conn.execute(
            "SELECT capture_id FROM hygiene_capture_variants"
        )
        return {str(dict(row)["capture_id"]) for row in await cur.fetchall()}

    async def backfill_capture_variants_once(self, batch_size: int = 20) -> int:
        if self._image_variants is None:
            return 0
        derivatives = await self._variant_capture_ids()
        sources = sorted(
            capture_id
            for capture_id in await self._referenced_capture_ids()
            if capture_id not in derivatives
        )
        if not sources:
            return 0
        existing: dict[str, set[str]] = {}
        for chunk in _chunked(sources, SQL_ID_CHUNK_SIZE):
            placeholders = ",".join("?" * len(chunk))
            cur = await self._conn.execute(
                f"""SELECT source_capture_id, variant
                    FROM hygiene_capture_variants
                    WHERE source_capture_id IN ({placeholders})""",
                chunk,
            )
            for row in await cur.fetchall():
                existing.setdefault(str(dict(row)["source_capture_id"]), set()).add(
                    str(dict(row)["variant"])
                )
        complete = {
            capture_id
            for capture_id, variants in existing.items()
            if set(CAPTURE_VARIANTS).issubset(variants)
        }
        missing = [capture_id for capture_id in sources if capture_id not in complete]
        created = 0
        for capture_id in missing[: max(1, int(batch_size))]:
            try:
                data = await self._captures.get_async(capture_id)
                variants = await asyncio.to_thread(self._image_variants.generate, data)
                generated: dict[str, tuple[str, GeneratedVariant]] = {}
                for variant_name, variant in variants.items():
                    derivative_id = await self._captures.put_async(
                        variant.data,
                        content_type="image/jpeg",
                    )
                    generated[variant_name] = (derivative_id, variant)
                await self._insert_variants(capture_id, generated)
                await self._conn.commit()
                created += 1
            except FileNotFoundError:
                logger.warning("hygiene variant backfill source missing capture=%s", capture_id)
            except InvalidImageError:
                logger.warning("hygiene variant backfill source invalid capture=%s", capture_id)
            except Exception:
                await self._conn.rollback()
                logger.exception("hygiene variant backfill failed capture=%s", capture_id)
            await asyncio.sleep(0)
        return created

    async def variant_backfill_loop(self, batch_size: int = 20) -> None:
        while True:
            try:
                created = await self.backfill_capture_variants_once(batch_size)
                if created == 0:
                    await asyncio.sleep(300)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("hygiene variant backfill loop failed")
                await asyncio.sleep(300)

    @serialized_write
    async def sweep_capture_orphans(
        self,
        max_age_seconds: int = ORPHAN_CAPTURE_MIN_AGE_SECONDS,
    ) -> int:
        """删掉没人引用的 capture。

        必须拿写锁：它会 ``commit()``，而在共享连接上无锁 commit 会把另一个协程
        写了一半的事务顺手提交掉（锁在这里就是事务边界）。
        """
        referenced = await self._referenced_capture_ids()
        await self._drop_unrooted_variant_rows(referenced)
        keep = referenced | await self._variant_capture_ids()
        now = self._now_dt().timestamp()
        removed = 0
        for capture_id in await self._captures.list_ids_async():
            if capture_id in keep:
                continue
            modified = await self._captures.modified_at_async(capture_id)
            if modified is None or now - modified < max_age_seconds:
                continue
            await self._captures.delete_async(capture_id)
            removed += 1
        return removed

    async def _drop_unrooted_variant_rows(self, referenced: set[str]) -> None:
        """Drop variant rows whose source is deleted or is itself a derivative."""
        cur = await self._conn.execute(
            "SELECT DISTINCT source_capture_id FROM hygiene_capture_variants"
        )
        stale = [
            str(dict(row)["source_capture_id"])
            for row in await cur.fetchall()
            if str(dict(row)["source_capture_id"]) not in referenced
        ]
        removed = 0
        for chunk in _chunked(stale, SQL_ID_CHUNK_SIZE):
            placeholders = ",".join("?" * len(chunk))
            cur = await self._conn.execute(
                f"""DELETE FROM hygiene_capture_variants
                    WHERE source_capture_id IN ({placeholders})""",
                chunk,
            )
            removed += max(0, cur.rowcount or 0)
        if stale:
            await self._conn.commit()
            logger.info("hygiene variant rows pruned count=%s", removed)

    async def capture_maintenance_loop(
        self,
        interval_seconds: int = 3600,
    ) -> None:
        while True:
            try:
                removed = await self.sweep_capture_orphans()
                if removed:
                    logger.info("hygiene orphan captures removed count=%s", removed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("hygiene capture maintenance failed")
            await asyncio.sleep(interval_seconds)

    @serialized_write
    async def replace_standard(self, actor: dict, item_id: int, capture) -> dict:
        self._require_super(actor)
        data = self._require_capture(capture)
        item = await self._fetch_item(item_id)
        if item is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        content_type = (capture.get("content_type") or "image/jpeg").strip()
        capture_id, generated = await self._store_capture(
            data,
            content_type,
            require_image=True,
        )
        now = self._now_iso()
        try:
            std_cur = await self._conn.execute(
                """INSERT INTO hygiene_standards
                   (item_id, capture_id, content_type, byte_size,
                    content_sha256, markup_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id,
                    capture_id,
                    content_type,
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                    self._markup_json(capture),
                    now,
                ),
            )
            standard_id = int(std_cur.lastrowid)
            await self._conn.execute(
                """UPDATE hygiene_daily_items
                   SET current_standard_id = ?, updated_at = ?
                   WHERE id = ?""",
                (standard_id, now, item_id),
            )
            await self._insert_variants(capture_id, generated)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            await self._delete_capture_files(
                [capture_id, *(item[0] for item in generated.values())]
            )
            raise
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

    @serialized_write
    async def update_standard_markup(self, actor: dict, item_id: int, markup) -> dict:
        """只改当前标准图的标注，不重新上传图片。

        做法是**新插一行 `hygiene_standards`**（复用当前行的 capture_id / 类型 /
        字节数 / sha256，只换 markup_json），再把 `current_standard_id` 指过去：

        * 图片文件、缩略图/预览变体都不用重做，`standard-manifest` 的 sha256 也不变，
          员工端不会因为改了个圈就把几百兆标准图重下一遍；
        * 已经提交的日常记录握着**旧的** frozen_standard_id，它们看到的仍是当时那版
          标注——「冻结标准真冻结」不被这次编辑破坏；
        * `current_standard_id` 变了，员工端正在拍摄时会照常提示「标准图已更新，请先
          查看新版再拍摄」，这正是我们要的：标注变了就该重新看一眼。
        """
        self._require_super(actor)
        item = await self._fetch_item(item_id)
        if item is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        current_id = dict(item)["current_standard_id"]
        if not current_id:
            raise HygieneWorkError("standard_not_found", "standard_not_found")
        cur = await self._conn.execute(
            """SELECT capture_id, content_type, byte_size, content_sha256
               FROM hygiene_standards WHERE id = ?""",
            (int(current_id),),
        )
        row = await cur.fetchone()
        if row is None:
            raise HygieneWorkError("standard_not_found", "standard_not_found")
        current = dict(row)
        now = self._now_iso()
        try:
            std_cur = await self._conn.execute(
                """INSERT INTO hygiene_standards
                   (item_id, capture_id, content_type, byte_size,
                    content_sha256, markup_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(item_id),
                    current["capture_id"],
                    current["content_type"],
                    current["byte_size"],
                    current["content_sha256"],
                    json.dumps(self._normalize_markup(markup), ensure_ascii=False),
                    now,
                ),
            )
            standard_id = int(std_cur.lastrowid)
            await self._conn.execute(
                """UPDATE hygiene_daily_items
                   SET current_standard_id = ?, updated_at = ?
                   WHERE id = ?""",
                (standard_id, now, int(item_id)),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        logger.info(
            "hygiene standard markup updated item=%s standard=%s from=%s",
            item_id,
            standard_id,
            current_id,
        )
        return {
            "id": int(item_id),
            "zone_id": int(dict(item)["zone_id"]),
            "name": dict(item)["name"],
            "current_standard_id": standard_id,
            "capture_id": current["capture_id"],
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

    def _actor_zone_id(self, actor: dict) -> Optional[int]:
        if not actor or actor.get("kind") != "staff":
            return None
        if "zone_id" not in actor:
            return None
        zone_id = actor.get("zone_id")
        if not zone_id:
            raise HygieneWorkError("zone_required", "zone_required")
        return int(zone_id)

    def _require_zone_access(self, actor: dict, zone_id: int) -> None:
        selected = self._actor_zone_id(actor)
        if selected is not None and selected != int(zone_id):
            raise HygieneWorkError("zone_mismatch", "zone_mismatch")

    def _require_zone_access_for(self, actor: Optional[dict], zone_id: int) -> None:
        """带 actor 的读接口守卫：actor 缺失按**拒绝**处理。

        原来写的是 ``actor or {"kind": "super"}``——功能上等于「没传 actor 就是
        全权超管」。当前调用点都传了非空 actor，所以不可利用；但这是「默认放开」
        的形状：将来多一个忘了传 actor 的调用点，横向隔离会静默消失。管理员路径
        必须显式传 ``{"kind": "super"}``。
        """
        if not actor:
            raise HygieneWorkError("forbidden", "forbidden")
        self._require_zone_access(actor, zone_id)

    def _require_live_capture(self, capture) -> bytes:
        if not capture or capture.get("live") is not True:
            raise HygieneWorkError("live_required", "live_required")
        data = capture.get("bytes")
        if not data:
            raise HygieneWorkError("capture_required", "capture_required")
        if len(data) > MAX_CAPTURE_BYTES:
            raise HygieneWorkError("capture_too_large", "capture_too_large")
        return data

    def _photographer(self, actor: dict) -> str:
        return (
            actor.get("name")
            or actor.get("phone")
            or actor.get("display_name")
            or ""
        ).strip()

    def _watermark(self, captured_at: str, zone_name: str, photographer: str) -> dict:
        return {
            "time": captured_at,
            "zone": zone_name,
            "photographer": photographer,
        }

    async def _fetch_item_with_zone(self, item_id: int):
        cur = await self._conn.execute(
            """SELECT i.id, i.zone_id, i.name, i.current_standard_id,
                      z.name AS zone_name, z.day_shift, z.night_shift
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
        now = self._now_iso()
        await self._conn.execute(
            """INSERT OR IGNORE INTO hygiene_daily_instances
               (business_date, shift, item_id, status, pending_submission_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, NULL, ?, ?)""",
            (business_date, shift, item_id, STATUS_TODO, now, now),
        )
        existing = await self._fetch_instance(business_date, shift, item_id)
        if existing is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        return dict(existing)

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

    async def _fetch_latest_submission(self, instance_id: int):
        """该实例最近一次提交，不论它是否还挂在实例上。

        历史回看要用（ADR-0088）：被驳回后 `pending_submission_id` 会清空，但那天确实
        交过一张照片，管理端要能看见它。
        """
        cur = await self._conn.execute(
            """SELECT id, instance_id, capture_id, content_type, frozen_standard_id,
                      submitter_id, submitter_phone, zone_name, captured_at
               FROM hygiene_daily_submissions WHERE instance_id = ?
               ORDER BY id DESC LIMIT 1""",
            (int(instance_id),),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    def _inbox_from_parts(
        self,
        item: dict,
        shift: str,
        business_date: str,
        instance,
        submission,
        rejected: bool = False,
        reject_reason: Optional[str] = None,
    ) -> dict:
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
            # 今天这一项被打回过：员工端据此提示"上次被驳回，请重拍"，
            # 不然他只看到状态回到待拍，会照着原样再拍一遍。
            "rejected": bool(rejected),
            "reject_reason": reject_reason or None,
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
        """交一张日常实拍。

        校验与图片落盘都在写锁**外面**：``_store_capture`` 只碰磁盘（3 次 fsync +
        PIL 编解码，12MP 实测约 100ms），原来它被 ``@serialized_write`` 罩着，等于
        每次拍照都把全局写锁与一个打开的写事务占住，注册、选班、验收、开单全排队。
        """
        target_shift = self._actor_shift(actor, shift)
        data = self._require_live_capture(capture)
        item_row = await self._fetch_item_with_zone(item_id)
        if item_row is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        item = dict(item_row)
        self._require_zone_access(actor, item["zone_id"])
        if not self._zone_shift_enabled(item, target_shift):
            raise HygieneWorkError("zone_shift_mismatch", "zone_shift_mismatch")
        standard_id = item.get("current_standard_id")
        if not standard_id:
            raise HygieneWorkError("standard_required", "standard_required")
        photographer = self._photographer(actor)
        if not photographer:
            raise HygieneWorkError("photographer_required", "photographer_required")
        business_date = hygiene_business_date(self._now_dt())
        content_type = (capture.get("content_type") or "image/jpeg").strip()
        capture_id, generated = await self._store_capture(data, content_type)
        try:
            return await self._commit_daily_submission(
                actor=actor,
                item=item,
                item_id=item_id,
                target_shift=target_shift,
                business_date=business_date,
                standard_id=int(standard_id),
                photographer=photographer,
                capture_id=capture_id,
                generated=generated,
                content_type=content_type,
            )
        except Exception:
            # 事务没落库就把刚写下的文件收拾掉，别给孤儿清理留活。
            await self._delete_capture_files(
                [capture_id, *(variant[0] for variant in generated.values())]
            )
            raise

    @serialized_write
    async def _commit_daily_submission(
        self,
        *,
        actor: dict,
        item: dict,
        item_id: int,
        target_shift: str,
        business_date: str,
        standard_id: int,
        photographer: str,
        capture_id: str,
        generated: dict,
        content_type: str,
    ) -> dict:
        """``submit_daily`` 的纯 SQL 段：拿写锁做事务，不碰磁盘。"""
        instance = await self._ensure_instance(business_date, target_shift, item_id)
        if instance["status"] == STATUS_PASSED:
            raise HygieneWorkError("already_accepted", "already_accepted")
        now = self._now_iso()
        # 待验收状态下的再次提交＝同一张实拍的重传（弱网重发、上传成功但响应丢了
        # 之后的客户端重试）。覆盖原来那条 pending 提交：不新增行、也不再记一次
        # 「实拍」事件，否则红黑榜的实拍次数会平白多出来。被打回后实例回到"待拍"，
        # 那时再交属于新一次实拍，走 INSERT 保留历史。
        previous = None
        if instance["status"] == STATUS_PENDING and instance.get("pending_submission_id"):
            previous = await self._fetch_submission(instance.get("pending_submission_id"))
        if previous is not None and int(previous["submitter_id"]) != int(actor["id"]):
            # 待验收的那条是同事交的：不能覆盖他的证据。换区只留痕、不阻断（§1.1 的
            # 既定决策），所以同区出现别人的待验收是正常情形——按"又一次实拍"处理，
            # 两条提交与两次实拍计数都留着，管理员能看到是谁拍的哪一张。
            previous = None
        try:
            if previous is not None:
                submission_id = int(previous["id"])
                await self._conn.execute(
                    """UPDATE hygiene_daily_submissions
                       SET capture_id = ?, content_type = ?, frozen_standard_id = ?,
                           submitter_id = ?, submitter_phone = ?, zone_name = ?,
                           captured_at = ?
                       WHERE id = ?""",
                    (
                        capture_id,
                        content_type,
                        standard_id,
                        int(actor["id"]),
                        photographer,
                        item["zone_name"],
                        now,
                        submission_id,
                    ),
                )
                await self._conn.execute(
                    """UPDATE hygiene_daily_instances
                       SET status = ?, pending_submission_id = ?, updated_at = ?
                       WHERE id = ?""",
                    (STATUS_PENDING, submission_id, now, instance["id"]),
                )
                await self._insert_variants(capture_id, generated)
                await self._conn.commit()
                # 被替换掉的那张已无任何行引用，交给常规孤儿清理回收（含变体），
                # 比在这里手删文件安全：万一还有别处引用它就不会误删。
                #
                # 已知取舍：并发提交同一项时两个请求都在锁外落盘，锁内后到的那个替换
                # 掉先到的，先到者的文件要等孤儿清理（最长一小时）才回收。只是磁盘
                # 垃圾，计数与证据链都正确（替换分支不记实拍事件）。
                #
                # 只有本人能走到这里：同事交的待验收在上面已被重置成 None、改走
                # INSERT 新增一条，否则换区之后就能把别人的证据无声顶掉。
            else:
                cur = await self._conn.execute(
                    """INSERT INTO hygiene_daily_submissions
                       (instance_id, capture_id, content_type, frozen_standard_id,
                        submitter_id, submitter_phone, zone_name, captured_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        instance["id"],
                        capture_id,
                        content_type,
                        standard_id,
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
                await self._insert_variants(capture_id, generated)
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
        except Exception:
            await self._conn.rollback()
            raise
        logger.info(
            "hygiene daily submitted item=%s shift=%s capture=%s%s",
            item_id,
            target_shift,
            capture_id,
            " (replaced pending)" if previous is not None else "",
        )
        return {
            "item_id": int(item_id),
            "shift": target_shift,
            "business_date": business_date,
            "status": STATUS_PENDING,
            "zone_name": item["zone_name"],
            "capture_id": capture_id,
            "frozen_standard_id": standard_id,
            "submitter_id": int(actor["id"]),
            "submitter_phone": photographer,
            "watermark": self._watermark(now, item["zone_name"], photographer),
        }

    def _inbox_filter(self, actor: dict) -> tuple[tuple[str, ...], Optional[int]]:
        if actor and actor.get("kind") == "staff":
            picked = actor.get("shift")
            if picked not in DAILY_SHIFTS:
                return (), None
            try:
                zone_id = self._actor_zone_id(actor)
            except HygieneWorkError:
                return (), None
            return (picked,), zone_id
        return DAILY_SHIFTS, None

    async def list_daily_work(
        self, actor: dict, business_date: Optional[str] = None
    ) -> list[dict]:
        """列出一个营业日的日常检查。

        不给日期就是当天。带历史日期是管理端回看（ADR-0088）：实例与提交照旧，
        只是不再限定「待验收」——回看要能看见那天交没交、漏了哪些。
        """
        target = business_date or hygiene_business_date(self._now_dt())
        shifts, zone_id = self._inbox_filter(actor)
        if not shifts:
            return []
        shift_sql = " UNION ALL ".join("SELECT ? AS shift" for _ in shifts)
        sql = f"""WITH shifts(shift) AS ({shift_sql})
                 SELECT i.id AS item_id, i.zone_id, i.name AS item_name,
                        i.current_standard_id, z.name AS zone_name,
                        z.day_shift, z.night_shift,
                        s.markup_json,
                        inst.id AS instance_id, inst.status AS instance_status,
                        inst.pending_submission_id,
                        sub.id AS submission_id, sub.capture_id, sub.content_type,
                        sub.frozen_standard_id, sub.submitter_id,
                        sub.submitter_phone, sub.zone_name AS submission_zone_name,
                        sub.captured_at, sh.shift AS shift
                 FROM hygiene_daily_items i
                 JOIN hygiene_zones z ON z.id = i.zone_id
                 JOIN hygiene_standards s ON s.id = i.current_standard_id
                 CROSS JOIN shifts sh
                 LEFT JOIN hygiene_daily_instances inst
                   ON inst.item_id = i.id
                  AND inst.business_date = ?
                  AND inst.shift = sh.shift
                 LEFT JOIN hygiene_daily_submissions sub
                   ON sub.id = inst.pending_submission_id
                 WHERE i.current_standard_id IS NOT NULL"""
        params: list = [*shifts, target]
        if zone_id is not None:
            sql += " AND i.zone_id = ?"
            params.append(zone_id)
        sql += " ORDER BY i.id ASC, sh.shift ASC"
        cur = await self._conn.execute(sql, params)
        rejected_reasons = await self._rejected_reasons(
            target, tuple(shifts), zone_id
        )
        inbox = []
        for row in await cur.fetchall():
            mapping = dict(row)
            if not self._zone_shift_enabled(mapping, mapping["shift"]):
                continue
            item = {
                "id": mapping["item_id"],
                "name": mapping["item_name"],
                "zone_id": mapping["zone_id"],
                "zone_name": mapping["zone_name"],
                "current_standard_id": mapping["current_standard_id"],
                "markup_json": mapping.get("markup_json"),
            }
            instance = None
            if mapping.get("instance_id") is not None:
                instance = {
                    "id": mapping["instance_id"],
                    "status": mapping["instance_status"],
                    "pending_submission_id": mapping.get("pending_submission_id"),
                }
            submission = None
            if mapping.get("submission_id") is not None:
                submission = {
                    "id": mapping["submission_id"],
                    "capture_id": mapping["capture_id"],
                    "content_type": mapping["content_type"],
                    "frozen_standard_id": mapping["frozen_standard_id"],
                    "submitter_id": mapping["submitter_id"],
                    "submitter_phone": mapping["submitter_phone"],
                    "zone_name": mapping["submission_zone_name"],
                    "captured_at": mapping["captured_at"],
                }
            key = (int(mapping["item_id"]), mapping["shift"])
            inbox.append(
                self._inbox_from_parts(
                    item,
                    mapping["shift"],
                    target,
                    instance,
                    submission,
                    rejected=key in rejected_reasons,
                    reject_reason=rejected_reasons.get(key),
                )
            )
        return inbox

    async def get_daily_review(
        self,
        item_id: int,
        shift: str,
        actor: Optional[dict] = None,
        business_date: Optional[str] = None,
    ) -> dict:
        """取一次日常提交的对照资料。

        当天：实例必须是「待验收」，否则报 not_pending（验收路径不变）。
        历史（ADR-0088）：任何状态都能看，被驳回后取最近一次提交，只读。
        """
        if shift not in DAILY_SHIFTS:
            raise HygieneWorkError("shift_mismatch", "shift_mismatch")
        today = hygiene_business_date(self._now_dt())
        target = business_date or today
        instance_row = await self._fetch_instance(target, shift, item_id)
        if instance_row is None:
            raise HygieneWorkError("not_pending", "not_pending")
        instance = dict(instance_row)
        if target == today and instance["status"] != STATUS_PENDING:
            raise HygieneWorkError("not_pending", "not_pending")
        submission = await self._fetch_submission(instance.get("pending_submission_id"))
        if submission is None and target != today:
            submission = await self._fetch_latest_submission(instance["id"])
        if submission is None:
            raise HygieneWorkError("not_pending", "not_pending")
        item = await self._fetch_item_with_zone(item_id)
        if item is not None:
            self._require_zone_access_for(actor, item["zone_id"])
        # 那一版标准图可能已经被「数据与照片」清掉（ADR-0087）：历史回看照常打开，
        # 只是左边没有对照图可显示。
        try:
            standard = await self.standard_by_id(int(submission["frozen_standard_id"]))
        except HygieneWorkError:
            standard = None
        return {
            "item_id": int(item_id),
            "shift": shift,
            "business_date": target,
            "status": instance["status"],
            "historical": target != today,
            "capture_id": submission["capture_id"],
            # 照片与对照图都可能已经被清掉（ADR-0087），
            # 前端据此显示占位而不是破图。
            "capture_available": await self._captures.exists_async(submission["capture_id"]),
            "content_type": submission["content_type"],
            "frozen_standard_id": int(submission["frozen_standard_id"]),
            "frozen_markup": (standard or {}).get("markup") or [],
            "standard_available": bool(standard)
            and await self._captures.exists_async(standard["capture_id"]),
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

    async def _deep_clean_rejected_reasons(self, business_date: str, item_ids) -> dict:
        """当天被驳回过的专项项 → 最近一次原因。

        专项驳回事件的 shift 留空（那是对日常的判据），所以这里按 item_id + shift IS NULL
        区分；一次查完，不在行循环里逐条查。
        """
        ids = [int(item_id) for item_id in item_ids if item_id is not None]
        if not ids or self._has_event_reason is not True:
            return {}
        placeholders = ",".join("?" * len(ids))
        cur = await self._conn.execute(
            f"""SELECT item_id, reason FROM hygiene_board_events
                WHERE board = ? AND event_type = ? AND business_date = ?
                  AND shift IS NULL AND item_id IN ({placeholders})
                ORDER BY id DESC""",
            [BOARD_PERSON, EVENT_REJECT, business_date, *ids],
        )
        found: dict = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            found.setdefault(int(mapping["item_id"]), mapping.get("reason"))
        return found

    async def _fix_rejected_reasons(self, business_date: str, ticket_ids) -> dict:
        """当天被驳回过的整改单 → 最近一次原因（按事件上的 ticket_id 关联）。"""
        ids = [int(ticket_id) for ticket_id in ticket_ids if ticket_id is not None]
        if not ids or self._has_event_ticket is not True:
            return {}
        placeholders = ",".join("?" * len(ids))
        cur = await self._conn.execute(
            f"""SELECT ticket_id, reason FROM hygiene_board_events
                WHERE board = ? AND event_type = ? AND business_date = ?
                  AND ticket_id IN ({placeholders})
                ORDER BY id DESC""",
            [BOARD_PERSON, EVENT_REJECT, business_date, *ids],
        )
        found: dict = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            found.setdefault(int(mapping["ticket_id"]), mapping.get("reason"))
        return found

    async def _rejected_reasons(
        self,
        business_date: str,
        shifts: tuple,
        zone_id: Optional[int] = None,
    ) -> dict:
        """当天被驳回过的 (item_id, shift) → 最近一次的原因（可能为 None）。

        员工端要能看出"这一项是上次被打回的、哪里不合格"，否则他只看到状态回到
        待拍，会照着原样重拍。这里一次查完，不在 list_daily_work 的行循环里逐条查。
        """
        if not shifts:
            return {}
        # 只用 prepare() 预热的缓存：读路径无锁，绝不能触发探测——探测失败要
        # rollback，会把并发写者未提交的事务一起回滚掉（见 _event_reason_supported）。
        has_reason = self._has_event_reason is True
        columns = "item_id, shift, reason" if has_reason else "item_id, shift, NULL AS reason"
        placeholders = ",".join("?" * len(shifts))
        sql = f"""SELECT {columns} FROM hygiene_board_events
                  WHERE board = ? AND event_type = ? AND business_date = ?
                    AND item_id IS NOT NULL AND shift IN ({placeholders})"""
        params: list = [BOARD_PERSON, EVENT_REJECT, business_date, *shifts]
        if zone_id is not None:
            sql += " AND zone_id = ?"
            params.append(int(zone_id))
        # id DESC：同一个 key 第一次出现的就是最近一次驳回。
        sql += " ORDER BY id DESC"
        cur = await self._conn.execute(sql, params)
        found: dict = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            key = (int(mapping["item_id"]), mapping["shift"])
            found.setdefault(key, mapping.get("reason"))
        return found

    @serialized_write
    async def accept_daily(self, actor: dict, item_id: int, shift: str) -> dict:
        instance, submission = await self._pending_instance(item_id, shift)
        item_row = await self._fetch_item_with_zone(item_id)
        if item_row is not None:
            self._require_zone_access(actor, item_row["zone_id"])
        self._require_reviewer(actor, submission["submitter_id"])
        now = self._now_iso()
        cur = await self._conn.execute(
            """UPDATE hygiene_daily_instances
               SET status = ?, updated_at = ?
               WHERE id = ? AND status = ?""",
            (STATUS_PASSED, now, instance["id"], STATUS_PENDING),
        )
        if cur.rowcount != 1:
            await self._conn.rollback()
            raise HygieneWorkError("not_pending", "not_pending")
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

    @serialized_write
    async def reject_daily(
        self,
        actor: dict,
        item_id: int,
        shift: str,
        reason: Optional[str] = None,
    ) -> dict:
        instance, submission = await self._pending_instance(item_id, shift)
        item_row = await self._fetch_item_with_zone(item_id)
        if item_row is not None:
            self._require_zone_access(actor, item_row["zone_id"])
        self._require_reviewer(actor, submission["submitter_id"])
        now = self._now_iso()
        cur = await self._conn.execute(
            """UPDATE hygiene_daily_instances
               SET status = ?, pending_submission_id = NULL, updated_at = ?
               WHERE id = ? AND status = ?""",
            (STATUS_TODO, now, instance["id"], STATUS_PENDING),
        )
        if cur.rowcount != 1:
            await self._conn.rollback()
            raise HygieneWorkError("not_pending", "not_pending")
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
            reason=_clean_reject_reason(reason),
        )
        await self._conn.commit()
        logger.info(
            "hygiene daily rejected item=%s shift=%s reason=%s",
            item_id,
            shift,
            "yes" if _clean_reject_reason(reason) else "no",
        )
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
        hour, minute = (int(part) for part in hhmm.split(":"))
        business = datetime.strptime(hygiene_business_date(local), "%Y-%m-%d").date()
        deadline_date = business
        if hour < BUSINESS_DAY_CUT_HOUR:
            deadline_date = business + timedelta(days=1)
        deadline = datetime(
            deadline_date.year,
            deadline_date.month,
            deadline_date.day,
            hour,
            minute,
            tzinfo=CHINA_TZ,
        )
        return local >= deadline

    async def _catalog_daily_items(self) -> list:
        cur = await self._conn.execute(
            """SELECT i.id, i.zone_id, i.name, z.name AS zone_name,
                      z.day_shift, z.night_shift
               FROM hygiene_daily_items i
               JOIN hygiene_zones z ON z.id = i.zone_id
               WHERE i.current_standard_id IS NOT NULL
               ORDER BY i.id ASC"""
        )
        return [dict(row) for row in await cur.fetchall()]

    async def _overdue_notice_keys(self, business_date: str) -> set[tuple[str, int]]:
        cur = await self._conn.execute(
            """SELECT shift, item_id FROM hygiene_overdue_notices
               WHERE business_date = ?""",
            (business_date,),
        )
        return {
            (str(dict(row)["shift"]), int(dict(row)["item_id"]))
            for row in await cur.fetchall()
        }

    def _overdue_digest_text(
        self,
        business_date: str,
        shift: str,
        items: list[dict],
    ) -> str:
        by_zone: dict[str, list[str]] = {}
        for item in items:
            by_zone.setdefault(item["zone_name"], []).append(item["name"])
        lines = [f"【卫生逾期】{business_date} {shift}"]
        for zone_name, names in by_zone.items():
            lines.append(f"{zone_name}：{'、'.join(names)}")
        lines.append("请到员工卫生入口补拍。")
        return "\n".join(lines)

    async def _record_overdue_notices(
        self,
        business_date: str,
        shift: str,
        items: list[dict],
    ) -> None:
        now = self._now_iso()
        await self._conn.executemany(
            """INSERT OR IGNORE INTO hygiene_overdue_notices
               (business_date, shift, item_id, zone_id, notified_at)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (
                    business_date,
                    shift,
                    int(item["id"]),
                    int(item["zone_id"]),
                    now,
                )
                for item in items
            ],
        )
        for item in items:
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
            # 驳回原因与关联的整改单：不带出来的话它们只进库、任何读取接口都看不到。
            "reason": mapping.get("reason"),
            "ticket_id": (
                None if mapping.get("ticket_id") is None else int(mapping["ticket_id"])
            ),
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
        reason: Optional[str] = None,
        ticket_id: Optional[int] = None,
    ) -> None:
        columns = [
            "board",
            "event_type",
            "zone_id",
            "employee_id",
            "item_id",
            "shift",
            "business_date",
            "occurred_at",
        ]
        values: list = [
            board,
            event_type,
            zone_id,
            employee_id,
            item_id,
            shift,
            business_date,
            self._now_iso(),
        ]
        if reason and self._has_event_reason is True:
            # 只用缓存结果，绝不在写事务里探测：探测失败要 rollback，而它跑在
            # 同一事务的中途，会把前面刚写好的状态更新一起回滚掉（见 prepare）。
            columns.append("reason")
            values.append(reason)
        if ticket_id is not None and self._has_event_ticket is True:
            # 整改单的关联键：整改 id 与检查项不是一套编号，只能单独存。
            columns.append("ticket_id")
            values.append(int(ticket_id))
        placeholders = ",".join("?" * len(columns))
        await self._conn.execute(
            f"INSERT INTO hygiene_board_events ({', '.join(columns)}) "
            f"VALUES ({placeholders})",
            values,
        )

    async def _event_reason_supported(self) -> bool:
        """``hygiene_board_events.reason`` 是否可用（只探一次）。

        SQLite 侧由 ``migrate_hygiene_columns()`` 在启动时补列；PG 按设计不在启动期
        改结构，既有库需要跑 ``migrations/pg/0002_hygiene_indexes.sql``。缺列时降级
        成「能标出被驳回、但没有原因文字」，而不是让驳回本身报错。

        **只允许在无事务上下文里调用（即 ``prepare()``）**：探测失败必须 rollback
        （PG 下 aborted 事务不 rollback 就没法继续用连接），而这条连接是和写请求
        共用的——在读路径里 rollback 会把另一个协程写了一半的事务一起回滚掉
        （实测：并发写者的 UPDATE 会静默丢失）。所以写路径与读路径都只用缓存值。
        """
        if self._has_event_reason is None:
            self._has_event_reason = await self._probe_event_column("reason")
        return bool(self._has_event_reason)

    async def _event_ticket_supported(self) -> bool:
        """``hygiene_board_events.ticket_id`` 是否可用（只探一次）。

        与 reason 分开探测：用户可能只应用了 0002 而没应用 0003，那时整改驳回的
        标记降级为"认不出"，但日常/专项的驳回原因仍然可用。
        同样只允许在无事务上下文（``prepare()``）里调用。
        """
        if self._has_event_ticket is None:
            self._has_event_ticket = await self._probe_event_column("ticket_id")
        return bool(self._has_event_ticket)

    async def _probe_event_column(self, column: str) -> bool:
        """探一列在不在。列名只来自代码里的两个字面量，不接受外部输入。"""
        try:
            cur = await self._conn.execute(
                f"SELECT {column} FROM hygiene_board_events LIMIT 0"
            )
            await cur.fetchall()
            return True
        except Exception:
            await self._conn.rollback()
            logger.error(
                "hygiene_board_events.%s 不存在，相关功能降级；"
                "PG 部署请应用 migrations/pg/ 下的增量迁移",
                column,
            )
            return False

    @serialized_write
    async def record_zone_switch(
        self,
        actor: dict,
        *,
        from_zone_id: int,
        from_zone_name: str,
        to_zone_id: int,
        to_zone_name: str,
    ) -> dict:
        """留痕：员工当天自己把责任区从 A 换成 B。

        换区本身允许（门店临时换岗是真实需求），这里只记录事件，让个人榜能显示
        换区次数、事件流能还原时间线——否则一个跨区刷实拍的人，数字与只在本区干活
        的人看起来一样。管理员改派走 super_set_assignment，不经过这里。
        """
        if not actor or actor.get("id") is None:
            raise HygieneWorkError("forbidden", "forbidden")
        business_date = hygiene_business_date(self._now_dt())
        employee_id = int(actor["id"])
        await self._insert_board_event(
            BOARD_PERSON,
            EVENT_ZONE_SWITCH,
            zone_id=int(to_zone_id),
            employee_id=employee_id,
            business_date=business_date,
        )
        await self._conn.commit()
        logger.info(
            "hygiene zone switched employee=%s date=%s from=%s(%s) to=%s(%s)",
            employee_id,
            business_date,
            from_zone_id,
            from_zone_name,
            to_zone_id,
            to_zone_name,
        )
        return {
            "employee_id": employee_id,
            "business_date": business_date,
            "from_zone_id": int(from_zone_id),
            "from_zone_name": from_zone_name,
            "to_zone_id": int(to_zone_id),
            "to_zone_name": to_zone_name,
        }

    async def list_zone_board_events(
        self,
        zone_id=None,
        since: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list:
        reason_col = "reason" if self._has_event_reason is True else "NULL AS reason"
        ticket_col = (
            "ticket_id" if self._has_event_ticket is True else "NULL AS ticket_id"
        )
        columns = (
            "id, board, event_type, zone_id, employee_id, item_id, "
            f"shift, business_date, occurred_at, {reason_col}, {ticket_col}"
        )
        sql = f"""SELECT {columns}
                 FROM hygiene_board_events
                 WHERE board = ?"""
        params: list = [BOARD_ZONE]
        if zone_id is not None:
            sql += " AND zone_id = ?"
            params.append(int(zone_id))
        if since:
            sql += " AND occurred_at >= ?"
            params.append(since)
        sql += " ORDER BY id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        cur = await self._conn.execute(sql, params)
        return [self._event_from_row(row) for row in await cur.fetchall()]

    async def list_person_board_events(
        self,
        employee_id=None,
        since: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list:
        reason_col = "reason" if self._has_event_reason is True else "NULL AS reason"
        ticket_col = (
            "ticket_id" if self._has_event_ticket is True else "NULL AS ticket_id"
        )
        columns = (
            "id, board, event_type, zone_id, employee_id, item_id, "
            f"shift, business_date, occurred_at, {reason_col}, {ticket_col}"
        )
        sql = f"""SELECT {columns}
                 FROM hygiene_board_events
                 WHERE board = ?"""
        params: list = [BOARD_PERSON]
        if employee_id is not None:
            sql += " AND employee_id = ?"
            params.append(int(employee_id))
        if since:
            sql += " AND occurred_at >= ?"
            params.append(since)
        sql += " ORDER BY id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        cur = await self._conn.execute(sql, params)
        return [self._event_from_row(row) for row in await cur.fetchall()]

    @serialized_write
    async def purge_old_board_events(self, now: Optional[datetime] = None) -> int:
        """清掉保留期之外的看板事件，返回删除行数。"""
        clock = now or self._now_dt()
        cutoff = (clock - timedelta(days=BOARD_EVENT_RETENTION_DAYS)).isoformat()
        cur = await self._conn.execute(
            "DELETE FROM hygiene_board_events WHERE occurred_at < ?",
            (cutoff,),
        )
        removed = int(cur.rowcount or 0)
        await self._conn.commit()
        if removed:
            logger.info("卫生看板事件清理 removed=%s cutoff=%s", removed, cutoff)
        return removed

    async def _maybe_purge_board_events(self) -> None:
        now = time.monotonic()
        if now - self._last_board_purge < BOARD_EVENT_PURGE_INTERVAL_SECONDS:
            return
        self._last_board_purge = now
        try:
            await self.purge_old_board_events()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("卫生看板事件清理失败: %s", exc)

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
        start, end = self._week_bounds(now)
        cur = await self._conn.execute(
            """SELECT employee_id, event_type, COUNT(*) AS event_count
               FROM hygiene_board_events
               WHERE board = ? AND occurred_at >= ? AND occurred_at < ?
                 AND employee_id IS NOT NULL
               GROUP BY employee_id, event_type""",
            (BOARD_PERSON, start.isoformat(), end.isoformat()),
        )
        by_id = {}
        for event_row in await cur.fetchall():
            event = dict(event_row)
            employee_id = int(event["employee_id"])
            row = by_id.setdefault(
                employee_id,
                {
                    "employee_id": employee_id,
                    "name": None,
                    "phone": None,
                    **{key: 0 for key in PERSON_COUNT_KEYS},
                },
            )
            kind = event["event_type"]
            if kind in PERSON_COUNT_KEYS:
                row[kind] += int(event["event_count"])
        if by_id:
            placeholders = ",".join("?" * len(by_id))
            cur = await self._conn.execute(
                f"SELECT id, name, phone FROM hygiene_employees WHERE id IN ({placeholders})",
                list(by_id),
            )
            for row in await cur.fetchall():
                mapping = dict(row)
                by_id[int(mapping["id"])]["name"] = mapping["name"]
                by_id[int(mapping["id"])]["phone"] = mapping["phone"]
        people = list(by_id.values())
        people.sort(key=lambda row: (-row["逾期"], -row["驳回"], row["employee_id"]))
        return people

    async def zone_board(self, now: Optional[datetime] = None) -> list:
        start, end = self._week_bounds(now)
        cur = await self._conn.execute(
            """SELECT zone_id, event_type, COUNT(*) AS event_count
               FROM hygiene_board_events
               WHERE board = ? AND occurred_at >= ? AND occurred_at < ?
                 AND zone_id IS NOT NULL
               GROUP BY zone_id, event_type""",
            (BOARD_ZONE, start.isoformat(), end.isoformat()),
        )
        by_id = {}
        for event_row in await cur.fetchall():
            event = dict(event_row)
            zone_id = int(event["zone_id"])
            row = by_id.setdefault(
                zone_id,
                {
                    "zone_id": zone_id,
                    "zone_name": None,
                    **{key: 0 for key in ZONE_COUNT_KEYS},
                },
            )
            kind = event["event_type"]
            if kind in ZONE_COUNT_KEYS:
                row[kind] += int(event["event_count"])
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
        inst_cur = await self._conn.execute(
            """SELECT item_id, shift, status
               FROM hygiene_daily_instances
               WHERE business_date = ?""",
            (business_date,),
        )
        statuses = {
            (str(dict(row)["shift"]), int(dict(row)["item_id"])): str(
                dict(row)["status"]
            )
            for row in await inst_cur.fetchall()
        }
        noticed = await self._overdue_notice_keys(business_date)
        notified = []
        for shift, hhmm in shift_clocks:
            if not self._clock_reached(now, hhmm):
                continue
            missing = []
            for item in items:
                if not self._zone_shift_enabled(item, shift):
                    continue
                key = (shift, int(item["id"]))
                if statuses.get(key, STATUS_TODO) != STATUS_TODO:
                    continue
                if key in noticed:
                    continue
                missing.append(item)
            if not missing:
                continue
            text = self._overdue_digest_text(business_date, shift, missing)
            if self._notifier is not None:
                await self._notifier.notify_group_text(text)
            await self._record_overdue_notices(business_date, shift, missing)
            for item in missing:
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
        if notified and self._on_change is not None:
            await self._on_change({
                "resource": "boards",
                "action": "overdue",
                "business_date": business_date,
            })
        return notified

    async def _deep_clean_complete(self, business_date: str) -> bool:
        weekday = self._weekday_of(business_date)
        cur = await self._conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM hygiene_deep_clean_items
                  WHERE weekday = ?) AS total,
                 (SELECT COUNT(*)
                  FROM hygiene_deep_clean_instances inst
                  JOIN hygiene_deep_clean_items item ON item.id = inst.item_id
                  WHERE inst.business_date = ?
                    AND item.weekday = ?
                    AND inst.status = ?) AS passed""",
            (weekday, business_date, weekday, STATUS_PASSED),
        )
        row = dict(await cur.fetchone())
        return int(row["total"]) == 0 or int(row["passed"]) == int(row["total"])

    async def _deep_clean_all_submitted(self, business_date: str) -> bool:
        weekday = self._weekday_of(business_date)
        cur = await self._conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM hygiene_deep_clean_items
                  WHERE weekday = ?) AS total,
                 (SELECT COUNT(*)
                  FROM hygiene_deep_clean_instances inst
                  JOIN hygiene_deep_clean_items item ON item.id = inst.item_id
                  WHERE inst.business_date = ?
                    AND item.weekday = ?
                    AND inst.status != ?) AS submitted""",
            (weekday, business_date, weekday, STATUS_TODO),
        )
        row = dict(await cur.fetchone())
        return int(row["total"]) == 0 or int(row["submitted"]) == int(row["total"])

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
        if await self._deep_clean_all_submitted(business_date):
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
        start_text = start.isoformat()
        end_text = end.isoformat()
        item_cur = await self._conn.execute(
            """SELECT weekday, COUNT(*) AS item_count
               FROM hygiene_deep_clean_items
               GROUP BY weekday"""
        )
        item_counts = {
            int(dict(row)["weekday"]): int(dict(row)["item_count"])
            for row in await item_cur.fetchall()
        }
        inst_cur = await self._conn.execute(
            """SELECT inst.business_date, item.weekday, inst.status, COUNT(*) AS item_count
               FROM hygiene_deep_clean_instances inst
               JOIN hygiene_deep_clean_items item ON item.id = inst.item_id
               WHERE inst.business_date BETWEEN ? AND ?
               GROUP BY inst.business_date, item.weekday, inst.status""",
            (start_text, end_text),
        )
        instance_counts: dict[tuple[str, int, str], int] = {}
        for row in await inst_cur.fetchall():
            mapping = dict(row)
            instance_counts[
                (
                    str(mapping["business_date"]),
                    int(mapping["weekday"]),
                    str(mapping["status"]),
                )
            ] = int(mapping["item_count"])
        notice_cur = await self._conn.execute(
            """SELECT business_date FROM hygiene_deep_clean_overdue_notices
               WHERE business_date BETWEEN ? AND ?""",
            (start_text, end_text),
        )
        missed_dates = {str(dict(row)["business_date"]) for row in await notice_cur.fetchall()}
        days = []
        cursor = start
        while cursor <= end:
            business_date = cursor.isoformat()
            weekday = cursor.weekday()
            item_count = item_counts.get(weekday, 0)
            if item_count:
                complete = (
                    instance_counts.get((business_date, weekday, STATUS_PASSED), 0)
                    == item_count
                )
                missed = business_date in missed_dates
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
                        "item_count": item_count,
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

    @serialized_write
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

    @serialized_write
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
        try:
            # 提交过一次的专项项都有 instance 行，外键指向 item：必须先按依赖顺序清
            # 干净，否则超管删一个用过的专项项必然 IntegrityError（当时是 500）。
            await self._drop_deep_clean_item_rows(int(item_id))
            await self._conn.execute(
                "DELETE FROM hygiene_deep_clean_items WHERE id = ?",
                (int(item_id),),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        logger.info(
            "hygiene deep clean item removed id=%s weekday=%s",
            mapping["id"],
            mapping["weekday"],
        )
        return {
            "id": int(mapping["id"]),
            "weekday": int(mapping["weekday"]),
            "name": mapping["name"],
        }

    async def _drop_deep_clean_item_rows(self, item_id: int) -> None:
        """按依赖顺序清掉专项项的实例与提交（capture 文件交给常规孤儿清理）。"""
        cur = await self._conn.execute(
            "SELECT id FROM hygiene_deep_clean_instances WHERE item_id = ?",
            (int(item_id),),
        )
        instance_ids = [int(dict(row)["id"]) for row in await cur.fetchall()]
        if instance_ids:
            placeholders = ",".join("?" * len(instance_ids))
            await self._conn.execute(
                f"UPDATE hygiene_deep_clean_instances SET pending_submission_id = NULL "
                f"WHERE id IN ({placeholders})",
                instance_ids,
            )
            await self._conn.execute(
                f"DELETE FROM hygiene_deep_clean_submissions "
                f"WHERE instance_id IN ({placeholders})",
                instance_ids,
            )
            await self._conn.execute(
                f"DELETE FROM hygiene_deep_clean_instances WHERE id IN ({placeholders})",
                instance_ids,
            )
        await self._drop_teaching_examples(TEACHING_DEEP_CLEAN, [int(item_id)])

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

    def _deep_clean_row(
        self,
        item: dict,
        business_date: str,
        instance,
        submission,
        rejected: bool = False,
        reject_reason=None,
    ) -> dict:
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
            # 今天这一项被打回过：员工端要明说，否则他只看到状态回到"待回拍"。
            "rejected": bool(rejected),
            "reject_reason": reject_reason or None,
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
        cur = await self._conn.execute(
            """SELECT i.id AS item_id, i.weekday, i.name AS item_name,
                      inst.id AS instance_id, inst.status AS instance_status,
                      inst.pending_submission_id,
                      sub.id AS submission_id,
                      sub.before_capture_id, sub.after_capture_id,
                      sub.before_content_type, sub.after_content_type,
                      sub.submitter_id, sub.submitter_phone, sub.item_name AS submission_item_name,
                      sub.before_captured_at, sub.after_captured_at
               FROM hygiene_deep_clean_items i
               LEFT JOIN hygiene_deep_clean_instances inst
                 ON inst.item_id = i.id AND inst.business_date = ?
               LEFT JOIN hygiene_deep_clean_submissions sub
                 ON sub.id = inst.pending_submission_id
               WHERE i.weekday = ?
               ORDER BY i.id ASC""",
            (business_date, weekday),
        )
        raw_rows = [dict(row) for row in await cur.fetchall()]
        rejected_reasons = await self._deep_clean_rejected_reasons(
            business_date, [row["item_id"] for row in raw_rows]
        )
        rows = []
        for mapping in raw_rows:
            item = {
                "id": mapping["item_id"],
                "weekday": mapping["weekday"],
                "name": mapping["item_name"],
            }
            instance = None
            if mapping.get("instance_id") is not None:
                instance = {
                    "id": mapping["instance_id"],
                    "status": mapping["instance_status"],
                    "pending_submission_id": mapping.get("pending_submission_id"),
                }
            submission = None
            if mapping.get("submission_id") is not None:
                submission = {
                    "id": mapping["submission_id"],
                    "before_capture_id": mapping["before_capture_id"],
                    "after_capture_id": mapping["after_capture_id"],
                    "before_content_type": mapping["before_content_type"],
                    "after_content_type": mapping["after_content_type"],
                    "submitter_id": mapping["submitter_id"],
                    "submitter_phone": mapping["submitter_phone"],
                    "item_name": mapping["submission_item_name"],
                    "before_captured_at": mapping["before_captured_at"],
                    "after_captured_at": mapping["after_captured_at"],
                }
            rows.append(
                self._deep_clean_row(
                    item,
                    business_date,
                    instance,
                    submission,
                    rejected=int(mapping["item_id"]) in rejected_reasons,
                    reject_reason=rejected_reasons.get(int(mapping["item_id"])),
                )
            )
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
        now = self._now_iso()
        await self._conn.execute(
            """INSERT OR IGNORE INTO hygiene_deep_clean_instances
               (business_date, item_id, status, pending_submission_id, created_at, updated_at)
               VALUES (?, ?, ?, NULL, ?, ?)""",
            (business_date, int(item_id), STATUS_TODO, now, now),
        )
        existing = await self._fetch_deep_clean_instance(business_date, item_id)
        if existing is None:
            raise HygieneWorkError("item_not_found", "item_not_found")
        return existing

    @serialized_write
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
        before_id, before_generated = await self._store_capture(before_data, before_type)
        after_id, after_generated = await self._store_capture(after_data, after_type)
        now = self._now_iso()
        try:
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
            await self._insert_variants(before_id, before_generated)
            await self._insert_variants(after_id, after_generated)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            await self._delete_capture_files(
                [
                    before_id,
                    after_id,
                    *(item[0] for item in before_generated.values()),
                    *(item[0] for item in after_generated.values()),
                ]
            )
            raise
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

    @serialized_write
    async def accept_deep_clean_pair(self, actor: dict, item_id: int) -> dict:
        instance, submission = await self._pending_deep_clean(item_id)
        self._require_reviewer(actor, submission["submitter_id"])
        now = self._now_iso()
        cur = await self._conn.execute(
            """UPDATE hygiene_deep_clean_instances
               SET status = ?, updated_at = ?
               WHERE id = ? AND status = ?""",
            (STATUS_PASSED, now, instance["id"], STATUS_PENDING),
        )
        if cur.rowcount != 1:
            await self._conn.rollback()
            raise HygieneWorkError("not_pending", "not_pending")
        await self._conn.commit()
        logger.info("hygiene deep-clean accepted item=%s", item_id)
        work = await self.list_deep_clean_work(actor)
        return {
            "item_id": int(item_id),
            "business_date": instance["business_date"],
            "status": STATUS_PASSED,
            "task_status": work["status"],
        }

    @serialized_write
    async def reject_deep_clean_pair(
        self,
        actor: dict,
        item_id: int,
        reason: Optional[str] = None,
    ) -> dict:
        instance, submission = await self._pending_deep_clean(item_id)
        self._require_reviewer(actor, submission["submitter_id"])
        now = self._now_iso()
        cur = await self._conn.execute(
            """UPDATE hygiene_deep_clean_instances
               SET status = ?, pending_submission_id = NULL, updated_at = ?
               WHERE id = ? AND status = ?""",
            (STATUS_TODO, now, instance["id"], STATUS_PENDING),
        )
        if cur.rowcount != 1:
            await self._conn.rollback()
            raise HygieneWorkError("not_pending", "not_pending")
        # 专项驳回原来一条事件都不写：红黑榜的「驳回」只统计得到日常，员工端也完全
        # 看不出被打回过。shift 留空，免得与日常的 (item_id, shift) 判据撞车。
        await self._insert_board_event(
            BOARD_PERSON,
            EVENT_REJECT,
            employee_id=int(submission["submitter_id"]),
            item_id=int(item_id),
            business_date=instance["business_date"],
            reason=_clean_reject_reason(reason),
        )
        await self._conn.commit()
        logger.info(
            "hygiene deep-clean rejected item=%s reason=%s",
            item_id,
            "yes" if _clean_reject_reason(reason) else "no",
        )
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

    def _fix_row(
        self,
        ticket: dict,
        reshoot=None,
        rejected: bool = False,
        reject_reason=None,
    ) -> dict:
        opener_id = ticket.get("opener_id")
        open_watermark = self._watermark(
            ticket.get("created_at") or self._now_iso(),
            ticket["zone_name"],
            ticket.get("opener_phone") or "",
        )
        watermark = open_watermark
        reshoot_capture_id = None
        if reshoot is not None:
            reshoot_capture_id = reshoot["capture_id"]
            watermark = self._watermark(
                reshoot["captured_at"],
                reshoot["zone_name"],
                reshoot["photographer_phone"],
            )
        return {
            # 这张单被打回过：员工端要能区分"被打回"与"从没回拍过"。
            "rejected": bool(rejected),
            "reject_reason": reject_reason or None,
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
            "open_watermark": open_watermark,
            "watermark": watermark,
        }

    async def _fetch_fix_ticket(self, ticket_id: int):
        cur = await self._conn.execute(
            """SELECT t.id, t.zone_id, t.ticket_type, t.body_text, t.duration_seconds,
                      t.deadline, t.opener_kind, t.opener_id, t.opener_phone, t.status,
                      t.capture_id, t.content_type, t.markup_json, t.pending_reshoot_id,
                      t.created_at, z.name AS zone_name
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

    @serialized_write
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
        self._require_zone_access(actor, zone_id)
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
        capture_id, generated = await self._store_capture(data, content_type)
        now = self._now_iso()
        duration_seconds = int(span.total_seconds())
        deadline = (self._now_dt() + span).isoformat()
        try:
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
            await self._insert_variants(capture_id, generated)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            await self._delete_capture_files(
                [capture_id, *(item[0] for item in generated.values())]
            )
            raise
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
            "open_watermark": self._watermark(now, zone_name, opener_phone),
            "watermark": self._watermark(now, zone_name, opener_phone),
        }

    async def list_fix_tickets(self, actor: dict) -> list:
        sql = """SELECT t.id, t.zone_id, t.ticket_type, t.body_text, t.duration_seconds,
                        t.deadline, t.opener_kind, t.opener_id, t.opener_phone, t.status,
                        t.capture_id, t.content_type, t.markup_json, t.pending_reshoot_id,
                        t.created_at, z.name AS zone_name,
                        r.id AS reshoot_id, r.capture_id AS reshoot_capture_id,
                        r.content_type AS reshoot_content_type,
                        r.photographer_id AS reshoot_photographer_id,
                        r.photographer_phone AS reshoot_photographer_phone,
                        r.zone_name AS reshoot_zone_name,
                        r.captured_at AS reshoot_captured_at
                 FROM hygiene_fix_tickets t
                 JOIN hygiene_zones z ON z.id = t.zone_id
                 LEFT JOIN hygiene_fix_reshoots r ON r.id = t.pending_reshoot_id
                 WHERE t.status != ?"""
        params: list = [STATUS_PASSED]
        zone_id = self._actor_zone_id(actor)
        if zone_id is not None:
            sql += " AND t.zone_id = ?"
            params.append(zone_id)
        sql += " ORDER BY t.id ASC"
        cur = await self._conn.execute(sql, params)
        raw_rows = [dict(row) for row in await cur.fetchall()]
        rejected_reasons = await self._fix_rejected_reasons(
            hygiene_business_date(self._now_dt()),
            [row["id"] for row in raw_rows],
        )
        rows = []
        for ticket in raw_rows:
            reshoot = None
            if ticket.get("reshoot_id") is not None:
                reshoot = {
                    "id": ticket["reshoot_id"],
                    "capture_id": ticket["reshoot_capture_id"],
                    "content_type": ticket["reshoot_content_type"],
                    "photographer_id": ticket["reshoot_photographer_id"],
                    "photographer_phone": ticket["reshoot_photographer_phone"],
                    "zone_name": ticket["reshoot_zone_name"],
                    "captured_at": ticket["reshoot_captured_at"],
                }
            rows.append(
                self._fix_row(
                    ticket,
                    reshoot,
                    rejected=int(ticket["id"]) in rejected_reasons,
                    reject_reason=rejected_reasons.get(int(ticket["id"])),
                )
            )
        return rows

    async def get_fix_ticket(
        self,
        ticket_id: int,
        actor: Optional[dict] = None,
    ) -> dict:
        ticket = await self._fetch_fix_ticket(ticket_id)
        if ticket is None:
            raise HygieneWorkError("ticket_not_found", "ticket_not_found")
        self._require_zone_access_for(actor, ticket["zone_id"])
        reshoot = await self._fetch_fix_reshoot(ticket.get("pending_reshoot_id"))
        return self._fix_row(ticket, reshoot)

    async def get_fix_review(
        self,
        ticket_id: int,
        actor: Optional[dict] = None,
    ) -> dict:
        ticket = await self._fetch_fix_ticket(ticket_id)
        if ticket is None:
            raise HygieneWorkError("ticket_not_found", "ticket_not_found")
        self._require_zone_access_for(actor, ticket["zone_id"])
        if ticket["status"] != STATUS_PENDING:
            raise HygieneWorkError("not_pending", "not_pending")
        reshoot = await self._fetch_fix_reshoot(ticket.get("pending_reshoot_id"))
        if reshoot is None:
            raise HygieneWorkError("not_pending", "not_pending")
        return self._fix_row(ticket, reshoot)

    @serialized_write
    async def reshoot_fix(self, actor: dict, ticket_id: int, live_capture) -> dict:
        self._require_staff_submitter(actor)
        data = self._require_live_capture(live_capture)
        ticket = await self._fetch_fix_ticket(ticket_id)
        if ticket is None:
            raise HygieneWorkError("ticket_not_found", "ticket_not_found")
        self._require_zone_access(actor, ticket["zone_id"])
        if ticket["status"] == STATUS_PASSED:
            raise HygieneWorkError("already_accepted", "already_accepted")
        photographer = self._photographer(actor)
        if not photographer:
            raise HygieneWorkError("photographer_required", "photographer_required")
        content_type = (live_capture.get("content_type") or "image/jpeg").strip()
        capture_id, generated = await self._store_capture(data, content_type)
        now = self._now_iso()
        try:
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
            await self._insert_variants(capture_id, generated)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            await self._delete_capture_files(
                [capture_id, *(item[0] for item in generated.values())]
            )
            raise
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

    @serialized_write
    async def accept_fix(self, actor: dict, ticket_id: int) -> dict:
        ticket, _reshoot = await self._pending_fix(ticket_id)
        self._require_zone_access(actor, ticket["zone_id"])
        self._require_fix_reviewer(actor, ticket)
        now = self._now_iso()
        cur = await self._conn.execute(
            """UPDATE hygiene_fix_tickets
               SET status = ?, updated_at = ?
               WHERE id = ? AND status = ?""",
            (STATUS_PASSED, now, int(ticket_id), STATUS_PENDING),
        )
        if cur.rowcount != 1:
            await self._conn.rollback()
            raise HygieneWorkError("not_pending", "not_pending")
        await self._conn.commit()
        logger.info("hygiene fix accepted ticket=%s", ticket_id)
        return {
            "id": int(ticket_id),
            "status": STATUS_PASSED,
            "deadline": ticket["deadline"],
        }

    @serialized_write
    async def reject_fix(
        self,
        actor: dict,
        ticket_id: int,
        reason: Optional[str] = None,
    ) -> dict:
        ticket, reshoot = await self._pending_fix(ticket_id)
        self._require_zone_access(actor, ticket["zone_id"])
        self._require_fix_reviewer(actor, ticket)
        now_dt = self._now_dt()
        now = now_dt.isoformat()
        deadline = (now_dt + timedelta(seconds=int(ticket["duration_seconds"]))).isoformat()
        cur = await self._conn.execute(
            """UPDATE hygiene_fix_tickets
               SET status = ?, pending_reshoot_id = NULL, deadline = ?, updated_at = ?
               WHERE id = ? AND status = ?""",
            (STATUS_FIX_TODO, deadline, now, int(ticket_id), STATUS_PENDING),
        )
        if cur.rowcount != 1:
            await self._conn.rollback()
            raise HygieneWorkError("not_pending", "not_pending")
        # 与专项同理：整改回拍被驳回也不写事件，红黑榜统计不到。记在回拍人名下
        # （没有 item_id，整改单的 id 与检查项不是一套编号，不混用）。
        photographer_id = None
        if reshoot is not None and reshoot.get("photographer_id") is not None:
            try:
                photographer_id = int(reshoot["photographer_id"])
            except (TypeError, ValueError):
                photographer_id = None
        await self._insert_board_event(
            BOARD_PERSON,
            EVENT_REJECT,
            zone_id=int(ticket["zone_id"]),
            employee_id=photographer_id,
            business_date=hygiene_business_date(now_dt),
            reason=_clean_reject_reason(reason),
            ticket_id=int(ticket_id),
        )
        await self._conn.commit()
        logger.info(
            "hygiene fix rejected ticket=%s deadline=%s reason=%s",
            ticket_id,
            deadline,
            "yes" if _clean_reject_reason(reason) else "no",
        )
        return {
            "id": int(ticket_id),
            "status": STATUS_FIX_TODO,
            "deadline": deadline,
            "reshoot_capture_id": None,
        }

    async def _fix_ticket_capture_ids(self, ticket_id: int, ticket: dict) -> tuple[list[str], list[str]]:
        """Return (original captures, derivative captures) owned by one fix ticket."""
        cur = await self._conn.execute(
            """SELECT capture_id FROM hygiene_fix_reshoots
               WHERE ticket_id = ? ORDER BY id ASC""",
            (int(ticket_id),),
        )
        roots = [str(ticket["capture_id"])]
        roots.extend(str(dict(row)["capture_id"]) for row in await cur.fetchall())
        placeholders = ",".join("?" * len(roots))
        cur = await self._conn.execute(
            f"""SELECT capture_id FROM hygiene_capture_variants
                WHERE source_capture_id IN ({placeholders})""",
            roots,
        )
        derivatives = [str(dict(row)["capture_id"]) for row in await cur.fetchall()]
        return roots, derivatives

    @serialized_write
    async def delete_fix_ticket(self, actor: dict, ticket_id: int) -> dict:
        """超级管理员删除整改单，连带回拍、逾期记录与实拍文件。"""
        self._require_super(actor)
        ticket = await self._fetch_fix_ticket(ticket_id)
        if ticket is None:
            raise HygieneWorkError("ticket_not_found", "ticket_not_found")
        roots, derivatives = await self._fix_ticket_capture_ids(
            int(ticket_id), ticket
        )
        try:
            await self._conn.execute(
                "UPDATE hygiene_fix_tickets SET pending_reshoot_id = NULL WHERE id = ?",
                (int(ticket_id),),
            )
            await self._conn.execute(
                "DELETE FROM hygiene_fix_reshoots WHERE ticket_id = ?",
                (int(ticket_id),),
            )
            await self._conn.execute(
                "DELETE FROM hygiene_fix_overdue_notices WHERE ticket_id = ?",
                (int(ticket_id),),
            )
            placeholders = ",".join("?" * len(roots))
            await self._conn.execute(
                f"""DELETE FROM hygiene_capture_variants
                    WHERE source_capture_id IN ({placeholders})""",
                roots,
            )
            await self._conn.execute(
                "DELETE FROM hygiene_fix_tickets WHERE id = ?",
                (int(ticket_id),),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        await self._delete_capture_files([*roots, *derivatives])
        logger.info(
            "hygiene fix deleted ticket=%s zone=%s",
            ticket_id,
            ticket["zone_id"],
        )
        return {
            "id": int(ticket["id"]),
            "zone_id": int(ticket["zone_id"]),
            "zone_name": ticket["zone_name"],
            "status": ticket["status"],
        }

    async def _fix_notice_ids(self) -> set[int]:
        cur = await self._conn.execute(
            "SELECT ticket_id FROM hygiene_fix_overdue_notices"
        )
        return {int(dict(row)["ticket_id"]) for row in await cur.fetchall()}

    def _fix_overdue_digest_text(self, zone_name: str, tickets: list[dict]) -> str:
        lines = [f"【整改逾期】{zone_name}"]
        for ticket in tickets:
            body = (ticket.get("body_text") or "").strip()
            suffix = f"：{body}" if body else ""
            lines.append(f"- {ticket['ticket_type']}{suffix}")
        lines.append("请到员工卫生入口回拍。")
        return "\n".join(lines)

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
        last_photographer_id = ticket.get("last_photographer_id")
        if last_photographer_id is not None:
            await self._insert_board_event(
                BOARD_PERSON,
                EVENT_MISSED_DAILY,
                zone_id=zone_id,
                employee_id=int(last_photographer_id),
            )

    async def _sweep_fix_overdue(self, now: datetime) -> list:
        cur = await self._conn.execute(
            """SELECT t.id, t.zone_id, t.ticket_type, t.body_text, t.deadline, t.status,
                      z.name AS zone_name,
                      (SELECT r.photographer_id
                       FROM hygiene_fix_reshoots r
                       WHERE r.ticket_id = t.id
                       ORDER BY r.id DESC LIMIT 1) AS last_photographer_id
               FROM hygiene_fix_tickets t
               JOIN hygiene_zones z ON z.id = t.zone_id
               WHERE t.status != ?""",
            (STATUS_PASSED,),
        )
        noticed = await self._fix_notice_ids()
        by_zone: dict[str, list[dict]] = {}
        for row in await cur.fetchall():
            ticket = dict(row)
            if self._parse_iso(ticket["deadline"]) > now:
                continue
            if int(ticket["id"]) in noticed:
                continue
            by_zone.setdefault(ticket["zone_name"], []).append(ticket)
        notified = []
        for zone_name, tickets in by_zone.items():
            text = self._fix_overdue_digest_text(zone_name, tickets)
            if self._notifier is not None:
                await self._notifier.notify_group_text(text)
            for ticket in tickets:
                await self._record_fix_overdue(ticket)
                notified.append(
                    {
                        "ticket_id": int(ticket["id"]),
                        "zone_id": int(ticket["zone_id"]),
                        "kind": "整改单",
                        "text": text,
                    }
                )
            await self._conn.commit()
        return notified

    async def overdue_scheduler_loop(self) -> None:
        logger.info("卫生逾期调度器已启动")
        while True:
            try:
                await self.sweep_overdue()
                await self._maybe_purge_board_events()
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

    @serialized_write
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

    # ---- 数据与照片的管理端删除（ADR-0087）-----------------------------------
    #
    # 删除只走这里：写锁、状态回退、照片与变体文件的清理都在同一个地方，别处不要再
    # 拼一套 DELETE。查询与打包在 services/hygiene/archive.py，那边只读。
    #
    # 一致性口径：先删库、commit，再删文件。反过来的话，文件删了而事务回滚，记录就
    # 指向一张不存在的图；库先删则最坏是留下一个孤儿文件，由 sweep_capture_orphans 收。

    @serialized_write
    async def delete_archive_record(self, actor: dict, kind: str, record_id: int) -> dict:
        """删一条卫生记录连同它的照片。"""
        self._require_super(actor)
        normalized = str(kind or "").strip()
        if normalized not in ARCHIVE_DELETE_KINDS:
            raise HygieneWorkError("bad_kind", "bad_kind")
        target = int(record_id)
        if normalized == KIND_DAILY:
            return await self._archive_drop_daily_submissions("s.id = ?", (target,), required=True)
        if normalized == KIND_DEEP:
            return await self._archive_drop_deep_submissions("d.id = ?", (target,), required=True)
        if normalized == KIND_FIX_RESHOOT:
            return await self._archive_drop_fix_reshoots("r.id = ?", (target,), required=True)
        if normalized == KIND_TEACHING:
            return await self._archive_drop_teaching_examples("e.id = ?", (target,), required=True)
        return await self._archive_drop_standard_versions("st.id = ?", (target,), required=True)

    @serialized_write
    async def purge_archive_records(
        self,
        actor: dict,
        kinds,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> dict:
        """按营业日区间清理记录与照片。

        不含整改单整单：那仍然是整改页的能力（ADR-0081），免得"清旧照片"顺手删掉还
        没做完的工作单。红黑榜事件与逾期通知也不回滚——它们只记"当时发生过"。
        """
        self._require_super(actor)
        # 查询时空列表等于"全部类型"，删除时不能沿用它：那会变成"清空这一段的一切"。
        requested = [
            str(value).strip() for value in (kinds or []) if str(value or "").strip()
        ]
        if not requested:
            raise HygieneWorkError("kind_required", "kind_required")
        selected = [k for k in normalize_kinds(requested) if k in ARCHIVE_DELETE_KINDS]
        if not selected:
            raise HygieneWorkError("kind_required", "kind_required")
        start, end = parse_archive_range(date_from, date_to)
        results = []
        for kind in selected:
            if kind == KIND_DAILY:
                results.append(
                    await self._archive_drop_daily_submissions(
                        "i.business_date BETWEEN ? AND ?", (start, end)
                    )
                )
            elif kind == KIND_DEEP:
                results.append(
                    await self._archive_drop_deep_submissions(
                        "i.business_date BETWEEN ? AND ?", (start, end)
                    )
                )
            elif kind == KIND_FIX_RESHOOT:
                results.append(
                    await self._archive_drop_fix_reshoots(
                        "r.captured_at >= ? AND r.captured_at < ?",
                        created_range(start, end),
                    )
                )
            elif kind == KIND_TEACHING:
                results.append(
                    await self._archive_drop_teaching_examples(
                        "e.created_at >= ? AND e.created_at < ?",
                        created_range(start, end),
                    )
                )
            else:
                results.append(
                    await self._archive_drop_standard_versions(
                        "st.created_at >= ? AND st.created_at < ?",
                        created_range(start, end),
                    )
                )
        summary = {
            "date_from": start,
            "date_to": end,
            "kinds": results,
            "records": sum(item["records"] for item in results),
            "photos": sum(item["photos"] for item in results),
            "files": sum(item["files"] for item in results),
        }
        logger.info(
            "hygiene archive purged %s..%s records=%s photos=%s",
            start,
            end,
            summary["records"],
            summary["photos"],
        )
        return summary

    async def _archive_rows(self, sql: str, params) -> list[dict]:
        cur = await self._conn.execute(sql, params)
        return [dict(row) for row in await cur.fetchall()]

    async def _archive_one(self, sql: str, params) -> Optional[dict]:
        cur = await self._conn.execute(sql, params)
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def _archive_derivatives(self, roots) -> list[str]:
        """原图对应的缩略图/preview 文件 id。它们不出现在业务表里，只能反查。"""
        unique = [str(item) for item in dict.fromkeys(roots) if item]
        found: list[str] = []
        for chunk in _chunked(unique, SQL_ID_CHUNK_SIZE):
            placeholders = ",".join("?" * len(chunk))
            rows = await self._archive_rows(
                f"""SELECT capture_id FROM hygiene_capture_variants
                    WHERE source_capture_id IN ({placeholders})""",
                tuple(chunk),
            )
            found.extend(str(row["capture_id"]) for row in rows)
        return found

    async def _archive_drop_variants(self, roots) -> None:
        unique = [str(item) for item in dict.fromkeys(roots) if item]
        for chunk in _chunked(unique, SQL_ID_CHUNK_SIZE):
            placeholders = ",".join("?" * len(chunk))
            await self._conn.execute(
                f"""DELETE FROM hygiene_capture_variants
                    WHERE source_capture_id IN ({placeholders})""",
                tuple(chunk),
            )

    async def _archive_delete_rows(self, table: str, ids) -> int:
        """按主键删行。表名只来自本模块常量，值全部参数化。"""
        unique = [int(item) for item in dict.fromkeys(ids)]
        for chunk in _chunked(unique, SQL_ID_CHUNK_SIZE):
            placeholders = ",".join("?" * len(chunk))
            await self._conn.execute(
                f"DELETE FROM {table} WHERE id IN ({placeholders})", tuple(chunk)
            )
        return len(unique)

    async def _archive_finish(self, roots, derivatives) -> None:
        await self._archive_drop_variants(roots)
        await self._conn.commit()
        # 文件删除放在 commit 之后，且它自己吞异常只告警。
        await self._delete_capture_files([*roots, *derivatives])

    async def _archive_drop_daily_submissions(
        self, where: str, params, required: bool = False
    ) -> dict:
        rows = await self._archive_rows(
            f"""SELECT s.id, s.capture_id, s.instance_id, i.pending_submission_id
                FROM hygiene_daily_submissions s
                JOIN hygiene_daily_instances i ON i.id = s.instance_id
                WHERE {where}""",
            params,
        )
        if not rows:
            if required:
                raise HygieneWorkError("record_not_found", "record_not_found")
            return _archive_result(KIND_DAILY, 0, 0)
        ids = [int(row["id"]) for row in rows]
        roots = [str(row["capture_id"]) for row in rows]
        derivatives = await self._archive_derivatives(roots)
        resets = [
            row for row in rows
            if int(row["pending_submission_id"] or 0) == int(row["id"])
        ]
        now = self._now_iso()
        try:
            await self._archive_delete_rows("hygiene_daily_submissions", ids)
            for row in resets:
                # 照片没了就不能还算完成或待验收：退回待拍等重拍。
                await self._conn.execute(
                    """UPDATE hygiene_daily_instances
                       SET status = ?, pending_submission_id = NULL, updated_at = ?
                       WHERE id = ? AND pending_submission_id = ?""",
                    (STATUS_TODO, now, int(row["instance_id"]), int(row["id"])),
                )
            await self._archive_finish(roots, derivatives)
        except Exception:
            await self._conn.rollback()
            raise
        logger.info(
            "hygiene archive deleted daily submissions=%s reset=%s photos=%s",
            len(ids),
            len(resets),
            len(roots) + len(derivatives),
        )
        return _archive_result(
            KIND_DAILY,
            len(ids),
            len(roots),
            status_reset=len(resets),
            files=len(roots) + len(derivatives),
        )

    async def _archive_drop_deep_submissions(
        self, where: str, params, required: bool = False
    ) -> dict:
        rows = await self._archive_rows(
            f"""SELECT d.id, d.before_capture_id, d.after_capture_id, d.instance_id,
                       i.pending_submission_id
                FROM hygiene_deep_clean_submissions d
                JOIN hygiene_deep_clean_instances i ON i.id = d.instance_id
                WHERE {where}""",
            params,
        )
        if not rows:
            if required:
                raise HygieneWorkError("record_not_found", "record_not_found")
            return _archive_result(KIND_DEEP, 0, 0)
        ids = [int(row["id"]) for row in rows]
        roots = [
            str(value)
            for row in rows
            for value in (row["before_capture_id"], row["after_capture_id"])
            if value
        ]
        derivatives = await self._archive_derivatives(roots)
        resets = [
            row for row in rows
            if int(row["pending_submission_id"] or 0) == int(row["id"])
        ]
        now = self._now_iso()
        try:
            await self._archive_delete_rows("hygiene_deep_clean_submissions", ids)
            for row in resets:
                await self._conn.execute(
                    """UPDATE hygiene_deep_clean_instances
                       SET status = ?, pending_submission_id = NULL, updated_at = ?
                       WHERE id = ? AND pending_submission_id = ?""",
                    (STATUS_TODO, now, int(row["instance_id"]), int(row["id"])),
                )
            await self._archive_finish(roots, derivatives)
        except Exception:
            await self._conn.rollback()
            raise
        logger.info(
            "hygiene archive deleted deep submissions=%s reset=%s photos=%s",
            len(ids),
            len(resets),
            len(roots) + len(derivatives),
        )
        return _archive_result(
            KIND_DEEP,
            len(ids),
            len(roots),
            status_reset=len(resets),
            files=len(roots) + len(derivatives),
        )

    async def _archive_drop_fix_reshoots(
        self, where: str, params, required: bool = False
    ) -> dict:
        rows = await self._archive_rows(
            f"""SELECT r.id, r.capture_id, r.ticket_id, t.pending_reshoot_id
                FROM hygiene_fix_reshoots r
                JOIN hygiene_fix_tickets t ON t.id = r.ticket_id
                WHERE {where}""",
            params,
        )
        if not rows:
            if required:
                raise HygieneWorkError("record_not_found", "record_not_found")
            return _archive_result(KIND_FIX_RESHOOT, 0, 0)
        ids = [int(row["id"]) for row in rows]
        roots = [str(row["capture_id"]) for row in rows]
        derivatives = await self._archive_derivatives(roots)
        resets = [
            row for row in rows
            if int(row["pending_reshoot_id"] or 0) == int(row["id"])
        ]
        now = self._now_iso()
        try:
            await self._archive_delete_rows("hygiene_fix_reshoots", ids)
            for row in resets:
                await self._conn.execute(
                    """UPDATE hygiene_fix_tickets
                       SET status = ?, pending_reshoot_id = NULL, updated_at = ?
                       WHERE id = ? AND pending_reshoot_id = ?""",
                    (STATUS_FIX_TODO, now, int(row["ticket_id"]), int(row["id"])),
                )
            await self._archive_finish(roots, derivatives)
        except Exception:
            await self._conn.rollback()
            raise
        logger.info(
            "hygiene archive deleted fix reshoots=%s reset=%s photos=%s",
            len(ids),
            len(resets),
            len(roots) + len(derivatives),
        )
        return _archive_result(
            KIND_FIX_RESHOOT,
            len(ids),
            len(roots),
            status_reset=len(resets),
            files=len(roots) + len(derivatives),
        )

    async def _archive_drop_teaching_examples(
        self, where: str, params, required: bool = False
    ) -> dict:
        rows = await self._archive_rows(
            f"""SELECT e.id, e.left_capture_id, e.right_capture_id
                FROM hygiene_teaching_examples e
                WHERE {where}""",
            params,
        )
        if not rows:
            if required:
                raise HygieneWorkError("record_not_found", "record_not_found")
            return _archive_result(KIND_TEACHING, 0, 0)
        ids = [int(row["id"]) for row in rows]
        roots = [
            str(value)
            for row in rows
            for value in (row["left_capture_id"], row["right_capture_id"])
            if value
        ]
        derivatives = await self._archive_derivatives(roots)
        try:
            await self._archive_delete_rows("hygiene_teaching_examples", ids)
            await self._archive_finish(roots, derivatives)
        except Exception:
            await self._conn.rollback()
            raise
        logger.info(
            "hygiene archive deleted teaching examples=%s photos=%s",
            len(ids),
            len(roots) + len(derivatives),
        )
        return _archive_result(
            KIND_TEACHING, len(ids), len(roots), files=len(roots) + len(derivatives)
        )

    async def _standard_referenced(self, standard_id: int) -> bool:
        """这张标准图有没有被任何一次提交冻结成对照依据。

        有就不能删：一是外键本来就挡着（SQLite 与 PG 都建了
        `hygiene_daily_submissions.frozen_standard_id`），二是历史回看要靠它还原
        「提交当时的标准图」——删了那次对照就永远缺一半。
        """
        row = await self._archive_one(
            """SELECT COUNT(*) AS n FROM hygiene_daily_submissions
               WHERE frozen_standard_id = ?""",
            (int(standard_id),),
        )
        return bool(int((row or {}).get("n") or 0))

    async def _archive_drop_standard_versions(
        self, where: str, params, required: bool = False
    ) -> dict:
        """删标准图的历史版本。

        当前版本不能删：日常检查项必须有当前标准图（ADR-0055）。被任何一次提交冻结成
        对照的版本也不能删（ADR-0069）——那既是外键约束，也是历史回看的依据。所以真正
        删得掉的只有"换过图、但还没被谁拿来对照过"的那几版。
        """
        rows = await self._archive_rows(
            f"""SELECT st.id, st.capture_id, st.item_id, it.current_standard_id
                FROM hygiene_standards st
                JOIN hygiene_daily_items it ON it.id = st.item_id
                WHERE {where}""",
            params,
        )
        if not rows:
            if required:
                raise HygieneWorkError("record_not_found", "record_not_found")
            return _archive_result(KIND_STANDARD, 0, 0)
        droppable = []
        blocked = 0
        for row in rows:
            if int(row["current_standard_id"] or 0) == int(row["id"]):
                blocked += 1
                continue
            if await self._standard_referenced(int(row["id"])):
                blocked += 1
                continue
            droppable.append(row)
        if required and not droppable:
            raise HygieneWorkError("standard_in_use", "standard_in_use")
        if not droppable:
            return _archive_result(KIND_STANDARD, 0, 0)
        ids = [int(row["id"]) for row in droppable]
        roots = [str(row["capture_id"]) for row in droppable]
        derivatives = await self._archive_derivatives(roots)
        try:
            await self._archive_delete_rows("hygiene_standards", ids)
            await self._archive_finish(roots, derivatives)
        except Exception:
            await self._conn.rollback()
            raise
        logger.info(
            "hygiene archive deleted standard versions=%s blocked=%s photos=%s",
            len(ids),
            blocked,
            len(roots) + len(derivatives),
        )
        return _archive_result(
            KIND_STANDARD,
            len(ids),
            len(roots),
            blocked=blocked,
            files=len(roots) + len(derivatives),
        )
