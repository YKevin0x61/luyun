#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等待时长 → 优先级 / 催单界的策略函数（读 config.PRIORITY_LEVELS）。

DOC-07（ticket 18）：这份策略的**唯一实现**在 db_core 里，方向是 `services → db_core`。
原先它住在 `services/urgency_policy.py`，而 `db_core/aggregation.py` 与
`db_core/orders_repo.py` 反向 `from services.urgency_policy import …`：虽然那两个模块
本身不回头依赖 db_core，但 `services/__init__.py` 有副作用（→ prep_plan_service →
database → db_core.aggregation），于是 `import db_core.aggregation` 直接
`ImportError: partially initialized module 'db_core.aggregation'`。db_core 是仓储层，
必须能被单独导入，所以策略函数下沉到这里，服务的旧路径只做 re-export。

阈值语义与迁移前完全一致（严格大于、默认 20/15 分钟），
`tests/test_urgency_policy.py` 的断言未改。
"""

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
