#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收通过的实拍 → 订阅了「验收照片」的渠道（票 04：照片切到统一出站）。

锁住这条链路的外部行为：验收**通过时**在同一个事务里登记一行待发（与验收同生共死，
「验收成功却没登记」不存在）、真正的读图与发送由统一出站在卫生写锁之外做、同一张采集图
只投递一次、驳回不投递、员工重拍后再通过是新的一次投递；以及三个边界：图被清理 /
图装不下 → 记失败并写明原因、不无限重试；没有任何订阅目标时一条不发，也不在以后补发
旧照片。

假发送器与假采集库沿用既有缝隙（`FakeCaptureStore` + `patch.object(wecom_push_service,
"send_image", …)`）；出站状态机本身在 `tests/test_wecom_outbox.py` 里测。
"""

import asyncio
import contextlib
import io
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from PIL import Image

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.attire import HygieneAttire
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.wecom_share import HygieneCaptureSharer
from services.hygiene.work import HygieneWork, HygieneWorkError
from services.wecom_outbox import WeComOutbox
from services.wecom_push_service import (
    encrypt_webhook_url,
    mask_webhook_url,
    wecom_push_service,
)
from services.wecom_push_topics import TOPIC_HYGIENE_PHOTO
from tests.hygiene_duty import assign_duty

SUPER = {"kind": "super"}
EMPLOYEE_ID = 10
PHONE = "13800138010"

URL_HYGIENE = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hygiene-key-0000"
URL_DAILY = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=daily-key-0000"


class HygieneCaptureShareTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
        self.captures = FakeCaptureStore()
        # 每条用例一个出站实例：节流窗口在内存里，共用一个单例会让用例互相影响。
        # 图片渲染器就是 sharer 自己（发送时才读采集图），与 main.py 的接线同一形状。
        self.outbox = WeComOutbox(now=lambda: self.fixed_now, gap_seconds=0)
        self.sharer = HygieneCaptureSharer(
            self.db, self.captures, outbox=self.outbox, now=lambda: self.fixed_now
        )
        self.outbox.register_renderer(TOPIC_HYGIENE_PHOTO, self.sharer.render)
        self.work = HygieneWork(
            self.db,
            captures=self.captures,
            now=lambda: self.fixed_now,
            image_variants=ImageVariantGenerator(),
            capture_sharer=self.sharer,
        )
        await self.work.prepare()
        self.attire = HygieneAttire(self.work, now=lambda: self.fixed_now)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    # ── 夹具 ──────────────────────────────────────────────────────────────

    def _photo(self, size=(1200, 900), color=(180, 160, 140)) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", size, color).save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    def _live(self, data: bytes) -> dict:
        return {"bytes": data, "content_type": "image/jpeg", "live": True}

    def _staff(self, shift="白班") -> dict:
        return {
            "kind": "staff",
            "id": EMPLOYEE_ID,
            "permission": "普通员工",
            "name": "员工",
            "phone": PHONE,
            "shift": shift,
        }

    async def _channel(self, name="卫生群", url=URL_HYGIENE, *, enabled=True) -> int:
        return await self.db.wecom_webhook_create({
            "name": name,
            "webhook_url_encrypted": encrypt_webhook_url(url),
            "webhook_url_masked": mask_webhook_url(url),
            "enabled": enabled,
            "notes": "",
        })

    async def _subscribe(self, channel_id: int, topic=TOPIC_HYGIENE_PHOTO) -> int:
        return await self.db.wecom_subscription_upsert({
            "topic_id": topic,
            "target_channel_id": channel_id,
        })

    async def _subscription_ready(self, **kwargs) -> int:
        """「订阅了验收照片的渠道」——这条链路唯一的收件人来源。"""
        channel_id = await self._channel(**kwargs)
        await self._subscribe(channel_id)
        return channel_id

    async def _item(self) -> int:
        """一个检查项（提交不依赖排班：员工 actor 自带班次与姓名）。"""
        zones = await self.work.list_zones()
        zone = next(z for z in zones if z["name"] == "案板")
        item = await self.work.add_daily_item(
            SUPER,
            zone["id"],
            "案板表面",
            {"bytes": self._photo((200, 200)), "content_type": "image/jpeg", "markup": []},
        )
        return int(item["id"])

    async def _submit(self, item_id: int, photo: bytes) -> dict:
        return await self.work.submit_daily(
            self._staff(), item_id, self._live(photo), shift="白班"
        )

    async def _outbox_rows(self) -> list:
        return await self.db.wecom_outbox_recent()

    async def _share_rows(self) -> list:
        """旧的分享登记表：票 01 已把行搬进出站表，这张表只剩只读历史。"""
        cur = await self.db._conn.execute(
            """SELECT id, kind, ref_key, capture_id, extra_capture_id, caption,
                      status, attempts, last_error
                 FROM hygiene_wecom_shares ORDER BY id"""
        )
        return [dict(row) for row in await cur.fetchall()]

    async def _variant_id(self, source_capture_id: str, variant: str):
        cur = await self.db._conn.execute(
            """SELECT capture_id FROM hygiene_capture_variants
                WHERE source_capture_id = ? AND variant = ?""",
            (source_capture_id, variant),
        )
        row = await cur.fetchone()
        return None if row is None else str(dict(row)["capture_id"])

    async def _delete_capture(self, source_capture_id: str) -> None:
        """采集图连同它的变体一起消失（保留期清理 / 人工删图）。"""
        for variant in ("preview", "thumb"):
            variant_id = await self._variant_id(source_capture_id, variant)
            if variant_id:
                await self.captures.delete_async(variant_id)
        await self.captures.delete_async(source_capture_id)

    @contextlib.asynccontextmanager
    async def _sender(self, *, image_ok=True):
        image = AsyncMock(return_value=(image_ok, "ok" if image_ok else "image boom"))
        with patch.object(wecom_push_service, "send_image", new=image):
            yield image

    async def _dispatch(self) -> int:
        """真正的发送：由既有 30 秒企微调度循环驱动，不新增常驻 task。"""
        return await self.outbox.dispatch_pending(self.db)

    # ── 主路径 ────────────────────────────────────────────────────────────

    async def test_accept_registers_one_pending_delivery(self):
        channel_id = await self._subscription_ready()
        item_id = await self._item()
        submitted = await self._submit(item_id, self._photo())

        await self.work.accept_daily(SUPER, item_id, "白班")

        rows = await self._outbox_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["topic_id"], TOPIC_HYGIENE_PHOTO)
        self.assertEqual(int(rows[0]["target_channel_id"]), channel_id)
        self.assertEqual(rows[0]["status"], "pending")
        # 发送记录的内容摘要：哪一项、什么时候验的（**不带员工姓名**）
        summary = rows[0]["content_summary"]
        self.assertIn("案板", summary)
        self.assertIn("案板表面", summary)
        self.assertIn("验收通过", summary)
        self.assertNotIn("员工", summary)
        # 参数里存的是**采集图引用**（不是 base64），发送时才去读
        self.assertIn(str(submitted["capture_id"]), rows[0]["params_json"])

    async def test_the_registration_joins_the_callers_transaction(self):
        """出站行落进**调用方的事务**：调用方回滚它就不该在，提交了才真的在。

        这条就是「验收成功但没登记」的反面 —— 登记与验收同生共死。
        """
        await self._subscription_ready()

        created = await self.sharer.enqueue(
            kind="daily", ref_key="1:白班:2026-09-13", capture_id="cap-1", caption="x"
        )
        await self.db._conn.rollback()
        self.assertTrue(created)
        self.assertEqual(await self._outbox_rows(), [], "登记自己提交了，验收就会漏掉它")

        await self.sharer.enqueue(
            kind="daily", ref_key="1:白班:2026-09-13", capture_id="cap-1", caption="x"
        )
        await self.db._conn.commit()
        self.assertEqual(len(await self._outbox_rows()), 1)

    async def test_the_dispatcher_sends_the_preview_photo(self):
        await self._subscription_ready()
        item_id = await self._item()
        submitted = await self._submit(item_id, self._photo())
        source_capture_id = str(submitted["capture_id"])
        await self.work.accept_daily(SUPER, item_id, "白班")

        async with self._sender() as image:
            sent = await self._dispatch()

        self.assertEqual(sent, 1)
        self.assertEqual(image.await_count, 1)
        self.assertEqual(image.await_args.args[0], URL_HYGIENE)
        preview_id = await self._variant_id(source_capture_id, "preview")
        self.assertIsNotNone(preview_id)
        self.assertEqual(image.await_args.args[1], self.captures.get(preview_id))
        row = (await self._outbox_rows())[0]
        self.assertEqual(row["status"], "sent")
        self.assertEqual(int(row["message_bytes"]), len(self.captures.get(preview_id)))

    async def test_accept_does_not_wait_for_the_send(self):
        """验收只登记：读图与网络发送在卫生写锁之外，由 30 秒调度循环驱动。"""
        await self._subscription_ready()
        item_id = await self._item()
        await self._submit(item_id, self._photo())

        async def slow_send(_url, _data):
            await asyncio.sleep(0.6)
            return True, "ok"

        sender = AsyncMock(side_effect=slow_send)
        with patch.object(wecom_push_service, "send_image", new=sender):
            started = time.monotonic()
            await self.work.accept_daily(SUPER, item_id, "白班")
            elapsed = time.monotonic() - started

            self.assertEqual(sender.await_count, 0, "验收不该在写锁里发照片")
        self.assertLess(elapsed, 0.4, "验收在等发送，写锁被网络 IO 占住了")
        self.assertEqual((await self._outbox_rows())[0]["status"], "pending")

    async def test_the_legacy_share_table_is_no_longer_written(self):
        """旧的分享登记表停写、只留只读历史（它的行票 01 已搬进出站表）。"""
        await self._subscription_ready()
        item_id = await self._item()
        await self._submit(item_id, self._photo())

        await self.work.accept_daily(SUPER, item_id, "白班")

        self.assertEqual(await self._share_rows(), [])
        self.assertEqual(len(await self._outbox_rows()), 1)

    async def test_same_photo_is_never_delivered_twice(self):
        await self._subscription_ready()
        item_id = await self._item()
        await self._submit(item_id, self._photo())

        await self.work.accept_daily(SUPER, item_id, "白班")
        with self.assertRaises(HygieneWorkError):
            await self.work.accept_daily(SUPER, item_id, "白班")

        async with self._sender() as image:
            await self._dispatch()
            await self._dispatch()

        self.assertEqual(len(await self._outbox_rows()), 1)
        self.assertEqual(image.await_count, 1)

    async def test_reject_does_not_register(self):
        await self._subscription_ready()
        item_id = await self._item()
        await self._submit(item_id, self._photo())

        await self.work.reject_daily(SUPER, item_id, "白班", reason="有水渍")

        self.assertEqual(await self._outbox_rows(), [])

    async def test_reshoot_after_reject_delivers_the_new_photo(self):
        await self._subscription_ready()
        item_id = await self._item()
        await self._submit(item_id, self._photo(color=(120, 120, 120)))
        await self.work.reject_daily(SUPER, item_id, "白班", reason="有水渍")
        reshot = await self._submit(item_id, self._photo(color=(240, 240, 240)))
        source_capture_id = str(reshot["capture_id"])

        await self.work.accept_daily(SUPER, item_id, "白班")

        async with self._sender() as image:
            await self._dispatch()

        rows = await self._outbox_rows()
        self.assertEqual(len(rows), 1, "驳回那次不该留行，通过这次只留一行")
        self.assertIn(source_capture_id, rows[0]["params_json"])
        preview_id = await self._variant_id(source_capture_id, "preview")
        self.assertEqual(image.await_args.args[1], self.captures.get(preview_id))

    async def test_every_subscribed_channel_gets_the_photo(self):
        first = await self._subscription_ready()
        second = await self._channel("验收群 B", URL_DAILY)
        await self._subscribe(second)
        item_id = await self._item()
        await self._submit(item_id, self._photo())

        await self.work.accept_daily(SUPER, item_id, "白班")
        async with self._sender() as image:
            await self._dispatch()

        self.assertEqual(
            {call.args[0] for call in image.await_args_list}, {URL_HYGIENE, URL_DAILY}
        )
        self.assertEqual(
            {int(row["target_channel_id"]) for row in await self._outbox_rows()},
            {first, second},
        )

    # ── 专项：前后对照拼成一张图 ──────────────────────────────────────────

    async def _deep_clean_item(self) -> int:
        item = await self.work.add_deep_clean_item(
            SUPER, self.fixed_now.weekday(), "冷柜一号"
        )
        return int(item["id"])

    async def _submit_deep_clean(
        self, item_id: int, before: bytes, after: bytes
    ) -> dict:
        return await self.work.submit_deep_clean_pair(
            self._staff(), item_id, self._live(before), self._live(after)
        )

    async def test_deep_clean_pair_is_composed_into_one_photo(self):
        await self._subscription_ready()
        item_id = await self._deep_clean_item()
        submitted = await self._submit_deep_clean(
            item_id,
            self._photo((800, 600), (220, 40, 40)),
            self._photo((800, 600), (40, 40, 220)),
        )

        await self.work.accept_deep_clean_pair(SUPER, item_id)

        rows = await self._outbox_rows()
        self.assertEqual(len(rows), 1)
        params = rows[0]["params_json"]
        self.assertIn(str(submitted["after_capture_id"]), params)
        self.assertIn(str(submitted["before_capture_id"]), params)
        self.assertIn("专项前后对照", rows[0]["content_summary"])
        self.assertIn("左 前 / 右 后", rows[0]["content_summary"])

        async with self._sender() as image:
            sent = await self._dispatch()

        self.assertEqual(sent, 1)
        self.assertEqual(image.await_count, 1)  # 一张拼图 = 一条图片消息
        composed = Image.open(io.BytesIO(image.await_args.args[1]))
        self.assertGreater(composed.width, composed.height)

    async def test_deep_clean_accept_twice_delivers_once(self):
        await self._subscription_ready()
        item_id = await self._deep_clean_item()
        await self._submit_deep_clean(
            item_id, self._photo((400, 400), (10, 10, 10)), self._photo((400, 400), (240, 240, 240))
        )
        await self.work.accept_deep_clean_pair(SUPER, item_id)
        with self.assertRaises(HygieneWorkError):
            await self.work.accept_deep_clean_pair(SUPER, item_id)

        async with self._sender() as image:
            await self._dispatch()
            await self._dispatch()

        self.assertEqual(len(await self._outbox_rows()), 1)
        self.assertEqual(image.await_count, 1)

    async def test_deep_clean_reject_does_not_register(self):
        await self._subscription_ready()
        item_id = await self._deep_clean_item()
        await self._submit_deep_clean(
            item_id, self._photo((400, 400), (10, 10, 10)), self._photo((400, 400), (240, 240, 240))
        )

        await self.work.reject_deep_clean_pair(SUPER, item_id, reason="还有水渍")

        self.assertEqual(await self._outbox_rows(), [])

    # ── 整改回拍 ──────────────────────────────────────────────────────────

    async def _fix_ticket(self) -> int:
        zones = await self.work.list_zones()
        zone = next(z for z in zones if z["name"] == "案板")
        ticket = await self.work.open_fix(
            SUPER,
            zone["id"],
            "卫生",
            "案板有油",
            timedelta(hours=2),
            self._live(self._photo((200, 200))),
        )
        await self.work.reshoot_fix(
            self._staff(), int(ticket["id"]), self._live(self._photo((800, 600)))
        )
        return int(ticket["id"])

    async def test_fix_reshoot_is_delivered_after_accept(self):
        await self._subscription_ready()
        ticket_id = await self._fix_ticket()

        await self.work.accept_fix(SUPER, ticket_id)

        rows = await self._outbox_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn("整改回拍", rows[0]["content_summary"])
        self.assertIn("案板", rows[0]["content_summary"])
        self.assertIn("卫生", rows[0]["content_summary"])

        async with self._sender() as image:
            sent = await self._dispatch()

        self.assertEqual(sent, 1)
        self.assertEqual(image.await_count, 1)
        self.assertEqual((await self._outbox_rows())[0]["status"], "sent")

    async def test_fix_accept_twice_delivers_once(self):
        await self._subscription_ready()
        ticket_id = await self._fix_ticket()
        await self.work.accept_fix(SUPER, ticket_id)
        with self.assertRaises(HygieneWorkError):
            await self.work.accept_fix(SUPER, ticket_id)

        async with self._sender() as image:
            await self._dispatch()
            await self._dispatch()

        self.assertEqual(len(await self._outbox_rows()), 1)
        self.assertEqual(image.await_count, 1)

    async def test_fix_reject_does_not_register(self):
        await self._subscription_ready()
        ticket_id = await self._fix_ticket()

        await self.work.reject_fix(SUPER, ticket_id, reason="还有油")

        self.assertEqual(await self._outbox_rows(), [])

    # ── 仪容仪表 ──────────────────────────────────────────────────────────

    async def _on_duty_employee(self) -> int:
        now_iso = self.fixed_now.isoformat()
        await self.db._conn.execute(
            """INSERT INTO hygiene_employees
                   (id, phone, name, password_hash, permission, approved,
                    created_at, updated_at)
               VALUES (?, ?, ?, '', '普通员工', 1, ?, ?)""",
            (EMPLOYEE_ID, PHONE, "张三", now_iso, now_iso),
        )
        await self.db._conn.commit()
        await assign_duty(self.db, EMPLOYEE_ID, slot="day", now=self.fixed_now)
        return EMPLOYEE_ID

    async def _set_attire_standard(self) -> None:
        await self.attire.set_standard(
            {"bytes": self._photo((300, 300)), "content_type": "image/jpeg", "markup": []}
        )

    async def test_attire_accept_delivers_without_naming_the_person(self):
        await self._subscription_ready()
        employee_id = await self._on_duty_employee()
        await self._set_attire_standard()
        await self.attire.submit(employee_id, self._live(self._photo((600, 800))))

        await self.attire.accept(employee_id)

        rows = await self._outbox_rows()
        self.assertEqual(len(rows), 1)
        summary = rows[0]["content_summary"]
        self.assertIn("仪容仪表", summary)
        self.assertNotIn("张三", summary)
        self.assertNotIn(PHONE, summary)
        self.assertNotIn(PHONE[-4:], summary)
        self.assertNotIn("普通员工", summary)

        async with self._sender() as image:
            sent = await self._dispatch()

        self.assertEqual(sent, 1)
        self.assertEqual(image.await_count, 1)

    async def test_attire_reshoot_after_reject_delivers_the_new_photo(self):
        await self._subscription_ready()
        employee_id = await self._on_duty_employee()
        await self._set_attire_standard()
        await self.attire.submit(
            employee_id, self._live(self._photo((600, 800), (10, 10, 10)))
        )
        await self.attire.reject(employee_id, "领口没扣好")
        await self.attire.submit(
            employee_id, self._live(self._photo((600, 800), (240, 240, 240)))
        )
        capture_id = str(await self.attire.pending_capture(employee_id))

        await self.attire.accept(employee_id)

        rows = await self._outbox_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn(capture_id, rows[0]["params_json"])

    async def test_attire_accept_twice_delivers_once(self):
        await self._subscription_ready()
        employee_id = await self._on_duty_employee()
        await self._set_attire_standard()
        await self.attire.submit(employee_id, self._live(self._photo((600, 800))))
        await self.attire.accept(employee_id)
        with self.assertRaises(HygieneWorkError):
            await self.attire.accept(employee_id)

        async with self._sender() as image:
            await self._dispatch()
            await self._dispatch()

        self.assertEqual(len(await self._outbox_rows()), 1)
        self.assertEqual(image.await_count, 1)

    async def test_attire_reject_does_not_register(self):
        await self._subscription_ready()
        employee_id = await self._on_duty_employee()
        await self._set_attire_standard()
        await self.attire.submit(employee_id, self._live(self._photo((600, 800))))

        await self.attire.reject(employee_id, "领口没扣好")

        self.assertEqual(await self._outbox_rows(), [])

    # ── 空态与边界 ────────────────────────────────────────────────────────

    async def test_no_subscription_registers_nothing_and_never_backfills(self):
        """没有订阅目标：一条不发、不重试，以后订阅了也不补发这张旧照片。"""
        await self._channel("没人订阅的群", URL_DAILY)
        item_id = await self._item()
        await self._submit(item_id, self._photo())

        accepted = await self.work.accept_daily(SUPER, item_id, "白班")

        self.assertEqual(accepted["status"], "已通过")
        self.assertEqual(await self._outbox_rows(), [])

        await self._subscription_ready()  # 事后才订阅
        async with self._sender() as image:
            self.assertEqual(await self._dispatch(), 0)

        self.assertEqual(image.await_count, 0)

    async def test_a_disabled_subscribed_channel_is_skipped_and_recorded(self):
        """渠道停用不动订阅：仍然登记一行，派发时标成跳过并写明原因，一条都不发。"""
        await self._subscription_ready(enabled=False)
        item_id = await self._item()
        await self._submit(item_id, self._photo())

        await self.work.accept_daily(SUPER, item_id, "白班")
        async with self._sender() as image:
            await self._dispatch()

        self.assertEqual(image.await_count, 0)
        row = (await self._outbox_rows())[0]
        self.assertEqual(row["status"], "skipped")
        self.assertIn("停用", row["last_error"])

    async def test_missing_outbox_table_does_not_break_accept(self):
        """迁移 0016 还没应用时：验收照常成功，只是这张照片不发。

        （真去 DROP 表会污染同一个测试库的其它用例，所以直接把探测结果钉成"没有表"。）
        """
        await self._subscription_ready()
        item_id = await self._item()
        await self._submit(item_id, self._photo())
        self.sharer._outbox_ready = False

        accepted = await self.work.accept_daily(SUPER, item_id, "白班")

        self.assertEqual(accepted["status"], "已通过")
        self.assertEqual(await self._outbox_rows(), [])
        self.assertEqual(await self._dispatch(), 0)

    async def test_a_cleaned_photo_fails_with_a_clear_reason(self):
        """图被清理：记失败并写明原因，不无限重试。"""
        await self._subscription_ready()
        item_id = await self._item()
        submitted = await self._submit(item_id, self._photo())
        await self.work.accept_daily(SUPER, item_id, "白班")
        await self._delete_capture(str(submitted["capture_id"]))

        async with self._sender() as image:
            self.assertEqual(await self._dispatch(), 0)
            self.fixed_now += timedelta(hours=1)
            self.assertEqual(await self._dispatch(), 0)

        self.assertEqual(image.await_count, 0)
        row = (await self._outbox_rows())[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(int(row["attempts"]), 1)
        self.assertIn("图片已被清理", row["last_error"])

    async def test_a_photo_cleaned_during_retry_fails_instead_of_retrying_forever(self):
        """发送失败要重试，但重试时图已经被清理：记失败并写明「图片已被清理」。"""
        await self._subscription_ready()
        item_id = await self._item()
        submitted = await self._submit(item_id, self._photo())
        await self.work.accept_daily(SUPER, item_id, "白班")

        async with self._sender(image_ok=False) as image:
            self.assertEqual(await self._dispatch(), 0)
            row = (await self._outbox_rows())[0]
            self.assertEqual(row["status"], "pending")
            self.assertEqual(int(row["attempts"]), 1)
            self.assertIn("image boom", row["last_error"])
            self.assertEqual(
                row["scheduled_at"], (self.fixed_now + timedelta(minutes=1)).isoformat()
            )

            await self._delete_capture(str(submitted["capture_id"]))
            self.fixed_now += timedelta(minutes=1)
            self.assertEqual(await self._dispatch(), 0)
            self.fixed_now += timedelta(hours=1)
            self.assertEqual(await self._dispatch(), 0)

        self.assertEqual(image.await_count, 1, "图都没了就不该再发")
        row = (await self._outbox_rows())[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(int(row["attempts"]), 2)
        self.assertIn("图片已被清理", row["last_error"])

    async def test_unreadable_pair_photo_does_not_break_the_accept(self):
        """任一张读不到：验收照样落库，这次投递记失败并写清原因。"""
        await self._subscription_ready()
        item_id = await self._deep_clean_item()
        submitted = await self._submit_deep_clean(
            item_id, self._photo((400, 400), (10, 10, 10)), self._photo((400, 400), (240, 240, 240))
        )
        await self._delete_capture(str(submitted["before_capture_id"]))

        accepted = await self.work.accept_deep_clean_pair(SUPER, item_id)

        self.assertEqual(accepted["status"], "已通过")
        async with self._sender() as image:
            await self._dispatch()
        self.assertEqual(image.await_count, 0)
        row = (await self._outbox_rows())[0]
        self.assertEqual(int(row["attempts"]), 1)
        self.assertIn("图片已被清理", row["last_error"])

    async def test_oversized_photo_fails_with_the_size_reason(self):
        """降档到底仍然装不下：记失败并写明字节数与上限，不静默、不无限重试。"""
        await self._subscription_ready()
        oversized = b"P" * (3 * 1024 * 1024)  # 3MB：超过群机器人 image 的 2MB 硬线
        source_capture_id = self.captures.put(oversized)
        await self.sharer.enqueue(
            kind="daily",
            ref_key="1:白班:2026-09-13",
            capture_id=source_capture_id,
            caption="超限",
        )
        await self.db._conn.commit()

        async with self._sender() as image:
            self.assertEqual(await self._dispatch(), 0)

        self.assertEqual(image.await_count, 0)
        row = (await self._outbox_rows())[0]
        self.assertEqual(row["status"], "failed")
        self.assertIn(str(len(oversized)), row["last_error"])
        self.assertIn("超过企业微信 image 上限", row["last_error"])

    async def test_oversized_preview_falls_back_to_thumb(self):
        await self._subscription_ready()
        oversized = b"P" * (3 * 1024 * 1024)  # 3MB：超过群机器人 image 的 2MB 硬线
        thumb = b"T" * 2048
        source_capture_id = self.captures.put(b"ORIGINAL-BYTES")
        preview_id = self.captures.put(oversized)
        thumb_id = self.captures.put(thumb)
        for variant, capture_id, size in (
            ("preview", preview_id, len(oversized)),
            ("thumb", thumb_id, len(thumb)),
        ):
            await self.db._conn.execute(
                """INSERT INTO hygiene_capture_variants
                   (source_capture_id, variant, capture_id, content_type,
                    width, height, byte_size, content_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_capture_id,
                    variant,
                    capture_id,
                    "image/jpeg",
                    1200,
                    900,
                    size,
                    "0" * 64,
                    self.fixed_now.isoformat(),
                ),
            )
        await self.db._conn.commit()
        await self.sharer.enqueue(
            kind="daily",
            ref_key="1:白班:2026-09-13",
            capture_id=source_capture_id,
            caption="超限降档",
        )
        await self.db._conn.commit()

        async with self._sender() as image:
            sent = await self._dispatch()

        self.assertEqual(sent, 1)
        self.assertEqual(image.await_args.args[1], thumb)

    async def test_original_is_used_when_variants_are_missing(self):
        """老照片还没回填变体：原图装得下就直接发原图。"""
        await self._subscription_ready()
        raw = self._photo((300, 300))
        source_capture_id = self.captures.put(raw)
        await self.sharer.enqueue(
            kind="daily",
            ref_key="2:白班:2026-09-13",
            capture_id=source_capture_id,
            caption="没有变体",
        )
        await self.db._conn.commit()

        async with self._sender() as image:
            await self._dispatch()

        self.assertEqual(image.await_args.args[1], raw)


if __name__ == "__main__":
    unittest.main()
