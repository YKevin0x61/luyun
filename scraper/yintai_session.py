#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YintaiSession：用移动银台 HTTP 接口替代 PosSession 的取数职责（无浏览器）。

只实现 ``TableChangeDetector`` / ``RestaurantScraper`` 真正用到的接口子集，
方法名与语义与 ``PosSession`` 对齐，因此上层检测器不需要改动：

- ``scrape_table_data()``    → 餐桌列表，**取材失败返回 None**（与 PosSession 同一套语义）
- ``resolve_point_id()``     → 桌号 → pointId
- ``fetch_table_orders()``   → 单桌点菜明细 → 入库订单行

明细解析复用 ``scraper.order_parse.parse_api_order_response``，与 cy7mm 链路同一套
去重/退菜/套菜逻辑，这里只把移动银台的响应转成它认识的形状。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from scraper._common import CHINA_TZ
from scraper.order_parse import parse_api_order_response
from scraper.yintai_auth import YintaiSession as YintaiAuthSession, login_yintai
from scraper.yintai_client import (
    YintaiAuthError,
    YintaiClient,
    YintaiCredentials,
    YintaiError,
)

logger_ = logging.getLogger(__name__)

_DEFAULT_CONFIG: Dict[str, Any] = {
    "settings": {
        "headless": True,
        "timeout": 30000,
        "retry_count": 3,
        "interval_min": 5,
        "interval_max": 20,
        "delivery_cancel_miss_threshold": 3,
    },
    "business_hours": {"work_start": "07:30", "work_end": "21:30"},
}

#: 移动银台返回的 orderTime 是 ISO 片段（``2026-10-29T13:42:44.407``），
#: 而共用解析器按 ``HH:MM`` 拼当天日期，这里先归一。
_HHMM_SLICE = slice(11, 16)


def _to_hhmm(value: Any) -> str:
    """``2026-10-29T13:42:44.407`` → ``13:42``；已是 ``HH:MM`` 则原样返回。"""
    if not value:
        return ""
    text = str(value)
    if "T" in text and len(text) >= 16:
        return text[_HHMM_SLICE]
    if len(text) == 5 and text[2] == ":":
        return text
    return ""


def to_legacy_detail_shape(
    item_rows: List[Dict[str, Any]],
    *,
    table_number: str,
    bill_id: str = "",
    people_qty: Any = None,
) -> Dict[str, Any]:
    """把 ``mergebillview`` 的 ``itemList`` 转成共用解析器认识的形状。"""
    sc_detail: List[Dict[str, Any]] = []
    for row in item_rows or []:
        if not isinstance(row, dict):
            continue
        quantity = float(row.get("lastQty") or 0)
        subtotal = float(row.get("lastSubtotal") or 0)
        unit_price = (subtotal / quantity) if quantity else subtotal
        sc_detail.append(
            {
                "itemName": row.get("itemName") or row.get("name") or "",
                "lastQty": quantity,
                "lastPrice": unit_price,
                "orderTime": _to_hhmm(row.get("orderTime")),
                "methodText": row.get("itemSizeName") or "",
            }
        )
    return {
        "success": True,
        "data": {
            "bsCode": bill_id or "",
            "pointName": table_number,
            "peopleQty": people_qty,
            "scDetail": sc_detail,
        },
    }


class YintaiSession:
    """不依赖浏览器的采集会话。"""

    def __init__(self, dish_catalog, *, logger: Optional[logging.Logger] = None):
        from scraper.order_line_builder import OrderLineBuilder

        self.logger = logger or logging.getLogger(__name__)
        self.dish_catalog = dish_catalog
        self.order_lines = OrderLineBuilder(dish_catalog)
        self.config: Dict[str, Any] = {
            "settings": dict(_DEFAULT_CONFIG["settings"]),
            "business_hours": dict(_DEFAULT_CONFIG["business_hours"]),
        }
        self._creds = None
        self._client: Optional[YintaiClient] = None
        self._session: Optional[YintaiAuthSession] = None
        self._point_index: Dict[str, Dict[str, Any]] = {}
        self._no_credentials = True
        self.paused = False
        self.last_auth_error: str = ""
        self._consecutive_failures = 0

    # ---------------------------------------------------------------- lifecycle
    def inject_credentials(self, bundle) -> None:
        self._creds = bundle
        self._no_credentials = bundle is None
        self._client = None
        self._point_index.clear()

    @property
    def page(self):
        """移动银台不依赖浏览器；保留属性以兼容调用方读取。"""
        return None

    @property
    def context(self):
        """移动银台没有 Playwright context；保留属性以兼容调用方。"""
        return None

    @property
    def consecutive_login_failures(self) -> int:
        """连续登录失败次数，供回退 / 告警判定使用。"""
        return self._consecutive_failures

    def force_unpaused(self) -> None:
        """绕过营业时间门禁（CLI / 认证探针）。"""
        self.paused = False

    def _is_business_hours(self) -> bool:
        """按 work_start/work_end 判断当前是否营业中；配置异常时放行。"""
        try:
            current = datetime.now(CHINA_TZ).strftime("%H:%M")
            hours = self.config.get("business_hours") or {}
            start = str(hours.get("work_start") or "07:30")
            end = str(hours.get("work_end") or "21:30")
            if start <= end:
                return start <= current < end
            return current >= start or current < end
        except Exception:  # noqa: BLE001 - 配置异常不应拦住采集
            return True

    def refresh_business_hours(self) -> bool:
        """按营业时间重算暂停状态；返回当前是否营业中。"""
        open_now = self._is_business_hours()
        self.paused = not open_now
        return open_now

    def load_credentials_sync(self) -> None:
        """从磁盘刷新凭据缓存（同步，供空闲循环调用）。"""
        try:
            from services import credentials_store

            bundle = credentials_store.reload()
        except Exception as exc:  # noqa: BLE001 - 读不到就保持现状
            self.logger.warning("移动银台：读取凭据失败 %s", exc)
            return
        self.inject_credentials(bundle)

    async def reload_credentials(self) -> bool:
        """凭据被外部更新后调用：刷新缓存并作废当前会话，下次 ensure_ready 重登。"""
        previous = self._creds
        try:
            from services import credentials_store

            bundle = credentials_store.reload()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("移动银台：重新读取凭据失败 %s", exc)
            return False
        self.inject_credentials(bundle)
        if bundle is None:
            self.logger.warning("🧹 凭据已清空，移动银台进入待机状态")
            return False
        changed = (
            previous is None
            or getattr(previous, "phone", None) != bundle.phone
            or getattr(previous, "password", None) != bundle.password
            or getattr(previous, "extra", None) != bundle.extra
        )
        if changed:
            self._consecutive_failures = 0
            self.logger.info("♻️ 凭据已更新，下一轮将重新登录移动银台")
        return True

    def apply_runtime_settings(self, data: dict) -> None:
        """把运行配置字典应用到内存 ``self.config``（只覆盖存在的字段）。"""
        hours = self.config.setdefault("business_hours", {})
        if data.get("work_start"):
            hours["work_start"] = data["work_start"]
        if data.get("work_end"):
            hours["work_end"] = data["work_end"]
        settings = self.config.setdefault("settings", {})
        for source_key, target_key in (
            ("timeout_ms", "timeout"),
            ("retry_count", "retry_count"),
            ("interval_min", "interval_min"),
            ("interval_max", "interval_max"),
            ("delivery_cancel_miss_threshold", "delivery_cancel_miss_threshold"),
        ):
            if source_key in data:
                settings[target_key] = int(data[source_key])

    async def reload_runtime_settings(self, db) -> dict:
        """从 DB 重新加载运行配置并应用到内存。"""
        from services.runtime_settings import load_runtime_settings

        data = await load_runtime_settings(db)
        self.apply_runtime_settings(data)
        self.logger.info(
            "♻️ 运行配置已生效：营业 %s-%s，轮询 %s~%ss",
            self.config["business_hours"]["work_start"],
            self.config["business_hours"]["work_end"],
            self.config["settings"]["interval_min"],
            self.config["settings"]["interval_max"],
        )
        return data

    async def login(self, phone: str, password: str) -> bool:
        """显式账号密码登录（CLI / 认证探针）；凭据里已有账号时等价于 ensure_ready。"""
        return await self.ensure_ready()

    async def init_browser(self, headless: bool = True) -> bool:
        """移动银台不需要浏览器；保留签名以兼容调用方。"""
        return True

    async def release_browser(self) -> bool:
        await self.close()
        return True

    async def probe_bs_detail_api(self):
        """cy7mm 专属探针，移动银台无对应能力。"""
        return None

    async def probe_busy_point_api_login_ok(self):
        """cy7mm 专属探针，移动银台无对应能力。"""
        return None

    def session_status(self) -> Dict[str, Any]:
        """采集会话状态快照（供 /api/scraper 类接口使用）。"""
        return {
            "source": "yintai",
            "logged_in": self._session is not None,
            "dev_code": getattr(self._session, "dev_code", None),
            "user": getattr(self._session, "user_name", None),
            "paused": self.paused,
            "consecutive_login_failures": self._consecutive_failures,
            "last_auth_error": self.last_auth_error,
        }

    @property
    def no_credentials(self) -> bool:
        return self._no_credentials

    @property
    def http(self):
        """无语义，仅为兼容 ``PosSession.http.api_failures`` 的读取点。"""

        class _Null:
            api_failures = 0

        return _Null()

    def _ensure_client(self) -> YintaiClient:
        if self._client is not None:
            return self._client
        if self._creds is None:
            raise YintaiAuthError("未注入移动银台凭据")
        creds = YintaiCredentials.from_bundle(self._creds)
        self._client = YintaiClient(creds)
        return self._client

    async def ensure_ready(self, *, ignore_pause: bool = False) -> bool:
        """确保已有可用会话；必要时用账号密码走一遍完整登录。"""
        if self._client is not None:
            self._consecutive_failures = 0
            return True
        # 营业时间门禁：非营业时段门店 POS 不会开班，此时登录必然失败，
        # 不能让它累计成「连续失败」去触发回退 cy7mm。
        if not ignore_pause and self.paused:
            self.logger.debug("移动银台：非营业时段，跳过登录")
            return False
        bundle = self._creds
        if bundle is None:
            self.logger.warning("⚠️ 移动银台：尚未配置凭据")
            return False
        try:
            self._client = self._ensure_client()
            return True
        except YintaiAuthError:
            pass  # 没有现成 accesskey，走登录

        extra = (getattr(bundle, "extra", None) or {}).get("yintai") or {}
        base_url = str(extra.get("base_url") or "").rstrip("/")
        user_name = str(extra.get("user_name") or getattr(bundle, "phone", "") or "")
        password = str(extra.get("password") or getattr(bundle, "password", "") or "")
        if not base_url or not user_name or not password:
            self.last_auth_error = "缺少 base_url / user_name / password"
            self._consecutive_failures += 1
            self.logger.error("❌ 移动银台登录参数不全：%s", self.last_auth_error)
            return False
        # 复用已登记的 devId：每次登录都新注册一个设备会让门店设备号无限增长
        saved_dev_id = str(extra.get("dev_id") or "").strip() or None
        try:
            session = await login_yintai(
                base_url, user_name=user_name, password=password, dev_id=saved_dev_id
            )
        except Exception as exc:  # noqa: BLE001 - 登录失败一律按取材失败处理
            self.last_auth_error = str(exc)
            self._consecutive_failures += 1
            self.logger.error(
                "❌ 移动银台登录失败（连续 %s 次）：%s", self._consecutive_failures, exc
            )
            return False

        self._session = session
        if isinstance(extra, dict) and session.dev_id:
            extra["dev_id"] = session.dev_id
        self._client = YintaiClient(
            YintaiCredentials(
                base_url=session.base_url,
                dev_id=session.dev_id,
                access_key=session.access_key,
                access_token=session.access_token,
                dev_code=session.dev_code,
                user_id=session.user_id,
            )
        )
        self._consecutive_failures = 0
        self.logger.info("✅ 移动银台登录成功：%s（devCode=%s）", session.user_name, session.dev_code)
        return True

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------- 取数
    async def scrape_table_data(self) -> Optional[List[Dict[str, Any]]]:
        """餐桌列表；取材失败返回 None，正常但无桌返回 []。"""
        if not await self.ensure_ready():
            return None
        try:
            states = await self._ensure_client().fetch_point_states()
        except YintaiAuthError as exc:
            self.last_auth_error = str(exc)
            self.logger.error("❌ 移动银台鉴权失效：%s", exc)
            return None
        except YintaiError as exc:
            self.logger.error("❌ 移动银台餐桌取材失败：%s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 - 取材失败一律整轮跳过
            self.logger.error("❌ 移动银台餐桌取材异常：%s", exc)
            return None

        self._point_index = {
            s.point_name: {
                "point_id": s.point_id,
                "bill_id": s.bill_id,
                "people_qty": s.people_qty,
                "amount": s.last_total,
                "occupied": s.is_occupied,
            }
            for s in states
        }
        tables = [
            {
                "table_number": s.point_name,
                "amount": s.last_total or 0.0,
                "point_id": s.point_id,
                "bill_id": s.bill_id,
                "people_qty": s.people_qty,
                "progress": s.serving_progress,
            }
            for s in states
            if s.is_occupied
        ]
        self.logger.info("✅ 移动银台餐桌 %s 张（有单 %s 张）", len(states), len(tables))
        return tables

    def resolve_point_id(self, table_number: str) -> str:
        info = self._point_index.get(str(table_number)) or {}
        return str(info.get("point_id") or "")

    async def fetch_table_orders(
        self, table_number: str, point_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """单桌点菜明细；没拿到返回 None，接口正常但无菜返回 []。"""
        if not point_id:
            self.logger.warning("⚠️ 餐桌 %s 缺少 pointId，跳过点菜明细", table_number)
            return None
        info = self._point_index.get(str(table_number)) or {}
        bill_id = str(info.get("bill_id") or "")
        if not bill_id:
            # 已开台但还没生成账单：接口正常回答「这桌没菜」
            self.logger.info("ℹ️ 餐桌 %s 暂无账单号，视为没有明细", table_number)
            return []

        try:
            rows = await self._ensure_client().fetch_bill_items(point_id, bill_id)
        except YintaiAuthError as exc:
            self.last_auth_error = str(exc)
            self.logger.error("❌ 移动银台鉴权失效（明细）：%s", exc)
            return None
        except YintaiError as exc:
            self.logger.error("❌ 餐桌 %s 点菜明细失败：%s", table_number, exc)
            return None

        payload = to_legacy_detail_shape(
            rows,
            table_number=str(table_number),
            bill_id=bill_id,
            people_qty=info.get("people_qty"),
        )
        return await parse_api_order_response(
            payload,
            str(table_number),
            order_lines=self.order_lines,
            logger=self.logger,
        )


__all__ = ["YintaiSession", "to_legacy_detail_shape"]
