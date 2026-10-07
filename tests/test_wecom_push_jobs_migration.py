#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""迁移 0018：推送任务认识自己的**内容类型**与**参数**，旧列只读。

验收依据是票 08 的两条：

1. 「迁移回填之后，每个旧任务仍按它原来绑定的渠道投递」——本票不改订阅，靠的是
   0016 的第三条规则（每条旧任务的「内容类型 × 它绑定的渠道」已经成了订阅）。所以
   这里按「旧结构种数据 → 跑 0016 + 0018 → 用**任务自己的 topic_id** 去查订阅」的
   形状验：任务的 topic_id 与订阅的 topic_id 对上，收件人集合就等于它原来绑定的渠道。
2. 「不删列、不改既有数据（除了回填新列）」——旧列留着，旧值一个字不动。

期望值一律写成字面量（`sales_report_text` / `sales_report` / 渠道 id），不由被测实现
推出来。迁移在会话开始时已由 conftest 应用过一次，所以回填语句必须**可重复执行**：
种一行「迁移前形状」的行（topic_id 为空）再跑一遍，正好把幂等一起验了。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from db_core.backend.pg import dsn_from_env
from tests.pg_probe import fetch_all, fetch_one, scalar, table_columns

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations" / "pg"
MIGRATION_0016 = MIGRATIONS / "0016_wecom_push_subscriptions.sql"
MIGRATION_0018 = MIGRATIONS / "0018_wecom_push_jobs_topic.sql"

SALES_LEGACY_TYPE = "sales_report_text"
QUALITY_LEGACY_TYPE = "data_quality_alert"
SALES_TOPIC = "sales_report"
QUALITY_TOPIC = "reconcile_diff"


def apply_migration(path: Path) -> None:
    """按管理员在「数据库迁移」面板里应用脚本的同一条路径跑一次。"""
    proc = subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsn_from_env(), "-f", str(path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr.strip() or proc.stdout.strip()


def seed_webhook(name: str, *, enabled: int = 1) -> int:
    return scalar(
        """INSERT INTO wecom_push_webhooks
             (name, webhook_url_encrypted, webhook_url_masked, enabled, hygiene_feed,
              notes, created_at, updated_at)
           VALUES (?, 'enc', 'mask', ?, 0, '', '2026-09-01T00:00:00+08:00',
                   '2026-09-01T00:00:00+08:00')
           RETURNING id""",
        (name, enabled),
    )


def seed_legacy_job(
    webhook_id: int,
    push_type: str,
    *,
    schedule_time: str = "21:30",
    date_range_mode: str = "today",
    station: str = "",
) -> int:
    """种一条「迁移前形状」的任务：新列还是空的，值都在旧列里。

    旧列（`push_type` / `webhook_id` / `date_range_mode` / `station`）与迁移前逐字一致；
    新列显式写成空，模拟迁移尚未回填的那一刻。
    """
    return scalar(
        """INSERT INTO wecom_push_jobs
             (name, webhook_id, push_type, schedule_time, date_range_mode, station,
              enabled, last_sent_date, notes, topic_id, params_json, created_at, updated_at)
           VALUES ('每日推送', ?, ?, ?, ?, ?, 1, '', '', NULL, '{}',
                   '2026-09-01T00:00:00+08:00', '2026-09-01T00:00:00+08:00')
           RETURNING id""",
        (webhook_id, push_type, schedule_time, date_range_mode, station),
    )


def job_row(job_id: int) -> dict:
    row = fetch_one(
        """SELECT topic_id, params_json, webhook_id, push_type, schedule_time,
                  date_range_mode, station
             FROM wecom_push_jobs WHERE id = ?""",
        (job_id,),
    )
    assert row is not None, f"任务 {job_id} 不见了"
    keys = ("topic_id", "params_json", "webhook_id", "push_type", "schedule_time",
            "date_range_mode", "station")
    return dict(zip(keys, row))


def subscribed_channels(topic_id: str) -> set[int]:
    return {
        int(row[0])
        for row in fetch_all(
            """SELECT target_channel_id FROM wecom_push_subscriptions
                WHERE topic_id = ? AND target_channel_id IS NOT NULL AND enabled = 1""",
            (topic_id,),
        )
    }


# ── 片 1：加成性 DDL ────────────────────────────────────────────────────────


def test_migration_adds_the_topic_and_params_columns():
    columns = table_columns("wecom_push_jobs")

    assert "topic_id" in columns
    assert "params_json" in columns


def test_migration_leaves_the_legacy_columns_alone():
    """旧列原样保留（只读）：回滚与排查都还有依据，只是新代码不再读它们。"""
    columns = table_columns("wecom_push_jobs")

    for legacy in ("webhook_id", "push_type", "schedule_time", "date_range_mode", "station"):
        assert legacy in columns, f"{legacy} 被迁移动了"


def test_the_legacy_webhook_column_has_a_default_so_inserts_still_work():
    """新代码不再写 webhook_id，而它是 NOT NULL —— 没有默认值的话 INSERT 直接失败。"""
    webhook_id = seed_webhook("门店群")
    job_id = scalar(
        """INSERT INTO wecom_push_jobs
             (name, push_type, schedule_time, date_range_mode, station, enabled,
              last_sent_date, notes, created_at, updated_at)
           VALUES ('不带收件人的任务', 'sales_report_text', '21:30', 'today', '', 1, '',
                   '', '2026-10-01T00:00:00+08:00', '2026-10-01T00:00:00+08:00')
           RETURNING id""",
    )
    row = job_row(job_id)

    assert row["webhook_id"] == 0
    assert row["topic_id"] == SALES_TOPIC, "新行的内容类型取列默认值"
    assert webhook_id > 0


# ── 片 2：回填 ─────────────────────────────────────────────────────────────


def test_backfill_maps_the_legacy_push_types_to_topics():
    sales_job = seed_legacy_job(seed_webhook("日报群"), SALES_LEGACY_TYPE)
    quality_job = seed_legacy_job(seed_webhook("告警群"), QUALITY_LEGACY_TYPE)

    apply_migration(MIGRATION_0018)

    assert job_row(sales_job)["topic_id"] == SALES_TOPIC
    assert job_row(quality_job)["topic_id"] == QUALITY_TOPIC


def test_backfill_moves_the_params_of_a_sales_report_job():
    job_id = seed_legacy_job(
        seed_webhook("日报群"),
        SALES_LEGACY_TYPE,
        schedule_time="22:30",
        date_range_mode="yesterday",
        station="shulong",
    )

    apply_migration(MIGRATION_0018)

    params = json.loads(job_row(job_id)["params_json"])
    assert params == {
        "schedule_time": "22:30",
        "date_range_mode": "yesterday",
        "station": "shulong",
    }


def test_backfill_leaves_out_the_station_of_a_reconcile_diff_job():
    """对账差异告警的定时侧没有「档口」这个参数（定时侧 schema 严格拒绝多余字段）。"""
    job_id = seed_legacy_job(
        seed_webhook("告警群"),
        QUALITY_LEGACY_TYPE,
        schedule_time="22:10",
        station="shulong",
    )

    apply_migration(MIGRATION_0018)

    params = json.loads(job_row(job_id)["params_json"])
    assert params == {"schedule_time": "22:10", "date_range_mode": "today"}
    assert "station" not in params


def test_backfill_does_not_touch_the_legacy_columns():
    """回填只写新列：旧列的值一个字不动（旧版本回滚回去读到的还是原文）。"""
    job_id = seed_legacy_job(
        seed_webhook("日报群"),
        SALES_LEGACY_TYPE,
        schedule_time="22:30",
        date_range_mode="yesterday",
        station="shulong",
    )

    apply_migration(MIGRATION_0018)

    row = job_row(job_id)
    assert (row["push_type"], row["schedule_time"], row["date_range_mode"], row["station"]) == (
        SALES_LEGACY_TYPE, "22:30", "yesterday", "shulong",
    )


def test_migration_is_repeatable_and_keeps_the_first_backfill():
    job_id = seed_legacy_job(seed_webhook("日报群"), SALES_LEGACY_TYPE, station="shulong")

    apply_migration(MIGRATION_0018)
    first = job_row(job_id)
    apply_migration(MIGRATION_0018)
    second = job_row(job_id)

    assert first["topic_id"] == second["topic_id"] == SALES_TOPIC
    assert first["params_json"] == second["params_json"]


def test_a_job_created_after_the_migration_is_not_rewritten_by_a_rerun():
    """回填只填空值：新代码建的任务（参数与内容类型都写好的）重跑迁移不会被改。"""
    job_id = scalar(
        """INSERT INTO wecom_push_jobs
             (name, webhook_id, push_type, schedule_time, date_range_mode, station,
              enabled, last_sent_date, notes, topic_id, params_json, created_at, updated_at)
           VALUES ('对账差异日报', 0, 'sales_report_text', '22:10', 'today', '',
                   1, '', '', ?, ?, '2026-10-01T00:00:00+08:00', '2026-10-01T00:00:00+08:00')
           RETURNING id""",
        (QUALITY_TOPIC, json.dumps({"schedule_time": "22:10", "date_range_mode": "today"})),
    )

    apply_migration(MIGRATION_0018)

    row = job_row(job_id)
    assert row["topic_id"] == QUALITY_TOPIC
    assert json.loads(row["params_json"]) == {
        "schedule_time": "22:10", "date_range_mode": "today",
    }


# ── 片 3：回填之后收件人仍然与迁移前一致 ────────────────────────────────────


def test_each_legacy_job_still_reaches_the_channel_it_was_bound_to():
    """迁移前：任务自己绑定收件人（webhook_id）。迁移后：收件人来自订阅。

    两次都跑一遍（0016 的回填 + 0018 的内容类型回填），断言的落点是「**任务自己的**
    topic_id 对应的订阅里，正好有它原来绑定的那个渠道」——任务不再持有收件人之后，
    这条等式就是「收件人没变」的全部含义。
    """
    sales_channel = seed_webhook("日报群")
    quality_channel = seed_webhook("告警群")
    sales_job = seed_legacy_job(sales_channel, SALES_LEGACY_TYPE)
    quality_job = seed_legacy_job(quality_channel, QUALITY_LEGACY_TYPE)

    apply_migration(MIGRATION_0016)
    apply_migration(MIGRATION_0018)

    sales_topic = job_row(sales_job)["topic_id"]
    quality_topic = job_row(quality_job)["topic_id"]

    assert sales_channel in subscribed_channels(sales_topic)
    assert quality_channel in subscribed_channels(quality_topic)
