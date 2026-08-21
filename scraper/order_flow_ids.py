#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""business_flow_id 生成与解析（增量加菜 / 对账补录）。"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Dict, List, Optional, Tuple

BS_CODE_PATTERN = re.compile(r"^(YY\d+-\d{6}-\d+)_")
FLOW_SEQ_SUFFIX = re.compile(r"_(\d{3})$")
# YY001301-260820-0001 → 260820
BS_CODE_BIZ_DATE_PATTERN = re.compile(r"-(\d{6})(?:-|$)")


def biz_date_from_bs_code(bs_code: str) -> Optional[str]:
    """Parse ``YYYY-MM-DD`` from a POS ``bsCode`` like ``YY001301-260820-0001``."""
    if not bs_code:
        return None
    match = BS_CODE_BIZ_DATE_PATTERN.search(str(bs_code))
    if not match:
        return None
    raw = match.group(1)
    try:
        parsed = date(int("20" + raw[:2]), int(raw[2:4]), int(raw[4:6]))
    except ValueError:
        return None
    return parsed.isoformat()


def extract_bs_code(business_flow_id: str) -> str:
    match = BS_CODE_PATTERN.match(business_flow_id or "")
    if match:
        return match.group(1)
    if business_flow_id and "_" in business_flow_id:
        return business_flow_id.split("_", 1)[0]
    return business_flow_id or "UNKNOWN"


def parse_order_flow_id(business_flow_id: str) -> Optional[Tuple[str, str]]:
    """从 business_flow_id 解析 (bs_code, dish_name)。支持 _001 与 _reconcile_001 后缀。"""
    if not business_flow_id:
        return None
    bs_code = extract_bs_code(business_flow_id)
    if not bs_code:
        return None
    remainder = business_flow_id[len(bs_code) + 1 :]
    if not remainder:
        return None
    if "_reconcile_" in remainder:
        dish_name = remainder.rsplit("_reconcile_", 1)[0]
        return bs_code, dish_name
    if "_refund_" in remainder:
        dish_name = remainder.rsplit("_refund_", 1)[0]
        return bs_code, dish_name
    match = FLOW_SEQ_SUFFIX.search(remainder)
    if match:
        dish_name = remainder[: match.start()]
        return bs_code, dish_name
    return bs_code, remainder


def max_seq_for_dish(orders: List[Dict], dish_name: str, price: float) -> int:
    max_seq = 0
    for order in orders:
        if order.get("dish_name") != dish_name:
            continue
        if float(order.get("price", 0.0)) != float(price):
            continue
        flow_id = order.get("business_flow_id", "")
        if "_refund_" in flow_id or "_reconcile_" in flow_id:
            continue
        match = FLOW_SEQ_SUFFIX.search(flow_id)
        if match:
            max_seq = max(max_seq, int(match.group(1)))
    return max_seq


def allocate_incremental_flow_ids(
    template_order: Dict,
    known_orders: List[Dict],
    count: int,
    *,
    refund: bool = False,
) -> List[str]:
    """为增量加菜/退菜分配互不重复的 business_flow_id。"""
    if count <= 0:
        return []
    dish_name = template_order.get("dish_name", "")
    price = float(template_order.get("price", 0.0))
    bs_code = extract_bs_code(template_order.get("business_flow_id", ""))
    if refund:
        timestamp_ms = int(time.time() * 1000)
        return [
            f"{bs_code}_{dish_name}_refund_{timestamp_ms}_{index + 1:03d}"
            for index in range(count)
        ]
    max_seq = max_seq_for_dish(known_orders, dish_name, price)
    return [
        f"{bs_code}_{dish_name}_{max_seq + index + 1:03d}"
        for index in range(count)
    ]


def allocate_unit_flow_ids(
    bs_code: str, dish_name: str, count: int, start_index: int = 1
) -> List[str]:
    """堂食/外卖普通行：{bs}_{菜名}_{001}。"""
    return [
        f"{bs_code}_{dish_name}_{start_index + index:03d}"
        for index in range(count)
    ]


def allocate_combo_flow_ids(
    bs_code: str, dish_name: str, count: int, start_index: int = 1
) -> List[str]:
    """套餐子项：{bs}_{菜名}_套餐_{001}。"""
    return [
        f"{bs_code}_{dish_name}_套餐_{start_index + index:03d}"
        for index in range(count)
    ]


def allocate_reconcile_flow_ids(bs_code: str, dish_name: str, count: int, start_index: int = 1) -> List[str]:
    """对账补录专用 ID。"""
    return [
        f"{bs_code}_{dish_name}_reconcile_{start_index + index:03d}"
        for index in range(count)
    ]
