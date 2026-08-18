#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""楼面控制台：等叫 / 叫起 / 加急 / 对调。"""

import tempfile
import unittest
from datetime import datetime, timedelta

from fastapi import HTTPException

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.floor_console import fire_portions, hold_portions, list_floor_tables, rush_portions
from services.kds_orders import complete_cooking, derive_steamer_phase, load_steamer
from services.kitchen_work import is_hold, is_pending_kitchen_work, work_enter_time


def _order(**overrides):
    row = {
        "business_flow_id": "floor-001",
        "table_number": "8",
        "dish_name": "虾饺",
        "quantity": 1,
        "order_time": datetime(2026, 8, 18, 10, 0, tzinfo=CHINA_TZ),
        "station": "shulong",
        "status": "未结",
        "source": "dine_in",
        "dish_status": "待出餐",
    }
    row.update(overrides)
    return row


async def _load(orders, order_id, *, steamer_id="1", port_index=3):
    return await load_steamer(
        orders,
        {
            "order_ids": [order_id],
            "steamer_id": steamer_id,
            "port_index": port_index,
            "loaded_at": "2026-08-18T10:05:00+08:00",
        },
    )


class FloorConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old
        self._tmpdir.cleanup()

    async def _by_flow(self):
        rows = await self.db.orders.get_orders(limit=-1)
        return {row["business_flow_id"]: row for row in rows}

    async def test_hold_unloaded_pending_is_not_kitchen_work(self):
        await self.db.orders.batch_insert_orders([_order()])
        row = (await self._by_flow())["floor-001"]
        result = await hold_portions(self.db.orders, {"order_ids": [row["_id"]]})
        self.assertTrue(result["success"])
        after = await self.db.orders.get_order_by_id(row["_id"])
        self.assertEqual(after["dish_status"], "待出餐")
        self.assertTrue(is_hold(after))
        self.assertFalse(is_pending_kitchen_work(after))
        self.assertIsNone(derive_steamer_phase(after))

    async def test_new_dine_in_pending_is_kitchen_work_until_held(self):
        await self.db.orders.batch_insert_orders([_order()])
        row = (await self._by_flow())["floor-001"]
        self.assertEqual(row["dish_status"], "待出餐")
        self.assertFalse(is_hold(row))
        self.assertTrue(is_pending_kitchen_work(row))

    async def test_hold_ready_dish_conflicts(self):
        await self.db.orders.batch_insert_orders([_order()])
        row = (await self._by_flow())["floor-001"]
        await complete_cooking(
            self.db.orders,
            {
                "dish_name": "虾饺",
                "orders": [
                    {
                        "order_id": row["_id"],
                        "table_number": "8",
                        "complete_quantity": 1,
                    }
                ],
            },
        )
        with self.assertRaises(HTTPException) as raised:
            await hold_portions(self.db.orders, {"order_ids": [row["_id"]]})
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["conflicts"][0]["reason"], "已出餐")

    async def test_steaming_hold_swaps_same_dish_awaiting_other_table(self):
        await self.db.orders.batch_insert_orders(
            [
                _order(business_flow_id="t8", table_number="8"),
                _order(business_flow_id="t9", table_number="9"),
            ]
        )
        by_flow = await self._by_flow()
        await _load(self.db.orders, by_flow["t8"]["_id"])
        result = await hold_portions(self.db.orders, {"order_ids": [by_flow["t8"]["_id"]]})
        self.assertEqual(len(result["substituted"]), 1)
        held = await self.db.orders.get_order_by_id(by_flow["t8"]["_id"])
        sub = await self.db.orders.get_order_by_id(by_flow["t9"]["_id"])
        self.assertTrue(is_hold(held))
        self.assertIsNone(held.get("placement"))
        self.assertFalse(is_hold(sub))
        self.assertEqual((sub.get("placement") or {}).get("steamer_id"), "1")
        self.assertEqual((sub.get("placement") or {}).get("port_index"), 3)
        self.assertEqual(derive_steamer_phase(sub), "在蒸")

    async def test_steaming_hold_without_substitute_conflicts_partial(self):
        await self.db.orders.batch_insert_orders(
            [
                _order(business_flow_id="await", table_number="8"),
                _order(business_flow_id="steam", table_number="8"),
            ]
        )
        by_flow = await self._by_flow()
        await _load(self.db.orders, by_flow["steam"]["_id"])
        result = await hold_portions(
            self.db.orders,
            {"order_ids": [by_flow["await"]["_id"], by_flow["steam"]["_id"]]},
        )
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["conflicts"][0]["reason"], "在蒸且无替补")
        held = await self.db.orders.get_order_by_id(by_flow["await"]["_id"])
        steaming = await self.db.orders.get_order_by_id(by_flow["steam"]["_id"])
        self.assertTrue(is_hold(held))
        self.assertFalse(is_hold(steaming))
        self.assertIsNotNone(steaming.get("placement"))

    async def test_never_held_work_enter_time_is_order_time(self):
        await self.db.orders.batch_insert_orders([_order()])
        row = (await self._by_flow())["floor-001"]
        self.assertFalse(is_hold(row))
        self.assertTrue(is_pending_kitchen_work(row))
        self.assertEqual(work_enter_time(row), row.get("order_time"))
        self.assertTrue(not row.get("fired_at"))

    async def test_fire_sets_work_enter_time_and_kitchen_work(self):
        await self.db.orders.batch_insert_orders([_order()])
        row = (await self._by_flow())["floor-001"]
        order_time = row.get("order_time")
        await hold_portions(self.db.orders, {"order_ids": [row["_id"]]})
        result = await fire_portions(self.db.orders, {"order_ids": [row["_id"]]})
        after = await self.db.orders.get_order_by_id(row["_id"])
        self.assertEqual(after["dish_status"], "待出餐")
        self.assertFalse(is_hold(after))
        self.assertTrue(is_pending_kitchen_work(after))
        self.assertIsNotNone(after.get("fired_at"))
        self.assertEqual(work_enter_time(after), after.get("fired_at"))
        self.assertNotEqual(work_enter_time(after), order_time)
        self.assertTrue(result["fired_at"])

    async def test_second_fire_uses_new_fired_at(self):
        await self.db.orders.batch_insert_orders([_order()])
        row = (await self._by_flow())["floor-001"]
        await hold_portions(self.db.orders, {"order_ids": [row["_id"]]})
        first = await fire_portions(self.db.orders, {"order_ids": [row["_id"]]})
        await hold_portions(self.db.orders, {"order_ids": [row["_id"]]})
        second = await fire_portions(self.db.orders, {"order_ids": [row["_id"]]})
        after = await self.db.orders.get_order_by_id(row["_id"])
        self.assertNotEqual(first["fired_at"], second["fired_at"])
        self.assertEqual(work_enter_time(after), after.get("fired_at"))
        self.assertEqual(after.get("fired_at").isoformat(), second["fired_at"])
        self.assertTrue(is_pending_kitchen_work(after))

    async def test_fire_partial_conflicts_non_hold(self):
        await self.db.orders.batch_insert_orders(
            [
                _order(business_flow_id="held", station="changfen"),
                _order(business_flow_id="open", station="changfen"),
            ]
        )
        by_flow = await self._by_flow()
        await hold_portions(self.db.orders, {"order_ids": [by_flow["held"]["_id"]]})
        result = await fire_portions(
            self.db.orders,
            {"order_ids": [by_flow["held"]["_id"], by_flow["open"]["_id"]]},
        )
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(
            result["conflicts"],
            [{"order_id": str(by_flow["open"]["_id"]), "reason": "不是等叫"}],
        )
        fired = await self.db.orders.get_order_by_id(by_flow["held"]["_id"])
        skipped = await self.db.orders.get_order_by_id(by_flow["open"]["_id"])
        self.assertFalse(is_hold(fired))
        self.assertTrue(is_pending_kitchen_work(fired))
        self.assertIsNotNone(fired.get("fired_at"))
        self.assertFalse(is_hold(skipped))
        self.assertTrue(not skipped.get("fired_at"))

    async def test_rush_only_unloaded_work_and_clears_on_hold(self):
        await self.db.orders.batch_insert_orders(
            [
                _order(business_flow_id="work", station="changfen"),
                _order(business_flow_id="held", station="changfen"),
            ]
        )
        by_flow = await self._by_flow()
        await hold_portions(self.db.orders, {"order_ids": [by_flow["held"]["_id"]]})
        result = await rush_portions(
            self.db.orders,
            {"order_ids": [by_flow["work"]["_id"], by_flow["held"]["_id"]]},
        )
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["conflicts"][0]["reason"], "等叫须先叫起")
        rushed = await self.db.orders.get_order_by_id(by_flow["work"]["_id"])
        self.assertTrue(rushed.get("is_rushed"))
        self.assertEqual(rushed["dish_status"], "待出餐")
        await hold_portions(self.db.orders, {"order_ids": [by_flow["work"]["_id"]]})
        after_hold = await self.db.orders.get_order_by_id(by_flow["work"]["_id"])
        self.assertFalse(after_hold.get("is_rushed"))
        self.assertEqual(after_hold["dish_status"], "待出餐")

    async def test_rush_steaming_conflicts_and_leaves_clock(self):
        await self.db.orders.batch_insert_orders([_order()])
        row = (await self._by_flow())["floor-001"]
        await _load(self.db.orders, row["_id"])
        before = await self.db.orders.get_order_by_id(row["_id"])
        loaded_at = (before.get("placement") or {}).get("loaded_at")
        self.assertEqual(loaded_at, "2026-08-18T10:05:00+08:00")
        with self.assertRaises(HTTPException) as raised:
            await rush_portions(self.db.orders, {"order_ids": [row["_id"]]})
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["conflicts"][0]["reason"], "在蒸")
        after = await self.db.orders.get_order_by_id(row["_id"])
        self.assertEqual(after["dish_status"], "待出餐")
        self.assertFalse(after.get("is_rushed"))
        self.assertEqual((after.get("placement") or {}).get("loaded_at"), loaded_at)
        self.assertEqual(derive_steamer_phase(after), "在蒸")

    async def test_complete_cooking_clears_rush(self):
        await self.db.orders.batch_insert_orders([_order(station="changfen")])
        row = (await self._by_flow())["floor-001"]
        await rush_portions(self.db.orders, {"order_ids": [row["_id"]]})
        rushed = await self.db.orders.get_order_by_id(row["_id"])
        self.assertTrue(rushed.get("is_rushed"))
        await complete_cooking(
            self.db.orders,
            {
                "dish_name": "虾饺",
                "orders": [
                    {
                        "order_id": row["_id"],
                        "table_number": "8",
                        "complete_quantity": 1,
                    }
                ],
            },
        )
        after = await self.db.orders.get_order_by_id(row["_id"])
        self.assertEqual(after["dish_status"], "已制作待上菜")
        self.assertFalse(after.get("is_rushed"))

    async def test_complete_cooking_rejects_hold(self):
        await self.db.orders.batch_insert_orders([_order(station="changfen")])
        row = (await self._by_flow())["floor-001"]
        await hold_portions(self.db.orders, {"order_ids": [row["_id"]]})
        with self.assertRaises(HTTPException) as raised:
            await complete_cooking(
                self.db.orders,
                {
                    "dish_name": "虾饺",
                    "orders": [
                        {
                            "order_id": row["_id"],
                            "table_number": "8",
                            "complete_quantity": 1,
                        }
                    ],
                },
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["conflicts"][0]["reason"], "等叫")

    async def test_list_floor_tables_skips_delivery_and_empty_pos(self):
        now = datetime.now(CHINA_TZ)
        await self.db.orders.batch_insert_orders(
            [
                _order(
                    business_flow_id="open",
                    table_number="8",
                    order_time=now,
                    station="changfen",
                ),
                _order(
                    business_flow_id="gone",
                    table_number="9",
                    order_time=now,
                    station="changfen",
                ),
                _order(
                    business_flow_id="wm",
                    table_number="W1",
                    order_time=now,
                    source="delivery",
                    station="changfen",
                ),
            ]
        )
        by_flow = await self._by_flow()
        tdb = self.db.table("orders")
        await tdb.execute(
            "UPDATE orders SET dish_status = '已制作待上菜' WHERE id = ?",
            (by_flow["gone"]["_id"],),
        )
        await tdb.commit()
        data = await list_floor_tables(
            self.db.orders,
            occupied_table_numbers=["8"],
            table_snapshot_exists=True,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
        )
        numbers = [table["table_number"] for table in data["tables"]]
        self.assertEqual(numbers, ["8"])
        self.assertEqual(data["tables"][0]["lines"][0]["phase"], "待出餐")

    async def test_hold_partial_conflicts_cooked_cancelled_already_held(self):
        await self.db.orders.batch_insert_orders(
            [
                _order(business_flow_id="ok", station="changfen"),
                _order(business_flow_id="cooked", station="changfen"),
                _order(business_flow_id="cancelled", station="changfen"),
                _order(business_flow_id="held", station="changfen"),
            ]
        )
        by_flow = await self._by_flow()
        await complete_cooking(
            self.db.orders,
            {
                "dish_name": "虾饺",
                "orders": [
                    {
                        "order_id": by_flow["cooked"]["_id"],
                        "table_number": "8",
                        "complete_quantity": 1,
                    }
                ],
            },
        )
        tdb = self.db.table("orders")
        await tdb.execute(
            "UPDATE orders SET dish_status = '已取消' WHERE id = ?",
            (by_flow["cancelled"]["_id"],),
        )
        await tdb.commit()
        await hold_portions(self.db.orders, {"order_ids": [by_flow["held"]["_id"]]})
        result = await hold_portions(
            self.db.orders,
            {
                "order_ids": [
                    by_flow["ok"]["_id"],
                    by_flow["cooked"]["_id"],
                    by_flow["cancelled"]["_id"],
                    by_flow["held"]["_id"],
                ]
            },
        )
        self.assertEqual(result["updated_count"], 1)
        reasons = {item["reason"] for item in result["conflicts"]}
        self.assertEqual(reasons, {"已出餐", "已取消", "已被等叫"})
        held_ok = await self.db.orders.get_order_by_id(by_flow["ok"]["_id"])
        self.assertEqual(held_ok["dish_status"], "待出餐")
        self.assertTrue(is_hold(held_ok))
        self.assertFalse(is_pending_kitchen_work(held_ok))

    async def test_list_floor_tables_shows_hold_phase(self):
        now = datetime.now(CHINA_TZ)
        await self.db.orders.batch_insert_orders(
            [_order(order_time=now, station="changfen")]
        )
        row = (await self._by_flow())["floor-001"]
        await hold_portions(self.db.orders, {"order_ids": [row["_id"]]})
        data = await list_floor_tables(
            self.db.orders,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
        )
        self.assertEqual(len(data["tables"]), 1)
        line = data["tables"][0]["lines"][0]
        self.assertEqual(line["phase"], "等叫")
        self.assertEqual(line["dish_status"], "待出餐")
        self.assertTrue(line["is_hold"])

    async def test_load_steamer_rejects_hold(self):
        await self.db.orders.batch_insert_orders([_order()])
        row = (await self._by_flow())["floor-001"]
        await hold_portions(self.db.orders, {"order_ids": [row["_id"]]})
        with self.assertRaises(HTTPException) as raised:
            await _load(self.db.orders, row["_id"])
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("等叫", str(raised.exception.detail))
