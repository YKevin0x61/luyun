#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KDS 厨房控菜业务逻辑。"""

from datetime import datetime
from typing import Dict

from fastapi import HTTPException

from database import CHINA_TZ, ensure_beijing_datetime
from db_core.ports import OrdersPort


def _is_refund_order(order: Dict) -> bool:
    if order.get("status") == "退菜":
        return True
    if "_refund_" in (order.get("business_flow_id") or ""):
        return True
    return int(order.get("quantity") or 0) < 0


async def complete_cooking(orders: OrdersPort, payload: Dict) -> Dict:
    ready_time = payload.get("ready_time") or datetime.now(CHINA_TZ).isoformat()
    ensure_beijing_datetime(ready_time)
    completions = []

    for item in payload["orders"]:
        order = await orders.resolve_order_for_cooking(
            order_id=str(item.get("order_id", "")),
            business_flow_id=str(item.get("business_flow_id", "") or ""),
            table_number=str(item.get("table_number", "") or ""),
            dish_name=str(payload.get("dish_name", "") or ""),
        )
        if not order:
            tried = item.get("order_id") or item.get("business_flow_id") or "unknown"
            raise HTTPException(status_code=404, detail=f"订单不存在: {tried}")
        if _is_refund_order(order):
            raise HTTPException(status_code=409, detail="退菜单不可制作完成")
        if order.get("dish_status", "待出餐") != "待出餐":
            raise HTTPException(status_code=409, detail=f"订单状态不可制作完成: {item['order_id']}")
        db_qty = int(order.get("quantity") or 1)
        complete_qty = int(item["complete_quantity"])
        if complete_qty > db_qty:
            raise HTTPException(status_code=400, detail="完成数量超过当前数量")
        if order.get("table_number") != item["table_number"]:
            raise HTTPException(status_code=400, detail="桌号不匹配")

        completions.append({"order": order, "complete_quantity": complete_qty})

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
