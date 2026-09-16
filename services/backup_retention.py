#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份保留配置：本机回滚快照与冷备各自的保留份数。

保留份数不再写死在代码里，而是持久化到 ``app_settings`` 的 ``backup_retention``
键。校验区间在保存前完成；清理前必须先给出「将删除哪些备份点」的预览，见
``services.backup_points``。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict

logger = logging.getLogger(__name__)

RETENTION_SETTINGS_KEY = "backup_retention"

SNAPSHOT_KEEP_DEFAULT = 5
SNAPSHOT_KEEP_MIN = 1
SNAPSHOT_KEEP_MAX = 20

COLD_KEEP_DEFAULT = 14
COLD_KEEP_MIN = 1
COLD_KEEP_MAX = 90

# 导出备份默认下载到浏览器；本机另存的那一份用于列表展示与直接恢复，
# 因此同样需要上限，避免长期占用磁盘。
EXPORT_KEEP_DEFAULT = 5
EXPORT_KEEP_MIN = 1
EXPORT_KEEP_MAX = 20


@dataclass(frozen=True)
class RetentionConfig:
    """备份点保留份数（本机回滚快照 / 导出备份 / 冷备）。"""

    snapshot_keep: int = SNAPSHOT_KEEP_DEFAULT
    export_keep: int = EXPORT_KEEP_DEFAULT
    cold_keep: int = COLD_KEEP_DEFAULT

    def to_dict(self) -> Dict[str, int]:
        return asdict(self)


_DEFAULT = RetentionConfig()
_cache: RetentionConfig = _DEFAULT


def _coerce(value: Any, field: str, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是整数")
    if number < lo or number > hi:
        raise ValueError(f"{field} 必须在 {lo}~{hi} 之间")
    return number


def validate_retention(payload: Dict[str, Any]) -> RetentionConfig:
    """校验并归一化保留配置；校验失败抛 ``ValueError``。"""
    merged = {**asdict(_DEFAULT), **(payload or {})}
    snapshot_keep = _coerce(
        merged["snapshot_keep"], "本机回滚快照保留份数", SNAPSHOT_KEEP_MIN, SNAPSHOT_KEEP_MAX
    )
    export_keep = _coerce(
        merged["export_keep"], "导出备份保留份数", EXPORT_KEEP_MIN, EXPORT_KEEP_MAX
    )
    cold_keep = _coerce(
        merged["cold_keep"], "冷备保留份数", COLD_KEEP_MIN, COLD_KEEP_MAX
    )
    if snapshot_keep == 1 and cold_keep == 1:
        raise ValueError("本机回滚快照与冷备不能同时只保留 1 份")
    return RetentionConfig(
        snapshot_keep=snapshot_keep,
        export_keep=export_keep,
        cold_keep=cold_keep,
    )


def cache_get() -> RetentionConfig:
    """当前进程缓存的保留配置（清理逻辑同步路径使用）。"""
    return _cache


def cache_set(config: RetentionConfig) -> None:
    global _cache
    _cache = config


async def load_retention(db) -> RetentionConfig:
    """从数据库读取保留配置并与默认值合并；读不到时用默认值。"""
    stored = await db.settings_get_json(RETENTION_SETTINGS_KEY, None)
    if not stored:
        cache_set(_DEFAULT)
        return _DEFAULT
    try:
        config = validate_retention(stored)
    except ValueError as exc:
        logger.warning("⚠️ 备份保留配置校验失败，回退默认值: %s", exc)
        config = _DEFAULT
    cache_set(config)
    return config


async def save_retention(db, payload: Dict[str, Any]) -> RetentionConfig:
    """校验后持久化保留配置并刷新进程缓存。"""
    return await save_retention_config(db, validate_retention(payload))


async def save_retention_config(db, config: RetentionConfig) -> RetentionConfig:
    """持久化一份已校验的保留配置并刷新进程缓存。"""
    ok = await db.settings_set_json(RETENTION_SETTINGS_KEY, config.to_dict())
    if not ok:
        raise RuntimeError("备份保留配置写入数据库失败")
    cache_set(config)
    return config


def load_from_db_sync(app_db_path: str) -> RetentionConfig:
    """同步读取保留配置（冷备脚本等无事件循环的调用方使用）。

    只读打开；读不到或校验失败时退回默认值，不影响冷备本身。
    """
    import json
    import sqlite3
    from pathlib import Path

    path = Path(app_db_path)
    if not path.is_file():
        return _DEFAULT
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?immutable=1", uri=True)
    except sqlite3.Error:
        return _DEFAULT
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (RETENTION_SETTINGS_KEY,),
        ).fetchone()
    except sqlite3.Error:
        return _DEFAULT
    finally:
        conn.close()
    if not row or not row[0]:
        return _DEFAULT
    try:
        return validate_retention(json.loads(row[0]))
    except (ValueError, TypeError):
        logger.warning("⚠️ 冷备读取保留配置失败，回退默认值")
        return _DEFAULT


def limits() -> Dict[str, int]:
    """页面展示默认值与上限。"""
    return {
        "snapshot_keep_default": SNAPSHOT_KEEP_DEFAULT,
        "snapshot_keep_min": SNAPSHOT_KEEP_MIN,
        "snapshot_keep_max": SNAPSHOT_KEEP_MAX,
        "export_keep_default": EXPORT_KEEP_DEFAULT,
        "export_keep_min": EXPORT_KEEP_MIN,
        "export_keep_max": EXPORT_KEEP_MAX,
        "cold_keep_default": COLD_KEEP_DEFAULT,
        "cold_keep_min": COLD_KEEP_MIN,
        "cold_keep_max": COLD_KEEP_MAX,
    }
