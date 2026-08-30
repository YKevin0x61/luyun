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
NEAR_REMAINING = 50
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


class PrepPlanForecastCoverageTests(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_item_and_rule(self):
        items = self.db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_items (
                    item_name, station, position, category, unit,
                    shelf_life_hours, lead_time_hours, min_batch_qty,
                    safety_stock_ratio, active, notes, created_at, updated_at
                ) VALUES (?, 'shulong', '熟笼', '', ?, 24, 0, 0, 0, 1, '', ?, ?)
                """,
                (ITEM_NAME, UNIT, self.stamp, self.stamp),
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

    async def _seed_batch(self, prep_item_id, remaining_qty, expires_at):
        batches = self.db.table("prep_batches")
        async with batches.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_batches (
                    prep_item_id, item_name, produced_qty, remaining_qty, unit,
                    produced_at, expires_at, status, operator, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', '', '', ?, ?)
                """,
                (
                    prep_item_id,
                    ITEM_NAME,
                    remaining_qty,
                    remaining_qty,
                    UNIT,
                    self.stamp,
                    expires_at.isoformat(),
                    self.stamp,
                    self.stamp,
                ),
            )
        await batches.commit()

    async def _forecast(self, start=WINDOW_START, end=WINDOW_END):
        return await self.service.compute_forecast(
            db=self.db,
            target_start=start.isoformat(),
            target_end=end.isoformat(),
            include_inventory=True,
            now=NOW,
        )

    def _board_item(self, result):
        matches = [row for row in result["items"] if row["item_name"] == ITEM_NAME]
        self.assertEqual(len(matches), 1)
        return matches[0]

    async def test_near_expiry_batch_covering_morning_does_not_cover_dinner(self):
        prep_item_id = await self._seed_item_and_rule()
        await self._seed_identical_history()
        await self._seed_batch(prep_item_id, NEAR_REMAINING, MORNING_END)

        result = await self._forecast()
        item = self._board_item(result)

        self.assertEqual(item["recommended_qty"], 40)
        self.assertEqual(item["available_near_expiry_qty"], 50)
        self.assertEqual(item["available_fresh_qty"], 0)
        self.assertEqual(item["confidence"], "high")
        self.assertIn(item["risk_level"], ("high", "medium", "low", "waste_risk", "normal"))

    async def test_batch_expiring_at_slot_end_can_cover_that_slot(self):
        prep_item_id = await self._seed_item_and_rule()
        await self._seed_identical_history()
        await self._seed_batch(prep_item_id, SLOT_QTY, MORNING_END)

        result = await self._forecast(start=WINDOW_START, end=MORNING_END)
        item = self._board_item(result)

        self.assertEqual(item["recommended_qty"], 0)
        self.assertEqual(item["available_near_expiry_qty"], 40)
        self.assertEqual(item["available_fresh_qty"], 0)

    async def test_expired_remaining_is_not_available(self):
        prep_item_id = await self._seed_item_and_rule()
        await self._seed_identical_history()
        await self._seed_batch(prep_item_id, 20, NOW - timedelta(minutes=1))

        result = await self._forecast()
        item = self._board_item(result)

        self.assertEqual(item["available_fresh_qty"], 0)
        self.assertEqual(item["available_near_expiry_qty"], 0)
        self.assertEqual(item["recommended_qty"], 80)

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
