#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP contract for hygiene image variants."""

import asyncio
import io
import tempfile
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
from services.hygiene.captures import FileCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork
from tests.hygiene_duty import assign_duty

SUPER = {"kind": "super"}
PHONE = "13800138000"
PASSWORD = "password123"

# 夹具里 accounts / work 的固定时刻。票 10 的前置数据（`assign_duty`）必须用同一个
# 时刻造「今天」——排班的营业日跟卫生的营业日差了，员工今天就查不到那一行排班。
FIXED_NOW = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def jpeg_bytes(width=800, height=600):
    output = io.BytesIO()
    Image.new("RGB", (width, height), (110, 170, 220)).save(
        output,
        format="JPEG",
        quality=88,
    )
    return output.getvalue()


@pytest.fixture
def image_http(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(
        db,
        now=lambda: FIXED_NOW,
    )
    work = HygieneWork(
        db,
        captures=FakeCaptureStore(),
        now=lambda: FIXED_NOW,
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    employee = _run(accounts.register(PHONE, PASSWORD, "张三"))
    _run(accounts.approve(employee["id"]))
    app = FastAPI()
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    with TestClient(app) as client:
        yield client, db, accounts, work
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _add_item(work, data):
    zone = _run(work.list_zones())[0]
    return _run(
        work.add_daily_item(
            SUPER,
            zone["id"],
            "案板表面",
            {"bytes": data, "content_type": "image/jpeg", "markup": []},
        )
    )


def _staff_id(db) -> int:
    """夹具注册的那名员工的 id —— ``assign_duty`` 按 id 定位，前置数据都要它。"""
    cur = _run(
        db._conn.execute(
            "SELECT id FROM hygiene_employees WHERE phone = ?",
            (PHONE,),
        )
    )
    row = _run(cur.fetchone())
    assert row is not None, f"夹具里应该有手机号 {PHONE} 的员工"
    return int(dict(row)["id"])


def _login_and_pick_zone(client, db, work):
    """登录并把「今天 · 白班档 · 第一个工作区」**配成排班**（票 10 的唯一入口）。

    ``/standards/{id}/image`` 现在按工作区切片（同一条数据在
    ``/staff/items/{id}/standard`` 上一直是有校验的），未排到区的员工会被 400
    挡下——这些用例测的是变体与 ETag，所以先把前置条件补上。

    票 10 之前这里打的是 ``POST /api/hygiene/staff/assignment``（员工当天自己选班次
    和工作区），那个入口现在一律 403：分工由**排班结果**决定，前置数据只能从上游配。
    ``now`` 用夹具那个固定时刻，不然排班的「今天」对不上卫生的营业日。
    """
    assert client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    ).status_code == 200
    zone = _run(work.list_zones())[0]
    _run(
        assign_duty(
            db,
            _staff_id(db),
            slot="day",
            zone_id=zone["id"],
            now=FIXED_NOW,
        )
    )


def test_staff_can_fetch_variant_and_etag_round_trip(image_http):
    client, db, _accounts, work = image_http
    original = jpeg_bytes()
    item = _add_item(work, original)
    _login_and_pick_zone(client, db, work)

    thumb = client.get(
        f"/api/hygiene/standards/{item['current_standard_id']}/image",
        params={"variant": "thumb"},
    )
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"
    assert thumb.content != original
    assert int(thumb.headers["content-length"]) == len(thumb.content)
    assert thumb.headers["etag"]

    cached = client.get(
        f"/api/hygiene/standards/{item['current_standard_id']}/image",
        params={"variant": "thumb"},
        headers={"If-None-Match": thumb.headers["etag"]},
    )
    assert cached.status_code == 304
    assert cached.content == b""


def test_original_endpoint_remains_backward_compatible(image_http):
    client, db, _accounts, work = image_http
    original = jpeg_bytes()
    item = _add_item(work, original)
    _login_and_pick_zone(client, db, work)

    response = client.get(
        f"/api/hygiene/standards/{item['current_standard_id']}/image"
    )
    assert response.status_code == 200
    assert response.content == original


def test_stale_standard_byte_size_does_not_set_wrong_content_length(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    try:
        db = DatabaseManager()
        _run(db.connect())
        set_runtime(AppRuntime(db=db))
        work = HygieneWork(
            db,
            captures=FileCaptureStore(tmp_path / "hygiene-captures"),
            now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
            image_variants=ImageVariantGenerator(),
        )
        _run(work.prepare())
        item = _add_item(work, jpeg_bytes())
        _run(
            db._conn.execute(
                "UPDATE hygiene_standards SET byte_size = ? WHERE id = ?",
                (1, item["current_standard_id"]),
            )
        )
        _run(db._conn.commit())

        app = FastAPI()
        app.include_router(hygiene_module.router)
        app.dependency_overrides[hygiene_module._get_work] = lambda: work
        app.dependency_overrides[
            hygiene_module.require_standard_cache_session
        ] = lambda: {"kind": "admin"}
        with TestClient(app) as client:
            response = client.get(
                f"/api/hygiene/standards/{item['current_standard_id']}/image"
            )
            assert response.status_code == 200
            assert int(response.headers["content-length"]) == len(response.content)
            assert len(response.content) > 1
    finally:
        _run(db.close())
        set_runtime(None)
        settings.DATABASE_DIR = old


def test_staff_upload_rejects_oversized_file_before_domain_write(
    image_http,
    monkeypatch,
):
    client, _db, _accounts, work = image_http
    monkeypatch.setattr(hygiene_module, "MAX_UPLOAD_BYTES", 10)
    assert client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    ).status_code == 200

    response = client.post(
        "/api/hygiene/staff/daily/1/submit",
        data={"live": "true", "shift": "白班"},
        files={"file": ("capture.jpg", b"x" * 11, "image/jpeg")},
    )

    assert response.status_code == 413
    assert "不能超过" in response.json()["detail"]
    assert _run(work.list_daily_work({"kind": "super"})) == []
