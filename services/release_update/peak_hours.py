#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Peak / business-hours check for Apply Update warning + override."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from database import CHINA_TZ
from services.runtime_settings import DEFAULT_RUNTIME_SETTINGS


def is_within_business_hours(
    work_start: str,
    work_end: str,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """True when current Beijing time is inside [work_start, work_end) same-day window."""
    current = now or datetime.now(CHINA_TZ)
    current_hm = current.strftime("%H:%M")
    # Same convention as scraper.pos_session._is_business_hours:
    # rest is [work_end, next work_start]; business is the complement.
    is_rest = (current_hm >= work_end) or (current_hm <= work_start)
    return not is_rest


class BusinessHoursPeakAdapter:
    """PeakHoursPort: peak == within configured business hours."""

    def __init__(
        self,
        work_start: Optional[str] = None,
        work_end: Optional[str] = None,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._work_start = work_start or DEFAULT_RUNTIME_SETTINGS["work_start"]
        self._work_end = work_end or DEFAULT_RUNTIME_SETTINGS["work_end"]
        self._now_fn = now_fn or (lambda: datetime.now(CHINA_TZ))

    def is_peak(self) -> bool:
        return is_within_business_hours(
            self._work_start,
            self._work_end,
            now=self._now_fn(),
        )
