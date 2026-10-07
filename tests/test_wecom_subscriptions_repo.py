#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""订阅 / 渠道群组 / 群组成员 / 出站记录的读写能力（迁移 0016 的表）。

Repo 层是后续票据（订阅矩阵、统一出站、发送记录页）共用的数据入口，所以这里断言
的是**行为**：订阅可以增删查、群组与成员可以增删查、出站行可以从待发走到终态。
不测 SQL 形状，也不测内部调用顺序。
"""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager

HYGIENE_TOPIC = "hygiene_reminder"
SALES_TOPIC = "sales_report"


class WeComSubscriptionRepoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.channel_id = await self.db.wecom_webhook_create({
            "name": "卫生群",
            "webhook_url_encrypted": "enc",
            "webhook_url_masked": "masked",
            "enabled": True,
            "notes": "",
        })
        self.channel_b = await self.db.wecom_webhook_create({
            "name": "日报群",
            "webhook_url_encrypted": "enc2",
            "webhook_url_masked": "masked2",
            "enabled": True,
            "notes": "",
        })

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_channel_subscription_round_trip(self):
        await self.db.wecom_subscription_upsert({
            "topic_id": HYGIENE_TOPIC,
            "target_channel_id": self.channel_id,
        })

        rows = await self.db.wecom_subscriptions_all(topic_id=HYGIENE_TOPIC)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["topic_id"], HYGIENE_TOPIC)
        self.assertEqual(int(rows[0]["target_channel_id"]), self.channel_id)
        self.assertIsNone(rows[0]["target_group_id"])
        self.assertTrue(rows[0]["enabled"])

    async def test_upsert_is_idempotent_for_the_same_topic_and_channel(self):
        """同一目标被多条路径命中时只留一行 —— 并集去重靠的就是这条唯一索引。"""
        first = await self.db.wecom_subscription_upsert({
            "topic_id": HYGIENE_TOPIC,
            "target_channel_id": self.channel_id,
        })
        second = await self.db.wecom_subscription_upsert({
            "topic_id": HYGIENE_TOPIC,
            "target_channel_id": self.channel_id,
        })

        self.assertEqual(first, second)
        self.assertEqual(len(await self.db.wecom_subscriptions_all()), 1)

    async def test_two_topics_are_independent_rows(self):
        await self.db.wecom_subscription_upsert({
            "topic_id": HYGIENE_TOPIC, "target_channel_id": self.channel_id})
        await self.db.wecom_subscription_upsert({
            "topic_id": SALES_TOPIC, "target_channel_id": self.channel_id})

        self.assertEqual(len(await self.db.wecom_subscriptions_all()), 2)
        self.assertEqual(len(await self.db.wecom_subscriptions_all(topic_id=SALES_TOPIC)), 1)

    async def test_disable_and_reenable_keeps_the_row(self):
        """取消勾选只是停用：店长重新勾上时不用重配。"""
        sub_id = await self.db.wecom_subscription_upsert({
            "topic_id": HYGIENE_TOPIC,
            "target_channel_id": self.channel_id,
            "enabled": False,
        })

        disabled = await self.db.wecom_subscriptions_all()
        self.assertFalse(disabled[0]["enabled"])

        updated = await self.db.wecom_subscription_upsert({
            "topic_id": HYGIENE_TOPIC,
            "target_channel_id": self.channel_id,
            "enabled": True,
        })
        self.assertEqual(updated, sub_id)
        self.assertEqual(len(await self.db.wecom_subscriptions_all()), 1)
        self.assertTrue((await self.db.wecom_subscriptions_all())[0]["enabled"])

    async def test_delete_subscription(self):
        sub_id = await self.db.wecom_subscription_upsert({
            "topic_id": HYGIENE_TOPIC,
            "target_channel_id": self.channel_id,
        })

        self.assertTrue(await self.db.wecom_subscription_delete(sub_id))
        self.assertEqual(await self.db.wecom_subscriptions_all(), [])

    async def test_subscription_can_target_a_group_and_is_shared_by_its_members(self):
        group_id = await self.db.wecom_channel_group_create({"name": "日报群组"})
        await self.db.wecom_channel_group_add_member(group_id, self.channel_id)
        await self.db.wecom_channel_group_add_member(group_id, self.channel_b)

        await self.db.wecom_subscription_upsert({
            "topic_id": SALES_TOPIC,
            "target_group_id": group_id,
        })

        rows = await self.db.wecom_subscriptions_all(topic_id=SALES_TOPIC)
        self.assertEqual(int(rows[0]["target_group_id"]), group_id)
        self.assertIsNone(rows[0]["target_channel_id"])
        members = await self.db.wecom_channel_group_members(group_id)
        self.assertEqual({int(m["channel_id"]) for m in members},
                         {self.channel_id, self.channel_b})

    async def test_group_crud_and_duplicate_membership(self):
        group_id = await self.db.wecom_channel_group_create({"name": "门店 A"})
        self.assertTrue(await self.db.wecom_channel_group_add_member(group_id, self.channel_id))
        # 同一渠道重复加入同一群组：幂等，不报错也不产生第二行。
        self.assertTrue(await self.db.wecom_channel_group_add_member(group_id, self.channel_id))
        self.assertEqual(len(await self.db.wecom_channel_group_members(group_id)), 1)

        self.assertTrue(await self.db.wecom_channel_group_update(group_id, {"name": "门店 B"}))
        listed = await self.db.wecom_channel_groups_all()
        self.assertEqual(listed[0]["name"], "门店 B")

        self.assertTrue(await self.db.wecom_channel_group_remove_member(group_id, self.channel_id))
        self.assertEqual(await self.db.wecom_channel_group_members(group_id), [])

        self.assertTrue(await self.db.wecom_channel_group_delete(group_id))
        self.assertEqual(await self.db.wecom_channel_groups_all(), [])

    async def test_a_channel_can_belong_to_two_groups(self):
        """「日报群组」与「门店 A」并存 —— 一个渠道可以属于多个群组。"""
        daily = await self.db.wecom_channel_group_create({"name": "日报群组"})
        store = await self.db.wecom_channel_group_create({"name": "门店 A"})
        await self.db.wecom_channel_group_add_member(daily, self.channel_id)
        await self.db.wecom_channel_group_add_member(store, self.channel_id)

        groups = await self.db.wecom_channel_groups_of_channel(self.channel_id)
        self.assertEqual({int(g["id"]) for g in groups}, {daily, store})

    async def test_deleting_a_channel_drops_its_subscriptions_and_memberships(self):
        group_id = await self.db.wecom_channel_group_create({"name": "门店 A"})
        await self.db.wecom_channel_group_add_member(group_id, self.channel_id)
        await self.db.wecom_subscription_upsert({
            "topic_id": HYGIENE_TOPIC, "target_channel_id": self.channel_id})

        self.assertTrue(await self.db.wecom_webhook_delete(self.channel_id))

        self.assertEqual(await self.db.wecom_subscriptions_all(), [])
        self.assertEqual(await self.db.wecom_channel_group_members(group_id), [])
        # 群组本身留着：成员少了不等于这个群组该消失。
        self.assertEqual(len(await self.db.wecom_channel_groups_all()), 1)

    async def test_group_creation_rejects_a_blank_name(self):
        with self.assertRaises(ValueError):
            await self.db.wecom_channel_group_create({"name": "   "})

    async def test_duplicate_group_name_does_not_silently_reuse_the_group(self):
        """同名群组返回 0，而不是把调用方接到另一个群组上（那会改到别人的收件人）。"""
        first = await self.db.wecom_channel_group_create({"name": "日报群组"})

        self.assertEqual(await self.db.wecom_channel_group_create({"name": "日报群组"}), 0)
        self.assertEqual(len(await self.db.wecom_channel_groups_all()), 1)
        self.assertNotEqual(first, 0)

    async def test_missing_group_returns_none_and_does_not_create_a_member(self):
        self.assertIsNone(await self.db.wecom_channel_group_get(999999))
        self.assertFalse(await self.db.wecom_channel_group_add_member(999999, self.channel_id))

    async def test_members_of_every_group_in_one_read(self):
        """页面上「每个群组有哪些成员」一次读完，不按群组逐条查。"""
        daily = await self.db.wecom_channel_group_create({"name": "日报群组"})
        store = await self.db.wecom_channel_group_create({"name": "门店 A"})
        await self.db.wecom_channel_group_add_member(daily, self.channel_id)
        await self.db.wecom_channel_group_add_member(store, self.channel_b)

        members = await self.db.wecom_channel_group_members_map()

        self.assertEqual({int(m["channel_id"]) for m in members[daily]}, {self.channel_id})
        self.assertEqual({int(m["channel_id"]) for m in members[store]}, {self.channel_b})
        self.assertEqual(members.get(999999, []), [])

    async def test_delete_subscription_by_its_target(self):
        """页面上取消勾选时把那一行删掉（停用另有 upsert 的 enabled 路径）。"""
        await self.db.wecom_subscription_upsert({
            "topic_id": HYGIENE_TOPIC, "target_channel_id": self.channel_id})
        await self.db.wecom_subscription_upsert({
            "topic_id": SALES_TOPIC, "target_channel_id": self.channel_id})

        removed = await self.db.wecom_subscription_delete_for(
            HYGIENE_TOPIC, target_channel_id=self.channel_id
        )

        self.assertEqual(removed, 1)
        remaining = await self.db.wecom_subscriptions_all()
        self.assertEqual([row["topic_id"] for row in remaining], [SALES_TOPIC])

    async def test_delete_subscription_for_an_untouched_target_removes_nothing(self):
        self.assertEqual(
            await self.db.wecom_subscription_delete_for(
                HYGIENE_TOPIC, target_group_id=999999
            ),
            0,
        )


class WeComOutboxRepoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.other = await self.db.wecom_webhook_create({
            "name": "另一个群",
            "webhook_url_encrypted": "enc",
            "webhook_url_masked": "masked",
            "enabled": True,
            "notes": "",
        })

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _enqueue(self, key: str = "hygiene:r1", **overrides):
        item = {
            "topic_id": HYGIENE_TOPIC,
            "target_channel_id": self.other,
            "content_summary": "漏拍汇总",
            "message_bytes": 128,
            "scheduled_at": "2026-10-01T21:30:00+08:00",
        }
        item.update(overrides)
        item.setdefault("idempotency_key", f"{key}:{item.get('target_channel_id')}")
        return await self.db.wecom_outbox_enqueue(item)

    async def test_enqueue_and_read_pending(self):
        outbox_id = await self._enqueue()

        rows = await self.db.wecom_outbox_pending()
        self.assertEqual([int(r["id"]) for r in rows], [outbox_id])
        self.assertEqual(rows[0]["topic_id"], HYGIENE_TOPIC)
        self.assertEqual(int(rows[0]["target_channel_id"]), self.other)
        self.assertEqual(int(rows[0]["attempts"]), 0)

    async def test_the_same_idempotency_key_is_delivered_once(self):
        """同一内容同一目标只落一行 —— 重试点验收、弱网重传都不刷屏。"""
        first = await self._enqueue()
        second = await self._enqueue()

        self.assertEqual(first, second)
        self.assertEqual(len(await self.db.wecom_outbox_pending()), 1)

    async def test_a_different_target_is_a_new_delivery(self):
        await self._enqueue()
        await self._enqueue(idempotency_key="hygiene:r1:other")

        self.assertEqual(len(await self.db.wecom_outbox_pending()), 2)

    async def test_mark_sent_removes_it_from_pending_and_records_the_finish(self):
        outbox_id = await self._enqueue()

        self.assertTrue(await self.db.wecom_outbox_mark_sent(outbox_id, message_bytes=256))

        self.assertEqual(await self.db.wecom_outbox_pending(), [])
        row = await self.db.wecom_outbox_get(outbox_id)
        self.assertEqual(row["status"], "sent")
        self.assertEqual(int(row["message_bytes"]), 256)
        self.assertTrue(row["finished_at"])
        self.assertEqual(row["last_error"], "")

    async def test_retry_keeps_attempts_and_the_error_for_diagnosis(self):
        outbox_id = await self._enqueue()

        self.assertTrue(await self.db.wecom_outbox_mark_retry(
            outbox_id, error="webhook 超时", status="pending", attempts=1))

        row = await self.db.wecom_outbox_get(outbox_id)
        self.assertEqual(int(row["attempts"]), 1)
        self.assertEqual(row["last_error"], "webhook 超时")
        self.assertEqual(row["status"], "pending")

    async def test_mark_failed_is_terminal(self):
        outbox_id = await self._enqueue()

        self.assertTrue(await self.db.wecom_outbox_mark_failed(
            outbox_id, error="重试用尽", attempts=3))

        row = await self.db.wecom_outbox_get(outbox_id)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(int(row["attempts"]), 3)
        self.assertNotIn(outbox_id, [int(r["id"]) for r in await self.db.wecom_outbox_pending()])

    async def test_recent_records_filter_by_topic_channel_and_status(self):
        mine = await self._enqueue()
        await self._enqueue(idempotency_key="hygiene:r2:other")
        await self.db.wecom_outbox_mark_sent(mine)

        self.assertEqual(len(await self.db.wecom_outbox_recent()), 2)
        self.assertEqual(
            [int(r["id"]) for r in await self.db.wecom_outbox_recent(status="sent")], [mine])
        self.assertEqual(
            len(await self.db.wecom_outbox_recent(status="pending")), 1)
        self.assertEqual(
            len(await self.db.wecom_outbox_recent(topic_id=SALES_TOPIC)), 0)
        self.assertEqual(
            len(await self.db.wecom_outbox_recent(channel_id=self.other)), 2)

    async def test_purge_keeps_unfinished_rows_whatever_their_age(self):
        """清理只删终态行：还没发出去的行不能被当成过期记录删掉。"""
        old_sent = await self._enqueue(
            idempotency_key="hygiene:old", created_at="2026-06-01T00:00:00+08:00")
        fresh_sent = await self._enqueue(
            idempotency_key="hygiene:new", created_at="2026-10-01T00:00:00+08:00")
        pending = await self._enqueue(
            idempotency_key="hygiene:todo", created_at="2026-06-01T00:00:00+08:00")
        await self.db.wecom_outbox_mark_sent(old_sent)
        await self.db.wecom_outbox_mark_sent(fresh_sent)

        removed = await self.db.wecom_outbox_purge_finished_before("2026-09-01T00:00:00+08:00")

        self.assertEqual(removed, 1, "只有那条已发且过期的记录被清掉")
        remaining = {int(r["id"]) for r in await self.db.wecom_outbox_recent()}
        self.assertEqual(remaining, {fresh_sent, pending})

    async def test_enqueue_rejects_a_row_without_a_target(self):
        with self.assertRaises(ValueError):
            await self._enqueue(target_channel_id=None, idempotency_key="hygiene:no-target")

    async def test_recent_records_can_be_filtered_by_one_channel(self):
        """渠道卡片上的「最近一次发送成功」按渠道取一条，不必拉整页再挑。"""
        mine = await self._enqueue()
        await self._enqueue(idempotency_key="hygiene:r2:other")
        await self.db.wecom_outbox_mark_sent(mine)

        rows = await self.db.wecom_outbox_recent(
            channel_id=self.other, status="sent", limit=1
        )

        self.assertEqual([int(r["id"]) for r in rows], [mine])


if __name__ == "__main__":
    unittest.main()
