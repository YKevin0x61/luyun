#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared password hashing: SHA-256 then bcrypt (avoids bcrypt's 72-byte limit)."""

from __future__ import annotations

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
