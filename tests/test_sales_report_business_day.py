#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""销售报表按**营业日**切（06:00 切点），不是日历日（CORR-05）。

缺陷背景：`resolve_report_dates` 按日历日给出日期，`compute_sales_report` 把它翻成
`[00:00:00, 23:59:59.999]`；而采集/对账/卫生都按 06:00 营业日切。同一个「今天」两套
含义，凌晨时段两边对不上（当期生产库 06:00 前订单 0 行，所以还没有数据被算错）。

本文件钉区间本身：营业日 2026-05-02 的报表 = `[05-02 06:00, 05-03 06:00)`。
`tests/test_wecom_push.py` 钉的是端点解析与 `last_sent_date` 的排重键。

报表按菜品聚合（`dish_sales` / `summary`），所以断言走件数与金额，而不是逐单主键。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from database import CHINA_TZ, DatabaseManager

BIZ_DATE = "2026-05-02"


def _run(coro):
    return asyncio.run(coro)


def _ts(year, month, day, hour, minute=0) -> str:
    return datetime(year, month, day, hour, minute, tzinfo=CHINA_TZ).isoformat()


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


async def _seed(db, rows):
    """`rows` 是 ``(菜品名, order_time)`` —— 菜品名用来区分是哪一单。"""
    conn = db.table("orders").conn
    async with conn.cursor() as cursor:
        for index, (dish_name, order_time) in enumerate(rows):
            await cursor.execute(
                """INSERT INTO orders
                   (business_flow_id, table_number, dish_name, quantity, order_time,
                    price, total_amount, status, category, station, dish_status,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"flow-{index}",
                    "A1",
                    dish_name,
                    1,
                    order_time,
                    10.0,
                    10.0,
                    "已结",
                    "点心",
                    "steamer",
                    "已出餐",
                    order_time,
                    order_time,
                ),
            )
    await conn.commit()


def _dishes(report) -> set:
    return {row["dish_name"] for row in report["dish_sales"]}


def test_business_day_range_covers_0600_to_next_0600(db):
    """四条边界：05:59 属前一营业日，06:00 起算，次日 01:30 仍在窗口内，次日 06:00 出窗。"""
    _run(
        _seed(
            db,
            [
                ("前一晚的夜宵", _ts(2026, 5, 2, 5, 59)),
                ("营业日开门", _ts(2026, 5, 2, 6, 0)),
                ("次日凌晨", _ts(2026, 5, 3, 1, 30)),
                ("次日开门", _ts(2026, 5, 3, 6, 0)),
            ],
        )
    )

    report = _run(db.reports.compute_sales_report(BIZ_DATE, BIZ_DATE))

    assert _dishes(report) == {"营业日开门", "次日凌晨"}, (
        f"营业日 {BIZ_DATE} 的窗口应为 [06:00, 次日 06:00)，实际命中 {sorted(_dishes(report))}"
    )
    assert report["summary"]["total_orders"] == 2


def test_early_morning_order_belongs_to_the_previous_business_day(db):
    """01:30 的单属于前一晚：两个营业日各拿到自己那一单。"""
    _run(
        _seed(
            db,
            [
                ("前一夜", _ts(2026, 5, 2, 1, 30)),
                ("当天白天", _ts(2026, 5, 2, 12, 0)),
            ],
        )
    )

    first = _run(db.reports.compute_sales_report("2026-05-01", "2026-05-01"))
    second = _run(db.reports.compute_sales_report(BIZ_DATE, BIZ_DATE))

    assert _dishes(first) == {"前一夜"}
    assert _dishes(second) == {"当天白天"}


def test_report_revenue_ignores_the_previous_nights_orders(db):
    """金额也按同一把尺子：05-02 的报表不把 05-02 00:30 那单算进来。"""
    _run(
        _seed(
            db,
            [
                ("前一夜", _ts(2026, 5, 2, 0, 30)),
                ("当天白天", _ts(2026, 5, 2, 12, 0)),
            ],
        )
    )

    report = _run(db.reports.compute_sales_report(BIZ_DATE, BIZ_DATE))
    revenue = report["summary"]["total_revenue"]

    assert revenue == 10.0, f"营业日报表营业额应为 10.0（只含 12:00 那单），实际 {revenue}"


def test_multi_day_range_is_half_open_on_both_ends(db):
    """跨营业日区间：`[05-02 06:00, 05-04 06:00)`，两端各排掉一单。"""
    _run(
        _seed(
            db,
            [
                ("区间之前", _ts(2026, 5, 2, 5, 0)),
                ("区间第一天", _ts(2026, 5, 2, 20, 0)),
                ("区间最后一天", _ts(2026, 5, 3, 20, 0)),
                ("区间之后", _ts(2026, 5, 4, 6, 0)),
            ],
        )
    )

    report = _run(db.reports.compute_sales_report("2026-05-02", "2026-05-03"))

    assert _dishes(report) == {"区间第一天", "区间最后一天"}
