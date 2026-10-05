#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据管理页的按列搜索：数值列**不能**走 LIKE。

回归的是 2026-10-05 UI 审查的 A1：`/api/admin/tables/{t}/rows` 对任何列都拼
`{col} LIKE ?`，而 PostgreSQL 对 `bigint LIKE '%1%'` 直接报
`operator does not exist: bigint ~~ unknown` —— 26 个可选搜索字段里 9 个是数值列，
搜一次就报错，而且那条英文原文会被前端渲染进表格区给店长看。

改法：按 `information_schema` 里的列类型选操作符（数值列等值、文本列 LIKE），
数值列填了非数字给中文 400。
"""

from contextlib import asynccontextmanager, contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.admin import router as admin_router
from api.security import verify_admin_token
from config import settings
from database import DatabaseManager, get_db


def _make_app(seed=None) -> FastAPI:
    """连接、种子与请求都跑在 TestClient 的同一个事件循环里（asyncpg 连接与 loop 绑定）。"""

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


async def _seed(db):
    """report_dishes：文本列 dish_name + 数值列 display_order（bigint）；
    tables：带一个 double precision 列 amount，用来覆盖小数那条边界。"""
    conn = db._conn
    await conn.execute(
        """INSERT INTO report_dishes (tenant_id, id, dish_name, display_order, notes, created_at)
           VALUES (1, 101, '艇仔粥', 3, '', '2026-10-05 08:30:00')""",
    )
    await conn.execute(
        """INSERT INTO report_dishes (tenant_id, id, dish_name, display_order, notes, created_at)
           VALUES (1, 102, '肠粉', 1, '', '2026-10-05 08:40:00')""",
    )
    await conn.execute(
        """INSERT INTO "tables" (tenant_id, id, table_number, amount, people, duration, status, updated_at)
           VALUES (1, 201, 'A1', 12.5, 2, 30, 'open', '2026-10-05 08:30:00')""",
    )
    await conn.commit()


@pytest.fixture
def admin_client(tmp_path):
    with _client(tmp_path, seed=_seed) as (client, db):
        yield client, db


def _rows(client, table, query):
    return client.get(f"/api/admin/tables/{table}/rows?page=1&page_size=50&{query}")


def test_text_column_still_uses_like(admin_client):
    """主路径：文本列照旧模糊匹配。"""
    client, _ = admin_client
    resp = _rows(client, "report_dishes", "search_field=dish_name&search_value=粥")
    assert resp.status_code == 200, resp.text
    assert [r["dish_name"] for r in resp.json()["rows"]] == ["艇仔粥"]


def test_numeric_column_searches_by_equality(admin_client):
    """主路径：数值列按等值搜，不再抛 PG 的 operator does not exist。"""
    client, _ = admin_client
    resp = _rows(client, "report_dishes", "search_field=display_order&search_value=3")
    assert resp.status_code == 200, resp.text
    assert [r["dish_name"] for r in resp.json()["rows"]] == ["艇仔粥"]

    # 边界：等值不是模糊 —— 搜 1 只命中 display_order=1 那行，不把 3 捞进来
    resp = _rows(client, "report_dishes", "search_field=display_order&search_value=1")
    assert [r["dish_name"] for r in resp.json()["rows"]] == ["肠粉"]


def test_numeric_column_rejects_non_number_with_chinese_hint(admin_client):
    """边界：数值列填了非数字 → 中文 400，而不是把英文 DB 原文透传给店长。"""
    client, _ = admin_client
    resp = _rows(client, "report_dishes", "search_field=display_order&search_value=abc")
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "数值列" in detail
    assert "operator does not exist" not in detail


def test_decimal_column_accepts_decimal(admin_client):
    """边界：double precision 列可以按小数搜。"""
    client, _ = admin_client
    resp = _rows(client, "tables", "search_field=amount&search_value=12.5")
    assert resp.status_code == 200, resp.text
    assert [r["table_number"] for r in resp.json()["rows"]] == ["A1"]


def test_unknown_column_is_rejected(admin_client):
    """边界：字段名不存在时给中文 400，而不是让 SQL 去报错。"""
    client, _ = admin_client
    resp = _rows(client, "report_dishes", "search_field=not_a_column&search_value=1")
    assert resp.status_code == 400, resp.text
    assert "不存在" in resp.json()["detail"]
