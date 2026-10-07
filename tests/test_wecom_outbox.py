#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一出站的状态机（票 03）：假发送器 + 假时钟。

覆盖 spec「Testing Decisions」第 2 条：入队 → 节流 → 成功 / 失败 → 补发 / 退避重试 →
尝试次数用尽，以及「同一内容同一目标只投递一次」。断言的是**外部行为**：出站行的终态、
错误与尝试次数、实际发出去的消息顺序，不测 SQL 形状与内部调用顺序。
"""

import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel, ConfigDict

from config import settings
from database import CHINA_TZ, DatabaseManager
from services import wecom_push_service as wecom_push_service_module
from services.wecom_outbox import RenderedDelivery, WeComOutbox, wecom_outbox
from services.wecom_push_service import (
    WECOM_TEXT_BYTE_LIMIT,
    encrypt_webhook_url,
    mask_webhook_url,
    wecom_push_service,
)
from services.wecom_push_topics import (
    TOPIC_HYGIENE_PHOTO,
    PushTopic,
    PushTrigger,
    register_topic,
    unregister_topic,
)

TOPIC = "hygiene_reminder"

URL_A = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hygiene-key-0000"
URL_B = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=daily-key-0000"


class FakeSender:
    """假发送器：记下每条消息，按脚本回失败。"""

    def __init__(self):
        self.sent = []
        self.images = []
        self.failures = []

    async def send_text(self, webhook_url, content):
        if self.failures:
            return False, self.failures.pop(0)
        self.sent.append((webhook_url, content))
        return True, "ok"

    async def send_image(self, webhook_url, image_bytes):
        if self.failures:
            return False, self.failures.pop(0)
        self.images.append((webhook_url, image_bytes))
        return True, "ok"


class OutboxTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.clock = datetime(2026, 5, 2, 21, 30, tzinfo=CHINA_TZ)
        self.sender = FakeSender()
        self.outbox = WeComOutbox(sender=self.sender, now=lambda: self.clock, gap_seconds=0)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_dir
        self._tmpdir.cleanup()

    async def _channel(self, name, url, *, enabled=True):
        return await self.db.wecom_webhook_create({
            "name": name,
            "webhook_url_encrypted": encrypt_webhook_url(url),
            "webhook_url_masked": mask_webhook_url(url),
            "enabled": enabled,
            "notes": "",
        })

    async def _subscribe(self, channel_ids, topic=TOPIC):
        for channel_id in channel_ids:
            await self.db.wecom_subscription_upsert({
                "topic_id": topic,
                "target_channel_id": channel_id,
            })

    async def _enqueue(self, text="今天漏拍 2 项", *, reference="2026-05-02:abcd", schedule_id=None):
        return await self.outbox.enqueue_topic(
            self.db,
            TOPIC,
            params={"text": text},
            trigger=PushTrigger.EVENT,
            business_reference=reference,
            schedule_id=schedule_id,
        )

    async def _rows(self):
        return await self.db.wecom_outbox_recent()


class OutboxEnqueueAndSendTest(OutboxTestCase):
    async def test_enqueue_writes_one_pending_row_per_channel(self):
        first = await self._channel("卫生群 A", URL_A)
        second = await self._channel("卫生群 B", URL_B)
        await self._subscribe([first, second])

        ids = await self._enqueue()

        self.assertEqual(len(ids), 2)
        rows = await self._rows()
        self.assertEqual({int(row["target_channel_id"]) for row in rows}, {first, second})
        self.assertEqual({row["status"] for row in rows}, {"pending"})

    async def test_dispatch_sends_the_text_and_marks_sent(self):
        channel = await self._channel("卫生群", URL_A)
        await self._subscribe([channel])
        await self._enqueue("今天漏拍 2 项")

        sent = await self.outbox.dispatch_pending(self.db)

        self.assertEqual(sent, 1)
        self.assertEqual(self.sender.sent, [(URL_A, "今天漏拍 2 项")])
        rows = await self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "sent")
        self.assertEqual(int(rows[0]["message_bytes"]), len("今天漏拍 2 项".encode("utf-8")))
        self.assertEqual(int(rows[0]["attempts"]), 1)

    async def test_nothing_to_dispatch_returns_zero(self):
        self.assertEqual(await self.outbox.dispatch_pending(self.db), 0)


class OutboxIdempotencyTest(OutboxTestCase):
    async def test_same_content_same_target_enqueues_once(self):
        """同一个业务事件对同一个渠道重复触发（弱网重传 / 循环重跑）只落一行。"""
        channel = await self._channel("卫生群", URL_A)
        await self._subscribe([channel])

        first = await self._enqueue(reference="2026-05-02:abcd")
        second = await self._enqueue(reference="2026-05-02:abcd")

        self.assertEqual(first, second)
        self.assertEqual(len(await self._rows()), 1)

    async def test_a_different_business_reference_is_a_new_delivery(self):
        """换一份汇总（新的业务引用）就是新的一封，不被上一条的幂等键挡住。"""
        channel = await self._channel("卫生群", URL_A)
        await self._subscribe([channel])

        await self._enqueue(reference="2026-05-02:abcd")
        await self._enqueue(reference="2026-05-02:ef01")

        self.assertEqual(len(await self._rows()), 2)


class OutboxMessageTest(OutboxTestCase):
    async def test_long_text_is_split_into_ordered_messages(self):
        """超长正文仍按行拆成多条，同一投递的拆分段按顺序发，仍算**一行**发送记录。"""
        channel = await self._channel("卫生群", URL_A)
        await self._subscribe([channel])
        text = "\n".join(f"第 {index} 项：" + "补" * 60 for index in range(60))
        await self._enqueue(text)

        sent = await self.outbox.dispatch_pending(self.db)

        self.assertEqual(sent, 1)
        self.assertGreater(len(self.sender.sent), 1)
        self.assertTrue(all(url == URL_A for url, _ in self.sender.sent))
        for _, chunk in self.sender.sent:
            self.assertLessEqual(len(chunk.encode("utf-8")), WECOM_TEXT_BYTE_LIMIT)
        self.assertEqual("\n".join(chunk for _, chunk in self.sender.sent), text)
        rows = await self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "sent")
        self.assertEqual(int(rows[0]["message_bytes"]), len(text.encode("utf-8")))


class OutboxSkippedChannelTest(OutboxTestCase):
    async def test_disabled_channel_is_skipped_and_the_reason_is_recorded(self):
        """渠道停用不动订阅：仍然入队，但记录里写明跳过原因，一条消息都不发。"""
        channel = await self._channel("停用的卫生群", URL_A, enabled=False)
        await self._subscribe([channel])

        ids = await self._enqueue()
        await self.outbox.dispatch_pending(self.db)

        self.assertEqual(len(ids), 1)
        rows = await self._rows()
        self.assertEqual(rows[0]["status"], "skipped")
        self.assertIn("停用", rows[0]["last_error"])
        self.assertEqual(self.sender.sent, [])


IMAGE_BYTES = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


class OutboxImageDeliveryTest(OutboxTestCase):
    """图片类投递（票 04）：正文在**发送时**才渲染成一张图，一行 = 一条图片消息。

    照片存的是采集图引用而不是 base64（ADR 0095），所以这一步要读盘、专项还要拼图。
    渲染不出来的内容重试多少次都是同一个结果（图被清理了 / 装不下），直接落终态并写明
    原因 —— 不无限重试，也不静默。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel = await self._channel("卫生群", URL_A)
        await self._subscribe([self.channel], topic=TOPIC_HYGIENE_PHOTO)
        self.outbox.register_renderer(TOPIC_HYGIENE_PHOTO, self._render_image)

    @staticmethod
    async def _render_image(_row, _params):
        return RenderedDelivery(image_bytes=IMAGE_BYTES)

    async def _enqueue_photo(self, reference="daily:1:白班:2026-05-02:cap-1"):
        return await self.outbox.enqueue_topic(
            self.db,
            TOPIC_HYGIENE_PHOTO,
            params={
                "ref_key": "1:白班:2026-05-02",
                "capture_id": "cap-1",
                "extra_capture_id": "",
            },
            trigger=PushTrigger.EVENT,
            business_reference=reference,
            summary="【卫生验收】案板 · 案板表面",
        )

    async def test_an_image_delivery_sends_exactly_one_image_message(self):
        await self._enqueue_photo()

        sent = await self.outbox.dispatch_pending(self.db)

        self.assertEqual(sent, 1)
        self.assertEqual(self.sender.images, [(URL_A, IMAGE_BYTES)])
        self.assertEqual(self.sender.sent, [], "图片类内容不该再发一条文字")
        row = (await self._rows())[0]
        self.assertEqual(row["status"], "sent")
        self.assertEqual(int(row["message_bytes"]), len(IMAGE_BYTES))
        self.assertEqual(row["content_summary"], "【卫生验收】案板 · 案板表面")

    async def test_a_render_failure_fails_the_row_and_is_never_retried(self):
        """图被清理 / 装不下不会因为重试而变好：记失败、写明原因、不再重试。"""

        async def broken(_row, _params):
            raise ValueError("图片已被清理（采集图 cap-1 不存在，可能已过保留期）")

        self.outbox.register_renderer(TOPIC_HYGIENE_PHOTO, broken)
        await self._enqueue_photo()

        self.assertEqual(await self.outbox.dispatch_pending(self.db), 0)
        self.clock += timedelta(hours=1)
        self.assertEqual(await self.outbox.dispatch_pending(self.db), 0)

        self.assertEqual(self.sender.images, [])
        row = (await self._rows())[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(int(row["attempts"]), 1)
        self.assertIn("图片已被清理", row["last_error"])
        self.assertIsNotNone(row["finished_at"])


class OutboxTransactionTest(OutboxTestCase):
    """登记与业务事务同生共死（票 04）：验收路径要的是「验收成功 = 这一行在」。

    出站默认自己提交（一条投递一个写单元）；``commit=False`` 让出站行落进**调用方的
    事务**里 —— 调用方回滚，这一行跟着消失；调用方提交，它才真的在。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel = await self._channel("卫生群", URL_A)
        await self._subscribe([self.channel])

    async def _enqueue_in_transaction(self, reference="ref-1"):
        return await self.outbox.enqueue_topic(
            self.db,
            TOPIC,
            params={"text": "验收通过，照片待发"},
            trigger=PushTrigger.EVENT,
            business_reference=reference,
            commit=False,
        )

    async def test_the_callers_rollback_discards_the_outbox_row(self):
        ids = await self._enqueue_in_transaction()

        await self.db._conn.rollback()

        self.assertEqual(len(ids), 1, "登记要拿到这一行的 id，才好回给调用方")
        self.assertEqual(await self._rows(), [])

    async def test_the_callers_commit_keeps_the_outbox_row(self):
        await self._enqueue_in_transaction()

        await self.db._conn.commit()

        rows = await self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pending")


class DigestParams(BaseModel):
    """夹具用的定时内容类型参数：日期口径 + 正文。

    营业日冻结是出站的通用行为（不挑内容类型），所以这里用注册表登记的夹具内容类型来
    验证——它同时带 `date_range_mode`（要冻结的那个）与 `text`（发送时渲染的正文）。
    """

    model_config = ConfigDict(extra="forbid")

    schedule_time: str = "05:50"
    date_range_mode: str = "today"
    text: str = ""


FIXTURE_TOPIC = "fixture_digest"


class OutboxBusinessDateTest(OutboxTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        register_topic(PushTopic(
            id=FIXTURE_TOPIC,
            name="夹具日报",
            triggers=frozenset({PushTrigger.SCHEDULED}),
            schedule_params_model=DigestParams,
        ))

    async def asyncTearDown(self):
        unregister_topic(FIXTURE_TOPIC)
        await super().asyncTearDown()

    async def _enqueue_fixture(self, mode="today", reference="2026-05-01", schedule_id=None):
        channel = await self._channel("日报群", URL_A)
        await self.db.wecom_subscription_upsert({
            "topic_id": FIXTURE_TOPIC, "target_channel_id": channel,
        })
        return await self.outbox.enqueue_topic(
            self.db,
            FIXTURE_TOPIC,
            params={"date_range_mode": mode, "text": "【日报】内容"},
            trigger=PushTrigger.SCHEDULED,
            business_reference=reference,
            schedule_id=schedule_id,
        )

    async def test_the_business_day_is_frozen_when_enqueued(self):
        """入队时就把「今天」解析成具体营业日：发送 / 补发时再解析一次就会换天。"""
        self.clock = datetime(2026, 5, 2, 5, 50, tzinfo=CHINA_TZ)

        await self._enqueue_fixture("today")

        params = json.loads((await self._rows())[0]["params_json"])
        self.assertEqual(params["business_date"], "2026-05-01")
        self.assertNotIn("date_range_mode", params, "留着它等于留一个以后再解析一次的入口")

    async def test_yesterday_mode_freezes_the_previous_business_day(self):
        self.clock = datetime(2026, 5, 2, 10, 0, tzinfo=CHINA_TZ)

        await self._enqueue_fixture("yesterday")

        params = json.loads((await self._rows())[0]["params_json"])
        self.assertEqual(params["business_date"], "2026-05-01")

    async def test_a_send_after_the_cut_keeps_the_frozen_business_day(self):
        """发送落在 06:00 之后也不换天：发送路径只读参数，不再解析「今天」。"""
        self.clock = datetime(2026, 5, 2, 5, 55, tzinfo=CHINA_TZ)
        await self._enqueue_fixture("today")

        self.clock = datetime(2026, 5, 2, 6, 5, tzinfo=CHINA_TZ)
        self.assertEqual(await self.outbox.dispatch_pending(self.db), 1)

        params = json.loads((await self._rows())[0]["params_json"])
        self.assertEqual(params["business_date"], "2026-05-01")

    async def test_scheduled_failure_is_redelivered_once_after_ten_minutes(self):
        """定时类失败在当天晚些补发**一次**；补发跨过 06:00 也还是同一个营业日。

        （`schedule_id` 非空就是定时投递 —— 事件类走的是退避重试，见下一条用例。）
        """
        self.clock = datetime(2026, 5, 2, 5, 55, tzinfo=CHINA_TZ)
        await self._enqueue_fixture("today", schedule_id=7)
        self.sender.failures = ["boom1", "boom2"]

        await self.outbox.dispatch_pending(self.db)
        row = (await self._rows())[0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(int(row["attempts"]), 1)
        self.assertEqual(
            row["scheduled_at"], (self.clock + timedelta(minutes=10)).isoformat()
        )

        # 补发时间没到：这一轮不动它
        self.assertEqual(await self.outbox.dispatch_pending(self.db), 0)
        self.assertEqual(len(self.sender.failures), 1)

        self.clock = datetime(2026, 5, 2, 6, 5, tzinfo=CHINA_TZ)  # 已跨过切日
        self.assertEqual(await self.outbox.dispatch_pending(self.db), 0)

        row = (await self._rows())[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(int(row["attempts"]), 2)
        self.assertIn("boom2", row["last_error"])
        params = json.loads(row["params_json"])
        self.assertEqual(params["business_date"], "2026-05-01")

    async def test_event_failure_retries_three_times_with_backoff_then_fails(self):
        channel = await self._channel("卫生群", URL_A)
        await self._subscribe([channel])
        await self._enqueue()
        self.sender.failures = ["boom1", "boom2", "boom3", "boom4"]

        await self.outbox.dispatch_pending(self.db)
        row = (await self._rows())[0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(int(row["attempts"]), 1)
        self.assertIn("boom1", row["last_error"])
        self.assertEqual(
            row["scheduled_at"], (self.clock + timedelta(minutes=1)).isoformat()
        )

        self.assertEqual(await self.outbox.dispatch_pending(self.db), 0, "没到点不该重试")
        self.assertEqual(len(self.sender.failures), 3)

        self.clock += timedelta(minutes=1)
        await self.outbox.dispatch_pending(self.db)
        row = (await self._rows())[0]
        self.assertEqual(int(row["attempts"]), 2)
        self.assertEqual(
            row["scheduled_at"], (self.clock + timedelta(minutes=5)).isoformat()
        )

        self.clock += timedelta(minutes=5)
        await self.outbox.dispatch_pending(self.db)
        row = (await self._rows())[0]
        self.assertEqual(int(row["attempts"]), 3)
        self.assertEqual(
            row["scheduled_at"], (self.clock + timedelta(minutes=15)).isoformat()
        )

        self.clock += timedelta(minutes=15)
        await self.outbox.dispatch_pending(self.db)
        row = (await self._rows())[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(int(row["attempts"]), 4, "首发 1 次 + 重试 3 次")
        self.assertIn("boom4", row["last_error"], "用尽后保留最后一次错误")
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual(await self.outbox.dispatch_pending(self.db), 0)


class OutboxThrottleTest(OutboxTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._old_limit = settings.WECOM_OUTBOX_RATE_LIMIT_PER_MINUTE
        settings.WECOM_OUTBOX_RATE_LIMIT_PER_MINUTE = 1

    async def asyncTearDown(self):
        settings.WECOM_OUTBOX_RATE_LIMIT_PER_MINUTE = self._old_limit
        await super().asyncTearDown()

    async def test_over_budget_deliveries_wait_in_the_queue(self):
        """超出本渠道这一分钟额度的投递**排队**，由下一轮接着发，不丢弃。"""
        channel = await self._channel("卫生群", URL_A)
        await self._subscribe([channel])
        await self._enqueue("第一封", reference="ref-1")
        await self._enqueue("第二封", reference="ref-2")

        self.assertEqual(await self.outbox.dispatch_pending(self.db), 1)
        self.assertEqual(self.sender.sent, [(URL_A, "第一封")])
        self.assertEqual(
            sorted(row["status"] for row in await self._rows()), ["pending", "sent"]
        )

        self.clock += timedelta(seconds=61)
        self.assertEqual(await self.outbox.dispatch_pending(self.db), 1)

        self.assertEqual([content for _, content in self.sender.sent], ["第一封", "第二封"])
        self.assertEqual({row["status"] for row in await self._rows()}, {"sent"})

    async def test_each_channel_has_its_own_budget(self):
        first = await self._channel("卫生群 A", URL_A)
        second = await self._channel("卫生群 B", URL_B)
        await self._subscribe([first, second])
        await self._enqueue("广播")

        self.assertEqual(await self.outbox.dispatch_pending(self.db), 2)

        self.assertEqual(
            {url for url, _ in self.sender.sent}, {URL_A, URL_B}
        )


class OutboxRetentionTest(OutboxTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel_id = await self._channel("卫生群", URL_A)
        self._old_days = settings.WECOM_OUTBOX_RETENTION_DAYS

    async def asyncTearDown(self):
        settings.WECOM_OUTBOX_RETENTION_DAYS = self._old_days
        await super().asyncTearDown()

    async def _row(self, *, status, days_ago, key):
        return await self.db.wecom_outbox_enqueue({
            "topic_id": TOPIC,
            "target_channel_id": self.channel_id,
            "idempotency_key": key,
            "status": status,
            "created_at": (self.clock - timedelta(days=days_ago)).isoformat(),
        })

    async def _ids(self):
        return {int(row["id"]) for row in await self.db.wecom_outbox_recent(limit=200)}

    async def test_purge_removes_expired_finished_rows_only(self):
        expired = await self._row(status="sent", days_ago=91, key="old-sent")
        recent = await self._row(status="sent", days_ago=1, key="new-sent")
        still_pending = await self._row(status="pending", days_ago=1200, key="old-pending")

        removed = await self.outbox.purge_expired(self.db)

        self.assertEqual(removed, 1)
        self.assertEqual(await self._ids(), {recent, still_pending})
        self.assertNotIn(expired, await self._ids())

    async def test_retention_days_is_configurable(self):
        settings.WECOM_OUTBOX_RETENTION_DAYS = 30
        expired = await self._row(status="failed", days_ago=31, key="old-failed")
        kept = await self._row(status="failed", days_ago=10, key="recent-failed")

        self.assertEqual(await self.outbox.purge_expired(self.db), 1)
        self.assertEqual(await self._ids(), {kept})

    async def test_zero_retention_days_keeps_everything(self):
        settings.WECOM_OUTBOX_RETENTION_DAYS = 0
        old = await self._row(status="sent", days_ago=3650, key="ancient")

        self.assertEqual(await self.outbox.purge_expired(self.db), 0)
        self.assertEqual(await self._ids(), {old})

    async def test_unfinished_rows_are_never_purged(self):
        """待发 / 发送中的行不管多老都不删：那是还没发出去的消息，删掉就是静默丢失。"""
        expired = await self._row(status="sent", days_ago=91, key="old-sent")
        pending = await self._row(status="pending", days_ago=91, key="old-pending")
        sending = await self._row(status="sending", days_ago=91, key="old-sending")

        self.assertEqual(await self.outbox.purge_expired(self.db), 1)
        self.assertEqual(await self._ids(), {pending, sending})
        self.assertNotIn(expired, await self._ids())


class OutboxRetentionScheduleTest(OutboxTestCase):
    """清理挂在既有 30 秒企微循环上（票 07 验收：确认清理确实在跑）。

    `purge_expired` 单独测过（上面那一类）；这一条测的是**循环真的会调它**——一个写好
    了但没人调用的清理等于没做，而发送记录表会一直长下去。

    派发与发送中兜底在这一条里桩掉：本用例要的是「清理跑起来了、且只碰终态行」，让
    派发把待发的行发出去（或标成跳过）反而把夹具本身变成了非终态 → 终态的过程，断言
    就没法钉在「老不老的待发行都不许删」上。两者本就是同一个循环里顺序执行的三件事，
    各自另有覆盖（`OutboxSchedulerLoopTest` / `OutboxStaleSendingTest`）。
    """

    ANCIENT_DAYS = 900

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # 清理有一小时一次的节流：不重置的话这一轮未必会跑（单例的窗口可能刚被用过）。
        wecom_outbox._next_purge_at = None
        self.channel_id = await self._channel("卫生群", URL_A)
        self.old_failed = await self._aged_row("loop-old-failed", "failed")
        self.old_pending = await self._aged_row("loop-old-pending", "pending")
        self.old_sending = await self._aged_row("loop-old-sending", "sending")

    async def _aged_row(self, key, status):
        return await self.db.wecom_outbox_enqueue({
            "topic_id": TOPIC,
            "target_channel_id": self.channel_id,
            "idempotency_key": key,
            "status": status,
            "created_at": (
                datetime.now(CHINA_TZ) - timedelta(days=self.ANCIENT_DAYS)
            ).isoformat(),
        })

    async def _ids(self):
        return {int(row["id"]) for row in await self.db.wecom_outbox_recent(limit=200)}

    async def test_the_resident_loop_purges_expired_rows_and_keeps_the_rest(self):
        with patch.object(wecom_outbox, "dispatch_pending", new=AsyncMock(return_value=0)), \
             patch.object(wecom_outbox, "requeue_stale_sending", new=AsyncMock(return_value=0)), \
             patch.object(wecom_push_service_module, "SCHEDULER_INTERVAL_SECONDS", 0.01):
            task = asyncio.create_task(wecom_push_service.scheduler_loop(self.db))
            try:
                await self._wait_for_purge()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        remaining = await self._ids()
        self.assertNotIn(self.old_failed, remaining, "过期的终态行该被清理掉")
        self.assertIn(self.old_pending, remaining, "待发的行不许被清理（哪怕很老）")
        self.assertIn(self.old_sending, remaining, "发送中的行不许被清理（哪怕很老）")

    async def _wait_for_purge(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.old_failed not in await self._ids():
                return
            await asyncio.sleep(0.02)
        self.fail(f"清理没有在 {timeout}s 内跑起来")


class OutboxRecordQueryTest(OutboxTestCase):
    """发送记录页要的那条查询（票 07）：筛选组合 + 分页 + 总数。

    缝隙沿用出站这条（spec「Testing Decisions」第 2 条）：断言的是**页面读到的行**——
    按时间倒序、筛选生效、每一页的行数与总数，不测 SQL 形状。
    """

    SECONDS_AGO = (0, 60, 120, 180, 240)

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel_a = await self._channel("卫生群", URL_A)
        self.channel_b = await self._channel("日报群", URL_B)
        self.ids = []
        for index, topic_id in enumerate(
            ("hygiene_reminder", "sales_report", "hygiene_reminder", "sales_report", "hygiene_reminder")
        ):
            status = "failed" if index in (1, 3) else "sent"
            self.ids.append(await self._row(
                key=f"record-{index}",
                topic_id=topic_id,
                status=status,
                seconds_ago=self.SECONDS_AGO[index],
                channel_id=self.channel_a if index % 2 == 0 else self.channel_b,
            ))
        self.newest, self.oldest = self.ids[0], self.ids[-1]

    async def _row(self, *, key, topic_id, status, seconds_ago, channel_id):
        return await self.db.wecom_outbox_enqueue({
            "topic_id": topic_id,
            "params_json": json.dumps({"text": key}),
            "target_channel_id": channel_id,
            "idempotency_key": key,
            "status": status,
            "created_at": (self.clock - timedelta(seconds=seconds_ago)).isoformat(),
        })

    async def test_records_come_newest_first_and_are_paginated(self):
        page = await self.db.wecom_outbox_page(page=1, page_size=2)

        self.assertEqual([row["id"] for row in page["rows"]],
                         [self.newest, self.ids[1]])
        self.assertEqual(page["total"], 5)
        self.assertEqual(page["page"], 1)
        self.assertEqual(page["page_size"], 2)
        self.assertEqual(page["pages"], 3)

        last = await self.db.wecom_outbox_page(page=3, page_size=2)
        self.assertEqual([row["id"] for row in last["rows"]], [self.oldest])

        empty = await self.db.wecom_outbox_page(page=4, page_size=2)
        self.assertEqual(empty["rows"], [])
        self.assertEqual(empty["total"], 5)

    async def test_filters_by_content_type_and_status_together(self):
        page = await self.db.wecom_outbox_page(
            topic_id="sales_report", status="failed", page_size=50
        )

        self.assertEqual(page["total"], 2)
        self.assertEqual([row["id"] for row in page["rows"]], [self.ids[1], self.ids[3]])
        self.assertEqual({row["topic_id"] for row in page["rows"]}, {"sales_report"})
        self.assertEqual({row["status"] for row in page["rows"]}, {"failed"})

    async def test_filters_by_channel(self):
        page = await self.db.wecom_outbox_page(channel_id=self.channel_b, page_size=50)

        self.assertEqual([row["id"] for row in page["rows"]], [self.ids[1], self.ids[3]])

    async def test_page_size_and_page_number_are_clamped(self):
        """页大小与页码都不信外部输入：0 / 负数 / 超上限一律收进安全区间。"""
        oversized = await self.db.wecom_outbox_page(page=0, page_size=9999)

        self.assertEqual(oversized["page_size"], 200, "页大小要有上限（一次最多 200 行）")
        self.assertEqual(oversized["page"], 1)


class OutboxChannelLastSentTest(OutboxTestCase):
    """渠道卡片上「最近一次发送成功时间」要按渠道聚合（票 07 顺手修的票 06 遗留）。

    旧写法是「取最近 200 条成功记录、按渠道取首条」：某个群长期没发过，它那条成功
    记录早就被挤出 200 条之外，卡片上就显示成「从未发送」—— 恰恰是「这个地址是不是
    失效了」最需要看的那一眼（用户故事 26）给出的却是错的。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel_quiet = await self._channel("长期未发的群", URL_A)
        self.channel_busy = await self._channel("每天在发的群", URL_B)

    async def _finished(self, channel_id, *, seconds_ago, key, status="sent"):
        """造一行**终态**记录，完成时间由夹具给（成功时间就是 `finished_at`）。"""
        stamp = (self.clock - timedelta(seconds=seconds_ago)).isoformat()
        tdb = self.db._connection.table("wecom_push_outbox")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """INSERT INTO wecom_push_outbox
                   (topic_id, params_json, target_channel_id, status, attempts,
                    last_error, idempotency_key, created_at, finished_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)""",
                ("sales_report", "{}", int(channel_id), status, "boom", key, stamp, stamp),
            )
        await tdb.commit()

    async def test_the_quiet_channel_keeps_its_own_last_success(self):
        """安静渠道的那一条成功记录不被别人的成功记录挤掉。"""
        await self._finished(self.channel_quiet, seconds_ago=86400, key="quiet-1")
        newest_quiet = (self.clock - timedelta(seconds=30)).isoformat()
        await self._finished(self.channel_quiet, seconds_ago=30, key="quiet-2")
        for index in range(250):
            await self._finished(self.channel_busy, seconds_ago=index, key=f"busy-{index}")

        last_sent = await self.db.wecom_channel_last_sent()

        self.assertEqual(last_sent.get(self.channel_quiet), newest_quiet,
                         "安静渠道的时间不该被 250 条新记录挤出结果")
        self.assertEqual(
            last_sent.get(self.channel_busy), (self.clock).isoformat(),
            "在发的渠道取它自己最近的那一条",
        )

    async def test_only_successful_deliveries_count(self):
        """失败 / 跳过 / 待发的行都不是「发送成功」：从没成功过的渠道不在结果里。"""
        await self._finished(self.channel_quiet, seconds_ago=10, key="quiet-failed",
                             status="failed")
        await self._finished(self.channel_quiet, seconds_ago=20, key="quiet-skipped",
                             status="skipped")

        last_sent = await self.db.wecom_channel_last_sent()

        self.assertNotIn(self.channel_quiet, last_sent)
        await self._finished(self.channel_quiet, seconds_ago=5, key="quiet-sent")

        self.assertIn(self.channel_quiet, await self.db.wecom_channel_last_sent())


class DyingSender(FakeSender):
    """模拟「进程在发送途中退出」：取消异常从发送器里穿出去。

    真实现场是崩溃 / systemd 重启 / **更新作业重启应用**：``mark_sending`` 已经落库，
    写终态的代码没跑到，这一行就停在 ``sending``。派发路径对 ``CancelledError`` 是
    往上抛（让关闭流程走完），所以它正好留下那一行，不用手工改库造夹具。
    """

    async def send_text(self, webhook_url, content):
        raise asyncio.CancelledError()


class OutboxStaleSendingTest(OutboxTestCase):
    """卡在「发送中」的行要有兜底（票 03 返工）：超阈值回待发，用尽记失败。"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._old_timeout = settings.WECOM_OUTBOX_SENDING_TIMEOUT_SECONDS
        self.channel = await self._channel("卫生群", URL_A)
        await self._subscribe([self.channel])

    async def asyncTearDown(self):
        settings.WECOM_OUTBOX_SENDING_TIMEOUT_SECONDS = self._old_timeout
        await super().asyncTearDown()

    async def _die_mid_send(self):
        """派发一轮，停在 mark_sending 与写终态之间；返回那一行（status=sending）。"""
        dying = WeComOutbox(sender=DyingSender(), now=lambda: self.clock, gap_seconds=0)
        with self.assertRaises(asyncio.CancelledError):
            await dying.dispatch_pending(self.db)
        row = (await self._rows())[0]
        self.assertEqual(row["status"], "sending", "夹具没把这一行留在发送中")
        return row

    async def test_sending_row_within_the_threshold_is_untouched(self):
        """正常在发（还没超阈值）的行不许被动：改了它就是制造重复投递。"""
        await self._enqueue("正在发的一封")
        row = await self._die_mid_send()
        sending_at = row["sending_at"]
        self.assertIsNotNone(sending_at, "进入发送的时刻要落库，否则兜底没有判据")

        self.clock += timedelta(seconds=100)  # < 默认 300

        self.assertEqual(await self.outbox.requeue_stale_sending(self.db), 0)
        after = (await self._rows())[0]
        self.assertEqual(after["status"], "sending")
        self.assertEqual(int(after["attempts"]), 0)
        self.assertEqual(after["sending_at"], sending_at)
        self.assertEqual(self.sender.sent, [], "没超阈值的行不该被重发")

    async def test_stale_sending_row_goes_back_to_the_queue_then_sent(self):
        """超阈值的行回到待发（attempts +1、保留原有错误），下一轮正常发出。"""
        await self._enqueue("卡住的一封")
        self.sender.failures = ["boom1"]

        self.assertEqual(await self.outbox.dispatch_pending(self.db), 0)
        self.assertEqual(int((await self._rows())[0]["attempts"]), 1)

        self.clock += timedelta(minutes=1)  # 退避到点
        row = await self._die_mid_send()
        self.assertEqual(int(row["attempts"]), 1, "崩掉的那次还没记进 attempts")
        self.assertIn("boom1", row["last_error"], "上一条错误不该被 mark_sending 抹掉")

        self.clock += timedelta(seconds=301)  # > 默认 300
        self.assertEqual(await self.outbox.requeue_stale_sending(self.db), 1)

        row = (await self._rows())[0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(int(row["attempts"]), 2, "这次尝试确实消耗了一次")
        self.assertIn("发送中进程退出", row["last_error"])
        self.assertIn("boom1", row["last_error"], "原有错误信息要保留")

        self.assertEqual(await self.outbox.dispatch_pending(self.db), 1)
        row = (await self._rows())[0]
        self.assertEqual(row["status"], "sent")
        self.assertEqual(int(row["attempts"]), 3)
        self.assertEqual([content for _, content in self.sender.sent], ["卡住的一封"])

    async def test_stale_sending_row_with_attempts_used_up_is_failed(self):
        """重试次数已用尽的行不许无限重发：直接记失败，原因可读。"""
        await self._enqueue("发不出去的一封")
        self.sender.failures = ["boom1", "boom2", "boom3"]
        for _ in range(3):  # 事件类上限 4：首发 + 两次退避重试之后只剩最后一次
            await self.outbox.dispatch_pending(self.db)
            self.clock += timedelta(minutes=16)

        row = (await self._rows())[0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(int(row["attempts"]), 3)

        await self._die_mid_send()  # 最后一次尝试中途进程退出

        self.clock += timedelta(seconds=301)
        self.assertEqual(await self.outbox.requeue_stale_sending(self.db), 1)

        row = (await self._rows())[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(int(row["attempts"]), 4, "崩掉的那次也算一次尝试")
        self.assertIn("发送中进程退出", row["last_error"])
        self.assertIn("重试次数已用尽", row["last_error"])
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual(await self.outbox.dispatch_pending(self.db), 0, "失败的行不该再发")
        self.assertEqual(self.sender.sent, [])

    async def test_zero_threshold_turns_the_safety_net_off(self):
        """0 = 不兜底：发送中的行永远不动（与节流 / 保留天数同一个口径）。"""
        settings.WECOM_OUTBOX_SENDING_TIMEOUT_SECONDS = 0
        await self._enqueue("卡住的一封")
        await self._die_mid_send()

        self.clock += timedelta(days=2)

        self.assertEqual(await self.outbox.requeue_stale_sending(self.db), 0)
        self.assertEqual((await self._rows())[0]["status"], "sending")

    async def test_the_pending_query_still_returns_pending_rows_only(self):
        """兜底另起一条查询：``wecom_outbox_pending`` 对其它调用方的语义不变。"""
        await self._enqueue("卡住的一封")
        row = await self._die_mid_send()

        self.assertEqual(await self.db.wecom_outbox_pending(), [])

        self.clock += timedelta(seconds=301)
        stale = await self.db.wecom_outbox_stale_sending(
            (self.clock - timedelta(seconds=300)).isoformat()
        )
        self.assertEqual([int(item["id"]) for item in stale], [int(row["id"])])

    async def test_a_row_stuck_before_the_migration_is_recovered_too(self):
        """迁移前就卡住的行没有 sending_at：按 created_at 兜底，不能永远捞不回来。"""
        outbox_id = await self.db.wecom_outbox_enqueue({
            "topic_id": TOPIC,
            "params_json": json.dumps({"text": "迁移前卡住的一封"}),
            "target_channel_id": self.channel,
            "idempotency_key": "legacy-stuck",
            "status": "sending",
            "created_at": (self.clock - timedelta(hours=2)).isoformat(),
        })
        self.assertIsNone((await self._rows())[0]["sending_at"], "旧行没有这一列的值")

        self.assertEqual(await self.outbox.requeue_stale_sending(self.db), 1)

        row = (await self._rows())[0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(int(row["attempts"]), 1)

        self.assertEqual(await self.outbox.dispatch_pending(self.db), 1)
        self.assertEqual((await self._rows())[0]["status"], "sent")
        self.assertEqual([content for _, content in self.sender.sent], ["迁移前卡住的一封"])
        self.assertEqual(int(outbox_id), int(row["id"]))


class OutboxZeroSubscriptionTest(OutboxTestCase):
    async def test_no_subscription_enqueues_nothing_and_logs(self):
        await self._channel("没人订阅的群", URL_A)

        with self.assertLogs("services.wecom_outbox", level="WARNING") as captured:
            ids = await self._enqueue()

        self.assertEqual(ids, [])
        self.assertEqual(await self._rows(), [])
        self.assertTrue(
            any("零订阅" in line for line in captured.output), captured.output
        )


class OutboxSchedulerLoopTest(OutboxTestCase):
    async def test_the_existing_wecom_loop_dispatches_the_outbox(self):
        """重试 / 补发 / 节流由现有 30 秒企微调度循环驱动 —— 不新增常驻 task。"""
        channel = await self._channel("卫生群", URL_A)
        await self._subscribe([channel])
        await self._enqueue("今天漏拍 2 项")
        sender = AsyncMock(return_value=(True, "ok"))

        with patch.object(wecom_push_service, "send_text", new=sender), patch.object(
            wecom_push_service_module, "SCHEDULER_INTERVAL_SECONDS", 0.01
        ):
            task = asyncio.create_task(wecom_push_service.scheduler_loop(self.db))
            try:
                row = await self._wait_for_status("sent")
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(row["status"], "sent")
        sender.assert_awaited()

    async def test_the_existing_wecom_loop_recovers_stuck_sending_rows(self):
        """兜底也挂在这条既有循环上（不新增常驻 task）：卡住的行会被捞回来重发。"""
        channel = await self._channel("卫生群", URL_A)
        await self._subscribe([channel])
        row_id = (await self._enqueue("卡住的一封"))[0]
        # 上一轮进程在发送途中退出留下的行：停在 sending，进入时刻是墙上时钟的一小时前
        await self.db.wecom_outbox_mark_sending(
            row_id,
            sending_at=(datetime.now(CHINA_TZ) - timedelta(hours=1)).isoformat(),
        )
        sender = AsyncMock(return_value=(True, "ok"))

        with patch.object(wecom_push_service, "send_text", new=sender), patch.object(
            wecom_push_service_module, "SCHEDULER_INTERVAL_SECONDS", 0.01
        ):
            task = asyncio.create_task(wecom_push_service.scheduler_loop(self.db))
            try:
                row = await self._wait_for_status("sent")
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(row["status"], "sent")
        self.assertEqual(int(row["attempts"]), 2, "崩掉的那次 + 这次成功")
        sender.assert_awaited()

    async def _wait_for_status(self, status, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = await self.db.wecom_outbox_recent()
            if rows and rows[0]["status"] == status:
                return rows[0]
            await asyncio.sleep(0.02)
        self.fail(f"出站行没有在 {timeout}s 内变成 {status}")


if __name__ == "__main__":
    unittest.main()
