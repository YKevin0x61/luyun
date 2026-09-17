#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""订单接口的 limit 安全上限，以及密码哈希的异步包装。

背景：本项目强制单 worker 单事件循环部署。一次 18.4 万行的订单响应会让
事件循环连续占用约 5 秒，期间整个后端被冻结（实测并发 /api/healthz 从
1ms 恶化到 5027ms）；bcrypt(rounds=12) 单次约 168ms 的同步计算同理。
"""

import unittest

from api.orders import MAX_ORDERS_LIMIT, _clamp_orders_limit
from services.password_hash import (
    hash_password_async,
    verify_password,
    verify_password_async,
)

PASSWORD = "perftest123"


class ClampOrdersLimitTest(unittest.TestCase):
    def test_normal_value_returned_unchanged(self):
        """正常分页请求不受影响。"""
        self.assertEqual(_clamp_orders_limit(30), 30)
        self.assertEqual(_clamp_orders_limit(1000), 1000)
        self.assertEqual(_clamp_orders_limit(MAX_ORDERS_LIMIT), MAX_ORDERS_LIMIT)

    def test_unlimited_clamped_to_max(self):
        """limit<=0 原先表示「无限制」，必须收进上限。"""
        self.assertEqual(_clamp_orders_limit(-1), MAX_ORDERS_LIMIT)
        self.assertEqual(_clamp_orders_limit(0), MAX_ORDERS_LIMIT)

    def test_oversized_clamped_to_max(self):
        """超大 limit 同样收进上限，避免一次响应阻塞事件循环。"""
        self.assertEqual(_clamp_orders_limit(10_000), MAX_ORDERS_LIMIT)
        self.assertEqual(_clamp_orders_limit(10 ** 9), MAX_ORDERS_LIMIT)


class PasswordHashAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_roundtrip(self):
        hashed = await hash_password_async(PASSWORD)
        self.assertTrue(await verify_password_async(PASSWORD, hashed))

    async def test_wrong_password_returns_false(self):
        hashed = await hash_password_async(PASSWORD)
        self.assertFalse(await verify_password_async("wrongpassword", hashed))

    async def test_async_compatible_with_sync(self):
        """异步包装只把同步实现挪进线程池，哈希格式必须保持一致。"""
        hashed = await hash_password_async(PASSWORD)
        self.assertTrue(verify_password(PASSWORD, hashed))

    async def test_invalid_password_still_raises(self):
        """校验异常要能穿过线程池原样抛出。"""
        with self.assertRaises(ValueError):
            await hash_password_async("short")


if __name__ == "__main__":
    unittest.main()
