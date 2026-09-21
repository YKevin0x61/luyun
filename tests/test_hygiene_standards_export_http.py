#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出标准图的 HTTP 契约：一个 zip、按责任区建子文件夹、标注已经烘焙进像素。

服务层的渲染/打包细节在 ``test_hygiene_standards_export.py``；这里只钉端点行为。
用 ``FileCaptureStore`` 而不是 ``FakeCaptureStore``：后者 ``path_async`` 返回 None，
而导出是按文件路径逐张读的，假存储测不出真实链路。

责任区直接用建库时 seed 好的那几个（``SEED_ZONE_NAMES``），不自己建同名区。
"""

import asyncio
import io
import zipfile
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.captures import FileCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork

SUPER = {"kind": "super"}
CIRCLE = {"kind": "circle", "x": 0.5, "y": 0.5, "r": 0.15}
CAPTION = {"kind": "caption", "x": 0.4, "y": 0.3, "text": "台面要干净"}
MARK_RGB = (63, 224, 176)
EXPORT_PATH = "/api/hygiene/admin/standards-export"


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _jpeg(width: int = 640, height: int = 480) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (20, 40, 60)).save(output, format="JPEG", quality=95)
    return output.getvalue()


def _mark_pixels(data: bytes) -> int:
    with Image.open(io.BytesIO(data)) as image:
        pixels = list(image.convert("RGB").getdata())
    return sum(
        1
        for red, green, blue in pixels
        if abs(red - MARK_RGB[0]) <= 40
        and abs(green - MARK_RGB[1]) <= 40
        and abs(blue - MARK_RGB[2]) <= 40
    )


def _build(tmp_path, items):
    """起一套最小运行时。``items`` 是 ``(种子责任区下标, 检查项名, 标注)``。"""
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    work = HygieneWork(
        db,
        captures=FileCaptureStore(tmp_path / "captures"),
        now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    zones = _run(work.list_zones())
    for zone_index, item_name, markup in items:
        _run(
            work.add_daily_item(
                SUPER,
                zones[zone_index]["id"],
                item_name,
                {"bytes": _jpeg(), "markup": list(markup)},
            )
        )
    app = FastAPI()
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    app.dependency_overrides[hygiene_module.require_session] = lambda: "test-admin"
    return app, db, zones


@pytest.fixture
def export_http(tmp_path):
    old = settings.DATABASE_DIR
    app, db, zones = _build(
        tmp_path,
        [
            (0, "玻璃窗", [CIRCLE, CAPTION]),
            (2, "蒸笼内壁", []),
        ],
    )
    with TestClient(app) as client:
        yield client, zones
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


@pytest.fixture
def empty_http(tmp_path):
    old = settings.DATABASE_DIR
    app, db, _zones = _build(tmp_path, [])
    with TestClient(app) as client:
        yield client
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def test_export_returns_zip_grouped_by_zone(export_http):
    client, zones = export_http
    response = client.get(EXPORT_PATH)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    # 中文文件名走 RFC 5987，前端 api.download 才会解析出「标准图-YYYYMMDD.zip」
    assert "filename*=UTF-8''" in response.headers["content-disposition"]

    marked_member = f"{zones[0]['name']}/玻璃窗.jpg"
    plain_member = f"{zones[2]['name']}/蒸笼内壁.jpg"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = sorted(archive.namelist())
        marked = archive.read(marked_member)
        plain = archive.read(plain_member)

    assert names == sorted([marked_member, plain_member])
    assert _mark_pixels(marked) > 200, "导出图里没有标注"
    assert _mark_pixels(plain) == 0, "没标注的检查项不该被画上东西"


def test_export_preview_variant_is_smaller(export_http):
    client, _zones = export_http
    original = client.get(f"{EXPORT_PATH}?size=original")
    preview = client.get(f"{EXPORT_PATH}?size=preview")

    assert original.status_code == 200
    assert preview.status_code == 200
    assert len(preview.content) <= len(original.content)


def test_export_rejects_unknown_size(export_http):
    client, _zones = export_http
    assert client.get(f"{EXPORT_PATH}?size=huge").status_code == 422


def test_export_without_any_standard_returns_404(empty_http):
    response = empty_http.get(EXPORT_PATH)

    assert response.status_code == 404
    assert "标准图" in response.json()["detail"]
