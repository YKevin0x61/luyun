#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据采集质量企微告警。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
from database import CHINA_TZ, DatabaseManager
from scraper.settled_reconcile import ReconcileResult, reconcile_result_to_dict
from services.wecom_push_service import decrypt_webhook_url, resolve_report_dates, wecom_push_service

logger = logging.getLogger(__name__)

DATA_QUALITY_PUSH_TYPE = "data_quality_alert"


def should_alert_reconcile(result: ReconcileResult) -> bool:
    if result.missed_qty >= settings.RECONCILE_MISS_QTY_ALERT:
        return True
    return result.miss_rate_pct > settings.RECONCILE_MISS_RATE_ALERT_PCT


def build_reconcile_alert_message(result: ReconcileResult) -> str:
    lines = [
        f"【数据质量告警】{result.biz_date} 对账",
        f"漏抓 {result.missed_qty} 份 / {result.missed_keys} 键",
        f"漏抓率 {result.miss_rate_pct}%（网页 {result.pos_total_qty} 份，本地 {result.db_total_qty} 份）",
        f"受影响账单 {result.affected_bills} 张",
    ]
    if result.api_failures:
        lines.append(f"明细 API 失败 {len(result.api_failures)} 笔")
    top_diffs = sorted(result.diffs, key=lambda item: item.missed_qty, reverse=True)[:5]
    if top_diffs:
        lines.append("Top 漏抓:")
        for item in top_diffs:
            lines.append(f"- {item.bs_code} {item.dish_name}: 缺 {item.missed_qty}")
    return "\n".join(lines)


def build_unmapped_alert_message(dishes: List[str], biz_date: str) -> str:
    preview = ", ".join(dishes[:15])
    suffix = f" 等 {len(dishes)} 个" if len(dishes) > 15 else ""
    return (
        f"【档口映射提醒】{biz_date}\n"
        f"未映射菜品 {len(dishes)} 个：{preview}{suffix}\n"
        f"请在 Admin 维护 dish_stations 后执行 sync-stations"
    )


async def get_unmapped_dish_names(dish_catalog) -> List[str]:
    """Delegate to DishCatalog — sole unmapped-listing semantics."""
    result = await dish_catalog.unmapped_dishes()
    return list(result.get("dishes") or [])


async def send_to_enabled_webhooks(db: DatabaseManager, content: str) -> Dict[str, Any]:
    webhooks = await db.wecom_webhooks_all(include_disabled=False)
    if not webhooks:
        return {"sent": 0, "error": "无启用的企微 webhook"}
    sent = 0
    errors: List[str] = []
    for webhook in webhooks:
        try:
            url = decrypt_webhook_url(webhook["webhook_url_encrypted"])
            ok, response_text = await wecom_push_service.send_text(url, content)
            if ok:
                sent += 1
            else:
                errors.append(f"{webhook.get('name', webhook['id'])}: {response_text}")
        except Exception as exc:
            errors.append(f"{webhook.get('name', webhook['id'])}: {exc}")
    return {"sent": sent, "errors": errors}


async def maybe_send_data_quality_alerts(
    db: DatabaseManager,
    result: ReconcileResult,
    *,
    include_unmapped: bool = True,
    dish_catalog=None,
) -> Dict[str, Any]:
    messages: List[str] = []
    if should_alert_reconcile(result):
        messages.append(build_reconcile_alert_message(result))
    if include_unmapped and settings.UNMAPPED_DISH_ALERT_ENABLED:
        catalog = dish_catalog
        if catalog is None:
            from services.dish_catalog import get_dish_catalog
            catalog = get_dish_catalog()
        unmapped = await get_unmapped_dish_names(catalog)
        if unmapped:
            messages.append(build_unmapped_alert_message(unmapped, result.biz_date))
    if not messages:
        return {"sent": 0, "skipped": True}
    combined = "\n\n".join(messages)
    return await send_to_enabled_webhooks(db, combined)


def load_reconcile_summary_for_date(biz_date: str) -> Optional[Dict[str, Any]]:
    """读取 data/reconcile/reconcile_{biz_date}.json，不存在则返回 None。"""
    path = Path(settings.DATABASE_DIR) / "reconcile" / f"reconcile_{biz_date}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def build_data_quality_status_message(
    *,
    biz_date: str,
    health: Dict[str, Any],
    reconcile_summary: Optional[Dict[str, Any]] = None,
    unmapped_dishes: Optional[List[str]] = None,
) -> str:
    """企微定时任务 / 预览用的数据质量摘要（非阈值过滤）。"""
    lines = [f"【数据质量日报】{biz_date}"]
    api_failures = int(health.get("api_failures") or 0)
    lines.append(f"采集 API 失败累计: {api_failures}")
    if health.get("last_scrape_at"):
        lines.append(f"最后采集: {health['last_scrape_at']}")
    summary = reconcile_summary or {}
    lr = health.get("last_reconcile") or {}
    if not summary and lr:
        summary = {
            "missed_keys": lr.get("missed_keys"),
            "missed_qty": lr.get("missed_qty"),
            "miss_rate_pct": lr.get("miss_rate_pct"),
            "pos_total_qty": None,
            "db_total_qty": None,
            "affected_bills": None,
            "biz_date": biz_date,
        }
    if summary:
        missed_qty = summary.get("missed_qty")
        miss_rate = summary.get("miss_rate_pct")
        lines.append(
            f"对账漏抓: {missed_qty} 份 / {summary.get('missed_keys', 0)} 键，"
            f"漏抓率 {miss_rate}%"
        )
        if summary.get("pos_total_qty") is not None:
            lines.append(
                f"网页 {summary.get('pos_total_qty')} 份，本地 {summary.get('db_total_qty')} 份，"
                f"受影响账单 {summary.get('affected_bills', 0)} 张"
            )
    else:
        lines.append("对账: 尚无报告，请执行 reconcile_settled_bills 或在 Admin 触发对账")
    dishes = unmapped_dishes or []
    if dishes:
        preview = ", ".join(dishes[:10])
        suffix = f" 等 {len(dishes)} 个" if len(dishes) > 10 else ""
        lines.append(f"未映射菜品 {len(dishes)} 个: {preview}{suffix}")
    else:
        lines.append("未映射菜品: 0")
    if summary and should_alert_reconcile_from_summary(summary):
        lines.append("⚠️ 漏抓超过告警阈值，请检查采集或对账修复")
    return "\n".join(lines)


def should_alert_reconcile_from_summary(summary: Dict[str, Any]) -> bool:
    missed_qty = float(summary.get("missed_qty") or 0)
    miss_rate = float(summary.get("miss_rate_pct") or 0)
    if missed_qty >= settings.RECONCILE_MISS_QTY_ALERT:
        return True
    return miss_rate > settings.RECONCILE_MISS_RATE_ALERT_PCT


async def build_data_quality_job_message(
    db: DatabaseManager,
    *,
    date_range_mode: str = "today",
    dish_catalog=None,
) -> str:
    from services.scraper_health import read_health

    biz_date, _ = resolve_report_dates(date_range_mode)
    health = read_health()
    reconcile_summary = load_reconcile_summary_for_date(biz_date)
    unmapped: List[str] = []
    if settings.UNMAPPED_DISH_ALERT_ENABLED:
        catalog = dish_catalog
        if catalog is None:
            from services.dish_catalog import get_dish_catalog
            catalog = get_dish_catalog()
        unmapped = await get_unmapped_dish_names(catalog)
    return build_data_quality_status_message(
        biz_date=biz_date,
        health=health,
        reconcile_summary=reconcile_summary,
        unmapped_dishes=unmapped,
    )


def build_health_payload(
    biz_date: str,
    result: ReconcileResult,
    *,
    api_failures_runtime: int = 0,
) -> Dict[str, Any]:
    return {
        "biz_date": biz_date,
        "api_failures": api_failures_runtime + len(result.api_failures),
        "last_reconcile": {
            "at": datetime.now(CHINA_TZ).isoformat(),
            "missed_keys": result.missed_keys,
            "missed_qty": result.missed_qty,
            "miss_rate_pct": result.miss_rate_pct,
            "summary": reconcile_result_to_dict(result),
        },
    }
