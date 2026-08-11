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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from database import CHINA_TZ, ensure_beijing_datetime
from services.dish_normalize import normalize_dish_name

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
                "safety_stock_ratio": float(row["safety_stock_ratio"] or DEFAULT_SAFETY_STOCK_RATIO),
            }
        return prep_map

    @staticmethod
    async def _load_active_batches(db, now_dt: datetime) -> Dict[Tuple[str, str], float]:
        available_map: Dict[Tuple[str, str], float] = defaultdict(float)
        tdb = db.table("prep_batches")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT item_name, unit, remaining_qty
                FROM prep_batches
                WHERE status IN ('active', 'near_expiry')
                  AND remaining_qty > 0
                  AND expires_at > ?
                """,
                (now_dt.isoformat(),),
            )
            rows = await cursor.fetchall()
        for row in rows:
            key = (row["item_name"], row["unit"] or "")
            available_map[key] += float(row["remaining_qty"] or 0)
        return dict(available_map)

    async def compute_forecast(
        self,
        db,
        target_start: Optional[str],
        target_end: Optional[str],
        station: Optional[str] = None,
        include_inventory: bool = False,
    ) -> Dict[str, Any]:
        """
        计算备货预测。
        include_inventory=False => MVP1 行为（库存全部按 0）
        """
        start_dt, end_dt = self._parse_window(target_start, target_end)
        now_dt = datetime.now(CHINA_TZ)
        slots = self._build_slots(start_dt, end_dt)
        slot_names = [slot["slot_name"] for slot in slots]

        # 历史窗口：至少覆盖最近4个同星期 + 最近7天
        history_start = (start_dt - timedelta(days=35)).replace(hour=0, minute=0, second=0, microsecond=0)
        history_end = end_dt

        exact_rules, norm_rules = await self._load_rules(db)
        prep_item_map = await self._load_prep_item_map(db)
        available_map = await self._load_active_batches(db, now_dt) if include_inventory else {}

        orders_tdb = db.table("orders")
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
                  AND order_time <= ?
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

        def score_confidence(last7_non_zero_days: int, same_week_days: int, forecast_qty: float) -> str:
            if forecast_qty <= 0:
                return "none"
            if last7_non_zero_days >= 5 and same_week_days >= 3:
                return "high"
            if last7_non_zero_days >= 3 or same_week_days >= 2:
                return "medium"
            return "low"

        items: List[Dict[str, Any]] = []
        low_confidence_items: List[Dict[str, Any]] = []

        for series_key, day_slot_values in series_map.items():
            item_name, unit, station_id, position = series_key
            prep_key = (item_name, unit)
            item_meta = prep_item_map.get(prep_key, {})

            slot_results: List[Dict[str, Any]] = []
            total_forecast = 0.0
            total_available = 0.0
            total_recommended = 0.0
            confidence_values: List[str] = []

            for slot in slots:
                slot_start = slot["slot_start"]
                slot_name = slot["slot_name"]
                slot_date = slot_start.strftime("%Y-%m-%d")
                weekday = slot_start.weekday()

                # 最近4个同星期同窗口
                same_week_values = []
                same_week_days = 0
                for k in range(1, 5):
                    d = (slot_start - timedelta(days=7 * k)).strftime("%Y-%m-%d")
                    val = day_slot_values.get((d, slot_name), 0.0)
                    same_week_values.append(val)
                    if val > 0:
                        same_week_days += 1

                # 最近7天同窗口
                last7_values = []
                last7_non_zero = 0
                for k in range(1, 8):
                    d = (slot_start - timedelta(days=k)).strftime("%Y-%m-%d")
                    val = day_slot_values.get((d, slot_name), 0.0)
                    last7_values.append(val)
                    if val > 0:
                        last7_non_zero += 1

                yesterday_val = day_slot_values.get(((slot_start - timedelta(days=1)).strftime("%Y-%m-%d"), slot_name), 0.0)
                avg_same_week = sum(same_week_values) / len(same_week_values) if same_week_values else 0.0
                avg_last7 = sum(last7_values) / len(last7_values) if last7_values else 0.0

                weight_same, weight_last7, weight_yesterday = 0.5, 0.3, 0.2
                if same_week_days < 2:
                    weight_last7 += weight_same
                    weight_same = 0.0
                if avg_last7 <= 0 and avg_same_week > 0 and yesterday_val <= 0:
                    weight_same, weight_last7, weight_yesterday = 0.7, 0.3, 0.0

                forecast_qty = (
                    avg_same_week * weight_same
                    + avg_last7 * weight_last7
                    + yesterday_val * weight_yesterday
                )
                forecast_qty = max(forecast_qty, 0.0)
                confidence = score_confidence(last7_non_zero, same_week_days, forecast_qty)
                confidence_values.append(confidence)

                available_qty = 0.0
                if include_inventory:
                    # MVP2 简化：先用总可用库存展示，不做分桶抵扣细分
                    available_qty = float(available_map.get(prep_key, 0.0))
                safety_ratio = float(item_meta.get("safety_stock_ratio", DEFAULT_SAFETY_STOCK_RATIO))
                safety_qty_slot = forecast_qty * safety_ratio
                rec_slot = max(forecast_qty + safety_qty_slot - available_qty, 0.0)

                slot_results.append(
                    {
                        "slot_start": slot["slot_start"].isoformat(),
                        "slot_end": slot["slot_end"].isoformat(),
                        "forecast_qty": int(math.ceil(forecast_qty)),
                        "available_qty": round(available_qty, 2),
                        "recommended_qty": int(math.ceil(rec_slot)),
                    }
                )

                total_forecast += forecast_qty
                total_available += available_qty
                total_recommended += rec_slot

            safety_ratio = float(item_meta.get("safety_stock_ratio", DEFAULT_SAFETY_STOCK_RATIO))
            safety_qty = total_forecast * safety_ratio
            min_batch_qty = float(item_meta.get("min_batch_qty", DEFAULT_MIN_BATCH_QTY))
            recommended_qty = max(total_forecast + safety_qty - total_available, 0.0)
            if recommended_qty > 0 and min_batch_qty > 0 and recommended_qty < min_batch_qty:
                recommended_qty = min_batch_qty

            # MVP1：风险一律 normal；MVP2：按库存计算粗粒度风险
            if not include_inventory:
                risk_level = "normal"
            else:
                gap = total_forecast + safety_qty - total_available
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

            # 项目级 confidence：按最保守值聚合
            if "low" in confidence_values:
                item_confidence = "low"
            elif "medium" in confidence_values:
                item_confidence = "medium"
            elif "high" in confidence_values:
                item_confidence = "high"
            else:
                item_confidence = "none"

            base_item = {
                "prep_item_id": item_meta.get("prep_item_id"),
                "item_name": item_name,
                "station": station_id or "",
                "position": position or "",
                "unit": unit,
                "forecast_qty": int(math.ceil(total_forecast)),
                "safety_qty": round(safety_qty, 2),
                "available_qty": round(total_available, 2),
                "recommended_qty": int(math.ceil(recommended_qty)),
                "risk_level": risk_level,
                "confidence": item_confidence,
                "reason": "",
                "slots": slot_results,
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

            base_item["reason"] = (
                f"预测需求 {base_item['forecast_qty']} {unit}，"
                f"安全库存 {base_item['safety_qty']} {unit}，"
                f"可用库存 {base_item['available_qty']} {unit}，"
                f"建议制作 {base_item['recommended_qty']} {unit}"
            )
            items.append(base_item)

        missing_rules = []
        for (dish_name, dish_station), qty in sorted(missing_rule_dishes.items(), key=lambda x: x[1], reverse=True):
            missing_rules.append(
                {
                    "dish_name": dish_name,
                    "station": dish_station,
                    "forecast_qty": round(qty, 2),
                    "reason": "没有配置 semi_finished_rules，无法换算为备货品",
                }
            )

        summary = {
            "item_count": len(items),
            "missing_rule_count": len(missing_rules),
            "high_risk_count": sum(1 for item in items if item["risk_level"] == "high"),
            "expiry_risk_count": sum(1 for item in items if item["risk_level"] == "expiry_risk"),
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


prep_plan_service = PrepPlanService()

