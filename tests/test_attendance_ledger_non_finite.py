#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""考勤台账的存假列不许收非有限数（回归用例）。

来源：`tests/audit/repro_sched_nan_balance.py` 的复现脚本（2026-10-26 修完后转成这条
回归用例，脚本删除）。缺陷当时的表现：

`PUT /api/attendance/sheet/{员工}/manual` 的 `ManualColumnsRequest` 用
`Optional[float]`，pydantic 默认放行 `NaN` / `±Infinity`；`_ledger_value` 只做
`float(value)`，PostgreSQL 的 `DOUBLE PRECISION` 也收。坏值落库之后，
`AttendanceStore.sheet()` 把它塞进 `leave.balance` / `closing_balance_stored` 等字段，
FastAPI 的 JSON 序列化按 `allow_nan=False` 抛 `ValueError` —— **整张考勤表永久 500**，
页面上清不掉（只能进库手改）。

修法（`services/attendance/store.py`）：

* `_ledger_value` 在写台账之前拦非有限数，抛 `invalid_opening_balance` /
  `invalid_closing_balance`，HTTP 层译成 400（与「给了非数字」共用同一句文案）；
* `_ledger_number` 把**存量**坏值按「没填」读（`None`）—— 老版本已经写进去的行不该让
  整张表与导出件继续 500。

`<input type="number">` 的页面走不到这条路，但手搓请求 / 脚本 / 导入能，所以写口必须自己拦。
"""

import asyncio
import unittest
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.attendance as attendance_module
import api.auth as auth_module
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.attendance import AttendanceStore
from services.hygiene.accounts import EmployeeAccounts
from services.scheduling.store import SchedulingStore

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class NonFiniteBalanceTest(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager()
        _run(self.db.connect())
        set_runtime(AppRuntime(db=self.db))
        self.accounts = EmployeeAccounts(self.db, now=lambda: FIXED_NOW)
        _run(SchedulingStore(self.db, now=lambda: FIXED_NOW).prepare())
        self.app = FastAPI()
        self.app.include_router(auth_module.router)
        self.app.include_router(attendance_module.router)
        # 整页 500 是**服务端异常**：照它跑，不要让它在这里 raise 出来。
        self.client = TestClient(self.app, raise_server_exceptions=False)
        assert self.client.post("/api/auth/init", json=ADMIN).status_code == 200
        login = self.client.post(
            "/api/auth/login",
            json={"username": ADMIN["username"], "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        employee = _run(self.accounts.register(PHONE, PASSWORD, NAME))
        _run(self.accounts.approve(employee["id"]))
        self.employee_id = int(employee["id"])

    def tearDown(self):
        self.client.close()
        _run(self.db.close())
        set_runtime(None)

    def _put_manual(self, payload: dict):
        return self.client.put(
            f"/api/attendance/sheet/{self.employee_id}/manual",
            json=payload,
        )

    def test_non_finite_closing_balance_is_rejected_before_it_reaches_the_ledger(self):
        # httpx 的 json= 用标准库 json.dumps（默认 allow_nan=True），请求体里就是字面量
        # NaN —— 服务端 json.loads 认它，pydantic 的 float 也认它。
        write = self._put_manual({"closing_balance": float("nan")})
        self.assertEqual(write.status_code, 400, write.text)
        self.assertIn("期末存假", write.json()["detail"])

        # 坏值没落库：两个读口照常。
        self.assertEqual(self.client.get("/api/attendance/sheet").status_code, 200)
        self.assertEqual(self.client.get("/api/attendance/sheet.xlsx").status_code, 200)

    def test_non_finite_opening_balance_is_rejected(self):
        write = self._put_manual({"opening_balance": float("-inf")})
        self.assertEqual(write.status_code, 400, write.text)
        self.assertIn("期初存假", write.json()["detail"])
        self.assertEqual(self.client.get("/api/attendance/sheet").status_code, 200)

    def test_legacy_non_finite_value_is_read_as_blank_instead_of_500(self):
        """存量坏值（老版本写进来的那一行）按「没填」读，整页不再 500。"""
        write = self._put_manual({"closing_balance": 3})
        self.assertEqual(write.status_code, 200, write.text)
        month = write.json()["month"]

        async def _poison():
            await self.db._conn.execute(
                'UPDATE attendance_ledger SET closing_balance = \'NaN\' '
                'WHERE employee_id = ? AND "month" = ?',
                (self.employee_id, month),
            )
            await self.db._conn.commit()

        _run(_poison())

        sheet = self.client.get("/api/attendance/sheet")
        self.assertEqual(sheet.status_code, 200, sheet.text)
        card = next(
            row for row in sheet.json()["employees"]
            if int(row["id"]) == self.employee_id
        )
        # 读口把坏值当「没填」：卡片上那一格回到空，「有效存假」退回公式值。
        self.assertIsNone(card["closing_balance_stored"])
        self.assertFalse(card["leave"]["balance_is_manual"])


if __name__ == "__main__":
    unittest.main()
