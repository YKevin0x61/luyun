#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仪容的判定写口：读-判-写要在一个写单元里，否则并发点两次「通过」会都成功。

候选 05 的真 bug：``attire._decide`` 先读状态再 UPDATE，中间不持锁、自己裸 commit
（``services/hygiene/attire.py``）。两个人同时点验收时，两个请求都读到「待验收」，
后一个把前一个的结果盖掉 —— 而「验收过的不能被后来的一张盖掉」这条规矩正是它自己
写在 docstring 里的。

HTTP 契约在 ``test_hygiene_attire_http.py``；状态机主路径在 ``test_hygiene_attire.py``。
这里只盯并发与「提交不再手写」。
"""

from __future__ import annotations

import ast
import asyncio
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.attire import HygieneAttire
from services.hygiene.captures import FileCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.status import ATTIRE_STATUS_PASSED
from services.hygiene.work import HygieneWork, HygieneWorkError
from tests.hygiene_duty import assign_duty

ZHANGSAN = 11


def _jpeg(color) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (120, 90), color).save(output, format="JPEG", quality=90)
    return output.getvalue()


def _live() -> dict:
    return {
        "bytes": _jpeg((30, 90, 120)),
        "content_type": "image/jpeg",
        "live": True,
        "markup": [],
    }


def _standard() -> dict:
    return {"bytes": _jpeg((200, 200, 40)), "content_type": "image/jpeg", "markup": []}


class AttireWriteUnitTest(unittest.IsolatedAsyncioTestCase):
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

        now_iso = self.now_value.isoformat()
        await self.db._conn.execute(
            """INSERT INTO hygiene_employees
                   (id, phone, name, password_hash, permission, approved,
                    created_at, updated_at)
               VALUES (?, ?, ?, '', '普通员工', 1, ?, ?)""",
            (ZHANGSAN, "13800000001", "张三", now_iso, now_iso),
        )
        await self.db._conn.commit()
        await assign_duty(self.db, ZHANGSAN, slot="day", now=self.now_value)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_two_concurrent_accepts_only_one_wins(self):
        """同时点两次「通过」：一个成功，另一个必须报「已经处理过了」。"""
        await self.attire.set_standard(_standard())
        await self.attire.submit(ZHANGSAN, _live())

        results = await asyncio.gather(
            self.attire.accept(ZHANGSAN),
            self.attire.accept(ZHANGSAN),
            return_exceptions=True,
        )
        accepted = [item for item in results if isinstance(item, dict)]
        refused = [item for item in results if isinstance(item, HygieneWorkError)]
        self.assertEqual(len(accepted), 1, f"两次验收都过了：{results!r}")
        self.assertEqual(len(refused), 1, f"丢了一个结果：{results!r}")
        self.assertEqual(refused[0].code, "attire_not_pending")
        # 落库的状态也是「通过」，没有被第二次改回去。
        self.assertEqual((await self.attire.staff_view(ZHANGSAN))["status"], ATTIRE_STATUS_PASSED)

    async def test_attire_does_not_commit_by_hand(self):
        """提交与回滚交给写单元：attire 里不该再出现 .commit() / .rollback()。"""
        path = Path(__file__).resolve().parent.parent / "services/hygiene/attire.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("commit", "rollback"):
                calls.append(f"{func.attr}@{node.lineno}")
        self.assertEqual(calls, [], f"仪容的写路径又自己收尾了：{calls}")
