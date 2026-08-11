#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from scraper._common import (
    ScraperSessionError,
    pos_response_indicates_auth_failure,
    pos_session_lost,
)
from scraper.delivery_bill_tracker import (
    DELIVERY_POLL_INTERVAL_SECONDS,
    DeliveryBillTracker,
)
from scraper.pos_session import PosSession
from scraper.sly20_auth import parse_manageable_shop_payload

SAMPLE_SHOP_PAYLOAD = {
    "code": 2000,
    "data": [
        {
            "groupId": "506534412784093242",
            "shops": [
                {
                    "shopName": "LuckIn",
                    "bizShops": [
                        {
                            "bizShopCode": "200002",
                            "bizShopName": "LuckIn",
                            "bizGroupCode": "100001",
                            "productCode": "005",
                        },
                        {
                            "bizShopCode": "515185",
                            "bizGroupCode": "114940",
                            "productCode": "008",
                        },
                    ],
                }
            ],
        }
    ],
}


class _ProbeHost(PosSession):
    def __init__(self):
        self.table_mapping = {"1": 698, "福运": 741}
        self._creds = None


class PosApiProbeHelpersTest(unittest.TestCase):
    def test_auth_failure_hints(self):
        self.assertTrue(pos_response_indicates_auth_failure({"message": "当前用户未在本系统登录"}))
        self.assertTrue(pos_response_indicates_auth_failure({"code": 401}))
        self.assertFalse(pos_response_indicates_auth_failure({"success": True, "data": []}))

    def test_resolve_point_id_prefers_api_field(self):
        host = _ProbeHost()
        point_id = host._resolve_point_id_from_busy_row(
            {"pointName": "1", "pointId": "10000100000000698"}
        )
        self.assertEqual(point_id, "10000100000000698")

    def test_resolve_point_id_falls_back_to_mapping(self):
        host = _ProbeHost()
        host._creds = type("Creds", (), {"shop_id": "100001"})()
        point_id = host._resolve_point_id_from_busy_row({"pointName": "1"})
        self.assertEqual(point_id, "10000100000000698")

    def test_busy_point_rows_from_body(self):
        host = _ProbeHost()
        rows = host._busy_point_rows_from_body({"success": True, "data": [{"pointName": "1"}]})
        self.assertEqual(len(rows), 1)

    def test_settled_bill_rows_from_body(self):
        body = {"success": True, "data": {"list": [{"bsId": "1"}, {"bsId": ""}]}}
        rows = DeliveryBillTracker._settled_bill_rows_from_body(body)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bsId"], "1")

    def test_parse_manageable_shop_payload_filters_cy7mm(self):
        rows = parse_manageable_shop_payload(SAMPLE_SHOP_PAYLOAD)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["shop_id"], "100001")
        self.assertEqual(rows[0]["company_id"], "200002")
        self.assertEqual(rows[0]["delivery_shop_id"], "200002")


class PosSessionLostTest(unittest.TestCase):
    """会话失效判定：既要认得出真失效，也不能把业务错误误判成失效。"""

    def test_html_login_page_is_session_lost(self):
        html = "<!DOCTYPE html><html><head></head><body>redirecting to /login</body></html>"
        self.assertTrue(pos_session_lost(200, html))

    def test_http_401_is_session_lost(self):
        self.assertTrue(pos_session_lost(401, None))

    def test_known_chinese_hint_is_session_lost(self):
        self.assertTrue(
            pos_session_lost(200, {"success": False, "message": "当前用户未在本系统登录"})
        )

    def test_normal_payload_is_not_session_lost(self):
        self.assertFalse(pos_session_lost(200, {"success": True, "data": [], "code": None}))

    def test_business_error_mentioning_token_is_not_session_lost(self):
        """收紧后不再按 'token' 子串误判——重登一次要 25~48s，误判代价很高。"""
        self.assertFalse(
            pos_session_lost(200, {"success": False, "message": "打印 token 生成失败"})
        )

    def test_code_1000_is_not_session_lost(self):
        """182 份抓包中从未出现顶层 code=1000，不再当作鉴权失败。"""
        self.assertFalse(pos_session_lost(200, {"code": 1000, "message": "业务处理失败"}))


class _RecoveryClient:
    """PosHttpClient recovery path without Playwright."""

    def __init__(self, *, recover_ok: bool, responses):
        import logging

        from scraper.pos_http_client import PosHttpClient

        self.recover_calls = 0
        self.request_calls = 0
        self._responses = list(responses)
        self._recover_ok = recover_ok

        async def recover():
            self.recover_calls += 1
            return self._recover_ok

        self.http = PosHttpClient(
            get_page=lambda: None,
            recover=recover,
            timeout_ms=1000,
            max_retries=1,
            retry_backoff_s=0.01,
            logger_=logging.getLogger("test"),
        )

    async def _request(self):
        self.request_calls += 1
        return self._responses.pop(0)


class SessionRecoveryTest(unittest.IsolatedAsyncioTestCase):
    OK = (200, {"success": True, "data": []})
    LOST = (200, {"success": False, "message": "当前用户未在本系统登录"})

    async def test_healthy_response_does_not_relogin(self):
        host = _RecoveryClient(recover_ok=True, responses=[self.OK])
        status, body = await host.http.request_with_recovery(
            host._request, context_label="probe"
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        self.assertEqual(host.recover_calls, 0)
        self.assertEqual(host.request_calls, 1)

    async def test_session_lost_triggers_relogin_and_retry(self):
        host = _RecoveryClient(recover_ok=True, responses=[self.LOST, self.OK])
        status, body = await host.http.request_with_recovery(
            host._request, context_label="probe"
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        self.assertEqual(host.recover_calls, 1)
        self.assertEqual(host.request_calls, 2)

    async def test_relogin_failure_raises_so_tracker_can_alert(self):
        host = _RecoveryClient(recover_ok=False, responses=[self.LOST])
        with self.assertRaises(ScraperSessionError):
            await host.http.request_with_recovery(host._request, context_label="probe")
        self.assertEqual(host.recover_calls, 1)
        # 重登失败后不应再打一次请求
        self.assertEqual(host.request_calls, 1)

    async def test_only_retries_once(self):
        """重试后仍失效则直接返回，不无限重登。"""
        host = _RecoveryClient(recover_ok=True, responses=[self.LOST, self.LOST])
        status, body = await host.http.request_with_recovery(
            host._request, context_label="probe"
        )
        self.assertFalse(body["success"])
        self.assertEqual(host.recover_calls, 1)
        self.assertEqual(host.request_calls, 2)


class DeliveryPollThrottleTest(unittest.TestCase):
    """已结账单降频：首次立即拉，间隔内跳过。"""

    def _host(self):
        import logging

        class _S:
            pass

        class _St:
            pass

        host = DeliveryBillTracker(_S(), _St(), logger_=logging.getLogger("test"))
        host._last_delivery_poll_at = None
        return host

    def test_first_call_is_due(self):
        host = self._host()
        self.assertTrue(host._delivery_poll_due())

    def test_second_call_within_interval_is_skipped(self):
        host = self._host()
        self.assertTrue(host._delivery_poll_due())
        self.assertFalse(host._delivery_poll_due())

    def test_due_again_after_interval(self):
        host = self._host()
        self.assertTrue(host._delivery_poll_due())
        host._last_delivery_poll_at -= DELIVERY_POLL_INTERVAL_SECONDS + 1
        self.assertTrue(host._delivery_poll_due())


if __name__ == "__main__":
    unittest.main()
