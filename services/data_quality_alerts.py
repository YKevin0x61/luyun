#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据采集质量企微告警：三类内容各自按订阅投递。

对账差异（``reconcile_diff``）、未映射菜品（``unmapped_dish``）、采集失败
（``scraper_failure``）都是**事件**触发，出口统一是 ``enqueue_alert``：解析订阅 → 每目标
渠道一行出站记录 → 由 30 秒企微调度循环按节流发送（ADR 0094 / 0095）。

切换前这三份内容是**一条合成消息广播给所有启用渠道**：同一个「数据质量告警」走定时任务
时只发一个群、走告警链路时发给全部群，两条路径的收件人语义互相矛盾。现在每类内容只发给
订阅了它的渠道，未订阅的启用渠道一条都不入队，零订阅的那类一条不发并记日志说明原因。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
from database import CHINA_TZ, DatabaseManager
from scraper.settled_reconcile import ReconcileResult, reconcile_result_to_dict
from services.wecom_outbox import WeComOutbox, resolve_targets, wecom_outbox
from services.wecom_push_service import resolve_report_dates
from services.wecom_push_topics import (
    TOPIC_RECONCILE_DIFF,
    TOPIC_UNMAPPED_DISH,
    PushTrigger,
)

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


def alert_reference(scope: str, content: str) -> str:
    """出站幂等键的一半：作用域 + 文案摘要。

    **作用域**由调用点给：对账差异与未映射菜品取营业日（同一天里同一段文案只投递一次——
    巡检每 5 分钟一轮、对账被手工重跑一次都不该在群里刷屏），采集失败取本次告警的时点
    （Tracker 已经按「次数门槛 + 最小间隔」去抖，同类故障再次告警就是一封新消息，
    不能因为文案与上次逐字相同就被幂等键吃掉）。
    """
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
    return f"{scope}:{digest}"


async def enqueue_alert(
    db: DatabaseManager,
    topic_id: str,
    content: str,
    *,
    scope: str,
    outbox: Optional[WeComOutbox] = None,
) -> Dict[str, Any]:
    """把一份采集类告警按订阅入队：每目标渠道一行出站记录（ADR 0094）。

    收件人只有一条真相来源——订阅了 ``topic_id`` 这类内容的渠道：没订阅的启用渠道**一条
    都不入队**，某类内容零订阅时一条不发并记日志说明原因。停用的渠道照样入队，由派发统一
    跳过并在记录里写明原因（订阅保留，投递不投），所以调用点**不要**自己再筛 `enabled`。

    返回形状与切换前的广播入口一致（``sent`` / ``errors``，调用方按 ``sent`` 判断有没有
    投出去）：``sent`` 现在是**已入队的投递行数**——一行一个订阅目标渠道，之后由 30 秒
    企微调度循环按节流发送。任何情况下都返回 dict，调用方不会拿到 None。

    ``scope`` 是这次告警的业务作用域（幂等键的一半，取值见 ``alert_reference``）。
    """
    text = (content or "").strip()
    if not text:
        return {"sent": 0, "skipped": True, "reason": "告警内容为空"}
    sender = outbox if outbox is not None else wecom_outbox
    try:
        # 订阅先解一次：零订阅（配置问题，店长该去订阅矩阵勾一下）与入队失败（事故）
        # 是两回事，返回里的原因要分得清，不能都写成「没发出去」。
        targets = await resolve_targets(db, topic_id)
        if not targets:
            logger.warning(
                "采集类告警零订阅，本次一条不发 topic=%s scope=%s", topic_id, scope
            )
            return {"sent": 0, "errors": [], "reason": "没有任何渠道订阅这类内容"}
        outbox_ids = await sender.enqueue_topic(
            db,
            topic_id,
            params={"text": text},
            trigger=PushTrigger.EVENT,
            business_reference=alert_reference(scope, text),
            targets=targets,
        )
    except Exception as exc:
        # 入队失败（参数不合 schema / 库写不进去）不该把采集与对账流程带走：
        # 记下原因，让调用方按「没投出去」处理。
        logger.error("采集类告警入队失败 topic=%s scope=%s: %s", topic_id, scope, exc)
        return {"sent": 0, "errors": [str(exc)], "reason": f"入队失败：{exc}"}
    if not outbox_ids:
        logger.error(
            "采集类告警入队未成功（目标 %s 个渠道，详见出站日志）topic=%s",
            len(targets),
            topic_id,
        )
        return {"sent": 0, "errors": [], "reason": "入队未成功（详见出站日志）"}
    return {"sent": len(outbox_ids), "errors": [], "outbox_ids": outbox_ids}


async def maybe_send_data_quality_alerts(
    db: DatabaseManager,
    result: ReconcileResult,
    *,
    include_unmapped: bool = True,
    dish_catalog=None,
    outbox: Optional[WeComOutbox] = None,
) -> Dict[str, Any]:
    """日终对账之后的两类告警：各自入队，按各自的订阅投递。

    - 对账差异超阈值 → ``reconcile_diff``；
    - 未映射菜品非空 → ``unmapped_dish``。

    切换前两者被拼成**一条**消息广播给所有启用渠道：没订日报的群也会收到对账告警，
    订了日报的群还会收到跟它无关的档口提醒。现在每类内容只发给订阅了它的渠道，
    零订阅的那类一条不发（由 ``enqueue_alert`` 记日志说明原因）。
    """
    alerts: Dict[str, Dict[str, Any]] = {}
    if should_alert_reconcile(result):
        alerts[TOPIC_RECONCILE_DIFF] = await enqueue_alert(
            db,
            TOPIC_RECONCILE_DIFF,
            build_reconcile_alert_message(result),
            scope=result.biz_date,
            outbox=outbox,
        )
    if include_unmapped and settings.UNMAPPED_DISH_ALERT_ENABLED:
        catalog = dish_catalog
        if catalog is None:
            from services.dish_catalog import get_dish_catalog
            catalog = get_dish_catalog()
        unmapped = await get_unmapped_dish_names(catalog)
        if unmapped:
            alerts[TOPIC_UNMAPPED_DISH] = await enqueue_alert(
                db,
                TOPIC_UNMAPPED_DISH,
                build_unmapped_alert_message(unmapped, result.biz_date),
                scope=result.biz_date,
                outbox=outbox,
            )
    if not alerts:
        return {"sent": 0, "skipped": True}
    return {
        "sent": sum(int(item.get("sent") or 0) for item in alerts.values()),
        "errors": [
            error for item in alerts.values() for error in item.get("errors", [])
        ],
        "alerts": alerts,
    }


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
    lines.append(f"采集 API 失败（最近一轮）: {api_failures}")
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
