#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删除路径不能 500：子行按依赖顺序清干净，兜底给 409。

两个必现故障（审查报告 §2.1）：
1. ``delete_zone``：``hygiene_shift_picks.zone_id`` 有外键指向 ``hygiene_zones``，
   只要当天有人选过这个区，删区就 IntegrityError。现场老库缺这条外键，所以只有
   从源码新建的库会踩到——新门店/CI/开发环境一删就 500。
2. ``remove_deep_clean_item``：专项项被提交过一次就有 instance 行，外键指向 item，
   直接删 item 必炸。

另外锁住：删检查项要连带清掉引用它的卫生教材行（否则教材页 404，且 capture
文件因为"仍被引用"永远不回收）。
"""

import asyncio
import io
import sqlite3
from datetime import datetime
from unittest import mock

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
    return {
        "bytes": _jpeg(),
        "content_type": "image/jpeg",
        "live": True,
        "markup": [],
    }


@pytest.fixture
def delete_http(tmp_path):
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
    employee = _run(accounts.register(PHONE, PASSWORD, "张三"))
    _run(accounts.approve(employee["id"]))
    app = FastAPI()
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    app.dependency_overrides[hygiene_module.require_session] = lambda: "test-admin"
    with TestClient(app) as client:
        yield client, db, accounts, work, employee
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _login(client):
    response = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    )
    assert response.status_code == 200


def _count(db, sql, params=()):
    cur = _run(db._conn.execute(sql, params))
    return int(dict(_run(cur.fetchone()))["n"])


def test_delete_zone_with_picked_assignment_succeeds(delete_http):
    client, db, _accounts, work, employee = delete_http
    zone = _run(work.list_zones())[0]
    _login(client)
    assert client.post(
        "/api/hygiene/staff/assignment",
        json={"shift": "白班", "zone_id": zone["id"]},
    ).status_code == 200
    assert _count(
        db,
        "SELECT COUNT(*) AS n FROM hygiene_shift_picks WHERE zone_id = ?",
        (zone["id"],),
    ) == 1

    response = client.request("DELETE", f"/api/hygiene/admin/zones/{zone['id']}")
    assert response.status_code == 200, response.text
    assert _count(db, "SELECT COUNT(*) AS n FROM hygiene_zones WHERE id = ?", (zone["id"],)) == 0
    # 班次记录保留，只是解绑区域（当天的选择作废）
    assert _count(
        db,
        "SELECT COUNT(*) AS n FROM hygiene_shift_picks WHERE zone_id = ?",
        (zone["id"],),
    ) == 0
    assert _count(db, "SELECT COUNT(*) AS n FROM hygiene_shift_picks") == 1


def test_remove_deep_clean_item_that_was_submitted_succeeds(delete_http):
    client, db, _accounts, work, employee = delete_http
    item = _run(work.add_deep_clean_item(SUPER, 6, "抽油烟机深度清洗"))
    actor = {
        "kind": "staff",
        "id": employee["id"],
        "permission": "普通员工",
        "name": "张三",
        "phone": PHONE,
        "shift": "白班",
        "zone_id": _run(work.list_zones())[0]["id"],
    }
    _run(work.submit_deep_clean_pair(actor, item["id"], _capture(), _capture()))
    assert _count(
        db,
        "SELECT COUNT(*) AS n FROM hygiene_deep_clean_instances WHERE item_id = ?",
        (item["id"],),
    ) == 1

    response = client.delete(f"/api/hygiene/admin/deep-clean/items/{item['id']}")
    assert response.status_code == 200, response.text
    assert _count(
        db,
        "SELECT COUNT(*) AS n FROM hygiene_deep_clean_instances WHERE item_id = ?",
        (item["id"],),
    ) == 0
    assert _count(db, "SELECT COUNT(*) AS n FROM hygiene_deep_clean_submissions") == 0


def test_delete_daily_item_drops_teaching_examples(delete_http):
    client, db, _accounts, work, _employee = delete_http
    zone = _run(work.list_zones())[0]
    item = _run(work.add_daily_item(SUPER, zone["id"], "案板-台面", _capture()))
    now = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ).isoformat()
    _run(
        db._conn.execute(
            """INSERT INTO hygiene_teaching_examples
               (kind, title, left_label, right_label, left_capture_id, right_capture_id,
                left_content_type, right_content_type, left_markup_json, item_id, shift, created_at)
               VALUES ('daily', '案板对照', '标准', '实拍', 'a', 'b',
                       'image/jpeg', 'image/jpeg', '[]', ?, '白班', ?)""",
            (item["id"], now),
        )
    )
    _run(db._conn.commit())
    assert _count(db, "SELECT COUNT(*) AS n FROM hygiene_teaching_examples") == 1

    response = client.delete(f"/api/hygiene/admin/items/{item['id']}")
    assert response.status_code == 200, response.text
    assert _count(db, "SELECT COUNT(*) AS n FROM hygiene_teaching_examples") == 0


def test_unclean_reference_falls_back_to_409_not_500(delete_http):
    client, _db, _accounts, work, _employee = delete_http
    zone = _run(work.list_zones())[0]
    boom = mock.AsyncMock(side_effect=sqlite3.IntegrityError("FOREIGN KEY constraint failed"))
    with mock.patch.object(work, "delete_zone", boom):
        response = client.request("DELETE", f"/api/hygiene/admin/zones/{zone['id']}")
    assert response.status_code == 409, response.text
    assert "关联数据" in response.json()["detail"]
