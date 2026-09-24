#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PERF-15（ticket 18，T3-V2）：档口映射查询的 TTL 缓存回归用例。

`DatabaseManager._get_dish_station_mapping()` 原先每次调用都对 `orders` 全表跑
`GROUP BY dish_name, station`（生产库 20 万行量级，EXPLAIN 是 Parallel Seq Scan，
报告实测 166ms），而它挂在 `/api/semi-rules/dishes/grouped` 上，面板刷新一次扫一次。
现在带 `DISH_STATION_MAPPING_TTL_SECONDS` 的实例级缓存。

用例不连库：拿一个**计数用的假连接**顶掉 `db._connection`（与验证阶段的一次性探针
同一手法），只数 `cursor.execute` 被调用了几次——缓存被短路时（例如把 TTL 判断写成
`if False:`）第二条用例会立刻变红。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from database import DatabaseManager
from db_core.orders_repo import DISH_STATION_MAPPING_TTL_SECONDS

# (dish_name, station, cnt)：含一条 station 为空的脏数据，钉住"空档口不入映射"。
_MAPPING_ROWS = [
    ("虾饺", "steamer", 30),
    ("虾饺", "点心", 3),      # 同一菜品的第二档口：按频次只留第一条
    ("叉烧包", "steamer", 9),
    ("流沙包", "", 5),        # 空档口：不应出现在映射里
]
_EXPECTED_MAPPING = {"虾饺": "steamer", "叉烧包": "steamer"}


def _run(coro):
    return asyncio.run(coro)


class _CountingCursor:
    def __init__(self, counter):
        self._counter = counter

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, sql, params=None):
        self._counter["sql_calls"] += 1
        self._counter["sql"] = " ".join(str(sql).split())
        if self._counter["fail"]:
            raise RuntimeError("boom: 模拟查询失败")

    async def fetchall(self):
        return list(self._counter["rows"])


class _StubConnection:
    def __init__(self, counter):
        self._counter = counter

    def cursor(self):
        return _CountingCursor(self._counter)


class _StubTable:
    def __init__(self, counter):
        self._counter = counter
        self.conn = _StubConnection(counter)


class _StubPgConnection:
    def __init__(self, counter):
        self._counter = counter

    def table(self, name):
        self._counter["tables"].append(name)
        return _StubTable(self._counter)


@pytest.fixture
def stub_db():
    counter = {
        "sql_calls": 0,
        "sql": "",
        "tables": [],
        "rows": list(_MAPPING_ROWS),
        "fail": False,
    }
    db = DatabaseManager()
    db._connection = _StubPgConnection(counter)
    return db, counter


def test_repeated_calls_within_the_ttl_hit_the_cache(stub_db):
    db, counter = stub_db

    first = _run(db._get_dish_station_mapping())
    assert counter["sql_calls"] == 1
    assert counter["tables"] == ["orders"]
    assert first == _EXPECTED_MAPPING

    second = _run(db._get_dish_station_mapping())
    assert counter["sql_calls"] == 1, "同一 TTL 窗口内的第二次调用不应再打 SQL"
    assert second == first
    assert "GROUP BY dish_name, station" in counter["sql"], counter["sql"]


def test_cache_expiry_runs_the_query_once_more(stub_db):
    db, counter = stub_db
    assert _run(db._get_dish_station_mapping()) == _EXPECTED_MAPPING
    assert counter["sql_calls"] == 1

    db._dish_station_mapping_cache = (
        time.monotonic() - DISH_STATION_MAPPING_TTL_SECONDS - 1,
        {"过期占位": "stale"},
    )
    refreshed = _run(db._get_dish_station_mapping())

    assert counter["sql_calls"] == 2, "TTL 过期后应重新查询一次"
    assert refreshed == _EXPECTED_MAPPING, "过期缓存不得被返回"
    assert "过期占位" not in refreshed


def test_empty_result_is_cached_too(stub_db):
    db, counter = stub_db
    counter["rows"] = []

    assert _run(db._get_dish_station_mapping()) == {}
    assert _run(db._get_dish_station_mapping()) == {}
    assert counter["sql_calls"] == 1, "空结果同样应进缓存（docstring 承诺）"


def test_failed_query_is_not_cached(stub_db):
    db, counter = stub_db
    counter["fail"] = True

    assert _run(db._get_dish_station_mapping()) == {}
    assert counter["sql_calls"] == 1
    assert getattr(db, "_dish_station_mapping_cache", None) is None, "失败不得写缓存"

    counter["fail"] = False
    assert _run(db._get_dish_station_mapping()) == _EXPECTED_MAPPING
    assert counter["sql_calls"] == 2, "上一轮失败后应立刻重试而不是被空映射钉住"
