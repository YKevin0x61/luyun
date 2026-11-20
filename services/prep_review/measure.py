#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘度量：同一行，两条并列基线。

店长可能手改机器给的参考值。复盘不能只挑其中一个数当「建议量」——那会让同一天
在当天视图和 90 天汇总里得出相反结论。本模块只做一件事：把每一行同时摊成

- **参考值**（reference）：机器算出来的 suggested_qty；
- **下达量**（planned）：店长当天最终执行的 final_qty；没手改时回落参考值。

基线的选择只在这里发生。当天快照摘要、90 天汇总、分组、售罄阈值与导出都调用
本模块，不再各自写一遍 `final_qty if ... else suggested_qty`。

比例的分母仍是实卖量：`Σ实际 <= 0` 时返回 None，不拿 0 伪装成「刚好」。
这里不读库、不格式化，纯函数便于用表驱动测试钉住口径。
"""

from typing import Any, Dict, List, Optional

REFERENCE = "reference"
PLANNED = "planned"
SOURCE_MANUAL = "manual"
SOURCE_SUGGESTED = "suggested"

_BASELINES = (REFERENCE, PLANNED)


def _number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sold(row: Dict[str, Any]) -> Optional[float]:
    """汇总行用 sold，当天快照行用 sold_qty；两者都只认「有没有实际」。"""
    key = "sold_qty" if "sold_qty" in row else "sold"
    return _number(row.get(key))


def _planned_source(row: Dict[str, Any], final: Optional[float], reference: float) -> str:
    raw = str(row.get("qty_source") or "").strip().lower()
    if raw == SOURCE_MANUAL:
        return SOURCE_MANUAL
    if raw == SOURCE_SUGGESTED:
        # 旧行 / 手工修数可能把 final 改成别的值却留着默认来源。数值与参考
        # 不同就是人改过，不能因为来源列写着 suggested 就冒充「未手改」。
        return SOURCE_SUGGESTED if final is None or final == reference else SOURCE_MANUAL
    return SOURCE_SUGGESTED if final is None or final == reference else SOURCE_MANUAL


def basis(row: Dict[str, Any]) -> Dict[str, Any]:
    """把一条快照 / 当天快照行摊成两条基线。

    `planned_source` 说明下达量从哪来：`manual` 是店长改过，`suggested`
    是没改、直接等于参考值（页面标「未手改」）。
    """
    reference = _number(row.get("suggested_qty"))
    if reference is None:
        reference = 0.0
    final = _number(row.get("final_qty"))
    planned = reference if final is None else final
    return {
        "reference_qty": reference,
        "planned_qty": planned,
        "planned_source": _planned_source(row, final, reference),
    }


def _baseline(rows: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    qty_key = f"{key}_qty"
    sold_total = 0.0
    qty_total = 0.0
    shortfall = 0.0
    overfill = 0.0
    absolute = 0.0
    direction = 0.0
    coverage = 0.0
    covered = 0
    for row in rows:
        sold = _sold(row)
        qty = _number(row.get(qty_key))
        if sold is None or qty is None:
            continue
        sold_total += sold
        qty_total += qty
        diff = qty - sold
        absolute += abs(diff)
        direction += diff
        shortfall += max(0.0, -diff)
        overfill += max(0.0, diff)
        coverage += min(qty, sold)
        if qty >= sold:
            covered += 1
    denom = sold_total if sold_total > 0 else None
    return {
        "qty_total": round(qty_total, 2),
        "shortfall_qty": round(shortfall, 2),
        "overfill_qty": round(overfill, 2),
        "ratio": None if denom is None else qty_total / denom,
        "shortfall_ratio": None if denom is None else shortfall / denom,
        "overfill_ratio": None if denom is None else overfill / denom,
        "absolute_deviation": None if denom is None else absolute / denom,
        "direction_deviation": None if denom is None else direction / denom,
        "coverage_qty": None if denom is None else coverage / denom,
        "covered_items": covered,
        "coverage_items": (covered / len(rows)) if rows else None,
    }


def _sources(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {SOURCE_MANUAL: 0, SOURCE_SUGGESTED: 0}
    for row in rows:
        source = row.get("planned_source")
        if source in counts:
            counts[source] += 1
    return counts


def metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """90 天汇总：同一批品-天同时给参考 / 下达两套指标。"""
    usable = [row for row in rows if _sold(row) is not None]
    days = {row["day"] for row in usable}
    sold_total = sum(_sold(row) or 0.0 for row in usable)
    return {
        "measured_days": len(days),
        "measured_item_days": len(usable),
        "sold_total": round(sold_total, 2),
        "planned_sources": _sources(usable),
        REFERENCE: _baseline(usable, REFERENCE),
        PLANNED: _baseline(usable, PLANNED),
    }


def snapshot_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """当天某个快照钟点的摘要；窗口未结束的行不参与两条基线的指标。"""
    usable = [i for i in items if _sold(i) is not None]
    pending = sum(1 for i in items if _sold(i) is None)
    drifted = sum(1 for i in usable if not i.get("same_rules"))
    sold_out = sum(1 for i in items if i.get("sold_out_at"))
    sold_out_reliable = sum(1 for i in items if i.get("sold_out_reliable"))
    sold_total = sum(_sold(i) or 0.0 for i in usable)
    return {
        "measured": len(usable),
        "pending": pending,
        "drifted": drifted,
        "sold_out": sold_out,
        "sold_out_reliable": sold_out_reliable,
        "sold_total": round(sold_total, 1),
        REFERENCE: _baseline(usable, REFERENCE),
        PLANNED: _baseline(usable, PLANNED),
    }


def day_totals(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """按天汇总两条基线的方向 / 绝对偏差，供分组维度使用。"""
    totals: Dict[str, Dict[str, float]] = {}
    for row in rows:
        sold = _sold(row)
        if sold is None:
            continue
        agg = totals.setdefault(
            row["day"],
            {
                "sold": 0.0,
                "items": 0,
                f"{REFERENCE}_direction": 0.0,
                f"{REFERENCE}_absolute": 0.0,
                f"{PLANNED}_direction": 0.0,
                f"{PLANNED}_absolute": 0.0,
            },
        )
        agg["sold"] += sold
        agg["items"] += 1
        for key in _BASELINES:
            qty = _number(row.get(f"{key}_qty"))
            if qty is None:
                continue
            diff = qty - sold
            agg[f"{key}_direction"] += diff
            agg[f"{key}_absolute"] += abs(diff)
    return totals