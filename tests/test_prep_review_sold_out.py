#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""售罄推算（票 05 / spec §3 决策 2/13/22、§5「事件」）。

验的是「某个品当天累计卖出首次达到 07:00 主口径建议量的时刻」有没有被算对：

- 跨线发生在两条订单之间时，按累计量线性插值到分钟，且不晚于跨线那一单；
- 没卖到建议量、没有 07:00 快照的品都不写事件（不猜）；
- 同键只留最早一条，重复执行不新增、也不把已有时间改晚。

事件表的列就是复盘页的读取契约，所以这里直接查 `prep_plan_events`。
"""

import unittest
from datetime import datetime, timedelta

from database import CHINA_TZ, DatabaseManager
from services.prep_review import events as prep_review_events
from services.prep_review import sold_out

ITEM_NAME = "虾饺馅"
UNIT = "份"
STATION = "shulong"
POSITION = "熟笼"
PLAN_DAY = "2026-10-20"


def _at(day, hour, minute=0):
    year, month, date = (int(part) for part in day.split("-"))
    return datetime(year, month, date, hour, minute, tzinfo=CHINA_TZ)


class SoldOutFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 全量回填的门闩是进程级全局；用例之间必须归零，否则先跑的用例
        # 会把后跑的「90 天扫描」挡掉。
        sold_out._last_full_run_day = None
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.stamp = datetime.now(CHINA_TZ).isoformat()

    async def asyncTearDown(self):
        await self.db.close()

    async def _seed_item(self, *, item_name=ITEM_NAME):
        items = self.db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_items (
                    item_name, station, position, category, unit, kind, active,
                    shelf_life_hours, lead_time_hours, min_batch_qty,
                    safety_stock_ratio, notes, created_at, updated_at
                ) VALUES (?, ?, ?, '半成品', ?, 'made', 1, 24, 0, 0, 0.15, '', ?, ?)
                """,
                (item_name, STATION, POSITION, UNIT, self.stamp, self.stamp),
            )
            item_id = cursor.lastrowid
        await items.commit()
        return item_id

    async def _seed_rule(self, item_id, *, item_name=ITEM_NAME, factor=1.0):
        dish_name = f"{item_name}菜"
        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO semi_finished_rules (
                    dish_name, semi_name, position, factor, unit, category,
                    notes, created_at, updated_at, prep_item_id
                ) VALUES (?, ?, ?, ?, ?, '半成品', '', ?, ?, ?)
                """,
                (dish_name, item_name, POSITION, factor, UNIT, self.stamp, self.stamp, item_id),
            )
        await rules.commit()
        return dish_name

    async def _seed_order(self, flow_id, at, qty, *, dish_name, status="已结"):
        result = await self.db.orders.batch_insert_orders(
            [
                {
                    "business_flow_id": flow_id,
                    "table_number": "A1",
                    "dish_name": dish_name,
                    "quantity": qty,
                    "order_time": at,
                    "station": STATION,
                    "source": "dine_in",
                    "status": status,
                }
            ]
        )
        self.assertEqual(result["inserted_count"], 1)

    async def _seed_snapshot(
        self,
        plan_day,
        item_id,
        *,
        item_name=ITEM_NAME,
        suggested=0.0,
        final_qty=None,
        clock_hour=7,
        with_window=True,
    ):
        snapshot_at = _at(plan_day, clock_hour)
        snapshots = self.db.table("prep_plan_snapshots")
        async with snapshots.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_snapshots (
                    snapshot_at, window_start, window_end, plan_day,
                    prep_item_id, item_name, unit, station,
                    suggested_qty, final_qty, demand_qty, store_factor, quantile,
                    basis_json, rules_fingerprint, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0.5, '{}', 'fp', ?)
                """,
                (
                    snapshot_at.isoformat(),
                    snapshot_at.isoformat() if with_window else "",
                    (snapshot_at + timedelta(hours=24)).isoformat() if with_window else "",
                    plan_day,
                    item_id,
                    item_name,
                    UNIT,
                    STATION,
                    suggested,
                    final_qty,
                    suggested,
                    self.stamp,
                ),
            )
        await snapshots.commit()

    async def _events(self, *, kind=None):
        tdb = self.db.table("prep_plan_events")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute("SELECT * FROM prep_plan_events ORDER BY id")
            rows = await cursor.fetchall()
        events = [dict(row) for row in rows]
        if kind is not None:
            events = [event for event in events if event["kind"] == kind]
        return events


class InferenceTests(SoldOutFixture):
    async def test_sold_out_interpolates_between_orders(self):
        """建议 10、11:20 累计 10.5（11:00 已卖 9.5）→ 售罄在 11:10。

        `orders.quantity` 是 BIGINT，订单份数只能是整数；0.5 份由配方系数
        0.5 折算出来（票面口径就是「数量 × 配方系数」）。
        """
        item_id = await self._seed_item()
        dish_name = await self._seed_rule(item_id, factor=0.5)
        await self._seed_snapshot(PLAN_DAY, item_id, suggested=10)
        await self._seed_order("sold-out-1", _at(PLAN_DAY, 11, 0), 19, dish_name=dish_name)
        await self._seed_order("sold-out-2", _at(PLAN_DAY, 11, 20), 2, dish_name=dish_name)

        result = await sold_out.infer_sold_out(self.db, PLAN_DAY)
        self.assertTrue(result["success"])

        events = await self._events(kind=prep_review_events.SOLD_OUT)
        self.assertEqual(len(events), 1, events)
        event = events[0]
        self.assertEqual(event["plan_day"], PLAN_DAY)
        self.assertEqual(event["prep_item_id"], item_id)
        self.assertEqual(datetime.fromisoformat(event["at"]), _at(PLAN_DAY, 11, 10))
        self.assertGreaterEqual(datetime.fromisoformat(event["at"]), _at(PLAN_DAY, 11, 0))
        self.assertLessEqual(datetime.fromisoformat(event["at"]), _at(PLAN_DAY, 11, 20))

    async def test_no_event_when_below_suggested(self):
        """没卖到建议量 → 不写事件（不猜）。"""
        item_id = await self._seed_item()
        dish_name = await self._seed_rule(item_id)
        await self._seed_snapshot(PLAN_DAY, item_id, suggested=10)
        await self._seed_order("below-1", _at(PLAN_DAY, 11, 0), 4, dish_name=dish_name)
        await self._seed_order("below-2", _at(PLAN_DAY, 12, 0), 5, dish_name=dish_name)

        await sold_out.infer_sold_out(self.db, PLAN_DAY)

        self.assertEqual(await self._events(kind=prep_review_events.SOLD_OUT), [])

    async def test_skips_item_without_0700_snapshot(self):
        """只有非 07:00 的快照 → 不是主口径，跳过。"""
        item_id = await self._seed_item()
        dish_name = await self._seed_rule(item_id)
        await self._seed_snapshot(PLAN_DAY, item_id, suggested=1, clock_hour=12)
        await self._seed_order("noon-1", _at(PLAN_DAY, 13, 0), 5, dish_name=dish_name)

        await sold_out.infer_sold_out(self.db, PLAN_DAY)

        self.assertEqual(await self._events(kind=prep_review_events.SOLD_OUT), [])

    async def test_snapshot_without_window_falls_back_to_review_day(self):
        """快照缺窗口 → 按 07:00 复盘日 `[07:00, 次日 07:00)` 兜底，不再是自然日。

        `prep_plan_snapshots.window_start/window_end` 在 schema 里是 NOT NULL（0034），
        所以「缺窗口」在库里只能是空串 / 坏值，NULL 进不来；空串与 NULL 走的是
        `_to_china` 的同一个 None 分支。

        造数：建议 5；06:30 卖 2（复盘日窗口之外）、09:00 卖 2、10:00 卖 3。
        按 07:00 窗口算，10:00 累计 2→5 跨线，售罄就是 10:00；
        若退回自然日 00:00，06:30 那 2 份会算进来，09:00 累计就到 4，
        跨线插值成 09:20 —— 两个时刻可区分。
        """
        item_id = await self._seed_item()
        dish_name = await self._seed_rule(item_id)
        await self._seed_snapshot(PLAN_DAY, item_id, suggested=5, with_window=False)
        await self._seed_order("nowin-1", _at(PLAN_DAY, 6, 30), 2, dish_name=dish_name)
        await self._seed_order("nowin-2", _at(PLAN_DAY, 9, 0), 2, dish_name=dish_name)
        await self._seed_order("nowin-3", _at(PLAN_DAY, 10, 0), 3, dish_name=dish_name)

        await sold_out.infer_sold_out(self.db, PLAN_DAY)

        events = await self._events(kind=prep_review_events.SOLD_OUT)
        self.assertEqual(len(events), 1, events)
        self.assertEqual(datetime.fromisoformat(events[0]["at"]), _at(PLAN_DAY, 10, 0))

    async def test_snapshot_without_orders_writes_nothing(self):
        """07:00 快照有、当天零订单 → 没跨线，无事件。"""
        item_id = await self._seed_item()
        await self._seed_snapshot(PLAN_DAY, item_id, suggested=1)

        await sold_out.infer_sold_out(self.db, PLAN_DAY)

        self.assertEqual(await self._events(kind=prep_review_events.SOLD_OUT), [])

    async def test_first_order_already_exceeds(self):
        """第一条贡献订单就超量 → 取本单时间，不向前插值。"""
        item_id = await self._seed_item()
        dish_name = await self._seed_rule(item_id)
        await self._seed_snapshot(PLAN_DAY, item_id, suggested=2)
        await self._seed_order("first-1", _at(PLAN_DAY, 9, 30), 5, dish_name=dish_name)

        await sold_out.infer_sold_out(self.db, PLAN_DAY)

        events = await self._events(kind=prep_review_events.SOLD_OUT)
        self.assertEqual(len(events), 1, events)
        self.assertEqual(datetime.fromisoformat(events[0]["at"]), _at(PLAN_DAY, 9, 30))

    async def test_multiple_items_do_not_interfere(self):
        """同一天多个品各自算各自的跨线时刻。"""
        item_a = await self._seed_item(item_name="虾饺馅")
        dish_a = await self._seed_rule(item_a, item_name="虾饺馅")
        item_b = await self._seed_item(item_name="烧卖馅")
        dish_b = await self._seed_rule(item_b, item_name="烧卖馅")
        await self._seed_snapshot(PLAN_DAY, item_a, item_name="虾饺馅", suggested=2)
        await self._seed_snapshot(PLAN_DAY, item_b, item_name="烧卖馅", suggested=3)

        await self._seed_order("multi-a1", _at(PLAN_DAY, 10, 0), 1, dish_name=dish_a)
        await self._seed_order("multi-b1", _at(PLAN_DAY, 9, 0), 2, dish_name=dish_b)
        await self._seed_order("multi-b2", _at(PLAN_DAY, 11, 0), 2, dish_name=dish_b)
        await self._seed_order("multi-a2", _at(PLAN_DAY, 12, 0), 1, dish_name=dish_a)

        await sold_out.infer_sold_out(self.db, PLAN_DAY)

        events = await self._events(kind=prep_review_events.SOLD_OUT)
        by_item = {event["prep_item_id"]: datetime.fromisoformat(event["at"]) for event in events}
        self.assertEqual(by_item, {
            item_a: _at(PLAN_DAY, 12, 0),
            item_b: _at(PLAN_DAY, 10, 0),
        })

    async def test_final_qty_takes_priority_over_suggested(self):
        """建议 10 但师傅改成 2 → 按 2 算，10 点就售罄，而不是等到卖满 10。"""
        item_id = await self._seed_item()
        dish_name = await self._seed_rule(item_id)
        await self._seed_snapshot(PLAN_DAY, item_id, suggested=10, final_qty=2)
        await self._seed_order("final-1", _at(PLAN_DAY, 9, 0), 1, dish_name=dish_name)
        await self._seed_order("final-2", _at(PLAN_DAY, 10, 0), 1, dish_name=dish_name)
        await self._seed_order("final-3", _at(PLAN_DAY, 11, 0), 5, dish_name=dish_name)

        await sold_out.infer_sold_out(self.db, PLAN_DAY)

        events = await self._events(kind=prep_review_events.SOLD_OUT)
        self.assertEqual(len(events), 1, events)
        self.assertEqual(datetime.fromisoformat(events[0]["at"]), _at(PLAN_DAY, 10, 0))

    async def test_repeat_run_is_idempotent(self):
        """重复执行不产生第二条，at 也不变。"""
        item_id = await self._seed_item()
        dish_name = await self._seed_rule(item_id, factor=0.5)
        await self._seed_snapshot(PLAN_DAY, item_id, suggested=10)
        await self._seed_order("idem-1", _at(PLAN_DAY, 11, 0), 19, dish_name=dish_name)
        await self._seed_order("idem-2", _at(PLAN_DAY, 11, 20), 2, dish_name=dish_name)

        await sold_out.infer_sold_out(self.db, PLAN_DAY)
        first = await self._events(kind=prep_review_events.SOLD_OUT)
        await sold_out.infer_sold_out(self.db, PLAN_DAY)
        second = await self._events(kind=prep_review_events.SOLD_OUT)

        self.assertEqual(len(first), 1, first)
        self.assertEqual(len(second), 1, second)
        self.assertEqual(first[0]["at"], second[0]["at"])
        self.assertEqual(datetime.fromisoformat(second[0]["at"]), _at(PLAN_DAY, 11, 10))

    async def test_existing_earlier_event_is_not_moved_later(self):
        """已有更早的记录（例如现场真的点过售罄）→ 推算结果不把它改晚。"""
        item_id = await self._seed_item()
        dish_name = await self._seed_rule(item_id)
        await self._seed_snapshot(PLAN_DAY, item_id, suggested=1)
        await self._seed_order("earlier-1", _at(PLAN_DAY, 11, 0), 5, dish_name=dish_name)
        await prep_review_events.record_sold_out(
            self.db,
            at=_at(PLAN_DAY, 8, 0).isoformat(),
            plan_day=PLAN_DAY,
            prep_item_id=item_id,
            station=STATION,
        )

        await sold_out.infer_sold_out(self.db, PLAN_DAY)

        events = await self._events(kind=prep_review_events.SOLD_OUT)
        self.assertEqual(len(events), 1, events)
        self.assertEqual(datetime.fromisoformat(events[0]["at"]), _at(PLAN_DAY, 8, 0))

    async def test_later_existing_event_is_replaced_with_earlier(self):
        """已有更晚的记录（订单迟到后推算出更早时刻）→ 用更早的覆盖。"""
        item_id = await self._seed_item()
        dish_name = await self._seed_rule(item_id, factor=0.5)
        await self._seed_snapshot(PLAN_DAY, item_id, suggested=10)
        await self._seed_order("replace-1", _at(PLAN_DAY, 11, 0), 19, dish_name=dish_name)
        await self._seed_order("replace-2", _at(PLAN_DAY, 11, 20), 2, dish_name=dish_name)
        await prep_review_events.record_sold_out(
            self.db,
            at=_at(PLAN_DAY, 12, 0).isoformat(),
            plan_day=PLAN_DAY,
            prep_item_id=item_id,
            station=STATION,
        )

        await sold_out.infer_sold_out(self.db, PLAN_DAY)

        events = await self._events(kind=prep_review_events.SOLD_OUT)
        self.assertEqual(len(events), 1, events)
        self.assertEqual(datetime.fromisoformat(events[0]["at"]), _at(PLAN_DAY, 11, 10))


class BackfillTests(SoldOutFixture):
    async def test_run_backfill_scans_history_and_stays_idempotent(self):
        """90 天全量扫能补到历史日期；同一自然日再跑不重复写。"""
        day = "2026-09-01"
        item_id = await self._seed_item()
        dish_name = await self._seed_rule(item_id)
        await self._seed_snapshot(day, item_id, suggested=1)
        await self._seed_order("backfill-1", _at(day, 10, 0), 3, dish_name=dish_name)

        now = _at("2026-11-07", 12, 0)
        first = await sold_out.run_backfill(self.db, now)
        second = await sold_out.run_backfill(self.db, now + timedelta(hours=1))

        self.assertTrue(first["success"])
        self.assertTrue(first["full_scan"])
        self.assertFalse(second["full_scan"])
        events = await self._events(kind=prep_review_events.SOLD_OUT)
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0]["plan_day"], day)
        self.assertEqual(datetime.fromisoformat(events[0]["at"]), _at(day, 10, 0))


class FailureIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_sold_out_swallows_write_failure(self):
        """事件表写不进去只返回 False，不能把调用方（PATCH / 回填）带崩。"""
        ok = await prep_review_events.record_sold_out(
            object(),
            at=_at(PLAN_DAY, 11, 10).isoformat(),
            plan_day=PLAN_DAY,
            prep_item_id=1,
        )
        self.assertFalse(ok)

    async def test_run_backfill_swallows_database_failure(self):
        """整库查询失败时本趟回填照常返回，下一轮再试。"""
        sold_out._last_full_run_day = None
        result = await sold_out.run_backfill(object(), _at(PLAN_DAY, 12, 0))
        self.assertTrue(result["success"])
        self.assertFalse(result["full_scan"])
