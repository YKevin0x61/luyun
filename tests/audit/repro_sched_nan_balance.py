#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缺陷：**期末存假 / 期初存假接受 NaN / Infinity，落库后考勤表整页 500 且不会自愈**。

链路：`PUT /api/attendance/sheet/{员工}/manual` → `ManualColumnsRequest` 的
`Optional[float]`（pydantic 默认允许 nan / inf）→ `_ledger_value` 的 `float(value)` 原样收下
→ PostgreSQL `DOUBLE PRECISION` 允许 NaN / Infinity → `AttendanceStore.sheet()` 把它
塞进 `leave.balance` / `closing_balance_stored` → FastAPI 的 JSON 序列化
（`json.dumps(..., allow_nan=False)`）抛 `ValueError: Out of range float values are not
JSON compliant` → **整张考勤表 500**，而坏值还留在台账里，刷新多少次都还是 500；
只能进库手工清掉（页面上的输入框是 `<input type="number">`，走不到这条路，但任何
手搓请求 / 脚本 / 导入都行）。

预期：非有限数在入口就拒（400 `invalid_closing_balance` / `invalid_opening_balance`），
绝不落库。

确认后修哪里：`api/attendance.py` 的 `ManualColumnsRequest`（加 `allow_inf_nan=False`
或自定义校验）与/或 `services/attendance/store.py` 的 `_ledger_value`。

跑法：`.venv/bin/python -m pytest tests/audit/repro_sched_nan_balance.py -x -q`
"""

import asyncio
import tempfile
import unittest
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.attendance as attendance_module
import api.auth as auth_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
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


class NanBalanceTest(unittest.TestCase):
    def setUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
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
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def test_non_finite_closing_balance_must_be_rejected_before_it_reaches_the_ledger(self):
        # httpx 的 json= 用标准库 json.dumps（默认 allow_nan=True），于是请求体里就是
        # 字面量 NaN —— 服务端 json.loads 认它，pydantic 的 float 也认它。
        write = self.client.put(
            f"/api/attendance/sheet/{self.employee_id}/manual",
            json={"closing_balance": float("nan")},
        )
        sheet_after = self.client.get("/api/attendance/sheet")
        xlsx_after = self.client.get("/api/attendance/sheet.xlsx")
        self.assertEqual(
            write.status_code,
            400,
            "非有限数被当作合法存假：PUT closing_balance=NaN 返回 "
            f"{write.status_code}；随后 GET /sheet 返回 {sheet_after.status_code}、"
            f"GET /sheet.xlsx 返回 {xlsx_after.status_code}（坏值留在台账里，页面永久 500）",
        )


if __name__ == "__main__":
    unittest.main()
