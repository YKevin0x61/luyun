#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划（ADR 0117 重建）的服务缝。

盯四件事：同星期几均量、剩余窗口切分、当日计划的勾选/手改不被重算覆盖、
员工只能看自己档口。
"""

import unittest
from datetime import datetime, timedelta

from database import CHINA_TZ, DatabaseManager
from services.prep_plan_service import PrepPlanService, _conformal_quantile

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

    async def _seed_item(self, *, item_name=ITEM_NAME, unit=UNIT, station=STATION):
        items = self.db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_items (
                    item_name, station, position, category, unit, kind, active,
                    shelf_life_hours, lead_time_hours, min_batch_qty,
                    safety_stock_ratio, notes, created_at, updated_at
                ) VALUES (?, ?, '熟笼', '半成品', ?, 'made', 1, 24, 0, 0, 0.15, '', ?, ?)
                """,
                (item_name, station, unit, self.stamp, self.stamp),
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
        self.assertEqual(len(entry["basis"]["weekdays"]), 4)
        # 序列稳定 → 残差全 0，不该凭空加裕度
        self.assertEqual(entry["basis"]["buffer_qty"], 0)
        self.assertEqual(entry["basis"]["buffer_source"], "conformal")

    async def test_remaining_window_only_counts_the_rest_of_day(self):
        """中午 12 点看，只剩 18:30 那 40 → 建议量 40，不是全天 80。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()

        result = await self.service.compute_suggestions(self.db, TODAY_NOON)
        self.assertEqual(self._entry(result)["suggested_qty"], 40)

    async def test_closed_candidate_day_drops_out_of_the_denominator(self):
        """某个同星期几整天没单（不做生意）→ 不进分母，均量还是 80。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary(except_days=("2026-09-24",))

        result = await self.service.compute_suggestions(self.db, TODAY_MORNING)
        entry = self._entry(result)
        self.assertEqual(entry["suggested_qty"], 80)
        self.assertEqual(entry["basis"]["active_days"], 3)

    async def test_after_close_gives_zero(self):
        """打烊后没有「今天还要做多少」这回事。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)

        result = await self.service.compute_suggestions(self.db, TODAY_NIGHT)
        self.assertTrue(result["window"]["closed"])
        self.assertEqual(self._entry(result)["suggested_qty"], 0)

    async def test_item_without_recipe_is_flagged(self):
        """没有配方系数的品给 0，并标 missing_rule（页面要能解释为什么空着）。"""
        await self._seed_item(item_name="鱼皇料")
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)

        result = await self.service.compute_suggestions(self.db, TODAY_MORNING)
        entry = self._entry(result, item_name="鱼皇料")
        self.assertEqual(entry["suggested_qty"], 0)
        self.assertEqual(entry["basis"]["status"], "missing_rule")
        self.assertIn("鱼皇料", result["missing_rule"])

    async def test_history7_covers_seven_days(self):
        await self._seed_item()
        await self._seed_rule()
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)

        result = await self.service.compute_suggestions(self.db, TODAY_MORNING)
        entry = self._entry(result)
        self.assertEqual(len(entry["history7"]), 7)
        self.assertEqual(entry["history7"][0]["day"], "2026-10-22")


class ForecastBufferTests(PrepPlanRebuildFixture):
    """ADR 0119：建议量 = 同星期几均量 × 整店水位 + 该品自己的残差 conformal 分位。"""

    def test_conformal_quantile_is_the_finite_sample_order_statistic(self):
        # n=4, α=0.1 → ceil(5×0.9)=5 → 夹到 n → 取最大值
        self.assertEqual(_conformal_quantile([1, 2, 3, 4], 0.10), 4)
        # n=28, α=0.1 → 第 27 个（保证有限样本下覆盖率 ≥ 90%）
        self.assertEqual(_conformal_quantile(list(range(1, 29)), 0.10), 27)
        self.assertEqual(_conformal_quantile([7], 0.10), 7)
        self.assertEqual(_conformal_quantile([], 0.10), 0.0)
        # 均量估高了不给负缓冲
        self.assertEqual(_conformal_quantile([-9, -3, -1], 0.10), 0.0)

    async def test_cold_start_uses_proportional_buffer(self):
        """只有 4 个候选日、残差不足 14 天：按 50% 比例兜底，不硬套 conformal 分位。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)
        await self._seed_orders(CANDIDATE_DAYS, hour=18, minute=30, qty=40)

        entry = self._entry(await self.service.compute_suggestions(self.db, TODAY_MORNING))
        self.assertEqual(entry["basis"]["buffer_source"], "cold_start")
        self.assertLess(entry["basis"]["residual_days"], 14)
        self.assertEqual(entry["suggested_qty"], 120)  # 80 × 1.0 + 80 × 0.5

    async def test_store_factor_is_clipped(self):
        """近 7 天是前 21 天的 1.5 倍 → 系数夹到 1.3，不会跟着疯涨。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()
        last_week = [
            (TODAY_MORNING.date() - timedelta(days=i)).isoformat() for i in range(1, 8)
        ]
        await self._seed_orders(last_week, hour=20, minute=0, qty=80)

        entry = self._entry(await self.service.compute_suggestions(self.db, TODAY_MORNING))
        self.assertEqual(entry["basis"]["store_factor"], 1.3)
        self.assertGreaterEqual(entry["suggested_qty"], 104)


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
            self.db, entry["id"], final_qty=95, done=True, produced_qty=80
        )
        await self.service.update_entry(self.db, entry["id"], done=False)

        again = await self.service.get_today(self.db, now=TODAY_MORNING)
        refreshed = self._entry(again)
        self.assertEqual(refreshed["final_qty"], 95)
        self.assertEqual(refreshed["qty_source"], "manual")
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
        self.assertEqual(refreshed["suggested_qty"], 40)
        self.assertEqual(refreshed["final_qty"], 95)

    async def test_summary_counts_remaining_by_station(self):
        await self._seed_item()
        await self._seed_rule()
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)

        today = await self.service.get_today(self.db, now=TODAY_MORNING)
        entry = self._entry(today)
        await self.service.update_entry(self.db, entry["id"], done=True, produced_qty=10)

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
        await self._seed_item(item_name=ITEM_NAME, station="shulong")
        await self._seed_item(item_name="蛋挞盏", station="xibing")
        await self._seed_rule(semi_name=ITEM_NAME)
        await self._seed_rule(semi_name="蛋挞盏")
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=10, dish_name=DISH_NAME)

        await self.service.set_staff_station(self.db, self.employee_id, "shulong", now=TODAY_MORNING)
        result = await self.service.get_today(self.db, now=TODAY_MORNING, employee_id=self.employee_id)
        self.assertEqual({row["station"] for row in result["entries"]}, {"shulong"})

    async def test_staff_cannot_touch_another_station(self):
        await self._seed_item(item_name="蛋挞盏", station="xibing")
        await self._seed_rule(semi_name="蛋挞盏")
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=10)

        await self.service.set_staff_station(self.db, self.employee_id, "shulong", now=TODAY_MORNING)
        today = await self.service.get_today(self.db, now=TODAY_MORNING)
        entry = self._entry(today, item_name="蛋挞盏")

        with self.assertRaises(PermissionError):
            await self.service.update_entry(
                self.db, entry["id"], done=True, produced_qty=1, employee_id=self.employee_id
            )
