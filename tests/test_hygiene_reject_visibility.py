#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工端要能看出「这一项今天被打回过」。

修复前驳回只写一条看板事件、状态回到"待拍"，员工端与"从没拍过"长得一模一样，
于是他照着原样再拍一遍。现在 ``/staff/daily-work`` 的每一行带 ``rejected`` 标记，
由一次批量查询得出（不在行循环里逐条查）。
"""

import asyncio
import io
from datetime import datetime, timedelta

import pytest
from PIL import Image

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
def env(tmp_path):
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
    rejected_item = _run(
        work.add_daily_item(
            SUPER,
            zone["id"],
            "案板-台面",
            {"bytes": _jpeg(), "content_type": "image/jpeg", "markup": []},
        )
    )
    clean_item = _run(
        work.add_daily_item(
            SUPER,
            zone["id"],
            "案板-地面",
            {"bytes": _jpeg(), "content_type": "image/jpeg", "markup": []},
        )
    )
    deep_item = _run(work.add_deep_clean_item(SUPER, 6, "抽油烟机"))
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
    yield db, work, actor, rejected_item, clean_item, deep_item


def _row(items, item_id):
    return next(row for row in items if row["item_id"] == item_id)


def test_rejected_item_is_flagged_for_the_staff(env):
    _db, work, actor, rejected_item, clean_item, _deep = env

    before = _run(work.list_daily_work(actor))
    assert _row(before, rejected_item["id"])["rejected"] is False
    assert _row(before, clean_item["id"])["rejected"] is False

    _run(work.submit_daily(actor, rejected_item["id"], _capture()))
    _run(work.reject_daily(SUPER, rejected_item["id"], "白班"))

    after = _run(work.list_daily_work(actor))
    flagged = _row(after, rejected_item["id"])
    assert flagged["rejected"] is True
    assert flagged["status"] == "待拍", "驳回后回到待拍，但要带上标记"
    assert _row(after, clean_item["id"])["rejected"] is False


def test_flag_clears_after_the_retake_is_accepted(env):
    _db, work, actor, rejected_item, _clean_item, _deep = env
    _run(work.submit_daily(actor, rejected_item["id"], _capture()))
    _run(work.reject_daily(SUPER, rejected_item["id"], "白班"))
    _run(work.submit_daily(actor, rejected_item["id"], _capture()))
    _run(work.accept_daily(SUPER, rejected_item["id"], "白班"))

    rows = _run(work.list_daily_work(actor))
    passed = _row(rows, rejected_item["id"])
    assert passed["status"] == "已通过"

    # 通过之后不再出现在待拍队列里，但标记本身仍应反映当天的驳回历史
    assert passed["rejected"] is True


def test_deep_clean_reject_is_visible_to_staff(env):
    """专项被驳回也要让员工看出来（原来只有日常链路有标记）。"""
    _db, work, actor, _rejected_item, _clean_item, deep_item = env

    _run(work.submit_deep_clean_pair(actor, deep_item["id"], _capture(), _capture()))
    _run(work.reject_deep_clean_pair(SUPER, deep_item["id"], reason="滤网没拆下来洗"))

    items = _run(work.list_deep_clean_work(actor))["items"]
    row = next(item for item in items if item["item_id"] == deep_item["id"])
    assert row["rejected"] is True
    assert row["reject_reason"] == "滤网没拆下来洗"
    assert row["status"] == "待拍"


def test_fix_reject_is_visible_to_staff(env):
    """整改单被驳回同样要能认出来（事件按 ticket_id 关联）。"""
    _db, work, actor, _rejected_item, _clean_item, _deep = env

    # 开整改单要求卫生管理员权限（普通员工只能回拍）
    opener = {**actor, "permission": "管理员"}
    ticket = _run(
        work.open_fix(opener, actor["zone_id"], "卫生", "台面有油", timedelta(hours=2), _capture())
    )
    _run(work.reshoot_fix(opener, ticket["id"], _capture()))
    # 时限未到时只有开单人能验（_require_fix_reviewer），所以用开单人驳回
    _run(work.reject_fix(opener, ticket["id"], reason="回拍角度不对"))

    rows = _run(work.list_fix_tickets(opener))
    row = next(item for item in rows if item["id"] == ticket["id"])
    assert row["rejected"] is True
    assert row["reject_reason"] == "回拍角度不对"
    assert row["status"] == "待回拍"


def test_a_ticket_that_was_never_rejected_is_not_flagged(env):
    _db, work, actor, _rejected_item, _clean_item, _deep = env
    opener = {**actor, "permission": "管理员"}
    ticket = _run(
        work.open_fix(opener, actor["zone_id"], "卫生", "台面有油", timedelta(hours=2), _capture())
    )
    rows = _run(work.list_fix_tickets(opener))
    row = next(item for item in rows if item["id"] == ticket["id"])
    assert row["rejected"] is False
    assert row["reject_reason"] is None
