#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Domain errors raised by kitchen writes and kitchen/floor services."""

from __future__ import annotations

import sqlite3
from typing import List, Optional


class ConflictError(Exception):
    """409-class kitchen/floor conflict. Empty conflicts → string HTTP detail."""

    def __init__(self, message: str, conflicts: Optional[List] = None) -> None:
        super().__init__(message)
        self.message = message
        self.conflicts = list(conflicts or [])


# asyncpg 的完整性异常类名（不 import asyncpg：这里只需要按类名判定，避免在
# 没有装 asyncpg 的环境里导入失败）。
_PG_INTEGRITY_CLASS_NAMES = frozenset(
    {
        "IntegrityConstraintViolationError",
        "RestrictViolationError",
        "NotNullViolationError",
        "ForeignKeyViolationError",
        "UniqueViolationError",
        "CheckViolationError",
        "ExclusionViolationError",
    }
)


def is_integrity_violation(exc: BaseException) -> bool:
    """唯一键 / 外键 / 非空 / CHECK 冲突——两种驱动都识别。

    SQLite 抛 ``sqlite3.IntegrityError``；PostgreSQL（asyncpg）抛
    ``UniqueViolationError`` 等 ``IntegrityConstraintViolationError`` 子类。
    调用方按类名判定，就不必为每个后端写一份 except。
    """
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    return any(cls.__name__ in _PG_INTEGRITY_CLASS_NAMES for cls in type(exc).__mro__)
