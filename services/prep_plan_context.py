#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货复盘的环境信号（票 02 / 票 08）。

一天一行，落在 `prep_plan_context`：节假日、天气、当天在岗人数、人工备注，以及
票 08 补的四项 —— 数据质量（订单条数 / 快照覆盖小时 / 采集最近成功）、营业时段与
来源、是否工作日、厨房出餐节奏。复盘页读它解释「今天为什么多做/少做了」以及
「那天的数据可不可信」——这些是快照数字本身讲不出来的外部原因，所以按**天**存，
不按快照时刻存。

四条硬约定：

- **只补空、不覆盖**。当天第一次写进来之后，人工备注、`captured_at`、已经抓到的
  天气与人数都不再被自动刷新改写；管理员改备注只动 `note` 一列。失败时更不写空，
  原值原样留着。
- **动态信号每次覆盖**。数据质量、营业时段、厨房节奏会随一天推进而变（订单数、
  快照数、出餐行数），冻结在第一次写入就没有解释力；`is_workday` 跟已落库的
  节假日名保持一致。见 `refresh` 里 upsert 的注释。
- **外部依赖失败不阻断快照**。没有 key、超时、接口 500 一律只记一条带 `plan_day`
  与城市的 WARNING，返回「没有新值」，于是 upsert 自然保住旧值。信号里的坏时间戳 /
  坏 JSON 也只降级成默认值，不抛。
- 天气客户端是注入的（测试用替身，**绝不出网**）。
"""

import json
import logging
import statistics
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx

from database import CHINA_TZ
from services.app_runtime import get_runtime
from services.business_day import workbench_day_range
from services.identity.zones import ZoneDirectory
from services.prep_plan_service import (
    DEFAULT_CLOSE,
    DEFAULT_OPEN,
    FALLBACK_RUNTIME_SETTINGS_KEY,
    POSITION_STATION_MAP,
    RUNTIME_SETTINGS_KEY,
)

logger = logging.getLogger(__name__)

# app_settings 里的配置键：{"api_key": "...", "city": "广州", "location_id": "...", "updated_at": "..."}
# location_id 是 GEO 接口换出来的城市 id，首次换到后缓存，避免每小时都查一次。
WEATHER_SETTINGS_KEY = "prep_review_weather"
QWEATHER_GEO_URL = "https://geoapi.qweather.com/v2/city/lookup"
QWEATHER_DAILY_URL = "https://devapi.qweather.com/v7/weather/3d"
# 天气接口再慢也不能把整点快照或页面请求拖住；超时按「没有新值」处理。
WEATHER_TIMEOUT_SECONDS = 5.0


def _weather_urls(config: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """按 `api_host` 算 GEO / 预报地址；宿主为空时回落老域名。

    和风 2024-06 之后注册的账号只认 `https://<id>.re.qweatherapi.com`，老账号仍是
    `geoapi` + `devapi` 两个域名。这里只做字符串拼接，不做探测 —— 填错 Host 由
    「测试连通」当场给人话原因。
    """
    host = str((config or {}).get("api_host") or "").strip().rstrip("/")
    if not host:
        return QWEATHER_GEO_URL, QWEATHER_DAILY_URL
    return f"{host}/geo/v2/city/lookup", f"{host}/v7/weather/3d"


def _mask_api_key(api_key: Optional[str]) -> str:
    """`abcdEFGH1234wxyz` → `abcd****wxyz`。

    短 Key（≤ 8 位）首尾各 4 位就等于明文，直接全遮。空 Key 返回空串，页面据此
    判断「还没配」。
    """
    key = str(api_key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def _normalize_api_host(value: Optional[str]) -> str:
    """校验并归一化 API Host；空值合法（表示走老域名）。

    只收带主机的 https 地址：http / ftp / 缺主机 / 带 query 或 fragment 的一律
    拒绝，避免拼出 `https://host?x/geo/...` 这种坏 URL。末尾斜杠去掉，落库只存
    base，拼接规则由 `_weather_urls` 独占。
    """
    host = str(value or "").strip().rstrip("/")
    if not host:
        return ""
    parsed = urlparse(host)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "API Host 必须是 https:// 开头的地址，例如 https://xxxxxx.re.qweatherapi.com"
        )
    return host


def _weather_payload(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """对外形状（GET / PUT 共用）：只给脱敏 Key 与可公开的配置。"""
    data = config if isinstance(config, dict) else {}
    api_key = str(data.get("api_key") or "").strip()
    city = str(data.get("city") or "").strip()
    return {
        "configured": bool(api_key and city),
        "api_key_masked": _mask_api_key(api_key),
        "city": city,
        "api_host": str(data.get("api_host") or "").strip(),
        "updated_at": str(data.get("updated_at") or ""),
    }

# 人力按岗位名（POSITION_STATION_MAP 的 key）聚；认不出的工作区与没配区的都落 other。
OTHER_POSITION = "other"

# 门店 KDS 尚未上线（用户 2026-11-07 确认）。`orders.ready_time` / `is_rushed` / `is_hold`
# 只由 KDS 出餐动作写入，现在库里那几行是试用期的一次性批量确认（2026-11-07 查库：近 30
# 天 100 行、ready 与 order 相差数小时），拿它们算中位出餐时长会得出「出一份要几小时」。
# 所以厨房节奏先不采集：写 `available: false` 并跳过整天订单扫描，避免既做无用功又给出
# 假信号。采集逻辑与用例都留着，KDS 正式上线后把这里改成 True 即生效。
KDS_LIVE = False


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


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_iso_datetime(raw: Optional[str]) -> Optional[datetime]:
    """解析订单里的 ISO 时间戳；空值 / 坏值返回 None（不抛）。"""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(CHINA_TZ)


def _normalize_hhmm(value: Any) -> Optional[str]:
    """把 `HH:MM` 归一化成零填充；坏值（或越界）返回 None，由调用方回落默认。"""
    hour_text, _, minute_text = str(value or "").strip().partition(":")
    try:
        hour, minute = int(hour_text), int(minute_text[:2])
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


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

    async def lookup_location(
        self, city: str, api_key: str, url: str = QWEATHER_GEO_URL
    ) -> Optional[str]:
        payload = await self._get_json(
            url, {"location": city, "key": api_key, "number": 1}
        )
        locations = payload.get("location") or []
        if not locations:
            return None
        location_id = str(locations[0].get("id") or "").strip()
        return location_id or None

    async def daily(
        self,
        location_id: str,
        api_key: str,
        day: str,
        url: str = QWEATHER_DAILY_URL,
    ) -> Optional[Dict[str, Any]]:
        payload = await self._get_json(
            url, {"location": location_id, "key": api_key}
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
                SELECT plan_day, holiday_name, weather_json, staff_json,
                       data_quality_json, runtime_json, kitchen_json, is_workday,
                       note, captured_at, updated_at
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
            "data_quality": _loads_object(row["data_quality_json"]),
            "runtime": _loads_object(row["runtime_json"]),
            "kitchen": _loads_object(row["kitchen_json"]),
            "is_workday": None if row["is_workday"] is None else bool(row["is_workday"]),
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
        # 票 08：数据质量 / 营业时段 / 是否工作日 / 厨房节奏。其中前三项口径互相关联
        # （工作日看节假日、营业时段看配置），厨房节奏是当日订单的聚合。
        data_quality = await self._data_quality(db, plan_day)
        runtime = await self._runtime_window(db)
        kitchen = await self._kitchen_stats(db, plan_day)

        tdb = db.table("prep_plan_context")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_context (
                    plan_day, holiday_name, weather_json, staff_json,
                    data_quality_json, runtime_json, kitchen_json, is_workday,
                    captured_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    -- 这四项是**动态信号**，每次刷新都覆盖：订单数、快照覆盖小时与
                    -- 出餐行数会随一天推进而变，冻结在第一次写入就没有解释力了。
                    -- `is_workday` 跟已落库的节假日名保持一致（节假日只补空不覆盖），
                    -- 避免「节假日名是国庆节、却标成工作日」这种自相矛盾。
                    data_quality_json = EXCLUDED.data_quality_json,
                    runtime_json = EXCLUDED.runtime_json,
                    kitchen_json = EXCLUDED.kitchen_json,
                    is_workday = (
                        COALESCE(prep_plan_context.holiday_name, EXCLUDED.holiday_name)
                        IS NULL
                    ),
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    plan_day,
                    holiday_name,
                    json.dumps(weather, ensure_ascii=False) if weather else "{}",
                    json.dumps(staff, ensure_ascii=False) if staff else "{}",
                    json.dumps(data_quality, ensure_ascii=False),
                    json.dumps(runtime, ensure_ascii=False),
                    json.dumps(kitchen, ensure_ascii=False),
                    # 自然日 + 法定节假日：节假日为非工作日。周末按工作日处理
                    # （店里周末照常营业），**调休 / 补班暂不可知** —— 日历表只有
                    # 节假日，没有补班日。
                    holiday_name is None,
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

    # ---------------------------------------------------------- 天气配置

    async def read_weather_config(self, db) -> Dict[str, Any]:
        """读天气配置，只返回脱敏 Key 与页面需要的字段。"""
        config = await db.settings_get_json(WEATHER_SETTINGS_KEY, {}) or {}
        if not isinstance(config, dict):
            config = {}
        payload = _weather_payload(config)
        if not payload["updated_at"]:
            payload["updated_at"] = await db.settings_updated_at(WEATHER_SETTINGS_KEY) or ""
        return payload

    async def save_weather_config(
        self,
        db,
        *,
        api_key: Optional[str] = None,
        city: str,
        api_host: Optional[str] = None,
    ) -> Dict[str, Any]:
        """保存天气配置；空 Key 保留原值，任一关键项变了才清 `location_id`。

        未知字段原样保留（别人的键不归这里管）。`api_host` 省略表示保持原值，
        空串表示明确清空、回落老域名 —— 界面每次都会带这个字段，省略只服务脚本。
        """
        existing = await db.settings_get_json(WEATHER_SETTINGS_KEY, {}) or {}
        if not isinstance(existing, dict):
            existing = {}
        city_text = str(city or "").strip()
        if not city_text:
            raise ValueError("城市不能为空")

        old_key = str(existing.get("api_key") or "").strip()
        typed_key = str(api_key or "").strip()
        resolved_key = typed_key or old_key
        old_city = str(existing.get("city") or "").strip()
        old_host = str(existing.get("api_host") or "").strip()
        source_host = existing.get("api_host") if api_host is None else api_host
        resolved_host = _normalize_api_host(source_host)

        changed = (
            resolved_key != old_key
            or city_text != old_city
            or resolved_host != old_host
        )
        config = dict(existing)
        config["api_key"] = resolved_key
        config["city"] = city_text
        config["api_host"] = resolved_host
        if changed:
            # 新 Key 查旧城市 id、新城市复用旧 id 都会拿到错的天气，宁可重查一次 GEO。
            config["location_id"] = ""
        config["updated_at"] = datetime.now(CHINA_TZ).isoformat()

        if not await db.settings_set_json(WEATHER_SETTINGS_KEY, config):
            raise RuntimeError("天气配置保存失败")
        logger.info(
            "天气配置已保存 city=%s host=%s key=%s",
            city_text,
            resolved_host or "默认域名",
            _mask_api_key(resolved_key),
        )
        return _weather_payload(config)

    async def test_weather_config(
        self,
        db,
        *,
        api_key: Optional[str] = None,
        city: str,
        api_host: Optional[str] = None,
    ) -> Dict[str, Any]:
        """跑一次「GEO 换 id → 拉今天预报」，不写库；失败是 200 + 人话原因。"""
        city_text = str(city or "").strip()
        if not city_text:
            raise ValueError("城市不能为空")

        existing = await db.settings_get_json(WEATHER_SETTINGS_KEY, {}) or {}
        if not isinstance(existing, dict):
            existing = {}
        stored_key = str(existing.get("api_key") or "").strip()
        resolved_key = str(api_key or "").strip() or stored_key
        if not resolved_key:
            raise ValueError("请先填写 API Key")
        source_host = existing.get("api_host") if api_host is None else api_host
        resolved_host = _normalize_api_host(source_host)
        geo_url, daily_url = _weather_urls({"api_host": resolved_host})

        try:
            location_id = await self._weather_client.lookup_location(
                city_text, resolved_key, url=geo_url
            )
        except Exception as exc:
            return {"ok": False, "error": self._weather_error(exc)}
        if not location_id:
            return {
                "ok": False,
                "error": "没找到这个城市，换个写法试试（例如 广州）",
            }

        today = datetime.now(CHINA_TZ).date().isoformat()
        try:
            daily = await self._weather_client.daily(
                location_id, resolved_key, today, url=daily_url
            )
        except Exception as exc:
            return {"ok": False, "error": self._weather_error(exc)}
        if not daily:
            return {
                "ok": False,
                "error": "和风天气没返回今天的预报，检查 Key 权限或城市",
            }
        return {
            "ok": True,
            "location_id": location_id,
            "sample": {
                "fxDate": today,
                "textDay": str(daily.get("text") or ""),
                "tempMin": daily.get("temp_min"),
                "tempMax": daily.get("temp_max"),
            },
        }

    @staticmethod
    def _weather_error(exc: Exception) -> str:
        """把异常翻成人话；只记状态码与异常类型，不把 URL / 异常文本写进日志。

        httpx 的异常文本带完整请求 URL（含 `key=`），原样落日志或回前端都等于
        泄密，所以这里按类型分支，绝不 `str(exc)` 外传。
        """
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            logger.warning("和风天气连通测试失败 status=%s", status)
            if status in (401, 403):
                return "Key 无效，或这个 Key 没有天气接口权限"
            if status == 404:
                return "和风天气没找到这个城市或接口地址"
            if status == 429:
                return "和风天气请求太频繁，稍后再试"
            if 500 <= status < 600:
                return "和风天气服务暂时不可用，稍后再试"
            return f"和风天气拒绝了这次请求（HTTP {status}），检查城市与 API Host"
        if isinstance(exc, httpx.TimeoutException):
            logger.warning("和风天气连通测试超时")
            return "连接和风天气超时，检查网络或 API Host"
        if isinstance(exc, httpx.RequestError):
            logger.warning(
                "和风天气连通测试网络错误 type=%s", type(exc).__name__
            )
            return "连不上和风天气，检查网络或 API Host"
        logger.warning("和风天气连通测试失败 type=%s", type(exc).__name__)
        return "测试失败，检查 Key、城市和 API Host"

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
        if not row:
            return None
        name = (row["name"] or "").strip()
        return name or None

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

        geo_url, daily_url = _weather_urls(config)
        location_id = str(config.get("location_id") or "").strip()
        try:
            if not location_id:
                location_id = await self._weather_client.lookup_location(
                    city, api_key, url=geo_url
                )
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

            daily = await self._weather_client.daily(
                location_id, api_key, plan_day, url=daily_url
            )
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
            # 异常文本可能带请求 URL（含 key），日志只留类型，不泄密。
            logger.warning(
                "天气获取失败，保留原值 plan_day=%s city=%s type=%s",
                plan_day,
                city,
                type(exc).__name__,
            )
            return None

    async def _staff_counts(self, db, plan_day: str) -> Dict[str, int]:
        """当天有班次的人在岗数，按工作区名映射成岗位；认不出的落 other。

        工作区名单走公共层的 `ZoneDirectory`（AGENTS.md：整店共享的区名单只有这一个
        只读入口）。这里只查「按区数了几个人」，再去名单里把区 id 换成名字，
        不 import 卫生的任何东西。
        """
        tdb = db.table("staff_assignments")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT zone_id, COUNT(*) AS staff_count
                FROM staff_assignments
                WHERE business_date = ? AND shift_id IS NOT NULL
                GROUP BY zone_id
                """,
                (plan_day,),
            )
            rows = await cursor.fetchall()

        zone_names = {
            zone["id"]: zone["name"] for zone in await ZoneDirectory(db).list_zones()
        }
        counts: Dict[str, int] = {}
        for row in rows:
            zone_name = str(zone_names.get(row["zone_id"]) or "").strip()
            position = zone_name if zone_name in POSITION_STATION_MAP else OTHER_POSITION
            counts[position] = counts.get(position, 0) + int(row["staff_count"] or 0)
        return counts

    # ------------------------------------------------------------ 票 08 四个信号

    async def _data_quality(self, db, plan_day: str) -> Dict[str, Any]:
        """当天数据可不可信：订单条数 / 首末单时刻 / 快照覆盖小时 / 采集最近成功。

        窗口跟**工作台日**（自然日 00:00，ADR 0125）走：这是解释「当天为什么多做 /
        少做」的信号，不跟随复盘日 07:00 的品-天窗口（ADR 0140）。

        `scraper_last_success` 先读运行时状态里的成功时间戳；`PosSession.session_status`
        目前没有这个字段，所以回落到当日订单行最后落库时间（`orders.updated_at`）——
        它就是「采集器最后一次成功写入」的代理口径。两者都读不到才是 None。
        """
        empty = {
            "orders_today": 0,
            "first_order_at": None,
            "last_order_at": None,
            "snapshot_hours": 0,
            "scraper_last_success": self._scraper_last_success(),
        }
        try:
            start, end = workbench_day_range((plan_day or "").strip())
        except ValueError:
            return empty
        tdb = db.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT COUNT(*) AS n, MIN(order_time) AS first_at,
                       MAX(order_time) AS last_at, MAX(updated_at) AS last_write
                FROM orders
                WHERE order_time >= ? AND order_time < ?
                """,
                (start.isoformat(), end.isoformat()),
            )
            row = await cursor.fetchone()
        snapshots = db.table("prep_plan_snapshots")
        async with snapshots.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT COUNT(DISTINCT snapshot_at) AS n FROM prep_plan_snapshots "
                "WHERE plan_day = ?",
                (plan_day,),
            )
            snap = await cursor.fetchone()
        last_write = (row["last_write"] if row else None) or None
        return {
            "orders_today": _to_int(row["n"]) if row else 0,
            "first_order_at": (row["first_at"] if row else None) or None,
            "last_order_at": (row["last_at"] if row else None) or None,
            "snapshot_hours": _to_int(snap["n"]) if snap else 0,
            "scraper_last_success": self._scraper_last_success() or last_write,
        }

    @staticmethod
    def _scraper_last_success() -> Optional[str]:
        """采集器最近一次成功的时间戳（运行时状态有就用，没有返回 None）。"""
        runtime = get_runtime()
        scraper = getattr(runtime, "scraper", None) if runtime else None
        if scraper is None:
            return None
        try:
            status = scraper.get_status() or {}
        except Exception:  # 采集器状态读不到不能拖垮环境信号
            logger.warning("读取采集器状态失败，scraper_last_success 留空", exc_info=True)
            return None
        if not isinstance(status, dict):
            return None
        for key in ("last_success_at", "last_cycle_at"):
            value = status.get(key)
            if value:
                return str(value)
        return None

    async def _runtime_window(self, db) -> Dict[str, str]:
        """当天的营业时段与来源：`prep_plan_runtime` → `scraper_runtime` → 默认。

        只回落到**命中的配置键**（哪怕某个字段是坏值、单独回落到默认）：`source`
        用来解释「当天按什么口径算的」，所以宁可标 `prep_plan_runtime` 也不谎称默认。
        """
        primary = await db.settings_get_json(RUNTIME_SETTINGS_KEY, None)
        if isinstance(primary, dict) and any(
            primary.get(key) for key in ("open", "close", "work_start", "work_end")
        ):
            return {
                "open": _normalize_hhmm(primary.get("open") or primary.get("work_start"))
                or DEFAULT_OPEN,
                "close": _normalize_hhmm(primary.get("close") or primary.get("work_end"))
                or DEFAULT_CLOSE,
                "source": "prep_plan_runtime",
            }
        fallback = await db.settings_get_json(FALLBACK_RUNTIME_SETTINGS_KEY, {}) or {}
        if isinstance(fallback, dict) and any(
            fallback.get(key) for key in ("work_start", "work_end")
        ):
            return {
                "open": _normalize_hhmm(fallback.get("work_start")) or DEFAULT_OPEN,
                "close": _normalize_hhmm(fallback.get("work_end")) or DEFAULT_CLOSE,
                "source": "scraper_runtime",
            }
        return {"open": DEFAULT_OPEN, "close": DEFAULT_CLOSE, "source": "default"}

    async def _kitchen_stats(self, db, plan_day: str) -> Dict[str, Any]:
        """当天厨房节奏：已出餐行数 / 中位出餐分钟 / 催菜行数 / 等叫行数。

        窗口与 `_data_quality` 一样跟**工作台日**（自然日 00:00）走，不跟复盘日 07:00
        窗口：厨房节奏解释的是「当天现场什么状态」，不是品-天的销量口径（ADR 0140）。

        `ready_time` 非空才算已出餐；时长 = `ready_time - order_time`，坏时间戳与
        「ready 早于 order」的坏行跳过、不参与中位数。一行有效时长都没有时中位数是
        None —— 写 0 会被读成「瞬间出餐」。

        **KDS 未上线时不做采集**（见 `KDS_LIVE`）：这四项的信号源只有 KDS 的出餐动作，
        现在库里那点 `ready_time` 是试用期批量补出来的，算出来的中位时长会把复盘带偏。
        此时返回 `available: false` + 全 0，并跳过整天订单扫描；KDS 上线后把
        `KDS_LIVE` 改成 True 就恢复真实聚合。
        """
        empty = {
            "available": False,
            "ready_lines": 0,
            "median_ready_minutes": None,
            "rushed_lines": 0,
            "hold_lines": 0,
        }
        if not KDS_LIVE:
            return {**empty, "reason": "kds_not_live"}
        try:
            start, end = workbench_day_range((plan_day or "").strip())
        except ValueError:
            return empty
        tdb = db.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT order_time, ready_time, is_rushed, is_hold
                FROM orders
                WHERE order_time >= ? AND order_time < ?
                """,
                (start.isoformat(), end.isoformat()),
            )
            rows = await cursor.fetchall()
        durations = []
        ready_lines = 0
        rushed_lines = 0
        hold_lines = 0
        for row in rows:
            if _to_int(row["is_rushed"]):
                rushed_lines += 1
            if _to_int(row["is_hold"]):
                hold_lines += 1
            if not (row["ready_time"] or "").strip():
                continue
            ready_lines += 1
            order_at = _parse_iso_datetime(row["order_time"])
            ready_at = _parse_iso_datetime(row["ready_time"])
            if order_at is None or ready_at is None:
                continue
            minutes = (ready_at - order_at).total_seconds() / 60.0
            if minutes < 0:
                continue
            durations.append(minutes)
        return {
            "available": True,
            "ready_lines": ready_lines,
            "median_ready_minutes": (
                round(statistics.median(durations), 1) if durations else None
            ),
            "rushed_lines": rushed_lines,
            "hold_lines": hold_lines,
        }


prep_plan_context = PrepPlanContextService()


# 模块级入口：票面约定的服务层 API。路由与测试都走这里，注入的客户端统一来自
# 上面的单例，避免在路由里 new 客户端、也避免两套配置。
async def read_weather_config(db) -> Dict[str, Any]:
    return await prep_plan_context.read_weather_config(db)


async def save_weather_config(
    db,
    *,
    api_key: Optional[str] = None,
    city: str,
    api_host: Optional[str] = None,
) -> Dict[str, Any]:
    return await prep_plan_context.save_weather_config(
        db, api_key=api_key, city=city, api_host=api_host
    )


async def test_weather_config(
    db,
    *,
    api_key: Optional[str] = None,
    city: str,
    api_host: Optional[str] = None,
) -> Dict[str, Any]:
    return await prep_plan_context.test_weather_config(
        db, api_key=api_key, city=city, api_host=api_host
    )
