#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份导入暂存：preview 解密后落盘，apply 通过 token 一次性消费。"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from config import settings
from services.credentials_store import CHINA_TZ

logger = logging.getLogger(__name__)

STAGING_DIRNAME = ".import-staging"
STAGING_META_FILENAME = "staging_meta.json"
BACKUP_META_FILENAME = "meta.json"
CREDENTIALS_FILENAME = "credentials.json"
RUNTIME_FILENAME = "runtime.json"
APP_DB_FILENAME = "app.db"
RECIPES_DB_FILENAME = "recipes.db"
TTL_SECONDS = 15 * 60


def _staging_root() -> Path:
    return Path(settings.DATABASE_DIR) / STAGING_DIRNAME


def _parse_iso_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _write_private_bytes(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _write_private_json(path: Path, payload: dict) -> None:
    _write_private_bytes(path, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_valid_token(token: str) -> bool:
    if not token or len(token) > 128:
        return False
    return all(ch.isalnum() or ch in "-_" for ch in token)


def _remove_dir(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _iter_staging_dirs() -> list[Path]:
    root = _staging_root()
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if p.is_dir()]


def cleanup_expired_staging() -> int:
    """删除已过期的暂存目录，返回清理数量。"""
    removed = 0
    now = datetime.now(CHINA_TZ)
    for staging_dir in _iter_staging_dirs():
        meta_path = staging_dir / STAGING_META_FILENAME
        if not meta_path.is_file():
            _remove_dir(staging_dir)
            removed += 1
            continue
        try:
            meta = _read_json(meta_path)
            expires_at = _parse_iso_ts(meta["expires_at"])
        except Exception:
            _remove_dir(staging_dir)
            removed += 1
            continue
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=CHINA_TZ)
        if expires_at <= now:
            _remove_dir(staging_dir)
            removed += 1
    if removed:
        logger.info("🧹 已清理 %s 个过期备份导入暂存", removed)
    return removed


def _remove_staging_for_owner(owner: str) -> None:
    for staging_dir in _iter_staging_dirs():
        meta_path = staging_dir / STAGING_META_FILENAME
        if not meta_path.is_file():
            continue
        try:
            meta = _read_json(meta_path)
        except Exception:
            continue
        if meta.get("owner") == owner:
            _remove_dir(staging_dir)


def _staging_dir_for_token(token: str) -> Path:
    return _staging_root() / token


def create_staging(owner: str, parsed: dict) -> str:
    """写入解密后的备份成员，返回 import token。同一 owner 只保留最新一份。"""
    cleanup_expired_staging()
    _remove_staging_for_owner(owner)

    root = _staging_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass

    token = secrets.token_urlsafe(32)
    staging_dir = _staging_dir_for_token(token)
    staging_dir.mkdir(parents=True, exist_ok=False)
    try:
        os.chmod(staging_dir, 0o700)
    except OSError:
        pass

    now = datetime.now(CHINA_TZ)
    expires_at = now + timedelta(seconds=TTL_SECONDS)
    _write_private_json(
        staging_dir / STAGING_META_FILENAME,
        {
            "token": token,
            "owner": owner,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        },
    )
    _write_private_json(staging_dir / BACKUP_META_FILENAME, parsed["meta"])
    _write_private_json(staging_dir / CREDENTIALS_FILENAME, parsed["credentials"])

    runtime = parsed.get("runtime")
    if runtime is not None:
        _write_private_json(staging_dir / RUNTIME_FILENAME, runtime)

    app_db_bytes = parsed.get("app_db_bytes")
    if app_db_bytes:
        _write_private_bytes(staging_dir / APP_DB_FILENAME, app_db_bytes)

    recipes_db_bytes = parsed.get("recipes_db_bytes")
    if recipes_db_bytes:
        _write_private_bytes(staging_dir / RECIPES_DB_FILENAME, recipes_db_bytes)

    return token


def _load_staging_meta(staging_dir: Path) -> dict:
    meta_path = staging_dir / STAGING_META_FILENAME
    if not meta_path.is_file():
        raise FileNotFoundError("导入暂存不存在")
    return _read_json(meta_path)


def _assert_staging_access(staging_dir: Path, owner: str) -> dict:
    if not staging_dir.is_dir():
        raise FileNotFoundError("导入暂存不存在")

    staging_meta = _load_staging_meta(staging_dir)
    if staging_meta.get("owner") != owner:
        raise PermissionError("导入暂存与当前会话不匹配")

    expires_at = _parse_iso_ts(staging_meta["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=CHINA_TZ)
    if expires_at <= datetime.now(CHINA_TZ):
        _remove_dir(staging_dir)
        raise TimeoutError("预览已过期，请重新上传备份文件")

    return staging_meta


def load_parsed_from_staging(token: str, owner: str) -> dict:
    """读取暂存并还原为 parse_backup 同构 dict（不消费）。"""
    if not _is_valid_token(token):
        raise FileNotFoundError("导入暂存不存在")

    staging_dir = _staging_dir_for_token(token)
    _assert_staging_access(staging_dir, owner)

    meta_path = staging_dir / BACKUP_META_FILENAME
    cred_path = staging_dir / CREDENTIALS_FILENAME
    if not meta_path.is_file() or not cred_path.is_file():
        raise FileNotFoundError("导入暂存不完整")

    runtime_data = None
    runtime_path = staging_dir / RUNTIME_FILENAME
    if runtime_path.is_file():
        runtime_data = _read_json(runtime_path)

    app_db_bytes = None
    app_db_path = staging_dir / APP_DB_FILENAME
    if app_db_path.is_file():
        app_db_bytes = app_db_path.read_bytes()

    recipes_db_bytes = None
    recipes_db_path = staging_dir / RECIPES_DB_FILENAME
    if recipes_db_path.is_file():
        recipes_db_bytes = recipes_db_path.read_bytes()

    return {
        "meta": _read_json(meta_path),
        "credentials": _read_json(cred_path),
        "runtime": runtime_data,
        "app_db_bytes": app_db_bytes,
        "recipes_db_bytes": recipes_db_bytes,
    }


def consume_staging(token: str, owner: str) -> dict:
    """读取暂存、删除目录并返回 parsed dict（一次性 token）。"""
    parsed = load_parsed_from_staging(token, owner)
    _remove_dir(_staging_dir_for_token(token))
    return parsed
