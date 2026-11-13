#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""退菜明细（refund_records，迁移 0056）的落库、查询与同步解析。

三块：
1. repo 的「整单替换」语义（重复同步不叠行、POS 撤单后旧行消失）；
2. 分页与汇总（汇总按账单算单数、按明细算行数）；
3. tracker 解析 cy7mm 的 ``children`` JSON 字符串与 coStatus 分类。
"""

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.analytics import router as analytics_router
from api.security import verify_admin_token
from config import settings
from database import DatabaseManager, get_db
from scraper.refund_tracker import (
    RefundTracker,
    _combine_datetime,
    _parse_children,
)

BS_ID = 987654321
BS_CODE = "YY001301-261113-0162"
REFUND_DATE = "2026-11-13"


def _run(coro):
    return asyncio.run(coro)


def _row(line, *, item="桐乡胎菊", qty=2.0, money=12.0, co_status=1):
    return {
        "bs_code": BS_CODE,
        "source_line": line,
        "point_name": "加3",
        "item_name": item,
        "qty": qty,
        "money": money,
        "reason": "客人误点/多点[退菜]",
        "operator": "赵文浩",
        "auth_emp": "",
        "refund_date": REFUND_DATE,
        "refund_time": f"2026-11-13T19:08:4{line}+08:00",
        "co_status": co_status,
        "settle_time": "11-13 19:08:54",
        "open_time": "11-13 16:52:51",
        "settle_man": "收银",
        "synced_at": "2026-11-13T19:20:00+08:00",
    }


@pytest.fixture
def db(tmp_path):
    old_dir = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    manager = DatabaseManager()
    _run(manager.connect())
    yield manager
    _run(manager.close())
    settings.DATABASE_DIR = old_dir


def test_replace_bill_is_idempotent(db):
    rows = [_row(0), _row(1)]
    assert _run(db.refund_records_replace_bill(shop_id="164876", bs_id=BS_ID, rows=rows)) == 2
    # 同一账单再同步一遍：结果仍是 2 行，不是 4 行。
    _run(db.refund_records_replace_bill(shop_id="164876", bs_id=BS_ID, rows=rows))
    detail = _run(db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE))
    assert detail["total"] == 2


def test_replace_bill_drops_removed_lines(db):
    _run(db.refund_records_replace_bill(shop_id="164876", bs_id=BS_ID, rows=[_row(0), _row(1)]))
    # POS 撤销了一行：只传剩下的那条，旧的两行都要消失。
    _run(db.refund_records_replace_bill(shop_id="164876", bs_id=BS_ID, rows=[_row(0)]))
    detail = _run(db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE))
    assert detail["total"] == 1
    assert detail["items"][0]["item_name"] == "桐乡胎菊"


def test_replace_bill_with_no_rows_clears_bill(db):
    _run(db.refund_records_replace_bill(shop_id="164876", bs_id=BS_ID, rows=[_row(0)]))
    _run(db.refund_records_replace_bill(shop_id="164876", bs_id=BS_ID, rows=[]))
    detail = _run(db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE))
    assert detail["total"] == 0


def test_detail_pagination_and_order(db):
    rows = [_row(0, item="A"), _row(1, item="B"), _row(2, item="C")]
    _run(db.refund_records_replace_bill(shop_id="164876", bs_id=BS_ID, rows=rows))

    first = _run(
        db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE, page=1, page_size=2)
    )
    assert first["total"] == 3
    assert [item["item_name"] for item in first["items"]] == ["C", "B"]  # 时刻倒序

    second = _run(
        db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE, page=2, page_size=2)
    )
    assert [item["item_name"] for item in second["items"]] == ["A"]


def test_summary_counts_bills_separately_from_lines(db):
    _run(db.refund_records_replace_bill(shop_id="164876", bs_id=BS_ID, rows=[_row(0), _row(1)]))
    _run(
        db.refund_records_replace_bill(
            shop_id="164876", bs_id=BS_ID + 1, rows=[_row(0, item="另一道")]
        )
    )
    summary = _run(db.refund_records_summary(start_date=REFUND_DATE, end_date=REFUND_DATE))
    assert summary["bill_count"] == 2
    assert summary["line_count"] == 3
    assert summary["total_qty"] == 6.0
    assert summary["total_money"] == 36.0


def _add_mapping(db, dish_name, station_id):
    """写一条菜品档口映射，供「楼面菜品被排除」的用例用。"""
    tdb = db.table("dish_stations")
    now = "2026-11-13T00:00:00+08:00"

    async def run():
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """INSERT INTO dish_stations (dish_name, station_id, notes, created_at, updated_at)
                   VALUES (?, ?, NULL, ?, ?)""",
                (dish_name, station_id, now, now),
            )
        await tdb.commit()

    _run(run())


def test_loumian_dishes_are_excluded_from_detail(db):
    _add_mapping(db, "桐乡胎菊", "loumian")
    _add_mapping(db, "陈年普洱", "shulong")
    _run(
        db.refund_records_replace_bill(
            shop_id="164876",
            bs_id=BS_ID,
            rows=[
                _row(0, item="(普通)桐乡胎菊"),  # 楼面：不进明细
                _row(1, item="(普通)陈年普洱"),  # 熟笼：保留
            ],
        )
    )
    detail = _run(db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE))
    assert detail["total"] == 1
    assert [item["item_name"] for item in detail["items"]] == ["(普通)陈年普洱"]


def test_loumian_dishes_are_excluded_from_summary(db):
    _add_mapping(db, "桐乡胎菊", "loumian")
    _run(
        db.refund_records_replace_bill(
            shop_id="164876",
            bs_id=BS_ID,
            rows=[_row(0, item="(普通)桐乡胎菊", qty=2.0, money=12.0)],
        )
    )
    summary = _run(db.refund_records_summary(start_date=REFUND_DATE, end_date=REFUND_DATE))
    assert summary == {"bill_count": 0, "line_count": 0, "total_qty": 0.0, "total_money": 0.0}


def test_children_string_is_parsed():
    raw = json.dumps(
        [{"itemName": "(普通)桐乡胎菊", "qty": 2.0, "time": "19:08:45"}], ensure_ascii=False
    )
    assert _parse_children(raw)[0]["itemName"] == "(普通)桐乡胎菊"
    assert _parse_children("not-json") == []
    assert _parse_children(None) == []
    assert _parse_children([{"itemName": "x"}, "junk"]) == [{"itemName": "x"}]


def test_combine_datetime_pads_hour():
    assert _combine_datetime("2026-11-13", "9:08:45") == "2026-11-13T09:08:45+08:00"
    assert _combine_datetime("2026-11-13", "") == "2026-11-13T00:00:00+08:00"


# ---- tracker 与 cy7mm 接口的对接 ----


class _FakeResponse:
    def __init__(self, body, status=200):
        self.status = status
        self._body = body

    async def json(self):
        return self._body


class _FakeHttp:
    """按 URL / currentPage / coStatus 回假数据，记录每次请求体。"""

    def __init__(self, bill, *, status_bill=None, always_fail=False, present_bill=None):
        self.bill = bill
        self.status_bill = status_bill
        self.always_fail = always_fail
        self.present_bill = present_bill
        self.calls = []
        self.urls = []

    async def build_headers(self, **kwargs):
        return {}

    async def post_form(self, url, *, headers, encoded_data, context_label):
        payload = json.loads(parse_qs(encoded_data)["data"][0])
        self.calls.append(payload)
        self.urls.append(url)
        if self.always_fail:
            return _FakeResponse("<script>alert('系统异常！');</script>", status=500)
        if "getexceptionpresent" in url:
            if payload["currentPage"] > 1 or self.present_bill is None:
                return _FakeResponse(
                    {"success": True, "data": {"dataList": [], "dataSize": 0}}
                )
            return _FakeResponse(
                {"success": True, "data": {"dataList": [self.present_bill], "dataSize": 1}}
            )
        if payload["currentPage"] > 1:
            return _FakeResponse({"success": True, "data": {"dataList": [], "dataSize": 1}})
        co_status = payload.get("coStatus") or ""
        if co_status and self.status_bill is not None:
            return _FakeResponse(
                {"success": True, "data": {"dataList": [self.status_bill], "dataSize": 1}}
            )
        if co_status:
            return _FakeResponse({"success": True, "data": {"dataList": [], "dataSize": 0}})
        return _FakeResponse({"success": True, "data": {"dataList": [self.bill], "dataSize": 1}})


def _bill(children, *, bs_id=BS_ID, bs_code=BS_CODE):
    return {
        "bsId": bs_id,
        "bsCode": bs_code,
        "pointName": "加3",
        "settleTime": "11-13 19:08:54",
        "openTime": "11-13 16:52:51",
        "settleMan": "收银",
        "children": json.dumps(children, ensure_ascii=False),
    }


def _session(http):
    return SimpleNamespace(http=http, _creds=SimpleNamespace(shop_id="164876"))


def test_sync_writes_children_and_never_duplicates(db):
    bill = _bill(
        [
            {"itemName": "(普通)桐乡胎菊", "qty": 2.0, "money": 12.0, "reason": "客人误点/多点[退菜]", "operator": "赵文浩", "time": "19:08:45"},
            {"itemName": "(普通)桐乡胎菊", "qty": 2.0, "money": 12.0, "reason": "客人误点/多点[退菜]", "operator": "赵文浩", "time": "19:08:45"},
        ]
    )
    http = _FakeHttp(bill)
    tracker = RefundTracker(_session(http))
    written = _run(tracker.sync(db))
    assert written == 14  # 7 天各 1 单 × 每条 2 行；同单每天整单替换
    detail = _run(db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE))

    # 同一时刻同菜品的两条真实重复行都要留下 —— 靠 source_line 而不是字段组合去重。
    assert detail["total"] == 2
    assert detail["items"][0]["refund_time"] == "2026-11-13T19:08:45+08:00"

    # 再来一轮（强制关掉节流与 backfill 标记）：行数不变。
    tracker._last_sync_at = None
    tracker._backfilled = True
    _run(tracker.sync(db))
    assert _run(db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE))["total"] == 2


def test_sync_stores_present_as_separate_kind(db):
    """同一张账单既退菜又赠菜：kind 参与唯一键，两类各自留行，互不覆盖。"""
    co_bill = _bill([{"itemName": "鲈鱼", "qty": 1.0, "money": 68.0, "time": "20:01:02"}])
    present_bill = _bill(
        [{"itemName": "香煎手工韭菜饺(1只)", "qty": 2.0, "money": 13.6, "reason": "营销活动", "time": "09:13:22"}]
    )
    tracker = RefundTracker(_session(_FakeHttp(co_bill, present_bill=present_bill)))
    _run(tracker.sync(db))

    co = _run(db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE, kind="co"))
    present = _run(
        db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE, kind="present")
    )
    assert [row["item_name"] for row in co["items"]] == ["鲈鱼"]
    assert [row["item_name"] for row in present["items"]] == ["香煎手工韭菜饺(1只)"]
    assert present["items"][0]["kind"] == "present"
    # 赠单没有阶段概念。
    assert present["items"][0]["co_status"] == 0
    # 不筛 kind 时两类都在。
    assert _run(db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE))["total"] == 2
    # 两类请求各打到自己的接口。
    assert any("getexceptionpresent" in url for url in tracker._session.http.urls)


def test_present_without_bs_id_falls_back_to_bs_code(db):
    """赠单接口只有第一张账单带 bsId，其余靠单号里的数字兜底，不能整批丢掉。"""
    bill = _bill([{"itemName": "手磨新疆红枣糕", "qty": 1.0, "money": 18.0, "time": "11:21:03"}])
    bill.pop("bsId")
    tracker = RefundTracker(_session(_FakeHttp(bill, present_bill=bill)))
    _run(tracker.sync(db))
    present = _run(
        db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE, kind="present")
    )
    assert present["total"] == 1
    assert present["items"][0]["item_name"] == "手磨新疆红枣糕"


def test_sync_marks_pass_then_returned_status(db):
    bill = _bill([{"itemName": "鲈鱼", "qty": 1.0, "money": 68.0, "time": "20:01:02"}])
    status_bill = _bill([{"itemName": "鲈鱼", "qty": 1.0, "money": 68.0, "time": "20:01:02"}])
    tracker = RefundTracker(_session(_FakeHttp(bill, status_bill=status_bill)))
    _run(tracker.sync(db))
    detail = _run(db.refund_records_detail(start_date=REFUND_DATE, end_date=REFUND_DATE))
    # coStatus 只查最近两天，且 2/3/4 依次覆盖：最后一次命中是 4=返位后退单。
    assert detail["items"][0]["co_status"] == 4


def test_sync_keeps_backfill_open_when_api_fails(db):
    tracker = RefundTracker(_session(_FakeHttp(None, always_fail=True)))
    written = _run(tracker.sync(db))
    assert written == 0
    assert tracker._backfilled is False


def test_sync_skips_without_http(db):
    tracker = RefundTracker(SimpleNamespace(http=None, _creds=None))
    assert _run(tracker.sync(db)) == 0


# ---- API ----


@pytest.fixture
def api_client(db):
    app = FastAPI()
    app.include_router(analytics_router, dependencies=[Depends(verify_admin_token)])
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[verify_admin_token] = lambda: True
    with TestClient(app) as client:
        yield client, db
    app.dependency_overrides.clear()


def test_refund_detail_endpoint(api_client):
    client, db = api_client
    _run(
        db.refund_records_replace_bill(
            shop_id="164876", bs_id=BS_ID, rows=[_row(0, item="A"), _row(1, item="B")]
        )
    )
    response = client.get(
        "/api/analytics/refunds/detail",
        params={"start_date": REFUND_DATE, "end_date": REFUND_DATE, "page": 1, "page_size": 1},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["total"] == 2
    assert len(payload["items"]) == 1
    assert payload["summary"]["co"]["bill_count"] == 1
    assert payload["summary"]["co"]["line_count"] == 2
    assert payload["summary"]["present"]["line_count"] == 0
    assert payload["items"][0]["money"] == 12.0


def test_refund_detail_endpoint_filters_by_kind(api_client):
    client, db = api_client
    _run(
        db.refund_records_replace_bill(
            shop_id="164876", bs_id=BS_ID, rows=[_row(0, item="退掉的菜")], kind="co"
        )
    )
    _run(
        db.refund_records_replace_bill(
            shop_id="164876", bs_id=BS_ID, rows=[_row(0, item="送出去的菜")], kind="present"
        )
    )
    all_rows = client.get(
        "/api/analytics/refunds/detail",
        params={"start_date": REFUND_DATE, "end_date": REFUND_DATE},
    ).json()
    assert all_rows["total"] == 2

    present = client.get(
        "/api/analytics/refunds/detail",
        params={"start_date": REFUND_DATE, "end_date": REFUND_DATE, "kind": "present"},
    ).json()
    assert present["kind"] == "present"
    assert [row["item_name"] for row in present["items"]] == ["送出去的菜"]
    assert present["summary"]["present"]["line_count"] == 1
    assert present["summary"]["co"]["line_count"] == 1

    bad = client.get(
        "/api/analytics/refunds/detail",
        params={"start_date": REFUND_DATE, "end_date": REFUND_DATE, "kind": "xyz"},
    )
    assert bad.status_code == 400


def test_refund_detail_endpoint_rejects_bad_page(api_client):
    client, _ = api_client
    response = client.get("/api/analytics/refunds/detail", params={"page": 0})
    assert response.status_code == 422
