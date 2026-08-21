#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scraper state persistence: table snapshots and delivery-bill dedupe files."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Dict, Optional

from scraper._common import CHINA_TZ, DATA_DIR

logger = logging.getLogger(__name__)


def _json_default(obj):
    """Serialize datetime values that appear in table-order snapshots."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def previous_biz_date_of(biz_date: str) -> str:
    year, month, day = (int(part) for part in biz_date.split("-"))
    return (date(year, month, day) - timedelta(days=1)).isoformat()


class ScraperStateStore:
    """Owns in-memory + on-disk table / delivery tracking state (06:00 biz-day rollover)."""

    def __init__(
        self,
        *,
        table_state_file: Optional[str] = None,
        delivery_bills_file: Optional[str] = None,
        logger_: Optional[logging.Logger] = None,
    ):
        self.logger = logger_ or logger
        self._table_state_file = table_state_file or str(DATA_DIR / "table_state.json")
        self._delivery_bills_file = delivery_bills_file or str(DATA_DIR / "delivery_bills.json")

        self.previous_tables_state: Dict = {}
        self.previous_table_orders: Dict = {}
        self.is_first_run = True

        self.delivery_bill_state: dict = {}
        self.collected_delivery_bills: set = set()
        self.last_prev_day_cancel_sweep_biz_date: str = ""

        self.load_table_state()
        self.collected_delivery_bills = self.load_delivery_bills()

    def current_biz_date(self) -> str:
        now = datetime.now(CHINA_TZ)
        if now.hour < 6:
            d = now.date() - timedelta(days=1)
        else:
            d = now.date()
        return d.isoformat()

    def previous_biz_date(self) -> str:
        return previous_biz_date_of(self.current_biz_date())

    def load_table_state(self) -> None:
        biz_date = self.current_biz_date()
        if not os.path.exists(self._table_state_file):
            return
        try:
            with open(self._table_state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved_date = data.get("biz_date", "")
            if saved_date != biz_date:
                self.logger.info(
                    f"🗓️  营业日切换（{saved_date} → {biz_date}），清空餐桌状态"
                )
                self.previous_tables_state = {}
                self.previous_table_orders = {}
                self.is_first_run = True
                return
            self.previous_tables_state = data.get("table_states", {})
            self.previous_table_orders = data.get("table_orders", {})
            self.is_first_run = data.get("is_first_run", False)
            self.logger.info(
                f"📂 加载餐桌状态: {len(self.previous_tables_state)} 张桌, "
                f"is_first_run={self.is_first_run}"
            )
        except Exception as e:
            self.logger.warning(f"⚠️  加载餐桌状态失败: {e}")

    def save_table_state(self) -> None:
        try:
            data = {
                "biz_date": self.current_biz_date(),
                "table_states": self.previous_tables_state,
                "table_orders": self.previous_table_orders,
                "is_first_run": self.is_first_run,
            }
            with open(self._table_state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=_json_default)
        except Exception as e:
            self.logger.warning(f"⚠️  保存餐桌状态失败: {e}")

    def load_delivery_bills(self) -> set:
        self.delivery_bill_state = {}
        self.last_prev_day_cancel_sweep_biz_date = ""
        biz_date = self.current_biz_date()
        if not os.path.exists(self._delivery_bills_file):
            return set()
        try:
            with open(self._delivery_bills_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved_date = data.get("biz_date", "")
            bills: list = data.get("bills", [])
            self.delivery_bill_state = data.get("bill_state", {}) or {}
            self.last_prev_day_cancel_sweep_biz_date = (
                data.get("last_prev_day_cancel_sweep_biz_date", "") or ""
            )
            if saved_date != biz_date:
                self.logger.info(
                    "🗓️  营业日切换（%s → %s），保留外卖跟踪待昨日窗口扫描",
                    saved_date,
                    biz_date,
                )
            else:
                self.logger.info(
                    "📂 加载 %s 条已采集外卖账单, %s 条跟踪记录",
                    len(bills),
                    len(self.delivery_bill_state),
                )
            return set(bills)
        except Exception as e:
            self.logger.warning(f"⚠️  加载外卖账单记录失败: {e}")
            return set()

    def save_delivery_bills(self) -> None:
        try:
            data = {
                "biz_date": self.current_biz_date(),
                "bills": list(self.collected_delivery_bills),
                "bill_state": self.delivery_bill_state,
                "last_prev_day_cancel_sweep_biz_date": (
                    self.last_prev_day_cancel_sweep_biz_date or ""
                ),
            }
            with open(self._delivery_bills_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=_json_default)
        except Exception as e:
            self.logger.warning(f"⚠️  保存外卖账单记录失败: {e}")
