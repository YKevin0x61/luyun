#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划（ADR 0117 重建）的服务缝：落库、接口、员工档口。

参考值算法本身（分位 / 水位 / 分段 / 兜底）在纯模块里，测试见
tests/test_prep_plan_forecast.py；这里只盯：
当日计划的勾选/手改不被重算覆盖、快照与回填、接口透传时间跨度、员工只能看自己档口。
"""

import unittest
from datetime import datetime, timedelta

from database import CHINA_TZ, DatabaseManager
from services.prep_plan_service import PrepPlanService

ITEM_NAME = "虾饺馅"
UNIT = "份"
DISH_NAME = "虾饺皇"
STATION = "shulong"

# 2026-10-22 是周四；往前 4 个同星期几 = 10-15 / 10-08 / 10-01 / 09-24
TODAY_MORNING = datetime(2026, 10, 22, 8, 0, tzinfo=CHINA_TZ)
TODAY_NOON = datetime(2026, 10, 22, 12, 0, tzinfo=CHINA_TZ)
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

    async def _seed_rule(self, *, dish_name=DISH_NAME, semi_name=ITEM_NAME, unit=UNIT,
                         factor=1, prep_item_id=None):
        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO semi_finished_rules (
                    dish_name, semi_name, position, factor, unit, category,
                    notes, created_at, updated_at, prep_item_id
                ) VALUES (?, ?, '熟笼', ?, ?, '半成品', '', ?, ?, ?)
                """,
                (dish_name, semi_name, factor, unit, self.stamp, self.stamp, prep_item_id),
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
        """8 周稳定销量：同星期几的量恒定、整店水位恒为 1.0。

        这样断言只考落库/接口这一层，不会被水位系数干扰；算法口径见纯测。
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


class HorizonWindowTests(PrepPlanRebuildFixture):
    """时间跨度：get_today 把 horizon 透传下去，非法跨度拒绝。

    算法本身的分段、水位、兜底已在 tests/test_prep_plan_forecast.py 纯测里覆盖；
    这里只验证落库/接口这一层不丢跨度。48 小时以上的分段见纯测的
    HorizonSliceTests，不要再靠造库用例验证算法。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()

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


class IdentityMatchTests(PrepPlanRebuildFixture):
    """ADR 0141：规则有 `prep_item_id` 就按 id 对齐，品名/单位漂移不再报 missing_rule。

    这两条在旧实现里都是 missing_rule（键是规则侧 `(semi_name, unit)`，与备货品的
    `(item_name, unit)` 对不上），参考量算成 0。
    """

    async def test_renamed_rule_with_prep_item_id_still_gets_a_reference(self):
        item_id = await self._seed_item()
        # 规则的半成品名还是旧名，只靠 prep_item_id 对齐到现名主数据。
        await self._seed_rule(prep_item_id=item_id, semi_name=f"{ITEM_NAME}-旧名")
        await self._seed_stationary()

        computed = await self.service.compute_suggestions(self.db, TODAY_MORNING)
        self.assertNotIn(ITEM_NAME, computed["missing_rule"])
        entry = self._entry(computed)
        self.assertEqual(entry["basis"]["status"], "ok")
        self.assertGreater(entry["suggested_qty"], 0)

        payload = await self.service.get_today(self.db, now=TODAY_MORNING)
        entry = self._entry(payload)
        self.assertEqual(entry["basis"]["status"], "ok")
        self.assertGreater(entry["suggested_qty"], 0)

    async def test_unit_drift_still_matches_by_prep_item_id(self):
        item_id = await self._seed_item(unit=UNIT)  # 备货品按「份」
        # 规则板单位漂成「斤」：旧实现文本键对不上 → missing_rule；按 id 仍应命中。
        await self._seed_rule(prep_item_id=item_id, unit="斤")
        await self._seed_stationary()

        payload = await self.service.get_today(self.db, now=TODAY_MORNING)
        entry = self._entry(payload)
        self.assertEqual(entry["basis"]["status"], "ok")
        self.assertGreater(entry["suggested_qty"], 0)


class RenamedItemSnapshotTests(PrepPlanRebuildFixture):
    """ADR 0141：同一 id 当天改名后，快照明细并成一行；显示名取最新，hours 按时间排。"""

    async def test_renamed_item_merges_into_one_row_with_sorted_hours(self):
        # 第一次快照用「乙馅」，改名成「甲馅」；按旧文本分桶，行序里 08:00 会排在
        # 07:00 前面，hours 不再按时间递增。
        item_id = await self._seed_item(item_name="乙馅")
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_stationary()
        await self.service.write_snapshot(self.db, TODAY_MORNING)

        items = self.db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE prep_items SET item_name = ? WHERE id = ?", ("甲馅", item_id)
            )
        await items.commit()
        await self.service.write_snapshot(self.db, TODAY_MORNING + timedelta(hours=1))

        day = await self.service.get_snapshot_day(self.db, TODAY_MORNING.date().isoformat())
        self.assertEqual(len(day["items"]), 1)
        row = day["items"][0]
        self.assertEqual(row["item_name"], "甲馅")
        self.assertEqual(len(row["hours"]), 2)
        stamps = [hour["snapshot_at"] for hour in row["hours"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(len(set(stamps)), 2)
