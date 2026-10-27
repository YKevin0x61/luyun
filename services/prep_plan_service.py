#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划服务（ADR 0117 重建）。

只回答一个问题：**今天从现在到打烊，各档口还要做多少、还要买多少。**

算法（刻意做得能被人手算复核）：

    基准量 = 近 4 个同星期几、在「剩余窗口」同样钟点区间内的实际用量均量
    实际用量 = 卖出菜品数量 × 配方系数（semi_finished_rules）
    建议量 = 基准量 × 整店水位系数 + 该品的残差 conformal 分位

整店水位系数 = 近 7 天 / 前 21 天的点单量比（限幅 0.8~1.3，只用当天之前的数据）。
残差 = 历史上每一天「实际用量 − 当天基准量」，取近 28 天的 conformal 次序统计量，
α=0.10 即目标覆盖率 90%。ADR 0119 记录了这次从「均量」改成「均量 + 残差缓冲」的原因：
实测均量口径只有 54.8% 的品-天够卖，缺货量占 18.2%。

仍然不做的事（ADR 0117 砍掉）：生意日档位加权、已消耗扣减、历史近邻日混合、
24 小时窗口、歇业日日历。

营业日按**自然日**切，不按 06:00。备货是「今天做今天卖」的事，师傅不会在凌晨
两点打开看；零点到开店前这段落在自然日边界上的偏差是已知的，不修。
"""

import hashlib
import json
import logging
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from config import KITCHEN_STATIONS
from database import CHINA_TZ
from services.dish_normalize import normalize_dish_name

logger = logging.getLogger(__name__)

SAME_WEEKDAY_LOOKBACK = 4
HISTORY_DAYS = 7
DEFAULT_OPEN = "07:00"
DEFAULT_CLOSE = "21:30"
RUNTIME_SETTINGS_KEY = "prep_plan_runtime"
FORECAST_SETTINGS_KEY = "prep_plan_forecast"
FALLBACK_RUNTIME_SETTINGS_KEY = "scraper_runtime"
LOUMIAN_STATION = "loumian"

# 预测口径（ADR 0120：少剩货优先，取需求分布的 p40，不再加任何安全缓冲）
#
# 为什么不加缓冲：24 小时保质 + 不能结转，多做出来的就是报废。实测（90 个营业日、8 周
# 同星期几、07:00 快照）「单边多出」随分位的变化是：p30 12.0% / p40 15.7% / 均量 20.2%
# / p80 36.3%。上一轮的 conformal 缓冲为了「九成够卖」把净多做推到 +76.7%，与新鲜度直接冲突。
FORECAST_QUANTILE = 0.40       # 取需求分位；0.4 = 剩货 15.7%、缺货 21.6%
FORECAST_WINDOW_WEEKS = 8      # 同星期几取近 8 周（p40 在 4 个样本上估不稳）
STORE_FACTOR_SHORT_DAYS = 7    # 整店水位：近 7 天 …
STORE_FACTOR_LONG_DAYS = 21    # … 对前 21 天
# 水位限幅收到 ±15%：趋势修正是真需求，但 ±30% 对「少剩货」太激进
STORE_FACTOR_MIN = 0.75
STORE_FACTOR_MAX = 1.30
# 参考值的时间跨度：现在起的未来 24 小时（与保质期对齐，见 ADR 0122）
FORECAST_HORIZON_HOURS = 24
# 偶发品下限：近 7 天卖出过的品，建议量至少 1 个单位，否则 p40 会让长尾品静默消失
SPARSE_FLOOR_DAYS = 7
SPARSE_FLOOR_QTY = 1.0

# 配方岗位 → 档口（沿用旧口径；半成品规则里 position 用的就是这一套名字）
# 岗位 = 备货品「谁做的」，不是档口本身：多个岗位可以归到同一个档口
# （熟笼/熟笼冻品/馅档/案板 都算熟笼档）。2026-10-26 用户裁定删掉三个不是岗位的名字：
#   「明档」「熟笼冻品」「西饼凉菜」——它们是旧数据里的子分类写法，跟「明档1/明档2」
#   重复或语义不清。存量数据由 `0038_normalize_positions.sql` 归并到近义岗位。
POSITION_STATION_MAP = {
    "熟笼": "shulong",
    "馅档": "shulong",
    "案板": "shulong",
    "西饼": "xibing",
    "肠粉": "changfen",
    "明档1": "mingdang1",
    "明档2": "mingdang2",
    "煎炸": "jianzha",
}


def _quantile(values: List[float], q: float) -> float:
    """经验分位（线性插值）。

    ADR 0120 起建议量取需求分布的 p40：少剩货优先，不加任何安全缓冲。
    24 小时保质下多做出来的就是报废，缓冲的代价是真金白银。
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    q = min(max(q, 0.0), 1.0)
    pos = q * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(ordered) - 1)
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo))


class _UsageIndex:
    """`(日, 备货品) -> [(时间戳, 用量)]` 的有序索引，负责按时间窗口求和。

    一次查询把整个回看窗口的订单都折进来，之后各个口径的窗口求和都是二分，
    不再为每个窗口单独跑 SQL（否则 28 天残差 × 4 个候选日会把页面拖死）。
    """

    def __init__(self) -> None:
        self._by_key: Dict[Tuple[date, Tuple[str, str]], List[Tuple[int, float]]] = defaultdict(list)
        self._raw: Dict[date, List[Tuple[int, float]]] = defaultdict(list)
        self._idx: Dict[Tuple[date, Tuple[str, str]], List[int]] = {}
        self._raw_idx: Dict[date, List[int]] = {}

    def add(self, day: date, key: Tuple[str, str], epoch: int, qty: float) -> None:
        self._by_key[(day, key)].append((epoch, qty))

    def add_raw(self, day: date, epoch: int, qty: float) -> None:
        self._raw[day].append((epoch, qty))

    def finalize(self) -> None:
        for rows in self._by_key.values():
            rows.sort()
        for rows in self._raw.values():
            rows.sort()

    def days(self) -> set:
        return set(self._raw)

    @staticmethod
    def _sum(rows, idx, start: int, end: int) -> float:
        lo = bisect_left(idx, start)
        hi = bisect_right(idx, end)
        if lo >= hi:
            return 0.0
        return sum(row[1] for row in rows[lo:hi])

    def window(self, day: date, key: Tuple[str, str], start: int, end: int) -> float:
        rows = self._by_key.get((day, key))
        if not rows:
            return 0.0
        idx = self._idx.get((day, key))
        if idx is None:
            idx = [row[0] for row in rows]
            self._idx[(day, key)] = idx
        return self._sum(rows, idx, start, end)

    def raw_window(self, day: date, start: int, end: int) -> float:
        """整店水位用：不分备货品，按原始点单数量汇总（不依赖配方系数）。"""
        rows = self._raw.get(day)
        if not rows:
            return 0.0
        idx = self._raw_idx.get(day)
        if idx is None:
            idx = [row[0] for row in rows]
            self._raw_idx[day] = idx
        return self._sum(rows, idx, start, end)


def _parse_hhmm(text: Optional[str], fallback: str) -> time:
    for candidate in ((text or "").strip(), fallback):
        try:
            hour_text, minute_text = candidate.split(":", 1)
            return time(int(hour_text), int(minute_text[:2]))
        except (ValueError, AttributeError):
            continue
    return time(7, 0)


def _at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=CHINA_TZ)


def _epoch(moment: datetime) -> int:
    return int(moment.timestamp())


def _parse_stamp(text) -> Optional[datetime]:
    """解析库里存的 ISO 时间戳；解析不了就返回 None（宁可不结转，也不认过期货）。"""
    raw = (text or "").strip() if isinstance(text, str) else text
    if not raw:
        return None
    try:
        parsed = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(CHINA_TZ)


def _loads_json(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


class PrepPlanService:
    """备货计划业务服务。"""

    # ------------------------------------------------------------------ 配置

    async def _runtime(self, db) -> Tuple[time, time]:
        """开门 / 打烊时刻。优先 `prep_plan_runtime`，没有就沿用采集时段。"""
        cfg = await db.settings_get_json(RUNTIME_SETTINGS_KEY, None)
        if not isinstance(cfg, dict):
            cfg = await db.settings_get_json(FALLBACK_RUNTIME_SETTINGS_KEY, {}) or {}
        open_t = _parse_hhmm(cfg.get("open") or cfg.get("work_start"), DEFAULT_OPEN)
        close_t = _parse_hhmm(cfg.get("close") or cfg.get("work_end"), DEFAULT_CLOSE)
        if close_t <= open_t:
            close_t = _parse_hhmm(DEFAULT_CLOSE, DEFAULT_CLOSE)
        return open_t, close_t

    # ------------------------------------------------------------------ 基础数据

    async def _load_rules(self, db) -> Tuple[Dict[str, List[dict]], Dict[str, List[dict]]]:
        """菜品 → 备货品换算规则。原名精确匹配 + 归一化名兜底（沿用旧口径）。"""
        exact: Dict[str, List[dict]] = defaultdict(list)
        norm: Dict[str, List[dict]] = defaultdict(list)
        tdb = db.table("semi_finished_rules")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT dish_name, semi_name, position, factor, unit FROM semi_finished_rules"
            )
            rows = await cursor.fetchall()
        for row in rows:
            entry = {
                "semi_name": row["semi_name"] or "",
                "position": row["position"] or "",
                "factor": float(row["factor"] or 1),
                "unit": row["unit"] or "",
            }
            exact[row["dish_name"] or ""].append(entry)
            norm[normalize_dish_name(row["dish_name"] or "")].append(entry)
        return exact, norm

    async def _load_items(self, db) -> List[dict]:
        tdb = db.table("prep_items")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, item_name, station, position, unit, kind, category
                FROM prep_items
                WHERE active = 1
                ORDER BY item_name
                """
            )
            rows = await cursor.fetchall()
        return [
            {
                "prep_item_id": int(row["id"]),
                "item_name": row["item_name"] or "",
                "station": row["station"] or "",
                "position": row["position"] or "",
                "unit": row["unit"] or "",
                "kind": row["kind"] or "made",
                "category": row["category"] or "",
            }
            for row in rows
        ]

    # ------------------------------------------------------------------ 算法
    #
    # 建议量 = p40(近 8 周同星期几、同钟点窗口) × 整店水位 − 未过期的在库量
    #
    # 三段各有各的职责，谁都能单独复核：
    #   1. p40：少剩货优先。不做安全缓冲——24 小时保质下多做出来的就是报废。
    #   2. 整店水位：修「最近明显比前几周旺/淡」的系统性偏差，限幅 0.85~1.15。
    #   3. 扣在库：昨天剩的（且没过期）先从今天的量里抵掉，否则就是重复生产。
    # 偶发品给 1 个单位下限，避免 p40 把长尾品压成 0 后静默消失。

    async def _fetch_range(self, db, start_dt: datetime, end_dt: datetime) -> List[dict]:
        """一次取出一个时间跨度内的订单行，Python 侧再分桶。"""
        sql = (
            "SELECT dish_name, quantity, order_time FROM orders "
            f"WHERE station != '{LOUMIAN_STATION}' "
            "AND order_time >= ? AND order_time <= ?"
        )
        tdb = db.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(sql, (start_dt.isoformat(), end_dt.isoformat()))
            rows = await cursor.fetchall()
        return [
            {
                "dish_name": row["dish_name"] or "",
                "quantity": float(row["quantity"] or 0),
                "order_time": row["order_time"] or "",
            }
            for row in rows
        ]

    async def _fetch_orders(
        self, db, intervals: List[Tuple[str, datetime, datetime]]
    ) -> List[dict]:
        """按若干区间查订单行（复盘 / 在库量用；建议量走 `_fetch_range`）。"""
        clauses: List[str] = []
        params: List[Any] = []
        for _scope, start_dt, end_dt in intervals:
            clauses.append("(order_time >= ? AND order_time <= ?)")
            params.extend([start_dt.isoformat(), end_dt.isoformat()])
        sql = (
            "SELECT dish_name, quantity, order_time FROM orders "
            f"WHERE station != '{LOUMIAN_STATION}' AND (" + " OR ".join(clauses) + ")"
        )
        tdb = db.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(sql, tuple(params))
            rows = await cursor.fetchall()
        return [
            {
                "dish_name": row["dish_name"] or "",
                "quantity": float(row["quantity"] or 0),
                "order_time": row["order_time"] or "",
            }
            for row in rows
        ]

    @staticmethod
    def _order_datetime(raw: str) -> Optional[datetime]:
        text = (raw or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CHINA_TZ)
        return parsed.astimezone(CHINA_TZ)

    async def _forecast_config(self, db) -> dict:
        """预测参数。没配就用回测定下来的默认值。"""
        raw = None
        try:
            raw = await db.settings_get_json(FORECAST_SETTINGS_KEY, None)
        except Exception:  # 配置表读不到不该让整页挂掉
            logger.warning("备货预测参数读不到，用默认值")
        cfg = raw if isinstance(raw, dict) else {}
        quantile = float(cfg.get("quantile", FORECAST_QUANTILE))
        quantile = min(max(quantile, 0.05), 0.95)
        weeks = int(cfg.get("window_weeks") or FORECAST_WINDOW_WEEKS)
        weeks = min(max(weeks, 4), 12)
        return {
            "quantile": quantile,
            "window_weeks": weeks,
            # 水位要回看 28 天（7+21），同星期几要回看 weeks*7 天，取大者再加 1 天余量
            "lookback_days": max(weeks * 7, STORE_FACTOR_SHORT_DAYS + STORE_FACTOR_LONG_DAYS) + 1,
            "factor_min": float(cfg.get("factor_min", STORE_FACTOR_MIN)),
            "factor_max": float(cfg.get("factor_max", STORE_FACTOR_MAX)),
        }

    async def compute_suggestions(self, db, now: datetime) -> dict:
        """算出今天这份清单的建议量。返回纯计算结果，不落库。"""
        open_t, close_t = await self._runtime(db)
        cfg = await self._forecast_config(db)
        today = now.date()
        # ADR 0122：参考值覆盖「现在起的未来 24 小时」，与 24 小时保质对齐。
        # 这样下午打开页面不会因为「到打烊只剩 7 小时」而看着数字腰斩。
        window_start = now
        window_end = now + timedelta(hours=FORECAST_HORIZON_HOURS)
        after_close = now.time() >= close_t
        closed = False  # 滚动窗口下不存在「今天已打烊就没数」这回事
        clock_t = window_start.time()

        rows = await self._fetch_range(
            db, _at(today - timedelta(days=cfg["lookback_days"]), time(0, 0)), now
        )
        exact_rules, norm_rules = await self._load_rules(db)
        items = await self._load_items(db)

        usage = _UsageIndex()
        for row in rows:
            parsed = self._order_datetime(row["order_time"])
            if parsed is None:
                continue
            day = parsed.date()
            epoch = int(parsed.timestamp())
            qty = row["quantity"]
            usage.add_raw(day, epoch, qty)
            if qty == 0:
                continue
            dishes = exact_rules.get(row["dish_name"]) or norm_rules.get(
                normalize_dish_name(row["dish_name"]), ()
            )
            for rule in dishes:
                usage.add(day, (rule["semi_name"], rule["unit"]), epoch, qty * rule["factor"])
        usage.finalize()

        days_with_orders = usage.days()
        today_candidates = [
            today - timedelta(days=7 * i) for i in range(1, cfg["window_weeks"] + 1)
        ]
        active_candidates = [day for day in today_candidates if day in days_with_orders]
        recent_days = [
            today - timedelta(days=i)
            for i in range(1, SPARSE_FLOOR_DAYS + 1)
            if (today - timedelta(days=i)) in days_with_orders
        ]

        def _span(day):
            """从 day 的同钟点起算、往后 24 小时：[start, end)。跨天要拆成两段求和。"""
            start = _at(day, clock_t)
            return start, start + timedelta(hours=FORECAST_HORIZON_HOURS)

        def window_qty(day, key):
            start, end = _span(day)
            total = usage.window(day, key, _epoch(start), _epoch(end))
            # 窗口跨过零点：把次日那段补上
            total += usage.window(day + timedelta(days=1), key, _epoch(start), _epoch(end))
            return total

        def store_volume(day):
            if day not in days_with_orders:
                return None
            start, end = _span(day)
            total = usage.raw_window(day, _epoch(start), _epoch(end))
            total += usage.raw_window(day + timedelta(days=1), _epoch(start), _epoch(end))
            return total

        factor_cache: Dict[date, float] = {}

        def day_factor(day) -> float:
            """整店水位：近 7 天 / 前 21 天。只用 day 之前的数据，不泄漏。"""
            if day in factor_cache:
                return factor_cache[day]
            short = [
                v
                for v in (store_volume(day - timedelta(days=i)) for i in range(1, STORE_FACTOR_SHORT_DAYS + 1))
                if v is not None
            ]
            long_ = [
                v
                for v in (
                    store_volume(day - timedelta(days=i))
                    for i in range(STORE_FACTOR_SHORT_DAYS + 1, STORE_FACTOR_SHORT_DAYS + STORE_FACTOR_LONG_DAYS + 1)
                )
                if v is not None
            ]
            short_avg = (sum(short) / len(short)) if short else 0.0
            long_avg = (sum(long_) / len(long_)) if long_ else 0.0
            factor = (short_avg / long_avg) if long_avg > 0 else 1.0
            factor = min(max(factor, cfg["factor_min"]), cfg["factor_max"])
            factor_cache[day] = factor
            return factor

        factor = day_factor(today)
        rule_index = {
            (rule["semi_name"], rule["unit"])
            for rules in exact_rules.values()
            for rule in rules
        }
        entries: List[dict] = []
        for item in items:
            key = (item["item_name"], item["unit"])
            has_rule = key in rule_index
            values = [window_qty(day, key) for day in active_candidates]
            if not has_rule:
                demand_qty = 0.0
                status = "missing_rule"
            elif not active_candidates:
                demand_qty = 0.0
                status = "no_history"
            else:
                # 需求分位（p40）：少剩货优先，不加任何安全缓冲
                demand_qty = round(max(0.0, _quantile(values, cfg["quantile"]) * factor), 1)
                status = "ok"

            floor_applied = False
            if status in ("ok", "no_history") and demand_qty < SPARSE_FLOOR_QTY:
                # p40 会把偶发品压成 0；近 7 天真的卖出过就保底 1 个单位
                if any(window_qty(dd, key) > 0 for dd in recent_days):
                    demand_qty = SPARSE_FLOOR_QTY
                    floor_applied = True

            # ADR 0121：不登记产量，也不维护在库量。页面给的就是「今天参考值」，
            # 师傅拿它减掉自己看到的实物库存，决定还要备多少。
            suggested = demand_qty

            hour_values = [
                usage.window(
                    day, key, _epoch(_at(day, clock_t)), _epoch(_at(day, clock_t) + timedelta(hours=1))
                )
                for day in active_candidates
            ]
            next_hour_qty = (
                round((sum(hour_values) / len(hour_values)) * factor, 1) if hour_values else 0.0
            )
            station = item["station"] or POSITION_STATION_MAP.get(item["position"], "")
            entries.append(
                {
                    "prep_item_id": item["prep_item_id"],
                    "item_name": item["item_name"],
                    "unit": item["unit"],
                    "station": station,
                    "position": item["position"],
                    "kind": item["kind"],
                    "suggested_qty": suggested,
                    "next_hour_qty": next_hour_qty,
                    "basis": {
                        "status": status,
                        "weekdays": [
                            {"day": day.isoformat(), "qty": round(value, 1)}
                            for day, value in zip(active_candidates, values)
                        ],
                        "active_days": len(active_candidates),
                        "store_factor": round(factor, 3),
                        "quantile": cfg["quantile"],
                        # 未扣在库的需求量：页面拿它显示「今天要卖多少」
                        "demand_qty": demand_qty,
                        "floor_applied": floor_applied,
                    },
                    "history7": [
                        {
                            "day": day.isoformat(),
                            "qty": round(
                                usage.window(
                                    day, key,
                                    _epoch(_at(day, time(0, 0))),
                                    _epoch(_at(day, time(23, 59, 59))),
                                ),
                                1,
                            ),
                        }
                        for day in (today - timedelta(days=i) for i in range(0, HISTORY_DAYS))
                    ],
                }
            )

        entries.sort(
            key=lambda e: (e["station"], e["kind"], -e["suggested_qty"], e["item_name"])
        )
        used_positions = {item["position"] for item in items if item["position"]}
        positions = [p for p in POSITION_STATION_MAP if p in used_positions]
        positions += sorted(used_positions - set(POSITION_STATION_MAP))

        return {
            "plan_day": today.isoformat(),
            "generated_at": now.isoformat(),
            "positions": positions,
            "window": {
                "start": window_start.isoformat(),
                "end": window_end.isoformat(),
                "closed": closed,
                "after_close": after_close,
                "horizon_hours": FORECAST_HORIZON_HOURS,
            },
            "entries": entries,
            "missing_rule": [
                e["item_name"] for e in entries if e["basis"]["status"] == "missing_rule"
            ],
            "forecast": {
                "quantile": cfg["quantile"],
                "window_weeks": cfg["window_weeks"],
                "store_factor": round(factor, 3),
            },
        }

    # ------------------------------------------------------------------ 当日计划持久化

    async def _ensure_day(self, db, plan_day: str, window: dict) -> int:
        stamp = datetime.now(CHINA_TZ).isoformat()
        tdb = db.table("prep_plan_days")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id FROM prep_plan_days WHERE plan_day = ?", (plan_day,)
            )
            row = await cursor.fetchone()
            if row:
                day_id = int(row["id"])
                await cursor.execute(
                    "UPDATE prep_plan_days SET window_start = ?, window_end = ?, updated_at = ? WHERE id = ?",
                    (window["start"], window["end"], stamp, day_id),
                )
            else:
                await cursor.execute(
                    """
                    INSERT INTO prep_plan_days (plan_day, window_start, window_end, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (plan_day, window["start"], window["end"], stamp, stamp),
                )
                day_id = int(cursor.lastrowid or 0)
        await tdb.commit()
        return day_id

    async def _persist_entries(self, db, day_id: int, entries: List[dict]) -> None:
        """写当日明细。手改过的 `final_qty` 与勾选状态一律保留。"""
        stamp = datetime.now(CHINA_TZ).isoformat()
        tdb = db.table("prep_plan_entries")
        async with tdb.conn.cursor() as cursor:
            for entry in entries:
                await cursor.execute(
                    """
                    INSERT INTO prep_plan_entries (
                        plan_day_id, prep_item_id, item_name, unit, station, kind,
                        suggested_qty, final_qty, qty_source, basis_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'suggested', ?, ?, ?)
                    ON CONFLICT (plan_day_id, prep_item_id) DO UPDATE SET
                        item_name = excluded.item_name,
                        unit = excluded.unit,
                        station = excluded.station,
                        kind = excluded.kind,
                        suggested_qty = excluded.suggested_qty,
                        final_qty = CASE
                            WHEN prep_plan_entries.qty_source = 'manual'
                            THEN prep_plan_entries.final_qty
                            ELSE excluded.final_qty
                        END,
                        basis_json = excluded.basis_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        day_id,
                        entry["prep_item_id"],
                        entry["item_name"],
                        entry["unit"],
                        entry["station"],
                        entry["kind"],
                        entry["suggested_qty"],
                        entry["suggested_qty"],
                        json.dumps(entry["basis"], ensure_ascii=False),
                        stamp,
                        stamp,
                    ),
                )
        await tdb.commit()

    async def _read_entries(self, db, day_id: int) -> List[dict]:
        tdb = db.table("prep_plan_entries")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, prep_item_id, item_name, unit, station, kind,
                       suggested_qty, final_qty, qty_source,
                       done_at, done_by, basis_json
                FROM prep_plan_entries
                WHERE plan_day_id = ?
                ORDER BY station, kind, suggested_qty DESC, item_name
                """,
                (day_id,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "id": int(row["id"]),
                "prep_item_id": int(row["prep_item_id"] or 0),
                "item_name": row["item_name"] or "",
                "unit": row["unit"] or "",
                "station": row["station"] or "",
                "kind": row["kind"] or "made",
                "suggested_qty": float(row["suggested_qty"] or 0),
                "final_qty": float(row["final_qty"] or 0),
                "qty_source": row["qty_source"] or "suggested",
                "done_at": row["done_at"],
                "done_by": row["done_by"] or "",
                "basis": _loads_json(row["basis_json"]),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------ 对外

    async def get_today(
        self,
        db,
        now: Optional[datetime] = None,
        station: Optional[str] = None,
        employee_id: Optional[int] = None,
    ) -> dict:
        now = now or datetime.now(CHINA_TZ)
        computed = await self.compute_suggestions(db, now)
        day_id = await self._ensure_day(db, computed["plan_day"], computed["window"])
        await self._persist_entries(db, day_id, computed["entries"])
        entries = await self._read_entries(db, day_id)

        # 备货计划按**岗位**分组（案板 / 熟笼 / 西饼…），档口只在 POS 侧有意义。
        # `prep_plan_entries` 里只落了 station，岗位从这次算好的 entries 按品补回来。
        source_by_item = {e["prep_item_id"]: e for e in computed["entries"]}
        for entry in entries:
            source = source_by_item.get(entry["prep_item_id"])
            entry["history7"] = source["history7"] if source else []
            entry["position"] = (source["position"] if source else "") or ""

        staff_station: Optional[str] = None
        needs_station_pick = False
        if employee_id is not None:
            staff_station = await self.get_staff_station(db, employee_id, now)
            needs_station_pick = not staff_station
            if staff_station:
                entries = [e for e in entries if e["position"] == staff_station]
            else:
                # 员工还没选岗位：先让他选，不拿全店清单糊他一脸
                entries = []
        elif station and station not in ("", "all"):
            entries = [e for e in entries if e["position"] == station]

        position_rank = {name: index for index, name in enumerate(computed.get("positions") or [])}
        entries.sort(key=lambda e: (
            position_rank.get(e["position"], len(position_rank)),
            "" if e["position"] else "z",
            e["kind"],
            -e["suggested_qty"],
            e["item_name"],
        ))

        remaining = [e for e in entries if not e["done_at"]]
        done = [e for e in entries if e["done_at"]]
        return {
            "success": True,
            "plan_day": computed["plan_day"],
            "generated_at": computed["generated_at"],
            "window": computed["window"],
            "missing_rule": computed["missing_rule"],
            # 岗位清单（不是档口）：员工选岗位、管理端切换分组都用它。
            "stations": [
                {"id": position, "label": position}
                for position in (computed.get("positions") or [])
            ],
            "staff": employee_id is not None,
            "staff_station": staff_station,
            "needs_station_pick": needs_station_pick,
            "remaining_count": len(remaining),
            "done_count": len(done),
            "entries": entries,
        }

    async def update_entry(
        self,
        db,
        entry_id: int,
        final_qty: Optional[float] = None,
        done: Optional[bool] = None,
        done_by: str = "",
        employee_id: Optional[int] = None,
    ) -> dict:
        # ADR 0121：不再登记产量，勾选只是「这项处理过了」的标记。
        tdb = db.table("prep_plan_entries")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id, prep_item_id FROM prep_plan_entries WHERE id = ?", (entry_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("entry_not_found")
            if employee_id is not None:
                staff_position = await self.get_staff_station(db, employee_id)
                position = await self._item_position(db, int(row["prep_item_id"] or 0))
                if not staff_position or position != staff_position:
                    raise PermissionError("station_forbidden")
            stamp = datetime.now(CHINA_TZ).isoformat()
            if final_qty is not None:
                await cursor.execute(
                    """
                    UPDATE prep_plan_entries
                       SET final_qty = ?, qty_source = 'manual', updated_at = ?
                     WHERE id = ?
                    """,
                    (float(final_qty), stamp, entry_id),
                )
            if done is not None:
                await cursor.execute(
                    """
                    UPDATE prep_plan_entries
                       SET done_at = ?, done_by = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (stamp if done else None, done_by if done else "", stamp, entry_id),
                )
        await tdb.commit()
        return {"success": True, "id": entry_id}

    async def get_summary(self, db, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(CHINA_TZ)
        today = await self.get_today(db, now=now)
        buckets: Dict[str, dict] = {}
        for entry in today["entries"]:
            position = entry.get("position") or "uncategorized"
            bucket = buckets.setdefault(
                position,
                {
                    "station": position,
                    "label": "未分类" if position == "uncategorized" else position,
                    "total": 0,
                    "remaining": 0,
                },
            )
            bucket["total"] += 1
            if not entry["done_at"]:
                bucket["remaining"] += 1
        stations = sorted(buckets.values(), key=lambda b: (-b["remaining"], b["station"]))
        return {
            "success": True,
            "plan_day": today["plan_day"],
            "window": today["window"],
            "total": len(today["entries"]),
            "remaining": today["remaining_count"],
            "stations": stations,
        }

    # ------------------------------------------------------------------ 员工档口

    async def _item_position(self, db, prep_item_id: int) -> str:
        """备货品的岗位（员工越权校验用；entries 表里没落岗位）。"""
        tdb = db.table("prep_items")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute("SELECT position FROM prep_items WHERE id = ?", (prep_item_id,))
            row = await cursor.fetchone()
        return (row["position"] if row else "") or ""

    async def get_staff_station(
        self, db, employee_id: int, now: Optional[datetime] = None
    ) -> Optional[str]:
        now = now or datetime.now(CHINA_TZ)
        tdb = db.table("prep_staff_station_picks")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT station FROM prep_staff_station_picks WHERE employee_id = ? AND pick_day = ?",
                (employee_id, now.date().isoformat()),
            )
            row = await cursor.fetchone()
        return (row["station"] if row else None) or None

    async def set_staff_station(
        self, db, employee_id: int, station: str, now: Optional[datetime] = None
    ) -> dict:
        station = (station or "").strip()
        if not station:
            raise ValueError("station_required")
        now = now or datetime.now(CHINA_TZ)
        pick_day = now.date().isoformat()
        tdb = db.table("prep_staff_station_picks")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_staff_station_picks (employee_id, pick_day, station, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (tenant_id, employee_id, pick_day) DO UPDATE SET station = excluded.station
                """,
                (employee_id, pick_day, station, now.isoformat()),
            )
        await tdb.commit()
        return {"success": True, "station": station, "pick_day": pick_day}

    # ------------------------------------------------------------------ 主数据

    async def list_items(self, db) -> List[dict]:
        items = await self._load_items(db)
        return [
            {
                "id": item["prep_item_id"],
                "item_name": item["item_name"],
                "unit": item["unit"],
                "station": item["station"],
                "kind": item["kind"],
                "category": item["category"],
            }
            for item in items
        ]

    async def update_item(
        self, db, item_id: int, kind: Optional[str] = None, station: Optional[str] = None
    ) -> dict:
        sets: List[str] = []
        params: List[Any] = []
        if kind is not None:
            if kind not in ("made", "bought"):
                raise ValueError("invalid_kind")
            sets.append("kind = ?")
            params.append(kind)
        if station is not None:
            sets.append("station = ?")
            params.append(station)
        if not sets:
            raise ValueError("nothing_to_update")
        sets.append("updated_at = ?")
        params.append(datetime.now(CHINA_TZ).isoformat())
        params.append(item_id)
        tdb = db.table("prep_items")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"UPDATE prep_items SET {', '.join(sets)} WHERE id = ?", tuple(params)
            )
        await tdb.commit()
        return {"success": True, "id": item_id}


    # ------------------------------------------------------------------ 快照与复盘（ADR 0123）

    async def _sold_qty_map(self, db, start_dt: datetime, end_dt: datetime) -> Dict[Tuple[str, str], float]:
        """一段时间里「卖出菜品 × 配方系数」的用量，按 (备货品, 单位) 聚合。复盘用。"""
        rows = await self._fetch_orders(db, [("sold", start_dt, end_dt)])
        exact_rules, norm_rules = await self._load_rules(db)
        totals: Dict[Tuple[str, str], float] = defaultdict(float)
        for row in rows:
            if row["quantity"] == 0:
                continue
            dishes = exact_rules.get(row["dish_name"]) or norm_rules.get(
                normalize_dish_name(row["dish_name"]), ()
            )
            for rule in dishes:
                totals[(rule["semi_name"], rule["unit"])] += row["quantity"] * rule["factor"]
        return totals

    async def _rules_fingerprint(self, db) -> str:
        """当日配方表的指纹。配方一改，历史窗口的「实际销量」就与当时的预测不同源。"""
        tdb = db.table("semi_finished_rules")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT dish_name, semi_name, unit, factor FROM semi_finished_rules"
            )
            rows = await cursor.fetchall()
        parts = sorted(
            f"{row['dish_name'] or ''}|{row['semi_name'] or ''}|{row['unit'] or ''}|"
            f"{float(row['factor'] or 1):.6f}"
            for row in rows
        )
        return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()

    async def write_snapshot(self, db, now: Optional[datetime] = None) -> dict:
        """整点写一份参考值快照。同一天同一整点重复调用按 upsert 处理。"""
        now = now or datetime.now(CHINA_TZ)
        stamp = now.replace(minute=0, second=0, microsecond=0)
        computed = await self.compute_suggestions(db, stamp)
        fingerprint = await self._rules_fingerprint(db)
        created = datetime.now(CHINA_TZ).isoformat()
        window = computed["window"]
        tdb = db.table("prep_plan_snapshots")
        written = 0
        async with tdb.conn.cursor() as cursor:
            for entry in computed["entries"]:
                basis = entry.get("basis") or {}
                await cursor.execute(
                    """
                    INSERT INTO prep_plan_snapshots (
                        snapshot_at, window_start, window_end, plan_day,
                        prep_item_id, item_name, unit, station,
                        suggested_qty, demand_qty, store_factor, quantile,
                        basis_json, rules_fingerprint, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (tenant_id, snapshot_at, prep_item_id) DO NOTHING
                    """,
                    (
                        stamp.isoformat(),
                        window["start"],
                        window["end"],
                        computed["plan_day"],
                        entry["prep_item_id"],
                        entry["item_name"],
                        entry["unit"],
                        entry["station"],
                        entry["suggested_qty"],
                        basis.get("demand_qty", entry["suggested_qty"]),
                        basis.get("store_factor", 1.0),
                        basis.get("quantile", FORECAST_QUANTILE),
                        json.dumps(basis, ensure_ascii=False),
                        fingerprint,
                        created,
                    ),
                )
                written += 1
        await tdb.commit()
        return {"success": True, "snapshot_at": stamp.isoformat(), "rows": written}

    async def backfill_actuals(self, db, now: Optional[datetime] = None) -> dict:
        """把「窗口已经结束、实际还缺」的快照窗口补算出来。

        实际销量 = 卖出菜品 × 配方系数，与参考值同源口径；用当前配方算，
        因此把当前指纹一起存下来，复盘页据此判断两边是否同源。
        """
        now = now or datetime.now(CHINA_TZ)
        tdb = db.table("prep_plan_snapshots")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT DISTINCT s.window_start, s.window_end
                FROM prep_plan_snapshots s
                LEFT JOIN prep_plan_actuals a
                       ON a.window_start = s.window_start AND a.window_end = s.window_end
                WHERE a.id IS NULL AND s.window_end <= ?
                ORDER BY s.window_start
                """,
                (now.isoformat(),),
            )
            pending = await cursor.fetchall()
        if not pending:
            return {"success": True, "windows": 0, "rows": 0}

        fingerprint = await self._rules_fingerprint(db)
        computed_at = datetime.now(CHINA_TZ).isoformat()
        items = await self._load_items(db)
        written = 0
        adb = db.table("prep_plan_actuals")
        for row in pending:
            start = datetime.fromisoformat(row["window_start"])
            end = datetime.fromisoformat(row["window_end"])
            sold = await self._sold_qty_map(db, start, end)
            async with adb.conn.cursor() as cursor:
                for item in items:
                    key = (item["item_name"], item["unit"])
                    await cursor.execute(
                        """
                        INSERT INTO prep_plan_actuals (
                            window_start, window_end, prep_item_id, item_name, unit,
                            station, sold_qty, rules_fingerprint, computed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (tenant_id, window_start, window_end, prep_item_id)
                        DO NOTHING
                        """,
                        (
                            row["window_start"],
                            row["window_end"],
                            item["prep_item_id"],
                            item["item_name"],
                            item["unit"],
                            item["station"] or POSITION_STATION_MAP.get(item["position"], ""),
                            round(sold.get(key, 0.0), 2),
                            fingerprint,
                            computed_at,
                        ),
                    )
                    written += 1
            await adb.commit()
        return {"success": True, "windows": len(pending), "rows": written}

    async def get_snapshot_day(self, db, day: str) -> dict:
        """某一天的快照 + 实际，按品聚合。复盘页的唯一数据源。"""
        tdb = db.table("prep_plan_snapshots")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT s.snapshot_at, s.window_start, s.prep_item_id, s.item_name,
                       s.unit, s.station, s.suggested_qty, s.final_qty, s.store_factor,
                       s.rules_fingerprint, s.snapshot_at,
                       a.sold_qty, a.rules_fingerprint AS actual_fingerprint
                FROM prep_plan_snapshots s
                LEFT JOIN prep_plan_actuals a
                       ON a.window_start = s.window_start
                      AND a.window_end = s.window_end
                      AND a.prep_item_id = s.prep_item_id
                WHERE s.plan_day = ?
                ORDER BY s.item_name, s.snapshot_at
                """,
                (day,),
            )
            rows = await cursor.fetchall()

        by_item: Dict[Tuple[str, str], dict] = {}
        for row in rows:
            key = (row["item_name"] or "", row["unit"] or "")
            bucket = by_item.setdefault(
                key,
                {
                    "item_name": key[0],
                    "unit": key[1],
                    "station": row["station"] or "",
                    "hours": [],
                },
            )
            same_rules = (row["rules_fingerprint"] or "") == (row["actual_fingerprint"] or "")
            bucket["hours"].append(
                {
                    "snapshot_at": row["snapshot_at"],
                    "suggested_qty": float(row["suggested_qty"] or 0),
                    "final_qty": row["final_qty"],
                    "sold_qty": None if row["sold_qty"] is None else float(row["sold_qty"]),
                    "store_factor": float(row["store_factor"] or 1),
                    "same_rules": same_rules if row["sold_qty"] is not None else None,
                }
            )
        items = sorted(by_item.values(), key=lambda b: (-len(b["hours"]), b["item_name"]))
        return {"success": True, "day": day, "items": items, "rows": len(rows)}


prep_plan_service = PrepPlanService()
