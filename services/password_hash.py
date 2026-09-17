#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared password hashing: SHA-256 then bcrypt (avoids bcrypt's 72-byte limit)."""

from __future__ import annotations

import asyncio
import hashlib

import bcrypt

from config import settings

BCRYPT_ROUNDS = 12


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
    return hashed.decode("utf-8")


def verify_password(password: str, stored_hash: str) -> bool:
    # Prefer bcrypt(sha256(password)); also accept legacy bcrypt(plaintext).
    stored = stored_hash.encode("utf-8")
    for candidate in (password_digest(password).encode("utf-8"), password.encode("utf-8")):
        try:
            if bcrypt.checkpw(candidate, stored):
                return True
        except (ValueError, TypeError):
            continue
    return False


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
