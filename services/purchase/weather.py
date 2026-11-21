#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购下单页头部的三天天气。

沿用备货复盘的同一套和风配置（app_settings 的 prep_review_weather），不新增
Key、不把 Key 下发给页面。城市取配置里的 city，没填就按广州；取不到预报时
返回 available=false 与空 days，让页面画三张占位卡，不抛错、不阻断下单。

缓存 10 分钟：同一进程里连续刷新页面不必每次都打和风；Key/城市/Host 或日期
一变就失效。缓存里只有展示字段，没有 Key。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from database import CHINA_TZ
from services.prep_plan_context import (
    QWeatherClient,
    WEATHER_SETTINGS_KEY,
    _weather_urls,
)

logger = logging.getLogger(__name__)

WEATHER_CACHE_SECONDS = 600
_DAY_LABELS = ("今天", "明天", "后天")
_CACHE: dict[str, Any] = {"key": None, "at": None, "payload": None}


def _empty(*, configured: bool, city: str, fetched_at: str = "") -> dict:
    return {
        "configured": bool(configured),
        "available": False,
        "city": city,
        "fetched_at": fetched_at,
        "days": [],
    }


def _normalise_day(entry: dict, index: int) -> dict:
    return {
        "date": str(entry.get("date") or ""),
        "label": _DAY_LABELS[index] if index < len(_DAY_LABELS) else "",
        "text": str(entry.get("text") or ""),
        "temp_max": entry.get("temp_max"),
        "temp_min": entry.get("temp_min"),
        "humidity": entry.get("humidity"),
        "wind_dir": str(entry.get("wind_dir") or ""),
        "wind_scale": str(entry.get("wind_scale") or ""),
        "icon": str(entry.get("icon") or ""),
    }


def reset_weather_cache() -> None:
    """测试用：清掉进程内缓存，避免上一条用例的天气串到下一条。"""
    _CACHE["key"] = None
    _CACHE["at"] = None
    _CACHE["payload"] = None


async def purchase_weather(
    db,
    *,
    now: Optional[datetime] = None,
    client: Optional[Any] = None,
) -> dict:
    """读配置并返回三天天气；没有配置或失败都走空占位。"""
    config = await db.settings_get_json(WEATHER_SETTINGS_KEY, {}) or {}
    if not isinstance(config, dict):
        config = {}
    api_key = str(config.get("api_key") or "").strip()
    city = str(config.get("city") or "").strip() or "广州"
    if not api_key:
        return _empty(configured=False, city=city)

    current = now or datetime.now(CHINA_TZ)
    host = str(config.get("api_host") or "").strip()
    cache_key = (api_key, city, host, current.date().isoformat())
    cached_at = _CACHE.get("at")
    if (
        _CACHE.get("key") == cache_key
        and isinstance(cached_at, datetime)
        and (current - cached_at).total_seconds() < WEATHER_CACHE_SECONDS
    ):
        return dict(_CACHE["payload"])

    geo_url, daily_url = _weather_urls(config)
    location_id = str(config.get("location_id") or "").strip()
    weather_client = client or QWeatherClient()
    try:
        if not location_id:
            location_id = await weather_client.lookup_location(
                city, api_key, url=geo_url
            )
        if not location_id:
            logger.warning("采购天气未找到城市 city=%s", city)
            return _empty(configured=True, city=city, fetched_at=current.isoformat())
        raw_days = await weather_client.daily_range(
            location_id, api_key, url=daily_url
        )
        days = [
            _normalise_day(entry, index)
            for index, entry in enumerate(raw_days or [])
            if isinstance(entry, dict)
        ]
        if not days:
            logger.warning("采购天气没有返回预报 city=%s", city)
            return _empty(configured=True, city=city, fetched_at=current.isoformat())
        payload = {
            "configured": True,
            "available": True,
            "city": city,
            "fetched_at": current.isoformat(),
            "days": days,
        }
        _CACHE["key"] = cache_key
        _CACHE["at"] = current
        _CACHE["payload"] = payload
        return dict(payload)
    except Exception as exc:  # 断网、超时、HTTP 4xx/5xx、坏 JSON 都走这里
        # 异常文本可能带请求 URL（含 key），日志只留类型与城市。
        logger.warning("采购天气获取失败 city=%s type=%s", city, type(exc).__name__)
        return _empty(configured=True, city=city, fetched_at=current.isoformat())
