#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R-T3-03 / 票 21：档口统计与进单速率默认走**营业日** `[06:00, 次日 06:00)`。

DATA-03 把三个经营聚合改成营业日窗口后，`get_station_stats` 与
`GET /api/orders/station/{id}/stats`、`/stations-today-stats`、`/station-speed` 的默认
窗口还是自然日：凌晨 00:00–06:00 档口面板「今日」清零、速率图看的是空日历日。

本文件钉三件事：

1. 营业日切点只有一处定义（`db_core/business_day.py`），与 `services.business_day`
   同一把尺子；
2. 省略窗口时 `get_station_stats` 取当前营业日（05:30 的单落前一个营业日、06:30 落当前
   营业日、次日 06:00:00.000 因右端开区间不进窗）；
3. 三个档口 API 出口跟着换尺子（`/station-speed` 省略 `date` 时 target = 当前营业日起点）。
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime

import pytest

from database import CHINA_TZ, DatabaseManager

STATION = "steamer"

def _run(coro):
    return asyncio.run(coro)


def _ts(year, month, day, hour, minute=0) -> str:
    return datetime(year, month, day, hour, minute, tzinfo=CHINA_TZ).isoformat()


class _FrozenDatetime(datetime):
    """把目标模块里的 `datetime.now()` 钉在某一刻（`monkeypatch` 换模块属性）。"""

    frozen: datetime

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.frozen.replace(tzinfo=None)
        return cls.frozen.astimezone(tz)


def _freeze(monkeypatch, module, moment: datetime) -> None:
    monkeypatch.setattr(module, "datetime", type("_Frozen", (_FrozenDatetime,), {"frozen": moment}))


@pytest.fixture
def db(tmp_path):
    from config import settings

    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    manager = DatabaseManager()
    assert _run(manager.connect())
    yield manager
    _run(manager.close())
    settings.DATABASE_DIR = old


_DEFAULT_LINE = {
    "table_number": "A1",
    "quantity": 1,
    "price": 10.0,
    "total_amount": 10.0,
    "status": "已结",
    "category": "点心",
    "station": STATION,
    "dish_status": "已出餐",
}


async def _seed_lines(db, lines):
    """按字段落库（未给的取 `_DEFAULT_LINE`）；每行必须有 `dish_name` 与 `order_time`。"""
    conn = db.table("orders").conn
    async with conn.cursor() as cursor:
        for index, line in enumerate(lines):
            row = {**_DEFAULT_LINE, **line}
            await cursor.execute(
                """INSERT INTO orders
                   (business_flow_id, table_number, dish_name, quantity, order_time,
                    price, total_amount, status, category, station, dish_status,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"flow-{index}",
                    row["table_number"],
                    row["dish_name"],
                    row["quantity"],
                    row["order_time"],
                    row["price"],
                    row["total_amount"],
                    row["status"],
                    row["category"],
                    row["station"],
                    row["dish_status"],
                    row["order_time"],
                    row["order_time"],
                ),
            )
    await conn.commit()


# 营业日 2026-05-02 的四条边界单：05:30 属前一营业日，12:00 与次日 01:30 在窗内，
# 次日 06:00 是开区间右端（出窗）。
_BOUNDARY_LINES = [
    {"dish_name": "前一夜夜宵", "order_time": _ts(2026, 5, 2, 5, 30)},
    {"dish_name": "营业日中午", "order_time": _ts(2026, 5, 2, 12, 0)},
    {"dish_name": "次日凌晨", "order_time": _ts(2026, 5, 3, 1, 30)},
    {"dish_name": "次日开门", "order_time": _ts(2026, 5, 3, 6, 0)},
]


# ── 切点定义：只有一处 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "hour, expected_start_date",
    [(5, "2026-05-01"), (6, "2026-05-02"), (12, "2026-05-02"), (23, "2026-05-02")],
)
def test_business_day_window_start_depends_on_the_cut_hour(hour, expected_start_date):
    """06:00 前算前一个营业日；06:00 起算当天。窗口长度恒为 24h。"""
    from db_core.business_day import business_day_window

    start, end = business_day_window(datetime(2026, 5, 2, hour, 0, tzinfo=CHINA_TZ))

    assert start.strftime("%Y-%m-%d") == expected_start_date, start
    assert (start.hour, start.minute, start.second) == (6, 0, 0), start
    assert (end - start).total_seconds() == 86400


def test_reports_reuses_the_single_business_day_definition():
    """票 21①：`db_core/reports.py` 的 `_business_day_range` 不再是第二份实现。"""
    from db_core import reports as db_reports
    from db_core.business_day import BUSINESS_DAY_CUT_HOUR, business_day_range

    assert db_reports._business_day_range is business_day_range
    assert db_reports.BUSINESS_DAY_CUT_HOUR == BUSINESS_DAY_CUT_HOUR == 6


def test_db_core_business_day_matches_services_business_day():
    """db_core 内独立定义（不得 import services），但两边必须同一把尺子。"""
    from db_core.business_day import BUSINESS_DAY_CUT_HOUR, business_day_range
    from services.business_day import (
        BUSINESS_DAY_CUT_HOUR as SERVICE_CUT_HOUR,
        business_date_range,
    )

    assert BUSINESS_DAY_CUT_HOUR == SERVICE_CUT_HOUR == 6
    for business_date in ("2026-05-01", "2026-05-02", "2026-12-31"):
        assert business_day_range(business_date) == business_date_range(business_date)


# ── `get_station_stats` 的默认窗口 ─────────────────────────────────────────


def test_station_stats_defaults_to_the_current_business_day(db, monkeypatch):
    """冻结在 05-03 04:00：默认窗口 = 营业日 05-02 = [05-02 06:00, 05-03 06:00)。"""
    import db_core.aggregation as agg

    _run(_seed_lines(db, _BOUNDARY_LINES))
    _freeze(monkeypatch, agg, datetime(2026, 5, 3, 4, 0, tzinfo=CHINA_TZ))

    stats = _run(db.orders.get_station_stats(STATION))

    assert stats["total_orders"] == 2, (
        f"默认窗口应是营业日 [05-02 06:00, 05-03 06:00)，"
        f"只含 12:00 与次日 01:30，实际 {stats}"
    )
    assert stats["total_quantity"] == 2
    assert stats["urgent_count"] == 2, "两单都已超过 urgent 阈值（now 冻结在 04:00）"


def test_station_stats_early_morning_order_belongs_to_the_previous_business_day(db, monkeypatch):
    """05:59 时默认窗口起点是**前一天** 06:00：05:30 那单仍在窗内，10:00 那单不算。"""
    import db_core.aggregation as agg

    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "清晨开店前", "order_time": _ts(2026, 5, 2, 5, 30)},
                {"dish_name": "当天上午", "order_time": _ts(2026, 5, 2, 10, 0)},
            ],
        )
    )
    _freeze(monkeypatch, agg, datetime(2026, 5, 2, 5, 59, tzinfo=CHINA_TZ))

    stats = _run(db.orders.get_station_stats(STATION))

    assert stats["total_orders"] == 1, (
        "00:00–05:59 的默认窗口是 [前一日 06:00, 当日 06:00)：只该留下 05:30 那单，"
        f"实际 {stats}"
    )


def test_station_stats_excludes_the_next_business_day_boundary(db, monkeypatch):
    """次日 06:00:00.000 因右端开区间不进窗；它属于新的营业日。"""
    import db_core.aggregation as agg

    _run(_seed_lines(db, [_BOUNDARY_LINES[2], _BOUNDARY_LINES[3]]))

    _freeze(monkeypatch, agg, datetime(2026, 5, 3, 4, 0, tzinfo=CHINA_TZ))
    stats = _run(db.orders.get_station_stats(STATION))
    assert stats["total_orders"] == 1, f"次日 06:00 是开区间右端，实际 {stats}"

    _freeze(monkeypatch, agg, datetime(2026, 5, 3, 6, 30, tzinfo=CHINA_TZ))
    next_day = _run(db.orders.get_station_stats(STATION))
    assert next_day["total_orders"] == 1, f"06:30 时同一个 06:00 的单应属于新营业日，实际 {next_day}"


def test_station_stats_explicit_window_right_end_is_open(db):
    """显式窗口也按半开区间：右端 12:00 同刻的单被排除。"""
    _run(_seed_lines(db, [{"dish_name": "整点单", "order_time": _ts(2026, 5, 2, 12, 0)}]))

    stats = _run(
        db.orders.get_station_stats(
            STATION,
            start_time=datetime(2026, 5, 2, 6, 0, tzinfo=CHINA_TZ),
            end_time=datetime(2026, 5, 2, 12, 0, tzinfo=CHINA_TZ),
        )
    )

    assert stats["total_orders"] == 0, f"右端应为开区间，实际 {stats}"


# ── API 出口 ──────────────────────────────────────────────────────────────


def test_station_stats_endpoint_uses_the_business_day_window(db, monkeypatch):
    """`GET /api/orders/station/{id}/stats` 省略窗口时同口径（不是自然日）。"""
    import db_core.aggregation as agg
    from api import orders as api_orders

    _run(_seed_lines(db, _BOUNDARY_LINES))
    _freeze(monkeypatch, agg, datetime(2026, 5, 3, 4, 0, tzinfo=CHINA_TZ))

    stats = _run(
        api_orders.get_station_order_stats(STATION, start_time=None, end_time=None, db=db)
    )

    assert stats["total_orders"] == 2, f"端点默认窗口应是当前营业日，实际 {stats}"


def test_stations_today_stats_endpoint_uses_the_business_day_window(db, monkeypatch):
    """`GET /api/orders/stations-today-stats`：02:00 的「今日」= 前一日 06:00 起。"""
    from api import orders as api_orders

    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "前一夜", "order_time": _ts(2026, 5, 1, 20, 0)},
                {"dish_name": "凌晨归前一营业日", "order_time": _ts(2026, 5, 2, 1, 30)},
            ],
        )
    )
    _freeze(monkeypatch, api_orders, datetime(2026, 5, 2, 2, 0, tzinfo=CHINA_TZ))

    data = _run(api_orders.get_stations_today_stats(db=db))

    counts = {row["station_id"]: row["count"] for row in data["stats"]}
    assert counts.get(STATION) == 2, (
        "02:00 的「今日档口单数」应取营业日 [05-01 06:00, 05-02 06:00)：两单都在窗内，"
        f"实际 {counts}"
    )
    assert data["date"] == "2026-05-01", "返回日期应是当前营业日起点日期"


def test_station_speed_default_target_is_the_business_day_start(db, monkeypatch):
    """`/station-speed` 省略 `date`：02:00 → 前一天；06:30 → 当天。"""
    from api import orders as api_orders

    _freeze(monkeypatch, api_orders, datetime(2026, 5, 2, 2, 0, tzinfo=CHINA_TZ))
    early = _run(api_orders.get_station_speed(date=None, db=db))
    assert early["date"] == "2026-05-01", f"02:00 的默认目标应是当前营业日起点，实际 {early['date']}"

    _freeze(monkeypatch, api_orders, datetime(2026, 5, 2, 6, 30, tzinfo=CHINA_TZ))
    after_cut = _run(api_orders.get_station_speed(date=None, db=db))
    assert after_cut["date"] == "2026-05-02", after_cut


def test_station_endpoint_docs_describe_the_business_day():
    """参数描述与 docstring 同步改口径（不再写「默认今日」）。"""
    from api import orders as api_orders

    speed_src = inspect.getsource(api_orders.get_station_speed)
    stats_src = inspect.getsource(api_orders.get_station_order_stats)
    counts_src = inspect.getsource(api_orders.get_stations_today_stats)

    assert "默认当前营业日（06:00 起）" in speed_src, speed_src
    assert "营业日" in stats_src, stats_src
    assert "营业日" in counts_src, counts_src
