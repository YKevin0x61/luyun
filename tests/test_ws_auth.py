#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WebSocket 鉴权：/ws/realtime 支持 Session Cookie 或 ?token= API Token。"""

import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services import auth_service
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts


class _FakeWebSocket:
    """最小 fake websocket：只提供 authenticate_ws 需要的 cookies/query_params。"""

    def __init__(self, cookies=None, query_params=None):
        self.cookies = cookies or {}
        self.query_params = query_params or {}


class AuthenticateWsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        set_runtime(AppRuntime(db=self.db))
        await auth_service.init_user("admin", "password123")
        self.accounts = EmployeeAccounts(
            self.db,
            now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
        )

    async def asyncTearDown(self):
        await self.db.close()
        set_runtime(None)
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def test_no_credentials_returns_none(self):
        from api.security import authenticate_ws

        ws = _FakeWebSocket()
        self.assertIsNone(await authenticate_ws(ws))

    async def test_valid_api_token_query_param_returns_api_token(self):
        from api.security import authenticate_ws

        plain, _meta = await auth_service.issue_api_token(label="kds-1")
        ws = _FakeWebSocket(query_params={"token": plain})
        self.assertEqual(await authenticate_ws(ws), "api_token")

    async def test_valid_session_cookie_returns_session(self):
        from api.security import authenticate_ws

        session_id, _expires_at = await auth_service.create_session(remember=False)
        ws = _FakeWebSocket(cookies={settings.SESSION_COOKIE_NAME: session_id})
        self.assertEqual(await authenticate_ws(ws), "session")

    async def test_invalid_cookie_and_token_returns_none(self):
        from api.security import authenticate_ws

        ws = _FakeWebSocket(
            cookies={settings.SESSION_COOKIE_NAME: "bogus-session"},
            query_params={"token": "bogus-token"},
        )
        self.assertIsNone(await authenticate_ws(ws))

    async def test_approved_staff_session_returns_staff(self):
        import main
        from api.security import authenticate_ws

        employee = await self.accounts.register(
            "13800138000",
            "password123",
            "张三",
        )
        await self.accounts.approve(employee["id"])
        login = await self.accounts.login("13800138000", "password123")
        previous = main.employee_accounts
        main.employee_accounts = self.accounts
        try:
            ws = _FakeWebSocket(
                cookies={
                    settings.STAFF_SESSION_COOKIE_NAME: login["session_id"],
                },
            )
            self.assertEqual(await authenticate_ws(ws), "staff")
        finally:
            main.employee_accounts = previous


if __name__ == "__main__":
    unittest.main()
