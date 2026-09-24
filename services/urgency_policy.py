#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容壳：等待时长策略的实现在 `db_core.urgency_policy`（DOC-07 / ticket 18）。

`db_core` 不得反向 import `services`（`services/__init__.py` 有副作用，
会让 `import db_core.aggregation` 撞循环导入），所以本模块**只做 re-export**，
不留任何逻辑——两个名字指向同一个函数对象，阈值/边界语义不会漂移。
新代码请直接 `from db_core.urgency_policy import …`。
"""

from db_core.urgency_policy import (
    high_cutoff,
    high_threshold_ms,
    level_for_wait_ms,
    urgent_cutoff,
    urgent_threshold_ms,
)

__all__ = [
    "high_cutoff",
    "high_threshold_ms",
    "level_for_wait_ms",
    "urgent_cutoff",
    "urgent_threshold_ms",
]
