#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""票 19 / MERGE-01：并发测试会话不能共用一个默认测试库。

背景：`tests/conftest.py` 以前在没设 `LUYUN_TEST_DSN` 时固定用 `luyun_test`。两个会话
（多 agent / 多 worktree / 并行分片）同时跑就会互相清空现场——会话夹具
`DROP SCHEMA public CASCADE`、每个用例前 `TRUNCATE` 全表，实测出现过一次
20 failed + 6 errors 的假红（.scratch/project-review-2026-09-23/issues/19）。

修法（方案 1）：未显式设置 `LUYUN_TEST_DSN` 时，库名按 PID 派生
`luyun_test_<pid>`（仍在 `TEST_DB_NAME_RE` 放行的形态内），并在会话开始打印实际库名。

本文件钉两件事：
1. 派生逻辑：不同 PID → 不同库名，都匹配 `TEST_DB_NAME_RE`，且都不是共享的 `luyun_test`；
2. 真并发的两个会话：两个子进程都不设 `LUYUN_TEST_DSN`，各自拿到不同库名。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import conftest

TESTS_DIR = Path(__file__).resolve().parent


def test_default_db_name_is_derived_from_pid():
    first = conftest._default_test_db_name(pid=101)
    second = conftest._default_test_db_name(pid=202)

    assert first != second
    assert first == f"{conftest.TEST_DB_NAME}_101"
    assert second == f"{conftest.TEST_DB_NAME}_202"
    assert conftest._default_test_db_name() == f"{conftest.TEST_DB_NAME}_{os.getpid()}"


def test_derived_names_stay_inside_the_allowed_shapes():
    for pid in (1, 101, 99999):
        name = conftest._default_test_db_name(pid=pid)
        assert conftest.TEST_DB_NAME_RE.match(name), (
            f"{name!r} 不在 TEST_DB_NAME_RE 放行的形态里（conftest 会直接拒绝启动）"
        )
        assert name != conftest.TEST_DB_NAME, (
            f"默认库名仍与共享的 {conftest.TEST_DB_NAME!r} 相同，并发会话会互相清库"
        )


def test_session_db_name_follows_the_environment_override():
    """显式给了 LUYUN_TEST_DSN 就照用；没给才按 PID 派生。"""
    explicit = os.environ.get("LUYUN_TEST_DSN")
    if explicit:
        assert conftest._TEST_DB_NAME == urlparse(explicit).path.lstrip("/")
    else:
        assert conftest._TEST_DB_NAME == conftest._default_test_db_name()


_PROBE = "import conftest, sys; sys.stdout.write(conftest._TEST_DB_NAME)"


def _spawn_session_probe():
    """起一个「新会话」：只 import conftest（不跑 pytest、不建库），打印它拿到的库名。"""
    env = {**os.environ}
    env.pop("LUYUN_TEST_DSN", None)  # 两个会话都不显式指定
    return subprocess.Popen(
        [sys.executable, "-c", _PROBE],
        cwd=str(TESTS_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_two_concurrent_sessions_get_different_databases():
    """两个子进程同时起、都不设 LUYUN_TEST_DSN → 派生出的库名必须不同。"""
    first = _spawn_session_probe()
    second = _spawn_session_probe()
    out_first, err_first = first.communicate(timeout=120)
    out_second, err_second = second.communicate(timeout=120)

    assert first.returncode == 0, err_first
    assert second.returncode == 0, err_second

    name_first, name_second = out_first.strip(), out_second.strip()
    assert name_first and name_second, (out_first, out_second)
    assert name_first != name_second, (
        f"两个并发会话拿到了同一个测试库 {name_first!r}——它们会互相 TRUNCATE / DROP SCHEMA"
    )
    for name in (name_first, name_second):
        assert conftest.TEST_DB_NAME_RE.match(name), f"{name!r} 不在 TEST_DB_NAME_RE 放行的形态里"


def test_v3_session_dsn_name_and_default_are_wired_consistently():
    """独立验证（verifier-db / V3）：本会话真在用的 DSN 库名 = `_TEST_DB_NAME`，且绝不落回共享名。"""
    from urllib.parse import urlparse as _urlparse

    assert _urlparse(conftest._TEST_DSN).path.lstrip("/") == conftest._TEST_DB_NAME, (
        "conftest 报出来的库名与它真正钉进 POSTGRES_DSN 的库名不一致"
    )
    assert conftest.TEST_DB_NAME_RE.match(conftest._TEST_DB_NAME), conftest._TEST_DB_NAME
    if not os.environ.get("LUYUN_TEST_DSN"):
        assert conftest._TEST_DB_NAME != conftest.TEST_DB_NAME, (
            "未设 LUYUN_TEST_DSN 时库名仍是共享的 luyun_test——并发会话会互相清库"
        )
        assert conftest._TEST_DB_NAME == conftest._default_test_db_name()
    else:
        assert conftest._TEST_DB_NAME != conftest.TEST_DB_NAME or (
            os.environ["LUYUN_TEST_DSN"].rstrip("/").endswith(conftest.TEST_DB_NAME)
        )
