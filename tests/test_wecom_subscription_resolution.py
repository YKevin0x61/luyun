#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""订阅求解（票 03 唯一新增的测试缝隙）：内容类型 → 目标渠道集合。

口径来自 spec 的「领域模型」与「出站与发送记录」两节：

- **并集去重**：同一渠道被多条路径命中（既属于被订阅的群组、又被单独订阅）时只投递一次；
- **群组停用**只暂停这一组的订阅，成员自身的订阅不受影响；
- **渠道停用**则订阅保留、投递跳过，并在出站记录里标注原因（所以求解结果里它**还在**，
  带一个跳过原因，而不是被悄悄丢掉）；
- 某内容类型**零订阅**时输出空集合（一条不发，由调用方记日志）。

求解在服务层（`services.wecom_outbox.resolve_targets`），定时 / 事件 / 手工三条触发
路径共用同一份断言。这里只测外部行为：算出哪些渠道、哪些带跳过原因，不测 SQL 形状。
"""

import tempfile
import unittest

from config import settings
from database import DatabaseManager
from services.wecom_outbox import ResolvedTarget, resolve_targets
from services.wecom_push_service import encrypt_webhook_url, mask_webhook_url

TOPIC = "hygiene_reminder"
OTHER_TOPIC = "sales_report"

URL_A = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hygiene-key-0000"
URL_B = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=daily-key-0000"


class SubscriptionResolutionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

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

    async def _subscribe_channel(self, channel_id, topic=TOPIC):
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

    async def _subscribe_group(self, group_id, topic=TOPIC):
        return await self.db.wecom_subscription_upsert({
            "topic_id": topic,
            "target_group_id": group_id,
        })

    async def test_channel_subscription_resolves_to_that_channel(self):
        channel_id = await self._channel("卫生群", URL_A)
        await self._subscribe_channel(channel_id)

        targets = await resolve_targets(self.db, TOPIC)

        self.assertEqual(
            targets,
            [ResolvedTarget(channel_id=channel_id, channel_name="卫生群", enabled=True, skipped_reason="")],
        )

    async def test_group_subscription_resolves_to_its_members(self):
        first = await self._channel("卫生群 A", URL_A)
        second = await self._channel("卫生群 B", URL_B)
        group_id = await self._group("门店群组", [first, second])
        await self._subscribe_group(group_id)

        targets = await resolve_targets(self.db, TOPIC)

        self.assertEqual({target.channel_id for target in targets}, {first, second})

    async def test_union_dedupes_a_channel_hit_by_two_paths(self):
        """既属于被订阅的群组、又被单独订阅 —— 只算一个目标（避免同一封发两遍）。"""
        channel_id = await self._channel("卫生群", URL_A)
        group_id = await self._group("门店群组", [channel_id])
        await self._subscribe_group(group_id)
        await self._subscribe_channel(channel_id)

        targets = await resolve_targets(self.db, TOPIC)

        self.assertEqual([target.channel_id for target in targets], [channel_id])

    async def test_only_the_subscribed_topic_is_resolved(self):
        channel_id = await self._channel("日报群", URL_A)
        await self._subscribe_channel(channel_id, topic=OTHER_TOPIC)

        self.assertEqual(await resolve_targets(self.db, TOPIC), [])

    async def test_disabled_subscription_is_not_a_target(self):
        channel_id = await self._channel("卫生群", URL_A)
        await self.db.wecom_subscription_upsert({
            "topic_id": TOPIC,
            "target_channel_id": channel_id,
            "enabled": False,
        })

        self.assertEqual(await resolve_targets(self.db, TOPIC), [])

    async def test_disabled_group_pauses_only_its_own_subscription(self):
        """群组停用只暂停这一组的订阅：成员自己的订阅照常命中。"""
        subscribed_alone = await self._channel("自己订了卫生提醒", URL_A)
        await self._subscribe_channel(subscribed_alone)
        only_in_group = await self._channel("只在停用群组里", URL_B)
        group_id = await self._group("停用群组", [subscribed_alone, only_in_group], enabled=False)
        await self._subscribe_group(group_id)

        targets = await resolve_targets(self.db, TOPIC)

        self.assertEqual([target.channel_id for target in targets], [subscribed_alone])

    async def test_disabled_channel_is_still_resolved_with_a_skip_reason(self):
        """渠道停用不动订阅：它仍被求解出来，只是带着跳过原因（记录里要标注）。"""
        channel_id = await self._channel("停用的卫生群", URL_A, enabled=False)
        await self._subscribe_channel(channel_id)

        targets = await resolve_targets(self.db, TOPIC)

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].channel_id, channel_id)
        self.assertFalse(targets[0].enabled)
        self.assertIn("停用", targets[0].skipped_reason)

    async def test_zero_subscriptions_resolves_to_nothing(self):
        await self._channel("没人订阅的群", URL_A)

        self.assertEqual(await resolve_targets(self.db, TOPIC), [])


if __name__ == "__main__":
    unittest.main()
