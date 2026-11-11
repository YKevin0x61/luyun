#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参考值（Reference Qty）的纯函数测试（ADR 0137）。

不连库、不碰系统时钟：orders / rules / items 都是普通 dict，`now` 用固定值钉死。
落库与接口那一层的行为仍在 tests/test_prep_plan_today.py。
"""

import unittest
from collections import defaultdict
from datetime import datetime, time, timedelta

from database import CHINA_TZ
from services.prep_plan_forecast import (
    _quantile,
    compute_reference,
    fit_qty,
    forecast_config,
    resolve_horizon_hours,
)

# 2026-10-22 是周四；8 个候选日 = 往前 1..8 周的周四
NOW = datetime(2026, 10, 22, 8, 0, tzinfo=CHINA_TZ)
CANDIDATES = [NOW - timedelta(days=7 * i) for i in range(1, 9)]
CLOSE = time(21, 30)

_IDS = iter(range(1, 1000))


def _clock(moment, hour, minute=0):
    return moment.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _order(moment, dish, qty):
    return {"dish_name": dish, "quantity": qty, "order_time": moment.isoformat()}


def _item(
    name,
    *,
    unit="份",
    station="",
    position="熟笼",
    kind="made",
    prep_item_id=None,
):
    return {
        "prep_item_id": prep_item_id if prep_item_id is not None else next(_IDS),
        "item_name": name,
        "unit": unit,
        "station": station,
        "position": position,
        "kind": kind,
    }


def _rule(semi, *, unit="份", factor=1.0):
    return {"semi_name": semi, "unit": unit, "factor": factor}


def _thursday_orders(dish, quantities):
    return [
        _order(_clock(day, 9), dish, qty)
        for day, qty in zip(CANDIDATES, quantities)
    ]


def _pad_volume(orders, *, constant=10000.0):
    """把 -1..-28 天每天的整店点单量补成常数，让整店水位系数正好是 1.0。

    补的是「无关菜」的订单行：它没有配方，只参与整店水位，不影响任何备货品。
    """
    totals = defaultdict(float)
    for row in orders:
        totals[datetime.fromisoformat(row["order_time"]).date()] += row["quantity"]
    for offset in range(1, 61):
        day = NOW - timedelta(days=offset)
        if offset <= 28:
            # 水位只回看 28 天：把这 28 天补成等量，系数才正好是 1.0
            need = constant - totals[day.date()]
        else:
            # 更早的日子只用来让同星期几的候选日「有营业」
            need = 1.0 if totals[day.date()] == 0 else 0.0
        if need > 0:
            orders.append(_order(_clock(day, 9), "无关菜", need))
    return orders


def _reference(
    orders,
    items,
    exact_rules=None,
    *,
    now=NOW,
    horizon=24,
    cfg=None,
    close_time=CLOSE,
):
    return compute_reference(
        now,
        horizon,
        forecast_config(None) if cfg is None else cfg,
        orders,
        exact_rules or {},
        {},
        items,
        close_time=close_time,
    )


def _entry(payload, name):
    for row in payload["entries"]:
        if row["item_name"] == name:
            return row
    raise AssertionError(f"没有这个备货品：{name}")


class ForecastConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = forecast_config(None)
        self.assertEqual(cfg["quantile"], 0.50)
        self.assertEqual(cfg["quantile_major"], 0.70)
        self.assertEqual(cfg["major_share"], 0.02)
        self.assertEqual(cfg["window_weeks"], 8)
        # 8 周 = 56 天，水位要 7 + 21 = 28 天，取大者再加 1 天余量
        self.assertEqual(cfg["lookback_days"], 57)
        self.assertEqual((cfg["factor_min"], cfg["factor_max"]), (0.75, 1.30))

    def test_clamps_and_derives_lookback(self):
        cfg = forecast_config(
            {
                "quantile": 0.99,
                "quantile_major": 0.01,
                "major_share": 2,
                "window_weeks": 99,
            }
        )
        self.assertEqual(cfg["quantile"], 0.95)
        self.assertEqual(cfg["quantile_major"], 0.05)
        self.assertEqual(cfg["major_share"], 1.0)
        self.assertEqual(cfg["window_weeks"], 12)
        self.assertEqual(cfg["lookback_days"], 85)

        cfg = forecast_config(
            {
                "quantile": -1,
                "quantile_major": 5,
                "major_share": -1,
                "window_weeks": 1,
            }
        )
        self.assertEqual(cfg["quantile"], 0.05)
        self.assertEqual(cfg["quantile_major"], 0.95)
        self.assertEqual(cfg["major_share"], 0.0)
        self.assertEqual(cfg["window_weeks"], 4)
        self.assertEqual(cfg["lookback_days"], 29)

    def test_zero_weeks_falls_back_and_non_dict_is_default(self):
        self.assertEqual(forecast_config({"window_weeks": 0})["window_weeks"], 8)
        self.assertEqual(forecast_config([1, 2])["quantile"], 0.50)

    def test_factor_bounds_pass_through(self):
        cfg = forecast_config({"factor_min": 0.9, "factor_max": 1.1})
        self.assertEqual((cfg["factor_min"], cfg["factor_max"]), (0.9, 1.1))


class QuantileTests(unittest.TestCase):
    def test_linear_interpolation(self):
        self.assertAlmostEqual(_quantile([1, 2, 3, 4], 0.5), 2.5)
        self.assertAlmostEqual(_quantile([1, 2, 3, 4], 0.25), 1.75)
        # 8 个样本、q=0.4：pos = 0.4 * 7 = 2.8 → 20 + (30 - 20) * 0.8
        self.assertAlmostEqual(
            _quantile([0, 10, 20, 30, 40, 50, 60, 70], 0.4), 28.0
        )
        self.assertAlmostEqual(
            _quantile([0, 10, 20, 30, 40, 50, 60, 70], 0.7), 49.0
        )

    def test_edges(self):
        self.assertEqual(_quantile([], 0.5), 0.0)
        self.assertEqual(_quantile([7], 0.9), 7.0)
        self.assertEqual(_quantile([1, 2, 3, 4], 0.0), 1.0)
        self.assertEqual(_quantile([1, 2, 3, 4], 1.0), 4.0)
        self.assertEqual(_quantile([1, 2, 3, 4], 2.0), 4.0)
        self.assertEqual(_quantile([1, 2, 3, 4], -1.0), 1.0)


class FitQtyTests(unittest.TestCase):
    def test_made_rounds_up_and_bought_keeps_one_decimal(self):
        self.assertEqual(fit_qty(2.0, "made"), 2.0)
        self.assertEqual(fit_qty(2.2, "made"), 3.0)
        # 浮点误差不该把 2.0000000000000004 顶成 3
        self.assertEqual(fit_qty(2.0000000000000004, "made"), 2.0)
        self.assertEqual(fit_qty(0.0, "made"), 0.0)
        self.assertEqual(fit_qty(1.26, "bought"), 1.3)
        self.assertEqual(fit_qty(1.234, "bought"), 1.2)


class HorizonResolverTests(unittest.TestCase):
    def test_accepts_whitelist(self):
        self.assertEqual(resolve_horizon_hours(None), 24)
        self.assertEqual(resolve_horizon_hours(""), 24)
        self.assertEqual(resolve_horizon_hours("48"), 48)
        self.assertEqual(resolve_horizon_hours(168), 168)

    def test_rejects_other_values(self):
        for value in (30, "abc", 0, -24):
            with self.assertRaises(ValueError):
                resolve_horizon_hours(value)


class MajorQuantileTests(unittest.TestCase):
    def test_major_item_uses_p70_and_minor_uses_p50(self):
        # 8 个周四：前 4 周 10 份、后 4 周 20 份；另有一条「烧卖」的订单行把
        # 总配料订单行数抬到 9，主力判据（≥ 2%）只让虾饺馅算主力。
        orders = _thursday_orders("虾饺皇", [10, 10, 10, 10, 20, 20, 20, 20])
        orders.append(_order(_clock(CANDIDATES[0], 9), "烧卖", 5))
        orders = _pad_volume(orders)
        items = [_item("虾饺馅"), _item("烧卖馅")]
        rules = {"虾饺皇": [_rule("虾饺馅")], "烧卖": [_rule("烧卖馅")]}

        payload = _reference(
            orders, items, rules, cfg=forecast_config({"major_share": 0.5})
        )
        major = _entry(payload, "虾饺馅")
        minor = _entry(payload, "烧卖馅")

        self.assertTrue(major["basis"]["is_major"])
        self.assertEqual(major["basis"]["quantile"], 0.70)
        # p70：pos = 0.7 * 7 = 4.9 → 落在两个 20 之间
        self.assertEqual(major["basis"]["quantile_qty"], 20.0)
        self.assertEqual(major["basis"]["demand_qty"], 20.0)
        self.assertEqual(
            [row["qty"] for row in major["basis"]["weekdays"]],
            [10.0, 10.0, 10.0, 10.0, 20.0, 20.0, 20.0, 20.0],
        )
        self.assertEqual(major["basis"]["active_days"], 8)

        self.assertFalse(minor["basis"]["is_major"])
        self.assertEqual(minor["basis"]["quantile"], 0.50)
        # 8 个候选值 [5, 0, 0, 0, 0, 0, 0, 0] 的 p50 = 0 → 近两周有 5 份，退回均值
        self.assertTrue(minor["basis"]["fallback_applied"])
        self.assertFalse(minor["basis"]["floor_applied"])
        self.assertEqual(minor["basis"]["quantile_qty"], 5.0)
        self.assertEqual(minor["basis"]["demand_qty"], 5.0)

        self.assertEqual(payload["forecast"]["quantile"], 0.50)
        self.assertEqual(payload["forecast"]["quantile_major"], 0.70)
        self.assertEqual(payload["forecast"]["major_share"], 0.5)
        self.assertEqual(payload["forecast"]["window_weeks"], 8)
        self.assertEqual(payload["forecast"]["store_factor"], 1.0)

    def test_minor_quantile_is_linear_interpolated(self):
        orders = _pad_volume(
            _thursday_orders("虾饺皇", [10, 10, 10, 10, 20, 20, 20, 20])
        )
        # 两行陪衬配方把占比压到 8/10；它们在水位窗口之外，不影响系数
        orders += [
            _order(_clock(NOW - timedelta(days=35), 9), "陪衬菜", 1),
            _order(_clock(NOW - timedelta(days=36), 9), "陪衬菜", 1),
        ]
        cfg = forecast_config({"major_share": 0.95})
        payload = _reference(
            orders,
            [_item("虾饺馅")],
            {"虾饺皇": [_rule("虾饺馅")], "陪衬菜": [_rule("陪衬料")]},
            cfg=cfg,
        )
        entry = _entry(payload, "虾饺馅")
        self.assertFalse(entry["basis"]["is_major"])
        self.assertEqual(entry["basis"]["quantile"], 0.50)
        # p50：pos = 0.5 * 7 = 3.5 → 10 + (20 - 10) * 0.5
        self.assertEqual(entry["basis"]["quantile_qty"], 15.0)
        self.assertEqual(entry["basis"]["demand_qty"], 15.0)


class StoreFactorTests(unittest.TestCase):
    def _factor(self, short, long_):
        orders = [
            _order(_clock(NOW - timedelta(days=offset), 9), "虾饺皇", short)
            for offset in range(1, 8)
        ]
        orders += [
            _order(_clock(NOW - timedelta(days=offset), 9), "虾饺皇", long_)
            for offset in range(8, 29)
        ]
        # 分位选哪一档与水位正交：陪衬只占 28/29≈96.6% 这条线之外的 3.4%，
        # 主力占比仍 ≥ 0.95，走 p70；本数据 p50=p70，手算基准量不受分档影响。
        # 陪衬订单下在 -35 天，落在 28 天水位窗口之外，不会扰动系数。
        orders.append(_order(_clock(NOW - timedelta(days=35), 9), "陪衬菜", 1))
        cfg = forecast_config({"major_share": 0.95})
        payload = _reference(
            orders,
            [_item("虾饺馅")],
            {"虾饺皇": [_rule("虾饺馅")], "陪衬菜": [_rule("陪衬料")]},
            cfg=cfg,
        )
        return payload, _entry(payload, "虾饺馅")

    def test_ratio_is_clamped_up(self):
        payload, entry = self._factor(30, 20)
        # 近 7 天 30 / 前 21 天 20 = 1.5 → 限到 1.30；候选日 [30, 20, 20, 20] 的 p50 = 20
        self.assertEqual(payload["forecast"]["store_factor"], 1.3)
        self.assertEqual(entry["basis"]["store_factor"], 1.3)
        self.assertEqual(entry["basis"]["quantile_qty"], 20.0)
        self.assertEqual(entry["basis"]["demand_qty"], 26.0)

    def test_ratio_is_clamped_down(self):
        payload, entry = self._factor(10, 20)
        # 10 / 20 = 0.5 → 抬到 0.75；候选日 [10, 20, 20, 20] 的 p50 = 20
        self.assertEqual(payload["forecast"]["store_factor"], 0.75)
        self.assertEqual(entry["basis"]["demand_qty"], 15.0)

    def test_ratio_in_range(self):
        payload, entry = self._factor(22, 20)
        # 22 / 20 = 1.1；候选日 [22, 20, 20, 20] 的 p50 = 20
        self.assertEqual(payload["forecast"]["store_factor"], 1.1)
        self.assertEqual(entry["basis"]["demand_qty"], 22.0)


class HorizonSliceTests(unittest.TestCase):
    def test_48_hours_is_two_slices(self):
        orders = []
        for day in CANDIDATES:
            orders.append(_order(_clock(day, 9), "虾饺皇", 10))  # 周四
            orders.append(
                _order(_clock(day + timedelta(days=1), 9), "虾饺皇", 20)  # 周五
            )
        orders = _pad_volume(orders)
        payload = _reference(
            orders, [_item("虾饺馅")], {"虾饺皇": [_rule("虾饺馅")]}, horizon=48
        )
        entry = _entry(payload, "虾饺馅")

        self.assertEqual(payload["window"]["horizon_hours"], 48)
        self.assertEqual(
            payload["window"]["end"], (NOW + timedelta(hours=48)).isoformat()
        )
        self.assertEqual(len(entry["basis"]["slices"]), 2)
        self.assertEqual(
            [row["quantile_qty"] for row in entry["basis"]["slices"]], [10.0, 20.0]
        )
        self.assertEqual(
            [row["demand_qty"] for row in entry["basis"]["slices"]], [10.0, 20.0]
        )
        self.assertEqual(entry["basis"]["slices"][0]["start"], NOW.isoformat())
        self.assertEqual(
            entry["basis"]["slices"][0]["end"], (NOW + timedelta(hours=24)).isoformat()
        )
        # 两段相加：不是拿 48 小时总量再取一次分位
        self.assertEqual(entry["basis"]["quantile_qty"], 30.0)
        self.assertEqual(entry["basis"]["demand_qty"], 30.0)


class StaleFallbackTests(unittest.TestCase):
    def test_zero_candidates_fall_back_to_14_day_mean(self):
        orders = _pad_volume(
            [
                _order(_clock(NOW - timedelta(days=2), 9), "虾饺皇", 6),
                _order(_clock(NOW - timedelta(days=3), 9), "虾饺皇", 4),
            ]
        )
        payload = _reference(orders, [_item("虾饺馅")], {"虾饺皇": [_rule("虾饺馅")]})
        entry = _entry(payload, "虾饺馅")

        self.assertEqual(entry["basis"]["status"], "ok")
        self.assertTrue(entry["basis"]["fallback_applied"])
        self.assertEqual([row["qty"] for row in entry["basis"]["weekdays"]], [0.0] * 8)
        # 候选日全 0，但近 14 天同时段有量：退回均值 (6 + 4) / 2
        self.assertEqual(entry["basis"]["quantile_qty"], 5.0)
        self.assertEqual(entry["basis"]["demand_qty"], 5.0)


class SparseFloorTests(unittest.TestCase):
    def test_item_sold_in_last_week_keeps_one_unit(self):
        # 候选日（8 个周四）一条都没有 → no_history；近 7 天卖过 3 份 → 保底 1
        orders = [
            _order(_clock(NOW - timedelta(days=3), 9), "虾饺皇", 3),
            _order(_clock(NOW - timedelta(days=4), 9), "无关菜", 100),
        ]
        payload = _reference(orders, [_item("虾饺馅")], {"虾饺皇": [_rule("虾饺馅")]})
        entry = _entry(payload, "虾饺馅")

        self.assertEqual(entry["basis"]["status"], "no_history")
        self.assertEqual(entry["basis"]["quantile_qty"], 0.0)
        self.assertEqual(entry["basis"]["demand_qty"], 1.0)
        self.assertTrue(entry["basis"]["floor_applied"])


class RuleStatusTests(unittest.TestCase):
    def test_missing_and_broken_rules_are_reported(self):
        orders = [
            _order(_clock(CANDIDATES[0], 9), "零系数菜", 5),
            _order(_clock(CANDIDATES[0], 9), "虾饺皇", 10),
        ]
        items = [_item("没接配方的料"), _item("零系数馅"), _item("虾饺馅")]
        rules = {
            "零系数菜": [_rule("零系数馅", factor=0.0)],
            "虾饺皇": [_rule("虾饺馅")],
        }
        payload = _reference(orders, items, rules)

        self.assertEqual(payload["missing_rule"], ["没接配方的料"])
        self.assertEqual(payload["broken_rule"], ["零系数馅"])
        self.assertEqual(
            _entry(payload, "没接配方的料")["basis"]["status"], "missing_rule"
        )
        self.assertEqual(_entry(payload, "零系数馅")["basis"]["status"], "broken_rule")
        self.assertEqual(_entry(payload, "虾饺馅")["basis"]["status"], "ok")
        self.assertEqual(_entry(payload, "没接配方的料")["basis"]["demand_qty"], 0.0)
        self.assertEqual(_entry(payload, "零系数馅")["basis"]["demand_qty"], 0.0)


class WindowAndHistoryTests(unittest.TestCase):
    def test_dates_and_history_are_pinned_by_now(self):
        orders = _pad_volume(
            [
                _order(datetime(2026, 10, 22, 7, 0, tzinfo=CHINA_TZ), "虾饺皇", 7),
                _order(datetime(2026, 10, 21, 9, 0, tzinfo=CHINA_TZ), "虾饺皇", 5),
            ]
        )
        payload = _reference(orders, [_item("虾饺馅")], {"虾饺皇": [_rule("虾饺馅")]})

        self.assertEqual(payload["plan_day"], "2026-10-22")
        self.assertEqual(payload["generated_at"], NOW.isoformat())
        self.assertEqual(payload["window"]["start"], NOW.isoformat())
        self.assertEqual(
            payload["window"]["end"], (NOW + timedelta(hours=24)).isoformat()
        )
        self.assertFalse(payload["window"]["after_close"])
        self.assertFalse(payload["window"]["closed"])

        history = _entry(payload, "虾饺馅")["history"]
        self.assertEqual(len(history), 8)
        self.assertEqual(
            [row["day"] for row in history],
            [(NOW - timedelta(days=i)).date().isoformat() for i in range(8)],
        )
        self.assertEqual([row["qty"] for row in history], [7.0, 5.0] + [0.0] * 6)

    def test_window_starts_at_now_not_midnight(self):
        """中午打开页面：窗口从 12:30 起算，候选日 11:30 的订单不算进这一段。"""
        noon = datetime(2026, 10, 22, 12, 30, tzinfo=CHINA_TZ)
        orders = []
        for day in CANDIDATES:
            orders.append(_order(_clock(day, 13), "虾饺皇", 7))
            orders.append(_order(_clock(day, 11, 30), "虾饺皇", 100))
        orders = _pad_volume(orders)
        payload = _reference(
            orders, [_item("虾饺馅")], {"虾饺皇": [_rule("虾饺馅")]}, now=noon
        )
        entry = _entry(payload, "虾饺馅")

        self.assertEqual(payload["window"]["start"], noon.isoformat())
        self.assertEqual(entry["basis"]["quantile_qty"], 7.0)
        self.assertEqual(entry["suggested_qty"], 7)

    def test_after_close_only_flips_the_window_flag(self):
        night = datetime(2026, 10, 22, 22, 0, tzinfo=CHINA_TZ)
        payload = _reference(
            _pad_volume([_order(_clock(CANDIDATES[0], 9), "虾饺皇", 10)]),
            [_item("虾饺馅")],
            {"虾饺皇": [_rule("虾饺馅")]},
            now=night,
        )
        self.assertTrue(payload["window"]["after_close"])
        self.assertEqual(payload["window"]["start"], night.isoformat())
        self.assertEqual(payload["plan_day"], "2026-10-22")
        self.assertEqual(payload["entries"][0]["basis"]["status"], "ok")


class PositionsAndStationTests(unittest.TestCase):
    def test_positions_order_and_station_fallback(self):
        orders = _pad_volume([_order(_clock(CANDIDATES[0], 9), "虾饺皇", 10)])
        items = [
            _item("凉菜料", position="凉菜", prep_item_id=3),
            _item("明档料", position="明档1", prep_item_id=2),
            _item("虾饺馅", position="熟笼", prep_item_id=1),
        ]
        payload = _reference(orders, items, {"虾饺皇": [_rule("虾饺馅")]})

        # 已知岗位按 POSITION_STATION_MAP 的顺序，未知岗位排在后面按名字排
        self.assertEqual(payload["positions"], ["熟笼", "明档1", "凉菜"])
        self.assertEqual(_entry(payload, "虾饺馅")["station"], "shulong")
        self.assertEqual(_entry(payload, "明档料")["station"], "mingdang1")
        self.assertEqual(_entry(payload, "凉菜料")["station"], "")
        # 排序键：(档口, 形态, -建议量, 品名)
        self.assertEqual(
            [row["item_name"] for row in payload["entries"]],
            ["凉菜料", "明档料", "虾饺馅"],
        )


if __name__ == "__main__":
    unittest.main()
