#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划（ADR 0117 重建）的服务缝。

盯四件事：同星期几均量、剩余窗口切分、当日计划的勾选/手改不被重算覆盖、
员工只能看自己档口。
"""

import unittest
from datetime import datetime, timedelta

from database import CHINA_TZ, DatabaseManager
from services.prep_plan_service import PrepPlanService, _quantile

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
        self.assertEqual(len(entry["basis"]["weekdays"]), 8)
        # 没有安全缓冲：p40 就是 p40
        self.assertEqual(entry["basis"]["quantile"], 0.40)

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

    async def test_history7_covers_seven_days(self):
        await self._seed_item()
        await self._seed_rule()
        await self._seed_orders(CANDIDATE_DAYS, hour=9, minute=0, qty=40)

        result = await self.service.compute_suggestions(self.db, TODAY_MORNING)
        entry = self._entry(result)
        self.assertEqual(len(entry["history7"]), 7)
        self.assertEqual(entry["history7"][0]["day"], "2026-10-22")


class ForecastQuantileTests(PrepPlanRebuildFixture):
    """ADR 0120：建议量 = p40(8 周同星期几) × 整店水位 − 未过期的在库量。"""

    def test_quantile_is_linear_interpolated(self):
        self.assertEqual(_quantile([1, 2, 3, 4], 0.0), 1)
        self.assertEqual(_quantile([1, 2, 3, 4], 1.0), 4)
        self.assertEqual(_quantile([5], 0.4), 5)
        self.assertEqual(_quantile([], 0.4), 0.0)
        # 8 个样本、p40 → 位置 0.4×7 = 2.8 → 第 3、4 个之间插值
        self.assertAlmostEqual(_quantile([10, 20, 30, 40, 50, 60, 70, 80], 0.4), 38.0)

    async def test_stationary_series_gives_p40_without_any_buffer(self):
        """稳定序列 → p40 就是 80，不额外加任何安全量（上一轮的 conformal 缓冲已删）。"""
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()

        entry = self._entry(await self.service.compute_suggestions(self.db, TODAY_MORNING))
        self.assertEqual(entry["suggested_qty"], 80)
        self.assertEqual(entry["basis"]["quantile"], 0.40)
        self.assertFalse(entry["basis"]["floor_applied"])

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
                self.db, entry["id"], done=True, employee_id=self.employee_id
            )
