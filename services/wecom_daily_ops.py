#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运营日报：9 个模块的数据聚合 + Pillow 出图 + 文字降级（ADR 0144）。

这是「推送内容类型」``daily_ops_report`` 的渲染实现，只负责**取数与排版**：

* 取数按模块拆开，每个模块一个 ``_collect_*``，**任何一块失败都只让那一块变空**
  （``None``），不让整张日报发不出去 —— 店长要的是「今天大致怎么样」，不是
  「九个模块全有数据才发」。
* 出图沿用「下周排班表」那套（``services/wecom_schedule_image.py``）：750px 宽、
  字号按「图宽缩放后仍可读」定；本机没有中文字体时返回 ``None``，调用方退文字。
* 图上**不出现营业额与客单价**（ADR 0144）：只保留订单量、在售菜品数这类非金额指标。

模块开关由任务参数 ``modules`` 给出（默认全开）；没开或没数据的模块折叠成一行
小字（「损耗 暂无报损记录」），不再占一张卡。
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from db_core.utils import CHINA_TZ
from services.cjk_font import load_cjk_font
from services.wastage.store import REASONS as WASTAGE_REASONS

logger = logging.getLogger(__name__)

# ── 模块 ────────────────────────────────────────────────────────────────────
# 顺序就是图上的顺序；id 进参数（`modules`），标签只用于文字版与折叠行。
MODULE_ORDERS = "orders"
MODULE_HOURLY = "hourly"
MODULE_DISHES = "dishes"
MODULE_HYGIENE = "hygiene"
MODULE_SCHEDULING = "scheduling"
MODULE_OVERTIME = "overtime"
MODULE_REFUNDS = "refunds"
MODULE_DATA_QUALITY = "data_quality"
MODULE_TRAFFIC = "traffic"
MODULE_WASTAGE = "wastage"
MODULE_PURCHASE = "purchase"

MODULE_LABELS: Dict[str, str] = {
    MODULE_ORDERS: "今日订单",
    MODULE_HOURLY: "分时段订单量",
    MODULE_DISHES: "菜品销量",
    MODULE_HYGIENE: "卫生",
    MODULE_SCHEDULING: "排班",
    MODULE_OVERTIME: "加班补钟",
    MODULE_REFUNDS: "退单与赠单",
    MODULE_DATA_QUALITY: "数据质量",
    MODULE_TRAFFIC: "客流",
    MODULE_WASTAGE: "损耗",
    MODULE_PURCHASE: "厨房采购",
}

DEFAULT_MODULES: Tuple[str, ...] = tuple(MODULE_LABELS)

# ── 版式 ────────────────────────────────────────────────────────────────────
# 与原型同口径：图宽 750，正文 21px。微信按宽度缩到手机屏宽（约 390pt，0.52 倍），
# 21px 缩完约 11pt，是群里能读的下限；再小一档（12–14px）就只剩轮廓了。
IMAGE_WIDTH = 750
MARGIN = 32
CARD_GAP = 14
CARD_PAD = 26
CARD_RADIUS = 14

FONT_TITLE = 50
FONT_CARD_TITLE = 27
FONT_BODY = 21
FONT_SMALL = 18
FONT_TINY = 15
FONT_METRIC = 34
FONT_HERO = 40
FONT_DATE_CHIP = 40

COLOR_PAPER = "#f7f2e9"
COLOR_CARD = "#ffffff"
COLOR_INK = "#2a2118"
COLOR_INK2 = "#6f6252"
COLOR_LINE = "#e7ddcd"
COLOR_ACCENT = "#b8432f"
COLOR_GREEN = "#2f7355"
COLOR_GOLD = "#b07d2b"
COLOR_SOFT = "#f6efe2"
COLOR_BAR = "#d9c9ae"
COLOR_BAR_MID = "#9aae9d"
COLOR_MUTED = "#9a8b76"

MAX_IMAGE_BYTES = 2 * 1024 * 1024

# 退单/赠单卡：一天几十条明细、原因通常三五类，超过这个数就折成「其他 N 类」。
MAX_REFUND_REASONS = 6

# 损耗卡一条一行（单位不统一，合计没有意义）。超过这个条数折成「其他 N 笔」。
MAX_WASTAGE_ROWS = 7

# 采购异常分四类，一类一行（标量 + 前几个例子），不逐条列：一天几十项，
# 列全了卡片会有半屏高，而店长要的是「哪一类多」。
#: 菜品 TOP10 的行高。10 行时 44px 刚好，48px 会把卡片撑到 480px。
DISH_ROW_HEIGHT = 44
PURCHASE_ROW_HEIGHT = 32
#: 采购异常明细的总行数上限（含每类的标题行）。53 项是常态，故障日可能上百项。
MAX_PURCHASE_ROWS = 60
#: 采购明细两列之间的间距。
PURCHASE_COL_GAP = 28
#: 采购四类的标题行高（比明细行高，字号也大一档 —— 53 条明细里要能一眼分出四段）。
PURCHASE_TITLE_HEIGHT = 46
#: 纯文字版每类最多列几条（出图失败时的降级通道，受微信 2048 字节限制）。
TEXT_PURCHASE_ITEMS = 3
WASTAGE_ROW_HEIGHT = 32
REFUND_ROW_HEIGHT = 30
REFUND_REASON_HEIGHT = 28

# 菜品名里的「(普通)」「(大)」前缀是 POS 带过来的规格标记，日报上读着碍眼。
_DISH_PREFIX = re.compile(r"^[（(][^）)]{1,6}[）)]\s*")
# 退单原因自带「[退菜]」后缀（POS 原文），卡片已经分了「退单/赠单」两栏，重复。
_REASON_TAG = re.compile(r"\[(?:退菜|赠菜|退单|赠单)\]$")

WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


# ── 数据形状 ────────────────────────────────────────────────────────────────
@dataclass
class DailyOpsReport:
    """一张日报要用的全部数据；``None`` 表示这一块当天没有数据（折叠成一行）。"""

    business_date: str
    weekday: str = ""
    orders: Optional[Dict[str, Any]] = None
    hourly: Optional[List[Dict[str, Any]]] = None
    dishes: Optional[List[Dict[str, Any]]] = None
    hygiene: Optional[Dict[str, Any]] = None
    scheduling: Optional[Dict[str, Any]] = None
    overtime: Optional[Dict[str, Any]] = None
    refunds: Optional[Dict[str, Any]] = None
    data_quality: Optional[Dict[str, Any]] = None
    traffic: Optional[Dict[str, Any]] = None
    wastage: Optional[Dict[str, Any]] = None
    purchase: Optional[Dict[str, Any]] = None
    # 模块 -> 这一块为什么是空的（折行右侧的小字）。
    empty_reason: Dict[str, str] = field(default_factory=dict)


def module_sort_key(module: str) -> int:
    try:
        return list(DEFAULT_MODULES).index(module)
    except ValueError:
        return len(DEFAULT_MODULES)


def normalize_modules(raw: Any) -> List[str]:
    """参数里的 ``modules`` → 合法且去重的模块列表（按图上顺序）。

    未知 id 直接丢掉：参数由注册表的 schema 校验，但历史出站行里可能留着已经下线
    的模块，渲染时不该因此整张图失败。
    """
    if raw is None:
        return list(DEFAULT_MODULES)
    items = [str(item) for item in raw] if isinstance(raw, (list, tuple)) else [str(raw)]
    selected = {item for item in items if item in MODULE_LABELS}
    return sorted(selected, key=module_sort_key) or list(DEFAULT_MODULES)


# ── 取数 ────────────────────────────────────────────────────────────────────
async def _fetch(db, table: str, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """按表取一次多行（表名只影响库连接的路由，SQL 里自己写）。"""
    async with db.table(table).conn.cursor() as cursor:
        await cursor.execute(sql, list(params))
        return [dict(row) for row in await cursor.fetchall()]


def _parse_day(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


async def _collect_orders(db, business_date: str) -> Optional[Dict[str, Any]]:
    """订单量 + 在售菜品数 + 对上周同一天的环比（不算金额，ADR 0144）。"""
    today = await db.compute_sales_report(business_date, business_date)
    summary = today.get("summary") or {}
    total = int(summary.get("total_orders") or 0)
    if total <= 0:
        return None
    day = _parse_day(business_date)
    delta: Optional[float] = None
    last_week_total = 0
    if day is not None:
        last_week = (day - timedelta(days=7)).isoformat()
        try:
            prev = await db.compute_sales_report(last_week, last_week)
            last_week_total = int((prev.get("summary") or {}).get("total_orders") or 0)
        except Exception as exc:  # noqa: BLE001 —— 环比算不出就不显示
            logger.debug("运营日报环比取数失败：%s", exc)
    if last_week_total > 0:
        delta = round((total - last_week_total) / last_week_total * 100, 1)

    hourly = await _collect_hourly(db, business_date)
    peak = max(hourly, key=lambda row: row["orders"]) if hourly else None
    return {
        "orders": total,
        "items": int(summary.get("total_dishes") or 0),
        "dishes": int(summary.get("unique_dishes") or 0),
        "delta_pct": delta,
        "peak_hour": peak["hour"] if peak else None,
        "peak_orders": peak["orders"] if peak else 0,
    }


async def _collect_hourly(db, business_date: str) -> Optional[List[Dict[str, Any]]]:
    rows = await db.aggregate_sales_trend(business_date, business_date, granularity="hour")
    hours = []
    for row in rows:
        try:
            hour = int(row.get("period"))
        except (TypeError, ValueError):
            continue
        hours.append({"hour": hour, "orders": int(row.get("order_lines") or 0)})
    hours = [item for item in hours if item["orders"] > 0]
    return hours or None


async def _collect_dishes(db, business_date: str) -> Optional[List[Dict[str, Any]]]:
    report = await db.compute_sales_report(business_date, business_date)
    dishes = []
    for row in (report.get("dish_sales") or [])[:10]:
        name = _DISH_PREFIX.sub("", str(row.get("dish_name") or "").strip())
        qty = int(row.get("qty") or 0)
        if name and qty > 0:
            dishes.append({"name": name, "qty": qty})
    return dishes or None


async def _collect_hygiene(db, business_date: str) -> Optional[Dict[str, Any]]:
    """今天的卫生任务完成情况 + 逾期项数。

    口径取自 ``hygiene_daily_instances.status``（待拍 / 待验收 / 已通过 / 待回拍），
    不是事件表：事件只在「驳回」「一次通过」「逾期」这些**结论**上落一行，一天下来
    只提交了几项的话，事件表看不出「今天一共有多少项该做」——而日报要回答的正是
    「今天的卫生做完了没有」。
    """
    rows = await _fetch(
        db,
        "hygiene_daily_instances",
        "SELECT status, COUNT(*) AS n FROM hygiene_daily_instances "
        "WHERE business_date = ? GROUP BY status",
        [business_date],
    )
    counts = {str(row.get("status") or ""): int(row.get("n") or 0) for row in rows}
    passed = counts.get("已通过", 0)
    rework = counts.get("待回拍", 0)
    total = sum(counts.values())
    missed_rows = await _fetch(
        db,
        "hygiene_board_events",
        "SELECT COUNT(*) AS n FROM hygiene_board_events "
        "WHERE business_date = ? AND event_type = ?",
        [business_date, "逾期"],
    )
    missed = int((missed_rows[0] if missed_rows else {}).get("n") or 0)
    if total <= 0 and missed <= 0:
        return None
    reviewed = passed + rework
    rate = round(passed / reviewed * 100) if reviewed else None
    return {
        "done": passed,
        "total": total,
        "rework": rework,
        "missed": missed,
        "rate": rate,
    }


async def _collect_scheduling(db, business_date: str) -> Optional[Dict[str, Any]]:
    """今天与明天：各班人数 + 休假/请假名单（ADR 0144「今天/明天分开」）。"""
    from services.scheduling.store import SchedulingStore

    store = SchedulingStore(db)
    day = _parse_day(business_date)
    if day is None:
        return None
    today = await store.day_detail(day.isoformat())
    tomorrow = await store.day_detail((day + timedelta(days=1)).isoformat())
    if not (today.get("total") or today.get("off_count")):
        return None

    def pack(detail: Mapping[str, Any], when: date) -> Dict[str, Any]:
        shifts = [
            {"name": str((group.get("shift") or {}).get("name") or ""), "count": int(group.get("count") or 0)}
            for group in detail.get("groups") or []
            if int(group.get("count") or 0) > 0
        ]
        off = []
        for person in detail.get("off_people") or []:
            name = str(person.get("name") or "").strip()
            if not name:
                continue
            off.append({"name": name, "leave": bool(person.get("leave")), "half_rest": bool(person.get("half_rest"))})
        return {
            "date": when.isoformat(),
            "weekday": WEEKDAY_LABELS[when.weekday()],
            "total": int(detail.get("total") or 0),
            "shifts": shifts,
            "off": off,
        }

    return {
        "today": pack(today, day),
        "tomorrow": pack(tomorrow, day + timedelta(days=1)),
    }


async def _collect_overtime(db, business_date: str) -> Optional[Dict[str, Any]]:
    """今日加班 + 本月累计 + 补钟余量（小时，余量折成天）。"""
    day = _parse_day(business_date)
    if day is None:
        return None
    month_start = day.replace(day=1).isoformat()
    today_rows = await _fetch(
        db,
        "overtime_entries",
        "SELECT COALESCE(SUM(half_hours), 0) AS hours, COUNT(DISTINCT employee_id) AS people "
        "FROM overtime_entries WHERE entry_date = ? AND status = 'approved' AND half_hours > 0",
        [day.isoformat()],
    )
    month_rows = await _fetch(
        db,
        "overtime_entries",
        "SELECT COALESCE(SUM(half_hours), 0) AS hours FROM overtime_entries "
        "WHERE entry_date >= ? AND entry_date <= ? AND status = 'approved' AND half_hours > 0",
        [month_start, day.isoformat()],
    )
    ledger_rows = await _fetch(
        db,
        "overtime_entries",
        "SELECT COALESCE(SUM(half_hours), 0) AS balance FROM overtime_entries WHERE status = 'approved'",
    )
    today_row = today_rows[0] if today_rows else {}
    today_hours = float(today_row.get("hours") or 0) / 2.0  # half_hours：半小时为单位
    today_people = int(today_row.get("people") or 0)
    month_hours = float((month_rows[0] if month_rows else {}).get("hours") or 0) / 2.0
    balance_hours = float((ledger_rows[0] if ledger_rows else {}).get("balance") or 0) / 2.0
    if today_people <= 0 and month_hours <= 0:
        return None
    return {
        "today_hours": round(today_hours, 1),
        "today_people": today_people,
        "month_hours": round(month_hours, 1),
        "balance_days": round(balance_hours / 8.0, 1),
    }


async def _collect_refunds(db, business_date: str) -> Optional[Dict[str, Any]]:
    """退单 / 赠单（商龙云「异常汇总」）：笔数、金额、按原因聚合。

    读 ``refund_records``（爬虫从 cy7mm 异常明细落的一手数据，一行一个菜品）——
    只有它带 ``reason``。``orders`` 那条老口径（数量为负 / 状态含「退」）在软取消
    时份数会归零、也拿不到原因，做不了「为什么退」这一列。

    不做楼面过滤：退单表只有桌台名 ``point_name``，反查不到档口。
    """
    rows = await _fetch(
        db,
        "refund_records",
        'SELECT "kind", "reason", COUNT(*) AS lines, '
        'COALESCE(SUM("qty"), 0) AS qty, COALESCE(SUM("money"), 0) AS money '
        'FROM "refund_records" WHERE "refund_date" = ? '
        'GROUP BY "kind", "reason" ORDER BY money DESC',
        [business_date],
    )
    totals = {kind: {"lines": 0, "money": 0.0, "qty": 0.0} for kind in ("co", "present")}
    reasons: Dict[str, List[Dict[str, Any]]] = {"co": [], "present": []}
    for row in rows:
        kind = str(row.get("kind") or "co")
        if kind not in totals:
            continue
        count = int(row.get("lines") or 0)
        money = float(row.get("money") or 0)
        totals[kind]["lines"] += count
        totals[kind]["money"] += money
        totals[kind]["qty"] += float(row.get("qty") or 0)
        reasons[kind].append(
            {
                "reason": _REASON_TAG.sub("", str(row.get("reason") or "").strip()).strip()
                or "未填原因",
                "lines": count,
                "money": money,
            }
        )
    if not any(block["lines"] for block in totals.values()):
        return None
    # 原因按金额排序，尾巴折成「其他 N 类」：日报要回答「为什么退」，不是抄明细。
    for kind, items in reasons.items():
        if len(items) > MAX_REFUND_REASONS:
            head = items[: MAX_REFUND_REASONS - 1]
            tail = items[MAX_REFUND_REASONS - 1 :]
            head.append(
                {
                    "reason": f"其他 {len(tail)} 类",
                    "lines": sum(item["lines"] for item in tail),
                    "money": sum(item["money"] for item in tail),
                }
            )
            reasons[kind] = head
    return {"co": totals["co"], "present": totals["present"], "reasons": reasons}


async def _collect_data_quality(business_date: str) -> Optional[Dict[str, Any]]:
    from services.data_quality_alerts import load_reconcile_summary_for_date
    from services.scraper_health import read_health

    health = read_health() or {}
    summary = load_reconcile_summary_for_date(business_date) or {}
    last = health.get("last_reconcile") or {}
    miss_rate = summary.get("miss_rate_pct")
    if miss_rate is None:
        miss_rate = last.get("miss_rate_pct")
    missed_qty = summary.get("missed_qty")
    if missed_qty is None:
        missed_qty = last.get("missed_qty")
    try:
        from services.dish_catalog import get_dish_catalog
        from services.data_quality_alerts import get_unmapped_dish_names

        unmapped = len(await get_unmapped_dish_names(get_dish_catalog()))
    except Exception as exc:  # noqa: BLE001 —— 未映射取不到不拖垮整块
        logger.debug("运营日报未映射菜品取数失败：%s", exc)
        unmapped = None
    if miss_rate is None and missed_qty is None and unmapped is None:
        return None
    return {
        "miss_rate_pct": float(miss_rate) if miss_rate is not None else None,
        "missed_qty": int(missed_qty or 0),
        "unmapped": unmapped,
        "reconciled": bool(summary),
    }


async def _collect_traffic(db, business_date: str) -> Optional[Dict[str, Any]]:
    rows = await _fetch(
        db,
        "dining_traffic",
        "SELECT MAX(in_seat_people) AS seats, MAX(open_tables) AS open_tables, "
        "MAX(total_tables) AS total_tables FROM dining_traffic WHERE plan_day = ?",
        [business_date],
    )
    row = rows[0] if rows else {}
    seats = int(row.get("seats") or 0)
    open_tables = int(row.get("open_tables") or 0)
    total_tables = int(row.get("total_tables") or 0)
    if seats <= 0:
        return None
    return {
        "seats": seats,
        "open_tables": open_tables,
        "total_tables": total_tables,
        "rate": round(open_tables / total_tables * 100) if total_tables else None,
    }


#: 采购异常的四类，按「最该先看」排序；颜色区分严重度。
PURCHASE_KINDS = (
    ("over", "超量", COLOR_ACCENT),
    ("under", "低量", COLOR_GOLD),
    ("no_order", "未下单", COLOR_INK),
    ("zero_stock", "零库存", COLOR_ACCENT),
)


def _purchase_detail(kind: str, row: Mapping[str, Any]) -> str:
    """一条异常的可核对信息：实际 / 参考 + 单位 + 倍数，或天数，或库存。"""
    if kind == "no_order":
        return f"{int(row.get('days') or 0)} 天"
    if kind == "zero_stock":
        return "0"
    parts = [
        f"{_fmt_qty(row.get('quantity') or 0)} / {_fmt_qty(row.get('reference') or 0)}",
        str(row.get("unit") or "").strip(),
        f"{float(row.get('ratio') or 0):.1f}×",
    ]
    return " ".join(part for part in parts if part)


def _purchase_sort_key(kind: str, row: Mapping[str, Any]) -> tuple:
    """严重的在前；同类内超量按倍数降序、低量按倍数升序、未下单按天数降序。"""
    high = 0 if row.get("severity") == "high" else 1
    if kind == "over":
        return (high, -float(row.get("ratio") or 0))
    if kind == "under":
        return (high, float(row.get("ratio") or 0))
    if kind == "no_order":
        return (high, -int(row.get("days") or 0))
    return (high, 0)


async def _collect_purchase(db, business_date: str) -> Optional[Dict[str, Any]]:
    """厨房采购异常：超量 / 低量 / 长期未下单 / 盘点为零，四类逐条列明细。

    口径直接复用采购分析的 ``PurchaseService.anomalies_for_day``（同一个函数体，
    只是不判人）——日报不另算一套：阈值（1.5 / 0.5 / 3 天）一旦漂了，店长看到的
    日报和采购分析页就会互相打脸。

    采购按自然日、日报按营业日（06:00 切）：22:15 发的就是当天，两者同一天；
    早上 8 点发的日报看的是前一天，采购侧也是那一天。

    行数封顶 ``MAX_PURCHASE_ROWS``：53 项是常态（卡片约 1800px），但系统故障时
    能刷出上百项，不封顶会把整张图撑到七八千像素、微信里滑不动。
    """
    from services.purchase.service import PurchaseService

    data = await PurchaseService().anomalies_for_day(db, day=business_date)
    prepared: List[Dict[str, Any]] = []
    total = 0
    high_total = 0
    for kind, label, color in PURCHASE_KINDS:
        rows = data.get(kind) or []
        if not rows:
            continue
        ordered = sorted(rows, key=lambda row: _purchase_sort_key(kind, row))
        high = sum(1 for row in ordered if row.get("severity") == "high")
        items = []
        for row in ordered:
            submitter = ""
            if kind in ("over", "under"):
                names = [str(name).strip() for name in (row.get("submitters") or []) if str(name).strip()]
                submitter = " · ".join(names[:2])
            items.append(
                {
                    "name": str(row.get("name") or "").strip() or "未填品名",
                    "detail": _purchase_detail(kind, row),
                    "by": submitter,
                    "high": row.get("severity") == "high",
                }
            )
        prepared.append(
            {
                "kind": kind,
                "label": label,
                "color": color,
                "count": len(ordered),
                "high": high,
                "items": items,
            }
        )
        total += len(ordered)
        high_total += high
    if total <= 0:
        return None

    groups, folded = _cap_purchase_groups(prepared)
    return {"total": total, "high": high_total, "groups": groups, "folded": folded}


def _cap_purchase_groups(
    prepared: Sequence[Mapping[str, Any]], budget: int = MAX_PURCHASE_ROWS
) -> tuple:
    """按 ``MAX_PURCHASE_ROWS``（含每类的标题行）截断，返回 (groups, 折掉的条数)。

    按四类顺序吃预算：前一类的明细先占满，后面的类别可能整类被折走 —— 顺序本身就是
    「最该先看」的优先级，被折掉的必然是最后那几类。
    """
    groups: List[Dict[str, Any]] = []
    folded = 0
    for group in prepared:
        items = list(group.get("items") or [])
        if budget <= 1:
            folded += len(items)
            continue
        take = min(len(items), budget - 1)
        groups.append(dict(group, items=items[:take]))
        folded += len(items) - take
        budget -= 1 + take
    return groups, folded


async def _collect_wastage(db, business_date: str) -> Optional[Dict[str, Any]]:
    """报损明细：一条一行（品名 · 数量单位 · 原因 · 提交人）。

    不给「合计数量」：单位不统一（g / 份 / 个），加起来没有意义。店长要看的是
    「今天丢了什么、丢了多少、为什么丢、谁报的」，所以逐条列出，超过上限才折行。
    """
    rows = await _fetch(
        db,
        "wastage_records",
        'SELECT "item_name", "quantity", "unit", "reason", "operator_name" '
        'FROM "wastage_records" '
        'WHERE "wastage_date" = ? AND "revoked_at" IS NULL '
        'ORDER BY "reason", "id"',
        [business_date],
    )
    entries: List[Dict[str, Any]] = []
    for row in rows:
        code = str(row.get("reason") or "").strip()
        entries.append(
            {
                "item": str(row.get("item_name") or "").strip() or "未填品名",
                "qty": float(row.get("quantity") or 0),
                "unit": str(row.get("unit") or "").strip(),
                "reason": WASTAGE_REASONS.get(code, code or "未填原因"),
                "code": code,
                # 超管那台共享账号没有员工名，`operator_name` 会是空串 —— 那一行就不画提交人。
                "by": str(row.get("operator_name") or "").strip(),
            }
        )
    if not entries:
        return None
    return {"count": len(entries), "entries": entries}


async def collect_report(
    db,
    *,
    business_date: str,
    modules: Optional[Sequence[str]] = None,
) -> DailyOpsReport:
    """取一张日报的全部数据；单块失败只把那一块置空（不抛）。"""
    selected = normalize_modules(modules)
    day = _parse_day(business_date)
    report = DailyOpsReport(
        business_date=str(business_date),
        weekday=WEEKDAY_LABELS[day.weekday()] if day else "",
    )

    collectors = {
        MODULE_ORDERS: lambda: _collect_orders(db, business_date),
        MODULE_HOURLY: lambda: _collect_hourly(db, business_date),
        MODULE_DISHES: lambda: _collect_dishes(db, business_date),
        MODULE_HYGIENE: lambda: _collect_hygiene(db, business_date),
        MODULE_SCHEDULING: lambda: _collect_scheduling(db, business_date),
        MODULE_OVERTIME: lambda: _collect_overtime(db, business_date),
        MODULE_REFUNDS: lambda: _collect_refunds(db, business_date),
        MODULE_DATA_QUALITY: lambda: _collect_data_quality(business_date),
        MODULE_TRAFFIC: lambda: _collect_traffic(db, business_date),
        MODULE_WASTAGE: lambda: _collect_wastage(db, business_date),
        MODULE_PURCHASE: lambda: _collect_purchase(db, business_date),
    }
    for module in selected:
        collector = collectors.get(module)
        if collector is None:
            continue
        try:
            value = await collector()
        except Exception as exc:  # noqa: BLE001 —— 单块失败不拖垮整张日报
            logger.warning("运营日报「%s」取数失败：%s", MODULE_LABELS.get(module, module), exc)
            value = None
        setattr(report, module, value)

    for module in DEFAULT_MODULES:
        if getattr(report, module) is None:
            report.empty_reason[module] = _empty_reason(module, module in selected)
    return report


def _empty_reason(module: str, selected: bool) -> str:
    if not selected:
        return "未勾选"
    return {
        MODULE_ORDERS: "暂无订单",
        MODULE_HOURLY: "暂无分时段数据",
        MODULE_DISHES: "暂无菜品销量",
        MODULE_HYGIENE: "暂无卫生记录",
        MODULE_SCHEDULING: "暂无排班",
        MODULE_OVERTIME: "暂无加班记录",
        MODULE_REFUNDS: "今日无退单",
        MODULE_DATA_QUALITY: "暂无对账结果",
        MODULE_TRAFFIC: "暂无客流快照",
        MODULE_WASTAGE: "暂无报损记录",
        MODULE_PURCHASE: "今日无采购异常",
    }.get(module, "暂无数据")


# ── 文字版（出图失败时的降级、以及排查用）────────────────────────────────────
def render_text(report: DailyOpsReport) -> str:
    lines: List[str] = [f"【运营日报】{_date_label(report)}"]
    orders = report.orders
    if orders:
        delta = orders.get("delta_pct")
        trend = f"，较上周同日 {delta:+.1f}%" if delta is not None else ""
        lines.append(
            f"订单 {orders['orders']} 单 · 菜件 {orders['items']} 件 · 在售 {orders['dishes']} 道{trend}"
        )
    if report.hourly:
        peak = max(report.hourly, key=lambda row: row["orders"])
        lines.append(f"分时段：高峰 {peak['hour']:02d}:00 · {peak['orders']} 单")
    if report.dishes:
        top = "、".join(f"{item['name']} {item['qty']}" for item in report.dishes)
        lines.append(f"菜品 TOP5：{top}")
    hygiene = report.hygiene
    if hygiene:
        rate = f"，一次通过率 {hygiene['rate']}%" if hygiene.get("rate") is not None else ""
        lines.append(
            f"卫生：已通过 {hygiene['done']}/{hygiene['total']} · 待回拍 {hygiene['rework']}"
            f" · 逾期 {hygiene['missed']}{rate}"
        )
    scheduling = report.scheduling
    if scheduling:
        for key in ("today", "tomorrow"):
            day = scheduling.get(key) or {}
            shifts = " · ".join(f"{item['name']} {item['count']}" for item in day.get("shifts") or [])
            lines.append(f"{_day_prefix(key)} {day.get('date', '')} {shifts}，共 {day.get('total', 0)} 人")
            off = day.get("off") or []
            if off:
                names = "、".join(
                    f"{item['name']}（{'请假' if item['leave'] else ('休半天' if item['half_rest'] else '休')}）"
                    for item in off
                )
                lines.append(f"  {_day_prefix(key)}休假 {len(off)} 人：{names}")
            else:
                lines.append(f"  {_day_prefix(key)}休假 0 人")
    overtime = report.overtime
    if overtime:
        lines.append(
            f"加班补钟：今日 {overtime['today_hours']:g} 时（{overtime['today_people']} 人）"
            f" · 本月 {overtime['month_hours']:g} 时 · 补钟余 {overtime['balance_days']:g} 天"
        )
    refunds = report.refunds
    if refunds:
        parts = []
        for key, label in (("co", "退单"), ("present", "赠单")):
            block = refunds.get(key) or {}
            if block.get("lines"):
                parts.append(f"{label} {block['lines']} 笔 ¥{block['money']:.2f}")
        lines.append("退单与赠单：" + " · ".join(parts))
        for key, label in (("co", "退单"), ("present", "赠单")):
            for item in (refunds.get("reasons") or {}).get(key) or []:
                lines.append(f"  {label}·{item['reason']} {item['lines']} 笔 ¥{item['money']:.2f}")
    quality = report.data_quality
    if quality:
        parts = []
        if quality.get("miss_rate_pct") is not None:
            parts.append(f"漏抓率 {quality['miss_rate_pct']:g}%")
        if quality.get("unmapped") is not None:
            parts.append(f"未映射 {quality['unmapped']} 道")
        parts.append("对账已完成" if quality.get("reconciled") else "对账未完成")
        lines.append("数据质量：" + " · ".join(parts))
    traffic = report.traffic
    if traffic:
        lines.append(
            f"客流：在座 {traffic['seats']} 人 · 开台 {traffic['open_tables']}/{traffic['total_tables']}"
            + (f"（{traffic['rate']}%）" if traffic.get("rate") is not None else "")
        )
    wastage = report.wastage
    if wastage:
        lines.append(f"损耗：{wastage['count']} 条")
        for item in wastage.get("entries") or []:
            unit = f" {item['unit']}" if item.get("unit") else ""
            by = f" · {item['by']}" if item.get("by") else ""
            lines.append(
                f"  {item['item']} {_fmt_qty(item['qty'])}{unit} · {item['reason']}{by}"
            )
    if report.purchase:
        head = f"厨房采购：{report.purchase['total']} 项异常"
        if report.purchase.get("high"):
            head += f"（{report.purchase['high']} 项严重）"
        lines.append(head)
        # 纯文字是出图失败时的降级通道：每类只带三条，剩下的折「等 N 项」——
        # 53 行全写进正文约 4800 字节，微信单条上限 2048，会被拆成四五条连发。
        for group in report.purchase.get("groups") or []:
            lines.append(f"  {group['label']} {group['count']} 项")
            items = group.get("items") or []
            for item in items[:TEXT_PURCHASE_ITEMS]:
                by = f" · {item['by']}" if item.get("by") else ""
                lines.append(f"    {item['name']} {item['detail']}{by}")
            if len(items) > TEXT_PURCHASE_ITEMS:
                lines.append(f"    等 {len(items) - TEXT_PURCHASE_ITEMS} 项")
        if report.purchase.get("folded"):
            lines.append(f"  其余 {report.purchase['folded']} 项未列出")
    folded = [module for module in DEFAULT_MODULES if getattr(report, module) is None]
    if folded:
        lines.append("暂无数据：" + "、".join(MODULE_LABELS[module] for module in folded))
    return "\n".join(lines)


def _day_prefix(key: str) -> str:
    return "今天" if key == "today" else "明天"


def _date_label(report: DailyOpsReport) -> str:
    day = _parse_day(report.business_date)
    if day is None:
        return report.business_date
    return f"{day.year}年{day.month}月{day.day}日 {WEEKDAY_LABELS[day.weekday()]}"


# ── 画图 ────────────────────────────────────────────────────────────────────
def _at(value: float) -> int:
    """文字坐标取整：小数坐标会让小字抗锯齿发灰，缩到手机宽度就是糊。"""
    return int(round(value))


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if font is None or draw.textlength(text, font=font) <= max_width:
        return text
    trimmed = text
    while trimmed and draw.textlength(trimmed + "…", font=font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else ""


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


class _Canvas:
    """把「先量后画」压成一次遍历：所有块高度在这里累加。"""

    def __init__(self) -> None:
        self.blocks: List[Tuple[str, Any, int]] = []
        self.height = 0

    def add(self, kind: str, data: Any, height: int) -> None:
        self.blocks.append((kind, data, height))
        self.height += height


# 卡片内部的高度预算：标题一行（字号 + 与正文的间距），指标区一行（数值 + 标签）。
# 先量后画是两遍，量用的常量必须与 `_draw_*` 里写死的偏移逐字一致，不然卡片会
# 「内容画出卡外」——手机上就是文字压在下一张卡上。
CARD_HEAD = CARD_PAD + FONT_CARD_TITLE + 14
KPI_BODY = 44 + 18 + 8
FOOTER_HEIGHT = 46


def _card_height(body: int) -> int:
    """卡片总高 = 上留白 + 标题行 + 正文区（正文区由调用方按行数算）+ 下留白。"""
    return CARD_HEAD + body + CARD_PAD


def _measure(report: DailyOpsReport, modules: Sequence[str]) -> _Canvas:
    canvas = _Canvas()
    canvas.height = 160  # 头部（标题 + 副标题 + 红线）
    for module in modules:
        if getattr(report, module) is None:
            canvas.add("folded", module, 44)
            continue
        if module in (MODULE_ORDERS, MODULE_HYGIENE, MODULE_OVERTIME, MODULE_TRAFFIC):
            canvas.add(module, getattr(report, module), _card_height(KPI_BODY))
        elif module == MODULE_HOURLY:
            # 柱图区 150 + 小时标签 30 + 数值上方的 4px
            canvas.add(module, report.hourly, _card_height(184))
        elif module == MODULE_DISHES:
            canvas.add(module, report.dishes, _card_height(len(report.dishes or []) * DISH_ROW_HEIGHT - 4))
        elif module == MODULE_SCHEDULING:
            canvas.add(module, report.scheduling, _card_height(2 * 108 - 12))
        elif module == MODULE_REFUNDS:
            canvas.add(
                module,
                report.refunds,
                _card_height(_refund_body_height(report.refunds or {})),
            )
        elif module == MODULE_WASTAGE:
            canvas.add(
                module,
                report.wastage,
                _card_height(_wastage_body_height(report.wastage or {})),
            )
        elif module == MODULE_PURCHASE:
            canvas.add(
                module,
                report.purchase,
                _card_height(_purchase_body_height(report.purchase or {})),
            )
        elif module == MODULE_DATA_QUALITY:
            canvas.add(module, report.data_quality, _card_height(len(_quality_rows(report.data_quality)) * 34))
    # 每块后面都跟一个 CARD_GAP（画的时候最后一块也加），页脚再占 FOOTER_HEIGHT。
    canvas.height += FOOTER_HEIGHT + CARD_GAP * len(canvas.blocks)
    return canvas


def render_png(report: DailyOpsReport, *, modules: Optional[Sequence[str]] = None) -> Optional[bytes]:
    """日报 → PNG 字节；本机没有中文字体时返回 ``None``（调用方退文字）。"""
    if load_cjk_font(20) is None:
        return None
    selected = normalize_modules(modules)
    canvas = _measure(report, selected)

    image = Image.new("RGB", (IMAGE_WIDTH, canvas.height), COLOR_PAPER)
    draw = ImageDraw.Draw(image)
    _draw_header(draw, report)

    y = 160
    for kind, data, height in canvas.blocks:
        if kind == "folded":
            _draw_folded(draw, y, data, report.empty_reason.get(data, "暂无数据"))
        else:
            _draw_card(draw, y, height)
            _draw_module(draw, y, height, kind, data, report)
        y += height + CARD_GAP

    _draw_footer(draw, canvas.height, report)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"运营日报图 {len(data)} 字节，超过企业微信 image 限制 {MAX_IMAGE_BYTES} 字节")
    return data


def _draw_header(draw: ImageDraw.ImageDraw, report: DailyOpsReport) -> None:
    draw.text((MARGIN, 34), "OPERATIONS DAILY", font=load_cjk_font(FONT_TINY), fill=COLOR_INK2)
    draw.text((MARGIN, 52), "运营日报", font=load_cjk_font(FONT_TITLE), fill=COLOR_INK)
    draw.text(
        (MARGIN, 112),
        f"禄运茶居 · {_date_label(report)}",
        font=load_cjk_font(FONT_BODY),
        fill=COLOR_INK2,
    )
    chip_w, chip_h = 92, 100
    chip_x = IMAGE_WIDTH - MARGIN - chip_w
    draw.rounded_rectangle([chip_x, 34, chip_x + chip_w, 34 + chip_h], radius=12, fill=COLOR_INK)
    day = _parse_day(report.business_date)
    if day is not None:
        draw.text((chip_x + chip_w / 2, 34 + 40), f"{day.day}", font=load_cjk_font(FONT_DATE_CHIP),
                  fill=COLOR_PAPER, anchor="mm")
        draw.text((chip_x + chip_w / 2, 34 + 78), f"{day.month:02d}月 · {report.weekday}",
                  font=load_cjk_font(FONT_TINY), fill=COLOR_PAPER, anchor="mm")
    draw.rectangle([MARGIN, 148, IMAGE_WIDTH - MARGIN, 152], fill=COLOR_ACCENT)


def _draw_card(draw: ImageDraw.ImageDraw, y: int, height: int) -> None:
    draw.rounded_rectangle(
        [MARGIN, y, IMAGE_WIDTH - MARGIN, y + height],
        radius=CARD_RADIUS,
        fill=COLOR_CARD,
        outline=COLOR_LINE,
        width=1,
    )


def _card_title(draw: ImageDraw.ImageDraw, y: int, title: str, tag: str = "", tag_color: str = COLOR_INK2) -> int:
    left = MARGIN + CARD_PAD
    draw.text((left, y + CARD_PAD), title, font=load_cjk_font(FONT_CARD_TITLE), fill=COLOR_INK)
    if tag:
        font = load_cjk_font(FONT_TINY)
        width = draw.textlength(tag, font=font)
        draw.text((IMAGE_WIDTH - MARGIN - CARD_PAD - width, y + CARD_PAD + 8), tag, font=font, fill=tag_color)
    return y + CARD_PAD + FONT_CARD_TITLE + 14


def _draw_folded(draw: ImageDraw.ImageDraw, y: int, module: str, reason: str) -> None:
    left = MARGIN + 6
    draw.text((left, y + 8), MODULE_LABELS.get(module, module), font=load_cjk_font(FONT_SMALL), fill=COLOR_MUTED)
    font = load_cjk_font(FONT_SMALL)
    width = draw.textlength(reason, font=font)
    draw.text((IMAGE_WIDTH - MARGIN - 6 - width, y + 8), reason, font=font, fill=COLOR_MUTED)


def _draw_footer(draw: ImageDraw.ImageDraw, height: int, report: DailyOpsReport) -> None:
    font = load_cjk_font(FONT_TINY)
    day = _parse_day(report.business_date)
    if day is not None:
        end = day + timedelta(days=1)
        left = f"营业日 {day.isoformat()} 06:00 — {end.isoformat()} 06:00"
        draw.text((MARGIN, height - 34), left, font=font, fill=COLOR_MUTED)
    right = "厨务管家 自动生成"
    width = draw.textlength(right, font=font)
    draw.text((IMAGE_WIDTH - MARGIN - width, height - 34), right, font=font, fill=COLOR_MUTED)


def _draw_module(draw: ImageDraw.ImageDraw, y: int, height: int, module: str, data: Any, report: DailyOpsReport) -> None:
    drawer = {
        MODULE_ORDERS: _draw_orders,
        MODULE_HOURLY: _draw_hourly,
        MODULE_DISHES: _draw_dishes,
        MODULE_HYGIENE: _draw_hygiene,
        MODULE_SCHEDULING: _draw_scheduling,
        MODULE_OVERTIME: _draw_overtime,
        MODULE_REFUNDS: _draw_refunds,
        MODULE_DATA_QUALITY: _draw_data_quality,
        MODULE_TRAFFIC: _draw_traffic,
        MODULE_WASTAGE: _draw_wastage,
        MODULE_PURCHASE: _draw_purchase,
    }.get(module)
    if drawer is not None:
        drawer(draw, y, height, data, report)


def _kpi_row(draw: ImageDraw.ImageDraw, y: int, items: Sequence[Tuple[str, str, str]]) -> None:
    """一行指标：[(数值, 标签, 颜色)]，等宽分栏。"""
    usable = IMAGE_WIDTH - 2 * MARGIN - 2 * CARD_PAD
    col = usable / max(1, len(items))
    left = MARGIN + CARD_PAD
    for index, (value, label, color) in enumerate(items):
        x = left + index * col
        draw.text((x, y), value, font=load_cjk_font(FONT_METRIC), fill=color)
        draw.text((x, y + 44), label, font=load_cjk_font(FONT_SMALL), fill=COLOR_INK2)
        if index:
            draw.line([(x - 18, y + 4), (x - 18, y + 66)], fill=COLOR_LINE, width=1)


def _draw_orders(draw: ImageDraw.ImageDraw, y: int, height: int, data: Mapping[str, Any], report: DailyOpsReport) -> None:
    delta = data.get("delta_pct")
    tag = f"订单较上周同日 {delta:+.1f}%" if delta is not None else ""
    top = _card_title(draw, y, "今日订单", tag)
    _kpi_row(
        draw,
        top,
        [
            (_fmt_int(data.get("orders")), "订单数", COLOR_INK),
            (_fmt_int(data.get("items")), "菜件数", COLOR_INK),
            (_fmt_int(data.get("dishes")), "在售菜品", COLOR_INK),
        ],
    )


def _draw_hourly(draw: ImageDraw.ImageDraw, y: int, height: int, data: Sequence[Mapping[str, Any]], report: DailyOpsReport) -> None:
    peak = max(data, key=lambda row: row["orders"])
    tag = f"午市高峰 {peak['hour']:02d}:00 · {peak['orders']} 单"
    top = _card_title(draw, y, "分时段订单量", tag)
    chart_top = top + 4
    chart_height = 150
    left = MARGIN + CARD_PAD
    usable = IMAGE_WIDTH - 2 * MARGIN - 2 * CARD_PAD
    gap = 8
    columns = max(1, len(data))
    width = max(18, (usable - gap * (columns - 1)) / columns)
    peak_value = max(row["orders"] for row in data)
    for index, row in enumerate(data):
        bar_h = max(6, row["orders"] / peak_value * chart_height)
        x = left + index * (width + gap)
        color = COLOR_ACCENT if row is peak else (COLOR_BAR_MID if row["orders"] >= peak_value * 0.45 else COLOR_BAR)
        draw.rounded_rectangle(
            [x, chart_top + chart_height - bar_h, x + width, chart_top + chart_height],
            radius=3,
            fill=color,
        )
        draw.text(
            (_at(x + width / 2), chart_top + chart_height + 18),
            f"{row['hour']:02d}",
            font=load_cjk_font(FONT_TINY),
            fill=COLOR_INK2,
            anchor="mm",
        )


def _draw_dishes(draw: ImageDraw.ImageDraw, y: int, height: int, data: Sequence[Mapping[str, Any]], report: DailyOpsReport) -> None:
    top = _card_title(draw, y, "菜品销量 TOP 10", f"全店 {report.orders['dishes'] if report.orders else '—'} 道")
    left = MARGIN + CARD_PAD
    usable = IMAGE_WIDTH - 2 * MARGIN - 2 * CARD_PAD
    # 10 名里有「皇上皇腊味萝卜糕」这种 8 字名，210 的列宽会让它压到条形图上。
    name_w = 240
    qty_w = 54
    track_x = left + name_w + 16
    track_w = usable - name_w - qty_w - 32
    peak = max(item["qty"] for item in data)
    for index, item in enumerate(data, start=1):
        row_y = top + (index - 1) * DISH_ROW_HEIGHT
        draw.text((left, row_y + 6), f"{index:02d}", font=load_cjk_font(FONT_SMALL), fill=COLOR_MUTED)
        draw.text(
            (left + 34, row_y + 4),
            _truncate(draw, item["name"], load_cjk_font(FONT_BODY), name_w - 34),
            font=load_cjk_font(FONT_BODY),
            fill=COLOR_INK,
        )
        draw.rounded_rectangle([track_x, row_y + 14, track_x + track_w, row_y + 22], radius=4, fill=COLOR_SOFT)
        fill_w = max(4, track_w * item["qty"] / peak)
        draw.rounded_rectangle([track_x, row_y + 14, track_x + fill_w, row_y + 22], radius=4, fill=COLOR_GOLD)
        qty_text = str(item["qty"])
        font = load_cjk_font(FONT_BODY)
        draw.text(
            (IMAGE_WIDTH - MARGIN - CARD_PAD - draw.textlength(qty_text, font=font), row_y + 4),
            qty_text,
            font=font,
            fill=COLOR_INK,
        )


def _draw_hygiene(draw: ImageDraw.ImageDraw, y: int, height: int, data: Mapping[str, Any], report: DailyOpsReport) -> None:
    tag = f"一次通过率 {data['rate']}%" if data.get("rate") is not None else "今日进度"
    top = _card_title(draw, y, "卫生", tag, COLOR_GREEN if data.get("rate") is not None and data["rate"] >= 80 else COLOR_INK2)
    _kpi_row(
        draw,
        top,
        [
            (f"{data['done']}/{data['total']}" if data.get("total") else "—", "已通过", COLOR_GREEN if data["done"] else COLOR_INK),
            (str(data["rework"]), "待回拍", COLOR_ACCENT if data["rework"] else COLOR_INK),
            (str(data["missed"]), "逾期项", COLOR_ACCENT if data["missed"] else COLOR_INK),
        ],
    )


def _draw_scheduling(draw: ImageDraw.ImageDraw, y: int, height: int, data: Mapping[str, Any], report: DailyOpsReport) -> None:
    top = _card_title(draw, y, "排班")
    for key in ("today", "tomorrow"):
        day = data.get(key) or {}
        block_top = top
        block_h = 96
        draw.rounded_rectangle(
            [MARGIN + CARD_PAD, block_top, IMAGE_WIDTH - MARGIN - CARD_PAD, block_top + block_h],
            radius=12,
            fill=COLOR_SOFT,
        )
        left = MARGIN + CARD_PAD + 16
        draw.text((left, block_top + 14), _day_prefix(key), font=load_cjk_font(FONT_SMALL), fill=COLOR_INK)
        month_day = (day.get("date") or "")[5:].replace("-", "/")
        draw.text((left + 52, block_top + 14), f"{month_day} {day.get('weekday', '')}",
                  font=load_cjk_font(FONT_TINY), fill=COLOR_INK2)
        total_text = f"{day.get('total', 0)} 人"
        font = load_cjk_font(FONT_TINY)
        draw.text(
            (IMAGE_WIDTH - MARGIN - CARD_PAD - 16 - draw.textlength(total_text, font=font), block_top + 14),
            total_text,
            font=font,
            fill=COLOR_INK2,
        )
        shifts = " · ".join(f"{item['name']} {item['count']}" for item in day.get("shifts") or []) or "无人排班"
        draw.text((left, block_top + 38), shifts, font=load_cjk_font(FONT_BODY), fill=COLOR_INK)
        off = day.get("off") or []
        off_text = f"休假 {len(off)} 人" if off else "休假 0 人"
        draw.text((left, block_top + 66), off_text, font=load_cjk_font(FONT_SMALL),
                  fill=COLOR_ACCENT if off else COLOR_INK2)
        if off:
            names = "、".join(
                f"{item['name']}（{'请假' if item['leave'] else ('休半天' if item['half_rest'] else '休')}）"
                for item in off
            )
            draw.text(
                (left + 96, block_top + 66),
                _truncate(draw, names, load_cjk_font(FONT_SMALL), IMAGE_WIDTH - MARGIN - CARD_PAD - left - 96 - 16),
                font=load_cjk_font(FONT_SMALL),
                fill=COLOR_INK,
            )
        top = block_top + block_h + 12


def _draw_overtime(draw: ImageDraw.ImageDraw, y: int, height: int, data: Mapping[str, Any], report: DailyOpsReport) -> None:
    top = _card_title(draw, y, "加班补钟", "本月累计", COLOR_INK2)
    _kpi_row(
        draw,
        top,
        [
            (f"{data['today_hours']:g} 时", f"今日加班 · {data['today_people']} 人", COLOR_INK),
            (f"{data['month_hours']:g}", "本月累计（时）", COLOR_INK),
            (f"{data['balance_days']:g} 天", "补钟余量", COLOR_ACCENT if data["balance_days"] > 0 else COLOR_INK),
        ],
    )


def _refund_kind_rows(data: Mapping[str, Any]) -> int:
    return sum(1 for kind in ("co", "present") if (data.get(kind) or {}).get("lines"))


def _refund_reason_rows(data: Mapping[str, Any]) -> int:
    return sum(
        len((data.get("reasons") or {}).get(kind) or [])
        for kind in ("co", "present")
        if (data.get(kind) or {}).get("lines")
    )


def _refund_body_height(data: Mapping[str, Any]) -> int:
    """退单卡的正文高度：原因有几行就多高，和别的卡一样按内容量。"""
    return (
        _refund_kind_rows(data) * REFUND_ROW_HEIGHT
        + _refund_reason_rows(data) * REFUND_REASON_HEIGHT
    )


def _draw_refunds(draw: ImageDraw.ImageDraw, y: int, height: int, data: Mapping[str, Any], report: DailyOpsReport) -> None:
    total = int(data["co"]["lines"]) + int(data["present"]["lines"])
    top = _card_title(draw, y, "退单与赠单", f"共 {total} 笔", COLOR_ACCENT)
    left = MARGIN + CARD_PAD
    right = IMAGE_WIDTH - MARGIN - CARD_PAD
    small = load_cjk_font(FONT_SMALL)
    cursor = top
    for kind, label in (("co", "退单"), ("present", "赠单")):
        block = data.get(kind) or {}
        if not block.get("lines"):
            continue
        draw.text((left, cursor), label, font=load_cjk_font(FONT_BODY), fill=COLOR_INK)
        tail = f"{block['lines']} 笔 · ¥{block['money']:.2f}"
        width = draw.textlength(tail, font=small)
        draw.text(
            (right - width, cursor + 3),
            tail,
            font=small,
            fill=COLOR_ACCENT if kind == "co" else COLOR_GOLD,
        )
        cursor += REFUND_ROW_HEIGHT
        for item in (data.get("reasons") or {}).get(kind) or []:
            name = _truncate(draw, item["reason"], small, right - left - 200)
            draw.text((left + 16, cursor), name, font=small, fill=COLOR_INK2)
            tail = f"{item['lines']} 笔 · ¥{item['money']:.2f}"
            width = draw.textlength(tail, font=small)
            draw.text((right - width, cursor), tail, font=small, fill=COLOR_MUTED)
            cursor += REFUND_REASON_HEIGHT


def _quality_rows(data: Mapping[str, Any]) -> List[Tuple[str, str, str]]:
    """数据质量卡的行（量高与绘制共用：行数不同，卡片不能一块高一块空）。"""
    rows: List[Tuple[str, str, str]] = []
    if data.get("miss_rate_pct") is not None:
        rows.append(("采集漏抓率", f"{data['miss_rate_pct']:.1f}%", COLOR_GREEN if data["miss_rate_pct"] < 1 else COLOR_ACCENT))
    if data.get("unmapped") is not None:
        rows.append(("未映射菜品", f"{data['unmapped']} 道", COLOR_ACCENT if data["unmapped"] else COLOR_GREEN))
    rows.append(("对账差异", f"{data['missed_qty']} 份", COLOR_ACCENT if data["missed_qty"] else COLOR_GREEN))
    return rows


def _draw_data_quality(draw: ImageDraw.ImageDraw, y: int, height: int, data: Mapping[str, Any], report: DailyOpsReport) -> None:
    tag = "对账已完成" if data.get("reconciled") else "对账未完成"
    top = _card_title(draw, y, "数据质量", tag, COLOR_GREEN if data.get("reconciled") else COLOR_ACCENT)
    rows = _quality_rows(data)
    left = MARGIN + CARD_PAD
    for index, (label, value, color) in enumerate(rows):
        row_y = top + index * 34
        draw.text((left, row_y), label, font=load_cjk_font(FONT_BODY), fill=COLOR_INK2)
        font = load_cjk_font(FONT_BODY)
        draw.text(
            (IMAGE_WIDTH - MARGIN - CARD_PAD - draw.textlength(value, font=font), row_y),
            value,
            font=font,
            fill=color,
        )
        if index < len(rows) - 1:
            draw.line([left, row_y + 30, IMAGE_WIDTH - MARGIN - CARD_PAD, row_y + 30], fill=COLOR_LINE, width=1)


def _draw_traffic(draw: ImageDraw.ImageDraw, y: int, height: int, data: Mapping[str, Any], report: DailyOpsReport) -> None:
    top = _card_title(draw, y, "客流", f"开台 {data['open_tables']}/{data['total_tables']}")
    _kpi_row(
        draw,
        top,
        [
            (str(data["seats"]), "在座人数", COLOR_INK),
            (f"{data['open_tables']}/{data['total_tables']}", "开台", COLOR_INK),
            (f"{data['rate']}%" if data.get("rate") is not None else "—", "平均上座率", COLOR_INK),
        ],
    )


def _fmt_qty(value: Any) -> str:
    """数量：整数不带小数，小保留一位（1507 / 2.5）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{int(number):,}" if number == int(number) else f"{number:,.1f}"


def _wastage_rows(data: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
    """要画的行 + 被折起来的条数。"""
    entries = list(data.get("entries") or [])
    if len(entries) <= MAX_WASTAGE_ROWS:
        return entries, 0
    return entries[:MAX_WASTAGE_ROWS], len(entries) - MAX_WASTAGE_ROWS


def _wastage_body_height(data: Mapping[str, Any]) -> int:
    rows, folded = _wastage_rows(data)
    return (len(rows) + (1 if folded else 0)) * WASTAGE_ROW_HEIGHT


def _draw_wastage(draw: ImageDraw.ImageDraw, y: int, height: int, data: Mapping[str, Any], report: DailyOpsReport) -> None:
    top = _card_title(draw, y, "损耗", f"{data['count']} 笔", COLOR_ACCENT)
    left = MARGIN + CARD_PAD
    right = IMAGE_WIDTH - MARGIN - CARD_PAD
    small = load_cjk_font(FONT_SMALL)
    tiny = load_cjk_font(FONT_TINY)
    rows, folded = _wastage_rows(data)
    cursor = top
    for item in rows:
        # 品名 + 提交人：谁报的跟丢的是同一件事，挤在同一行，右侧留给数量与原因。
        name = _truncate(draw, item["item"], small, 200)
        draw.text((left, cursor), name, font=small, fill=COLOR_INK)
        if item.get("by") and tiny is not None:
            by_x = left + draw.textlength(name, font=small) + 8
            draw.text((by_x, cursor + 4), f"· {item['by']}", font=tiny, fill=COLOR_INK2)
        unit = f" {item['unit']}" if item.get("unit") else ""
        tail = f"{_fmt_qty(item['qty'])}{unit} · {item['reason']}"
        width = draw.textlength(tail, font=small)
        color = COLOR_ACCENT if item.get("code") in ("spoiled", "damaged") else COLOR_INK2
        draw.text((right - width, cursor), tail, font=small, fill=color)
        cursor += WASTAGE_ROW_HEIGHT
    if folded:
        draw.text((left, cursor), f"其他 {folded} 笔", font=small, fill=COLOR_MUTED)
        cursor += WASTAGE_ROW_HEIGHT


def _purchase_body_height(data: Mapping[str, Any]) -> int:
    """类标题一行（偏高）+ 明细两列竖向排（一半在左、一半在右）。"""
    height = 0
    for group in data.get("groups") or []:
        items = len(group.get("items") or [])
        height += PURCHASE_TITLE_HEIGHT + ((items + 1) // 2) * PURCHASE_ROW_HEIGHT
    if data.get("folded"):
        height += PURCHASE_ROW_HEIGHT
    return height


def _draw_purchase(draw: ImageDraw.ImageDraw, y: int, height: int, data: Mapping[str, Any], report: DailyOpsReport) -> None:
    severe = int(data.get("high") or 0)
    tag = f"{data['total']} 项 · {severe} 项严重" if severe else f"{data['total']} 项"
    top = _card_title(draw, y, "厨房采购", tag, COLOR_ACCENT if severe else COLOR_INK2)
    left = MARGIN + CARD_PAD
    right = IMAGE_WIDTH - MARGIN - CARD_PAD
    small = load_cjk_font(FONT_SMALL)
    tiny = load_cjk_font(FONT_TINY)
    label_font = load_cjk_font(FONT_BODY)
    # 53 行单列会把卡片撑到 1800px，所以明细分两列：每类左列排一半、右列排另一半，
    # 同一类里从上往下读仍然连续（不是左右左右交替）。
    col_w = (right - left - PURCHASE_COL_GAP) // 2
    cursor = top
    for group in data.get("groups") or []:
        # 类标题比明细大一档、带一块色条，还要双绘一遍补出「粗」——Pillow 的中文字体
        # 只能按字号取，没有 weight 可用，0.6px 的偏移重绘是这里唯一能加粗的办法。
        draw.rectangle([left, cursor + 5, left + 5, cursor + 30], fill=group["color"])
        title = f"{group['label']} {group['count']} 项"
        draw.text((left + 15, cursor + 2), title, font=label_font, fill=group["color"])
        draw.text((left + 15.6, cursor + 2), title, font=label_font, fill=group["color"])
        cursor += PURCHASE_TITLE_HEIGHT
        items = group.get("items") or []
        split = (len(items) + 1) // 2
        for index, item in enumerate(items):
            column = 0 if index < split else 1
            row = index if column == 0 else index - split
            x0 = left + column * (col_w + PURCHASE_COL_GAP)
            yy = cursor + row * PURCHASE_ROW_HEIGHT
            by = item.get("by") or ""
            detail = item["detail"]
            block = draw.textlength(detail, font=small)
            if by:
                block += 10 + draw.textlength(by, font=tiny)
            detail_x = x0 + col_w - block
            name = _truncate(draw, item["name"], small, max(48, detail_x - 8 - x0))
            draw.text((x0, yy), name, font=small, fill=COLOR_ACCENT if item.get("high") else COLOR_INK)
            draw.text(
                (detail_x, yy), detail, font=small,
                fill=COLOR_ACCENT if item.get("high") else COLOR_INK2,
            )
            if by:
                draw.text(
                    (detail_x + draw.textlength(detail, font=small) + 10, yy + 3),
                    by, font=tiny, fill=COLOR_INK2,
                )
        cursor += max(split, len(items) - split) * PURCHASE_ROW_HEIGHT
    if data.get("folded"):
        draw.text((left, cursor), f"其余 {data['folded']} 项未列出", font=tiny, fill=COLOR_MUTED)


# ── 统一出站上的渲染器 ──────────────────────────────────────────────────────
class DailyOpsReportRenderer:
    """运营日报在统一出站上的渲染器（``main.py`` 的 lifespan 里登记）。

    出站行里只有**冻结的营业日**与勾选的模块，图和正文要发送时现算 —— 与销售报表同一
    个理由：不登记就会落到默认文本渲染，参数里没有 ``text``，每一条日报都以「正文渲染
    失败」落终态且不重试。出图失败（本机没有中文字体、图超 2MB、画图抛异常）一律退回
    文字版，宁可推一条不那么好看的文字，也不要当天一条都不发。
    """

    def __init__(self, db) -> None:
        self._db = db

    async def render(self, row: Mapping[str, Any], params: Mapping[str, Any]) -> "RenderedDelivery":
        from services.wecom_outbox import RenderedDelivery

        text = str(params.get("text") or "").strip()
        if text:
            return RenderedDelivery(text=text)

        business_date = str(params.get("business_date") or "").strip()
        if not business_date:
            raise ValueError("运营日报的出站行里没有冻结的营业日（business_date）")

        modules = normalize_modules(params.get("modules"))
        report = await collect_report(self._db, business_date=business_date, modules=modules)
        image: Optional[bytes] = None
        try:
            image = render_png(report, modules=modules)
        except Exception as exc:  # noqa: BLE001 —— 出图失败退文字
            logger.warning("运营日报出图失败，改发文字版：%s", exc)
            image = None
        if image:
            return RenderedDelivery(image_bytes=image)
        return RenderedDelivery(text=render_text(report))
