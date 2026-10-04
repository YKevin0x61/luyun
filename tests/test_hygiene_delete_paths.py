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

票 10 之后：员工当天自选班次/工作区的入口撤了（``POST /api/hygiene/staff/assignment``
一律 403），``hygiene_shift_picks`` 只剩**存量数据**这一个来源 —— 老库升级上来的行还在
那张表里，``delete_zone`` 的解绑分支（先 ``zone_id = NULL`` 再删区）保护的就是它们。
所以第 1 条用例的前置数据改成「排班配今天的分工 + 直接种一行存量自选」。
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
from services.hygiene.accounts import EmployeeAccounts, hygiene_business_date
from services.hygiene.captures import FakeCaptureStore
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
        db, now=lambda: FIXED_NOW
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


def _seed_shift_pick(db, employee_id, zone_id):
    """种一行**存量**的当日自选（``hygiene_shift_picks``）。

    票 10 撤掉了员工当天自选的入口（``POST /api/hygiene/staff/assignment`` 现在 403），
    这张表没有再写入的路径 —— 老库升级上来的人当天选过哪个区就还留在里面，`delete_zone`
    的解绑分支保护的正是这些行。所以前置数据只能直接种，日期/班次按夹具那个固定时刻走。
    """
    stamp = FIXED_NOW.isoformat()
    _run(
        db._conn.execute(
            """INSERT INTO hygiene_shift_picks
               (employee_id, business_date, shift, zone_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                int(employee_id),
                hygiene_business_date(FIXED_NOW),
                "白班",
                int(zone_id),
                stamp,
                stamp,
            ),
        )
    )
    _run(db._conn.commit())


def test_delete_zone_with_picked_assignment_succeeds(delete_http):
    client, db, _accounts, work, employee = delete_http
    zone = _run(work.list_zones())[0]
    _login(client)
    # 票 10：今天在哪个区由排班给（自选入口已 403）。这一行排班指着这个区，删区时它还在
    # （`staff_assignments.zone_id` 没有外键），删除不该因此失败。
    _run(
        assign_duty(
            db,
            employee["id"],
            slot="day",
            zone_id=zone["id"],
            now=FIXED_NOW,
        )
    )
    # 存量自选行：`hygiene_shift_picks.zone_id` 那条外键（老库）要求删区前先解绑。
    _seed_shift_pick(db, employee["id"], zone["id"])
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
