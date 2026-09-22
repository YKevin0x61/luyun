#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Environment adapter for Update Preflight (restart · credentials · dirty tree)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

from config import settings
from services.disk_guard import min_free_mb
from services.github_release_config import get_effective_config
from services.release_update import PreflightEnv
from services.release_update.deploy_mode import (
    docker_container_name,
    docker_sock_path,
    resolve_deploy_mode,
)

# 预检探测是**子进程**（与"不引入 Python 端连接"的既有取舍一致），所以口令只能走
# 环境变量：argv 里的字符串对同机其它用户可见（`ps -ef` / `/proc/<pid>/cmdline`），
# 而 `POSTGRES_DSN`/`REDIS_URL` 按惯例可以写成 `scheme://user:password@host/db`。
# 环境变量对同机 root 同样可读，但门槛高一个数量级——这也正是 psql/redis-cli 自己
# 推荐的做法（`PGPASSWORD` / `REDISCLI_AUTH`）。
_LIBPQ_KEYWORD_DSN = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>'(?:[^'\\]|\\.)*'|\S*)"
)


def _unescape_libpq_value(value: str) -> str:
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        value = value[1:-1]
    return re.sub(r"\\(.)", r"\1", value)


def _libpq_keyword_fields(value: str) -> dict:
    """解析 libpq keyword/value DSN（`host=… port=… password=…`）。

    只有**不含 `://`** 的值才走这里：URL 形态交给 ``urlsplit``，两者语法不同。
    """
    if "://" in value:
        return {}
    return {
        match.group("key").lower(): _unescape_libpq_value(match.group("value"))
        for match in _LIBPQ_KEYWORD_DSN.finditer(value)
    }


def _pg_client_probe(dsn: str) -> Optional[tuple[list[str], dict[str, str]]]:
    """把 DSN 拆成 ``(argv, env)``：口令进 ``PGPASSWORD``，argv 里只有连接要素。

    返回 ``None`` 表示解析不出主机——调用方按「跳过探测」处理，**不要**退回
    `-d <dsn>`：那正是本条要消除的暴露面。
    """
    fields = _libpq_keyword_fields(dsn)
    if fields:
        host, port = fields.get("host"), fields.get("port")
        user, dbname = fields.get("user"), fields.get("dbname") or fields.get("database")
        password = fields.get("password")
    else:
        try:
            parsed = urlsplit(dsn)
        except ValueError:
            return None
        host, port = parsed.hostname, parsed.port
        user = unquote(parsed.username) if parsed.username else None
        password = unquote(parsed.password) if parsed.password else None
        dbname = unquote(parsed.path[1:]) if parsed.path.startswith("/") else None

    if not host:
        return None

    argv: list[str] = ["-h", host]
    if port:
        argv += ["-p", str(port)]
    if user:
        argv += ["-U", user]
    # 库名要显式给出，否则探测的是与该用户名同名的默认库，`pg_isready -q` 照样回 0，
    # 「库不存在」会变成假绿灯。
    if dbname:
        argv += ["-d", dbname]
    env = dict(os.environ)
    if password:
        env["PGPASSWORD"] = password
    return argv, env


def _redis_client_probe(url: str) -> Optional[tuple[list[str], dict[str, str]]]:
    """把 URL 拆成 ``(argv, env)``：口令进 ``REDISCLI_AUTH``，argv 里只有主机/端口。

    ``None`` 表示解析不出主机（同上：不退回 ``-u <url>``）。
    """
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    host = parsed.hostname
    if not host:
        return None

    argv = ["-h", host]
    if parsed.port:
        argv += ["-p", str(parsed.port)]
    env = dict(os.environ)
    if parsed.password:
        env["REDISCLI_AUTH"] = unquote(parsed.password)
    return argv, env


class DefaultPreflightEnvAdapter:
    """Inspect Runtime Instance readiness for Version Check / Apply gates."""

    def __init__(self, deploy_dir: Path) -> None:
        self._deploy_dir = Path(deploy_dir)

    def inspect_env(self) -> PreflightEnv:
        disk_ok, disk_free_mb = self._disk_state()
        database_ok, database_detail = self._database_state()
        redis_configured, redis_reachable, redis_detail = self._redis_state()
        return PreflightEnv(
            restart_ready=self._restart_ready(),
            credentials_ready=self._credentials_ready(),
            dirty_tree=self._deploy_tree_dirty(),
            disk_ok=disk_ok,
            disk_free_mb=disk_free_mb,
            database_ok=database_ok,
            database_detail=database_detail,
            redis_configured=redis_configured,
            redis_reachable=redis_reachable,
            redis_detail=redis_detail,
        )

    def _redis_state(self) -> tuple[bool, Optional[bool], Optional[str]]:
        """Redis 可达性，返回 ``(已配置, 是否可达, 说明)``。

        Redis 是部署必需组件（realtime nudge 的跨进程广播，ADR 0090）：没配
        `REDIS_URL` 时应用**启动即失败**，所以那种情况回 `(False, None, ...)`
        ——由 `_build_preflight` 当硬门禁拦下更新。配了但探测不通回
        `(True, False, ...)`，只提示不拦：应用会退避重连，一次抖动不该挡住更新。

        探测走 `redis-cli -u <url> ping`（与 `pg_isready` 同一路数，不引入 Python
        端连接）。`redis-cli` 缺失按「跳过探测」放过——探测工具本身的缺陷不该反过来
        挡住更新，与 pg_isready 一致；但**没配 REDIS_URL 不同**，那是配置问题。
        说明里刻意不回显 URL：它可能带密码。
        """
        url = (os.environ.get("LUYUN_REDIS_URL") or getattr(settings, "REDIS_URL", "") or "").strip()
        if not url:
            return False, None, (
                "REDIS_URL 未配置：Redis 是部署必需组件（realtime nudge 跨进程广播），"
                "更新后应用将无法启动；请先安装 Redis 并在 env.production 配置 REDIS_URL"
            )
        if not shutil.which("redis-cli"):
            return True, None, "Redis 已配置（未安装 redis-cli，跳过探测）"
        probe = _redis_client_probe(url)
        if probe is None:
            return True, None, "Redis 已配置（URL 解析不出主机，跳过探测）"
        argv, env = probe
        try:
            completed = subprocess.run(
                ["redis-cli", *argv, "ping"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired):
            return True, None, "Redis 已配置（探测超时，跳过）"
        if completed.returncode == 0 and "PONG" in (completed.stdout or "").upper():
            return True, True, "Redis 可访问（跨进程 nudge 广播可用）"
        return True, False, "Redis 已配置但不可访问（检查 Redis 服务；应用仍能启动并在后台重连）"

    def _database_state(self) -> tuple[bool, Optional[str]]:
        """业务库可达性。

        后端只剩 PostgreSQL（SQLite 已在 ADR 0089 退场）：``DATABASE_BACKEND``
        不是 postgres 就直接判红，别让老部署在「新代码 + 老库」的错配下把更新
        点下去。随后用 pg_isready 探测。与磁盘同一原则——探测工具缺失或超时
        判为可用，不让探测本身的缺陷反过来挡住更新。
        """
        backend = (getattr(settings, "DATABASE_BACKEND", "") or "").strip().lower()
        if backend != "postgres":
            return False, (
                f"DATABASE_BACKEND={backend or '(空)'}：SQLite 后端已移除，"
                "请先迁移到 PostgreSQL（deploy/enable_postgres.sh）再更新"
            )

        dsn = os.environ.get("LUYUN_POSTGRES_DSN") or getattr(settings, "POSTGRES_DSN", "")
        if not dsn:
            return False, "DATABASE_BACKEND=postgres，但 POSTGRES_DSN 未配置"
        if not shutil.which("pg_isready"):
            return True, "PostgreSQL（未安装 pg_isready，跳过探测）"
        probe = _pg_client_probe(dsn)
        if probe is None:
            return True, "PostgreSQL（DSN 解析不出主机，跳过探测）"
        argv, env = probe
        try:
            completed = subprocess.run(
                ["pg_isready", *argv, "-q"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired):
            return True, "PostgreSQL（探测超时，跳过）"
        if completed.returncode == 0:
            return self._pg_dump_state(dsn)
        return False, "PostgreSQL 不可访问（检查数据库服务与 POSTGRES_DSN）"

    def _pg_dump_state(self, dsn: str) -> tuple[bool, Optional[str]]:
        """更新前备份在 PG 后端下靠 pg_dump，而 pg_dump 拒绝 dump 比它新的服务端。

        pg_dump 15 对上服务端 16 会直接 `aborting because of server version
        mismatch` —— 现场更新作业就是这样卡在 backing_up 的。镜像里的客户端来自
        基础镜像的 apt 源，很容易落后于 compose 起的 postgres 大版本，所以这里提前
        红灯，而不是等更新走到一半失败。

        注意与 pg_isready 的差别：pg_dump **缺失**同样意味着备份必失败，因此判红，
        不能套用「探测工具缺失就放过」的原则。
        """
        exe = shutil.which("pg_dump")
        if not exe:
            return False, "未找到 pg_dump（PostgreSQL 后端需要 postgresql-client）"
        client = self._tool_major_version(exe)
        server = self._server_major_version(dsn)
        if client is None or server is None:
            return True, f"PostgreSQL 可访问（pg_dump {client or '?'}，未能比对服务端版本）"
        if client < server:
            return False, (
                f"pg_dump {client} 低于服务端 {server}：更新前备份会失败，"
                "请重建镜像（镜像里的 postgresql-client 需与服务端同大版本）"
            )
        return True, f"PostgreSQL 可访问（pg_dump {client} ≥ 服务端 {server}）"

    @staticmethod
    def _tool_major_version(exe: str) -> Optional[int]:
        try:
            completed = subprocess.run(
                [exe, "--version"], capture_output=True, text=True, timeout=10, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        match = re.search(r"(\d+)\.", completed.stdout or "")
        return int(match.group(1)) if match else None

    @staticmethod
    def _server_major_version(dsn: str) -> Optional[int]:
        """用 psql 读服务端版本；psql 本身不做版本检查，旧客户端也能连新服务端。

        连接要素同样走 `_pg_client_probe`（口令进 `PGPASSWORD`，不进 argv）。
        DSN 解析不出主机时直接放弃比对，与「工具缺失」同一处理。
        """
        psql = shutil.which("psql")
        if not psql:
            return None
        probe = _pg_client_probe(dsn)
        if probe is None:
            return None
        argv, env = probe
        try:
            completed = subprocess.run(
                [psql, *argv, "-tAc", "SHOW server_version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        match = re.search(r"(\d+)", completed.stdout or "")
        return int(match.group(1)) if match else None

    def _disk_state(self) -> tuple[bool, Optional[float]]:
        """更新会跑 pip sync 写 .venv，必须先确认还有空间。

        读不到用量时判为可用：探测失败（权限、异常路径）不该反过来挡住更新。
        """
        free_mb = min_free_mb()
        if free_mb is None:
            return True, None
        return free_mb >= settings.UPDATE_MIN_FREE_MB, free_mb

    def _restart_ready(self) -> bool:
        mode = resolve_deploy_mode()
        if mode == "docker":
            return bool(docker_sock_path().exists() and docker_container_name())
        return bool(shutil.which("systemctl"))

    def _credentials_ready(self) -> bool:
        # Public repo: repo alone is enough for anonymous Releases access.
        # Optional PAT (env / Admin) only raises API rate limits.
        cfg = get_effective_config()
        return bool((cfg.repo or "").strip())

    def _deploy_tree_dirty(self) -> bool:
        """True when a git worktree exists and is dirty or cannot be proven clean.

        Bundle-only Runtime Instances without ``.git`` are treated as clean;
        Apply Update will replace the tree from the Release Bundle.
        """
        git_dir = self._deploy_dir / ".git"
        if not git_dir.exists():
            return False
        try:
            completed = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self._deploy_dir),
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            # Fail closed: cannot prove the tree is clean.
            return True
        if completed.returncode != 0:
            return True
        return bool((completed.stdout or "").strip())
