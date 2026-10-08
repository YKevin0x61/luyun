#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加班与补钟（`CONTEXT.md` 的「加班与补钟登记」）。

与排班、卫生同一条分层规矩：这一层只从公共层取「这个人是谁」（`services.identity`），
不 import 排班或卫生的模块 —— 三个域靠数据对接，不靠调用。
"""

from services.overtime.ledger import (
    HALF_HOURS_MAX,
    KIND_MAKEUP,
    KIND_OVERTIME,
    MAX_REASON,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_VOIDED,
    EntryActor,
    OvertimeError,
    OvertimeLedger,
)

__all__ = [
    "HALF_HOURS_MAX",
    "KIND_MAKEUP",
    "KIND_OVERTIME",
    "MAX_REASON",
    "STATUS_APPROVED",
    "STATUS_CANCELLED",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "STATUS_VOIDED",
    "EntryActor",
    "OvertimeError",
    "OvertimeLedger",
]
