#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准图清单与单张标准图必须按责任区切片。

``/standard-manifest`` 是给员工端整包离线缓存用的：不做切片就等于把全店标准图
发给每个员工（拿 standard_id 遍历 ``/standards/{id}/image`` 即可下载）。同一条
数据在 ``/staff/items/{item_id}/standard`` 上是有责任区校验的，两条路径不能一条
严一条松。
"""

import asyncio
import io
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork

SUPER = {"kind": "super"}
PHONE = "13800138000"
PASSWORD = "password123"


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _jpeg():
    output = io.BytesIO()
    Image.new("RGB", (80, 60), (120, 180, 220)).save(output, format="JPEG")
    return output.getvalue()


def _capture():
    return {"bytes": _jpeg(), "content_type": "image/jpeg", "markup": []}


@pytest.fixture
def scope_http(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(
        db, now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
    )
    work = HygieneWork(
        db,
        captures=FakeCaptureStore(),
        now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    zones = _run(work.list_zones())[:2]
    mine = _run(work.add_daily_item(SUPER, zones[0]["id"], "案板-台面", _capture()))
    other = _run(work.add_daily_item(SUPER, zones[1]["id"], "馅档-台面", _capture()))
    employee = _run(accounts.register(PHONE, PASSWORD, "张三"))
    _run(accounts.approve(employee["id"]))

    app = FastAPI()
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    with TestClient(app) as client:
        yield client, app, db, accounts, work, zones, mine, other
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _login(client):
    response = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    )
    assert response.status_code == 200


def _pick(client, zone_id):
    response = client.post(
        "/api/hygiene/staff/assignment",
        json={"shift": "白班", "zone_id": zone_id},
    )
    assert response.status_code == 200


def _manifest_item_ids(client):
    response = client.get("/api/hygiene/standard-manifest")
    assert response.status_code == 200
    return [int(entry["item_id"]) for entry in response.json()["standards"]]


def test_staff_manifest_is_sliced_to_own_zone(scope_http):
    client, _app, _db, _accounts, _work, zones, mine, other = scope_http
    _login(client)
    _pick(client, zones[0]["id"])

    item_ids = _manifest_item_ids(client)
    assert mine["id"] in item_ids
    assert other["id"] not in item_ids, "不能把别的责任区的标准图发给员工"


def test_staff_cannot_fetch_other_zone_standard_image(scope_http):
    client, _app, _db, _accounts, _work, zones, mine, other = scope_http
    _login(client)
    _pick(client, zones[0]["id"])

    owned = client.get(f"/api/hygiene/standards/{mine['current_standard_id']}/image")
    assert owned.status_code == 200
    assert owned.headers["content-type"] == "image/jpeg"

    foreign = client.get(
        f"/api/hygiene/standards/{other['current_standard_id']}/image"
    )
    assert foreign.status_code == 403, "跨责任区必须拒绝（此前是 200）"


def test_staff_without_zone_gets_empty_manifest_not_error(scope_http):
    client, _app, _db, _accounts, _work, _zones, _mine, _other = scope_http
    _login(client)

    response = client.get("/api/hygiene/standard-manifest")
    assert response.status_code == 200, "还没选责任区不该把页面挡在 400"
    assert response.json()["standards"] == []


def test_admin_session_still_sees_every_zone(scope_http):
    client, app, _db, _accounts, _work, _zones, mine, other = scope_http
    app.dependency_overrides[hygiene_module.require_standard_cache_session] = (
        lambda: {"kind": "admin"}
    )

    item_ids = _manifest_item_ids(client)
    assert mine["id"] in item_ids
    assert other["id"] in item_ids, "管理员/缓存面板需要全量清单"

    for standard_id in (mine["current_standard_id"], other["current_standard_id"]):
        response = client.get(f"/api/hygiene/standards/{standard_id}/image")
        assert response.status_code == 200


def test_manifest_hands_out_the_preview_variant(scope_http):
    """整份清单会被离线缓存：下发原图就等于让每台手机存几百 MB。"""
    client, app, _db, _accounts, _work, zones, mine, _other = scope_http
    _login(client)
    _pick(client, zones[0]["id"])

    response = client.get("/api/hygiene/standard-manifest")
    entries = response.json()["standards"]
    assert entries
    for entry in entries:
        assert entry["image_url"].endswith("?variant=preview")

    image = client.get(entries[0]["image_url"])
    assert image.status_code == 200, "缓存下来的 URL 必须真能取到图"
    assert image.headers["content-type"] == "image/jpeg"
