#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""票 23：`GET /api/orders/priority/urgent` 的候选窗口 = 当前营业日。

原先窗口是自然日 `[00:00, 23:59:59.999]`：凌晨 00:00 一过，前一个营业日
（00:00–06:00 仍属它）里已经严重超时的单就从紧急列表里消失了。

本文件钉三件事：

1. 冻 00:05（cutoff = 前一日 23:45）时，前一日 23:00 的超时单仍在列表里；
2. 冻 06:05 时，属于**前一个营业日**的 05:50 单不再作为候选（自然日窗口会把它算进来）；
3. `urgent_cutoff` 的「超时才算」与「按时间升序」两条原有语义不变，窗口与文案改走营业日。
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


_REAL_DATETIME = datetime


class _FrozenMeta(type):
    """让 `isinstance(value, 冻结类)` 仍然认真 datetime。

    `api/orders.py` 的紧急订单筛选里有一句 `isinstance(order_time, datetime)`。若把模块属性
    `datetime` 换成一个普通子类，这句对从库里取出的**真** datetime 会返回 False，筛选结果
    恒为空（`tests/test_urgent_window.py` 初版就踩过这个坑）。用 metaclass 把实例检查委派
    给真的 `datetime` 类即可：`datetime.now()` 被钉住，`isinstance` 行为不变。
    """

    def __instancecheck__(cls, instance):
        return isinstance(instance, _REAL_DATETIME)


class _FrozenDatetime(datetime, metaclass=_FrozenMeta):
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


def _urgent(db):
    orders = _run(_urgent_orders(db))
    return [order["dish_name"] for order in orders]


async def _urgent_orders(db):
    from api import orders as api_orders

    return await api_orders.get_urgent_orders(db=db)


# ── 窗口边界 ─────────────────────────────────────────────────────────────


def test_previous_business_day_order_stays_visible_after_midnight(db, monkeypatch):
    """冻 00:05（cutoff = 前一日 23:45）：前一日 23:00 的超时单必须还在紧急列表里。

    自然日窗口在这里会直接把 23:00 那单漏掉（返回空列表）。
    """
    from api import orders as api_orders

    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "前一夜已超时", "order_time": _ts(2026, 5, 1, 23, 0)},
                {"dish_name": "刚下单未超时", "order_time": _ts(2026, 5, 2, 0, 3)},
            ],
        )
    )
    _freeze(monkeypatch, api_orders, datetime(2026, 5, 2, 0, 5, tzinfo=CHINA_TZ))

    assert _urgent(db) == ["前一夜已超时"], (
        "00:05 时的候选窗口是营业日 [05-01 06:00, 05-02 06:00)，"
        "cutoff = 05-01 23:45，因此 23:00 的超时单在列表里、00:03 的不在"
    )


def test_previous_business_day_order_is_not_a_candidate_after_the_cut(db, monkeypatch):
    """冻 06:05：05:30 与 23:00 都属前一个营业日，即使已超时也不是候选。

    05:30 离 06:05 有 35 分钟，早已超过 20 分钟阈值；自然日窗口（[00:00, 23:59:59.999]）
    会把它当成候选返回，营业日窗口 [05-02 06:00, …) 不会。
    """
    from api import orders as api_orders

    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "前一日凌晨五点半", "order_time": _ts(2026, 5, 2, 5, 30)},
                {"dish_name": "前一日深夜", "order_time": _ts(2026, 5, 1, 23, 0)},
            ],
        )
    )
    _freeze(monkeypatch, api_orders, datetime(2026, 5, 2, 6, 5, tzinfo=CHINA_TZ))

    assert _urgent(db) == [], (
        "06:05 的候选窗口是 [05-02 06:00, 05-03 06:00)，两单都在窗口之前"
    )


def test_overdue_filter_and_ascending_order_are_unchanged(db, monkeypatch):
    """窗口内仍只保留「早于 cutoff」的单，并保持按时间升序。"""
    from api import orders as api_orders

    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "十一点", "order_time": _ts(2026, 5, 2, 11, 0)},
                {"dish_name": "十点", "order_time": _ts(2026, 5, 2, 10, 0)},
                {"dish_name": "十二点零五", "order_time": _ts(2026, 5, 2, 12, 5)},
                {"dish_name": "十三点", "order_time": _ts(2026, 5, 2, 13, 0)},
            ],
        )
    )
    _freeze(monkeypatch, api_orders, datetime(2026, 5, 2, 12, 0, tzinfo=CHINA_TZ))

    assert _urgent(db) == ["十点", "十一点"], (
        "cutoff = 11:40：只该留下 10:00 与 11:00，且按时间升序"
    )


# ── 静态：窗口与文案改走营业日 ────────────────────────────────────────────


def test_urgent_endpoint_uses_the_business_day_window():
    from api import orders as api_orders

    src = inspect.getsource(api_orders.get_urgent_orders)

    assert "business_day_window(" in src, src
    assert "hour=0, minute=0, second=0" not in src, "紧急订单窗口不应再有自然日 00:00 起点"
    assert "当前营业日" in src, "docstring 应与窗口口径一致"
