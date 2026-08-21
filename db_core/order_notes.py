#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical 备注 identity for dine-in 加菜 / 退菜 / 退后重下 / 对调."""

from typing import Any

_PLATFORM_NOTES_PREFIX = "外卖平台:"


def canonical_order_notes(value: Any) -> str:
    """Identity key for an order-line 备注.

    Strip; missing/blank is empty. Strings starting with 外卖平台: are not 备注.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if text.startswith(_PLATFORM_NOTES_PREFIX):
        return ""
    return text
