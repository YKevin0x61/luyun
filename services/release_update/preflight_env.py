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

from config import settings
from services.disk_guard import min_free_mb
from services.github_release_config import get_effective_config
from services.release_update import PreflightEnv
from services.release_update.deploy_mode import (
    docker_container_name,
    docker_sock_path,
    resolve_deploy_mode,
)


class DefaultPreflightEnvAdapter:
    """Inspect Runtime Instance readiness for Version Check / Apply gates."""

    def __init__(self, deploy_dir: Path) -> None:
        self._deploy_dir = Path(deploy_dir)

    def inspect_env(self) -> PreflightEnv:
        disk_ok, disk_free_mb = self._disk_state()
        database_ok, database_detail = self._database_state()
        return PreflightEnv(
            restart_ready=self._restart_ready(),
            credentials_ready=self._credentials_ready(),
            dirty_tree=self._deploy_tree_dirty(),
            disk_ok=disk_ok,
            disk_free_mb=disk_free_mb,
            database_ok=database_ok,
            database_detail=database_detail,
        )

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
        try:
            completed = subprocess.run(
                ["pg_isready", "-d", dsn, "-q"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
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
        """用 psql 读服务端版本；psql 本身不做版本检查，旧客户端也能连新服务端。"""
        psql = shutil.which("psql")
        if not psql:
            return None
        try:
            completed = subprocess.run(
                [psql, "-d", dsn, "-tAc", "SHOW server_version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
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
