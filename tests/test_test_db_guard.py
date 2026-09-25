#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`tests/conftest.py` 的测试库守卫：判定必须落在**解析后的库名**上，且形态有界。

缺陷背景（`.scratch/project-review-2026-09-22` TEST-04）：守卫原来对**整串 DSN**
做 `dsn.rstrip("/").endswith(TEST_DB_NAME)`，于是库名**以 luyun_test 开头**的派生库
（`luyun_test_a`、`luyun_test_0902`）被拒 —— 拒绝本身是对的，坏在方式：报错说的是
"测试 DSN 必须指向测试库 luyun_test"，而 DSN 看上去正指向测试库，人只会觉得守卫坏了。
并行会话按 conftest 的注释把 `LUYUN_TEST_DSN` 指向自己的库时，最常见的命名恰恰就是
这种"前缀式"，撞上就是一条看不懂的 INTERNALERROR。

第一次修只放行了 `*_luyun_test` 后缀形态，`luyun_test_c` 照旧 INTERNALERROR（票面
验收"派生库名不再让 pytest INTERNALERROR"只做了一半）。规则改成 `TEST_DB_NAME_RE`
的**两种有界形态**：

- `<前缀>_luyun_test`：`luyun_test`、`impl_luyun_test`、`a_luyun_test`；
- `luyun_test_<后缀>`：`luyun_test_a`、`luyun_test_a_b`。

**真库与"像真库备份"的名字仍然被拒**：`luyun`、`luyun_prod`、`postgres` 不匹配任何
一支；`not_luyun_test_backup` 中间夹着 `luyun_test`、两头都有字，也不匹配 —— 只要
放宽成子串匹配，它就会被放行，那等于把真库的备份当测试库用。

为什么用子进程：守卫住在 `pytest_configure` 里，进程内的守卫早在本会话启动时跑过了。
子进程故意把 `LUYUN_TEST_DSN` 指到别的库，并带 `-p no:watchdog`（插件不存在，pytest
静默忽略）—— 守卫放行的话会话会在建库/psql 那步继续（`--collect-only` 只收集、不跑
用例），守卫拒绝的话直接以 RuntimeError 结束；两种结局都能用报错前缀区分。放行的探针
库会被真正建出来（conftest 的 `_ensure_test_database`，与平时跑测试同一路数），所以
探针只允许用 `luyun_test` 派生形态的库名——按约定那就是可丢的测试库。

只钉住一条契约：**参数化的 DSN 拒绝/放行判定**。库名合法性、schema 重建、TRUNCATE
隔离都由 `conftest.py` 自己负责，不在本文件重复断言。

票 27 之后本文件还多了第二组用例（见文件末尾「票 27」一节）：钉住新增的**生产库硬护栏**
（`_guard_effective_dsn()` / `_assert_live_connection_is_a_test_db()`）在真 pytest 进程里的
端到端行为——退出码 3、消息点名被冻结的库名、不误杀测试库。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import conftest

REPO_ROOT = Path(__file__).resolve().parents[1]

# 守卫拒绝时的报错前缀（conftest.pytest_configure 的原话）。
_REJECTION_MARKER = "测试 DSN 必须指向测试库"

# 放行的探针 DSN：库名落在两种有界形态上。子进程会真的建库（可丢的测试库）。
_ACCEPTED_DSNS = (
    "postgresql://localhost:5432/luyun_test",
    "postgresql://localhost:5432/a_luyun_test",
    "postgresql://localhost:5432/impl_luyun_test",
    # 后缀式派生名（TEST-04 的另一半）：票面点名的 `luyun_test_c` 就是这一形态。
    "postgresql://localhost:5432/luyun_test_a",
    "postgresql://localhost:5432/luyun_test_c",
    # 多段后缀也必须放过：会话名往往带分支/日期，不止一段。
    "postgresql://localhost:5432/luyun_test_a_b",
)

# 拒绝的探针 DSN：真库，以及"看着像派生名、其实是别的库"的名字。
_REJECTED_DSNS = (
    # 本机 .env 指着的真库 —— 守卫存在的唯一理由。
    "postgresql://localhost:5432/luyun",
    "postgresql://localhost:5432/luyun_prod",
    "postgresql://localhost:5432/postgres",
    # 中间夹着 luyun_test、两头都有字：一旦用子串匹配放行它，真库的备份就成了测试库。
    "postgresql://localhost:5432/not_luyun_test_backup",
    # 只是拼得像 `luyun_test`，后缀形态要求 `luyun_test_` 这个下划线。
    "postgresql://localhost:5432/luyun_testx",
)


def _run_probe(dsn: str) -> subprocess.CompletedProcess:
    """在子进程里起一个 pytest 会话，只为看守卫放行还是拒绝。

    `PYTHONPYCACHEPREFIX` 指到系统临时目录：仓内 `tests/__pycache__/conftest.*.pyc`
    的失效判据是"源文件 mtime + 大小"，改一行不改大小时可能读到旧的字节码——那会让
    这个探针报出与实际源码不符的结论。换个缓存前缀就永远从源码重新编译。
    """
    env = dict(os.environ)
    env["LUYUN_TEST_DSN"] = dsn
    env["PYTHONPYCACHEPREFIX"] = tempfile.mkdtemp(prefix="luyun-guard-probe-")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:watchdog",
            "tests/test_spa_page_routes.py",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _guard_output(proc: subprocess.CompletedProcess) -> str:
    return f"{proc.stdout}\n{proc.stderr}"


@pytest.mark.parametrize("dsn", _ACCEPTED_DSNS)
def test_guard_accepts_both_bounded_test_db_name_shapes(dsn: str):
    """`<前缀>_luyun_test` 与 `luyun_test_<后缀>` 都放行（含多段后缀）。"""
    output = _guard_output(_run_probe(dsn))
    assert _REJECTION_MARKER not in output, f"守卫拒绝了合法测试库 {dsn}：\n{output}"


@pytest.mark.parametrize("dsn", _REJECTED_DSNS)
def test_guard_rejects_everything_else(dsn: str):
    """真库与不符合规则的库名都必须被拒 —— 放行派生名不许削弱这条。"""
    output = _guard_output(_run_probe(dsn))
    assert _REJECTION_MARKER in output, f"守卫放行了非测试库 {dsn}：\n{output}"


def test_guard_error_message_names_the_examples():
    """报错要能照做：给出可用库名示例与两种放行形态。"""
    output = _guard_output(_run_probe("postgresql://localhost:5432/luyun"))
    assert "库名以 luyun_test 结尾" in output, output
    assert "luyun_test_ 开头" in output, output
    examples = re.findall(r"postgresql://localhost:5432/(\w+)", output)
    assert "luyun_test" in examples, f"报错示例里没有可用库名：{examples}"
    assert any(name.endswith("_luyun_test") for name in examples if name != "luyun_test"), (
        f"报错示例里没有前缀式派生名：{examples}"
    )
    assert any(name.startswith("luyun_test_") for name in examples), (
        f"报错示例里没有后缀式派生名：{examples}"
    )


# ── 票 27：生产库硬护栏（导入期冻结 DSN）──────────────────────────────────────
# 事故（2026-09-25）：pytest 插件在**导入期** import `db_core.reports` → 连带 import
# `config`，`.env` 里的真库被冻进 `settings.POSTGRES_DSN`；conftest 之后设置的
# `POSTGRES_DSN` 环境变量对已导入的 pydantic-settings 无效，于是 conftest 照常在测试库
# 建 schema，夹具数据却写进真库（生产 `orders` +325 行、`public.tables` 被覆盖 3 行）。
#
# 护栏本体在 conftest：`_guard_effective_dsn()`（`pytest_configure` 第一条动作，同时看
# 环境变量与已导入的 `config.settings.POSTGRES_DSN`）与
# `_assert_live_connection_is_a_test_db()`（用应用 DSN 真连一次，`SELECT
# current_database()` 必须是本会话的测试库）。下面这组用例钉住它们在**真 pytest 进程**
# 里的端到端行为：退出码 3、消息点名被冻结的库名、测试库不被误杀。

_GUARD_TARGET = "tests/test_test_db_guard.py"
# 本机 `.env` 指向的真库（事故里被写穿的那个库名）。
_GUARD_PROD_DSN = "postgresql://localhost:5432/luyun"
# 建/删"另一个测试库"用管理连接（与 conftest 同源）。
_GUARD_ADMIN_DSN = os.environ.get(
    "LUYUN_TEST_ADMIN_DSN", "postgresql://localhost:5432/postgres"
)
# 规范测试库（本文件既有探针也会用到它）：用来造"两个合法测试库不一致"的形状。
_GUARD_OTHER_TEST_DSN = "postgresql://localhost:5432/luyun_test"


def _guard_probe_dsn() -> str:
    """子进程专用测试库：带父进程 PID，免得与并发会话互相 `DROP SCHEMA`。"""
    return f"postgresql://localhost:5432/luyun_test_guard_{os.getpid()}"


def _psql_admin(sql: str) -> None:
    subprocess.run(
        ["psql", "-q", "-d", _GUARD_ADMIN_DSN, "-c", sql],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run_guard_child(*, pre_import: str = "", env_extra: dict | None = None) -> tuple[int, str]:
    """起一个真 pytest 子进程跑 `--collect-only`，可先执行 `pre_import` 模拟导入期 import。

    子进程带着干净的 DSN 环境（父进程的 `LUYUN_TEST_DSN` / `POSTGRES_DSN` 一律清掉），
    需要什么由 `env_extra` 显式给。靶子是本文件自身：`--collect-only` 只加载 conftest 与
    收集用例、不执行用例体，所以不会递归起子进程。返回 `(退出码, stdout+stderr)`。
    """
    script = (
        (pre_import + "\n" if pre_import else "")
        + "import pytest, sys\n"
        + f"sys.exit(pytest.main(['--collect-only', '-q', {_GUARD_TARGET!r}]))\n"
    )
    env = {**os.environ}
    env.pop("LUYUN_TEST_DSN", None)
    env.pop("POSTGRES_DSN", None)
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc.returncode, f"{proc.stdout}\n{proc.stderr}"


def test_prod_dsn_frozen_by_early_config_import_is_refused():
    """导入期 `import config` 把真库冻进 settings → 拒跑（rc=3），消息点名该库名。"""
    rc, out = _run_guard_child(
        pre_import="import config",
        env_extra={"POSTGRES_DSN": _GUARD_PROD_DSN, "LUYUN_TEST_DSN": _guard_probe_dsn()},
    )
    assert rc == 3, f"应当拒跑（rc=3），实际 rc={rc}\n{out}"
    assert "拒绝运行" in out
    # 精确点名被冻结的库名：`'luyun'` 带引号，`luyun_test_guard_<pid>` 不会误命中。
    assert "'luyun'" in out, f"拒跑消息必须点名实际库名 'luyun'\n{out}"
    assert "config 已在 conftest 之前被导入" in out, out


def test_incident_shape_is_caught_by_the_config_signal_alone():
    """事故的精确形状：`config` 冻在真库、环境变量随后被清掉 → 只有 config 那一处信号能发现。"""
    rc, out = _run_guard_child(
        pre_import="import config, os\nos.environ.pop('POSTGRES_DSN', None)",
        env_extra={"POSTGRES_DSN": _GUARD_PROD_DSN, "LUYUN_TEST_DSN": _guard_probe_dsn()},
    )
    assert rc == 3, f"应当拒跑（rc=3），实际 rc={rc}\n{out}"
    assert "已导入的 config.settings.POSTGRES_DSN 的库名 'luyun'" in out, out


def test_normal_session_is_not_refused():
    """正常场景：DSN 指向测试库、没人提前 import config → 正常收集（rc=0）。"""
    rc, out = _run_guard_child(env_extra={"LUYUN_TEST_DSN": _guard_probe_dsn()})
    assert rc == 0, f"正常会话不应被拒跑，实际 rc={rc}\n{out}"
    assert "拒绝运行" not in out
    assert "::test_prod_dsn_frozen_by_early_config_import_is_refused" in out, (
        f"靶子文件应当被收集到\n{out}"
    )


def test_early_config_import_with_a_test_dsn_is_not_refused():
    """指纹场景：`config` 确实在 conftest 之前被导入，但它的 DSN 本身就是测试库 → 不误杀。"""
    dsn = _guard_probe_dsn()
    rc, out = _run_guard_child(
        pre_import="import config",
        env_extra={"POSTGRES_DSN": dsn, "LUYUN_TEST_DSN": dsn},
    )
    assert rc == 0, f"不应误杀，实际 rc={rc}\n{out}"
    assert "拒绝运行" not in out


def test_mismatched_test_databases_are_refused_by_the_live_check():
    """两处信号都是测试库形状、但**不是同一个**库 → 自证连上后发现不一致，仍拒跑。

    这种形状同样危险：夹具数据会落进一个没被本会话 `TRUNCATE` 的库（表现为随机假红、
    脏数据）。这条用例让"真连一次"那步变成承重件——把自证调用删掉，它就绿不了。
    """
    _psql_admin('CREATE DATABASE "luyun_test"')  # 已存在时报错，忽略
    rc, out = _run_guard_child(
        pre_import="import config",
        env_extra={"POSTGRES_DSN": _GUARD_OTHER_TEST_DSN, "LUYUN_TEST_DSN": _guard_probe_dsn()},
    )
    assert rc == 3, f"应当拒跑（rc=3），实际 rc={rc}\n{out}"
    assert "实际连到的库是 'luyun_test'" in out, out


def test_unreachable_test_shaped_dsn_is_refused_by_the_live_check():
    """自证连不上（DSN 是测试库形状、库却不存在）→ 同样拒跑：库名没被确认前不往下走。"""
    absent = f"luyun_test_guard_absent_{os.getpid()}"
    rc, out = _run_guard_child(
        pre_import="import config",
        env_extra={
            "POSTGRES_DSN": f"postgresql://localhost:5432/{absent}",
            "LUYUN_TEST_DSN": _guard_probe_dsn(),
        },
    )
    assert rc == 3, f"应当拒跑（rc=3），实际 rc={rc}\n{out}"
    assert "连不上 PostgreSQL" in out and absent in out, out


def test_dsn_db_name_parsing():
    """判据落在解析后的库名上（口令不参与、查询串不干扰）。"""
    assert conftest._dsn_db_name("postgresql://user:secret@localhost:5432/luyun") == "luyun"
    assert (
        conftest._dsn_db_name("postgresql://localhost:5432/luyun_test_db10?sslmode=disable")
        == "luyun_test_db10"
    )
    assert conftest._dsn_db_name("") is None
    assert conftest._dsn_db_name(None) is None


def test_live_connection_is_the_session_test_database():
    """兜底自证：应用实际用的 DSN 连出来的库名 == 本会话认定的测试库（真连一次）。"""
    import config as config_module
    import pg_probe

    dsn = config_module.settings.POSTGRES_DSN
    name = conftest._dsn_db_name(dsn)
    assert name is not None and conftest.TEST_DB_NAME_RE.match(name), (
        f"测试进程的 settings.POSTGRES_DSN 指向 {name!r}"
    )
    assert name == conftest._TEST_DB_NAME
    assert pg_probe.scalar("SELECT current_database()") == conftest._TEST_DB_NAME
