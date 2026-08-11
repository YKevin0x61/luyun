#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Admin 表数据批量更新 API 测试。"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.admin import router as admin_router
from api.security import verify_admin_token
from config import settings
from database import DatabaseManager, get_db


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def admin_client(tmp_path):
    old_dir = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[verify_admin_token] = lambda: True
    with TestClient(app) as client:
        yield client, db
    _run(db.close())
    app.dependency_overrides.clear()
    settings.DATABASE_DIR = old_dir


def test_batch_update_sets_column_for_selected_rows(admin_client):
    client, db = admin_client
    conn = db.table("report_dishes").conn

    async def seed():
        async with conn.cursor() as cursor:
            for idx in range(3):
                await cursor.execute(
                    """INSERT INTO report_dishes
                       (dish_name, display_order, notes, created_at)
                       VALUES (?, ?, ?, '2026-01-01')""",
                    (f"批量更新菜{idx}", idx, f"旧备注{idx}"),
                )
        await db.table("report_dishes").commit()

    _run(seed())

    rows = client.get("/api/admin/tables/report_dishes/rows?page=1&page_size=10").json()["rows"]
    target = [row for row in rows if str(row.get("dish_name", "")).startswith("批量更新菜")]
    assert len(target) >= 2
    target_ids = [target[0]["rowid"], target[1]["rowid"]]

    resp = client.post(
        "/api/admin/tables/report_dishes/rows/batch-update",
        json={"row_ids": target_ids, "column": "notes", "value": "新备注"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["affected"] == 2

    updated = client.get("/api/admin/tables/report_dishes/rows?page=1&page_size=10").json()["rows"]
    by_id = {row["rowid"]: row for row in updated}
    for rid in target_ids:
        assert by_id[rid]["notes"] == "新备注"


def test_batch_update_rejects_primary_key(admin_client):
    client, _ = admin_client
    resp = client.post(
        "/api/admin/tables/report_dishes/rows/batch-update",
        json={"row_ids": [1], "column": "id", "value": 999},
    )
    assert resp.status_code == 403


def test_batch_update_rejects_unknown_column(admin_client):
    client, _ = admin_client
    resp = client.post(
        "/api/admin/tables/report_dishes/rows/batch-update",
        json={"row_ids": [1], "column": "not_a_column", "value": "x"},
    )
    assert resp.status_code == 400


def test_dish_stations_admin_writes_are_forbidden(admin_client):
    client, _ = admin_client
    resp = client.post(
        "/api/admin/tables/dish_stations/rows/batch-update",
        json={"row_ids": [1], "column": "station_id", "value": "shulong"},
    )
    assert resp.status_code == 403
    assert "只读" in resp.json()["detail"]
