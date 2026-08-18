#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统完整备份 v2：口令加密 tar 归档、快照回滚、数据库覆盖/合并。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import struct
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken

from config import settings
from db_core.schema import ALL_TABLES
from services import credentials_store
from services.credentials_store import CHINA_TZ, _derive_backup_key

logger = logging.getLogger(__name__)

# —— v2 备份格式 ——
BACKUP_MAGIC = b"LUYUNBK2"
BACKUP_VERSION = 2
SNAPSHOT_DIRNAME = "restore_snapshots"
SNAPSHOT_KEEP = 5

# auth 在 ALL_TABLES 里是虚拟项，实际表名如下
AUTH_PHYSICAL_TABLES = ("admin_user", "sessions", "api_tokens")
RECIPE_TABLES = ("sop_stations", "sop_recipes", "sop_recipes_history")

# 每张表的去重键（与 api/admin.py 导入逻辑一致）
TABLE_DEDUP_KEY: Dict[str, str] = {
    "orders": "business_flow_id",
    "dish_stations": "dish_name",
    "semi_finished_rules": "dish_name",
    "report_dishes": "dish_name",
    "tables": "table_number",
    "stations": "station_id",
}


def _snapshot_root() -> Path:
    return Path(settings.DATABASE_DIR) / SNAPSHOT_DIRNAME


def get_recipes_db_path() -> str:
    """与 RecipeStore 一致：RECIPES_DB_PATH 环境变量 > APP_DB_PATH。"""
    return os.environ.get("RECIPES_DB_PATH") or settings.APP_DB_PATH


def get_credentials_file_path() -> str:
    return os.path.join(settings.DATABASE_DIR, "credentials.enc")


def _app_db_target_tables() -> List[str]:
    """覆盖/合并 app.db 时涉及的真实表名（排除 logs 与虚拟 auth）。"""
    tables = [t for t in ALL_TABLES if t not in ("logs", "auth")]
    tables.extend(AUTH_PHYSICAL_TABLES)
    return tables


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_tar_member(tar: tarfile.TarFile, name: str) -> bytes:
    member = tar.getmember(name)
    extracted = tar.extractfile(member)
    if extracted is None:
        raise ValueError("备份校验失败（文件可能被篡改）")
    return extracted.read()


# ==================== v2 加密归档 ====================

def build_backup(
    passphrase: str,
    *,
    include_runtime: bool,
    runtime_data: Optional[dict],
    include_app_db: bool,
    app_db_bytes: Optional[bytes],
    include_recipes: bool,
    recipes_db_bytes: Optional[bytes],
    app_version: str,
) -> bytes:
    """构建 v2 口令加密备份二进制包。"""
    passphrase = (passphrase or "").strip()
    if len(passphrase) < credentials_store.BACKUP_PASSPHRASE_MIN_LENGTH:
        raise ValueError(f"导出口令至少 {credentials_store.BACKUP_PASSPHRASE_MIN_LENGTH} 位")

    bundle = credentials_store.get_credentials()
    if bundle is None:
        raise ValueError("当前未配置凭据")

    credentials_bytes = json.dumps(
        bundle.to_storage(), ensure_ascii=False
    ).encode("utf-8")

    members: Dict[str, bytes] = {"credentials.json": credentials_bytes}
    includes = {"runtime": False, "app_db": False, "recipes_db": False}

    if include_runtime and runtime_data is not None:
        members["runtime.json"] = json.dumps(
            runtime_data, ensure_ascii=False
        ).encode("utf-8")
        includes["runtime"] = True

    if include_app_db and app_db_bytes:
        members["app.db"] = app_db_bytes
        includes["app_db"] = True

    if include_recipes and recipes_db_bytes:
        members["recipes.db"] = recipes_db_bytes
        includes["recipes_db"] = True

    sha256_map = {
        name: _sha256_hex(data)
        for name, data in members.items()
        if name != "meta.json"
    }

    meta = {
        "version": BACKUP_VERSION,
        "exported_at": datetime.now(CHINA_TZ).isoformat(),
        "app_version": app_version,
        "includes": includes,
        "sha256": sha256_map,
    }
    members["meta.json"] = json.dumps(meta, ensure_ascii=False).encode("utf-8")

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    tar_bytes = tar_buffer.getvalue()

    salt = os.urandom(credentials_store.BACKUP_SALT_BYTES)
    iterations = credentials_store.BACKUP_KDF_ITERATIONS
    key = _derive_backup_key(passphrase, salt, iterations)
    token = Fernet(key).encrypt(tar_bytes)

    header_obj = {
        "salt": base64.b64encode(salt).decode("ascii"),
        "iterations": iterations,
        "created_at": datetime.now(CHINA_TZ).isoformat(),
    }
    header_bytes = json.dumps(header_obj, ensure_ascii=False).encode("utf-8")

    return (
        BACKUP_MAGIC
        + struct.pack(">I", len(header_bytes))
        + header_bytes
        + token
    )


def parse_backup(blob: bytes, passphrase: str) -> dict:
    """解密并校验 v2 备份包，返回各成员内容。"""
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

        credentials_bytes = _read_tar_member(tar, "credentials.json")
        if expected_sha.get("credentials.json") != _sha256_hex(credentials_bytes):
            raise ValueError("备份校验失败（文件可能被篡改）")

        runtime_data = None
        if "runtime.json" in tar.getnames():
            runtime_bytes = _read_tar_member(tar, "runtime.json")
            if expected_sha.get("runtime.json") != _sha256_hex(runtime_bytes):
                raise ValueError("备份校验失败（文件可能被篡改）")
            runtime_data = json.loads(runtime_bytes.decode("utf-8"))

        app_db_bytes = None
        if "app.db" in tar.getnames():
            app_db_bytes = _read_tar_member(tar, "app.db")
            if expected_sha.get("app.db") != _sha256_hex(app_db_bytes):
                raise ValueError("备份校验失败（文件可能被篡改）")

        recipes_db_bytes = None
        if "recipes.db" in tar.getnames():
            recipes_db_bytes = _read_tar_member(tar, "recipes.db")
            if expected_sha.get("recipes.db") != _sha256_hex(recipes_db_bytes):
                raise ValueError("备份校验失败（文件可能被篡改）")

    credentials = json.loads(credentials_bytes.decode("utf-8"))
    return {
        "meta": meta,
        "credentials": credentials,
        "runtime": runtime_data,
        "app_db_bytes": app_db_bytes,
        "recipes_db_bytes": recipes_db_bytes,
    }


# ==================== 快照（明文回滚点）====================

def _sqlite_backup_sync(src_path: str, dst_path: str) -> None:
    """同步 sqlite3 backup API 复制整库。"""
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    if os.path.exists(dst_path):
        os.unlink(dst_path)
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def create_restore_snapshot(
    app_db_path: str,
    recipes_db_path: str,
    cred_file_path: str,
) -> str:
    """创建本地明文回滚快照，保留最新 SNAPSHOT_KEEP 份。"""
    ts = datetime.now(CHINA_TZ).strftime("%Y%m%d_%H%M%S")
    snap_dir = _snapshot_root() / ts
    snap_dir.mkdir(parents=True, exist_ok=True)

    if os.path.isfile(app_db_path):
        _sqlite_backup_sync(app_db_path, str(snap_dir / "app.db"))

    if (
        os.path.isfile(recipes_db_path)
        and os.path.abspath(recipes_db_path) != os.path.abspath(app_db_path)
    ):
        _sqlite_backup_sync(recipes_db_path, str(snap_dir / "recipes.db"))

    if os.path.isfile(cred_file_path):
        dest = snap_dir / "credentials.enc"
        shutil.copy2(cred_file_path, dest)
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass

    key_file_path = Path(cred_file_path).parent / ".cred_key"
    if os.path.isfile(key_file_path):
        dest_key = snap_dir / ".cred_key"
        shutil.copy2(key_file_path, dest_key)
        try:
            os.chmod(dest_key, 0o600)
        except OSError:
            pass

    meta = {
        "ts": ts,
        "created_at": datetime.now(CHINA_TZ).isoformat(),
        "files": [p.name for p in snap_dir.iterdir() if p.is_file()],
    }
    (snap_dir / "snapshot_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _prune_old_snapshots()
    logger.info("📸 [审计] 已创建回滚快照 ts=%s", ts)
    return ts


def _prune_old_snapshots() -> None:
    root = _snapshot_root()
    if not root.is_dir():
        return
    dirs = sorted(
        [d for d in root.iterdir() if d.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )
    for old in dirs[SNAPSHOT_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)


def list_snapshots() -> List[dict]:
    """列出本地快照（时间倒序）。"""
    root = _snapshot_root()
    if not root.is_dir():
        return []

    items: List[dict] = []
    for snap_dir in root.iterdir():
        if not snap_dir.is_dir():
            continue
        files = [p.name for p in snap_dir.iterdir() if p.is_file()]
        size_bytes = sum(
            p.stat().st_size for p in snap_dir.iterdir() if p.is_file()
        )
        created_at = datetime.fromtimestamp(
            snap_dir.stat().st_mtime, tz=CHINA_TZ
        ).isoformat()
        meta_path = snap_dir / "snapshot_meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                created_at = meta.get("created_at", created_at)
            except Exception:
                pass
        items.append({
            "ts": snap_dir.name,
            "created_at": created_at,
            "size_bytes": size_bytes,
            "files": sorted(files),
        })

    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


def restore_from_snapshot(ts: str) -> None:
    """从指定快照恢复 app.db / recipes.db / credentials.enc（文件级 + 在线库需另行 ATTACH）。"""
    import re

    if not re.fullmatch(r"\d{8}_\d{6}", ts):
        raise FileNotFoundError(ts)

    snap_dir = _snapshot_root() / ts
    if not snap_dir.is_dir():
        raise FileNotFoundError(ts)

    app_db_path = settings.APP_DB_PATH
    recipes_db_path = get_recipes_db_path()
    cred_file_path = get_credentials_file_path()

    snap_app = snap_dir / "app.db"
    if snap_app.is_file():
        _sqlite_backup_sync(str(snap_app), app_db_path)

    snap_recipes = snap_dir / "recipes.db"
    if snap_recipes.is_file():
        _sqlite_backup_sync(str(snap_recipes), recipes_db_path)

    snap_cred = snap_dir / "credentials.enc"
    if snap_cred.is_file():
        os.makedirs(os.path.dirname(cred_file_path), exist_ok=True)
        shutil.copy2(str(snap_cred), cred_file_path)
        try:
            os.chmod(cred_file_path, 0o600)
        except OSError:
            pass

    logger.info("⏪ [审计] 已从快照回滚 ts=%s", ts)


# ==================== app.db 覆盖 / 合并 ====================

async def _write_temp_db(db_bytes: bytes) -> str:
    fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="luyun-bak-")
    os.close(fd)
    with open(tmp_path, "wb") as f:
        f.write(db_bytes)
    return tmp_path


async def _tables_in_attached_db(conn, alias: str) -> set[str]:
    cursor = await conn.execute(
        f"SELECT name FROM {alias}.sqlite_master WHERE type='table'"
    )
    rows = await cursor.fetchall()
    return {row[0] for row in rows}


async def _copy_table_overwrite(conn, table: str, alias: str = "src") -> None:
    """逐表覆盖：只复制两边都有的列，避免 schema 差异导致 INSERT SELECT * 失败。"""
    cursor = await conn.execute(f"PRAGMA {alias}.table_info({table})")
    src_cols = [row[1] for row in await cursor.fetchall()]
    if not src_cols:
        return

    cursor = await conn.execute(f"PRAGMA main.table_info({table})")
    main_cols = [row[1] for row in await cursor.fetchall()]
    common_cols = [c for c in src_cols if c in main_cols]
    if not common_cols:
        return

    cols_str = ", ".join(common_cols)
    await conn.execute(f"DELETE FROM main.{table}")
    await conn.execute(
        f"INSERT INTO main.{table} ({cols_str}) SELECT {cols_str} FROM {alias}.{table}"
    )


async def overwrite_app_db_from_bytes(db, app_db_bytes: bytes) -> None:
    """在存活连接上逐表替换 app.db 数据（ATTACH 临时源库）。"""
    tmp_path = await _write_temp_db(app_db_bytes)
    target_tables = set(_app_db_target_tables())
    try:
        escaped = tmp_path.replace("'", "''")
        await db._conn.execute(f"ATTACH DATABASE '{escaped}' AS src")
        src_tables = await _tables_in_attached_db(db._conn, "src")
        tables_to_copy = sorted(target_tables & src_tables)

        await db._conn.execute("BEGIN")
        try:
            for table in tables_to_copy:
                await _copy_table_overwrite(db._conn, table, alias="src")
            await db._conn.commit()
        except Exception:
            await db._conn.rollback()
            raise
        finally:
            await db._conn.execute("DETACH DATABASE src")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def merge_app_db_from_file(
    db,
    src_db_path: str,
    tables: Optional[Sequence[str]] = None,
) -> dict:
    """
    按唯一键去重合并源 .db 文件到当前库（与 admin 导入 execute 行为一致）。
    返回 {total_imported, results}。
    """
    want_tables = list(tables) if tables else list(ALL_TABLES)
    want_tables = [t for t in want_tables if t in ALL_TABLES]

    src_conn = await aiosqlite.connect(src_db_path)
    src_conn.row_factory = aiosqlite.Row

    results: List[dict] = []
    try:
        for table in want_tables:
            dedup_key = TABLE_DEDUP_KEY.get(table)

            async with src_conn.execute(f"PRAGMA table_info({table})") as cur:
                rows = await cur.fetchall()
                if not rows:
                    results.append({"table": table, "status": "表不存在", "imported": 0})
                    continue
                src_cols = [r[1] for r in rows]

            dst_tdb = db.table_or_none(table)
            if dst_tdb is None:
                results.append({"table": table, "status": "目标表不可用", "imported": 0})
                continue

            async with dst_tdb.conn.cursor() as cur:
                await cur.execute(f"PRAGMA table_info({table})")
                rows = await cur.fetchall()
                dst_cols = [r[1] for r in rows]

            common_cols = [c for c in src_cols if c in dst_cols and c != "id"]
            if not common_cols:
                results.append({"table": table, "status": "无匹配列", "imported": 0})
                continue

            existing_keys: set = set()
            if dedup_key and dedup_key in common_cols:
                async with dst_tdb.conn.cursor() as cur:
                    await cur.execute(f"SELECT {dedup_key} FROM {table}")
                    rows = await cur.fetchall()
                    existing_keys = {r[0] for r in rows if r[0]}

            imported = 0
            cols_str = ", ".join(common_cols)
            placeholders = ", ".join(["?"] * len(common_cols))
            insert_sql = (
                f"INSERT OR IGNORE INTO {table} ({cols_str}) VALUES ({placeholders})"
            )

            async with src_conn.execute(f"SELECT {cols_str} FROM {table}") as src_cur:
                async for row in src_cur:
                    key_val = (
                        row[common_cols.index(dedup_key)]
                        if dedup_key and dedup_key in common_cols
                        else None
                    )
                    if key_val is not None and key_val in existing_keys:
                        continue
                    try:
                        async with dst_tdb.conn.cursor() as dst_cur:
                            await dst_cur.execute(insert_sql, row)
                        imported += 1
                        if key_val is not None:
                            existing_keys.add(key_val)
                    except Exception:
                        pass

            await dst_tdb.commit()
            results.append({"table": table, "status": "OK", "imported": imported})
    finally:
        await src_conn.close()

    total_imported = sum(r.get("imported", 0) for r in results)
    return {"total_imported": total_imported, "results": results}


async def merge_app_db_from_bytes(db, app_db_bytes: bytes) -> dict:
    tmp_path = await _write_temp_db(app_db_bytes)
    try:
        return await merge_app_db_from_file(db, tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ==================== recipes 覆盖 / 合并 ====================

async def overwrite_recipes_from_bytes(recipe_store, recipes_db_bytes: bytes) -> None:
    """在 RecipeStore 连接上逐表替换配方数据。"""
    tmp_path = await _write_temp_db(recipes_db_bytes)
    conn = recipe_store.conn
    try:
        escaped = tmp_path.replace("'", "''")
        await conn.execute(f"ATTACH DATABASE '{escaped}' AS src")
        src_tables = await _tables_in_attached_db(conn, "src")
        tables_to_copy = [t for t in RECIPE_TABLES if t in src_tables]

        await conn.execute("BEGIN")
        try:
            for table in tables_to_copy:
                await _copy_table_overwrite(conn, table, alias="src")
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.execute("DETACH DATABASE src")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def merge_recipes_from_bytes(recipe_store, recipes_db_bytes: bytes) -> dict:
    """配方库合并：有则按主键/唯一键跳过重复，无表则跳过。"""
    tmp_path = await _write_temp_db(recipes_db_bytes)
    src_conn = await aiosqlite.connect(tmp_path)
    src_conn.row_factory = aiosqlite.Row
    conn = recipe_store.conn
    imported_total = 0
    results: List[dict] = []

    recipe_dedup = {
        "sop_stations": "slug",
        "sop_recipes": None,
        "sop_recipes_history": None,
    }

    try:
        for table in RECIPE_TABLES:
            async with src_conn.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ) as cur:
                if not await cur.fetchone():
                    results.append({"table": table, "status": "表不存在", "imported": 0})
                    continue

            async with src_conn.execute(f"PRAGMA table_info({table})") as cur:
                src_cols = [r[1] for r in await cur.fetchall()]

            async with conn.execute(f"PRAGMA table_info({table})") as cur:
                dst_cols = [r[1] for r in await cur.fetchall()]

            common_cols = [c for c in src_cols if c in dst_cols and c != "id"]
            if not common_cols:
                results.append({"table": table, "status": "无匹配列", "imported": 0})
                continue

            dedup_key = recipe_dedup.get(table)
            existing_keys: set = set()
            if dedup_key and dedup_key in common_cols:
                async with conn.execute(f"SELECT {dedup_key} FROM {table}") as cur:
                    rows = await cur.fetchall()
                    existing_keys = {r[0] for r in rows if r[0]}

            imported = 0
            cols_str = ", ".join(common_cols)
            placeholders = ", ".join(["?"] * len(common_cols))
            insert_sql = (
                f"INSERT OR IGNORE INTO {table} ({cols_str}) VALUES ({placeholders})"
            )

            async with src_conn.execute(f"SELECT {cols_str} FROM {table}") as src_cur:
                async for row in src_cur:
                    key_val = (
                        row[common_cols.index(dedup_key)]
                        if dedup_key and dedup_key in common_cols
                        else None
                    )
                    if key_val is not None and key_val in existing_keys:
                        continue
                    try:
                        await conn.execute(insert_sql, tuple(row))
                        imported += 1
                        if key_val is not None:
                            existing_keys.add(key_val)
                    except Exception:
                        pass

            await conn.commit()
            imported_total += imported
            results.append({"table": table, "status": "OK", "imported": imported})
    finally:
        await src_conn.close()
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return {"total_imported": imported_total, "results": results}


def export_recipes_db_bytes(recipes_db_path: str) -> Optional[bytes]:
    """把「仅配方表」导出为一个精简 sqlite 库的字节串。

    配方表现与业务表同库存放于 app.db，因此不能直接整库拷贝（否则 recipes 成员会
    是 app.db 的完整副本，与 app.db 成员重复，备份体积翻倍）。这里只把 RECIPE_TABLES
    的表结构与数据复制进一个新建的临时库，恢复逻辑（overwrite/merge_recipes）只读这些表，
    行为不变。源库不含任何配方表时返回 None。
    """
    if not os.path.isfile(recipes_db_path):
        return None

    src = sqlite3.connect(recipes_db_path)
    try:
        placeholders = ", ".join(["?"] * len(RECIPE_TABLES))
        existing_rows = src.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
            RECIPE_TABLES,
        ).fetchall()
        existing = {row[0] for row in existing_rows}
        if not existing:
            return None

        fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="luyun-recipes-export-")
        os.close(fd)
        dst = sqlite3.connect(tmp_path)
        try:
            for table in RECIPE_TABLES:
                if table not in existing:
                    continue
                ddl_row = src.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not ddl_row or not ddl_row[0]:
                    continue
                dst.execute(ddl_row[0])

                col_rows = src.execute(f"PRAGMA table_info({table})").fetchall()
                col_names = [c[1] for c in col_rows]
                if not col_names:
                    continue
                cols_str = ", ".join(col_names)
                data_rows = src.execute(f"SELECT {cols_str} FROM {table}").fetchall()
                if data_rows:
                    row_placeholders = ", ".join(["?"] * len(col_names))
                    dst.executemany(
                        f"INSERT INTO {table} ({cols_str}) VALUES ({row_placeholders})",
                        data_rows,
                    )
            dst.commit()
        finally:
            dst.close()

        try:
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    finally:
        src.close()
