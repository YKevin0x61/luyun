#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WebSocket 鉴权：/ws/realtime 支持 Session Cookie 或 ?token= API Token。

票 08 起连接还可以用 `?identity=staff|admin` **显式声明**自己带的是哪套凭据：
声明只决定「用哪一份凭据校验」，绝不放宽校验本身（fail-closed）——声明 staff 时
管理端 cookie / token 一律不看，没有有效员工会话就拒绝连接；不带声明时优先级
一个字不变（管理端 cookie → ?token → 员工 cookie）。
"""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime

from config import settings
from database import CHINA_TZ, DatabaseManager
from services import auth_service
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts


class _FakeWebSocket:
    """最小 fake websocket：只提供 identify_ws 需要的 cookies/query_params。"""

    def __init__(self, cookies=None, query_params=None):
        self.cookies = cookies or {}
        self.query_params = query_params or {}


class _WsAuthCase(unittest.IsolatedAsyncioTestCase):
    """公共夹具：真库 + 已初始化的管理员 + 员工账号服务。"""

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

    async def _staff_login(self, phone="13800138000", name="张三"):
        """注册 → 批准 → 登录，返回 (employee, login)。"""
        employee = await self.accounts.register(phone, "password123", name)
        await self.accounts.approve(employee["id"])
        login = await self.accounts.login(phone, "password123")
        return employee, login

    async def _admin_session(self):
        session_id, _expires_at = await auth_service.create_session(remember=False)
        return session_id


class AuthenticateWsTest(_WsAuthCase):
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

        session_id = await self._admin_session()
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

        _employee, login = await self._staff_login()
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

    # ------------------------------------------------------------------
    # 票 08：显式声明只选凭据，不放宽校验
    # ------------------------------------------------------------------

    async def test_two_cookies_with_staff_declaration_is_staff(self):
        """同一浏览器同时持两套 cookie：声明员工就是员工（管理端 cookie 让位）。"""
        import main
        from api.security import WsIdentity, authenticate_ws, identify_ws

        session_id = await self._admin_session()
        employee, login = await self._staff_login()
        previous = main.employee_accounts
        main.employee_accounts = self.accounts
        try:
            ws = _FakeWebSocket(
                cookies={
                    settings.SESSION_COOKIE_NAME: session_id,
                    settings.STAFF_SESSION_COOKIE_NAME: login["session_id"],
                },
                query_params={"identity": "staff"},
            )
            self.assertEqual(
                await identify_ws(ws),
                WsIdentity(auth="staff", employee_id=employee["id"]),
            )
            self.assertEqual(await authenticate_ws(ws), "staff")
        finally:
            main.employee_accounts = previous

    async def test_two_cookies_without_declaration_stays_admin(self):
        """没有声明：优先级一个字不变，管理端 cookie 仍然优先（现状钉住）。"""
        from api.security import WsIdentity, identify_ws

        session_id = await self._admin_session()
        _employee, login = await self._staff_login()
        ws = _FakeWebSocket(
            cookies={
                settings.SESSION_COOKIE_NAME: session_id,
                settings.STAFF_SESSION_COOKIE_NAME: login["session_id"],
            },
        )
        self.assertEqual(await identify_ws(ws), WsIdentity(auth="session"))

    async def test_staff_declaration_without_staff_session_is_rejected(self):
        """声明员工却只有管理端凭据（cookie + token）：拒绝，不许回落。"""
        import main
        from api.security import identify_ws

        session_id = await self._admin_session()
        plain, _meta = await auth_service.issue_api_token(label="kds-1")
        previous = main.employee_accounts
        main.employee_accounts = self.accounts
        try:
            ws = _FakeWebSocket(
                cookies={
                    settings.SESSION_COOKIE_NAME: session_id,
                    settings.STAFF_SESSION_COOKIE_NAME: "bogus-staff-session",
                },
                query_params={"identity": "staff", "token": plain},
            )
            self.assertIsNone(
                await identify_ws(ws),
                "声明员工时管理端 cookie 与 token 都不能把连接救回来",
            )
        finally:
            main.employee_accounts = previous

    async def test_admin_declaration_with_only_staff_cookie_is_rejected(self):
        """声明管理端却只有员工 cookie：拒绝，不许回落到员工身份。"""
        import main
        from api.security import identify_ws

        _employee, login = await self._staff_login()
        previous = main.employee_accounts
        main.employee_accounts = self.accounts
        try:
            ws = _FakeWebSocket(
                cookies={settings.STAFF_SESSION_COOKIE_NAME: login["session_id"]},
                query_params={"identity": "admin"},
            )
            self.assertIsNone(await identify_ws(ws))
        finally:
            main.employee_accounts = previous

    async def test_admin_declaration_accepts_admin_cookie_or_token(self):
        """声明管理端：管理端 cookie 或 API token 都能验，员工 cookie 不看。"""
        from api.security import WsIdentity, identify_ws

        session_id = await self._admin_session()
        ws_cookie = _FakeWebSocket(
            cookies={settings.SESSION_COOKIE_NAME: session_id},
            query_params={"identity": "admin"},
        )
        self.assertEqual(await identify_ws(ws_cookie), WsIdentity(auth="session"))

        plain, _meta = await auth_service.issue_api_token(label="kds-1")
        ws_token = _FakeWebSocket(query_params={"identity": "admin", "token": plain})
        self.assertEqual(await identify_ws(ws_token), WsIdentity(auth="api_token"))

    async def test_unknown_declaration_is_rejected(self):
        """认不出的声明 fail-closed：既不按声明放行，也不静默退回旧优先级。"""
        from api.security import identify_ws

        session_id = await self._admin_session()
        ws = _FakeWebSocket(
            cookies={settings.SESSION_COOKIE_NAME: session_id},
            query_params={"identity": "employee"},
        )
        self.assertIsNone(await identify_ws(ws))

    async def test_kds_token_link_is_unchanged(self):
        """KDS 的 `?token=` 不带声明：行为与改动前逐条一致（token 先于员工 cookie）。"""
        from api.security import authenticate_ws

        plain, _meta = await auth_service.issue_api_token(label="kds-1")
        self.assertEqual(
            await authenticate_ws(_FakeWebSocket(query_params={"token": plain})),
            "api_token",
        )

        import main

        _employee, login = await self._staff_login()
        previous = main.employee_accounts
        main.employee_accounts = self.accounts
        try:
            both = _FakeWebSocket(
                cookies={settings.STAFF_SESSION_COOKIE_NAME: login["session_id"]},
                query_params={"token": plain},
            )
            self.assertEqual(await authenticate_ws(both), "api_token")
        finally:
            main.employee_accounts = previous


class _LiveWebSocket:
    """端点级 fake：上行消息脚本化，连接活着期间记录服务端推送。"""

    def __init__(self, cookies=None, query_params=None):
        self.cookies = cookies or {}
        self.query_params = query_params or {}
        self.sent = []
        self.accepted = False
        self.closed_code = None
        self._inbox = asyncio.Queue()

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000):
        self.closed_code = code
        self._inbox.put_nowait(None)

    async def send_json(self, payload):
        self.sent.append(payload)

    async def receive_text(self):
        raw = await self._inbox.get()
        if raw is None:
            raise RuntimeError("客户端断开")
        return raw

    def push(self, payload):
        self._inbox.put_nowait(json.dumps(payload))

    def disconnect(self):
        self._inbox.put_nowait(None)

    def types(self):
        return [m.get("type") for m in self.sent]

    def nudges(self):
        return [m for m in self.sent if m.get("type") == "nudge"]


async def _wait_until(predicate, timeout=5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


class DeclaredIdentityScopeTest(_WsAuthCase):
    """同浏览器两套 cookie：声明的身份决定这条连接能订到什么、收到什么。

    走真实端点 `main.realtime_ws`（只把 hub 换成一份干净的），断言只有
    「订阅被接受还是被拒」「收得到哪几条 nudge」。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        import main

        from services.realtime.hub import RealtimeHub

        self.main = main
        self._prev_accounts = main.employee_accounts
        self._prev_hub = main.realtime_hub
        self.hub = RealtimeHub()
        main.realtime_hub = self.hub
        main.employee_accounts = self.accounts
        self._opened = []

    async def asyncTearDown(self):
        for ws, task in self._opened:
            ws.disconnect()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except asyncio.TimeoutError:  # pragma: no cover - 端点卡住才会走到
                task.cancel()
        self.main.employee_accounts = self._prev_accounts
        self.main.realtime_hub = self._prev_hub
        await super().asyncTearDown()

    async def _open(self, cookies, query_params=None):
        ws = _LiveWebSocket(cookies=cookies, query_params=query_params)
        task = asyncio.create_task(self.main.realtime_ws(ws))
        self._opened.append((ws, task))
        connected = await _wait_until(lambda: "connected" in ws.types())
        self.assertTrue(connected, "端点没有回 connected：鉴权或接线挂了")
        return ws

    async def _send(self, ws, payload):
        before = len(ws.sent)
        ws.push(payload)
        ok = await _wait_until(lambda: len(ws.sent) > before)
        self.assertTrue(ok, f"端点没有回应 {payload}")
        return ws.sent[-1]

    async def test_two_cookie_browser_declaring_staff_gets_staff_view(self):
        session_id = await self._admin_session()
        employee, login = await self._staff_login()
        mine = employee["id"]
        colleague = mine + 1
        ws = await self._open(
            cookies={
                settings.SESSION_COOKIE_NAME: session_id,
                settings.STAFF_SESSION_COOKIE_NAME: login["session_id"],
            },
            query_params={"identity": "staff"},
        )

        # 经营主题订不到（管理端 cookie 在场也不行）
        answer = await self._send(ws, {"action": "subscribe", "id": "orders-1", "topics": ["orders"]})
        self.assertEqual(answer["type"], "error")
        self.assertEqual(answer["code"], "INVALID_SUBSCRIBE")

        # 卫生订得到
        self.assertEqual(
            await self._send(ws, {"action": "subscribe", "id": "hygiene-1", "topics": ["hygiene"]}),
            {"type": "subscribed", "id": "hygiene-1"},
        )

        colleague_scope = {"resource": "assignment", "action": "changed", "employee_id": colleague}
        mine_scope = {"resource": "assignment", "action": "changed", "employee_id": mine}
        store_scope = {"resource": "boards", "action": "changed"}
        await self.hub.broadcast_nudge("hygiene", colleague_scope)
        await self.hub.broadcast_nudge("hygiene", mine_scope)
        await self.hub.broadcast_nudge("hygiene", store_scope)

        self.assertEqual(
            [m["scope"] for m in ws.nudges()],
            [mine_scope, store_scope],
            "同事那条必须收不到；自己的与全店级的照收",
        )

    async def test_two_cookie_browser_without_declaration_stays_admin(self):
        """不带声明：还是管理端身份（现状钉住），经营主题照订照收。"""
        session_id = await self._admin_session()
        _employee, login = await self._staff_login()
        ws = await self._open(
            cookies={
                settings.SESSION_COOKIE_NAME: session_id,
                settings.STAFF_SESSION_COOKIE_NAME: login["session_id"],
            },
        )

        self.assertEqual(
            await self._send(ws, {"action": "subscribe", "id": "orders-1", "topics": ["orders"]}),
            {"type": "subscribed", "id": "orders-1"},
        )
        await self.hub.broadcast_nudge("orders", {"station": "A"})
        self.assertEqual([m["scope"] for m in ws.nudges()], [{"station": "A"}])

    async def test_staff_declaration_without_staff_session_refuses_the_connection(self):
        """只有管理端 cookie 却声明员工：连接被 4401 拒绝，且一个订阅都不存在。"""
        session_id = await self._admin_session()
        ws = _LiveWebSocket(
            cookies={settings.SESSION_COOKIE_NAME: session_id},
            query_params={"identity": "staff"},
        )
        task = asyncio.create_task(self.main.realtime_ws(ws))
        self._opened.append((ws, task))
        self.assertTrue(await _wait_until(lambda: ws.closed_code is not None))
        self.assertEqual(ws.closed_code, 4401)
        self.assertEqual(ws.types(), [], "被拒绝的连接不该收到 connected")

        # 拒绝之后不留下任何可派发的订阅：广播什么都收不到。
        await self.hub.broadcast_nudge("hygiene", {"resource": "boards", "action": "changed"})
        self.assertEqual(ws.nudges(), [])


if __name__ == "__main__":
    unittest.main()
