#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享账号、Session、API Token 存储与校验。"""

from __future__ import annotations

import bcrypt
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from config import settings
from database import CHINA_TZ, DatabaseManager

logger = logging.getLogger(__name__)
BCRYPT_ROUNDS = 12


def _db() -> DatabaseManager:
    from services.app_runtime import get_runtime

    runtime = get_runtime()
    if runtime is None or runtime.db is None:
        raise RuntimeError("auth_service db not initialized")
    return runtime.db


def _conn():
    return _db().table("auth").conn


def _now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat()


def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def _password_digest(password: str) -> str:
    """SHA-256 后再 bcrypt，避免 bcrypt 72 字节上限。"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _validate_password(password: str) -> None:
    if len(password) < settings.AUTH_MIN_PASSWORD_LENGTH:
        raise ValueError("password_too_short")
    if len(password.encode("utf-8")) > settings.AUTH_MAX_PASSWORD_BYTES:
        raise ValueError("password_too_long")


def _hash_password(password: str) -> str:
    _validate_password(password)
    digest = _password_digest(password).encode("utf-8")
    hashed = bcrypt.hashpw(digest, bcrypt.gensalt(rounds=BCRYPT_ROUNDS))
    return hashed.decode("utf-8")


def _verify_password(password: str, stored_hash: str) -> bool:
    # 兼容旧版 passlib/bcrypt(明文密码)；新版为 bcrypt(sha256(password))
    stored = stored_hash.encode("utf-8")
    for candidate in (_password_digest(password).encode("utf-8"), password.encode("utf-8")):
        try:
            if bcrypt.checkpw(candidate, stored):
                return True
        except (ValueError, TypeError):
            continue
    return False


def validate_password(password: str) -> None:
    """校验密码长度（供 API 层复用）。"""
    _validate_password(password)


async def is_initialized() -> bool:
    async with _conn().cursor() as cursor:
        await cursor.execute("SELECT 1 FROM admin_user WHERE id = 1")
        row = await cursor.fetchone()
    return row is not None


async def init_user(username: str, password: str) -> None:
    if await is_initialized():
        raise ValueError("already_initialized")
    _validate_password(password)
    now = _now_iso()
    password_hash = _hash_password(password)
    async with _conn().cursor() as cursor:
        await cursor.execute(
            """INSERT INTO admin_user (id, username, password_hash, created_at, updated_at)
               VALUES (1, ?, ?, ?, ?)""",
            (username.strip(), password_hash, now, now),
        )
    await _conn().commit()
    logger.info("Auth initialized for user=%s", username.strip())


async def authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    async with _conn().cursor() as cursor:
        await cursor.execute(
            "SELECT username, password_hash FROM admin_user WHERE id = 1 AND username = ?",
            (username.strip(),),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    if not _verify_password(password, row["password_hash"]):
        return None
    return {"username": row["username"]}


async def change_password(old_password: str, new_password: str) -> None:
    user = await authenticate(
        (await get_admin_username()) or "",
        old_password,
    )
    if not user:
        raise ValueError("invalid_password")
    _validate_password(new_password)
    now = _now_iso()
    async with _conn().cursor() as cursor:
        await cursor.execute(
            "UPDATE admin_user SET password_hash = ?, updated_at = ? WHERE id = 1",
            (_hash_password(new_password), now),
        )
    await _conn().commit()


async def get_admin_username() -> Optional[str]:
    async with _conn().cursor() as cursor:
        await cursor.execute("SELECT username FROM admin_user WHERE id = 1")
        row = await cursor.fetchone()
    return row["username"] if row else None


async def create_session(remember: bool = False) -> Tuple[str, str]:
    session_id = secrets.token_urlsafe(32)
    now_dt = datetime.now(CHINA_TZ)
    if remember:
        expires_dt = now_dt + timedelta(days=settings.SESSION_REMEMBER_DAYS)
    else:
        expires_dt = now_dt + timedelta(hours=settings.SESSION_TTL_HOURS)
    now = now_dt.isoformat()
    expires_at = expires_dt.isoformat()
    async with _conn().cursor() as cursor:
        await cursor.execute(
            """INSERT INTO sessions (session_id, expires_at, created_at, last_seen_at)
               VALUES (?, ?, ?, ?)""",
            (session_id, expires_at, now, now),
        )
    await _conn().commit()
    return session_id, expires_at


async def validate_session_id(session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    async with _conn().cursor() as cursor:
        await cursor.execute(
            "SELECT expires_at FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
    if not row:
        return False
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=CHINA_TZ)
    if datetime.now(CHINA_TZ) >= expires_at:
        await delete_session(session_id)
        return False
    return True


async def delete_session(session_id: str) -> None:
    async with _conn().cursor() as cursor:
        await cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    await _conn().commit()


async def issue_api_token(label: str = "") -> Tuple[str, Dict[str, Any]]:
    plain = secrets.token_urlsafe(32)
    token_hash = _hash_token(plain)
    now = _now_iso()
    async with _conn().cursor() as cursor:
        await cursor.execute(
            """INSERT INTO api_tokens (token_hash, label, expires_at, created_at, revoked_at)
               VALUES (?, ?, NULL, ?, NULL)""",
            (token_hash, label or "", now),
        )
    await _conn().commit()
    return plain, {"token_hash": token_hash, "label": label or "", "created_at": now}


async def validate_api_token(plain: Optional[str]) -> bool:
    if not plain:
        return False
    token_hash = _hash_token(plain)
    async with _conn().cursor() as cursor:
        await cursor.execute(
            """SELECT expires_at, revoked_at FROM api_tokens WHERE token_hash = ?""",
            (token_hash,),
        )
        row = await cursor.fetchone()
    if not row or row["revoked_at"]:
        return False
    if row["expires_at"]:
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=CHINA_TZ)
        if datetime.now(CHINA_TZ) >= expires_at:
            return False
    return True


async def revoke_api_token(token_hash: str) -> bool:
    now = _now_iso()
    async with _conn().cursor() as cursor:
        await cursor.execute(
            "UPDATE api_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (now, token_hash),
        )
        changed = cursor.rowcount
    await _conn().commit()
    return changed > 0


async def list_api_tokens() -> list[Dict[str, Any]]:
    async with _conn().cursor() as cursor:
        await cursor.execute(
            """SELECT token_hash, label, expires_at, created_at, revoked_at
               FROM api_tokens ORDER BY created_at DESC"""
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]
