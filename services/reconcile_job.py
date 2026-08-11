#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对账任务执行（CLI / Admin / 定时调度共用）。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from config import settings
from database import CHINA_TZ, DatabaseManager
from scraper.restaurant_scraper import RestaurantScraper
from scraper.settled_reconcile import (
    ReconcileResult,
    build_fix_orders_from_diffs,
    reconcile_result_to_dict,
    run_settled_reconcile,
    sweep_cancelled_delivery_for_biz_date,
    write_reconcile_outputs,
)
from services.data_quality_alerts import maybe_send_data_quality_alerts
from services.realtime.hub import realtime_hub
from services.scraper_health import record_reconcile_summary, update_runtime_health

logger = logging.getLogger(__name__)

_reconcile_running = False

# 对账阶段中文标签，供前端进度条直接展示（stage 未命中时回退为原始值）。
_STAGE_LABELS = {
    "initializing": "初始化爬虫会话",
    "fetching_bills": "拉取账单列表",
    "matching_bills": "逐单核对",
    "building_fix_orders": "生成补录订单",
    "sweeping_cancelled": "复核外卖取消",
    "writing_report": "生成报告",
    "done": "完成",
    "error": "失败",
}

# 进度为进程内存态，不落盘：仅用于运行中的实时展示，重启/崩溃后无需恢复。
_reconcile_progress: Dict[str, Any] = {
    "running": False,
    "biz_date": None,
    "stage": None,
    "stage_label": "",
    "current": 0,
    "total": 0,
    "error": None,
    "updated_at": None,
}


def get_reconcile_progress() -> Dict[str, Any]:
    return dict(_reconcile_progress)


async def _set_stage(
    biz_date: str,
    stage: str,
    *,
    running: bool = True,
    current: int = 0,
    total: int = 0,
    error: Optional[str] = None,
) -> None:
    _reconcile_progress.update(
        {
            "running": running,
            "biz_date": biz_date,
            "stage": stage,
            "stage_label": _STAGE_LABELS.get(stage, stage),
            "current": current,
            "total": total,
            "error": error,
            "updated_at": datetime.now(CHINA_TZ).isoformat(),
        }
    )
    try:
        await realtime_hub.broadcast_nudge("admin", {"kind": "reconcile"})
    except Exception:
        logger.debug("对账进度 nudge 广播失败", exc_info=True)


def default_biz_date() -> str:
    now = datetime.now(CHINA_TZ)
    biz_day = now.date()
    if now.hour < 6:
        biz_day = biz_day - timedelta(days=1)
    return biz_day.isoformat()


async def execute_reconcile(
    db: DatabaseManager,
    adapter: RestaurantScraper,
    biz_date: Optional[str] = None,
    *,
    fix: bool = False,
    notify: bool = False,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """执行对账，可选补录与企微告警。返回结果摘要。"""
    global _reconcile_running
    if _reconcile_running:
        return {"success": False, "error": "对账任务正在运行中"}
    _reconcile_running = True

    resolved_date = biz_date or default_biz_date()
    out_dir = output_dir or (Path(settings.DATABASE_DIR) / "reconcile")

    async def _on_bill_progress(current: int, total: int) -> None:
        await _set_stage(resolved_date, "matching_bills", current=current, total=total)

    await _set_stage(resolved_date, "initializing")

    try:
        # 对账默认 22:05 触发，已过营业时段（work_end 21:30），必须跳过暂停门禁，
        # 否则 ensure_ready() 直接返回 False，对账永远跑不起来。
        if not await adapter.ensure_ready(ignore_pause=True):
            await _set_stage(resolved_date, "error", running=False, error="爬虫未初始化，请检查凭据")
            return {"success": False, "error": "爬虫未初始化，请检查凭据"}

        await _set_stage(resolved_date, "fetching_bills")
        result, bills_meta = await run_settled_reconcile(
            adapter, db, resolved_date, on_progress=_on_bill_progress
        )
        fixed_count = 0

        if fix and result.diffs:
            await _set_stage(resolved_date, "building_fix_orders")
            from scraper.order_line_builder import OrderLineBuilder

            fix_orders = await build_fix_orders_from_diffs(
                result.diffs,
                bills_meta,
                order_lines=OrderLineBuilder(adapter.dish_catalog),
            )
            if fix_orders:
                await db.orders.save_orders(fix_orders)
                fixed_count = len(fix_orders)
                result, bills_meta = await run_settled_reconcile(
                    adapter, db, resolved_date, on_progress=_on_bill_progress
                )

        # 兜底：处理“DB 有、POS 当日列表已无”的外卖取消单（退菜 + 归零）
        cancel_summary: Dict[str, Any] = {"skipped": True}
        if fix:
            await _set_stage(resolved_date, "sweeping_cancelled")
            cancel_summary = await sweep_cancelled_delivery_for_biz_date(adapter, db, resolved_date)
            if cancel_summary.get("cancelled_rows"):
                logger.info(
                    "🚫 对账兜底取消外卖单 %s 单 / %s 行",
                    cancel_summary.get("cancelled_bills"),
                    cancel_summary.get("cancelled_rows"),
                )

        await _set_stage(resolved_date, "writing_report")
        md_path, json_path = write_reconcile_outputs(result, out_dir)
        update_runtime_health(api_failures=adapter.settled_api_failures)
        record_reconcile_summary(
            resolved_date,
            missed_keys=result.missed_keys,
            missed_qty=result.missed_qty,
            miss_rate_pct=result.miss_rate_pct,
            report_md=str(md_path),
            report_json=str(json_path),
        )

        alert_result: Dict[str, Any] = {"skipped": True}
        if notify:
            alert_result = await maybe_send_data_quality_alerts(
                db, result, dish_catalog=adapter.dish_catalog
            )

        await _set_stage(resolved_date, "done", running=False)
        return {
            "success": True,
            "biz_date": resolved_date,
            "fixed_count": fixed_count,
            "missed_keys": result.missed_keys,
            "missed_qty": result.missed_qty,
            "miss_rate_pct": result.miss_rate_pct,
            "report_md": str(md_path),
            "report_json": str(json_path),
            "alert": alert_result,
            "delivery_cancel": cancel_summary,
            "result": reconcile_result_to_dict(result),
        }
    except Exception as exc:
        await _set_stage(resolved_date, "error", running=False, error=str(exc))
        raise
    finally:
        _reconcile_running = False


def is_reconcile_running() -> bool:
    return _reconcile_running
