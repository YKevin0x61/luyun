#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3R-V1/V2 / 票 22：档口订单计数必须带右端（营业日半开区间）。

`aggregate_station_counts` 原先只判下界（`order_time >= ?`），档口「今日单数」跨营业日
只增不减：00:00–06:00 看档口面板时，前一个营业日的单还挂在「今日」上。

本文件钉四件事：

1. 传了 `end_time` 时右端是**开区间**（次日 06:00:00.000 的同刻单不计入）、左端闭；
2. 只传 `start_time` 时保持旧行为（只判下界），兼容单参数调用方；
3. 两个出口都把营业日窗口两端传下去：`/stations-today-stats` 用探针，`main.py` 的
   `/api/dashboard/summary` 用静态断言（该端点的旁路依赖太多，不适合整链跑）；
4. `db_core/ports.py` 与 `db_core/adapters/orders.py` 的签名同步暴露可选右端。
"""

from __future__ import annotations

import asyncio
import inspect
import re
from datetime import datetime

import pytest

from database import CHINA_TZ, DatabaseManager

STATION = "steamer"

BUSINESS_START = datetime(2026, 5, 2, 6, 0, tzinfo=CHINA_TZ)
BUSINESS_END = datetime(2026, 5, 3, 6, 0, tzinfo=CHINA_TZ)


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


def _counts(db, **kwargs):
    rows = _run(db.orders.aggregate_station_counts(**kwargs))
    return {row["station_id"]: row["count"] for row in rows}


# 营业日 2026-05-02 = [05-02 06:00, 05-03 06:00)：窗前一单、窗内一单、右端同刻一单、窗后一单。
_WINDOW_ROWS = [
    {"dish_name": "门前", "order_time": _ts(2026, 5, 2, 5, 30)},
    {"dish_name": "窗内", "order_time": _ts(2026, 5, 2, 12, 0)},
    {"dish_name": "右端同刻", "order_time": _ts(2026, 5, 3, 6, 0)},
    {"dish_name": "窗后", "order_time": _ts(2026, 5, 3, 7, 0)},
]


# ── 聚合本身：右端语义 ────────────────────────────────────────────────────


def test_window_excludes_the_open_right_end(db):
    """传了 `end_time`：只有窗内那单计入，右端同刻与窗后都不算。"""
    _run(_seed_lines(db, _WINDOW_ROWS))

    counts = _counts(db, start_time=BUSINESS_START, end_time=BUSINESS_END)

    assert counts.get(STATION) == 1, (
        "窗口 [05-02 06:00, 05-03 06:00) 只该含 12:00 那单（右端开区间），"
        f"实际 {counts}"
    )


def test_window_start_is_inclusive_and_right_end_is_open(db):
    """左端 `order_time >= start` 含同刻；右端 `order_time < end` 排除同刻。"""
    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "左端同刻", "order_time": _ts(2026, 5, 2, 6, 0)},
                {"dish_name": "右端同刻", "order_time": _ts(2026, 5, 3, 6, 0)},
            ],
        )
    )

    counts = _counts(db, start_time=BUSINESS_START, end_time=BUSINESS_END)

    assert counts.get(STATION) == 1, f"左端闭、右端开，应只留左端同刻那单，实际 {counts}"


def test_default_end_time_keeps_the_legacy_lower_bound_only(db):
    """票 22 验收：缺省 `end_time` 时行为与旧版一致——只判下界。"""
    _run(_seed_lines(db, _WINDOW_ROWS))

    counts = _counts(db, start_time=BUSINESS_START)

    assert counts.get(STATION) == 3, (
        "缺省右端时仍应只判下界（窗内 + 右端同刻 + 窗后），兼容单参数调用方，"
        f"实际 {counts}"
    )


# ── 出口：两端都要传下去 ──────────────────────────────────────────────────


def test_stations_today_stats_endpoint_passes_the_right_end(db, monkeypatch):
    """`/stations-today-stats` 探针：02:00 取前一个营业日，且不含**下一个**营业日的单。"""
    from api import orders as api_orders

    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "前一晚", "order_time": _ts(2026, 5, 1, 20, 0)},
                {"dish_name": "凌晨", "order_time": _ts(2026, 5, 2, 1, 30)},
                {"dish_name": "次日开门后", "order_time": _ts(2026, 5, 2, 7, 0)},
            ],
        )
    )
    _freeze(monkeypatch, api_orders, datetime(2026, 5, 2, 2, 0, tzinfo=CHINA_TZ))

    data = _run(api_orders.get_stations_today_stats(db=db))

    counts = {row["station_id"]: row["count"] for row in data["stats"]}
    assert counts.get(STATION) == 2, (
        "02:00 的窗口是 [05-01 06:00, 05-02 06:00)：前两单在内、07:00 那单属于下一个营业日，"
        f"只传下界会多算一单，实际 {counts}"
    )
    assert data["date"] == "2026-05-01"


def test_dashboard_summary_passes_the_business_day_window():
    """`/api/dashboard/summary` 静态断言：起点来自营业日窗口，计数同时拿到右端。"""
    import main as main_module

    src = inspect.getsource(main_module.get_dashboard_summary)

    assert "business_day_window(" in src, src
    assert re.search(
        r"aggregate_station_counts\(\s*business_day_start\s*,\s*business_day_end\s*\)", src
    ), f"档口计数必须把窗口两端都传下去，实际源码：{src}"
    assert "hour=0, minute=0, second=0" not in src, "仪表盘不应再有自然日 00:00 起点"


# ── 接口签名：三处同步 ───────────────────────────────────────────────────


def test_signatures_expose_the_optional_right_end(db):
    """`_AggregationMixin` / `OrdersPort` / `OrdersPortAdapter` 三处签名一致。"""
    from db_core.adapters.orders import OrdersPortAdapter
    from db_core.aggregation import _AggregationMixin
    from db_core.ports import OrdersPort

    for label, func in (
        ("db_core/aggregation.py", _AggregationMixin.aggregate_station_counts),
        ("db_core/ports.py", OrdersPort.aggregate_station_counts),
        ("db_core/adapters/orders.py", OrdersPortAdapter.aggregate_station_counts),
    ):
        params = inspect.signature(func).parameters
        assert "end_time" in params, f"{label} 缺少可选右端 end_time"
        assert params["end_time"].default is None, f"{label} 的 end_time 默认值应为 None"

    # 运行时（适配器实例）同样可传两端。
    counts = _counts(db, start_time=BUSINESS_START, end_time=BUSINESS_END)
    assert counts == {} or counts.get(STATION) is None
