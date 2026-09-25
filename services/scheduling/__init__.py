#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班系统（独立于卫生）。

对外只出 `SchedulingStore`：班次、轮转规则、展开出来的排班结果。
"""

from services.scheduling.store import (
    DEFAULT_SHIFTS,
    EXPANSION_DAYS,
    MAX_CYCLE_DAYS,
    MAX_SHIFT_NAME,
    REST,
    SOURCE_OVERRIDE,
    SOURCE_RULE,
    SchedulingError,
    SchedulingStore,
)

__all__ = [
    "DEFAULT_SHIFTS",
    "EXPANSION_DAYS",
    "MAX_CYCLE_DAYS",
    "MAX_SHIFT_NAME",
    "REST",
    "SOURCE_OVERRIDE",
    "SOURCE_RULE",
    "SchedulingError",
    "SchedulingStore",
]
