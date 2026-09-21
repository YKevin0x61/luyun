#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP: PATCH /api/hygiene/admin/items/{id}/standard/markup（只改标注、不换图）。"""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.work import HygieneWork

SUPER = {"kind": "super"}
ADMIN_INIT = {
    "username": "admin",
    "password": "password123",
    "confirm_password": "password123",
}
STANDARD_BYTES = b"STANDARD-JPEG-LITERAL"
CIRCLE = {"kind": "circle", "x": 0.4, "y": 0.3, "r": 0.1}
CAPTION = {"kind": "caption", "x": 0.1, "y": 0.2, "text": "看这里"}


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


@pytest.fixture
def markup_http(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(
        db, now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
    )
    captures = FakeCaptureStore()
    work = HygieneWork(
        db,
        captures=captures,
        now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
        notifier=None,
    )
    _run(work.prepare())
    zone = _run(work.create_zone(SUPER, "库房", ["白班", "夜班"]))
    item = _run(work.add_daily_item(
        SUPER,
        zone["id"],
        "货架表面",
        {"bytes": STANDARD_BYTES, "content_type": "image/jpeg", "markup": [CIRCLE]},
    ))
    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    with TestClient(app) as client:
        yield client, item, work, captures
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _admin(client):
    assert client.post("/api/auth/init", json=ADMIN_INIT).status_code == 200
    return client


def test_requires_admin_session(markup_http):
    client, item, _work, _captures = markup_http
    resp = client.patch(
        f"/api/hygiene/admin/items/{item['id']}/standard/markup",
        json={"markup": [CAPTION]},
    )
    assert resp.status_code == 401


def test_updates_markup_without_reuploading_the_image(markup_http):
    client, item, _work, captures = markup_http
    _admin(client)
    blobs_before = dict(captures.blobs)

    resp = client.patch(
        f"/api/hygiene/admin/items/{item['id']}/standard/markup",
        json={"markup": [CIRCLE, CAPTION]},
    )

    assert resp.status_code == 200
    updated = resp.json()["item"]
    assert updated["current_standard_id"] != item["current_standard_id"]
    assert updated["capture_id"] == item["capture_id"], "图片不应该换"
    assert captures.blobs == blobs_before, "不应该产生新的图片文件"

    # 图片端点仍然能按新的 standard_id 取到同一张图。
    image = client.get(f"/api/hygiene/admin/items/{item['id']}/standard")
    assert image.status_code == 200
    assert image.content == STANDARD_BYTES
    assert image.headers["content-type"].startswith("image/jpeg")


def test_edited_markup_reaches_the_staff_work_list(markup_http):
    client, item, work, _captures = markup_http
    _admin(client)
    assert client.patch(
        f"/api/hygiene/admin/items/{item['id']}/standard/markup",
        json={"markup": [CAPTION]},
    ).status_code == 200

    rows = _run(work.list_daily_work(SUPER))
    row = next(entry for entry in rows if entry["item_id"] == item["id"])
    assert row["markup"] == [_normalized_caption()]


def test_empty_markup_clears_the_annotations(markup_http):
    client, item, work, _captures = markup_http
    _admin(client)

    resp = client.patch(
        f"/api/hygiene/admin/items/{item['id']}/standard/markup",
        json={"markup": []},
    )

    assert resp.status_code == 200

    rows = _run(work.list_daily_work(SUPER))
    row = next(entry for entry in rows if entry["item_id"] == item["id"])
    assert row["markup"] == []


def test_unknown_item_is_404(markup_http):
    client, _item, _work, _captures = markup_http
    _admin(client)
    resp = client.patch(
        "/api/hygiene/admin/items/99999/standard/markup",
        json={"markup": [CIRCLE]},
    )
    assert resp.status_code == 404


def test_garbage_body_is_422_not_500(markup_http):
    client, item, _work, _captures = markup_http
    _admin(client)
    resp = client.patch(
        f"/api/hygiene/admin/items/{item['id']}/standard/markup",
        json={"markup": "not-a-list"},
    )
    assert resp.status_code == 422


def _normalized_caption():
    return {"kind": "caption", "x": 0.1, "y": 0.2, "text": "看这里"}
