#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""订单接口的 limit 安全上限。

背景：本项目强制单 worker 单事件循环部署。一次 18.4 万行的订单响应会让
事件循环连续占用约 5 秒，期间整个后端被冻结（实测并发 /api/healthz 从
1ms 恶化到 5027ms）。密码哈希相关测试见 test_password_hash.py。
"""

import unittest

from api.orders import MAX_ORDERS_LIMIT, _clamp_orders_limit


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


if __name__ == "__main__":
    unittest.main()
