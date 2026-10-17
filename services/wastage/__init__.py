#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日常损耗登记（从 worktool2 那个小程序搬过来的那一块）。

对外只有两个名字：`WastageStore`（台账读写）与 `WastageActor`（谁在登记）。
原因表与单位候选也转发出来 —— 页面文案、接口校验与库里的 CHECK 用的是同一份。
"""

from services.wastage.store import (  # noqa: F401
    KIND_ADMIN,
    KIND_STAFF,
    MAX_IMAGES,
    MAX_ITEM_NAME,
    MAX_QUANTITY,
    MAX_REMARK,
    MAX_TEMPLATES,
    MAX_UNIT,
    REASONS,
    REASON_ORDER,
    UNITS,
    WastageActor,
    WastageError,
    WastageStore,
    reason_label,
)

__all__ = [
    "KIND_ADMIN",
    "KIND_STAFF",
    "MAX_IMAGES",
    "MAX_ITEM_NAME",
    "MAX_QUANTITY",
    "MAX_REMARK",
    "MAX_TEMPLATES",
    "MAX_UNIT",
    "REASONS",
    "REASON_ORDER",
    "UNITS",
    "WastageActor",
    "WastageError",
    "WastageStore",
    "reason_label",
]
