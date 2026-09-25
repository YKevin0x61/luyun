#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""票 25 / R-T8-01：面板聚合与 `get_orders` 的窗口语义统一到营业日。

背景（票 25 证据）：

* `db_core/reports.py` 的 `aggregate_kds_backlog` / `aggregate_dashboard_extras`
  原先用自然日 `[今日 00:00, 现在]`：00:00–06:00 看面板时，前一夜 06:00 之后入单的
  待出餐项与菜品分类整段丢失；
* `db_core/orders_repo.py:get_orders` 的右端是闭区间 `order_time <= ?`，与营业日窗口
  `[start, end)` 差一个整点（06:00:00.000 的单会同时落进两个窗口）；
* `api/admin.py:/sync-stations` 从自然日起点同步档口映射，凌晨改映射不会重同步前一夜。

本文件钉住营业日边界（06:00 切、右端开区间），全部用例把 `datetime.now()` 冻在固定时刻，
因此在任何墙钟时段（含 00:00–06:00 与 06:00–06:45）结论一致。
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta

import pytest

import db_core.reports as reports_module
from database import CHINA_TZ, DatabaseManager

CUT = 6
STATION = "dimsum"

# 营业日 2026-05-02 = [05-02 06:00, 05-03 06:00)
BEFORE_CUT = datetime(2026, 5, 2, 5, 30, tzinfo=CHINA_TZ)
AFTER_CUT = datetime(2026, 5, 2, 6, 30, tzinfo=CHINA_TZ)


def _run(coro):
    return asyncio.run(coro)


def _ts(year, month, day, hour, minute=0, second=0, microsecond=0) -> str:
    return datetime(
        year, month, day, hour, minute, second, microsecond, tzinfo=CHINA_TZ
    ).isoformat()


class _FrozenDatetime(datetime):
    """把目标模块里的 `datetime.now()` 钉在某一刻（monkeypatch 换模块属性）。"""

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
    "dish_status": "待出餐",
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
                    source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    row.get("source"),
                    row["order_time"],
                    row["order_time"],
                ),
            )
    await conn.commit()


# ── aggregate_kds_backlog：营业日窗口 ─────────────────────────────────────


def test_kds_backlog_before_the_cut_keeps_the_previous_evening(db, monkeypatch):
    """05:30 时：前一晚 23:00 与当日 05:00 都是「当前营业日」，再往前一天的 05:00 不是。"""
    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "前一晚", "order_time": _ts(2026, 5, 1, 23, 0)},
                {"dish_name": "当日凌晨", "order_time": _ts(2026, 5, 2, 5, 0)},
                {"dish_name": "上一营业日", "order_time": _ts(2026, 5, 1, 5, 0)},
            ],
        )
    )
    _freeze(monkeypatch, reports_module, BEFORE_CUT)

    backlog = _run(db.aggregate_kds_backlog())

    assert backlog["total_pending"] == 2, (
        "05:30 的当前营业日 [05-01 06:00, 05-02 06:00) 应含 23:00 与 05:00 两单，"
        f"旧的自然日窗口只含 05:00 一单；实际 {backlog['total_pending']}"
    )
    dimsum = next(s for s in backlog["stations"] if s["station_id"] == STATION)
    assert dimsum["pending"] == 2


def test_kds_backlog_after_the_cut_drops_the_previous_evening(db, monkeypatch):
    """06:30 时：前一晚 23:00 已属上一营业日（窗口外），只有 06:05 那单在窗内。"""
    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "前一晚", "order_time": _ts(2026, 5, 1, 23, 0)},
                {"dish_name": "当前营业日", "order_time": _ts(2026, 5, 2, 6, 5)},
            ],
        )
    )
    _freeze(monkeypatch, reports_module, AFTER_CUT)

    backlog = _run(db.aggregate_kds_backlog())

    assert backlog["total_pending"] == 1, (
        "06:30 的当前营业日 [05-02 06:00, 05-03 06:00) 只该含 06:05 一单，"
        f"实际 {backlog['total_pending']}"
    )


def test_kds_backlog_window_is_half_open_at_both_ends(db, monkeypatch):
    """窗口 [05-02 06:00, 05-03 06:00)：左端前一微秒与右端同刻出窗，左端同刻/窗内/右端前一微秒入窗。"""
    _run(
        _seed_lines(
            db,
            [
                {
                    "dish_name": "左端前一微秒",
                    "order_time": _ts(2026, 5, 2, 5, 59, 59, 999999),
                    "station": "wok",
                },
                {"dish_name": "左端同刻", "order_time": _ts(2026, 5, 2, 6, 0)},
                {"dish_name": "窗内", "order_time": _ts(2026, 5, 2, 6, 5)},
                {"dish_name": "右端前一微秒", "order_time": _ts(2026, 5, 3, 5, 59, 59, 999999)},
                {
                    "dish_name": "右端同刻",
                    "order_time": _ts(2026, 5, 3, 6, 0),
                    "station": "tanmian",
                },
            ],
        )
    )
    _freeze(monkeypatch, reports_module, AFTER_CUT)

    backlog = _run(db.aggregate_kds_backlog())

    assert backlog["total_pending"] == 3, (
        "窗口 [05-02 06:00, 05-03 06:00)：左端同刻/窗内/右端前一微秒 3 单在窗内，"
        "左端前一微秒（05-02 05:59:59.999999）与右端同刻（05-03 06:00:00.000）都出窗；"
        f"实际 {backlog['total_pending']}"
    )
    assert {s["station_id"] for s in backlog["stations"]} == {STATION}, (
        "左端前一微秒（wok）与右端同刻（tanmian）都不应出现在聚合里"
    )


# ── aggregate_dashboard_extras：分类数与紧急订单数 ────────────────────────


def test_dashboard_extras_before_the_cut_counts_the_previous_evening(db, monkeypatch):
    """05:30 时：前一晚 23:00 与当日 05:00 计入分类数，且都在「最新 20 分钟」之外。"""
    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "前一晚", "order_time": _ts(2026, 5, 1, 23, 0), "category": "点心"},
                {"dish_name": "当日凌晨", "order_time": _ts(2026, 5, 2, 5, 0), "category": "面点"},
                {"dish_name": "上一营业日", "order_time": _ts(2026, 5, 1, 5, 0), "category": "甜品"},
            ],
        )
    )
    _freeze(monkeypatch, reports_module, BEFORE_CUT)

    extras = _run(db.aggregate_dashboard_extras())

    assert extras["dish_category_count"] == 2, (
        "当前营业日应数到 点心/面点 两类（旧自然日只会数到当日 05:00 的面点）；"
        f"实际 {extras['dish_category_count']}"
    )
    assert extras["urgent_order_count"] == 2, (
        "05:10 之前（即最新 20 分钟之外）的两单都算紧急；"
        f"实际 {extras['urgent_order_count']}"
    )


def test_dashboard_extras_after_the_cut_uses_the_current_business_day(db, monkeypatch):
    """06:30 时：左端前一微秒/05:00/次日整点都出窗，右端前一微秒在窗内，20 分钟内的单不算紧急。"""
    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "窗内", "order_time": _ts(2026, 5, 2, 6, 10)},
                {"dish_name": "窗内稍后", "order_time": _ts(2026, 5, 2, 6, 15)},
                {
                    "dish_name": "右端前一微秒",
                    "order_time": _ts(2026, 5, 3, 5, 59, 59, 999999),
                    "category": "蒸点",
                },
                {
                    "dish_name": "左端前一微秒",
                    "order_time": _ts(2026, 5, 2, 5, 59, 59, 999999),
                    "category": "甜品",
                },
                {"dish_name": "上一营业日", "order_time": _ts(2026, 5, 2, 5, 0), "category": "甜品"},
                {"dish_name": "次日整点", "order_time": _ts(2026, 5, 3, 6, 0), "category": "面点"},
            ],
        )
    )
    _freeze(monkeypatch, reports_module, AFTER_CUT)

    extras = _run(db.aggregate_dashboard_extras())

    assert extras["dish_category_count"] == 2, (
        "当前营业日 [05-02 06:00, 05-03 06:00) 只该数到 点心/蒸点 两类：右端前一微秒"
        "（05-03 05:59:59.999999）在窗内，左端前一微秒（05-02 05:59:59.999999）、05:00、"
        f"次日 06:00 整点都在窗口外；实际 {extras['dish_category_count']}"
    )
    assert extras["urgent_order_count"] == 0, (
        "06:10/06:15 都不早于 now-20min（06:10），左端前一微秒在窗口外，紧急数应为 0；"
        f"实际 {extras['urgent_order_count']}"
    )


def test_dashboard_extras_falls_back_to_dish_names_inside_the_window(db, monkeypatch):
    """分类全为空（历史数据 / 新库）时兜底按 `dish_name` 去重，且兜底查询同样是半开营业日窗口。

    `_DEFAULT_LINE` 带 `"category": "点心"`，所以窗内行必须**显式**给 `"category": None`
    才会进 `category_count == 0` 的兜底分支；若兜底分支被删/被改坏，这里拿到的会是
    分类查询的 0（或把窗口外的行算进来的 3），断言即红。
    """
    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "兜底甲", "order_time": _ts(2026, 5, 2, 10, 0), "category": None},
                {
                    "dish_name": "兜底乙",
                    "order_time": _ts(2026, 5, 3, 5, 59, 59, 999999),
                    "category": None,
                },
                {
                    "dish_name": "兜底丙",
                    "order_time": _ts(2026, 5, 2, 5, 59, 59, 999999),
                    "category": None,
                },
                {"dish_name": "兜底丁", "order_time": _ts(2026, 5, 3, 6, 0), "category": None},
            ],
        )
    )
    _freeze(monkeypatch, reports_module, AFTER_CUT)

    extras = _run(db.aggregate_dashboard_extras())

    assert extras["dish_category_count"] == 2, (
        "分类全空时必须走 dish_name 兜底分支：窗内两条（兜底甲 05-02 10:00、"
        "兜底乙 05-03 05:59:59.999999）计入 = 2；左端前一微秒（兜底丙）与次日 06:00 "
        f"整点（兜底丁）在窗口外；实际 {extras['dish_category_count']}"
    )


# ── get_orders：右端闭区间改开区间 ───────────────────────────────────────


def test_get_orders_excludes_the_exact_end_time(db):
    """`order_time <= ?` 改 `< ?`：正好等于 end_time 的单不再返回，前 1 微秒仍返回。"""
    end = datetime(2026, 5, 2, 6, 0, tzinfo=CHINA_TZ)
    start = end - timedelta(hours=2)
    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "左端同刻", "order_time": start.isoformat()},
                {"dish_name": "窗内", "order_time": (end - timedelta(hours=1)).isoformat()},
                {
                    "dish_name": "右端前一微秒",
                    "order_time": (end - timedelta(microseconds=1)).isoformat(),
                },
                {"dish_name": "右端同刻", "order_time": end.isoformat()},
            ],
        )
    )

    rows = _run(db.orders.get_orders(start_time=start, end_time=end))

    names = {row["dish_name"] for row in rows}
    assert names == {"左端同刻", "窗内", "右端前一微秒"}, (
        "右端是同刻开区间：正好等于 end_time 的单不返回（否则 06:00 整点会被两个"
        f"营业日窗口各算一次）；实际 {names}"
    )


def test_search_orders_raw_excludes_the_exact_end_time(db):
    """`search_orders_raw` 的 order_time_end 同样是开区间（票 25 统一）。"""
    end = datetime(2026, 5, 2, 6, 0, tzinfo=CHINA_TZ)
    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "窗内", "order_time": (end - timedelta(minutes=30)).isoformat()},
                {"dish_name": "右端同刻", "order_time": end.isoformat()},
            ],
        )
    )

    rows = _run(
        db.orders.search_orders_raw(
            {}, 100, order_time_start=end - timedelta(hours=2), order_time_end=end
        )
    )

    names = {row["dish_name"] for row in rows}
    assert names == {"窗内"}, f"右端同刻不应返回；实际 {names}"


def test_delivery_flow_ids_exclude_the_exact_end_time(db):
    """`get_delivery_flow_ids`（切日窗口拉回用）的右端也是开区间。"""
    end = datetime(2026, 5, 2, 6, 0, tzinfo=CHINA_TZ)
    _run(
        _seed_lines(
            db,
            [
                {
                    "dish_name": "窗内外卖",
                    "order_time": (end - timedelta(minutes=30)).isoformat(),
                    "source": "delivery",
                },
                {
                    "dish_name": "右端同刻外卖",
                    "order_time": end.isoformat(),
                    "source": "delivery",
                },
            ],
        )
    )

    flow_ids = _run(db.orders.get_delivery_flow_ids(end - timedelta(hours=2), end))

    assert flow_ids == ["flow-0"], f"右端同刻的外卖流水号不应返回；实际 {flow_ids}"


# ── /sync-stations：当前营业日起点 ───────────────────────────────────────


class _RecordingCatalog:
    def __init__(self):
        self.calls = []

    async def sync_orders_since(self, since):
        self.calls.append(since)
        return {"success": True}


def test_sync_stations_uses_the_current_business_day_start(monkeypatch):
    """冻 02:00（还在上一营业日）：起点必须是「昨天 06:00」，不是「今天 00:00」。"""
    from api import admin as admin_module

    moment = datetime(2026, 5, 2, 2, 0, tzinfo=CHINA_TZ)
    _freeze(monkeypatch, admin_module, moment)
    catalog = _RecordingCatalog()

    result = _run(admin_module.sync_orders_stations(dish_catalog=catalog))

    assert result == {"success": True}
    assert catalog.calls == [datetime(2026, 5, 1, 6, 0, tzinfo=CHINA_TZ)], (
        "02:00 时当前营业日从昨天 06:00 开始（自然日写法会给今天 00:00）"
    )


def test_panel_and_sync_sources_are_free_of_natural_day_windows():
    """静态断言（与 verify③ 同口径）：面板聚合与 /sync-stations 源码里没有自然日字面量。"""
    from api import admin as admin_module

    reports_src = inspect.getsource(reports_module)
    assert "hour=0, minute=0, second=0" not in reports_src
    mixin = reports_module._ReportsMixin
    assert "business_day_window" in inspect.getsource(mixin.aggregate_kds_backlog)
    assert "business_day_window" in inspect.getsource(
        mixin.aggregate_dashboard_extras
    )
    assert "营业日" in mixin.aggregate_kds_backlog.__doc__
    assert "营业日" in mixin.aggregate_dashboard_extras.__doc__

    sync_src = inspect.getsource(admin_module.sync_orders_stations)
    assert "hour=0, minute=0, second=0" not in sync_src
    assert "business_day_window" in sync_src
    assert "营业日" in admin_module.sync_orders_stations.__doc__

    assert CUT == reports_module.BUSINESS_DAY_CUT_HOUR
