#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统完整备份：口令加密 tar 归档、快照回滚、卫生照片、冷备归档。

归档格式向后兼容：magic 保持 ``LUYUNBK2``，``meta.json`` 的 ``version`` 递增。
新增成员（两类卫生照片）都是可选成员，v2 备份仍可解析。

业务数据只有整库 ``pg_dump`` 一种形态（成员 ``app.pgdump``）：SQLite 时代的
``app.db`` / ``recipes.db`` 成员随 ADR 0089 退场，解析端仍能识别旧成员，但恢复端
会明确拒绝（SQLite 库灌不进 PostgreSQL）。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import asyncpg
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.hmac import HMAC

from config import settings
from services import backup_retention, credentials_store
from services.credentials_store import CHINA_TZ, _derive_backup_key

logger = logging.getLogger(__name__)

# 快照保留份数的默认值单一来源在 backup_retention；这里保留旧名以兼容调用方
SNAPSHOT_KEEP = backup_retention.SNAPSHOT_KEEP_DEFAULT

# —— 备份格式 ——
BACKUP_MAGIC = b"LUYUNBK2"
BACKUP_VERSION = 3
EXPORT_DIRNAME = "backup_exports"
SNAPSHOT_DIRNAME = "restore_snapshots"

# —— 卫生照片归档内目录 ——
PHOTO_STANDARD = "standard"
PHOTO_OTHER = "other"
PHOTO_MEMBER_DIRS = {
    PHOTO_STANDARD: "photos/standard",
    PHOTO_OTHER: "photos/other",
}

# —— 快照来由 ——
PROVENANCE_MANUAL = "manual"
PROVENANCE_PRE_IMPORT = "pre_import"
PROVENANCE_PRE_UPDATE = "pre_update"
PROVENANCE_PRE_ROLLBACK = "pre_rollback"
PROVENANCE_LABELS = {
    PROVENANCE_MANUAL: "手动",
    PROVENANCE_PRE_IMPORT: "覆盖导入前",
    PROVENANCE_PRE_UPDATE: "更新作业前",
    PROVENANCE_PRE_ROLLBACK: "回滚前",
}

# 备份归档覆盖的内容类别（页面与恢复结果共用同一组领域词）
CONTENT_CREDENTIALS = "credentials"
CONTENT_RUNTIME = "runtime"
CONTENT_APP_DB = "app_db"
# PostgreSQL 后端下业务数据是整库 pg_dump 出来的，不是可挂载的 app.db 文件。
# 单独一个类别，避免恢复流程把 .pgdump 当成 SQLite 文件去 copy。
CONTENT_APP_PG = "app_pg"
CONTENT_RECIPES = "recipes_db"
CONTENT_STANDARD_PHOTOS = "standard_photos"
CONTENT_OTHER_PHOTOS = "other_photos"
CONTENT_LABELS = {
    CONTENT_CREDENTIALS: "凭据",
    CONTENT_RUNTIME: "运行配置",
    CONTENT_APP_DB: "业务数据",
    CONTENT_APP_PG: "业务数据 (PostgreSQL)",
    CONTENT_RECIPES: "配方数据",
    CONTENT_STANDARD_PHOTOS: "标准图",
    CONTENT_OTHER_PHOTOS: "其它照片",
}

# 内容类别的等价覆盖关系：PG 后端的整库 pg_dump 同时含业务数据与配方数据
# （recipe 表就在同一个库里），因此它满足 app_db 与 recipes_db 两类。
# 缺项判定与展示都必须走这里，否则 PG 快照会一边列出「业务数据 (PostgreSQL)」，
# 一边报「缺少业务数据 / 配方数据」。
CONTENT_SATISFIED_BY: Dict[str, Tuple[str, ...]] = {
    CONTENT_APP_DB: (CONTENT_APP_DB, CONTENT_APP_PG),
    CONTENT_RECIPES: (CONTENT_RECIPES, CONTENT_APP_PG),
}


def contents_cover(contents: Sequence[str], content: str) -> bool:
    """contents 是否覆盖某个内容类别（考虑 PG 整库备份这类等价形式）。"""
    options = CONTENT_SATISFIED_BY.get(content, (content,))
    return any(option in contents for option in options)

# 基础校验读取的关键表；缺失任一即视为不可恢复
KEY_TABLES = ("orders", "tables", "dish_stations")

# 冷备状态文件（固定位置，冷备任务每次运行覆盖写入）
COLD_STATUS_FILENAME = "cold_backup_status.json"
COLD_ARCHIVE_NAME = "luyun_cold_backup.tar"
COLD_MANIFEST_NAME = "manifest.json"
COLD_CHECKSUMS_NAME = "SHA256SUMS"


def _snapshot_root() -> Path:
    return Path(settings.DATABASE_DIR) / SNAPSHOT_DIRNAME


def _export_root() -> Path:
    return Path(settings.DATABASE_DIR) / EXPORT_DIRNAME


def get_hygiene_capture_root() -> Path:
    """卫生照片文件目录（与启动时 FileCaptureStore 使用同一路径）。"""
    return Path(settings.DATABASE_DIR) / "hygiene-captures"


def get_credentials_file_path() -> str:
    return os.path.join(settings.DATABASE_DIR, "credentials.enc")


def get_cold_backup_dir() -> Path:
    """冷备输出根目录；部署脚本可用 BACKUP_DIR 覆盖。"""
    override = os.environ.get("BACKUP_DIR")
    if override:
        return Path(override)
    return Path(settings.COLD_BACKUP_DIR)


def cold_status_path(backup_dir: Optional[Path] = None) -> Path:
    return (backup_dir or get_cold_backup_dir()) / COLD_STATUS_FILENAME


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_tar_member(tar: tarfile.TarFile, name: str) -> bytes:
    member = tar.getmember(name)
    extracted = tar.extractfile(member)
    if extracted is None:
        raise ValueError("备份校验失败（文件可能被篡改）")
    return extracted.read()


# ==================== 卫生照片分类 ====================

# 标准图：日常检查项的合格参照，含全部历史版本（hygiene_standards 每一行一个版本）
_STANDARD_PHOTO_QUERIES: Tuple[str, ...] = (
    "SELECT capture_id FROM hygiene_standards",
)

# 其它照片：日常实拍、专项（深度清洁）、整改与回拍、教材
_OTHER_PHOTO_QUERIES: Tuple[str, ...] = (
    "SELECT capture_id FROM hygiene_daily_submissions",
    "SELECT before_capture_id AS capture_id FROM hygiene_deep_clean_submissions",
    "SELECT after_capture_id AS capture_id FROM hygiene_deep_clean_submissions",
    "SELECT capture_id FROM hygiene_fix_tickets",
    "SELECT capture_id FROM hygiene_fix_reshoots",
    "SELECT left_capture_id AS capture_id FROM hygiene_teaching_examples",
    "SELECT right_capture_id AS capture_id FROM hygiene_teaching_examples",
)

# 派生图（缩略图/预览图）由源照片派生，随源照片一起归类
_VARIANT_QUERY = "SELECT source_capture_id, capture_id FROM hygiene_capture_variants"


def _business_conn(db):
    """取业务库共享连接。

    ``db`` 通常是 ``DatabaseManager``（全部表共用一条连接）；也接受直接传入
    ``PgConnection`` / ``TableView``，谁的 ``execute`` 可用就用谁。
    """
    conn = getattr(db, "_conn", None)
    if conn is not None and hasattr(conn, "execute"):
        return conn
    return db


async def _query_capture_ids(conn, sql: str) -> List[str]:
    try:
        cursor = await conn.execute(sql)
        rows = await cursor.fetchall()
    except asyncpg.UndefinedTableError:
        # 表不存在（旧库/未启用卫生模块）不算错误，按「没有这类照片」处理
        return []
    return [row[0] for row in rows if row and row[0]]


async def _variant_rows(conn) -> List[Tuple[Any, Any]]:
    try:
        cursor = await conn.execute(_VARIANT_QUERY)
        rows = await cursor.fetchall()
    except asyncpg.UndefinedTableError:
        return []
    return [(row[0], row[1]) for row in rows]


async def classify_hygiene_capture_ids(
    db,
    *,
    include_variants: bool = True,
) -> Dict[str, List[str]]:
    """按业务身份把库里引用的照片分成标准图与其它照片两类。

    **查询走业务库连接**（``db`` 是 ``DatabaseManager`` 或它下面的连接）。SQLite
    时代这里按文件路径打开 ``data/app.db``：PG 门店上那个文件并不存在，分类于是
    静默返回空，照片差异 / 恢复后缺图两类检查全部「通过」。分类必须落在真正的
    业务库上，缺表（未启用卫生模块）才按「没有这类照片」处理。

    分类在备份创建时确定并写入清单，不在读取时猜测。返回 ``capture_id``
    列表（保持稳定顺序，便于测试与清单比对）。

    ``include_variants=False`` 只返回业务表的原始照片，不含派生图；恢复后
    一致性检查只关心原始照片（派生图缺失时接口会回退到原图）。
    """
    conn = _business_conn(db)
    result: Dict[str, List[str]] = {PHOTO_STANDARD: [], PHOTO_OTHER: []}

    standard: set = set()
    for sql in _STANDARD_PHOTO_QUERIES:
        standard.update(await _query_capture_ids(conn, sql))

    other: set = set()
    for sql in _OTHER_PHOTO_QUERIES:
        other.update(await _query_capture_ids(conn, sql))
    # 一张照片同时被两类引用时，标准图优先（标准图更严格）
    other -= standard

    # 派生图跟随源照片归类
    if include_variants:
        for source_id, variant_id in await _variant_rows(conn):
            if not variant_id:
                continue
            if source_id in standard:
                standard.add(variant_id)
            elif source_id in other:
                other.add(variant_id)

    result[PHOTO_STANDARD] = sorted(standard)
    result[PHOTO_OTHER] = sorted(other)
    return result


def collect_photo_blobs(
    capture_root: Path,
    capture_ids: Sequence[str],
) -> Tuple[Dict[str, bytes], List[str]]:
    """读取照片字节，返回 ``(存在的照片, 缺失的 capture_id)``。"""
    blobs: Dict[str, bytes] = {}
    missing: List[str] = []
    for capture_id in capture_ids:
        path = Path(capture_root) / capture_id
        try:
            if not path.is_file():
                missing.append(capture_id)
                continue
            blobs[capture_id] = path.read_bytes()
        except OSError:
            missing.append(capture_id)
    return blobs, missing


async def missing_hygiene_capture_ids(
    db,
    capture_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """核对「库里引用的照片」是否真的在磁盘上（只做存在性检查，不读字节）。

    恢复/回滚之后调用：库可以被单独换掉，照片文件却不会跟着来，于是库指向
    不存在的图片，前端清单里这些标准图静默缺失。这里把缺口显式报出来。
    """
    root = Path(capture_root) if capture_root is not None else get_hygiene_capture_root()
    classified = await classify_hygiene_capture_ids(db, include_variants=False)
    missing: Dict[str, List[str]] = {}
    for kind in (PHOTO_STANDARD, PHOTO_OTHER):
        gone = []
        for capture_id in classified[kind]:
            try:
                if not (root / capture_id).is_file():
                    gone.append(capture_id)
            except OSError:
                gone.append(capture_id)
        if gone:
            missing[kind] = gone
    return {
        "ok": not missing,
        "missing": missing,
        "standard_missing": len(missing.get(PHOTO_STANDARD, [])),
        "other_missing": len(missing.get(PHOTO_OTHER, [])),
        "checked_at": datetime.now(CHINA_TZ).isoformat(),
    }


async def collect_hygiene_photo_paths(
    db,
    capture_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """收集两类卫生照片的**路径**（不读字节）。

    照片动辄几十上百 MB，导出时没必要先全读进内存再写进 tar：tarfile 可以直接从
    文件流式读（``addfile`` 分块），校验和边读边算。返回
    ``members = [(归档内路径, 磁盘路径)]``，顺序稳定（类别内按 capture_id 排序），
    与 ``manifest`` 的口径一致。
    """
    root = Path(capture_root) if capture_root is not None else get_hygiene_capture_root()
    classified = await classify_hygiene_capture_ids(db)

    members: List[Tuple[str, Path]] = []
    manifest: Dict[str, Any] = {}
    missing: Dict[str, List[str]] = {PHOTO_STANDARD: [], PHOTO_OTHER: []}

    for kind in (PHOTO_STANDARD, PHOTO_OTHER):
        member_dir = PHOTO_MEMBER_DIRS[kind]
        # 先探一遍可用性：摘要一旦开始喂数据就没法回退，读不了的照片必须在这一步
        # 就排除掉，否则 manifest.sha256 会与归档内容对不上。
        available: List[Tuple[str, Path, int]] = []
        for capture_id in sorted(classified[kind]):
            path = root / capture_id
            try:
                if not path.is_file():
                    raise OSError("照片文件不存在")
                with open(path, "rb") as probe:
                    probe.read(1)
                size = path.stat().st_size
            except OSError:
                missing[kind].append(capture_id)
                continue
            available.append((capture_id, path, size))

        digest = hashlib.sha256()
        total_bytes = 0
        for capture_id, path, size in available:
            digest.update(capture_id.encode("utf-8"))
            digest.update(b"\0")
            # 这里不再吞 OSError：探测之后文件消失属于异常，宁可让导出失败，
            # 也不要产出一份 sha256 与内容不符的备份。
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            members.append((f"{member_dir}/{capture_id}", path))
            total_bytes += size
        manifest[kind] = {
            "count": len(available),
            "bytes": total_bytes,
            "sha256": digest.hexdigest(),
            "referenced": len(classified[kind]),
            "missing": len(missing[kind]),
        }

    return {"members": members, "manifest": manifest, "missing": missing}


def photo_included_kinds(manifest: Optional[dict]) -> List[str]:
    """清单里实际包含照片的类别（count > 0）。"""
    if not manifest:
        return []
    return [
        kind
        for kind in (PHOTO_STANDARD, PHOTO_OTHER)
        if int((manifest.get(kind) or {}).get("count") or 0) > 0
    ]



# ==================== 口令加密归档 ====================

# 导出成员来源：小成员直接给 bytes，大成员给磁盘路径（流式读，不驻留内存）
MemberSource = Union[bytes, str, Path]

# —— 分块 Fernet ——
#
# 导出包可能上百 MB，而 ``Fernet.encrypt()`` 要求整包 bytes：它内部会同时持有密文、
# base64 串与最终 token（base64 还让体积涨三分之一），实测 200MB 成品要 ~1.1GB 峰值。
# 下面按 Fernet 规范分块产出**同样的字节流**：
#
#   token = base64url(0x80 || timestamp(8) || iv(16) || AES128-CBC(PKCS7(data)) || HMAC-SHA256)
#
# 同一 key/iv/timestamp 下输出与 ``Fernet.encrypt`` 逐字节相同，所以旧版本照样能解开
# 新包（回滚安全），解析路径也完全不用改。
_FERNET_VERSION = b"\x80"
_EXPORT_CHUNK_SIZE = 4 * 1024 * 1024


class _Base64ChunkWriter:
    """按 3 字节对齐分块做 urlsafe base64，输出与一次性编码逐字节相同。"""

    def __init__(self, out) -> None:
        self._out = out
        self._pending = b""

    def write(self, data: bytes) -> None:
        if not data:
            return
        self._pending += data
        usable = len(self._pending) - (len(self._pending) % 3)
        if usable:
            self._out.write(base64.urlsafe_b64encode(self._pending[:usable]))
            self._pending = self._pending[usable:]

    def close(self) -> None:
        if self._pending:
            self._out.write(base64.urlsafe_b64encode(self._pending))
            self._pending = b""


def _fernet_encrypt_stream(
    src_path: str,
    out_handle,
    key: bytes,
    *,
    iv: Optional[bytes] = None,
    timestamp: Optional[int] = None,
    chunk_size: int = _EXPORT_CHUNK_SIZE,
    progress: Optional[Callable[[int], None]] = None,
) -> None:
    """把 ``src_path`` 的内容加密成 Fernet token 写进 ``out_handle``。

    分块读、分块加密、增量 HMAC、分块 base64：内存占用只与 ``chunk_size`` 有关，
    与包大小无关。``iv`` / ``timestamp`` / ``chunk_size`` 只为测试注入——生产路径用
    随机 IV 与当前时间，输出与分块大小无关。``progress(已读字节)`` 用于上报进度。
    """
    raw_key = base64.urlsafe_b64decode(key)
    signing_key, encryption_key = raw_key[:16], raw_key[16:]
    iv = iv if iv is not None else os.urandom(16)
    stamp = int(time.time()) if timestamp is None else int(timestamp)

    hasher = HMAC(signing_key, hashes.SHA256())
    encryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(iv)).encryptor()
    padder = sym_padding.PKCS7(algorithms.AES.block_size).padder()

    writer = _Base64ChunkWriter(out_handle)
    head = _FERNET_VERSION + struct.pack(">Q", stamp) + iv
    hasher.update(head)
    writer.write(head)

    read_bytes = 0
    with open(src_path, "rb") as src:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            read_bytes += len(chunk)
            piece = encryptor.update(padder.update(chunk))
            if piece:
                hasher.update(piece)
                writer.write(piece)
            if progress is not None:
                progress(read_bytes)

    tail = encryptor.update(padder.finalize()) + encryptor.finalize()
    if tail:
        hasher.update(tail)
        writer.write(tail)
    writer.write(hasher.finalize())
    writer.close()
    if progress is not None:
        progress(read_bytes)


class _HashingReader:
    """给 tarfile 用的分块读取器：边喂数据边算 sha256，不把整个成员读进内存。"""

    def __init__(self, path: Path, on_bytes: Optional[Callable[[int], None]] = None):
        self._handle = open(path, "rb")
        self._on_bytes = on_bytes
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = self._handle.read(size if size and size > 0 else 1024 * 1024)
        if chunk:
            self.digest.update(chunk)
            if self._on_bytes is not None:
                self._on_bytes(len(chunk))
        return chunk

    def close(self) -> None:
        self._handle.close()


def _tar_member_size(source: MemberSource) -> int:
    """成员的字节数（打包前算总量用，不读内容）。"""
    if isinstance(source, (str, Path)):
        try:
            return Path(source).stat().st_size
        except OSError:
            return 0
    return len(source) if isinstance(source, bytes) else 0


def _add_member(
    tar: tarfile.TarFile,
    name: str,
    source: MemberSource,
    on_bytes: Optional[Callable[[int], None]] = None,
) -> Tuple[str, int]:
    """把一个成员写进 tar，返回 ``(sha256, 字节数)``。

    bytes 直接写；路径走 :class:`_HashingReader` 分块流式写——``app.db`` /
    ``app.pgdump`` / 几十上百 MB 的照片因此不需要先读进内存。
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        size = path.stat().st_size
        reader = _HashingReader(path, on_bytes)
        try:
            info = tarfile.TarInfo(name=name)
            info.size = size
            tar.addfile(info, reader)
        finally:
            reader.close()
        return reader.digest.hexdigest(), size

    data = source if isinstance(source, bytes) else b""
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))
    if on_bytes is not None and data:
        on_bytes(len(data))
    return _sha256_hex(data), len(data)


def build_export_backup_to_file(
    dst_path: str,
    passphrase: str,
    *,
    include_runtime: bool,
    runtime_data: Optional[dict],
    include_app_db: bool,
    app_version: str,
    app_pg_source: Optional[MemberSource] = None,
    include_standard_photos: bool = False,
    include_other_photos: bool = False,
    photo_members: Optional[Sequence[Tuple[str, MemberSource]]] = None,
    photo_manifest: Optional[dict] = None,
    photo_missing: Optional[dict] = None,
    consistency: Optional[dict] = None,
    provenance: str = PROVENANCE_MANUAL,
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> dict:
    """构建口令加密的导出包并**直接写到 dst_path**，返回 meta。

    与一次性堆内存的老实现相比只有"边算边写"这一点不同：

    - 成员逐个写进临时 tar（大成员流式，见 :func:`_add_member`），不再先把所有
      成员塞进一个 dict、再整体 tar 进内存；
    - 加密时只把 tar 读一份进内存（``Fernet`` 要求严格 bytes，喂不了 mmap），
      输出直接落盘；
    - ``archive_sha256`` 对最终文件分块计算，不再为算哈希多留一份整包。

    ``progress(stage, done, total, unit)`` 上报进度。``unit`` 是 ``"count"``（张数）
    或 ``"bytes"``（字节）——归档与加密都给真实字节数，界面因此能显示真实百分比，
    而不是一直转圈。回调异常只记日志，不影响导出本身。
    """

    def _report(
        stage: str,
        done: int = 0,
        total: int = 0,
        unit: str = "count",
    ) -> None:
        if progress is None:
            return
        try:
            progress(stage, done, total, unit)
        except Exception:  # noqa: BLE001 —— 进度上报不该把导出搞挂
            logger.debug("导出进度回调失败 stage=%s", stage, exc_info=True)

    passphrase = (passphrase or "").strip()
    if len(passphrase) < credentials_store.BACKUP_PASSPHRASE_MIN_LENGTH:
        raise ValueError(
            f"导出口令至少 {credentials_store.BACKUP_PASSPHRASE_MIN_LENGTH} 位"
        )

    bundle = credentials_store.get_credentials()
    if bundle is None:
        raise ValueError("当前未配置凭据")
    credentials_bytes = json.dumps(
        bundle.to_storage(), ensure_ascii=False
    ).encode("utf-8")

    if include_standard_photos and include_other_photos:
        want_photo_kinds = {PHOTO_STANDARD, PHOTO_OTHER}
    elif include_standard_photos:
        want_photo_kinds = {PHOTO_STANDARD}
    elif include_other_photos:
        want_photo_kinds = {PHOTO_OTHER}
    else:
        want_photo_kinds = set()

    includes = {
        "runtime": False,
        "app_pg": False,
        CONTENT_STANDARD_PHOTOS: False,
        CONTENT_OTHER_PHOTOS: False,
    }

    _report("collecting")
    members: List[Tuple[str, MemberSource]] = [("credentials.json", credentials_bytes)]
    if include_runtime and runtime_data is not None:
        members.append(
            ("runtime.json", json.dumps(runtime_data, ensure_ascii=False).encode("utf-8"))
        )
        includes["runtime"] = True
    # 业务数据只有一种形态：整库 pg_dump（成员名与冷备一致，恢复端走 pg_restore）。
    # SQLite 时代的 app.db / recipes.db 成员已随 ADR 0089 退场：配方表就在同一个库里，
    # 随 app.pgdump 一起走。
    if include_app_db and app_pg_source is not None:
        members.append(("app.pgdump", app_pg_source))
        includes["app_pg"] = True
        # 配方表（sop_*）就在同一个库里：整库 dump 确实含配方，清单如实标出来，
        # 页面才不会一边打包了配方、一边显示「含配方：否」。
        includes["recipes_db"] = True

    prefix_ok = {kind: PHOTO_MEMBER_DIRS[kind] + "/" for kind in want_photo_kinds}
    photo_list: List[Tuple[str, MemberSource]] = [
        (name, source)
        for name, source in (photo_members or [])
        if any(name.startswith(prefix) for prefix in prefix_ok.values())
    ]

    photos_meta: Dict[str, Any] = {}
    member_manifest = photo_manifest or {}
    for kind in (PHOTO_STANDARD, PHOTO_OTHER):
        entry = dict(member_manifest.get(kind) or {})
        entry.setdefault("count", 0)
        entry.setdefault("bytes", 0)
        entry.setdefault("sha256", None)
        entry.setdefault("missing", 0)
        entry["included"] = kind in want_photo_kinds
        if kind in want_photo_kinds:
            includes[
                CONTENT_STANDARD_PHOTOS if kind == PHOTO_STANDARD else CONTENT_OTHER_PHOTOS
            ] = True
        photos_meta[kind] = entry

    fd, tar_path = tempfile.mkstemp(suffix=".tar", prefix="luyun-export-")
    os.close(fd)
    try:
        digests: Dict[str, str] = {}
        # 归档进度按**字节**上报：总量在打包前就能算出来（成员大小之和），
        # 所以界面显示的是真实百分比，而不是一直转圈。
        total_bytes = sum(_tar_member_size(source) for _n, source in members) + sum(
            _tar_member_size(source) for _n, source in photo_list
        )
        written = {"bytes": 0}

        def _on_member_bytes(count: int) -> None:
            written["bytes"] += count
            _report("archiving", written["bytes"], total_bytes, "bytes")

        with tarfile.open(tar_path, mode="w") as tar:
            _report("archiving", 0, total_bytes, "bytes")
            for name, source in members:
                digests[name] = _add_member(tar, name, source, _on_member_bytes)[0]

            total_photos = len(photo_list)
            for index, (name, source) in enumerate(photo_list, start=1):
                digests[name] = _add_member(tar, name, source, _on_member_bytes)[0]
                _report("photos", index, total_photos)

            meta: Dict[str, Any] = {
                "version": BACKUP_VERSION,
                "exported_at": datetime.now(CHINA_TZ).isoformat(),
                "app_version": app_version,
                "provenance": provenance,
                "includes": includes,
                "photos": photos_meta,
                "sha256": {
                    name: digest
                    for name, digest in digests.items()
                    if not name.startswith("photos/")
                },
            }
            if photo_missing:
                meta["photos_missing"] = {
                    kind: list(ids) for kind, ids in photo_missing.items() if ids
                }
            if consistency is not None:
                meta["consistency"] = consistency

            meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
            info = tarfile.TarInfo(name="meta.json")
            info.size = len(meta_bytes)
            tar.addfile(info, io.BytesIO(meta_bytes))

        salt = os.urandom(credentials_store.BACKUP_SALT_BYTES)
        iterations = credentials_store.BACKUP_KDF_ITERATIONS
        key = _derive_backup_key(passphrase, salt, iterations)

        header_obj = {
            "salt": base64.b64encode(salt).decode("ascii"),
            "iterations": iterations,
            "created_at": datetime.now(CHINA_TZ).isoformat(),
        }
        header_bytes = json.dumps(header_obj, ensure_ascii=False).encode("utf-8")

        # 加密进度同样按字节：tar 大小已知，读多少就是多少
        tar_size = os.path.getsize(tar_path)
        _report("encrypting", 0, tar_size, "bytes")

        def _on_encrypt_bytes(read_bytes: int) -> None:
            _report("encrypting", read_bytes, tar_size, "bytes")

        with open(dst_path, "wb") as out:
            out.write(BACKUP_MAGIC)
            out.write(struct.pack(">I", len(header_bytes)))
            out.write(header_bytes)
            # 分块加密直接写进目标文件的 token 段：tar 不会整包留在内存里
            _fernet_encrypt_stream(tar_path, out, key, progress=_on_encrypt_bytes)

        meta["archive_bytes"] = os.path.getsize(dst_path)
        meta["archive_sha256"] = sha256_file(Path(dst_path))
        return meta
    finally:
        try:
            os.unlink(tar_path)
        except OSError:
            pass


def build_export_backup(
    passphrase: str,
    *,
    include_runtime: bool,
    runtime_data: Optional[dict],
    include_app_db: bool,
    app_pg_bytes: Optional[bytes] = None,
    include_standard_photos: bool = False,
    include_other_photos: bool = False,
    photo_members: Optional[Dict[str, bytes]] = None,
    photo_manifest: Optional[dict] = None,
    photo_missing: Optional[dict] = None,
    consistency: Optional[dict] = None,
    provenance: str = PROVENANCE_MANUAL,
    app_version: str,
) -> Tuple[bytes, dict]:
    """构建口令加密备份包与它的 ``meta``（调用方据此写导出侧车清单）。

    内存版：把成员字节落成临时文件后调用流式实现，再把成品读回来。导出端点用的是
    :func:`build_export_backup_to_file`；这个入口留给测试与小包调用方，签名不变。
    """
    passphrase = (passphrase or "").strip()
    if len(passphrase) < credentials_store.BACKUP_PASSPHRASE_MIN_LENGTH:
        raise ValueError(f"导出口令至少 {credentials_store.BACKUP_PASSPHRASE_MIN_LENGTH} 位")

    fd, tmp_path = tempfile.mkstemp(suffix=".luyunbak", prefix="luyun-export-")
    os.close(fd)
    try:
        meta = build_export_backup_to_file(
            tmp_path,
            passphrase,
            include_runtime=include_runtime,
            runtime_data=runtime_data,
            include_app_db=include_app_db,
            app_pg_source=app_pg_bytes,
            include_standard_photos=include_standard_photos,
            include_other_photos=include_other_photos,
            photo_members=[(name, data) for name, data in (photo_members or {}).items()],
            photo_manifest=photo_manifest,
            photo_missing=photo_missing,
            consistency=consistency,
            provenance=provenance,
            app_version=app_version,
        )
        with open(tmp_path, "rb") as handle:
            blob = handle.read()
        return blob, meta
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def build_backup(
    passphrase: str,
    *,
    include_runtime: bool,
    runtime_data: Optional[dict],
    include_app_db: bool,
    app_pg_bytes: Optional[bytes] = None,
    include_standard_photos: bool = False,
    include_other_photos: bool = False,
    photo_members: Optional[Dict[str, bytes]] = None,
    photo_manifest: Optional[dict] = None,
    photo_missing: Optional[dict] = None,
    consistency: Optional[dict] = None,
    provenance: str = PROVENANCE_MANUAL,
    app_version: str,
) -> bytes:
    """构建口令加密备份二进制包（向后兼容入口，只返回字节）。"""
    blob, _meta = build_export_backup(
        passphrase,
        include_runtime=include_runtime,
        runtime_data=runtime_data,
        include_app_db=include_app_db,
        app_pg_bytes=app_pg_bytes,
        include_standard_photos=include_standard_photos,
        include_other_photos=include_other_photos,
        photo_members=photo_members,
        photo_manifest=photo_manifest,
        photo_missing=photo_missing,
        consistency=consistency,
        provenance=provenance,
        app_version=app_version,
    )
    return blob


def _read_photo_members(tar: tarfile.TarFile) -> Dict[str, bytes]:
    photos: Dict[str, bytes] = {}
    for name in tar.getnames():
        for kind, member_dir in PHOTO_MEMBER_DIRS.items():
            prefix = member_dir + "/"
            if name.startswith(prefix) and name != prefix:
                photos[name] = _read_tar_member(tar, name)
                break
    return photos


def archive_integrity_errors(
    meta: dict,
    photo_counts: Dict[str, int],
) -> List[str]:
    """归档内一致性：清单声明的照片数量必须与归档内容一致。"""
    errors: List[str] = []
    declared = meta.get("photos") or {}
    for kind in (PHOTO_STANDARD, PHOTO_OTHER):
        entry = declared.get(kind) or {}
        if not entry or not entry.get("included"):
            continue
        expected = int(entry.get("count") or 0)
        actual = int(photo_counts.get(kind) or 0)
        if expected != actual:
            label = (
                CONTENT_LABELS[CONTENT_STANDARD_PHOTOS]
                if kind == PHOTO_STANDARD
                else CONTENT_LABELS[CONTENT_OTHER_PHOTOS]
            )
            errors.append(f"{label}数量与清单不符（清单 {expected}，归档 {actual}）")
    return errors


def parse_backup(blob: bytes, passphrase: str) -> dict:
    """解密并校验备份包，返回各成员内容（照片按类别分开）。

    成员形态只剩一种业务数据：``app.pgdump``（整库，配方表在其中）。旧包里的
    ``app.db`` / ``recipes.db`` 成员会被读出来但不参与恢复——SQLite 库灌不进
    PostgreSQL，导入侧据此明确报错并给指引。
    """
    passphrase = (passphrase or "").strip()
    if not passphrase:
        raise ValueError("请输入解密口令")

    if not blob.startswith(BACKUP_MAGIC):
        raise ValueError("不是有效的备份文件")

    offset = len(BACKUP_MAGIC)
    if len(blob) < offset + 4:
        raise ValueError("不是有效的备份文件")

    header_len = struct.unpack(">I", blob[offset : offset + 4])[0]
    offset += 4
    header_end = offset + header_len
    if len(blob) < header_end:
        raise ValueError("不是有效的备份文件")

    try:
        header = json.loads(blob[offset:header_end].decode("utf-8"))
        salt = base64.b64decode(header["salt"])
        iterations = int(header["iterations"])
    except Exception:
        raise ValueError("不是有效的备份文件")

    token = blob[header_end:]
    key = _derive_backup_key(passphrase, salt, iterations)
    try:
        tar_bytes = Fernet(key).decrypt(token)
    except InvalidToken:
        raise ValueError("口令错误或文件损坏")

    tar_buffer = io.BytesIO(tar_bytes)
    with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
        meta_bytes = _read_tar_member(tar, "meta.json")
        meta = json.loads(meta_bytes.decode("utf-8"))
        expected_sha = meta.get("sha256") or {}
        names = tar.getnames()

        credentials_bytes = _read_tar_member(tar, "credentials.json")
        if expected_sha.get("credentials.json") != _sha256_hex(credentials_bytes):
            raise ValueError("备份校验失败（文件可能被篡改）")

        runtime_data = None
        if "runtime.json" in names:
            runtime_bytes = _read_tar_member(tar, "runtime.json")
            if expected_sha.get("runtime.json") != _sha256_hex(runtime_bytes):
                raise ValueError("备份校验失败（文件可能被篡改）")
            runtime_data = json.loads(runtime_bytes.decode("utf-8"))

        app_db_bytes = None
        if "app.db" in names:
            app_db_bytes = _read_tar_member(tar, "app.db")
            if expected_sha.get("app.db") != _sha256_hex(app_db_bytes):
                raise ValueError("备份校验失败（文件可能被篡改）")

        app_pg_bytes = None
        if "app.pgdump" in names:
            app_pg_bytes = _read_tar_member(tar, "app.pgdump")
            if expected_sha.get("app.pgdump") != _sha256_hex(app_pg_bytes):
                raise ValueError("备份校验失败（文件可能被篡改）")

        raw_photos = _read_photo_members(tar)

    # 归档内一致性：清单声明与归档内容必须对得上（不一致 = 这份备份坏了）
    photos: Dict[str, Dict[str, bytes]] = {PHOTO_STANDARD: {}, PHOTO_OTHER: {}}
    for kind, member_dir in PHOTO_MEMBER_DIRS.items():
        prefix = member_dir + "/"
        photos[kind] = {
            name[len(prefix):]: data
            for name, data in raw_photos.items()
            if name.startswith(prefix)
        }

    integrity_errors = archive_integrity_errors(
        meta, {kind: len(blobs) for kind, blobs in photos.items()}
    )

    credentials = json.loads(credentials_bytes.decode("utf-8"))
    return {
        "meta": meta,
        "credentials": credentials,
        "runtime": runtime_data,
        # SQLite 时代的 app.db 成员只用于识别与明确拒绝（灌不进 PostgreSQL），
        # 新备份里不会再有它。
        "app_db_bytes": app_db_bytes,
        "app_pg_bytes": app_pg_bytes,
        "standard_photos": photos[PHOTO_STANDARD],
        "other_photos": photos[PHOTO_OTHER],
        "archive_integrity_errors": integrity_errors,
    }


# ==================== 快照（明文回滚点）====================


def _redact_dsn_password(text: str, dsn: str) -> str:
    """抹掉文本里可能出现的 DSN 密码。

    pg_dump 偶尔会把连接串回显进 stderr；错误信息会进更新作业日志、后台页面和
    运维报告，密码不能跟着走。
    """
    if not text or not dsn:
        return text
    match = re.search(r"://[^:/@]+:([^@]*)@", dsn)
    if match and match.group(1):
        text = text.replace(match.group(1), "***")
    return text


def _pg_dump_failure_hint(stderr: str) -> str:
    """把 pg_dump 的常见失败翻译成可执行的下一步。"""
    low = (stderr or "").lower()
    if "version mismatch" in low:
        return (
            "（pg_dump 版本低于服务端：镜像里的 postgresql-client 需与服务端同大版本，"
            "重建镜像后重试）"
        )
    if "password authentication failed" in low or "authentication" in low:
        return "（认证失败：核对 POSTGRES_DSN 的用户名/密码，以及 pg_hba.conf 是否已 reload）"
    if "could not connect" in low or "connection refused" in low or "could not translate host" in low:
        return "（连不上服务端：核对 DSN 的主机/端口与 postgres 容器状态）"
    if "permission denied" in low:
        return "（权限不足：确认该角色能读取全部表）"
    return ""


def _pg_dump_sync(dst_path: str) -> None:
    """用 pg_dump 导出整库（custom format）。

    选 custom format 而不是 plain SQL：它能配合 ``pg_restore`` 做单表/选择性恢复，
    也自带压缩。``--no-owner --no-acl`` 让备份在换用户/换机器的恢复场景下不报权限错。
    """
    dsn = os.environ.get("LUYUN_POSTGRES_DSN") or getattr(settings, "POSTGRES_DSN", "")
    if not dsn:
        raise RuntimeError("POSTGRES_DSN 未配置，无法备份 PostgreSQL")
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    if os.path.exists(dst_path):
        os.unlink(dst_path)
    cmd = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--file",
        dst_path,
        dsn,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "未找到 pg_dump —— PostgreSQL 后端需要安装 postgresql-client"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("pg_dump 超时（1800s）") from exc
    if proc.returncode != 0:
        # 只脱敏密码，stderr 原文要带出来：否则现场只剩「退出码 1」，根因全靠猜。
        detail = _redact_dsn_password((proc.stderr or "").strip(), dsn)
        hint = _pg_dump_failure_hint(detail)
        message = f"pg_dump 失败（退出码 {proc.returncode}）"
        if detail:
            message = f"{message}：{detail}"
        if hint:
            message = f"{message} {hint}"
        raise RuntimeError(message)


# 整库恢复后重置 identity 序列：pg_restore 只灌数据，不动序列当前值，
# 不重置会让下一条 INSERT 撞主键（deploy/README.md 10.4 手工流程的最后一步）。
PG_SEQUENCE_RESET_SQL = """
DO $$
DECLARE r RECORD; seq TEXT;
BEGIN
  FOR r IN
    SELECT c.relname FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r' AND n.nspname = 'public'
      AND EXISTS (SELECT 1 FROM pg_attribute a
                  WHERE a.attrelid = c.oid AND a.attname = 'id' AND a.attnum > 0)
  LOOP
    seq := pg_get_serial_sequence('public.' || quote_ident(r.relname), 'id');
    IF seq IS NOT NULL THEN
      EXECUTE format(
        'SELECT setval(%L, COALESCE((SELECT MAX(id) FROM public.%I), 1))',
        seq, r.relname);
    END IF;
  END LOOP;
END $$;
"""


def restore_pg_dump_sync(dump_path: str) -> None:
    """用 pg_restore 把整库 dump 灌回 PostgreSQL（覆盖当前库内容）。

    ``--clean --if-exists`` 会先 drop 再重建对象，因此调用方必须保证：
    恢复期间不再有人写库，恢复完成后重建数据库连接（drop/重建会让既有连接上的
    prepared statement 失效），并让当前后台会话失效（auth 表被一起替换）。

    失败时把 stderr 脱敏后原样带出；密码不落日志、也不进异常消息。
    """
    if not os.path.isfile(dump_path):
        raise RuntimeError("快照里没有 PostgreSQL 整库备份（app.pgdump）")
    dsn = os.environ.get("LUYUN_POSTGRES_DSN") or getattr(settings, "POSTGRES_DSN", "")
    if not dsn:
        raise RuntimeError("POSTGRES_DSN 未配置，无法恢复 PostgreSQL")
    if shutil.which("pg_restore") is None:
        raise RuntimeError(
            "未找到 pg_restore —— PostgreSQL 后端需要安装 postgresql-client"
        )
    cmd = [
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-acl",
        "-d",
        dsn,
        dump_path,
    ]
    logger.warning("⏮ [审计] 开始 PostgreSQL 整库恢复（会 drop 并重建对象）: %s", dump_path)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("pg_restore 超时（3600s）") from exc
    if proc.returncode != 0:
        detail = _redact_dsn_password((proc.stderr or "").strip(), dsn)
        message = f"pg_restore 失败（退出码 {proc.returncode}）"
        if detail:
            message = f"{message}：{detail}"
        message = (
            f"{message}。当前库可能处于部分恢复状态，"
            "请用恢复前自动生成的那份本机回滚快照重试，或按 deploy/README.md 手工恢复"
        )
        raise RuntimeError(message)

    # 序列跟上数据：失败只告警，不回滚已经完成的整库恢复
    if _pg_psql_rows(PG_SEQUENCE_RESET_SQL) is None:
        logger.warning("⚠️ 整库恢复完成，但 identity 序列重置失败（新写入可能撞主键）")
    logger.warning("✅ [审计] PostgreSQL 整库恢复完成")


def _pg_dump_with_progress(
    dst_path: str,
    on_bytes: Callable[[int], None],
    interval: float = 0.3,
) -> None:
    """跑 pg_dump，边跑边报"已写入多少字节"。

    pg_dump 没有进度输出，custom format 的总量事先也不知道——所以这里不给百分比，
    只盯输出文件大小：界面显示「正在导出业务数据… 12.3 MB」，比一直转圈诚实。
    """
    dsn = os.environ.get("LUYUN_POSTGRES_DSN") or getattr(settings, "POSTGRES_DSN", "")
    if not dsn:
        raise RuntimeError("POSTGRES_DSN 未配置，无法备份 PostgreSQL")
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    if os.path.exists(dst_path):
        os.unlink(dst_path)
    cmd = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--file",
        dst_path,
        dsn,
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "未找到 pg_dump —— PostgreSQL 后端需要安装 postgresql-client"
        ) from exc

    deadline = time.time() + 1800
    stderr_text = ""
    try:
        while proc.poll() is None:
            try:
                on_bytes(os.path.getsize(dst_path))
            except OSError:
                pass
            if time.time() > deadline:
                proc.kill()
                raise RuntimeError("pg_dump 超时（1800s）")
            time.sleep(interval)
        stderr_text = proc.communicate()[1] or ""
    finally:
        if proc.poll() is None:
            proc.kill()

    try:
        on_bytes(os.path.getsize(dst_path))
    except OSError:
        pass

    if proc.returncode != 0:
        # 只脱敏密码，stderr 原文要带出来：否则现场只剩「退出码 1」，根因全靠猜。
        detail = _redact_dsn_password(stderr_text.strip(), dsn)
        hint = _pg_dump_failure_hint(detail)
        message = f"pg_dump 失败（退出码 {proc.returncode}）"
        if detail:
            message = f"{message}：{detail}"
        if hint:
            message = f"{message} {hint}"
        raise RuntimeError(message)


async def export_pg_dump_to_file(
    dst_path: str,
    progress: Optional[Callable[[int], None]] = None,
) -> None:
    """把整个 PostgreSQL 库打成 custom-format dump **直接写进 dst_path**。

    导出任务走这条：pg_dump 只能写文件，落到磁盘后由 tar 流式读走，中间不经过内存。
    ``progress(已写字节)`` 可选，用来让界面显示"导出了多少"。
    """
    if progress is None:
        await asyncio.to_thread(_pg_dump_sync, dst_path)
        return
    await asyncio.to_thread(_pg_dump_with_progress, dst_path, progress)


async def export_pg_dump_bytes() -> Optional[bytes]:
    """把整个 PostgreSQL 库打成 custom-format dump 的字节串。

    这是导出包里的「业务数据」成员（``app.pgdump``）。dump 走临时文件：
    pg_dump 只能写文件，不产出 stdout 流。
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".pgdump", prefix="luyun-export-")
    os.close(fd)
    try:
        await asyncio.to_thread(_pg_dump_sync, tmp_path)
        with open(tmp_path, "rb") as handle:
            return handle.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def restore_app_pg_from_bytes(db, dump_bytes: bytes) -> None:
    """用整库 dump 覆盖当前 PostgreSQL（``pg_restore --clean``）。

    调用方负责前置快照与恢复后的会话失效。这里只管两件必须有的事：

    - **先断开我们自己的连接**：``--clean`` 要 drop 并重建对象，我方的未提交事务
      会持表锁让 drop 卡住；
    - **再把连接接回来**：恢复失败时后台还要能报错、能重试（连接留着的话其上的
      prepared statement 也已失效）。

    整库替换，没有「合并导入」这种粒度：``pg_restore --clean`` 是库级操作。
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".pgdump", prefix="luyun-restore-")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(dump_bytes)
        await db.close()
        try:
            await asyncio.to_thread(restore_pg_dump_sync, tmp_path)
        finally:
            await db.connect()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _scan_capture_members(root: Path) -> Dict[str, Any]:
    """照片收集兜底：直接扫目录，不查库。

    ``_write_snapshot_photos_pg`` 在分类不可用（psql 缺失 / 查询失败）时退到这里：
    照片全部落在同一个目录里，恢复也是整目录写回。多带上几个孤儿文件（库里已删、
    磁盘未清）比漏带业务照片安全得多——但清单会标 ``unclassified``，不假装分成
    了两类。
    """
    members: Dict[str, bytes] = {}
    kind_dir = PHOTO_MEMBER_DIRS[PHOTO_OTHER]
    total_bytes = 0
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if not entry.is_file():
                continue
            try:
                data = entry.read_bytes()
            except OSError:
                continue
            members[f"{kind_dir}/{entry.name}"] = data
            total_bytes += len(data)
    digest = hashlib.sha256()
    for name in sorted(members):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(members[name])
    return {
        "members": members,
        "manifest": {
            PHOTO_STANDARD: {"count": 0, "bytes": 0},
            PHOTO_OTHER: {
                "count": len(members),
                "bytes": total_bytes,
                "sha256": digest.hexdigest(),
            },
        },
        "missing": {PHOTO_STANDARD: [], PHOTO_OTHER: []},
    }


def _link_or_copy(src: Path, dst: Path) -> None:
    """照片文件内容不可变，优先硬链接复用；失败时退回复制。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _pg_psql_rows(sql: str) -> Optional[List[List[str]]]:
    """用 psql 同步查询 PG（DSN 与 pg_dump 同一来源）。

    返回每行的列列表；psql 缺失或查询失败返回 ``None``（调用方退化为目录扫描）；
    表不存在按「没有这类照片」返回空列表（旧库 / 未启用卫生模块属正常情况）。
    """
    dsn = os.environ.get("LUYUN_POSTGRES_DSN") or getattr(settings, "POSTGRES_DSN", "")
    if not dsn or shutil.which("psql") is None:
        return None
    cmd = ["psql", dsn, "-At", "-F", "\t", "-c", sql]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("照片分类查询执行失败，退化为目录扫描: %s", exc)
        return None
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        if "does not exist" in detail or "不存在" in detail:
            return []
        logger.warning(
            "照片分类查询失败，退化为目录扫描: %s",
            _redact_dsn_password(detail[:200], dsn),
        )
        return None
    return [line.split("\t") for line in (proc.stdout or "").splitlines() if line.strip()]


def _classify_capture_ids_pg() -> Optional[Dict[str, List[str]]]:
    """同步版分类：psql 查业务库，按库引用把 capture_id 分成标准图 / 其它照片。

    快照创建是同步路径（更新作业在独立进程里跑，没有事件循环可用），所以这里用
    ``psql`` 而不是 ``DatabaseManager``；SQL 与 :func:`classify_hygiene_capture_ids`
    共用同一批常量，口径一致（标准图优先、派生图跟随源图）。
    恢复端只按 capture_id 写回同一目录，因此分类只影响清单与覆盖结论。
    """
    standard: set = set()
    other: set = set()
    for sql in _STANDARD_PHOTO_QUERIES:
        rows = _pg_psql_rows(sql)
        if rows is None:
            return None
        standard.update(row[0] for row in rows if row and row[0])
    for sql in _OTHER_PHOTO_QUERIES:
        rows = _pg_psql_rows(sql)
        if rows is None:
            return None
        other.update(row[0] for row in rows if row and row[0])
    # 一张照片同时被两类引用时标准图优先（与异步分类口径一致）
    other -= standard

    variants = _pg_psql_rows(_VARIANT_QUERY)
    if variants is None:
        return None
    for row in variants:
        if len(row) < 2 or not row[1]:
            continue
        source_id, variant_id = row[0], row[1]
        if source_id in standard:
            standard.add(variant_id)
        elif source_id in other:
            other.add(variant_id)

    return {PHOTO_STANDARD: sorted(standard), PHOTO_OTHER: sorted(other)}


def _write_snapshot_photos_pg(snap_dir: Path) -> Dict[str, Any]:
    """照片入快照：按库引用分类，文件以硬链接（或复制）落盘。

    psql 不可用 / 查询失败时退化为「目录扫描 + 全部计入其它照片」，并把
    ``unclassified`` 标出来：标准图恒为 0 是「没分类」，不是「本来就没有」。
    """
    root = get_hygiene_capture_root()
    classified = _classify_capture_ids_pg()
    if classified is None:
        logger.warning(
            "PG 照片分类不可用（psql 缺失或查询失败），退化为目录扫描：照片全部计入其它照片"
        )
        return _write_snapshot_photos_scan(snap_dir, root)

    manifest: Dict[str, Any] = {}
    missing: Dict[str, List[str]] = {}
    for kind in (PHOTO_STANDARD, PHOTO_OTHER):
        blobs, gone = collect_photo_blobs(root, classified[kind])
        if gone:
            missing[kind] = gone
        for capture_id, data in blobs.items():
            target = snap_dir / PHOTO_MEMBER_DIRS[kind] / capture_id
            target.parent.mkdir(parents=True, exist_ok=True)
            _link_or_copy(root / capture_id, target)
        digest = hashlib.sha256()
        for capture_id in sorted(blobs):
            digest.update(capture_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(blobs[capture_id])
        manifest[kind] = {
            "count": len(blobs),
            "bytes": sum(len(blob) for blob in blobs.values()),
            "sha256": digest.hexdigest(),
            "referenced": len(classified[kind]),
            "missing": len(gone),
            "written": len(blobs),
            "unclassified": False,
        }
    return {"manifest": manifest, "missing": missing}


def _write_snapshot_photos_scan(snap_dir: Path, root: Path) -> Dict[str, Any]:
    """目录扫描兜底：capture 目录里的文件平铺存放，全部计入其它照片。"""
    collected = _scan_capture_members(root)
    manifest: Dict[str, Any] = {}
    for kind in (PHOTO_STANDARD, PHOTO_OTHER):
        prefix = PHOTO_MEMBER_DIRS[kind] + "/"
        written = 0
        for name in collected["members"]:
            if not name.startswith(prefix):
                continue
            _link_or_copy(root / name[len(prefix):], snap_dir / name)
            written += 1
        entry = dict(collected["manifest"].get(kind) or {})
        entry["count"] = written
        entry["written"] = written
        entry["unclassified"] = True
        manifest[kind] = entry
    return {
        "manifest": manifest,
        "missing": {k: v for k, v in collected["missing"].items() if v},
    }


def photo_consistency(
    manifest: Dict[str, Any],
    missing: Dict[str, List[str]],
) -> Dict[str, Any]:
    """照片完整度：库引用了、但**备份创建时源磁盘上就已经没有**的文件。

    这**不是**「这份备份坏了」：照片是逐个 ``_link_or_copy`` 落盘的，拷贝失败会
    直接抛错、快照根本建不出来。能走到这里的缺失说明源文件本来就不在，因此
    ``blocking=False`` —— 恢复数据不该被它拦下（页面上只作警告展示）。
    """
    messages: List[str] = []
    missing_total = 0
    for kind in (PHOTO_STANDARD, PHOTO_OTHER):
        gone = missing.get(kind) or []
        missing_total += len(gone)
        if gone:
            label = (
                CONTENT_LABELS[CONTENT_STANDARD_PHOTOS]
                if kind == PHOTO_STANDARD
                else CONTENT_LABELS[CONTENT_OTHER_PHOTOS]
            )
            messages.append(
                f"{label}有 {len(gone)} 个文件在源磁盘上已缺失（备份里没有，恢复后仍缺）"
            )
    return {
        "ok": not messages,
        "blocking": False,
        "missing_total": missing_total,
        "errors": messages,
        "standard": manifest.get(PHOTO_STANDARD, {}),
        "other": manifest.get(PHOTO_OTHER, {}),
    }


def create_restore_snapshot(
    cred_file_path: str,
    *,
    provenance: str = PROVENANCE_MANUAL,
    include_photos: bool = True,
    keep: Optional[int] = None,
) -> str:
    """创建本机回滚快照（整库 dump + 凭据 + 两类卫生照片），按保留配置清理旧快照。

    ``provenance`` 标注这次快照是谁在什么场景下建的，供清理保护与页面展示识别。
    ``keep`` 为 ``None`` 时使用保留配置中的本机回滚快照份数。

    业务数据只有一条路径：整库 ``pg_dump``（成员 ``app.pgdump``）；配方表在同一个
    库里，因此不再单独导出 ``recipes.db``。这里保持**同步**：更新作业是独立进程
    （无事件循环、不持有 DatabaseManager），升级前必须能建出这份快照。
    """
    ts = datetime.now(CHINA_TZ).strftime("%Y%m%d_%H%M%S")
    snap_dir = _snapshot_root() / ts
    snap_dir.mkdir(parents=True, exist_ok=True)

    contents: List[str] = []
    _pg_dump_sync(str(snap_dir / "app.pgdump"))
    contents.append(CONTENT_APP_PG)
    # 运行配置存在同一个库的 app_settings 表里，随整库 dump 一并带走
    contents.append(CONTENT_RUNTIME)

    if os.path.isfile(cred_file_path):
        dest = snap_dir / "credentials.enc"
        shutil.copy2(cred_file_path, dest)
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
        contents.append(CONTENT_CREDENTIALS)

    key_file_path = Path(cred_file_path).parent / ".cred_key"
    if os.path.isfile(key_file_path):
        dest_key = snap_dir / ".cred_key"
        shutil.copy2(key_file_path, dest_key)
        try:
            os.chmod(dest_key, 0o600)
        except OSError:
            pass

    photo_info: Dict[str, Any] = {
        "manifest": {},
        "missing": {},
    }
    consistency: Optional[dict] = None
    if include_photos:
        photo_info = _write_snapshot_photos_pg(snap_dir)
        manifest = photo_info["manifest"]
        for kind, content_key in (
            (PHOTO_STANDARD, CONTENT_STANDARD_PHOTOS),
            (PHOTO_OTHER, CONTENT_OTHER_PHOTOS),
        ):
            if int(manifest.get(kind, {}).get("written") or 0) > 0:
                contents.append(content_key)
        consistency = photo_consistency(manifest, photo_info["missing"])

    meta = {
        "ts": ts,
        "created_at": datetime.now(CHINA_TZ).isoformat(),
        "provenance": provenance,
        "contents": contents,
        "photos": photo_info["manifest"],
        "files": sorted(
            str(p.relative_to(snap_dir))
            for p in snap_dir.rglob("*")
            if p.is_file()
        ),
    }
    if consistency is not None:
        meta["consistency"] = consistency
    if photo_info["missing"]:
        meta["photos_missing"] = photo_info["missing"]
    (snap_dir / "snapshot_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _prune_old_snapshots(keep=keep)
    logger.info("📸 [审计] 已创建回滚快照 ts=%s 来由=%s", ts, provenance)
    return ts


def _snapshot_keep_default() -> int:
    """快照保留份数的默认值；运行配置不可读时退回同一个常量。"""
    try:
        from services import backup_retention

        return backup_retention.cache_get().snapshot_keep
    except Exception:
        return backup_retention.SNAPSHOT_KEEP_DEFAULT


def _prune_old_snapshots(keep: Optional[int] = None) -> None:
    """按清理计划删除超出保留份数的本机回滚快照（受保护项不动）。"""
    plan = plan_snapshot_cleanup(keep)
    for entry in plan["delete"]:
        shutil.rmtree(_snapshot_root() / entry["ts"], ignore_errors=True)


def _protected_snapshot_names(dirs: List[Path]) -> set:
    """永不自动清理：更新作业前快照 + 最近一份。"""
    protected: set = set()
    if not dirs:
        return protected
    protected.add(dirs[0].name)
    for snap_dir in dirs:
        meta = _read_snapshot_meta(snap_dir)
        if meta.get("provenance") == PROVENANCE_PRE_UPDATE:
            protected.add(snap_dir.name)
    return protected


def _split_by_retention(
    dirs: List[Path],
    limit: int,
    protected_names: set,
) -> tuple:
    """按保留份数把「最新在前」的备份点目录切成 ``(kept, delete)``。

    唯一规则，预览与实际清理共用：从最新往旧遍历，受保护项永远保留；
    非受保护项在总保留数未达 ``keep`` 前保留，其余进入删除集合。受保护项多于
    ``keep`` 时总数会超过 ``keep``——这是「永不自动删」的必然结果，预览会如实列出。
    """
    kept: List[Path] = []
    delete: List[Path] = []
    for entry in dirs:
        if entry.name in protected_names or len(kept) < max(0, limit):
            kept.append(entry)
        else:
            delete.append(entry)
    return kept, delete


def plan_snapshot_cleanup(keep: Optional[int] = None) -> Dict[str, Any]:
    """本机回滚快照的清理计划（预览与实际删除的唯一来源）。"""
    limit = _snapshot_keep_default() if keep is None else int(keep)
    root = _snapshot_root()
    dirs = (
        sorted(
            [d for d in root.iterdir() if d.is_dir()],
            key=lambda p: p.name,
            reverse=True,
        )
        if root.is_dir()
        else []
    )
    protected_names = _protected_snapshot_names(dirs)
    kept, delete = _split_by_retention(dirs, limit, protected_names)

    def _entry(snap_dir: Path, protected: bool) -> Dict[str, Any]:
        meta = _read_snapshot_meta(snap_dir)
        provenance = meta.get("provenance") or PROVENANCE_MANUAL
        entry: Dict[str, Any] = {
            "ts": snap_dir.name,
            "created_at": meta.get("created_at"),
            "size_bytes": _dir_size(snap_dir),
            "provenance": provenance,
            "provenance_label": PROVENANCE_LABELS.get(provenance, "手动"),
            "protected": protected,
        }
        if protected:
            entry["reason"] = (
                "更新前快照，永不自动清理"
                if provenance == PROVENANCE_PRE_UPDATE
                else "最近一份备份点，永不自动清理"
            )
        return entry

    return {
        "keep": max(0, limit),
        "kept": [d.name for d in kept],
        "protected": [_entry(d, True) for d in dirs if d.name in protected_names],
        "delete": [_entry(d, False) for d in delete],
    }


def _read_snapshot_meta(snap_dir: Path) -> dict:
    meta_path = snap_dir / "snapshot_meta.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _dir_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def list_snapshots() -> List[dict]:
    """列出本地快照（时间倒序），带上介质、来由、体积与覆盖内容。"""
    root = _snapshot_root()
    if not root.is_dir():
        return []

    items: List[dict] = []
    for snap_dir in root.iterdir():
        if not snap_dir.is_dir():
            continue
        meta = _read_snapshot_meta(snap_dir)
        files = sorted(
            str(p.relative_to(snap_dir))
            for p in snap_dir.rglob("*")
            if p.is_file()
        )
        size_bytes = _dir_size(snap_dir)
        created_at = datetime.fromtimestamp(
            snap_dir.stat().st_mtime, tz=CHINA_TZ
        ).isoformat()
        created_at = meta.get("created_at", created_at)
        contents = meta.get("contents")
        if contents is None:
            # 旧快照没有覆盖内容清单：按文件推断，缺项留给备份点层标注
            contents = _infer_snapshot_contents(files)
        items.append({
            "ts": snap_dir.name,
            "created_at": created_at,
            "size_bytes": size_bytes,
            "files": files,
            "provenance": meta.get("provenance"),
            "contents": contents,
            "photos": meta.get("photos") or {},
            "photos_missing": meta.get("photos_missing") or {},
            "row_counts": meta.get("row_counts") or {},
            "consistency": meta.get("consistency"),
        })

    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


def _infer_snapshot_contents(files: Sequence[str]) -> List[str]:
    contents: List[str] = []
    names = set(files)
    if "app.db" in names:
        contents.append(CONTENT_APP_DB)
        contents.append(CONTENT_RUNTIME)
    if "app.pgdump" in names:
        contents.append(CONTENT_APP_PG)
        contents.append(CONTENT_RUNTIME)
    if "recipes.db" in names:
        contents.append(CONTENT_RECIPES)
    if "credentials.enc" in names:
        contents.append(CONTENT_CREDENTIALS)
    if any(f.startswith(PHOTO_MEMBER_DIRS[PHOTO_STANDARD] + "/") for f in files):
        contents.append(CONTENT_STANDARD_PHOTOS)
    if any(f.startswith(PHOTO_MEMBER_DIRS[PHOTO_OTHER] + "/") for f in files):
        contents.append(CONTENT_OTHER_PHOTOS)
    return contents


def restore_snapshot_photos(ts: str, kinds: Sequence[str]) -> Dict[str, int]:
    """把快照里的指定照片类别复制回运行实例，返回各类恢复数量。"""
    snap_dir = _snapshot_root() / ts
    root = get_hygiene_capture_root()
    root.mkdir(parents=True, exist_ok=True)
    restored: Dict[str, int] = {}
    for kind in kinds:
        member_dir = PHOTO_MEMBER_DIRS.get(kind)
        if member_dir is None:
            continue
        src_dir = snap_dir / member_dir
        count = 0
        if src_dir.is_dir():
            for src in src_dir.iterdir():
                if not src.is_file():
                    continue
                dst = root / src.name
                try:
                    if not dst.exists():
                        shutil.copy2(src, dst)
                    count += 1
                except OSError:
                    logger.warning("恢复卫生照片失败: %s", src)
        restored[kind] = count
    return restored


# ==================== 导出备份侧车清单 ====================

def export_sidecar_path(archive_path: Path) -> Path:
    return archive_path.with_suffix(archive_path.suffix + ".json")


def write_export_sidecar(archive_path: Path, meta: dict) -> None:
    payload = {
        "name": archive_path.name,
        "size_bytes": archive_path.stat().st_size,
        "archive_sha256": meta.get("archive_sha256"),
        "created_at": meta.get("exported_at"),
        "provenance": meta.get("provenance") or PROVENANCE_MANUAL,
        "meta": meta,
    }
    export_sidecar_path(archive_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_export_sidecar(archive_path: Path) -> Optional[dict]:
    path = export_sidecar_path(archive_path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_export_archives() -> List[dict]:
    """列出本机保留的导出备份（时间倒序）。"""
    root = _export_root()
    if not root.is_dir():
        return []
    items: List[dict] = []
    for archive in root.glob("*.luyunbak"):
        sidecar = read_export_sidecar(archive)
        meta = (sidecar or {}).get("meta") or {}
        created_at = (sidecar or {}).get("created_at") or datetime.fromtimestamp(
            archive.stat().st_mtime, tz=CHINA_TZ
        ).isoformat()
        items.append({
            "path": str(archive),
            "name": archive.name,
            "created_at": created_at,
            "size_bytes": archive.stat().st_size,
            "provenance": (sidecar or {}).get("provenance") or PROVENANCE_MANUAL,
            "archive_sha256": (sidecar or {}).get("archive_sha256"),
            "meta": meta,
            "has_sidecar": sidecar is not None,
        })
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items


# ==================== 冷备归档与状态 ====================

def build_cold_backup_archive(
    *,
    cred_file_path: Optional[str] = None,
    capture_root: Optional[Path] = None,
    app_version: str = "",
    include_standard_photos: bool = True,
    include_other_photos: bool = True,
    runtime_data: Optional[dict] = None,
) -> Tuple[Path, dict]:
    """生成冷备单一归档（库快照 + 凭据 + 密钥 + 卫生照片 + 清单 + 校验和）。

    归档是明文 tar：凭据以 ``credentials.enc`` 形式随附，密钥文件 ``.cred_key``
    也一并归档，因此备份目录权限必须受控。库快照走 ``pg_dump``（成员
    ``app.pgdump``）：它是事务一致的快照，不需要停写窗口；配方表在同一个库里，
    随整库 dump 一起带走。
    """
    cred_file_path = cred_file_path or get_credentials_file_path()
    backup_dir = get_cold_backup_dir()
    ts = datetime.now(CHINA_TZ).strftime("%Y%m%d_%H%M%S")
    out_dir = backup_dir / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / COLD_ARCHIVE_NAME

    fd, tmp_db = tempfile.mkstemp(suffix=".pgdump", prefix="luyun-cold-")
    os.close(fd)
    try:
        _pg_dump_sync(tmp_db)
        with open(tmp_db, "rb") as handle:
            app_db_bytes: Optional[bytes] = handle.read()
    finally:
        try:
            os.unlink(tmp_db)
        except OSError:
            pass

    # 照片按目录全量带走：整库 dump 里虽然有引用关系，但冷备跑在独立进程里，
    # 分类要另开数据库连接，收益不抵复杂度（多带几个孤儿文件比漏带安全）。
    photo_info = _scan_capture_members(capture_root or get_hygiene_capture_root())
    members: Dict[str, bytes] = {}
    if app_db_bytes is not None:
        members["app.pgdump"] = app_db_bytes
    if os.path.isfile(cred_file_path):
        with open(cred_file_path, "rb") as handle:
            members["credentials.enc"] = handle.read()
    key_file = Path(cred_file_path).parent / ".cred_key"
    if key_file.is_file():
        with open(key_file, "rb") as handle:
            members[".cred_key"] = handle.read()
    if runtime_data is not None:
        members["runtime.json"] = json.dumps(
            runtime_data, ensure_ascii=False
        ).encode("utf-8")
    for name, data in photo_info["members"].items():
        members[name] = data

    # 一致性核对要从库里查照片引用，这里不做（整库 pg_dump + 全量照片目录，
    # 不存在「库引用了但没备份」的情况）。
    consistency = None
    raw_contents = [
        content
        for content, present in (
            (CONTENT_APP_PG, app_db_bytes is not None),
            # 运行配置存在同一个库的 app_settings 表里，随整库 dump 一并带走
            (CONTENT_RUNTIME, app_db_bytes is not None or runtime_data is not None),
            (CONTENT_CREDENTIALS, "credentials.enc" in members),
            (
                CONTENT_STANDARD_PHOTOS,
                bool(photo_info["manifest"].get(PHOTO_STANDARD, {}).get("count")),
            ),
            (
                CONTENT_OTHER_PHOTOS,
                bool(photo_info["manifest"].get(PHOTO_OTHER, {}).get("count")),
            ),
        )
        if present
    ]
    manifest = {
        "version": BACKUP_VERSION,
        "created_at": datetime.now(CHINA_TZ).isoformat(),
        "app_version": app_version,
        "provenance": PROVENANCE_MANUAL,
        "kind": "cold_backup",
        "contents": raw_contents,
        "contents_labels": [CONTENT_LABELS[c] for c in raw_contents],
        "photos": photo_info["manifest"],
        "photos_missing": {
            k: v for k, v in photo_info["missing"].items() if v
        },
        "consistency": consistency,
    }

    checksums = {
        name: _sha256_hex(data)
        for name, data in members.items()
    }
    members[COLD_MANIFEST_NAME] = json.dumps(
        manifest, ensure_ascii=False, indent=2
    ).encode("utf-8")
    checksum_lines = [
        f"{digest}  {name}" for name, digest in sorted(checksums.items())
    ]
    members[COLD_CHECKSUMS_NAME] = ("\n".join(checksum_lines) + "\n").encode("utf-8")

    tmp_archive = out_dir / f".{COLD_ARCHIVE_NAME}.tmp"
    with tarfile.open(tmp_archive, mode="w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    os.replace(tmp_archive, archive_path)

    manifest["archive"] = str(archive_path)
    manifest["archive_bytes"] = archive_path.stat().st_size
    manifest["archive_sha256"] = sha256_file(archive_path)
    return archive_path, manifest


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_cold_backup_status(
    *,
    ok: bool,
    archive: Optional[Path],
    error: Optional[str] = None,
    manifest: Optional[dict] = None,
    backup_dir: Optional[Path] = None,
) -> dict:
    """冷备任务每次运行写出的状态文件（时间、结果、归档名、体积、校验结论）。"""
    target_dir = backup_dir or get_cold_backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest or {}
    status = {
        "ran_at": datetime.now(CHINA_TZ).isoformat(),
        "ok": bool(ok),
        "archive": str(archive) if archive else None,
        "archive_name": Path(archive).name if archive else None,
        "ts": Path(archive).parent.name if archive else None,
        "size_bytes": manifest.get("archive_bytes"),
        "sha256": manifest.get("archive_sha256"),
        "checksum_ok": bool(ok and archive and manifest.get("archive_sha256")),
        "contents": manifest.get("contents") or [],
        "consistency": manifest.get("consistency"),
        "error": error,
    }
    cold_status_path(target_dir).write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return status


def read_cold_backup_status(backup_dir: Optional[Path] = None) -> Optional[dict]:
    path = cold_status_path(backup_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cold_run_dirs(backup_dir: Optional[Path] = None) -> List[Path]:
    root = backup_dir or get_cold_backup_dir()
    if not root.is_dir():
        return []
    return sorted(
        [
            d
            for d in root.iterdir()
            if d.is_dir() and re.fullmatch(r"\d{8}_\d{6}", d.name or "")
        ],
        key=lambda p: p.name,
        reverse=True,
    )


def plan_cold_cleanup(
    keep: int,
    backup_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """冷备的清理计划（预览与实际删除的唯一来源）；最近一份永不自动删。"""
    limit = max(1, int(keep))
    dirs = _cold_run_dirs(backup_dir)
    protected_names = {dirs[0].name} if dirs else set()
    kept, delete = _split_by_retention(dirs, limit, protected_names)

    def _entry(run_dir: Path, is_protected: bool) -> Dict[str, Any]:
        archive = run_dir / COLD_ARCHIVE_NAME
        entry: Dict[str, Any] = {
            "ts": run_dir.name,
            "created_at": None,
            "size_bytes": archive.stat().st_size if archive.is_file() else _dir_size(run_dir),
            "path": str(archive) if archive.is_file() else str(run_dir),
            "protected": is_protected,
        }
        if is_protected:
            entry["reason"] = "最近一份备份点，永不自动清理"
        return entry

    return {
        "keep": limit,
        "kept": [d.name for d in kept],
        "protected": [_entry(d, True) for d in dirs if d.name in protected_names],
        "delete": [_entry(d, False) for d in delete],
    }


def plan_export_cleanup(
    keep: int,
    backup_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """导出备份（本机保留的副本）的清理计划；最近一份永不自动删。"""
    limit = max(1, int(keep))
    items = list_export_archives()
    protected = items[:1]
    delete = items[limit:]

    def _entry(item: dict, is_protected: bool) -> Dict[str, Any]:
        entry: Dict[str, Any] = {
            "name": item["name"],
            "created_at": item.get("created_at"),
            "size_bytes": item.get("size_bytes") or 0,
            "path": item["path"],
            "protected": is_protected,
        }
        if is_protected:
            entry["reason"] = "最近一份备份点，永不自动清理"
        return entry

    delete_names = {entry["name"] for entry in delete}
    return {
        "keep": limit,
        "kept": [item["name"] for item in items if item["name"] not in delete_names],
        "protected": [_entry(item, True) for item in protected],
        "delete": [_entry(item, False) for item in delete],
    }


def prune_export_backups(keep: int) -> List[str]:
    """删除超出保留份数的本机导出备份副本（连同侧车清单）。"""
    deleted: List[str] = []
    for entry in plan_export_cleanup(keep)["delete"]:
        path = Path(entry["path"])
        path.unlink(missing_ok=True)
        export_sidecar_path(path).unlink(missing_ok=True)
        deleted.append(entry["name"])
    return deleted


def prune_cold_backups(
    keep: int,
    backup_dir: Optional[Path] = None,
) -> List[str]:
    """按清理计划删除超出保留份数的冷备，返回被删除的时间戳。"""
    plan = plan_cold_cleanup(keep, backup_dir)
    root = backup_dir or get_cold_backup_dir()
    for entry in plan["delete"]:
        shutil.rmtree(root / entry["ts"], ignore_errors=True)
    return [entry["ts"] for entry in plan["delete"]]


def scan_cold_backup_dirs(backup_dir: Optional[Path] = None) -> List[dict]:
    """状态文件缺失时退回目录扫描，让旧部署也有可见状态。"""
    root = backup_dir or get_cold_backup_dir()
    if not root.is_dir():
        return []
    items: List[dict] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        archive = entry / COLD_ARCHIVE_NAME
        legacy_files = sorted(
            p.name for p in entry.iterdir() if p.is_file()
        )
        if archive.is_file():
            items.append({
                "ts": entry.name,
                "archive": str(archive),
                "size_bytes": archive.stat().st_size,
                "legacy": False,
                "contents": [CONTENT_APP_PG],
            })
        elif legacy_files:
            items.append({
                "ts": entry.name,
                "archive": str(entry),
                "size_bytes": _dir_size(entry),
                "legacy": True,
                "files": legacy_files,
                # contents 是内容代码（其它分支都填 CONTENT_APP_PG）；填中文标签会让
                # 「缺项」与健康覆盖结论判定失真。
                "contents": [CONTENT_APP_PG],
            })
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items

