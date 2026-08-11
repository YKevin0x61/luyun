#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Release token for Version Check / Update Job.

Editable from Admin「系统更新」without restarting the process. Encrypted at
``data/github_release.enc`` with the same Fernet key as POS credentials
(``LUYUN_CRED_KEY`` / ``data/.cred_key``). Repo is fixed in ``config.GITHUB_REPO``;
only the PAT is stored here. ``GITHUB_RELEASES_TOKEN`` remains a bootstrap
fallback when no token is stored.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import InvalidToken

from config import settings
from db_core.utils import CHINA_TZ
from services.credentials_store import _FILE_MODE, _ensure_data_dir, _fernet

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent
_CONFIG_FILE = _BASE_DIR / "data" / "github_release.enc"

_lock = threading.RLock()


@dataclass
class GitHubReleaseConfig:
    repo: str = ""
    token: Optional[str] = None
    updated_at: Optional[str] = None


def _fixed_repo() -> str:
    return (settings.GITHUB_REPO or "").strip()


def _read_stored() -> Optional[dict[str, Any]]:
    if not _CONFIG_FILE.exists():
        return None
    try:
        raw = _fernet().decrypt(_CONFIG_FILE.read_bytes())
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except InvalidToken:
        logger.error("❌ github_release.enc 解密失败（密钥不匹配？）")
        return None
    except Exception as exc:
        logger.error("❌ 读取 github_release.enc 失败: %s", exc)
        return None


def _write_stored(payload: dict[str, Any]) -> None:
    _ensure_data_dir()
    token = _fernet().encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    _CONFIG_FILE.write_bytes(token)
    try:
        os.chmod(_CONFIG_FILE, _FILE_MODE)
    except OSError:
        pass


def load_stored() -> GitHubReleaseConfig:
    """Return token persisted via Admin UI (may be empty). Repo is always fixed."""
    with _lock:
        data = _read_stored() or {}
    return GitHubReleaseConfig(
        repo=_fixed_repo(),
        token=(str(data["token"]).strip() if data.get("token") else None) or None,
        updated_at=data.get("updated_at"),
    )


def get_effective_config() -> GitHubReleaseConfig:
    """Fixed repo + stored token, falling back to env token."""
    stored = load_stored()
    env_token = (settings.GITHUB_RELEASES_TOKEN or "").strip() or None
    return GitHubReleaseConfig(
        repo=_fixed_repo(),
        token=stored.token or env_token,
        updated_at=stored.updated_at,
    )


def save_config(
    *,
    token: Optional[str] = None,
    clear_token: bool = False,
) -> GitHubReleaseConfig:
    """Upsert token; omit keeps previous; clear_token wipes stored token."""
    with _lock:
        current = _read_stored() or {}
        next_token: Optional[str]
        if clear_token:
            next_token = None
        elif token is not None and str(token).strip():
            next_token = str(token).strip()
        else:
            prev = current.get("token")
            next_token = str(prev).strip() if prev else None

        payload = {
            "token": next_token,
            "updated_at": datetime.now(CHINA_TZ).isoformat(),
        }
        _write_stored(payload)

    return load_stored()


def public_status() -> dict[str, Any]:
    """Safe view for Admin UI (never returns the raw token)."""
    stored = load_stored()
    effective = get_effective_config()
    env_token = bool((settings.GITHUB_RELEASES_TOKEN or "").strip())
    return {
        "repo": effective.repo,
        "token_configured": bool(effective.token),
        "stored_token": bool(stored.token),
        "env_token": env_token,
        "updated_at": stored.updated_at,
    }
