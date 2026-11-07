#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日常损耗登记（从 worktool2 那个小程序搬过来）：台账、统计、模板、照片。

主缝 = 服务层 + 真库 + 注入时钟 + `FakeCaptureStore`。HTTP 面另有一小段薄缝，
只回答「谁能打、打进去会怎样」——两个身份走同一个面，差别在归属与统计那一档 403。

口径来源是源项目的 `cloudfunctions/manageWastage`：六个原因、单位候选、
「其他」必须写说明、每人 20 个模板、一段日期内的四样统计。
"""

import asyncio
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.wastage as wastage_module
import main as main_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from db_core.schema import ADMIN_READ_ONLY_TABLES, WASTAGE_TABLES
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.wastage.store import (
    AMEND_WINDOW_HOURS,
    MAX_IMAGES,
    MAX_TEMPLATES,
    WastageActor,
    WastageError,
    WastageStore,
)

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
PHONE2 = "13900139000"
NAME2 = "李四"
# 2026-09-24 10:00（北京时）→ 自然日的今天 = 9/24。
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-24"
YESTERDAY = "2026-09-23"


def _staff(employee_id, name=NAME):
    return WastageActor(kind="staff", employee_id=employee_id, name=name)


ADMIN_ACTOR = WastageActor(kind="admin", employee_id=None, name="管理端")

# 可变时钟：24h 窗口的边界用例要把「现在」往前推。store 用例与 HTTP fixture 共用它，
# 每个用例开始时回到 FIXED_NOW —— 一个用例推过的钟不能污染下一个。
_CLOCK = {"now": FIXED_NOW}


class WastageStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.accounts = EmployeeAccounts(self.db, now=lambda: FIXED_NOW)
        self.captures = FakeCaptureStore()
        _CLOCK["now"] = FIXED_NOW
        self.store = WastageStore(
            self.db, captures=self.captures, now=lambda: _CLOCK["now"]
        )

    async def asyncTearDown(self):
        _CLOCK["now"] = FIXED_NOW
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _employee(self, phone=PHONE, name=NAME):
        employee = await self.accounts.register(phone, PASSWORD, name)
        await self.accounts.approve(employee["id"])
        return employee

    async def _prep_item(self, name="五花肉", unit="kg"):
        """插一个备货品主数据：损耗记录可选地挂到它上面（0049 预留的 prep_item_id）。"""
        items = self.db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_items (
                    item_name, station, position, category, unit, kind, active,
                    shelf_life_hours, lead_time_hours, min_batch_qty,
                    safety_stock_ratio, notes, created_at, updated_at
                ) VALUES (?, 'shulong', '熟笼', '原料', ?, 'bought', 1, 24, 0, 0, 0.15, '', ?, ?)
                """,
                (name, unit, FIXED_NOW.isoformat(), FIXED_NOW.isoformat()),
            )
            item_id = cursor.lastrowid
        await items.commit()
        return int(item_id)

    # ── 登记与归属 ──────────────────────────────────────────────────────────
    async def test_employee_records_one_and_sees_only_own(self):
        """员工登记一笔：自己看得到，另一位员工看不到（归属是服务层一处判的）。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)

        created = await self.store.add_record(
            _staff(first["id"]),
            item_name="五花肉",
            quantity=1.5,
            unit="kg",
            reason="spoiled",
            remark="夏天没放住",
            wastage_date=TODAY,
        )

        self.assertEqual(created["item_name"], "五花肉")
        self.assertEqual(created["quantity"], 1.5)
        self.assertEqual(created["unit"], "kg")
        self.assertEqual(created["reason"], "spoiled")
        self.assertEqual(created["reason_text"], "变质腐败")
        self.assertEqual(created["wastage_date"], TODAY)
        self.assertEqual(created["operator_kind"], "staff")
        self.assertEqual(created["employee_id"], first["id"])
        self.assertEqual(created["operator_name"], NAME)

        mine = await self.store.list_records(_staff(first["id"]))
        self.assertEqual(mine["total"], 1)
        self.assertEqual(mine["records"][0]["id"], created["id"])

        other = await self.store.list_records(_staff(second["id"]))
        self.assertEqual(other["total"], 0)

    # ── 挂备货品与 07:00 窗口（票 09）──────────────────────────────────────
    async def test_record_can_link_a_prep_item_and_carries_its_0700_window(self):
        """登记时可选挂到备货品：复盘代价按 id 对齐；窗口按自然日的 07:00 起算。

        `window_start` / `window_end` 是 0049 预留的两列（可空）：老行没有，新的登记
        一律写 —— 复盘要的「这笔报废落在哪个品-天」就是靠它和 07:00 主口径对齐。
        """
        employee = await self._employee()
        item_id = await self._prep_item()

        created = await self.store.add_record(
            _staff(employee["id"]),
            item_name="五花肉",
            quantity=1.5,
            unit="kg",
            reason="excess",
            wastage_date=TODAY,
            prep_item_id=item_id,
        )

        self.assertEqual(created["prep_item_id"], item_id)
        # 2026-09-24 07:00 → 次日 07:00（自然日的一个 24 小时窗口）。
        self.assertEqual(created["window_start"], "2026-09-24T07:00:00+08:00")
        self.assertEqual(created["window_end"], "2026-09-25T07:00:00+08:00")

        listed = await self.store.list_records(_staff(employee["id"]))
        self.assertEqual(listed["records"][0]["prep_item_id"], item_id)

    async def test_record_without_a_prep_item_leaves_the_link_empty(self):
        """不选备货品：与改造前一个字都不差（两列为空，复盘用品名兜底）。"""
        employee = await self._employee()
        created = await self.store.add_record(
            _staff(employee["id"]),
            item_name="青菜",
            quantity=1,
            unit="份",
            reason="expired",
            wastage_date=TODAY,
        )
        self.assertIsNone(created["prep_item_id"])
        # 窗口照样写：没有备货品的报废也要落进某一天（品名兜底时用它对齐）。
        self.assertEqual(created["window_start"], "2026-09-24T07:00:00+08:00")

    async def test_rejects_an_unknown_prep_item(self):
        """挂一个不存在的备货品 = 复盘里对齐不到任何品，直接拒（中文 400）。"""
        employee = await self._employee()
        with self.assertRaises(WastageError) as caught:
            await self.store.add_record(
                _staff(employee["id"]),
                item_name="五花肉",
                quantity=1,
                unit="kg",
                reason="excess",
                wastage_date=TODAY,
                prep_item_id=999999,
            )
        self.assertEqual(caught.exception.code, "unknown_prep_item")

    async def test_admin_sees_the_whole_shop(self):
        """管理端那一档不加人过滤：谁的损耗都在分析页里。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        for actor, item in ((_staff(first["id"]), "青菜"), (_staff(second["id"]), "豆腐")):
            await self.store.add_record(
                actor, item_name=item, quantity=1, unit="份", reason="expired"
            )
        await self.store.add_record(
            ADMIN_ACTOR, item_name="鸡蛋", quantity=6, unit="个", reason="damaged"
        )

        listed = await self.store.list_records(ADMIN_ACTOR)
        self.assertEqual(listed["total"], 3)
        # 新的在前（同一天登记的按 id 倒序，最后插的那条在最前）。
        self.assertEqual(listed["records"][0]["item_name"], "鸡蛋")
        self.assertEqual(listed["records"][0]["operator_kind"], "admin")
        self.assertIsNone(listed["records"][0]["employee_id"])
        self.assertEqual(listed["records"][0]["operator_name"], "管理端")

    async def test_filters_by_range_reason_and_keyword(self):
        """明细的三把筛子：日期范围、原因、物料名（关键词走参数化的 LIKE）。"""
        employee = await self._employee()
        actor = _staff(employee["id"])
        await self.store.add_record(
            actor, item_name="五花肉", quantity=1, unit="kg", reason="spoiled",
            wastage_date=TODAY,
        )
        await self.store.add_record(
            actor, item_name="豆芽", quantity=2, unit="斤", reason="excess",
            wastage_date=YESTERDAY,
        )

        today_only = await self.store.list_records(
            actor, start_date=TODAY, end_date=TODAY
        )
        self.assertEqual([r["item_name"] for r in today_only["records"]], ["五花肉"])

        by_reason = await self.store.list_records(actor, reason="excess")
        self.assertEqual([r["item_name"] for r in by_reason["records"]], ["豆芽"])

        by_keyword = await self.store.list_records(actor, keyword="花")
        self.assertEqual([r["item_name"] for r in by_keyword["records"]], ["五花肉"])

        # 关键词里的 LIKE 通配符要当字面量：搜一个 `%` 不该把全表捞出来。
        literal = await self.store.list_records(actor, keyword="%")
        self.assertEqual(literal["total"], 0)

    # ── 校验 ────────────────────────────────────────────────────────────────
    async def test_input_guards(self):
        """数量、单位、原因、「其他」的说明、未来的日子。"""
        employee = await self._employee()
        actor = _staff(employee["id"])

        async def rejected(code, **kwargs):
            payload = {
                "item_name": "青菜",
                "quantity": 1,
                "unit": "份",
                "reason": "spoiled",
            }
            payload.update(kwargs)
            with self.assertRaises(WastageError) as caught:
                await self.store.add_record(actor, **payload)
            self.assertEqual(caught.exception.code, code)

        await rejected("missing_item_name", item_name="  ")
        await rejected("quantity_not_positive", quantity=0)
        await rejected("quantity_not_positive", quantity=-1)
        await rejected("invalid_quantity", quantity="abc")
        await rejected("missing_unit", unit="")
        await rejected("invalid_reason", reason="melted")
        await rejected("missing_remark", reason="other", remark="")
        await rejected("future_day", wastage_date="2026-09-25")
        await rejected("remark_too_long", remark="字" * 201)

        # 「其他」写了说明就能过；三位小数是允许的（界面上 1.25 kg 很常见）。
        ok = await self.store.add_record(
            actor,
            item_name="青菜",
            quantity=1.25,
            unit="kg",
            reason="other",
            remark="客人退菜",
        )
        self.assertEqual(ok["quantity"], 1.25)
        self.assertEqual(ok["remark"], "客人退菜")

    # ── 统计 ────────────────────────────────────────────────────────────────
    async def test_statistics_totals_reasons_ranking_and_daily(self):
        """按月统计四样：总笔数 / 物料种类 / 原因分布 / 物料排行 / 逐日趋势。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        a, b = _staff(first["id"]), _staff(second["id"])

        await self.store.add_record(
            a, item_name="五花肉", quantity=2, unit="kg", reason="spoiled",
            wastage_date="2026-09-02",
        )
        await self.store.add_record(
            a, item_name="五花肉", quantity=1, unit="kg", reason="spoiled",
            wastage_date="2026-09-02",
        )
        await self.store.add_record(
            b, item_name="豆腐", quantity=3, unit="盒", reason="expired",
            wastage_date="2026-09-05",
        )
        # 上个月的这一笔不该进 9 月的账。
        await self.store.add_record(
            b, item_name="豆芽", quantity=1, unit="斤", reason="excess",
            wastage_date="2026-08-31",
        )

        stats = await self.store.statistics(month="2026-09")

        self.assertEqual(stats["start_date"], "2026-09-01")
        self.assertEqual(stats["end_date"], "2026-09-30")
        self.assertEqual(stats["total_count"], 3)
        self.assertEqual(stats["item_type_count"], 2)
        self.assertEqual(
            [(row["reason"], row["count"], row["percentage"]) for row in stats["by_reason"]],
            [("spoiled", 2, 67), ("expired", 1, 33)],
        )
        self.assertEqual(
            [(row["item_name"], row["count"], row["total_quantity"], row["unit"])
             for row in stats["top_items"]],
            [("五花肉", 2, 3.0, "kg"), ("豆腐", 1, 3.0, "盒")],
        )
        self.assertEqual(
            [(row["date"], row["count"]) for row in stats["daily_stats"]],
            [("2026-09-02", 2), ("2026-09-05", 1)],
        )

    async def test_statistics_rejects_bad_month(self):
        with self.assertRaises(WastageError) as caught:
            await self.store.statistics(month="2026-9")
        self.assertEqual(caught.exception.code, "invalid_month")

    # ── 模板 ────────────────────────────────────────────────────────────────
    async def test_templates_are_saved_listed_and_deleted_by_owner(self):
        """存模板 → 列表里在前 → 只能删自己的 → 自动模板来自自己的历史。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        a, b = _staff(first["id"]), _staff(second["id"])

        await self.store.save_template(a, item_name="五花肉", unit="kg", reason="spoiled")
        # 历史高频：a 记过两次「青菜 / 份 / 变质」，它会作为自动模板补进来。
        for _ in range(2):
            await self.store.add_record(
                a, item_name="青菜", quantity=1, unit="份", reason="spoiled"
            )
        # b 的记录不该出现在 a 的模板里。
        await self.store.add_record(
            b, item_name="豆腐", quantity=1, unit="盒", reason="expired"
        )

        mine = await self.store.templates(a)
        names = [(t["item_name"], t["is_custom"]) for t in mine["templates"]]
        self.assertIn(("五花肉", True), names)
        self.assertIn(("青菜", False), names)
        self.assertNotIn(("豆腐", False), names)

        custom_id = mine["templates"][0]["id"]
        # 别人的模板：对 b 来说是「不存在」。
        with self.assertRaises(WastageError) as caught:
            await self.store.delete_template(b, custom_id)
        self.assertEqual(caught.exception.code, "unknown_template")
        self.assertEqual(len((await self.store.templates(a))["templates"]), 2)

        await self.store.delete_template(a, custom_id)
        self.assertEqual(len((await self.store.templates(a))["templates"]), 1)

    async def test_templates_reject_duplicates_and_the_cap(self):
        employee = await self._employee()
        actor = _staff(employee["id"])
        await self.store.save_template(actor, item_name="五花肉", unit="kg", reason="spoiled")

        with self.assertRaises(WastageError) as caught:
            await self.store.save_template(
                actor, item_name="五花肉", unit="kg", reason="spoiled"
            )
        self.assertEqual(caught.exception.code, "template_exists")

        for index in range(MAX_TEMPLATES + 2):
            name = f"物料{index}"
            try:
                await self.store.save_template(
                    actor, item_name=name, unit="份", reason="expired"
                )
            except WastageError as exc:
                self.assertEqual(exc.code, "too_many_templates")
                break
        else:  # pragma: no cover - 上限没生效才会走到这里
            self.fail("模板数超过上限却没有被拒")

        rows = await self.store.templates(actor)
        custom = [t for t in rows["templates"] if t["is_custom"]]
        self.assertEqual(len(custom), MAX_TEMPLATES)

    # ── 照片 ────────────────────────────────────────────────────────────────
    async def test_images_bind_to_the_record_and_stay_private(self):
        """先传图、再带 id 提交：图片挂上记录，别人读不到。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        a, b = _staff(first["id"]), _staff(second["id"])

        image = await self.store.add_image(a, b"\xff\xd8\xff\xe0jpeg", "image/jpeg")
        # 真实存储给的是 32 位十六进制文件名；测试里注入的是 `FakeCaptureStore`，
        # 只断言「拿到了一个 id」——形状由那两套实现各自保证。
        self.assertTrue(image["capture_id"])

        record = await self.store.add_record(
            a,
            item_name="五花肉",
            quantity=1,
            unit="kg",
            reason="spoiled",
            image_ids=[image["capture_id"]],
        )
        self.assertEqual(len(record["images"]), 1)
        self.assertEqual(record["images"][0]["capture_id"], image["capture_id"])
        self.assertEqual(
            record["images"][0]["url"], f"/api/wastage/images/{image['capture_id']}"
        )

        # 归属：a 读得到，b 读不到（一律「不存在」，不告诉 b 这张图存在）。
        self.assertIsNotNone(await self.store.image_meta(a, image["capture_id"]))
        self.assertIsNone(await self.store.image_meta(b, image["capture_id"]))
        self.assertIsNone(await self.store.image_bytes(b, image["capture_id"]))
        # 管理端看得到全店的图（分析页要点开看）。
        self.assertIsNotNone(await self.store.image_meta(ADMIN_ACTOR, image["capture_id"]))

    async def test_image_guards(self):
        """别人的图不能被绑到自己这条记录上；超过 3 张不收。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        a, b = _staff(first["id"]), _staff(second["id"])

        stolen = await self.store.add_image(b, b"\xff\xd8\xff\xe0jpeg", "image/jpeg")
        with self.assertRaises(WastageError) as caught:
            await self.store.add_record(
                a,
                item_name="五花肉",
                quantity=1,
                unit="kg",
                reason="spoiled",
                image_ids=[stolen["capture_id"]],
            )
        self.assertEqual(caught.exception.code, "unknown_image")

        ids = [
            (await self.store.add_image(a, b"\xff\xd8\xff\xe0jpeg", "image/jpeg"))["capture_id"]
            for _ in range(MAX_IMAGES + 1)
        ]
        with self.assertRaises(WastageError) as caught:
            await self.store.add_record(
                a,
                item_name="五花肉",
                quantity=1,
                unit="kg",
                reason="spoiled",
                image_ids=ids,
            )
        self.assertEqual(caught.exception.code, "too_many_images")

    async def test_history_suggestions_come_from_own_records(self):
        """名字补全：员工只看到自己记过的，管理端看到全店的。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        a, b = _staff(first["id"]), _staff(second["id"])
        await self.store.add_record(a, item_name="五花肉", quantity=1, unit="kg", reason="spoiled")
        await self.store.add_record(b, item_name="豆腐", quantity=1, unit="盒", reason="expired")

        self.assertEqual(
            await self.store.suggestions(a), [{"item_name": "五花肉", "unit": "kg"}]
        )
        self.assertEqual(
            sorted(await self.store.suggestions(ADMIN_ACTOR), key=lambda d: d["item_name"]),
            [{"item_name": "五花肉", "unit": "kg"}, {"item_name": "豆腐", "unit": "盒"}],
        )

    # ── 更正：24 小时内、仅限本人 ────────────────────────────────────────────
    async def _revisions(self, record_id):
        cur = await self.db._conn.execute(
            'SELECT "action", operator_kind, employee_id, operator_name,'
            " before_json, after_json, created_at"
            " FROM wastage_record_revisions WHERE record_id = ? ORDER BY id",
            (int(record_id),),
        )
        return await cur.fetchall()

    async def test_owner_amends_within_window_and_leaves_a_revision(self):
        """本人 24h 内改数量与原因：值变、仍可改、修订里留改前改后（照片不在快照里）。"""
        employee = await self._employee()
        actor = _staff(employee["id"])
        created = await self.store.add_record(
            actor,
            item_name="五花肉",
            quantity=1.5,
            unit="kg",
            reason="spoiled",
            wastage_date=TODAY,
        )
        self.assertTrue(created["can_amend"])
        self.assertEqual(created["amend_deadline_at"], "2026-09-25T10:00:00+08:00")

        amended = await self.store.amend_record(
            actor, created["id"], quantity=2.25, reason="expired"
        )
        self.assertEqual(amended["quantity"], 2.25)
        self.assertEqual(amended["reason"], "expired")
        self.assertEqual(amended["reason_text"], "过期丢弃")
        self.assertTrue(amended["can_amend"])
        # 没传的字段不动（partial update，不是整条覆盖）。
        self.assertEqual(amended["item_name"], "五花肉")
        self.assertEqual(amended["unit"], "kg")
        self.assertEqual(amended["wastage_date"], TODAY)

        rows = await self._revisions(created["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "edit")
        self.assertEqual(rows[0]["operator_kind"], "staff")
        self.assertEqual(rows[0]["employee_id"], employee["id"])
        self.assertEqual(rows[0]["operator_name"], NAME)
        self.assertEqual(rows[0]["created_at"], FIXED_NOW.isoformat())
        before = json.loads(rows[0]["before_json"])
        after = json.loads(rows[0]["after_json"])
        self.assertEqual((before["quantity"], before["reason"]), (1.5, "spoiled"))
        self.assertEqual((after["quantity"], after["reason"]), (2.25, "expired"))
        self.assertNotIn("images", before)
        self.assertNotIn("images", after)

        # 改完仍要满足登记时的规则：「其他」必须带说明、日子不能是明天。
        with self.assertRaises(WastageError) as caught:
            await self.store.amend_record(actor, created["id"], reason="other")
        self.assertEqual(caught.exception.code, "missing_remark")
        with self.assertRaises(WastageError) as caught:
            await self.store.amend_record(actor, created["id"], wastage_date="2026-09-25")
        self.assertEqual(caught.exception.code, "future_day")

        # 什么都没传不算一次更正：不写库，也不凭空留一条修订。
        untouched = await self.store.amend_record(actor, created["id"])
        self.assertEqual(untouched["quantity"], 2.25)
        self.assertEqual(len(await self._revisions(created["id"])), 1)

    async def test_only_the_owner_can_amend(self):
        """别人记的那一笔：改不了（403 not_owner），也撤回不了。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        created = await self.store.add_record(
            _staff(first["id"]),
            item_name="五花肉",
            quantity=1,
            unit="kg",
            reason="spoiled",
        )

        with self.assertRaises(WastageError) as caught:
            await self.store.amend_record(_staff(second["id"]), created["id"], quantity=2)
        self.assertEqual(caught.exception.code, "not_owner")
        with self.assertRaises(WastageError) as caught:
            await self.store.revoke_record(_staff(second["id"]), created["id"])
        self.assertEqual(caught.exception.code, "not_owner")

        # 失败的那两次没有留下任何痕迹：值没变、也没修订。
        still = await self.store.record_by_id(created["id"])
        self.assertEqual(still["quantity"], 1.0)
        self.assertEqual(await self._revisions(created["id"]), [])

    async def test_window_closes_after_24_hours(self):
        """从 created_at 算的 24h：过了就 409；明细里的 can_amend 也跟着变 false。"""
        employee = await self._employee()
        actor = _staff(employee["id"])
        created = await self.store.add_record(
            actor, item_name="五花肉", quantity=1, unit="kg", reason="spoiled"
        )

        _CLOCK["now"] = FIXED_NOW + timedelta(hours=24, seconds=1)
        self.assertLess(created["amend_deadline_at"], _CLOCK["now"].isoformat())
        with self.assertRaises(WastageError) as caught:
            await self.store.amend_record(actor, created["id"], quantity=2)
        self.assertEqual(caught.exception.code, "amend_window_closed")
        with self.assertRaises(WastageError) as caught:
            await self.store.revoke_record(actor, created["id"])
        self.assertEqual(caught.exception.code, "amend_window_closed")

        listed = await self.store.list_records(actor)
        self.assertFalse(listed["records"][0]["can_amend"])
        # 窗口关的是「更正」，不是「读」：这一笔还在明细里。
        self.assertEqual(listed["total"], 1)

    async def test_window_boundary_just_inside_is_still_open(self):
        """恰好在 24h 内（+23h59m）还能改；卡在 24h 整点也还算「24 小时内」。"""
        employee = await self._employee()
        actor = _staff(employee["id"])
        created = await self.store.add_record(
            actor, item_name="五花肉", quantity=1, unit="kg", reason="spoiled"
        )

        _CLOCK["now"] = FIXED_NOW + timedelta(hours=23, minutes=59)
        amended = await self.store.amend_record(actor, created["id"], quantity=2)
        self.assertEqual(amended["quantity"], 2.0)

        _CLOCK["now"] = FIXED_NOW + timedelta(hours=24)
        again = await self.store.amend_record(actor, created["id"], quantity=3)
        self.assertEqual(again["quantity"], 3.0)
        self.assertTrue(again["can_amend"])
        self.assertEqual(len(await self._revisions(created["id"])), 2)

    async def test_revoke_hides_the_record_from_every_read_path(self):
        """软删：明细、统计、补全、模板历史都不再看见它，但行还在库里（留痕）。"""
        employee = await self._employee()
        actor = _staff(employee["id"])
        keep = await self.store.add_record(
            actor, item_name="豆腐", quantity=1, unit="盒", reason="expired",
            wastage_date=TODAY,
        )
        gone = await self.store.add_record(
            actor, item_name="青菜", quantity=2, unit="份", reason="spoiled",
            wastage_date=TODAY,
        )

        revoked = await self.store.revoke_record(actor, gone["id"])
        self.assertIsNotNone(revoked["revoked_at"])
        self.assertFalse(revoked["can_amend"])
        self.assertIsNone(await self.store.record_by_id(gone["id"]))

        listed = await self.store.list_records(actor)
        self.assertEqual([row["id"] for row in listed["records"]], [keep["id"]])

        stats = await self.store.statistics(month="2026-09")
        self.assertEqual(stats["total_count"], 1)
        self.assertEqual([row["item_name"] for row in stats["top_items"]], ["豆腐"])

        self.assertEqual(
            await self.store.suggestions(actor), [{"item_name": "豆腐", "unit": "盒"}]
        )
        templates = await self.store.templates(actor)
        self.assertEqual(
            [t["item_name"] for t in templates["templates"]], ["豆腐"]
        )

        # 行没有被物理删：撤回时间与撤回人都在。
        cur = await self.db._conn.execute(
            'SELECT revoked_at, revoked_by_kind, revoked_by_employee_id'
            " FROM wastage_records WHERE id = ?",
            (int(gone["id"]),),
        )
        row = await cur.fetchone()
        self.assertIsNotNone(row["revoked_at"])
        self.assertEqual(row["revoked_by_kind"], "staff")
        self.assertEqual(row["revoked_by_employee_id"], employee["id"])

    async def test_revoke_writes_a_revision_with_the_whole_snapshot(self):
        """撤回也留修订：before = 撤回前的整行快照，after 是空对象。"""
        employee = await self._employee()
        actor = _staff(employee["id"])
        created = await self.store.add_record(
            actor, item_name="五花肉", quantity=1.5, unit="kg", reason="spoiled",
            wastage_date=TODAY,
        )

        await self.store.revoke_record(actor, created["id"])
        rows = await self._revisions(created["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "revoke")
        self.assertEqual(rows[0]["employee_id"], employee["id"])
        before = json.loads(rows[0]["before_json"])
        self.assertEqual(before["item_name"], "五花肉")
        self.assertEqual(before["quantity"], 1.5)
        self.assertEqual(json.loads(rows[0]["after_json"]), {})

    async def test_revoked_record_cannot_be_amended_or_revoked_again(self):
        """撤回过的对写接口就是「不存在」：再改再撤回都报 unknown_record。"""
        employee = await self._employee()
        actor = _staff(employee["id"])
        created = await self.store.add_record(
            actor, item_name="五花肉", quantity=1, unit="kg", reason="spoiled"
        )
        await self.store.revoke_record(actor, created["id"])

        with self.assertRaises(WastageError) as caught:
            await self.store.amend_record(actor, created["id"], quantity=2)
        self.assertEqual(caught.exception.code, "unknown_record")
        with self.assertRaises(WastageError) as caught:
            await self.store.revoke_record(actor, created["id"])
        self.assertEqual(caught.exception.code, "unknown_record")

    async def test_admin_amend_and_revoke_are_limited_to_admin_records(self):
        """管理端只能改「管理端记的」；员工的记录他改不了（员工也改不了管理端的）。"""
        employee = await self._employee()
        staff_actor = _staff(employee["id"])
        staff_record = await self.store.add_record(
            staff_actor, item_name="五花肉", quantity=1, unit="kg", reason="spoiled"
        )
        admin_record = await self.store.add_record(
            ADMIN_ACTOR, item_name="鸡蛋", quantity=6, unit="个", reason="damaged"
        )

        with self.assertRaises(WastageError) as caught:
            await self.store.amend_record(ADMIN_ACTOR, staff_record["id"], quantity=2)
        self.assertEqual(caught.exception.code, "not_owner")
        with self.assertRaises(WastageError) as caught:
            await self.store.revoke_record(ADMIN_ACTOR, staff_record["id"])
        self.assertEqual(caught.exception.code, "not_owner")
        with self.assertRaises(WastageError) as caught:
            await self.store.amend_record(staff_actor, admin_record["id"], quantity=1)
        self.assertEqual(caught.exception.code, "not_owner")

        amended = await self.store.amend_record(
            ADMIN_ACTOR, admin_record["id"], remark="搬货时磕了"
        )
        self.assertEqual(amended["remark"], "搬货时磕了")
        revoked = await self.store.revoke_record(ADMIN_ACTOR, admin_record["id"])
        self.assertEqual(revoked["revoked_by_kind"], "admin")
        self.assertIsNone(revoked["revoked_by_employee_id"])
        self.assertEqual(len(await self._revisions(admin_record["id"])), 2)


class WastageTableContractTest(unittest.TestCase):
    """新表进表名清单，Admin 的数据浏览器里只读（写入口在 HTTP 那一侧钉）。"""

    def test_new_tables_are_registered_read_only(self):
        self.assertIn("wastage_records", WASTAGE_TABLES)
        for table in WASTAGE_TABLES:
            self.assertIn(table, ADMIN_READ_ONLY_TABLES)

        from api.admin import _admin_catalog

        catalog = _admin_catalog()
        for table in WASTAGE_TABLES:
            self.assertIn(table, catalog["tables"])
            self.assertEqual(catalog["table_meta"][table]["group"], "wastage")
            self.assertTrue(catalog["table_meta"][table]["read_only"])
        groups = {group["key"]: group for group in catalog["groups"]}
        self.assertEqual(set(groups["wastage"]["tables"]), set(WASTAGE_TABLES))


# ── HTTP 薄缝：谁能打、打进去会怎样 ──────────────────────────────────────────


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


def _jpeg_bytes(color="red"):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def wastage_http(tmp_path, monkeypatch):
    real_init = WastageStore.__init__

    def _pinned_init(self, *args, **kwargs):
        kwargs.setdefault("now", lambda: _CLOCK["now"])
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(WastageStore, "__init__", _pinned_init)
    # 照片目录：这一轮指到 tmp_path（`_captures()` 是模块级单例，先清掉上一次的）。
    monkeypatch.setattr(wastage_module, "_capture_store", None)

    _CLOCK["now"] = FIXED_NOW
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    monkeypatch.setattr(main_module, "employee_accounts", accounts)

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(wastage_module.router)
    with TestClient(app) as client:
        init = client.post("/api/auth/init", json=ADMIN)
        assert init.status_code == 200, init.text
        login = client.post(
            "/api/auth/login",
            json={"username": ADMIN["username"], "password": ADMIN["password"]},
        )
        assert login.status_code == 200, login.text
        yield client, db, accounts
    _run(db.close())
    set_runtime(None)
    monkeypatch.setattr(wastage_module, "_capture_store", None)
    settings.DATABASE_DIR = old
    _CLOCK["now"] = FIXED_NOW


def _employee_id(accounts, phone=PHONE, name=NAME):
    employee = _run(accounts.register(phone, PASSWORD, name))
    _run(accounts.approve(employee["id"]))
    return employee["id"]


def _staff_cookie(client, accounts, phone=PHONE):
    session_id = _run(accounts.login(phone, PASSWORD))["session_id"]
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, session_id)
    return session_id


def _admin_cookie(client):
    """换回管理端 cookie（先清掉员工那个）。"""
    client.cookies.clear()
    login = client.post(
        "/api/auth/login",
        json={"username": ADMIN["username"], "password": ADMIN["password"]},
    )
    assert login.status_code == 200, login.text


def test_endpoints_need_some_session(wastage_http):
    """匿名一律 401：登记、明细、统计、上传都进不去。"""
    client, _db, _accounts = wastage_http
    client.cookies.clear()
    assert client.get("/api/wastage/meta").status_code == 401
    assert client.get("/api/wastage/records").status_code == 401
    assert client.get("/api/wastage/statistics").status_code == 401
    assert client.post("/api/wastage/records", json={"item_name": "青菜"}).status_code == 401
    assert client.post(
        "/api/wastage/images", files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")}
    ).status_code == 401


def test_staff_records_and_cannot_read_statistics(wastage_http):
    """员工能登记、只看得到自己的；统计那一条是 403（不是 404）。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    meta = client.get("/api/wastage/meta")
    assert meta.status_code == 200, meta.text
    body = meta.json()
    assert body["today"] == TODAY
    assert [row["value"] for row in body["meta"]["reasons"]] == [
        "spoiled", "expired", "damaged", "production_fail", "excess", "other",
    ]
    assert body["meta"]["max_images"] == MAX_IMAGES

    created = client.post(
        "/api/wastage/records",
        json={
            "item_name": "五花肉",
            "quantity": 1.5,
            "unit": "kg",
            "reason": "spoiled",
            "wastage_date": TODAY,
        },
    )
    assert created.status_code == 200, created.text
    record = created.json()["record"]
    assert record["item_name"] == "五花肉"

    listed = client.get("/api/wastage/records")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    # 校验失败回一句中文 400（不是 pydantic 的英文 422）。
    bad = client.post(
        "/api/wastage/records",
        json={"item_name": "青菜", "quantity": 0, "unit": "份", "reason": "spoiled"},
    )
    assert bad.status_code == 400, bad.text
    assert "数量要大于 0" in bad.json()["detail"]

    forbidden = client.get("/api/wastage/statistics", params={"month": "2026-09"})
    assert forbidden.status_code == 403, forbidden.text


def _prep_item(db, name="五花肉", unit="kg"):
    """插一个备货品主数据，返回 id（登记页下拉的候选项就是它）。"""

    async def work():
        items = db.table("prep_items")
        async with items.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_items (
                    item_name, station, position, category, unit, kind, active,
                    shelf_life_hours, lead_time_hours, min_batch_qty,
                    safety_stock_ratio, notes, created_at, updated_at
                ) VALUES (?, 'shulong', '熟笼', '原料', ?, 'bought', 1, 24, 0, 0, 0.15, '', ?, ?)
                """,
                (name, unit, FIXED_NOW.isoformat(), FIXED_NOW.isoformat()),
            )
            item_id = cursor.lastrowid
        await items.commit()
        return int(item_id)

    return _run(work())


def test_record_can_carry_a_prep_item(wastage_http):
    """登记页可选「对应备货品」：接口收 prep_item_id，落库并回传 07:00 窗口。"""
    client, db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    item_id = _prep_item(db)

    created = client.post(
        "/api/wastage/records",
        json={
            "item_name": "五花肉",
            "quantity": 1.5,
            "unit": "kg",
            "reason": "excess",
            "wastage_date": TODAY,
            "prep_item_id": item_id,
        },
    )
    assert created.status_code == 200, created.text
    record = created.json()["record"]
    assert record["prep_item_id"] == item_id
    assert record["window_start"] == "2026-09-24T07:00:00+08:00"
    assert record["window_end"] == "2026-09-25T07:00:00+08:00"

    # 留空照旧能登记：prep_item_id 为 null，窗口照样有。
    blank = client.post(
        "/api/wastage/records",
        json={
            "item_name": "青菜",
            "quantity": 1,
            "unit": "份",
            "reason": "spoiled",
            "wastage_date": TODAY,
        },
    )
    assert blank.status_code == 200, blank.text
    assert blank.json()["record"]["prep_item_id"] is None

    # 挂一个不存在的备货品：中文 400，不是 500。
    bad = client.post(
        "/api/wastage/records",
        json={
            "item_name": "五花肉",
            "quantity": 1,
            "unit": "kg",
            "reason": "excess",
            "wastage_date": TODAY,
            "prep_item_id": 999999,
        },
    )
    assert bad.status_code == 400, bad.text
    assert "备货品" in bad.json()["detail"]


def test_admin_reads_statistics_and_sees_every_record(wastage_http):
    """管理端：统计拿得到、明细是全店的。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    client.post(
        "/api/wastage/records",
        json={
            "item_name": "五花肉",
            "quantity": 1,
            "unit": "kg",
            "reason": "spoiled",
            "wastage_date": TODAY,
        },
    )

    _admin_cookie(client)
    stats = client.get("/api/wastage/statistics", params={"month": "2026-09"})
    assert stats.status_code == 200, stats.text
    payload = stats.json()
    assert payload["total_count"] == 1
    assert payload["by_reason"][0]["reason"] == "spoiled"
    assert payload["by_reason"][0]["percentage"] == 100

    listed = client.get("/api/wastage/records")
    assert listed.json()["total"] == 1


# ── 共用电脑的两套 cookie：页面用 `?identity=` 声明「此刻按谁看」（Slice A 修复）──
#
# `/workbench/staff/loss` 的落点身份是 `both`（员工与店长都能进同一页）。两套 cookie
# 都在时后端默认「管理端优先」（与 CSRF / WS 那几处一致），于是员工页会把员工记的账
# 归属成管理端、明细还能看到全店。页面按工作台身份给请求加 `?identity=staff|admin`：
# 声明**只选凭据、不给身份**（fail-closed），不带声明时优先级逐字不变。


def _employee_session(accounts, phone=PHONE):
    return _run(accounts.login(phone, PASSWORD))["session_id"]


def _dual_cookie(client, accounts, phone=PHONE):
    """共用电脑：管理端会话还在，员工又登了自己的会话（两套 cookie 都在）。"""
    _admin_cookie(client)
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, _employee_session(accounts, phone))


def _record_body(item="青菜", qty=1.5):
    return {
        "item_name": item,
        "quantity": qty,
        "unit": "kg",
        "reason": "expired",
        "wastage_date": TODAY,
    }


def test_dual_cookie_without_declaration_keeps_admin_first(wastage_http):
    """不带声明的调用优先级逐字不变：两套 cookie 都在时仍然是管理端（opt-in 的声明）。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _dual_cookie(client, accounts)

    created = client.post("/api/wastage/records", json=_record_body())
    assert created.status_code == 200, created.text
    record = created.json()["record"]
    assert record["operator_kind"] == "admin"
    assert record["employee_id"] is None


def test_dual_cookie_staff_declaration_attributes_the_record_to_the_employee(wastage_http):
    """员工页声明 `?identity=staff` 后，写按员工归属（不是共用账号的管理端）。"""
    client, _db, accounts = wastage_http
    employee_id = _employee_id(accounts)
    _dual_cookie(client, accounts)

    created = client.post(
        "/api/wastage/records", params={"identity": "staff"}, json=_record_body()
    )
    assert created.status_code == 200, created.text
    record = created.json()["record"]
    assert record["operator_kind"] == "staff"
    assert record["employee_id"] == employee_id


def test_dual_cookie_staff_declaration_sees_only_his_own_records(wastage_http):
    """员工页声明后，明细只有自己记的那几笔 —— 看不到同事记的（含照片与「谁记的」）。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _employee_id(accounts, PHONE2, NAME2)

    # 同事先用他自己的员工会话记一笔。
    _staff_cookie(client, accounts, PHONE2)
    other = client.post("/api/wastage/records", json=_record_body(item="豆腐", qty=2))
    assert other.status_code == 200, other.text
    other_record = other.json()["record"]

    # 共用电脑：管理端 cookie + 本人员工 cookie，打开员工那一页并声明身份。
    _dual_cookie(client, accounts)
    listed = client.get("/api/wastage/records", params={"identity": "staff"})
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["records"]] == []
    assert listed.json()["total"] == 0

    # 本人员工记一笔：明细里只有自己的。
    own = client.post(
        "/api/wastage/records", params={"identity": "staff"}, json=_record_body()
    )
    listed = client.get("/api/wastage/records", params={"identity": "staff"})
    assert [row["id"] for row in listed.json()["records"]] == [own.json()["record"]["id"]]
    assert other_record["id"] not in [row["id"] for row in listed.json()["records"]]


def test_dual_cookie_admin_declaration_keeps_the_shop_wide_view(wastage_http):
    """店长用同一页时声明 `?identity=admin`：统计与全店明细照旧。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _dual_cookie(client, accounts)

    own = client.post(
        "/api/wastage/records", params={"identity": "staff"}, json=_record_body()
    )
    assert own.status_code == 200, own.text

    listed = client.get("/api/wastage/records", params={"identity": "admin"})
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["records"]] == [own.json()["record"]["id"]]
    stats = client.get(
        "/api/wastage/statistics", params={"month": "2026-09", "identity": "admin"}
    )
    assert stats.status_code == 200, stats.text
    assert stats.json()["total_count"] == 1


def test_dual_cookie_image_upload_honours_the_declaration(wastage_http):
    """照片上传也要声明：双 cookie 下 `?identity=staff` 传的图属于员工，另一位员工读不到。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _employee_id(accounts, PHONE2, NAME2)
    _dual_cookie(client, accounts)

    upload = client.post(
        "/api/wastage/images",
        params={"identity": "staff"},
        files={"file": ("shot.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert upload.status_code == 200, upload.text
    capture_id = upload.json()["image"]["capture_id"]

    # 同一个请求身份能读；换成另一位员工（不带声明，只有他自己的 cookie）读不到。
    assert (
        client.get(f"/api/wastage/images/{capture_id}", params={"identity": "staff"})
        .status_code
        == 200
    )
    _staff_cookie(client, accounts, PHONE2)
    assert client.get(f"/api/wastage/images/{capture_id}").status_code == 404


def test_declared_identity_must_match_the_credential(wastage_http):
    """声明只选凭据、不给身份：那一支验不过就是 401，不回落到另一支（fail-closed）。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)

    # 只有管理端 cookie，却声明 staff。
    _admin_cookie(client)
    assert (
        client.get("/api/wastage/records", params={"identity": "staff"}).status_code
        == 401
    )

    # 只有员工 cookie，却声明 admin。
    _staff_cookie(client, accounts)
    assert (
        client.get("/api/wastage/records", params={"identity": "admin"}).status_code
        == 401
    )

    # 认不出的声明也拒绝，不静默退回默认优先级。
    _dual_cookie(client, accounts)
    for value in ("wibble", ""):
        response = client.get("/api/wastage/records", params={"identity": value})
        assert response.status_code == 401, (value, response.text)
    # 不带声明照旧可用（双 cookie 时走管理端）。
    assert client.get("/api/wastage/records").status_code == 200


def test_images_upload_bind_and_read(wastage_http):
    """照片走真实的 HTTP 链路：传 → 提交时带上 → 自己读得到、别人读不到。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    upload = client.post(
        "/api/wastage/images",
        files={"file": ("shot.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert upload.status_code == 200, upload.text
    capture_id = upload.json()["image"]["capture_id"]

    created = client.post(
        "/api/wastage/records",
        json={
            "item_name": "五花肉",
            "quantity": 1,
            "unit": "kg",
            "reason": "spoiled",
            "wastage_date": TODAY,
            "image_ids": [capture_id],
        },
    )
    assert created.status_code == 200, created.text
    assert len(created.json()["record"]["images"]) == 1

    mine = client.get(f"/api/wastage/images/{capture_id}")
    assert mine.status_code == 200
    assert mine.headers["content-type"].startswith("image/jpeg")

    # 另一位员工（另一个 cookie）读同一张图：404，不是 403。
    _employee_id(accounts, PHONE2, NAME2)
    _staff_cookie(client, accounts, PHONE2)
    assert client.get(f"/api/wastage/images/{capture_id}").status_code == 404
    # 管理端读得到（分析页要点开看）。
    _admin_cookie(client)
    assert client.get(f"/api/wastage/images/{capture_id}").status_code == 200


def test_upload_rejects_a_non_image(wastage_http):
    """不是图片的字节一律 400：类型按字节内容判，不信客户端的 Content-Type。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    rejected = client.post(
        "/api/wastage/images",
        files={"file": ("evil.svg", b"<svg onload=alert(1)></svg>", "image/svg+xml")},
    )
    assert rejected.status_code == 400, rejected.text
    assert "重新拍一张" in rejected.json()["detail"]


def test_upload_explains_the_iphone_heic_case(wastage_http):
    """iPhone 相机默认的 HEIC 要单独说一句：那不是坏图，是这台机器存不了的格式。

    2026-10-17 真机撞过：用系统相机拍 → 后端只回「换个 JPG / PNG」，用户不知道该换什么。
    """
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    # `....ftypheic`：HEIF 容器的文件头（PIL 解不了，除 Safari 外的浏览器也显示不了）。
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 32
    rejected = client.post(
        "/api/wastage/images",
        files={"file": ("IMG_0001.HEIC", heic, "image/heic")},
    )
    assert rejected.status_code == 400, rejected.text
    detail = rejected.json()["detail"]
    assert "HEIC" in detail
    assert "拍一张" in detail


def test_employee_with_wastage_cap_sees_the_whole_shop(wastage_http):
    """开了「损耗分析」（`wastage`）的员工与店长同一面（2026-10-14 用户裁定）。

    判据只在 `admin_caps` 那一列、每次请求现读：给他能力就当场生效，收回就当场 403
    —— 没有第二份「登录时快照的权限」。
    """
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    upload = client.post(
        "/api/wastage/images",
        files={"file": ("shot.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert upload.status_code == 200, upload.text
    capture_id = upload.json()["image"]["capture_id"]
    created = client.post(
        "/api/wastage/records",
        json={
            "item_name": "五花肉",
            "quantity": 1,
            "unit": "kg",
            "reason": "spoiled",
            "wastage_date": TODAY,
            "image_ids": [capture_id],
        },
    )
    assert created.status_code == 200, created.text

    # 另一位员工（李四）自己记一笔 —— 没开能力时只看得到自己那一笔、统计 403。
    other_id = _employee_id(accounts, PHONE2, NAME2)
    _staff_cookie(client, accounts, PHONE2)
    other = client.post(
        "/api/wastage/records",
        json={
            "item_name": "生菜",
            "quantity": 2,
            "unit": "kg",
            "reason": "expired",
            "wastage_date": TODAY,
        },
    )
    assert other.status_code == 200, other.text
    assert client.get("/api/wastage/records").json()["total"] == 1
    assert client.get(
        "/api/wastage/statistics", params={"month": "2026-09"}
    ).status_code == 403
    assert client.get(f"/api/wastage/images/{capture_id}").status_code == 404

    # 勾上「损耗分析」之后（同一个会话，不重登）：统计 200、明细是全店的、别人的照片也看得到。
    _run(accounts.set_admin_caps(other_id, ["wastage"]))
    stats = client.get("/api/wastage/statistics", params={"month": "2026-09"})
    assert stats.status_code == 200, stats.text
    assert stats.json()["total_count"] == 2
    listed = client.get("/api/wastage/records").json()
    assert listed["total"] == 2
    assert {row["item_name"] for row in listed["records"]} == {"五花肉", "生菜"}
    assert client.get(f"/api/wastage/images/{capture_id}").status_code == 200
    # 补全也跟着看全店（他登记时该认得店里所有会坏的物料）。
    suggestions = client.get("/api/wastage/suggestions").json()["suggestions"]
    assert {row["item_name"] for row in suggestions} >= {"五花肉", "生菜"}
    # 点一下 chip 连单位一起填：每条建议都带着这个名字最近一次用的单位。
    assert all(row["unit"] for row in suggestions)

    # 收回就当场收回：下一次请求 403，明细回到只看自己的那几笔。
    _run(accounts.set_admin_caps(other_id, []))
    assert client.get(
        "/api/wastage/statistics", params={"month": "2026-09"}
    ).status_code == 403
    assert client.get("/api/wastage/records").json()["total"] == 1


# ── 更正（HTTP 面）：本人 24h 内可改可撤，别人不行、过了窗口不行 ─────────────


def test_amend_endpoint_returns_new_values_and_list_flags_can_amend(wastage_http):
    """PATCH 回了整条新记录；列表每条都带服务端算好的 can_amend / deadline。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    meta = client.get("/api/wastage/meta").json()
    assert meta["meta"]["amend_window_hours"] == AMEND_WINDOW_HOURS == 24

    created = client.post("/api/wastage/records", json=_record_body("五花肉", 1.5))
    assert created.status_code == 200, created.text
    record_id = created.json()["record"]["id"]
    assert created.json()["record"]["can_amend"] is True

    amended = client.patch(
        f"/api/wastage/records/{record_id}",
        json={"quantity": 2.25, "reason": "spoiled"},
    )
    assert amended.status_code == 200, amended.text
    record = amended.json()["record"]
    assert record["quantity"] == 2.25
    assert record["reason"] == "spoiled"
    assert record["can_amend"] is True

    listed = client.get("/api/wastage/records").json()
    assert listed["total"] == 1
    row = listed["records"][0]
    assert row["can_amend"] is True
    assert row["amend_deadline_at"] == "2026-09-25T10:00:00+08:00"


def test_amend_and_revoke_reject_other_people_and_the_closed_window(wastage_http):
    """归属 403 与窗口 409 是两个错：前者说明「不是你的」，后者说明「太晚了」。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _employee_id(accounts, PHONE2, NAME2)

    _staff_cookie(client, accounts)
    created = client.post("/api/wastage/records", json=_record_body())
    assert created.status_code == 200, created.text
    record_id = created.json()["record"]["id"]

    # 李四改不了、也撤不了张三记的这一笔。
    _staff_cookie(client, accounts, PHONE2)
    denied = client.patch(f"/api/wastage/records/{record_id}", json={"quantity": 2})
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"] == "只能改自己记的那几条"
    assert client.delete(f"/api/wastage/records/{record_id}").status_code == 403

    # 换回张三，把钟推到 25 小时后：窗口关了。
    _staff_cookie(client, accounts)
    _CLOCK["now"] = FIXED_NOW + timedelta(hours=25)
    late = client.patch(f"/api/wastage/records/{record_id}", json={"quantity": 2})
    assert late.status_code == 409, late.text
    assert "超过 24 小时" in late.json()["detail"]
    assert client.delete(f"/api/wastage/records/{record_id}").status_code == 409
    # 窗口关的是「更正」，不是「读」：明细里还在，只是不能再改。
    listed = client.get("/api/wastage/records").json()
    assert listed["total"] == 1
    assert listed["records"][0]["can_amend"] is False


def test_owner_revokes_and_the_record_disappears(wastage_http):
    """DELETE 是软删：明细与统计都不再算它；再撤一次是 404。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    created = client.post("/api/wastage/records", json=_record_body())
    assert created.status_code == 200, created.text
    record_id = created.json()["record"]["id"]

    revoked = client.delete(f"/api/wastage/records/{record_id}")
    assert revoked.status_code == 200, revoked.text
    record = revoked.json()["record"]
    assert record["revoked_at"]
    assert record["can_amend"] is False
    assert client.get("/api/wastage/records").json()["total"] == 0

    again = client.delete(f"/api/wastage/records/{record_id}")
    assert again.status_code == 404, again.text
    assert "找不到这条登记" in again.json()["detail"]

    _admin_cookie(client)
    stats = client.get("/api/wastage/statistics", params={"month": "2026-09"}).json()
    assert stats["total_count"] == 0


def test_amend_and_revoke_need_a_session(wastage_http):
    client, _db, _accounts = wastage_http
    client.cookies.clear()
    assert client.patch("/api/wastage/records/1", json={"quantity": 2}).status_code == 401
    assert client.delete("/api/wastage/records/1").status_code == 401


def test_admin_amends_and_revokes_only_his_own_records(wastage_http):
    """管理端记的账归管理端；员工的账他改不了（员工也改不了管理端的）。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    staff_created = client.post("/api/wastage/records", json=_record_body())
    assert staff_created.status_code == 200, staff_created.text
    staff_id = staff_created.json()["record"]["id"]

    _admin_cookie(client)
    admin_created = client.post("/api/wastage/records", json=_record_body("鸡蛋", 6))
    assert admin_created.status_code == 200, admin_created.text
    admin_id = admin_created.json()["record"]["id"]

    amended = client.patch(f"/api/wastage/records/{admin_id}", json={"quantity": 5})
    assert amended.status_code == 200, amended.text
    assert amended.json()["record"]["quantity"] == 5.0

    denied = client.patch(f"/api/wastage/records/{staff_id}", json={"quantity": 2})
    assert denied.status_code == 403, denied.text
    assert "只能改自己记的那几条" in denied.json()["detail"]
    assert client.delete(f"/api/wastage/records/{staff_id}").status_code == 403
