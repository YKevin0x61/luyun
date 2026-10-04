#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生群消息的收件人（票 02）：只发勾了「这是卫生群」且启用中的 webhook。

以前这里是"发给所有启用中的 webhook" —— 销售日报群、数据质量群、当初做测试用的群都会
收到漏拍汇总与整改通知。这里锁住新口径，以及最危险的那个空态：**一个卫生群都没勾时
一条都不发**（「企微推送」页顶部会显式提示，不让它静默）。
"""

import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from config import settings
from database import DatabaseManager
from services.hygiene.notifier import WeComGroupTextNotifier
from services.wecom_push_service import (
    encrypt_webhook_url,
    mask_webhook_url,
    wecom_push_service,
)

URL_HYGIENE = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hygiene-key-0000"
URL_DAILY = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=daily-key-0000"


class HygieneGroupNotifierTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.notifier = WeComGroupTextNotifier(self.db)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_dir
        self._tmpdir.cleanup()

    async def _hook(self, name, url, *, hygiene_feed=False, enabled=True):
        return await self.db.wecom_webhook_create({
            "name": name,
            "webhook_url_encrypted": encrypt_webhook_url(url),
            "webhook_url_masked": mask_webhook_url(url),
            "enabled": enabled,
            "hygiene_feed": hygiene_feed,
            "notes": "",
        })

    async def _notify(self, text="日常漏拍：案板表面 ×2"):
        sender = AsyncMock(return_value=(True, "ok"))
        with patch.object(wecom_push_service, "send_text", new=sender):
            await self.notifier.notify_group_text(text)
        return sender

    async def test_only_the_hygiene_group_gets_the_text(self):
        await self._hook("日报群", URL_DAILY)
        await self._hook("卫生群", URL_HYGIENE, hygiene_feed=True)

        sender = await self._notify()

        self.assertEqual(sender.await_count, 1)
        self.assertEqual(sender.await_args.args[0], URL_HYGIENE)

    async def test_marked_but_disabled_group_gets_nothing(self):
        await self._hook("停用的卫生群", URL_HYGIENE, hygiene_feed=True, enabled=False)

        sender = await self._notify()

        self.assertEqual(sender.await_count, 0)

    async def test_no_hygiene_group_sends_nothing(self):
        await self._hook("日报群", URL_DAILY)

        sender = await self._notify()

        self.assertEqual(sender.await_count, 0)

    async def test_every_hygiene_group_gets_the_text(self):
        """两个卫生群各收一份 —— 不合并、不去重（既有行为）。"""
        await self._hook("卫生群 A", URL_HYGIENE, hygiene_feed=True)
        await self._hook("卫生群 B", URL_DAILY, hygiene_feed=True)

        sender = await self._notify()

        self.assertEqual(sender.await_count, 2)
        self.assertEqual(
            {call.args[0] for call in sender.await_args_list}, {URL_HYGIENE, URL_DAILY}
        )

    async def test_long_text_is_still_split_per_group(self):
        """超长文案仍按字节分块发全，且只发给卫生群。"""
        await self._hook("日报群", URL_DAILY)
        await self._hook("卫生群", URL_HYGIENE, hygiene_feed=True)
        long_text = "\n".join(f"第 {i} 项：" + "补" * 60 for i in range(60))

        sender = await self._notify(long_text)

        self.assertGreater(sender.await_count, 1)
        self.assertTrue(all(call.args[0] == URL_HYGIENE for call in sender.await_args_list))

    async def test_blank_text_is_ignored(self):
        await self._hook("卫生群", URL_HYGIENE, hygiene_feed=True)

        sender = await self._notify("   \n  ")

        self.assertEqual(sender.await_count, 0)
