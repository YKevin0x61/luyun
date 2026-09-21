#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待验收状态下的重复提交按「重传」处理，不虚增红黑榜的实拍次数。

前端在弱网下会重试同一张照片（上传成功但响应丢了、或者员工看到失败又点了一次），
而 ``hygiene_daily_submissions`` 没有 instance 唯一约束——修复前每次提交都新增一行
并追加一条「实拍」看板事件，红黑榜的次数因此平白多出来。

现在：实例处于"待验收"时再次提交＝覆盖原来那条 pending 提交（不新增行、不记事件）；
被打回后（实例回到"待拍"）再交属于新一次实拍，仍然保留历史。
"""

import asyncio
import io
from datetime import datetime

import pytest
from PIL import Image

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import EVENT_CAPTURE, HygieneWork, HygieneWorkError

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


def _jpeg(tint=200):
    output = io.BytesIO()
    Image.new("RGB", (80, 60), (120, 180, tint)).save(output, format="JPEG")
    return output.getvalue()


def _capture(tint=200):
    return {
        "bytes": _jpeg(tint),
        "content_type": "image/jpeg",
        "live": True,
        "markup": [],
    }


@pytest.fixture
def work_env(tmp_path):
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
    _run(accounts.pick_assignment(employee["id"], "白班", zone["id"]))
    actor = {
        "kind": "staff",
        "id": employee["id"],
        "permission": "普通员工",
        "name": "张三",
        "phone": PHONE,
        "shift": "白班",
        "zone_id": zone["id"],
    }
    yield db, work, item, actor, accounts
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _count(db, sql, params=()):
    cur = _run(db._conn.execute(sql, params))
    return int(dict(_run(cur.fetchone()))["n"])


def _submissions(db):
    return _count(db, "SELECT COUNT(*) AS n FROM hygiene_daily_submissions")


def _capture_events(db):
    return _count(
        db,
        "SELECT COUNT(*) AS n FROM hygiene_board_events WHERE event_type = ?",
        (EVENT_CAPTURE,),
    )


def test_resubmit_while_pending_replaces_instead_of_duplicating(work_env):
    db, work, item, actor, _accounts = work_env
    first = _run(work.submit_daily(actor, item["id"], _capture(180)))
    second = _run(work.submit_daily(actor, item["id"], _capture(240)))

    assert first["capture_id"] != second["capture_id"]
    assert _submissions(db) == 1, "重传不该新增提交行"
    assert _capture_events(db) == 1, "重传不该多记一次实拍"

    cur = _run(
        db._conn.execute(
            "SELECT capture_id FROM hygiene_daily_submissions ORDER BY id ASC"
        )
    )
    stored = dict(_run(cur.fetchone()))["capture_id"]
    assert stored == second["capture_id"], "留下的是最新那张"

    cur = _run(
        db._conn.execute(
            "SELECT pending_submission_id FROM hygiene_daily_instances"
        )
    )
    assert dict(_run(cur.fetchone()))["pending_submission_id"] is not None


def test_resubmit_after_reject_is_a_new_capture(work_env):
    db, work, item, actor, _accounts = work_env
    _run(work.submit_daily(actor, item["id"], _capture(180)))
    _run(work.reject_daily(SUPER, item["id"], "白班"))
    _run(work.submit_daily(actor, item["id"], _capture(240)))

    assert _submissions(db) == 2, "打回后重拍要保留历次记录"
    assert _capture_events(db) == 2


def test_resubmit_after_accept_is_refused(work_env):
    _db, work, item, actor, _accounts = work_env
    _run(work.submit_daily(actor, item["id"], _capture()))
    _run(work.accept_daily(SUPER, item["id"], "白班"))

    with pytest.raises(HygieneWorkError) as raised:
        _run(work.submit_daily(actor, item["id"], _capture()))
    assert raised.value.code == "already_accepted"


def test_resubmit_does_not_overwrite_a_colleagues_capture(work_env):
    """同事交的待验收不能被顶掉。

    换区只留痕、不阻断（§1.1 的既定决策），所以"同一项被别人交过"是正常情形。
    覆盖分支只能吃本人的重传；别人的提交要按新一次实拍记，否则管理员看到的证据
    与提交人会无声换人。
    """
    db, work, item, actor_a, accounts = work_env
    first = _run(work.submit_daily(actor_a, item["id"], _capture(180)))

    colleague = _run(accounts.register("13900139000", PASSWORD, "李四"))
    _run(accounts.approve(colleague["id"]))
    _run(accounts.pick_assignment(colleague["id"], "白班", actor_a["zone_id"]))
    actor_b = {
        "kind": "staff",
        "id": colleague["id"],
        "permission": "普通员工",
        "name": "李四",
        "phone": "13900139000",
        "shift": "白班",
        "zone_id": actor_a["zone_id"],
    }
    _run(work.submit_daily(actor_b, item["id"], _capture(240)))

    assert _submissions(db) == 2, "别人的提交要新增一行，不能覆盖"
    assert _capture_events(db) == 2

    cur = _run(
        db._conn.execute(
            "SELECT capture_id, submitter_id FROM hygiene_daily_submissions ORDER BY id ASC"
        )
    )
    rows = [dict(row) for row in _run(cur.fetchall())]
    assert rows[0]["capture_id"] == first["capture_id"], "先交的那张必须还在"
    assert int(rows[0]["submitter_id"]) == int(actor_a["id"])
    assert int(rows[1]["submitter_id"]) == int(colleague["id"])
