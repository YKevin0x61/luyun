#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""厨房采购超量 / 低量预警：提交后按当天累计比对参考值，跨阈值才推企微。

口径与采购分析页的「异常预警」一致（见 `services/purchase/service.py` 的
`analysis_anomalies`）：当天有效订单（取消的不算）按商品累计，参考值取提交时的
采购参考值；**只推这一单新跨过阈值的商品**，避免同一商品当天每次补单都重复提醒。
一张采购单最多一条群消息。

为什么单独一个模块：注册表只声明「有哪些内容类型」，中文文案属于业务；采购提交
（`PurchaseService.submit_order`）也不该因为群通知失败而回滚 —— 入队失败只记日志。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional

from db_core.utils import CHINA_TZ
from services.wecom_outbox import PushTrigger, wecom_outbox
from services.wecom_push_topics import TOPIC_PURCHASE_ANOMALY
from services.wecom_request_notice import admin_url

logger = logging.getLogger(__name__)

# 与采购分析「异常预警」的默认参数保持一致（页面上也是 1.5 / 0.5）。
OVER_RATIO = 1.5
UNDER_RATIO = 0.5

# 一行一个商品，最多列这么多；再多就写「还有 N 项」，免得群消息超过企微 2048 字节。
MAX_LINES = 8

ANALYSIS_PATH = "/workbench/kitchen/purchase/analysis"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _reference_value(reference: Any) -> Optional[float]:
    """参考值可能是 ProductReference，也可能是测试里的字典 / 数字。"""
    if reference is None:
        return None
    if isinstance(reference, Mapping):
        raw = reference.get("reference")
    else:
        raw = getattr(reference, "reference", reference)
    if raw is None:
        return None
    value = _as_float(raw)
    return value if value > 0 else None


def _quantity_text(value: Any) -> str:
    number = _as_float(value)
    if abs(number - round(number)) < 0.0001:
        return str(int(round(number)))
    return f"{round(number, 2):g}"


def _ratio_text(value: Any) -> str:
    return f"{_as_float(value):.2f}"


def _submitted_text(value: Any) -> str:
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            return "—"
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text[:16].replace("T", " ")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CHINA_TZ)
    return moment.astimezone(CHINA_TZ).strftime("%m-%d %H:%M")


def detect_anomalies(
    *,
    products: Iterable[Mapping[str, Any]],
    quantities: Mapping[Any, Any],
    references: Mapping[Any, Any],
    previous_quantities: Mapping[Any, Any],
    over_ratio: float = OVER_RATIO,
    under_ratio: float = UNDER_RATIO,
) -> List[Dict[str, Any]]:
    """找出这一单让当天累计量新跨过超量 / 低量阈值的商品。

    ``previous_quantities`` 是**提交前**当天已有有效采购量（不含本单）。判定用
    跨阈值而不是「当前超了」，所以同一商品当天再次补单不会重复提醒；本来就已经
    超量或低量的商品，再买一点也不会重新触发。

    恰好等于阈值（例如参考 10、今天累计 15 就是 1.5 倍）不触发，与采购分析页
    ``ratio > over_ratio`` / ``ratio < under_ratio`` 的边界一致。
    """
    if over_ratio <= 0 or under_ratio <= 0:
        return []
    quantity_by_id = {_as_int(key): _as_float(value) for key, value in quantities.items()}
    previous_by_id = {_as_int(key): _as_float(value) for key, value in previous_quantities.items()}
    reference_by_id = {_as_int(key): value for key, value in references.items()}

    found: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for product in products:
        product_id = _as_int(product.get("id"))
        if not product_id or product_id in seen:
            continue
        quantity = quantity_by_id.get(product_id, 0.0)
        if quantity <= 0:
            continue
        reference = _reference_value(reference_by_id.get(product_id))
        if reference is None:
            continue
        before = max(0.0, previous_by_id.get(product_id, 0.0))
        after = before + quantity
        before_ratio = (before / reference) if before > 0 else 0.0
        after_ratio = after / reference

        kind = ""
        if after_ratio > over_ratio and before_ratio <= over_ratio:
            kind = "over"
        elif after_ratio < under_ratio and (before <= 0 or before_ratio >= under_ratio):
            kind = "under"
        if not kind:
            continue

        found.append(
            {
                "kind": kind,
                "product_id": product_id,
                "name": str(product.get("name") or f"商品 {product_id}"),
                "unit": str(product.get("unit") or ""),
                "order_quantity": round(quantity, 2),
                "quantity": round(after, 2),
                "reference": round(reference, 2),
                "ratio": round(after_ratio, 4),
            }
        )
        seen.add(product_id)
    return found


def render_purchase_anomaly_text(
    order: Mapping[str, Any],
    anomalies: Iterable[Mapping[str, Any]],
    *,
    over_ratio: float = OVER_RATIO,
    under_ratio: float = UNDER_RATIO,
    base_url: Optional[str] = None,
) -> str:
    """把异常拼成一条企微群文本；没有异常时返回空串。"""
    rows = [row for row in anomalies if row.get("kind") in ("over", "under")]
    if not rows:
        return ""

    order_no = str(order.get("order_no") or "采购单")
    position_name = str(order.get("position_name") or "—")
    submitter_name = str(order.get("submitter_name") or "—")
    lines = [
        f"【厨房采购预警】{order_no}",
        f"{position_name} · {submitter_name} · {_submitted_text(order.get('submitted_at'))}",
        "口径：当天累计 / 参考值（取消单不计）",
    ]

    for kind, label, ratio_text in (
        ("over", "超量", f">{over_ratio:g} 倍"),
        ("under", "低量", f"<{under_ratio:g} 倍"),
    ):
        group = [row for row in rows if row.get("kind") == kind]
        if not group:
            continue
        lines.append(f"{label}（{ratio_text}）：")
        for row in group[:MAX_LINES]:
            unit = str(row.get("unit") or "")
            lines.append(
                "· {name} 本单 {order_qty}{unit} / 今日 {today_qty}{unit} / "
                "参考 {reference}{unit}（{ratio} 倍）".format(
                    name=row.get("name") or "—",
                    order_qty=_quantity_text(row.get("order_quantity")),
                    today_qty=_quantity_text(row.get("quantity")),
                    unit=unit,
                    reference=_quantity_text(row.get("reference")),
                    ratio=_ratio_text(row.get("ratio")),
                )
            )
        if len(group) > MAX_LINES:
            lines.append(f"· 还有 {len(group) - MAX_LINES} 项，去采购分析看")

    link = admin_url(ANALYSIS_PATH, base_url)
    if link:
        lines.append(f"看分析：{link}")
    return "\n".join(lines)


async def notify_purchase_anomalies(
    db: Any,
    *,
    order: Mapping[str, Any],
    products: Iterable[Mapping[str, Any]],
    quantities: Mapping[Any, Any],
    references: Mapping[Any, Any],
    previous_quantities: Mapping[Any, Any],
    over_ratio: float = OVER_RATIO,
    under_ratio: float = UNDER_RATIO,
    base_url: Optional[str] = None,
) -> List[int]:
    """检测并入队；任何异常（包括出站写库失败）都只记日志，不向提交方抛。"""
    try:
        anomalies = detect_anomalies(
            products=products,
            quantities=quantities,
            references=references,
            previous_quantities=previous_quantities,
            over_ratio=over_ratio,
            under_ratio=under_ratio,
        )
        text = render_purchase_anomaly_text(
            order,
            anomalies,
            over_ratio=over_ratio,
            under_ratio=under_ratio,
            base_url=base_url,
        )
        if not text:
            return []
        order_id = order.get("id")
        reference = f"purchase_anomaly:{order_id}" if order_id else f"purchase_anomaly:{order.get('order_no')}"
        queued = await wecom_outbox.enqueue_topic(
            db,
            TOPIC_PURCHASE_ANOMALY,
            params={"text": text},
            trigger=PushTrigger.EVENT,
            business_reference=reference,
            summary=text.splitlines()[0],
        )
        if not queued:
            logger.info("厨房采购预警无需推送（没有订阅或渠道停用） order=%s", order_id)
        return list(queued)
    except Exception:  # noqa: BLE001 - 群通知不能影响采购单提交
        logger.warning("厨房采购预警入队失败 order=%s", order.get("order_no"), exc_info=True)
        return []
