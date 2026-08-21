#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""已结账单与本地 orders 对账。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from database import CHINA_TZ, DatabaseManager, ensure_beijing_datetime
from scraper.order_flow_ids import parse_order_flow_id
from scraper.order_source import SOURCE_DELIVERY, classify_order_source
from services.dish_normalize import strip_trailing_dash_suffix

DishKey = Tuple[str, str]


@dataclass
class ReconcileDiffItem:
    bs_code: str
    dish_name: str
    pos_qty: float
    db_qty: float
    missed_qty: float
    diff_type: str  # full | partial

    @property
    def key(self) -> DishKey:
        return self.bs_code, self.dish_name


@dataclass
class ReconcileResult:
    biz_date: str
    pos_bill_count: int
    pos_line_count: int
    pos_total_qty: float
    db_total_qty: float
    missed_keys: int
    missed_qty: float
    affected_bills: int
    api_failures: List[Dict[str, str]] = field(default_factory=list)
    diffs: List[ReconcileDiffItem] = field(default_factory=list)

    @property
    def miss_rate_pct(self) -> float:
        if self.pos_total_qty <= 0:
            return 0.0
        return round(self.missed_qty / self.pos_total_qty * 100, 4)


def aggregate_pos_dishes(bills_with_dishes: Dict[str, List[Dict]]) -> Dict[DishKey, float]:
    totals: Dict[DishKey, float] = {}
    for bs_code, dish_rows in bills_with_dishes.items():
        for row in dish_rows:
            # 与采集入口一致剥离尾部 (-)，保证与 DB 侧聚合键可比。
            name = strip_trailing_dash_suffix(row.get("name") or "")
            if not name:
                continue
            qty = float(row.get("lastQty", 0) or 0)
            if qty <= 0:
                continue
            key = (bs_code, name)
            totals[key] = totals.get(key, 0.0) + qty
    return totals


def aggregate_db_orders(orders: List[Dict]) -> Dict[DishKey, float]:
    totals: Dict[DishKey, float] = {}
    for order in orders:
        if order.get("status") == "退菜":
            continue
        parsed = parse_order_flow_id(order.get("business_flow_id", ""))
        if not parsed:
            continue
        bs_code, dish_name = parsed
        # 历史 flow_id 内嵌菜名可能带 (-)，剥离后与 POS 侧聚合键对齐。
        dish_name = strip_trailing_dash_suffix(dish_name)
        qty = float(order.get("quantity", 0) or 0)
        key = (bs_code, dish_name)
        totals[key] = totals.get(key, 0.0) + qty
    return totals


def compute_reconcile_diff(
    pos_totals: Dict[DishKey, float],
    db_totals: Dict[DishKey, float],
) -> List[ReconcileDiffItem]:
    diffs: List[ReconcileDiffItem] = []
    all_keys = set(pos_totals.keys()) | set(db_totals.keys())
    for bs_code, dish_name in sorted(all_keys):
        pos_qty = pos_totals.get((bs_code, dish_name), 0.0)
        db_qty = db_totals.get((bs_code, dish_name), 0.0)
        missed = pos_qty - db_qty
        if missed <= 0:
            continue
        diff_type = "full" if db_qty <= 0 else "partial"
        diffs.append(
            ReconcileDiffItem(
                bs_code=bs_code,
                dish_name=dish_name,
                pos_qty=pos_qty,
                db_qty=db_qty,
                missed_qty=missed,
                diff_type=diff_type,
            )
        )
    return diffs


def build_reconcile_result(
    biz_date: str,
    bills_with_dishes: Dict[str, List[Dict]],
    db_orders: List[Dict],
    api_failures: Optional[List[Dict[str, str]]] = None,
) -> ReconcileResult:
    pos_totals = aggregate_pos_dishes(bills_with_dishes)
    db_totals = aggregate_db_orders(db_orders)
    diffs = compute_reconcile_diff(pos_totals, db_totals)
    pos_line_count = sum(len(rows) for rows in bills_with_dishes.values())
    pos_total_qty = sum(pos_totals.values())
    db_total_qty = sum(db_totals.values())
    missed_qty = sum(item.missed_qty for item in diffs)
    affected_bills = len({item.bs_code for item in diffs})
    return ReconcileResult(
        biz_date=biz_date,
        pos_bill_count=len(bills_with_dishes),
        pos_line_count=pos_line_count,
        pos_total_qty=pos_total_qty,
        db_total_qty=db_total_qty,
        missed_keys=len(diffs),
        missed_qty=missed_qty,
        affected_bills=affected_bills,
        api_failures=api_failures or [],
        diffs=diffs,
    )


def reconcile_result_to_dict(result: ReconcileResult) -> Dict[str, Any]:
    return {
        "biz_date": result.biz_date,
        "pos_bill_count": result.pos_bill_count,
        "pos_line_count": result.pos_line_count,
        "pos_total_qty": result.pos_total_qty,
        "db_total_qty": result.db_total_qty,
        "missed_keys": result.missed_keys,
        "missed_qty": result.missed_qty,
        "miss_rate_pct": result.miss_rate_pct,
        "affected_bills": result.affected_bills,
        "api_failures": result.api_failures,
        "diffs": [
            {
                "bs_code": item.bs_code,
                "dish_name": item.dish_name,
                "pos_qty": item.pos_qty,
                "db_qty": item.db_qty,
                "missed_qty": item.missed_qty,
                "diff_type": item.diff_type,
            }
            for item in result.diffs
        ],
    }


def render_reconcile_markdown(result: ReconcileResult) -> str:
    lines = [
        f"# {result.biz_date} 菜品明细对账报告",
        "",
        "## 总览",
        f"- 网页账单数: `{result.pos_bill_count}`",
        f"- 网页明细行数: `{result.pos_line_count}`",
        f"- 网页有效份数: `{result.pos_total_qty}`",
        f"- 本地 DB 份数: `{result.db_total_qty}`",
        f"- 漏抓菜品键数: `{result.missed_keys}`",
        f"- 漏抓总份数: `{result.missed_qty}`",
        f"- 漏抓率: `{result.miss_rate_pct}%`",
        f"- 受影响账单数: `{result.affected_bills}`",
        "",
    ]
    if result.api_failures:
        lines.append("## 网页明细请求失败单号")
        for failure in result.api_failures:
            lines.append(f"- `{failure.get('bs_code', '')}`: {failure.get('error', '')}")
        lines.append("")

    by_bill: Dict[str, List[ReconcileDiffItem]] = {}
    for item in result.diffs:
        by_bill.setdefault(item.bs_code, []).append(item)
    ranked = sorted(by_bill.items(), key=lambda pair: sum(i.missed_qty for i in pair[1]), reverse=True)
    if ranked:
        lines.append("## 漏抓最多的账单（Top 10）")
        for index, (bs_code, items) in enumerate(ranked[:10], start=1):
            missed = sum(item.missed_qty for item in items)
            full_count = sum(1 for item in items if item.diff_type == "full")
            partial_count = len(items) - full_count
            lines.append(
                f"{index}. `{bs_code}` - 漏抓 `{missed}` 份, "
                f"菜品 `{len(items)}` 项 (完全 `{full_count}`, 部分 `{partial_count}`)"
            )
        lines.append("")
        lines.append("## 按账单明细")
        for bs_code, items in ranked:
            missed = sum(item.missed_qty for item in items)
            lines.append(f"### {bs_code}")
            lines.append(f"- 漏抓总份数: `{missed}`")
            lines.append("")
            lines.append("| 类型 | 菜品 | 网页数量 | DB数量 | 漏抓数量 |")
            lines.append("|---|---|---:|---:|---:|")
            for item in items:
                label = "完全漏抓" if item.diff_type == "full" else "部分漏抓"
                lines.append(
                    f"| {label} | {item.dish_name} | {item.pos_qty} | {item.db_qty} | {item.missed_qty} |"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def load_db_orders_for_biz_date(db: DatabaseManager, biz_date: str) -> List[Dict]:
    from datetime import timedelta

    begin = datetime.fromisoformat(f"{biz_date}T06:00:00").replace(tzinfo=CHINA_TZ)
    end = begin + timedelta(days=1)
    return await db.orders.get_orders(start_time=begin, end_time=end, limit=-1)


async def build_fix_orders_from_diffs(
    diffs: List[ReconcileDiffItem],
    bills_meta: Dict[str, Dict],
    *,
    order_lines,
) -> List[Dict]:
    """根据漏抓 diff 生成待补录订单行（经 OrderLineBuilder）。"""
    from scraper.order_line_builder import FLOW_MODE_RECONCILE, RawOrderLine

    orders: List[Dict] = []
    now_iso = datetime.now(CHINA_TZ).isoformat()
    reconcile_seq: Dict[DishKey, int] = {}

    for item in diffs:
        bill = bills_meta.get(item.bs_code, {})
        source = classify_order_source(
            table_number=bill.get("pointName") or bill.get("tableNumber") or "",
            bill_source=bill.get("billSource") or "",
            order_source=bill.get("orderSource") or "",
            people_qty=bill.get("peopleQty"),
        )
        if source == SOURCE_DELIVERY:
            table_number = bill.get("pointName") or "外卖"
        else:
            table_number = bill.get("pointName") or bill.get("tableNumber") or "未知"
        order_time = bill.get("settleTime") or now_iso
        try:
            order_time_dt = ensure_beijing_datetime(order_time)
        except Exception:
            order_time_dt = datetime.now(CHINA_TZ)
        price = float(bill.get("price", 0) or 0)
        dish_key = item.key
        start_index = reconcile_seq.get(dish_key, 1)
        missed = int(item.missed_qty)
        reconcile_seq[dish_key] = start_index + missed
        rows = await order_lines.expand(
            RawOrderLine(
                bs_code=item.bs_code,
                dish_name=item.dish_name,
                quantity=missed,
                unit_price=price,
                table_number=table_number,
                order_time=order_time_dt,
                flow_mode=FLOW_MODE_RECONCILE,
                start_index=start_index,
                overlays={
                    "status": "已结",
                    "priority": "normal",
                    "notes": f"reconcile_fix|{item.bs_code}",
                    "source": source,
                },
            )
        )
        orders.extend(rows)
    return orders


def write_reconcile_outputs(result: ReconcileResult, output_dir: Path) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"reconcile_{result.biz_date}.md"
    json_path = output_dir / f"reconcile_{result.biz_date}.json"
    md_path.write_text(render_reconcile_markdown(result), encoding="utf-8")
    json_path.write_text(
        json.dumps(reconcile_result_to_dict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return md_path, json_path


def write_scraper_health(data_dir: Path, payload: Dict[str, Any]) -> Path:
    path = data_dir / "scraper_health.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def run_settled_reconcile(
    adapter,
    db: DatabaseManager,
    biz_date: str,
    *,
    sleep_between_bills_s: float = 0.3,
    on_progress: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> Tuple[ReconcileResult, Dict[str, Dict]]:
    import asyncio

    begin, end = adapter.biz_datetime_range(biz_date)
    bills = await adapter.fetch_settled_bill_list(begin, end, delivery_only=False)
    bills_with_dishes: Dict[str, List[Dict]] = {}
    bills_meta: Dict[str, Dict] = {}
    api_failures: List[Dict[str, str]] = []
    total = len(bills)

    for index, bill in enumerate(bills, start=1):
        bs_code = bill.get("bsCode") or bill.get("bsId") or ""
        if bs_code:
            bills_meta[bs_code] = bill
            try:
                dishes = await adapter.fetch_settled_bill_raw_dishes(bill, begin, end)
                bills_with_dishes[bs_code] = dishes
            except Exception as exc:
                api_failures.append({"bs_code": bs_code, "error": str(exc)})
                bills_with_dishes[bs_code] = []
        if on_progress:
            await on_progress(index, total)
        if sleep_between_bills_s > 0:
            await asyncio.sleep(sleep_between_bills_s)

    db_orders = await load_db_orders_for_biz_date(db, biz_date)
    result = build_reconcile_result(biz_date, bills_with_dishes, db_orders, api_failures)
    return result, bills_meta


def _bill_is_delivery(bill: Dict, delivery_platforms=None) -> bool:
    """与实时采集一致的外卖判定：无人数或平台名命中。"""
    return (
        classify_order_source(
            table_number=bill.get("pointName") or "",
            bill_source=bill.get("billSource") or "",
            order_source=bill.get("orderSource") or "",
            people_qty=bill.get("peopleQty"),
            delivery_platforms=delivery_platforms,
        )
        == SOURCE_DELIVERY
    )


def _bs_codes_from_flow_ids(flow_ids) -> set:
    codes = set()
    for flow_id in flow_ids or []:
        parsed = parse_order_flow_id(flow_id)
        if parsed:
            codes.add(parsed[0])
    return codes


def _skipped_cancel_summary(*, error=None) -> Dict[str, Any]:
    summary = {
        "checked": 0,
        "cancelled_bills": 0,
        "cancelled_rows": 0,
        "restored_bills": 0,
        "restored_rows": 0,
        "skipped": True,
    }
    if error:
        summary["error"] = error
    return summary


async def sweep_cancelled_delivery_for_biz_date(adapter, db, biz_date: str) -> Dict[str, Any]:
    """对账/切日窗口：该营业日 POS 已结外卖列表 vs 本地库。

    - DB 未取消、POS 已无 → 软删除（退菜 + 归零）。
    - DB 已取消、POS 仍在 → 拉明细 revert 恢复数量。
    防误杀：拉取失败或当日外卖列表为空时直接跳过。
    """
    begin, end = adapter.biz_datetime_range(biz_date)
    try:
        present_bills = await adapter.fetch_settled_bill_list(begin, end, delivery_only=True)
    except Exception as exc:
        return _skipped_cancel_summary(error=str(exc))

    # 成功但为空：极可能是异常状态而非“全天外卖都被取消”，跳过以防误杀
    if not present_bills:
        return _skipped_cancel_summary()

    present_bills_by_code = {}
    for bill in present_bills:
        bs_code = bill.get("bsCode") or bill.get("bsId") or ""
        if bs_code:
            present_bills_by_code[bs_code] = bill
    present_bs_codes = set(present_bills_by_code)

    flow_ids = await db.orders.get_delivery_flow_ids(begin, end)
    db_bs_codes = _bs_codes_from_flow_ids(flow_ids)
    cancelled_flow_ids = await db.orders.get_cancelled_delivery_flow_ids(begin, end)
    cancelled_bs_codes = _bs_codes_from_flow_ids(cancelled_flow_ids)

    vanished = db_bs_codes - present_bs_codes
    cancelled_rows = 0
    for bs_code in sorted(vanished):
        cancelled_rows += await db.orders.mark_delivery_cancelled(bs_code)

    restored_rows = 0
    restored_bills = 0
    to_restore = cancelled_bs_codes & present_bs_codes
    fetch_lines = getattr(adapter, "fetch_delivery_order_lines", None)
    if fetch_lines is None:
        fetch_lines = getattr(adapter, "_get_delivery_bill_dishes", None)
    if to_restore and fetch_lines is not None:
        for bs_code in sorted(to_restore):
            bill = present_bills_by_code.get(bs_code)
            if not bill:
                continue
            restored_orders = await fetch_lines(bill)
            if restored_orders:
                restored = await db.orders.revert_delivery_cancelled(restored_orders)
                if restored:
                    restored_bills += 1
                    restored_rows += restored

    return {
        "checked": len(db_bs_codes | cancelled_bs_codes),
        "cancelled_bills": len(vanished),
        "cancelled_rows": cancelled_rows,
        "restored_bills": restored_bills,
        "restored_rows": restored_rows,
        "skipped": False,
    }
