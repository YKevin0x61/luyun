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
    VOLATILITY_LOOKBACK_DAYS,
    assign_volatility_safety_ratios,
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
    async def _load_closed_days(db) -> Set[str]:
        """读「备货歇业日」日历（迁移 0025），返回营业日字符串集合。

        表读不到时按「没有歇业日」处理 —— 与其它迁移的降级口径一致（见 migrations/pg/README.md）。
        """
        try:
            tdb = db.table("prep_closed_days")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute("SELECT day FROM prep_closed_days")
                rows = await cursor.fetchall()
        except Exception:  # noqa: BLE001
            logger.warning("备货歇业日读不到（迁移 0025 未应用？），按「没有歇业日」处理")
            return set()
        return {str(row["day"]) for row in rows if row["day"]}

    @staticmethod
    def _normalize_day(value: str) -> str:
        """把入参收敛成营业日 `YYYY-MM-DD`；不合法就抛 ValueError（路由转 400）。

        允许传完整 ISO 时间（`2026-10-17T07:30:00`）—— 前端的时间窗输入就是那个形状。
        """
        try:
            return datetime.strptime((value or "").strip()[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"日期格式应为 YYYY-MM-DD：{value}") from exc

    @staticmethod
    async def list_closed_days(db) -> Dict[str, Any]:
        """列出全部歇业日，按日期升序。"""
        tdb = db.table("prep_closed_days")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute("SELECT day, note FROM prep_closed_days ORDER BY day ASC")
            rows = await cursor.fetchall()
        return {
            "success": True,
            "days": [{"day": str(r["day"]), "note": r["note"] or ""} for r in rows],
        }

    @staticmethod
    async def add_closed_day(db, day: str, note: str = "") -> Dict[str, Any]:
        """把一个营业日标成歇业。同一天重复标只更新说明、不报错（幂等，前端好写）。"""
        value = PrepPlanService._normalize_day(day)
        now_iso = datetime.now(CHINA_TZ).isoformat()
        tdb = db.table("prep_closed_days")

        async with tdb.conn.cursor() as cursor:
            await cursor.execute("SELECT id FROM prep_closed_days WHERE day = ? LIMIT 1", (value,))
            existing = await cursor.fetchone()

        if existing:
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE prep_closed_days SET note = ?, updated_at = ? WHERE id = ?",
                    (note or "", now_iso, existing["id"]),
                )
            await tdb.commit()
            return {"success": True, "day": value, "created": False}

        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_closed_days (day, note, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (value, note or "", now_iso, now_iso),
            )
        await tdb.commit()
        return {"success": True, "day": value, "created": True}

    @staticmethod
    async def remove_closed_day(db, day: str) -> Dict[str, Any]:
        """取消某天的歇业标记。那天本来就没标也不报错。"""
        value = PrepPlanService._normalize_day(day)
        tdb = db.table("prep_closed_days")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute("DELETE FROM prep_closed_days WHERE day = ?", (value,))
        await tdb.commit()
        return {"success": True, "day": value}

    async def compute_forecast(
        self,
        db,
        target_start: Optional[str],
        target_end: Optional[str],
        station: Optional[str] = None,
        now: Optional[datetime] = None,
        model_safety_ratio: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        计算备货预测。

        **库存一律按 0**：这个功能不登记备货数量（ADR 0111），每次计算只回答
        「这个窗口还要做多少」，不看已经做过什么 —— 所以建议制作量 = 各时段预测 + 安全量。
        窗口里若有**歇业日**（`prep_closed_days`），落在那几天的时段直接不进建议；
        整段都在歇业日里时返回 `closed=True`，而不是一份算出来的清单。
        now: 计算时刻；默认 datetime.now(CHINA_TZ)。
        """
        start_dt, end_dt = self._parse_window(target_start, target_end)
        now_dt = ensure_beijing_datetime(now) if now is not None else datetime.now(CHINA_TZ)
        slots = self._build_slots(start_dt, end_dt)

        # 歇业日：预测器对「哪天不做生意」一无所知，会照同星期算出一份像模像样的清单
        # （实测国庆当天给出 52 个品、合计 1311，金莎的信心还是 high）。师傅照做就是报废。
        closed_days = await self._load_closed_days(db)
        if closed_days:
            slots = [s for s in slots if s["slot_start"].strftime("%Y-%m-%d") not in closed_days]
        if not slots:
            return {
                "success": True,
                "closed": True,
                "closed_reason": "这个窗口整段落在歇业日里",
                "target_window": {
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "station": station or "all_kitchen",
                },
                "summary": {"item_count": 0, "todo_count": 0, "missing_rule_count": 0},
                "items": [],
                "missing_rules": [],
                "low_confidence": [],
            }

        # 历史窗口：严格冻结在 start_dt 之前，避免目标日订单泄漏
        history_start = (start_dt - timedelta(days=SAME_WEEK_LOOKBACK_DAYS + 7)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        history_end = start_dt

        exact_rules, norm_rules = await self._load_rules(db)
        prep_item_map = await self._load_prep_item_map(db)

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
        if closed_days:
            # 歇业日不进样本：它们的零消耗不是「那天用得少」，是那天没开门
            effective_complete_days = {
                d for d in effective_complete_days if d not in closed_days
            }

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

        # 安全裕度按备货品自身的波动分配（ADR 0110）：波动小的备得少、波动大的备得多，
        # 全店的总预算不变。品数不足或算不出波动时 `assign_volatility_safety_ratios`
        # 会回落到中档（= MODEL_CALIBRATED_SAFETY_RATIO），行为与分档前一致。
        # 调用方显式给了 model_safety_ratio 就不启用 —— 那是「照我说的来」。
        volatility_ratios: Dict[Tuple[str, str, str, str], float] = {}
        if model_safety_ratio is None:
            volatility_days = sorted(effective_complete_days)[-VOLATILITY_LOOKBACK_DAYS:]
            volatility_ratios = assign_volatility_safety_ratios(
                {
                    key: [
                        sum(
                            series_map.get(key, {}).get((d, s), 0.0)
                            for s in ("morning", "lunch", "afternoon", "dinner")
                        )
                        for d in volatility_days
                    ]
                    for key in all_series_keys
                }
            )

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
                    model_safety_ratio=volatility_ratios.get(
                        series_key, MODEL_CALIBRATED_SAFETY_RATIO
                    ),
                )

            safety_qty = total_forecast * eff_safety_ratio
            min_batch_qty = float(item_meta.get("min_batch_qty", DEFAULT_MIN_BATCH_QTY))

            # 0 库存口径：没有批次可抵，建议制作量就是各时段预测加安全量
            recommended_qty = max(total_forecast + safety_qty, 0.0)
            min_batch_applied = False
            if recommended_qty > 0 and min_batch_qty > 0 and recommended_qty < min_batch_qty:
                recommended_qty = min_batch_qty
                min_batch_applied = True

            if "low" in confidence_values:
                item_confidence = "low"
            elif "medium" in confidence_values:
                item_confidence = "medium"
            elif "high" in confidence_values:
                item_confidence = "high"
            else:
                item_confidence = "none"

            display_slots = []
            for slot_def, raw_forecast in zip(slots, slot_forecasts):
                display_slots.append(
                    {
                        "slot_name": slot_def["slot_name"],
                        "slot_start": slot_def["slot_start"],
                        "slot_end": slot_def["slot_end"],
                        "forecast_qty": int(math.ceil(raw_forecast)),
                        "recommended_qty": int(
                            math.ceil(raw_forecast * (1.0 + eff_safety_ratio))
                        ),
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
                "recommended_qty": int(math.ceil(recommended_qty)),
                "min_batch_qty": round(min_batch_qty, 2),
                "min_batch_applied": min_batch_applied,
                "confidence": item_confidence,
                "reason": "",
                "slots": display_slots,
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
                f"建议制作 {base_item['recommended_qty']} {unit}"
            )
            if min_batch_applied:
                reason += f"（已按最小批量 {int(math.ceil(min_batch_qty))} {unit} 上调）"
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

        summary = {
            "item_count": len(items),
            "todo_count": sum(1 for item in items if item["recommended_qty"] > 0),
            "missing_rule_count": len(missing_rules),
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
                    item_count, missing_rule_count, summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_date,
                    start_dt.isoformat(),
                    end_dt.isoformat(),
                    method,
                    created_by or "",
                    int(summary["item_count"]),
                    int(summary["missing_rule_count"]),
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
                        forecast_qty, safety_qty, recommended_qty,
                        confidence, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        float(item["recommended_qty"]),
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
                            slot_start, slot_end, forecast_qty, recommended_qty, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

