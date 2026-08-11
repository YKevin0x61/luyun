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

logger = logging.getLogger(__name__)


class RestaurantScraper:
    """Composition root for POS scrape + reconcile surface."""

    def __init__(self, dish_catalog):
        self.logger = logging.getLogger(__name__)
        self.dish_catalog = dish_catalog

        self.state = ScraperStateStore(logger_=self.logger)
        self.session = PosSession(dish_catalog, logger_=self.logger)
        self.tables = TableChangeDetector(self.session, self.state, logger_=self.logger)
        self.delivery = DeliveryBillTracker(self.session, self.state, logger_=self.logger)

        self.logger.info("✅ 餐厅采集组合根初始化成功")

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

    async def scrape_table_data(self) -> List[Dict]:
        return await self.session.scrape_table_data()

    async def monitor_table_orders(
        self, current_tables_data: Optional[List[Dict]] = None
    ) -> List[Dict]:
        return await self.tables.monitor_table_orders(current_tables_data)

    # ---- owned orchestration ----

    async def run_cycle(self, db) -> None:
        """One scrape cycle: ready → tables → monitor → delivery → health.

        Raises ScraperSessionError when login failures exceed alert threshold.
        """
        from config import settings
        from services.realtime.hub import realtime_hub
        from services.scraper_health import update_runtime_health

        if not await self.ensure_ready():
            login_failures = self.session.consecutive_login_failures
            if login_failures >= settings.SCRAPER_ALERT_FAILURE_THRESHOLD:
                raise ScraperSessionError(
                    f"连续 {login_failures} 次登录失败，POS 会话无法建立"
                )
            return

        tables_data = await self.scrape_table_data()

        if tables_data and db:
            await db.save_table_data(tables_data)
            self.logger.info("✅ 保存 %s 条餐桌数据", len(tables_data))
            await realtime_hub.broadcast_nudge("tables", {"count": len(tables_data)})

            self.logger.info("🎯 调用智能监控方法 monitor_table_orders()")
            all_orders = await self.monitor_table_orders(tables_data)
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
            api_failures=self.settled_api_failures,
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
