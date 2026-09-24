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
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

RETENTION_SETTINGS_KEY = "backup_retention"

# 同步读取的来源（PERF-10）：冷备脚本只有分出「读到的配置」和「读取失败退回默认」，
# 才能决定要不要剪除超额冷备——读取失败时拿默认值 14 去删备份，等于用一个没人选过
# 的数字决定删不删别人的归档。
RETENTION_SOURCE_CONFIGURED = "configured"      # 库里读到且校验通过
RETENTION_SOURCE_DEFAULT = "default"            # 库里没有这个键：未配置，用默认值
RETENTION_SOURCE_FALLBACK = "default-fallback"  # 连不上库 / 值坏了：默认值兜底

# 旁路连接的建立超时与整体读取上限（PERF-09）：冷备脚本无事件循环，靠这两层
# 把「库不响应」变成一次可记录的失败，而不是永久挂住定时任务。
RETENTION_READ_TIMEOUT_SECONDS = 15.0

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


@dataclass(frozen=True)
class RetentionLoad:
    """一次同步读取的结果：配置 + 它是从哪来的。

    ``source`` 取 :data:`RETENTION_SOURCE_CONFIGURED` /
    :data:`RETENTION_SOURCE_DEFAULT` / :data:`RETENTION_SOURCE_FALLBACK` 之一；
    ``error`` 只在兜底时有值，用来写进冷备状态文件与告警。
    """

    config: RetentionConfig
    source: str
    error: Optional[str] = None

    @property
    def is_fallback(self) -> bool:
        """这份值是不是「读取失败后退回默认」（退默认就不该据它删数据）。"""
        return self.source == RETENTION_SOURCE_FALLBACK


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


def load_from_pg_sync_detailed(dsn: Optional[str] = None) -> RetentionLoad:
    """同步读取保留配置，并保留「这份值是哪来的」。

    只读。读失败**不抛**：冷备本身照跑（连不上保留配置不是冷备的致命错误），但
    调用方要能从 ``source`` 看出这是兜底默认值，从而跳过会删数据的清理。读取走
    ``db_core.backend.pg.connect_ephemeral``：同样的 ``statement_timeout`` /
    ``lock_timeout``，再加连接超时与 :func:`asyncio.wait_for` 两层上限（PERF-09）。

    SQLite 退场前这里读的是 ``app.db`` 的 ``app_settings``（ADR 0089），现在直接
    连 PostgreSQL 读同一张表。
    """
    import asyncio
    import json

    from db_core.backend.pg import connect_ephemeral

    async def _read() -> Optional[str]:
        conn = await connect_ephemeral(dsn, timeout=RETENTION_READ_TIMEOUT_SECONDS)
        try:
            return await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = $1",
                RETENTION_SETTINGS_KEY,
            )
        finally:
            await conn.close()

    try:
        raw = asyncio.run(
            asyncio.wait_for(_read(), timeout=RETENTION_READ_TIMEOUT_SECONDS)
        )
    except Exception as exc:  # noqa: BLE001 - 连不上库不是冷备的致命错误
        logger.warning(
            "⚠️ 冷备读取保留配置失败（%s），回退默认值 %s 且本次不剪除冷备",
            exc,
            _DEFAULT.cold_keep,
        )
        return RetentionLoad(_DEFAULT, RETENTION_SOURCE_FALLBACK, str(exc))
    if not raw:
        return RetentionLoad(_DEFAULT, RETENTION_SOURCE_DEFAULT)
    try:
        return RetentionLoad(
            validate_retention(json.loads(raw)), RETENTION_SOURCE_CONFIGURED
        )
    except (ValueError, TypeError) as exc:
        logger.warning(
            "⚠️ 冷备保留配置不可用（%s），回退默认值 %s 且本次不剪除冷备",
            exc,
            _DEFAULT.cold_keep,
        )
        return RetentionLoad(_DEFAULT, RETENTION_SOURCE_FALLBACK, str(exc))


def load_from_pg_sync(dsn: Optional[str] = None) -> RetentionConfig:
    """同步读取保留配置（冷备脚本等无事件循环的调用方使用）。

    只读；连不上库、读不到或校验失败时退回默认值，不影响冷备本身。需要区分
    「读到的值」与「兜底默认值」的调用方用 :func:`load_from_pg_sync_detailed`。
    """
    return load_from_pg_sync_detailed(dsn).config


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
