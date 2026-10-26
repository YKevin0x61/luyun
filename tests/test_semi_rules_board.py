#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""换算规则板 `/api/semi-rules/board`：分组、覆盖率、楼面排除、待配清单。"""

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.semi_rules import router
from api.security import verify_admin_token
from database import CHINA_TZ, DatabaseManager, get_db
from services.dish_catalog import get_dish_catalog


class _FakeCatalog:
    def __init__(self, mapping=None):
        self._mapping = mapping or {}

    async def as_dict(self):
        return self._mapping


def _run(coro):
    return asyncio.run(coro)


def _now_iso(days_ago=1):
    return (datetime.now(CHINA_TZ) - timedelta(days=days_ago)).isoformat()


async def _insert_order(db, dish_name, qty, when=None):
    when = when or _now_iso()
    tdb = db.table("orders")
    async with tdb.conn.cursor() as cursor:
        await cursor.execute(
            "INSERT INTO orders (table_number, dish_name, quantity, order_time,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("A1", dish_name, qty, when, when, when),
        )
    await tdb.commit()


@pytest.fixture
def board_client():
    db = DatabaseManager()
    _run(db.connect())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[verify_admin_token] = lambda: True
    app.dependency_overrides[get_dish_catalog] = lambda: _FakeCatalog({"茶饮": "loumian"})
    with TestClient(app) as client:
        yield client, db
    _run(db.close())


def test_board_groups_takeout_variants(board_client):
    """同菜的堂食 + 外卖两行折成一组，销量相加，主名取堂食名。"""
    client, db = board_client
    _run(db.semi_rules_upsert({
        "dish_name": "金莎脆皮红米肠", "semi_name": "金莎",
        "factor": 1.5, "unit": "条", "category": "半成品", "position": "熟笼",
    }))
    _run(db.semi_rules_upsert({
        "dish_name": "(外卖)金莎脆皮红米肠", "semi_name": "金莎",
        "factor": 1.5, "unit": "条", "category": "半成品", "position": "熟笼",
    }))
    _run(_insert_order(db, "金莎脆皮红米肠", 10))
    _run(_insert_order(db, "(外卖)金莎脆皮红米肠", 5))

    data = client.get("/api/semi-rules/board").json()
    assert data["success"] is True
    cov = data["coverage"]
    assert cov["total_rules"] == 2
    assert cov["total_groups"] == 1
    assert cov["active_groups"] == 1
    assert cov["covered_groups"] == 1
    assert cov["pending_groups"] == 0
    assert cov["qty_covered_pct"] == 100.0

    group = data["groups"][0]
    assert group["main_name"] == "金莎脆皮红米肠"
    assert group["has_rules"] is True
    assert group["qty"] == 15
    assert len(group["variants"]) == 2
    assert {v["dish_name"] for v in group["variants"]} == {
        "金莎脆皮红米肠", "(外卖)金莎脆皮红米肠",
    }


def test_board_excludes_loumian_from_coverage(board_client):
    """楼面档（茶饮/耗材）不需要配方系数，不进覆盖率分母。"""
    client, db = board_client
    _run(db.semi_rules_upsert({
        "dish_name": "金莎脆皮红米肠", "semi_name": "金莎",
        "factor": 1.5, "unit": "条",
    }))
    _run(_insert_order(db, "金莎脆皮红米肠", 10))
    _run(_insert_order(db, "茶饮", 999))

    cov = client.get("/api/semi-rules/board").json()["coverage"]
    assert cov["active_groups"] == 1
    assert cov["covered_groups"] == 1
    assert cov["qty_covered_pct"] == 100.0


def test_board_lists_pending_groups_without_rules(board_client):
    """在卖但没规则 → 进待配清单，并带销量，前端按销量排序。"""
    client, db = board_client
    _run(_insert_order(db, "荔湾鲜虾艇仔粥", 42))
    _run(_insert_order(db, "(外卖)荔湾鲜虾艇仔粥", 8))

    data = client.get("/api/semi-rules/board").json()
    assert data["coverage"]["pending_groups"] == 1
    assert data["coverage"]["covered_groups"] == 0
    group = data["groups"][0]
    assert group["has_rules"] is False
    assert group["main_name"] == "荔湾鲜虾艇仔粥"
    assert group["qty"] == 50


async def _seed_unit_drift(db):
    when = _now_iso()
    tdb = db.table("prep_items")
    async with tdb.conn.cursor() as cursor:
        await cursor.execute(
            "INSERT INTO prep_items (item_name, unit, position, kind, active,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("虾饺", "个", "案板", "made", 1, when, when),
        )
    await tdb.commit()
    await db.semi_rules_upsert({
        "dish_name": "金牌禄运虾饺皇", "semi_name": "虾饺",
        "factor": 1, "unit": "份",
    })
    # 手工把 prep_item_id 接上（回填脚本的效果）
    tdb_rules = db.table("semi_finished_rules")
    async with tdb_rules.conn.cursor() as cursor:
        await cursor.execute(
            "UPDATE semi_finished_rules SET prep_item_id ="
            " (SELECT id FROM prep_items WHERE item_name = '虾饺')"
        )
    await tdb_rules.commit()


def test_board_flags_unit_drift(board_client):
    """规则写「份」、备货品主数据是「个」：标 unit_drift，不自动改数。"""
    client, db = board_client
    _run(_seed_unit_drift(db))

    variant = client.get("/api/semi-rules/board").json()["groups"][0]["variants"][0]
    assert variant["prep_item_name"] == "虾饺"
    assert variant["unit_drift"] is True
    assert variant["unit"] == "份"
