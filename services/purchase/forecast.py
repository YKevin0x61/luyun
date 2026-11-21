#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购参考值与建议量的纯函数（spec §5，沿用 worktool2 小程序的口径）。

这一套与备货计划的参考值（services/prep_plan_forecast.py，ADR 0137）**是两套独立
模型**（ADR 0157）：这里照搬门店原来的「日期标签 + 同标签加权 + 近 N 天 WMA」，
不改用量、不校准、不共享参数。采购页显示的建议量只能是参考，最终由师傅按现场改。

函数全部无数据库、无时钟：调用方把「今天的日期、历史盘点、历史采购、标签」传进来，
这样回放与单测可以固定任意一天。日期一律是 datetime.date，数量是 float。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Mapping, Optional, Sequence

__all__ = [
    "DEFAULT_TAG",
    "TAG_VALUES",
    "ProductReference",
    "daily_usage_series",
    "effective_tag",
    "reference_for_product",
    "suggested_quantity",
]

DEFAULT_TAG = "3万"
TAG_VALUES: tuple[str, ...] = ("2万", "3万", "4万", "5万", "6万", "7万", "8万")

#: 参考值窗口（spec §5）。改这里要同步页面的「口径」说明。
REFERENCE_DAYS = 7
SAME_TAG_DAYS = 60
CV_DAYS = 30
RECENT_DAYS = 7
DEFAULT_SAFETY_FACTOR = 1.2


@dataclass
class ProductReference:
    """一个商品在「今天」算出的参考值。"""

    reference: Optional[float]
    target_tag: str
    base_wma: float = 0.0
    same_tag_value: float = 0.0
    same_tag_samples: int = 0
    same_tag_nonzero: int = 0
    recent3_avg: float = 0.0
    cv: Optional[float] = None
    is_long_cycle: bool = False
    reason: str = ""
    usage_days: int = 0
    purchase_days: int = 0
    details: dict = field(default_factory=dict)


def _wma(values: Sequence[float]) -> float:
    """按时间升序、权重 1..n 的加权平均。空序列返回 0。"""
    if not values:
        return 0.0
    total = 0.0
    weight_sum = 0.0
    for index, value in enumerate(values, start=1):
        total += float(value) * index
        weight_sum += index
    return total / weight_sum if weight_sum else 0.0


def _round1(value: float) -> float:
    return round(float(value) + 1e-9, 1)


def effective_tag_detail(day: date, date_tags: Mapping[date, str],
                         weekday_defaults: Mapping[int, str]) -> tuple[str, str]:
    """返回 (标签, 来源)。来源是 date / weekday / default，页面用来解释标签怎么来的。

    星期键统一用前端与云开发的 Date.getDay() 约定：0=周日、1=周一 … 6=周六。
    Python 的 date.weekday() 是 0=周一，这里必须换算，否则整周错一天。
    """
    tag = date_tags.get(day)
    if tag in TAG_VALUES:
        return tag, "date"
    tag = weekday_defaults.get((day.weekday() + 1) % 7)
    if tag in TAG_VALUES:
        return tag, "weekday"
    return DEFAULT_TAG, "default"


def effective_tag(day: date, date_tags: Mapping[date, str],
                  weekday_defaults: Mapping[int, str]) -> str:
    """指定日期 > 星期默认 > 3万（spec §5.2）。"""
    return effective_tag_detail(day, date_tags, weekday_defaults)[0]


def daily_usage_series(stock_by_date: Mapping[date, float],
                       purchase_by_date: Mapping[date, float],
                       start: date, end: date) -> dict[date, float]:
    """逐日用量：昨库存 + 昨采购 - 今库存，负数归零；缺任一端不算。

    end 是包含的。只有「今天和昨天都有盘点」的日期才有值，所以序列天然有空洞
    —— 调用方必须按 dict 的缺失（不是 0）区分「没盘点」与「盘了是 0」。
    """
    result: dict[date, float] = {}
    cursor = start
    while cursor <= end:
        previous = cursor - timedelta(days=1)
        if cursor in stock_by_date and previous in stock_by_date:
            usage = (
                float(stock_by_date[previous])
                + float(purchase_by_date.get(previous, 0.0))
                - float(stock_by_date[cursor])
            )
            result[cursor] = max(0.0, usage)
        cursor += timedelta(days=1)
    return result


def _purchase_series(purchases: Iterable[tuple[date, float]]) -> dict[date, float]:
    result: dict[date, float] = {}
    for day, quantity in purchases:
        if quantity is None:
            continue
        result[day] = result.get(day, 0.0) + float(quantity)
    return result


def _base_wma(usage: Mapping[date, float], purchases: Mapping[date, float],
              today: date, reference_days: int = REFERENCE_DAYS) -> tuple[float, int]:
    """近 N 天用量 ≥3 天走用量 WMA，否则退回同一窗口的采购量序列。

    N 由下单页的「参考 N 天」决定（3–90，默认 7）；同标签 60 天、CV 30 天不随
    这个值变（用户 2026-11-22 裁定）。用量与采购兜底都只看这个窗口，窗口外
    的历史不能把参考值抬高。
    """
    window_start = today - timedelta(days=reference_days)
    values = [
        float(usage[day])
        for day in sorted(usage)
        if window_start <= day < today
    ]
    if len(values) >= 3:
        wma_n = _wma(values)
        wma_2 = _wma(values[-2:])
        return max(min(wma_2, wma_n), wma_n * 0.5), len(values)

    purchase_values = [
        float(purchases[day])
        for day in sorted(purchases)
        if window_start <= day < today and purchases[day] > 0
    ]
    if len(purchase_values) >= 2:
        return _wma(purchase_values), 0
    if purchase_values:
        return purchase_values[-1], 0
    return 0.0, 0


def _same_tag_value(usage: Mapping[date, float], purchases: Mapping[date, float],
                    date_tags: Mapping[date, str],
                    weekday_defaults: Mapping[int, str], target_tag: str,
                    target: date, usage_days: int) -> tuple[float, int, int]:
    """近 60 天同标签的 WMA；返回（值, 样本数, 非零数）。"""
    start = target - timedelta(days=SAME_TAG_DAYS)
    values: list[float] = []
    for offset in range(SAME_TAG_DAYS):
        day = start + timedelta(days=offset)
        if effective_tag(day, date_tags, weekday_defaults) != target_tag:
            continue
        if usage_days >= 3:
            if day in usage:
                values.append(float(usage[day]))
        else:
            quantity = float(purchases.get(day, 0.0))
            if quantity > 0:
                values.append(quantity)
    nonzero = sum(1 for value in values if value > 0)
    return _round1(_wma(values)), len(values), nonzero


def _cv(usage: Mapping[date, float], today: date) -> Optional[float]:
    start = today - timedelta(days=CV_DAYS)
    values = [float(usage[day]) for day in sorted(usage) if start <= day < today and usage[day] > 0]
    if len(values) < 5:
        return None
    mean = sum(values) / len(values)
    if mean <= 0:
        return None
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) / mean


def _recent3_avg(usage: Mapping[date, float], today: date) -> float:
    start = today - timedelta(days=RECENT_DAYS)
    values = [float(usage[day]) for day in sorted(usage) if start <= day < today]
    if not values:
        return 0.0
    return sum(values[-3:]) / len(values[-3:])


def reference_for_product(
    *,
    today: date,
    purchases: Iterable[tuple[date, float]],
    stocks: Mapping[date, float],
    date_tags: Optional[Mapping[date, str]] = None,
    weekday_defaults: Optional[Mapping[int, str]] = None,
    is_long_cycle: bool = False,
    reference_days: int = REFERENCE_DAYS,
) -> ProductReference:
    """算一个商品在 today 提交时给明天看的参考值。

    purchases 是历史采购（提交日, 数量），stocks 是盘点快照（日期 → 当前库存）。
    目标标签取**明天**的日期标签；历史窗口都截至今天（不含今天）。
    """
    tags = date_tags or {}
    weekdays = weekday_defaults or {}
    purchase_map = _purchase_series(purchases)
    target = today + timedelta(days=1)
    target_tag = effective_tag(target, tags, weekdays)
    reference_days = max(3, min(90, int(reference_days or REFERENCE_DAYS)))

    start = today - timedelta(days=max(SAME_TAG_DAYS, CV_DAYS, reference_days) + 2)
    usage = daily_usage_series(stocks, purchase_map, start, today)
    usage_days = sum(1 for day in usage if day < today)
    purchase_days = sum(1 for day in purchase_map if day < today and purchase_map[day] > 0)

    if is_long_cycle:
        if purchase_days <= 0:
            return ProductReference(
                None, target_tag, is_long_cycle=True, reason="no_purchase_history",
                usage_days=usage_days, purchase_days=purchase_days,
            )
        total = sum(
            float(quantity) for day, quantity in purchase_map.items()
            if day < today and quantity > 0
        )
        value = _round1(total / purchase_days)
        if value <= 0:
            return ProductReference(
                None, target_tag, is_long_cycle=True, reason="non_positive",
                usage_days=usage_days, purchase_days=purchase_days,
            )
        return ProductReference(
            value, target_tag, same_tag_value=value, is_long_cycle=True,
            reason="long_cycle", usage_days=usage_days, purchase_days=purchase_days,
        )

    base, base_samples = _base_wma(usage, purchase_map, today, reference_days)
    if base <= 0:
        return ProductReference(
            None, target_tag, base_wma=0.0, reason="no_base",
            usage_days=usage_days, purchase_days=purchase_days,
        )

    same_tag, samples, nonzero = _same_tag_value(
        usage, purchase_map, tags, weekdays, target_tag, target, usage_days
    )
    recent3 = _recent3_avg(usage, today)
    cv = _cv(usage, today)

    if nonzero < 3:
        value = base
        reason = "same_tag_samples_lt_3"
    elif nonzero >= 6:
        trend_protect = recent3 * 0.7 if cv is not None and cv >= 0.5 else 0.0
        value = max(same_tag, trend_protect)
        reason = "same_tag_samples_ge_6"
    else:
        if cv is not None and cv >= 0.7:
            weight, trend_weight = 0.55, 0.9
        elif cv is not None and cv >= 0.4:
            weight, trend_weight = 0.7, 0.8
        else:
            weight, trend_weight = 0.85, 0.7
        blended = same_tag * weight + base * (1 - weight)
        value = max(blended, recent3 * trend_weight)
        reason = "blended"

    value = _round1(value)
    if value <= 0:
        return ProductReference(
            None, target_tag, base_wma=_round1(base), same_tag_value=same_tag,
            same_tag_samples=samples, same_tag_nonzero=nonzero,
            recent3_avg=_round1(recent3), cv=cv, reason="non_positive",
            usage_days=usage_days, purchase_days=purchase_days,
        )
    return ProductReference(
        value, target_tag, base_wma=_round1(base), same_tag_value=same_tag,
        same_tag_samples=samples, same_tag_nonzero=nonzero,
        recent3_avg=_round1(recent3), cv=cv, reason=reason,
        usage_days=usage_days, purchase_days=purchase_days,
        details={"base_samples": base_samples},
    )


def suggested_quantity(reference: Optional[float], *, safety_factor: float = DEFAULT_SAFETY_FACTOR,
                       current_stock: Optional[float] = None,
                       order_step: float = 1.0) -> Optional[float]:
    """建议量 = 参考值 × 安全系数 − 当前盘点，按 orderStep 向上取整。

    参考值空（'!' / 0）不给建议。结果小于 1 时保留 1 位小数，下限 0 —— 这是小程序
    的原口径（spec §5.5）：小单位的商品不该被 1 的步长强行放大。
    """
    if reference is None or reference <= 0:
        return None
    step = float(order_step or 1.0)
    if step <= 0:
        step = 1.0
    raw = float(reference) * float(safety_factor or DEFAULT_SAFETY_FACTOR) - float(current_stock or 0.0)
    raw = max(0.0, raw)
    if raw <= 0:
        return 0.0
    if raw < 1:
        return _round1(raw)
    return round(math.ceil(raw / step) * step + 1e-9, 2)
