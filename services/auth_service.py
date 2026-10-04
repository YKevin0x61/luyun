#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享账号、Session、API Token 存储与校验。"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from config import settings
from database import CHINA_TZ, DatabaseManager
# 节流窗口与员工端共用同一个值：两套会话的闲置口径必须逐条一致（票 10），
# 而这个值只该有一份（`services/identity/accounts.py` 是它的定义处）。
from services.identity.accounts import LAST_SEEN_REFRESH_SECONDS
from services.password_hash import hash_password_async as _hash_password
from services.password_hash import needs_rehash as _needs_rehash
from services.password_hash import validate_password as _validate_password
from services.password_hash import verify_password_async as _verify_password

logger = logging.getLogger(__name__)


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


def _parse_stamp(value: Any) -> Optional[datetime]:
    """ISO 时间戳 → 带时区的 datetime；空值或坏值返回 None（不抛）。

    与员工端 `services/identity/accounts.py` 的 `_parse_stamp` 同一口径：库里存的
    时刻可能是旧版本写进去的裸时间（没有时区），一律按北京时间解读。
    """
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=CHINA_TZ)
    return stamp


def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


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
    password_hash = await _hash_password(password)
    async with _conn().cursor() as cursor:
        await cursor.execute(
            """INSERT INTO admin_user (id, username, password_hash, created_at, updated_at)
               VALUES (1, ?, ?, ?, ?)""",
            (username.strip(), password_hash, now, now),
        )
    await _conn().commit()
    logger.info("Auth initialized for user=%s", username.strip())


async def _upgrade_legacy_hash(username: str, password: str) -> None:
    """legacy 无前缀哈希验证通过后按现行格式重写。

    重写失败不影响本次登录——它只是让下次验证少跑一次 bcrypt。
    """
    try:
        new_hash = await _hash_password(password)
        async with _conn().cursor() as cursor:
            await cursor.execute(
                "UPDATE admin_user SET password_hash = ?, updated_at = ? "
                "WHERE id = 1 AND username = ?",
                (new_hash, _now_iso(), username),
            )
        await _conn().commit()
        logger.info("Auth upgraded legacy password hash for user=%s", username)
    except Exception as exc:
        logger.warning("Auth legacy hash upgrade skipped for user=%s: %s", username, exc)
        # 全进程共用一条连接，失败后必须回滚，否则未提交事务会污染后续请求。
        try:
            await _conn().rollback()
        except Exception:
            logger.debug("Auth legacy hash upgrade rollback failed", exc_info=True)


async def authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    async with _conn().cursor() as cursor:
        await cursor.execute(
            "SELECT username, password_hash FROM admin_user WHERE id = 1 AND username = ?",
            (username.strip(),),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    stored_hash = row["password_hash"]
    if not await _verify_password(password, stored_hash):
        return None
    if _needs_rehash(stored_hash):
        await _upgrade_legacy_hash(row["username"], password)
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
    new_hash = await _hash_password(new_password)
    async with _conn().cursor() as cursor:
        await cursor.execute(
            "UPDATE admin_user SET password_hash = ?, updated_at = ? WHERE id = 1",
            (new_hash, now),
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


async def _touch_session(session_id: str, now_iso: str) -> None:
    """刷新 `last_seen_at`（只在节流窗口过后调用）。

    必须落在 `DatabaseManager._write_lock` 里：`validate_session_id` 在**每个**管理端
    请求上都会跑，如果在锁外 `commit()`，就会把并发写者（`serialized_write` 里的显式
    事务）刚写了一半的事务顺手提交掉 —— 与员工端
    `EmployeeAccounts._touch_staff_session` 是同一条理由、同一个锁。
    """
    async with _db()._write_lock:
        async with _conn().cursor() as cursor:
            await cursor.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE session_id = ?",
                (now_iso, session_id),
            )
        await _conn().commit()


async def validate_session_id(session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    async with _conn().cursor() as cursor:
        await cursor.execute(
            "SELECT expires_at, last_seen_at FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
    if not row:
        return False
    now_dt = datetime.now(CHINA_TZ)
    # 判定顺序与员工端逐条一致：先绝对有效期，再闲置上限，最后才谈刷新。
    expires_at = _parse_stamp(row["expires_at"]) or now_dt
    if now_dt >= expires_at:
        await delete_session(session_id)
        return False
    # 闲置上限（票 10）：勾了「记住我」的会话有效期 30 天，这条把「机器一直挂着没人用」
    # 的窗口收窄 —— 与员工端共用 `SESSION_IDLE_HOURS`（默认 336 = 14 天，0 = 关闭）。
    # 在用的会话按下面的节流刷新 last_seen_at，所以闲置窗口跟着活动滑动；但这条**绝不
    # 延长 expires_at**：那两档（记住我 30 天 / 否则 8 小时）一个字都不改。
    idle_hours = int(getattr(settings, "SESSION_IDLE_HOURS", 0) or 0)
    last_seen = _parse_stamp(row["last_seen_at"]) or now_dt
    if idle_hours > 0 and now_dt - last_seen >= timedelta(hours=idle_hours):
        await delete_session(session_id)
        logger.info("管理端会话闲置超时已登出（阈值 %sh）", idle_hours)
        return False
    # 节流刷新：SPA 的状态探测与实时拉取很频繁，每次请求都写库没有意义；5 分钟的粒度
    # 对「闲置多久算失效」完全够用（员工端同一套写法、同一个值）。
    if (now_dt - last_seen).total_seconds() >= LAST_SEEN_REFRESH_SECONDS:
        await _touch_session(session_id, now_dt.isoformat())
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
