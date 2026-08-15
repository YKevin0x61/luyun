#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Classify POS intake rows as dine-in vs delivery (orders.source)."""

from __future__ import annotations

from typing import Any, Iterable, Optional

SOURCE_DINE_IN = "dine_in"
SOURCE_DELIVERY = "delivery"

# Platform names that appear in orderSource / pointName. POS / 扫码点餐 / 移动银台 are dine-in.
DEFAULT_DELIVERY_PLATFORMS = ("美团", "淘宝闪购")


def classify_order_source(
    *,
    table_number: str = "",
    bill_source: str = "",
    order_source: str = "",
    people_qty: Any = None,
    delivery_platforms: Optional[Iterable[str]] = None,
) -> str:
    """Return SOURCE_DELIVERY or SOURCE_DINE_IN from POS bill/table signals.

    POS settled-bill channel is ``orderSource``; older code read ``billSource``.
    Callers should pass both. peopleQty == 0 is the live delivery signal.
    """
    platforms = tuple(delivery_platforms or DEFAULT_DELIVERY_PLATFORMS)
    texts = (
        str(table_number or ""),
        str(bill_source or ""),
        str(order_source or ""),
    )
    if any(platform in text for text in texts for platform in platforms):
        return SOURCE_DELIVERY
    if _people_qty_is_zero(people_qty):
        return SOURCE_DELIVERY
    return SOURCE_DINE_IN


def _people_qty_is_zero(people_qty: Any) -> bool:
    if people_qty is None or people_qty == "":
        return False
    try:
        return int(float(people_qty)) == 0
    except (TypeError, ValueError):
        return False
