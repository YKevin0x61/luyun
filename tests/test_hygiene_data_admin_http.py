#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据与照片管理端点的 HTTP 契约：鉴权、参数校验、导出作业、历史回看队列。

服务层细节在 ``test_hygiene_data_admin.py``；这里只钉端点行为。用
``FileCaptureStore``：导出与存储概况都按文件路径算，假存储测不出真实链路。
"""

import asyncio
import io
import json
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.archive import HygieneDataArchive
from services.hygiene.captures import FileCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork

SUPER = {"kind": "super"}
DAY_PHONE = "13800000001"
NOW = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
RECORDS = "/api/hygiene/admin/data/records"
STORAGE = "/api/hygiene/admin/data/storage"
EXPORT_JOBS = "/api/hygiene/admin/data/export/jobs"
QUEUE = "/api/hygiene/admin/daily-queue"


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _jpeg(color=(30, 90, 120)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (120, 90), color).save(output, format="JPEG", quality=90)
    return output.getvalue()


def _live(color=(30, 90, 120)):
    return {"bytes": _jpeg(color), "content_type": "image/jpeg", "live": True}


def _standard(color=(200, 200, 40)):
    return {"bytes": _jpeg(color), "content_type": "image/jpeg"}


def _staff(employee_id, phone, shift):
    return {
        "kind": "staff",
        "id": employee_id,
        "permission": "普通员工",
        "phone": phone,
        "shift": shift,
    }


class _Runtime:
    def __init__(self, db, work, archive):
        self.db = db
        self.work = work
        self.archive = archive


def _build(tmp_path, *, submit: bool):
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    captures = FileCaptureStore(Path(tmp_path) / "hygiene-captures")
    work = HygieneWork(
        db,
        captures=captures,
        now=lambda: NOW,
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    archive = HygieneDataArchive(db, captures=captures)

    now_iso = NOW.isoformat()
    _run(
        db._conn.execute(
            """INSERT INTO hygiene_employees
                   (id, phone, name, password_hash, permission, approved, created_at, updated_at)
               VALUES (?, ?, ?, '', '普通员工', 1, ?, ?)""",
            (11, DAY_PHONE, "张三", now_iso, now_iso),
        )
    )
    _run(db._conn.commit())

    zones = _run(work.list_zones())
    zone = zones[0]
    item = _run(work.add_daily_item(SUPER, zone["id"], "案板表面", _standard()))
    if submit:
        _run(work.submit_daily(_staff(11, DAY_PHONE, "白班"), item["id"], _live()))
    return _Runtime(db, work, archive), item, zone


def _make_app(runtime, *, authed: bool):
    app = FastAPI()
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_work] = lambda: runtime.work
    app.dependency_overrides[hygiene_module._get_archive] = lambda: runtime.archive
    if authed:
        app.dependency_overrides[hygiene_module.require_session] = lambda: "test-admin"
    return app


@pytest.fixture
def data_http(tmp_path):
    old = settings.DATABASE_DIR
    runtime, _item, _zone = _build(tmp_path, submit=True)
    with TestClient(_make_app(runtime, authed=True)) as client:
        yield client, runtime
    _run(runtime.db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


@pytest.fixture
def anon_http(tmp_path):
    old = settings.DATABASE_DIR
    runtime, _item, _zone = _build(tmp_path, submit=False)
    with TestClient(_make_app(runtime, authed=False)) as client:
        yield client
    _run(runtime.db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _record(client, query: str = "") -> dict:
    response = client.get(f"{RECORDS}{query}")
    assert response.status_code == 200, response.text
    return response.json()


def test_every_data_route_requires_an_admin_session(anon_http):
    client = anon_http
    assert client.get(RECORDS).status_code == 401
    assert client.get(STORAGE).status_code == 401
    assert client.get("/api/hygiene/admin/data/photo/whatever").status_code == 401
    assert client.post(EXPORT_JOBS).status_code == 401
    assert client.delete("/api/hygiene/admin/data/records/daily/1").status_code == 401
    assert client.post("/api/hygiene/admin/data/purge", json={"kinds": ["daily"]}).status_code == 401


def test_records_endpoint_filters_and_validates_dates(data_http):
    client, _runtime = data_http
    listed = _record(client, "?date_from=2026-09-13&date_to=2026-09-13")
    assert listed["date_from"] == "2026-09-13"
    assert listed["total"] >= 2  # 标准图版本 + 实拍
    kinds = {item["kind"] for item in listed["items"]}
    assert "daily" in kinds and "standard" in kinds
    daily = next(item for item in listed["items"] if item["kind"] == "daily")
    assert daily["submitter_name"] == "张三"
    assert daily["photos"][0]["capture_id"]

    only_daily = _record(
        client, "?date_from=2026-09-13&date_to=2026-09-13&kinds=daily"
    )
    assert {item["kind"] for item in only_daily["items"]} == {"daily"}

    assert client.get(f"{RECORDS}?date_from=2026-13-01").status_code == 400
    assert client.get(f"{RECORDS}?kinds=nope").status_code == 400
    assert (
        client.get(f"{RECORDS}?date_from=2020-01-01&date_to=2026-09-13").status_code == 400
    )


def test_storage_endpoint_reports_counts(data_http):
    client, _runtime = data_http
    response = client.get(STORAGE)
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["total"]["count"] >= 2
    assert summary["total"]["bytes"] > 0
    assert any(bucket["kind"] == "daily" and bucket["count"] == 1 for bucket in summary["kinds"])


def test_photo_endpoint_serves_variants_and_404s_unknown(data_http):
    client, _runtime = data_http
    daily = next(item for item in _record(client)["items"] if item["kind"] == "daily")
    capture_id = daily["photos"][0]["capture_id"]
    image = client.get(f"/api/hygiene/admin/data/photo/{capture_id}")
    assert image.status_code == 200, image.text
    assert image.headers["content-type"].startswith("image/")
    assert client.get(f"/api/hygiene/admin/data/photo/{capture_id}?variant=thumb").status_code == 200
    assert client.get("/api/hygiene/admin/data/photo/not-a-capture").status_code == 404
    assert (
        client.get(f"/api/hygiene/admin/data/photo/{capture_id}?variant=huge").status_code
        == 422
    )


def test_delete_endpoint_removes_record_and_resets_todo(data_http):
    client, runtime = data_http
    daily = next(item for item in _record(client)["items"] if item["kind"] == "daily")
    path = f"/api/hygiene/admin/data/records/daily/{daily['record_id']}"
    response = client.delete(path)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["records"] == 1
    assert body["status_reset"] == 1

    inbox = _run(runtime.work.list_daily_work(SUPER))
    assert all(row["status"] == "待拍" for row in inbox)

    assert client.delete(path).status_code == 404
    assert client.delete("/api/hygiene/admin/data/records/fix/1").status_code == 400


def test_purge_endpoint_validates_and_clears_range(data_http):
    client, _runtime = data_http
    bad = client.post(
        "/api/hygiene/admin/data/purge",
        json={"kinds": ["daily"], "date_from": "2026-09-20", "date_to": "2026-09-01"},
    )
    assert bad.status_code == 400

    # 空类型是"没选"，不是"全部"：宁可报错也不能变成清空这一段。
    empty = client.post("/api/hygiene/admin/data/purge", json={"kinds": []})
    assert empty.status_code == 400
    assert client.get(RECORDS).json()["total"] >= 2

    cleared = client.post(
        "/api/hygiene/admin/data/purge",
        json={"kinds": ["daily"], "date_from": "2026-09-13", "date_to": "2026-09-13"},
    )
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body["records"] == 1
    assert body["photos"] == 1
    assert body["files"] >= 1
    remaining = _record(client, "?date_from=2026-09-13&date_to=2026-09-13&kinds=daily")
    assert remaining["total"] == 0


def test_export_job_cycle_downloads_zip(data_http):
    client, _runtime = data_http
    started = client.post(f"{EXPORT_JOBS}?date_from=2026-09-13&date_to=2026-09-13&kinds=daily")
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]

    deadline = time.time() + 30
    state = {}
    while time.time() < deadline:
        state = client.get(f"{EXPORT_JOBS}/{job_id}").json()
        if state["state"] != "running":
            break
        time.sleep(0.05)
    assert state["state"] == "done", state
    assert state["count"] == 1
    assert state["records"] == 1

    download = client.get(f"{EXPORT_JOBS}/{job_id}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = archive.namelist()
        assert "记录.csv" in names
        assert any(name.startswith("照片/日常实拍/") for name in names)
        assert "案板表面" in archive.read("记录.csv").decode("utf-8-sig")

    assert client.get(f"{EXPORT_JOBS}/missing-job").status_code == 404


def test_export_job_rejects_bad_range_before_starting(data_http):
    client, _runtime = data_http
    response = client.post(f"{EXPORT_JOBS}?date_from=2020-01-01&date_to=2026-09-13")
    assert response.status_code == 400


def test_export_job_reports_empty_range(data_http):
    client, _runtime = data_http
    started = client.post(f"{EXPORT_JOBS}?date_from=2026-08-01&date_to=2026-08-02")
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    deadline = time.time() + 30
    state = {}
    while time.time() < deadline:
        state = client.get(f"{EXPORT_JOBS}/{job_id}").json()
        if state["state"] != "running":
            break
        time.sleep(0.05)
    assert state["state"] == "failed"
    assert "没有可导出的记录" in state["error"]


def test_daily_queue_by_date_returns_today_pending_only_by_default(data_http):
    client, runtime = data_http
    today = client.get(QUEUE)
    assert today.status_code == 200, today.text
    body = today.json()
    assert body["is_today"] is True
    assert body["date"] == "2026-09-13"
    assert {row["status"] for row in body["items"]} == {"待验收"}

    # 往后推两天再回看：历史那天看得到全部检查项与状态，今天则是新一天的待拍。
    runtime.work._now = lambda: NOW + timedelta(days=2)
    history = client.get(f"{QUEUE}?date=2026-09-13")
    assert history.status_code == 200, history.text
    past = history.json()
    assert past["is_today"] is False
    statuses = {row["status"] for row in past["items"]}
    # 回看列当天全部检查项：交了的还在待验收，没交的是待拍——这才看得出漏没漏。
    self_statuses = statuses
    assert "待验收" in self_statuses
    assert "已通过" not in self_statuses
    assert "待拍" in self_statuses

    fresh = client.get(QUEUE).json()
    assert fresh["is_today"] is True
    assert fresh["date"] != "2026-09-13"
    # 默认队列只回待验收：新的一天还没人交，所以是空的（未拍项只在回看里列）。
    assert fresh["items"] == []

    assert client.get(f"{QUEUE}?date=13-09-2026").status_code == 400


def test_daily_review_and_capture_accept_a_business_date(data_http):
    client, runtime = data_http
    row = client.get(QUEUE).json()["items"][0]
    item_id = row["item_id"]
    shift = row["shift"]

    runtime.work._now = lambda: NOW + timedelta(days=2)
    review = client.get(
        f"/api/hygiene/admin/daily/{item_id}/review", params={"shift": shift, "date": "2026-09-13"}
    )
    assert review.status_code == 200, review.text
    payload = review.json()
    assert payload["business_date"] == "2026-09-13"
    assert payload["historical"] is True
    assert payload["capture_available"] is True

    shot = client.get(
        f"/api/hygiene/admin/daily/{item_id}/capture",
        params={"shift": shift, "date": "2026-09-13", "variant": "preview"},
    )
    assert shot.status_code == 200, shot.text
    assert shot.headers["content-type"].startswith("image/")

    frozen = client.get(
        f"/api/hygiene/admin/daily/{item_id}/frozen-standard",
        params={"shift": shift, "date": "2026-09-13"},
    )
    assert frozen.status_code == 200, frozen.text

    assert (
        client.get(
            f"/api/hygiene/admin/daily/{item_id}/review",
            params={"shift": shift, "date": "not-a-date"},
        ).status_code
        == 400
    )


def test_accept_and_reject_have_no_history_entry(data_http):
    """验收作用于当前营业日：接口不接受营业日参数，历史提交动不了。"""
    client, runtime = data_http
    row = client.get(QUEUE).json()["items"][0]
    runtime.work._now = lambda: NOW + timedelta(days=2)
    response = client.post(
        f"/api/hygiene/admin/daily/{row['item_id']}/accept",
        json={"shift": row["shift"], "date": "2026-09-13"},
    )
    # 那一天的实例今天不存在，所以是"没有待验收的实拍"，而不是把 09-13 改成了已通过。
    assert response.status_code == 400
    assert "没有待验收" in response.json()["detail"]
    history = client.get(f"{QUEUE}?date=2026-09-13").json()
    statuses = {entry["status"] for entry in history["items"]}
    assert "待验收" in statuses
    assert "已通过" not in statuses
