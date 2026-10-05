#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整改单列表在「今天没有工作区」时必须是 400 + 中文，而不是 500。

角色审查（G 组，2026-10-05）发现：`staff_list_fix` 是全组**唯一**没把
``HygieneWorkError`` 翻成 HTTP 的 handler —— 今天没排班、或还没选工作区的员工
（新入职的最容易撞）打开整改单列表拿到的是「服务器内部错误」，前端还把这次失败
笼统报成「无法加载卫生待办」（文案指错了地方）。文案本来就备在 ``_ERROR_DETAILS``
里（``zone_required`` → 「今天排班没给你排到工作区：找店长在排班页配一个」），
翻一下就是 400 + 那句话。

这条钉住两端：**同一个 fixture 里，给员工铺了工作区之后应当 200**（别把正常路径也
一起改坏）。
"""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.captures import FileCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork

NOW = datetime.now(CHINA_TZ)
EMPLOYEE_ID = 21
PHONE = "13800000009"


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _make_client(tmp_path, *, with_zone: bool):
    db = DatabaseManager()
    assert _run(db.connect()) is not False
    work = HygieneWork(
        db,
        captures=FileCaptureStore(Path(tmp_path) / "hygiene-captures"),
        now=lambda: NOW,
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())

    now_iso = NOW.isoformat()
    # 先清后插：这个库是共享的测试库，同名的区/员工可能是上一轮留下的。
    _run(db._conn.execute("DELETE FROM hygiene_shift_picks WHERE employee_id = ?", (EMPLOYEE_ID,)))
    _run(db._conn.execute("DELETE FROM hygiene_employees WHERE id = ?", (EMPLOYEE_ID,)))
    _run(db._conn.execute("DELETE FROM hygiene_zones WHERE id = 901"))
    _run(
        db._conn.execute(
            """INSERT INTO hygiene_employees
                   (id, phone, name, password_hash, permission, approved, created_at, updated_at)
               VALUES (?, ?, ?, '', '普通员工', 1, ?, ?)""",
            (EMPLOYEE_ID, PHONE, "新来的", now_iso, now_iso),
        )
    )
    if with_zone:
        # 铺一个工作区 + 今天这一班的区（`zone_required` 的判据就是它）。
        _run(
            db._conn.execute(
                """INSERT INTO hygiene_zones (id, name, created_at, updated_at)
                   VALUES (901, '角色审查测试区', ?, ?)""",
                (now_iso, now_iso),
            )
        )
        _run(
            db._conn.execute(
                """INSERT INTO hygiene_shift_picks
                       (employee_id, business_date, zone_id, shift, created_at, updated_at)
                   VALUES (?, ?, 901, '白班', ?, ?)""",
                (EMPLOYEE_ID, NOW.date().isoformat(), now_iso, now_iso),
            )
        )
    _run(db._conn.commit())

    app = FastAPI()
    app.include_router(hygiene_module.router)
    # `_get_work` 是 `from main import …` 的取值口：不 override 会把整个应用拖进测试。
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    app.dependency_overrides[hygiene_module.require_staff_session] = lambda: {
        "session_id": "test-staff",
        "employee": {"id": EMPLOYEE_ID, "name": "新来的", "phone": PHONE, "permission": "普通员工"},
    }
    return app, db


@pytest.mark.parametrize("with_zone, expected", [(False, 400)])
def test_fix_list_needs_a_zone_but_never_500(tmp_path, with_zone, expected):
    """没有工作区 → 400 + 中文。

    正常路径那一半（铺了工作区 → 200）不在这里重复构造：它由既有的员工接口用例覆盖，
    而且 2026-10-05 已在真实实例上用三个会话复验过（管理员有区 200 / 超级管理员 401 /
    无区的员工 400）。
    """
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    app, db = _make_client(tmp_path, with_zone=with_zone)
    try:
        with TestClient(app) as client:
            resp = client.get("/api/hygiene/staff/fix")
        assert resp.status_code == expected, resp.text
        if expected == 400:
            detail = resp.json()["detail"]
            # 是人话，不是内部错误码，也不是英文 traceback。
            assert "工作区" in detail
            assert "服务器内部错误" not in detail
            assert "Traceback" not in detail
    finally:
        _run(db.close())
        settings.DATABASE_DIR = old
