#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Data-management catalog covers every physical app.db table read-only where needed."""

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


def test_catalog_includes_physical_tables_and_groups(admin_client):
    client, _ = admin_client
    body = client.get("/api/admin/tables").json()

    assert body["success"] is True
    assert "auth" not in body["tables"]
    assert "orders" in body["tables"]
    assert "admin_user" in body["tables"]
    assert "sop_recipes" in body["tables"]
    assert "hygiene_zones" in body["tables"]
    assert "logs" in body["tables"]

    meta = body["table_meta"]
    assert meta["orders"]["read_only"] is False
    assert meta["dish_stations"]["read_only"] is True
    assert meta["sop_recipes"]["read_only"] is True
    assert meta["hygiene_zones"]["read_only"] is True
    assert meta["logs"]["route"] == "/logs"
    assert meta["admin_user"]["redacted_columns"] == ["password_hash"]

    group_keys = [group["key"] for group in body["groups"]]
    assert group_keys == ["business", "recipe", "hygiene", "auth", "external"]


def test_read_only_physical_tables_can_be_browsed(admin_client):
    client, db = admin_client

    async def seed():
        await db._conn.execute(
            """INSERT INTO hygiene_zones
               (name, day_shift, night_shift, created_at, updated_at)
               VALUES ('后厨', 1, 1, '2026-01-01', '2026-01-01')"""
        )
        await db._conn.commit()

    _run(seed())

    schema = client.get("/api/admin/tables/hygiene_zones/schema")
    assert schema.status_code == 200
    assert schema.json()["read_only"] is True
    assert any(col["name"] == "name" for col in schema.json()["columns"])

    rows = client.get("/api/admin/tables/hygiene_zones/rows?page=1&page_size=10")
    assert rows.status_code == 200
    assert rows.json()["read_only"] is True
    assert rows.json()["rows"][0]["name"] == "后厨"


def test_auth_secret_columns_are_redacted(admin_client):
    client, db = admin_client

    async def seed():
        await db._conn.execute(
            """INSERT INTO admin_user
               (id, username, password_hash, created_at, updated_at)
               VALUES (1, 'admin', 'secret-hash', '2026-01-01', '2026-01-01')"""
        )
        await db._conn.commit()

    _run(seed())

    rows = client.get("/api/admin/tables/admin_user/rows?page=1&page_size=10")
    assert rows.status_code == 200
    assert rows.json()["rows"][0]["password_hash"] == "已隐藏"

    blocked = client.get(
        "/api/admin/tables/admin_user/rows",
        params={"search_field": "password_hash", "search_value": "secret"},
    )
    assert blocked.status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/admin/tables/hygiene_zones/rows", {"values": {"name": "x"}}),
        ("put", "/api/admin/tables/sop_recipes/rows/1", {"values": {"recipe_name": "x"}}),
        ("delete", "/api/admin/tables/admin_user/rows/1", None),
        ("post", "/api/admin/tables/hygiene_zones/columns", {"column_name": "x", "column_type": "TEXT"}),
    ],
)
def test_read_only_table_writes_are_rejected(admin_client, method, path, payload):
    client, _ = admin_client
    request = getattr(client, method)
    response = request(path, json=payload) if payload is not None else request(path)
    assert response.status_code == 403


def test_logs_external_entry_is_not_a_generic_table(admin_client):
    client, _ = admin_client
    response = client.get("/api/admin/tables/logs/rows")
    assert response.status_code == 400
    assert "/logs" in response.json()["detail"]
