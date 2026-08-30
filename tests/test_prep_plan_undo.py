#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划：撤销登记（服务缝）。"""

from services.prep_plan_service import UNDO_MOVEMENT_REASON
from tests.test_prep_plan_forecast import (
    ITEM_NAME,
    NOW,
    SLOT_QTY,
    UNIT,
    WINDOW_END,
    WINDOW_START,
    PrepPlanServiceFixture,
)


class PrepPlanUndoRecordTests(PrepPlanServiceFixture):
    async def test_undo_record_drops_produced_available_and_restores_recommended(self):
        await self._seed_item_and_rule()
        await self._seed_identical_history()

        batch = await self.service.record_batch(
            db=self.db,
            item_name=ITEM_NAME,
            unit=UNIT,
            produced_qty=SLOT_QTY,
            produced_at=WINDOW_START.isoformat(),
            operator="后厨",
        )

        undone = await self.service.undo_record(
            db=self.db,
            batch_id=batch["batch_id"],
            operator="后厨",
        )
        self.assertEqual(undone["reason"], UNDO_MOVEMENT_REASON)
        self.assertNotEqual(undone["reason"], "discard")

        result = await self.service.compute_forecast(
            db=self.db,
            target_start=WINDOW_START.isoformat(),
            target_end=WINDOW_END.isoformat(),
            include_inventory=True,
            now=NOW,
        )
        item = self._board_item(result)
        self.assertEqual(item["produced_qty"], 0)
        self.assertEqual(item["available_fresh_qty"], 0)
        self.assertEqual(item["available_near_expiry_qty"], 0)
        self.assertEqual(item["recommended_qty"], 80)

    async def test_undo_reason_differs_from_discard_and_stays_out_of_later_forecast(self):
        await self._seed_item_and_rule()
        await self._seed_identical_history()

        undo_batch = await self.service.record_batch(
            db=self.db,
            item_name=ITEM_NAME,
            unit=UNIT,
            produced_qty=SLOT_QTY,
            produced_at=WINDOW_START.isoformat(),
            operator="后厨",
        )
        discarded_batch = await self.service.record_batch(
            db=self.db,
            item_name=ITEM_NAME,
            unit=UNIT,
            produced_qty=SLOT_QTY,
            produced_at=WINDOW_START.isoformat(),
            operator="后厨",
        )

        undone = await self.service.undo_record(
            db=self.db,
            batch_id=undo_batch["batch_id"],
            operator="后厨",
        )
        discarded = await self.service.retire_batch(
            db=self.db,
            batch_id=discarded_batch["batch_id"],
            reason="discard",
            operator="后厨",
        )
        self.assertEqual(undone["reason"], UNDO_MOVEMENT_REASON)
        self.assertEqual(discarded["reason"], "discard")
        self.assertNotEqual(undone["reason"], discarded["reason"])

        first = await self.service.compute_forecast(
            db=self.db,
            target_start=WINDOW_START.isoformat(),
            target_end=WINDOW_END.isoformat(),
            include_inventory=True,
            now=NOW,
        )
        first_item = self._board_item(first)
        self.assertEqual(first_item["produced_qty"], 40)
        self.assertEqual(first_item["available_fresh_qty"], 0)
        self.assertEqual(first_item["available_near_expiry_qty"], 0)

        second = await self.service.compute_forecast(
            db=self.db,
            target_start=WINDOW_START.isoformat(),
            target_end=WINDOW_END.isoformat(),
            include_inventory=True,
            now=NOW,
        )
        second_item = self._board_item(second)
        self.assertEqual(second_item["produced_qty"], 40)
        self.assertEqual(second_item["available_fresh_qty"], 0)
        self.assertEqual(second_item["available_near_expiry_qty"], 0)
        self.assertEqual(second_item["recommended_qty"], 80)
