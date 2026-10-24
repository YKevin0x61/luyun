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

# 报损复用既有的损耗台账（ADR 0118 决策 6）：不另写一套报废账。
from services.wastage.store import WastageActor, WastageStore

logger = logging.getLogger(__name__)

SAME_WEEKDAY_LOOKBACK = 4
HISTORY_DAYS = 7
DEFAULT_OPEN = "07:00"
DEFAULT_CLOSE = "21:30"
RUNTIME_SETTINGS_KEY = "prep_plan_runtime"
FORECAST_SETTINGS_KEY = "prep_plan_forecast"
FALLBACK_RUNTIME_SETTINGS_KEY = "scraper_runtime"
LOUMIAN_STATION = "loumian"

# 预测口径（默认值 = 真实数据回测选出来的点，见 .scratch/prep-plan-backtest/）
FORECAST_ALPHA = 0.10          # 残差分位：覆盖率目标 1-α；0.10 实测 93.2% @ 1.76x 备货量
FORECAST_RESIDUAL_DAYS = 28    # 残差样本窗口（天）
STORE_FACTOR_SHORT_DAYS = 7    # 整店水位：近 7 天 …
STORE_FACTOR_LONG_DAYS = 21    # … 对前 21 天
STORE_FACTOR_MIN = 0.8
STORE_FACTOR_MAX = 1.3
# 残差样本少于这个数时 conformal 分位退化（n 很小时它等于取最大值），改用比例兜底。
# 定 7 而不是 14：实测 14 会把评估期早期的历史缺口日切成冷启动，覆盖率白掉 2.7 个点
# （07:00 92.8% → 90.1%）。样本少时宁可多备，也不要用更小的缓冲。
MIN_RESIDUAL_DAYS = 7
COLD_START_BUFFER_RATIO = 0.5
# 缓冲上限：**实测不该设**。压到 2× 基准量覆盖率掉 3.4 个点（07:00 92.8% → 89.4%），
# 压到 5× 也掉 2.8 个点、19:00 掉 5.5 个点——尖峰品的缓冲本来就该比基准量大一个量级，
# 上限只会让这些品重新变成「经常不够卖」。要限流请改 alpha，不要用上限。
BUFFER_CAP_RATIO = 0.0

# 配方岗位 → 档口（沿用旧口径；半成品规则里 position 用的就是这一套名字）
POSITION_STATION_MAP = {
    "熟笼": "shulong",
    "熟笼冻品": "shulong",
    "馅档": "shulong",
    "案板": "shulong",
    "西饼": "xibing",
    "西饼凉菜": "xibing",
    "肠粉": "changfen",
    "明档": "mingdang1",
    "明档1": "mingdang1",
    "煎炸": "jianzha",
}


def _conformal_quantile(residuals: List[float], alpha: float) -> float:
    """conformal 的次序统计量：取第 ceil((n+1)(1-α)) 个最小残差。

    有限样本下这条保证「真实值 <= 预测值」的比例不低于 1-α，比直接用经验分位可靠。
    残差为负说明均量本来就估高了，不给负缓冲（不能靠少备来凑覆盖率）。
    """
    if not residuals:
        return 0.0
    n = len(residuals)
    rank = min(max(int(math.ceil((n + 1) * (1 - alpha))), 1), n)
    value = sorted(residuals)[rank - 1]
    return max(0.0, float(value))


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
    # 建议量 = 整店水位缩放后的「近 4 周同星期几、同钟点窗口」均量
    #        + 该品自己的残差 conformal 分位（近 28 天，默认 α=0.10）
    #
    # 为什么加「残差」而不是直接对需求量取高分位：需求是尖峰分布，直接取 9 折分位会把
    # 本来稳定的品一起抬高；按「这个品历史上均量估偏了多少」加缓冲，稳定的加一点、
    # 尖峰的加很多，同样的覆盖率备货量少得多（证据见 .scratch/prep-plan-backtest/）。
    # conformal 的次序统计量保证有限样本下覆盖率 >= 1-α，不是拍一个系数。

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
        alpha = float(cfg.get("alpha") or FORECAST_ALPHA)
        alpha = min(max(alpha, 0.01), 0.99)
        residual_days = int(cfg.get("residual_days") or FORECAST_RESIDUAL_DAYS)
        residual_days = min(max(residual_days, 7), 56)
        return {
            "alpha": alpha,
            "residual_days": residual_days,
            # 残差窗口 28 天 + 每个残差日还要回看 4 周 → 至少 56 天历史
            "lookback_days": residual_days + 28,
            "factor_min": float(cfg.get("factor_min") or STORE_FACTOR_MIN),
            "factor_max": float(cfg.get("factor_max") or STORE_FACTOR_MAX),
        }

    async def compute_suggestions(self, db, now: datetime) -> dict:
        """算出今天这份清单的建议量。返回纯计算结果，不落库。"""
        open_t, close_t = await self._runtime(db)
        cfg = await self._forecast_config(db)
        today = now.date()
        window_start = max(now, _at(today, open_t))
        window_end = _at(today, close_t)
        closed = now >= window_end
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
            today - timedelta(days=7 * i) for i in range(1, SAME_WEEKDAY_LOOKBACK + 1)
        ]
        active_candidates = [day for day in today_candidates if day in days_with_orders]
        residual_days = [
            today - timedelta(days=i)
            for i in range(1, cfg["residual_days"] + 1)
            if (today - timedelta(days=i)) in days_with_orders
        ]

        def window_qty(day, key):
            return usage.window(day, key, _epoch(_at(day, clock_t)), _epoch(_at(day, close_t)))

        def same_weekday_base(day, key):
            """某一天在同样钟点窗口的「4 周同星期几」均量，不含任何缓冲。"""
            vals = [
                window_qty(c, key)
                for c in (day - timedelta(days=7 * i) for i in range(1, SAME_WEEKDAY_LOOKBACK + 1))
                if c in days_with_orders
            ]
            return (sum(vals) / len(vals)) if vals else 0.0

        def store_volume(day):
            if day not in days_with_orders:
                return None
            return usage.raw_window(day, _epoch(_at(day, clock_t)), _epoch(_at(day, close_t)))

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
                base = 0.0
                residuals: List[float] = []
                status = "missing_rule"
            elif not active_candidates:
                base = 0.0
                residuals = []
                status = "no_history"
            else:
                base = sum(values) / len(values)
                residuals = []
                for dd in residual_days:
                    # 基线为 0 的日子（同星期几那几天没数据 / 店主歇业）也要算：
                    # 那正是「没有可比日时公式会估成 0」这个失败模式的实测记录，
                    # 把它剔掉等于假装断档不会发生，覆盖率会掉回去（实测 92.8% → 88.3%）。
                    base_dd = same_weekday_base(dd, key)
                    residuals.append(window_qty(dd, key) - base_dd * day_factor(dd))
                status = "ok"

            if len(residuals) >= MIN_RESIDUAL_DAYS:
                buffer_qty = _conformal_quantile(residuals, cfg["alpha"])
                buffer_source = "conformal"
                if BUFFER_CAP_RATIO > 0 and base > 0 and buffer_qty > base * BUFFER_CAP_RATIO:
                    buffer_qty = base * BUFFER_CAP_RATIO
                    buffer_source = "capped"
            else:
                # 新店 / 刚上线：还没有足够残差，先按比例给保守裕度，别让首月裸奔
                buffer_qty = base * COLD_START_BUFFER_RATIO
                buffer_source = "cold_start"
            suggested = round(max(0.0, base * factor + buffer_qty), 1)

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
                        "buffer_qty": round(buffer_qty, 1),
                        "buffer_source": buffer_source,
                        "residual_days": len(residuals),
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
        return {
            "plan_day": today.isoformat(),
            "generated_at": now.isoformat(),
            "window": {
                "start": window_start.isoformat(),
                "end": window_end.isoformat(),
                "closed": closed,
            },
            "entries": entries,
            "missing_rule": [
                e["item_name"] for e in entries if e["basis"]["status"] == "missing_rule"
            ],
            "forecast": {
                "alpha": cfg["alpha"],
                "residual_days": cfg["residual_days"],
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
                       suggested_qty, final_qty, qty_source, produced_qty,
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
                "produced_qty": float(row["produced_qty"] or 0),
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

        staff_station: Optional[str] = None
        needs_station_pick = False
        if employee_id is not None:
            staff_station = await self.get_staff_station(db, employee_id, now)
            needs_station_pick = not staff_station
            if staff_station:
                entries = [e for e in entries if e["station"] == staff_station]
            else:
                # 员工还没选档口：先让他选，不拿全店清单糊他一脸
                entries = []
        elif station and station not in ("", "all"):
            entries = [e for e in entries if e["station"] == station]

        source_by_item = {e["prep_item_id"]: e for e in computed["entries"]}
        for entry in entries:
            source = source_by_item.get(entry["prep_item_id"])
            entry["history7"] = source["history7"] if source else []

        remaining = [e for e in entries if not e["done_at"]]
        done = [e for e in entries if e["done_at"]]
        return {
            "success": True,
            "plan_day": computed["plan_day"],
            "generated_at": computed["generated_at"],
            "window": computed["window"],
            "missing_rule": computed["missing_rule"],
            # 全部后厨档口（不是「有品的那些」）：员工选档口时要能选到还没配备货品的档。
            "stations": [
                {"id": station_id, "label": config.get("name") or station_id}
                for station_id, config in KITCHEN_STATIONS.items()
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
        produced_qty: Optional[float] = None,
        done_by: str = "",
        employee_id: Optional[int] = None,
    ) -> dict:
        # v2（ADR 0118）：勾必须带「这次做了多少」。只勾不填数等于 v1，在库量就无从算起。
        if done and produced_qty is None:
            raise ValueError("produced_qty_required")
        tdb = db.table("prep_plan_entries")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id, station FROM prep_plan_entries WHERE id = ?", (entry_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("entry_not_found")
            if employee_id is not None:
                staff_station = await self.get_staff_station(db, employee_id)
                if not staff_station or row["station"] != staff_station:
                    raise PermissionError("station_forbidden")
            stamp = datetime.now(CHINA_TZ).isoformat()
            if produced_qty is not None:
                await cursor.execute(
                    """
                    UPDATE prep_plan_entries
                       SET produced_qty = produced_qty + ?,
                           produced_by = ?,
                           produced_at = ?,
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (float(produced_qty), done_by, stamp, stamp, entry_id),
                )
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
            station = entry["station"] or "uncategorized"
            bucket = buckets.setdefault(
                station, {"station": station, "total": 0, "remaining": 0}
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


    # ------------------------------------------------------------------ v2：产量、在库量、补货、复盘

    async def _production_map(self, db, plan_day: str) -> Dict[int, dict]:
        """某一天每个品的建议量与产量（按 prep_item_id）。"""
        tdb = db.table("prep_plan_entries")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT e.prep_item_id, e.item_name, e.unit, e.suggested_qty, e.produced_qty
                FROM prep_plan_entries e
                JOIN prep_plan_days d ON d.id = e.plan_day_id
                WHERE d.plan_day = ?
                """,
                (plan_day,),
            )
            rows = await cursor.fetchall()
        out: Dict[int, dict] = {}
        for row in rows:
            item_id = int(row["prep_item_id"] or 0)
            if not item_id:
                continue
            out[item_id] = {
                "item_name": row["item_name"] or "",
                "unit": row["unit"] or "",
                "suggested_qty": float(row["suggested_qty"] or 0),
                "produced_qty": float(row["produced_qty"] or 0),
            }
        return out

    async def _consumed_map(
        self, db, start_dt: datetime, end_dt: datetime
    ) -> Dict[Tuple[str, str], float]:
        """一段时间里「卖出菜品 × 配方系数」的用量，按 (备货品, 单位) 聚合。"""
        rows = await self._fetch_orders(db, [("consumed", start_dt, end_dt)])
        exact_rules, norm_rules = await self._load_rules(db)
        totals: Dict[Tuple[str, str], float] = defaultdict(float)
        for row in rows:
            if row["quantity"] == 0:
                continue
            dishes = exact_rules.get(row["dish_name"]) or norm_rules.get(
                normalize_dish_name(row["dish_name"]), []
            )
            for rule in dishes:
                totals[(rule["semi_name"], rule["unit"])] += row["quantity"] * rule["factor"]
        return totals

    async def _waste_map(self, db, day: str) -> Dict[str, float]:
        """某一天按备货品名汇总的报损量（来自既有损耗台账）。"""
        try:
            tdb = db.table("wastage_records")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT item_name, SUM(quantity) AS total FROM wastage_records"
                    " WHERE wastage_date = ? GROUP BY item_name",
                    (day,),
                )
                rows = await cursor.fetchall()
        except Exception:  # 台账表缺失时按「没有报损」处理，不让备货页整页挂掉
            logger.warning("报损表读不到，按 0 处理（迁移 0029 是否已应用？）")
            return {}
        return {row["item_name"]: float(row["total"] or 0) for row in rows}

    async def _on_hand_map(self, db, now: datetime) -> Dict[int, float]:
        """在库量 = 今天做的 + 昨天剩的 − 今天卖的 − 今天报损。

        只结转昨天（ADR 0118）：再早的不算，过期不自动扣，靠报损体现。
        """
        today = now.date()
        yesterday = today - timedelta(days=1)
        today_items = await self._production_map(db, today.isoformat())
        yday_items = await self._production_map(db, yesterday.isoformat())
        sold_today = await self._consumed_map(db, _at(today, time(0, 0)), now)
        sold_yday = await self._consumed_map(
            db, _at(yesterday, time(0, 0)), _at(yesterday, time(23, 59, 59))
        )
        waste_today = await self._waste_map(db, today.isoformat())
        waste_yday = await self._waste_map(db, yesterday.isoformat())

        items = await self._load_items(db)
        on_hand: Dict[int, float] = {}
        for item in items:
            item_id = item["prep_item_id"]
            key = (item["item_name"], item["unit"])
            produced_today = (today_items.get(item_id) or {}).get("produced_qty", 0.0)
            produced_yday = (yday_items.get(item_id) or {}).get("produced_qty", 0.0)
            left_yday = max(
                0.0,
                produced_yday - sold_yday.get(key, 0.0) - waste_yday.get(item["item_name"], 0.0),
            )
            value = (
                produced_today
                + left_yday
                - sold_today.get(key, 0.0)
                - waste_today.get(item["item_name"], 0.0)
            )
            on_hand[item_id] = round(max(0.0, value), 1)
        return on_hand

    async def get_restock(
        self,
        db,
        now: Optional[datetime] = None,
        employee_id: Optional[int] = None,
        station: Optional[str] = None,
    ) -> dict:
        """补货 tab：在库量不够再卖 1 小时的品，以及「补到打烊」要做的量。"""
        now = now or datetime.now(CHINA_TZ)
        # 补货要能直接勾：先把今天的计划落库，拿到每条明细的 id
        today = await self.get_today(db, now=now)
        entry_ids = {row["prep_item_id"]: row["id"] for row in today["entries"]}
        computed = await self.compute_suggestions(db, now)
        on_hand = await self._on_hand_map(db, now)

        staff_station = await self.get_staff_station(db, employee_id, now) if employee_id else None
        if employee_id is not None and not staff_station:
            return {
                "success": True,
                "closed": computed["window"]["closed"],
                "rows": [],
                "needs_station_pick": True,
                "staff_station": None,
                "produced_today": 0,
            }

        rows: List[dict] = []
        for entry in computed["entries"]:
            hour = float(entry.get("next_hour_qty") or 0)
            hand = float(on_hand.get(entry["prep_item_id"], 0.0))
            if computed["window"]["closed"] or hour <= 0 or hand >= hour:
                continue
            if staff_station and entry["station"] != staff_station:
                continue
            if station and station not in ("", "all") and entry["station"] != station:
                continue
            cover_minutes = int(round(hand / hour * 60)) if hour > 0 else 0
            rows.append(
                {
                    "entry_id": entry_ids.get(entry["prep_item_id"]),
                    "prep_item_id": entry["prep_item_id"],
                    "item_name": entry["item_name"],
                    "unit": entry["unit"],
                    "station": entry["station"],
                    "kind": entry["kind"],
                    "on_hand": round(hand, 1),
                    "next_hour_qty": round(hour, 1),
                    "cover_minutes": max(0, min(cover_minutes, 60)),
                    "restock_qty": round(max(0.0, entry["suggested_qty"] - hand), 1),
                    "suggested_qty": entry["suggested_qty"],
                }
            )
        rows.sort(key=lambda r: (r["cover_minutes"], -r["restock_qty"]))
        produced_today = sum(
            row["produced_qty"]
            for row in (await self._production_map(db, now.date().isoformat())).values()
        )
        return {
            "success": True,
            "closed": computed["window"]["closed"],
            "rows": rows,
            "needs_station_pick": False,
            "staff_station": staff_station,
            "produced_today": round(produced_today, 1),
        }

    async def get_review(
        self, db, day: Optional[str] = None, now: Optional[datetime] = None
    ) -> dict:
        """复盘：看昨天（或指定日）每个品的建议 / 实际做 / 卖出 / 剩 / 报废。"""
        now = now or datetime.now(CHINA_TZ)
        target = day or (now.date() - timedelta(days=1)).isoformat()
        entries = await self._production_map(db, target)
        sold = await self._consumed_map(
            db,
            datetime.fromisoformat(f"{target}T00:00:00").replace(tzinfo=CHINA_TZ),
            datetime.fromisoformat(f"{target}T23:59:59").replace(tzinfo=CHINA_TZ),
        )
        waste = await self._waste_map(db, target)

        rows: List[dict] = []
        over = under = 0
        waste_total = 0.0
        for item in await self._load_items(db):
            item_id = item["prep_item_id"]
            record = entries.get(item_id)
            if record is None:
                continue
            key = (item["item_name"], item["unit"])
            sold_qty = round(sold.get(key, 0.0), 1)
            waste_qty = round(waste.get(item["item_name"], 0.0), 1)
            produced = record["produced_qty"]
            left = round(max(0.0, produced - sold_qty - waste_qty), 1)
            delta = round(produced - record["suggested_qty"], 1)
            if delta >= max(1.0, record["suggested_qty"] * 0.2):
                over += 1
            elif delta <= -max(1.0, record["suggested_qty"] * 0.2):
                under += 1
            waste_total += waste_qty
            rows.append(
                {
                    "prep_item_id": item_id,
                    "item_name": item["item_name"],
                    "unit": item["unit"],
                    "station": item["station"],
                    "suggested_qty": record["suggested_qty"],
                    "produced_qty": produced,
                    "sold_qty": sold_qty,
                    "left_qty": left,
                    "waste_qty": waste_qty,
                    "delta": delta,
                }
            )
        rows.sort(key=lambda r: -abs(r["delta"]))
        return {
            "success": True,
            "day": target,
            "rows": rows,
            "summary": {
                "over": over,
                "under": under,
                "waste_total": round(waste_total, 1),
            },
        }

    async def add_waste(
        self,
        db,
        prep_item_id: int,
        quantity: float,
        reason: str,
        remark: str = "",
        employee_id: Optional[int] = None,
        employee_name: str = "",
        now: Optional[datetime] = None,
    ) -> dict:
        """备货页的「报废」：写进既有损耗台账，物料名就是备货品名。"""
        now = now or datetime.now(CHINA_TZ)
        items = await self._load_items(db)
        item = next((row for row in items if row["prep_item_id"] == int(prep_item_id)), None)
        if item is None:
            raise ValueError("item_not_found")
        if employee_id is not None:
            staff_station = await self.get_staff_station(db, employee_id, now)
            if not staff_station or item["station"] != staff_station:
                raise PermissionError("station_forbidden")
        actor = WastageActor(
            kind="staff" if employee_id is not None else "admin",
            employee_id=employee_id,
            name=employee_name,
        )
        store = WastageStore(db)
        record = await store.add_record(
            actor,
            item_name=item["item_name"],
            quantity=quantity,
            unit=item["unit"],
            reason=reason,
            remark=remark,
            wastage_date=now.date().isoformat(),
        )
        return {"success": True, "record": record}


prep_plan_service = PrepPlanService()
