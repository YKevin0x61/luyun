#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""密码哈希：现行格式、legacy 兼容与惰性重写判定。

历史数据存在两种不带前缀的裸 bcrypt（bcrypt(plaintext) 与
bcrypt(sha256(password))），从哈希本身无法区分，必须都还能登录，
否则老账号会被直接锁死。
"""

import unittest

import bcrypt

from services.password_hash import (
    BCRYPT_ROUNDS,
    hash_password,
    hash_password_async,
    needs_rehash,
    password_digest,
    verify_password,
    verify_password_async,
)

PASSWORD = "perftest123"


def _legacy_bcrypt_plaintext(password: str) -> str:
    """复现历史格式：bcrypt(plaintext)，无前缀。"""
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("utf-8")


def _legacy_bcrypt_sha256(password: str) -> str:
    """复现历史格式：bcrypt(sha256(password))，同样无前缀。"""
    return bcrypt.hashpw(
        password_digest(password).encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("utf-8")


class HashFormatTest(unittest.TestCase):
    def test_new_hash_carries_prefix(self):
        self.assertTrue(hash_password(PASSWORD).startswith("sha256$"))

    def test_needs_rehash_only_for_unprefixed(self):
        self.assertFalse(needs_rehash(hash_password(PASSWORD)))
        self.assertTrue(needs_rehash(_legacy_bcrypt_plaintext(PASSWORD)))
        self.assertTrue(needs_rehash(_legacy_bcrypt_sha256(PASSWORD)))


class VerifyTest(unittest.TestCase):
    def test_current_format_roundtrip(self):
        stored = hash_password(PASSWORD)
        self.assertTrue(verify_password(PASSWORD, stored))
        self.assertFalse(verify_password("wrongpassword", stored))

    def test_legacy_bcrypt_plaintext_still_verifies(self):
        self.assertTrue(verify_password(PASSWORD, _legacy_bcrypt_plaintext(PASSWORD)))

    def test_legacy_bcrypt_sha256_still_verifies(self):
        self.assertTrue(verify_password(PASSWORD, _legacy_bcrypt_sha256(PASSWORD)))

    def test_malformed_hash_returns_false(self):
        self.assertFalse(verify_password(PASSWORD, "not-a-bcrypt-hash"))
        self.assertFalse(verify_password(PASSWORD, ""))


class AsyncWrapperTest(unittest.IsolatedAsyncioTestCase):
    async def test_roundtrip(self):
        stored = await hash_password_async(PASSWORD)
        self.assertTrue(await verify_password_async(PASSWORD, stored))

    async def test_wrong_password_returns_false(self):
        stored = await hash_password_async(PASSWORD)
        self.assertFalse(await verify_password_async("wrongpassword", stored))

    async def test_invalid_password_still_raises(self):
        """校验异常要能穿过线程池原样抛出。"""
        with self.assertRaises(ValueError):
            await hash_password_async("short")


if __name__ == "__main__":
    unittest.main()
