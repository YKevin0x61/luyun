#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""班次配色（0043）：色键入库、自动挑空闲色、撞色与停用/重新启用的边界。

主缝 = 服务层 + 真库（同 `tests/test_scheduling.py`）。这一层值得单独测，因为颜色
的**唯一性**是库里的部分唯一索引 + 服务层一道白名单闸共同守着的，前端只负责画。
"""

import tempfile
import unittest
from datetime import datetime

import asyncpg

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.scheduling.store import (
    SHIFT_COLORS,
    SHIFT_COLOR_NEUTRAL,
    SchedulingError,
    SchedulingStore,
    _shift_payload,
)

PASSWORD = "password123"
TODAY = "2026-09-24"


class ShiftColorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.fixed_now = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
        self.store = SchedulingStore(self.db, now=lambda: self.fixed_now)
        await self.store.prepare()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _shift(self, name):
        for shift in await self.store.list_shifts(include_inactive=True):
            if shift["name"] == name:
                return shift
        raise AssertionError(f"没有班次 {name}")

    # ── 默认与自动挑色 ──────────────────────────────────────────────────

    async def test_default_shifts_get_the_first_two_main_colors(self):
        """启动期铺的两条默认班次按顺序拿到前两档主色，不是两条中性灰。"""
        day = await self._shift("白班")
        night = await self._shift("夜班")
        self.assertEqual(day["color_key"], SHIFT_COLORS[0])
        self.assertEqual(night["color_key"], SHIFT_COLORS[1])

    async def test_create_shift_picks_the_first_free_color(self):
        """新建班次不给色：自动拿第一个空闲主色（不是跟着序号重复）。"""
        created = await self.store.create_shift("中班")
        self.assertEqual(created["color_key"], SHIFT_COLORS[2])

    async def test_create_shift_accepts_an_explicit_color(self):
        created = await self.store.create_shift("中班", color_key="lilac")
        self.assertEqual(created["color_key"], "lilac")
        self.assertEqual((await self._shift("中班"))["color_key"], "lilac")

    async def test_a_ninth_active_shift_falls_back_to_neutral(self):
        """8 档都占满之后：第 9 条拿中性灰，而且多条中性灰可以共存（哨兵不占唯一额）。"""
        for index in range(len(SHIFT_COLORS) - 2):  # 默认两条已经占了两档
            await self.store.create_shift(f"班次{index + 3}")
        ninth = await self.store.create_shift("第九班")
        tenth = await self.store.create_shift("第十班")
        self.assertEqual(ninth["color_key"], SHIFT_COLOR_NEUTRAL)
        self.assertEqual(tenth["color_key"], SHIFT_COLOR_NEUTRAL)

    # ── 撞色与白名单 ────────────────────────────────────────────────────

    async def test_duplicate_color_is_refused_with_the_other_shifts_name(self):
        with self.assertRaises(SchedulingError) as caught:
            await self.store.create_shift("中班", color_key=SHIFT_COLORS[0])
        self.assertEqual(caught.exception.code, "shift_color_taken")
        # 文案要能照做：说清是谁占着这档色。
        self.assertIn("白班", str(caught.exception))

    async def test_unknown_color_key_is_refused(self):
        with self.assertRaises(SchedulingError) as caught:
            await self.store.create_shift("中班", color_key="amber")
        self.assertEqual(caught.exception.code, "invalid_shift_color")

    # ── 停用 / 重新启用 ────────────────────────────────────────────────

    async def test_a_stopped_shift_keeps_its_color_but_frees_the_slot(self):
        """停用不丢色，但那一档可以给别人；重新启用时撞色会被拦下来。"""
        day = await self._shift("白班")
        await self.store.update_shift(day["id"], is_active=False)

        # 停用后：自己那一行还带着原色。
        self.assertEqual((await self._shift("白班"))["color_key"], SHIFT_COLORS[0])
        # 但这一档已经空出来了，新班次能拿走。
        created = await self.store.create_shift("中班")
        self.assertEqual(created["color_key"], SHIFT_COLORS[0])

        # 再把白班启用回来就撞上了：报错并点名占着这档色的班次。
        with self.assertRaises(SchedulingError) as caught:
            await self.store.update_shift(day["id"], is_active=True)
        self.assertEqual(caught.exception.code, "shift_color_taken")
        self.assertIn("中班", str(caught.exception))

    async def test_changing_only_the_color_does_not_touch_other_fields(self):
        night = await self._shift("夜班")
        updated = await self.store.update_shift(night["id"], color_key="pine")
        self.assertEqual(updated["color_key"], "pine")
        self.assertEqual(updated["name"], "夜班")
        self.assertEqual(updated["duty_slot"], "night")
        self.assertTrue(updated["is_active"])

    async def test_empty_string_means_neutral_not_untouched(self):
        """空串 = 回中性（跟 `duty_slot` 同一套口径）；`None` 才是不动。"""
        night = await self._shift("夜班")
        cleared = await self.store.update_shift(night["id"], color_key="")
        self.assertEqual(cleared["color_key"], SHIFT_COLOR_NEUTRAL)
        again = await self.store.update_shift(night["id"], name="夜班2")
        self.assertEqual(again["color_key"], SHIFT_COLOR_NEUTRAL)

    # ── 读接口 ──────────────────────────────────────────────────────────

    async def test_calendar_and_week_carry_the_color_key(self):
        """月历与周表都用同一份班次列：缺了它，同一个班次两页两个颜色。"""
        calendar = await self.store.month_calendar("2026-09")
        self.assertTrue(all("color_key" in shift for shift in calendar["shifts"]))
        week = await self.store.week_grid(TODAY)
        self.assertTrue(all("color_key" in shift for shift in week["shifts"]))

    async def test_missing_column_degrades_to_no_color_instead_of_raising(self):
        """列还没建（0043 没应用）时：`color_key` 变成 `None`，页面据此降级。

        直接测行映射而不是去真删一列：删列在测试库里会把后面的用例带崩，
        而读取端的降级判据就是这个 `None`。
        """
        payload = _shift_payload(
            {"id": 1, "name": "白班", "sort_order": 10, "is_active": 1, "duty_slot": "day"},
            color_ready=False,
        )
        self.assertIsNone(payload["color_key"])
        self.assertEqual(payload["name"], "白班")

    async def test_read_path_really_survives_a_missing_column(self):
        """把 `color_key` 那一列真的藏起来，读一次周表：不能 500，只是没颜色。

        `ALTER TABLE ... DROP COLUMN` 在本包里是安全的：本类每个用例都跑在
        `TRUNCATE` 干净基线之上，而列是 conftest 按迁移重建的（下一个用例重新建库前
        不会复用这条连接上的结构判断）。为避免影响其它用例，这里把列加回去。
        """
        await self.db._conn.execute("ALTER TABLE staff_shifts DROP COLUMN color_key")
        await self.db._conn.commit()
        try:
            shifts = await self.store.list_shifts()
            self.assertTrue(shifts)
            self.assertTrue(all(shift["color_key"] is None for shift in shifts))
        finally:
            await self.db._conn.execute(
                'ALTER TABLE staff_shifts ADD COLUMN IF NOT EXISTS "color_key" TEXT NOT NULL DEFAULT $$neutral$$'
            )
            await self.db._conn.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS "uq_staff_shifts_active_color"
                       ON staff_shifts (tenant_id, color_key)
                    WHERE is_active = 1 AND color_key <> 'neutral'"""
            )
            await self.db._conn.commit()

    async def test_unique_index_is_behind_the_service_layer_guard(self):
        """服务层那道闸之外，库里还有一道：绕过它直接写也得报。"""
        with self.assertRaises(asyncpg.UniqueViolationError) as caught:
            await self.db._conn.execute(
                """INSERT INTO staff_shifts (name, sort_order, is_active, duty_slot, color_key, created_at, updated_at)
                   VALUES ('绕过闸', 99, 1, NULL, 'moss', 'x', 'x')"""
            )
        self.assertEqual(
            getattr(caught.exception, "constraint_name", ""), "uq_staff_shifts_active_color"
        )
        await self.db._conn.rollback()


if __name__ == "__main__":
    unittest.main()
