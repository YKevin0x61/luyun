#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驳回要带原因，且三类驳回都要进看板。

审查报告 §4.4 的剩余部分：
* 员工端只知道"被驳回了"，不知道哪里不合格 → 只能原样重拍。原因落在
  ``hygiene_board_events.reason``，随 ``/staff/daily-work`` 一起下发。
* 专项与整改的驳回**此前一条事件都不写**：红黑榜的「驳回」只统计得到日常。
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
from services.hygiene.work import (
    EVENT_REJECT,
    MAX_REJECT_REASON_LENGTH,
    HygieneWork,
)

SUPER = {"kind": "super"}
PHONE = "13800138000"
PASSWORD = "password123"
CLOCK = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)


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
    return {"bytes": _jpeg(), "content_type": "image/jpeg", "live": True, "markup": []}


@pytest.fixture
def env(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: CLOCK)
    work = HygieneWork(
        db,
        captures=FakeCaptureStore(),
        now=lambda: CLOCK,
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    zone = _run(work.list_zones())[0]
    daily = _run(
        work.add_daily_item(
            SUPER,
            zone["id"],
            "案板-台面",
            {"bytes": _jpeg(), "content_type": "image/jpeg", "markup": []},
        )
    )
    deep = _run(work.add_deep_clean_item(SUPER, 6, "抽油烟机"))
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
    yield db, work, daily, deep, actor


def _reject_events(db):
    cur = _run(
        db._conn.execute(
            "SELECT COUNT(*) AS n FROM hygiene_board_events WHERE event_type = ?",
            (EVENT_REJECT,),
        )
    )
    return int(dict(_run(cur.fetchone()))["n"])


def test_reject_reason_reaches_the_staff_inbox(env):
    _db, work, daily, _deep, actor = env
    _run(work.submit_daily(actor, daily["id"], _capture()))
    _run(work.reject_daily(SUPER, daily["id"], "白班", reason="  台面还有油渍  "))

    row = next(
        item for item in _run(work.list_daily_work(actor)) if item["item_id"] == daily["id"]
    )
    assert row["rejected"] is True
    assert row["reject_reason"] == "台面还有油渍", "两端空白要去掉"


def test_a_reject_without_reason_leaves_it_empty(env):
    _db, work, daily, _deep, actor = env
    _run(work.submit_daily(actor, daily["id"], _capture()))
    _run(work.reject_daily(SUPER, daily["id"], "白班"))

    row = next(
        item for item in _run(work.list_daily_work(actor)) if item["item_id"] == daily["id"]
    )
    assert row["rejected"] is True
    assert row["reject_reason"] is None


def test_reason_is_trimmed_to_a_sane_length(env):
    _db, work, daily, _deep, actor = env
    _run(work.submit_daily(actor, daily["id"], _capture()))
    _run(work.reject_daily(SUPER, daily["id"], "白班", reason="啰" * 500))

    row = next(
        item for item in _run(work.list_daily_work(actor)) if item["item_id"] == daily["id"]
    )
    assert len(row["reject_reason"]) == MAX_REJECT_REASON_LENGTH


def test_deep_clean_reject_now_records_a_board_event(env):
    _db, work, _daily, deep, actor = env
    _run(work.submit_deep_clean_pair(actor, deep["id"], _capture(), _capture()))
    assert _reject_events(_db) == 0

    _run(work.reject_deep_clean_pair(SUPER, deep["id"], reason="滤网没拆"))
    assert _reject_events(_db) == 1, "专项驳回也要进看板"

    boards = _run(work.list_boards())
    people = [row for row in boards["people"] if row["employee_id"] == actor["id"]]
    assert people and people[0]["驳回"] == 1


def test_latest_reason_wins_when_rejected_twice(env):
    _db, work, daily, _deep, actor = env
    _run(work.submit_daily(actor, daily["id"], _capture()))
    _run(work.reject_daily(SUPER, daily["id"], "白班", reason="第一次：有油"))
    _run(work.submit_daily(actor, daily["id"], _capture()))
    _run(work.reject_daily(SUPER, daily["id"], "白班", reason="第二次：角落没擦"))

    row = next(
        item for item in _run(work.list_daily_work(actor)) if item["item_id"] == daily["id"]
    )
    assert row["reject_reason"] == "第二次：角落没擦", "显示最近一次的原因"


def test_missing_reason_column_degrades_instead_of_failing(env):
    """PG 既有库没跑 0002 时的降级：驳回照常工作，只是不带原因文字。

    SQLite 侧列一定存在，所以这里直接把探测结果钉成"缺列"，模拟 PG 场景。
    """
    db, work, daily, _deep, actor = env
    work._has_event_reason = False

    _run(work.submit_daily(actor, daily["id"], _capture()))
    _run(work.reject_daily(SUPER, daily["id"], "白班", reason="这条不该落库"))

    row = next(
        item for item in _run(work.list_daily_work(actor)) if item["item_id"] == daily["id"]
    )
    assert row["rejected"] is True, "缺列不影响是否被驳回"
    assert row["reject_reason"] is None

    # 事件本身仍然写进去了（只是没有 reason 列的值）
    assert _reject_events(db) == 1

    # 而且没往 reason 列写东西
    cur = _run(
        db._conn.execute(
            "SELECT COUNT(*) AS n FROM hygiene_board_events "
            "WHERE event_type = ? AND reason IS NOT NULL",
            (EVENT_REJECT,),
        )
    )
    assert int(dict(_run(cur.fetchone()))["n"]) == 0


def test_the_write_path_never_probes_the_schema(env):
    """写路径不能触发列探测。

    PG 缺列时探测必须 rollback，而它跑在驳回事务的中途（状态 UPDATE 之后），
    那次 rollback 会把状态更新一起回滚掉——结果是"记了一笔驳回事件、状态却没变"。
    所以探测只允许发生在 prepare()（无事务）或只读路径。
    """
    db, work, daily, _deep, actor = env
    _run(work.submit_daily(actor, daily["id"], _capture()))

    work._has_event_reason = None  # 模拟"还没探过"
    probes = []

    async def spy():
        probes.append(1)
        return False

    work._event_reason_supported = spy
    _run(work.reject_daily(SUPER, daily["id"], "白班", reason="有油"))

    assert probes == [], "驳回过程中不应探测 schema"
    # 状态确实回退了（没有被那次 rollback 吞掉）
    row = next(
        item for item in _run(work.list_daily_work(actor)) if item["item_id"] == daily["id"]
    )
    assert row["status"] == "待拍"
    assert _reject_events(db) == 1


def test_reason_still_works_when_only_0002_was_applied(env):
    """只应用了 0002（有 reason、没有 ticket_id）时，驳回原因不该跟着降级。

    两列分开探测就是为了这个：否则用户只跑了一半迁移，已有的功能会一起消失。
    """
    db, work, daily, _deep, actor = env
    work._has_event_reason = True     # 0002 已应用
    work._has_event_ticket = False    # 0003 还没应用

    _run(work.submit_daily(actor, daily["id"], _capture()))
    _run(work.reject_daily(SUPER, daily["id"], "白班", reason="台面还有油"))

    row = next(
        item for item in _run(work.list_daily_work(actor)) if item["item_id"] == daily["id"]
    )
    assert row["rejected"] is True
    assert row["reject_reason"] == "台面还有油", "reason 列在就该给原因"
