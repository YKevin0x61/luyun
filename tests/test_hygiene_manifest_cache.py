#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准图清单不该每个请求都扫一遍 capture 目录。

审查报告 §3.5：manifest 要核对"标准图文件还在不在"，原来是每个请求 iterdir +
逐文件 stat。文件上到几万时单次请求几百毫秒，而这份清单在每次卫生路由切换时都会
被拉一次。现在文件集合带 60 秒 TTL 缓存，响应也给出 Cache-Control/ETag。
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


class CountingCaptureStore(FakeCaptureStore):
    def __init__(self):
        super().__init__()
        self.list_calls = 0

    async def list_ids_async(self):
        self.list_calls += 1
        return await super().list_ids_async()


@pytest.fixture
def env(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(
        db, now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
    )
    store = CountingCaptureStore()
    work = HygieneWork(
        db,
        captures=store,
        now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    zone = _run(work.list_zones())[0]
    _run(
        work.add_daily_item(
            SUPER,
            zone["id"],
            "案板-台面",
            {"bytes": _jpeg(), "content_type": "image/jpeg", "markup": []},
        )
    )
    app = FastAPI()
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    app.dependency_overrides[hygiene_module.require_standard_cache_session] = (
        lambda: {"kind": "admin"}
    )
    with TestClient(app) as client:
        yield client, work, store
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def test_directory_scan_happens_once_within_the_ttl(env):
    _client, work, store = env
    before = store.list_calls

    for _ in range(3):
        manifest = _run(work.standard_manifest())
        assert manifest["standards"], "清单本身不能是空的"

    assert store.list_calls - before == 1, "TTL 内只应扫一次目录"


def test_manifest_carries_cache_headers(env):
    client, _work, _store = env
    response = client.get("/api/hygiene/standard-manifest")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=30"
    assert response.headers["etag"], "换版要能被缓存识别出来"
    # version 就是 ETag 的依据：同内容必须稳定
    first = response.headers["etag"]
    again = client.get("/api/hygiene/standard-manifest")
    assert again.headers["etag"] == first
