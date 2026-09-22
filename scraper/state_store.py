#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scraper state persistence: table snapshots and delivery-bill dedupe files."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional

from scraper._common import CHINA_TZ, DATA_DIR
from services.business_day import business_date_of, previous_business_date

logger = logging.getLogger(__name__)


def _json_default(obj):
    """Serialize datetime values that appear in table-order snapshots."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def previous_biz_date_of(biz_date: str) -> str:
    """前一营业日。切日规则与实现统一在 `services.business_day`（CORR-05）。"""
    return previous_business_date(biz_date)


def _write_json_atomic(path: str, payload: dict) -> None:
    """先写同目录的 `.tmp` 再 `os.replace()`，与 `release_update/job_state.py` 同款。

    直接 `open(path, "w")` 覆盖写不是原子的：断电 / `kill -9` / 盘满会在原地留下半截
    JSON。读侧虽然只 warning，但 `is_first_run=True` 会被保留下来，下一轮把所有在座
    桌台按「新增」重放（CORR-06）。`os.replace` 在同一文件系统内是原子的，读者要么
    看到旧内容、要么看到新内容。

    临时文件必须与目标**同目录**（跨设备 rename 不原子），名字固定成 `<name>.tmp`：
    同一进程内状态保存是串行的，不需要唯一后缀；固定名字还能让"有没有残留"一眼可见。
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, default=_json_default)
        # 先把内容刷到盘再 rename：否则断电后可能留下一个"已改名但内容为空"的文件，
        # 那正是这套改动要消除的形态。
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


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

        # 「首次运行」只在**加载成功且文件里就是这么写**时为真（或根本没有状态文件）。
        # 状态文件存在但读不出来（截断 / 权限 / 盘错）是「状态丢了」，不是「第一次跑」：
        # 把它当成首次运行会让下一轮把在座桌台全部按新增重放。见 load_table_state。
        self._state_file_unreadable = False

        self.load_table_state()
        self.collected_delivery_bills = self.load_delivery_bills()

    def current_biz_date(self) -> str:
        """当前营业日（06:00 前算前一天）。规则见 `services.business_day`。"""
        return business_date_of(datetime.now(CHINA_TZ))

    def previous_biz_date(self) -> str:
        return previous_biz_date_of(self.current_biz_date())

    def load_table_state(self) -> None:
        biz_date = self.current_biz_date()
        if not os.path.exists(self._table_state_file):
            # 真的没有状态文件：确实是首次运行，is_first_run 保持 True。
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
            # 文件在、但读不出来（截断 / 权限 / 盘错）：**这不是首次运行**，而是状态丢了。
            # 保持 is_first_run=False 让 table_change_detector 走"有变化才报"的保守分支，
            # 而不是把在座桌台全部当新增重放一遍。
            self._state_file_unreadable = True
            self.is_first_run = False
            self.logger.error(
                f"❌ 加载餐桌状态失败（按状态丢失处理，不重放全量）: {e}"
            )

    def save_table_state(self) -> None:
        try:
            data = {
                "biz_date": self.current_biz_date(),
                "table_states": self.previous_tables_state,
                "table_orders": self.previous_table_orders,
                # 读不出来过就别把"首次运行"写进文件：这个标志是给下一轮的重放判据用的。
                "is_first_run": False if self._state_file_unreadable else self.is_first_run,
            }
            _write_json_atomic(self._table_state_file, data)
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
            _write_json_atomic(self._delivery_bills_file, data)
        except Exception as e:
            self.logger.warning(f"⚠️  保存外卖账单记录失败: {e}")
