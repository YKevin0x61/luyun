#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试进程级隔离：钉死 PostgreSQL 测试库，避免测试写到真实业务库。

后端已收敛为 PostgreSQL（ADR 0089）：SQLite 的「临时目录里的一个库文件」隔离
不再存在，测试改为

1. 把 ``DATABASE_BACKEND`` 与 DSN 钉死到专用测试库 ``luyun_test``；
2. 会话开始时用 ``migrations/pg/*.sql`` 重建 schema（0001 是 DROP + CREATE）；
3. 每个用例开始前 ``TRUNCATE`` 全部表（``RESTART IDENTITY CASCADE``）；
4. ``DATABASE_DIR`` 指向临时目录，让凭据/照片这类文件不落到仓库 ``data/``。

本机 ``.env`` 指向真实库（``luyun``），所以这里的 DSN 覆盖必须在 ``import config``
之前完成——pydantic-settings 的优先级是 env 变量 > ``.env``。
"""

import glob
import os
import signal
import subprocess
import sys
import tempfile
import time

import pytest

TEST_DB_NAME = "luyun_test"
# 建库/建 schema 用管理连接；CI 与本地默认都走本机 trust 认证。
_TEST_DSN = os.environ.get(
    "LUYUN_TEST_DSN", f"postgresql://localhost:5432/{TEST_DB_NAME}"
)
_ADMIN_DSN = os.environ.get("LUYUN_TEST_ADMIN_DSN", "postgresql://localhost:5432/postgres")

# 库名以 DSN 为准（不是常量）：并行干活时各会话可以把 LUYUN_TEST_DSN 指向自己的
# 库（名字仍须以 luyun_test 结尾，断言见 pytest_configure），避免共用同一个库时
# 互相 TRUNCATE / 撞唯一键。
from urllib.parse import urlparse  # noqa: E402

_TEST_DB_NAME = urlparse(_TEST_DSN).path.lstrip("/") or TEST_DB_NAME

# 必须早于 config 的 import：env 变量优先级高于 .env。
os.environ["DATABASE_BACKEND"] = "postgres"
os.environ["POSTGRES_DSN"] = _TEST_DSN
# lifespan 里的常驻后台循环（爬虫轮询、卫生调度、企微推送、数据质量调度）在共享
# 测试库上会长期驻留并互相干扰：测试进程一律关掉（见 config.DISABLE_BACKGROUND_TASKS）。
os.environ["DISABLE_BACKGROUND_TASKS"] = "true"
# LUYUN_POSTGRES_DSN 优先级高于 POSTGRES_DSN，留着会把测试引到真实库。
os.environ.pop("LUYUN_POSTGRES_DSN", None)

# TestClient 走 http://testserver，而 http.cookiejar 不会回发带 Secure 的 cookie，
# 于是所有"登录后"的用例都会 401。cookie 的 Secure 决策本身由
# tests/test_hygiene_cookie_secure.py 直接断言配置，不靠这里的开关兜着。
os.environ["SESSION_COOKIE_SECURE"] = "false"

# 用例之间共享一个测试库：某个用例把连接留在事务里时，下一条语句会一直等锁。
# 给测试连接加 statement_timeout，把「挂起」变成「超时失败」。
os.environ.setdefault("LUYUN_PG_STATEMENT_TIMEOUT_MS", "30000")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_FILES = sorted(glob.glob(os.path.join(_REPO_ROOT, "migrations", "pg", "*.sql")))


def _psql(dsn: str, sql: str | None = None, file: str | None = None) -> None:
    """跑一次 psql；失败直接把输出抛出去，别让测试在半个 schema 上跑。"""
    cmd = ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsn]
    cmd += ["-f", file] if file is not None else ["-c", sql or ""]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"psql 失败（dsn={dsn}）: {proc.stderr.strip() or proc.stdout.strip()}"
        )


def _ensure_test_database() -> None:
    exists = subprocess.run(
        [
            "psql",
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname='{_TEST_DB_NAME}'",
            _ADMIN_DSN,
        ],
        capture_output=True,
        text=True,
    )
    if exists.stdout.strip() != "1":
        _psql(_ADMIN_DSN, f'CREATE DATABASE "{_TEST_DB_NAME}"')


def _all_tables_sql() -> str:
    listing = subprocess.run(
        [
            "psql",
            "-tAc",
            "SELECT tablename FROM pg_tables WHERE schemaname='public'",
            _TEST_DSN,
        ],
        capture_output=True,
        text=True,
    )
    names = [n.strip() for n in listing.stdout.splitlines() if n.strip()]
    # schema_migrations 记的是迁移状态；tenants 是 0001 里 seed 的默认门店行——
    # 业务表的 tenant_id 都外键指向它，清掉它会让所有插入违反外键。
    names = [n for n in names if n not in ("schema_migrations", "tenants")]
    if not names:
        raise RuntimeError("测试库里没有任何表——schema 没建成功？")
    quoted = ", ".join(f'"{n}"' for n in names)
    return f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"


# 会话开始前清掉上一次（可能被 Ctrl-C 打断的）会话留下的后端连接：它们持有锁时
# TRUNCATE 会一直等下去。**只在会话级做**——用例级做会连测试进程自己正在用的
# asyncpg 连接一起杀掉（表现为 connection is closed / terminating connection）。
_KILL_OTHER_BACKENDS = (
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
)

# 用例之间只掐「事务没结束」的连接（见 _kill_stuck_backends 的说明）。
# 注意 `idle in transaction` 只覆盖一半情况：语句已执行完、事务没提交、客户端还没
# 发下一条时，state 仍是 active + wait_event=ClientRead——现场就是它持着
# admin_user 的锁把后续用例的 TRUNCATE 全部堵死。
_KILL_STUCK_BACKENDS = (
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
    "WHERE datname = current_database() AND pid <> pg_backend_pid() "
    "AND (state LIKE 'idle in transaction%' "
    "     OR (state = 'active' AND wait_event_type = 'Client'))"
)


def _kill_stale_backends() -> None:
    """会话开始前清掉上次会话残留的连接（可能持有锁）。"""
    subprocess.run(
        ["psql", "-q", "-d", _TEST_DSN, "-c", _KILL_OTHER_BACKENDS],
        capture_output=True,
        text=True,
    )


def _kill_stuck_backends() -> None:
    """只掐「事务没结束」的连接。

    用例之间**不能**无差别杀连接：测试进程自己会复用连接（类级 TestClient 的
    lifespan、模块级单例），杀掉它们会让后续用例报 connection is closed。
    真正会挡住 TRUNCATE 的是留在事务里的连接（``idle in transaction``），
    只处理这些。
    """
    subprocess.run(
        ["psql", "-q", "-d", _TEST_DSN, "-c", _KILL_STUCK_BACKENDS],
        capture_output=True,
        text=True,
    )


def _drop_dead_loop_connections() -> None:
    """把绑在已关闭事件循环上的全局连接丢掉。

    asyncpg 连接不能跨事件循环：用例里 ``asyncio.run(db.connect())`` 建好连接后，
    它会留在 ``services.app_runtime`` / ``main.db_manager`` 这两个进程级单例上；下一个
    用例换了新 loop，一碰就是「got Future attached to a different loop」。SQLite 时代
    每个用例一个临时库文件，天然没这个问题。

    这里只丢弃**绑在已关闭 loop 上**的连接，所以类级 TestClient（其 portal 线程里跑着
    活着的 loop）不受影响——早先「无条件重置单例」的做法正是因此把连接搞挂的。
    """
    from services.app_runtime import get_runtime, set_runtime

    runtime = get_runtime()
    db = getattr(runtime, "db", None) if runtime is not None else None
    if db is None:
        return
    raw = getattr(getattr(db, "_conn", None), "_raw", None)
    loop = getattr(raw, "_loop", None)
    if loop is None or loop.is_closed():
        set_runtime(None)
        module = sys.modules.get("main")
        if module is not None:
            module.db_manager = None


def _truncate_all() -> None:
    global _TRUNCATE_SQL
    if _TRUNCATE_SQL is None:
        _TRUNCATE_SQL = _all_tables_sql()
    sql = _TRUNCATE_SQL
    last_error = ""
    for attempt in range(3):
        proc = subprocess.run(
            [
                "psql",
                "-q",
                "-v",
                "ON_ERROR_STOP=1",
                "-d",
                _TEST_DSN,
                "-c",
                f"SET lock_timeout = '3s'; {sql}",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return
        last_error = proc.stderr.strip() or proc.stdout.strip()
        _kill_stuck_backends()
        time.sleep(0.2 * (attempt + 1))
    raise RuntimeError(f"清空测试库失败（重试 3 次）: {last_error}")


def pytest_configure(config):
    """兜底断言：确实跑在测试库上，否则宁可让整个会话失败，也不连真库。"""
    _start_watchdog()
    from config import settings

    if settings.DATABASE_BACKEND != "postgres":
        raise RuntimeError(
            "测试必须跑在 PostgreSQL 后端（conftest 已设置 DATABASE_BACKEND=postgres），"
            f"当前实际是 {settings.DATABASE_BACKEND!r}——SQLite 后端已移除（ADR 0089）"
        )

    from db_core.backend.pg import dsn_from_env

    dsn = dsn_from_env() or ""
    if not dsn.rstrip("/").endswith(TEST_DB_NAME):
        raise RuntimeError(
            f"测试 DSN 必须指向测试库 {TEST_DB_NAME}，当前是 {dsn!r}——拒绝在真实库上跑测试"
        )

    # 文件类数据（凭据、照片等）落到临时目录，别污染仓库 data/。
    settings.DATABASE_DIR = tempfile.mkdtemp(prefix="luyun-test-data-")

    _ensure_test_database()
    # 0001 是 bootstrap-only（含 DROP TABLE，但不含 tenants 的 DROP），只在空库可跑：
    # 每次会话先把 public schema 整个丢掉重建，保证从干净状态应用全量脚本。
    subprocess.run(
        ["psql", "-q", "-d", _TEST_DSN, "-c", _KILL_OTHER_BACKENDS],
        capture_output=True,
        text=True,
    )
    _psql(_TEST_DSN, "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
    for path in _SCHEMA_FILES:
        _psql(_TEST_DSN, file=path)
    global _TRUNCATE_SQL
    _TRUNCATE_SQL = _all_tables_sql()


class _CaseTimeout(Exception):
    """单个用例的墙钟超时（见 _arm_case_timeout）。"""


def _case_timeout_handler(signum, frame):
    raise _CaseTimeout(f"用例超过 {_CASE_TIMEOUT_SECONDS}s 未结束")


# 用例级别的墙钟上限：PG 化之后，某些用例会卡在锁等待或「等后台任务完成」的
# 轮询上，而不带超时的挂起会让整个套件失去意义（现场：跑到备份用例就永久停住）。
_CASE_TIMEOUT_SECONDS = 90


def _arm_case_timeout() -> None:
    if not hasattr(signal, "SIGALRM"):
        return
    signal.signal(signal.SIGALRM, _case_timeout_handler)
    signal.alarm(_CASE_TIMEOUT_SECONDS)


def _disarm_case_timeout() -> None:
    if hasattr(signal, "SIGALRM"):
        signal.alarm(0)


def pytest_runtest_setup(item):
    """每个用例从干净库开始：先丢掉绑在已关闭 loop 上的连接，再清数据。"""
    _drop_dead_loop_connections()
    # 单点防线：任何用例把后端改回 sqlite 又没恢复（setUp 抛错时 tearDown 不会执行，
    # 现场 `test_release_update_readiness.py` 就是这么把后面所有文件带崩的），
    # 会让后续每个 connect() 都抛「SQLite 后端已移除」。
    from config import settings as _settings

    if _settings.DATABASE_BACKEND != "postgres":
        _settings.DATABASE_BACKEND = "postgres"
    _truncate_all()
    _arm_case_timeout()


def pytest_runtest_teardown(item, nextitem):
    _disarm_case_timeout()


# ── 看门狗 ───────────────────────────────────────────────────────────
# SIGALRM 墙钟超时在 asyncio / 线程 join 路径上不一定能中断（现场：备份用例
# 等后台导出作业时仍旧挂死）。看门狗跑在独立线程里：超过阈值没收到用例心跳，
# 就打印当时的调用栈再退出，让「卡在哪」可见，而不是整个套件无限等待。
_WATCHDOG_SECONDS = 60
_WATCHDOG_REPORT = os.path.join(tempfile.gettempdir(), "luyun-pytest-watchdog.txt")
_last_heartbeat = time.monotonic()
_watchdog_started = False


def _start_watchdog() -> None:
    global _watchdog_started
    if _watchdog_started:
        return
    _watchdog_started = True

    import faulthandler
    import sys
    import threading

    def _loop() -> None:
        while True:
            time.sleep(5)
            if time.monotonic() - _last_heartbeat > _WATCHDOG_SECONDS:
                # 直接写文件 + 用 __stderr__：pytest 的捕获会吞掉进程退出前的缓冲，
                # 卡住时我们需要的是「栈落在磁盘上」。
                with open(_WATCHDOG_REPORT, "w", encoding="utf-8") as fh:
                    fh.write(
                        f"用例超过 {_WATCHDOG_SECONDS}s 没有进展，"
                        "打印调用栈后退出（避免整个套件挂死）\n"
                    )
                    fh.flush()
                    faulthandler.dump_traceback(file=fh)
                sys.__stderr__.write(
                    f"\n[watchdog] 疑似挂起，调用栈见 {_WATCHDOG_REPORT}\n"
                )
                sys.__stderr__.flush()
                os._exit(3)

    threading.Thread(target=_loop, name="pytest-watchdog", daemon=True).start()


def pytest_runtest_logstart(nodeid, location):
    global _last_heartbeat
    _last_heartbeat = time.monotonic()


@pytest.fixture(autouse=True)
def _no_real_database_restore(monkeypatch):
    """测试进程里绝不真的执行整库恢复。

    恢复是「pg_restore --clean 覆盖整库」，在共享的测试库上既会破坏其它测试的
    连接，也会因为别的连接持锁而**永久等待**（现场：全量跑到 backup 用例就挂住）。
    需要断言调用参数的用例自己 ``mock.patch.object``，会覆盖这里的替身。
    """
    import inspect
    from unittest import mock

    from services import backup_service

    for name in ("restore_app_pg_from_bytes", "restore_app_db_from_bytes"):
        target = getattr(backup_service, name, None)
        if target is None or isinstance(target, mock.Mock):
            continue
        replacement = (
            mock.AsyncMock() if inspect.iscoroutinefunction(target) else mock.Mock()
        )
        monkeypatch.setattr(backup_service, name, replacement)
