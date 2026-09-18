#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PostgreSQL 角色密码重置（后台「数据库凭据」）。

设计要点
--------
- **不需要额外凭据**：Docker 形态下 ``luyun`` 是 compose ``POSTGRES_USER`` 建的
  超级用户；systemd 形态下即使是普通角色，PostgreSQL 也允许用户改**自己**的
  密码。所以一条业务 DSN 就能完成 ALTER USER。
- **只允许改自己**：目标角色固定取自当前 DSN，接口不接受角色名参数。
- **失败必须回滚**：ALTER → 新密码验证 → 写文件，任何一步失败都要把密码改回去，
  否则应用下次重启就连不上库（那比密码弱更糟）。
- **密码不回显**：响应里只给长度与结论；密码只落 ``deploy/env.production``。
- 写文件前会检查 ``LUYUN_POSTGRES_DSN`` 环境变量：它的优先级高于 settings，
  若它被显式设置，写文件是不生效的，这种情况直接拒绝而不是假装成功。
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote, urlsplit

from config import settings
from services.backup_service import is_postgres_backend

logger = logging.getLogger(__name__)

PASSWORD_LENGTH = 32
# DSN 安全字符集：不含 : / @ ? # 等需要 percent-encode 的字符，写回的连接串
# 无需转义即可被 psql / asyncpg 直接使用。
_PASSWORD_ALPHABET = string.ascii_letters + string.digits
_DSN_ENV_OVERRIDE = "LUYUN_POSTGRES_DSN"
_DSN_LINE_RE = re.compile(r"^POSTGRES_DSN=.*$", re.MULTILINE)


@dataclass(frozen=True)
class DsnParts:
    user: str
    password: str
    host: str
    port: str
    database: str
    scheme: str = "postgresql"

    def to_dsn(self, password: Optional[str] = None) -> str:
        secret = self.password if password is None else password
        credentials = ""
        if self.user:
            credentials = quote(self.user, safe="")
            if secret:
                credentials = f"{credentials}:{quote(secret, safe='')}"
            credentials = f"{credentials}@"
        return f"{self.scheme}://{credentials}{self.host}:{self.port}/{self.database}"

    def masked(self) -> str:
        """日志/接口里用的脱敏串（密码位保持可读的 ``***``，不做 percent-encode）。"""
        if not self.password:
            return self.to_dsn()
        user = f"{quote(self.user, safe='')}:" if self.user else ""
        return f"{self.scheme}://{user}***@{self.host}:{self.port}/{self.database}"


def parse_dsn(dsn: str) -> Optional[DsnParts]:
    """解析 PostgreSQL DSN；不合法返回 None。"""
    if not dsn or "://" not in dsn:
        return None
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return None
    if not parts.hostname:
        return None
    scheme = (parts.scheme or "postgresql").lower()
    if scheme not in ("postgresql", "postgres"):
        scheme = "postgresql"
    return DsnParts(
        user=unquote(parts.username or ""),
        password=unquote(parts.password or ""),
        host=parts.hostname,
        port=str(parts.port or 5432),
        database=(parts.path or "/").lstrip("/") or "luyun",
        scheme=scheme,
    )


def build_dsn(parts: DsnParts, password: str) -> str:
    return parts.to_dsn(password=password)


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def render_env_production(text: str, dsn: str) -> str:
    """把 env.production 里的 POSTGRES_DSN 换成新值，其他行原样保留。"""
    line = f"POSTGRES_DSN={dsn}"
    if _DSN_LINE_RE.search(text):
        return _DSN_LINE_RE.sub(line, text)
    if text and not text.endswith("\n"):
        text = f"{text}\n"
    return f"{text}{line}\n"


def env_production_path() -> Path:
    return Path(__file__).resolve().parents[1] / "deploy" / "env.production"


def current_dsn() -> str:
    return os.environ.get(_DSN_ENV_OVERRIDE) or getattr(settings, "POSTGRES_DSN", "")


def _dsn_env_override() -> Optional[str]:
    """``LUYUN_POSTGRES_DSN`` 一旦显式设置，它优先于 env.production（见 pg.py）。"""
    value = (os.environ.get(_DSN_ENV_OVERRIDE) or "").strip()
    return value or None


def credentials_status() -> dict:
    """给后台展示的只读状态：不含密码明文。"""
    path = env_production_path()
    override = _dsn_env_override()
    parts = parse_dsn(override or current_dsn())
    writable = False
    if path.parent.is_dir():
        writable = os.access(path if path.exists() else path.parent, os.W_OK)
    return {
        "backend": (getattr(settings, "DATABASE_BACKEND", "sqlite") or "sqlite").lower(),
        "is_postgres": is_postgres_backend(),
        "user": parts.user if parts else None,
        "host": parts.host if parts else None,
        "port": parts.port if parts else None,
        "database": parts.database if parts else None,
        "dsn": parts.masked() if parts else None,
        "password_length": len(parts.password) if parts else 0,
        "env_file": str(path),
        "env_file_writable": writable,
        "env_override": bool(override),
        "env_override_target": parse_dsn(override).masked() if override and parse_dsn(override) else None,
    }


class PasswordResetError(RuntimeError):
    """重置失败（已在可能的情况下回滚）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


async def _apply_password(conn, username: str, password: str) -> None:
    """用 format(%I/%L) 生成 ALTER 语句：值经参数传递，不存在拼接注入。"""
    statement = await conn.fetchval(
        "SELECT format('ALTER USER %I WITH PASSWORD %L', $1::text, $2::text)",
        username,
        password,
    )
    await conn.execute(statement)


async def _password_hash(conn, username: str) -> Optional[str]:
    """读角色密码哈希（需要超级用户；普通角色读不到 pg_shadow）。"""
    try:
        return await conn.fetchval("SELECT passwd FROM pg_shadow WHERE usename = $1", username)
    except Exception:  # noqa: BLE001 - 权限不足时跳过该层校验
        return None


async def _verify_dsn_connects(dsn: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=15)
    try:
        await conn.fetchval("SELECT 1")
    finally:
        await conn.close()


def _write_env_production(dsn: str) -> Path:
    path = env_production_path()
    if not path.exists():
        raise PasswordResetError("env_missing", f"找不到 {path}，无法写回新 DSN")
    original = path.read_text(encoding="utf-8")
    updated = render_env_production(original, dsn)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(updated, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def _restart_application() -> None:
    from services.release_update.job_adapters import build_main_service_adapter

    build_main_service_adapter().restart()


async def reset_database_password(*, actor: str, restart: bool = True) -> dict:
    """重置当前业务角色的密码并写回 env.production。

    顺序：ALTER → 用新密码验证 → 写文件 → 重启。任何一步失败都回滚密码。
    """
    if not is_postgres_backend():
        raise PasswordResetError("not_postgres", "当前后端不是 PostgreSQL，无法重置数据库密码")
    override = _dsn_env_override()
    if override:
        raise PasswordResetError(
            "env_override",
            f"检测到环境变量 {_DSN_ENV_OVERRIDE} 被显式设置（{parse_dsn(override).masked() if parse_dsn(override) else '无法解析'}）："
            f"它的优先级高于 deploy/env.production，写文件不会生效。请先去掉该环境变量再试。",
        )

    parts = parse_dsn(current_dsn())
    if not parts or not parts.user:
        raise PasswordResetError("dsn_invalid", "当前 POSTGRES_DSN 无法解析出角色名")
    if not parts.password:
        raise PasswordResetError("password_unknown", "当前 DSN 里没有密码，无法回滚，拒绝自动重置")

    import asyncpg

    new_password = generate_password()
    conn = await asyncpg.connect(parts.to_dsn(), timeout=15)
    try:
        before = await _password_hash(conn, parts.user)
        await _apply_password(conn, parts.user, new_password)
        after = await _password_hash(conn, parts.user)
        if before is not None and after is not None and before == after:
            raise PasswordResetError("alter_noop", "ALTER USER 后密码哈希未变化，已中止")
    except PasswordResetError:
        await conn.close()
        raise
    except Exception as exc:  # noqa: BLE001 - 统一成可展示的错误
        await conn.close()
        raise PasswordResetError("alter_failed", f"ALTER USER 失败：{exc}") from exc

    new_dsn = build_dsn(parts, new_password)
    try:
        await _verify_dsn_connects(new_dsn)
    except Exception as exc:  # noqa: BLE001
        # 回滚：新密码连不上，绝不能把它写进环境文件
        try:
            await _apply_password(conn, parts.user, parts.password)
            logger.error("数据库密码重置失败，已回滚 old→new 的改动: %s", exc)
        except Exception:  # noqa: BLE001
            logger.critical("数据库密码重置失败且回滚失败，需人工介入: %s", exc, exc_info=True)
        await conn.close()
        raise PasswordResetError("verify_failed", f"新密码验证失败，已回滚：{exc}") from exc
    finally:
        if not conn.is_closed():
            await conn.close()

    try:
        env_path = _write_env_production(new_dsn)
    except PasswordResetError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PasswordResetError("env_write_failed", f"写回 env.production 失败：{exc}") from exc

    logger.info(
        "数据库密码已重置 actor=%s user=%s host=%s db=%s env=%s",
        actor,
        parts.user,
        parts.host,
        parts.database,
        env_path,
    )

    restarted = False
    restart_error: Optional[str] = None
    if restart:
        try:
            _restart_application()
            restarted = True
        except Exception as exc:  # noqa: BLE001 - 密码已改好，重启失败要如实告诉用户
            restart_error = str(exc)
            logger.error("数据库密码已更新，但自动重启失败: %s", exc)

    return {
        "ok": True,
        "user": parts.user,
        "host": parts.host,
        "database": parts.database,
        "password_length": len(new_password),
        "env_file": str(env_path),
        "restart_triggered": restarted,
        "restart_error": restart_error,
    }
