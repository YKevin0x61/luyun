#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新建备货品 `/api/semi-rules/prep-items`：分类由 kind 派生，不允许两个控件填出矛盾值。"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.semi_rules import router
from api.security import verify_admin_token
from database import DatabaseManager, get_db


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client():
    db = DatabaseManager()
    _run(db.connect())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[verify_admin_token] = lambda: True
    with TestClient(app) as test_client:
        yield test_client, db
    _run(db.close())


async def _read_item(db, name):
    tdb = db.table("prep_items")
    async with tdb.conn.cursor() as cursor:
        await cursor.execute("SELECT item_name, kind, category FROM prep_items WHERE item_name = ?", (name,))
        return await cursor.fetchone()


@pytest.mark.parametrize(
    ("kind", "category"),
    [("made", "半成品"), ("bought", "原料")],
)
def test_category_follows_kind(client, kind, category):
    """弹窗只填「半成品 / 原料」，落库时 category 跟着 kind 走，不会再出现两列互相打架。"""
    test_client, db = client
    name = f"验收品-{kind}"
    resp = test_client.post("/api/semi-rules/prep-items", json={
        "item_name": name, "unit": "份", "position": "", "kind": kind,
    })
    assert resp.status_code == 200, resp.text
    row = _run(_read_item(db, name))
    assert row is not None
    assert row["kind"] == kind
    assert row["category"] == category


def test_old_category_field_is_ignored(client):
    """老前端若还带 category，也不能覆盖由 kind 派生的值。"""
    test_client, db = client
    name = "验收品-旧字段"
    resp = test_client.post("/api/semi-rules/prep-items", json={
        "item_name": name, "unit": "斤", "kind": "bought", "category": "半成品",
    })
    assert resp.status_code == 200, resp.text
    row = _run(_read_item(db, name))
    assert row["kind"] == "bought"
    assert row["category"] == "原料"
