#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划（ADR 0117 重建）的服务缝。

盯四件事：同星期几均量、剩余窗口切分、当日计划的勾选/手改不被重算覆盖、
员工只能看自己档口。
"""

import unittest
from datetime import datetime, timedelta

from database import CHINA_TZ, DatabaseManager
from services.prep_plan_service import (
    FORECAST_SETTINGS_KEY,
    PrepPlanService,
    _quantile,
    fit_qty,
)

ITEM_NAME = "虾饺馅"
UNIT = "份"
DISH_NAME = "虾饺皇"
STATION = "shulong"

# 2026-10-22 是周四；往前 4 个同星期几 = 10-15 / 10-08 / 10-01 / 09-24
TODAY_MORNING = datetime(2026, 10, 22, 8, 0, tzinfo=CHINA_TZ)
TODAY_NOON = datetime(2026, 10, 22, 12, 0, tzinfo=CHINA_TZ)
TODAY_NIGHT = datetime(2026, 10, 22, 22, 0, tzinfo=CHINA_TZ)
CANDIDATE_DAYS = ("2026-10-15", "2026-10-08", "2026-10-01", "2026-09-24")


def _at(date_str, hour, minute=0):
    year, month, day = (int(part) for part in date_str.split("-"))
    return datetime(year, month, day, hour, minute, tzinfo=CHINA_TZ)


class PrepPlanRebuildFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.service = PrepPlanService()
        self.stamp = TODAY_MORNING.isoformat()

    async def asyncTearDown(self):
        await self.db.close()

    async def _seed_item(
        self, *, item_name=ITEM_NAME, unit=UNIT, station=STATION, position='熟笼', kind='made'
    ):
        items = self.db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_items (
                    item_name, station, position, category, unit, kind, active,
                    shelf_life_hours, lead_time_hours, min_batch_qty,
                    safety_stock_ratio, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 24, 0, 0, 0.15, '', ?, ?)
                """,
                (
                    item_name,
                    station,
                    position,
                    '半成品' if kind == 'made' else '原料',
                    unit,
                    kind,
                    self.stamp,
                    self.stamp,
                ),
            )
            item_id = cursor.lastrowid
        await items.commit()
        return item_id

    async def _seed_rule(self, *, dish_name=DISH_NAME, semi_name=ITEM_NAME, unit=UNIT, factor=1):
        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO semi_finished_rules (
                    dish_name, semi_name, position, factor, unit, category,
                    notes, created_at, updated_at
                ) VALUES (?, ?, '熟笼', ?, ?, '半成品', '', ?, ?)
                """,
                (dish_name, semi_name, factor, unit, self.stamp, self.stamp),
            )
        await rules.commit()

    async def _seed_orders(self, days, *, hour, minute, qty, dish_name=DISH_NAME):
        orders = []
        for index, day in enumerate(days):
            orders.append(
                {
                    "business_flow_id": f"prep-{day}-{hour}-{index}",
                    "table_number": "A1",
                    "dish_name": dish_name,
                    "quantity": qty,
                    "order_time": _at(day, hour, minute),
                    "station": STATION,
                }
            )
        if orders:
            result = await self.db.orders.batch_insert_orders(orders)
            self.assertEqual(result["inserted_count"], len(orders))

    async def _seed_stationary(self, *, except_days=(), morning=40, evening=40):
        """8 周稳定销量：残差恒为 0、整店水位恒为 1.0。

        这样断言只在考「同星期几均量」本身，不会被缓冲项和整店系数干扰。
        """
        skip = {str(day) for day in except_days}
        end = TODAY_MORNING.date() - timedelta(days=1)
        day = end - timedelta(days=56)
        days = []
        while day <= end:
            if day.isoformat() not in skip:
                days.append(day.isoformat())
            day += timedelta(days=1)
        if morning:
            await self._seed_orders(days, hour=9, minute=0, qty=morning)
        if evening:
            await self._seed_orders(days, hour=18, minute=30, qty=evening)

    def _entry(self, payload, item_name=ITEM_NAME):
        matches = [row for row in payload["entries"] if row["item_name"] == item_name]
        self.assertEqual(len(matches), 1, payload["entries"])
        return matches[0]


class SameWeekdayMeanTests(PrepPlanRebuildFixture):
    async def test_mean_of_four_same_weekdays(self):
        """4 个周四各 80（早 40 + 晚 40），早上 8 点看 → 建议量 80。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()

        result = await self.service.compute_suggestions(self.db, TODAY_MORNING)
        entry = self._entry(result)
        self.assertEqual(entry["suggested_qty"], 80)
        self.assertEqual(entry["basis"]["status"], "ok")
        self.assertEqual(len(entry["basis"]["weekdays"]), 8)
        # 只有一个配方菜品 → 主力品档位（p70），且不加任何安全缓冲
        self.assertEqual(entry["basis"]["quantile"], 0.70)
        # 页面柱形图的虚线画在这一档上：8 个候选日全是 80，落点就是 80（未乘水位、未取整）
        self.assertEqual(entry["basis"]["quantile_qty"], 80)

    async def test_window_spans_the_next_24_hours(self):
        """中午 12 点看，窗口是「现在 → 明天 12 点」，跨两天各取一段（ADR 0122）。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()

        # 每个候选日的 [12:00, 次日 12:00) = 当天 18:30 的 40 + 次日 09:00 的 40 = 80
        entry = self._entry(await self.service.compute_suggestions(self.db, TODAY_NOON))
        self.assertEqual(entry["basis"]["demand_qty"] > 40, True)
        self.assertEqual(entry["suggested_qty"], entry["basis"]["demand_qty"])

    async def test_closed_candidate_day_drops_out_of_the_denominator(self):
        """某个同星期几整天没单（不做生意）→ 不进分母，均量还是 80。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary(except_days=("2026-09-24",))

        result = await self.service.compute_suggestions(self.db, TODAY_MORNING)
        entry = self._entry(result)
        self.assertEqual(entry["suggested_qty"], 80)
        self.assertEqual(entry["basis"]["active_days"], 7)

    async def test_after_close_still_covers_the_next_24_hours(self):
        """ADR 0122：滚动 24 小时窗口，打烊后仍然给「到明天此刻」的参考值。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)

        result = await self.service.compute_suggestions(self.db, TODAY_NIGHT)
        self.assertFalse(result["window"]["closed"])
        self.assertTrue(result["window"]["after_close"])
        self.assertEqual(result["window"]["horizon_hours"], 24)

    async def test_item_without_recipe_is_flagged(self):
        """没有配方系数的品给 0，并标 missing_rule（页面要能解释为什么空着）。"""
        await self._seed_item(item_name="鱼皇料")
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)

        result = await self.service.compute_suggestions(self.db, TODAY_MORNING)
        entry = self._entry(result, item_name="鱼皇料")
        self.assertEqual(entry["suggested_qty"], 0)
        self.assertEqual(entry["basis"]["status"], "missing_rule")
        self.assertIn("鱼皇料", result["missing_rule"])

    async def test_history_covers_today_plus_seven_whole_days(self):
        """柱形图给 8 根：今天（只算到此刻）+ 前面 7 个完整营业日，才比得出趋势。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)

        result = await self.service.compute_suggestions(self.db, TODAY_MORNING)
        entry = self._entry(result)
        self.assertEqual(len(entry["history"]), 8)
        self.assertEqual(entry["history"][0]["day"], "2026-10-22")
        self.assertEqual(entry["history"][-1]["day"], "2026-10-15")


class HorizonWindowTests(PrepPlanRebuildFixture):
    """时间跨度：24 / 48 / 72 / 168 小时。

    窗口一律从**现在**起算，所以「N 小时」必须把窗口盖到的每个自然日都算进去——
    旧实现只加 day 和 day+1 两段，48 小时以上会整段整段地漏。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()

    async def _suggested(self, hours):
        result = await self.service.compute_suggestions(
            self.db, TODAY_MORNING, horizon_hours=hours
        )
        return result, self._entry(result)

    async def test_defaults_to_24_hours(self):
        result, entry = await self._suggested(None)
        self.assertEqual(result["window"]["horizon_hours"], 24)
        self.assertEqual(entry["suggested_qty"], 80)

    async def test_every_day_in_the_window_counts(self):
        """每天 09:00 40 + 18:30 40，08:00 看：每多盖到一天就多 80。"""
        for hours, expected in ((24, 80), (48, 160), (72, 240), (168, 560)):
            result, entry = await self._suggested(hours)
            self.assertEqual(result["window"]["horizon_hours"], hours)
            self.assertEqual(entry["suggested_qty"], expected, f"{hours} 小时")
            # 稳定序列上整店水位恒为 1，建议量就是分位量本身
            self.assertAlmostEqual(entry["basis"]["store_factor"], 1.0, places=2)

    async def test_window_bounds_follow_the_horizon(self):
        result, _ = await self._suggested(168)
        self.assertEqual(result["window"]["start"], TODAY_MORNING.isoformat())
        self.assertEqual(
            result["window"]["end"],
            (TODAY_MORNING + timedelta(hours=168)).isoformat(),
        )

    async def test_unsupported_horizon_is_rejected(self):
        with self.assertRaises(ValueError):
            await self.service.compute_suggestions(
                self.db, TODAY_MORNING, horizon_hours=30
            )

    async def test_get_today_passes_the_horizon_through(self):
        result = await self.service.get_today(self.db, now=TODAY_MORNING, horizon_hours=72)
        self.assertEqual(result["window"]["horizon_hours"], 72)
        self.assertEqual(self._entry(result)["suggested_qty"], 240)

    async def test_snapshot_stays_on_the_24_hour_window(self):
        """复盘按「一条快照 = 它自己那个 24 小时窗口」对账，页面切跨度不能改它。"""
        await self.service.write_snapshot(self.db, TODAY_MORNING)
        snapshots = self.db.table("prep_plan_snapshots")
        async with snapshots.conn.cursor() as cursor:
            await cursor.execute("SELECT window_start, window_end FROM prep_plan_snapshots")
            rows = await cursor.fetchall()
        self.assertTrue(rows)
        for row in rows:
            start = datetime.fromisoformat(str(row["window_start"]))
            end = datetime.fromisoformat(str(row["window_end"]))
            self.assertEqual(end - start, timedelta(hours=24))


class AdditiveSliceTests(PrepPlanRebuildFixture):
    """超过 24 小时的参考值 = 各段 24 小时的预测相加。

    用户口径（2026-11-05）：72 小时 = 未来 24 小时 + 第 25–48 小时 + 第 49–72 小时。
    所以每段各自按「近 8 周同星期几」取分位，再相加；不是拿整段总量取一次分位。
    24 小时只有一段，与回测口径（ADR 0122）逐字一致。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._seed_item()
        await self._seed_rule()

    async def _slices(self, hours):
        entry = self._entry(
            await self.service.compute_suggestions(
                self.db, TODAY_MORNING, horizon_hours=hours
            )
        )
        return entry["basis"]

    async def test_one_slice_per_24_hours(self):
        await self._seed_stationary()
        for hours, count in ((24, 1), (48, 2), (72, 3), (168, 7)):
            basis = await self._slices(hours)
            self.assertEqual(len(basis["slices"]), count, f"{hours} 小时")
            # 每段都是 24 小时，首尾相接
            for k, part in enumerate(basis["slices"]):
                start = datetime.fromisoformat(part["start"])
                end = datetime.fromisoformat(part["end"])
                self.assertEqual(start, TODAY_MORNING + timedelta(hours=24 * k))
                self.assertEqual(end - start, timedelta(hours=24))
            self.assertAlmostEqual(
                sum(part["quantile_qty"] for part in basis["slices"]),
                basis["quantile_qty"],
                places=6,
            )

    async def test_stationary_series_sums_the_same_number_each_day(self):
        await self._seed_stationary()
        # 每天 80、水位恒为 1：48 小时就是 80 + 80
        basis = await self._slices(48)
        self.assertEqual([part["quantile_qty"] for part in basis["slices"]], [80, 80])
        self.assertEqual(basis["quantile_qty"], 160)
        entry = self._entry(
            await self.service.compute_suggestions(self.db, TODAY_MORNING, horizon_hours=48)
        )
        self.assertEqual(entry["suggested_qty"], 160)

    async def test_each_slice_is_quantiled_on_its_own_weekday(self):
        """第 1 段看周四的 8 周、第 2 段看周五的 8 周，各取各的分位再相加。

        周四与周五的量是「此起彼伏」的（各段的 p70 都落在 10 与 300 之间的插值 271），
        但同一周两天合起来恒是 310 或 20 —— 先合计再取分位得到 310，
        分段相加才是 271 + 271 = 542。这条用例钉的就是「分段」这个口径。
        """
        thu = [10, 10, 10, 10, 10, 300, 300, 300]
        fri = [300, 300, 300, 10, 10, 10, 10, 10]
        for weeks_back, (thu_qty, fri_qty) in enumerate(zip(thu, fri), start=1):
            thursday = TODAY_MORNING.date() - timedelta(days=7 * weeks_back)
            friday = thursday + timedelta(days=1)
            await self._seed_orders([thursday.isoformat()], hour=9, minute=0, qty=thu_qty)
            await self._seed_orders([friday.isoformat()], hour=9, minute=0, qty=fri_qty)

        # 只有一个配方菜品 → 主力品，档位 p70（ADR 0128）
        expected_thu = _quantile(thu, 0.70)
        expected_fri = _quantile(fri, 0.70)
        basis = await self._slices(48)
        self.assertAlmostEqual(basis["slices"][0]["quantile_qty"], expected_thu, places=6)
        self.assertAlmostEqual(basis["slices"][1]["quantile_qty"], expected_fri, places=6)
        self.assertAlmostEqual(
            basis["quantile_qty"], expected_thu + expected_fri, places=6
        )
        # 反面：先合计再取分位会落到 310，说明加的是「两段的分位」而不是「分位的两段」
        self.assertAlmostEqual(_quantile([a + b for a, b in zip(thu, fri)], 0.70), 310.0)
        self.assertNotAlmostEqual(basis["quantile_qty"], 310.0, places=1)

    async def test_24_hours_has_no_decomposition_to_explain(self):
        await self._seed_stationary()
        basis = await self._slices(24)
        self.assertEqual(len(basis["slices"]), 1)
        self.assertEqual(basis["slices"][0]["quantile_qty"], basis["quantile_qty"])


class ForecastQuantileTests(PrepPlanRebuildFixture):
    """ADR 0128：建议量 = 分位(8 周同星期几) × 整店水位，主力品 p70、其余 p50。"""

    def test_quantile_is_linear_interpolated(self):
        self.assertEqual(_quantile([1, 2, 3, 4], 0.0), 1)
        self.assertEqual(_quantile([1, 2, 3, 4], 1.0), 4)
        self.assertEqual(_quantile([5], 0.4), 5)
        self.assertEqual(_quantile([], 0.4), 0.0)
        # 8 个样本、p40 → 位置 0.4×7 = 2.8 → 第 3、4 个之间插值
        self.assertAlmostEqual(_quantile([10, 20, 30, 40, 50, 60, 70, 80], 0.4), 38.0)

    async def test_stationary_series_gives_the_tier_quantile_without_any_buffer(self):
        """稳定序列 → 分位量就是 80，不额外加任何安全量（conformal 缓冲已删）。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()

        entry = self._entry(await self.service.compute_suggestions(self.db, TODAY_MORNING))
        self.assertEqual(entry["suggested_qty"], 80)
        # 只有一个配方菜品 → 订单行占比 100%，属于主力品 → p70
        self.assertTrue(entry["basis"]["is_major"])
        self.assertEqual(entry["basis"]["quantile"], 0.70)
        self.assertFalse(entry["basis"]["floor_applied"])

    async def test_major_share_setting_moves_an_item_between_tiers(self):
        """同一个品，只是把「主力」判据抬高，就应该从 p70 掉到 p50。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()  # 目标品：56 天 × 2 次 = 112 行
        companion_days = [
            (TODAY_MORNING.date() - timedelta(days=i)).isoformat() for i in range(1, 57)
        ]
        await self._seed_rule(dish_name="肠粉", semi_name="肠粉浆")  # 陪衬配方
        for hour in (12, 13, 14):  # 陪衬菜品 168 行，把目标品压到 40%
            await self._seed_orders(
                companion_days, hour=hour, minute=0, qty=1, dish_name="肠粉"
            )

        default_entry = self._entry(
            await self.service.compute_suggestions(self.db, TODAY_MORNING)
        )
        self.assertTrue(default_entry["basis"]["is_major"])
        self.assertEqual(default_entry["basis"]["quantile"], 0.70)
        self.assertAlmostEqual(default_entry["basis"]["line_share"], 0.4, places=2)

        await self.db.settings_set_json(FORECAST_SETTINGS_KEY, {"major_share": 0.5})
        strict_entry = self._entry(
            await self.service.compute_suggestions(self.db, TODAY_MORNING)
        )
        self.assertFalse(strict_entry["basis"]["is_major"])
        self.assertEqual(strict_entry["basis"]["quantile"], 0.50)

    async def test_recipe_that_outputs_nothing_is_reported_not_silently_zero(self):
        """配方条目在、菜也在卖、但系数是 0 → 不能静默给 0，要报 broken_rule。"""
        await self._seed_item()
        await self._seed_rule(factor=0)
        await self._seed_stationary()

        payload = await self.service.compute_suggestions(self.db, TODAY_MORNING)
        entry = self._entry(payload)
        self.assertEqual(entry["basis"]["status"], "broken_rule")
        self.assertEqual(entry["suggested_qty"], 0)
        self.assertEqual(payload["broken_rule"], [ITEM_NAME])

    async def test_recipe_whose_dishes_simply_stopped_selling_stays_quiet(self):
        """配方在、菜真的没人点了 → 不算断档，安静给 0（那些是待清理的孤儿配方）。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()  # 有量，页面不是空的
        await self._seed_item(item_name="南瓜卷")
        await self._seed_rule(dish_name="南瓜卷菜", semi_name="南瓜卷")

        payload = await self.service.compute_suggestions(self.db, TODAY_MORNING)
        entry = self._entry(payload, item_name="南瓜卷")
        self.assertEqual(entry["basis"]["status"], "ok")
        self.assertEqual(entry["suggested_qty"], 0)
        self.assertNotIn("南瓜卷", payload["broken_rule"])

    async def test_candidate_days_without_volume_fall_back_to_two_week_mean(self):
        """候选日全撞在没量的日子上、近两周其实有量 → 退回两周均值，不给 0。"""
        await self._seed_item()
        await self._seed_rule()
        # 候选日那天有别的菜在卖（所以算「有营业」），但这一品一条都没出
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=10, dish_name="肠粉")
        recent = [
            (TODAY_MORNING.date() - timedelta(days=i)).isoformat() for i in range(1, 4)
        ]
        await self._seed_orders(recent, hour=9, minute=0, qty=10)

        entry = self._entry(await self.service.compute_suggestions(self.db, TODAY_MORNING))
        self.assertTrue(entry["basis"]["fallback_applied"])
        self.assertEqual(entry["suggested_qty"], 10)

    async def test_store_factor_is_clipped_to_115_percent(self):
        """近 7 天是前 21 天的 1.5 倍 → 水位夹到 1.15，不再跟到 1.3（少剩货口径）。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()
        last_week = [
            (TODAY_MORNING.date() - timedelta(days=i)).isoformat() for i in range(1, 8)
        ]
        await self._seed_orders(last_week, hour=20, minute=0, qty=80)

        entry = self._entry(await self.service.compute_suggestions(self.db, TODAY_MORNING))
        self.assertEqual(entry["basis"]["store_factor"], 1.30)
        # p40(80,80,160…) 仍按分位算，再乘 1.15
        self.assertGreaterEqual(entry["suggested_qty"], 80 * 1.15)

    async def test_sparse_item_gets_floor_of_one(self):
        """8 周同星期几全是 0、但近 7 天卖过 → 保底 1，不让长尾品静默消失。"""
        await self._seed_item(item_name="马拉盏")
        await self._seed_rule(semi_name="马拉盏")
        recent = [
            (TODAY_MORNING.date() - timedelta(days=i)).isoformat() for i in range(1, 4)
        ]
        await self._seed_orders(recent, hour=9, minute=0, qty=1, dish_name=DISH_NAME)

        entry = self._entry(
            await self.service.compute_suggestions(self.db, TODAY_MORNING), item_name="马拉盏"
        )
        self.assertTrue(entry["basis"]["floor_applied"])
        self.assertEqual(entry["suggested_qty"], 1)

    async def test_demand_qty_is_reported_next_to_suggested(self):
        """页面要同时显示「今天要卖多少」和「还要做多少」，前者不扣在库。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()

        entry = self._entry(await self.service.compute_suggestions(self.db, TODAY_MORNING))
        self.assertEqual(entry["basis"]["demand_qty"], 80)
        self.assertEqual(entry["suggested_qty"], 80)
        # quantile_qty 是「分位落点」本身：乘水位、取整之前的那个数，页面靠它标虚线
        self.assertEqual(entry["basis"]["quantile_qty"], 80)

    async def test_quantile_qty_ignores_store_factor(self):
        """水位变化只影响参考值，不该挪动虚线：候选日用量的分位是原始事实。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()
        # 近 7 天里挑非候选日（候选日是周四）加量，把整店水位顶上去但不动候选日
        busy = [
            (TODAY_MORNING.date() - timedelta(days=i)).isoformat() for i in range(1, 7)
        ]
        await self._seed_orders(busy, hour=20, minute=0, qty=80)

        payload = await self.service.compute_suggestions(self.db, TODAY_MORNING)
        entry = self._entry(payload)
        self.assertGreater(entry["basis"]["store_factor"], 1.0)
        self.assertEqual(entry["basis"]["quantile_qty"], 80)
        self.assertGreater(entry["basis"]["demand_qty"], 80)


class SnapshotTests(PrepPlanRebuildFixture):
    """ADR 0123：整点快照 + 窗口结束后回填实际销量。"""

    async def test_snapshot_rows_are_written_once_per_item(self):
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()

        first = await self.service.write_snapshot(self.db, TODAY_MORNING)
        self.assertEqual(first["rows"], 1)
        # 同一整点重复写不应重复插
        second = await self.service.write_snapshot(self.db, TODAY_MORNING)
        self.assertEqual(second["rows"], 1)
        rows = self.db.table("prep_plan_snapshots")
        async with rows.conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) AS n FROM prep_plan_snapshots")
            self.assertEqual(int((await cursor.fetchone())["n"]), 1)

    async def test_actuals_backfilled_after_window_closes(self):
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()
        await self.service.write_snapshot(self.db, TODAY_MORNING)

        # 窗口还没结束 → 不回填
        early = await self.service.backfill_actuals(self.db, TODAY_MORNING + timedelta(hours=1))
        self.assertEqual(early["windows"], 0)

        # 窗口结束（24 小时后）→ 回填
        late = await self.service.backfill_actuals(self.db, TODAY_MORNING + timedelta(hours=25))
        self.assertEqual(late["windows"], 1)

        day = await self.service.get_snapshot_day(self.db, TODAY_MORNING.date().isoformat())
        self.assertEqual(len(day["items"]), 1)
        hour = day["items"][0]["hours"][0]
        self.assertIsNotNone(hour["sold_qty"])
        self.assertTrue(hour["same_rules"])

    async def test_snapshot_of_one_hour_is_judged_on_its_own_window(self):
        """ADR 0129：一条快照只跟它自己那个 24 小时窗口比，不跟同一天其他钟点平均。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()
        await self.service.write_snapshot(self.db, TODAY_MORNING)
        # 另一个钟点：窗口整体后移一小时，实际销量会变
        await self.service.write_snapshot(self.db, TODAY_MORNING + timedelta(hours=1))
        # 当天 08:30 卖出一单：只落在 08:00 那条快照的窗口里，09:00 那条吃不到
        await self._seed_orders([TODAY_MORNING.date().isoformat()], hour=8, minute=30, qty=100)
        await self.service.backfill_actuals(self.db, TODAY_MORNING + timedelta(hours=25))

        day = await self.service.get_snapshot_day(self.db, TODAY_MORNING.date().isoformat())
        clocks = [g["clock"] for g in day["snapshots"]]
        self.assertEqual(len(clocks), 2)
        # 只有 08:00/09:00 两条时没有 07:00 主口径 → 退回最早那条
        self.assertEqual(day["primary_clock"], "08:00")
        self.assertEqual(set(clocks), {"08:00", "09:00"})

        first = next(g for g in day["snapshots"] if g["clock"] == "08:00")["summary"]
        later = next(g for g in day["snapshots"] if g["clock"] == "09:00")["summary"]
        self.assertEqual(first["measured"], 1)
        self.assertEqual(first["pending"], 0)
        # 稳定序列：8 个同星期几都是 80 → 参考值 80
        self.assertEqual(
            next(g for g in day["snapshots"] if g["clock"] == "08:00")["items"][0]["suggested_qty"], 80
        )
        # 08:00 那条窗口里有 08:30 的 100 个 → 缺 20；09:00 那条吃不到，实际 0
        self.assertAlmostEqual(first["sold_total"], 100)
        self.assertAlmostEqual(first["shortfall"], 20)
        self.assertEqual(first["covered"], 0.0)
        self.assertAlmostEqual(first["ref_ratio"], 0.8)
        self.assertAlmostEqual(later["sold_total"], 0)
        self.assertAlmostEqual(later["overfill"], 80)
        # 实际为 0 时比例没有分母 → None，不是 0 也不是无穷
        self.assertIsNone(later["ref_ratio"])
        self.assertEqual(later["covered"], 1.0)
        item = next(g for g in day["snapshots"] if g["clock"] == "08:00")["items"][0]
        self.assertTrue(item["is_major"])
        self.assertAlmostEqual(item["quantile"], 0.70)

    async def test_window_still_open_shows_up_as_pending_not_zero(self):
        """窗口没结束就没有实际销量，不能当成「实际卖 0」把够卖率刷成 100%。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()
        await self.service.write_snapshot(self.db, TODAY_MORNING)

        day = await self.service.get_snapshot_day(self.db, TODAY_MORNING.date().isoformat())
        summary = day["snapshots"][0]["summary"]
        self.assertEqual(summary["measured"], 0)
        self.assertEqual(summary["pending"], 1)
        self.assertIsNone(summary["covered"])
        self.assertIsNone(summary["ref_ratio"])


class PlanPersistenceTests(PrepPlanRebuildFixture):
    async def test_done_and_manual_qty_survive_recompute(self):
        """勾选与手改是店长当天的动作，重算不许抹掉。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()

        first = await self.service.get_today(self.db, now=TODAY_MORNING)
        entry = self._entry(first)
        self.assertEqual(entry["suggested_qty"], 80)

        await self.service.update_entry(
            self.db, entry["id"], final_qty=95, done=True
        )
        await self.service.update_entry(self.db, entry["id"], done=False)

        again = await self.service.get_today(self.db, now=TODAY_MORNING)
        refreshed = self._entry(again)
        self.assertEqual(refreshed["final_qty"], 95)
        self.assertEqual(refreshed["qty_source"], "manual")
        # ADR 0121 起建议量不再扣在库，参考值不因勾选而变
        self.assertEqual(refreshed["suggested_qty"], 80)

    async def test_manual_qty_is_not_overwritten_by_a_later_window(self):
        """中午重算把建议量压到 40，但店长早上改的 95 保留（他改的是他那一份判断）。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()

        first = await self.service.get_today(self.db, now=TODAY_MORNING)
        entry = self._entry(first)
        await self.service.update_entry(self.db, entry["id"], final_qty=95)

        noon = await self.service.get_today(self.db, now=TODAY_NOON)
        refreshed = self._entry(noon)
        # 参考值随时间变（滚动 24 小时窗口），但手改的 95 必须留着
        self.assertEqual(refreshed["suggested_qty"], refreshed["basis"]["demand_qty"])
        self.assertEqual(refreshed["final_qty"], 95)

    async def test_summary_counts_remaining_by_station(self):
        await self._seed_item()
        await self._seed_rule()
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)

        today = await self.service.get_today(self.db, now=TODAY_MORNING)
        entry = self._entry(today)
        await self.service.update_entry(self.db, entry["id"], done=True)

        summary = await self.service.get_summary(self.db, now=TODAY_MORNING)
        self.assertEqual(summary["remaining"], 0)
        self.assertEqual(summary["stations"][0]["remaining"], 0)


class StaffStationTests(PrepPlanRebuildFixture):
    def setUp(self):
        self.employee_id = 401

    async def test_staff_must_pick_station_before_seeing_anything(self):
        await self._seed_item()
        await self._seed_rule()
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)

        result = await self.service.get_today(self.db, now=TODAY_MORNING, employee_id=self.employee_id)
        self.assertTrue(result["needs_station_pick"])
        self.assertEqual(result["entries"], [])
        self.assertIsNone(result["staff_station"])

    async def test_staff_sees_only_own_station(self):
        # 员工选的是**岗位**：两个品同档口（shulong）、不同岗位，只该看到自己岗位的那个
        await self._seed_item(item_name=ITEM_NAME, station="shulong", position="熟笼")
        await self._seed_item(item_name="蛋挞盏", station="shulong", position="馅档")
        await self._seed_rule(semi_name=ITEM_NAME)
        await self._seed_rule(semi_name="蛋挞盏")
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=10, dish_name=DISH_NAME)

        await self.service.set_staff_station(self.db, self.employee_id, "熟笼", now=TODAY_MORNING)
        result = await self.service.get_today(self.db, now=TODAY_MORNING, employee_id=self.employee_id)
        self.assertEqual({row["position"] for row in result["entries"]}, {"熟笼"})

    async def test_staff_cannot_touch_another_station(self):
        await self._seed_item(item_name="蛋挞盏", station="xibing", position="西饼")
        await self._seed_rule(semi_name="蛋挞盏")
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=10)

        await self.service.set_staff_station(self.db, self.employee_id, "熟笼", now=TODAY_MORNING)
        today = await self.service.get_today(self.db, now=TODAY_MORNING)
        entry = self._entry(today, item_name="蛋挞盏")

        with self.assertRaises(PermissionError):
            await self.service.update_entry(
                self.db, entry["id"], done=True, employee_id=self.employee_id
            )


class IntegerQtyTests(PrepPlanRebuildFixture):
    """半成品只能按整份做：参考值向上取整；原料照旧保留 1 位小数（0.5 斤称得出来）。

    烧卖算出来 58.7 份没有意义 —— 师傅要么做 58 份要么做 59 份，报「58.7 份」只是把
    计算过程漏给了看清单的人。
    """

    def test_fit_qty_rounds_made_up_and_leaves_bought_alone(self):
        self.assertEqual(fit_qty(58.7, "made"), 59.0)
        self.assertEqual(fit_qty(0.2, "made"), 1.0)
        self.assertEqual(fit_qty(0.0, "made"), 0.0)
        # 浮点误差不该白多算一份
        self.assertEqual(fit_qty(2.0000000000000004, "made"), 2.0)
        self.assertEqual(fit_qty(58.74, "bought"), 58.7)

    async def test_made_item_gets_a_whole_number(self):
        await self._seed_item()
        # 一份虾饺皇用 0.335 份馅，每天卖 100 份 → 每天 33.5 份
        await self._seed_rule(factor=0.335)
        await self._seed_stationary(morning=100, evening=0)

        payload = await self.service.get_today(self.db, now=TODAY_MORNING)
        entry = self._entry(payload)

        self.assertEqual(entry["basis"]["demand_qty"], 34)
        self.assertEqual(entry["suggested_qty"], 34)
        self.assertEqual(entry["final_qty"], 34)
        self.assertEqual(entry["suggested_qty"], int(entry["suggested_qty"]))

    async def test_bought_item_keeps_its_decimal(self):
        await self._seed_item(item_name="河粉", unit="斤", kind="bought")
        await self._seed_rule(semi_name="河粉", unit="斤", factor=0.335)
        await self._seed_stationary(morning=100, evening=0)

        payload = await self.service.get_today(self.db, now=TODAY_MORNING)
        entry = self._entry(payload, item_name="河粉")

        self.assertEqual(entry["suggested_qty"], 33.5)

    async def test_manual_qty_is_rounded_up_for_made_items(self):
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()

        payload = await self.service.get_today(self.db, now=TODAY_MORNING)
        entry = self._entry(payload)
        await self.service.update_entry(self.db, entry["id"], final_qty=2.4)

        again = await self.service.get_today(self.db, now=TODAY_MORNING)
        self.assertEqual(self._entry(again)["final_qty"], 3)

    async def test_manual_qty_keeps_the_decimal_for_bought_items(self):
        await self._seed_item(item_name="河粉", unit="斤", kind="bought")
        await self._seed_rule(semi_name="河粉", unit="斤")
        await self._seed_stationary()

        payload = await self.service.get_today(self.db, now=TODAY_MORNING)
        entry = self._entry(payload, item_name="河粉")
        await self.service.update_entry(self.db, entry["id"], final_qty=2.4)

        again = await self.service.get_today(self.db, now=TODAY_MORNING)
        self.assertEqual(self._entry(again, item_name="河粉")["final_qty"], 2.4)
