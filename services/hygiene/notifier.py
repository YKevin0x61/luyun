#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Group-text notifier for hygiene overdue / open-ticket reminders.

Production adapter uses enabled wecom_push_webhooks + send_text.
HygieneWork only calls notify_group_text; never userid or image bytes.
"""

from __future__ import annotations

import logging
from typing import Protocol

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
    """Looks up enabled wecom_push_webhooks and sends text via send_text."""

    def __init__(self, db):
        self._db = db

    async def notify_group_text(self, text: str) -> None:
        content = (text or "").strip()
        if not content:
            return
        from services.wecom_push_service import (
            decrypt_webhook_url,
            split_text_for_wecom,
            wecom_push_service,
        )

        webhooks = await self._db.wecom_webhooks_all(include_disabled=False)
        if not webhooks:
            logger.warning("hygiene group text skipped: no enabled wecom webhook")
            return
        chunks = split_text_for_wecom(content)
        for webhook in webhooks:
            for chunk in chunks:
                try:
                    url = decrypt_webhook_url(webhook["webhook_url_encrypted"])
                    ok, response_text = await wecom_push_service.send_text(url, chunk)
                    if not ok:
                        logger.warning(
                            "hygiene group text failed webhook=%s: %s",
                            webhook.get("name") or webhook.get("id"),
                            response_text,
                        )
                except Exception as exc:
                    logger.warning(
                        "hygiene group text error webhook=%s: %s",
                        webhook.get("name") or webhook.get("id"),
                        exc,
                    )
