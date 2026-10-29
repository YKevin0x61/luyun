#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""凭据里的移动银台配置：脱敏输出与字段合并。"""

import unittest

from services.credentials_store import CredentialBundle


class SafeDictTest(unittest.TestCase):
    def _bundle(self, extra):
        return CredentialBundle(
            phone="15300001234",
            password="secret",
            shop_id="164876",
            company_id="166768",
            shop_name="禄运茶居（猎德店）",
            delivery_shop_id="166768",
            extra=extra,
        )

    def test_masks_yintai_password(self):
        b = self._bundle({
            "pos_source": "yintai",
            "yintai": {
                "base_url": "https://shop.example.com",
                "user_name": "0126",
                "password": "p@ss-xyz-999",
                "pos_id": "16487600000000031",
            },
        })
        safe = b.to_safe_dict()
        self.assertEqual(safe["pos_source"], "yintai")
        self.assertEqual(safe["yintai"]["base_url"], "https://shop.example.com")
        self.assertEqual(safe["yintai"]["user_name"], "0126")
        self.assertEqual(safe["yintai"]["pos_id"], "16487600000000031")
        # 关键：密码不能出现在任何返回字段里
        self.assertNotIn("p@ss-xyz-999", str(safe))
        self.assertNotIn("password", safe["yintai"])
        self.assertTrue(safe["yintai"]["password_set"])

    def test_empty_extra_yields_blank_section(self):
        safe = self._bundle({}).to_safe_dict()
        self.assertEqual(safe["pos_source"], "")
        self.assertEqual(safe["yintai"]["base_url"], "")
        self.assertFalse(safe["yintai"]["password_set"])

    def test_phone_still_masked(self):
        safe = self._bundle({}).to_safe_dict()
        self.assertNotIn("15300001234", str(safe))


if __name__ == "__main__":
    unittest.main()
