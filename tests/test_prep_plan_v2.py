#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划 v2（ADR 0118）：产量、在库量、补货提醒、复盘、报废。"""

import unittest
from datetime import datetime, timedelta

from database import CHINA_TZ, DatabaseManager
from services.prep_plan_service import PrepPlanService

ITEM_NAME = "虾饺馅"
UNIT = "份"
DISH_NAME = "虾饺皇"
STATION = "shulong"

# 2026-10-23 是周五；候选日 = 10-16 / 10-09 / 10-02 / 09-25；昨天 = 10-22。
# 现在 10:00 → 剩余窗口 [10:00, 21:30]；「未来 1 小时」= 10:00–11:00。
TODAY = datetime(2026, 10, 23, 10, 0, tzinfo=CHINA_TZ)
YESTERDAY = "2026-10-22"
CANDIDATE_DAYS = ("2026-10-16", "2026-10-09", "2026-10-02", "2026-09-25")
SUGGESTED = 60.0  # 剩余窗口内的两段：10:30 各 20 + 12:00 各 40
NEXT_HOUR = 20.0  # 候选日 10:30 各 20


class PrepPlanV2Fixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.service = PrepPlanService()
        self.stamp = TODAY.isoformat()

    async def asyncTearDown(self):
        await self.db.close()

    async def _seed_item(self, item_name=ITEM_NAME, unit=UNIT, station=STATION):
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

    async def _seed_rule(self, semi_name=ITEM_NAME, unit=UNIT, factor=1, dish_name=DISH_NAME):
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

    async def _seed_orders(self, rows):
        orders = [
            {
                "business_flow_id": f"v2-{index}",
                "table_number": "A1",
                "dish_name": dish,
                "quantity": qty,
                "order_time": _at(day, hour),
                "station": STATION,
            }
            for index, (day, hour, qty, dish) in enumerate(rows)
        ]
        if orders:
            await self.db.orders.batch_insert_orders(orders)

    async def _seed_stationary(self):
        """8 周稳定销量（10:30 各 20、12:00 各 40）：残差恒 0、整店水位恒 1.0。

        不含昨天，好让「结转」相关用例自己决定昨天的数据。
        """
        end = TODAY.date() - timedelta(days=2)
        day = end - timedelta(days=55)
        rows = []
        while day <= end:
            rows.append((day.isoformat(), 10, 20, DISH_NAME))
            rows.append((day.isoformat(), 12, 40, DISH_NAME))
            day += timedelta(days=1)
        await self._seed_orders(rows)

    async def _bootstrap_today(self):
        """主数据 + 候选日订单（10:30 各 20、12:00 各 40），并生成今天的计划。"""
        item_id = await self._seed_item()
        await self._seed_rule()
        await self._seed_stationary()
        today = await self.service.get_today(self.db, now=TODAY)
        entry = next(row for row in today["entries"] if row["item_name"] == ITEM_NAME)
        return item_id, entry

    async def _insert_plan_entry(self, plan_day, *, produced, suggested=SUGGESTED, item_id):
        days = self.db.table("prep_plan_days")
        async with days.conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO prep_plan_days (plan_day, window_start, window_end, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (plan_day, self.stamp, self.stamp, self.stamp, self.stamp),
            )
            day_id = cursor.lastrowid
        await days.commit()

        entries = self.db.table("prep_plan_entries")
        async with entries.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_entries (
                    plan_day_id, prep_item_id, item_name, unit, station, kind,
                    suggested_qty, final_qty, qty_source, produced_qty, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'made', ?, ?, 'manual', ?, ?, ?)
                """,
                (day_id, item_id, ITEM_NAME, UNIT, STATION, suggested, suggested, produced, self.stamp, self.stamp),
            )
        await entries.commit()


def _at(date_str, hour, minute=0):
    year, month, day = (int(part) for part in date_str.split("-"))
    return datetime(year, month, day, hour, minute, tzinfo=CHINA_TZ)


class OnHandTests(PrepPlanV2Fixture):
    async def test_on_hand_is_produced_minus_sold_minus_waste(self):
        item_id, entry = await self._bootstrap_today()
        await self.service.update_entry(self.db, entry["id"], produced_qty=100, done=True)
        await self._seed_orders([("2026-10-23", 9, 40, DISH_NAME)])
        # now=TODAY：add_waste 默认取真实时钟，用例日期是固定的，不传给它会随当天日期漂
        await self.service.add_waste(self.db, item_id, 10, "excess", now=TODAY)

        on_hand = await self.service._on_hand_map(self.db, TODAY)
        self.assertEqual(on_hand.get(item_id), 50.0)

    async def test_yesterday_leftover_is_carried_over(self):
        item_id, _entry = await self._bootstrap_today()
        # 昨天做 100、卖 40、报废 10 → 昨天剩 50；今天什么也没做
        await self._insert_plan_entry(YESTERDAY, produced=100, item_id=item_id)
        await self._seed_orders([(YESTERDAY, 9, 40, DISH_NAME)])
        await self.service.add_waste(self.db, item_id, 10, "excess", now=TODAY)

        on_hand = await self.service._on_hand_map(self.db, TODAY)
        # 昨天剩 100 − 40 = 60（昨天没报损，那 10 记在今天），今天在库 60 − 10 = 50
        self.assertEqual(on_hand.get(item_id), 50.0)

    async def test_carry_over_only_looks_at_yesterday(self):
        item_id, _entry = await self._bootstrap_today()
        # 前天做 100，昨天与今天都没做也没卖 → 只结昨天，前天的不算
        await self._insert_plan_entry("2026-10-21", produced=100, item_id=item_id)
        on_hand = await self.service._on_hand_map(self.db, TODAY)
        self.assertEqual(on_hand.get(item_id), 0.0)

    async def test_on_hand_never_goes_negative(self):
        item_id, _entry = await self._bootstrap_today()
        await self._seed_orders([("2026-10-23", 9, 100, DISH_NAME)])
        on_hand = await self.service._on_hand_map(self.db, TODAY)
        self.assertEqual(on_hand.get(item_id), 0.0)


class RestockTests(PrepPlanV2Fixture):
    async def test_reminds_when_on_hand_below_next_hour(self):
        await self._bootstrap_today()
        restock = await self.service.get_restock(self.db, now=TODAY)
        self.assertEqual(len(restock["rows"]), 1)
        row = restock["rows"][0]
        self.assertEqual(row["item_name"], ITEM_NAME)
        self.assertEqual(row["on_hand"], 0.0)
        self.assertEqual(row["next_hour_qty"], NEXT_HOUR)
        self.assertEqual(row["restock_qty"], SUGGESTED)

    async def test_no_reminder_when_enough(self):
        _item_id, entry = await self._bootstrap_today()
        await self.service.update_entry(self.db, entry["id"], produced_qty=30, done=True)
        restock = await self.service.get_restock(self.db, now=TODAY)
        self.assertEqual(restock["rows"], [])

    async def test_after_close_no_reminder(self):
        await self._bootstrap_today()
        restock = await self.service.get_restock(
            self.db, now=datetime(2026, 10, 23, 22, 0, tzinfo=CHINA_TZ)
        )
        self.assertTrue(restock["closed"])
        self.assertEqual(restock["rows"], [])


class ProductionTests(PrepPlanV2Fixture):
    async def test_tick_requires_produced_qty(self):
        _item_id, entry = await self._bootstrap_today()
        with self.assertRaises(ValueError):
            await self.service.update_entry(self.db, entry["id"], done=True)

    async def test_production_accumulates(self):
        _item_id, entry = await self._bootstrap_today()
        await self.service.update_entry(self.db, entry["id"], produced_qty=80, done=True)
        await self.service.update_entry(self.db, entry["id"], produced_qty=30, done=True)

        rows = self.db.table("prep_plan_entries")
        async with rows.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT produced_qty FROM prep_plan_entries WHERE id = ?", (entry["id"],)
            )
            row = await cursor.fetchone()
        self.assertEqual(float(row["produced_qty"]), 110.0)


class ReviewTests(PrepPlanV2Fixture):
    async def test_review_reports_four_numbers(self):
        item_id, _entry = await self._bootstrap_today()
        await self._insert_plan_entry(YESTERDAY, produced=120, suggested=100, item_id=item_id)
        await self._seed_orders([(YESTERDAY, 9, 100, DISH_NAME)])

        review = await self.service.get_review(self.db, day=YESTERDAY, now=TODAY)
        row = next(r for r in review["rows"] if r["item_name"] == ITEM_NAME)
        self.assertEqual(row["suggested_qty"], 100.0)
        self.assertEqual(row["produced_qty"], 120.0)
        self.assertEqual(row["sold_qty"], 100.0)
        self.assertEqual(row["left_qty"], 20.0)
        self.assertEqual(row["delta"], 20.0)
        self.assertEqual(review["summary"]["over"], 1)


class WasteTests(PrepPlanV2Fixture):
    async def test_waste_writes_into_existing_ledger(self):
        item_id, _entry = await self._bootstrap_today()
        result = await self.service.add_waste(self.db, item_id, 5, "excess")
        self.assertTrue(result["success"])
        self.assertEqual(result["record"]["item_name"], ITEM_NAME)
        self.assertEqual(float(result["record"]["quantity"]), 5.0)

        rows = self.db.table("wastage_records")
        async with rows.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT count(*) AS n FROM wastage_records WHERE item_name = ?", (ITEM_NAME,)
            )
            row = await cursor.fetchone()
        self.assertEqual(int(row["n"]), 1)
