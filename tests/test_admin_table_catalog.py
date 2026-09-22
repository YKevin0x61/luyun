#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Data-management catalog covers every physical table read-only where needed."""

from contextlib import asynccontextmanager, contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.admin import router as admin_router
from api.security import verify_admin_token
from config import settings
from database import DatabaseManager, get_db


def _make_app(seed=None) -> FastAPI:
    """连接、种子与请求都跑在 TestClient 的同一个事件循环里。

    ``TestClient`` 在独立线程的 loop 里执行应用，而 asyncpg 的连接与建它的 loop
    绑定：用例里另外 ``asyncio.run(db.connect())`` 再发请求会报
    「attached to a different loop」。所以连接放进 lifespan（随 ``with TestClient``
    在 portal loop 里启动），需要写种子的用例把协程交给 ``seed`` 一起进去。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = DatabaseManager()
        assert await db.connect()
        app.state.db = db
        try:
            if seed is not None:
                await seed(db)
            yield
        finally:
            await db.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(admin_router)
    app.dependency_overrides[get_db] = lambda: app.state.db
    app.dependency_overrides[verify_admin_token] = lambda: True
    return app


@contextmanager
def _client(tmp_path, seed=None):
    old_dir = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    app = _make_app(seed)
    try:
        with TestClient(app) as client:
            yield client, app.state.db
    finally:
        app.dependency_overrides.clear()
        settings.DATABASE_DIR = old_dir


@pytest.fixture
def admin_client(tmp_path):
    with _client(tmp_path) as (client, db):
        yield client, db


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


def test_read_only_physical_tables_can_be_browsed(tmp_path):
    async def seed(db):
        await db._conn.execute(
            """INSERT INTO hygiene_zones
               (name, day_shift, night_shift, created_at, updated_at)
               VALUES ('后厨', 1, 1, '2026-01-01', '2026-01-01')"""
        )
        await db._conn.commit()

    with _client(tmp_path, seed) as (client, _db):
        schema = client.get("/api/admin/tables/hygiene_zones/schema")
        assert schema.status_code == 200
        assert schema.json()["read_only"] is True
        # 表结构来自 information_schema：列名与类型都要如实带出来
        columns = {col["name"]: col for col in schema.json()["columns"]}
        assert columns["name"]["type"] == "text"
        assert columns["id"]["pk"] is True

        rows = client.get("/api/admin/tables/hygiene_zones/rows?page=1&page_size=10")
        assert rows.status_code == 200
        assert rows.json()["read_only"] is True
        assert rows.json()["rows"][0]["name"] == "后厨"


def test_auth_secret_columns_are_redacted(tmp_path):
    async def seed(db):
        await db._conn.execute(
            """INSERT INTO admin_user
               (id, username, password_hash, created_at, updated_at)
               VALUES (1, 'admin', 'secret-hash', '2026-01-01', '2026-01-01')"""
        )
        await db._conn.commit()

    with _client(tmp_path, seed) as (client, _db):
        rows = client.get("/api/admin/tables/admin_user/rows?page=1&page_size=10")
        assert rows.status_code == 200
        assert rows.json()["rows"][0]["password_hash"] == "已隐藏"

        blocked = client.get(
            "/api/admin/tables/admin_user/rows",
            params={"search_field": "password_hash", "search_value": "secret"},
        )
        assert blocked.status_code == 403


def test_add_and_drop_column_use_pg_ddl(tmp_path):
    """加列 / 删列走 PG 的 ALTER TABLE，不再有重建表那一步。"""
    with _client(tmp_path) as (client, _db):
        added = client.post(
            "/api/admin/tables/tables/columns",
            json={"column_name": "remark", "column_type": "TEXT"},
        )
        assert added.status_code == 200, added.text

        schema = client.get("/api/admin/tables/tables/schema").json()["columns"]
        assert any(col["name"] == "remark" for col in schema)

        dropped = client.delete("/api/admin/tables/tables/columns/remark")
        assert dropped.status_code == 200, dropped.text

        schema = client.get("/api/admin/tables/tables/schema").json()["columns"]
        assert not any(col["name"] == "remark" for col in schema)


def test_add_column_rejects_non_numeric_default(tmp_path):
    with _client(tmp_path) as (client, _db):
        response = client.post(
            "/api/admin/tables/tables/columns",
            json={
                "column_name": "seats",
                "column_type": "INTEGER",
                "default_value": "1; DROP TABLE orders",
            },
        )
        assert response.status_code == 400
        assert "数字" in response.json()["detail"]


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
