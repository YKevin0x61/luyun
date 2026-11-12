#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货复盘聚合（票 06 后端 / spec §6「指标」、§7「页面」）。

钉四组契约：

1. **偏差口径**：以 07:00 主口径逐日逐品算量加权绝对偏差 / 方向偏差 / 数量口径
   够卖率；建议量优先 `final_qty`，空才用 `suggested_qty`；无实际（窗口未结束）的
   行不参与，既不当 0 也不算样本。
2. **分组对比**：星期几 / 节假日 / 天气 / 档口人数 / 备注；`n < 5` 的组两个偏差
   数值必须是 `null` 且 `insufficient=true`（不能拿小样本去下结论）。
3. **代价拆分**：多备量 = 次日仍卖出 + 报废 + 未明；三项之和回到总多备量（容差）；
   报废只算报废类原因，估算金额用期间内订单的加权成交均价（标 `estimated`）。
4. **快照明细**：`get_snapshot_day` 补上 0049 的轨迹列与当天首次售罄时刻，NULL
   原样给 `null`（写 0 会被误读成「之后没人买 / 没退过菜」）。
"""

import asyncio
import unittest
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.prep_plan import manager_router, router
from api.security import require_any_identity_session, verify_admin_token
from database import CHINA_TZ, DatabaseManager, get_db
from services.prep_review.summary import review_summary
from services.prep_plan_service import PrepPlanService
from services.wastage.store import WastageActor, WastageStore

ITEM_NAME = "虾饺馅"
UNIT = "份"
DISH_NAME = "虾饺皇"
STATION = "shulong"
FP = "fingerprint"

# 2026-10-19 是周一，10-20 是周二（与 test_prep_plan_today.py 的 10-22 周四一致）。
MONDAYS = ("2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05")
TUESDAYS = ("2026-10-06", "2026-10-13")


def _run(coro):
    return asyncio.run(coro)


def _deny_admin():
    raise HTTPException(status_code=401, detail="未授权")


class ReviewFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.service = PrepPlanService()
        self.stamp = datetime.now(CHINA_TZ).isoformat()

    async def asyncTearDown(self):
        await self.db.close()

    # ------------------------------------------------------------------ 夹具

    async def _seed_item(self, *, item_name=ITEM_NAME, unit=UNIT, station=STATION):
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

    async def _seed_rule(self, *, item_id, factor=1.0, dish_name=DISH_NAME,
                         semi_name=ITEM_NAME, unit=UNIT):
        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO semi_finished_rules (
                    dish_name, semi_name, position, factor, unit, category,
                    notes, created_at, updated_at, prep_item_id
                ) VALUES (?, ?, '熟笼', ?, ?, '半成品', '', ?, ?, ?)
                """,
                (dish_name, semi_name, factor, unit, self.stamp, self.stamp, item_id),
            )
        await rules.commit()

    async def _seed_snapshot(self, day, item_id, suggested, *, final=None,
                             item_name=ITEM_NAME, unit=UNIT, clock="07:00"):
        start = datetime.fromisoformat(f"{day}T{clock}:00+08:00")
        end = start + timedelta(hours=24)
        tdb = self.db.table("prep_plan_snapshots")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_snapshots (
                    snapshot_at, window_start, window_end, plan_day,
                    prep_item_id, item_name, unit, station,
                    suggested_qty, demand_qty, final_qty, store_factor, quantile,
                    basis_json, rules_fingerprint, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.5, '{}', ?, ?)
                """,
                (
                    start.isoformat(), start.isoformat(), end.isoformat(), day,
                    item_id, item_name, unit, STATION, suggested, suggested, final,
                    FP, self.stamp,
                ),
            )
        await tdb.commit()

    async def _seed_actual(self, day, item_id, sold, *, clock="07:00",
                           sold_1h=None, sold_2h=None, sold_next_day=None,
                           refund_qty=None, dine_in_qty=None, delivery_qty=None,
                           item_name=ITEM_NAME, unit=UNIT):
        start = datetime.fromisoformat(f"{day}T{clock}:00+08:00")
        end = start + timedelta(hours=24)
        tdb = self.db.table("prep_plan_actuals")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_actuals (
                    window_start, window_end, prep_item_id, item_name, unit, station,
                    sold_qty, rules_fingerprint, computed_at,
                    sold_1h, sold_2h, sold_next_day,
                    dine_in_qty, delivery_qty, refund_qty, hourly_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]')
                """,
                (
                    start.isoformat(), end.isoformat(), item_id, item_name, unit, STATION,
                    sold, FP, self.stamp,
                    sold_1h, sold_2h, sold_next_day,
                    dine_in_qty, delivery_qty, refund_qty,
                ),
            )
        await tdb.commit()

    async def _seed_context(self, day, *, holiday_name=None, weather=None,
                            staff=None, note=None):
        tdb = self.db.table("prep_plan_context")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_context (
                    plan_day, holiday_name, weather_json, staff_json, note,
                    captured_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, plan_day) DO UPDATE SET
                    holiday_name = EXCLUDED.holiday_name,
                    weather_json = EXCLUDED.weather_json,
                    staff_json = EXCLUDED.staff_json,
                    note = EXCLUDED.note,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    day, holiday_name,
                    _json(weather) if weather is not None else "{}",
                    _json(staff) if staff is not None else "{}",
                    note, self.stamp, self.stamp,
                ),
            )
        await tdb.commit()

    async def _seed_wastage(self, day, qty, *, item_id=None, item_name=ITEM_NAME,
                            reason="excess", unit=UNIT):
        tdb = self.db.table("wastage_records")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO wastage_records (
                    item_name, quantity, unit, reason, remark, wastage_date,
                    operator_kind, operator_name, created_at, updated_at,
                    prep_item_id, window_start, window_end
                ) VALUES (?, ?, ?, ?, '', ?, 'admin', '店长', ?, ?, ?, ?, ?)
                """,
                (
                    item_name, qty, unit, reason, day, self.stamp, self.stamp,
                    item_id, f"{day}T07:00:00+08:00",
                    f"{day}T07:00:00+08:00",
                ),
            )
            record_id = cursor.lastrowid
        await tdb.commit()
        return record_id

    async def _seed_order(self, day, qty, total_amount, *, hour=9,
                          dish_name=DISH_NAME, status="已结"):
        result = await self.db.orders.batch_insert_orders([{
            "business_flow_id": f"review-{day}-{hour}-{qty}",
            "table_number": "A1",
            "dish_name": dish_name,
            "quantity": qty,
            "order_time": datetime.fromisoformat(f"{day}T{hour:02d}:00:00+08:00"),
            "station": STATION,
            "status": status,
            "total_amount": total_amount,
        }])
        self.assertEqual(result["inserted_count"], 1)

    async def _seed_event(self, day, item_id, at, kind="sold_out"):
        tdb = self.db.table("prep_plan_events")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_events (at, plan_day, prep_item_id, kind)
                VALUES (?, ?, ?, ?)
                """,
                (at, day, item_id, kind),
            )
        await tdb.commit()

    async def _summary(self, end, days=90):
        return await review_summary(self.db, end=end, days=days)

    @staticmethod
    def _bucket(groups, dimension, key):
        return next(row for row in groups[dimension] if row["key"] == key)


def _json(value):
    import json

    return json.dumps(value, ensure_ascii=False)


class MetricTests(ReviewFixture):
    async def test_three_deviation_metrics_use_primary_clock_and_final_qty(self):
        item_id = await self._seed_item()
        # 10-19：建议 100（无手改），实际 80 → +20
        await self._seed_snapshot("2026-10-19", item_id, 100)
        await self._seed_actual("2026-10-19", item_id, 80)
        # 10-20：建议 50、店长手改成 60，实际 70 → −10（按 60 算，不是 50）
        await self._seed_snapshot("2026-10-20", item_id, 50, final=60)
        await self._seed_actual("2026-10-20", item_id, 70)
        # 10-21：窗口未结束（没有实际行）→ 不进任何指标
        await self._seed_snapshot("2026-10-21", item_id, 40)

        body = await self._summary("2026-10-22", days=10)
        metrics = body["metrics"]
        self.assertEqual(body["primary_clock"], "07:00")
        self.assertEqual(metrics["measured_days"], 2)
        self.assertEqual(metrics["measured_item_days"], 2)
        self.assertAlmostEqual(metrics["suggested_total"], 160)
        self.assertAlmostEqual(metrics["sold_total"], 150)
        self.assertAlmostEqual(metrics["absolute_deviation"], 30 / 150)
        self.assertAlmostEqual(metrics["direction_deviation"], 10 / 150)
        self.assertAlmostEqual(metrics["coverage_qty"], 140 / 150)
        # 品数口径：10-19 够卖、10-20 不够 → 1/2
        self.assertEqual(metrics["covered_items"], 1)
        self.assertAlmostEqual(metrics["coverage_items"], 1 / 2)

    async def test_non_primary_clock_rows_are_ignored(self):
        item_id = await self._seed_item()
        await self._seed_snapshot("2026-10-19", item_id, 100, clock="07:00")
        await self._seed_actual("2026-10-19", item_id, 90)
        # 08:00 那条窗口不算：复盘主口径只有 07:00
        await self._seed_snapshot("2026-10-19", item_id, 999, clock="08:00")
        await self._seed_actual("2026-10-19", item_id, 1, clock="08:00")

        body = await self._summary("2026-10-19", days=1)
        self.assertEqual(body["metrics"]["measured_item_days"], 1)
        self.assertAlmostEqual(body["metrics"]["suggested_total"], 100)
        self.assertAlmostEqual(body["metrics"]["sold_total"], 90)


class GroupTests(ReviewFixture):
    async def _seed_week(self):
        """5 个周一 + 2 个周二，每天一条 07:00 快照 / 实际（建议 100、实际 90）。"""
        item_id = await self._seed_item()
        for day in MONDAYS + TUESDAYS:
            await self._seed_snapshot(day, item_id, 100)
            await self._seed_actual(day, item_id, 90)
        for day in MONDAYS:
            await self._seed_context(
                day,
                weather={"text": "小雨", "temp_max": 22.0},
                staff={"熟笼": 3},
            )
        await self._seed_context(
            MONDAYS[0],
            holiday_name="国庆节",
            weather={"text": "小雨", "temp_max": 22.0},
            staff={"熟笼": 3},
        )
        await self._seed_context(TUESDAYS[0], staff={"熟笼": 1, "案板": 1}, note="团餐 30 人")
        await self._seed_context(TUESDAYS[1], staff={"熟笼": 1, "案板": 1})
        return item_id

    async def test_groups_by_weekday_holiday_weather_staff_and_note(self):
        await self._seed_week()
        body = await self._summary("2026-10-13", days=40)
        groups = body["groups"]

        monday = self._bucket(groups, "weekday", "1")
        self.assertEqual(monday["n"], 5)
        self.assertFalse(monday["insufficient"])
        self.assertAlmostEqual(monday["direction_deviation"], 10 / 90)
        self.assertAlmostEqual(monday["absolute_deviation"], 10 / 90)

        tuesday = self._bucket(groups, "weekday", "2")
        self.assertEqual(tuesday["n"], 2)
        self.assertTrue(tuesday["insufficient"])
        self.assertIsNone(tuesday["direction_deviation"])
        self.assertIsNone(tuesday["absolute_deviation"])

        rain = self._bucket(groups, "weather", "rain")
        self.assertEqual(rain["n"], 5)
        self.assertIsNotNone(rain["absolute_deviation"])
        no_weather = self._bucket(groups, "weather", "unknown")
        self.assertEqual(no_weather["label"], "无数据")
        self.assertEqual(no_weather["n"], 2)
        self.assertTrue(no_weather["insufficient"])
        self.assertIsNone(no_weather["absolute_deviation"])
        self.assertEqual(self._bucket(groups, "weather", "hot")["n"], 0)

        staff_34 = self._bucket(groups, "staff", "3-4")
        self.assertEqual(staff_34["n"], 5)
        self.assertIsNotNone(staff_34["direction_deviation"])
        staff_12 = self._bucket(groups, "staff", "1-2")
        self.assertEqual(staff_12["n"], 2)
        self.assertTrue(staff_12["insufficient"])

        holiday = self._bucket(groups, "holiday", "holiday")
        self.assertEqual(holiday["n"], 1)
        self.assertTrue(holiday["insufficient"])
        weekday = self._bucket(groups, "holiday", "weekday")
        self.assertEqual(weekday["n"], 6)
        self.assertFalse(weekday["insufficient"])

        self.assertEqual(self._bucket(groups, "note", "with")["n"], 1)
        self.assertEqual(self._bucket(groups, "note", "without")["n"], 6)

    async def test_high_temperature_and_staff_buckets(self):
        item_id = await self._seed_item()
        days = ("2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16")
        staff_counts = (1, 2, 3, 4, 5)
        for day, headcount in zip(days, staff_counts):
            await self._seed_snapshot(day, item_id, 100)
            await self._seed_actual(day, item_id, 90)
            await self._seed_context(
                day,
                weather={"text": "晴", "temp_max": 35.0},
                staff={"熟笼": headcount},
            )
        # 一天既没天气也没人力 → 两个维度都落「无数据」
        await self._seed_snapshot("2026-10-17", item_id, 100)
        await self._seed_actual("2026-10-17", item_id, 90)

        body = await self._summary("2026-10-17", days=10)
        groups = body["groups"]
        hot = self._bucket(groups, "weather", "hot")
        self.assertEqual(hot["n"], 5)
        self.assertIsNotNone(hot["absolute_deviation"])
        self.assertEqual(self._bucket(groups, "weather", "normal")["n"], 0)

        self.assertEqual(self._bucket(groups, "staff", "1-2")["n"], 2)
        self.assertEqual(self._bucket(groups, "staff", "3-4")["n"], 2)
        self.assertEqual(self._bucket(groups, "staff", "5+")["n"], 1)
        self.assertEqual(self._bucket(groups, "staff", "unknown")["n"], 1)
        # 每个桶都 < 5 天 → 都不给偏差数值
        for key in ("1-2", "3-4", "5+", "unknown"):
            row = self._bucket(groups, "staff", key)
            self.assertTrue(row["insufficient"])
            self.assertIsNone(row["direction_deviation"])


class CostTests(ReviewFixture):
    async def test_cost_split_sums_to_overfill_and_amount_uses_weighted_price(self):
        item_id = await self._seed_item()
        await self._seed_rule(item_id=item_id, factor=2.0)
        # 10-19：建议 100、实际 80 → 多备 20；次日累计到 90（多卖 10）；报废 8
        await self._seed_snapshot("2026-10-19", item_id, 100)
        await self._seed_actual("2026-10-19", item_id, 80, sold_next_day=90)
        await self._seed_wastage("2026-10-19", 5, item_id=item_id, reason="excess")
        await self._seed_wastage("2026-10-19", 3, item_id=None, reason="expired")
        # 非报废类（破损）不计入代价，只应出现在「登记总量」之外的说明里
        await self._seed_wastage("2026-10-19", 7, item_id=item_id, reason="damaged")
        # 10-20：建议 100、实际 90 → 多备 10；次日轨迹还没到 → 未明 10
        await self._seed_snapshot("2026-10-20", item_id, 100)
        await self._seed_actual("2026-10-20", item_id, 90)
        # 金额：30 份菜 × 系数 2 = 60 个半成品单位，总金额 500 → 单价 6.25
        await self._seed_order("2026-10-19", 10, 200)
        await self._seed_order("2026-10-20", 30, 300)

        body = await self._summary("2026-10-22", days=10)
        cost = body["cost"]
        self.assertAlmostEqual(cost["overfill_qty"], 30)
        self.assertAlmostEqual(cost["next_day_sold"], 10)
        self.assertAlmostEqual(cost["wastage"], 8)
        self.assertAlmostEqual(cost["unexplained"], 12)
        self.assertAlmostEqual(
            cost["next_day_sold"] + cost["wastage"] + cost["unexplained"],
            cost["overfill_qty"],
        )
        self.assertAlmostEqual(cost["estimated_amount"], 30 * 6.25)
        self.assertTrue(cost["estimated"])
        self.assertAlmostEqual(cost["wastage_recorded_qty"], 8)
        self.assertEqual(cost["pending_trajectory"], 1)
        self.assertIn("excess", cost["wastage_reasons"])
        self.assertNotIn("damaged", cost["wastage_reasons"])

    async def test_price_falls_back_to_name_key_when_rule_has_no_prep_item_id(self):
        """ADR 0141：规则没接主数据时价格表只有名字键，快照行按 id 也要查得到。

        旧实现用 `prices.get(("id", item_id))`，名字桶里的价查不到，金额静默为 0。
        """
        item_id = await self._seed_item()
        # 规则的 semi_name / unit 与快照行一致，方便按名字键对上。
        await self._seed_rule(item_id=item_id, factor=1.0)
        rules = self.db.table("semi_finished_rules")
        async with rules.conn.cursor() as cursor:
            await cursor.execute("UPDATE semi_finished_rules SET prep_item_id = NULL")
        await rules.commit()
        # 10-19：建议 100、实际 80 → 多备 20
        await self._seed_snapshot("2026-10-19", item_id, 100)
        await self._seed_actual("2026-10-19", item_id, 80)
        # 10 份菜卖 200 → 每个半成品单位 20
        await self._seed_order("2026-10-19", 10, 200)

        body = await self._summary("2026-10-22", days=10)
        cost = body["cost"]
        self.assertAlmostEqual(cost["overfill_qty"], 20)
        self.assertAlmostEqual(cost["estimated_amount"], 20 * 20)
        self.assertTrue(cost["estimated"])

    async def test_revoked_wastage_is_not_counted_in_cost(self):
        """撤回的那笔不进 cost.wastage，也不进 wastage_recorded_qty（台账契约）。"""
        item_id = await self._seed_item()
        await self._seed_rule(item_id=item_id, factor=1.0)
        await self._seed_snapshot("2026-10-19", item_id, 100)
        await self._seed_actual("2026-10-19", item_id, 80)
        await self._seed_wastage("2026-10-19", 3, item_id=item_id, reason="expired")
        gone = await self._seed_wastage(
            "2026-10-19", 5, item_id=item_id, reason="excess"
        )
        admin = WastageActor(kind="admin", employee_id=None, name="管理端")
        revoked = await WastageStore(self.db).revoke_record(admin, gone)
        self.assertIsNotNone(revoked["revoked_at"])

        body = await self._summary("2026-10-22", days=10)
        cost = body["cost"]
        self.assertAlmostEqual(cost["overfill_qty"], 20)
        self.assertAlmostEqual(cost["wastage"], 3)
        self.assertAlmostEqual(cost["wastage_recorded_qty"], 3)
        self.assertAlmostEqual(
            cost["next_day_sold"] + cost["wastage"] + cost["unexplained"],
            cost["overfill_qty"],
        )


class SnapshotDayTests(ReviewFixture):
    async def test_snapshot_day_carries_trajectory_and_sold_out_at(self):
        item_id = await self._seed_item()
        other_id = await self._seed_item(item_name="叉烧馅")
        day = "2026-10-22"
        await self._seed_snapshot(day, item_id, 100)
        await self._seed_actual(
            day, item_id, 80,
            sold_1h=85, sold_2h=88, sold_next_day=90,
            refund_qty=2, dine_in_qty=70, delivery_qty=10,
        )
        # 第二个品：轨迹列全 NULL，必须原样给 null（不是 0）
        await self._seed_snapshot(day, other_id, 20, item_name="叉烧馅")
        await self._seed_actual(day, other_id, 15, item_name="叉烧馅")
        # 售罄事件两条 → 取最早一条；第二个品没有事件
        await self._seed_event(day, item_id, f"{day}T09:00:00+08:00")
        await self._seed_event(day, item_id, f"{day}T08:10:00+08:00")

        payload = await self.service.get_snapshot_day(self.db, day)
        by_name = {row["item_name"]: row for row in payload["items"]}

        shrimp = by_name[ITEM_NAME]
        self.assertEqual(shrimp["sold_out_at"], f"{day}T08:10:00+08:00")
        hour = next(h for h in shrimp["hours"] if h["snapshot_at"].startswith(f"{day}T07:00"))
        self.assertAlmostEqual(hour["sold_1h"], 85)
        self.assertAlmostEqual(hour["sold_2h"], 88)
        self.assertAlmostEqual(hour["sold_next_day"], 90)
        self.assertAlmostEqual(hour["refund_qty"], 2)

        other = by_name["叉烧馅"]
        self.assertIsNone(other["sold_out_at"])
        other_hour = other["hours"][0]
        self.assertIsNone(other_hour["sold_1h"])
        self.assertIsNone(other_hour["sold_2h"])
        self.assertIsNone(other_hour["sold_next_day"])
        self.assertIsNone(other_hour["refund_qty"])

        group = payload["snapshots"][0]
        group_items = {row["item_name"]: row for row in group["items"]}
        self.assertEqual(group_items[ITEM_NAME]["sold_out_at"], f"{day}T08:10:00+08:00")
        self.assertIsNone(group_items["叉烧馅"]["sold_out_at"])
        self.assertAlmostEqual(group_items[ITEM_NAME]["sold_1h"], 85)
        self.assertIsNone(group_items["叉烧馅"]["sold_next_day"])


# --------------------------------------------------------------------- 接口


def _build_app(db, *, admin=True, session_identity=None):
    app = FastAPI()
    app.include_router(manager_router)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[verify_admin_token] = (lambda: True) if admin else _deny_admin
    # 2026-11-10：两个只读端点从「管理端专属 router」挪到 manager_router（员工会话 + 能力
    # 可读），所以夹具要把「任一身份」会话也注进来；admin=True 时给管理端投影。
    if session_identity is not None:
        app.dependency_overrides[require_any_identity_session] = lambda: session_identity
    else:
        app.dependency_overrides[require_any_identity_session] = (
            (lambda: {"kind": "admin"}) if admin else _deny_admin
        )
    return app


@pytest.fixture
def admin_client():
    db = DatabaseManager()
    _run(db.connect())
    with TestClient(_build_app(db, admin=True)) as client:
        yield client, db
    _run(db.close())


@pytest.fixture
def denied_client():
    db = DatabaseManager()
    _run(db.connect())
    with TestClient(_build_app(db, admin=False)) as client:
        yield client, db
    _run(db.close())


def _seed_one_day(db):
    async def work():
        stamp = datetime.now(CHINA_TZ).isoformat()
        items = db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_items (
                    item_name, station, position, category, unit, kind, active,
                    shelf_life_hours, lead_time_hours, min_batch_qty,
                    safety_stock_ratio, notes, created_at, updated_at
                ) VALUES ('虾饺馅', 'shulong', '熟笼', '半成品', '份', 'made', 1,
                          24, 0, 0, 0.15, '', ?, ?)
                """,
                (stamp, stamp),
            )
            item_id = cursor.lastrowid
        await items.commit()
        start = datetime.fromisoformat("2026-10-19T07:00:00+08:00")
        end = start + timedelta(hours=24)
        tdb = db.table("prep_plan_snapshots")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_snapshots (
                    snapshot_at, window_start, window_end, plan_day,
                    prep_item_id, item_name, unit, station,
                    suggested_qty, demand_qty, store_factor, quantile,
                    basis_json, rules_fingerprint, created_at
                ) VALUES (?, ?, ?, '2026-10-19', ?, '虾饺馅', '份', 'shulong',
                          100, 100, 1.0, 0.5, '{}', 'fp', ?)
                """,
                (start.isoformat(), start.isoformat(), end.isoformat(), item_id, stamp),
            )
        await tdb.commit()
        adb = db.table("prep_plan_actuals")
        async with adb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_plan_actuals (
                    window_start, window_end, prep_item_id, item_name, unit, station,
                    sold_qty, rules_fingerprint, computed_at
                ) VALUES (?, ?, ?, '虾饺馅', '份', 'shulong', 90, 'fp', ?)
                """,
                (start.isoformat(), end.isoformat(), item_id, stamp),
            )
        await adb.commit()

    _run(work())
    return db


def test_review_summary_api_returns_metrics_groups_and_cost(admin_client):
    client, db = admin_client
    _seed_one_day(db)
    resp = client.get(
        "/api/prep-plan/review/summary", params={"end": "2026-10-19", "days": 7}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["primary_clock"] == "07:00"
    assert body["period"] == {"start": "2026-10-13", "end": "2026-10-19", "days": 7}
    assert body["metrics"]["measured_item_days"] == 1
    assert body["metrics"]["suggested_total"] == 100
    assert body["metrics"]["sold_total"] == 90
    for dimension in ("weekday", "holiday", "weather", "staff", "note"):
        assert isinstance(body["groups"][dimension], list)
    assert body["cost"]["overfill_qty"] == 10
    assert body["cost"]["estimated"] is True


def test_review_summary_rejects_bad_day_and_out_of_range_days(admin_client):
    client, _db = admin_client
    resp = client.get("/api/prep-plan/review/summary", params={"end": "2026/10/19"})
    assert resp.status_code == 400
    resp = client.get("/api/prep-plan/review/summary", params={"days": 0})
    assert resp.status_code == 422
    resp = client.get("/api/prep-plan/review/summary", params={"days": 91})
    assert resp.status_code == 422


def test_review_summary_is_admin_only(denied_client):
    client, _db = denied_client
    resp = client.get("/api/prep-plan/review/summary")
    assert resp.status_code == 401

_STAFF_WITH_CAP = {"kind": "staff", "employee": {"id": 2, "name": "彭灼洪", "admin_caps": ["prep_review"]}}
_STAFF_PLAIN = {"kind": "staff", "employee": {"id": 3, "name": "张织纷", "admin_caps": []}}


def _staff_app(db, identity):
    app = FastAPI()
    app.include_router(manager_router)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[verify_admin_token] = _deny_admin
    app.dependency_overrides[require_any_identity_session] = lambda: identity
    return app


@pytest.fixture
def staff_cap_client():
    db = DatabaseManager()
    _run(db.connect())
    with TestClient(_staff_app(db, _STAFF_WITH_CAP)) as client:
        yield client, db
    _run(db.close())


@pytest.fixture
def staff_plain_client():
    db = DatabaseManager()
    _run(db.connect())
    with TestClient(_staff_app(db, _STAFF_PLAIN)) as client:
        yield client, db
    _run(db.close())


def test_staff_session_with_prep_review_cap_can_read_both_pages(staff_cap_client):
    client, _db = staff_cap_client
    resp = client.get("/api/prep-plan/review/summary", params={"end": "2026-10-19", "days": 7})
    assert resp.status_code == 200, resp.text
    resp = client.get("/api/prep-plan/snapshots", params={"day": "2026-10-19"})
    assert resp.status_code == 200, resp.text


def test_staff_session_without_cap_is_403(staff_plain_client):
    client, _db = staff_plain_client
    resp = client.get("/api/prep-plan/review/summary")
    assert resp.status_code == 403
    assert "备货复盘" in resp.json()["detail"]
    resp = client.get("/api/prep-plan/snapshots", params={"day": "2026-10-19"})
    assert resp.status_code == 403
    assert "备货复盘" in resp.json()["detail"]


def test_cost_price_prefers_id_bucket_and_falls_back_to_name_bucket():
    """ADR 0141：价格按 id 键优先、缺 id 才回落名字键；两桶都有价时不相加。"""
    from services.prep_review.summary import _cost

    row = {
        "day": "2026-10-19",
        "key": ("id", 7),
        "item_name": ITEM_NAME,
        "unit": UNIT,
        "suggested": 10.0,
        "sold": 0.0,
        "sold_next_day": 0.0,
    }
    args = ([row], {}, {}, 0.0)

    cost = _cost(*args, {("id", 7): 2.0, ("name", ITEM_NAME, UNIT): 99.0})
    assert cost["estimated_amount"] == 20.0

    cost = _cost(*args, {("name", ITEM_NAME, UNIT): 3.0})
    assert cost["estimated_amount"] == 30.0
