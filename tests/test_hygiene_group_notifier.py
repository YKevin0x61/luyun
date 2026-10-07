#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生群消息的收件人（票 03）：按**推送订阅**入队，由统一出站发给订阅了「卫生提醒」的渠道。

旧口径是渠道上的 `hygiene_feed` 布尔列（「这是卫生群」）。迁移 0016 把 `hygiene_feed = 1`
的渠道回填成 `hygiene_reminder` 订阅，所以收件人与迁移前逐条一致；从这里开始这条链路
可查、可重试、可补发。这里锁住新口径，以及两个最危险的边界：**零订阅时一条都不发**
（只记日志），以及**入队失败不阻塞卫生业务**（漏拍扫描 / 整改开单照常走完）。
"""

import tempfile
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.notifier import WeComGroupTextNotifier
from services.wecom_outbox import WeComOutbox
from services.wecom_push_service import (
    encrypt_webhook_url,
    mask_webhook_url,
    wecom_push_service,
)

TOPIC = "hygiene_reminder"
OTHER_TOPIC = "sales_report"

URL_HYGIENE = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hygiene-key-0000"
URL_DAILY = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=daily-key-0000"


class HygieneGroupNotifierTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        # 每条用例一个出站实例：节流窗口在内存里，共用一个单例会让用例之间互相影响。
        self.outbox = WeComOutbox()
        self.notifier = WeComGroupTextNotifier(self.db, outbox=self.outbox)

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

    async def _subscribe(self, channel_id, topic=TOPIC):
        return await self.db.wecom_subscription_upsert({
            "topic_id": topic,
            "target_channel_id": channel_id,
        })

    async def _group(self, name, channel_ids, *, enabled=True):
        group_id = await self.db.wecom_channel_group_create({"name": name})
        if not enabled:
            await self.db.wecom_channel_group_update(group_id, {"enabled": False})
        for channel_id in channel_ids:
            await self.db.wecom_channel_group_add_member(group_id, channel_id)
        return group_id

    async def _notify(self, text="日常漏拍：案板表面 ×2", notifier=None):
        """入队 + 派发（派发由 30 秒调度循环在真实运行里驱动）。"""
        sender = AsyncMock(return_value=(True, "ok"))
        with patch.object(wecom_push_service, "send_text", new=sender):
            await (notifier or self.notifier).notify_group_text(text)
            await self.outbox.dispatch_pending(self.db)
        return sender

    async def test_only_the_subscribed_group_gets_the_text(self):
        daily = await self._channel("日报群", URL_DAILY)
        await self._subscribe(daily, topic=OTHER_TOPIC)
        hygiene = await self._channel("卫生群", URL_HYGIENE)
        await self._subscribe(hygiene)

        sender = await self._notify()

        self.assertEqual(sender.await_count, 1)
        self.assertEqual(sender.await_args.args[0], URL_HYGIENE)

    async def test_a_channel_without_the_subscription_gets_nothing(self):
        await self._channel("日报群", URL_DAILY)

        sender = await self._notify()

        self.assertEqual(sender.await_count, 0)
        self.assertEqual(await self.db.wecom_outbox_recent(), [])

    async def test_a_disabled_subscribed_channel_gets_nothing_and_is_recorded_as_skipped(self):
        """渠道停用不动订阅：它仍然是一行出站记录，只是标成跳过并写明原因。"""
        hygiene = await self._channel("停用的卫生群", URL_HYGIENE, enabled=False)
        await self._subscribe(hygiene)

        sender = await self._notify()

        self.assertEqual(sender.await_count, 0)
        rows = await self.db.wecom_outbox_recent()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "skipped")
        self.assertIn("停用", rows[0]["last_error"])

    async def test_every_subscribed_channel_gets_the_text(self):
        first = await self._channel("卫生群 A", URL_HYGIENE)
        second = await self._channel("卫生群 B", URL_DAILY)
        await self._subscribe(first)
        await self._subscribe(second)

        sender = await self._notify()

        self.assertEqual(sender.await_count, 2)
        self.assertEqual(
            {call.args[0] for call in sender.await_args_list}, {URL_HYGIENE, URL_DAILY}
        )

    async def test_a_group_subscription_reaches_its_members(self):
        """群组整体订阅：成员各收一份。"""
        first = await self._channel("卫生群 A", URL_HYGIENE)
        second = await self._channel("卫生群 B", URL_DAILY)
        group_id = await self._group("门店群组", [first, second])
        await self.db.wecom_subscription_upsert({
            "topic_id": TOPIC, "target_group_id": group_id,
        })

        sender = await self._notify()

        self.assertEqual(
            {call.args[0] for call in sender.await_args_list}, {URL_HYGIENE, URL_DAILY}
        )

    async def test_a_disabled_group_only_pauses_its_own_subscription(self):
        """群组停用只暂停这一组的订阅：成员自己的订阅照常收到。"""
        member = await self._channel("卫生群", URL_HYGIENE)
        await self._subscribe(member)
        group_id = await self._group("停用群组", [member], enabled=False)
        await self.db.wecom_subscription_upsert({
            "topic_id": TOPIC, "target_group_id": group_id,
        })

        sender = await self._notify()

        self.assertEqual(sender.await_count, 1)
        self.assertEqual(sender.await_args.args[0], URL_HYGIENE)

    async def test_a_repeated_identical_reminder_on_the_same_day_is_enqueued_once(self):
        """同一营业日的同一段文案对同一渠道只落一行（同一内容同一目标只投递一次）。"""
        hygiene = await self._channel("卫生群", URL_HYGIENE)
        await self._subscribe(hygiene)

        await self._notify("日常漏拍：案板表面 ×2")
        await self._notify("日常漏拍：案板表面 ×2")

        self.assertEqual(len(await self.db.wecom_outbox_recent()), 1)

    async def test_the_same_text_on_another_business_day_is_a_new_delivery(self):
        """幂等只作用在同一营业日内：明天的同一段提醒照发，不会被昨天的键挡住。"""
        hygiene = await self._channel("卫生群", URL_HYGIENE)
        await self._subscribe(hygiene)
        clock = datetime(2026, 5, 2, 21, 0, tzinfo=CHINA_TZ)
        notifier = WeComGroupTextNotifier(
            self.db, outbox=self.outbox, now=lambda: clock
        )

        await self._notify("日常漏拍：案板表面 ×2", notifier=notifier)
        clock = datetime(2026, 5, 3, 21, 0, tzinfo=CHINA_TZ)
        await self._notify("日常漏拍：案板表面 ×2", notifier=notifier)

        self.assertEqual(len(await self.db.wecom_outbox_recent()), 2)

    async def test_the_send_log_carries_a_readable_summary(self):
        """发送记录的内容摘要取正文首行（页面照着它认这是哪一封）。"""
        hygiene = await self._channel("卫生群", URL_HYGIENE)
        await self._subscribe(hygiene)

        await self._notify("日常漏拍：案板表面 ×2\n详情：白班未拍")

        rows = await self.db.wecom_outbox_recent()
        self.assertEqual(rows[0]["content_summary"], "日常漏拍：案板表面 ×2")

    async def test_long_text_is_still_split_per_channel(self):
        """超长文案仍按字节分块发全，且只发给订阅了卫生提醒的渠道。"""
        await self._channel("日报群", URL_DAILY)
        hygiene = await self._channel("卫生群", URL_HYGIENE)
        await self._subscribe(hygiene)
        long_text = "\n".join(f"第 {i} 项：" + "补" * 60 for i in range(60))

        sender = await self._notify(long_text)

        self.assertGreater(sender.await_count, 1)
        self.assertTrue(all(call.args[0] == URL_HYGIENE for call in sender.await_args_list))
        rows = await self.db.wecom_outbox_recent()
        self.assertEqual(len(rows), 1, "拆成多条消息仍只算一次投递")

    async def test_blank_text_is_ignored(self):
        hygiene = await self._channel("卫生群", URL_HYGIENE)
        await self._subscribe(hygiene)

        sender = await self._notify("   \n  ")

        self.assertEqual(sender.await_count, 0)
        self.assertEqual(await self.db.wecom_outbox_recent(), [])

    async def test_enqueue_failure_never_breaks_the_hygiene_flow(self):
        """入队失败只记日志：漏拍扫描 / 整改开单不因为推送发不出去而失败。"""
        hygiene = await self._channel("卫生群", URL_HYGIENE)
        await self._subscribe(hygiene)

        with patch.object(
            self.outbox,
            "enqueue_topic",
            new=AsyncMock(side_effect=RuntimeError("数据库不可用")),
        ):
            with self.assertLogs("services.hygiene.notifier", level="WARNING") as captured:
                await self.notifier.notify_group_text("日常漏拍：案板表面 ×2")

        self.assertTrue(any("入队失败" in line for line in captured.output), captured.output)


if __name__ == "__main__":
    unittest.main()
