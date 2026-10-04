#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收通过的实拍 → 卫生群（票 03：日常检查的第一条完整链路）。

锁住这条链路的外部行为：验收后群里收到**一张照片**（2026-10 起不再发说明文字）、同一张照片
只发一次、驳回不发、没勾卫生群时验收照常成功但分享记为未发送、图片超限降档、发送失败会
重试并写日志，以及**验收不等发送**（发送是秒级网络 IO，绝不能占着卫生的写锁）。
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
from services.hygiene.wecom_share import (
    SHARE_STATUS_FAILED,
    SHARE_STATUS_PENDING,
    SHARE_STATUS_SENT,
    SHARE_STATUS_SKIPPED,
    HygieneCaptureSharer,
)
from services.hygiene.work import HygieneWork, HygieneWorkError
from services.wecom_push_service import (
    encrypt_webhook_url,
    mask_webhook_url,
    wecom_push_service,
)
from tests.hygiene_duty import assign_duty

SUPER = {"kind": "super"}
EMPLOYEE_ID = 10
PHONE = "13800138010"


class HygieneCaptureShareTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
        self.captures = FakeCaptureStore()
        self.sharer = HygieneCaptureSharer(
            self.db, self.captures, autoflush=False, now=lambda: self.fixed_now
        )
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
        task = self.sharer._flush_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
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

    async def _hygiene_hook(self, name="卫生群", *, hygiene_feed=True, enabled=True) -> int:
        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={name}-0000"
        return await self.db.wecom_webhook_create({
            "name": name,
            "webhook_url_encrypted": encrypt_webhook_url(url),
            "webhook_url_masked": mask_webhook_url(url),
            "enabled": enabled,
            "hygiene_feed": hygiene_feed,
            "notes": "",
        })

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

    async def _share_rows(self) -> list:
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

    async def _log_rows(self) -> list:
        cur = await self.db._conn.execute(
            "SELECT push_type, status, error FROM wecom_push_logs ORDER BY id"
        )
        return [dict(row) for row in await cur.fetchall()]

    @contextlib.asynccontextmanager
    async def _sender(self, *, text_ok=True, image_ok=True):
        text = AsyncMock(return_value=(text_ok, "ok" if text_ok else "text boom"))
        image = AsyncMock(return_value=(image_ok, "ok" if image_ok else "image boom"))
        with patch.object(wecom_push_service, "send_text", new=text), patch.object(
            wecom_push_service, "send_image", new=image
        ):
            yield text, image

    # ── 主路径 ────────────────────────────────────────────────────────────

    async def test_accept_sends_the_preview_photo(self):
        await self._hygiene_hook()
        item_id = await self._item()
        photo = self._photo()
        submitted = await self._submit(item_id, photo)
        source_capture_id = str(submitted["capture_id"])

        await self.work.accept_daily(SUPER, item_id, "白班")

        rows = await self._share_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "daily")
        self.assertEqual(rows[0]["status"], SHARE_STATUS_PENDING)
        self.assertIn("案板", rows[0]["caption"])
        self.assertIn("案板表面", rows[0]["caption"])
        self.assertIn("验收通过", rows[0]["caption"])
        # 不带员工姓名
        self.assertNotIn("员工", rows[0]["caption"])

        async with self._sender() as (text, image):
            sent = await self.sharer.flush_pending()

        self.assertEqual(sent, 1)
        # 只推图片：说明文字不再发到群里（caption 仍落库留档）
        self.assertEqual(text.await_count, 0)
        self.assertEqual(image.await_count, 1)
        preview_id = await self._variant_id(source_capture_id, "preview")
        self.assertIsNotNone(preview_id)
        self.assertEqual(image.await_args.args[1], self.captures.get(preview_id))
        self.assertEqual((await self._share_rows())[0]["status"], SHARE_STATUS_SENT)
        logs = await self._log_rows()
        self.assertEqual(logs[0]["push_type"], "hygiene_photo")
        self.assertEqual(logs[0]["status"], "success")

    async def test_same_photo_is_never_shared_twice(self):
        await self._hygiene_hook()
        item_id = await self._item()
        await self._submit(item_id, self._photo())

        await self.work.accept_daily(SUPER, item_id, "白班")
        with self.assertRaises(HygieneWorkError):
            await self.work.accept_daily(SUPER, item_id, "白班")

        async with self._sender() as (_text, image):
            await self.sharer.flush_pending()
            await self.sharer.flush_pending()

        self.assertEqual(len(await self._share_rows()), 1)
        self.assertEqual(image.await_count, 1)

    async def test_reject_does_not_share(self):
        await self._hygiene_hook()
        item_id = await self._item()
        await self._submit(item_id, self._photo())

        await self.work.reject_daily(SUPER, item_id, "白班", reason="有水渍")

        self.assertEqual(await self._share_rows(), [])

    async def test_reshoot_after_reject_shares_the_new_photo(self):
        await self._hygiene_hook()
        item_id = await self._item()
        await self._submit(item_id, self._photo(color=(120, 120, 120)))
        await self.work.reject_daily(SUPER, item_id, "白班", reason="有水渍")
        reshot = await self._submit(item_id, self._photo(color=(240, 240, 240)))
        source_capture_id = str(reshot["capture_id"])

        await self.work.accept_daily(SUPER, item_id, "白班")

        async with self._sender() as (_text, image):
            await self.sharer.flush_pending()

        rows = await self._share_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["capture_id"], source_capture_id)
        preview_id = await self._variant_id(source_capture_id, "preview")
        self.assertEqual(image.await_args.args[1], self.captures.get(preview_id))

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
        await self._hygiene_hook()
        item_id = await self._deep_clean_item()
        submitted = await self._submit_deep_clean(
            item_id,
            self._photo((800, 600), (220, 40, 40)),
            self._photo((800, 600), (40, 40, 220)),
        )

        await self.work.accept_deep_clean_pair(SUPER, item_id)

        rows = await self._share_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "deep_clean")
        self.assertEqual(rows[0]["capture_id"], str(submitted["after_capture_id"]))
        self.assertEqual(
            rows[0]["extra_capture_id"], str(submitted["before_capture_id"])
        )
        self.assertIn("专项前后对照", rows[0]["caption"])
        self.assertIn("左 前 / 右 后", rows[0]["caption"])

        async with self._sender() as (text, image):
            sent = await self.sharer.flush_pending()

        self.assertEqual(sent, 1)
        # 只推图片：说明文字不再发到群里（caption 仍落库留档）
        self.assertEqual(text.await_count, 0)
        self.assertEqual(image.await_count, 1)  # 一张拼图 = 一条图片消息
        composed = Image.open(io.BytesIO(image.await_args.args[1]))
        self.assertGreater(composed.width, composed.height)

    async def test_deep_clean_accept_twice_shares_once(self):
        await self._hygiene_hook()
        item_id = await self._deep_clean_item()
        await self._submit_deep_clean(
            item_id, self._photo((400, 400), (10, 10, 10)), self._photo((400, 400), (240, 240, 240))
        )
        await self.work.accept_deep_clean_pair(SUPER, item_id)
        with self.assertRaises(HygieneWorkError):
            await self.work.accept_deep_clean_pair(SUPER, item_id)

        async with self._sender() as (_text, image):
            await self.sharer.flush_pending()
            await self.sharer.flush_pending()

        self.assertEqual(len(await self._share_rows()), 1)
        self.assertEqual(image.await_count, 1)

    async def test_deep_clean_reject_does_not_share(self):
        await self._hygiene_hook()
        item_id = await self._deep_clean_item()
        await self._submit_deep_clean(
            item_id, self._photo((400, 400), (10, 10, 10)), self._photo((400, 400), (240, 240, 240))
        )

        await self.work.reject_deep_clean_pair(SUPER, item_id, reason="还有水渍")

        self.assertEqual(await self._share_rows(), [])

    async def test_unreadable_pair_photo_does_not_break_the_accept(self):
        """任一张读不到：验收照样落库，这次分享记失败并写清原因。"""
        await self._hygiene_hook()
        item_id = await self._deep_clean_item()
        submitted = await self._submit_deep_clean(
            item_id, self._photo((400, 400), (10, 10, 10)), self._photo((400, 400), (240, 240, 240))
        )
        before_id = str(submitted["before_capture_id"])
        for variant in ("preview", "thumb"):
            capture_id = await self._variant_id(before_id, variant)
            if capture_id:
                await self.captures.delete_async(capture_id)
        await self.captures.delete_async(before_id)

        accepted = await self.work.accept_deep_clean_pair(SUPER, item_id)

        self.assertEqual(accepted["status"], "已通过")
        async with self._sender() as (_text, image):
            await self.sharer.flush_pending()
        self.assertEqual(image.await_count, 0)
        rows = await self._share_rows()
        self.assertEqual(rows[0]["attempts"], 1)
        self.assertIn("读取照片失败", rows[0]["last_error"])

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

    async def test_fix_reshoot_is_shared_after_accept(self):
        await self._hygiene_hook()
        ticket_id = await self._fix_ticket()

        await self.work.accept_fix(SUPER, ticket_id)

        rows = await self._share_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "fix")
        self.assertIn("整改回拍", rows[0]["caption"])
        self.assertIn("案板", rows[0]["caption"])
        self.assertIn("卫生", rows[0]["caption"])

        async with self._sender() as (text, image):
            sent = await self.sharer.flush_pending()

        self.assertEqual(sent, 1)
        # 只推图片：说明文字不再发到群里（caption 仍落库留档）
        self.assertEqual(text.await_count, 0)
        self.assertEqual(image.await_count, 1)
        self.assertEqual((await self._share_rows())[0]["status"], SHARE_STATUS_SENT)

    async def test_fix_accept_twice_shares_once(self):
        await self._hygiene_hook()
        ticket_id = await self._fix_ticket()
        await self.work.accept_fix(SUPER, ticket_id)
        with self.assertRaises(HygieneWorkError):
            await self.work.accept_fix(SUPER, ticket_id)

        async with self._sender() as (_text, image):
            await self.sharer.flush_pending()
            await self.sharer.flush_pending()

        self.assertEqual(len(await self._share_rows()), 1)
        self.assertEqual(image.await_count, 1)

    async def test_fix_reject_does_not_share(self):
        await self._hygiene_hook()
        ticket_id = await self._fix_ticket()

        await self.work.reject_fix(SUPER, ticket_id, reason="还有油")

        self.assertEqual(await self._share_rows(), [])

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

    async def test_attire_accept_shares_without_naming_the_person(self):
        await self._hygiene_hook()
        employee_id = await self._on_duty_employee()
        await self._set_attire_standard()
        await self.attire.submit(employee_id, self._live(self._photo((600, 800))))

        await self.attire.accept(employee_id)

        rows = await self._share_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "attire")
        caption = rows[0]["caption"]
        self.assertIn("仪容仪表", caption)
        self.assertNotIn("张三", caption)
        self.assertNotIn(PHONE, caption)
        self.assertNotIn(PHONE[-4:], caption)
        self.assertNotIn("普通员工", caption)

        async with self._sender() as (_text, image):
            sent = await self.sharer.flush_pending()

        self.assertEqual(sent, 1)
        self.assertEqual(image.await_count, 1)

    async def test_attire_reshoot_after_reject_shares_the_new_photo(self):
        await self._hygiene_hook()
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

        rows = await self._share_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["capture_id"], capture_id)

    async def test_attire_accept_twice_shares_once(self):
        await self._hygiene_hook()
        employee_id = await self._on_duty_employee()
        await self._set_attire_standard()
        await self.attire.submit(employee_id, self._live(self._photo((600, 800))))
        await self.attire.accept(employee_id)
        with self.assertRaises(HygieneWorkError):
            await self.attire.accept(employee_id)

        async with self._sender() as (_text, image):
            await self.sharer.flush_pending()
            await self.sharer.flush_pending()

        self.assertEqual(len(await self._share_rows()), 1)
        self.assertEqual(image.await_count, 1)

    async def test_attire_reject_does_not_share(self):
        await self._hygiene_hook()
        employee_id = await self._on_duty_employee()
        await self._set_attire_standard()
        await self.attire.submit(employee_id, self._live(self._photo((600, 800))))

        await self.attire.reject(employee_id, "领口没扣好")

        self.assertEqual(await self._share_rows(), [])

    # ── 空态与失败 ────────────────────────────────────────────────────────

    async def test_without_hygiene_group_the_accept_still_succeeds(self):
        item_id = await self._item()
        await self._submit(item_id, self._photo())

        accepted = await self.work.accept_daily(SUPER, item_id, "白班")

        self.assertEqual(accepted["status"], "已通过")
        async with self._sender() as (text, image):
            sent = await self.sharer.flush_pending()
        self.assertEqual(sent, 0)
        self.assertEqual(text.await_count, 0)
        self.assertEqual(image.await_count, 0)
        rows = await self._share_rows()
        self.assertEqual(rows[0]["status"], SHARE_STATUS_SKIPPED)
        self.assertIn("没有启用中的卫生群", rows[0]["last_error"])

    async def test_marked_but_disabled_group_is_not_a_target(self):
        await self._hygiene_hook(enabled=False)
        item_id = await self._item()
        await self._submit(item_id, self._photo())

        await self.work.accept_daily(SUPER, item_id, "白班")
        async with self._sender() as (text, _image):
            await self.sharer.flush_pending()

        self.assertEqual(text.await_count, 0)
        self.assertEqual((await self._share_rows())[0]["status"], SHARE_STATUS_SKIPPED)

    async def test_missing_share_table_does_not_break_accept(self):
        """迁移 0013 还没应用时：验收照常成功，只是这张照片不发。

        （真去 DROP 表会污染同一个测试库的其它用例，所以直接把探测结果钉成"没有表"。）
        """
        await self._hygiene_hook()
        item_id = await self._item()
        await self._submit(item_id, self._photo())
        self.sharer._has_share_table = False

        accepted = await self.work.accept_daily(SUPER, item_id, "白班")

        self.assertEqual(accepted["status"], "已通过")
        self.assertEqual(await self._share_rows(), [])
        self.assertEqual(await self.sharer.flush_pending(), 0)

    async def test_failed_send_retries_then_gives_up(self):
        await self._hygiene_hook()
        item_id = await self._item()
        await self._submit(item_id, self._photo())
        await self.work.accept_daily(SUPER, item_id, "白班")

        async with self._sender(image_ok=False) as (_text, image):
            await self.sharer.flush_pending()
            rows = await self._share_rows()
            self.assertEqual(rows[0]["status"], SHARE_STATUS_PENDING)
            self.assertEqual(rows[0]["attempts"], 1)
            self.assertIn("image boom", rows[0]["last_error"])

            await self.sharer.flush_pending()
            await self.sharer.flush_pending()

        self.assertEqual(image.await_count, 3)
        rows = await self._share_rows()
        self.assertEqual(rows[0]["status"], SHARE_STATUS_FAILED)
        self.assertEqual(rows[0]["attempts"], 3)
        logs = await self._log_rows()
        self.assertEqual(len(logs), 3)
        self.assertTrue(all(row["status"] == "failed" for row in logs))

    # ── 降档 ──────────────────────────────────────────────────────────────

    async def test_oversized_preview_falls_back_to_thumb(self):
        await self._hygiene_hook()
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

        async with self._sender() as (_text, image):
            sent = await self.sharer.flush_pending()

        self.assertEqual(sent, 1)
        self.assertEqual(image.await_args.args[1], thumb)

    async def test_original_is_used_when_variants_are_missing(self):
        """老照片还没回填变体：原图装得下就直接发原图。"""
        await self._hygiene_hook()
        raw = self._photo((300, 300))
        source_capture_id = self.captures.put(raw)
        await self.sharer.enqueue(
            kind="daily",
            ref_key="2:白班:2026-09-13",
            capture_id=source_capture_id,
            caption="没有变体",
        )
        await self.db._conn.commit()

        async with self._sender() as (_text, image):
            await self.sharer.flush_pending()

        self.assertEqual(image.await_args.args[1], raw)

    # ── 不占写锁 ──────────────────────────────────────────────────────────

    async def test_accept_does_not_wait_for_the_send(self):
        await self._hygiene_hook()
        item_id = await self._item()
        await self._submit(item_id, self._photo())
        self.sharer._autoflush = True

        async def slow_send(_url, _data):
            await asyncio.sleep(0.6)
            return True, "ok"

        with patch.object(wecom_push_service, "send_text", new=AsyncMock(return_value=(True, "ok"))), patch.object(
            wecom_push_service, "send_image", new=slow_send
        ):
            started = time.monotonic()
            await self.work.accept_daily(SUPER, item_id, "白班")
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.4, "验收在等发送，写锁被网络 IO 占住了")

            # 发送确实被触发了，只是不等它
            await asyncio.sleep(0.1)
            self.assertEqual((await self._share_rows())[0]["status"], SHARE_STATUS_PENDING)
            task = self.sharer._flush_task
            self.assertIsNotNone(task)
            await task

        self.assertEqual((await self._share_rows())[0]["status"], SHARE_STATUS_SENT)
