#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prep plan reads/writes that used to live in the route."""

from tests.test_prep_plan_forecast import NOW, PrepPlanServiceFixture


class PrepPlanQueryTests(PrepPlanServiceFixture):
    async def test_current_plan_empty(self):
        result = await self.service.get_current_plan(self.db)
        self.assertEqual(result["run"], None)
        self.assertEqual(result["items"], [])

    async def test_current_plan_returns_covering_run(self):
        runs = self.db.table("prep_plan_runs")
        items = self.db.table("prep_plan_items")
        start = NOW.replace(hour=7, minute=30).isoformat()
        end = NOW.replace(hour=21, minute=30).isoformat()
        async with runs.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_runs (
                    plan_date, target_start, target_end, method, created_by,
                    item_count, missing_rule_count, summary_json, created_at
                ) VALUES (?, ?, ?, 'weighted_history', '', 1, 0, '{}', ?)
                """,
                (NOW.strftime("%Y-%m-%d"), start, end, self.stamp),
            )
            run_id = cursor.lastrowid
        await runs.commit()
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_items (
                    run_id, item_name, station, position, unit,
                    forecast_qty, safety_qty, recommended_qty,
                    confidence, reason, created_at
                ) VALUES (?, '虾饺馅', 'shulong', '熟笼', '份', 10, 0, 10, 'none', '', ?)
                """,
                (run_id, self.stamp),
            )
        await items.commit()

        result = await self.service.get_current_plan(
            self.db, now=NOW.replace(hour=12)
        )
        self.assertEqual(result["run"]["id"], run_id)
        self.assertEqual(result["items"][0]["item_name"], "虾饺馅")

    async def test_init_items_from_rules_uses_position_station_map(self):
        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO semi_finished_rules (
                    dish_name, semi_name, position, factor, unit, category,
                    notes, created_at, updated_at
                ) VALUES ('鲜虾饺', '虾饺馅', '馅档', 1, '份', '', '', ?, ?)
                """,
                (self.stamp, self.stamp),
            )
        await rules.commit()

        result = await self.service.init_items_from_rules(self.db)
        self.assertEqual(result["created"], 1)

        items = self.db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT item_name, station, position, unit FROM prep_items"
            )
            row = dict(await cursor.fetchone())
        self.assertEqual(row["item_name"], "虾饺馅")
        self.assertEqual(row["station"], "shulong")
        self.assertEqual(row["position"], "馅档")
        self.assertEqual(row["unit"], "份")
