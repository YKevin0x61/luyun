#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""楼面控制台：等叫 / 叫起 / 加急 / 对调 / 按桌列表。"""

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from fastapi import HTTPException

from db_core.ports import OrdersPort
from services.kds_orders import derive_steamer_phase
from services.kitchen_work import (
    is_cancelled_status,
    is_dine_in,
    is_hold,
    is_pending_kitchen_work,
    is_rushed,
    is_unloaded_pending_work,
    line_id,
    now_beijing,
    work_enter_time,
)

SHULONG_STATION_ID = "shulong"


def _conflict(order_id: str, reason: str) -> Dict[str, str]:
    return {"order_id": str(order_id), "reason": reason}


def _raise_if_all_failed(conflicts: List[Dict[str, str]], updated_count: int, action: str) -> None:
    if updated_count > 0:
        return
    if not conflicts:
        raise HTTPException(status_code=400, detail="订单行不能为空")
    raise HTTPException(
        status_code=409,
        detail={"message": f"{action}冲突", "conflicts": conflicts},
    )


def _uniq_ids(order_ids: Optional[Sequence[str]]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for raw in order_ids or []:
        oid = str(raw or "").strip()
        if not oid or oid in seen:
            continue
        seen.add(oid)
        out.append(oid)
    return out


async def _load_rows(orders: OrdersPort, order_ids: Sequence[str]) -> Dict[str, Optional[Dict]]:
    rows: Dict[str, Optional[Dict]] = {}
    for oid in order_ids:
        rows[oid] = await orders.get_order_by_id(oid)
    return rows


def _floor_keep_line(order: Dict) -> bool:
    if not is_dine_in(order):
        return False
    if is_hold(order):
        return True
    if is_pending_kitchen_work(order):
        return True
    return order.get("dish_status") == "已制作待上菜"


def floor_phase(order: Dict) -> str:
    if is_cancelled_status(order):
        return "已取消"
    status = order.get("dish_status") or "待出餐"
    if status == "已制作待上菜":
        return "已制作待上菜"
    if status == "已上菜":
        return "已上菜"
    if is_hold(order):
        return "等叫"
    if (order.get("station") or "") == SHULONG_STATION_ID:
        steamer = derive_steamer_phase(order)
        if steamer == "在蒸":
            return "在蒸"
        if steamer == "待上笼":
            return "待上笼"
    if status == "待出餐":
        return "待出餐"
    return status


async def list_floor_tables(
    orders: OrdersPort,
    *,
    occupied_table_numbers: Optional[Iterable[str]] = None,
    table_snapshot_exists: bool = False,
    start_time=None,
    end_time=None,
) -> Dict[str, Any]:
    occupied = {str(n) for n in (occupied_table_numbers or []) if str(n).strip()}
    rows = await orders.get_orders(
        start_time=start_time,
        end_time=end_time,
        limit=-1,
    )
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for row in rows:
        if not is_dine_in(row):
            continue
        grouped[str(row.get("table_number") or "")].append(row)

    tables = []
    for table_number, lines in grouped.items():
        if not table_number:
            continue
        if not any(_floor_keep_line(line) for line in lines):
            continue
        if table_snapshot_exists and table_number not in occupied:
            continue
        tables.append(
            {
                "table_number": table_number,
                "occupied": table_number in occupied,
                "lines": [
                    {
                        "order_id": line_id(line),
                        "dish_name": line.get("dish_name") or "",
                        "station": line.get("station") or "",
                        "quantity": int(line.get("quantity") or 1),
                        "dish_status": line.get("dish_status") or "待出餐",
                        "phase": floor_phase(line),
                        "is_hold": is_hold(line),
                        "is_rushed": is_rushed(line),
                        "order_time": line.get("order_time"),
                        "fired_at": line.get("fired_at"),
                        "work_enter_time": work_enter_time(line),
                    }
                    for line in lines
                    if _floor_keep_line(line) or is_cancelled_status(line)
                ],
            }
        )
    tables.sort(key=lambda item: item["table_number"])
    return {"tables": tables}


def _pick_substitute(
    target: Dict,
    pool: List[Dict],
    *,
    taken_ids: Set[str],
) -> Optional[Dict]:
    dish = target.get("dish_name") or ""
    table = target.get("table_number") or ""
    candidates = []
    for row in pool:
        oid = line_id(row)
        if not oid or oid in taken_ids:
            continue
        if (row.get("dish_name") or "") != dish:
            continue
        if not is_dine_in(row):
            continue
        if not is_unloaded_pending_work(row):
            continue
        candidates.append(row)
    same_table = [row for row in candidates if (row.get("table_number") or "") == table]
    rest = [row for row in candidates if (row.get("table_number") or "") != table]
    ordered = same_table + rest
    return ordered[0] if ordered else None


async def hold_portions(orders: OrdersPort, payload: Dict) -> Dict[str, Any]:
    order_ids = _uniq_ids(payload.get("order_ids"))
    if not order_ids:
        raise HTTPException(status_code=400, detail="订单行不能为空")

    rows_by_id = await _load_rows(orders, order_ids)
    pending_pool = await orders.get_orders(dish_status="待出餐", limit=-1)
    taken: Set[str] = set(order_ids)
    conflicts: List[Dict[str, str]] = []
    direct_holds: List[str] = []
    substitutes: List[Tuple[str, str]] = []

    unloaded: List[str] = []
    steaming: List[str] = []
    for oid in order_ids:
        row = rows_by_id.get(oid)
        if not row:
            conflicts.append(_conflict(oid, "不存在"))
            continue
        if not is_dine_in(row):
            conflicts.append(_conflict(oid, "外卖"))
            continue
        if is_cancelled_status(row):
            conflicts.append(_conflict(oid, "已取消"))
            continue
        if row.get("dish_status", "待出餐") != "待出餐":
            conflicts.append(_conflict(oid, "已出餐"))
            continue
        if is_hold(row):
            conflicts.append(_conflict(oid, "已被等叫"))
            continue
        if row.get("placement"):
            steaming.append(oid)
        else:
            unloaded.append(oid)

    for oid in unloaded:
        direct_holds.append(oid)

    for oid in steaming:
        target = rows_by_id[oid]
        sub = _pick_substitute(target, pending_pool, taken_ids=taken)
        if not sub:
            conflicts.append(_conflict(oid, "在蒸且无替补"))
            continue
        sub_id = line_id(sub)
        taken.add(sub_id)
        substitutes.append((oid, sub_id))
        # Substitute leaves the awaiting pool (now 在蒸).
        pending_pool = [row for row in pending_pool if line_id(row) != sub_id]

    if not direct_holds and not substitutes:
        _raise_if_all_failed(conflicts, 0, "等叫")

    applied = await orders.apply_floor_mutations(
        now=now_beijing().isoformat(),
        hold_ids=direct_holds,
        fire_ids=[],
        fired_at=None,
        rush_ids=[],
        substitutes=substitutes,
    )
    updated_count = len(direct_holds) + len(substitutes)
    _raise_if_all_failed(conflicts, updated_count, "等叫")
    return {
        "success": True,
        "updated_count": updated_count,
        "conflicts": conflicts,
        "stations": applied["stations"],
        "substituted": [
            {"held_id": held_id, "substitute_id": sub_id}
            for held_id, sub_id in substitutes
        ],
    }


async def fire_portions(orders: OrdersPort, payload: Dict) -> Dict[str, Any]:
    order_ids = _uniq_ids(payload.get("order_ids"))
    if not order_ids:
        raise HTTPException(status_code=400, detail="订单行不能为空")

    rows_by_id = await _load_rows(orders, order_ids)
    conflicts: List[Dict[str, str]] = []
    fire_ids: List[str] = []
    for oid in order_ids:
        row = rows_by_id.get(oid)
        if not row:
            conflicts.append(_conflict(oid, "不存在"))
            continue
        if not is_dine_in(row):
            conflicts.append(_conflict(oid, "外卖"))
            continue
        if is_cancelled_status(row):
            conflicts.append(_conflict(oid, "已取消"))
            continue
        if row.get("dish_status", "待出餐") != "待出餐":
            conflicts.append(_conflict(oid, "已出餐"))
            continue
        if not is_hold(row):
            conflicts.append(_conflict(oid, "不是等叫"))
            continue
        fire_ids.append(oid)

    if not fire_ids:
        _raise_if_all_failed(conflicts, 0, "叫起")

    fired_at = now_beijing().isoformat()
    applied = await orders.apply_floor_mutations(
        now=fired_at,
        hold_ids=[],
        fire_ids=fire_ids,
        fired_at=fired_at,
        rush_ids=[],
        substitutes=[],
    )
    _raise_if_all_failed(conflicts, applied["updated_count"], "叫起")
    return {
        "success": True,
        "updated_count": applied["updated_count"],
        "conflicts": conflicts,
        "stations": applied["stations"],
        "fired_at": fired_at,
    }


async def rush_portions(orders: OrdersPort, payload: Dict) -> Dict[str, Any]:
    order_ids = _uniq_ids(payload.get("order_ids"))
    if not order_ids:
        raise HTTPException(status_code=400, detail="订单行不能为空")

    rows_by_id = await _load_rows(orders, order_ids)
    conflicts: List[Dict[str, str]] = []
    rush_ids: List[str] = []
    for oid in order_ids:
        row = rows_by_id.get(oid)
        if not row:
            conflicts.append(_conflict(oid, "不存在"))
            continue
        if not is_dine_in(row):
            conflicts.append(_conflict(oid, "外卖"))
            continue
        if is_cancelled_status(row):
            conflicts.append(_conflict(oid, "已取消"))
            continue
        if not is_unloaded_pending_work(row):
            if is_hold(row):
                conflicts.append(_conflict(oid, "等叫须先叫起"))
            elif row.get("placement"):
                conflicts.append(_conflict(oid, "在蒸"))
            else:
                conflicts.append(_conflict(oid, "不是待出餐工作"))
            continue
        rush_ids.append(oid)

    if not rush_ids:
        _raise_if_all_failed(conflicts, 0, "加急")

    applied = await orders.apply_floor_mutations(
        now=now_beijing().isoformat(),
        hold_ids=[],
        fire_ids=[],
        fired_at=None,
        rush_ids=rush_ids,
        substitutes=[],
    )
    _raise_if_all_failed(conflicts, applied["updated_count"], "加急")
    return {
        "success": True,
        "updated_count": applied["updated_count"],
        "conflicts": conflicts,
        "stations": applied["stations"],
    }
