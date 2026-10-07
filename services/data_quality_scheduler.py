#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后台：日终对账调度 + 营业中未映射菜品提醒。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config import settings
from database import CHINA_TZ
from services.data_quality_alerts import (
    build_unmapped_alert_message,
    enqueue_alert,
    get_unmapped_dish_names,
)
from services.dish_catalog import get_dish_catalog
from services.reconcile_job import default_biz_date, execute_reconcile
from services.scraper_health import current_biz_date_str, read_health, record_unmapped_alert_at
from services.wecom_outbox import WeComOutbox
from services.wecom_push_topics import TOPIC_UNMAPPED_DISH

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


async def check_unmapped_dishes(
    db,
    *,
    dish_catalog=None,
    outbox: Optional[WeComOutbox] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """一轮未映射菜品巡检：过了最小间隔才按订阅入队一次告警。

    触发时机与切换前逐字一致（营业时段与 5 分钟一轮在循环里，最小间隔在这里），
    只有出口从「广播给所有启用渠道」换成「按订阅入队」——订阅了 `unmapped_dish` 的
    渠道各落一行出站记录，没订的启用渠道一条都收不到。

    返回入队结果（``sent`` = 已入队的投递行数）；没到间隔或没有未映射菜品时返回
    ``{"sent": 0, "skipped": True}``。
    """
    moment = now or datetime.now(CHINA_TZ)
    last_at_raw = read_health().get("last_unmapped_alert_at")
    if last_at_raw:
        try:
            last_at = datetime.fromisoformat(last_at_raw)
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=CHINA_TZ)
            if moment - last_at < timedelta(hours=settings.UNMAPPED_ALERT_INTERVAL_HOURS):
                return {"sent": 0, "skipped": True}
        except ValueError:
            pass

    catalog = dish_catalog if dish_catalog is not None else get_dish_catalog()
    unmapped = await get_unmapped_dish_names(catalog)
    if not unmapped:
        return {"sent": 0, "skipped": True}

    biz_date = current_biz_date_str()
    alert_result = await enqueue_alert(
        db,
        TOPIC_UNMAPPED_DISH,
        build_unmapped_alert_message(unmapped, biz_date),
        scope=biz_date,
        outbox=outbox,
    )
    if alert_result.get("sent", 0) > 0:
        record_unmapped_alert_at()
        logger.info("🔔 未映射菜品告警已入队 count=%s", len(unmapped))
    return alert_result


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

            db = get_db()
            if db is None:
                continue

            await check_unmapped_dishes(db)
        except asyncio.CancelledError:
            logger.info("未映射菜品巡检已停止")
            break
        except Exception as exc:
            logger.error("未映射菜品巡检异常: %s", exc)
