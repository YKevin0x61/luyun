#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OrderLineBuilder：原始菜行 → 拆份入库订单行。

调用方负责从 POS JSON 抠字段；本模块负责拆份、flow_id、档口、分类与统一行形状。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from database import CHINA_TZ
from scraper.order_flow_ids import (
    allocate_combo_flow_ids,
    allocate_reconcile_flow_ids,
    allocate_unit_flow_ids,
)

FLOW_MODE_UNIT = "unit"
FLOW_MODE_COMBO = "combo"
FLOW_MODE_RECONCILE = "reconcile"


def classify_dish(dish_name: str) -> str:
    """Heuristic dish category used at intake (茶水/点心/热菜…)."""
    if not dish_name:
        return "其他"
    if "茶" in dish_name or "菊" in dish_name:
        return "茶水"
    if "点" in dish_name:
        if "佳点" in dish_name:
            return "佳点"
        if "美点" in dish_name:
            return "美点"
        if "特点" in dish_name:
            return "特点"
        if "禄点" in dish_name:
            return "禄点"
        return "点心"
    if "凉" in dish_name or "拌" in dish_name:
        return "凉菜"
    if "蒸" in dish_name or "炸" in dish_name or "炒" in dish_name:
        return "热菜"
    return "其他"


@dataclass
class RawOrderLine:
    """One POS dish row before split-into-unit-orders."""

    bs_code: str
    dish_name: str
    quantity: int
    unit_price: float
    table_number: str
    order_time: datetime
    flow_mode: str = FLOW_MODE_UNIT
    start_index: int = 1
    overlays: Dict[str, Any] = field(default_factory=dict)


class OrderLineBuilder:
    """Deepen intake shape: one interface for dine-in / delivery / reconcile rows."""

    def __init__(self, dish_catalog):
        self._catalog = dish_catalog

    async def expand(self, raw: RawOrderLine) -> List[Dict[str, Any]]:
        qty = int(raw.quantity or 0)
        if qty <= 0:
            return []
        name = (raw.dish_name or "").strip()
        if not name:
            return []
        bs_code = (raw.bs_code or "").strip() or "UNKNOWN"
        order_time = _ensure_china_dt(raw.order_time)
        unit_price = float(raw.unit_price or 0.0)
        station = await self._catalog.resolve(name)
        category = classify_dish(name)
        flow_ids = _allocate_flow_ids(
            raw.flow_mode, bs_code, name, qty, start_index=raw.start_index
        )
        overlays = dict(raw.overlays or {})
        rows: List[Dict[str, Any]] = []
        for flow_id in flow_ids:
            row: Dict[str, Any] = {
                "table_number": raw.table_number,
                "dish_name": name,
                "quantity": 1,
                "order_time": order_time,
                "price": unit_price,
                "total_amount": unit_price,
                "category": category,
                "business_flow_id": flow_id,
                "station": station,
                "station_id": station,
            }
            row.update(overlays)
            rows.append(row)
        return rows

    async def expand_many(self, raws: List[RawOrderLine]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for raw in raws:
            out.extend(await self.expand(raw))
        return out


def _allocate_flow_ids(
    flow_mode: str,
    bs_code: str,
    dish_name: str,
    count: int,
    *,
    start_index: int,
) -> List[str]:
    mode = (flow_mode or FLOW_MODE_UNIT).strip().lower()
    if mode == FLOW_MODE_COMBO:
        return allocate_combo_flow_ids(bs_code, dish_name, count, start_index=start_index)
    if mode == FLOW_MODE_RECONCILE:
        return allocate_reconcile_flow_ids(
            bs_code, dish_name, count, start_index=start_index
        )
    return allocate_unit_flow_ids(bs_code, dish_name, count, start_index=start_index)


def _ensure_china_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=CHINA_TZ)
            return parsed
        except ValueError:
            pass
    return datetime.now(CHINA_TZ)
