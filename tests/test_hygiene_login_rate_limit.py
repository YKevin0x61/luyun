#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工登录要有限流。

审查报告 §1.5：管理员登录（api/auth.py）早就按 IP 限流，员工登录却可以无限次猜。
两个维度都要有——只按 IP 会被分布式绕过，只按手机号会被人拿来锁死同事的账号。
"""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork

PHONE = "13800138000"
PASSWORD = "password123"


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture
def login_http(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    # 计数器是模块级的，用例之间必须隔离。
    hygiene_module._staff_login_failures.clear()
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(
        db, now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
    )
    work = HygieneWork(
        db,
        captures=FakeCaptureStore(),
        now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    employee = _run(accounts.register(PHONE, PASSWORD, "张三"))
    _run(accounts.approve(employee["id"]))
    app = FastAPI()
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    with TestClient(app) as client:
        yield client, accounts
    hygiene_module._staff_login_failures.clear()
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _login(client, phone=PHONE, password="wrong-password"):
    return client.post(
        "/api/hygiene/staff/login",
        json={"phone": phone, "password": password},
    )


def test_repeated_failures_for_one_phone_get_throttled(login_http):
    client, _accounts = login_http
    limit = hygiene_module._STAFF_LOGIN_MAX_PER_PHONE

    for _ in range(limit):
        assert _login(client).status_code == 401

    blocked = _login(client)
    assert blocked.status_code == 429
    assert "尝试次数过多" in blocked.json()["detail"]


def test_a_successful_login_clears_the_counter(login_http):
    client, _accounts = login_http
    for _ in range(hygiene_module._STAFF_LOGIN_MAX_PER_PHONE - 1):
        assert _login(client).status_code == 401

    assert _login(client, password=PASSWORD).status_code == 200
    # 计数清零后又能正常失败，而不是接着被挡
    assert _login(client).status_code == 401


def test_ip_dimension_catches_spraying_across_phones(login_http):
    client, _accounts = login_http
    ip_limit = hygiene_module._STAFF_LOGIN_MAX_PER_IP

    for index in range(ip_limit):
        phone = f"1390013{index:04d}"
        assert _login(client, phone=phone).status_code == 401, phone

    blocked = _login(client, phone="13900139999")
    assert blocked.status_code == 429, "换手机号也不能绕过一个 IP 的总次数"


def test_throttling_does_not_leak_whether_the_account_exists(login_http):
    client, _accounts = login_http
    # 不存在的号码先耗掉自己的份额
    for _ in range(hygiene_module._STAFF_LOGIN_MAX_PER_PHONE):
        assert _login(client, phone="13700137000").status_code == 401
    assert _login(client, phone="13700137000").status_code == 429
    # 真实号码仍是 401（不是 429，也不是别的泄密状态）
    assert _login(client, phone=PHONE).status_code == 401


def test_tracking_table_does_not_grow_without_bound(login_http):
    """模块级限流表必须有上限，否则海量不同 IP/手机号能把它撑成内存泄漏。"""
    import time as _time

    client, _accounts = login_http
    failures = hygiene_module._staff_login_failures
    limit = hygiene_module._STAFF_LOGIN_MAX_TRACKED_KEYS

    # 全部塞成"窗口外"的陈旧键，模拟一次性撞完就不再来访的流量。
    stale = _time.time() - hygiene_module._STAFF_LOGIN_WINDOW_SECONDS - 10
    for index in range(limit + 100):
        failures[f"ip:203.0.{index // 256}.{index % 256}"].append(stale)

    assert len(failures) > limit
    assert _login(client, password=PASSWORD).status_code == 200  # 触发一次限流检查
    assert len(failures) < limit, "陈旧键应被清掉"


def test_a_flooded_table_is_trimmed_not_wiped(login_http):
    """窗口内活跃键就超限时按最旧淘汰，而不是整体清空。

    清空会把正在被限流的桶（包括受害者账号）一起重置，等于给攻击者一次重置全场
    计数的机会；按最旧淘汰则把限流强度保住，同时不让内存无限涨。
    """
    import time as _time

    client, _accounts = login_http
    failures = hygiene_module._staff_login_failures
    limit = hygiene_module._STAFF_LOGIN_MAX_TRACKED_KEYS
    now = _time.time()

    for index in range(limit + 10):
        # 越靠后越新：最旧的应该先被淘汰
        failures[f"phone:139{index:08d}"].append(now - (limit + 10 - index))

    assert _login(client, password=PASSWORD).status_code == 200
    assert 0 < len(failures) < limit, "要淘汰到水位以下，但不能全清"


def test_phone_bucket_key_is_normalized(login_http):
    """同一账号的不同写法必须落进同一个桶，否则手机号维度形同虚设。"""
    client, _accounts = login_http
    limit = hygiene_module._STAFF_LOGIN_MAX_PER_PHONE
    variants = [
        "13800138000",
        "+8613800138000",
        "138-0013-8000",
        "8613800138000",
        "138 0013 8000",
    ]

    for index in range(limit * 3):
        variant = variants[index % len(variants)]
        response = _login(client, phone=variant)
        if response.status_code == 429:
            break
    else:
        raise AssertionError("换写法就绕过了手机号维度的限流")

    # 而且桶里只有一个归一化后的键
    phone_keys = [key for key in hygiene_module._staff_login_failures if key.startswith("phone:")]
    assert phone_keys == ["phone:13800138000"]
