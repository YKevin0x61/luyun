#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KDS 厨房控菜业务逻辑。"""

from datetime import datetime
from typing import Dict, Optional, Set, Tuple

from config import settings
from database import CHINA_TZ, ensure_beijing_datetime
from db_core.errors import ConflictError
from db_core.ports import OrdersPort
from services.kitchen_work import is_cancelled_status, is_hold, is_refund_line

DEFAULT_AWAITING_CANCEL_NOTICE_SECONDS = 180


def steamer_awaiting_cancel_notice_seconds() -> int:
    layout = settings.KITCHEN_STATIONS.get("shulong", {}).get("steamer_layout") or {}
    raw = layout.get("awaiting_cancel_notice_seconds")
    if raw in (None, ""):
        return DEFAULT_AWAITING_CANCEL_NOTICE_SECONDS
    return int(raw)


def steamer_port_capacity() -> int:
    layout = settings.KITCHEN_STATIONS.get("shulong", {}).get("steamer_layout") or {}
    return int(layout.get("port_capacity") or 10)


def _placement_hole(order: Optional[Dict]) -> Optional[Tuple[str, int]]:
    placement = (order or {}).get("placement") or {}
    steamer_id = placement.get("steamer_id")
    port_index = placement.get("port_index")
    if steamer_id in (None, "") or port_index is None:
        return None
    return (str(steamer_id), int(port_index))


def _cooking_conflict(order_id: str, reason: str) -> Dict[str, str]:
    return {"order_id": str(order_id), "reason": reason}


def _raise_cooking_conflicts(conflicts: list, message: str = "出餐确认冲突") -> None:
    raise ConflictError(message, conflicts)


async def complete_cooking(orders: OrdersPort, payload: Dict) -> Dict:
    ready_time = payload.get("ready_time") or datetime.now(CHINA_TZ).isoformat()
    ensure_beijing_datetime(ready_time)
    completions = []
    conflicts = []
    seen_ids: Set[str] = set()

    for item in payload["orders"]:
        requested_id = str(item.get("order_id", "") or item.get("business_flow_id", "") or "")
        # A supplied id that misses is a conflict. Do not FIFO another
        # 待出餐 row via table + payload dish_name.
        locate_by_id = bool(requested_id.strip())
        order = await orders.resolve_order_for_cooking(
            order_id=str(item.get("order_id", "")),
            business_flow_id=str(item.get("business_flow_id", "") or ""),
            table_number=str(item.get("table_number", "") or ""),
            dish_name="" if locate_by_id else str(payload.get("dish_name", "") or ""),
        )
        if not order:
            conflicts.append(_cooking_conflict(requested_id or "unknown", "不存在"))
            continue
        oid = str(order.get("_id") or order.get("id") or requested_id)
        if oid in seen_ids:
            conflicts.append(_cooking_conflict(oid, "重复"))
            continue
        seen_ids.add(oid)
        if is_refund_line(order):
            conflicts.append(_cooking_conflict(oid, "退菜"))
            continue
        if order.get("dish_status", "待出餐") != "待出餐":
            conflicts.append(_cooking_conflict(oid, "已出餐"))
            continue
        if is_hold(order):
            conflicts.append(_cooking_conflict(oid, "等叫"))
            continue
        db_qty = int(order.get("quantity") or 1)
        complete_qty = int(item["complete_quantity"])
        if complete_qty > db_qty:
            conflicts.append(_cooking_conflict(oid, "数量超过"))
            continue
        if order.get("table_number") != item["table_number"]:
            conflicts.append(_cooking_conflict(oid, "桌号不匹配"))
            continue

        completions.append({"order": order, "complete_quantity": complete_qty})

    if conflicts:
        _raise_cooking_conflicts(conflicts)

    applied = await orders.apply_cooking_completion(
        ready_time=ready_time,
        completions=completions,
    )
    return {
        "success": True,
        "ready_time": ready_time,
        "updated_count": applied["updated_count"],
        # 内部字段：调用方（api/orders.py）用它决定按档口广播 orders nudge，
        # 不属于对外 API 响应契约，路由层会在返回给客户端前 pop 掉。
        "stations": applied["stations"],
    }


async def load_steamer(orders: OrdersPort, payload: Dict) -> Dict:
    order_ids = [str(oid) for oid in (payload.get("order_ids") or []) if str(oid).strip()]
    if not order_ids:
        raise ValueError("订单行不能为空")

    steamer_id = str(payload.get("steamer_id") or "").strip()
    if not steamer_id:
        raise ValueError("蒸炉不能为空")

    try:
        port_index = int(payload["port_index"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("蒸孔无效")

    loaded_at = payload.get("loaded_at") or datetime.now(CHINA_TZ).isoformat()
    ensure_beijing_datetime(loaded_at)

    conflicts = []
    for order_id in order_ids:
        order = await orders.get_order_by_id(order_id)
        if not order:
            raise KeyError(f"订单不存在: {order_id}")
        if order.get("dish_status", "待出餐") != "待出餐":
            raise ConflictError(f"订单状态不可上笼: {order_id}")
        if is_hold(order):
            conflicts.append(_cooking_conflict(order_id, "等叫"))
            continue
        if order.get("placement"):
            raise ConflictError(f"订单已上笼: {order_id}")
    if conflicts:
        _raise_cooking_conflicts(conflicts, message="上笼确认冲突")

    applied = await orders.apply_steamer_load(
        steamer_id=steamer_id,
        port_index=port_index,
        loaded_at=loaded_at,
        order_ids=order_ids,
        capacity=steamer_port_capacity(),
    )
    return {
        "success": True,
        "updated_count": applied["updated_count"],
        # Internal: route pops this before responding, then broadcasts by station.
        "stations": applied["stations"],
    }


async def move_steamer(orders: OrdersPort, payload: Dict) -> Dict:
    order_ids = [str(oid) for oid in (payload.get("order_ids") or []) if str(oid).strip()]
    if not order_ids:
        raise ValueError("订单行不能为空")

    steamer_id = str(payload.get("steamer_id") or "").strip()
    if not steamer_id:
        raise ValueError("蒸炉不能为空")

    try:
        port_index = int(payload["port_index"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("蒸孔无效")

    dest = (steamer_id, port_index)
    resolved = []
    for order_id in order_ids:
        order = await orders.get_order_by_id(order_id)
        if not order:
            raise KeyError(f"订单不存在: {order_id}")
        if order.get("dish_status", "待出餐") != "待出餐":
            raise ConflictError(f"订单状态不可换孔: {order_id}")
        if is_cancelled_status(order):
            raise ConflictError(f"退菜占位不可换孔: {order_id}")
        if not order.get("placement"):
            raise ConflictError(f"订单未上笼: {order_id}")
        resolved.append(order)

    if all(_placement_hole(order) == dest for order in resolved):
        return {"success": True, "updated_count": 0, "stations": []}

    applied = await orders.apply_steamer_move(
        steamer_id=steamer_id,
        port_index=port_index,
        order_ids=order_ids,
        capacity=steamer_port_capacity(),
    )
    return {
        "success": True,
        "updated_count": applied["updated_count"],
        "stations": applied["stations"],
    }


async def unload_steamer(orders: OrdersPort, payload: Dict) -> Dict:
    order_ids = [str(oid) for oid in (payload.get("order_ids") or []) if str(oid).strip()]
    if not order_ids:
        raise ValueError("订单行不能为空")

    for order_id in order_ids:
        order = await orders.get_order_by_id(order_id)
        if not order:
            raise KeyError(f"订单不存在: {order_id}")
        if order.get("dish_status", "待出餐") != "待出餐":
            raise ConflictError(f"订单状态不可下笼: {order_id}")
        if not order.get("placement"):
            raise ConflictError(f"订单未上笼: {order_id}")

    applied = await orders.apply_steamer_unload(order_ids=order_ids)
    return {
        "success": True,
        "updated_count": applied["updated_count"],
        "stations": applied["stations"],
    }


async def pluck_steamer(orders: OrdersPort, payload: Dict) -> Dict:
    order_ids = [str(oid) for oid in (payload.get("order_ids") or []) if str(oid).strip()]
    if not order_ids:
        raise ValueError("订单行不能为空")

    for order_id in order_ids:
        order = await orders.get_order_by_id(order_id)
        if not order:
            raise KeyError(f"订单不存在: {order_id}")
        if not is_cancelled_status(order) or not order.get("placement"):
            raise ConflictError(f"仅退菜占位可抽笼: {order_id}")

    applied = await orders.apply_steamer_pluck(order_ids=order_ids)
    return {
        "success": True,
        "updated_count": applied["updated_count"],
        "stations": applied["stations"],
    }
