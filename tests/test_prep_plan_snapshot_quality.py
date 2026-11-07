#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""票 08：快照最终建议量 / 实际量可刷新 / 数据质量与厨房节奏上下文。

三块契约，都在服务的公开缝上测，不碰私有方法：

1. `write_snapshot` 把当天计划里**手改过**的最终量抄进快照；没手改的行
   `final_qty == suggested_qty` 且 `qty_source = 'suggested'`。
2. `backfill_actuals` 对窗口结束 `ACTUALS_REFRESH_DAYS`（3）天内的已回填窗口重算
   `sold_qty`（迟到订单 / 对账补录会改变实际量），更早的窗口保持首次事实。
3. `prep_plan_context` 的四个新键：数据质量 / 营业时段 / 是否工作日 / 厨房节奏；
   坏数据只降级成默认值，不抛。

直接查库是刻意的：落库的列与 JSON 形状就是这批数据对复盘页的全部契约。
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import services.prep_plan_context as prep_plan_context_module
from database import CHINA_TZ, DatabaseManager
from services.prep_plan_context import PrepPlanContextService
from services.prep_plan_service import ACTUALS_REFRESH_DAYS, PrepPlanService

ITEM_NAME = "虾饺馅"
UNIT = "份"
DISH_NAME = "虾饺皇"
STATION = "shulong"

DAY = "2026-10-22"
# 2026-10-22 是周四；08:00 这条快照的窗口是 08:00 → 次日 08:00。
WINDOW_START = datetime(2026, 10, 22, 8, 0, tzinfo=CHINA_TZ)
WINDOW_END = WINDOW_START + timedelta(hours=24)


def _at(hours_after_start: float) -> datetime:
    return WINDOW_START + timedelta(hours=hours_after_start)


class _NoNetworkWeather:
    """天气替身：测试库里不配 key，本就不该出网；真被调用就让它显眼地失败。"""

    async def lookup_location(self, city, api_key):  # pragma: no cover - 兜底
        raise AssertionError("测试不应发起天气请求")

    async def daily(self, location_id, api_key, day):  # pragma: no cover - 兜底
        raise AssertionError("测试不应发起天气请求")


class PrepQualityFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.service = PrepPlanService()
        self.context = PrepPlanContextService(weather_client=_NoNetworkWeather())
        self.stamp = datetime.now(CHINA_TZ).isoformat()

    async def asyncTearDown(self):
        await self.db.close()

    # ------------------------------------------------------------------ 种子

    async def _seed_item(self):
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
                (ITEM_NAME, STATION, UNIT, self.stamp, self.stamp),
            )
            item_id = cursor.lastrowid
        await items.commit()
        return item_id

    async def _seed_rule(self, *, prep_item_id=None, factor=1.0):
        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO semi_finished_rules (
                    dish_name, semi_name, position, factor, unit, category,
                    notes, created_at, updated_at, prep_item_id
                ) VALUES (?, ?, '熟笼', ?, ?, '半成品', '', ?, ?, ?)
                """,
                (DISH_NAME, ITEM_NAME, factor, UNIT, self.stamp, self.stamp, prep_item_id),
            )
        await rules.commit()

    async def _seed_order(self, *, flow_id, at, qty, source="dine_in", status="已结"):
        result = await self.db.orders.batch_insert_orders(
            [
                {
                    "business_flow_id": flow_id,
                    "table_number": "A1",
                    "dish_name": DISH_NAME,
                    "quantity": qty,
                    "order_time": at,
                    "station": STATION,
                    "source": source,
                    "status": status,
                }
            ]
        )
        self.assertEqual(result["inserted_count"], 1, result)

    async def _seed_raw_order(self, *, flow_id, at, ready_time=None,
                              is_rushed=0, is_hold=0, qty=1):
        """厨房节奏要写 `ready_time` / `is_rushed` / `is_hold`，批量插入口不接这三列。"""
        tdb = self.db.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO orders (
                    business_flow_id, table_number, dish_name, quantity, order_time,
                    status, station, source, ready_time, is_rushed, is_hold,
                    created_at, updated_at
                ) VALUES (?, 'A1', ?, ?, ?, '已结', ?, '', ?, ?, ?, ?, ?)
                """,
                (
                    flow_id, DISH_NAME, qty, at.isoformat(), STATION,
                    ready_time.isoformat() if ready_time else None,
                    is_rushed, is_hold, self.stamp, self.stamp,
                ),
            )
        await tdb.commit()

    async def _snapshot_row(self, item_id):
        tdb = self.db.table("prep_plan_snapshots")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT * FROM prep_plan_snapshots
                WHERE snapshot_at = ? AND prep_item_id = ?
                """,
                (WINDOW_START.isoformat(), item_id),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def _actuals_row(self, start, end):
        tdb = self.db.table("prep_plan_actuals")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT * FROM prep_plan_actuals
                WHERE window_start = ? AND window_end = ?
                """,
                (start.isoformat(), end.isoformat()),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None


# ------------------------------------------------------------------ 快照最终建议量


class SnapshotFinalQtyTests(PrepQualityFixture):
    async def test_snapshot_copies_the_manual_final_qty(self):
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        today = await self.service.get_today(self.db, now=WINDOW_START)
        entry = next(row for row in today["entries"] if row["item_name"] == ITEM_NAME)
        manual_qty = entry["suggested_qty"] + 17
        await self.service.update_entry(self.db, entry["id"], final_qty=manual_qty)

        await self.service.write_snapshot(self.db, WINDOW_START)

        row = await self._snapshot_row(item_id)
        self.assertEqual(row["qty_source"], "manual")
        self.assertEqual(row["final_qty"], manual_qty)

    async def test_snapshot_without_manual_edit_uses_the_suggested_qty(self):
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self.service.get_today(self.db, now=WINDOW_START)

        await self.service.write_snapshot(self.db, WINDOW_START)

        row = await self._snapshot_row(item_id)
        self.assertEqual(row["qty_source"], "suggested")
        self.assertEqual(row["final_qty"], row["suggested_qty"])


# ------------------------------------------------------------------ 实际量可刷新


class ActualsRefreshTests(PrepQualityFixture):
    async def _first_backfill(self):
        await self.service.backfill_actuals(self.db, WINDOW_END + timedelta(hours=25))

    async def test_late_order_refreshes_sold_qty_within_the_refresh_window(self):
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self.service.write_snapshot(self.db, WINDOW_START)
        await self._seed_order(flow_id="in-09", at=_at(1), qty=10)
        await self._first_backfill()
        self.assertEqual((await self._actuals_row(WINDOW_START, WINDOW_END))["sold_qty"], 10)

        # 对账补录：订单落在窗口内，但第一次回填时还没进库。
        await self._seed_order(flow_id="late-in-window", at=_at(3), qty=5)
        within = WINDOW_END + timedelta(hours=25, minutes=30)
        self.assertLessEqual(within, WINDOW_END + timedelta(days=ACTUALS_REFRESH_DAYS))
        await self.service.backfill_actuals(self.db, within)

        self.assertEqual((await self._actuals_row(WINDOW_START, WINDOW_END))["sold_qty"], 15)

    async def test_windows_older_than_the_refresh_days_keep_the_first_fact(self):
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self.service.write_snapshot(self.db, WINDOW_START)
        await self._seed_order(flow_id="in-09", at=_at(1), qty=10)
        await self._first_backfill()

        await self._seed_order(flow_id="late-in-window", at=_at(3), qty=5)
        too_late = WINDOW_END + timedelta(days=ACTUALS_REFRESH_DAYS, hours=1)
        await self.service.backfill_actuals(self.db, too_late)

        row = await self._actuals_row(WINDOW_START, WINDOW_END)
        self.assertEqual(row["sold_qty"], 10)

    async def test_refresh_stores_the_current_rules_fingerprint(self):
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id, factor=1.0)
        await self.service.write_snapshot(self.db, WINDOW_START)
        await self._seed_order(flow_id="in-09", at=_at(1), qty=10)
        await self._first_backfill()
        first = await self._actuals_row(WINDOW_START, WINDOW_END)

        # 配方改了一个系数：指纹与按新配方算出的实际量都要跟着刷新。
        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute("UPDATE semi_finished_rules SET factor = 2.0")
        await rules.commit()
        await self.service.backfill_actuals(self.db, WINDOW_END + timedelta(hours=26))

        refreshed = await self._actuals_row(WINDOW_START, WINDOW_END)
        self.assertNotEqual(refreshed["rules_fingerprint"], first["rules_fingerprint"])
        self.assertEqual(
            refreshed["rules_fingerprint"],
            await self.service._rules_fingerprint(self.db),
        )
        self.assertEqual(refreshed["sold_qty"], 20)


# ------------------------------------------------------------------ 环境信号


class ContextSignalTests(PrepQualityFixture):
    async def _context(self):
        return await self.context.get_context(self.db, DAY)

    async def test_data_quality_counts_orders_snapshots_and_last_write(self):
        await self._seed_raw_order(flow_id="o1", at=_at(1))
        await self._seed_raw_order(flow_id="o2", at=_at(2), qty=2)
        tdb = self.db.table("prep_plan_snapshots")
        async with tdb.conn.cursor() as cursor:
            for hour in (8, 9):
                await cursor.execute(
                    """
                    INSERT INTO prep_plan_snapshots (
                        snapshot_at, window_start, window_end, plan_day,
                        prep_item_id, item_name, unit, station,
                        suggested_qty, demand_qty, store_factor, quantile,
                        basis_json, rules_fingerprint, created_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, 0, 0, 1, 0.5, '{}', 'fp', ?)
                    """,
                    (
                        datetime(2026, 10, 22, hour, 0, tzinfo=CHINA_TZ).isoformat(),
                        WINDOW_START.isoformat(),
                        WINDOW_END.isoformat(),
                        DAY, ITEM_NAME, UNIT, STATION, self.stamp,
                    ),
                )
        await tdb.commit()

        await self.context.refresh(self.db, DAY, now=WINDOW_START)

        quality = (await self._context())["data_quality"]
        self.assertEqual(quality["orders_today"], 2)
        self.assertEqual(quality["first_order_at"], _at(1).isoformat())
        self.assertEqual(quality["last_order_at"], _at(2).isoformat())
        self.assertEqual(quality["snapshot_hours"], 2)
        self.assertTrue(quality["scraper_last_success"])

    async def test_runtime_prefers_prep_plan_then_scraper_then_default(self):
        await self.context.refresh(self.db, DAY, now=WINDOW_START)
        default = (await self._context())["runtime"]
        self.assertEqual(default, {"open": "07:00", "close": "21:30", "source": "default"})

        await self.db.settings_set_json(
            "scraper_runtime", {"work_start": "08:00", "work_end": "22:00"}
        )
        await self.context.refresh(self.db, DAY, now=WINDOW_START)
        from_scraper = (await self._context())["runtime"]
        self.assertEqual(from_scraper["open"], "08:00")
        self.assertEqual(from_scraper["close"], "22:00")
        self.assertEqual(from_scraper["source"], "scraper_runtime")

        await self.db.settings_set_json(
            "prep_plan_runtime", {"open": "06:30", "close": "20:00"}
        )
        await self.context.refresh(self.db, DAY, now=WINDOW_START)
        from_prep = (await self._context())["runtime"]
        self.assertEqual(from_prep, {
            "open": "06:30", "close": "20:00", "source": "prep_plan_runtime",
        })

    async def test_runtime_falls_back_on_bad_values_without_raising(self):
        await self.db.settings_set_json(
            "prep_plan_runtime", {"open": "25:99", "close": "打烊"}
        )

        await self.context.refresh(self.db, DAY, now=WINDOW_START)

        runtime = (await self._context())["runtime"]
        self.assertEqual(runtime["open"], "07:00")
        self.assertEqual(runtime["close"], "21:30")

    async def test_is_workday_is_false_on_a_statutory_holiday(self):
        await self.context.refresh(self.db, DAY, now=WINDOW_START)
        self.assertTrue((await self._context())["is_workday"])

        tdb = self.db.table("statutory_holidays")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO statutory_holidays (day, name, created_at, updated_at)
                VALUES (?, '国庆节', ?, ?)
                """,
                (DAY, self.stamp, self.stamp),
            )
        await tdb.commit()

        await self.context.refresh(self.db, DAY, now=WINDOW_START)
        self.assertFalse((await self._context())["is_workday"])

    async def test_kitchen_stats_count_lines_and_take_the_median(self):
        await self._seed_raw_order(
            flow_id="k10", at=_at(1), ready_time=_at(1) + timedelta(minutes=10)
        )
        await self._seed_raw_order(
            flow_id="k20", at=_at(2), ready_time=_at(2) + timedelta(minutes=20),
            is_rushed=1,
        )
        await self._seed_raw_order(
            flow_id="k30", at=_at(3), ready_time=_at(3) + timedelta(minutes=30),
            is_hold=1,
        )
        # 还没出餐的行：不计入 ready_lines，也不参与中位数。
        await self._seed_raw_order(flow_id="k-raw", at=_at(4))

        # 门店 KDS 未上线，厨房节奏默认关（见 KDS_LIVE）；这里显式打开来测聚合口径。
        with patch.object(prep_plan_context_module, "KDS_LIVE", True):
            await self.context.refresh(self.db, DAY, now=WINDOW_START)

        kitchen = (await self._context())["kitchen"]
        self.assertTrue(kitchen["available"])
        self.assertEqual(kitchen["ready_lines"], 3)
        self.assertEqual(kitchen["median_ready_minutes"], 20.0)
        self.assertEqual(kitchen["rushed_lines"], 1)
        self.assertEqual(kitchen["hold_lines"], 1)

    async def test_bad_values_degrade_instead_of_raising(self):
        # 出餐时间解析不了、ready 早于 order：跳过时长，但行数照数。
        await self._seed_raw_order(flow_id="bad-1", at=_at(1))
        await self._seed_raw_order(
            flow_id="bad-2", at=_at(2) + timedelta(minutes=5), ready_time=_at(2)
        )
        tdb = self.db.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE orders SET ready_time = 'not-a-time' WHERE business_flow_id = 'bad-1'"
            )
        await tdb.commit()

        with patch.object(prep_plan_context_module, "KDS_LIVE", True):
            await self.context.refresh(self.db, DAY, now=WINDOW_START)

        kitchen = (await self._context())["kitchen"]
        self.assertEqual(kitchen["ready_lines"], 2)
        self.assertIsNone(kitchen["median_ready_minutes"])

        # 读侧坏 JSON 也只降级成空对象。
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE prep_plan_context SET data_quality_json = 'not-json' WHERE plan_day = ?",
                (DAY,),
            )
        await tdb.commit()
        self.assertEqual((await self._context())["data_quality"], {})

    async def test_kitchen_stats_stay_off_until_kds_goes_live(self):
        # 库里已经有 KDS 试用期留下的 ready_time，默认仍然是「不采」——那批行是一次性
        # 批量确认，算出来的中位时长会把复盘带偏（见 services/prep_plan_context.py 的
        # KDS_LIVE 注释）。
        await self._seed_raw_order(
            flow_id="kds-on", at=_at(1), ready_time=_at(1) + timedelta(minutes=10)
        )

        await self.context.refresh(self.db, DAY, now=WINDOW_START)
        kitchen = (await self._context())["kitchen"]
        self.assertFalse(kitchen["available"])
        self.assertEqual(kitchen["ready_lines"], 0)

        with patch.object(prep_plan_context_module, "KDS_LIVE", True):
            await self.context.refresh(self.db, DAY, now=WINDOW_START)
        kitchen = (await self._context())["kitchen"]
        self.assertTrue(kitchen["available"])
        self.assertEqual(kitchen["ready_lines"], 1)
        self.assertEqual(kitchen["median_ready_minutes"], 10.0)

    async def test_write_snapshot_persists_all_four_signals(self):
        item_id = await self._seed_item()
        await self._seed_rule(prep_item_id=item_id)
        await self._seed_order(flow_id="in-09", at=_at(1), qty=4)

        await self.service.write_snapshot(self.db, WINDOW_START)

        context = await self._context()
        self.assertEqual(context["data_quality"]["orders_today"], 1)
        self.assertEqual(context["data_quality"]["snapshot_hours"], 1)
        self.assertEqual(context["runtime"]["source"], "default")
        self.assertTrue(context["is_workday"])
        self.assertEqual(context["kitchen"]["ready_lines"], 0)
        self.assertIsNone(context["kitchen"]["median_ready_minutes"])
        # KDS 未上线：写 `available: false` 而不是「量到了但没测出来」的全 0。
        self.assertFalse(context["kitchen"]["available"])
        self.assertEqual(context["kitchen"]["reason"], "kds_not_live")


if __name__ == "__main__":
    unittest.main()
