#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后台：日终对账调度 + 营业中未映射菜品提醒。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from config import settings
from database import CHINA_TZ
from services.data_quality_alerts import (
    build_unmapped_alert_message,
    get_unmapped_dish_names,
    send_to_enabled_webhooks,
)
from services.dish_catalog import get_dish_catalog
from services.reconcile_job import default_biz_date, execute_reconcile
from services.scraper_health import current_biz_date_str, read_health, record_unmapped_alert_at

logger = logging.getLogger(__name__)

_last_reconcile_biz_date: str | None = None
_last_unmapped_alert_at: datetime | None = None


async def run_reconcile_scheduler(get_db, get_scraper):
    """每天在 RECONCILE_SCHEDULE_TIME 触发对账（需 RECONCILE_SCHEDULE_ENABLED）。"""
    global _last_reconcile_biz_date
    if not settings.RECONCILE_SCHEDULE_ENABLED:
        return

    logger.info(
        "📅 日终对账调度已启用 time=%s auto_fix=%s notify=%s",
        settings.RECONCILE_SCHEDULE_TIME,
        settings.RECONCILE_AUTO_FIX,
        settings.RECONCILE_AUTO_NOTIFY,
    )

    while True:
        try:
            await asyncio.sleep(30)
            now = datetime.now(CHINA_TZ)
            if now.strftime("%H:%M") != settings.RECONCILE_SCHEDULE_TIME:
                continue

            biz_date = default_biz_date()
            if _last_reconcile_biz_date == biz_date:
                continue

            db = get_db()
            scraper = get_scraper()
            if db is None or scraper is None:
                continue

            _last_reconcile_biz_date = biz_date
            logger.info("📅 触发日终对账 biz_date=%s", biz_date)
            result = await execute_reconcile(
                db,
                scraper,
                biz_date,
                fix=settings.RECONCILE_AUTO_FIX,
                notify=settings.RECONCILE_AUTO_NOTIFY,
            )
            logger.info("📅 日终对账完成: %s", result.get("missed_qty", "?"))
        except asyncio.CancelledError:
            logger.info("日终对账调度已停止")
            break
        except Exception as exc:
            logger.error("日终对账调度异常: %s", exc)


async def run_unmapped_dish_watchdog(get_db):
    """营业时段每 N 小时检查未映射菜品并企微提醒。"""
    if not settings.UNMAPPED_DISH_ALERT_ENABLED:
        return

    interval_hours = settings.UNMAPPED_ALERT_INTERVAL_HOURS
    logger.info("🔔 未映射菜品巡检已启用 interval=%sh", interval_hours)

    while True:
        try:
            await asyncio.sleep(300)
            now = datetime.now(CHINA_TZ)
            if now.hour < 7 or now.hour >= 22:
                continue

            health = read_health()
            last_at_raw = health.get("last_unmapped_alert_at")
            if last_at_raw:
                try:
                    last_at = datetime.fromisoformat(last_at_raw)
                    if last_at.tzinfo is None:
                        last_at = last_at.replace(tzinfo=CHINA_TZ)
                    if now - last_at < timedelta(hours=interval_hours):
                        continue
                except ValueError:
                    pass

            db = get_db()
            if db is None:
                continue

            unmapped = await get_unmapped_dish_names(get_dish_catalog())
            if not unmapped:
                continue

            message = build_unmapped_alert_message(unmapped, current_biz_date_str())
            alert_result = await send_to_enabled_webhooks(db, message)
            if alert_result.get("sent", 0) > 0:
                record_unmapped_alert_at()
                logger.info("🔔 未映射菜品告警已发送 count=%s", len(unmapped))
        except asyncio.CancelledError:
            logger.info("未映射菜品巡检已停止")
            break
        except Exception as exc:
            logger.error("未映射菜品巡检异常: %s", exc)
