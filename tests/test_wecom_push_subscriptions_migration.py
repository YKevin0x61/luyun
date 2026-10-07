#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""迁移 0016：推送订阅 / 渠道群组 / 群组成员 / 出站表，以及三条收件人回填规则。

这一票的验收依据是「**迁移本身不改变任何一封消息的收件人**」，所以用例全部按
「旧结构下种数据 → 跑 0016 → 查新表」的形状写：

1. 三条回填规则各一条断言（卫生类、采集类、每条现有任务原来绑定的渠道）；
2. 历史发送记录与照片分享搬进出站表，状态 / 目标 / 时间 / 错误 / 尝试次数不丢；
3. 重复执行不产生重复行（管理员可以放心重跑）；
4. **旧列与旧表原样保留**，新代码不再读它们 —— 回滚与排查都还有依据。

期望值一律写成字面量（内容类型 id、渠道 id），不由被测代码算出：从被测实现里
推导期望值，等于让断言永远成立（TDD 的反模式「同义反复」）。

迁移在会话开始时已由 ``tests/conftest.py`` 应用过一次（它会遍历
``migrations/pg/*.sql``），所以这里必须**可重复执行**——种完旧数据再跑一遍，
正好把幂等性也一起验了。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from db_core.backend.pg import dsn_from_env
from tests.pg_probe import fetch_all, scalar, table_columns

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "pg"
    / "0016_wecom_push_subscriptions.sql"
)

# 内容类型 id（领域词「推送内容类型」）；与代码里的注册表常量同一批字符串。
HYGIENE_TOPICS = ("hygiene_reminder", "hygiene_photo")
COLLECT_TOPICS = ("reconcile_diff", "unmapped_dish", "scraper_failure")
SALES_TOPIC = "sales_report"
# 旧任务的 `data_quality_alert` 日报（对账差异 + 未映射菜品 + 采集失败摘要）落这一类
# 内容；见 spec「统一出站」：同一份内容走定时与走告警链路时收件人语义一致。
DATA_QUALITY_TOPIC = "reconcile_diff"

SUBSCRIPTIONS = "wecom_push_subscriptions"


def apply_migration() -> None:
    """按管理员在「数据库迁移」面板里应用脚本的同一条路径跑一次 0016。"""
    proc = subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsn_from_env(), "-f", str(MIGRATION)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr.strip() or proc.stdout.strip()


def seed_webhook(name: str, *, enabled: int = 1, hygiene_feed: int = 0) -> int:
    """种一个旧结构的渠道（webhook）；返回它的 id。"""
    return scalar(
        """INSERT INTO wecom_push_webhooks
             (name, webhook_url_encrypted, webhook_url_masked, enabled, hygiene_feed,
              notes, created_at, updated_at)
           VALUES (?, 'enc', 'mask', ?, ?, '', '2026-09-01T00:00:00+08:00',
                   '2026-09-01T00:00:00+08:00')
           RETURNING id""",
        (name, enabled, hygiene_feed),
    )


def seed_job(webhook_id: int, push_type: str) -> int:
    """种一条旧结构的推送任务：它**自己**持有收件人（webhook_id）。"""
    return scalar(
        """INSERT INTO wecom_push_jobs
             (name, webhook_id, push_type, schedule_time, date_range_mode, station,
              enabled, last_sent_date, notes, created_at, updated_at)
           VALUES ('每日销售报表', ?, ?, '21:30', 'today', '', 1, '', '',
                   '2026-09-01T00:00:00+08:00', '2026-09-01T00:00:00+08:00')
           RETURNING id""",
        (webhook_id, push_type),
    )


def seed_log(webhook_id: int, *, status: str, push_type: str = "sales_report_text") -> int:
    return scalar(
        """INSERT INTO wecom_push_logs
             (job_id, webhook_id, webhook_name, push_type, status, message_bytes,
              error, response_text, sent_at)
           VALUES (NULL, ?, '核心群', ?, ?, 321, '超时', 'ok', '2026-09-30T21:30:05+08:00')
           RETURNING id""",
        (webhook_id, push_type, status),
    )


def seed_share(*, status: str = "pending", attempts: int = 0) -> int:
    return scalar(
        """INSERT INTO hygiene_wecom_shares
             (kind, ref_key, capture_id, extra_capture_id, dedupe_key, caption, status,
              attempts, last_error, created_at, sent_at)
           VALUES ('daily', '2026-09-30:zone-1', 'cap-1', 'cap-0', 'k-1', '漏拍',
                   ?, ?, '网络不可达', '2026-09-30T10:00:00+08:00',
                   CASE WHEN ? = 'sent' THEN '2026-09-30T10:00:03+08:00' END)
           RETURNING id""",
        (status, attempts, status),
    )


def subscriptions(topic_id: str | None = None) -> list[tuple]:
    """订阅行：(topic_id, target_channel_id, target_group_id, enabled)。"""
    sql = """SELECT topic_id, target_channel_id, target_group_id, enabled
               FROM wecom_push_subscriptions"""
    params: tuple = ()
    if topic_id is not None:
        sql += " WHERE topic_id = ?"
        params = (topic_id,)
    sql += " ORDER BY topic_id, target_channel_id NULLS FIRST, target_group_id NULLS FIRST"
    return fetch_all(sql, params)


def targets_of(topic_id: str) -> set[int]:
    """某类内容当前订阅到的渠道集合（群组目标这一票还没有数据，先只算直接订阅）。"""
    return {
        int(row[1])
        for row in subscriptions(topic_id)
        if row[1] is not None and int(row[3]) == 1
    }


# ── 片 1：新表与幂等 DDL ────────────────────────────────────────────────────


def test_migration_adds_the_four_tables():
    for table in (
        "wecom_push_subscriptions",
        "wecom_channel_groups",
        "wecom_channel_group_members",
        "wecom_push_outbox",
    ):
        assert table_columns(table), f"{table} 未建立"


def test_migration_leaves_the_legacy_tables_and_columns_alone():
    """旧列与旧表原样保留（只读）：回滚与排查都还有依据。"""
    assert "hygiene_feed" in table_columns("wecom_push_webhooks")
    for table in ("wecom_push_webhooks", "wecom_push_jobs", "wecom_push_logs",
                  "hygiene_wecom_shares"):
        assert table_columns(table), f"{table} 被迁移动了"


# ── 片 2：回填规则① —— hygiene_feed = 1 的渠道进卫生类订阅 ──────────────────


def test_rule_one_backfills_marked_channels_into_hygiene_topics():
    marked = seed_webhook("卫生群", hygiene_feed=1)
    seed_webhook("日报群", hygiene_feed=0)

    apply_migration()

    assert targets_of("hygiene_reminder") == {marked}
    assert targets_of("hygiene_photo") == {marked}


def test_rule_one_keeps_a_disabled_hygiene_channel_subscribed():
    """渠道停用只跳过投递，订阅保留：恢复启用后不用重配（用户故事 7）。"""
    marked = seed_webhook("卫生群", hygiene_feed=1, enabled=0)

    apply_migration()

    assert targets_of("hygiene_reminder") == {marked}


# ── 片 3：回填规则② —— 所有启用渠道进采集类订阅 ─────────────────────────────


def test_rule_two_backfills_every_enabled_channel_into_collect_topics():
    core = seed_webhook("核心群", enabled=1)
    daily = seed_webhook("日报群", enabled=1)
    seed_webhook("停用的群", enabled=0)

    apply_migration()

    for topic in ("reconcile_diff", "unmapped_dish", "scraper_failure"):
        assert targets_of(topic) == {core, daily}, topic


def test_disabled_channel_is_not_subscribed_to_collect_topics():
    channel = seed_webhook("停用的群", enabled=0)

    apply_migration()

    assert channel not in targets_of("reconcile_diff")


# ── 片 4：回填规则③ —— 每条任务原来绑定的渠道进它那个内容类型的订阅 ──────────


def test_rule_three_binds_each_job_to_its_topic_and_its_own_channel():
    bound = seed_webhook("日报群")
    other = seed_webhook("另一个群")
    seed_job(bound, "sales_report_text")

    apply_migration()

    assert targets_of("sales_report") == {bound}
    assert other not in targets_of("sales_report")


def test_rule_three_keeps_the_daily_data_quality_job_reachable():
    """旧任务的 `data_quality_alert` 落到对账差异告警这一类内容 —— 漏了它日报就没人收。"""
    bound = seed_webhook("日报群")
    seed_job(bound, "data_quality_alert")

    apply_migration()

    assert targets_of(DATA_QUALITY_TOPIC) == {bound}


# ── 片 5：历史行搬入出站表，字段不丢 ────────────────────────────────────────


def outbox_of(topic_id: str) -> list[dict]:
    rows = fetch_all(
        """SELECT topic_id, target_channel_id, status, attempts, message_bytes,
                  last_error, created_at, finished_at, content_summary, params_json
             FROM wecom_push_outbox WHERE topic_id = ? ORDER BY id""",
        (topic_id,),
    )
    keys = ("topic_id", "target_channel_id", "status", "attempts", "message_bytes",
            "last_error", "created_at", "finished_at", "content_summary", "params_json")
    return [dict(zip(keys, row)) for row in rows]


def test_history_moves_sent_logs_into_the_outbox():
    channel = seed_webhook("核心群")
    seed_log(channel, status="success")

    apply_migration()

    row = outbox_of(SALES_TOPIC)[0]
    assert row["target_channel_id"] == channel, "目标渠道"
    assert row["status"] == "sent", "状态：success → sent"
    assert row["message_bytes"] == 321, "字节数"
    assert row["finished_at"] == "2026-09-30T21:30:05+08:00", "完成时间"
    assert int(row["attempts"]) >= 1, "尝试次数（旧链路无重试，记 1 次）"


def test_history_keeps_the_error_of_a_failed_log():
    channel = seed_webhook("核心群")
    seed_log(channel, status="failed")

    apply_migration()

    row = outbox_of(SALES_TOPIC)[0]
    assert row["status"] == "failed"
    assert row["last_error"] == "超时", "错误信息"


def test_history_moves_photo_shares_with_their_status_and_attempts():
    hygiene_group = seed_webhook("卫生群", hygiene_feed=1)
    seed_share(status="sent", attempts=2)

    apply_migration()

    row = outbox_of("hygiene_photo")[0]
    assert row["target_channel_id"] == hygiene_group, "目标渠道：当时的卫生群"
    assert row["status"] == "sent"
    assert int(row["attempts"]) == 2, "尝试次数"
    assert row["content_summary"] == "漏拍", "内容摘要"
    assert row["created_at"] == "2026-09-30T10:00:00+08:00", "登记时间"
    assert row["finished_at"] == "2026-09-30T10:00:03+08:00", "完成时间"


def test_history_moves_a_share_that_never_had_a_group():
    """没有卫生群时的 `skipped` 历史：目标为空，但不静默丢掉这一行。"""
    seed_share(status="skipped")

    apply_migration()

    row = outbox_of("hygiene_photo")[0]
    assert row["target_channel_id"] is None
    assert row["status"] == "skipped"


# ── 片 6：重复执行 ──────────────────────────────────────────────────────────


def test_migration_is_repeatable_without_duplicating_rows():
    """管理员可以放心重跑：DDL 是 IF NOT EXISTS，回填撞唯一索引就跳过。"""
    marked = seed_webhook("卫生群", hygiene_feed=1)
    bound = seed_webhook("日报群")
    seed_job(bound, "sales_report_text")
    seed_log(bound, status="success")
    seed_share(status="sent", attempts=1)

    apply_migration()
    first = subscriptions()
    apply_migration()

    assert subscriptions() == first, "重跑不能新增订阅行"
    assert len(outbox_of(SALES_TOPIC)) == 1
    assert len(outbox_of("hygiene_photo")) == 1
    assert targets_of("hygiene_reminder") == {marked}
    assert targets_of("sales_report") == {bound}


def test_one_subscription_row_per_topic_and_target():
    """同一渠道被多条规则命中（既勾了卫生、又是任务收件人）也只留一行。"""
    channel = seed_webhook("核心群", hygiene_feed=1)
    seed_job(channel, "data_quality_alert")

    apply_migration()

    assert targets_of("reconcile_diff") == {channel}


# ── 片 7：上线对照 —— 每类内容的目标集合与旧规则逐条一致 ────────────────────


def test_targets_match_the_legacy_rules_for_the_real_deployment():
    """按 2026-09 的真库形状（3 个渠道、1 条任务、50 行发送记录）对照。

    旧规则：卫生类 → `hygiene_feed = 1` 的渠道；采集类 → 所有**启用**渠道；
    日报任务 → 它自己绑定的渠道。迁移后每类的目标集合必须逐条相同。
    """
    core = seed_webhook("核心群", enabled=1, hygiene_feed=1)
    social = seed_webhook("沟通群", enabled=0, hygiene_feed=0)
    hygiene_off = seed_webhook("停用的卫生群", enabled=0, hygiene_feed=1)
    seed_job(core, "sales_report_text")
    seed_job(social, "data_quality_alert")
    seed_log(core, status="success")
    seed_log(social, status="failed")

    apply_migration()

    assert targets_of("hygiene_reminder") == {core, hygiene_off}, "① 勾了卫生的渠道"
    assert targets_of("hygiene_photo") == {core, hygiene_off}
    # ② 采集类：每个启用渠道都在三类内容里各有一行。
    for topic in COLLECT_TOPICS:
        assert targets_of(topic) >= {core}, f"② 启用渠道 {topic}"
    assert targets_of("unmapped_dish") == {core}
    assert targets_of("scraper_failure") == {core}
    assert targets_of(SALES_TOPIC) == {core}, "③ 日报任务绑定的渠道"
    # ③ 数据质量任务绑在停用的沟通群上：任务不再持有收件人之后，这一行订阅就是它
    # 原来的收件人 —— 少了它，这条日报就没人收（见 ticket 08）。
    assert targets_of(DATA_QUALITY_TOPIC) == {core, social}


def test_a_job_pointing_at_a_deleted_channel_does_not_break_the_migration():
    """任务绑定的渠道已经没了（旧表没有外键）：跳过它，不能让整条迁移失败。"""
    seed_webhook("核心群")
    seed_job(999999, "sales_report_text")

    apply_migration()

    assert subscriptions(SALES_TOPIC) == []


def test_a_log_pointing_at_a_deleted_channel_does_not_break_the_migration():
    """真库上真出现过：`wecom_push_logs.webhook_id` 指向一个早已删掉的渠道。

    旧日志表没有外键，历史行留下孤儿引用是正常的。出站表的目标列有外键，直接搬会
    让**整条迁移失败**（真库 0016 首次试跑就是这里炸的），所以孤儿引用要搬成「没有
    目标」的行，把原来的渠道 id 留在参数里备查，不丢历史。
    """
    channel = seed_webhook("核心群")
    seed_log(999999, status="success")
    seed_log(channel, status="success")

    apply_migration()

    rows = outbox_of(SALES_TOPIC)
    assert [row["target_channel_id"] for row in rows] == [None, channel]
    assert "999999" in rows[0]["params_json"], "原渠道 id 留在参数里"
