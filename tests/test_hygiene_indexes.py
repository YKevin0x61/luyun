#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原图路径与驳回判据必须有索引可用。

审查报告 §3.3：``_original_capture_meta`` 用 6 路 ``UNION ALL`` 按 capture_id 反查
来源，而这几张表都没有 capture_id 索引——计划里是全表扫描。而所有图片端点默认
``variant="original"``，等于每个图片请求都做 6 次全表扫描，随提交量线性恶化
（100 个检查项时每天新增约 200 行提交）。

同一个文件里还有两条：``_daily_was_rejected``（accept_daily 每次都跑）与
``list_fix_tickets``（``status != 已通过`` + ``ORDER BY id``）。

────────────────────────────────────────────────────────────────────────
PostgreSQL 与 SQLite 的语义差异（本文件原按 SQLite 写，ADR 0089 后改成 PG）
────────────────────────────────────────────────────────────────────────
1. ``EXPLAIN QUERY PLAN`` 是 SQLite 语法，PG 报 ``syntax error at or near
   "QUERY"``；PG 用 ``EXPLAIN``，返回的是**每行一列的文本计划**。
2. PG 没有 ``sqlite_master``，索引存在性查 ``pg_indexes``。
3. **计划断言必须先把 seq scan 摁下去。** 测试库每个用例都 ``TRUNCATE``，表是空的，
   而 PG 的优化器在这种规模下会理直气壮地选 ``Seq Scan``：实测 ``ANALYZE`` 之后
   0 行统计下，连 ``hygiene_board_events`` 那条五列全等值的覆盖索引也输给了
   Seq Scan——所以「计划里出现 Index Scan」这种写法在测试库里会假红。本文件取计划
   前 ``SET enable_seqscan = off``：这个 GUC 只**劝退**、不禁止，谓词上真的没有
   可用索引时 PG 仍会退回 ``Seq Scan``（实测 cost=10000000000），因此
   「计划里没有 Seq Scan + 出现预期的索引名」等价于「这个谓词确实有索引可用」，
   既不会假红，也没有放宽成永真。
4. 反过来也有个陷阱：「空表下计划里出现 Seq Scan」在 PG 上不携带任何信息
   （空表上任何查询都是 Seq Scan），所以 ``list_fix_tickets`` 那条用例不再断言
   计划，只保留与优化器无关、原先就有的那一半：没有为不等条件建专用索引。
5. ``LIKE 'idx_hygiene%'`` 里的 ``_`` 在 PG 是单字符通配符，前缀匹配要用
   ``starts_with()``。

计划探针走 ``db._conn.raw``：``db._conn.execute("EXPLAIN …")`` 在方言层不算
``SELECT``/``WITH``，会落到 ``raw.execute`` 只拿回 command tag，``fetchall()``
恒为空（实测 rowcount=-1）。这条路径不过方言层，所以 SQL 里写 PG 原生的
``$1..$n`` 占位符，而不是项目惯例的 ``?``。
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

# 计划里最贵的那种节点：出现它就说明这个谓词没有索引可用。
SEQ_SCAN = "Seq Scan"


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture
def db(tmp_path):
    """业务库连接。

    PG 后端下没有「临时目录里的一个库文件」：DSN 由 ``tests/conftest.py`` 钉在
    ``luyun_test``，``DATABASE_DIR`` 只管照片/凭据这类文件数据。
    """
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    manager = DatabaseManager()
    _run(manager.connect())
    set_runtime(AppRuntime(db=manager))
    yield manager
    _run(manager.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _index_table(db, name):
    """索引建在哪张表上；不存在返回 None（``sqlite_master`` → ``pg_indexes``）。"""
    cur = _run(
        db._conn.execute(
            "SELECT tablename FROM pg_indexes "
            "WHERE schemaname = 'public' AND indexname = ?",
            (name,),
        )
    )
    row = _run(cur.fetchone())
    return None if row is None else row[0]


def _plan(db, sql, params=(), *, force_index=True):
    """``EXPLAIN`` 的文本计划（PG 版 ``EXPLAIN QUERY PLAN``）。

    ``force_index`` 取计划前打开 ``enable_seqscan = off``：见文件头的第 3 条。
    GUC 是会话级的，用完在 finally 里还原，不影响同一连接上的其它断言。
    """

    async def _explain() -> str:
        raw = db._conn.raw
        if not force_index:
            rows = await raw.fetch("EXPLAIN " + sql, *params)
            return "\n".join(row[0] for row in rows)
        await raw.execute("SET enable_seqscan = off")
        try:
            rows = await raw.fetch("EXPLAIN " + sql, *params)
        finally:
            await raw.execute("SET enable_seqscan = on")
        return "\n".join(row[0] for row in rows)

    return _run(_explain())


def _assert_index_plan(db, sql, params, expected, *, force_index=True):
    """没有全表扫描，且预期的索引确实参与了这条查询。"""
    plan = _plan(db, sql, params, force_index=force_index)
    assert SEQ_SCAN not in plan, f"仍有全表扫描：{plan}"
    for name in expected:
        assert name in plan, f"没有走 {name}：{plan}"
    return plan


def test_hardening_indexes_are_created(db):
    for name, table in EXPECTED_INDEXES.items():
        actual = _index_table(db, name)
        assert actual is not None, f"{name} 没有建立"
        assert actual == table, f"{name} 建在 {actual} 上，预期 {table}"


def test_original_capture_lookup_uses_indexes_not_scans(db):
    """原图反查的六路 UNION ALL：每一路都要走自己的索引。"""
    sql = """
        SELECT capture_id FROM hygiene_standards WHERE capture_id = $1
        UNION ALL
        SELECT capture_id FROM hygiene_daily_submissions WHERE capture_id = $2
        UNION ALL
        SELECT before_capture_id FROM hygiene_deep_clean_submissions WHERE before_capture_id = $3
        UNION ALL
        SELECT after_capture_id FROM hygiene_deep_clean_submissions WHERE after_capture_id = $4
        UNION ALL
        SELECT capture_id FROM hygiene_fix_tickets WHERE capture_id = $5
        UNION ALL
        SELECT capture_id FROM hygiene_fix_reshoots WHERE capture_id = $6
        LIMIT 1
    """
    _assert_index_plan(
        db,
        sql,
        ("x",) * 6,
        (
            "idx_hygiene_standards_capture",
            "idx_hygiene_daily_submissions_capture",
            "idx_hygiene_deep_clean_sub_before",
            "idx_hygiene_deep_clean_sub_after",
            "idx_hygiene_fix_tickets_capture",
            "idx_hygiene_fix_reshoots_capture",
        ),
    )


def test_reject_lookup_does_not_scan(db):
    """驳回判据（accept_daily 每次都跑）要走 board_events 的覆盖索引。"""
    _assert_index_plan(
        db,
        """SELECT 1 FROM hygiene_board_events
           WHERE board = $1 AND event_type = $2 AND item_id = $3
             AND shift = $4 AND business_date = $5 LIMIT 1""",
        ("person", "驳回", 1, "白班", "2026-09-13"),
        ("idx_hygiene_board_events_reject",),
    )


def test_fix_ticket_listing_has_no_dedicated_index(db):
    """记录一个已知取舍：不给 ``status != ?`` 建 (status, id) 专用索引。

    原先还断言「EXPLAIN 里出现 SCAN」——那是 SQLite 的机制说明（不等条件用不上
    索引），在 PG 上两头都不成立：空表上任何查询都是 Seq Scan，这条断言恒真、
    不携带信息；而 ``status <> ?`` 在 PG 上也不是「用不上索引」（数据有选择性时
    可以走 ``(status, deadline)`` 的 bitmap scan）。真要提速得把查询改成正面枚举
    状态，那是业务改动。所以这里只留与优化器无关的那一半：不要为不等条件建索引。
    """
    assert (
        _index_table(db, "idx_hygiene_fix_tickets_status_id") is None
    ), "不要为不等条件建永远不被选中的索引"


def test_hardening_indexes_land_on_a_prepared_worker(db):
    """work.prepare() 之后的库同样带索引（门店真实的启动路径）。

    PG 侧的结构来自 ``migrations/pg/*.sql``（应用不在启动期改结构），``prepare()``
    现在只探扩展列 / 回填元数据 / 播种，不再建表建索引。所以这条用例守的是：
    worker 连的那个库确实应用过迁移，且 ``prepare()`` 没有把索引弄丢。
    """
    work = HygieneWork(
        db,
        captures=FakeCaptureStore(),
        now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    cur = _run(
        db._conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
            # 不是 LIKE：PG 里 `_` 是单字符通配符，会匹配到 idxXhygiene…
            "AND starts_with(indexname, 'idx_hygiene')"
        )
    )
    names = {dict(row)["indexname"] for row in _run(cur.fetchall())}
    assert len(names) >= len(EXPECTED_INDEXES), f"prepare() 之后卫生索引只剩 {sorted(names)}"
    missing = sorted(set(EXPECTED_INDEXES) - names)
    assert not missing, f"prepare() 之后缺少索引：{missing}"
