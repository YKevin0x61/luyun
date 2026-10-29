#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
餐厅采集组合根：拥有一轮编排与状态 DTO，委托 PosSession / Detector / Tracker / StateStore。

不是 1:1 转发 facade —— 单轮采集逻辑在 ``run_cycle``；对账/探针用本类公开方法。
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any, Dict, List, Optional

from scraper._common import CHINA_TZ, ScraperSessionError
from scraper.delivery_bill_tracker import DeliveryBillTracker
from scraper.pos_session import PosSession
from scraper.state_store import ScraperStateStore
from scraper.table_change_detector import TableChangeDetector
from scraper.yintai_session import YintaiSession

logger = logging.getLogger(__name__)


class _DisabledDelivery:
    """移动银台模式下的外卖/已结账单占位实现。

    第一步切换范围只包含「桌台 + 菜品明细」，外卖链路（``DeliveryBillTracker``）
    仍绑定 cy7mm 的 ``session.http`` / ``session.page``，在移动银台下不可用。
    这里给出与 ``DeliveryBillTracker`` 同形状的空实现，调用方无需分支判断：
    所有取数方法返回空列表，取消计数恒为 0。
    """

    def __init__(self, logger):
        self._logger = logger
        self._warned = False

    def _warn_once(self) -> None:
        if not self._warned:
            self._warned = True
            self._logger.info(
                "ℹ️ 移动银台模式：外卖/已结账单链路未接入（第一步只切桌台+菜品明细），本轮跳过"
            )

    @property
    def last_delivery_cancel_count(self) -> int:
        return 0

    def biz_datetime_range(self, biz_date=None):
        """与 DeliveryBillTracker 同形状：营业日 06:00 起止。"""
        from datetime import datetime, timedelta

        from scraper._common import CHINA_TZ

        if biz_date:
            year, month, day = (int(part) for part in biz_date.split("-"))
            begin = datetime(year, month, day, 6, 0, 0, tzinfo=CHINA_TZ)
        else:
            now = datetime.now(CHINA_TZ)
            begin = now.replace(hour=6, minute=0, second=0, microsecond=0)
            if now.hour < 6:
                begin -= timedelta(days=1)
        return begin, begin + timedelta(days=1)

    async def probe_settled_bill_apis(self) -> Dict[str, Any]:
        return {
            "supported": False,
            "reason": "移动银台模式下外卖/已结账单链路未接入",
        }

    async def fetch_settled_bill_list(self, begin, end, delivery_only: bool = False):
        self._warn_once()
        return []

    async def fetch_settled_bill_raw_dishes(self, bill, begin, end):
        self._warn_once()
        return []

    async def fetch_settled_bills_for_biz_date(self, biz_date=None):
        self._warn_once()
        return []

    async def fetch_delivery_order_lines(self, bill):
        self._warn_once()
        return []

    async def maybe_sweep_previous_biz_date(self, db) -> None:
        self._warn_once()

    async def scrape_delivery_orders(self, db=None):
        self._warn_once()
        return []


class RestaurantScraper:
    """Composition root for POS scrape + reconcile surface."""

    def __init__(self, dish_catalog):
        self.logger = logging.getLogger(__name__)
        self.dish_catalog = dish_catalog

        self.state = ScraperStateStore(logger_=self.logger)
        self.pos_source = self._resolve_pos_source()
        if self.pos_source == "yintai":
            self.session = YintaiSession(dish_catalog, logger=self.logger)
            # 外卖/已结账单仍绑定 cy7mm 会话，移动银台下用占位实现（第一步范围）
            self.delivery = _DisabledDelivery(self.logger)
            self.logger.info("🔌 采集数据源：移动银台 Pro（HTTP，无浏览器；外卖暂未接入）")
        else:
            self.session = PosSession(dish_catalog, logger_=self.logger)
            self.delivery = DeliveryBillTracker(self.session, self.state, logger_=self.logger)
            self.logger.info("🔌 采集数据源：cy7mm（Playwright）")
        self.tables = TableChangeDetector(self.session, self.state, logger_=self.logger)

        self.logger.info("✅ 餐厅采集组合根初始化成功")

    @staticmethod
    def _resolve_pos_source() -> str:
        """解析采集数据源；未知值退回 cy7mm，避免配置写错把采集打停。

        优先级：凭据里保存的 ``extra.pos_source``（管理界面切换） > 环境变量
        ``SCRAPER_POS_SOURCE``。前者让门店能不重启服务就切数据源。
        """
        try:
            from services import credentials_store

            bundle = credentials_store.get_credentials()
            stored = str((getattr(bundle, "extra", None) or {}).get("pos_source") or "")
            stored = stored.strip().lower()
            if stored in ("cy7mm", "yintai"):
                return stored
        except Exception:  # noqa: BLE001 - 凭据读不到时退回环境变量
            pass
        try:
            from config import settings

            raw = str(getattr(settings, "SCRAPER_POS_SOURCE", "cy7mm") or "cy7mm")
        except Exception:  # noqa: BLE001 - 配置读不到时用默认值
            raw = "cy7mm"
        value = raw.strip().lower()
        return value if value in ("cy7mm", "yintai") else "cy7mm"

    # ---- lifecycle / config ----

    async def ensure_ready(self, *, ignore_pause: bool = False) -> bool:
        return await self.session.ensure_ready(ignore_pause=ignore_pause)

    def load_credentials_sync(self) -> None:
        self.session.load_credentials_sync()

    async def reload_credentials(self) -> bool:
        return await self.session.reload_credentials()

    def apply_runtime_settings(self, data: dict) -> None:
        self.session.apply_runtime_settings(data)

    async def reload_runtime_settings(self, db) -> dict:
        return await self.session.reload_runtime_settings(db)

    def refresh_business_hours(self) -> bool:
        return self.session.refresh_business_hours()

    async def release_browser(self) -> bool:
        return await self.session.release_browser()

    async def close(self) -> None:
        await self.session.close()

    def inject_credentials(self, bundle) -> None:
        self.session.inject_credentials(bundle)

    def prepare_unrestricted_probe(self) -> None:
        """Open hours + unpause for CLI / setup login probes."""
        self.session.config.setdefault("settings", {})["headless"] = True
        self.session.config.setdefault("business_hours", {})
        self.session.config["business_hours"]["work_start"] = "00:00"
        self.session.config["business_hours"]["work_end"] = "23:59"
        self.session.force_unpaused()

    async def init_browser(self, headless: bool = True):
        return await self.session.init_browser(headless=headless)

    async def login(self, phone: str, password: str) -> bool:
        return await self.session.login(phone, password)

    # ---- status ----

    @property
    def paused(self) -> bool:
        return self.session.paused

    @property
    def no_credentials(self) -> bool:
        return self.session.no_credentials

    @property
    def config(self) -> dict:
        return self.session.config

    @property
    def settled_api_failures(self) -> int:
        return self.session.http.api_failures

    @property
    def last_delivery_cancel_count(self) -> int:
        return self.delivery.last_delivery_cancel_count

    @property
    def page(self):
        return self.session.page

    @property
    def context(self):
        return self.session.context

    @property
    def table_list_url(self) -> str:
        return self.get_status().get("table_list_url") or ""

    def get_status(self) -> Dict[str, Any]:
        return self.session.session_status()

    def poll_interval_seconds(self) -> int:
        settings = self.config.get("settings", {})
        interval_min = int(settings.get("interval_min", 5))
        interval_max = int(settings.get("interval_max", 20))
        if interval_min > interval_max:
            interval_min, interval_max = interval_max, interval_min
        return random.randint(interval_min, interval_max)

    # ---- reconcile / settled surface ----

    def biz_datetime_range(self, biz_date: Optional[str] = None):
        return self.delivery.biz_datetime_range(biz_date)

    async def fetch_settled_bill_list(self, begin, end, delivery_only: bool = False):
        return await self.delivery.fetch_settled_bill_list(
            begin, end, delivery_only=delivery_only
        )

    async def fetch_settled_bill_raw_dishes(self, bill, begin, end):
        return await self.delivery.fetch_settled_bill_raw_dishes(bill, begin, end)

    async def fetch_delivery_order_lines(self, bill):
        return await self.delivery.fetch_delivery_order_lines(bill)

    async def fetch_settled_bills_for_biz_date(self, biz_date: Optional[str] = None):
        return await self.delivery.fetch_settled_bills_for_biz_date(biz_date)

    async def scrape_delivery_orders(self, db=None) -> List[Dict]:
        return await self.delivery.scrape_delivery_orders(db=db)

    async def probe_settled_bill_apis(self) -> Dict[str, Any]:
        return await self.delivery.probe_settled_bill_apis()

    async def probe_busy_point_api_login_ok(self) -> Dict[str, Any]:
        return await self.session.probe_busy_point_api_login_ok()

    async def probe_bs_detail_api(self) -> Dict[str, Any]:
        return await self.session.probe_bs_detail_api()

    async def scrape_table_data(self) -> Optional[List[Dict]]:
        """餐桌取材（与 `PosSession.scrape_table_data` 的三态契约一致）。

        - ``None``：这一轮没拿到（取材失败）——调用方跳过本轮餐桌路径，不推进任何桌的状态；
        - ``[]``：取材正常，但当前没有餐桌；
        - 非空列表：正常的餐桌快照。
        """
        return await self.session.scrape_table_data()

    async def monitor_table_orders(
        self,
        current_tables_data: Optional[List[Dict]] = None,
        orders=None,
    ) -> List[Dict]:
        return await self.tables.monitor_table_orders(
            current_tables_data, orders=orders
        )

    # ---- owned orchestration ----

    async def run_cycle(self, db) -> None:
        """One scrape cycle: ready → tables → monitor → delivery → health.

        Raises ScraperSessionError when login failures exceed alert threshold.
        """
        from config import settings
        from services.realtime.hub import realtime_hub
        from services.scraper_health import update_runtime_health

        # 本轮起点：`api_failures` 是进程内累计值，健康状态里写的是本轮增量
        # （消费方拿它判断「这一轮有没有硬失败」，见 CORR-03）。
        api_failures_before = self.settled_api_failures

        if not await self.ensure_ready():
            login_failures = self.session.consecutive_login_failures
            if login_failures >= settings.SCRAPER_ALERT_FAILURE_THRESHOLD:
                raise ScraperSessionError(
                    f"连续 {login_failures} 次登录失败，POS 会话无法建立"
                )
            return

        tables_data = await self.scrape_table_data()

        if tables_data is None:
            # 「取材失败」与「取材正常但没桌」（[]）是两件事（PosSession 的三态契约）：
            # 失败轮跳过餐桌路径——不落库、不广播、不推进任何桌的状态；外卖路径照常跑。
            self.logger.warning("⚠️ 餐桌取材失败，本轮跳过，不推进任何桌的状态")
        elif tables_data and db:
            await db.save_table_data(tables_data)
            self.logger.info("✅ 保存 %s 条餐桌数据", len(tables_data))
            await realtime_hub.broadcast_nudge("tables", {"count": len(tables_data)})

            self.logger.info("🎯 调用智能监控方法 monitor_table_orders()")
            all_orders = await self.monitor_table_orders(tables_data, orders=db.orders)
            self.logger.info(
                "🎯 智能监控返回 %s 个订单项目",
                len(all_orders) if all_orders else 0,
            )

            if all_orders:
                await db.orders.save_orders(all_orders)
                self.logger.info("✅ 保存 %s 条订单数据", len(all_orders))
                await realtime_hub.broadcast_nudge(
                    "orders", {"count": len(all_orders), "source": "table"}
                )
            elif self.tables.last_dine_in_port_updates:
                await realtime_hub.broadcast_nudge(
                    "orders",
                    {
                        "count": self.tables.last_dine_in_port_updates,
                        "source": "dine_in_cancel",
                    },
                )
        elif not tables_data:
            # []：取材正常，只是当前没有餐桌。不落库、不广播，也不告警。
            self.logger.info("ℹ️ 取材正常但当前没有餐桌，本轮跳过餐桌路径")

        delivery_orders = await self.scrape_delivery_orders(db=db)
        if delivery_orders:
            await db.orders.save_orders(delivery_orders)
            self.logger.info("✅ 保存 %s 条外卖订单数据", len(delivery_orders))
            await realtime_hub.broadcast_nudge(
                "orders", {"count": len(delivery_orders), "source": "delivery"}
            )

        cancel_changes = self.last_delivery_cancel_count
        if cancel_changes:
            self.logger.info("🚫 外卖取消/自愈影响 %s 行，广播刷新", cancel_changes)
            await realtime_hub.broadcast_nudge(
                "orders", {"count": cancel_changes, "source": "delivery_cancel"}
            )

        update_runtime_health(
            api_failures=max(self.settled_api_failures - api_failures_before, 0),
            last_scrape_at=datetime.now(CHINA_TZ).isoformat(),
        )


async def create_restaurant_scraper(dish_catalog) -> Optional[RestaurantScraper]:
    try:
        scraper = RestaurantScraper(dish_catalog)
        logger.info("✅ 餐厅采集组合根创建成功")
        return scraper
    except Exception as e:
        logger.error("❌ 初始化餐厅采集组合根失败: %s", e)
        return None
