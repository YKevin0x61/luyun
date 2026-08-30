#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划：报废临期批次（服务缝）。"""

from datetime import timedelta

from services.prep_plan_service import UNDO_MOVEMENT_REASON
from tests.test_prep_plan_forecast import (
    ITEM_NAME,
    NEAR_REMAINING,
    NOW,
    UNIT,
    WINDOW_END,
    WINDOW_START,
    PrepPlanServiceFixture,
)

NEAR_EXPIRES_AT = NOW + timedelta(hours=2)


class PrepPlanDiscardBatchTests(PrepPlanServiceFixture):
    async def _insert_batch(self, prep_item_id, remaining_qty, expires_at, produced_at=WINDOW_START):
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
                    produced_at.isoformat(),
                    expires_at.isoformat(),
                    self.stamp,
                    self.stamp,
                ),
            )
            batch_id = cursor.lastrowid
        await batches.commit()
        return batch_id

    async def test_discard_batch_drops_near_expiry_available_and_keeps_window_produced(self):
        prep_item_id = await self._seed_item_and_rule()
        await self._seed_identical_history()
        batch_id = await self._insert_batch(prep_item_id, NEAR_REMAINING, NEAR_EXPIRES_AT)

        before = await self.service.compute_forecast(
            db=self.db,
            target_start=WINDOW_START.isoformat(),
            target_end=WINDOW_END.isoformat(),
            include_inventory=True,
            now=NOW,
        )
        before_item = self._board_item(before)
        self.assertEqual(before_item["available_near_expiry_qty"], 50)
        self.assertEqual(before_item["produced_qty"], 50)
        near_rows = [row for row in before["expiring"] if row["batch_id"] == batch_id]
        self.assertEqual(len(near_rows), 1)
        self.assertEqual(near_rows[0]["remaining_qty"], 50)

        discarded = await self.service.discard_batch(
            db=self.db,
            batch_id=batch_id,
            operator="后厨",
        )
        self.assertEqual(discarded["reason"], "discard")
        self.assertNotEqual(discarded["reason"], UNDO_MOVEMENT_REASON)

        after = await self.service.compute_forecast(
            db=self.db,
            target_start=WINDOW_START.isoformat(),
            target_end=WINDOW_END.isoformat(),
            include_inventory=True,
            now=NOW,
        )
        after_item = self._board_item(after)
        self.assertEqual(after_item["available_near_expiry_qty"], 0)
        self.assertEqual(after_item["available_fresh_qty"], 0)
        self.assertEqual(after_item["produced_qty"], 50)
        self.assertFalse(any(row["batch_id"] == batch_id for row in after["expiring"]))

    async def test_expired_batch_never_listed_as_near_expiry(self):
        prep_item_id = await self._seed_item_and_rule()
        await self._seed_identical_history()
        expired_id = await self._insert_batch(
            prep_item_id,
            20,
            NOW - timedelta(minutes=1),
        )

        result = await self.service.compute_forecast(
            db=self.db,
            target_start=WINDOW_START.isoformat(),
            target_end=WINDOW_END.isoformat(),
            include_inventory=True,
            now=NOW,
        )
        item = self._board_item(result)
        self.assertEqual(item["available_near_expiry_qty"], 0)
        self.assertFalse(any(row["batch_id"] == expired_id for row in result["expiring"]))
        self.assertFalse(any(row["item_name"] == ITEM_NAME for row in result["expiring"]))
