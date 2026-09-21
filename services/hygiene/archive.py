#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生数据与照片的管理端视图：归一化查询、存储概况、台账打包。

**只读**：这里不写业务表、不改照片文件。删除是写路径，归 `HygieneWork`
（见 `delete_archive_record` / `purge_archive_records`）。分成两块是因为照片引用散在
六张业务表里，查询要 UNION 归一，而删除要回退待办状态——两者的关注点与风险不同。

六类照片各自的"营业日"来源不同：日常/专项挂在实例的 `business_date` 上，整改、教材、
标准图版本只有创建时间，所以后三类按 06:00 切日自行折算，与 ADR-0053 一致。
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from services.hygiene.accounts import hygiene_business_date
from database import CHINA_TZ
from services.hygiene.standards_export import safe_component

logger = logging.getLogger(__name__)

KIND_DAILY = "daily"
KIND_DEEP = "deep"
KIND_FIX = "fix"
KIND_FIX_RESHOOT = "fix_reshoot"
KIND_TEACHING = "teaching"
KIND_STANDARD = "standard"

KIND_LABELS = {
    KIND_DAILY: "日常实拍",
    KIND_DEEP: "专项前后",
    KIND_FIX: "整改原图",
    KIND_FIX_RESHOOT: "整改回拍",
    KIND_TEACHING: "卫生教材",
    KIND_STANDARD: "标准图版本",
}
ARCHIVE_KINDS = tuple(KIND_LABELS)

# 管理端能删的类型。整改单（KIND_FIX）不在里面：整单删除仍是整改页的能力
# （ADR-0081），免得「清旧照片」顺手删掉还没做完的工作单。
ARCHIVE_DELETE_KINDS = (
    KIND_DAILY,
    KIND_DEEP,
    KIND_FIX_RESHOOT,
    KIND_TEACHING,
    KIND_STANDARD,
)

# 默认与上限：不设区间时按最近 30 天查，避免"打开页面就全表扫"。
DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 366
# 单类单次最多取这么多行。区间拉到一年时，日常提交可能上万行，超出部分不静默丢，
# 而是在响应里挂 truncated 让前端提示收窄区间。
MAX_ROWS_PER_KIND = 5000
DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 100
# 变体归属：缩略图与 preview 的磁盘占用算到它的原图所属类型上。
VARIANT_ROLES = ("thumb", "preview")
# Keep every `IN (...)` well under SQLITE_MAX_VARIABLE_NUMBER（与 work.py 同口径）。
SQL_ID_CHUNK_SIZE = 500


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


class ArchiveQueryError(Exception):
    """查询参数不合法。API 层翻成 400。"""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def parse_archive_range(
    date_from: Optional[str],
    date_to: Optional[str],
    now: Optional[datetime] = None,
) -> tuple[str, str]:
    """把可选的营业日区间规范成 (from, to)，并挡住过宽与非法输入。"""
    today = hygiene_business_date(now or datetime.now(CHINA_TZ))
    end = _clean_date(date_to) or today
    start = _clean_date(date_from) or _shift_date(end, -(DEFAULT_RANGE_DAYS - 1))
    if start > end:
        raise ArchiveQueryError("bad_range", "开始日期晚于结束日期")
    span = (_date(start) - _date(end)).days
    if abs(span) >= MAX_RANGE_DAYS:
        raise ArchiveQueryError(
            "range_too_wide", f"一次最多查 {MAX_RANGE_DAYS} 天，请收窄区间"
        )
    return start, end


def _clean_date(raw: Optional[str]) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return _date(text).isoformat()
    except ValueError as exc:
        raise ArchiveQueryError("bad_date", "日期格式应为 YYYY-MM-DD") from exc


def _date(text: str):
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


def _shift_date(text: str, days: int) -> str:
    return (_date(text) + timedelta(days=days)).isoformat()


def created_range(start: str, end: str) -> tuple[str, str]:
    """把营业日区间翻成 created_at 的 ISO 半开区间（06:00 切日）。

    边界必须带 ``+08:00``：created_at 是 TEXT，比较是字典序，不带偏移的上界会把
    ``06:00:00+08:00`` 那一秒算进前一天。
    """
    return (
        f"{start}T06:00:00+08:00",
        f"{_shift_date(end, 1)}T06:00:00+08:00",
    )


def normalize_kinds(raw: Optional[Iterable[str]]) -> tuple[str, ...]:
    if raw is None:
        return ARCHIVE_KINDS
    items = [str(value).strip() for value in raw if str(value or "").strip()]
    if not items:
        return ARCHIVE_KINDS
    unknown = [value for value in items if value not in KIND_LABELS]
    if unknown:
        raise ArchiveQueryError("bad_kind", f"未知的类型：{unknown[0]}")
    return tuple(dict.fromkeys(items))


class HygieneDataArchive:
    """管理端的数据与照片视图。只读，不持有写锁。"""

    def __init__(self, conn_or_db, captures):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("HygieneDataArchive requires an open database connection")
        if captures is None:
            raise RuntimeError("HygieneDataArchive requires a capture store")
        self._conn = conn
        self._captures = captures

    async def list_records(
        self,
        *,
        kinds: Optional[Iterable[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        zone_id: Optional[int] = None,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> dict:
        selected = normalize_kinds(kinds)
        start, end = parse_archive_range(date_from, date_to)
        rows, truncated = await self._collect_rows(selected, start, end, zone_id)
        await self._attach_submitters(rows)
        rows.sort(key=_sort_key, reverse=True)
        total = len(rows)
        size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
        index = max(1, int(page or 1))
        window = rows[(index - 1) * size : index * size]
        return {
            "items": [await self._present(row) for row in window],
            "total": total,
            "page": index,
            "page_size": size,
            "date_from": start,
            "date_to": end,
            "kinds": list(selected),
            "truncated": truncated,
        }

    async def iter_records(
        self,
        *,
        kinds: Optional[Iterable[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        zone_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """导出用：同一套筛选，但不分页。"""
        selected = normalize_kinds(kinds)
        start, end = parse_archive_range(date_from, date_to)
        rows, _ = await self._collect_rows(selected, start, end, zone_id)
        await self._attach_submitters(rows)
        rows.sort(key=_sort_key, reverse=True)
        if limit is not None:
            rows = rows[:limit]
        return [await self._present(row) for row in rows]

    async def storage_summary(self) -> dict:
        """按类型统计照片张数与磁盘占用。

        占用取自文件系统而不是表里的 byte_size：只有标准图那一张表记了字节数，
        其余类型没有；而"磁盘到底被占了多大"本来就该问磁盘。
        """
        owners = await self._capture_owners()
        variants = await self._variant_sources()
        on_disk = await self._disk_sizes()

        buckets = {
            kind: {"kind": kind, "label": label, "count": 0, "bytes": 0, "files": 0}
            for kind, label in KIND_LABELS.items()
        }
        missing = 0
        seen_files = set()
        for capture_id, kind in owners.items():
            bucket = buckets.get(kind)
            if bucket is None:
                continue
            bucket["count"] += 1
            for member in (capture_id, *variants.get(capture_id, ())):
                seen_files.add(member)
                size = on_disk.get(member)
                if size is None:
                    if member == capture_id:
                        missing += 1
                    continue
                bucket["bytes"] += size
                bucket["files"] += 1

        orphan_ids = [cid for cid in on_disk if cid not in seen_files]
        orphan_bytes = sum(on_disk[cid] for cid in orphan_ids)
        ordered = [buckets[kind] for kind in ARCHIVE_KINDS]
        return {
            "kinds": ordered,
            "total": {
                "count": sum(bucket["count"] for bucket in ordered),
                "bytes": sum(bucket["bytes"] for bucket in ordered),
                "files": sum(bucket["files"] for bucket in ordered),
            },
            "missing_files": missing,
            "orphan_files": {"count": len(orphan_ids), "bytes": orphan_bytes},
            "generated_at": datetime.now(CHINA_TZ).isoformat(),
        }

    async def photo_path(self, capture_id: str, variant: str = "original"):
        """返回照片文件路径与元数据，供管理端取图与打包。

        变体缺失时回落原图，与 `HygieneWork.capture_view` 的口径一致。
        """
        cleaned = str(capture_id or "").strip()
        if not cleaned:
            raise ArchiveQueryError("bad_capture", "缺少照片标识")
        meta = await self._capture_meta(cleaned)
        wanted = str(variant or "original").strip().lower()
        if wanted in VARIANT_ROLES:
            row = await self._variant_meta(cleaned, wanted)
            if row is not None:
                path = await self._captures.path_async(row["capture_id"])
                if path is not None and Path(path).is_file():
                    return {
                        "capture_id": row["capture_id"],
                        "content_type": row["content_type"],
                        "variant": wanted,
                        "path": Path(path),
                    }
        if meta is None:
            raise ArchiveQueryError("capture_unknown", "这张照片不属于卫生记录")
        path = await self._captures.path_async(cleaned)
        if path is None or not Path(path).is_file():
            raise ArchiveQueryError("capture_missing", "照片文件已不在磁盘上")
        return {
            "capture_id": cleaned,
            "content_type": meta["content_type"],
            "variant": "original",
            "path": Path(path),
        }

    # ---- 内部：查询 ----

    async def _collect_rows(
        self, kinds: Iterable[str], start: str, end: str, zone_id: Optional[int]
    ) -> tuple[list[dict], bool]:
        rows: list[dict] = []
        truncated = False
        for kind in kinds:
            loader = _LOADERS[kind]
            found = await loader(self, start, end, zone_id)
            if len(found) > MAX_ROWS_PER_KIND:
                truncated = True
                found = found[:MAX_ROWS_PER_KIND]
            rows.extend(found)
        return rows, truncated

    async def _fetch(self, sql: str, params: list) -> list[dict]:
        cur = await self._conn.execute(sql, params)
        return [dict(row) for row in await cur.fetchall()]

    async def _attach_submitters(self, rows: list[dict]) -> None:
        """补上提交人姓名。

        水印优先显示姓名（CONTEXT「员工」），台账里也得是人名——六张业务表各自只存
        id 与手机号，姓名只在花名册里，所以批量查一次再回填，避免逐条查。
        """
        ids = sorted(
            {int(row["submitter_id"]) for row in rows if row.get("submitter_id")}
        )
        names: dict[int, tuple[str, str]] = {}
        for chunk in _chunks(ids, SQL_ID_CHUNK_SIZE):
            placeholders = ",".join("?" * len(chunk))
            found = await self._fetch(
                f"SELECT id, name, phone FROM hygiene_employees WHERE id IN ({placeholders})",
                list(chunk),
            )
            for row in found:
                names[int(row["id"])] = (row.get("name") or "", row.get("phone") or "")
        for row in rows:
            entry = names.get(int(row["submitter_id"] or 0))
            if entry is not None:
                row["submitter_name"], row["submitter_phone"] = entry

    async def _daily_rows(self, start, end, zone_id) -> list[dict]:
        sql = """SELECT s.id AS record_id, inst.business_date AS business_date,
                        s.captured_at AS occurred_at, z.id AS zone_id,
                        COALESCE(NULLIF(s.zone_name, ''), z.name) AS zone_name,
                        it.name AS title, inst.shift AS subtitle, inst.status AS status,
                        s.submitter_id AS submitter_id, s.submitter_phone AS submitter_phone,
                        s.capture_id AS photo_a, s.content_type AS type_a,
                        NULL AS photo_b, NULL AS type_b
                 FROM hygiene_daily_submissions s
                 JOIN hygiene_daily_instances inst ON inst.id = s.instance_id
                 JOIN hygiene_daily_items it ON it.id = inst.item_id
                 JOIN hygiene_zones z ON z.id = it.zone_id
                 WHERE inst.business_date BETWEEN ? AND ?"""
        params: list = [start, end]
        if zone_id is not None:
            sql += " AND z.id = ?"
            params.append(int(zone_id))
        return _tag(await self._fetch(sql, params), KIND_DAILY, roles=("实拍",))

    async def _deep_rows(self, start, end, zone_id) -> list[dict]:
        # 专项不挂卫生责任区：选了责任区就没有专项记录，这是领域事实不是疏漏。
        if zone_id is not None:
            return []
        sql = """SELECT d.id AS record_id, inst.business_date AS business_date,
                        d.after_captured_at AS occurred_at, NULL AS zone_id,
                        '' AS zone_name, it.name AS title, '' AS subtitle,
                        inst.status AS status, d.submitter_id AS submitter_id,
                        d.submitter_phone AS submitter_phone,
                        d.before_capture_id AS photo_a, d.before_content_type AS type_a,
                        d.after_capture_id AS photo_b, d.after_content_type AS type_b
                 FROM hygiene_deep_clean_submissions d
                 JOIN hygiene_deep_clean_instances inst ON inst.id = d.instance_id
                 JOIN hygiene_deep_clean_items it ON it.id = inst.item_id
                 WHERE inst.business_date BETWEEN ? AND ?"""
        return _tag(
            await self._fetch(sql, [start, end]), KIND_DEEP, roles=("清理前", "清理后")
        )

    async def _fix_rows(self, start, end, zone_id) -> list[dict]:
        lo, hi = created_range(start, end)
        sql = """SELECT t.id AS record_id, t.created_at AS occurred_at,
                        z.id AS zone_id, z.name AS zone_name, t.body_text AS title,
                        t.ticket_type AS subtitle, t.status AS status,
                        t.opener_id AS submitter_id, t.opener_phone AS submitter_phone,
                        t.capture_id AS photo_a, t.content_type AS type_a,
                        NULL AS photo_b, NULL AS type_b
                 FROM hygiene_fix_tickets t
                 JOIN hygiene_zones z ON z.id = t.zone_id
                 WHERE t.created_at >= ? AND t.created_at < ?"""
        params: list = [lo, hi]
        if zone_id is not None:
            sql += " AND z.id = ?"
            params.append(int(zone_id))
        return _tag(await self._fetch(sql, params), KIND_FIX, roles=("开单原图",))

    async def _fix_reshoot_rows(self, start, end, zone_id) -> list[dict]:
        lo, hi = created_range(start, end)
        sql = """SELECT r.id AS record_id, r.captured_at AS occurred_at,
                        z.id AS zone_id,
                        COALESCE(NULLIF(r.zone_name, ''), z.name) AS zone_name,
                        t.body_text AS title, t.ticket_type AS subtitle,
                        t.status AS status, r.photographer_id AS submitter_id,
                        r.photographer_phone AS submitter_phone,
                        r.capture_id AS photo_a, r.content_type AS type_a,
                        NULL AS photo_b, NULL AS type_b
                 FROM hygiene_fix_reshoots r
                 JOIN hygiene_fix_tickets t ON t.id = r.ticket_id
                 JOIN hygiene_zones z ON z.id = t.zone_id
                 WHERE r.captured_at >= ? AND r.captured_at < ?"""
        params: list = [lo, hi]
        if zone_id is not None:
            sql += " AND z.id = ?"
            params.append(int(zone_id))
        return _tag(await self._fetch(sql, params), KIND_FIX_RESHOOT, roles=("回拍",))

    async def _teaching_rows(self, start, end, zone_id) -> list[dict]:
        if zone_id is not None:
            return []
        lo, hi = created_range(start, end)
        sql = """SELECT e.id AS record_id, e.created_at AS occurred_at,
                        NULL AS zone_id, '' AS zone_name, e.title AS title,
                        e.kind AS subtitle, '教材' AS status, NULL AS submitter_id,
                        '' AS submitter_phone, e.left_capture_id AS photo_a,
                        e.left_content_type AS type_a, e.right_capture_id AS photo_b,
                        e.right_content_type AS type_b
                 FROM hygiene_teaching_examples e
                 WHERE e.created_at >= ? AND e.created_at < ?"""
        return _tag(
            await self._fetch(sql, [lo, hi]),
            KIND_TEACHING,
            roles=("左图", "右图"),
            no_submitter=True,
        )

    async def _standard_rows(self, start, end, zone_id) -> list[dict]:
        lo, hi = created_range(start, end)
        sql = """SELECT st.id AS record_id, st.created_at AS occurred_at,
                        z.id AS zone_id, z.name AS zone_name, it.name AS title,
                        '' AS subtitle,
                        CASE WHEN it.current_standard_id = st.id
                             THEN '当前标准图' ELSE '历史标准图' END AS status,
                        NULL AS submitter_id, '' AS submitter_phone,
                        st.capture_id AS photo_a, st.content_type AS type_a,
                        NULL AS photo_b, NULL AS type_b
                 FROM hygiene_standards st
                 JOIN hygiene_daily_items it ON it.id = st.item_id
                 JOIN hygiene_zones z ON z.id = it.zone_id
                 WHERE st.created_at >= ? AND st.created_at < ?"""
        params: list = [lo, hi]
        if zone_id is not None:
            sql += " AND z.id = ?"
            params.append(int(zone_id))
        return _tag(
            await self._fetch(sql, params),
            KIND_STANDARD,
            roles=("标准图",),
            no_submitter=True,
        )

    # ---- 内部：元数据 ----

    async def _present(self, row: dict) -> dict:
        """把库里的行补成给前端的记录形状（含提交人姓名与照片列表）。"""
        kind = row["kind"]
        photos = []
        for role_key, id_key, type_key in (
            ("role_a", "photo_a", "type_a"),
            ("role_b", "photo_b", "type_b"),
        ):
            capture_id = row.get(id_key)
            if not capture_id:
                continue
            photos.append(
                {
                    "role": row.get(role_key) or "照片",
                    "capture_id": str(capture_id),
                    "content_type": row.get(type_key) or "image/jpeg",
                }
            )
        occurred = str(row.get("occurred_at") or "")
        business_date = row.get("business_date")
        if not business_date:
            business_date = _business_date_of(occurred)
        return {
            "kind": kind,
            "kind_label": KIND_LABELS.get(kind, kind),
            "record_id": int(row["record_id"]),
            "business_date": business_date,
            "occurred_at": occurred,
            "zone_id": row.get("zone_id"),
            "zone_name": row.get("zone_name") or "",
            "title": row.get("title") or "",
            "subtitle": row.get("subtitle") or "",
            "status": row.get("status") or "",
            "submitter_name": row.get("submitter_name") or "",
            "submitter_phone": row.get("submitter_phone") or "",
            "photos": photos,
            "current": bool(row.get("current")),
        }

    async def _capture_owners(self) -> dict[str, str]:
        """capture_id → 类型。同一张图被多处引用时按先出现的类型归一。"""
        owners: dict[str, str] = {}
        pairs = (
            (KIND_DAILY, "SELECT capture_id FROM hygiene_daily_submissions"),
            (KIND_DEEP, "SELECT before_capture_id AS capture_id FROM hygiene_deep_clean_submissions"),
            (KIND_DEEP, "SELECT after_capture_id AS capture_id FROM hygiene_deep_clean_submissions"),
            (KIND_FIX, "SELECT capture_id FROM hygiene_fix_tickets"),
            (KIND_FIX_RESHOOT, "SELECT capture_id FROM hygiene_fix_reshoots"),
            (KIND_TEACHING, "SELECT left_capture_id AS capture_id FROM hygiene_teaching_examples"),
            (KIND_TEACHING, "SELECT right_capture_id AS capture_id FROM hygiene_teaching_examples"),
            (KIND_STANDARD, "SELECT capture_id FROM hygiene_standards"),
        )
        for kind, sql in pairs:
            for row in await self._fetch(sql, []):
                capture_id = str(row.get("capture_id") or "")
                if capture_id and capture_id not in owners:
                    owners[capture_id] = kind
        return owners

    async def _variant_sources(self) -> dict[str, tuple[str, ...]]:
        rows = await self._fetch(
            "SELECT source_capture_id, capture_id FROM hygiene_capture_variants", []
        )
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(str(row["source_capture_id"]), []).append(
                str(row["capture_id"])
            )
        return {key: tuple(value) for key, value in grouped.items()}

    async def _disk_sizes(self) -> dict[str, int]:
        sizes: dict[str, int] = {}
        for capture_id in await self._captures.list_ids_async():
            path = await self._captures.path_async(capture_id)
            if path is None:
                continue
            try:
                sizes[str(capture_id)] = Path(path).stat().st_size
            except OSError:
                continue
        return sizes

    async def _capture_meta(self, capture_id: str) -> Optional[dict]:
        sql = """SELECT capture_id, content_type FROM hygiene_standards WHERE capture_id = ?
                 UNION ALL
                 SELECT capture_id, content_type FROM hygiene_daily_submissions WHERE capture_id = ?
                 UNION ALL
                 SELECT before_capture_id, before_content_type FROM hygiene_deep_clean_submissions
                   WHERE before_capture_id = ?
                 UNION ALL
                 SELECT after_capture_id, after_content_type FROM hygiene_deep_clean_submissions
                   WHERE after_capture_id = ?
                 UNION ALL
                 SELECT capture_id, content_type FROM hygiene_fix_tickets WHERE capture_id = ?
                 UNION ALL
                 SELECT capture_id, content_type FROM hygiene_fix_reshoots WHERE capture_id = ?
                 UNION ALL
                 SELECT left_capture_id, left_content_type FROM hygiene_teaching_examples
                   WHERE left_capture_id = ?
                 UNION ALL
                 SELECT right_capture_id, right_content_type FROM hygiene_teaching_examples
                   WHERE right_capture_id = ?
                 LIMIT 1"""
        rows = await self._fetch(sql, [capture_id] * 8)
        return rows[0] if rows else None

    async def _variant_meta(self, capture_id: str, variant: str) -> Optional[dict]:
        rows = await self._fetch(
            """SELECT capture_id, content_type FROM hygiene_capture_variants
               WHERE source_capture_id = ? AND variant = ?""",
            [capture_id, variant],
        )
        return rows[0] if rows else None


def _tag(rows: list[dict], kind: str, roles=("照片",), no_submitter: bool = False) -> list[dict]:
    for row in rows:
        row["kind"] = kind
        row["role_a"] = roles[0] if roles else "照片"
        row["role_b"] = roles[1] if len(roles) > 1 else None
        row["current"] = row.get("status") == "当前标准图"
        if no_submitter:
            row["submitter_id"] = None
    return rows


def _business_date_of(occurred_at: str) -> str:
    text = str(occurred_at or "").strip()
    if not text:
        return ""
    try:
        return hygiene_business_date(datetime.fromisoformat(text))
    except ValueError:
        return text[:10]


def _sort_key(row: dict):
    return (str(row.get("occurred_at") or ""), int(row.get("record_id") or 0))


_LOADERS = {
    KIND_DAILY: HygieneDataArchive._daily_rows,
    KIND_DEEP: HygieneDataArchive._deep_rows,
    KIND_FIX: HygieneDataArchive._fix_rows,
    KIND_FIX_RESHOOT: HygieneDataArchive._fix_reshoot_rows,
    KIND_TEACHING: HygieneDataArchive._teaching_rows,
    KIND_STANDARD: HygieneDataArchive._standard_rows,
}


CSV_HEADER = ("类型", "营业日", "时间", "责任区", "项目/说明", "提交人", "状态", "照片文件")


def ledger_csv(records: Iterable[dict], photo_names: dict[tuple, str]) -> bytes:
    """记录台账。一行一张照片，便于按文件核对；没有照片的记录也留一行。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for record in records:
        base = (
            record["kind_label"],
            record["business_date"],
            record["occurred_at"],
            record["zone_name"],
            record["title"],
            record["submitter_name"] or record["submitter_phone"],
            record["status"],
        )
        if not record["photos"]:
            writer.writerow([*base, ""])
            continue
        for photo in record["photos"]:
            name = photo_names.get((record["kind"], record["record_id"], photo["capture_id"]))
            writer.writerow([*base, name or ""])
    # BOM 开头：Excel 打开中文 CSV 不乱码，是给人看的台账不是喂程序的。
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def photo_folder(kind: str) -> str:
    return f"照片/{safe_component(KIND_LABELS.get(kind, kind), '照片')}"


def photo_filename(record: dict, photo: dict, index: int) -> str:
    parts = [
        str(record.get("business_date") or "").replace("-", ""),
        str(record.get("zone_name") or ""),
        str(record.get("title") or ""),
        str(photo.get("role") or ""),
        str(record.get("submitter_name") or record.get("submitter_phone") or ""),
    ]
    stem = "-".join(part for part in (safe_component(p, "") for p in parts) if part)
    # capture_id 是无后缀的 uuid，扩展名只能看 content_type。
    suffix = _SUFFIXES.get(str(photo.get("content_type") or ""), ".jpg")
    name = f"{stem or 'photo'}{suffix}"
    return f"{index:02d}-{name}" if index > 1 else name


_SUFFIXES = {
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/jpeg": ".jpg",
}


def write_ledger_zip(entries: list[dict], target: Path, report=None) -> tuple[int, int]:
    """把 (记录, 照片路径) 写成一个 zip。返回 (写入张数, 跳过张数)。

    照片本来就是压过的 JPEG，deflate 收益接近 0 却要烧 CPU，直接 STORED。
    """
    written = 0
    skipped = 0
    total = sum(len(entry["photos"]) for entry in entries)
    names: dict[tuple, str] = {}
    records = [entry["record"] for entry in entries]
    for entry in entries:
        record = entry["record"]
        for index, photo in enumerate(entry["photos"], start=1):
            names[(record["kind"], record["record_id"], photo["capture_id"])] = (
                f"{photo_folder(record['kind'])}/{photo_filename(record, photo, index)}"
            )
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("记录.csv", ledger_csv(records, names))
        used: set[str] = set()
        for entry in entries:
            record = entry["record"]
            for index, photo in enumerate(entry["photos"], start=1):
                name = names[(record["kind"], record["record_id"], photo["capture_id"])]
                # 同名不同图（同一项同一分钟拍两张）不能互相覆盖。
                if name in used:
                    stem, dot, suffix = name.rpartition(".")
                    name = f"{stem}-{photo['capture_id'][:6]}{dot}{suffix}"
                used.add(name)
                source = photo.get("path")
                if source is None or not Path(source).is_file():
                    skipped += 1
                else:
                    archive.write(source, name)
                    written += 1
                if report is not None:
                    report(written + skipped, total)
    if skipped:
        # 文件缺失是"照片已经被清理过"，不是故障；但要留痕，免得台账少了几张没人知道。
        logger.warning("导出卫生数据：%s 张照片文件缺失，已跳过", skipped)
    return written, skipped
