#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Data-management catalog covers every physical table read-only where needed.

另外钉一条中文标签契约：catalog 里的每一张表、每一个列都必须在
``admin-web/src/utils/adminLabels.js`` 里有中文名（见文件末尾的
``test_every_catalog_table_and_column_has_a_chinese_label``）。
"""

from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
import re

import pg_probe
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.admin import router as admin_router
from api.security import verify_admin_token
from config import settings
from database import DatabaseManager, get_db
from db_core.schema import WECOM_SUBSCRIPTION_TABLES


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
    assert "staff_shifts" in body["tables"]
    assert "logs" in body["tables"]

    meta = body["table_meta"]
    assert meta["orders"]["read_only"] is False
    assert meta["dish_stations"]["read_only"] is True
    assert meta["sop_recipes"]["read_only"] is True
    assert meta["hygiene_zones"]["read_only"] is True
    assert meta["logs"]["route"] == "/logs"
    assert meta["admin_user"]["redacted_columns"] == ["password_hash"]

    group_keys = [group["key"] for group in body["groups"]]
    assert group_keys == [
        # 2026-10-26 按业务域重排：原来的「业务数据」把点餐、备货、企微历史表混在
        # 一格，侧边栏滚两屏才找到「企微推送」。
        "dining", "prep", "recipe", "hygiene", "scheduling",
        "wecom",      # 旧任务三张 + 迁移 0016 的订阅 / 出站 / 审计
        "hr",         # 加班与补钟（人事）+ 法定节假日 / 月度考勤台账
        "wastage",
        "config",     # 应用设置 + 登录凭据三张 + 运行日志入口
    ]


def test_table_groups_cover_every_table_exactly_once(admin_client):
    """分组是侧边栏与备份清单的唯一口径：不能漏表，也不能一张表挂两组。"""
    client, _ = admin_client
    body = client.get("/api/admin/tables").json()

    grouped: list[str] = []
    for group in body["groups"]:
        grouped.extend(group["tables"])

    assert len(grouped) == len(set(grouped)), "同一张表出现在多个分组里"
    assert set(grouped) == set(body["tables"]), "分组与 catalog 的表清单不一致"

    labels = {group["key"]: group["label"] for group in body["groups"]}
    assert labels["dining"] == "点餐数据"
    assert labels["prep"] == "备货计划"
    assert labels["hr"] == "人事考勤"
    assert labels["config"] == "系统设置"


def test_prep_and_wecom_tables_live_in_their_own_groups(admin_client):
    """点餐那格只装点餐的表；备货与企微各自成组，不再混进「业务数据」。"""
    client, _ = admin_client
    groups = {
        group["label"]: group["tables"]
        for group in client.get("/api/admin/tables").json()["groups"]
    }

    assert groups["点餐数据"] == [
        "orders", "tables", "stations", "dish_stations",
        "semi_finished_rules", "report_dishes",
    ]
    assert "prep_plan_snapshots" in groups["备货计划"]
    assert "prep_items" in groups["备货计划"]
    assert "wecom_push_jobs" in groups["企微推送"]
    assert "wecom_push_logs" in groups["企微推送"]
    assert "app_settings" in groups["系统设置"]
    assert groups["系统设置"][-1] == "logs"

    for table in ("wecom_push_jobs", "prep_items", "app_settings"):
        assert table not in groups["点餐数据"]


def test_push_subscription_tables_are_browsable_but_read_only(admin_client):
    """企微推送那几张新表只读展示：写入一律走企微推送的 repo 方法。

    迁移 0016 的四张 + 迁移 0019 的配置变更历史（票 11）。审计表进这个名单还多一层
    意思：它是**只追加**的（只写不改、没有保留期），数据管理页更不能给它开通用写入口。
    """
    client, _ = admin_client
    body = client.get("/api/admin/tables").json()
    meta = body["table_meta"]

    for table in WECOM_SUBSCRIPTION_TABLES:
        assert table in body["tables"]
        assert meta[table]["group"] == "wecom"
        assert meta[table]["read_only"] is True

    assert WECOM_SUBSCRIPTION_TABLES == (
        "wecom_push_subscriptions",
        "wecom_channel_groups",
        "wecom_channel_group_members",
        "wecom_push_outbox",
        "wecom_push_audit",
    )

    rejected = client.post(
        "/api/admin/tables/wecom_push_subscriptions/rows",
        json={"values": {"topic_id": "sales_report", "target_channel_id": 1}},
    )
    assert rejected.status_code == 403

    # 变更历史同样只能由这一页的写操作追加，不能从数据管理页手改一行。
    rejected_audit = client.post(
        "/api/admin/tables/wecom_push_audit/rows",
        json={"values": {"action": "update", "object_type": "wecom_push_webhooks"}},
    )
    assert rejected_audit.status_code == 403


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


# ==================== 中文标签覆盖 ====================
# 数据管理页的表名 / 列名只有一个翻译口：`admin-web/src/utils/adminLabels.js`。
# 那里缺一条映射，页面上露出的就是 `staff_shifts` / `shift_id` 这种裸标识符 ——
# 2026-10-26 用户报「数据管理页面，中文化」时，侧边栏整列都是英文。
#
# 为什么由 Python 侧钉：表清单的唯一来源是后端（`db_core/schema.py` 的表常量 +
# `api/admin.py` 的分组），列清单的唯一来源是数据库结构；前端再抄一份清单必然漂。
# 这里只**解析**前端文件里的键、不执行 JS（CI 的 Python job 没有 node）。

_LABELS_JS = (
    Path(__file__).resolve().parents[1] / "admin-web" / "src" / "utils" / "adminLabels.js"
)


def _js_object_body(source: str, name: str) -> str:
    match = re.search(rf"export const {name} = \{{(.*?)\n\}}", source, re.S)
    assert match, f"adminLabels.js 里找不到 {name}"
    return re.sub(r"//[^\n]*", "", match.group(1))


def _frontend_label_keys() -> dict[str, set[str]]:
    source = _LABELS_JS.read_text(encoding="utf-8")
    table_block = _js_object_body(source, "TABLE_LABELS")
    column_block = _js_object_body(source, "COLUMN_LABELS")
    keys = set(re.findall(r"([A-Za-z_][A-Za-z0-9_.]*)['\"]?\s*:", column_block))
    return {
        "tables": set(
            re.findall(r"^\s{2}([A-Za-z_][A-Za-z0-9_]*):", table_block, re.M)
        ),
        "pairs": {key for key in keys if "." in key},
        "columns": {key for key in keys if "." not in key},
    }


def test_every_catalog_table_and_column_has_a_chinese_label(admin_client):
    client, _ = admin_client
    body = client.get("/api/admin/tables").json()
    labels = _frontend_label_keys()

    tables = body["tables"]
    missing_tables = sorted(t for t in tables if t not in labels["tables"])

    missing_columns: list[str] = []
    for table in tables:
        meta = body["table_meta"].get(table, {})
        if meta.get("route"):
            # 点了会跳走的入口（logs）：数据管理页不展示它的列。
            continue
        columns = pg_probe.table_columns(table)
        assert columns, f"catalog 里的 {table} 在测试库里没有列"
        for column in columns:
            if f"{table}.{column}" in labels["pairs"]:
                continue
            if column in labels["columns"] or column.lower() in labels["columns"]:
                continue
            missing_columns.append(f"{table}.{column}")

    assert not missing_tables, (
        "这些表在数据管理页会露出英文表名，请在 adminLabels.js 的 TABLE_LABELS 补中文名："
        + ", ".join(missing_tables)
    )
    assert not missing_columns, (
        "这些列在数据管理页会露出英文列名，请在 adminLabels.js 的 COLUMN_LABELS 补中文名："
        + ", ".join(missing_columns)
    )


# ==================== 未应用迁移时的提示 ====================


class _MissingTableConn:
    """模拟「库里还没建这张表」：asyncpg 抛 UndefinedTableError。"""

    def _raise(self):
        import asyncpg

        raise asyncpg.UndefinedTableError(
            'relation "prep_plan_snapshots" does not exist'
        )

    async def execute(self, *_args, **_kwargs):
        self._raise()

    def cursor(self):
        @asynccontextmanager
        async def _cursor():
            self._raise()
            yield  # pragma: no cover —— 上面的 raise 一定先发生

        return _cursor()


@pytest.mark.parametrize(
    "path",
    [
        "/api/admin/tables/prep_plan_snapshots/schema",
        "/api/admin/tables/prep_plan_snapshots/rows",
    ],
)
def test_unmigrated_table_answers_with_migration_hint(admin_client, monkeypatch, path):
    """迁移没应用时，页面要看到「去数据库迁移」，而不是 asyncpg 的英文原文。

    清单来自代码常量、不查库，所以「列表里有、库里没有」是正常中间态；把
    `relation "x" does not exist` 原样渲染给店长看才是缺陷。
    """
    client, _ = admin_client
    monkeypatch.setattr("api.admin._table_conn", lambda db, table: _MissingTableConn())

    response = client.get(path)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "数据库迁移" in detail
    assert "does not exist" not in detail


