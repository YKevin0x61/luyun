#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工餐采购的数据层（迁移 0053）：菜单日、采购单、菜品快照、条目与汇总。

这一层不测业务规则（谁能改、提交校验、推送），那是 service 的用例。这里只回答：
表建得对、唯一键/级联/汇总的 SQL 行为符合预期、缺表时的错误是「去应用迁移」而不是 500。
"""

import unittest
from datetime import datetime

from database import CHINA_TZ, DatabaseManager
from services.staff_meal.store import (
    StaffMealError,
    StaffMealStore,
    month_bounds,
)

# 2026-11-11 18:00（北京时）。使用日是 11-13。
FIXED_NOW = datetime(2026, 11, 11, 18, 0, tzinfo=CHINA_TZ)
DAY = "2026-11-13"
DAY2 = "2026-11-14"


def _menu_day(**overrides):
    day = {
        "menu_date": DAY,
        "weekday": 4,
        "lunch_main": "辣子鸡",
        "lunch_side1": "平菇炒牛肉丸",
        "lunch_side2": "耗油娃娃菜",
        "dinner_main": "菠萝粒酸甜排骨",
        "dinner_side1": "洋葱炒猪颈肉",
        "dinner_side2": "耗油娃娃菜",
        "drink": "圣女果",
        "freeform": False,
        "source": "xlsx",
    }
    day.update(overrides)
    return day


class StaffMealStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DatabaseManager()
        await self.db.connect()
        self.store = StaffMealStore(self.db, now=lambda: FIXED_NOW)

    async def asyncTearDown(self):
        await self.db.close()

    # ── 菜单日 ────────────────────────────────────────────────

    async def test_menu_day_upsert_is_one_row_per_date(self):
        saved = await self.store.upsert_menu_day(_menu_day())
        self.assertEqual(saved["menu_date"], DAY)
        self.assertEqual(saved["lunch_main"], "辣子鸡")
        self.assertFalse(saved["freeform"])

        again = await self.store.upsert_menu_day(_menu_day(lunch_main="白切鸡"))
        self.assertEqual(again["id"], saved["id"], "同一天必须还是同一行")
        self.assertEqual(again["lunch_main"], "白切鸡")

        days = await self.store.menu_days_month("2026-11")
        self.assertEqual(len(days), 1)

    async def test_freeform_day_roundtrip(self):
        saved = await self.store.upsert_menu_day(
            _menu_day(
                freeform=True,
                lunch_main="自主发挥",
                lunch_side1=None,
                lunch_side2=None,
                dinner_main=None,
                dinner_side1=None,
                dinner_side2=None,
                drink=None,
            )
        )
        self.assertTrue(saved["freeform"])
        self.assertIsNone(saved["dinner_main"])

    async def test_update_menu_day_requires_existing(self):
        with self.assertRaises(StaffMealError) as ctx:
            await self.store.update_menu_day("2026-12-01", {"lunch_main": "x"})
        self.assertEqual(ctx.exception.code, "menu_day_not_found")

        await self.store.upsert_menu_day(_menu_day())
        updated = await self.store.update_menu_day(DAY, {"drink": "苹果"})
        self.assertEqual(updated["drink"], "苹果")

    async def test_month_bounds(self):
        self.assertEqual(month_bounds("2026-11"), ("2026-11-01", "2026-12-01"))
        self.assertEqual(month_bounds("2026-12"), ("2026-12-01", "2027-01-01"))
        for bad in ("2026-13", "2026-1", "202611", "", None):
            with self.assertRaises(StaffMealError) as ctx:
                month_bounds(bad)
            self.assertEqual(ctx.exception.code, "bad_month")

    # ── 采购单 ────────────────────────────────────────────────

    async def test_ensure_order_is_idempotent(self):
        first = await self.store.ensure_order(DAY)
        second = await self.store.ensure_order(DAY)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["status"], "draft")
        self.assertEqual(second["push_status"], "not_pushed")
        self.assertEqual(second["push_seq"], 0)

    async def test_bump_push_seq_and_update_order(self):
        order = await self.store.ensure_order(DAY)
        self.assertEqual(await self.store.bump_push_seq(order["id"]), 1)
        self.assertEqual(await self.store.bump_push_seq(order["id"]), 2)

        saved = await self.store.update_order(
            order["id"],
            {
                "status": "submitted",
                "submitter_employee_id": 7,
                "submitter_name": "余威威",
                "push_status": "pushing",
                "order_date": "1999-01-01",  # 白名单外的字段必须被忽略
            },
        )
        self.assertEqual(saved["status"], "submitted")
        self.assertEqual(saved["submitter_name"], "余威威")
        self.assertEqual(saved["order_date"], DAY)

    # ── 菜品与条目 ────────────────────────────────────────────

    async def _order_with_items(self):
        order = await self.store.ensure_order(DAY)
        dishes = await self.store.replace_dishes(
            order["id"],
            [
                {"dish_name": "辣子鸡", "meal": "lunch", "role": "main"},
                {"dish_name": "菠萝粒酸甜排骨", "meal": "dinner", "role": "main"},
            ],
        )
        items = await self.store.replace_items(
            order["id"],
            [
                {
                    "dish_id": dishes[0]["id"],
                    "item_name": "清远鸡",
                    "quantity": 3,
                    "unit": "只",
                },
                {
                    "dish_id": dishes[1]["id"],
                    "item_name": "排骨",
                    "quantity": 9,
                    "unit": "斤",
                },
                {
                    "dish_id": None,
                    "item_name": "沙姜",
                    "quantity": 0.5,
                    "unit": "斤",
                },
            ],
        )
        return order, dishes, items

    async def test_replace_dishes_and_items(self):
        order, dishes, items = await self._order_with_items()
        self.assertEqual([d["dish_name"] for d in dishes], ["辣子鸡", "菠萝粒酸甜排骨"])
        self.assertEqual(len(items), 3)
        self.assertAlmostEqual(items[0]["quantity"], 3.0)
        self.assertIsNone(items[2]["dish_id"])

    async def test_delete_order_cascades(self):
        order, _, _ = await self._order_with_items()
        await self.store.delete_order(order["id"])
        self.assertIsNone(await self.store.order_by_id(order["id"]))
        self.assertEqual(await self.store.dishes_for(order["id"]), [])
        self.assertEqual(await self.store.items_for(order["id"]), [])

    async def test_summary_groups_by_name_and_unit(self):
        order, dishes, _ = await self._order_with_items()
        # 同一天再追加两条同食材同单位、一条数量为空（同名菜只带出名称的场景）
        await self.store.replace_items(
            order["id"],
            [
                {"dish_id": dishes[1]["id"], "item_name": "排骨", "quantity": 9, "unit": "斤"},
                {"dish_id": None, "item_name": "排骨", "quantity": 5, "unit": "斤"},
                {"dish_id": None, "item_name": "排骨", "quantity": 1, "unit": "公斤"},
                {"dish_id": None, "item_name": "空心菜", "quantity": None, "unit": "斤"},
            ],
        )
        rows = await self.store.summary_rows(order["id"])
        by_key = {(row["item_name"], row["unit"]): row["quantity"] for row in rows}
        self.assertAlmostEqual(by_key[("排骨", "斤")], 14.0)
        self.assertAlmostEqual(by_key[("排骨", "公斤")], 1.0, places=2)
        self.assertIsNone(by_key[("空心菜", "斤")])

    async def test_item_names_for_dish_history(self):
        order, dishes, _ = await self._order_with_items()
        order2 = await self.store.ensure_order(DAY2)
        dishes2 = await self.store.replace_dishes(
            order2["id"],
            [{"dish_name": "菠萝粒酸甜排骨", "meal": "dinner", "role": "main"}],
        )
        await self.store.replace_items(
            order2["id"],
            [{"dish_id": dishes2[0]["id"], "item_name": "排骨", "quantity": 5, "unit": "斤"}],
        )

        rows = await self.store.item_names_for_dish("菠萝粒酸甜排骨")
        self.assertEqual(rows[0]["item_name"], "排骨")
        self.assertEqual(rows[0]["uses"], 2)
        self.assertEqual(rows[0]["unit"], "斤")
        self.assertNotIn("quantity", rows[0])

        self.assertEqual(await self.store.item_names_for_dish("不存在的菜"), [])
