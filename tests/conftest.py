#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试进程级隔离：钉死 SQLite 后端，避免测试写到真实 PostgreSQL 库。

各测试文件用 ``settings.DATABASE_DIR`` 指向临时目录来隔离数据，但那只对 SQLite
生效：本机 ``.env`` 里 ``DATABASE_BACKEND=postgres`` 时 ``DatabaseManager`` 会走
asyncpg 分支连上真实库（见 ADR 0084），测试注册的员工、会话就落进了真库。

所以在收集测试之前把后端钉死为 sqlite——env 变量优先级高于 .env，效果等同于
每次执行 ``DATABASE_BACKEND=sqlite pytest``，但不必记得带前缀。
"""

import os

# 必须早于 config 的 import：pydantic-settings 的优先级是 env 变量 > .env。
os.environ["DATABASE_BACKEND"] = "sqlite"

# TestClient 走 http://testserver，而 http.cookiejar 不会回发带 Secure 的 cookie，
# 于是所有"登录后"的用例都会 401。cookie 的 Secure 决策本身由
# tests/test_hygiene_cookie_secure.py 直接断言配置，不靠这里的开关兜着。
os.environ["SESSION_COOKIE_SECURE"] = "false"


def pytest_configure(config):
    """兜底断言：确实跑在 SQLite 上，否则宁可让整个会话失败，也不连真库。"""
    from config import settings

    if settings.DATABASE_BACKEND != "sqlite":
        raise RuntimeError(
            "测试必须跑在 SQLite 后端（conftest 已设置 DATABASE_BACKEND=sqlite），"
            f"当前实际是 {settings.DATABASE_BACKEND!r}——拒绝在真实数据库上跑测试"
        )
