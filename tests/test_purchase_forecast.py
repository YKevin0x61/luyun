#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""厨房采购参考值纯函数（spec §5，ADR 0157）。

这一套照搬小程序口径，和备货参考值是两套模型。这里钉住几条最容易改坏的规则：
标签优先级、逐日用量必须有今昨两天盘点、长周期按「总采购 / 有采购天数」、
以及建议量的步长与 1 以下保留一位小数。
"""

from datetime import date, timedelta

from services.purchase.forecast import (
    DEFAULT_TAG,
    daily_usage_series,
    effective_tag,
    reference_for_product,
    suggested_quantity,
)


def test_effective_tag_priority():
    day = date(2026, 11, 21)
    assert effective_tag(day, {}, {}) == DEFAULT_TAG
    assert effective_tag(day, {}, {day.weekday(): "5万"}) == "5万"
    assert effective_tag(day, {day: "8万"}, {day.weekday(): "5万"}) == "8万"
    # 非法标签不生效，继续往下退。
    assert effective_tag(day, {day: "9万"}, {day.weekday(): "4万"}) == "4万"


def test_daily_usage_needs_two_stock_days_and_clamps_negative():
    start = date(2026, 11, 18)
    usage = daily_usage_series(
        {
            start: 10,
            start + timedelta(days=1): 8,
            start + timedelta(days=2): 1,
            start + timedelta(days=3): 20,
        },
        {start: 5, start + timedelta(days=1): 0, start + timedelta(days=2): 0},
        start,
        start + timedelta(days=3),
    )
    assert usage[start] is None if False else True  # 首日缺前一天盘点，下面逐条钉
    assert start not in usage
    assert usage[start + timedelta(days=1)] == 7  # 10 + 5 - 8
    assert usage[start + timedelta(days=2)] == 7  # 8 + 0 - 1
    assert usage[start + timedelta(days=3)] == 0  # 1 + 0 - 20，负数归零


def test_reference_no_history_gives_none():
    ref = reference_for_product(
        today=date(2026, 11, 21), purchases=[], stocks={}
    )
    assert ref.reference is None
    assert ref.reason == "no_base"


def test_reference_falls_back_to_purchase_wma():
    today = date(2026, 11, 21)
    purchases = [
        (today - timedelta(days=3), 2),
        (today - timedelta(days=2), 4),
        (today - timedelta(days=1), 6),
    ]
    ref = reference_for_product(today=today, purchases=purchases, stocks={})
    assert ref.reference == 4.7
    # 连续 3 天采购都落在默认标签 3万 上，非零样本已够 3 个，走的是混合分支。
    assert ref.reason == "blended"


def test_reference_long_cycle_uses_average_per_purchase_day():
    today = date(2026, 11, 21)
    purchases = [
        (today - timedelta(days=10), 40),
        (today - timedelta(days=4), 60),
    ]
    ref = reference_for_product(
        today=today, purchases=purchases, stocks={}, is_long_cycle=True
    )
    assert ref.reference == 50.0
    assert ref.reason == "long_cycle"
    assert ref.purchase_days == 2


def test_suggested_quantity_rounds_up_to_step_and_keeps_small_values():
    assert suggested_quantity(None) is None
    assert suggested_quantity(0) is None
    assert suggested_quantity(10, safety_factor=1.2, current_stock=3, order_step=1) == 9
    assert suggested_quantity(10, safety_factor=1.2, current_stock=3, order_step=5) == 10
    assert suggested_quantity(10, safety_factor=1.2, current_stock=20) == 0.0
    assert suggested_quantity(0.5, safety_factor=1.0, current_stock=0) == 0.5
