#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Group-text notifier for hygiene overdue / open-ticket reminders.

Production adapter hands the text to the unified outbox (services/wecom_outbox.py):
订阅求解 → 每个目标渠道一行出站记录 → 由企微调度循环按节流发送。收件人从「渠道上的
卫生标记」改成「订阅了 hygiene_reminder 的渠道」（迁移 0016 已把旧标记回填成订阅，
所以上线瞬间每位收件人不变）。

HygieneWork only calls notify_group_text; never userid or image bytes.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Protocol

from db_core.utils import CHINA_TZ
from services.business_day import business_date_of
from services.wecom_outbox import wecom_outbox
from services.wecom_push_topics import TOPIC_HYGIENE_REMINDER, PushTrigger

logger = logging.getLogger(__name__)


class GroupTextNotifier(Protocol):
    async def notify_group_text(self, text: str) -> None:
        """Send plain text to the work group. No image, no userid."""


class FakeNotifier:
    """Test double: append texts. Never hits WeCom."""

    def __init__(self):
        self.texts = []

    async def notify_group_text(self, text: str) -> None:
        self.texts.append(text)


class WeComGroupTextNotifier:
    """把卫生的群消息交给**统一出站**：按「卫生提醒」的订阅入队，每目标渠道一行。

    入队失败只记日志，绝不向外抛：卫生业务流程（漏拍扫描 / 专项未完成 / 整改开单 /
    整改超时）不因为推送发不出去而失败。切换前是「尽力发」语义，切换后保持——区别在于
    现在失败会留在发送记录里、可重试、可补发，而不是发完就没了。
    """

    def __init__(self, db, *, outbox=None, now=None):
        self._db = db
        self._outbox = outbox if outbox is not None else wecom_outbox
        self._now = now or (lambda: datetime.now(CHINA_TZ))

    async def notify_group_text(self, text: str) -> None:
        content = (text or "").strip()
        if not content:
            return
        try:
            outbox_ids = await self._outbox.enqueue_topic(
                self._db,
                TOPIC_HYGIENE_REMINDER,
                params={"text": content},
                trigger=PushTrigger.EVENT,
                business_reference=self._business_reference(content),
            )
        except Exception as exc:
            logger.warning("卫生群文字提醒入队失败（不影响卫生流程）：%s", exc)
            return
        if not outbox_ids:
            logger.warning("卫生群文字提醒未入队：没有渠道订阅「卫生提醒」")

    def _business_reference(self, content: str) -> str:
        """业务引用 = 营业日 + 文案摘要。

        Protocol 契约只有 ``notify_group_text(text)``（四个调用点都不带业务 id），所以
        业务引用只能从文案与时间推。取「同一营业日 + 同一段文案」做作用域：卫生侧本来
        就按业务码去重（每条逾期项 / 每张整改单只通知一次），这里的幂等兜的是重复触发与
        重跑（例如通知已入队、记账还没落下时进程退出）；而服务侧两条业务事件若文案逐字
        相同，群里多发一遍也只是刷屏——合并掉是有意的。
        """
        digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
        return f"{business_date_of(self._now())}:{digest}"
