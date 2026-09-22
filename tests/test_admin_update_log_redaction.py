#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理员改表的日志不许把字段**原值**整条打出去。

缺陷背景（`.scratch/project-review-2026-09-22` SEC-06）：`update_row` 在写之前打
`logger.warning(f"[UPDATE] {table} rowid={row_id} cols={cols} values={[...]}")`，把所有
待更新字段的原值拼进 WARNING。读路径有一份 `_ADMIN_REDACTED_COLUMNS`
（`admin_user.password_hash` / `sessions.session_id` / `api_tokens.token_hash`），
这条写路径不看它——两个入口对同一份数据的可见性不一致。日志落 `logs` 表，可经
`/api/logs/*` 读、并随备份离开机器。

已核：三张含脱敏列的表都在 `AUTH_PHYSICAL_TABLES` 里，被 `_reject_read_only_table_write`
挡在写路径外，所以**没能**复现明文口令哈希落日志；能复现的是 `app_settings`、
`wecom_push_webhooks` 这类**可写**表的全部字段值。因此本文件钉两条：

1. 脱敏列的更新绝不能把原值写进日志（`_redacted_values_for_log` 的直测 + `update_row`
   走真实 HTTP 路径的 `caplog` 断言）；
2. 可写表的普通字段**仍然**要记 —— 那条日志是运维唯一能看到的"谁改了什么"，
   脱敏不是把日志删掉。

第 1 条没法用真实表跑（那三张表本来就被挡在写路径外，正是它们没出事的原因），所以
直测脱敏函数，再用 `app_settings` 的 HTTP 路径证明日志确实还带着普通字段值。
"""

from __future__ import annotations

import asyncio
import logging
import unittest

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.admin as admin_module
from api.security import verify_admin_token
from config import settings
from database import DatabaseManager


def _run(coro):
    return asyncio.run(coro)


class RedactedValuesForLogTest(unittest.TestCase):
    """脱敏函数本身：脱敏列不出现在日志值里，非脱敏列原样保留。"""

    def test_redacted_column_value_is_replaced(self):
        logged = admin_module._redacted_values_for_log(
            "admin_user",
            {"username": "admin", "password_hash": "$2b$12$abc"},
        )
        self.assertEqual(logged, {"username": "admin", "password_hash": "已隐藏"})
        self.assertNotIn("$2b$12$abc", str(logged))

    def test_redaction_is_per_table(self):
        """`session_id` 只在 sessions 表里脱敏，别的表同名/同形字段不误伤。"""
        logged = admin_module._redacted_values_for_log(
            "hygiene_staff_sessions",
            {"session_id": "plain-cookie-value"},
        )
        self.assertEqual(logged, {"session_id": "plain-cookie-value"})

    def test_table_without_redacted_columns_is_untouched(self):
        values = {"key": "wecom_push_time", "value": "21:30"}
        self.assertEqual(admin_module._redacted_values_for_log("app_settings", values), values)

    def test_log_line_helper_never_contains_redacted_values(self):
        line = admin_module._update_log_line(
            "admin_user", 7, {"password_hash": "$2b$12$abc", "username": "admin"}
        )
        self.assertNotIn("$2b$12$abc", line)
        self.assertIn("password_hash", line)
        self.assertIn("admin", line)


@pytest.fixture
def admin_client(tmp_path):
    """真实 DatabaseManager（conftest 钉死测试库）+ 只打桩凭据门。

    不用假连接：`update_row` 会先按真实 schema 校验表名/列名，假连接反而测不到
    HTTP 路径。库里的行是被 TRUNCATE 过的，`rowid=1` 影响 0 行也不影响日志断言。
    """
    old_dir = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    app = FastAPI()
    app.include_router(admin_module.router)
    app.dependency_overrides[admin_module.get_db] = lambda: db
    app.dependency_overrides[verify_admin_token] = lambda: True
    with TestClient(app) as client:
        yield client, db
    _run(db.close())
    app.dependency_overrides.clear()
    settings.DATABASE_DIR = old_dir


def test_update_row_still_logs_plain_values(admin_client, caplog):
    """普通可写表：日志仍要能看出改了什么（脱敏不是删日志）。"""
    client, db = admin_client
    conn = db.table("wecom_push_webhooks").conn

    async def seed():
        async with conn.cursor() as cursor:
            await cursor.execute(
                """INSERT INTO wecom_push_webhooks
                   (name, webhook_url_encrypted, webhook_url_masked, enabled, notes, created_at, updated_at)
                   VALUES ('日志脱敏用例', 'enc', 'masked', 1, '', '2026-01-01', '2026-01-01')"""
            )
        await conn.commit()

    _run(seed())
    row_id = client.get(
        "/api/admin/tables/wecom_push_webhooks/rows?page=1&page_size=10"
    ).json()["rows"][0]["rowid"]

    with caplog.at_level(logging.WARNING, logger=admin_module.logger.name):
        response = client.put(
            f"/api/admin/tables/wecom_push_webhooks/rows/{row_id}",
            json={"values": {"notes": "看板备注"}},
        )

    assert response.status_code == 200, response.text
    update_lines = [r.getMessage() for r in caplog.records if "[UPDATE]" in r.getMessage()]
    assert len(update_lines) == 1, update_lines
    assert "看板备注" in update_lines[0], update_lines
    assert "notes" in update_lines[0], update_lines


if __name__ == "__main__":
    unittest.main()
