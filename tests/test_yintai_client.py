#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""移动银台取数客户端：解析、鉴权失效识别、凭据读取。"""

import unittest

import httpx

from scraper.yintai_client import (
    YintaiAuthError,
    YintaiClient,
    YintaiCredentials,
    parse_point_states,
)

CREDS = YintaiCredentials(
    base_url="https://shop.example.com",
    dev_id="dev-1",
    access_key="ak-1",
    access_token="at-1",
    dev_code="097",
    user_id="1001",
)


def _client_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class ParsePointStatesTest(unittest.TestCase):
    def test_occupied_and_free_tables(self):
        payload = {
            "result": 1,
            "data": {
                "pointList": [
                    {
                        "pointId": "p1",
                        "pointName": "1",
                        "pointCode": "1",
                        "areaId": "a1",
                        "pointState": 1,
                        "defCapacity": 4,
                        "progressOfServingDishes": "0/0",
                    },
                    {
                        "pointId": "p2",
                        "pointName": "2",
                        "pointCode": "2",
                        "areaId": "a1",
                        "pointState": 2,
                        "openTime": "2026-10-29T13:42:44.261",
                        "peopleqty": 2,
                        "lastTotal": 12.0,
                        "bsId": "bs-9",
                        "progressOfServingDishes": "0/6",
                        "isVIP": 1,
                        "defCapacity": 4,
                    },
                ]
            },
        }

        states = parse_point_states(payload)

        self.assertEqual(len(states), 2)
        free, busy = states
        self.assertFalse(free.is_occupied)
        self.assertEqual(free.point_name, "1")
        self.assertEqual(free.capacity, 4)

        self.assertTrue(busy.is_occupied)
        self.assertEqual(busy.people_qty, 2)
        self.assertEqual(busy.last_total, 12.0)
        self.assertEqual(busy.bill_id, "bs-9")
        self.assertEqual(busy.serving_progress, "0/6")
        self.assertTrue(busy.is_vip)

    def test_empty_list(self):
        self.assertEqual(parse_point_states({"result": 1, "data": {"pointList": []}}), [])

    def test_missing_data_is_empty(self):
        self.assertEqual(parse_point_states({"result": 1}), [])


class YintaiClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_point_states_sends_auth_headers(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["accesskey"] = request.headers.get("accesskey")
            seen["devid"] = request.headers.get("devid")
            seen["accesstoken"] = request.headers.get("accesstoken")
            body = {
                "result": 1,
                "data": {
                    "pointList": [
                        {
                            "pointId": "p2",
                            "pointName": "2",
                            "pointCode": "2",
                            "areaId": "a1",
                            "pointState": 2,
                            "openTime": "2026-10-29T13:42:44",
                            "bsId": "bs-9",
                        }
                    ]
                },
            }
            return httpx.Response(200, json=body)

        client = YintaiClient(CREDS, client=_client_with(handler))
        states = await client.fetch_point_states()

        self.assertTrue(seen["path"].endswith("/mobiledevices/querypointlist"))
        self.assertEqual(seen["accesskey"], "ak-1")
        self.assertEqual(seen["devid"], "dev-1")
        self.assertEqual(seen["accesstoken"], "at-1")
        self.assertEqual(len(states), 1)
        self.assertTrue(states[0].is_occupied)

    async def test_auth_error_hint_raises_auth_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"result": -1, "msg": "客位档案生成失败，加密信息不正确，请重启APP重试"})

        client = YintaiClient(CREDS, client=_client_with(handler))
        with self.assertRaises(YintaiAuthError):
            await client.fetch_point_states()

    async def test_business_error_raises_plain_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"result": -1, "msg": "营业日未开班"})

        client = YintaiClient(CREDS, client=_client_with(handler))
        with self.assertRaises(Exception) as ctx:
            await client.fetch_point_states()
        self.assertNotIsInstance(ctx.exception, YintaiAuthError)

    async def test_http_500_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        client = YintaiClient(CREDS, client=_client_with(handler))
        with self.assertRaises(Exception):
            await client.fetch_point_states()

    async def test_regdev_without_token_headers(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["accesskey"] = request.headers.get("accesskey")
            seen["devid"] = request.headers.get("devid")
            return httpx.Response(200, json={"result": 1, "data": {"devCode": "097", "shopName": "猎德店"}})

        client = YintaiClient(CREDS, client=_client_with(handler))
        data = await client.regdev()

        self.assertIsNone(seen["accesskey"])
        self.assertEqual(seen["devid"], "dev-1")
        self.assertEqual(data["devCode"], "097")


class FromBundleTest(unittest.TestCase):
    def _bundle(self, extra):
        class B:
            pass

        b = B()
        b.extra = extra
        return b

    def test_reads_nested_section(self):
        creds = YintaiCredentials.from_bundle(
            self._bundle(
                {
                    "yintai": {
                        "base_url": "https://shop.example.com/",
                        "dev_id": "d",
                        "access_key": "k",
                        "access_token": "t",
                        "user_id": "9",
                    }
                }
            )
        )
        self.assertEqual(creds.base_url, "https://shop.example.com")
        self.assertEqual(creds.user_id, "9")

    def test_missing_fields_raise(self):
        with self.assertRaises(YintaiAuthError) as ctx:
            YintaiCredentials.from_bundle(self._bundle({}))
        self.assertIn("access_key", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class UnsafeEndpointGuardTest(unittest.IsolatedAsyncioTestCase):
    """防回归：写接口必须被客户端拦截。

    背景：2026-10-29 把 ``posserial``（实为**结班**）当查询接口调用，
    门店班次被结掉，之后所有设备登录都报「POS 尚未开班」，
    需要收银台关掉收银软件重开才恢复。
    """

    def _client(self):
        from scraper.yintai_client import YintaiClient, YintaiCredentials

        return YintaiClient(
            YintaiCredentials(
                base_url="https://example.invalid",
                dev_id="a" * 32,
                access_key="k",
                access_token="t",
            )
        )

    async def test_posserial_is_rejected(self):
        from scraper.yintai_client import YintaiUnsafeCallError

        client = self._client()
        with self.assertRaises(YintaiUnsafeCallError) as ctx:
            await client._post("/mobiledevices/posserial", {})
        msg = str(ctx.exception)
        self.assertIn("posserial", msg)
        self.assertIn("结班", msg)

    async def test_known_writes_are_rejected(self):
        from scraper.yintai_client import YintaiUnsafeCallError

        for endpoint in ("settle", "cancelsettle", "addorder", "cancelorder",
                         "wipezero", "openbill", "bindselectpos",
                         "checkandopenclass", "unbindpos", "printsettledbill"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(YintaiUnsafeCallError):
                    await self._client()._post(f"/mobiledevices/{endpoint}", {})

    async def test_unknown_endpoint_is_rejected_by_default(self):
        from scraper.yintai_client import YintaiUnsafeCallError

        with self.assertRaises(YintaiUnsafeCallError) as ctx:
            await self._client()._post("/mobiledevices/some_new_thing", {})
        self.assertIn("未登记为只读接口", str(ctx.exception))

    def test_read_only_whitelist_covers_scraper_needs(self):
        from scraper.yintai_client import READ_ONLY_ENDPOINTS

        for endpoint in ("querypointlist", "mergebillview", "querywuuorder",
                         "itemlist", "regdev", "getversion", "login", "archupdate"):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, READ_ONLY_ENDPOINTS)

    def test_posserial_never_whitelisted(self):
        from scraper.yintai_client import READ_ONLY_ENDPOINTS

        self.assertNotIn("posserial", READ_ONLY_ENDPOINTS)


class BillPayloadTypeTest(unittest.IsolatedAsyncioTestCase):
    """防回归：``mergebillview`` 的 pointId / bsId 必须传数字。

    服务端做 Long 强转，传字符串会报
    ``java.lang.String cannot be cast to java.lang.Long``（2026-10-29 实测）。
    """

    async def test_point_id_and_bs_id_are_sent_as_ints(self):
        from scraper.yintai_client import YintaiClient, YintaiCredentials

        captured = {}

        class _Fake(YintaiClient):
            async def _post(self, path, payload, *, with_token=True, allow_unsafe=False):
                captured["path"] = path
                captured["payload"] = payload
                return {"result": 1, "data": {"itemList": []}}

        client = _Fake(
            YintaiCredentials(
                base_url="https://example.invalid",
                dev_id="a" * 32,
                access_key="k",
                access_token="t",
                dev_code="120",
                user_id="16487600000000309",
            )
        )
        await client.fetch_bill_items("16487600000000698", "1667681000046620")
        params = captured["payload"]["params"]
        self.assertIsInstance(params["pointId"], int)
        self.assertIsInstance(params["bsId"], int)
        self.assertEqual(params["pointId"], 16487600000000698)
        self.assertEqual(params["bsId"], 1667681000046620)
