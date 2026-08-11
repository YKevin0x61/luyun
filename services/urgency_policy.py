#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single source for wait → priority / urgent cutoff (reads config.PRIORITY_LEVELS)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from config import PRIORITY_LEVELS

_DEFAULT_URGENT_MS = 20 * 60 * 1000
_DEFAULT_HIGH_MS = 15 * 60 * 1000


def urgent_threshold_ms() -> int:
    return int(PRIORITY_LEVELS.get("urgent", {}).get("threshold", _DEFAULT_URGENT_MS))


def high_threshold_ms() -> int:
    return int(PRIORITY_LEVELS.get("high", {}).get("threshold", _DEFAULT_HIGH_MS))


def level_for_wait_ms(wait_ms: float) -> str:
    """Map wait duration (ms) to urgent / high / normal.

    Uses strict greater-than to match legacy DishMergerService behaviour.
    """
    try:
        ms = float(wait_ms)
    except (TypeError, ValueError):
        return "normal"
    if ms > urgent_threshold_ms():
        return "urgent"
    if ms > high_threshold_ms():
        return "high"
    return "normal"


def urgent_cutoff(now: Optional[datetime] = None) -> datetime:
    """Orders with order_time earlier than this are urgent."""
    moment = now if now is not None else datetime.now()
    return moment - timedelta(milliseconds=urgent_threshold_ms())


def high_cutoff(now: Optional[datetime] = None) -> datetime:
    moment = now if now is not None else datetime.now()
    return moment - timedelta(milliseconds=high_threshold_ms())
