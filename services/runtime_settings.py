#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
爬虫运行期配置（非敏感）。

把原本硬编码在采集侧的营业时段、轮询间隔与浏览器选项
抽出来，持久化到 ``app_settings`` 表（键 ``scraper_runtime``），并提供校验、
读取与保存。保存后由调用方（API 层）触发爬虫热重载即时生效。

设计约束：
- 只支持「同日营业时段」（``work_start < work_end``），跨零点通宵营业直接拒绝，
  以规避 ``_is_business_hours`` 的静默错误。
- 轮询间隔用上下限（``interval_min``/``interval_max``）表达，主循环在其间随机取值。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

RUNTIME_SETTINGS_KEY = "scraper_runtime"

# —— 边界常量（避免魔术数字）——
INTERVAL_SECONDS_MIN = 1
INTERVAL_SECONDS_MAX = 3600
RETRY_COUNT_MIN = 0
RETRY_COUNT_MAX = 10
TIMEOUT_MS_MIN = 1000
TIMEOUT_MS_MAX = 300000
# 外卖“消失即取消”防误杀阈值：连续缺席多少次才判取消
DELIVERY_CANCEL_MISS_MIN = 1
DELIVERY_CANCEL_MISS_MAX = 20

_TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

# 默认运行配置（与历史硬编码值保持一致，确保迁移前后行为不变）
DEFAULT_RUNTIME_SETTINGS: Dict[str, Any] = {
    "work_start": "07:30",
    "work_end": "21:30",
    "interval_min": 5,
    "interval_max": 20,
    "headless": True,
    "retry_count": 3,
    "timeout_ms": 30000,
    "delivery_cancel_miss_threshold": 3,
}


def _to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _normalize_time(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _TIME_PATTERN.match(text):
        raise ValueError(f"{field} 必须是 HH:MM 24 小时制时间，如 07:30")
    # 归一化为零填充的 HH:MM，避免 "7:30" 与 "07:30" 比较不一致
    hours, minutes = text.split(":")
    return f"{int(hours):02d}:{int(minutes):02d}"


def _coerce_int(value: Any, field: str, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是整数")
    if number < lo or number > hi:
        raise ValueError(f"{field} 必须在 {lo}~{hi} 之间")
    return number


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def validate_runtime_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """校验并归一化运行配置；缺省字段用默认值补齐。校验失败抛 ``ValueError``。"""
    merged = {**DEFAULT_RUNTIME_SETTINGS, **(payload or {})}

    work_start = _normalize_time(merged["work_start"], "营业开始时间")
    work_end = _normalize_time(merged["work_end"], "营业结束时间")
    if _to_minutes(work_start) >= _to_minutes(work_end):
        raise ValueError(
            "营业开始时间必须早于结束时间（仅支持同日时段，暂不支持跨零点通宵营业）"
        )

    interval_min = _coerce_int(
        merged["interval_min"], "轮询间隔下限", INTERVAL_SECONDS_MIN, INTERVAL_SECONDS_MAX
    )
    interval_max = _coerce_int(
        merged["interval_max"], "轮询间隔上限", INTERVAL_SECONDS_MIN, INTERVAL_SECONDS_MAX
    )
    if interval_min > interval_max:
        raise ValueError("轮询间隔下限不能大于上限")

    retry_count = _coerce_int(merged["retry_count"], "重试次数", RETRY_COUNT_MIN, RETRY_COUNT_MAX)
    timeout_ms = _coerce_int(merged["timeout_ms"], "超时(毫秒)", TIMEOUT_MS_MIN, TIMEOUT_MS_MAX)
    delivery_cancel_miss_threshold = _coerce_int(
        merged["delivery_cancel_miss_threshold"],
        "外卖取消判定次数",
        DELIVERY_CANCEL_MISS_MIN,
        DELIVERY_CANCEL_MISS_MAX,
    )

    return {
        "work_start": work_start,
        "work_end": work_end,
        "interval_min": interval_min,
        "interval_max": interval_max,
        "headless": _coerce_bool(merged["headless"]),
        "retry_count": retry_count,
        "timeout_ms": timeout_ms,
        "delivery_cancel_miss_threshold": delivery_cancel_miss_threshold,
    }


async def load_runtime_settings(db) -> Dict[str, Any]:
    """从 DB 读取运行配置并与默认值合并；读不到时返回默认值副本。"""
    stored = await db.settings_get_json(RUNTIME_SETTINGS_KEY, None)
    if not stored:
        return dict(DEFAULT_RUNTIME_SETTINGS)
    try:
        return validate_runtime_settings(stored)
    except ValueError as exc:
        # 历史脏数据不应阻断启动，退回默认值并告警
        logger.warning("⚠️ 运行配置校验失败，回退默认值: %s", exc)
        return dict(DEFAULT_RUNTIME_SETTINGS)


async def save_runtime_settings(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    """校验后持久化运行配置，返回归一化后的完整配置。"""
    validated = validate_runtime_settings(payload)
    ok = await db.settings_set_json(RUNTIME_SETTINGS_KEY, validated)
    if not ok:
        raise RuntimeError("运行配置写入数据库失败")
    return validated
