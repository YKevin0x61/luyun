#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业微信推送服务与存储层的轻量测试。"""

import base64
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.wecom_push_service import (
    WECOM_IMAGE_BYTE_LIMIT,
    WECOM_TEXT_BYTE_LIMIT,
    RenderedMessage,
    assert_message_size,
    encrypt_webhook_url,
    decrypt_webhook_url,
    expand_messages,
    mask_webhook_url,
    render_sales_report_text,
    resolve_report_dates,
    split_text_for_wecom,
    validate_schedule_time,
    validate_webhook_url,
    wecom_push_service,
)

VALID_WEBHOOK = (
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
    "?key=693a91f6-7aoc-4bc4-97a0-0ec2sifa5aaa"
)


class WeComRenderTest(unittest.TestCase):
    def test_render_full_mode_aggregates_across_stations(self):
        report_data = {
            "date_range": {"start": "2026-05-01", "end": "2026-05-01"},
            "summary": {
                "total_orders": 10,
                "total_dishes": 12,
                "unique_dishes": 2,
                "covered_rules": 1,
            },
            "dish_sales": [
                {"dish_name": "虾饺", "station": "shulong", "qty": 8},
                {"dish_name": "虾饺", "station": "mingdang1", "qty": 2},
                {"dish_name": "烧卖", "station": "shulong", "qty": 4},
            ],
            "semi_finished": [
                {"position": "熟笼档", "items": [{"semi_name": "虾饺皮", "qty": 8, "unit": "个"}]},
            ],
        }
        rendered = render_sales_report_text(report_data, fixed_dishes=[])
        self.assertIn("【销售报表】2026-05-01", rendered.content)
        self.assertIn("1. 虾饺 10份", rendered.content)
        self.assertIn("【半成品用量】", rendered.content)
        self.assertEqual(rendered.byte_length, len(rendered.content.encode("utf-8")))

    def test_render_fixed_mode_matches_by_normalized_name(self):
        report_data = {
            "date_range": {"start": "2026-05-01", "end": "2026-05-01"},
            "summary": {"total_orders": 1, "total_dishes": 1, "unique_dishes": 1, "covered_rules": 0},
            "dish_sales": [
                {"dish_name": "(普通)四色时蔬水晶饺(-)", "station": "shulong", "qty": 3},
                {"dish_name": "四色时蔬水晶饺(-)", "station": "mingdang1", "qty": 2},
            ],
            "semi_finished": [],
        }
        fixed_dishes = [{"dish_name": "四色时蔬水晶饺"}, {"dish_name": "凤爪"}]
        rendered = render_sales_report_text(report_data, fixed_dishes=fixed_dishes)
        self.assertIn("1. 四色时蔬水晶饺 5份", rendered.content)
        self.assertIn("2. 凤爪 0份", rendered.content)
        self.assertIn("总计 5份", rendered.content)

    def test_render_splits_into_two_logical_parts(self):
        report_data = {
            "date_range": {"start": "2026-06-02", "end": "2026-06-02"},
            "summary": {"total_orders": 5, "total_dishes": 5, "unique_dishes": 2, "covered_rules": 1},
            "dish_sales": [{"dish_name": "虾饺", "station": "shulong", "qty": 5}],
            "semi_finished": [
                {"position": "案板", "items": [{"semi_name": "虾饺", "qty": 255, "unit": "个"}]},
            ],
        }
        rendered = render_sales_report_text(report_data, fixed_dishes=[])
        self.assertEqual(len(rendered.parts), 2)
        self.assertIn("【菜品销量】", rendered.parts[0])
        self.assertNotIn("【半成品用量】", rendered.parts[0])
        self.assertIn("【半成品用量】", rendered.parts[1])
        self.assertIn("（半成品用量）", rendered.parts[1])

    def test_render_single_part_when_no_semi(self):
        report_data = {
            "date_range": {"start": "2026-06-02", "end": "2026-06-02"},
            "summary": {"total_orders": 1, "total_dishes": 1, "unique_dishes": 1, "covered_rules": 0},
            "dish_sales": [{"dish_name": "虾饺", "station": "shulong", "qty": 1}],
            "semi_finished": [],
        }
        rendered = render_sales_report_text(report_data, fixed_dishes=[])
        self.assertEqual(len(rendered.parts), 1)

    def test_expand_messages_splits_only_oversized_part(self):
        small = "短消息"
        big = "\n".join(f"行{index}" for index in range(2000))
        expanded = expand_messages([small, big])
        self.assertEqual(expanded[0], small)
        self.assertGreater(len(expanded), 2)
        for message in expanded:
            self.assertLessEqual(len(message.encode("utf-8")), WECOM_TEXT_BYTE_LIMIT)

    def test_assert_message_size_rejects_oversized(self):
        oversized = RenderedMessage(content="x", byte_length=WECOM_TEXT_BYTE_LIMIT + 1)
        with self.assertRaises(ValueError):
            assert_message_size(oversized)


class WeComSplitTest(unittest.TestCase):
    def test_split_keeps_each_chunk_within_limit(self):
        content = "\n".join(f"{index}. 菜品名称示例 {index} 数量 {index}份" for index in range(400))
        chunks = split_text_for_wecom(content)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.encode("utf-8")), WECOM_TEXT_BYTE_LIMIT)
        self.assertEqual("\n".join(chunks).count("菜品名称示例"), 400)

    def test_split_hard_splits_single_overlong_line(self):
        line = "甲" * 2000  # 6000 bytes single line
        chunks = split_text_for_wecom(line)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.encode("utf-8")), WECOM_TEXT_BYTE_LIMIT)
        self.assertEqual("".join(chunks), line)

    def test_split_short_content_single_chunk(self):
        self.assertEqual(split_text_for_wecom("hello"), ["hello"])


class WeComWebhookHelperTest(unittest.TestCase):
    def test_validate_webhook_url_accepts_official_host(self):
        self.assertEqual(validate_webhook_url(VALID_WEBHOOK), VALID_WEBHOOK)

    def test_validate_webhook_url_rejects_foreign_host(self):
        with self.assertRaises(ValueError):
            validate_webhook_url("https://example.com/cgi-bin/webhook/send?key=abc")

    def test_mask_webhook_url_hides_key_middle(self):
        masked = mask_webhook_url(VALID_WEBHOOK)
        self.assertIn("key=693a...5aaa", masked)
        self.assertNotIn("7aoc", masked)

    def test_encrypt_decrypt_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("services.credentials_store._DATA_DIR", __import__("pathlib").Path(tmpdir)), \
                 patch("services.credentials_store._KEY_FILE", __import__("pathlib").Path(tmpdir) / ".cred_key"):
                encrypted = encrypt_webhook_url(VALID_WEBHOOK)
                self.assertNotIn("qyapi", encrypted)
                self.assertEqual(decrypt_webhook_url(encrypted), VALID_WEBHOOK)

    def test_env_key_survives_after_env_is_unset(self):
        """LUYUN_CRED_KEY must be persisted so a later process without the env can decrypt."""
        from pathlib import Path

        from cryptography.fernet import Fernet

        env_key = Fernet.generate_key().decode("ascii")
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / ".cred_key"
            with patch("services.credentials_store._DATA_DIR", Path(tmpdir)), \
                 patch("services.credentials_store._KEY_FILE", key_path):
                previous = os.environ.get("LUYUN_CRED_KEY")
                os.environ["LUYUN_CRED_KEY"] = env_key
                try:
                    encrypted = encrypt_webhook_url(VALID_WEBHOOK)
                finally:
                    os.environ.pop("LUYUN_CRED_KEY", None)
                    if previous is not None:
                        os.environ["LUYUN_CRED_KEY"] = previous
                self.assertEqual(decrypt_webhook_url(encrypted), VALID_WEBHOOK)

    def test_missing_key_does_not_mint_replacement_when_ciphertext_exists(self):
        """A vanished .cred_key must not be replaced with a fresh key (orphans webhooks)."""
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            key_path = data_dir / ".cred_key"
            with patch("services.credentials_store._DATA_DIR", data_dir), \
                 patch("services.credentials_store._KEY_FILE", key_path):
                encrypted = encrypt_webhook_url(VALID_WEBHOOK)
                (data_dir / "credentials.enc").write_bytes(b"placeholder")
                key_path.unlink()
                with self.assertRaises(ValueError):
                    decrypt_webhook_url(encrypted)
                self.assertFalse(key_path.exists())

    def test_validate_schedule_time(self):
        self.assertEqual(validate_schedule_time("09:05"), "09:05")
        with self.assertRaises(ValueError):
            validate_schedule_time("24:00")
        with self.assertRaises(ValueError):
            validate_schedule_time("9:5")

    def test_resolve_report_dates(self):
        now = datetime(2026, 5, 2, 21, 30, tzinfo=CHINA_TZ)
        self.assertEqual(resolve_report_dates("today", now), ("2026-05-02", "2026-05-02"))
        self.assertEqual(resolve_report_dates("yesterday", now), ("2026-05-01", "2026-05-01"))

    def test_early_morning_belongs_to_the_previous_business_day(self):
        """05:00 的「今天」是前一营业日（CORR-05）：采集/对账/卫生都这么切。"""
        now = datetime(2026, 5, 2, 5, 0, tzinfo=CHINA_TZ)
        self.assertEqual(resolve_report_dates("today", now), ("2026-05-01", "2026-05-01"))
        self.assertEqual(resolve_report_dates("yesterday", now), ("2026-04-30", "2026-04-30"))

    def test_cut_hour_boundary(self):
        """05:59 还是前一营业日，06:00 起算当天。"""
        before = datetime(2026, 5, 2, 5, 59, tzinfo=CHINA_TZ)
        after = datetime(2026, 5, 2, 6, 0, tzinfo=CHINA_TZ)
        self.assertEqual(resolve_report_dates("today", before)[0], "2026-05-01")
        self.assertEqual(resolve_report_dates("today", after)[0], "2026-05-02")


class WeComImagePayloadTest(unittest.IsolatedAsyncioTestCase):
    """群机器人的 image 消息：base64 + md5，超限在发之前就拦下来。"""

    async def test_payload_carries_base64_and_md5_of_the_raw_bytes(self):
        data = b"\xff\xd8\xff\xe0-JPEG-BYTES"

        with patch.object(
            wecom_push_service, "_post_payload", new=AsyncMock(return_value=(True, "ok"))
        ) as poster:
            ok, message = await wecom_push_service.send_image(VALID_WEBHOOK, data)

        self.assertTrue(ok)
        self.assertEqual(message, "ok")
        payload = poster.await_args.args[1]
        self.assertEqual(payload["msgtype"], "image")
        self.assertEqual(
            payload["image"]["base64"], base64.b64encode(data).decode("ascii")
        )
        self.assertEqual(payload["image"]["md5"], hashlib.md5(data).hexdigest())

    async def test_oversized_image_is_rejected_without_sending(self):
        oversized = b"x" * (WECOM_IMAGE_BYTE_LIMIT + 1)

        with patch.object(
            wecom_push_service, "_post_payload", new=AsyncMock(return_value=(True, "ok"))
        ) as poster:
            ok, message = await wecom_push_service.send_image(VALID_WEBHOOK, oversized)

        self.assertFalse(ok)
        self.assertIn("超过企业微信 image 限制", message)
        self.assertEqual(poster.await_count, 0)

    async def test_empty_image_is_rejected(self):
        with patch.object(
            wecom_push_service, "_post_payload", new=AsyncMock(return_value=(True, "ok"))
        ) as poster:
            ok, _message = await wecom_push_service.send_image(VALID_WEBHOOK, b"")

        self.assertFalse(ok)
        self.assertEqual(poster.await_count, 0)


class WeComStorageAndSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_webhook_and_job_crud(self):
        webhook_id = await self.db.wecom_webhook_create({
            "name": "测试群",
            "webhook_url_encrypted": "enc",
            "webhook_url_masked": "masked",
            "enabled": True,
            "notes": "",
        })
        self.assertGreater(webhook_id, 0)

        job_id = await self.db.wecom_job_create({
            "name": "每日报表",
            "topic_id": "sales_report",
            "params_json": json.dumps({
                "schedule_time": "21:30", "date_range_mode": "today", "station": "",
            }),
            "schedule_time": "21:30",
        })
        self.assertGreater(job_id, 0)
        job = await self.db.wecom_job_get(job_id)
        self.assertEqual(job["schedule_time"], "21:30")
        self.assertEqual(job["last_sent_date"], "")
        self.assertEqual(job["topic_id"], "sales_report")
        self.assertEqual(json.loads(job["params_json"])["date_range_mode"], "today")
        # 收件人不再是任务的一列：旧的 webhook_id 读都不读了（列还在，只读留档）。
        self.assertNotIn("webhook_id", job)

    async def test_job_update_moves_the_topic_and_the_params(self):
        job_id = await self.db.wecom_job_create({
            "name": "每日报表",
            "topic_id": "sales_report",
            "params_json": json.dumps({"schedule_time": "21:30", "date_range_mode": "today"}),
            "schedule_time": "21:30",
        })

        ok = await self.db.wecom_job_update(job_id, {
            "name": "对账差异日报",
            "topic_id": "reconcile_diff",
            "params_json": json.dumps({"schedule_time": "22:10", "date_range_mode": "today"}),
            "schedule_time": "22:10",
        })

        self.assertTrue(ok)
        job = await self.db.wecom_job_get(job_id)
        self.assertEqual(job["topic_id"], "reconcile_diff")
        self.assertEqual(json.loads(job["params_json"])["schedule_time"], "22:10")

    async def test_mark_sent_only_touches_the_business_date(self):
        job_id = await self.db.wecom_job_create({
            "name": "每日报表",
            "topic_id": "sales_report",
            "params_json": json.dumps({"schedule_time": "21:30", "date_range_mode": "today"}),
            "schedule_time": "21:30",
        })

        self.assertTrue(await self.db.wecom_job_mark_sent(job_id, "2026-05-02"))

        job = await self.db.wecom_job_get(job_id)
        self.assertEqual(job["last_sent_date"], "2026-05-02")
        self.assertEqual(job["topic_id"], "sales_report")
        self.assertEqual(json.loads(job["params_json"])["schedule_time"], "21:30")

    async def _channel(self, name):
        return await self.db.wecom_webhook_create({
            "name": name,
            "webhook_url_encrypted": encrypt_webhook_url(VALID_WEBHOOK),
            "webhook_url_masked": mask_webhook_url(VALID_WEBHOOK),
            "enabled": True,
            "notes": "",
        })

    async def _job(self, topic_id, schedule_time, *, params=None, last_sent_date=""):
        return await self.db.wecom_job_create({
            "name": "每日报表",
            "topic_id": topic_id,
            "params_json": json.dumps(params or {
                "schedule_time": schedule_time, "date_range_mode": "today", "station": "",
            }),
            "schedule_time": schedule_time,
            "last_sent_date": last_sent_date,
        })

    async def _subscribe(self, topic_id, channel_id):
        await self.db.wecom_subscription_upsert({
            "topic_id": topic_id,
            "target_channel_id": channel_id,
        })

    async def test_dispatch_enqueues_one_delivery_per_subscribed_channel(self):
        """到点不是「直接发」而是「按订阅入队」：收件人来自订阅，一行一个目标。"""
        first = await self._channel("门店群")
        second = await self._channel("日报群")
        await self._subscribe("sales_report", first)
        await self._subscribe("sales_report", second)
        job_id = await self._job("sales_report", "21:30")
        now = datetime(2026, 5, 2, 21, 30, tzinfo=CHINA_TZ)

        dispatched = await wecom_push_service.dispatch_due_jobs(self.db, now=now)

        self.assertEqual(dispatched, 1)
        rows = await self.db.wecom_outbox_recent()
        self.assertEqual({int(row["target_channel_id"]) for row in rows}, {first, second})
        self.assertEqual({row["status"] for row in rows}, {"pending"}, "入队不等于已发")
        self.assertEqual({row["topic_id"] for row in rows}, {"sales_report"})
        self.assertEqual(
            {int(row["schedule_id"]) for row in rows}, {job_id},
            "定时投递带着任务 id：失败当天补发靠它认出来（出站状态机）",
        )
        params = json.loads(rows[0]["params_json"])
        self.assertEqual(params["business_date"], "2026-05-02", "营业日在入队时冻结")
        self.assertNotIn("date_range_mode", params, "冻结之后不再留「以后再解析一次」的口子")
        job = await self.db.wecom_job_get(job_id)
        self.assertEqual(job["last_sent_date"], "2026-05-02")

    async def test_dispatch_skips_when_already_sent_today(self):
        channel_id = await self._channel("测试群")
        await self._subscribe("sales_report", channel_id)
        now = datetime(2026, 5, 2, 21, 30, tzinfo=CHINA_TZ)
        job_id = await self._job("sales_report", "21:30", last_sent_date="2026-05-02")

        dispatched = await wecom_push_service.dispatch_due_jobs(self.db, now=now)

        self.assertEqual(dispatched, 0)
        self.assertEqual(await self.db.wecom_outbox_recent(), [])
        self.assertEqual((await self.db.wecom_job_get(job_id))["last_sent_date"], "2026-05-02")

    async def test_dispatch_skips_by_business_day_not_calendar_day(self):
        """`last_sent_date` 是营业日：22:00 推过之后，次日 01:00 仍是同一场营业（CORR-05）。

        日历日排重下这两个时刻同属前一天，恰好也挡得住；真正的差别在跨零点：22:00 推过
        之后，次日 01:00 按日历日是新的一天、按营业日还是同一场营业。
        """
        channel_id = await self._channel("测试群")
        await self._subscribe("sales_report", channel_id)
        # 5/2 22:00 推过 → 营业日 2026-05-02；5/3 01:00 仍属该营业日。
        await self._job("sales_report", "01:00", last_sent_date="2026-05-02")

        now = datetime(2026, 5, 3, 1, 0, tzinfo=CHINA_TZ)
        dispatched = await wecom_push_service.dispatch_due_jobs(self.db, now=now)

        self.assertEqual(dispatched, 0, "同一营业日不该再推一次")
        self.assertEqual(await self.db.wecom_outbox_recent(), [])

    async def test_dispatch_enqueues_nothing_when_nobody_subscribes(self):
        """零订阅 = 一条都不发，而且不算「今天发过了」：店长补上订阅后当天还能发。"""
        await self._channel("没人订阅的群")
        job_id = await self._job("sales_report", "21:30")
        now = datetime(2026, 5, 2, 21, 30, tzinfo=CHINA_TZ)

        dispatched = await wecom_push_service.dispatch_due_jobs(self.db, now=now)

        self.assertEqual(dispatched, 0)
        self.assertEqual(await self.db.wecom_outbox_recent(), [])
        self.assertEqual((await self.db.wecom_job_get(job_id))["last_sent_date"], "")

    async def test_dispatch_keeps_the_scheduled_semantics_for_a_reconcile_diff_job(self):
        """对账差异告警的定时侧（原「数据质量日报」）同样按订阅投递、同样按营业日排重。"""
        channel_id = await self._channel("告警群")
        await self._subscribe("reconcile_diff", channel_id)
        job_id = await self._job(
            "reconcile_diff", "22:10",
            params={"schedule_time": "22:10", "date_range_mode": "today"},
        )
        now = datetime(2026, 5, 2, 22, 10, tzinfo=CHINA_TZ)

        self.assertEqual(await wecom_push_service.dispatch_due_jobs(self.db, now=now), 1)

        rows = await self.db.wecom_outbox_recent()
        self.assertEqual({row["topic_id"] for row in rows}, {"reconcile_diff"})
        self.assertEqual(json.loads(rows[0]["params_json"])["business_date"], "2026-05-02")
        self.assertEqual((await self.db.wecom_job_get(job_id))["last_sent_date"], "2026-05-02")


if __name__ == "__main__":
    unittest.main()
