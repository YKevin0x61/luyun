#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试进程级隔离：钉死 PostgreSQL 测试库，避免测试写到真实业务库。

后端已收敛为 PostgreSQL（ADR 0089）：SQLite 的「临时目录里的一个库文件」隔离
不再存在，测试改为

1. 把 ``DATABASE_BACKEND`` 与 DSN 钉死到专用测试库（``luyun_test`` 及其派生名，
   见 ``TEST_DB_NAME_RE``）；并行跑（pytest-xdist）时**每个 worker 各拿一个库**；
2. 会话开始时用 ``migrations/pg/*.sql`` 重建 schema（0001 是 DROP + CREATE），
   紧接着全清一次、记下「干净基线」：哪些表该是空的、哪些序列该没被调用过；
3. 每个用例开始前把**偏离基线的部分**清回去（``TRUNCATE ... RESTART IDENTITY
   CASCADE``、必要时 ``ALTER SEQUENCE ... RESTART``）——库本来就干净时整段跳过。
   语义与「每个用例前全清」完全等价（用例看到的状态一样），只是不再让没写过库的
   用例白付一次全清；清哪些表由确定性查询决定，不依赖统计视图（后者的上报有节流，
   见 ``_cleanup_dirty``）；
4. ``DATABASE_DIR`` 指向临时目录，让凭据/照片这类文件不落到仓库 ``data/``。

本机 ``.env`` 指向真实库（``luyun``），所以这里的 DSN 覆盖必须在 ``import config``
之前完成——pydantic-settings 的优先级是 env 变量 > ``.env``。
"""

import glob
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

import pytest

TEST_DB_NAME = "luyun_test"


def _dsn_db_name(dsn: str | None) -> str | None:
    """取 DSN 的**库名**：错误消息里只报库名，不打印可能带口令的 DSN 全文。"""
    if not dsn:
        return None
    return urlparse(dsn).path.lstrip("/") or None


def _default_test_db_name(pid=None) -> str:
    """未显式给 ``LUYUN_TEST_DSN`` 时用的默认测试库名：它带**进程号**（MERGE-01）。

    两个并发会话（多 agent / 多 worktree / 并行分片）都落在同一个固定库名上时，
    各自会话开始 `DROP SCHEMA public CASCADE`、每个用例前清库，会互相清空
    对方——现场表现是一批与改动无关的 failed/error（MERGE-01）。带上 PID 后每个
    进程拿到自己的库，库名仍是 ``TEST_DB_NAME_RE`` 允许的 `luyun_test_<后缀>` 形态。
    显式 `LUYUN_TEST_DSN` 仍然优先：CI 与需要固定库名的场景不受影响。

    xdist 的 worker 也是**独立进程**、PID 各不相同，所以这一条天然覆盖 `-n 4`：四个
    worker 就是四个库，不需要为并行另起一套命名。刻意**不**用 `gw0` 这种固定 worker 名
    做后缀——同机上两个并行会话会双双落进 `luyun_test_gw0`，正好回到 MERGE-01 那个
    「互相清空」的老问题。
    """
    return f"{TEST_DB_NAME}_{os.getpid() if pid is None else pid}"

# 可接受的库名只有两种**有界**形态（判定对象是解析后的库名，见 pytest_configure）：
#   1. `<前缀>_luyun_test`（前缀可省）—— 并行会话各开一个库的既有用法，库名以它结尾；
#   2. `luyun_test_<后缀>` —— 同一件事的另一种命名（TEST-04：`luyun_test_a` 曾因
#      守卫写成 `dsn.endswith("luyun_test")` 而被拒，报错却说自己"必须指向测试库"）。
# 刻意**不给"前后都带东西"的名字留位置**：`not_luyun_test_backup` 这类名字中间夹着
# `luyun_test`、两头都有字，放行它就等于把真库的备份当测试库用。
#
# 并发会话**必须**各用一个库名（见 `_default_test_db_name`）：库名相同则两边的
# `DROP SCHEMA` / `TRUNCATE` 互相清空，红的是对方，不是你的改动。
TEST_DB_NAME_RE = re.compile(r"^(?:\w*_)?luyun_test$|^luyun_test_\w+$")

# ── 生产库硬护栏（票 27 / 2026-09-25 真实事故）──────────────────────────────
# 事故形状：某个 pytest 插件在**导入期** `import config`（或 import 连带 config 的模块，
# 例如 `db_core.reports`），pydantic-settings 当场把 `.env` 里的生产库读进
# `settings.POSTGRES_DSN`；本文件下面那次 `os.environ["POSTGRES_DSN"] = _TEST_DSN` 只对
# **尚未导入**的 config 有效，于是 conftest 照常在测试库建 schema，被测的
# `DatabaseManager` 却把夹具数据写进真库（2026-09-25 凌晨：生产 `orders` 被写 325 行、
# `public.tables` 被覆盖 3 行）。
#
# 所以判据不能只看"我们打算连的库"（`_TEST_DB_NAME`），必须看**生效值**：
#   1. `os.environ["POSTGRES_DSN"]` —— 被测应用最终读的就是它；
#   2. `"config" in sys.modules` 时 `config.settings.POSTGRES_DSN` 的**当前值** —— 这正是
#      "有人在 conftest 之前导入了 config" 的指纹，也是那次事故里唯一能看出问题的信号。
# 任一处不是测试库形状（`TEST_DB_NAME_RE`）→ 立即拒跑：不建 schema、不连库。
#
# 调用点在 `pytest_configure` 的第一行：在那里 `pytest.exit(returncode=3)` 才会是退出码 3；
# 写在 conftest **导入期**会被 pytest 包装成 `ImportError while loading conftest`（退出码 4）。
# 配套的"导入期不许 import config / db_core"禁令写在 AGENTS.md 的「Testing & CI」。


def _refuse_to_run(message: str) -> None:
    """拒跑：先写 stderr（pytest 的 Exit 摘要不保证带全文），再以退出码 3 结束会话。"""
    sys.stderr.write(f"\n{message}\n")
    sys.stderr.flush()
    pytest.exit(message, returncode=3)


def _guard_effective_dsn() -> None:
    """生效的 PostgreSQL DSN 不是测试库 → 拒跑（`pytest_configure` 第一条动作）。

    两处信号都看（票 27）：环境变量 `POSTGRES_DSN`（被测应用读它）与已导入的
    `config.settings.POSTGRES_DSN`（在 conftest 之前 import config 时被 `.env` 冻结，
    之后的环境变量覆盖对 pydantic-settings 无效）。错误消息只带库名，不带 DSN 全文。
    """
    signals: list[tuple[str, str | None]] = [
        ("环境变量 POSTGRES_DSN", os.environ.get("POSTGRES_DSN")),
    ]
    early_config = sys.modules.get("config")
    if early_config is not None:
        signals.append(
            (
                "已导入的 config.settings.POSTGRES_DSN",
                getattr(getattr(early_config, "settings", None), "POSTGRES_DSN", None),
            )
        )

    offenders = [
        f"{source} 的库名 {name!r}"
        for source, dsn in signals
        if (name := _dsn_db_name(dsn)) is not None and not TEST_DB_NAME_RE.match(name)
    ]
    if not offenders:
        return

    fingerprint = (
        "config 已在 conftest 之前被导入（本缺陷的指纹）"
        if early_config is not None
        else "config 未被提前导入"
    )
    _refuse_to_run(
        "[conftest] 测试进程指向了非测试库，拒绝运行："
        + "；".join(offenders)
        + f"（{fingerprint}）。"
        + f"测试 DSN 必须指向测试库：库名以 {TEST_DB_NAME} 结尾"
        + f"（如 impl_{TEST_DB_NAME}），或以 {TEST_DB_NAME}_ 开头"
        + f"（如 {TEST_DB_NAME}_a）；可用示例："
        + f"postgresql://localhost:5432/{TEST_DB_NAME}、"
        + f"postgresql://localhost:5432/impl_{TEST_DB_NAME}、"
        + f"postgresql://localhost:5432/{TEST_DB_NAME}_a。"
        + "请把 LUYUN_TEST_DSN 指向测试库，并清掉指向真库的 POSTGRES_DSN；"
        + "若确实是导入期 import 了 config，改成在 pytest_configure/fixture 里延迟 import"
        + "（见 AGENTS.md「Testing & CI」）。"
    )


def _assert_live_connection_is_a_test_db(dsn: str | None) -> None:
    """兜底自证（票 27）：真连一次，让库名自己回答"这次连的是哪个库"。

    上面的判据看的是环境变量与已导入的 settings；万一实际连接来自别处（DSN 又被谁
    改过、或 config 之外还有一条取 DSN 的路径），只有真连一次才看得见。用**被测应用
    同一个 DSN** 跑 `SELECT current_database()`，结果必须是本会话认定的测试库。
    """
    declared = _dsn_db_name(dsn)
    if declared is None or not TEST_DB_NAME_RE.match(declared):
        _refuse_to_run(
            f"[conftest] 非测试库 DSN，拒绝运行：解析出的库名 {declared!r} 不匹配 "
            f"TEST_DB_NAME_RE（{TEST_DB_NAME_RE.pattern}）"
        )

    probe = subprocess.run(
        ["psql", "-tAc", "SELECT current_database()", dsn or ""],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        _refuse_to_run(
            f"[conftest] 连不上 PostgreSQL（DSN 库名 {declared!r}），拒绝运行："
            f"{(probe.stderr or probe.stdout).strip()}"
        )

    connected = probe.stdout.strip()
    if connected != _TEST_DB_NAME or not TEST_DB_NAME_RE.match(connected):
        _refuse_to_run(
            f"[conftest] 实际连到的库是 {connected!r}，本会话认定的测试库是 "
            f"{_TEST_DB_NAME!r}（DSN 库名 {declared!r}）——两边不一致，拒绝运行"
        )

# 建库/建 schema 用管理连接；CI 与本地默认都走本机 trust 认证。
# 没给 LUYUN_TEST_DSN 时按 PID 派生唯一库名（MERGE-01）；给了就完全以它为准。
# 但显式 DSN 与 xdist 不能并存：那会让所有 worker 落进同一个库、互相清空对方，
# 正是 MERGE-01 的并行版（断言在 pytest_configure，见 _refuse_shared_db_under_xdist）。
_EXPLICIT_TEST_DSN = os.environ.get("LUYUN_TEST_DSN") or None
_TEST_DSN = _EXPLICIT_TEST_DSN or f"postgresql://localhost:5432/{_default_test_db_name()}"
_ADMIN_DSN = os.environ.get("LUYUN_TEST_ADMIN_DSN", "postgresql://localhost:5432/postgres")

# 库名以 DSN 为准（不是常量）：并行干活时各会话可以把 LUYUN_TEST_DSN 指向自己的
# 库（名字仍须匹配 TEST_DB_NAME_RE，断言见 pytest_configure），避免共用同一个库时
# 互相 TRUNCATE / 撞唯一键。
_TEST_DB_NAME = _dsn_db_name(_TEST_DSN) or TEST_DB_NAME

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


def _query(sql: str) -> list[list[str]]:
    """跑一次只读查询，按 ``|`` 切行切列返回（空结果 → 空列表）。

    用 ``-tAF'|'`` 而不是逐表一次 psql：每起一个 psql 进程约 15ms（实测），
    56 张表各来一次就是秒级开销，而它们本来可以拼进一条查询。
    """
    proc = subprocess.run(
        ["psql", "-tAF|", "-v", "ON_ERROR_STOP=1", "-d", _TEST_DSN, "-c", sql],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql 查询失败: {proc.stderr.strip() or proc.stdout.strip()}")
    return [line.split("|") for line in proc.stdout.splitlines() if line.strip()]


# schema_migrations 记的是迁移状态；tenants 是 0001 里 seed 的默认门店行——业务表的
# tenant_id 都外键指向它，清掉它会让所有插入违反外键。这两张表从不参与清理，与
# 「每例全清」时代逐字一致。
_NEVER_CLEARED = ("schema_migrations", "tenants")


def _business_tables() -> list[str]:
    """public 下**参与清理**的表：除 ``_NEVER_CLEARED`` 之外的全部。"""
    rows = _query("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
    names = [r[0] for r in rows if r[0] not in _NEVER_CLEARED]
    if not names:
        raise RuntimeError("测试库里没有任何表——schema 没建成功？")
    return names


def _truncate_sql(tables: list[str]) -> str:
    """``TRUNCATE`` 这些表并重置它们的序列。

    ``CASCADE`` 只波及**引用**被截断表的子表；``tenants`` 是纯父表（只被引用、不引用
    别的表），而它从不进清理集合——所以 CASCADE 永远碰不到门店基线行。
    """
    quoted = ", ".join(f'"{n}"' for n in tables)
    return f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"


def _owned_sequences(tables: list[str]) -> dict[str, int]:
    """这些表拥有的序列 → 起始值（重置回它就算复位）。

    归属走 ``pg_depend``（identity 是 ``'i'``、serial 是 ``'a'``，两种都算 owned），
    不能用 ``pg_get_serial_sequence(table, 'id')``：那要求主键列恰好叫 ``id``，而这里
    的表主键列名并不统一。
    """
    listed = ", ".join(f"'{t}'" for t in tables)
    rows = _query(
        "SELECT s.relname, seq.seqstart "
        "FROM pg_class s "
        "JOIN pg_namespace n ON n.oid = s.relnamespace "
        "JOIN pg_sequence seq ON seq.seqrelid = s.oid "
        "JOIN pg_depend d ON d.objid = s.oid "
        "     AND d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_class'::regclass "
        "     AND d.deptype IN ('a', 'i') "
        "JOIN pg_class t ON t.oid = d.refobjid "
        f"WHERE s.relkind = 'S' AND n.nspname = 'public' AND t.relname IN ({listed})"
    )
    return {r[0]: int(r[1]) for r in rows}


def _used_sequences(names: list[str]) -> list[str]:
    """其中**被调用过**的序列（``pg_sequences.last_value`` 非 NULL 即表示用过）。

    ``RESTART IDENTITY`` 之外还有半条语义要守：用例「插入若干行再删光」之后表是空的，
    但序列已经被推进——有测试直接断言新插入的行 id 从 1 开始（``test_recipe_store`` /
    ``test_recipe_api`` 的注释就写着靠 conftest 的 RESTART IDENTITY），所以序列也得
    复位。序列状态是直接读序列页、不是累计统计视图，没有 ``pg_stat_*`` 那种最长 1 秒
    的上报节流，读到的就是真值（见 ``_cleanup_dirty`` 的说明）。
    """
    if not names:
        return []
    listed = ", ".join(f"'{n}'" for n in names)
    rows = _query(
        "SELECT sequencename FROM pg_sequences "
        "WHERE schemaname = 'public' AND last_value IS NOT NULL "
        f"AND sequencename IN ({listed})"
    )
    return [r[0] for r in rows]


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
    loop = db.bound_loop() if db is not None else None
    if loop is None or loop.is_closed():
        set_runtime(None)
        module = sys.modules.get("main")
        if module is not None:
            module.db_manager = None


def _run_cleanup_command(sql: str) -> None:
    """执行一条清理语句，失败重试三次（每次先掐掉卡在事务里的连接）。"""
    last_error = ""
    for attempt in range(3):
        proc = subprocess.run(
            ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", _TEST_DSN, "-c", sql],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return
        last_error = proc.stderr.strip() or proc.stdout.strip()
        _kill_stuck_backends()
        time.sleep(0.2 * (attempt + 1))
    raise RuntimeError(f"清空测试库失败（重试 3 次）: {last_error}")


# 「干净基线」：会话开始全清一次后学到的状态。用例之间只需要把**偏离它的部分**清回去。
# 语义上等价于「每个用例前全清」——用例开始时看到的库状态逐字相同——但没写过库的用例
# 从此不必付那次全清（实测全清 56 张表 69ms，其中进程启动 15ms；干净时这里只要一次
# 21ms 的探测查询，脏了才追加一次清理）。
_CLEAN_TABLES: list[str] = []
_CLEAN_SEQUENCES: dict[str, int] = {}
_CLEANUP_STATS = {"skipped": 0, "cleaned": 0, "tables": 0, "sequences": 0}


def _learn_clean_baseline() -> None:
    """会话开始：先按老规矩全清一次，再把「清完的样子」记成基线。

    为什么要先全清：清理集合必须等于「用例开始时库该有的样子」。老规矩是每个用例前
    全清，用例看到的就是「除 ``_NEVER_CLEARED`` 外全空、序列未调用」；在会话开始时
    复现这个状态并把它记为基线，后面「只清偏离基线的部分」才与「每例全清」等价。
    """
    global _CLEAN_TABLES, _CLEAN_SEQUENCES
    _CLEAN_TABLES = _business_tables()
    _run_cleanup_command(f"SET lock_timeout = '3s'; {_truncate_sql(_CLEAN_TABLES)}")
    owned = _owned_sequences(_CLEAN_TABLES)
    # 全清带 RESTART IDENTITY，这些序列理论上都回到「未调用」。真遇到没被重置到的
    # （不属于任何表的游离序列、ownership 断了的），就从基线里摘出去不管它——那也正是
    # 「每例全清」时代的样子：当年的 TRUNCATE 同样碰不到那种序列。
    still_used = set(_used_sequences(list(owned)))
    _CLEAN_SEQUENCES = {n: v for n, v in owned.items() if n not in still_used}


def _dirty_probe_sql() -> str:
    """一条查询回答「相对基线，哪些表有行、哪些序列被调用过」。

    56 张空表的 ``EXISTS`` 拼成 ``UNION ALL`` 一次问完：实测查询本身约 6ms（整个 psql
    调用约 21ms，其余是进程启动）。等价于「逐表 count(*) > 0」，只是遇到第一行就返回。
    """
    if not _CLEAN_TABLES:
        raise RuntimeError("清理基线没建立——pytest_configure 没跑完？")
    parts = [
        f"SELECT 'table' AS kind, '{t}' AS name WHERE EXISTS (SELECT 1 FROM \"{t}\")"
        for t in _CLEAN_TABLES
    ]
    if _CLEAN_SEQUENCES:
        listed = ", ".join(f"'{n}'" for n in _CLEAN_SEQUENCES)
        parts.append(
            "SELECT 'sequence', sequencename FROM pg_sequences "
            "WHERE schemaname = 'public' AND last_value IS NOT NULL "
            f"AND sequencename IN ({listed})"
        )
    return " UNION ALL ".join(parts)


def _cleanup_dirty() -> None:
    """把库清回基线；本来就干净时一次探测查询就结束。

    **为什么不用 ``pg_stat_user_tables`` 的写入计数判脏**（那本是这里最省事的信号）：
    那套累计统计在后端提交时受 ``PGSTAT_MIN_INTERVAL``（1 秒）节流，没上报到共享内存
    之前别的连接读不到。实测 20 次「asyncpg 长连接写入后立刻从另一个连接查」，20 次
    全部看不到增长——**漏判一次就是用例间污染**，而污染的表现是相隔很远的用例莫名
    失败。所以判据只用确定性信号：表里有没有行、序列有没有被调用过，两者都不经过统计
    视图。代价是每例一次约 21ms 的探测（相比每例 69ms 的全清仍然是净赚）。
    """
    rows = _query(f"SET lock_timeout = '3s'; {_dirty_probe_sql()}")
    dirty_tables = [r[1] for r in rows if r[0] == "table"]
    dirty_sequences = [r[1] for r in rows if r[0] == "sequence"]
    if not dirty_tables and not dirty_sequences:
        _CLEANUP_STATS["skipped"] += 1
        return
    statements = []
    if dirty_tables:
        statements.append(_truncate_sql(dirty_tables))
    if dirty_sequences:
        # 插入后删光的用例：表已经空了，但序列被推进过，靠这一段复位（见 _used_sequences）。
        # 与上面的 TRUNCATE 重复重置同一条序列是允许的（顺序执行，结果一致）。
        statements.append(
            "; ".join(
                f'ALTER SEQUENCE "{n}" RESTART WITH {_CLEAN_SEQUENCES[n]}'
                for n in dirty_sequences
            )
        )
    _run_cleanup_command("SET lock_timeout = '3s'; " + "; ".join(statements))
    _CLEANUP_STATS["cleaned"] += 1
    _CLEANUP_STATS["tables"] += len(dirty_tables)
    _CLEANUP_STATS["sequences"] += len(dirty_sequences)


def _xdist_worker_id(config) -> str | None:
    """这个进程是真 xdist worker 就返回它的名字（``gw0``…），否则 ``None``。

    判据是 **``config.workerinput``**（xdist 只往 worker 进程里塞这个属性），刻意不用
    ``PYTEST_XDIST_WORKER`` 环境变量：worker 起出来的**子进程**会继承那个环境变量，而
    `tests/test_test_db_guard.py` 恰恰是用「spawn 一个 pytest 子进程」来验证拒跑行为的
    ——用环境变量判断的话，那些子进程会被自己的「并行不许设 LUYUN_TEST_DSN」规则拒掉
    （现场：串行全绿、并行 4 红，红的全是护栏用例）。
    """
    info = getattr(config, "workerinput", None)
    return info.get("workerid") if info else None


def _is_xdist_controller(config) -> bool:
    """xdist 的 controller 进程？——带着 ``-n`` 但没有 ``workerinput`` 的那个。

    controller 只收集与分派用例，一条用例都不跑；worker 才有 ``workerinput``。
    串行跑（没有 ``-n``）时两个条件都不成立，仍按老路建库建 schema。
    """
    if hasattr(config, "workerinput"):
        return False
    return bool(getattr(config.option, "numprocesses", None))


def pytest_configure(config):
    """兜底断言：确实跑在测试库上，否则宁可让整个会话失败，也不连真库。"""
    # 硬护栏（票 27）必须是这个 hook 的**第一条动作**：pytest.exit 在这里才会被当成
    # 退出码 3（在 conftest 导入期调用会被 pytest 包装成 ImportError、退出码 4），而且
    # 此时还没有建 schema、没有连库。
    _guard_effective_dsn()

    _start_watchdog()

    # 会话一开始就打出实际测试库与它的来源（MERGE-01）：并发会话各跑各的库，
    # 出问题时第一眼要能确认"这次连的是哪个库"。用 terminalreporter 而不是 print，
    # 免得被 pytest 的输出捕获吞掉。
    _db_source = (
        "LUYUN_TEST_DSN"
        if os.environ.get("LUYUN_TEST_DSN")
        else (
            f"未设置 LUYUN_TEST_DSN，xdist worker={_xdist_worker_id(config)}，"
            f"按 PID 派生（pid={os.getpid()}）"
            if _xdist_worker_id(config)
            else f"未设置 LUYUN_TEST_DSN，按 PID 派生（pid={os.getpid()}）"
        )
    )
    _announce = f"[conftest] 测试库: {_TEST_DB_NAME}（来源: {_db_source}）"
    _reporter = config.pluginmanager.getplugin("terminalreporter")
    if _reporter is not None:
        _reporter.write_line(_announce)
    else:  # pragma: no cover - 只有非终端插件环境（如嵌进别的 runner）会走到
        print(_announce)

    # 并行时每个 worker 必须各拿一个库：显式 LUYUN_TEST_DSN 会把所有 worker 指到同一个
    # 库上，各自 DROP SCHEMA / 清库，互相清掉对方的夹具数据（MERGE-01 的并行版，症状是
    # 一批与改动无关的红）。宁可拒跑，也不出一个会骗人的结果。
    #
    # 判据里必须**带上 controller**：只让 worker 拒跑的话，xdist 会把 worker 的
    # `pytest.exit(3)` 当成「worker 崩溃」→ 反复重启 worker 直到
    # "maximum crashed workers reached"，最后退出码是 5（no tests ran）而不是这里的 3，
    # 解释信息也被淹没。controller 同样跑这个 hook，在这里拒跑就能干净收场。
    if _EXPLICIT_TEST_DSN and (_xdist_worker_id(config) or _is_xdist_controller(config)):
        _refuse_to_run(
            f"[conftest] 并行（pytest-xdist）时不能设 LUYUN_TEST_DSN：库 "
            f"{_TEST_DB_NAME!r} 会被所有 worker 共用并互相清空。去掉 LUYUN_TEST_DSN，"
            "让每个 worker 按自己的 PID 派生一个库（luyun_test_<pid>）；"
            "或者去掉 -n 串行跑。"
        )

    # controller 不跑用例，也就没有库可准备；建库 + 重放 migrations 在这里纯属浪费
    # （还会多留一个没人用的库）。准备库是每个 worker 自己的事。
    if _is_xdist_controller(config):
        if _reporter is not None:
            _reporter.write_line(
                "[conftest] xdist controller：跳过建库与建 schema（各 worker 自备）"
            )
        return

    from config import settings

    if settings.DATABASE_BACKEND != "postgres":
        raise RuntimeError(
            "测试必须跑在 PostgreSQL 后端（conftest 已设置 DATABASE_BACKEND=postgres），"
            f"当前实际是 {settings.DATABASE_BACKEND!r}——SQLite 后端已移除（ADR 0089）"
        )

    from db_core.backend.pg import dsn_from_env

    dsn = dsn_from_env() or ""
    # 判定落在**解析后的库名**上，不是整串 DSN（TEST-04）。原先 `dsn.endswith("luyun_test")`
    # 把库名以 `luyun_test_` 开头的派生库（`luyun_test_a`）也拒掉，而报错说"必须指向
    # 测试库 luyun_test"——DSN 看上去正指向测试库，人只会以为守卫坏了。现在按
    # TEST_DB_NAME_RE 的两种有界形态判定，仍然拒绝真库 `luyun` / `luyun_prod` /
    # `postgres`，以及 `not_luyun_test_backup` 这种"中间夹着 luyun_test"的名字。
    if not TEST_DB_NAME_RE.match(_TEST_DB_NAME):
        raise RuntimeError(
            f"测试 DSN 必须指向测试库：库名以 {TEST_DB_NAME} 结尾"
            f"（如 impl_{TEST_DB_NAME}），或以 {TEST_DB_NAME}_ 开头"
            f"（如 {TEST_DB_NAME}_a），当前是 {_TEST_DB_NAME!r}"
            f"（DSN={dsn!r}）——拒绝在真实库上跑测试。"
            f"可用示例：postgresql://localhost:5432/{TEST_DB_NAME}、"
            f"postgresql://localhost:5432/impl_{TEST_DB_NAME}、"
            f"postgresql://localhost:5432/{TEST_DB_NAME}_a"
        )

    # 文件类数据（凭据、照片等）落到临时目录，别污染仓库 data/。
    settings.DATABASE_DIR = tempfile.mkdtemp(prefix="luyun-test-data-")

    _ensure_test_database()
    # 端到端自证（票 27）：上面的判据看的是环境变量与常量，真正建连的是被测应用。
    # 用应用实际会用的 DSN 真连一次，让 `SELECT current_database()` 回答"这次连的是
    # 哪个库"——放在建库之后（新库刚建出来才连得上）、DROP SCHEMA 之前（不允许在
    # 未确认库名时先动结构）。
    _assert_live_connection_is_a_test_db(settings.POSTGRES_DSN)

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
    _learn_clean_baseline()


class _CaseTimeout(Exception):
    """单个用例的墙钟超时（见 _arm_case_timeout）。"""


def _case_timeout_handler(signum, frame):
    raise _CaseTimeout(f"用例超过 {_CASE_TIMEOUT_SECONDS}s 未结束")


# 用例级别的墙钟上限：PG 化之后，某些用例会卡在锁等待或「等后台任务完成」的
# 轮询上，而不带超时的挂起会让整个套件失去意义（现场：跑到备份用例就永久停住）。
#
# 必须**明显小于** _WATCHDOG_SECONDS（见下方看门狗一节）：SIGALRM 的处理器在主线程
# 抛异常，实测能打断 time.sleep / Thread.join / await（epoll 等待被 EINTR 唤醒后，
# 按 PEP 475 传播处理器异常而不再重试），所以它是第一道防线——代价只有**一条**用例
# 记 failed，同批后续用例照常跑完、结果照常产出。余量 15s 留给超时后的报告与 teardown。
_CASE_TIMEOUT_SECONDS = 45


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
    _cleanup_dirty()
    _arm_case_timeout()


def pytest_runtest_teardown(item, nextitem):
    _disarm_case_timeout()


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """收尾报一句清理台账：跳过多少次、真清了多少表/序列。

    这一行是这项优化的**可观测面**——它直接回答「清库还在不在成本里」：干净用例占
    比越高，`跳过` 越大；如果某次改动让每个用例都写库，这里会立刻显示出来。
    """
    stats = _CLEANUP_STATS
    total = stats["skipped"] + stats["cleaned"]
    if total == 0:  # controller / --collect-only：没有用例跑过，不打印
        return
    terminalreporter.write_line(
        f"[conftest] 用例间清理：{total} 个用例中 {stats['skipped']} 个跳过（库本来就干净）、"
        f"{stats['cleaned']} 个执行过清理（共 {stats['tables']} 张表、{stats['sequences']} 条序列）"
    )


# ── 看门狗 ───────────────────────────────────────────────────────────
# 用例级 SIGALRM 之外的最后一道防线：跑在独立线程里，超过阈值没收到用例心跳就
# 打印当时的调用栈再退出，让「卡在哪」可见，而不是整个套件无限等待。它兜的是
# SIGALRM 打不断的路径——C 扩展里长时间不回到字节码的调用，以及信号处理器根本
# 不在其中执行的非主线程（心跳只在 pytest_runtest_logstart 更新，语义是「上一个
# 用例开始」，所以下面这个阈值实际就是单个用例的墙钟上限）。
#
# 不变量：_WATCHDOG_SECONDS > _CASE_TIMEOUT_SECONDS。倒挂（旧值 60 < 90）会让用例级
# 超时变成死代码——任何一条 60s+ 的慢用例先撞上看门狗的 os._exit(3)，整场 pytest
# （后面几百条用例的结果）一起丢掉。60s 同时还不算大，真挂死时仍会收场。
_WATCHDOG_SECONDS = 60
# 按 PID 区分：这份 dump 是排障现场，而并行会话（各自把 LUYUN_TEST_DSN 指向不同
# 测试库）共用固定路径会互相覆盖。旧路径 ``luyun-pytest-watchdog.txt``（不带 PID）
# 不再写；前缀沿用 tempfile.gettempdir()——macOS 上是 ``$TMPDIR``（/var/folders/…），
# 不是字面上的 /tmp。
_WATCHDOG_REPORT = os.path.join(
    tempfile.gettempdir(), f"luyun-pytest-watchdog.{os.getpid()}.txt"
)
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

    # 只列**实际存在**的替身目标（DATA-04）：`restore_app_db_from_bytes` 是 SQLite
    # 退场（ADR 0089）后实现已删除的死名字，留着会让人以为还有一条 SQLite 恢复路径。
    for name in ("restore_app_pg_from_bytes",):
        target = getattr(backup_service, name, None)
        if target is None or isinstance(target, mock.Mock):
            continue
        # 真实现另存一份：需要「跑真编排、只把 pg_restore 换成替身」的用例从这里
        # 取回（见 tests/test_backup_restore_flow.py 的通知断言）。用 setattr 而非
        # monkeypatch——它跨用例共享，而下面的替换本来就会被撤销。
        setattr(backup_service, f"_real_{name}", target)
        replacement = (
            mock.AsyncMock() if inspect.iscoroutinefunction(target) else mock.Mock()
        )
        monkeypatch.setattr(backup_service, name, replacement)
