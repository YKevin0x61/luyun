#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`tests/conftest.py` 的测试库守卫：判定必须落在**解析后的库名**上。

缺陷背景（`.scratch/project-review-2026-09-22` TEST-04）：守卫原来对**整串 DSN**
做 `dsn.rstrip("/").endswith(TEST_DB_NAME)`，于是库名**以 luyun_test 开头**的派生库
（`luyun_test_a`、`luyun_test_0902`）被拒 —— 拒绝本身是对的，坏在方式：报错说的是
"测试 DSN 必须指向测试库 luyun_test"，而 DSN 看上去正指向测试库，人只会觉得守卫坏了。
并行会话按 conftest 的注释把 `LUYUN_TEST_DSN` 指向自己的库时，最常见的命名恰恰就是
这种"前缀式"，撞上就是一条看不懂的 INTERNALERROR。

改成先 `urlparse` 出库名再判 `_TEST_DB_NAME.endswith(TEST_DB_NAME)` 后，判定对象是库名
而不是拼好的 URL，规则也从"整串以它结尾"变成"**库名以它结尾**"：

- `luyun_test`、`impl_luyun_test`、`a_luyun_test` 放行（前缀式派生名，本票要的形态）；
- **真库 `luyun` / `luyun_prod` / `postgres` 仍然被拒** —— 放行前缀式派生名不会削掉
  对真库的保护，规则本身（库名以 `luyun_test` 结尾）就把它们挡在外面。

有意保留的取舍：`luyun_test_a` 这类**库名前缀**形态仍被拒。要放行它就得改成子串
匹配，那样 `not_luyun_test_backup` 之类的库名也会被放行，反而削弱对真库的保护。

为什么用子进程：守卫住在 `pytest_configure` 里，进程内的守卫早在本会话启动时跑过了。
子进程故意把 `LUYUN_TEST_DSN` 指到别的库，并带 `-p no:watchdog`（插件不存在，pytest
静默忽略）—— 守卫放行的话会话会在建库/psql 那步继续（`--collect-only` 只收集、不跑
用例），守卫拒绝的话直接以 RuntimeError 结束；两种结局都能用报错前缀区分。放行的探针
库会被真正建出来（conftest 的 `_ensure_test_database`，与平时跑测试同一路数），所以
探针只允许用 `luyun_test` 结尾的库名——按约定那就是可丢的测试库。

只钉住这一条契约：**参数化的 DSN 拒绝/放行判定**。库名合法性、schema 重建、TRUNCATE
隔离都由 `conftest.py` 自己负责，不在本文件重复断言。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# 守卫拒绝时的报错前缀（conftest.pytest_configure 的原话）。
_REJECTION_MARKER = "测试 DSN 必须指向测试库"

# 放行的探针 DSN：库名以 luyun_test 结尾。子进程会真的建库（可丢的测试库）。
_ACCEPTED_DSNS = (
    "postgresql://localhost:5432/luyun_test",
    "postgresql://localhost:5432/a_luyun_test",
    "postgresql://localhost:5432/impl_luyun_test",
)

# 拒绝的探针 DSN：真库，以及"库名以 luyun_test 开头"的派生名（有意保留的取舍）。
_REJECTED_DSNS = (
    # 本机 .env 指着的真库 —— 守卫存在的唯一理由。
    "postgresql://localhost:5432/luyun",
    "postgresql://localhost:5432/luyun_prod",
    "postgresql://localhost:5432/postgres",
    # 派生名，但规则是"库名以 luyun_test 结尾"，所以仍被拒：放行它必须放宽成子串
    # 匹配，那会连 `not_luyun_test_backup` 一起放行（见模块 docstring 的取舍）。
    "postgresql://localhost:5432/luyun_test_a",
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
def test_guard_accepts_test_db_names_ending_in_luyun_test(dsn: str):
    """库名以 luyun_test 结尾的都放行（含前缀式派生名）。"""
    output = _guard_output(_run_probe(dsn))
    assert _REJECTION_MARKER not in output, f"守卫拒绝了合法测试库 {dsn}：\n{output}"


@pytest.mark.parametrize("dsn", _REJECTED_DSNS)
def test_guard_rejects_everything_else(dsn: str):
    """真库与不符合规则的库名都必须被拒 —— 放行派生名不许削弱这条。"""
    output = _guard_output(_run_probe(dsn))
    assert _REJECTION_MARKER in output, f"守卫放行了非测试库 {dsn}：\n{output}"


def test_guard_error_message_names_the_examples():
    """报错要能照做：给出可用库名示例与"库名以 luyun_test 结尾"的规则。"""
    output = _guard_output(_run_probe("postgresql://localhost:5432/luyun"))
    assert "库名以 luyun_test 结尾" in output, output
    examples = re.findall(r"postgresql://localhost:5432/(\w+)", output)
    assert "luyun_test" in examples, f"报错示例里没有可用库名：{examples}"
    assert any(name.endswith("_luyun_test") for name in examples if name != "luyun_test"), (
        f"报错示例里没有前缀式派生名：{examples}"
    )
