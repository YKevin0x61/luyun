#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参考值（Reference Qty）计算（ADR 0137）。

纯函数模块：输入是已经读好的订单行 / 配方 / 备货品 / 参数 / 时钟，输出页面与落库
共用的那份 dict。这里不读 DB、不读系统时钟、不看环境——同一份输入永远得到同一个结果。

口径（`GLOSSARY.md`「参考值 (Reference Qty)」）：

    基准量 = 近 8 周同星期几、在同样钟点窗口内的实际用量分位
    实际用量 = 卖出菜品数量 × 配方系数（semi_finished_rules）
    分位   = 主力品（占全部配料订单行 ≥ 2%）取 p70，其余取 p50
    建议量 = 基准量 × 整店水位系数

整店水位 = 近 7 天 / 前 21 天的点单量比（限幅，只用 now 之前的数据）。**不加安全
缓冲、也不扣库存**：24 小时保质下多做出来的就是报废。近 7 天卖出过的品保底 1 个
单位；候选日全是 0 但近 14 天有量时退回两周均值。跨度超过 24 小时按「每 24 小时
一段」分别算再相加，24 小时那档与回测口径逐字一致。

调用方 `services/prep_plan_service.py` 负责读设置、查订单、装配配方，以及把结果落库。
"""

import math
from bisect import bisect_left
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from database import CHINA_TZ
from services.dish_normalize import normalize_dish_name
from services.prep_item_identity import Key, keys_for, rule_key, rule_usage

# 展开那一栏的用量柱形图：今天 + 前面 7 个完整营业日。今天那根只算到此刻，
# 要和前 7 个整天比趋势就得给 8 根，否则一天是半天、其余是整天，看着像生意掉了。
HISTORY_DAYS = 8

# 预测口径（ADR 0128：分品分档取需求分位，不加任何安全缓冲）
#
# 2026-11-04 用 8 天真实快照复盘（548 品-天、07:00）：p40 把缺货推到 20.2%、单边多出只有
# 4.2%——缺货是多出的 4.8 倍，已经从「多做 76.7%」弹到另一头。改成分品分档：
#   · 主力品（占全部配料订单行 ≥ 2%）取 p70：缺货 13.5% / 多出 9.1%（实测反事实）
#   · 其余取 p50：p50 整体缺货 16.4% / 多出 6.0%
# 两个分位都仍低于「均量」口径的多出水平，所以没有回到多做的那一头。
FORECAST_QUANTILE = 0.50          # 非主力品：需求分位
FORECAST_QUANTILE_MAJOR = 0.70    # 主力品：需求分位
MAJOR_ITEM_LINE_SHARE = 0.02      # 主力品判据：占全部配料订单行 ≥ 2%
FORECAST_WINDOW_WEEKS = 8         # 同星期几取近 8 周（分位在 4 个样本上估不稳）
STORE_FACTOR_SHORT_DAYS = 7    # 整店水位：近 7 天 …
STORE_FACTOR_LONG_DAYS = 21    # … 对前 21 天
# 水位限幅 0.75–1.30：趋势修正是真需求，但更宽的修正对「少剩货」太激进
STORE_FACTOR_MIN = 0.75
STORE_FACTOR_MAX = 1.30
# 参考值的时间跨度：现在起的未来 24 小时（与保质期对齐，见 ADR 0122）
FORECAST_HORIZON_HOURS = 24
# 页面可选的时间跨度（小时）。24 是回测口径，其余是同一套算法的自然延伸：
# 窗口拉长到整段，窗口里每个自然日都要算进去（周末备货会看 48 / 72 小时或整周）。
FORECAST_HORIZONS = (24, 48, 72, 168)
# 偶发品下限：近 7 天卖出过的品，建议量至少 1 个单位，否则分位会让长尾品静默消失
SPARSE_FLOOR_DAYS = 7
SPARSE_FLOOR_QTY = 1.0
# 候选日全为 0、但近 14 天同时段其实有量时，退回 14 天均值 × 水位。
# 覆盖「新品只有最近两周有量」「候选日恰好都撞上采集断档」这一类——不给 0 让人误以为不用做。
STALE_FALLBACK_DAYS = 14

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


def forecast_config(raw: Optional[dict]) -> dict:
    """预测参数。没配就用回测定下来的默认值。

    `raw` 是设置表里那一条 JSON（读不到就是 `None`）；DB 读取与异常兜底留在
    service，这里只做默认值、夹紧与 `lookback_days` 派生。
    """
    cfg = raw if isinstance(raw, dict) else {}
    quantile = float(cfg.get("quantile", FORECAST_QUANTILE))
    quantile = min(max(quantile, 0.05), 0.95)
    quantile_major = float(cfg.get("quantile_major", FORECAST_QUANTILE_MAJOR))
    quantile_major = min(max(quantile_major, 0.05), 0.95)
    major_share = float(cfg.get("major_share", MAJOR_ITEM_LINE_SHARE))
    major_share = min(max(major_share, 0.0), 1.0)
    weeks = int(cfg.get("window_weeks") or FORECAST_WINDOW_WEEKS)
    weeks = min(max(weeks, 4), 12)
    return {
        "quantile": quantile,
        # 主力品分位；把 major_share 配成 0 就等于全品统一用 quantile
        "quantile_major": quantile_major,
        "major_share": major_share,
        "window_weeks": weeks,
        # 水位要回看 28 天（7+21），同星期几要回看 weeks*7 天，取大者再加 1 天余量
        "lookback_days": max(weeks * 7, STORE_FACTOR_SHORT_DAYS + STORE_FACTOR_LONG_DAYS) + 1,
        "factor_min": float(cfg.get("factor_min", STORE_FACTOR_MIN)),
        "factor_max": float(cfg.get("factor_max", STORE_FACTOR_MAX)),
    }


def _quantile(values: List[float], q: float) -> float:
    """经验分位（线性插值）。

    ADR 0128 起按品分档取分位（主力品 p70 / 其余 p50），不加任何安全缓冲。
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


def resolve_horizon_hours(value) -> int:
    """把页面传来的时间跨度收成 `FORECAST_HORIZONS` 里的一个；非法值抛 ValueError。

    只认整数小时且必须在白名单里：窗口长度决定「同星期几的哪一段」与整店水位怎么取，
    随手传个 30 小时会让水位参考日错半格，宁可直接 400 让调用方改。
    """
    if value is None or value == "":
        return FORECAST_HORIZON_HOURS
    try:
        hours = int(value)
    except (TypeError, ValueError):
        raise ValueError("时间跨度只支持 24 / 48 / 72 / 168 小时") from None
    if hours not in FORECAST_HORIZONS:
        raise ValueError("时间跨度只支持 24 / 48 / 72 / 168 小时")
    return hours


def fit_qty(qty: float, kind: str) -> float:
    """按备货品形态把数量收口：半成品只能整份做，原料可以论斤论包。

    半成品向上取整而不是四舍五入 —— 差 0.2 份也得下这一份的工，向下取整等于让人少做一份；
    多一分的代价是几份报废，少一份的代价是一道菜停售。
    原料保留 1 位小数：0.5 斤是真的可以称出来的。
    """
    value = float(qty or 0.0)
    if kind == "made":
        # 1e-9 挡住浮点误差：2.0000000000000004 不该被顶成 3
        return float(math.ceil(value - 1e-9))
    return round(value, 1)


class _UsageIndex:
    """`(日, 备货品) -> [(时间戳, 用量)]` 的有序索引，负责按时间窗口求和。

    一次查询把整个回看窗口的订单都折进来，之后各个口径的窗口求和都是二分，
    不再为每个窗口单独跑 SQL（否则 28 天残差 × 4 个候选日会把页面拖死）。
    """

    def __init__(self) -> None:
        self._by_key: Dict[Tuple[date, Key], List[Tuple[int, float]]] = defaultdict(list)
        self._raw: Dict[date, List[Tuple[int, float]]] = defaultdict(list)
        self._idx: Dict[Tuple[date, Key], List[int]] = {}
        self._raw_idx: Dict[date, List[int]] = {}

    def add(self, day: date, key: Key, epoch: int, qty: float) -> None:
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

    def nonzero_keys(self) -> set:
        """回看期内真正算出过正量的备货品键。

        用来识别「配方条目在、菜也在卖，但系数被存成 0 / 明细被清空」——这种品会静默
        算成 0，2026-10-27 虾饺就这样参考值 0 而实卖 143 个。
        """
        return {
            key
            for (_day, key), rows in self._by_key.items()
            if any(row[1] > 0 for row in rows)
        }

    @staticmethod
    def _sum(rows, idx, start: int, end: int) -> float:
        """`[start, end)` —— 右端是**开**区间。

        24 小时滚动窗口的结束时刻正好落在一个整点上，右端若取闭区间，那条「次日同钟点」
        的订单会被算进前一天的窗口（实测：08:00 与 09:00 两条快照，09:00 那条的候选量
        凭空多出一天的量）。用 bisect_left 扣掉等于 end 的那些。
        """
        lo = bisect_left(idx, start)
        hi = bisect_left(idx, end)
        if lo >= hi:
            return 0.0
        return sum(row[1] for row in rows[lo:hi])

    def window(self, day: date, key: Key, start: int, end: int) -> float:
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


def _at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=CHINA_TZ)


def _epoch(moment: datetime) -> int:
    return int(moment.timestamp())


def order_datetime(raw: str) -> Optional[datetime]:
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


def compute_reference(
    now: datetime,
    horizon_hours: Optional[int],
    cfg: dict,
    orders: List[dict],
    exact_rules: Dict[str, List[dict]],
    norm_rules: Dict[str, List[dict]],
    items: List[dict],
    *,
    close_time: time,
) -> dict:
    """算出这份清单的参考值。返回纯计算结果，不落库、不读系统时钟。

    `now` 必须带时区；`orders` 是订单行（`dish_name` / `quantity` / `order_time`
    三个键，与 `_fetch_range` 的返回同形）；`exact_rules` / `norm_rules` 是
    `_load_rules` 的返回；`items` 是 `_load_items` 的返回；`close_time` 只用来判
    「现在是否已过打烊」（`window.after_close`），窗口本身仍从 `now` 起算。

    `horizon_hours` 是参考值覆盖的时间跨度（24 / 48 / 72 / 168，默认 24）：
    窗口一律从 `now` 起算，不截到打烊、也不按营业日取整。
    """
    horizon = resolve_horizon_hours(horizon_hours)
    # 参考值按 24 小时切段：48 小时 = 2 段、7 天 = 7 段，各段自己算完再相加。
    # 24 小时就是 1 段——那是回测过的口径（ADR 0122），多段的每一段都用同一套算法，
    # 所以「7 天」是 7 个 24 小时预测之和，不是拿 7 天的总量再取一次分位。
    slice_count = max(1, math.ceil(horizon / 24))
    today = now.date()
    # ADR 0122：参考值覆盖「现在起的未来 N 小时」，与 24 小时保质对齐。
    # 这样下午打开页面不会因为「到打烊只剩 7 小时」而看着数字腰斩。
    window_start = now
    window_end = now + timedelta(hours=horizon)
    after_close = now.time() >= close_time
    closed = False  # 滚动窗口下不存在「今天已打烊就没数」这回事
    clock_t = window_start.time()

    usage = _UsageIndex()
    # 主力品判据用「订单行数」：跨单位可比（个/条/份/斤/包 不能直接相加），
    # 含义也直白——有多少次被点到。
    line_counts: Dict[Key, int] = {}
    dish_lines: Dict[str, int] = {}
    for row in orders:
        parsed = order_datetime(row["order_time"])
        if parsed is None:
            continue
        day = parsed.date()
        epoch = int(parsed.timestamp())
        qty = row["quantity"]
        usage.add_raw(day, epoch, qty)
        if qty == 0:
            continue
        dish_lines[row["dish_name"]] = dish_lines.get(row["dish_name"], 0) + 1
        dishes = exact_rules.get(row["dish_name"]) or norm_rules.get(
            normalize_dish_name(row["dish_name"]), ()
        )
        for key, amount in rule_usage(dishes, qty):
            line_counts[key] = line_counts.get(key, 0) + 1
            usage.add(day, key, epoch, amount)
    usage.finalize()
    total_lines = sum(line_counts.values()) or 1
    mapped_keys = usage.nonzero_keys()

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

    def _span_days_from(start: datetime, hours: int) -> List[date]:
        """`[start, start + hours)` 盖到的每个自然日（右开：正好落在零点的算前一天）。"""
        last = (start + timedelta(hours=hours) - timedelta(microseconds=1)).date()
        days: List[date] = []
        cursor = start.date()
        while cursor <= last:
            days.append(cursor)
            cursor += timedelta(days=1)
        return days

    def _day_span(day):
        """day 那天同钟点起算的 24 小时：[start, end)。这就是「一段」。"""
        start = _at(day, clock_t)
        return start, start + timedelta(hours=24)

    def _window_span(day):
        """从 day 的同钟点起算、往后 horizon 小时：[start, end)。"""
        start = _at(day, clock_t)
        return start, start + timedelta(hours=horizon)

    def bucket_qty(day, keys):
        """day 那天那 24 小时里，这一品能做多少（id 桶 + 名字桶）。"""
        start, end = _day_span(day)
        lo, hi = _epoch(start), _epoch(end)
        return sum(
            usage.window(d, key, lo, hi)
            for key in keys
            for d in _span_days_from(start, 24)
        )

    def window_qty(day, keys):
        """day 起整个窗口的量：同星期几柱形图上那一根（多段时是各段之和）。"""
        start, end = _window_span(day)
        lo, hi = _epoch(start), _epoch(end)
        return sum(
            usage.window(d, key, lo, hi)
            for key in keys
            for d in _span_days_from(start, horizon)
        )

    def hour_qty(day, keys):
        """day 的同钟点起 1 小时里，这一品能做多少（页面「接下来一小时」）。"""
        start = _at(day, clock_t)
        lo, hi = _epoch(start), _epoch(start + timedelta(hours=1))
        return sum(usage.window(day, key, lo, hi) for key in keys)

    def day_total_qty(day, keys):
        """day 自然日全天的量（8 天历史柱形图）。"""
        lo = _epoch(_at(day, time(0, 0)))
        hi = _epoch(_at(day, time(23, 59, 59)))
        return sum(usage.window(day, key, lo, hi) for key in keys)

    def bucket_volume(day):
        """整店水位用的量：day 那 24 小时全店卖了多少。"""
        if day not in days_with_orders:
            return None
        start, end = _day_span(day)
        return sum(
            usage.raw_window(d, _epoch(start), _epoch(end))
            for d in _span_days_from(start, 24)
        )

    factor_cache: Dict[date, float] = {}

    def day_factor(day) -> float:
        """整店水位：近 7 天 / 前 21 天。只用 day 之前的数据，不泄漏。

        水位一律按等长的 **24 小时**窗口比，与参考值跨几天无关：多段窗口里后面几段
        的最近 7 天窗口还跑在 now 之后（订单没发生），把它们当完整窗口平均进来会把
        水位系统性压到下限（跨 7 天时实测 0.75）。所以就一个水位，全程乘一次。
        """
        if day in factor_cache:
            return factor_cache[day]
        short = [
            v
            for v in (
                bucket_volume(day - timedelta(days=i))
                for i in range(1, STORE_FACTOR_SHORT_DAYS + 1)
            )
            if v is not None
        ]
        long_ = [
            v
            for v in (
                bucket_volume(day - timedelta(days=i))
                for i in range(
                    STORE_FACTOR_SHORT_DAYS + 1,
                    STORE_FACTOR_SHORT_DAYS + STORE_FACTOR_LONG_DAYS + 1,
                )
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
    # 规则侧身份键（ADR 0141）：有 prep_item_id 按 id，没接上主数据才回落
    # (semi_name, unit)。备货品侧用 keys_for 同时查两种桶。
    rule_index = {
        rule_key(rule)
        for rules in exact_rules.values()
        for rule in rules
    }
    # 每个备货品被哪些明细菜品引用：配方在、但这些菜近两周一条订单都没有时，
    # 说明是「配方接不上菜」（系数被存成 0、明细被清空），不是真没需求。
    rule_dishes: Dict[Key, set] = {}
    for dish_name, rules in exact_rules.items():
        for rule in rules:
            rule_dishes.setdefault(rule_key(rule), set()).add(dish_name)

    entries: List[dict] = []
    for item in items:
        # 备货品侧的身份键（ADR 0141）：有 id 时同时查 id 桶与名字桶，缺 id 只查名字桶。
        item_keys = keys_for(item)
        has_rule = any(key in rule_index for key in item_keys)
        line_share = sum(line_counts.get(key, 0) for key in item_keys) / total_lines
        quantile_used = (
            cfg["quantile_major"] if line_share >= cfg["major_share"] else cfg["quantile"]
        )
        values = [window_qty(day, item_keys) for day in active_candidates]
        fallback_applied = False
        # 每段的分位落点（未乘水位、未取整）；页面据此显示「几段相加＝合计」
        slice_bases: List[float] = []
        # 分位落在多少：页面在同星期几的柱形图上用虚线标出这一档，
        # 让人直接看到「8 个候选日里的哪一条」被选中，而不是只给一个结果数。
        base_qty = 0.0
        if not has_rule:
            demand_qty = 0.0
            status = "missing_rule"
        elif not active_candidates:
            demand_qty = 0.0
            status = "no_history"
        elif not any(key in mapped_keys for key in item_keys) and any(
            dish_lines.get(name)
            for key in item_keys
            for name in rule_dishes.get(key, ())
        ):
            # 配方条目在、明细菜品也在卖，但一整段回看期一条量都算不出来：
            # 系数被存成 0 或明细被清空的典型形态。这种「静默 0」会真的少备一天货
            # （2026-10-27 虾饺参考 0、实卖 143 个），宁可报出来让人确认。
            demand_qty = 0.0
            status = "broken_rule"
        else:
            for k in range(slice_count):
                shift = timedelta(days=k)
                # 第 k 段的候选日 = 今天 - 7i + k 天：同一批星期几，整体往后挪 k 天
                active_k = [
                    day + shift
                    for day in active_candidates
                    if (day + shift) in days_with_orders
                ]
                values_k = [bucket_qty(day, item_keys) for day in active_k]
                base_k = _quantile(values_k, quantile_used) if values_k else 0.0
                if base_k <= 0:
                    # 该段候选日全撞在淡日/采集断档上，但近两周这一段有量：退回均值，不给 0
                    day_k = today + shift
                    usable = [
                        q
                        for q in (
                            bucket_qty(day_k - timedelta(days=i), item_keys)
                            for i in range(1, STALE_FALLBACK_DAYS + 1)
                        )
                        if q > 0
                    ]
                    if usable:
                        base_k = sum(usable) / len(usable)
                        fallback_applied = True
                slice_bases.append(base_k)
            base_qty = round(sum(slice_bases), 1)
            demand_qty = fit_qty(max(0.0, sum(slice_bases) * factor), item["kind"])
            status = "ok"

        floor_applied = False
        if status in ("ok", "no_history") and demand_qty < SPARSE_FLOOR_QTY:
            # 分位会把偶发品压成 0；近 7 天真的卖出过就保底 1 个单位
            if any(bucket_qty(dd, item_keys) > 0 for dd in recent_days):
                demand_qty = SPARSE_FLOOR_QTY
                floor_applied = True

        # ADR 0121：不登记产量，也不维护在库量。页面给的就是「今天参考值」，
        # 师傅拿它减掉自己看到的实物库存，决定还要备多少。
        suggested = demand_qty

        hour_values = [hour_qty(day, item_keys) for day in active_candidates]
        next_hour_qty = (
            fit_qty((sum(hour_values) / len(hour_values)) * factor, item["kind"])
            if hour_values
            else 0.0
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
                    # 这一品实际用的分位（主力品 p70 / 其余 p50），页面按它显示档位
                    "quantile": quantile_used,
                    "is_major": line_share >= cfg["major_share"],
                    "line_share": round(line_share, 4),
                    # 分位落点（未乘整店水位、未取整），柱形图虚线的位置
                    "quantile_qty": base_qty,
                    # 逐 24 小时的分段落点：48 小时两段、7 天七段，加起来就是 quantile_qty
                    "slices": [
                        {
                            "start": (now + timedelta(hours=24 * k)).isoformat(),
                            "end": (now + timedelta(hours=24 * (k + 1))).isoformat(),
                            "quantile_qty": round(base_k, 1),
                            "demand_qty": round(base_k * factor, 1),
                        }
                        for k, base_k in enumerate(slice_bases)
                    ],
                    # 未扣在库的需求量：页面拿它显示「今天要卖多少」
                    "demand_qty": demand_qty,
                    "floor_applied": floor_applied,
                    "fallback_applied": fallback_applied,
                },
                "history": [
                    {
                        "day": day.isoformat(),
                        "qty": round(day_total_qty(day, item_keys), 1),
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
            "horizon_hours": horizon,
        },
        "entries": entries,
        "missing_rule": [
            e["item_name"] for e in entries if e["basis"]["status"] == "missing_rule"
        ],
        "broken_rule": [
            e["item_name"] for e in entries if e["basis"]["status"] == "broken_rule"
        ],
        "forecast": {
            "quantile": cfg["quantile"],
            "quantile_major": cfg["quantile_major"],
            "major_share": cfg["major_share"],
            "window_weeks": cfg["window_weeks"],
            "store_factor": round(factor, 3),
        },
    }
