#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Domain errors raised by kitchen writes and kitchen/floor services."""

from __future__ import annotations

from typing import List, Optional


class ConflictError(Exception):
    """409-class kitchen/floor conflict. Empty conflicts → string HTTP detail."""

    def __init__(self, message: str, conflicts: Optional[List] = None) -> None:
        super().__init__(message)
        self.message = message
        self.conflicts = list(conflicts or [])
