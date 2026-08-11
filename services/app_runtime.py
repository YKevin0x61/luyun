#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Process-wide application runtime (db / dish catalog / scraper).

``main`` keeps module-level aliases that point at the same objects for gradual
migration; ``get_db()`` prefers ``get_runtime().db``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class AppRuntime:
    db: Any = None
    dish_catalog: Any = None
    scraper: Any = None


_runtime: Optional[AppRuntime] = None


def get_runtime() -> Optional[AppRuntime]:
    return _runtime


def set_runtime(runtime: Optional[AppRuntime]) -> None:
    global _runtime
    _runtime = runtime
