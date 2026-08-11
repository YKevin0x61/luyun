#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""admin nudge 的 table scope 单测：`broadcast_realtime_event("admin_data_changed",
table=...)` 应把 `table` 放进 nudge 的 scope（此前只搬了 `station`，`table` 被
丢弃，导致前端按表刷新退化为全表刷新）。

不启动完整 main.app lifespan：只 import `main` 模块（触发模块级代码，不触发
`lifespan`），并对 `services.realtime.hub.realtime_hub.broadcast_nudge` 做
实例级 monkeypatch 记录调用（同 `tests/test_ws_orders_nudge.py` 的写法）。
"""

import asyncio
import unittest

import main as main_module
from services.realtime.hub import realtime_hub


def _run_async(coro):
    return asyncio.run(coro)


class AdminNudgeScopeTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

        async def _fake_broadcast_nudge(topic, scope=None):
            self.calls.append((topic, scope or {}))

        self._had_instance_override = "broadcast_nudge" in vars(realtime_hub)
        self._orig_broadcast_nudge = realtime_hub.__dict__.get("broadcast_nudge")
        realtime_hub.broadcast_nudge = _fake_broadcast_nudge

    def tearDown(self):
        if self._had_instance_override:
            realtime_hub.broadcast_nudge = self._orig_broadcast_nudge
        else:
            del realtime_hub.__dict__["broadcast_nudge"]

    def test_admin_data_changed_puts_table_into_scope(self):
        _run_async(main_module.broadcast_realtime_event(
            "admin_data_changed", action="update_row", table="orders", rowid=1,
        ))

        self.assertIn(("admin", {"table": "orders"}), self.calls)

    def test_admin_data_changed_without_table_has_empty_scope(self):
        _run_async(main_module.broadcast_realtime_event(
            "admin_data_changed", action="export_db",
        ))

        self.assertIn(("admin", {}), self.calls)

    def test_admin_data_changed_merges_station_and_table_when_present(self):
        _run_async(main_module.broadcast_realtime_event(
            "admin_data_changed", action="update_row", table="orders", station="shulong",
        ))

        self.assertIn(("admin", {"table": "orders", "station": "shulong"}), self.calls)

    def test_non_admin_event_table_field_still_ignored(self):
        """回归：非 admin topic 的事件即便带 table 字段也不放入 scope（保持既有行为不变）。"""
        _run_async(main_module.broadcast_realtime_event(
            "orders_updated", table="orders", count=3,
        ))

        self.assertIn(("orders", {}), self.calls)

    def test_station_scope_still_works_for_non_admin_events(self):
        """回归：station 字段依然正常搬进 scope，不受本次改动影响。"""
        _run_async(main_module.broadcast_realtime_event(
            "orders_updated", station="shulong", count=1, source="table",
        ))

        self.assertIn(("orders", {"station": "shulong"}), self.calls)


if __name__ == "__main__":
    unittest.main()
