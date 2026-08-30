#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划：登记产量写成备货批次（服务缝）。"""

import inspect
from datetime import timedelta

from api.prep_plan import CreateBatchRequest
from database import ensure_beijing_datetime
from services.prep_plan_service import PrepPlanService
from tests.test_prep_plan_forecast import (
    ITEM_NAME,
    NOW,
    SLOT_QTY,
    UNIT,
    WINDOW_END,
    WINDOW_START,
    PrepPlanServiceFixture,
)


class PrepPlanRecordBatchTests(PrepPlanServiceFixture):
    async def test_recorded_batch_counts_as_window_produced_qty(self):
        await self._seed_item_and_rule()
        await self._seed_identical_history()

        await self.service.record_batch(
            db=self.db,
            item_name=ITEM_NAME,
            unit=UNIT,
            produced_qty=SLOT_QTY,
            produced_at=WINDOW_START.isoformat(),
            operator="后厨",
        )

        result = await self.service.compute_forecast(
            db=self.db,
            target_start=WINDOW_START.isoformat(),
            target_end=WINDOW_END.isoformat(),
            include_inventory=True,
            now=NOW,
        )
        item = self._board_item(result)
        self.assertEqual(item["produced_qty"], 40)

    async def test_record_raises_available_drops_recommended_and_sets_expiry_from_shelf_life(self):
        await self._seed_item_and_rule()
        await self._seed_identical_history()

        self.assertNotIn("expires_at", inspect.signature(PrepPlanService.record_batch).parameters)
        self.assertNotIn("expires_at", CreateBatchRequest.model_fields)

        before = await self.service.compute_forecast(
            db=self.db,
            target_start=WINDOW_START.isoformat(),
            target_end=WINDOW_END.isoformat(),
            include_inventory=True,
            now=NOW,
        )
        before_item = self._board_item(before)
        self.assertEqual(before_item["recommended_qty"], 80)
        self.assertEqual(before_item["available_fresh_qty"], 0)

        produced_at = WINDOW_START
        batch = await self.service.record_batch(
            db=self.db,
            item_name=ITEM_NAME,
            unit=UNIT,
            produced_qty=SLOT_QTY,
            produced_at=produced_at.isoformat(),
            operator="后厨",
        )
        self.assertEqual(
            ensure_beijing_datetime(batch["expires_at"]),
            produced_at + timedelta(hours=24),
        )

        after = await self.service.compute_forecast(
            db=self.db,
            target_start=WINDOW_START.isoformat(),
            target_end=WINDOW_END.isoformat(),
            include_inventory=True,
            now=NOW,
        )
        after_item = self._board_item(after)
        self.assertEqual(after_item["available_fresh_qty"], 40)
        self.assertEqual(after_item["available_near_expiry_qty"], 0)
        self.assertEqual(after_item["recommended_qty"], 40)
        self.assertEqual(after_item["produced_qty"], 40)

    async def test_record_may_exceed_recommended_qty(self):
        await self._seed_item_and_rule()
        await self._seed_identical_history()
        await self.service.record_batch(
            db=self.db,
            item_name=ITEM_NAME,
            unit=UNIT,
            produced_qty=100,
            produced_at=WINDOW_START.isoformat(),
            operator="后厨",
        )
        result = await self.service.compute_forecast(
            db=self.db,
            target_start=WINDOW_START.isoformat(),
            target_end=WINDOW_END.isoformat(),
            include_inventory=True,
            now=NOW,
        )
        item = self._board_item(result)
        self.assertEqual(item["produced_qty"], 100)
        self.assertEqual(item["recommended_qty"], 0)
        self.assertEqual(item["available_fresh_qty"], 100)

    async def test_record_rejects_non_positive_qty_and_missing_master(self):
        await self._seed_item_and_rule()
        with self.assertRaises(ValueError):
            await self.service.record_batch(
                db=self.db,
                item_name=ITEM_NAME,
                unit=UNIT,
                produced_qty=0,
            )
        with self.assertRaises(ValueError):
            await self.service.record_batch(
                db=self.db,
                item_name="没有主数据的馅",
                unit=UNIT,
                produced_qty=10,
            )
