#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仪容仪表：按人一张自拍（今天排到班次才要拍）—— 判据、状态机与标准图。

需求原文：「卫生系统那里还要有一项仪容仪表的拍照，排班上面除了休假的都要拍」。
用户锁定的四条口径：**要验收** · **不要钟点** · 入口在「今天」页那张卡 · **要标准图**。

所以这个文件盯四件事：

1. **谁要拍**：判据是排班给的那一行有 ``shift_id`` —— 那天休（``shift_id`` 为空）与
   今天没排到（新人还没配规则）都不用拍，这正是「除了休假的都要拍」。
2. **没钟点**：这一项不产生逾期、不进看板；模块里根本没有 overdue 那一套，所以
   这个文件不拨假时钟也不会红。
3. **状态机跟日常一个形状**：待拍 → 等验收 → 通过 / 驳回（带原因）→ 重拍；
   已经通过的那张不再接受重拍（跟日常 ``already_accepted`` 同一条口径）。
4. **必须现场拍**：``live`` 标志走日常那条同一个闸（ADR 0050 的两步拍照），
   不是「从相册挑一张」。**标准图反过来不要求现场拍** —— 它是管理员手里的样板。

服务层细节在这里；HTTP 契约在 ``test_hygiene_attire_http.py``。
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.attire import (
    STATUS_PASSED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_TODO,
    HygieneAttire,
)
from services.hygiene.captures import FileCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork, HygieneWorkError
from tests.hygiene_duty import assign_duty

import io

DAY_PHONE = "13800000001"
NIGHT_PHONE = "13800000002"
OFF_PHONE = "13800000003"

ZHANGSAN = 11  # 今天排到白班档
LISI = 12  # 今天休
WANGWU = 13  # 今天没排到（新人还没配规则）

SHOT_COLOR = (30, 90, 120)
RESHOT_COLOR = (200, 40, 40)
STANDARD_COLOR = (200, 200, 40)
STANDARD_COLOR_2 = (40, 200, 200)


def _jpeg(color) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (120, 90), color).save(output, format="JPEG", quality=90)
    return output.getvalue()


def _live(color=SHOT_COLOR) -> dict:
    return {
        "bytes": _jpeg(color),
        "content_type": "image/jpeg",
        "live": True,
        "markup": [],
    }


def _standard(color=STANDARD_COLOR) -> dict:
    """标准图**没有** ``live``：它不要求现场拍摄（跟日常标准图同一条路）。"""
    return {"bytes": _jpeg(color), "content_type": "image/jpeg", "markup": []}


class AttireTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.now_value = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
        self.work = HygieneWork(
            self.db,
            captures=FileCaptureStore(Path(self._tmpdir.name) / "hygiene-captures"),
            now=lambda: self.now_value,
            image_variants=ImageVariantGenerator(),
        )
        await self.work.prepare()
        self.attire = HygieneAttire(self.work, now=lambda: self.now_value)
        self.day = self.attire.today()
        # 前提：营业日跟排班的「今天」是同一个，否则下面全是假红。
        self.assertEqual(self.day, "2026-09-13")

        now_iso = self.now_value.isoformat()
        for employee_id, phone, name in (
            (ZHANGSAN, DAY_PHONE, "张三"),
            (LISI, NIGHT_PHONE, "李四"),
            (WANGWU, OFF_PHONE, "王五"),
        ):
            await self.db._conn.execute(
                """INSERT INTO hygiene_employees
                       (id, phone, name, password_hash, permission, approved,
                        created_at, updated_at)
                   VALUES (?, ?, ?, '', '普通员工', 1, ?, ?)""",
                (employee_id, phone, name, now_iso, now_iso),
            )
        # 先落库再配排班：`set_rule` 经另一条连接读花名册，未提交的账号它看不见
        # （报 `unknown_employee`）。
        await self.db._conn.commit()
        # 张三：今天排到（配一条白班档的规则，展开出今天那一行）。
        self.shift_id = await assign_duty(self.db, ZHANGSAN, slot="day", now=self.now_value)
        # 李四：今天**休** —— 排班给了结果行但 shift_id 为空，这才是「休假」的形态
        # （跟「没排到」不是一回事，判据要分得开）。
        await self.db._conn.execute(
            """INSERT INTO staff_assignments
                   (employee_id, business_date, shift_id, zone_id, source,
                    created_at, updated_at)
               VALUES (?, ?, NULL, NULL, 'manual', ?, ?)""",
            (LISI, self.day, now_iso, now_iso),
        )
        # 王五：什么都不插 —— 新人还没配规则，那天没有他的行。
        await self.db._conn.commit()

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    # ── 判据：谁要拍 ────────────────────────────────────────────────────

    async def test_required_follows_the_schedule(self):
        """排到班次才要拍；休假的与没排到的都不用 —— 这正是「除了休假的都要拍」。"""
        zhang = await self.attire.staff_view(ZHANGSAN)
        self.assertTrue(zhang["required"])
        self.assertEqual(zhang["shift_id"], self.shift_id)
        self.assertEqual(zhang["status"], STATUS_TODO)
        self.assertFalse(zhang["has_standard"])
        self.assertIsNone(zhang["submitted_at"])

        for employee_id, who in ((LISI, "休假的"), (WANGWU, "没排到的")):
            view = await self.attire.staff_view(employee_id)
            self.assertFalse(view["required"], f"{who}不该被要求拍照")
            self.assertIsNone(view["shift_id"])
            # 不用拍的人也不该被告知「你去拍」：状态停在待拍、没有图。
            self.assertEqual(view["status"], STATUS_TODO)

    async def test_admin_day_lists_only_scheduled_people(self):
        """管理员那张表就是当天要拍的人 —— 休假的与没排到的不在里面。"""
        day = await self.attire.admin_day()
        self.assertEqual(day["business_date"], self.day)
        self.assertEqual([p["employee_id"] for p in day["people"]], [ZHANGSAN])
        self.assertEqual(day["people"][0]["name"], "张三")
        self.assertEqual(day["counts"], {
            "required": 1, "pending": 0, "passed": 0, "rejected": 0, "todo": 1,
        })
        self.assertFalse(day["has_standard"])

    # ── 标准图：交之前得先有样板 ────────────────────────────────────────

    async def test_submit_without_standard_is_refused(self):
        """还没传标准图时不给交：员工照不了没有的东西拍，报的是这一句而不是「参数不对」。"""
        with self.assertRaises(HygieneWorkError) as caught:
            await self.attire.submit(ZHANGSAN, _live())
        self.assertEqual(caught.exception.code, "attire_standard_required")

    async def test_submit_without_duty_is_refused(self):
        await self.attire.set_standard(_standard())
        with self.assertRaises(HygieneWorkError) as caught:
            await self.attire.submit(LISI, _live())
        self.assertEqual(caught.exception.code, "attire_not_required")

    async def test_standard_keeps_every_upload_and_latest_wins(self):
        """换标准图是**插新行**：旧版本留着，读的是最新那条（跟日常标准图一个口径）。"""
        first = await self.attire.set_standard(_standard())
        second = await self.attire.set_standard(_standard(STANDARD_COLOR_2))
        self.assertNotEqual(first["capture_id"], second["capture_id"])
        current = await self.attire.current_standard()
        self.assertEqual(current["capture_id"], second["capture_id"])
        # 标注形状按惯例序列化（`markup_json` 是 `'[]'`，不是 NULL）。
        self.assertEqual(current["markup_json"], "[]")

    async def test_standard_upload_does_not_need_live_capture(self):
        """标准图是管理员的样板，不要求现场拍摄 —— 而员工交的那张要求（下面那条）。"""
        standard = await self.attire.set_standard(_standard())  # 没有 live 标志
        self.assertTrue(standard["capture_id"])

    # ── 员工交：必须现场拍 ──────────────────────────────────────────────

    async def test_submit_requires_live_capture(self):
        """ADR 0050：必须是**现场拍的**那一张，相册里挑的不算。"""
        await self.attire.set_standard(_standard())
        from_album = _live()
        from_album["live"] = False
        with self.assertRaises(HygieneWorkError) as caught:
            await self.attire.submit(ZHANGSAN, from_album)
        self.assertEqual(caught.exception.code, "live_required")
        # 被拒之后不留半行：还是「待拍」。
        self.assertEqual((await self.attire.staff_view(ZHANGSAN))["status"], STATUS_TODO)

    # ── 状态机主路径 ────────────────────────────────────────────────────

    async def test_submit_then_accept(self):
        await self.attire.set_standard(_standard())

        view = await self.attire.submit(ZHANGSAN, _live())
        self.assertEqual(view["status"], STATUS_PENDING)
        self.assertTrue(view["has_standard"])
        self.assertIsNotNone(view["submitted_at"])

        day = await self.attire.admin_day()
        self.assertEqual(day["counts"]["pending"], 1)
        self.assertEqual(day["counts"]["todo"], 0)
        self.assertTrue(day["people"][0]["has_shot"])
        self.assertTrue(day["has_standard"])

        decided = await self.attire.accept(ZHANGSAN)
        self.assertEqual(decided["status"], STATUS_PASSED)

        self.assertEqual((await self.attire.staff_view(ZHANGSAN))["status"], STATUS_PASSED)
        day = await self.attire.admin_day()
        self.assertEqual(day["counts"]["passed"], 1)
        self.assertEqual(day["counts"]["pending"], 0)

    async def test_pending_capture_is_the_shot_that_was_submitted(self):
        """验收端点按这个 id 取图，取不到就是 404 —— 所以它得是那张实拍的 id。"""
        await self.attire.set_standard(_standard())
        view = await self.attire.submit(ZHANGSAN, _live())
        capture_id = await self.attire.pending_capture(ZHANGSAN)
        self.assertTrue(capture_id)
        # 交完之后那一行能取到图（真去 capture store 里读一次）。
        stored = await self.work.capture_view(capture_id, "original")
        self.assertTrue(stored)
        self.assertIsNotNone(view["submitted_at"])

    async def test_reject_needs_a_reason(self):
        """驳回必须写清哪里不合格 —— 员工要照着这句重拍。"""
        await self.attire.set_standard(_standard())
        await self.attire.submit(ZHANGSAN, _live())
        for blank in (None, "", "   "):
            with self.assertRaises(HygieneWorkError) as caught:
                await self.attire.reject(ZHANGSAN, blank)
            self.assertEqual(caught.exception.code, "attire_note_required")
        # 驳回失败不改状态：还在等验收。
        self.assertEqual((await self.attire.staff_view(ZHANGSAN))["status"], STATUS_PENDING)

        decided = await self.attire.reject(ZHANGSAN, "头发没扎起来")
        self.assertEqual(decided["status"], STATUS_REJECTED)
        view = await self.attire.staff_view(ZHANGSAN)
        self.assertEqual(view["status"], STATUS_REJECTED)
        self.assertEqual(view["note"], "头发没扎起来")
        day = await self.attire.admin_day()
        self.assertEqual(day["counts"]["rejected"], 1)
        self.assertEqual(day["people"][0]["note"], "头发没扎起来")

    async def test_reshoot_after_reject_clears_the_old_reason(self):
        """驳回后重拍：回到等验收，上一条驳回原因要清掉 —— 否则像是「又被驳了」。"""
        await self.attire.set_standard(_standard())
        await self.attire.submit(ZHANGSAN, _live())
        await self.attire.reject(ZHANGSAN, "袖口不干净")

        view = await self.attire.submit(ZHANGSAN, _live(RESHOT_COLOR))
        self.assertEqual(view["status"], STATUS_PENDING)
        self.assertIsNone(view["note"])
        # 重拍是**覆盖**，不是新增一行（一天一行，UNIQUE 兜住）。
        day = await self.attire.admin_day()
        self.assertEqual(day["counts"]["pending"], 1)
        self.assertEqual(day["counts"]["rejected"], 0)
        self.assertEqual(day["counts"]["required"], 1)

    async def test_passed_shot_cannot_be_reshot(self):
        """已经通过的那张是记录，不能被后来的一张盖掉（跟日常 ``already_accepted`` 一致）。"""
        await self.attire.set_standard(_standard())
        await self.attire.submit(ZHANGSAN, _live())
        await self.attire.accept(ZHANGSAN)

        with self.assertRaises(HygieneWorkError) as caught:
            await self.attire.submit(ZHANGSAN, _live(RESHOT_COLOR))
        self.assertEqual(caught.exception.code, "already_accepted")
        self.assertEqual((await self.attire.staff_view(ZHANGSAN))["status"], STATUS_PASSED)

    # ── 验收的边界 ──────────────────────────────────────────────────────

    async def test_decide_without_a_shot(self):
        with self.assertRaises(HygieneWorkError) as caught:
            await self.attire.accept(WANGWU)
        self.assertEqual(caught.exception.code, "attire_not_found")

    async def test_decide_twice_is_refused(self):
        """重复点一次（或者员工刚重拍）要回一句人话，而不是悄悄改掉已经落定的那一张。"""
        await self.attire.set_standard(_standard())
        await self.attire.submit(ZHANGSAN, _live())
        await self.attire.accept(ZHANGSAN)
        with self.assertRaises(HygieneWorkError) as caught:
            await self.attire.accept(ZHANGSAN)
        self.assertEqual(caught.exception.code, "attire_not_pending")
        with self.assertRaises(HygieneWorkError) as caught:
            await self.attire.reject(ZHANGSAN, "重新来")
        self.assertEqual(caught.exception.code, "attire_not_pending")
        # 通过的那一张没被上面两次失败改动。
        self.assertEqual((await self.attire.staff_view(ZHANGSAN))["status"], STATUS_PASSED)

    async def test_another_day_is_a_new_row(self):
        """一天一行：昨天的通过不影响今天要拍。"""
        await self.attire.set_standard(_standard())
        await self.attire.submit(ZHANGSAN, _live())
        await self.attire.accept(ZHANGSAN)

        yesterday = await self.attire.admin_day("2026-09-12")
        # 昨天没有排班结果行 → 谁也不在那张表上（排班规则是今天起生效的）。
        self.assertEqual(yesterday["counts"]["required"], 0)
        self.assertEqual(yesterday["people"], [])
