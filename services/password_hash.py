#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared password hashing: SHA-256 then bcrypt (avoids bcrypt's 72-byte limit)."""

from __future__ import annotations

import asyncio
import hashlib

import bcrypt

from config import settings

BCRYPT_ROUNDS = 12

# 存储格式前缀。现行哈希写作 ``sha256$<bcrypt(sha256(password))>``；历史数据是
# 不带前缀的裸 bcrypt（既可能是 bcrypt(sha256)，也可能是 bcrypt(plaintext)），
# 从哈希本身无法区分。用前缀把「确定的现行格式」与「需要试探的 legacy」分开：
# 带前缀只跑一次 bcrypt，无前缀才走双候选，并在登录成功后由调用方重写。
_CURRENT_PREFIX = "sha256$"


def password_digest(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def validate_password(password: str) -> None:
    if len(password) < settings.AUTH_MIN_PASSWORD_LENGTH:
        raise ValueError("password_too_short")
    if len(password.encode("utf-8")) > settings.AUTH_MAX_PASSWORD_BYTES:
        raise ValueError("password_too_long")


def hash_password(password: str) -> str:
    validate_password(password)
    digest = password_digest(password).encode("utf-8")
    hashed = bcrypt.hashpw(digest, bcrypt.gensalt(rounds=BCRYPT_ROUNDS))
    return _CURRENT_PREFIX + hashed.decode("utf-8")


def _checkpw(candidate: bytes, stored: bytes) -> bool:
    try:
        return bcrypt.checkpw(candidate, stored)
    except (ValueError, TypeError):
        return False


def verify_password(password: str, stored_hash: str) -> bool:
    if stored_hash.startswith(_CURRENT_PREFIX):
        # 现行格式：只可能是 bcrypt(sha256(password))，一次 bcrypt 即可判定。
        stored = stored_hash[len(_CURRENT_PREFIX):].encode("utf-8")
        return _checkpw(password_digest(password).encode("utf-8"), stored)
    # legacy：无前缀，无法区分 bcrypt(sha256) 与 bcrypt(plaintext)，只能逐个试。
    # 密码错误时两次都会跑，这正是 needs_rehash 要在登录成功后重写的原因。
    stored = stored_hash.encode("utf-8")
    for candidate in (password_digest(password).encode("utf-8"), password.encode("utf-8")):
        if _checkpw(candidate, stored):
            return True
    return False


def needs_rehash(stored_hash: str) -> bool:
    """legacy 无前缀哈希需要重写为现行格式，好让后续验证只跑一次 bcrypt。"""
    return not stored_hash.startswith(_CURRENT_PREFIX)


# bcrypt(rounds=12) 是纯 CPU 同步调用，实测单次约 168ms。本项目强制单 worker
# 单事件循环部署（见 deploy/luyun.service），在协程里直接调用会把整个后端冻结
# 同样时长——KDS 心跳、WS 广播、爬虫循环一起卡住。凡在 async 路径上使用，一律
# 走下面两个包装，把 CPU 计算挪到线程池。

async def hash_password_async(password: str) -> str:
    """``hash_password`` 的异步包装；``ValueError`` 会原样透传。"""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, stored_hash: str) -> bool:
    """``verify_password`` 的异步包装，理由同 ``hash_password_async``。"""
    return await asyncio.to_thread(verify_password, password, stored_hash)
