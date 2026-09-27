#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工 WS 连接的归属隔离：派发侧 + 订阅侧都要挡住同事的个人事件。

背景：员工只允许订 hygiene（`STAFF_ALLOWED_TOPICS`），但 hygiene 的 nudge 里有
不少是**同事的个人事件**——换区 / 换班 / 花名册被审批、停用——scope 里带的是别人的
employee_id，而员工首页订阅 hygiene 时不带 filters。nudge 不带数据，可「谁在什么
时候换了区、谁被停用」的时序本身就是该挡的信息。

两层：
* 派发侧：`_dispatch_local` 对 staff 连接跳过 scope 里 employee_id 存在且不等于
  自己的 nudge；scope 里没有 employee_id 的是全店级事件，照常发。
* 订阅侧：`handle_message` 对 staff 连接作废客户端自报的 filters，强制换成自己的
  employee_id（否则 `{"filters": {"employee_id": 同事}}` 就能定向监听）。

管理端（session / api_token）两条都不受影响——店长本来就要看全店。

不启动 main.app lifespan（避免加载真实 POS 凭据 + 启动 Playwright）：派发/订阅用
最小 fake websocket 直接测 hub；端点接线（`/ws/realtime` 把建连时鉴权出来的
employee_id 交给 hub）只 import main 模块 + 替换 `main.employee_accounts` /
`main.realtime_hub`，同样不触发 lifespan。
"""

import unittest

from services.realtime.hub import RealtimeHub
from tests.test_ws_hub import _FakeWebSocket, _subscribe

MINE = 7
COLLEAGUE = 8
# 同事换区：`api/hygiene.py` 的 `_hygiene_nudge("assignment", "changed", employee_id=...)`
COLLEAGUE_SCOPE = {"resource": "assignment", "action": "changed", "employee_id": COLLEAGUE}
MINE_SCOPE = {"resource": "assignment", "action": "changed", "employee_id": MINE}
# 全店级：scope 里不带 employee_id（看板/档口/配置变更）
STORE_SCOPE = {"resource": "boards", "action": "changed"}


def _nudges(ws):
    return [m for m in ws.sent if m.get("type") == "nudge"]


class StaffScopeDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = RealtimeHub()

    async def test_staff_without_filters_skips_colleague_scoped_nudge(self):
        """员工首页的订法（不带 filters）：收不到同事那条，收得到自己的和全店的。"""
        ws = _FakeWebSocket()
        await self.hub.register(ws, "staff", employee_id=MINE)
        await _subscribe(self.hub, ws, "hygiene-1", ["hygiene"])

        await self.hub.broadcast_nudge("hygiene", COLLEAGUE_SCOPE)

        self.assertEqual(_nudges(ws), [], "员工不该收到 scope 里是同事 employee_id 的 nudge")

        await self.hub.broadcast_nudge("hygiene", MINE_SCOPE)

        self.assertEqual(
            [m["scope"]["employee_id"] for m in _nudges(ws)],
            [MINE],
            "自己那条要收到",
        )

        await self.hub.broadcast_nudge("hygiene", STORE_SCOPE)

        self.assertEqual(
            [m["scope"] for m in _nudges(ws)],
            [MINE_SCOPE, STORE_SCOPE],
            "全店级（scope 无 employee_id）照常发",
        )
        self.assertEqual([m["topic"] for m in _nudges(ws)], ["hygiene", "hygiene"])
        self.assertNotIn(
            COLLEAGUE,
            [m["scope"].get("employee_id") for m in _nudges(ws)],
            "整条连接上不该出现过同事的 employee_id",
        )

    async def test_staff_explicit_filters_cannot_target_colleague(self):
        """显式传 filters 定向监听同事：订阅侧被覆盖，派发侧也拦着。"""
        ws = _FakeWebSocket()
        await self.hub.register(ws, "staff", employee_id=MINE)

        with self.assertLogs("services.realtime.hub", level="WARNING") as logs:
            await _subscribe(self.hub, ws, "hygiene-1", ["hygiene"], {"employee_id": COLLEAGUE})

        self.assertEqual(len(logs.output), 1, "越权尝试只该记一条")
        self.assertIn("employee_id", logs.output[0])
        self.assertIn(str(COLLEAGUE), logs.output[0], "日志要留下被尝试的同事 id")
        # 落库的订阅 filters 是服务端算的，不是客户端传的那个。
        self.assertEqual(
            self.hub._connections[ws].subscriptions["hygiene-1"].filters,
            {"employee_id": MINE},
        )

        await self.hub.broadcast_nudge("hygiene", COLLEAGUE_SCOPE)

        self.assertEqual(_nudges(ws), [], "覆盖后仍然收不到同事那条")

        await self.hub.broadcast_nudge("hygiene", MINE_SCOPE)
        await self.hub.broadcast_nudge("hygiene", STORE_SCOPE)

        self.assertEqual(
            [m["scope"] for m in _nudges(ws)],
            [MINE_SCOPE, STORE_SCOPE],
            "自己的 + 全店的照收（filters 只按 employee_id，不会顺手挡掉全店级）",
        )

    async def test_staff_filters_matching_own_id_are_not_rewritten(self):
        """客户端传的就是自己的 id：结果一致，不该产生告警噪音。"""
        ws = _FakeWebSocket()
        await self.hub.register(ws, "staff", employee_id=MINE)

        with self.assertNoLogs("services.realtime.hub", level="WARNING"):
            await _subscribe(self.hub, ws, "hygiene-1", ["hygiene"], {"employee_id": MINE})

        self.assertEqual(
            self.hub._connections[ws].subscriptions["hygiene-1"].filters,
            {"employee_id": MINE},
        )

    async def test_staff_other_filters_are_dropped_without_security_warning(self):
        """只想收某个 resource 的 filters 也一并作废，但不算越权（不报 WARNING）。"""
        ws = _FakeWebSocket()
        await self.hub.register(ws, "staff", employee_id=MINE)

        with self.assertNoLogs("services.realtime.hub", level="WARNING"):
            await _subscribe(self.hub, ws, "hygiene-1", ["hygiene"], {"resource": "boards"})

        self.assertEqual(
            self.hub._connections[ws].subscriptions["hygiene-1"].filters,
            {"employee_id": MINE},
        )

    async def test_staff_connection_without_identity_gets_store_wide_only(self):
        """身份没解析出来的员工连接：fail-closed，带 employee_id 的一律不推。"""
        ws = _FakeWebSocket()
        await self.hub.register(ws, "staff")
        await _subscribe(self.hub, ws, "hygiene-1", ["hygiene"], {"employee_id": MINE})

        self.assertEqual(
            self.hub._connections[ws].subscriptions["hygiene-1"].filters,
            {},
            "认不出身份时留空 filters，由派发侧继续拦",
        )

        await self.hub.broadcast_nudge("hygiene", COLLEAGUE_SCOPE)
        await self.hub.broadcast_nudge("hygiene", MINE_SCOPE)

        self.assertEqual(_nudges(ws), [], "不知道该推给谁就不推")

        await self.hub.broadcast_nudge("hygiene", STORE_SCOPE)

        self.assertEqual([m["scope"] for m in _nudges(ws)], [STORE_SCOPE])

    async def test_admin_connections_still_receive_colleague_scoped_nudge(self):
        """管理端（session / api_token）行为不变：别人的 nudge 照收。"""
        for auth in ("session", "api_token"):
            ws = _FakeWebSocket()
            await self.hub.register(ws, auth)
            await _subscribe(self.hub, ws, "hygiene-1", ["hygiene"])

            await self.hub.broadcast_nudge("hygiene", COLLEAGUE_SCOPE)
            await self.hub.broadcast_nudge("hygiene", MINE_SCOPE)
            await self.hub.broadcast_nudge("hygiene", STORE_SCOPE)

            self.assertEqual(
                [m["scope"] for m in _nudges(ws)],
                [COLLEAGUE_SCOPE, MINE_SCOPE, STORE_SCOPE],
                auth,
            )

    async def test_admin_explicit_filters_are_still_honored(self):
        """管理端能自己按 employee_id 收窄：客户端 filters 不被覆盖。"""
        ws = _FakeWebSocket()
        await self.hub.register(ws, "session")
        await _subscribe(self.hub, ws, "hygiene-1", ["hygiene"], {"employee_id": COLLEAGUE})

        self.assertEqual(
            self.hub._connections[ws].subscriptions["hygiene-1"].filters,
            {"employee_id": COLLEAGUE},
        )

        await self.hub.broadcast_nudge("hygiene", MINE_SCOPE)
        self.assertEqual(_nudges(ws), [])

        await self.hub.broadcast_nudge("hygiene", COLLEAGUE_SCOPE)
        self.assertEqual([m["scope"] for m in _nudges(ws)], [COLLEAGUE_SCOPE])


class _FakeStaffAccounts:
    """只实现 identify_ws 用到的那一个方法，避免本文件拉进 DB/花名册。"""

    def __init__(self, by_cookie):
        self._by_cookie = by_cookie

    async def get_staff_session(self, session_id):
        return self._by_cookie.get(session_id)


class _RecordingHub:
    """记录 register/unregister：验端点把哪些身份交给了 hub。"""

    def __init__(self):
        self.registered = []
        self.unregistered = []

    async def register(self, websocket, auth, employee_id=None):
        self.registered.append((auth, employee_id))

    async def handle_message(self, websocket, raw):
        raise AssertionError("本用例不发 subscribe 消息")

    def unregister(self, websocket):
        self.unregistered.append(websocket)


class _EndpointWebSocket:
    """最小 fake：够 `/ws/realtime` 走完 accept → 鉴权 → register → 收消息循环。"""

    def __init__(self, cookies=None, query_params=None):
        self.cookies = cookies or {}
        self.query_params = query_params or {}
        self.sent = []
        self.accepted = False
        self.closed_code = None

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000):
        self.closed_code = code

    async def send_json(self, payload):
        self.sent.append(payload)

    async def receive_text(self):
        # 模拟客户端断开：让端点的收消息循环退出（WebSocketDisconnect 与其它异常
        # 在端点里都走 unregister，这里用后者，免得依赖 starlette 的异常类型）。
        raise RuntimeError("客户端断开")


class RealtimeEndpointIdentityTest(unittest.IsolatedAsyncioTestCase):
    """端点接线：建连时鉴权出来的 employee_id 必须进 ConnectionState。"""

    async def asyncSetUp(self):
        import main

        self.main = main
        self._prev_accounts = main.employee_accounts
        self._prev_hub = main.realtime_hub
        self.hub = _RecordingHub()
        main.realtime_hub = self.hub

    async def asyncTearDown(self):
        self.main.employee_accounts = self._prev_accounts
        self.main.realtime_hub = self._prev_hub

    async def test_staff_connection_registers_with_its_employee_id(self):
        from config import settings

        self.main.employee_accounts = _FakeStaffAccounts({"staff-cookie-1": {"id": MINE}})
        ws = _EndpointWebSocket(cookies={settings.STAFF_SESSION_COOKIE_NAME: "staff-cookie-1"})

        await self.main.realtime_ws(ws)

        self.assertTrue(ws.accepted)
        self.assertEqual(ws.sent, [{"type": "connected"}])
        self.assertEqual(self.hub.registered, [("staff", MINE)])
        self.assertEqual(self.hub.unregistered, [ws])

    async def test_staff_connection_with_unknown_cookie_is_rejected(self):
        from config import settings

        self.main.employee_accounts = _FakeStaffAccounts({})
        ws = _EndpointWebSocket(
            cookies={settings.STAFF_SESSION_COOKIE_NAME: "bogus-staff-cookie"}
        )

        await self.main.realtime_ws(ws)

        self.assertEqual(ws.closed_code, 4401)
        self.assertEqual(self.hub.registered, [])

    async def test_connection_without_credentials_is_rejected(self):
        self.main.employee_accounts = _FakeStaffAccounts({})
        ws = _EndpointWebSocket()

        await self.main.realtime_ws(ws)

        self.assertEqual(ws.closed_code, 4401)
        self.assertEqual(self.hub.registered, [])

    async def test_identify_ws_carries_employee_id_but_authenticate_ws_keeps_string(self):
        """`authenticate_ws` 的返回值不变（老调用方不受影响），身份走 identify_ws。"""
        from api.security import WsIdentity, authenticate_ws, identify_ws
        from config import settings

        self.main.employee_accounts = _FakeStaffAccounts({"staff-cookie-1": {"id": MINE}})
        ws = _EndpointWebSocket(cookies={settings.STAFF_SESSION_COOKIE_NAME: "staff-cookie-1"})

        self.assertEqual(await identify_ws(ws), WsIdentity(auth="staff", employee_id=MINE))
        self.assertEqual(await authenticate_ws(ws), "staff")

        anonymous = _EndpointWebSocket()
        self.assertIsNone(await identify_ws(anonymous))
        self.assertIsNone(await authenticate_ws(anonymous))


if __name__ == "__main__":
    unittest.main()
