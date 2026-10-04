#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`DutyRoster`（票 10）：卫生读的「今天谁在哪」就是排班结果里的值。

主缝 = 公共层 + 真库 + 注入时钟：排班的窗口与营业日由时钟决定（同 `tests/test_scheduling.py`）。
"""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.accounts import EmployeeAccounts
from services.identity import DutyRoster
from services.scheduling.store import SchedulingStore

PASSWORD = "password123"
# 2026-09-24 是周四，10:00 已经过了 06:00 的切日点 → 营业日就是 9/24。
TODAY = "2026-09-24"


class DutyRosterTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
        self.accounts = EmployeeAccounts(self.db, now=lambda: self.fixed_now)
        self.store = SchedulingStore(self.db, now=lambda: self.fixed_now)
        self.duty = DutyRoster(self.db)
        await self.store.prepare()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _employee(self, phone="13800138000", name="张三"):
        employee = await self.accounts.register(phone, PASSWORD, name)
        await self.accounts.approve(employee["id"])
        return employee

    async def _shift_id(self, name):
        for shift in await self.store.list_shifts():
            if shift["name"] == name:
                return shift["id"]
        raise AssertionError(f"没有班次 {name}")

    async def _zone(self, name="案板"):
        """建一个工作区（表是卫生那条线建的，公共层只读它）。"""
        now = self.fixed_now.isoformat()
        cur = await self.db._conn.execute(
            """INSERT INTO hygiene_zones (name, day_shift, night_shift, created_at, updated_at)
               VALUES (?, 1, 1, ?, ?)""",
            (name, now, now),
        )
        await self.db._conn.commit()
        return int(cur.lastrowid)

    async def test_it_reads_exactly_what_the_schedule_wrote(self):
        """验收 2：卫生拿到的班次与工作区，就是 `staff_assignments` 那一行的值。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        zone = await self._zone("案板")
        await self.store.set_zone_default(employee["id"], day, zone)
        await self.store.set_rule(employee["id"], [day])

        duty = await self.duty.duty_for(employee["id"], TODAY)

        self.assertEqual(
            duty,
            {
                "available": True,
                "scheduled": True,
                "shift_id": day,
                "shift_name": "白班",
                "zone_id": zone,
                "zone_name": "案板",
                # 卫生档位（票 10）：这条班次挂号在卫生的哪一档日常检查上。
                # 「白班」这一档由 `0010` 迁移回填（`prepare()` 建的默认两条一样）。
                "duty_slot": "day",
                "source": "rule",
            },
        )
        # 拿的是**结果行**：跟排班页读的是同一份数据，不是卫生自己算的。
        cur = await self.db._conn.execute(
            """SELECT shift_id, zone_id FROM staff_assignments
               WHERE employee_id = ? AND business_date = ?""",
            (employee["id"], TODAY),
        )
        row = dict(await cur.fetchone())
        self.assertEqual((row["shift_id"], row["zone_id"]), (duty["shift_id"], duty["zone_id"]))

    async def test_a_renamed_shift_comes_back_with_its_new_name(self):
        """班次是数据（票 11 能改名）：这里跟着新名字走，卫生不许按名字对齐。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])

        await self.store.update_shift(day, name="早班")

        duty = await self.duty.duty_for(employee["id"], TODAY)
        self.assertEqual((duty["shift_id"], duty["shift_name"]), (day, "早班"))

    async def test_the_duty_slot_follows_the_shift_id_not_its_name(self):
        """验收 2 的接缝：档位认的是**班次 id**，改名不改档。

        票 10 的转出注记专门点了这个坑：按名字对齐的话，店长把「夜班」改成「晚班」
        的那天，所有夜班的人就都交不了日常了。
        """
        employee = await self._employee()
        night = await self._shift_id("夜班")
        await self.store.set_rule(employee["id"], [night])
        self.assertEqual((await self.duty.duty_for(employee["id"], TODAY))["duty_slot"], "night")

        await self.store.update_shift(night, name="晚班")

        duty = await self.duty.duty_for(employee["id"], TODAY)
        self.assertEqual(
            (duty["shift_id"], duty["shift_name"], duty["duty_slot"]),
            (night, "晚班", "night"),
        )

    async def test_a_shift_without_a_duty_slot_gives_none(self):
        """没标档位的班次 → `duty_slot` 空 = 今天没有日常可交，而不是猜一档。"""
        employee = await self._employee()
        middle = await self.store.create_shift("中班")
        await self.store.set_rule(employee["id"], [middle["id"]])

        duty = await self.duty.duty_for(employee["id"], TODAY)

        self.assertEqual((duty["shift_name"], duty["duty_slot"]), ("中班", None))

    async def test_a_missing_duty_slot_column_reads_as_not_connected_yet(self):
        """0010 还没应用：按「还没接上」降级，不是 500，也不是「今天休」。

        门店的正常顺序是「先更新代码、再在 Admin 里应用增量迁移」，那段窗口里
        员工端不该炸 —— 说一句「今天没有日常可交」比一页 500 好。
        """
        employee = await self._employee()
        day = await self._shift_id("白班")
        await self.store.set_rule(employee["id"], [day])

        await self.db._conn.execute("ALTER TABLE staff_shifts DROP COLUMN duty_slot")
        await self.db._conn.commit()
        try:
            duty = await self.duty.duty_for(employee["id"], TODAY)
            self.assertEqual((duty["available"], duty["scheduled"]), (False, False))
            # 连接还能接着用（失败的事务已经回滚掉了）。
            cur = await self.db._conn.execute("SELECT COUNT(*) AS n FROM staff_shifts")
            self.assertIsNotNone(await cur.fetchone())
        finally:
            await self.db._conn.execute("ALTER TABLE staff_shifts ADD COLUMN duty_slot TEXT")
            await self.db._conn.execute("UPDATE staff_shifts SET duty_slot = 'day' WHERE name = '白班'")
            await self.db._conn.commit()

    async def test_moving_the_zone_on_that_day_follows_immediately(self):
        """验收 6：店长把某天的区改掉（单日覆盖），卫生那边当天就认新的区。"""
        employee = await self._employee()
        day = await self._shift_id("白班")
        first = await self._zone("案板")
        second = await self._zone("熟笼")
        await self.store.set_zone_default(employee["id"], day, first)
        await self.store.set_rule(employee["id"], [day])
        self.assertEqual((await self.duty.duty_for(employee["id"], TODAY))["zone_name"], "案板")

        await self.store.set_override(employee["id"], TODAY, shift_id=day, zone_id=second)

        duty = await self.duty.duty_for(employee["id"], TODAY)
        self.assertEqual(
            (duty["zone_id"], duty["zone_name"], duty["source"]), (second, "熟笼", "override")
        )

    async def test_no_row_rest_day_and_missing_tables_read_differently(self):
        """三种「今天没有班」分得开：还没接上 / 没排到他 / 那天休。"""
        employee = await self._employee()
        day = await self._shift_id("白班")

        # 没配规则：那天没排到他。
        duty = await self.duty.duty_for(employee["id"], TODAY)
        self.assertEqual((duty["available"], duty["scheduled"]), (True, False))
        self.assertEqual(
            (duty["shift_id"], duty["shift_name"], duty["zone_id"], duty["zone_name"]),
            (None, None, None, None),
        )

        # 规则里那一格是休：排到了，只是那天没有班次。
        await self.store.set_rule(employee["id"], [None])
        duty = await self.duty.duty_for(employee["id"], TODAY)
        self.assertEqual((duty["available"], duty["scheduled"]), (True, True))
        self.assertIsNone(duty["shift_id"])

        # 0005 没应用：是「还没接上」，不是「今天休」；不抛异常，连接还能接着用。
        await self.db._conn.execute("ALTER TABLE staff_assignments RENAME TO staff_assignments_tmp")
        await self.db._conn.commit()
        try:
            duty = await self.duty.duty_for(employee["id"], TODAY)
            self.assertEqual((duty["available"], duty["scheduled"]), (False, False))
            cur = await self.db._conn.execute("SELECT COUNT(*) AS n FROM hygiene_zones")
            self.assertIsNotNone(await cur.fetchone())
        finally:
            await self.db._conn.execute(
                "ALTER TABLE staff_assignments_tmp RENAME TO staff_assignments"
            )
            await self.db._conn.commit()

    async def test_a_dangling_shift_id_gives_no_name_instead_of_an_error(self):
        """`staff_assignments.shift_id` 没有外键：班次行被手工删掉时给 `None`，不 500。"""
        employee = await self._employee()
        stamp = self.fixed_now.isoformat()
        await self.db._conn.execute(
            """INSERT INTO staff_assignments
                   (employee_id, business_date, shift_id, zone_id, source, created_at, updated_at)
               VALUES (?, ?, 987654, NULL, 'rule', ?, ?)""",
            (employee["id"], TODAY, stamp, stamp),
        )
        await self.db._conn.commit()

        duty = await self.duty.duty_for(employee["id"], TODAY)

        self.assertEqual((duty["available"], duty["scheduled"]), (True, True))
        self.assertEqual((duty["shift_id"], duty["shift_name"]), (987654, None))
