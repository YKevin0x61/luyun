#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作台 nudge module：topic / scope / 收件人数 / 去重。

不启动 lifespan、不连库：patch 点是这个 module 自己的 `realtime_hub` 单例
（与 `services.realtime.hub.realtime_hub` 是同一个对象），和既有 API 测试的写法
一致。这里只验「谁会被叫醒、scope 怎么拼」，hub 的归属隔离与主题白名单仍由
`tests/test_ws_hub.py` / `tests/test_ws_staff_scope_isolation.py` 自己守。
"""

import asyncio
import unittest

import services.realtime.workbench as workbench
from services.realtime.hub import realtime_hub


def _run(coro):
    return asyncio.run(coro)


class WorkbenchNudgeTest(unittest.TestCase):
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

    def test_scheduling_fans_out_one_nudge_per_employee_without_duplicates(self):
        """换班牵两个人：一人一条、scope 各带各的 id；重复与 None 都不额外播。"""
        _run(workbench.nudge_scheduling("swap_answered", 9, 7, 9, None))

        self.assertEqual(
            sorted(self.calls, key=lambda call: call[1]["employee_id"]),
            [
                ("scheduling", {"reason": "swap_answered", "employee_id": 7}),
                ("scheduling", {"reason": "swap_answered", "employee_id": 9}),
            ],
        )

    def test_scheduling_without_a_known_employee_broadcasts_nothing(self):
        """连申请人是谁都不知道时不推全店 —— nudge 不带数据，但时序不能乱给。"""
        _run(workbench.nudge_scheduling("request_rejected", None))

        self.assertEqual(self.calls, [])

    def test_overtime_uses_its_own_topic_and_owner_scope(self):
        """台账是另一条主题：同一笔只牵一个人，重复 id 只播一条。"""
        _run(workbench.nudge_overtime("entry_approved", 12, 12))

        self.assertEqual(
            self.calls,
            [("overtime", {"reason": "entry_approved", "employee_id": 12})],
        )

    def test_hygiene_store_wide_event_carries_no_employee_id(self):
        """全店级卫生事件（看板 / 档口 / 配置）不带 employee_id，员工也照常收。"""
        _run(workbench.nudge_hygiene("boards", "changed"))

        self.assertEqual(
            self.calls, [("hygiene", {"resource": "boards", "action": "changed"})]
        )

    def test_hygiene_personal_event_carries_owner_and_extra_scope(self):
        """个人事件带 employee_id 与额外字段（item_id 等），一条只叫一个人。"""
        _run(
            workbench.nudge_hygiene(
                "daily", "submitted", item_id=3, employee_id=42
            )
        )

        self.assertEqual(
            self.calls,
            [
                (
                    "hygiene",
                    {
                        "resource": "daily",
                        "action": "submitted",
                        "item_id": 3,
                        "employee_id": 42,
                    },
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
