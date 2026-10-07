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
from services.wecom_outbox import WeComOutbox
from services.wecom_push_service import (
    WECOM_TEXT_BYTE_LIMIT,
    encrypt_webhook_url,
    mask_webhook_url,
    wecom_push_service,
)
from services.wecom_push_topics import (
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
        self.failures = []

    async def send_text(self, webhook_url, content):
        if self.failures:
            return False, self.failures.pop(0)
        self.sent.append((webhook_url, content))
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
