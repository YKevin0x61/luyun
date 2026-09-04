#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pending kitchen work vs 等叫, 进入待出餐工作时刻, and 熟笼工作阶段."""

from datetime import datetime
from typing import Any, Dict, Optional

from db_core.utils import CHINA_TZ, ensure_beijing_datetime

STEAMER_PHASE_AWAITING = "待上笼"
STEAMER_PHASE_STEAMING = "在蒸"
STEAMER_PHASE_CANCEL_HOLD = "退菜占位"
STEAMER_PHASE_AWAITING_NOTICE = "待上笼退示"

SOURCE_DELIVERY = "delivery"


def line_id(order: Optional[Dict]) -> str:
    if not order:
        return ""
    return str(order.get("_id") or order.get("id") or "")


def flag_true(value: Any) -> bool:
    if value in (True, 1, "1"):
        return True
    if isinstance(value, str) and value.lower() in ("true", "yes"):
        return True
    return False


def is_hold(order: Optional[Dict]) -> bool:
    return bool(order) and flag_true(order.get("is_hold"))


def is_rushed(order: Optional[Dict]) -> bool:
    return bool(order) and flag_true(order.get("is_rushed"))


def is_dine_in(order: Optional[Dict]) -> bool:
    if not order:
        return False
    source = (order.get("source") or "").strip()
    return source != SOURCE_DELIVERY


def is_refund_line(order: Optional[Dict]) -> bool:
    if not order:
        return False
    if order.get("status") == "退菜":
        return True
    if "_refund_" in (order.get("business_flow_id") or ""):
        return True
    return int(order.get("quantity") or 0) < 0


def is_cancelled_status(order: Optional[Dict]) -> bool:
    if not order:
        return False
    if order.get("dish_status") == "已取消":
        return True
    return is_refund_line(order)


def is_pending_kitchen_work(order: Optional[Dict]) -> bool:
    if not order:
        return False
    if order.get("dish_status", "待出餐") != "待出餐":
        return False
    if is_cancelled_status(order):
        return False
    if is_hold(order):
        return False
    return True


def is_unloaded_pending_work(order: Optional[Dict]) -> bool:
    return is_pending_kitchen_work(order) and not (order or {}).get("placement")


def work_enter_time(order: Optional[Dict]):
    """进入待出餐工作时刻: 叫起过则叫起时刻，否则下单时间。"""
    if not order:
        return None
    fired_at = order.get("fired_at")
    if fired_at not in (None, ""):
        return fired_at
    return order.get("order_time")


def work_enter_datetime(order: Optional[Dict]) -> Optional[datetime]:
    raw = work_enter_time(order)
    if raw in (None, ""):
        return None
    return ensure_beijing_datetime(raw)


def now_beijing() -> datetime:
    return datetime.now(CHINA_TZ)


def derive_steamer_phase(
    order: Optional[Dict],
    now: Optional[datetime] = None,
    notice_seconds: Optional[int] = None,
) -> Optional[str]:
    """熟笼工作阶段 on the order line. Not a 出餐状态. Ack does not change this."""
    if not order:
        return None
    cancelled = order.get("dish_status") == "已取消"
    if cancelled and order.get("placement"):
        return STEAMER_PHASE_CANCEL_HOLD
    if cancelled:
        # 抽笼 keeps loaded_at; 待上笼退示 is only for never-loaded cancels.
        # 退菜已确认 is device-local; this phase stays so load/complete stay blocked.
        if order.get("loaded_at"):
            return None
        return STEAMER_PHASE_AWAITING_NOTICE
    if is_refund_line(order):
        return None
    if order.get("dish_status", "待出餐") != "待出餐":
        return None
    if is_hold(order):
        return None
    if order.get("placement"):
        return STEAMER_PHASE_STEAMING
    return STEAMER_PHASE_AWAITING


def _iso_work_enter_time(order: Optional[Dict]):
    raw = work_enter_time(order)
    if raw in (None, ""):
        return None
    if hasattr(raw, "isoformat"):
        return raw.isoformat()
    return raw


def annotate_kitchen_work(order: Optional[Dict]) -> Optional[Dict]:
    """Stamp steamer_phase / is_pending_kitchen_work / work_enter_time. Mutates."""
    if not order:
        return order
    order["steamer_phase"] = derive_steamer_phase(order)
    order["is_pending_kitchen_work"] = is_pending_kitchen_work(order)
    order["work_enter_time"] = _iso_work_enter_time(order)
    return order
