#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard kds_backlog aggregation and table live list."""

import contextlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import db_core.reports as reports_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from db_core.business_day import business_day_window

# 票 25：面板聚合的窗口现在是**营业日**（06:00 切），用例不能再拿墙钟当基准
# ——旧写法 `now - 45min` / `now - 1 day` 在 00:00–06:00 会掉到窗口外（这正是
# 已知的凌晨假红），改营业日后同样会在 06:00–06:45 复现。所以：
#   ① 相对时刻造的用例把 db_core.reports 的 datetime 冻在营业日内的固定时刻；
#   ② 窗口排除类用例按 business_day_window(now) 的边界造数据。
_PANEL_NOW = datetime(2026, 5, 2, 12, 0, tzinfo=CHINA_TZ)


class _FrozenDatetime(datetime):
    """冻结 now() 的 datetime 子类。

    db_core/reports.py 内没有对 datetime 取值做 isinstance 判断（只有
    db_core/utils.py 的 ensure_beijing_datetime 做，而它在自己的命名空间里
    引用真 datetime），所以普通子类替换即可，不需要 __instancecheck__ 委派。
    """

    frozen: datetime = _PANEL_NOW

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.frozen.replace(tzinfo=None)
        return cls.frozen.astimezone(tz)


@contextlib.contextmanager
def _frozen_panel_clock(moment=_PANEL_NOW):
    """把 db_core.reports 模块里的 datetime 冻到 moment（默认营业日内正午）。"""
    frozen_cls = type("_FrozenPanelDatetime", (_FrozenDatetime,), {"frozen": moment})
    with mock.patch.object(reports_module, "datetime", frozen_cls):
        yield moment


class DashboardOpsPanelsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        # 冻结的基准时刻（只用于造数据与 created_at，聚合时钟见 _frozen_panel_clock）
        self.now = _PANEL_NOW

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _insert_order(self, **kwargs):
        tdb = self.db.table("orders")
        now_iso = self.now.isoformat()
        await tdb.execute(
            """INSERT INTO orders
               (business_flow_id, table_number, dish_name, quantity, order_time,
                price, total_amount, status, category, station, dish_status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kwargs.get("business_flow_id", "flow-001"),
                kwargs.get("table_number", "A1"),
                kwargs.get("dish_name", "虾饺"),
                kwargs.get("quantity", 1),
                kwargs.get("order_time", (self.now - timedelta(minutes=5)).isoformat()),
                10.0,
                10.0,
                kwargs.get("status", "未结"),
                "点心",
                kwargs.get("station", "dimsum"),
                kwargs.get("dish_status", "待出餐"),
                now_iso,
                now_iso,
            ),
        )
        await tdb.commit()

    async def test_aggregate_kds_backlog_groups_pending_by_station(self):
        await self._insert_order(
            business_flow_id="pending-1",
            station="dimsum",
            order_time=(self.now - timedelta(minutes=5)).isoformat(),
        )
        await self._insert_order(
            business_flow_id="pending-2",
            station="dimsum",
            order_time=(self.now - timedelta(minutes=45)).isoformat(),
        )
        await self._insert_order(
            business_flow_id="pending-3",
            station="wok",
            order_time=(self.now - timedelta(minutes=3)).isoformat(),
        )
        await self._insert_order(
            business_flow_id="done-1",
            station="wok",
            dish_status="已制作待上菜",
            order_time=(self.now - timedelta(minutes=30)).isoformat(),
        )

        # 聚合时钟冻在 12:00（营业日内），四条相对时刻全部在窗口里
        with _frozen_panel_clock():
            backlog = await self.db.aggregate_kds_backlog()

        self.assertEqual(backlog["total_pending"], 3)
        self.assertEqual(backlog["overdue_count"], 1)
        self.assertEqual(backlog["busiest_station"]["station_id"], "dimsum")
        self.assertEqual(backlog["busiest_station"]["pending"], 2)
        dimsum = next(s for s in backlog["stations"] if s["station_id"] == "dimsum")
        self.assertEqual(dimsum["pending"], 2)
        self.assertEqual(dimsum["overdue"], 1)
        self.assertEqual(dimsum["load_level"], "low")
        self.assertGreaterEqual(dimsum["oldest_wait_minutes"], 20.0)

    async def test_aggregate_kds_backlog_load_levels(self):
        tdb = self.db.table("orders")
        now_iso = self.now.isoformat()
        for idx in range(8):
            await tdb.execute(
                """INSERT INTO orders
                   (business_flow_id, table_number, dish_name, quantity, order_time,
                    price, total_amount, status, category, station, dish_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"medium-{idx}",
                    "B1",
                    "肠粉",
                    1,
                    (self.now - timedelta(minutes=1)).isoformat(),
                    10.0,
                    10.0,
                    "未结",
                    "点心",
                    "changfen",
                    "待出餐",
                    now_iso,
                    now_iso,
                ),
            )
        await tdb.commit()

        with _frozen_panel_clock():
            backlog = await self.db.aggregate_kds_backlog()
        changfen = next(s for s in backlog["stations"] if s["station_id"] == "changfen")
        self.assertEqual(changfen["pending"], 8)
        self.assertEqual(changfen["load_level"], "medium")

    async def test_get_table_live_list_returns_occupied_sorted_by_duration(self):
        # tables.duration 单位是分钟（与 POS dinnerTime 一致）
        tables = [
            {"table_number": "T1", "amount": 120.0, "people": 2, "duration": 30},
            {"table_number": "T2", "amount": 80.0, "people": 3, "duration": 60},
            {"table_number": "T3", "amount": 0.0, "people": 0, "duration": 0},
        ]
        await self.db.save_table_data(tables)

        live = await self.db.get_table_live_list()

        self.assertEqual(live["total_occupied"], 2)
        self.assertEqual([row["table_number"] for row in live["tables"]], ["T2", "T1"])
        self.assertEqual(live["tables"][0]["duration_minutes"], 60)
        self.assertEqual(live["tables"][1]["amount"], 120.0)

    async def test_aggregate_kds_backlog_excludes_loumian_and_yesterday(self):
        # 票 26 / R-T8-03：窗口与聚合必须冻在**同一时刻**——这里 business_start 按 _PANEL_NOW
        # 推，聚合调用放进 _frozen_panel_clock()。只冻一侧（例如窗口取墙钟 datetime.now()、
        # 聚合不冻钟）仍会随墙钟翻页：跨 06:00 的瞬间造数用的是旧营业日窗口、聚合按新窗口
        # 查，previous_business_day 会掉进（或整段掉出）窗口，断言在这几百毫秒内亚秒级假红。
        business_start, _business_end = business_day_window(_PANEL_NOW)
        in_window = (business_start + timedelta(hours=1)).isoformat()
        previous_business_day = (business_start - timedelta(minutes=1)).isoformat()

        await self._insert_order(
            business_flow_id="kitchen-today",
            station="shulong",
            order_time=in_window,
        )
        await self._insert_order(
            business_flow_id="loumian-today",
            station="loumian",
            order_time=in_window,
        )
        await self._insert_order(
            business_flow_id="kitchen-yesterday",
            station="shulong",
            order_time=previous_business_day,
        )

        with _frozen_panel_clock():
            backlog = await self.db.aggregate_kds_backlog()

        self.assertEqual(backlog["total_pending"], 1)
        self.assertEqual(backlog["busiest_station"]["station_id"], "shulong")
        station_ids = {s["station_id"] for s in backlog["stations"]}
        self.assertNotIn("loumian", station_ids)


if __name__ == "__main__":
    unittest.main()
