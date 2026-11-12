#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘日（07:00 切）这把尺子（ADR 0140）。

钉住三件事：

1. `review_day_range` 的窗口是 `[当日 07:00, 次日 07:00)`，东八区，跨月 / 跨年不跳日；
2. 坏日期按 `date.fromisoformat` 的契约抛 `ValueError`（不悄悄兜底成别的日界）；
3. `is_primary_clock` 只认 `HH:MM` 形式的 `07:00`，含首尾空白，空值 / 坏值 False 且不抛。

窗口两端用 `datetime` 直接比（不是比字符串）：同一天的不同写法都必须落在同一个
半开区间上，否则「复盘日」又变成各写各的。
"""

from datetime import datetime, timedelta

import pytest

from db_core.utils import CHINA_TZ
from services.business_day import REVIEW_CLOCK, is_primary_clock, review_day_range


def test_review_clock_is_0700() -> None:
    assert REVIEW_CLOCK == "07:00"


def test_review_day_range_starts_at_0700_and_ends_next_day() -> None:
    start, end = review_day_range("2026-11-01")
    assert start == datetime(2026, 11, 1, 7, 0, tzinfo=CHINA_TZ)
    assert end == datetime(2026, 11, 2, 7, 0, tzinfo=CHINA_TZ)
    # 半开区间：窗口宽度恰好 24 小时，起点带东八区偏移。
    assert end - start == timedelta(hours=24)
    assert start.utcoffset() == timedelta(hours=8)


def test_review_day_range_crosses_month_end() -> None:
    start, end = review_day_range("2026-10-31")
    assert start == datetime(2026, 10, 31, 7, 0, tzinfo=CHINA_TZ)
    assert end == datetime(2026, 11, 1, 7, 0, tzinfo=CHINA_TZ)


def test_review_day_range_crosses_year_end() -> None:
    start, end = review_day_range("2025-12-31")
    assert start == datetime(2025, 12, 31, 7, 0, tzinfo=CHINA_TZ)
    assert end == datetime(2026, 1, 1, 7, 0, tzinfo=CHINA_TZ)


@pytest.mark.parametrize("bad", ["", "2026-13-01", "2026-11-31", "2026-11-01T00:00"])
def test_review_day_range_rejects_bad_dates(bad: str) -> None:
    with pytest.raises(ValueError):
        review_day_range(bad)


@pytest.mark.parametrize(
    "clock",
    ["07:00", " 07:00 ", "\t07:00\n"],
)
def test_is_primary_clock_accepts_0700_with_surrounding_space(clock: str) -> None:
    assert is_primary_clock(clock) is True


@pytest.mark.parametrize(
    "clock",
    ["08:00", "7:00", "07:0", "0700", "", "   ", None],
)
def test_is_primary_clock_rejects_everything_else(clock) -> None:
    assert is_primary_clock(clock) is False
