#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「卫生群」标记（票 01）：HTTP 契约 + 收件人过滤。

这一票的关键契约是**三态**：只有明确传 true / false 才写 `hygiene_feed` 这一列。
列表上的「停用 / 启用」快捷开关、以及还缓存着旧 bundle 的浏览器，提交的载荷里都没有
这个字段 —— 一旦把它当普通布尔（默认 False）处理，点一下「停用」就会把标记清掉，
而界面上完全看不出来。
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.security import verify_admin_token
from api.wecom_push import router as wecom_push_router
from config import settings
from database import DatabaseManager, get_db

WEBHOOK_URL = (
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
    "?key=693a91f6-7aoc-4bc4-97a0-0ec2sifa5aaa"
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def wecom_client(tmp_path):
    old_dir = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    app = FastAPI()
    app.include_router(wecom_push_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[verify_admin_token] = lambda: True
    with TestClient(app) as client:
        yield client, db
    _run(db.close())
    app.dependency_overrides.clear()
    settings.DATABASE_DIR = old_dir


def _create(client, name="门店群", hygiene_feed=None, enabled=True):
    payload = {"name": name, "webhook_url": WEBHOOK_URL, "enabled": enabled, "notes": ""}
    if hygiene_feed is not None:
        payload["hygiene_feed"] = hygiene_feed
    response = client.post("/api/wecom-push/webhooks", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["webhook"]


# ── 标记本身 ────────────────────────────────────────────────────────────────


def test_new_webhook_can_be_marked_as_hygiene_group(wecom_client):
    client, _db = wecom_client

    created = _create(client, hygiene_feed=True)

    assert created["hygiene_feed"] is True
    listed = client.get("/api/wecom-push/webhooks").json()["webhooks"]
    assert [row["hygiene_feed"] for row in listed] == [True]


def test_new_webhook_is_not_a_hygiene_group_by_default(wecom_client):
    client, _db = wecom_client

    assert _create(client)["hygiene_feed"] is False


# ── 三态：不带这个字段就不许动它 ────────────────────────────────────────────


def test_toggle_enabled_without_the_field_keeps_the_mark(wecom_client):
    """列表上的「停用」快捷开关不发 hygiene_feed —— 标记必须原样留着。"""
    client, _db = wecom_client
    created = _create(client, hygiene_feed=True)

    response = client.put(
        f"/api/wecom-push/webhooks/{created['id']}",
        json={"name": created["name"], "webhook_url": None, "enabled": False, "notes": ""},
    )

    assert response.status_code == 200, response.text
    body = response.json()["webhook"]
    assert body["enabled"] is False
    assert body["hygiene_feed"] is True


def test_explicit_false_clears_the_mark(wecom_client):
    """取消勾选要真的能取消 —— 三态不能变成"永远写不进去"。"""
    client, _db = wecom_client
    created = _create(client, hygiene_feed=True)

    response = client.put(
        f"/api/wecom-push/webhooks/{created['id']}",
        json={"name": created["name"], "enabled": True, "hygiene_feed": False, "notes": ""},
    )

    assert response.json()["webhook"]["hygiene_feed"] is False


# ── 收件人过滤（后续票据复用这条查询） ──────────────────────────────────────


def test_hygiene_only_filter_returns_marked_and_enabled_groups(wecom_client):
    client, db = wecom_client
    marked = _create(client, name="卫生群", hygiene_feed=True)
    daily = _create(client, name="日报群")
    marked_disabled = _create(client, name="停用的卫生群", hygiene_feed=True, enabled=False)

    hygiene_groups = _run(db.wecom_webhooks_all(include_disabled=False, hygiene_only=True))
    assert {row["id"] for row in hygiene_groups} == {marked["id"]}

    with_disabled = _run(db.wecom_webhooks_all(include_disabled=True, hygiene_only=True))
    assert {row["id"] for row in with_disabled} == {marked["id"], marked_disabled["id"]}


def test_default_listing_is_unchanged(wecom_client):
    """不传 hygiene_only 时行为与从前一致：所有启用中的都回。"""
    client, db = wecom_client
    marked = _create(client, name="卫生群", hygiene_feed=True)
    daily = _create(client, name="日报群")
    _create(client, name="停用的群", enabled=False)

    listed = _run(db.wecom_webhooks_all(include_disabled=False))

    assert {row["id"] for row in listed} == {marked["id"], daily["id"]}
