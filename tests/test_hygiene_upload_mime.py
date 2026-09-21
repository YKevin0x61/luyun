#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片类型由内容裁决，不信客户端的 Content-Type。

修复前的链路（审查报告 §1.3，已实测）：员工上传 ``<svg><script>`` 并声明
``image/svg+xml`` → 只检查 ``startswith("image/")`` 就放过 → 落库并以
``Content-Type: image/svg+xml`` 回写，且没有 nosniff —— 受害者直接打开该 URL 就是
同源脚本执行。

现在：能识别的按真实格式回写（JPEG/PNG/GIF/WebP/BMP/ICO），认不出来的一律
``application/octet-stream`` + ``nosniff`` + ``Content-Disposition: attachment``。
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
from services.hygiene.images import ImageVariantGenerator, sniff_image_content_type
from services.hygiene.work import HygieneWork

SUPER = {"kind": "super"}
PHONE = "13800138000"
PASSWORD = "password123"

SVG_PAYLOAD = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120">'
    b'<script>document.title="pwned"</script>'
    b'<rect width="120" height="120" fill="red"/></svg>'
)


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


@pytest.fixture
def upload_http(tmp_path):
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
    zone = _run(work.list_zones())[0]
    item = _run(
        work.add_daily_item(
            SUPER,
            zone["id"],
            "案板-台面",
            {"bytes": _jpeg(), "content_type": "image/jpeg", "markup": []},
        )
    )
    employee = _run(accounts.register(PHONE, PASSWORD, "张三"))
    _run(accounts.approve(employee["id"]))
    app = FastAPI()
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    app.dependency_overrides[hygiene_module.require_session] = lambda: "test-admin"
    with TestClient(app) as client:
        yield client, db, work, zone, item
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _submit(client, item_id, payload, declared_type):
    return client.post(
        f"/api/hygiene/staff/daily/{item_id}/submit",
        files={"file": ("capture.bin", payload, declared_type)},
        data={"live": "true", "shift": "白班"},
    )


def _prepare_staff(client, zone):
    assert client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    ).status_code == 200
    assert client.post(
        "/api/hygiene/staff/assignment",
        json={"shift": "白班", "zone_id": zone["id"]},
    ).status_code == 200


def test_sniff_helper_only_trusts_content():
    assert sniff_image_content_type(_jpeg()) == "image/jpeg"
    assert sniff_image_content_type(SVG_PAYLOAD) is None
    assert sniff_image_content_type(b"<html><script>x</script></html>") is None
    assert sniff_image_content_type(b"") is None


def test_svg_upload_is_stored_as_octet_stream_and_never_served_as_svg(upload_http):
    client, db, _work, zone, item = upload_http
    _prepare_staff(client, zone)

    response = _submit(client, item["id"], SVG_PAYLOAD, "image/svg+xml")
    assert response.status_code == 200, response.text

    cur = _run(
        db._conn.execute(
            "SELECT content_type FROM hygiene_daily_submissions ORDER BY id DESC LIMIT 1"
        )
    )
    stored = dict(_run(cur.fetchone()))["content_type"]
    assert stored == "application/octet-stream", "落库类型必须由内容决定"

    image = client.get(
        f"/api/hygiene/staff/daily/{item['id']}/capture",
        params={"shift": "白班"},
    )
    assert image.status_code == 200
    assert image.headers["content-type"].split(";")[0] == "application/octet-stream"
    assert image.headers.get("x-content-type-options") == "nosniff"
    assert image.headers["content-disposition"].startswith("attachment")


def test_real_jpeg_keeps_image_type_and_stays_inline(upload_http):
    client, _db, _work, zone, item = upload_http
    _prepare_staff(client, zone)

    response = _submit(client, item["id"], _jpeg(), "image/jpeg")
    assert response.status_code == 200, response.text

    image = client.get(
        f"/api/hygiene/staff/daily/{item['id']}/capture",
        params={"shift": "白班"},
    )
    assert image.status_code == 200
    assert image.headers["content-type"].split(";")[0] == "image/jpeg"
    assert image.headers.get("x-content-type-options") == "nosniff"
    assert image.headers["content-disposition"].startswith("inline")


def test_declared_type_cannot_downgrade_a_real_image(upload_http):
    """声明 svg 但内容是 JPEG：按内容走，图片照样能显示。"""
    client, _db, _work, zone, item = upload_http
    _prepare_staff(client, zone)

    response = _submit(client, item["id"], _jpeg(), "image/svg+xml")
    assert response.status_code == 200, response.text

    image = client.get(
        f"/api/hygiene/staff/daily/{item['id']}/capture",
        params={"shift": "白班"},
    )
    assert image.headers["content-type"].split(";")[0] == "image/jpeg"


def test_standard_upload_rejects_non_image_payload(upload_http):
    """标准图要求可解码：SVG 无法解码，必须 400 而不是被存下来。"""
    client, _db, _work, zone, _item = upload_http

    response = client.post(
        f"/api/hygiene/admin/zones/{zone['id']}/items",
        files={"file": ("evil.svg", SVG_PAYLOAD, "image/svg+xml")},
        data={"name": "伪装标准图"},
    )
    assert response.status_code == 400
    assert "图片无法读取" in response.json()["detail"]
