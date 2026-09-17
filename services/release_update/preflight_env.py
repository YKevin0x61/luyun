#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Environment adapter for Update Preflight (restart · credentials · dirty tree)."""

from __future__ import annotations

import os
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

        SQLite：只需确认数据目录还在（更新不替换库文件）。
        PostgreSQL：用 pg_isready 探测。与磁盘同一原则——探测工具缺失或超时
        判为可用，不让探测本身的缺陷反过来挡住更新。
        """
        backend = (getattr(settings, "DATABASE_BACKEND", "sqlite") or "sqlite").lower()
        if backend != "postgres":
            return True, f"SQLite（{getattr(settings, 'DATABASE_DIR', 'data')}）"

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
            return True, "PostgreSQL 可访问"
        return False, "PostgreSQL 不可访问（检查数据库服务与 POSTGRES_DSN）"

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
