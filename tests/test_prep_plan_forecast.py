#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划预测：分桶建议量与可用库存分列（服务缝）。"""

import tempfile
import unittest
from datetime import datetime, timedelta

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.prep_plan_service import PrepPlanService

# Sunday; last 4 same-weekdays + last 7 days of identical slot qty → forecast = 40.
TARGET_DAY = datetime(2026, 8, 30, tzinfo=CHINA_TZ)
NOW = datetime(2026, 8, 30, 7, 0, tzinfo=CHINA_TZ)
WINDOW_START = datetime(2026, 8, 30, 7, 30, tzinfo=CHINA_TZ)
WINDOW_END = datetime(2026, 8, 30, 21, 30, tzinfo=CHINA_TZ)
MORNING_END = datetime(2026, 8, 30, 11, 0, tzinfo=CHINA_TZ)
SLOT_QTY = 40
ITEM_NAME = "虾饺馅"
UNIT = "份"
DISH_NAME = "虾饺"

# Aug 2/9/16/23 = last 4 Sundays; Aug 23–29 = last 7 days (23 overlaps).
HISTORY_DATES = (
    "2026-08-02",
    "2026-08-09",
    "2026-08-16",
    "2026-08-23",
    "2026-08-24",
    "2026-08-25",
    "2026-08-26",
    "2026-08-27",
    "2026-08-28",
    "2026-08-29",
)


def _at(date_str, hour, minute=0):
    year, month, day = (int(part) for part in date_str.split("-"))
    return datetime(year, month, day, hour, minute, tzinfo=CHINA_TZ)


class PrepPlanWindowTests(unittest.TestCase):
    """午后补货窗口的切片口径：终点撑到第二天 13:00，即午市（11:00–14:00）的三分之二。

    12:00 终点只把午市算进一小时 —— 那是缺货风险最高的一段，`_build_slots` 必须跟着窗口走。
    """

    def _next_lunch_slice(self, end):
        start = _at("2026-08-30", 16, 0)
        slots = PrepPlanService._build_slots(start, end)
        next_day = [s for s in slots if s["slot_name"] == "lunch" and s["slot_start"].day == 31]
        self.assertEqual(len(next_day), 1, slots)
        return next_day[0]

    def test_lunch_slice_ends_at_window_end_13(self):
        lunch = self._next_lunch_slice(_at("2026-08-31", 13, 0))
        self.assertEqual(lunch["slot_start"].hour, 11)
        self.assertEqual(lunch["slot_end"].hour, 13)
        self.assertEqual(lunch["slot_end"].minute, 0)

    def test_lunch_slice_covers_two_of_three_hours(self):
        lunch = self._next_lunch_slice(_at("2026-08-31", 13, 0))
        span = (lunch["slot_end"] - lunch["slot_start"]).total_seconds()
        self.assertEqual(span, 2 * 3600)

    def test_lunch_slice_still_full_when_window_reaches_14(self):
        lunch = self._next_lunch_slice(_at("2026-08-31", 14, 0))
        span = (lunch["slot_end"] - lunch["slot_start"]).total_seconds()
        self.assertEqual(span, 3 * 3600)


class PrepPlanServiceFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.service = PrepPlanService()
        self.stamp = NOW.isoformat()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old
        self._tmpdir.cleanup()

    async def _seed_item_and_rule(self, *, min_batch_qty=0):
        items = self.db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_items (
                    item_name, station, position, category, unit,
                    shelf_life_hours, lead_time_hours, min_batch_qty,
                    safety_stock_ratio, active, notes, created_at, updated_at
                ) VALUES (?, 'shulong', '熟笼', '', ?, 24, 0, ?, 0, 1, '', ?, ?)
                """,
                (ITEM_NAME, UNIT, min_batch_qty, self.stamp, self.stamp),
            )
            prep_item_id = cursor.lastrowid
        await items.commit()

        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO semi_finished_rules (
                    dish_name, semi_name, position, factor, unit, category,
                    notes, created_at, updated_at
                ) VALUES (?, ?, '熟笼', 1, ?, '', '', ?, ?)
                """,
                (DISH_NAME, ITEM_NAME, UNIT, self.stamp, self.stamp),
            )
        await rules.commit()
        return prep_item_id

    async def _seed_identical_history(self, *, morning=True, dinner=True):
        orders = []
        seq = 0
        for date_str in HISTORY_DATES:
            if morning:
                seq += 1
                orders.append(
                    {
                        "business_flow_id": f"hist-m-{seq}",
                        "table_number": "A1",
                        "dish_name": DISH_NAME,
                        "quantity": SLOT_QTY,
                        "order_time": _at(date_str, 9),
                        "station": "shulong",
                    }
                )
            if dinner:
                seq += 1
                orders.append(
                    {
                        "business_flow_id": f"hist-d-{seq}",
                        "table_number": "A1",
                        "dish_name": DISH_NAME,
                        "quantity": SLOT_QTY,
                        "order_time": _at(date_str, 18, 30),
                        "station": "shulong",
                    }
                )
        result = await self.db.orders.batch_insert_orders(orders)
        self.assertEqual(result["inserted_count"], len(orders))

    async def _mark_closed(self, day):
        """把某个营业日标成歇业（prep_closed_days，迁移 0025）。"""
        tdb = self.db.table("prep_closed_days")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_closed_days (day, note, created_at, updated_at)
                VALUES (?, '', ?, ?)
                """,
                (day, self.stamp, self.stamp),
            )
        await tdb.commit()

    async def _forecast(self, start=WINDOW_START, end=WINDOW_END):
        return await self.service.compute_forecast(
            db=self.db,
            target_start=start.isoformat(),
            target_end=end.isoformat(),
            now=NOW,
        )

    def _board_item(self, result):
        matches = [row for row in result["items"] if row["item_name"] == ITEM_NAME]
        self.assertEqual(len(matches), 1)
        return matches[0]


class PrepPlanForecastCoverageTests(PrepPlanServiceFixture):
    async def test_closed_days_crud(self):
        """歇业日的增删查：同一天重复标只更新说明（幂等），取消之后就没有了。"""
        self.assertEqual((await self.service.list_closed_days(self.db))["days"], [])

        added = await self.service.add_closed_day(self.db, "2026-10-17", "春节")
        self.assertTrue(added["created"])
        again = await self.service.add_closed_day(self.db, "2026-10-17", "改过的说明")
        self.assertFalse(again["created"])

        rows = (await self.service.list_closed_days(self.db))["days"]
        self.assertEqual(rows, [{"day": "2026-10-17", "note": "改过的说明"}])

        await self.service.remove_closed_day(self.db, "2026-10-17")
        self.assertEqual((await self.service.list_closed_days(self.db))["days"], [])

    async def test_closed_day_rejects_a_bad_date(self):
        """日期格式不对要抛 ValueError（路由转 400），别把脏值写进日历。"""
        with self.assertRaises(ValueError):
            await self.service.add_closed_day(self.db, "不是日期")

    async def test_closed_day_returns_no_advice_instead_of_a_list(self):
        """整段落在歇业日里时不给清单。

        实测（2026-10-14，真库）：国庆当天店里整天没有一条订单，预测器却算出
        「52 个品、建议量合计 1311」，金莎的信心还是 high —— 师傅照做就是报废。
        所以这里返回 `closed=True` 加一句原因，而不是一份看着可信的清单。
        """
        await self._seed_item_and_rule()
        await self._seed_identical_history()
        await self._mark_closed("2026-08-30")

        result = await self._forecast()

        self.assertTrue(result["closed"])
        self.assertEqual(result["items"], [])
        self.assertIn("歇业日", result["closed_reason"])
        self.assertEqual(result["summary"]["todo_count"], 0)

    async def test_closed_day_slices_drop_out_of_a_cross_day_window(self):
        """跨天窗口里，歇业那天的时段不进建议，其余照常。

        只做「整段歇业」那一条的话，跨天窗口会把歇业日也一起算进去 ——
        「今天 14:00 → 明天 13:00」这种窗口正好跨两天。
        """
        await self._seed_item_and_rule()
        await self._seed_identical_history()
        await self._mark_closed("2026-08-31")

        result = await self._forecast(start=WINDOW_START, end=_at("2026-08-31", 21, 30))
        item = self._board_item(result)

        self.assertFalse(result.get("closed"))
        self.assertEqual(
            {slot["slot_start"].strftime("%Y-%m-%d") for slot in item["slots"]},
            {"2026-08-30"},
        )
    async def test_zero_inventory_baseline_and_no_inventory_fields(self):
        """0 库存口径（ADR 0111）：建议制作量 = 预测 + 安全量，返回里不再有库存字段。

        这里原本有三条批次用例（临期只盖到期前的时段、过期余量不算可用）。
        批次模型整体退场后那几条路不存在了 —— 改成锁住新口径：库存相关的字段
        **一个都不返回**，免得哪天有人手滑把它们加回来。
        """
        await self._seed_item_and_rule()
        await self._seed_identical_history()

        result = await self._forecast()
        item = self._board_item(result)

        self.assertEqual(item["recommended_qty"], 80)
        for gone in (
            "available_qty",
            "available_fresh_qty",
            "available_near_expiry_qty",
            "produced_qty",
            "undo_batch_id",
            "batches",
            "risk_level",
            "can_record",
        ):
            self.assertNotIn(gone, item)
        self.assertNotIn("expiring", result)
        for gone in ("high_risk_count", "expiry_risk_count", "waste_risk_count"):
            self.assertNotIn(gone, result["summary"])

    async def test_safety_ratio_follows_each_items_own_volatility(self):
        """ADR 0110：波动不同的备货品，在同一窗口拿到不同的有效安全系数。

        三个品的日总量分别恒定 / 轻微起伏 / 忽高忽低，全店预算不变 ——
        稳的备得少、乱的备得多。品数不足三件时不会分档（见策略层用例）。
        """
        specs = [
            ("稳品", "菜稳", lambda i: 80),
            ("中品", "菜中", lambda i: 80 if i % 2 == 0 else 120),
            ("乱品", "菜乱", lambda i: 10 if i % 2 == 0 else 400),
        ]
        items = self.db.table("prep_items")
        rules = self.db.table("semi_finished_rules")
        orders = []
        for idx, (item_name, dish_name, shape) in enumerate(specs):
            async with items.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO prep_items (
                        item_name, station, position, category, unit,
                        shelf_life_hours, lead_time_hours, min_batch_qty,
                        safety_stock_ratio, active, notes, created_at, updated_at
                    ) VALUES (?, 'shulong', '熟笼', '', '份', 24, 0, 0, 0.15, 1, '', ?, ?)
                    """,
                    (item_name, self.stamp, self.stamp),
                )
            await items.commit()
            async with rules.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO semi_finished_rules (
                        dish_name, semi_name, position, factor, unit, category,
                        notes, created_at, updated_at
                    ) VALUES (?, ?, '熟笼', 1, '份', '', '', ?, ?)
                    """,
                    (dish_name, item_name, self.stamp, self.stamp),
                )
            await rules.commit()

            for i in range(30):
                orders.append(
                    {
                        "business_flow_id": f"vol-{idx}-{i}",
                        "table_number": "A1",
                        "dish_name": dish_name,
                        "quantity": shape(i),
                        "order_time": _at(f"2026-07-{i + 1:02d}", 18, 30),
                        "station": "shulong",
                    }
                )
        inserted = await self.db.orders.batch_insert_orders(orders)
        self.assertEqual(inserted["inserted_count"], len(orders))

        result = await self._forecast()
        by_name = {row["item_name"]: row for row in result["items"]}
        self.assertEqual(set(by_name), {"稳品", "中品", "乱品"})

        steady = by_name["稳品"]["effective_safety_ratio"]
        middling = by_name["中品"]["effective_safety_ratio"]
        wild = by_name["乱品"]["effective_safety_ratio"]
        self.assertLess(steady, middling)
        self.assertLess(middling, wild)
        self.assertAlmostEqual(steady, 0.22, places=4)
        self.assertAlmostEqual(middling, 0.31, places=4)
        self.assertAlmostEqual(wild, 0.40, places=4)

    async def test_missing_rule_dish_does_not_enter_execute_board(self):
        await self._seed_item_and_rule()
        await self._seed_identical_history()
        extra = await self.db.orders.batch_insert_orders(
            [
                {
                    "business_flow_id": "no-rule-1",
                    "table_number": "A2",
                    "dish_name": "无规则菜",
                    "quantity": 10,
                    "order_time": _at("2026-08-23", 9),
                    "station": "shulong",
                }
            ]
        )
        self.assertEqual(extra["inserted_count"], 1)

        result = await self._forecast()
        self.assertTrue(any(row["item_name"] == ITEM_NAME for row in result["items"]))
        self.assertFalse(any(row["item_name"] == "无规则菜" for row in result["items"]))
        self.assertIn("无规则菜", [row["dish_name"] for row in result["missing_rules"]])

    async def test_low_sample_item_stays_on_execute_board(self):
        await self._seed_item_and_rule()
        seeded = await self.db.orders.batch_insert_orders(
            [
                {
                    "business_flow_id": "low-sample-1",
                    "table_number": "A1",
                    "dish_name": DISH_NAME,
                    "quantity": SLOT_QTY,
                    "order_time": _at("2026-08-29", 9),
                    "station": "shulong",
                }
            ]
        )
        self.assertEqual(seeded["inserted_count"], 1)

        result = await self._forecast()
        item = self._board_item(result)
        self.assertEqual(item["confidence"], "low")
        self.assertGreater(item["recommended_qty"], 0)

    async def test_min_batch_lifts_when_gap_is_positive(self):
        await self._seed_item_and_rule(min_batch_qty=100)
        await self._seed_identical_history()

        result = await self._forecast()
        item = self._board_item(result)
        self.assertEqual(item["recommended_qty"], 100)
        self.assertTrue(item["min_batch_applied"])

    async def test_target_day_orders_do_not_affect_forecast_or_leak(self):
        await self._seed_item_and_rule()
        await self._seed_identical_history()

        # 在目标日 (2026-08-30) 插入海量订单，这绝不能泄漏进历史并改变预测需求
        extra = await self.db.orders.batch_insert_orders(
            [
                {
                    "business_flow_id": "target-day-leak-1",
                    "table_number": "A99",
                    "dish_name": DISH_NAME,
                    "quantity": 9999,
                    "order_time": _at("2026-08-30", 12),
                    "station": "shulong",
                }
            ]
        )
        self.assertEqual(extra["inserted_count"], 1)

        result = await self._forecast()
        item = self._board_item(result)
        # 预测需求依然保持历史正常值（40 + 40 = 80），不受目标日 9999 订单影响
        self.assertEqual(item["forecast_qty"], 80)

    async def test_target_day_only_dish_never_enters_forecast_items(self):
        # 规则配置了“新鲜菠萝包”
        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO semi_finished_rules (
                    dish_name, semi_name, position, factor, unit, category,
                    notes, created_at, updated_at
                ) VALUES ('菠萝包', '菠萝面团', '西饼', 1, '个', '', '', ?, ?)
                """,
                (self.stamp, self.stamp),
            )
        await rules.commit()

        # 仅在目标日当天 (2026-08-30) 下单了菠萝包，历史从未下过
        extra = await self.db.orders.batch_insert_orders(
            [
                {
                    "business_flow_id": "target-day-first-time",
                    "table_number": "B1",
                    "dish_name": "菠萝包",
                    "quantity": 50,
                    "order_time": _at("2026-08-30", 10),
                    "station": "xibing",
                }
            ]
        )
        self.assertEqual(extra["inserted_count"], 1)

        result = await self._forecast()
        # 菠萝面团绝不能出现在预测列表中
        item_names = [row["item_name"] for row in result["items"]]
        self.assertNotIn("菠萝面团", item_names)

    async def test_effective_safety_ratio_respects_manual_floor_and_caps_at_40(self):
        # 种子品配置超大安全库存比例 0.80 (80%)
        items = self.db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_items (
                    item_name, station, position, category, unit,
                    shelf_life_hours, lead_time_hours, min_batch_qty,
                    safety_stock_ratio, active, notes, created_at, updated_at
                ) VALUES ('超大安全品', 'shulong', '熟笼', '', '份', 24, 0, 0, 0.80, 1, '', ?, ?)
                """,
                (self.stamp, self.stamp),
            )
        await items.commit()

        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO semi_finished_rules (
                    dish_name, semi_name, position, factor, unit, category,
                    notes, created_at, updated_at
                ) VALUES ('超大菜', '超大安全品', '熟笼', 1, '份', '', '', ?, ?)
                """,
                (self.stamp, self.stamp),
            )
        await rules.commit()

        await self.db.orders.batch_insert_orders(
            [
                {
                    "business_flow_id": "huge-safety-1",
                    "table_number": "A1",
                    "dish_name": "超大菜",
                    "quantity": 100,
                    "order_time": _at("2026-08-29", 9),
                    "station": "shulong",
                }
            ]
        )

        result = await self._forecast()
        matches = [row for row in result["items"] if row["item_name"] == "超大安全品"]
        self.assertEqual(len(matches), 1)
        item = matches[0]
        # 单品配置为 0.80，但系统强制上限为 0.40
        self.assertEqual(item["effective_safety_ratio"], 0.40)

