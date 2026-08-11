#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Admin 表数据批量删除 API 测试。"""

import asyncio
import unittest

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


def test_batch_delete_removes_rows(admin_client):
    client, db = admin_client
    conn = db.table("report_dishes").conn

    async def seed():
        async with conn.cursor() as cursor:
            for idx in range(3):
                await cursor.execute(
                    """INSERT INTO report_dishes
                       (dish_name, display_order, notes, created_at)
                       VALUES (?, ?, '', '2026-01-01')""",
                    (f"批量测试菜{idx}", idx),
                )
        await db.table("report_dishes").commit()

    _run(seed())

    list_resp = client.get("/api/admin/tables/report_dishes/rows?page=1&page_size=10")
    assert list_resp.status_code == 200
    rows = list_resp.json()["rows"]
    assert len(rows) >= 3
    target_ids = [rows[0]["rowid"], rows[1]["rowid"]]

    del_resp = client.post(
        "/api/admin/tables/report_dishes/rows/batch-delete",
        json={"row_ids": target_ids},
    )
    assert del_resp.status_code == 200
    body = del_resp.json()
    assert body["success"] is True
    assert body["affected"] == 2

    list_after = client.get("/api/admin/tables/report_dishes/rows?page=1&page_size=10").json()
    remaining_ids = {row["rowid"] for row in list_after["rows"]}
    assert not (set(target_ids) & remaining_ids)


def test_batch_delete_rejects_empty_ids(admin_client):
    client, _ = admin_client
    resp = client.post(
        "/api/admin/tables/report_dishes/rows/batch-delete",
        json={"row_ids": []},
    )
    assert resp.status_code == 400


def test_dish_stations_admin_delete_forbidden(admin_client):
    client, _ = admin_client
    resp = client.post(
        "/api/admin/tables/dish_stations/rows/batch-delete",
        json={"row_ids": [1]},
    )
    assert resp.status_code == 403


if __name__ == "__main__":
    unittest.main()
