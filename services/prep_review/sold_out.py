#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""售罄时刻推算（票 05 / spec §3 决策 2/3/22、§5「事件」「售罄」）。

复盘页要回答「这个品几点卖完的」，但现场没有人会专门去点一个「售罄」按钮。
这里只用两样已经存在的事实反推：

- **07:00 主口径快照**给出的建议量（`final_qty` 优先，空则 `suggested_qty`）；
- 当天订单流水（卖出菜品数量 × 配方系数，与票 04 的 `_sold_stats` 同口径）。

累计销量首次 ≥ 建议量的位置通常落在两条订单之间：用前一条累计和这一条的
增量在两条 `order_time` 之间线性插值到分钟，保证推出来的时刻**不晚于**真正
跨线的那一单。第一条贡献订单就直接超量时没有「前一条」可插值，取本单时间。

三条约定：

- **不猜**：没有 07:00 快照、建议量为空/非正、当天没卖到建议量的品都不写事件。
- **幂等**：同 `(plan_day, prep_item_id, kind='sold_out')` 只留最早一条（订单迟
  到、回填重跑都可能算出更早的时刻），由 `events.record_sold_out` 负责。
- **旁路**：单日推算失败只记 warning，不能拖垮每小时回填；全量 90 天扫描按
  本地自然日最多一次（与 `retention.run_daily` 同一门闩姿势）。
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from database import CHINA_TZ
from services.dish_normalize import normalize_dish_name
from services.prep_plan_service import (
    LOUMIAN_STATION,
    _rule_totals_key,
    prep_plan_service,
)
from services.prep_review.events import record_sold_out
from services.prep_review.retention import PRIMARY_CLOCK

logger = logging.getLogger(__name__)

#: 每小时节拍顺带推算的最近天数（今天 + 昨天）。
RECENT_DAYS = 2
#: 全量回填覆盖的天数（含今天）。
FULL_BACKFILL_DAYS = 90

#: 全量扫描的「同一本地自然日最多一次」门闩；失败不记日期，下一轮重试。
_last_full_run_day: Optional[date] = None


def _to_china(raw: Any) -> Optional[datetime]:
    """解析库里的时间文本并归一到 `CHINA_TZ`；解析不了返回 None。"""
    text = (raw or "").strip() if isinstance(raw, str) else raw
    if not text:
        return None
    try:
        parsed = text if isinstance(text, datetime) else datetime.fromisoformat(str(text))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(CHINA_TZ)


def _day_bounds(plan_day: str) -> Tuple[datetime, datetime]:
    """快照缺窗口时的兜底：按自然日切（与备货计划的营业日口径一致）。"""
    day = date.fromisoformat(plan_day)
    start = datetime(day.year, day.month, day.day, tzinfo=CHINA_TZ)
    return start, start + timedelta(days=1)


def _window(row: Dict[str, Any], plan_day: str) -> Tuple[datetime, datetime]:
    start = _to_china(row.get("window_start"))
    end = _to_china(row.get("window_end"))
    if start is None or end is None or end <= start:
        return _day_bounds(plan_day)
    return start, end


async def _load_targets(db, plan_day: str) -> Dict[int, Dict[str, Any]]:
    """该 `plan_day` 有 07:00 主口径快照的备货品与建议量。

    同一品理论上一天只会有一条 07:00 快照（`snapshot_at + prep_item_id` 唯一），
    真出现重复时取最新写的，不合并 —— 售罄只对得上一份建议量。
    """
    tdb = db.table("prep_plan_snapshots")
    async with tdb.conn.cursor() as cursor:
        await cursor.execute(
            """
            SELECT prep_item_id, item_name, unit, station,
                   suggested_qty, final_qty, window_start, window_end, snapshot_at
              FROM prep_plan_snapshots
             WHERE plan_day = ?
               AND prep_item_id IS NOT NULL
               AND to_char(
                       snapshot_at::timestamptz AT TIME ZONE 'Asia/Shanghai',
                       'HH24:MI'
                   ) = ?
             ORDER BY snapshot_at DESC
            """,
            (plan_day, PRIMARY_CLOCK),
        )
        rows = await cursor.fetchall()

    targets: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        item_id = int(row["prep_item_id"])
        if item_id in targets:
            continue
        # 师傅手改过的量（final_qty）才是现场真正执行的量；没改过才用建议量。
        if row["final_qty"] is not None:
            qty = float(row["final_qty"])
        elif row["suggested_qty"] is not None:
            qty = float(row["suggested_qty"])
        else:
            continue
        if qty <= 0:
            # 建议量 0 的品没有「跨线」可言，写一条 0 点售罄只会污染复盘页。
            continue
        start, end = _window(dict(row), plan_day)
        targets[item_id] = {
            "prep_item_id": item_id,
            "item_name": row["item_name"] or "",
            "unit": row["unit"] or "",
            "station": row["station"] or None,
            "qty": qty,
            "start": start,
            "end": end,
        }
    return targets


async def _load_orders(
    db, start: datetime, end: datetime
) -> List[Dict[str, Any]]:
    """一次取回覆盖全部目标窗口的订单行（与 `_fetch_orders` 同口径）。"""
    tdb = db.table("orders")
    async with tdb.conn.cursor() as cursor:
        await cursor.execute(
            """
            SELECT dish_name, quantity, order_time, status
              FROM orders
             WHERE station != ? AND order_time >= ? AND order_time <= ?
            """,
            (LOUMIAN_STATION, start.isoformat(), end.isoformat()),
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


def _build_timelines(
    orders: List[Dict[str, Any]], exact_rules, norm_rules
) -> Dict[Any, List[Tuple[datetime, float]]]:
    """按备货品聚合「这一单贡献了多少份」，键与 `_sold_qty_for` 完全一致。"""
    timelines: Dict[Any, List[Tuple[datetime, float]]] = defaultdict(list)
    for row in orders:
        order_at = prep_plan_service._order_datetime(row["order_time"])
        if order_at is None:
            continue
        # 退菜行数量已归零，不是销量（与 `_sold_stats` 一致）。
        if row["status"] == "退菜":
            continue
        quantity = float(row["quantity"] or 0)
        if quantity == 0:
            continue
        dish_name = row["dish_name"] or ""
        dishes = exact_rules.get(dish_name) or norm_rules.get(
            normalize_dish_name(dish_name), ()
        )
        for rule in dishes:
            amount = quantity * rule["factor"]
            if amount <= 0:
                continue
            timelines[_rule_totals_key(rule)].append((order_at, amount))
    for pairs in timelines.values():
        pairs.sort(key=lambda pair: pair[0])
    return timelines


def _infer_at(
    pairs: List[Tuple[datetime, float]], target: float
) -> Optional[Tuple[datetime, float, float]]:
    """累计量首次 ≥ 目标量的时刻；返回 (at, 跨线前累计, 跨线后累计)。

    跨线发生在两条订单之间时按增量线性插值，向下取整到分钟并夹在两条订单
    时间之间；第一条贡献订单就超量时没有前一条可插值，直接取本单时间。
    """
    cumulative = 0.0
    prev_at: Optional[datetime] = None
    for order_at, amount in pairs:
        before = cumulative
        cumulative += amount
        if cumulative >= target:
            if prev_at is None or before <= 0:
                return order_at, before, cumulative
            span = (order_at - prev_at).total_seconds()
            if span <= 0 or amount <= 0:
                return order_at, before, cumulative
            fraction = (target - before) / amount
            inferred = prev_at + timedelta(seconds=span * fraction)
            # 「插值到分钟」：向下取整保证不晚于跨线那一单；夹住下界避免取整
            # 跑到上一单之前（订单时间带秒时才会发生）。
            inferred = inferred.replace(second=0, microsecond=0)
            if inferred < prev_at:
                inferred = prev_at
            if inferred > order_at:
                inferred = order_at
            return inferred, before, cumulative
        prev_at = order_at
    return None


async def infer_sold_out(db, plan_day: str) -> dict:
    """推算单个 `plan_day` 各品的售罄时刻并写事件。

    只处理有 07:00 主口径快照的品；没有订单、没卖到建议量的品都不写。事件写入
    走 `record_sold_out`，同键只留最早，重复调用是幂等的。
    """
    targets = await _load_targets(db, plan_day)
    if not targets:
        return {"success": True, "plan_day": plan_day, "targets": 0, "written": 0}

    windows = [(item["start"], item["end"]) for item in targets.values()]
    orders = await _load_orders(
        db,
        min(start for start, _ in windows),
        max(end for _, end in windows),
    )
    exact_rules, norm_rules = await prep_plan_service._load_rules(db)
    timelines = _build_timelines(orders, exact_rules, norm_rules)

    written = 0
    for item in targets.values():
        pairs = list(timelines.get(item["prep_item_id"], ()))
        # 规则没接上主数据时按 (品名, 单位) 文本键；两种键相加，与 `_sold_qty_for` 一致。
        pairs.extend(
            timelines.get((item["item_name"], item["unit"]), ())
        )
        pairs = [
            (at, amount)
            for at, amount in pairs
            if item["start"] <= at <= item["end"]
        ]
        pairs.sort(key=lambda pair: pair[0])
        crossing = _infer_at(pairs, item["qty"])
        if crossing is None:
            continue
        at, before, after = crossing
        if await record_sold_out(
            db,
            at=at.isoformat(),
            plan_day=plan_day,
            prep_item_id=item["prep_item_id"],
            qty_before=round(before, 4),
            qty_after=round(after, 4),
            station=item["station"],
            meta={
                "suggested_qty": item["qty"],
                "source": "order_inference",
            },
        ):
            written += 1
    return {
        "success": True,
        "plan_day": plan_day,
        "targets": len(targets),
        "written": written,
    }


async def _infer_day_quietly(db, plan_day: str) -> bool:
    """单日失败只记 warning：一项坏数据不能让整趟回填停下。"""
    try:
        await infer_sold_out(db, plan_day)
        return True
    except Exception:  # noqa: BLE001 - 旁路推算，失败下一趟再试
        logger.warning("售罄时刻推算失败，跳过 plan_day=%s", plan_day, exc_info=True)
        return False


async def _plan_days_with_primary_snapshot(db, start: date, end: date) -> List[str]:
    """`[start, end]` 里有 07:00 快照的 plan_day（升序、去重）。"""
    tdb = db.table("prep_plan_snapshots")
    async with tdb.conn.cursor() as cursor:
        await cursor.execute(
            """
            SELECT DISTINCT plan_day
              FROM prep_plan_snapshots
             WHERE plan_day >= ? AND plan_day <= ?
               AND to_char(
                       snapshot_at::timestamptz AT TIME ZONE 'Asia/Shanghai',
                       'HH24:MI'
                   ) = ?
             ORDER BY plan_day
            """,
            (start.isoformat(), end.isoformat(), PRIMARY_CLOCK),
        )
        rows = await cursor.fetchall()
    return [row["plan_day"] for row in rows if row["plan_day"]]


async def run_backfill(db, now: Optional[datetime] = None) -> dict:
    """每小时回填入口：最近 2 天每趟都算，近 90 天全量同一自然日最多一次。

    全量扫描只补历史（跳过最近 2 天，避免和上面的每趟重复查）；某天失败不写
    门闩日期，下一小时会连同历史一起重试。
    """
    global _last_full_run_day

    now = (now or datetime.now(CHINA_TZ)).astimezone(CHINA_TZ)
    today = now.date()
    result = {
        "success": True,
        "days": 0,
        "written_days": 0,
        "full_scan": False,
    }

    for offset in range(RECENT_DAYS):
        day = today - timedelta(days=offset)
        result["days"] += 1
        if await _infer_day_quietly(db, day.isoformat()):
            result["written_days"] += 1

    if _last_full_run_day == today:
        return result

    scan_end = today - timedelta(days=RECENT_DAYS)
    scan_start = today - timedelta(days=FULL_BACKFILL_DAYS - 1)
    if scan_end >= scan_start:
        try:
            plan_days = await _plan_days_with_primary_snapshot(db, scan_start, scan_end)
        except Exception:  # noqa: BLE001 - 与单日失败同一姿势，下一轮再扫
            logger.warning("售罄全量回填取天数失败，下一轮再试", exc_info=True)
            return result

        all_ok = True
        for plan_day in plan_days:
            result["days"] += 1
            if await _infer_day_quietly(db, plan_day):
                result["written_days"] += 1
            else:
                all_ok = False
        # 只有整趟都没出错才记门闩；否则下个小时重试失败的那部分。
        if all_ok:
            _last_full_run_day = today
            result["full_scan"] = True
    else:
        _last_full_run_day = today
        result["full_scan"] = True
    return result
