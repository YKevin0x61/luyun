#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""移动银台登录：accesskey 算法 + 登录顺序契约。"""

import base64
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from scraper.yintai_auth import (
    ACCESS_KEY_SECRET,
    YintaiLoginError,
    make_access_key,
)

# 真实样本：(devId, devRandom, 服务端实际使用的 accesskey)
# 只收「代理抓包里同一会话同时看到 devRandom 与 accesskey」的三组，排除事后推算
REAL_SAMPLES = [
    ("602fa453f77b4e92af41b71a16e1f0e9", "716", "NqrX5ep5UW3TrwwmyTt1zfhSpGIxzVbb4MBKvRn2ZOo="),
    ("2a66b85c801f417998de073e2c7dd90f", "129", "1nnhnzMu2neSusRXeOoDLZU7VIKyX2jCxoK6opV/WGw="),
    ("9ed73d022cf3416f819eb0ca3145fe3a", "38", "cjkMKyk8Lj1ALuH6XQA7Hw=="),
]


class MakeAccessKeyTest(unittest.TestCase):
    def test_matches_real_server_keys(self):
        """核心断言：算法必须能复现服务端真实用过的 accesskey。"""
        for dev_id, rnd, expected in REAL_SAMPLES:
            with self.subTest(dev_random=rnd):
                self.assertEqual(make_access_key(dev_id, rnd), expected)

    def test_known_vector_shape(self):
        """加解密自洽：解回来应是 devId 的对应切片。"""
        dev_id = "9ed73d022cf3416f819eb0ca3145fe3a"
        key = make_access_key(dev_id, "38")
        raw = base64.b64decode(key)
        decryptor = Cipher(algorithms.AES(ACCESS_KEY_SECRET), modes.ECB()).decryptor()
        plain = decryptor.update(raw) + decryptor.finalize()
        pad = plain[-1]
        self.assertEqual(plain[:-pad].decode(), dev_id[3:11])

    def test_rejects_bad_dev_random(self):
        for bad in ("", "7", "abc", "-12", "7x"):
            with self.subTest(dev_random=bad):
                with self.assertRaises(YintaiLoginError):
                    make_access_key("a" * 32, bad)

    def test_rejects_slice_beyond_dev_id(self):
        # devRandom="932" → 需要 devId[9:41]，但 devId 只有 32 位
        with self.assertRaises(YintaiLoginError) as ctx:
            make_access_key("a" * 32, "932")
        self.assertIn("只有 32 位", str(ctx.exception))

    def test_slice_uses_first_digit_as_start(self):
        dev_id = "0123456789abcdef0123456789abcdef"
        # "415" → a=4, b=15 → devId[4:19]
        key = make_access_key(dev_id, "415")
        raw = base64.b64decode(key)
        decryptor = Cipher(algorithms.AES(ACCESS_KEY_SECRET), modes.ECB()).decryptor()
        plain = decryptor.update(raw) + decryptor.finalize()
        self.assertEqual(plain[:-plain[-1]].decode(), dev_id[4:19])


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """记录调用顺序，验证契约（regdev 必须先于 getversion）。"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def post(self, url, headers=None, json=None):
        path = url.split("/cy7/canyin/")[-1]
        self.calls.append((path, dict(headers or {}), json))
        for key, payload in self.responses.items():
            if key in path:
                return _FakeResponse(payload)
        raise AssertionError(f"未预期的请求：{path}")

    async def aclose(self):
        pass


def _responses(dev_code="107", dev_random="415", token="tok", ok=True):
    return {
        "regdev": {"result": 1, "data": {"devCode": dev_code, "shopId": 166768}},
        "getversion": {"result": 1, "data": {
            "devRandom": dev_random, "cyVersion": "7.7.78.222.1", "protocolVersion": "1.5.8.1"}},
        "getaccesstokenforinside": {"result": 1, "data": {"accessToken": token}},
        "archupdate": {"result": 1, "data": {"archCode": "x"}},
        "mobiledevices/login": {"result": 1, "data": {"id": "u1", "name": "赵文浩"}},
    } if ok else {}


class LoginOrderTest(unittest.IsolatedAsyncioTestCase):
    async def test_regdev_before_getversion_and_headers(self):
        from scraper.yintai_auth import login_yintai

        client = _FakeClient(_responses())
        session = await login_yintai(
            "https://shop.example.com", user_name="0126", password="000",
            dev_id="0123456789abcdef0123456789abcdef", client=client,
        )
        paths = [c[0] for c in client.calls]
        self.assertEqual(paths[:2], ["ordererreceive/mobiledevices/regdev",
                                     "ordererreceive/mobiledevices/getversion"])
        self.assertEqual(session.dev_code, "107")
        self.assertEqual(session.user_id, "u1")
        self.assertEqual(session.user_name, "赵文浩")

        # regdev 不带 accesskey；getversion 也不带
        self.assertNotIn("accesskey", client.calls[0][1])
        self.assertNotIn("accesskey", client.calls[1][1])
        # 之后必须带自算的 accesskey 与必备头
        login_headers = client.calls[-1][1]
        self.assertEqual(login_headers["accesskey"],
                         make_access_key("0123456789abcdef0123456789abcdef", "415"))
        for h in ("producttype", "requesttype", "cy_locale", "devid", "accesstoken"):
            self.assertIn(h, login_headers)

    async def test_archupdate_failure_is_reported(self):
        from scraper.yintai_auth import login_yintai

        res = _responses()
        res["archupdate"] = {"result": -1, "msg": "档案更新失败，未找到加密信息，请重启APP"}
        client = _FakeClient(res)
        with self.assertRaises(YintaiLoginError) as ctx:
            await login_yintai("https://shop.example.com", user_name="a", password="b",
                               dev_id="0123456789abcdef0123456789abcdef", client=client)
        self.assertIn("archupdate", str(ctx.exception))

    async def test_login_failure_is_reported(self):
        from scraper.yintai_auth import login_yintai

        res = _responses()
        res["mobiledevices/login"] = {"result": -1, "msg": "用户名或密码错误"}
        client = _FakeClient(res)
        with self.assertRaises(YintaiLoginError) as ctx:
            await login_yintai("https://shop.example.com", user_name="a", password="b",
                               dev_id="0123456789abcdef0123456789abcdef", client=client)
        self.assertIn("login", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
