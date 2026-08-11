#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爬虫与对账健康状态持久化（data/scraper_health.json）。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from config import settings
from database import CHINA_TZ

HEALTH_FILENAME = "scraper_health.json"


def health_file_path() -> Path:
    return Path(settings.DATABASE_DIR) / HEALTH_FILENAME


def read_health() -> Dict[str, Any]:
    path = health_file_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_health(payload: Dict[str, Any]) -> Path:
    path = health_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def merge_health(**updates: Any) -> Dict[str, Any]:
    current = read_health()
    current.update(updates)
    write_health(current)
    return current


def current_biz_date_str() -> str:
    now = datetime.now(CHINA_TZ)
    biz_day = now.date()
    if now.hour < 6:
        biz_day = biz_day - timedelta(days=1)
    return biz_day.isoformat()


def update_runtime_health(
    *,
    api_failures: int = 0,
    delivery_bills_pending: Optional[int] = None,
    last_scrape_at: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "biz_date": current_biz_date_str(),
        "api_failures": api_failures,
        "updated_at": datetime.now(CHINA_TZ).isoformat(),
    }
    if delivery_bills_pending is not None:
        payload["delivery_bills_pending"] = delivery_bills_pending
    if last_scrape_at:
        payload["last_scrape_at"] = last_scrape_at
    existing = read_health()
    last_reconcile = existing.get("last_reconcile")
    if last_reconcile:
        payload["last_reconcile"] = last_reconcile
    if existing.get("last_unmapped_alert_at"):
        payload["last_unmapped_alert_at"] = existing["last_unmapped_alert_at"]
    return merge_health(**payload)


def record_reconcile_summary(
    biz_date: str,
    *,
    missed_keys: int,
    missed_qty: float,
    miss_rate_pct: float,
    report_md: str = "",
    report_json: str = "",
) -> Dict[str, Any]:
    return merge_health(
        biz_date=biz_date,
        last_reconcile={
            "at": datetime.now(CHINA_TZ).isoformat(),
            "missed_keys": missed_keys,
            "missed_qty": missed_qty,
            "miss_rate_pct": miss_rate_pct,
            "report_md": report_md,
            "report_json": report_json,
        },
    )


def record_unmapped_alert_at() -> Dict[str, Any]:
    return merge_health(last_unmapped_alert_at=datetime.now(CHINA_TZ).isoformat())


def record_scraper_failure(consecutive_failures: int, error: str) -> Dict[str, Any]:
    """爬虫主循环单轮异常时调用：写入连续失败计数与最近错误。"""
    return merge_health(
        status="error",
        consecutive_failures=consecutive_failures,
        last_error=error,
        last_error_at=datetime.now(CHINA_TZ).isoformat(),
    )


def record_scraper_success() -> Dict[str, Any]:
    """爬虫主循环单轮成功完成时调用：清零连续失败计数。"""
    return merge_health(status="ok", consecutive_failures=0)
