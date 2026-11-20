#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""照片台账：卫生域的每一张照片，只有这一张表知道「谁持有它、它是什么」。

台账之下是十处来源（`SOURCES`）：日常、专项前后、整改原图、整改回拍、教材左右、
标准图、仪容实拍与仪容标准图。三个问题共用这一张表：

- `roots()` —— 谁被业务表持有（清扫不能删、变体回填要从这里出发）；
- `owners()` —— 每张图属于哪一类（归档页按类型分桶）；
- `meta()` —— 这张图的 Content-Type / 字节数 / 摘要（下发、导出、ETag 都要）。

变体（缩略图、预览图）也在这里：`derivatives()` / `variants()` / `variant_index()`。
写变体仍在 `HygieneWork`（那是写路径），台账只读。

以前这四件事各写各的清单：清扫认 10 处、元数据只认 6 处，于是教材与仪容的 PNG
被当成 JPEG 下发、导出成 .jpg、也拿不到 ETag。**新增一张带 capture_id 的表时，
必须同时改 `SOURCES`** —— `tests/test_hygiene_captures_ledger.py` 会拿
`information_schema` 对拍，漏登记就红。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from services.hygiene.kinds import (
    KIND_ATTIRE,
    KIND_DAILY,
    KIND_DEEP,
    KIND_FIX,
    KIND_FIX_RESHOOT,
    KIND_LABELS,
    KIND_STANDARD,
    KIND_TEACHING,
)


@dataclass(frozen=True)
class Source:
    """一处持有照片的列。`size_column` / `sha_column` 目前只有 `hygiene_standards` 记了值。"""

    table: str
    id_column: str
    type_column: str
    kind: str
    size_column: Optional[str] = None
    sha_column: Optional[str] = None


# 顺序即优先级：同一张图被两处引用时，`owners()` 与 `meta()` 都取先出现的那处
# （标准图排在前面 —— 冻结版本是那张图的本体，实拍只是引用了它）。
SOURCES = (
    Source("hygiene_standards", "capture_id", "content_type", KIND_STANDARD,
           "byte_size", "content_sha256"),
    Source("hygiene_daily_submissions", "capture_id", "content_type", KIND_DAILY),
    Source("hygiene_deep_clean_submissions", "before_capture_id",
           "before_content_type", KIND_DEEP),
    Source("hygiene_deep_clean_submissions", "after_capture_id",
           "after_content_type", KIND_DEEP),
    Source("hygiene_fix_tickets", "capture_id", "content_type", KIND_FIX),
    Source("hygiene_fix_reshoots", "capture_id", "content_type", KIND_FIX_RESHOOT),
    Source("hygiene_teaching_examples", "left_capture_id", "left_content_type",
           KIND_TEACHING),
    Source("hygiene_teaching_examples", "right_capture_id", "right_content_type",
           KIND_TEACHING),
    Source("hygiene_attire_shots", "capture_id", "content_type", KIND_ATTIRE),
    Source("hygiene_attire_standard", "capture_id", "content_type", KIND_ATTIRE),
)

# 带 `capture_id` 字样、但**刻意不登记**的列。守卫要求这张清单与
# `information_schema` 里的实际列严丝合缝，所以每一条都写清理由：
#
# - 变体表的 `source_capture_id` 指回根图（根本身已经登记），`capture_id` 是派生文件；
# - 企微分享表的两个列引用的是业务表已经持有的图（含专项「前」那张），不新增可达性 ——
#   登记它会让「分享过、但业务行已经删了」的图多活 24 小时，而那时分享也已经没有内容可发。
ALLOWED_UNREGISTERED = frozenset(
    {
        ("hygiene_capture_variants", "source_capture_id"),
        ("hygiene_capture_variants", "capture_id"),
        ("hygiene_wecom_shares", "capture_id"),
        ("hygiene_wecom_shares", "extra_capture_id"),
    }
)

_FILE_META_CACHE: dict[tuple, tuple[int, str]] = {}
_FILE_META_CACHE_LIMIT = 512


def claimed_columns() -> set[tuple[str, str]]:
    """台账声明自己认领的 (表, 列)。守卫拿它与库里的实际列对拍。"""
    return {(source.table, source.id_column) for source in SOURCES}


_ROOTS_SQL = " UNION ".join(
    f"SELECT {source.id_column} AS capture_id FROM {source.table}"
    for source in SOURCES
)

_OWNERS_SQL = " UNION ALL ".join(
    f"SELECT {source.id_column} AS capture_id, '{source.kind}' AS kind "
    f"FROM {source.table}"
    for source in SOURCES
)

_META_SQL = " UNION ALL ".join(
    "SELECT {id} AS capture_id, {type} AS content_type, {size} AS byte_size, "
    "{sha} AS sha256 FROM {table} WHERE {id} = ?".format(
        id=source.id_column,
        type=source.type_column,
        size=source.size_column or "NULL",
        sha=source.sha_column or "NULL",
        table=source.table,
    )
    for source in SOURCES
) + " LIMIT 1"

_VARIANTS_SQL = """SELECT variant, capture_id, content_type, byte_size, content_sha256
                      FROM hygiene_capture_variants WHERE source_capture_id = ?"""


async def _rows(conn, sql: str, params=()) -> list[dict]:
    cur = await conn.execute(sql, params)
    return [dict(row) for row in await cur.fetchall()]


async def roots(conn) -> set[str]:
    """业务表直接持有的照片（变体的根）。清扫与变体回填都以它为准。"""
    return {
        str(row["capture_id"])
        for row in await _rows(conn, _ROOTS_SQL)
        if row.get("capture_id")
    }


async def derivatives(conn) -> set[str]:
    """变体自己生成的图。它们永远不该再作为变体的根（否则缩略图会套缩略图）。"""
    rows = await _rows(conn, "SELECT capture_id FROM hygiene_capture_variants")
    return {str(row["capture_id"]) for row in rows if row.get("capture_id")}


async def reachable(conn) -> set[str]:
    """根 + 派生。清扫的 keep 集合：不在里面的文件才是孤儿。"""
    return await roots(conn) | await derivatives(conn)


async def owners(conn) -> dict[str, str]:
    """capture_id → 类型。多处引用时取 `SOURCES` 里先出现的那处。"""
    owners_map: dict[str, str] = {}
    for row in await _rows(conn, _OWNERS_SQL):
        capture_id = str(row.get("capture_id") or "")
        if capture_id and capture_id not in owners_map:
            owners_map[capture_id] = str(row.get("kind") or "")
    return owners_map


async def _file_meta(captures, capture_id: str) -> Optional[tuple[int, str]]:
    """按磁盘上那份文件算大小与摘要。同一 (id, mtime) 只算一次。"""
    try:
        modified = await captures.modified_at_async(capture_id)
    except Exception:  # 文件不在时各 store 抛的不一样，统一当作「算不出」
        modified = None
    key = (capture_id, modified) if modified is not None else None
    if key is not None and key in _FILE_META_CACHE:
        return _FILE_META_CACHE[key]
    try:
        data = await captures.get_async(capture_id)
    except Exception:
        return None
    value = (len(data), hashlib.sha256(data).hexdigest())
    if key is not None:
        while len(_FILE_META_CACHE) >= _FILE_META_CACHE_LIMIT:
            _FILE_META_CACHE.pop(next(iter(_FILE_META_CACHE)))
        _FILE_META_CACHE[key] = value
    return value


async def meta(conn, capture_id: str, captures=None) -> Optional[dict]:
    """这张图的元数据；没有任何业务表持有它时返回 None。

    `byte_size` / `sha256` 只有标准图存了列。其余类型在给了 `captures` 时按实际文件补 ——
    下发的就是磁盘上那份，摘要与 ETag 必须跟着它走。
    """
    cleaned = str(capture_id or "").strip()
    if not cleaned:
        return None
    rows = await _rows(conn, _META_SQL, tuple([cleaned] * len(SOURCES)))
    if not rows:
        return None
    row = rows[0]
    size = None if row.get("byte_size") is None else int(row["byte_size"])
    sha = row.get("sha256")
    if captures is not None and (size is None or sha is None):
        filled = await _file_meta(captures, cleaned)
        if filled is not None:
            size = size if size is not None else filled[0]
            sha = sha or filled[1]
    return {
        "capture_id": cleaned,
        "content_type": row.get("content_type") or "image/jpeg",
        "byte_size": size,
        "sha256": sha,
    }


async def variants(conn, capture_id: str) -> dict[str, dict]:
    """这张图的变体，按角色（thumb / preview）索引。"""
    cleaned = str(capture_id or "").strip()
    if not cleaned:
        return {}
    rows = await _rows(conn, _VARIANTS_SQL, (cleaned,))
    return {str(row["variant"]): row for row in rows}


async def variant_index(conn) -> dict[str, tuple[str, ...]]:
    """根 → 变体文件 ids，供存储概况把变体字节算回原图。"""
    grouped: dict[str, list[str]] = {}
    rows = await _rows(conn, "SELECT source_capture_id, capture_id FROM hygiene_capture_variants")
    for row in rows:
        grouped.setdefault(str(row["source_capture_id"]), []).append(
            str(row["capture_id"])
        )
    return {key: tuple(value) for key, value in grouped.items()}


__all__ = [
    "ALLOWED_UNREGISTERED",
    "KIND_LABELS",
    "SOURCES",
    "Source",
    "claimed_columns",
    "derivatives",
    "meta",
    "owners",
    "reachable",
    "roots",
    "variant_index",
    "variants",
]