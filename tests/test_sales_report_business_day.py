#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""销售报表与经营聚合按**营业日**切（06:00 切点），不是日历日（CORR-05 / DATA-03）。

缺陷背景：`resolve_report_dates` 按日历日给出日期，`compute_sales_report` 把它翻成
`[00:00:00, 23:59:59.999]`；而采集/对账/卫生都按 06:00 营业日切。同一个「今天」两套
含义，凌晨时段两边对不上（当期生产库 06:00 前订单 0 行，所以还没有数据被算错）。
`compute_sales_report` 先修了一轮；DATA-03 是同一把尺子没落到三个经营聚合上
（`aggregate_table_operations` / `aggregate_sales_trend` / `aggregate_refund_stats`），
所以下面每个聚合都钉「窗口 = [营业日 06:00, 次日 06:00)」，并顺带盖上三个 API 出口
（`api/tables.py` 的 `/operations`、`api/analytics.py` 的 `/sales-trend`、`/refunds`）。

本文件钉区间本身：营业日 2026-05-02 的报表 = `[05-02 06:00, 05-03 06:00)`。
`tests/test_wecom_push.py` 钉的是端点解析与 `last_sent_date` 的排重键。

报表按菜品聚合（`dish_sales` / `summary`），所以断言走件数与金额，而不是逐单主键。

末尾一节是 SEC-09：导出接口的日期/档口参数先校验，非法输入必须是 4xx，而不是
「导出 CSV 失败」的 500。
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


_DEFAULT_LINE = {
    "table_number": "A1",
    "quantity": 1,
    "price": 10.0,
    "total_amount": 10.0,
    "status": "已结",
    "category": "点心",
    "station": "steamer",
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


async def _seed(db, rows):
    """`rows` 是 ``(菜品名, order_time)`` —— 菜品名用来区分是哪一单。"""
    await _seed_lines(
        db, [{"dish_name": name, "order_time": order_time} for name, order_time in rows]
    )


# 营业日 2026-05-02 的四条边界单：05:30 属前一营业日，12:00 与次日 01:30 在窗内，
# 次日 06:00 出窗。表号用来检查「同一桌只算窗内的单」。
_BOUNDARY_LINES = [
    {"dish_name": "前一夜的夜宵", "order_time": _ts(2026, 5, 2, 5, 30), "table_number": "A1"},
    {"dish_name": "营业日中午", "order_time": _ts(2026, 5, 2, 12, 0), "table_number": "A1"},
    {"dish_name": "次日凌晨", "order_time": _ts(2026, 5, 3, 1, 30), "table_number": "B2"},
    {"dish_name": "次日开门", "order_time": _ts(2026, 5, 3, 6, 0), "table_number": "B2"},
]

# 两条退菜单：05:30 属前一营业日，次日 01:30 在窗内。
_REFUND_LINES = [
    {
        "dish_name": "前一夜退的菜",
        "order_time": _ts(2026, 5, 2, 5, 30),
        "quantity": -1,
        "total_amount": -10.0,
        "status": "已退",
    },
    {
        "dish_name": "次日凌晨退的菜",
        "order_time": _ts(2026, 5, 3, 1, 30),
        "quantity": -1,
        "total_amount": -10.0,
        "status": "已退",
    },
]


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


# ── DATA-03：三个经营聚合跟着换尺子 ─────────────────────────────────────────


def test_table_operations_are_aggregated_by_business_day(db):
    """翻台/客单：营业日 05-02 只算 12:00 与次日 01:30 两单，05:30 与次日 06:00 出窗。"""
    _run(_seed_lines(db, _BOUNDARY_LINES))

    data = _run(db.reports.aggregate_table_operations(BIZ_DATE, BIZ_DATE))

    by_table = {row["table_number"]: row["order_lines"] for row in data["by_table"]}
    assert by_table == {"A1": 1, "B2": 1}, (
        f"营业日 {BIZ_DATE} 的窗口是 [05-02 06:00, 05-03 06:00)："
        f"A1 只该留下中午那单、B2 只该留下凌晨那单，实际 {by_table}"
    )
    assert data["tables_served"] == 2
    assert data["total_revenue"] == 20.0


def test_sales_trend_is_aggregated_by_business_day(db):
    """趋势：凌晨 01:30 归到 05-03（它属于 05-02 营业日），05:30/次日 06:00 都不进窗。"""
    _run(_seed_lines(db, _BOUNDARY_LINES))

    series = _run(db.reports.aggregate_sales_trend(BIZ_DATE, BIZ_DATE, "day"))

    assert {str(row["period"]): row["order_lines"] for row in series} == {
        "2026-05-02": 1,
        "2026-05-03": 1,
    }, f"实际 {series}"


def test_refund_stats_are_aggregated_by_business_day(db):
    """退菜：只有次日 01:30 那笔在营业日窗口内；05-02 05:30 那笔属于前一晚。"""
    _run(_seed_lines(db, _BOUNDARY_LINES))
    _run(_seed_lines(db, _REFUND_LINES))

    data = _run(db.reports.aggregate_refund_stats(BIZ_DATE, BIZ_DATE))

    assert data["refund_line_count"] == 1
    assert {row["dish_name"] for row in data["items"]} == {"次日凌晨退的菜"}


def test_api_endpoints_expose_the_business_day_windows(db):
    """三个 API 出口（api/tables.py 的 /operations、api/analytics.py 的 /sales-trend、/refunds）。"""
    from api.analytics import refund_stats, sales_trend
    from api.tables import get_table_operations

    _run(_seed_lines(db, _BOUNDARY_LINES))
    _run(_seed_lines(db, _REFUND_LINES))

    operations = _run(
        get_table_operations(start_date=BIZ_DATE, end_date=BIZ_DATE, db=db)
    )
    assert operations["success"] is True
    assert operations["tables_served"] == 2
    assert operations["date_range"] == {"start": BIZ_DATE, "end": BIZ_DATE}

    trend = _run(
        sales_trend(
            granularity="day",
            start_date=BIZ_DATE,
            end_date=BIZ_DATE,
            station=None,
            db=db,
        )
    )
    assert trend["success"] is True
    assert {str(row["period"]) for row in trend["series"]} == {"2026-05-02", "2026-05-03"}

    refunds = _run(
        refund_stats(start_date=BIZ_DATE, end_date=BIZ_DATE, station=None, db=db)
    )
    assert refunds["success"] is True
    assert {row["dish_name"] for row in refunds["items"]} == {"次日凌晨退的菜"}


# ── SEC-09：导出接口的参数校验（api/export_api.py）──────────────────────────


class _StubReportsPort:
    """只验参数校验，不进报表层：记录调用并回一份最小报表。"""

    def __init__(self):
        self.calls = []

    async def compute_sales_report(self, start_date, end_date, station=None):
        self.calls.append((start_date, end_date, station))
        return {
            "dish_sales": [
                {"dish_name": "虾饺", "station": "steamer", "qty": 2, "total_amount": 24.0}
            ],
            "semi_finished": [],
        }


class _StubDb:
    def __init__(self):
        self.reports = _StubReportsPort()


def _export_client(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import export_api
    from database import get_db

    app = FastAPI()
    app.include_router(export_api.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_export_rejects_malformed_date_before_the_report_layer():
    """形状不对（pattern 挡下）→ 422，且报表层一次都没被调用。"""
    db = _StubDb()

    response = _export_client(db).get(
        "/api/export/sales-report.csv",
        params={"start_date": "不是日期", "end_date": BIZ_DATE},
    )

    assert response.status_code == 422, response.text
    assert "start_date" in response.text, "422 要点出是哪个参数"
    assert db.reports.calls == []


def test_export_rejects_impossible_calendar_date_with_400():
    """形状对但日历上不存在（2026-02-30）→ 400，不再是 500「导出 CSV 失败」。"""
    db = _StubDb()

    response = _export_client(db).get(
        "/api/export/sales-report.csv",
        params={"start_date": "2026-02-30", "end_date": BIZ_DATE},
    )

    assert response.status_code == 400, response.text
    assert "start_date" in response.text
    assert db.reports.calls == []


def test_export_rejects_overlong_station_name():
    response = _export_client(_StubDb()).get(
        "/api/export/sales-report.csv",
        params={"start_date": BIZ_DATE, "end_date": BIZ_DATE, "station": "x" * 65},
    )

    assert response.status_code == 422, response.text


def test_export_streams_csv_with_a_quoted_filename():
    """合法参数：200 + CSV 正文 + 经 quote() 写入 Content-Disposition 的文件名。"""
    db = _StubDb()

    response = _export_client(db).get(
        "/api/export/sales-report.csv",
        params={"start_date": BIZ_DATE, "end_date": BIZ_DATE, "station": "steamer"},
    )

    assert response.status_code == 200, response.text
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="sales_2026-05-02_2026-05-02.csv"'
    )
    assert "虾饺" in response.text
    assert db.reports.calls == [(BIZ_DATE, BIZ_DATE, "steamer")]


# ── 独立验证（verifier-db / V3）：口径交叉核对 + 真报表层的导出校验 ────────────


def test_v3_business_day_range_agrees_with_services_business_day():
    """DATA-03 鉴别力所在：报表层的切点必须与 `services.business_day` 同一把尺子。"""
    from db_core import reports as db_reports
    from services.business_day import business_date_range

    for business_date in ("2026-05-01", "2026-05-02", "2026-12-31"):
        report_start, report_end = db_reports._business_day_range(business_date)
        services_start, services_end = business_date_range(business_date)
        assert report_start == services_start, f"{business_date}: 起点口径不一致"
        assert report_end == services_end, f"{business_date}: 终点口径不一致"
        assert report_start.hour == 6 and report_start.minute == 0, (
            f"{business_date}: 切点应是 06:00，实际 {report_start}"
        )
        assert (report_end - report_start).total_seconds() == 86400


def test_v3_early_morning_order_belongs_to_the_previous_business_day(db):
    """验收⑤：00:00–06:00 的单只出现在**前一**营业日的报表里。"""
    _run(
        _seed_lines(
            db,
            [
                {"dish_name": "清晨开店前的单", "order_time": _ts(2026, 5, 2, 5, 30)},
                {"dish_name": "当天上午的单", "order_time": _ts(2026, 5, 2, 10, 0)},
            ],
        )
    )

    today = _run(db.reports.compute_sales_report(BIZ_DATE, BIZ_DATE))
    yesterday = _run(db.reports.compute_sales_report("2026-05-01", "2026-05-01"))

    assert _dishes(today) == {"当天上午的单"}, "05:30 的单属前一营业日，不该算进 05-02"
    assert _dishes(yesterday) == {"清晨开店前的单"}, "05:30 的单必须落在前一营业日"
    assert today["summary"]["total_revenue"] == 10.0, today["summary"]


def test_v3_export_validates_before_the_real_report_layer(db):
    """SEC-09：非法参数在报表层之前被挡（422/400），合法参数走真报表层出 CSV。"""
    from api import export_api

    _run(_seed_lines(db, [{"dish_name": "虾饺", "order_time": _ts(2026, 5, 2, 12, 0)}]))
    client = _export_client(db)

    malformed = client.get(
        "/api/export/sales-report.csv",
        params={"start_date": "不是日期", "end_date": BIZ_DATE},
    )
    assert malformed.status_code == 422, malformed.text
    assert "start_date" in malformed.text

    impossible = client.get(
        "/api/export/sales-report.csv",
        params={"start_date": "2026-02-30", "end_date": BIZ_DATE},
    )
    assert impossible.status_code == 400, impossible.text
    assert "start_date" in impossible.text

    # 合法参数：直接调路由函数——TestClient 会另起事件循环，而 asyncpg 连接不能跨循环。
    async def _collect():
        response = await export_api.export_sales_report_csv(
            start_date=BIZ_DATE, end_date=BIZ_DATE, station="steamer", db=db
        )
        chunks = [chunk async for chunk in response.body_iterator]
        text = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in chunks
        )
        return response, text

    response, body = _run(_collect())
    assert response.status_code == 200
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="sales_2026-05-02_2026-05-02.csv"'
    ), response.headers
    assert "类型,名称,档口/岗位" in body
    assert "虾饺" in body

