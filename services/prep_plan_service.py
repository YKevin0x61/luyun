#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
备货计划服务层。

当前实现重点：
- MVP1：预测（按备货品消耗量序列）
- MVP2：批次/流水管理 + 临期查询
- MVP3：计划留痕 + 准确率计算（基础版）
"""

import logging
import math
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from database import CHINA_TZ, ensure_beijing_datetime
from services.dish_normalize import normalize_dish_name
from services.prep_forecast_policy import (
    DEFAULT_BASE_WEIGHTS,
    DEFAULT_SAFETY_STOCK_RATIO,
    MAX_SAFETY_STOCK_RATIO,
    MODEL_CALIBRATED_SAFETY_RATIO,
    SAME_WEEK_LOOKBACK_DAYS,
    SLOT_BASE_WEIGHTS,
    compute_effective_safety_ratio,
    compute_signals,
    extract_signal_dates,
    find_complete_store_days,
    renormalize_and_combine,
    score_confidence,
)

logger = logging.getLogger(__name__)

SLOT_DEFINITIONS = [
    ("morning", 7, 30, 11, 0),
    ("lunch", 11, 0, 14, 0),
    ("afternoon", 14, 0, 17, 0),
    ("dinner", 17, 0, 21, 30),
    ("next_morning", 7, 30, 11, 0),  # 次日早段（在跨天构建时特殊处理）
]

DEFAULT_SAFETY_STOCK_RATIO = 0.15
DEFAULT_MIN_BATCH_QTY = 0.0
DEFAULT_SHELF_LIFE_HOURS = 24.0

POSITION_STATION_MAP = {
    "熟笼": "shulong",
    "熟笼冻品": "shulong",
    "馅档": "shulong",
    "案板": "shulong",
    "西饼": "xibing",
    "西饼凉菜": "xibing",
    "肠粉": "changfen",
    "明档": "mingdang1",
    "明档1": "mingdang1",
    "煎炸": "jianzha",
}

NEAR_EXPIRY_HOURS = 4.0
UNDO_MOVEMENT_REASON = "undo_record"


def split_available_qty(batches: List[Dict[str, Any]], now_dt: datetime, near_hours: float = NEAR_EXPIRY_HOURS) -> Tuple[float, float]:
    """Split live remaining into non-near-expiry vs near-expiry (still usable)."""
    near_end = now_dt + timedelta(hours=near_hours)
    fresh = 0.0
    near = 0.0
    for batch in batches:
        qty = float(batch.get("remaining_qty") or 0)
        if qty <= 0:
            continue
        expires_at = ensure_beijing_datetime(batch["expires_at"])
        if expires_at <= now_dt:
            continue
        if expires_at <= near_end:
            near += qty
        else:
            fresh += qty
    return fresh, near


def cover_slot_demand(
    slots: List[Dict[str, Any]],
    forecasts: List[float],
    safety_ratio: float,
    batches: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], float]:
    """
    FEFO coverage: a batch can only cover a slot if expires_at >= slot_end.
    Demand per slot is forecast * (1 + safety_ratio).
    """
    remaining = [max(float(batch.get("remaining_qty") or 0), 0.0) for batch in batches]
    order = sorted(
        range(len(batches)),
        key=lambda index: ensure_beijing_datetime(batches[index]["expires_at"]),
    )
    slot_results: List[Dict[str, Any]] = []
    total_uncovered = 0.0
    for slot, forecast in zip(slots, forecasts):
        demand = max(float(forecast or 0), 0.0) * (1.0 + float(safety_ratio or 0))
        covered = 0.0
        slot_end = slot["slot_end"]
        if demand > 0:
            for index in order:
                expires_at = ensure_beijing_datetime(batches[index]["expires_at"])
                if expires_at < slot_end:
                    continue
                take = min(remaining[index], demand - covered)
                if take <= 0:
                    continue
                remaining[index] -= take
                covered += take
                if covered >= demand:
                    break
        uncovered = max(demand - covered, 0.0)
        total_uncovered += uncovered
        slot_results.append(
            {
                "slot_name": slot.get("slot_name") or "",
                "slot_start": slot["slot_start"].isoformat() if hasattr(slot["slot_start"], "isoformat") else slot["slot_start"],
                "slot_end": slot["slot_end"].isoformat() if hasattr(slot["slot_end"], "isoformat") else slot["slot_end"],
                "forecast_qty": float(forecast or 0),
                "available_qty": covered,
                "recommended_qty": uncovered,
            }
        )
    return slot_results, total_uncovered


class PrepPlanService:
    """备货计划业务服务。"""

    @staticmethod
    def _parse_window(target_start: Optional[str], target_end: Optional[str]) -> Tuple[datetime, datetime]:
        start_dt = ensure_beijing_datetime(target_start) if target_start else datetime.now(CHINA_TZ)
        end_dt = ensure_beijing_datetime(target_end) if target_end else (start_dt + timedelta(hours=24))
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=24)
        return start_dt, end_dt

    @staticmethod
    def _slot_name_for(dt: datetime) -> Optional[str]:
        minute = dt.hour * 60 + dt.minute
        if 7 * 60 + 30 <= minute < 11 * 60:
            return "morning"
        if 11 * 60 <= minute < 14 * 60:
            return "lunch"
        if 14 * 60 <= minute < 17 * 60:
            return "afternoon"
        if 17 * 60 <= minute < 21 * 60 + 30:
            return "dinner"
        return None

    @staticmethod
    def _build_slots(target_start: datetime, target_end: datetime) -> List[Dict[str, Any]]:
        """
        构造目标窗口内的业务时段切片。
        返回列表元素：slot_name/slot_start/slot_end
        """
        slots: List[Dict[str, Any]] = []
        cursor_day = target_start.replace(hour=0, minute=0, second=0, microsecond=0)
        end_day = target_end.replace(hour=0, minute=0, second=0, microsecond=0)

        while cursor_day <= end_day:
            day = cursor_day
            candidates = [
                ("morning", day.replace(hour=7, minute=30), day.replace(hour=11, minute=0)),
                ("lunch", day.replace(hour=11, minute=0), day.replace(hour=14, minute=0)),
                ("afternoon", day.replace(hour=14, minute=0), day.replace(hour=17, minute=0)),
                ("dinner", day.replace(hour=17, minute=0), day.replace(hour=21, minute=30)),
            ]
            for slot_name, raw_start, raw_end in candidates:
                slot_start = max(raw_start, target_start)
                slot_end = min(raw_end, target_end)
                if slot_end > slot_start:
                    slots.append(
                        {
                            "slot_name": slot_name,
                            "slot_start": slot_start,
                            "slot_end": slot_end,
                        }
                    )
            cursor_day += timedelta(days=1)
        return slots

    @staticmethod
    async def _load_rules(db) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
        exact_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        norm_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        tdb = db.table("semi_finished_rules")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT dish_name, semi_name, position, factor, unit, category
                FROM semi_finished_rules
                """
            )
            rows = await cursor.fetchall()
        for row in rows:
            dish_name = row["dish_name"]
            rule_entry = {
                "semi_name": row["semi_name"],
                "position": row["position"] or "",
                "factor": float(row["factor"] or 1),
                "unit": row["unit"] or "",
                "category": row["category"] or "",
            }
            exact_map[dish_name].append(rule_entry)
            norm_map[normalize_dish_name(dish_name)].append(rule_entry)
        return exact_map, norm_map

    async def compute_actual_consumption_totals(
        self,
        db,
        target_start: str,
        target_end: str,
        station: Optional[str] = None,
    ) -> Dict[Tuple[str, str], float]:
        """
        计算指定窗口内的“实际备货品消耗总量”：
        orders.quantity × semi_finished_rules.factor 后按 (semi_name, unit) 聚合。
        """
        start_dt = ensure_beijing_datetime(target_start)
        end_dt = ensure_beijing_datetime(target_end)
        exact_rules, norm_rules = await self._load_rules(db)

        orders_tdb = db.table("orders")
        params: List[Any] = [start_dt.isoformat(), end_dt.isoformat()]
        station_clause = " AND station != 'loumian'"
        if station and station != "all":
            if station == "loumian":
                raise ValueError("备货统计不支持楼面档口")
            station_clause = " AND station = ?"
            params.append(station)

        async with orders_tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT dish_name, quantity, station
                FROM orders
                WHERE order_time >= ?
                  AND order_time <= ?
                  {station_clause}
                """,
                tuple(params),
            )
            rows = await cursor.fetchall()

        totals: Dict[Tuple[str, str], float] = defaultdict(float)
        for row in rows:
            dish_name = row["dish_name"] or ""
            qty = float(row["quantity"] or 0)
            if qty == 0:
                continue
            rules = exact_rules.get(dish_name) or norm_rules.get(normalize_dish_name(dish_name), [])
            for rule in rules:
                key = (rule["semi_name"], rule["unit"] or "")
                totals[key] += qty * float(rule["factor"] or 1)
        return dict(totals)

    @staticmethod
    async def _load_prep_item_map(db) -> Dict[Tuple[str, str], Dict[str, Any]]:
        prep_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        tdb = db.table("prep_items")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, item_name, station, position, category, unit,
                       shelf_life_hours, lead_time_hours, min_batch_qty,
                       safety_stock_ratio, active
                FROM prep_items
                WHERE active = 1
                """
            )
            rows = await cursor.fetchall()
        for row in rows:
            key = (row["item_name"], row["unit"] or "")
            prep_map[key] = {
                "prep_item_id": row["id"],
                "item_name": row["item_name"],
                "station": row["station"] or "",
                "position": row["position"] or "",
                "category": row["category"] or "",
                "unit": row["unit"] or "",
                "shelf_life_hours": float(row["shelf_life_hours"] or DEFAULT_SHELF_LIFE_HOURS),
                "lead_time_hours": float(row["lead_time_hours"] or 0),
                "min_batch_qty": float(row["min_batch_qty"] or DEFAULT_MIN_BATCH_QTY),
                "safety_stock_ratio": (
                    float(row["safety_stock_ratio"])
                    if row["safety_stock_ratio"] is not None
                    else DEFAULT_SAFETY_STOCK_RATIO
                ),
            }
        return prep_map

    @staticmethod
    async def _load_live_batches(db, now_dt: datetime) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
        grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        tdb = db.table("prep_batches")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, prep_item_id, item_name, unit, produced_qty, remaining_qty,
                       produced_at, expires_at, status, notes
                FROM prep_batches
                WHERE status IN ('active', 'near_expiry')
                  AND remaining_qty > 0
                  AND expires_at > ?
                ORDER BY expires_at ASC, id ASC
                """,
                (now_dt.isoformat(),),
            )
            rows = await cursor.fetchall()
        for row in rows:
            key = (row["item_name"], row["unit"] or "")
            grouped[key].append(dict(row))
        return dict(grouped)

    @staticmethod
    async def _load_undo_batch_ids(db) -> set:
        tdb = db.table("prep_stock_movements")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT DISTINCT batch_id
                FROM prep_stock_movements
                WHERE reason = ?
                """,
                (UNDO_MOVEMENT_REASON,),
            )
            rows = await cursor.fetchall()
        return {int(row["batch_id"]) for row in rows if row["batch_id"] is not None}

    @staticmethod
    async def _load_window_production(
        db,
        start_dt: datetime,
        end_dt: datetime,
        undo_ids: set,
    ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        produced: Dict[Tuple[str, str], Dict[str, Any]] = defaultdict(
            lambda: {"qty": 0.0, "undo_batch_id": None, "latest_at": ""}
        )
        tdb = db.table("prep_batches")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, item_name, unit, produced_qty, produced_at
                FROM prep_batches
                WHERE produced_at >= ?
                  AND produced_at <= ?
                ORDER BY produced_at DESC, id DESC
                """,
                (start_dt.isoformat(), end_dt.isoformat()),
            )
            rows = await cursor.fetchall()
        for row in rows:
            batch_id = int(row["id"])
            if batch_id in undo_ids:
                continue
            key = (row["item_name"], row["unit"] or "")
            entry = produced[key]
            entry["qty"] += float(row["produced_qty"] or 0)
            if entry["undo_batch_id"] is None:
                entry["undo_batch_id"] = batch_id
                entry["latest_at"] = row["produced_at"] or ""
        return dict(produced)

    async def compute_forecast(
        self,
        db,
        target_start: Optional[str],
        target_end: Optional[str],
        station: Optional[str] = None,
        include_inventory: bool = False,
        now: Optional[datetime] = None,
        model_safety_ratio: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        计算备货预测。
        include_inventory=False => MVP1 行为（库存全部按 0）
        now: inventory clock; defaults to datetime.now(CHINA_TZ).
        """
        start_dt, end_dt = self._parse_window(target_start, target_end)
        now_dt = ensure_beijing_datetime(now) if now is not None else datetime.now(CHINA_TZ)
        slots = self._build_slots(start_dt, end_dt)

        # 历史窗口：严格冻结在 start_dt 之前，避免目标日订单泄漏
        history_start = (start_dt - timedelta(days=SAME_WEEK_LOOKBACK_DAYS + 7)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        history_end = start_dt

        exact_rules, norm_rules = await self._load_rules(db)
        prep_item_map = await self._load_prep_item_map(db)
        live_batches = await self._load_live_batches(db, now_dt) if include_inventory else {}
        undo_ids = await self._load_undo_batch_ids(db) if include_inventory else set()
        window_production = (
            await self._load_window_production(db, start_dt, end_dt, undo_ids) if include_inventory else {}
        )

        orders_tdb = db.table("orders")

        # 1. 查找历史区间内的全店完整营业日（首单 <= 08:15 且 末单 >= 20:00）
        async with orders_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT order_time
                FROM orders
                WHERE order_time >= ? AND order_time < ?
                """,
                (history_start.isoformat(), history_end.isoformat()),
            )
            time_rows = await cursor.fetchall()

        day_spans: Dict[str, Tuple[Optional[time], Optional[time]]] = {}
        for trow in time_rows:
            odt = ensure_beijing_datetime(trow["order_time"])
            d_str = odt.strftime("%Y-%m-%d")
            t_val = odt.time()
            if d_str not in day_spans:
                day_spans[d_str] = (t_val, t_val)
            else:
                min_t, max_t = day_spans[d_str]
                day_spans[d_str] = (min(min_t, t_val), max(max_t, t_val))

        complete_days = find_complete_store_days(day_spans)
        effective_complete_days = complete_days if complete_days else set(day_spans.keys())

        # 2. 查询档口在 [history_start, start_dt) 内的历史订单
        query_params: List[Any] = [history_start.isoformat(), history_end.isoformat()]
        station_clause = " AND station != 'loumian'"
        if station and station != "all":
            if station == "loumian":
                raise ValueError("备货预测不支持楼面档口")
            station_clause = " AND station = ?"
            query_params.append(station)

        async with orders_tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT dish_name, quantity, order_time, station
                FROM orders
                WHERE order_time >= ?
                  AND order_time < ?
                  {station_clause}
                """,
                query_params,
            )
            order_rows = await cursor.fetchall()

        # 序列：key=(semi_name, unit, station, position)
        # value[(date, slot_name)] = consumed_qty
        series_map: Dict[Tuple[str, str, str, str], Dict[Tuple[str, str], float]] = defaultdict(lambda: defaultdict(float))
        missing_rule_dishes: Dict[Tuple[str, str], float] = defaultdict(float)

        for row in order_rows:
            order_dt = ensure_beijing_datetime(row["order_time"])
            slot_name = self._slot_name_for(order_dt)
            if slot_name is None:
                continue

            dish_name = row["dish_name"] or ""
            qty = float(row["quantity"] or 0)
            if qty == 0:
                continue

            rules = exact_rules.get(dish_name)
            if not rules:
                rules = norm_rules.get(normalize_dish_name(dish_name), [])

            if not rules:
                missing_key = (dish_name, row["station"] or "")
                missing_rule_dishes[missing_key] += qty
                continue

            day_key = order_dt.strftime("%Y-%m-%d")
            for rule in rules:
                semi_name = rule["semi_name"]
                unit = rule["unit"] or ""
                position = rule["position"] or ""
                prep_key = (semi_name, unit)

                item_meta = prep_item_map.get(prep_key)
                station_id = (item_meta or {}).get("station") or POSITION_STATION_MAP.get(position, "")
                final_position = (item_meta or {}).get("position") or position

                series_key = (semi_name, unit, station_id, final_position)
                consumed_qty = qty * float(rule["factor"] or 1)
                series_map[series_key][(day_key, slot_name)] += consumed_qty

        all_series_keys: Set[Tuple[str, str, str, str]] = set(series_map.keys())
        for prep_key, meta in prep_item_map.items():
            s_id = meta.get("station") or ""
            pos = meta.get("position") or ""
            if station and station != "all" and s_id != station:
                continue
            all_series_keys.add((prep_key[0], prep_key[1], s_id, pos))

        items: List[Dict[str, Any]] = []
        low_confidence_items: List[Dict[str, Any]] = []

        for series_key in sorted(all_series_keys):
            item_name, unit, station_id, position = series_key
            prep_key = (item_name, unit)
            item_meta = prep_item_map.get(prep_key, {})
            day_slot_values = series_map.get(series_key, {})

            slot_forecasts: List[float] = []
            confidence_values: List[str] = []

            for slot in slots:
                slot_start = slot["slot_start"]
                slot_name = slot["slot_name"]

                signal_dates = extract_signal_dates(slot_start, effective_complete_days)
                s_val, r_val, y_val = compute_signals(day_slot_values, slot_name, signal_dates)
                weights = SLOT_BASE_WEIGHTS.get(slot_name, DEFAULT_BASE_WEIGHTS)

                fallback_vals = [
                    day_slot_values.get((d, slot_name), 0.0)
                    for d in effective_complete_days
                    if (d, slot_name) in day_slot_values
                ]

                forecast_qty, _ = renormalize_and_combine(
                    s_val, r_val, y_val, weights=weights, fallback_candidates=fallback_vals
                )

                valid_recent = sum(1 for d in signal_dates["recent"] if day_slot_values.get((d, slot_name), 0.0) > 0)
                valid_same_week = sum(1 for d in signal_dates["same_week"] if day_slot_values.get((d, slot_name), 0.0) > 0)
                conf = score_confidence(valid_recent, valid_same_week, forecast_qty)

                slot_forecasts.append(forecast_qty)
                confidence_values.append(conf)

            total_forecast = sum(slot_forecasts)
            item_safety_ratio = float(item_meta.get("safety_stock_ratio", DEFAULT_SAFETY_STOCK_RATIO))
            if model_safety_ratio is not None:
                eff_safety_ratio = compute_effective_safety_ratio(
                    item_safety_ratio=item_safety_ratio,
                    model_safety_ratio=model_safety_ratio,
                )
            elif item_safety_ratio == 0.0:
                eff_safety_ratio = 0.0
            else:
                eff_safety_ratio = compute_effective_safety_ratio(
                    item_safety_ratio=item_safety_ratio,
                    model_safety_ratio=MODEL_CALIBRATED_SAFETY_RATIO,
                )

            safety_qty = total_forecast * eff_safety_ratio
            min_batch_qty = float(item_meta.get("min_batch_qty", DEFAULT_MIN_BATCH_QTY))
            item_batches = live_batches.get(prep_key, []) if include_inventory else []
            available_fresh, available_near = split_available_qty(item_batches, now_dt)
            total_available = available_fresh + available_near

            if include_inventory:
                slot_results, uncovered = cover_slot_demand(slots, slot_forecasts, eff_safety_ratio, item_batches)
            else:
                slot_results, uncovered = cover_slot_demand(slots, slot_forecasts, eff_safety_ratio, [])

            recommended_qty = max(uncovered, 0.0)
            min_batch_applied = False
            if recommended_qty > 0 and min_batch_qty > 0 and recommended_qty < min_batch_qty:
                recommended_qty = min_batch_qty
                min_batch_applied = True

            if not include_inventory:
                risk_level = "normal"
            else:
                gap = recommended_qty
                shortage_rate = gap / max(total_forecast, 1.0)
                overload_rate = total_available / max(total_forecast, 1.0)
                if shortage_rate >= 0.5:
                    risk_level = "high"
                elif shortage_rate >= 0.2:
                    risk_level = "medium"
                elif gap > 0:
                    risk_level = "low"
                elif overload_rate > 1.3:
                    risk_level = "waste_risk"
                else:
                    risk_level = "normal"

            if "low" in confidence_values:
                item_confidence = "low"
            elif "medium" in confidence_values:
                item_confidence = "medium"
            elif "high" in confidence_values:
                item_confidence = "high"
            else:
                item_confidence = "none"

            production = window_production.get(prep_key, {})
            can_record = bool(item_meta.get("prep_item_id"))
            display_slots = []
            for slot_row, raw_forecast in zip(slot_results, slot_forecasts):
                display_slots.append(
                    {
                        "slot_name": slot_row["slot_name"],
                        "slot_start": slot_row["slot_start"],
                        "slot_end": slot_row["slot_end"],
                        "forecast_qty": int(math.ceil(raw_forecast)),
                        "available_qty": round(float(slot_row["available_qty"]), 2),
                        "recommended_qty": int(math.ceil(float(slot_row["recommended_qty"]))),
                    }
                )

            live_batch_payload = []
            for batch in item_batches:
                expires_at = ensure_beijing_datetime(batch["expires_at"])
                live_batch_payload.append(
                    {
                        "batch_id": batch["id"],
                        "produced_qty": round(float(batch.get("produced_qty") or 0), 2),
                        "remaining_qty": round(float(batch.get("remaining_qty") or 0), 2),
                        "produced_at": batch.get("produced_at") or "",
                        "expires_at": batch.get("expires_at") or "",
                        "near_expiry": expires_at <= now_dt + timedelta(hours=NEAR_EXPIRY_HOURS),
                    }
                )

            base_item = {
                "prep_item_id": item_meta.get("prep_item_id"),
                "item_name": item_name,
                "station": station_id or "",
                "position": position or "",
                "unit": unit,
                "forecast_qty": int(math.ceil(total_forecast)),
                "safety_qty": round(safety_qty, 2),
                "safety_stock_ratio": round(item_safety_ratio, 4),
                "effective_safety_ratio": round(eff_safety_ratio, 4),
                "available_qty": round(total_available, 2),
                "available_fresh_qty": round(available_fresh, 2),
                "available_near_expiry_qty": round(available_near, 2),
                "recommended_qty": int(math.ceil(recommended_qty)),
                "produced_qty": round(float(production.get("qty") or 0), 2),
                "undo_batch_id": production.get("undo_batch_id"),
                "min_batch_qty": round(min_batch_qty, 2),
                "min_batch_applied": min_batch_applied,
                "can_record": can_record,
                "risk_level": risk_level,
                "confidence": item_confidence,
                "reason": "",
                "slots": display_slots,
                "batches": live_batch_payload,
            }

            if item_confidence == "none":
                low_confidence_items.append(
                    {
                        "item_name": item_name,
                        "station": station_id or "",
                        "position": position or "",
                        "unit": unit,
                        "reason": "最近 7 天样本不足，未输出建议",
                    }
                )
                continue

            reason = (
                f"预测需求 {base_item['forecast_qty']} {unit}，"
                f"安全库存 {base_item['safety_qty']} {unit}，"
                f"可用 {base_item['available_fresh_qty']} {unit}，"
                f"临期 {base_item['available_near_expiry_qty']} {unit}，"
                f"建议制作 {base_item['recommended_qty']} {unit}"
            )
            if min_batch_applied:
                reason += f"（已按最小批量 {int(math.ceil(min_batch_qty))} {unit} 上调）"
            if not can_record:
                reason += "。没有备货品主数据，不能登记"
            base_item["reason"] = reason
            items.append(base_item)

        missing_rules = []
        for (dish_name, dish_station), qty in sorted(missing_rule_dishes.items(), key=lambda x: x[1], reverse=True):
            missing_rules.append(
                {
                    "dish_name": dish_name,
                    "station": dish_station,
                    "forecast_qty": round(qty, 2),
                    "reason": "没有配置半成品换算规则，无法换算为备货品",
                }
            )

        expiring = []
        if include_inventory:
            near_end = now_dt + timedelta(hours=NEAR_EXPIRY_HOURS)
            for key, batches in live_batches.items():
                item_name, unit = key
                meta = prep_item_map.get(key, {})
                for batch in batches:
                    expires_at = ensure_beijing_datetime(batch["expires_at"])
                    if expires_at > near_end:
                        continue
                    expiring.append(
                        {
                            "batch_id": batch["id"],
                            "item_name": item_name,
                            "unit": unit,
                            "station": meta.get("station") or "",
                            "position": meta.get("position") or "",
                            "remaining_qty": round(float(batch.get("remaining_qty") or 0), 2),
                            "expires_at": batch.get("expires_at") or "",
                        }
                    )
            expiring.sort(key=lambda row: row["expires_at"])

        summary = {
            "item_count": len(items),
            "todo_count": sum(1 for item in items if item["recommended_qty"] > 0),
            "missing_rule_count": len(missing_rules),
            "high_risk_count": sum(1 for item in items if item["risk_level"] == "high"),
            "expiry_risk_count": sum(1 for item in items if item["available_near_expiry_qty"] > 0),
            "waste_risk_count": sum(1 for item in items if item["risk_level"] == "waste_risk"),
        }

        return {
            "success": True,
            "target_window": {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "station": station or "all_kitchen",
            },
            "summary": summary,
            "items": sorted(items, key=lambda x: (x["station"], x["position"], -x["recommended_qty"], x["item_name"])),
            "missing_rules": missing_rules,
            "low_confidence": low_confidence_items,
            "expiring": expiring,
        }

    async def create_plan_run(
        self,
        db,
        target_start: Optional[str],
        target_end: Optional[str],
        method: str = "weighted_history",
        created_by: str = "",
        station: Optional[str] = None,
    ) -> Dict[str, Any]:
        forecast_result = await self.compute_forecast(
            db=db,
            target_start=target_start,
            target_end=target_end,
            station=station,
            include_inventory=True,
        )

        start_dt = ensure_beijing_datetime(forecast_result["target_window"]["start"])
        end_dt = ensure_beijing_datetime(forecast_result["target_window"]["end"])
        now_iso = datetime.now(CHINA_TZ).isoformat()
        plan_date = start_dt.strftime("%Y-%m-%d")

        runs_tdb = db.table("prep_plan_runs")
        items_tdb = db.table("prep_plan_items")
        slots_tdb = db.table("prep_plan_item_slots")

        summary = forecast_result["summary"]
        async with runs_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_runs (
                    plan_date, target_start, target_end, method, created_by,
                    item_count, missing_rule_count, high_risk_count,
                    expiry_risk_count, waste_risk_count, summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_date,
                    start_dt.isoformat(),
                    end_dt.isoformat(),
                    method,
                    created_by or "",
                    int(summary["item_count"]),
                    int(summary["missing_rule_count"]),
                    int(summary["high_risk_count"]),
                    int(summary["expiry_risk_count"]),
                    int(summary["waste_risk_count"]),
                    "",
                    now_iso,
                ),
            )
            run_id = cursor.lastrowid
        await runs_tdb.commit()

        for item in forecast_result["items"]:
            async with items_tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO prep_plan_items (
                        run_id, prep_item_id, item_name, station, position, unit,
                        forecast_qty, safety_qty, available_qty, recommended_qty,
                        risk_level, confidence, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        item.get("prep_item_id"),
                        item["item_name"],
                        item.get("station", ""),
                        item.get("position", ""),
                        item["unit"],
                        float(item["forecast_qty"]),
                        float(item["safety_qty"]),
                        float(item["available_qty"]),
                        float(item["recommended_qty"]),
                        item["risk_level"],
                        item.get("confidence", "none"),
                        item.get("reason", ""),
                        now_iso,
                    ),
                )
                plan_item_id = cursor.lastrowid
            await items_tdb.commit()

            for slot in item.get("slots", []):
                async with slots_tdb.conn.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO prep_plan_item_slots (
                            run_id, plan_item_id, prep_item_id, item_name, unit,
                            slot_start, slot_end, forecast_qty, available_qty, recommended_qty, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            plan_item_id,
                            item.get("prep_item_id"),
                            item["item_name"],
                            item["unit"],
                            slot["slot_start"],
                            slot["slot_end"],
                            float(slot["forecast_qty"]),
                            float(slot["available_qty"]),
                            float(slot["recommended_qty"]),
                            now_iso,
                        ),
                    )
            await slots_tdb.commit()

        return {
            "success": True,
            "run_id": run_id,
            "plan_date": plan_date,
            "summary": summary,
        }

    async def record_batch(
        self,
        db,
        item_name: str,
        produced_qty: float,
        unit: str = "",
        produced_at: Optional[str] = None,
        operator: str = "",
        notes: str = "",
    ) -> Dict[str, Any]:
        qty = float(produced_qty)
        if qty <= 0:
            raise ValueError("登记数量必须大于 0")

        prep_items_tdb = db.table("prep_items")
        batches_tdb = db.table("prep_batches")
        movements_tdb = db.table("prep_stock_movements")

        async with prep_items_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, item_name, unit, shelf_life_hours
                FROM prep_items
                WHERE item_name = ? AND unit = ? AND active = 1
                LIMIT 1
                """,
                (item_name, unit),
            )
            prep_item = await cursor.fetchone()
        if not prep_item:
            raise ValueError(f"备货品不存在或未启用: {item_name} ({unit})")

        produced_dt = ensure_beijing_datetime(produced_at) if produced_at else datetime.now(CHINA_TZ)
        now_iso = datetime.now(CHINA_TZ).isoformat()
        shelf_life_hours = float(prep_item["shelf_life_hours"] or DEFAULT_SHELF_LIFE_HOURS)
        expires_dt = produced_dt + timedelta(hours=shelf_life_hours)

        async with batches_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_batches (
                    prep_item_id, item_name, produced_qty, remaining_qty, unit,
                    produced_at, expires_at, status, operator, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    prep_item["id"],
                    item_name,
                    qty,
                    qty,
                    unit,
                    produced_dt.isoformat(),
                    expires_dt.isoformat(),
                    operator or "",
                    notes or "",
                    now_iso,
                    now_iso,
                ),
            )
            batch_id = cursor.lastrowid

        async with movements_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_stock_movements (
                    batch_id, prep_item_id, item_name, unit, movement_type, qty_delta,
                    reason, operator, source_type, source_id, created_at
                ) VALUES (?, ?, ?, ?, 'produce', ?, 'batch_create', ?, 'batch', ?, ?)
                """,
                (
                    batch_id,
                    prep_item["id"],
                    item_name,
                    unit,
                    qty,
                    operator or "",
                    str(batch_id),
                    now_iso,
                ),
            )
        await movements_tdb.commit()

        return {
            "success": True,
            "batch_id": batch_id,
            "produced_qty": qty,
            "remaining_qty": qty,
            "produced_at": produced_dt.isoformat(),
            "expires_at": expires_dt.isoformat(),
        }

    async def retire_batch(
        self,
        db,
        batch_id: int,
        reason: str,
        operator: str = "",
    ) -> Dict[str, Any]:
        if reason not in {UNDO_MOVEMENT_REASON, "discard"}:
            raise ValueError("不支持的批次处理原因")
        batches_tdb = db.table("prep_batches")
        movements_tdb = db.table("prep_stock_movements")
        now_iso = datetime.now(CHINA_TZ).isoformat()

        async with batches_tdb.conn.cursor() as cursor:
            await cursor.execute("SELECT * FROM prep_batches WHERE id = ? LIMIT 1", (batch_id,))
            batch = await cursor.fetchone()
        if not batch:
            raise KeyError("批次不存在")
        batch_dict = dict(batch)
        remaining = float(batch_dict["remaining_qty"] or 0)
        if remaining <= 0 and batch_dict.get("status") == "discarded":
            return {
                "success": True,
                "batch_id": batch_id,
                "remaining_qty": 0.0,
                "status": "discarded",
            }

        qty_delta = -remaining if remaining > 0 else 0.0
        movement_type = "discard"
        async with movements_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_stock_movements (
                    batch_id, prep_item_id, item_name, unit, movement_type, qty_delta,
                    reason, operator, source_type, source_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'batch', ?, ?)
                """,
                (
                    batch_id,
                    batch_dict.get("prep_item_id"),
                    batch_dict["item_name"],
                    batch_dict["unit"] or "",
                    movement_type,
                    qty_delta,
                    reason,
                    operator or "",
                    str(batch_id),
                    now_iso,
                ),
            )
        await movements_tdb.commit()

        async with batches_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE prep_batches
                SET remaining_qty = 0, status = 'discarded', updated_at = ?
                WHERE id = ?
                """,
                (now_iso, batch_id),
            )
        await batches_tdb.commit()
        return {
            "success": True,
            "batch_id": batch_id,
            "remaining_qty": 0.0,
            "status": "discarded",
            "reason": reason,
        }

    async def undo_record(
        self,
        db,
        batch_id: int,
        operator: str = "",
    ) -> Dict[str, Any]:
        return await self.retire_batch(
            db,
            batch_id,
            reason=UNDO_MOVEMENT_REASON,
            operator=operator,
        )

    async def discard_batch(
        self,
        db,
        batch_id: int,
        operator: str = "",
    ) -> Dict[str, Any]:
        return await self.retire_batch(
            db,
            batch_id,
            reason="discard",
            operator=operator,
        )

    async def get_current_plan(self, db, now: Optional[datetime] = None) -> Dict[str, Any]:
        now_iso = (now or datetime.now(CHINA_TZ)).isoformat()
        runs_tdb = db.table("prep_plan_runs")
        items_tdb = db.table("prep_plan_items")

        async with runs_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT *
                FROM prep_plan_runs
                WHERE target_start <= ? AND target_end > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (now_iso, now_iso),
            )
            run_row = await cursor.fetchone()
            if not run_row:
                await cursor.execute(
                    """
                    SELECT *
                    FROM prep_plan_runs
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                )
                run_row = await cursor.fetchone()
                if not run_row:
                    return {"success": True, "run": None, "items": [], "missing_rules": []}

        run = dict(run_row)
        async with items_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT *
                FROM prep_plan_items
                WHERE run_id = ?
                ORDER BY station ASC, position ASC, recommended_qty DESC, item_name ASC
                """,
                (run["id"],),
            )
            items = [dict(row) for row in await cursor.fetchall()]
        return {
            "success": True,
            "run": run,
            "items": items,
            "missing_rules": [],
        }

    async def update_batch(
        self,
        db,
        batch_id: int,
        remaining_qty: Optional[float] = None,
        status: Optional[str] = None,
        notes: Optional[str] = None,
        operator: str = "",
    ) -> Dict[str, Any]:
        batches_tdb = db.table("prep_batches")
        movements_tdb = db.table("prep_stock_movements")
        now_iso = datetime.now(CHINA_TZ).isoformat()

        async with batches_tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM prep_batches WHERE id = ? LIMIT 1",
                (batch_id,),
            )
            current = await cursor.fetchone()
        if not current:
            raise KeyError("批次不存在")
        current_dict = dict(current)

        update_fields = []
        update_values: List[Any] = []

        if remaining_qty is not None:
            new_qty = float(remaining_qty)
            old_qty = float(current_dict["remaining_qty"] or 0)
            delta = new_qty - old_qty
            if abs(delta) > 1e-9:
                async with movements_tdb.conn.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO prep_stock_movements (
                            batch_id, prep_item_id, item_name, unit, movement_type, qty_delta,
                            reason, operator, source_type, source_id, created_at
                        ) VALUES (?, ?, ?, ?, 'adjust', ?, 'batch_update', ?, 'batch', ?, ?)
                        """,
                        (
                            batch_id,
                            current_dict.get("prep_item_id"),
                            current_dict["item_name"],
                            current_dict["unit"],
                            delta,
                            operator or "",
                            str(batch_id),
                            now_iso,
                        ),
                    )
                await movements_tdb.commit()
            update_fields.append("remaining_qty = ?")
            update_values.append(new_qty)

        if status is not None:
            update_fields.append("status = ?")
            update_values.append(status)

        if notes is not None:
            update_fields.append("notes = ?")
            update_values.append(notes)

        update_fields.append("updated_at = ?")
        update_values.append(now_iso)
        update_values.append(batch_id)

        async with batches_tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"UPDATE prep_batches SET {', '.join(update_fields)} WHERE id = ?",
                tuple(update_values),
            )
            affected = cursor.rowcount
        await batches_tdb.commit()
        return {"success": True, "affected": affected}

    async def list_movements(
        self,
        db,
        item_name: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        tdb = db.table("prep_stock_movements")
        conditions = []
        params: List[Any] = []
        if item_name:
            conditions.append("item_name = ?")
            params.append(item_name)
        if start_date:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ)
            conditions.append("created_at >= ?")
            params.append(start_dt.isoformat())
        if end_date:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ) + timedelta(days=1)
            conditions.append("created_at < ?")
            params.append(end_dt.isoformat())
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT *
                FROM prep_stock_movements
                {where_clause}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                tuple(params + [limit]),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        return {"success": True, "items": rows, "count": len(rows)}

    async def create_movement(
        self,
        db,
        batch_id: int,
        item_name: str,
        movement_type: str,
        qty_delta: float,
        unit: str = "",
        reason: str = "",
        operator: str = "",
        source_type: str = "",
        source_id: str = "",
    ) -> Dict[str, Any]:
        allowed_types = {"produce", "discard", "adjust", "expire"}
        if movement_type not in allowed_types:
            raise ValueError(f"不支持的 movement_type: {movement_type}")
        if movement_type == "produce" and qty_delta <= 0:
            raise ValueError("produce 的 qty_delta 必须 > 0")
        if movement_type in {"discard", "expire"} and qty_delta >= 0:
            raise ValueError(f"{movement_type} 的 qty_delta 必须 < 0")

        batches_tdb = db.table("prep_batches")
        movements_tdb = db.table("prep_stock_movements")
        now_iso = datetime.now(CHINA_TZ).isoformat()

        async with batches_tdb.conn.cursor() as cursor:
            await cursor.execute("SELECT * FROM prep_batches WHERE id = ? LIMIT 1", (batch_id,))
            batch = await cursor.fetchone()
        if not batch:
            raise KeyError("批次不存在")
        batch_dict = dict(batch)

        async with movements_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_stock_movements (
                    batch_id, prep_item_id, item_name, unit, movement_type, qty_delta,
                    reason, operator, source_type, source_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    batch_dict.get("prep_item_id"),
                    item_name,
                    unit,
                    movement_type,
                    float(qty_delta),
                    reason or "",
                    operator or "",
                    source_type or "",
                    source_id or "",
                    now_iso,
                ),
            )
            movement_id = cursor.lastrowid
        await movements_tdb.commit()

        old_remaining = float(batch_dict["remaining_qty"] or 0)
        new_remaining = old_remaining + float(qty_delta)
        if new_remaining < 0:
            new_remaining = 0.0

        new_status = batch_dict["status"]
        if new_remaining <= 0 and new_status in {"active", "near_expiry"}:
            new_status = "active"  # MVP2: do not introduce used_up yet
        if movement_type == "discard":
            new_status = "discarded"
        if movement_type == "expire":
            new_status = "expired"

        async with batches_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE prep_batches
                SET remaining_qty = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_remaining, new_status, now_iso, batch_id),
            )
        await batches_tdb.commit()
        return {
            "success": True,
            "movement_id": movement_id,
            "remaining_qty": round(new_remaining, 2),
            "status": new_status,
        }

    async def list_expiring_batches(self, db, within_hours: int = 4) -> Dict[str, Any]:
        now_dt = datetime.now(CHINA_TZ)
        end_dt = now_dt + timedelta(hours=within_hours)
        tdb = db.table("prep_batches")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT *
                FROM prep_batches
                WHERE status IN ('active', 'near_expiry')
                  AND remaining_qty > 0
                  AND expires_at > ?
                  AND expires_at <= ?
                ORDER BY expires_at ASC
                """,
                (now_dt.isoformat(), end_dt.isoformat()),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        return {"success": True, "items": rows, "count": len(rows)}

    async def get_accuracy(self, db, start_date: str, end_date: str) -> Dict[str, Any]:
        runs_tdb = db.table("prep_plan_runs")
        items_tdb = db.table("prep_plan_items")
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ) + timedelta(days=1)

        async with runs_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, target_start, target_end
                FROM prep_plan_runs
                WHERE target_start >= ? AND target_start < ?
                """,
                (start_dt.isoformat(), end_dt.isoformat()),
            )
            run_rows = await cursor.fetchall()

        if not run_rows:
            return {"success": True, "summary": {"plan_count": 0, "accuracy": None}, "items": []}

        run_ids = [row["id"] for row in run_rows]
        placeholders = ",".join(["?"] * len(run_ids))
        async with items_tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT run_id, item_name, unit, forecast_qty
                FROM prep_plan_items
                WHERE run_id IN ({placeholders})
                """,
                tuple(run_ids),
            )
            plan_items = [dict(row) for row in await cursor.fetchall()]

        result_items = []
        accuracy_values = []
        for row in run_rows:
            actual_totals = await self.compute_actual_consumption_totals(
                db=db,
                target_start=row["target_start"],
                target_end=row["target_end"],
                station=None,
            )
            run_plan_items = [item for item in plan_items if item["run_id"] == row["id"]]
            for item in run_plan_items:
                key = (item["item_name"], item["unit"])
                forecast_qty = float(item["forecast_qty"] or 0)
                actual_qty = float(actual_totals.get(key, 0))
                if actual_qty <= 0:
                    acc = 1.0 if forecast_qty <= 0 else 0.0
                else:
                    acc = 1.0 - abs(forecast_qty - actual_qty) / actual_qty
                    if acc < 0:
                        acc = 0.0
                accuracy_values.append(acc)
                result_items.append(
                    {
                        "run_id": row["id"],
                        "item_name": item["item_name"],
                        "unit": item["unit"],
                        "forecast_qty": round(forecast_qty, 2),
                        "actual_qty": round(actual_qty, 2),
                        "accuracy": round(acc, 4),
                    }
                )

        overall = sum(accuracy_values) / len(accuracy_values) if accuracy_values else None
        return {
            "success": True,
            "summary": {
                "plan_count": len(run_rows),
                "item_count": len(result_items),
                "accuracy": round(overall, 4) if overall is not None else None,
            },
            "items": result_items[:2000],
        }

    async def init_items_from_rules(self, db) -> Dict[str, Any]:
        rules_tdb = db.table("semi_finished_rules")
        items_tdb = db.table("prep_items")
        now_iso = datetime.now(CHINA_TZ).isoformat()

        async with rules_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT semi_name, unit, category, position, COUNT(*) AS rule_count
                FROM semi_finished_rules
                GROUP BY semi_name, unit, category, position
                """
            )
            rule_rows = await cursor.fetchall()

        existing_keys = set()
        async with items_tdb.conn.cursor() as cursor:
            await cursor.execute("SELECT item_name, unit FROM prep_items")
            for row in await cursor.fetchall():
                existing_keys.add((row["item_name"], row["unit"] or ""))

        created = 0
        for row in rule_rows:
            item_name = row["semi_name"]
            unit = row["unit"] or ""
            key = (item_name, unit)
            if key in existing_keys:
                continue

            position = row["position"] or ""
            station = POSITION_STATION_MAP.get(position, "")

            async with items_tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO prep_items (
                        item_name, station, position, category, unit,
                        shelf_life_hours, lead_time_hours, min_batch_qty,
                        safety_stock_ratio, active, notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 24, 0, 0, 0.15, 1, 'auto_init_from_rules', ?, ?)
                    """,
                    (
                        item_name,
                        station,
                        position,
                        row["category"] or "",
                        unit,
                        now_iso,
                        now_iso,
                    ),
                )
            created += 1
            existing_keys.add(key)

        await items_tdb.commit()
        return {"success": True, "created": created, "total_rules": len(rule_rows)}


prep_plan_service = PrepPlanService()

