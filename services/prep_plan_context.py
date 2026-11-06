#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货复盘的环境信号（票 02）。

一天一行，落在 `prep_plan_context`：节假日、天气、当天在岗人数、人工备注。
复盘页（票 06）读它解释「今天为什么多做/少做了」——这些是快照数字本身讲不出来的
外部原因，所以按**天**存，不按快照时刻存：盘点的是一整天的生意，天气与节假日也
不会随整点变。

三条硬约定：

- **只补空、不覆盖**。当天第一次写进来之后，人工备注、`captured_at`、已经抓到的
  天气与人数都不再被自动刷新改写；管理员改备注只动 `note` 一列。失败时更不写空，
  原值原样留着。
- **外部依赖失败不阻断快照**。没有 key、超时、接口 500 一律只记一条带 `plan_day`
  与城市的 WARNING，返回「没有新值」，于是 upsert 自然保住旧值。
- 天气客户端是注入的（测试用替身，**绝不出网**）。
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from database import CHINA_TZ
from services.prep_plan_service import POSITION_STATION_MAP

logger = logging.getLogger(__name__)

# app_settings 里的配置键：{"api_key": "...", "city": "广州", "location_id": "...", "updated_at": "..."}
# location_id 是 GEO 接口换出来的城市 id，首次换到后缓存，避免每小时都查一次。
WEATHER_SETTINGS_KEY = "prep_review_weather"
QWEATHER_GEO_URL = "https://geoapi.qweather.com/v2/city/lookup"
QWEATHER_DAILY_URL = "https://devapi.qweather.com/v7/weather/3d"
# 天气接口再慢也不能把整点快照或页面请求拖住；超时按「没有新值」处理。
WEATHER_TIMEOUT_SECONDS = 5.0

# 人力按岗位名（POSITION_STATION_MAP 的 key）聚；认不出的工作区与没配区的都落 other。
OTHER_POSITION = "other"


def _loads_object(raw: Optional[str]) -> Dict[str, Any]:
    """把 JSON 文本读成 dict；坏值当空对象（不抛）。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class QWeatherClient:
    """和风天气的最小客户端：只做 GEO 换 id 与当天预报。

    返回形状对测试替身公开：
    - `lookup_location(city, api_key) -> Optional[str]`
    - `daily(location_id, api_key, day) -> Optional[dict]`，键为
      `text / temp_max / temp_min / precip`。
    """

    def __init__(self, timeout: float = WEATHER_TIMEOUT_SECONDS):
        self._timeout = timeout

    async def _get_json(self, url: str, params: Dict[str, Any]) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def lookup_location(self, city: str, api_key: str) -> Optional[str]:
        payload = await self._get_json(
            QWEATHER_GEO_URL, {"location": city, "key": api_key, "number": 1}
        )
        locations = payload.get("location") or []
        if not locations:
            return None
        location_id = str(locations[0].get("id") or "").strip()
        return location_id or None

    async def daily(
        self, location_id: str, api_key: str, day: str
    ) -> Optional[Dict[str, Any]]:
        payload = await self._get_json(
            QWEATHER_DAILY_URL, {"location": location_id, "key": api_key}
        )
        daily = payload.get("daily") or []
        entry = next((item for item in daily if item.get("fxDate") == day), None)
        if entry is None:
            return None
        return {
            "text": str(entry.get("textDay") or ""),
            "temp_max": _to_float(entry.get("tempMax")),
            "temp_min": _to_float(entry.get("tempMin")),
            "precip": _to_float(entry.get("precip")),
        }


class PrepPlanContextService:
    """读写 `prep_plan_context`。`weather_client` 只为测试注入，生产用默认客户端。"""

    def __init__(self, weather_client: Optional[Any] = None):
        self._weather_client = weather_client or QWeatherClient()

    # ------------------------------------------------------------------ 读

    async def get_context(self, db, plan_day: str) -> Optional[dict]:
        tdb = db.table("prep_plan_context")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT plan_day, holiday_name, weather_json, staff_json, note,
                       captured_at, updated_at
                FROM prep_plan_context
                WHERE plan_day = ?
                """,
                (plan_day,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "plan_day": row["plan_day"],
            "holiday_name": row["holiday_name"],
            "weather": _loads_object(row["weather_json"]),
            "staff": _loads_object(row["staff_json"]),
            "note": row["note"],
            "captured_at": row["captured_at"],
            "updated_at": row["updated_at"],
        }

    # ------------------------------------------------------------------ 写

    async def refresh(
        self, db, plan_day: str, now: Optional[datetime] = None
    ) -> Optional[dict]:
        """抓一次当天的环境信号并 upsert。任何外部信号失败都只记日志、不抛。"""
        now = now or datetime.now(CHINA_TZ)
        holiday_name = await self._holiday_name(db, plan_day)
        weather = await self._fetch_weather(db, plan_day, now)
        staff = await self._staff_counts(db, plan_day)

        tdb = db.table("prep_plan_context")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_context (
                    plan_day, holiday_name, weather_json, staff_json,
                    captured_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, plan_day) DO UPDATE SET
                    holiday_name = COALESCE(
                        prep_plan_context.holiday_name, EXCLUDED.holiday_name
                    ),
                    weather_json = CASE
                        WHEN prep_plan_context.weather_json IN ('', '{}')
                        THEN EXCLUDED.weather_json
                        ELSE prep_plan_context.weather_json
                    END,
                    staff_json = CASE
                        WHEN prep_plan_context.staff_json IN ('', '{}')
                        THEN EXCLUDED.staff_json
                        ELSE prep_plan_context.staff_json
                    END,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    plan_day,
                    holiday_name,
                    json.dumps(weather, ensure_ascii=False) if weather else "{}",
                    json.dumps(staff, ensure_ascii=False) if staff else "{}",
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        await tdb.commit()
        return await self.get_context(db, plan_day)

    async def set_note(
        self,
        db,
        plan_day: str,
        note: Optional[str],
        now: Optional[datetime] = None,
    ) -> Optional[dict]:
        """写人工备注。只动 `note` 与 `updated_at`，不碰信号字段与 `captured_at`。"""
        now = now or datetime.now(CHINA_TZ)
        text = (note or "").strip()
        tdb = db.table("prep_plan_context")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_context (plan_day, note, captured_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (tenant_id, plan_day) DO UPDATE SET
                    note = EXCLUDED.note,
                    updated_at = EXCLUDED.updated_at
                """,
                (plan_day, text, now.isoformat(), now.isoformat()),
            )
        await tdb.commit()
        return await self.get_context(db, plan_day)

    # -------------------------------------------------------------- 三个来源

    async def _holiday_name(self, db, plan_day: str) -> Optional[str]:
        tdb = db.table("statutory_holidays")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT name FROM statutory_holidays WHERE day = ? "
                "ORDER BY id DESC LIMIT 1",
                (plan_day,),
            )
            row = await cursor.fetchone()
        return row["name"] if row else None

    async def _fetch_weather(
        self, db, plan_day: str, now: datetime
    ) -> Optional[Dict[str, Any]]:
        """抓当天天气；没有配置 / 换不到城市 / 请求失败都返回 None（保留原值）。"""
        config = await db.settings_get_json(WEATHER_SETTINGS_KEY, {}) or {}
        if not isinstance(config, dict):
            config = {}
        api_key = str(config.get("api_key") or "").strip()
        city = str(config.get("city") or "").strip()
        if not api_key or not city:
            # 没配置不是故障，调试时能看到原因即可，别每小时刷一条告警。
            logger.debug("天气未配置，跳过 plan_day=%s", plan_day)
            return None

        location_id = str(config.get("location_id") or "").strip()
        try:
            if not location_id:
                location_id = await self._weather_client.lookup_location(city, api_key)
                if not location_id:
                    logger.warning(
                        "和风 GEO 未找到城市，天气留空 plan_day=%s city=%s",
                        plan_day,
                        city,
                    )
                    return None
                config["location_id"] = location_id
                config["updated_at"] = now.isoformat()
                await db.settings_set_json(WEATHER_SETTINGS_KEY, config)

            daily = await self._weather_client.daily(location_id, api_key, plan_day)
            if not daily:
                logger.warning(
                    "和风天气无当天数据，保留原值 plan_day=%s city=%s",
                    plan_day,
                    city,
                )
                return None
            payload = dict(daily)
            payload.setdefault("source", "qweather")
            payload["fetched_at"] = now.isoformat()
            return payload
        except Exception as exc:  # 断网、超时、HTTP 4xx/5xx、坏 JSON 都走这里
            logger.warning(
                "天气获取失败，保留原值 plan_day=%s city=%s err=%s",
                plan_day,
                city,
                exc,
            )
            return None

    async def _staff_counts(self, db, plan_day: str) -> Dict[str, int]:
        """当天有班次的人在岗数，按工作区名映射成岗位；认不出的落 other。"""
        tdb = db.table("staff_assignments")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT z.name AS zone_name, COUNT(*) AS staff_count
                FROM staff_assignments a
                LEFT JOIN hygiene_zones z ON z.id = a.zone_id
                WHERE a.business_date = ? AND a.shift_id IS NOT NULL
                GROUP BY z.name
                """,
                (plan_day,),
            )
            rows = await cursor.fetchall()

        counts: Dict[str, int] = {}
        for row in rows:
            zone_name = str(row["zone_name"] or "").strip()
            position = zone_name if zone_name in POSITION_STATION_MAP else OTHER_POSITION
            counts[position] = counts.get(position, 0) + int(row["staff_count"] or 0)
        return counts


prep_plan_context = PrepPlanContextService()
