#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`prep_plan_actuals` 实际侧采集（票 04 / spec §3 决策 15、§5「实际侧」）。

三层契约：

1. **对齐主键**：实际销量按 `prep_item_id` 聚合；规则缺 id 时回落 `(品名, 单位)`
   文本键，并留下一条能定位到品名的日志。品名改了历史不断。
2. **轨迹与拆分**：窗口结束后 1h / 2h / 24h 的**累计**量、堂食 / 外卖 / 退菜、
   逐小时曲线；累计量含窗口内，所以单调递增，页面用 `sold_1h - sold_qty` 得窗口后新增。
3. **历史回填**：90 天内可重入，重复跑不产生重复行；窗口结束 3 天内的
   `sold_qty` 随迟到订单重算（票 08），更早的窗口保持首次事实。

测试直接查 `prep_plan_actuals` 是刻意的：本票只负责「入库」，页面读取是票 06 的事，
落库的列就是这批数据对下游的全部契约。
"""

import json
import unittest
from datetime import datetime, timedelta

from database import CHINA_TZ, DatabaseManager
from services.prep_item_identity import total_for
from services.prep_plan_service import PrepPlanService

ITEM_NAME = "虾饺馅"
UNIT = "份"
DISH_NAME = "虾饺皇"
STATION = "shulong"

# 一个干净的 24 小时窗口：08:00 起算，次日夜 08:00 结束。
WINDOW_START = datetime(2026, 10, 22, 8, 0, tzinfo=CHINA_TZ)
WINDOW_END = WINDOW_START + timedelta(hours=24)
# 回填时刻：窗口结束后已经过了 25 小时，三个观测点（1h / 2h / 24h）都到齐。
NOW = WINDOW_END + timedelta(hours=25)


def _at(hours_after_start: float, day_offset: int = 0) -> datetime:
    return WINDOW_START + timedelta(days=day_offset, hours=hours_after_start)


class PrepActualsFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.service = PrepPlanService()
        self.stamp = datetime.now(CHINA_TZ).isoformat()

    async def asyncTearDown(self):
        await self.db.close()

    async def _seed_item(self, *, item_name=ITEM_NAME, unit=UNIT):
        items = self.db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_items (
                    item_name, station, position, category, unit, kind, active,
                    shelf_life_hours, lead_time_hours, min_batch_qty,
                    safety_stock_ratio, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'made', 1, 24, 0, 0, 0.15, '', ?, ?)
                """,
                (item_name, STATION, "熟笼", "半成品", unit, self.stamp, self.stamp),
            )
            item_id = cursor.lastrowid
        await items.commit()
        return item_id

    async def _seed_rule(self, *, dish_name=DISH_NAME, semi_name=ITEM_NAME,
                         unit=UNIT, factor=1.0, prep_item_id=None):
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

    async def _seed_order(self, *, flow_id, at, qty, source="dine_in",
                          dish_name=DISH_NAME, status="已结"):
        result = await self.db.orders.batch_insert_orders([{
            "business_flow_id": flow_id,
            "table_number": "A1",
            "dish_name": dish_name,
            "quantity": qty,
            "order_time": at,
            "station": STATION,
            "source": source,
            "status": status,
        }])
        self.assertEqual(result["inserted_count"], 1)

    async def _seed_snapshot(self):
        result = await self.service.write_snapshot(self.db, WINDOW_START)
        self.assertEqual(result["rows"], 1)
        return result

    async def _actuals(self):
        tdb = self.db.table("prep_plan_actuals")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT * FROM prep_plan_actuals
                WHERE window_start = ? AND window_end = ?
                """,
                (WINDOW_START.isoformat(), WINDOW_END.isoformat()),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]


class TrajectoryColumnTests(PrepActualsFixture):
    async def test_trajectory_columns_are_cumulative_and_monotonic(self):
        """窗口后 1h / 2h / 24h 是**累计**量：含窗口内，所以不会比 sold_qty 小。"""
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_snapshot()

        # 窗口内：09:00 堂食 10、20:00 外卖 5、23:00 堂食 3 → sold_qty = 18
        await self._seed_order(flow_id="in-09", at=_at(1), qty=10)
        await self._seed_order(flow_id="in-20", at=_at(12), qty=5, source="delivery")
        await self._seed_order(flow_id="in-23", at=_at(15), qty=3)
        # 窗口后 30 分钟：+2
        await self._seed_order(flow_id="after-30m", at=WINDOW_END + timedelta(minutes=30), qty=2)
        # 窗口后 1.5 小时：+4
        await self._seed_order(flow_id="after-90m", at=WINDOW_END + timedelta(minutes=90), qty=4)
        # 窗口后 10 小时：+1
        await self._seed_order(flow_id="after-10h", at=WINDOW_END + timedelta(hours=10), qty=1)

        await self.service.backfill_actuals(self.db, NOW)

        rows = await self._actuals()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row["sold_qty"], 18)
        self.assertAlmostEqual(row["sold_1h"], 20)
        self.assertAlmostEqual(row["sold_2h"], 24)
        self.assertAlmostEqual(row["sold_next_day"], 25)
        self.assertGreaterEqual(row["sold_1h"], row["sold_qty"])
        self.assertGreaterEqual(row["sold_2h"], row["sold_1h"])
        self.assertGreaterEqual(row["sold_next_day"], row["sold_2h"])


class AlignmentTests(PrepActualsFixture):
    async def test_sold_qty_is_same_before_and_after_aligning_prep_item_id(self):
        """验收①：规则接上 `prep_item_id` 前后，同一窗口的 sold_qty 必须一致。"""
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=None)
        await self._seed_snapshot()
        await self._seed_order(flow_id="in-09", at=_at(1), qty=10)
        await self._seed_order(flow_id="in-20", at=_at(12), qty=5, source="delivery")
        await self._seed_order(flow_id="in-23", at=_at(15), qty=3)

        # `write_snapshot` 也会加载规则并触发一次警告（实例级去重已记下）；
        # 换新实例让本次回填的警告重新出现。
        self.service = PrepPlanService()
        with self.assertLogs("services.prep_plan_service", level="WARNING") as logs:
            await self.service.backfill_actuals(self.db, NOW)
        before = (await self._actuals())[0]["sold_qty"]
        self.assertEqual(before, 18)
        self.assertTrue(any("prep_item_id" in line and ITEM_NAME in line for line in logs.output))

        # 重新回填同一窗口：先清掉实际侧行，再把规则对齐到主数据。
        actuals = self.db.table("prep_plan_actuals")
        async with actuals.conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM prep_plan_actuals WHERE window_start = ? AND window_end = ?",
                (WINDOW_START.isoformat(), WINDOW_END.isoformat()),
            )
        await actuals.commit()
        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE semi_finished_rules SET prep_item_id = ? WHERE semi_name = ?",
                (item_id, ITEM_NAME),
            )
        await rules.commit()

        # 新实例避免「缺 id 已警告过」的实例级去重影响断言。
        self.service = PrepPlanService()
        await self.service.backfill_actuals(self.db, NOW)
        after = (await self._actuals())[0]["sold_qty"]
        self.assertEqual(after, before)

    async def test_renamed_rule_still_aggregates_by_prep_item_id(self):
        """品名改了、id 还在：按 id 聚合，历史不断。"""
        item_id = await self._seed_item()
        # 规则的半成品名是旧名，靠 prep_item_id 对齐到现名主数据。
        await self._seed_rule(prep_item_id=item_id, semi_name="虾饺馅-旧名")
        await self._seed_snapshot()
        await self._seed_order(flow_id="in-09", at=_at(1), qty=7)

        await self.service.backfill_actuals(self.db, NOW)
        row = (await self._actuals())[0]
        self.assertEqual(row["sold_qty"], 7)

    async def test_rule_without_prep_item_id_falls_back_to_text_key(self):
        """缺 id 的规则回落 (品名, 单位) 文本键，并且日志点名品名。"""
        item_id = await self._seed_item()
        self.assertGreater(item_id, 0)
        await self._seed_rule(prep_item_id=None)
        await self._seed_snapshot()
        await self._seed_order(flow_id="in-09", at=_at(1), qty=4)

        self.service = PrepPlanService()
        with self.assertLogs("services.prep_plan_service", level="WARNING") as logs:
            await self.service.backfill_actuals(self.db, NOW)
        row = (await self._actuals())[0]
        self.assertEqual(row["sold_qty"], 4)
        self.assertTrue(any(ITEM_NAME in line and "prep_item_id" in line for line in logs.output))

    async def test_sold_qty_map_uses_canonical_keys(self):
        """`_sold_qty_map` 的输出契约：有 id 写 ("id", n) 键，缺 id 写 ("name", 品名, 单位) 键。"""
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_order(flow_id="in-09", at=_at(1), qty=2)
        totals = await self.service._sold_qty_map(self.db, WINDOW_START, WINDOW_END)
        self.assertEqual(totals, {("id", item_id): 2.0})

        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute("UPDATE semi_finished_rules SET prep_item_id = NULL")
        await rules.commit()
        self.service = PrepPlanService()
        totals = await self.service._sold_qty_map(self.db, WINDOW_START, WINDOW_END)
        self.assertEqual(totals, {("name", ITEM_NAME, UNIT): 2.0})
        self.assertEqual(total_for(totals, {
            "prep_item_id": item_id, "item_name": ITEM_NAME, "unit": UNIT,
        }), 2.0)


class ChannelAndRefundTests(PrepActualsFixture):
    async def test_dine_in_plus_delivery_equals_sold_qty(self):
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_snapshot()
        await self._seed_order(flow_id="in-09", at=_at(1), qty=10)
        await self._seed_order(flow_id="in-20", at=_at(12), qty=5, source="delivery")
        await self._seed_order(flow_id="in-23", at=_at(15), qty=3)

        await self.service.backfill_actuals(self.db, NOW)
        row = (await self._actuals())[0]
        self.assertEqual(row["sold_qty"], 18)
        self.assertEqual(row["dine_in_qty"], 13)
        self.assertEqual(row["delivery_qty"], 5)
        self.assertEqual(row["dine_in_qty"] + row["delivery_qty"], row["sold_qty"])

    async def test_unknown_source_is_folded_into_dine_in_and_logged(self):
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_snapshot()
        await self._seed_order(flow_id="in-09", at=_at(1), qty=6, source="phone")

        with self.assertLogs("services.prep_plan_service", level="WARNING") as logs:
            await self.service.backfill_actuals(self.db, NOW)
        row = (await self._actuals())[0]
        self.assertEqual(row["sold_qty"], 6)
        # 未知来源按堂食归集（orders.source 真实只有 dine_in / delivery），
        # 但日志必须点名，渠道拆分不能悄悄吞一类订单。
        self.assertEqual(row["dine_in_qty"], 6)
        self.assertEqual(row["delivery_qty"], 0)
        self.assertTrue(any("phone" in line for line in logs.output))

    async def test_refund_rows_are_approximated_and_kept_out_of_sales(self):
        """退菜行数量已归零：按每行 1 份近似，不进销量、不进渠道拆分。"""
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_snapshot()
        await self._seed_order(flow_id="in-09", at=_at(1), qty=10)
        await self._seed_order(flow_id="refund-1", at=_at(2), qty=0, status="退菜")
        await self._seed_order(
            flow_id="refund-2", at=_at(3), qty=0, status="退菜", source="delivery"
        )

        await self.service.backfill_actuals(self.db, NOW)
        row = (await self._actuals())[0]
        self.assertEqual(row["sold_qty"], 10)
        self.assertEqual(row["dine_in_qty"], 10)
        self.assertEqual(row["delivery_qty"], 0)
        self.assertEqual(row["refund_qty"], 2)


class HourlyCurveTests(PrepActualsFixture):
    async def test_hourly_json_sums_to_sold_qty(self):
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_snapshot()
        await self._seed_order(flow_id="in-09", at=_at(1), qty=10)
        await self._seed_order(flow_id="in-20", at=_at(12), qty=5, source="delivery")
        await self._seed_order(flow_id="in-23", at=_at(15), qty=3)

        await self.service.backfill_actuals(self.db, NOW)
        row = (await self._actuals())[0]
        buckets = json.loads(row["hourly_json"])
        self.assertEqual(
            buckets,
            [
                {"hour": "09:00", "qty": 10.0},
                {"hour": "20:00", "qty": 5.0},
                {"hour": "23:00", "qty": 3.0},
            ],
        )
        self.assertEqual(sum(item["qty"] for item in buckets), row["sold_qty"])

    async def test_hourly_json_is_empty_without_sales(self):
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_snapshot()

        await self.service.backfill_actuals(self.db, NOW)
        row = (await self._actuals())[0]
        self.assertEqual(json.loads(row["hourly_json"]), [])
        self.assertEqual(row["sold_qty"], 0)


class TrajectoryBackfillTests(PrepActualsFixture):
    async def test_observation_points_stay_null_until_they_happen(self):
        """窗口刚结束只写确定值：1h / 2h / 24h 未到就留 NULL，不能写 0。"""
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_snapshot()
        await self._seed_order(flow_id="in-09", at=_at(1), qty=10)

        await self.service.backfill_actuals(self.db, WINDOW_END + timedelta(minutes=30))
        row = (await self._actuals())[0]
        self.assertEqual(row["sold_qty"], 10)
        self.assertIsNone(row["sold_1h"])
        self.assertIsNone(row["sold_2h"])
        self.assertIsNone(row["sold_next_day"])

        # 1h 观测点过了也只补 1h；2h / 24h 仍为 NULL。
        await self.service.backfill_actual_trajectories(
            self.db, days=90, now=WINDOW_END + timedelta(hours=1)
        )
        row = (await self._actuals())[0]
        self.assertEqual(row["sold_1h"], 10)
        self.assertIsNone(row["sold_2h"])
        self.assertIsNone(row["sold_next_day"])

    async def test_backfill_is_idempotent_and_refreshes_sold_qty_within_three_days(self):
        """验收④（票 08 起）：回填可重入、不重复插行；3 天内 sold_qty 随订单重算。"""
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_snapshot()
        await self._seed_order(flow_id="in-09", at=_at(1), qty=10)
        await self._seed_order(flow_id="after-30m", at=WINDOW_END + timedelta(minutes=30), qty=2)
        await self._seed_order(flow_id="after-90m", at=WINDOW_END + timedelta(minutes=90), qty=4)

        # 窗口刚结束：只写窗口内销量，轨迹留 NULL。
        await self.service.backfill_actuals(self.db, WINDOW_END + timedelta(minutes=30))
        self.assertEqual((await self._actuals())[0]["sold_qty"], 10)

        # 人为把首次事实改掉：3 天内的窗口会被重算回来（票 08），而不是永久冻结。
        actuals = self.db.table("prep_plan_actuals")
        async with actuals.conn.cursor() as cursor:
            await cursor.execute("UPDATE prep_plan_actuals SET sold_qty = 999")
        await actuals.commit()

        first = await self.service.backfill_actuals(self.db, NOW)
        self.assertEqual(first["windows"], 1)   # 已存在的窗口也算「待刷新」
        self.assertEqual(first["refreshed"], 1)
        rows = await self._actuals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sold_qty"], 10)
        self.assertEqual(rows[0]["sold_1h"], 12)
        self.assertEqual(rows[0]["sold_2h"], 16)
        self.assertEqual(rows[0]["sold_next_day"], 16)

        # 重复执行：不新增行、数值不变。
        await self.service.backfill_actuals(self.db, NOW)
        again = await self.service.backfill_actual_trajectories(self.db, days=90, now=NOW)
        self.assertEqual(again["rows"], 0)
        rows = await self._actuals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sold_qty"], 10)
        self.assertEqual(rows[0]["sold_1h"], 12)

    async def test_sold_qty_is_frozen_once_the_window_leaves_the_refresh_window(self):
        """3 天外的窗口是历史事实：`backfill_actuals` 不再重算 `sold_qty`。"""
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_snapshot()
        await self._seed_order(flow_id="in-09", at=_at(1), qty=10)
        await self.service.backfill_actuals(self.db, WINDOW_END + timedelta(minutes=30))

        actuals = self.db.table("prep_plan_actuals")
        async with actuals.conn.cursor() as cursor:
            await cursor.execute("UPDATE prep_plan_actuals SET sold_qty = 999")
        await actuals.commit()

        far_future = WINDOW_END + timedelta(days=4)
        await self.service.backfill_actuals(self.db, far_future)
        # 轨迹列照常补齐，sold_qty 保持首次事实。
        await self.service.backfill_actual_trajectories(self.db, days=90, now=far_future)
        rows = await self._actuals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sold_qty"], 999)
        self.assertEqual(rows[0]["sold_1h"], 10)

    async def test_trajectory_backfill_ignores_windows_outside_the_day_range(self):
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_snapshot()
        await self._seed_order(flow_id="in-09", at=_at(1), qty=3)
        await self.service.backfill_actuals(self.db, WINDOW_END + timedelta(minutes=30))

        # 窗口已在 90 天之外：不回来补。
        far_future = WINDOW_END + timedelta(days=120)
        result = await self.service.backfill_actual_trajectories(
            self.db, days=90, now=far_future
        )
        self.assertEqual(result["rows"], 0)
        self.assertIsNone((await self._actuals())[0]["sold_1h"])
