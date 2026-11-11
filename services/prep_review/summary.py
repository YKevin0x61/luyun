#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货复盘聚合（票 06 后端 / spec §6「指标」、§7「页面」）。

一个只读接口回答三件事：**过去一段时间备得准不准、哪种日子不准、多备的代价去哪了**。
全部基于已落库的 07:00 主口径快照（ADR 0129）与实际销量，现算不缓存——90 天 × 品
的行数不大，缓存反而要处理失效。

## 口径（都能手算复核）

- **样本**：`period` 含 `end` 当天，`start = end − (days − 1)`。只取
  `snapshot_at` 钟点为 `07:00` 的快照（主口径）；08:00 / 19:00 那些窗口不参与，
  否则同日重叠窗口会把误差平均成假象。一天有几个品就有几条品-天样本。
- **建议量**：优先 `final_qty`（店长手改后的最终建议），为空才用 `suggested_qty`。
  手改是现场真实下达的量，复盘要评的是「最终建议准不准」。
- **实际量**：`prep_plan_actuals.sold_qty`，按 `(window_start, window_end, prep_item_id)`
  与快照一一配对（快照本身就是按窗口落的）。`sold_qty IS NULL` 表示窗口还没结束，
  **不参与任何指标、也不计入分组的天数**——把未结束窗口当 0 会把偏差算成灾难。
- **偏差**（量加权，不是按天平均）：
  - 绝对偏差 `Σ|建议 − 实际| / Σ实际`
  - 方向偏差 `Σ(建议 − 实际) / Σ实际`（正=系统性多备，负=系统性少备）
  - 数量口径够卖率 `Σmin(建议, 实际) / Σ实际`
  - 品数口径够卖率 = 「建议 ≥ 实际」的品-天数 / 有数据的品-天数
  - `Σ实际 ≤ 0` 时比例没有分母，返回 `null`（不拿 0 伪装成「完美」）。
- **分组**：`weekday`（1–7）/ `holiday`（节假日 vs 平日）/ `weather`（雨 / 高温 /
  正常 / 无数据）/ `staff`（当日各岗位人数合计 1–2 / 3–4 / 5+ / 无数据）/ `note`
  （有备注 / 无备注）。天气取 `weather_json.text` 含「雨」即为「雨」，否则
  `temp_max ≥ 33` 为「高温」，再去掉「没天气数据」后为「正常」；**雨优先于高温**。
  `n` 是落进该桶的**有数据天数**，不是品-天数。`n < MIN_GROUP_DAYS` 时两个偏差都
  返回 `null` 并标 `insufficient=true`——小样本的偏差方向只反映那几天的偶然。
- **代价**：只统计「多备」的品-天（`建议 − 实际 > 0`），把多备量拆成三份：
  - `next_day_sold`：次日仍卖掉的增量 `min(多备量, max(0, sold_next_day − sold_qty))`。
    `sold_next_day` 为 NULL 表示 24 小时观测点还没到，这部分暂记 0 并计入
    `pending_trajectory`（不是「没卖掉」，是「还不知道」）。
  - `wastage`：同期登记的报废量。走台账读口 `WastageStore.scrap_quantities`
    （撤回的记录不存在）；原因集合仍是复盘的 `WASTAGE_REASONS`：`excess` 备货过量 /
    `expired` 过期丢弃 / `spoiled` 变质腐败；`damaged` 破损、`production_fail` 制作失败
    属操作事故、`other` 原因不明，都不算「备多了的代价」。先按 `prep_item_id` 匹配，
    登记行缺 id 时按品名匹配；单条多备最多吃掉它自己的量（`min(剩余, 已登记报废)`），
    不跨品-天挪账。登记行虽然带 07:00 的 `window_start/window_end`，复盘仍按
    `wastage_date` 落到自然日，再与当天的 07:00 品-天对齐（登记入口不区分钟点）。
  - `unexplained`：扣掉上两项剩下的部分（负值归 0）。三项之和恒等于总多备量。
  - `estimated_amount`：多备量 × 估算单价。单价用 `period` 内订单的加权成交均价：
    `Σtotal_amount / Σ(quantity × 配方系数)`，即「每个半成品单位的销售额」。
    一张菜映射到多个半成品时按各自配方系数占比分摊金额，避免把整道菜价重复算给
    每个配料。没有订单证据的品单价按 0 处理，所以金额是**估算**（`estimated=true`），
    不是成本核算。
- `wastage_recorded_qty` 是同期报废类登记的原始总量（不封顶、撤回的不算），
  用来和损耗台账对账；它可能大于 `wastage`，因为只有「多备」的品-天才会被分摊。

只读：本模块不写任何表。
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from database import CHINA_TZ
from services.dish_normalize import normalize_dish_name
from services.prep_plan_service import LOUMIAN_STATION, prep_plan_service
from services.prep_review import retention
from services.wastage.store import WastageStore

logger = logging.getLogger(__name__)

#: 复盘主口径钟点（与 ADR 0129 / 保留策略同源）。
PRIMARY_CLOCK = retention.PRIMARY_CLOCK

#: 分组样本下限：不足 5 天不给偏差数值（页面显示「样本不足」）。
MIN_GROUP_DAYS = 5

#: 报废类原因（见模块 docstring「代价」）。
WASTAGE_REASONS = ("excess", "expired", "spoiled")

#: 「高温」阈值：日最高气温 ≥ 33°C。
HOT_TEMP_MAX = 33.0

_WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

_GROUP_BUCKETS: Dict[str, List[Tuple[str, str]]] = {
    "weekday": [(str(i), _WEEKDAY_LABELS[i - 1]) for i in range(1, 8)],
    "holiday": [("holiday", "节假日"), ("weekday", "平日")],
    "weather": [
        ("rain", "雨"),
        ("hot", "高温"),
        ("normal", "正常"),
        ("unknown", "无数据"),
    ],
    "staff": [
        ("1-2", "1-2 人"),
        ("3-4", "3-4 人"),
        ("5+", "5 人以上"),
        ("unknown", "无数据"),
    ],
    "note": [("with", "有备注"), ("without", "无备注")],
}

_GROUP_DIMENSIONS = tuple(_GROUP_BUCKETS)


def _parse_day(text: Any) -> date:
    try:
        return date.fromisoformat(str(text).strip())
    except (TypeError, ValueError):
        raise ValueError("end 必须是 YYYY-MM-DD")


def _json_dict(raw: Any) -> Dict[str, Any]:
    """`weather_json` / `staff_json` 是 TEXT；坏 JSON 当作没有数据。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _identity(prep_item_id: Any, item_name: Any, unit: Any) -> Tuple:
    """品的主键：优先 `prep_item_id`，没对齐主数据时回落 (品名, 单位)。"""
    if prep_item_id is not None:
        return ("id", int(prep_item_id))
    return ("name", str(item_name or ""), str(unit or ""))


def _weather_bucket(context: Optional[Dict[str, Any]]) -> str:
    weather = (context or {}).get("weather") or {}
    text = str(weather.get("text") or "")
    temp_max = weather.get("temp_max")
    if "雨" in text:
        return "rain"
    if temp_max is not None:
        try:
            if float(temp_max) >= HOT_TEMP_MAX:
                return "hot"
        except (TypeError, ValueError):
            pass
    # 有日期上下文但天气字段全空——与「那天没有 context 行」一样归无数据。
    if not text and temp_max is None:
        return "unknown"
    return "normal"


def _staff_total(context: Optional[Dict[str, Any]]) -> Optional[float]:
    staff = (context or {}).get("staff") or {}
    total = 0.0
    for value in staff.values():
        try:
            total += float(value)
        except (TypeError, ValueError):
            continue
    return total if total > 0 else None


def _staff_bucket(context: Optional[Dict[str, Any]]) -> str:
    total = _staff_total(context)
    if total is None:
        return "unknown"
    if total <= 2:
        return "1-2"
    if total <= 4:
        return "3-4"
    return "5+"


def _day_group_keys(day: str, context: Optional[Dict[str, Any]]) -> Dict[str, str]:
    holiday_name = str((context or {}).get("holiday_name") or "").strip()
    note = str((context or {}).get("note") or "").strip()
    return {
        "weekday": str(date.fromisoformat(day).isoweekday()),
        "holiday": "holiday" if holiday_name else "weekday",
        "weather": _weather_bucket(context),
        "staff": _staff_bucket(context),
        "note": "with" if note else "without",
    }


async def _load_rows(db, start_day: date, end_day: date) -> List[Dict[str, Any]]:
    """一次取期间内全部 07:00 快照及其配对实际（无 N+1）。"""
    tdb = db.table("prep_plan_snapshots")
    async with tdb.conn.cursor() as cursor:
        await cursor.execute(
            """
            SELECT s.plan_day, s.snapshot_at, s.prep_item_id, s.item_name, s.unit,
                   s.suggested_qty, s.final_qty,
                   a.sold_qty, a.sold_next_day
            FROM prep_plan_snapshots s
            LEFT JOIN prep_plan_actuals a
                   ON a.window_start = s.window_start
                  AND a.window_end = s.window_end
                  AND a.prep_item_id = s.prep_item_id
            WHERE s.plan_day >= ? AND s.plan_day <= ?
              AND substr(s.snapshot_at, 12, 5) = ?
            ORDER BY s.plan_day, s.item_name
            """,
            (start_day.isoformat(), end_day.isoformat(), PRIMARY_CLOCK),
        )
        raw = await cursor.fetchall()

    rows: List[Dict[str, Any]] = []
    for row in raw:
        final_qty = row["final_qty"]
        suggested = final_qty if final_qty is not None else row["suggested_qty"]
        sold = row["sold_qty"]
        next_day = row["sold_next_day"]
        rows.append(
            {
                "day": row["plan_day"] or str(row["snapshot_at"] or "")[:10],
                "key": _identity(row["prep_item_id"], row["item_name"], row["unit"]),
                "item_name": row["item_name"] or "",
                "suggested": float(suggested or 0.0),
                "sold": None if sold is None else float(sold),
                "sold_next_day": None if next_day is None else float(next_day),
            }
        )
    return rows


async def _load_contexts(db, start_day: date, end_day: date) -> Dict[str, Dict[str, Any]]:
    tdb = db.table("prep_plan_context")
    async with tdb.conn.cursor() as cursor:
        await cursor.execute(
            """
            SELECT plan_day, holiday_name, weather_json, staff_json, note
            FROM prep_plan_context
            WHERE plan_day >= ? AND plan_day <= ?
            """,
            (start_day.isoformat(), end_day.isoformat()),
        )
        raw = await cursor.fetchall()
    return {
        row["plan_day"]: {
            "holiday_name": row["holiday_name"] or "",
            "weather": _json_dict(row["weather_json"]),
            "staff": _json_dict(row["staff_json"]),
            "note": row["note"] or "",
        }
        for row in raw
    }


async def _load_wastage(
    db, start_day: date, end_day: date
) -> Tuple[Dict[Tuple[str, int], float], Dict[Tuple[str, str], float], float]:
    """期间内报废类登记量。按 id / 品名分两桶返回（两桶互斥），附原始总量。

    读走台账的 `scrap_quantities` 窄读口：撤回的记录在那边已经排除，这里只负责
    分桶与累加，不再自己写 SQL。
    """
    rows = await WastageStore(db).scrap_quantities(
        start_day.isoformat(), end_day.isoformat(), WASTAGE_REASONS
    )

    by_id: Dict[Tuple[str, int], float] = defaultdict(float)
    by_name: Dict[Tuple[str, str], float] = defaultdict(float)
    recorded = 0.0
    for row in rows:
        qty = row["quantity"]
        recorded += qty
        day = row["wastage_date"]
        if row["prep_item_id"] is not None:
            by_id[(day, row["prep_item_id"])] += qty
        else:
            by_name[(day, row["item_name"])] += qty
    return by_id, by_name, recorded


async def _load_unit_prices(
    db, start_day: date, end_day: date
) -> Dict[Tuple, float]:
    """期间内「每个半成品单位的销售额」加权均价（见模块 docstring）。"""
    exact, norm = await prep_plan_service._load_rules(db)
    start = datetime.combine(start_day, time.min, tzinfo=CHINA_TZ).isoformat()
    end = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=CHINA_TZ).isoformat()
    tdb = db.table("orders")
    async with tdb.conn.cursor() as cursor:
        await cursor.execute(
            """
            SELECT dish_name, quantity, total_amount
            FROM orders
            WHERE order_time >= ? AND order_time < ?
              AND quantity > 0
              AND station != ?
              AND COALESCE(status, '') != '退菜'
            """,
            (start, end, LOUMIAN_STATION),
        )
        raw = await cursor.fetchall()

    revenue: Dict[Tuple, float] = defaultdict(float)
    units: Dict[Tuple, float] = defaultdict(float)
    for row in raw:
        dish = row["dish_name"] or ""
        rules = exact.get(dish) or norm.get(normalize_dish_name(dish)) or []
        if not rules:
            continue
        total_factor = sum(float(rule["factor"] or 0.0) for rule in rules)
        if total_factor <= 0:
            continue
        amount = float(row["total_amount"] or 0.0)
        qty = float(row["quantity"] or 0.0)
        for rule in rules:
            key = _identity(rule.get("prep_item_id"), rule["semi_name"], rule["unit"])
            factor = float(rule["factor"] or 0.0)
            # 一道菜映射到多个半成品时按系数占比分摊金额；单价仍按各自单位数算。
            revenue[key] += amount * factor / total_factor
            units[key] += qty * factor
    return {
        key: revenue[key] / units[key]
        for key in revenue
        if units.get(key, 0.0) > 0
    }


def _metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    usable = [row for row in rows if row["sold"] is not None]
    days = {row["day"] for row in usable}
    suggested_total = sum(row["suggested"] for row in usable)
    sold_total = sum(row["sold"] for row in usable)
    diffs = [row["suggested"] - row["sold"] for row in usable]
    shortfall = sum(max(0.0, -diff) for diff in diffs)
    overfill = sum(max(0.0, diff) for diff in diffs)
    covered = sum(1 for diff in diffs if diff <= 0)
    denom = sold_total if sold_total > 0 else None
    return {
        "measured_days": len(days),
        "measured_item_days": len(usable),
        "suggested_total": round(suggested_total, 2),
        "sold_total": round(sold_total, 2),
        "shortfall_qty": round(shortfall, 2),
        "overfill_qty": round(overfill, 2),
        "absolute_deviation": None
        if denom is None
        else sum(abs(diff) for diff in diffs) / denom,
        "direction_deviation": None if denom is None else sum(diffs) / denom,
        "coverage_qty": None
        if denom is None
        else sum(min(row["suggested"], row["sold"]) for row in usable) / denom,
        "covered_items": covered,
        "coverage_items": (covered / len(usable)) if usable else None,
    }


def _groups(
    day_aggs: Dict[str, Dict[str, float]],
    day_keys: Dict[str, Dict[str, str]],
) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for dimension in _GROUP_DIMENSIONS:
        buckets: List[Dict[str, Any]] = []
        for key, label in _GROUP_BUCKETS[dimension]:
            days = [day for day in day_aggs if day_keys[day][dimension] == key]
            n = len(days)
            direction = sum(day_aggs[day]["direction"] for day in days)
            absolute = sum(day_aggs[day]["absolute"] for day in days)
            sold = sum(day_aggs[day]["sold"] for day in days)
            insufficient = n < MIN_GROUP_DAYS
            ratio_denom = sold if sold > 0 else None
            buckets.append(
                {
                    "key": key,
                    "label": label,
                    "n": n,
                    "direction_deviation": None
                    if insufficient or ratio_denom is None
                    else direction / ratio_denom,
                    "absolute_deviation": None
                    if insufficient or ratio_denom is None
                    else absolute / ratio_denom,
                    "insufficient": insufficient,
                }
            )
        out[dimension] = buckets
    return out


def _cost(
    rows: List[Dict[str, Any]],
    wastage_by_id: Dict[Tuple[str, int], float],
    wastage_by_name: Dict[Tuple[str, str], float],
    wastage_recorded: float,
    prices: Dict[Tuple, float],
) -> Dict[str, Any]:
    overfill_total = 0.0
    next_day_total = 0.0
    wastage_total = 0.0
    unexplained_total = 0.0
    amount_total = 0.0
    pending = 0
    for row in rows:
        if row["sold"] is None:
            continue
        overfill = max(0.0, row["suggested"] - row["sold"])
        if overfill <= 0:
            continue
        overfill_total += overfill

        if row["sold_next_day"] is None:
            next_day = 0.0
            pending += 1
        else:
            next_day = min(overfill, max(0.0, row["sold_next_day"] - row["sold"]))
        next_day_total += next_day

        remaining = overfill - next_day
        if row["key"][0] == "id":
            registered = wastage_by_id.get((row["day"], row["key"][1]), 0.0)
        else:
            registered = 0.0
        # 登记行自己缺 id 时按品名兜底；两桶互斥，不会重复计。
        registered += wastage_by_name.get((row["day"], row["item_name"]), 0.0)
        wastage = min(remaining, registered)
        wastage_total += wastage
        unexplained = max(0.0, remaining - wastage)
        unexplained_total += unexplained
        amount_total += overfill * prices.get(row["key"], 0.0)

    return {
        "overfill_qty": round(overfill_total, 2),
        "next_day_sold": round(next_day_total, 2),
        "wastage": round(wastage_total, 2),
        "unexplained": round(unexplained_total, 2),
        "estimated_amount": round(amount_total, 2),
        "estimated": True,
        "wastage_recorded_qty": round(wastage_recorded, 2),
        "wastage_reasons": list(WASTAGE_REASONS),
        "pending_trajectory": pending,
    }


async def review_summary(
    db, end: Optional[str] = None, days: int = 90
) -> Dict[str, Any]:
    """复盘聚合入口。只读、无 N+1（5 张表各一次查询）。"""
    end_day = _parse_day(end) if end else datetime.now(CHINA_TZ).date()
    days = int(days)
    if days < 1 or days > retention.RETENTION_DAYS:
        raise ValueError(f"days 必须在 1..{retention.RETENTION_DAYS} 之间")
    start_day = end_day - timedelta(days=days - 1)

    rows = await _load_rows(db, start_day, end_day)
    contexts = await _load_contexts(db, start_day, end_day)
    wastage_by_id, wastage_by_name, wastage_recorded = await _load_wastage(
        db, start_day, end_day
    )
    prices = await _load_unit_prices(db, start_day, end_day)

    day_aggs: Dict[str, Dict[str, float]] = {}
    for row in rows:
        if row["sold"] is None:
            continue
        agg = day_aggs.setdefault(
            row["day"], {"direction": 0.0, "absolute": 0.0, "sold": 0.0, "items": 0}
        )
        diff = row["suggested"] - row["sold"]
        agg["direction"] += diff
        agg["absolute"] += abs(diff)
        agg["sold"] += row["sold"]
        agg["items"] += 1

    day_keys = {
        day: _day_group_keys(day, contexts.get(day)) for day in day_aggs
    }

    return {
        "success": True,
        "period": {
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
            "days": days,
        },
        "primary_clock": PRIMARY_CLOCK,
        "min_group_days": MIN_GROUP_DAYS,
        "metrics": _metrics(rows),
        "groups": _groups(day_aggs, day_keys),
        "cost": _cost(
            rows, wastage_by_id, wastage_by_name, wastage_recorded, prices
        ),
    }
