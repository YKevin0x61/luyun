#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原图路径与驳回判据必须有索引可用。

审查报告 §3.3：``_original_capture_meta`` 用 6 路 ``UNION ALL`` 按 capture_id 反查
来源，而这几张表都没有 capture_id 索引——EXPLAIN 显示 ``SCAN``。而所有图片端点默认
``variant="original"``，等于每个图片请求都做 6 次全表扫描，随提交量线性恶化
（100 个检查项时每天新增约 200 行提交）。

同一个文件里还有两条：``_daily_was_rejected``（accept_daily 每次都跑）与
``list_fix_tickets``（``status != 已通过`` + ``ORDER BY id``）。
"""

import asyncio
from datetime import datetime

import pytest

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork

EXPECTED_INDEXES = {
    "idx_hygiene_standards_capture": "hygiene_standards",
    "idx_hygiene_daily_submissions_capture": "hygiene_daily_submissions",
    "idx_hygiene_deep_clean_sub_before": "hygiene_deep_clean_submissions",
    "idx_hygiene_deep_clean_sub_after": "hygiene_deep_clean_submissions",
    "idx_hygiene_fix_tickets_capture": "hygiene_fix_tickets",
    "idx_hygiene_fix_reshoots_capture": "hygiene_fix_reshoots",
    "idx_hygiene_board_events_reject": "hygiene_board_events",
}


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture
def db(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    manager = DatabaseManager()
    _run(manager.connect())
    set_runtime(AppRuntime(db=manager))
    yield manager
    _run(manager.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _plan(db, sql, params=()):
    cur = _run(db._conn.execute("EXPLAIN QUERY PLAN " + sql, params))
    rows = [dict(row) for row in _run(cur.fetchall())]
    return " | ".join(str(row.get("detail", "")) for row in rows)


def test_hardening_indexes_are_created(db):
    for name, table in EXPECTED_INDEXES.items():
        cur = _run(
            db._conn.execute(
                "SELECT tbl_name FROM sqlite_master WHERE type = 'index' AND name = ?",
                (name,),
            )
        )
        row = _run(cur.fetchone())
        assert row is not None, f"{name} 没有建立"
        assert dict(row)["tbl_name"] == table


def test_original_capture_lookup_uses_indexes_not_scans(db):
    """原图反查的六路 UNION ALL 不能出现全表扫描。"""
    sql = """
        SELECT capture_id FROM hygiene_standards WHERE capture_id = ?
        UNION ALL
        SELECT capture_id FROM hygiene_daily_submissions WHERE capture_id = ?
        UNION ALL
        SELECT before_capture_id FROM hygiene_deep_clean_submissions WHERE before_capture_id = ?
        UNION ALL
        SELECT after_capture_id FROM hygiene_deep_clean_submissions WHERE after_capture_id = ?
        UNION ALL
        SELECT capture_id FROM hygiene_fix_tickets WHERE capture_id = ?
        UNION ALL
        SELECT capture_id FROM hygiene_fix_reshoots WHERE capture_id = ?
        LIMIT 1
    """
    plan = _plan(db, sql, ("x",) * 6).upper()
    assert "SCAN" not in plan, f"仍有全表扫描：{plan}"
    assert plan.count("SEARCH") >= 6


def test_reject_lookup_does_not_scan(db):
    reject_plan = _plan(
        db,
        """SELECT 1 FROM hygiene_board_events
           WHERE board = ? AND event_type = ? AND item_id = ?
             AND shift = ? AND business_date = ? LIMIT 1""",
        ("person", "驳回", 1, "白班", "2026-09-13"),
    ).upper()
    assert plan_uses_index(reject_plan), f"驳回判据仍在扫描：{reject_plan}"


def plan_uses_index(plan: str) -> bool:
    return "SEARCH" in plan or "COVERING INDEX" in plan


def test_fix_ticket_listing_still_scans_on_purpose(db):
    """记录一个已知取舍：`status != ?` 是不等条件，SQLite 不会走索引。

    给它硬加一条 `(status, id)` 索引只会增加写入成本而永远不被选中（实测
    EXPLAIN 仍是 SCAN）。真要提速得把查询改成正面枚举状态，那是业务改动。
    """
    fix_plan = _plan(
        db,
        "SELECT id FROM hygiene_fix_tickets WHERE status != ? ORDER BY id ASC",
        ("已通过",),
    ).upper()
    assert "SCAN" in fix_plan
    cur = _run(
        db._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_hygiene_fix_tickets_status_id",),
        )
    )
    assert _run(cur.fetchone()) is None, "不要为不等条件建永远不被选中的索引"


def test_hardening_indexes_land_on_a_prepared_worker(db):
    """work.prepare() 之后的库同样带索引（门店真实的启动路径）。"""
    work = HygieneWork(
        db,
        captures=FakeCaptureStore(),
        now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    cur = _run(
        db._conn.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_hygiene%'"
        )
    )
    assert int(dict(_run(cur.fetchone()))["n"]) >= len(EXPECTED_INDEXES)
