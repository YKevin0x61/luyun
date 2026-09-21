#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生端会话：哈希存储 + last_seen_at 刷新 + 闲置上限。

库里存的必须是 ``sha256(cookie)``，不是 cookie 原文——否则拿到 ``app.db`` 或
``deploy/backup.sh`` 的备份就能把那串字符填进浏览器冒充在线员工。

存量行里存的就是 cookie 原文，所以 ``prepare()`` 可以**原地**哈希化：员工手里的
cookie 不变，校验时哈希后照样匹配，不需要重新登录。这条是这份测试的重点。
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta

import pytest

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.hygiene.accounts import (
    LAST_SEEN_REFRESH_SECONDS,
    EmployeeAccounts,
    hash_session_id,
)

PHONE = "13800138000"
PASSWORD = "password123"
NAME = "张三"
START = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


class _Clock:
    def __init__(self, start=START):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now = self.now + timedelta(**kwargs)


@pytest.fixture
def accounts_env(tmp_path):
    old_dir = settings.DATABASE_DIR
    old_idle = settings.SESSION_IDLE_HOURS
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    clock = _Clock()
    accounts = EmployeeAccounts(db, now=clock)
    employee = _run(accounts.register(PHONE, PASSWORD, NAME))
    _run(accounts.approve(employee["id"]))
    try:
        yield db, accounts, clock
    finally:
        _run(db.close())
        settings.DATABASE_DIR = old_dir
        settings.SESSION_IDLE_HOURS = old_idle


def _stored_session_ids(db):
    cur = _run(db._conn.execute("SELECT session_id FROM hygiene_staff_sessions"))
    return [row[0] for row in _run(cur.fetchall())]


def test_login_stores_hash_and_cookie_still_authenticates(accounts_env):
    db, accounts, _clock = accounts_env
    result = _run(accounts.login(PHONE, PASSWORD))
    cookie = result["session_id"]

    stored = _stored_session_ids(db)
    assert stored == [hash_session_id(cookie)]
    # 明文不能落库——这是这一项的全部意义。
    assert cookie not in stored

    employee = _run(accounts.get_staff_session(cookie))
    assert employee is not None
    assert employee["phone"] == PHONE
    assert _run(accounts.get_staff_session("not-a-real-session")) is None


def test_prepare_hashes_legacy_plaintext_rows_in_place(accounts_env):
    """存量明文行原地哈希化之后，员工手上的 cookie 仍然有效（不强制重登）。"""
    db, accounts, _clock = accounts_env
    legacy_cookie = "legacy-plaintext-cookie-value"
    _run(db._conn.execute(
        """INSERT INTO hygiene_staff_sessions
           (session_id, employee_id, expires_at, created_at, last_seen_at)
           VALUES (?, 1, ?, ?, ?)""",
        (
            legacy_cookie,
            (START + timedelta(days=30)).isoformat(),
            START.isoformat(),
            START.isoformat(),
        ),
    ))
    _run(db._conn.commit())
    assert _stored_session_ids(db) == [legacy_cookie]

    _run(accounts.prepare())

    assert _stored_session_ids(db) == [hash_session_id(legacy_cookie)]
    employee = _run(accounts.get_staff_session(legacy_cookie))
    assert employee is not None, "哈希化不能把已在用的会话踢下线"


def test_prepare_is_idempotent(accounts_env):
    db, accounts, _clock = accounts_env
    result = _run(accounts.login(PHONE, PASSWORD))
    before = _stored_session_ids(db)

    _run(accounts.prepare())
    _run(accounts.prepare())

    assert _stored_session_ids(db) == before == [hash_session_id(result["session_id"])]


def test_logout_deletes_by_hash(accounts_env):
    db, accounts, _clock = accounts_env
    cookie = _run(accounts.login(PHONE, PASSWORD))["session_id"]

    _run(accounts.logout(cookie))

    assert _stored_session_ids(db) == []
    assert _run(accounts.get_staff_session(cookie)) is None


def test_last_seen_refreshes_only_after_throttle_window(accounts_env):
    db, accounts, clock = accounts_env
    cookie = _run(accounts.login(PHONE, PASSWORD))["session_id"]

    def last_seen():
        cur = _run(db._conn.execute(
            "SELECT last_seen_at FROM hygiene_staff_sessions WHERE session_id = ?",
            (hash_session_id(cookie),),
        ))
        return _run(cur.fetchone())[0]

    initial = last_seen()

    # 轮询很频繁：节流窗口内不能再写库（员工端每 30s 一次 /me）。
    clock.advance(seconds=30)
    _run(accounts.get_staff_session(cookie))
    assert last_seen() == initial

    clock.advance(seconds=LAST_SEEN_REFRESH_SECONDS)
    _run(accounts.get_staff_session(cookie))
    assert last_seen() != initial


def test_idle_session_is_revoked_even_within_ttl(accounts_env):
    db, accounts, clock = accounts_env
    settings.SESSION_IDLE_HOURS = 12
    # 勾了「记住密码」：TTL 是 30 天，闲置上限必须先把这条拦掉。
    cookie = _run(accounts.login(PHONE, PASSWORD, remember=True))["session_id"]
    assert _run(accounts.get_staff_session(cookie)) is not None

    clock.advance(hours=12, minutes=1)

    assert _run(accounts.get_staff_session(cookie)) is None
    assert _stored_session_ids(db) == [], "闲置超时要把行删掉，而不是只拒绝这一次"


def test_idle_limit_disabled_when_zero(accounts_env):
    _db, accounts, clock = accounts_env
    settings.SESSION_IDLE_HOURS = 0
    cookie = _run(accounts.login(PHONE, PASSWORD, remember=True))["session_id"]

    clock.advance(days=20)

    assert _run(accounts.get_staff_session(cookie)) is not None


def test_change_password_keeps_current_session_by_hash(accounts_env):
    db, accounts, _clock = accounts_env
    cookie = _run(accounts.login(PHONE, PASSWORD))["session_id"]
    other = _run(accounts.login(PHONE, PASSWORD))["session_id"]

    _run(accounts.change_password(1, PASSWORD, "newpassword123", keep_session_id=cookie))

    stored = _stored_session_ids(db)
    assert stored == [hash_session_id(cookie)]
    assert _run(accounts.get_staff_session(cookie)) is not None
    assert _run(accounts.get_staff_session(other)) is None


def test_no_plaintext_survives_a_reconnect(tmp_path):
    """换一次连接（等价于重启进程）之后库里仍然是哈希。"""
    old_dir = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    try:
        accounts = EmployeeAccounts(db, now=_Clock())
        employee = _run(accounts.register(PHONE, PASSWORD, NAME))
        _run(accounts.approve(employee["id"]))
        cookie = _run(accounts.login(PHONE, PASSWORD))["session_id"]
        _run(db.close())

        raw = sqlite3.connect(str(tmp_path) + "/app.db")
        try:
            rows = raw.execute("SELECT session_id FROM hygiene_staff_sessions").fetchall()
        finally:
            raw.close()
        assert [row[0] for row in rows] == [hash_session_id(cookie)]
    finally:
        settings.DATABASE_DIR = old_dir
