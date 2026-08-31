#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
备货预测纯计算策略模块：
包含完整营业日判定、缺失日感知加权预测、置信度分级、有效安全量计算与评估指标。
无 IO / 无数据库依赖的纯函数设计，便于单测与滚动回测复用。
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# 营业日完整性判定阈值（全店维度）
STORE_OPEN_CUTOFF_TIME = time(8, 15)
STORE_CLOSE_CUTOFF_TIME = time(20, 0)

# 默认受约束基础权重（约束：same in [0.30, 0.70], recent in [0.20, 0.60], yesterday in [0, 0.25] 且和为 1）
DEFAULT_BASE_WEIGHTS: Dict[str, float] = {
    "same": 0.50,
    "recent": 0.30,
    "yesterday": 0.20,
}

# 分时段受约束权重
SLOT_BASE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "morning": {"same": 0.40, "recent": 0.40, "yesterday": 0.20},
    "lunch": {"same": 0.45, "recent": 0.35, "yesterday": 0.20},
    "afternoon": {"same": 0.55, "recent": 0.30, "yesterday": 0.15},
    "dinner": {"same": 0.60, "recent": 0.25, "yesterday": 0.15},
}

# 信号历史窗口与最小样本门槛
MAX_SAME_WEEK_DAYS = 4
MIN_SAME_WEEK_DAYS = 2
SAME_WEEK_LOOKBACK_DAYS = 56

MAX_RECENT_DAYS = 7
MIN_RECENT_DAYS = 3
RECENT_LOOKBACK_DAYS = 14

# 安全系数全局参数
MODEL_CALIBRATED_SAFETY_RATIO = 0.31
DEFAULT_SAFETY_STOCK_RATIO = 0.15
MAX_SAFETY_STOCK_RATIO = 0.40


def parse_date_str(d: str | date | datetime) -> date:
    """将日期字符串或 datetime 转换为 date 对象。"""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.fromisoformat(d.replace("Z", "+00:00")).date() if "T" in d else datetime.strptime(d, "%Y-%m-%d").date()


def is_store_day_complete(first_order_time: Optional[time], last_order_time: Optional[time]) -> bool:
    """
    判断全店某一天的订单采集是否完整：
    首单不晚于 08:15 且末单不早于 20:00。
    """
    if first_order_time is None or last_order_time is None:
        return False
    return first_order_time <= STORE_OPEN_CUTOFF_TIME and last_order_time >= STORE_CLOSE_CUTOFF_TIME


def find_complete_store_days(
    day_spans: Dict[str, Tuple[Optional[time], Optional[time]]]
) -> Set[str]:
    """
    筛选出所有满足首末单完整性条件的日期（格式 YYYY-MM-DD）。
    """
    complete: Set[str] = set()
    for day_str, (first_t, last_t) in day_spans.items():
        if is_store_day_complete(first_t, last_t):
            complete.add(day_str)
    return complete


def extract_signal_dates(
    target_date: str | date,
    complete_days: Set[str],
    same_week_lookback: int = SAME_WEEK_LOOKBACK_DAYS,
    max_same_week: int = MAX_SAME_WEEK_DAYS,
    recent_lookback: int = RECENT_LOOKBACK_DAYS,
    max_recent: int = MAX_RECENT_DAYS,
) -> Dict[str, List[str]]:
    """
    根据目标日期和全店完整日集合，提取可用于 S、R、Y 预测的完整历史日期。
    所有历史日期必须严格在 target_date 之前。
    """
    t_date = parse_date_str(target_date)

    # 1. 同星期候选日期（过去 56 天，严格不含目标日）
    same_week_dates: List[str] = []
    for k in range(1, (same_week_lookback // 7) + 1):
        cand = (t_date - timedelta(days=7 * k)).strftime("%Y-%m-%d")
        if cand in complete_days:
            same_week_dates.append(cand)
            if len(same_week_dates) >= max_same_week:
                break

    # 2. 最近完整营业日（过去 14 天，严格不含目标日）
    recent_dates: List[str] = []
    for k in range(1, recent_lookback + 1):
        cand = (t_date - timedelta(days=k)).strftime("%Y-%m-%d")
        if cand in complete_days:
            recent_dates.append(cand)
            if len(recent_dates) >= max_recent:
                break

    # 3. 昨天（仅当昨天为完整日时可用）
    yesterday_str = (t_date - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_dates: List[str] = [yesterday_str] if yesterday_str in complete_days else []

    return {
        "same_week": same_week_dates,
        "recent": recent_dates,
        "yesterday": yesterday_dates,
    }


def compute_signals(
    day_slot_values: Dict[Tuple[str, str], float],
    slot_name: str,
    signal_dates: Dict[str, List[str]],
    min_same_week: int = MIN_SAME_WEEK_DAYS,
    min_recent: int = MIN_RECENT_DAYS,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    计算单个时段的三组信号均值 (S, R, Y)。
    若样本量不足门槛或对应日期不完整，则返回 None，代表信号缺失。
    """
    same_dates = signal_dates.get("same_week", [])
    recent_dates = signal_dates.get("recent", [])
    yesterday_dates = signal_dates.get("yesterday", [])

    s_val: Optional[float] = None
    if len(same_dates) >= min_same_week:
        s_vals = [day_slot_values.get((d, slot_name), 0.0) for d in same_dates]
        s_val = sum(s_vals) / len(s_vals)

    r_val: Optional[float] = None
    if len(recent_dates) >= min_recent:
        r_vals = [day_slot_values.get((d, slot_name), 0.0) for d in recent_dates]
        r_val = sum(r_vals) / len(r_vals)

    y_val: Optional[float] = None
    if yesterday_dates:
        y_val = day_slot_values.get((yesterday_dates[0], slot_name), 0.0)

    return s_val, r_val, y_val


def renormalize_and_combine(
    s_val: Optional[float],
    r_val: Optional[float],
    y_val: Optional[float],
    weights: Optional[Dict[str, float]] = None,
    fallback_candidates: Optional[Sequence[float]] = None,
) -> Tuple[float, Dict[str, float]]:
    """
    根据可用信号对有效权重重新归一化并计算基础预测值。
    若 S、R、Y 均缺失（冷启动或历史全断档），尝试使用 fallback_candidates 的均值；若仍无则返回 0.0。
    """
    w = dict(weights or DEFAULT_BASE_WEIGHTS)
    signals = {"same": s_val, "recent": r_val, "yesterday": y_val}

    active_w = {k: wt for k, wt in w.items() if signals[k] is not None}
    total_w = sum(active_w.values())

    if total_w > 0:
        norm_w = {k: wt / total_w for k, wt in active_w.items()}
        raw_val = sum(norm_w[k] * (signals[k] or 0.0) for k in norm_w)
        return max(0.0, float(raw_val)), norm_w

    # 兜底回退
    if fallback_candidates and len(fallback_candidates) > 0:
        return max(0.0, float(sum(fallback_candidates) / len(fallback_candidates))), {"fallback": 1.0}

    return 0.0, {}


def score_confidence(
    valid_recent_days: int,
    valid_same_week_days: int,
    forecast_qty: float,
) -> str:
    """
    置信度评级：
    - none: 预测值 <= 0 或完全无历史
    - high: 最近 7 个完整日中 >= 5 个有单且同星期 >= 3 个
    - medium: 最近 7 个完整日中 >= 3 个有单或同星期 >= 2 个
    - low: 其他有历史样本但未达 medium 门槛
    """
    if forecast_qty <= 0:
        return "none"
    if valid_recent_days >= 5 and valid_same_week_days >= 3:
        return "high"
    if valid_recent_days >= 3 or valid_same_week_days >= 2:
        return "medium"
    return "low"


def compute_effective_safety_ratio(
    item_safety_ratio: Optional[float] = None,
    model_safety_ratio: float = MODEL_CALIBRATED_SAFETY_RATIO,
    max_safety_ratio: float = MAX_SAFETY_STOCK_RATIO,
) -> float:
    """
    计算有效安全库存系数：
    effective = min(max_safety_ratio, max(model_safety_ratio, item_safety_ratio))
    """
    item_ratio = DEFAULT_SAFETY_STOCK_RATIO if item_safety_ratio is None else float(item_safety_ratio)
    model_ratio = float(model_safety_ratio)
    effective = max(item_ratio, model_ratio)
    return min(float(max_safety_ratio), effective)


# ============================================================================
# 回测与校准评估纯函数
# ============================================================================

def pinball_loss(actual: float, forecast: float, tau: float = 0.8) -> float:
    """计算分位数 Pinball 损失。"""
    err = actual - forecast
    return err * tau if err >= 0 else err * (tau - 1.0)


def calculate_wape(actuals: Sequence[float], forecasts: Sequence[float]) -> float:
    """计算加权绝对百分比误差 (WAPE)。若实际总和为0则返回 0.0。"""
    total_actual = sum(actuals)
    if total_actual <= 0:
        return 0.0
    total_abs_err = sum(abs(a - f) for a, f in zip(actuals, forecasts))
    return float(total_abs_err / total_actual)


def calculate_coverage(actuals: Sequence[float], caps: Sequence[float]) -> float:
    """计算安全量覆盖率（cap >= actual 比例）。"""
    if not actuals:
        return 1.0
    covered = sum(1 for a, c in zip(actuals, caps) if c >= a)
    return float(covered / len(actuals))
