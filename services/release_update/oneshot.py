#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Start the Update Job outside the web request (systemd oneshot or Docker).

Both starters ultimately run ``scripts/run_update_job.py`` out of process so the
uvicorn worker never replaces its own application tree mid-request.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from services.release_update.deploy_mode import resolve_deploy_mode
from services.release_update.job_state import job_log_path

logger = logging.getLogger(__name__)

DEFAULT_UPDATE_UNIT = "luyun-update.service"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _update_job_script() -> Path:
    return _project_root() / "scripts" / "run_update_job.py"


class SystemdOneshotStarter:
    """OneshotStarterPort backed by ``systemctl start --no-block``."""

    def __init__(
        self,
        unit: str = DEFAULT_UPDATE_UNIT,
        *,
        systemctl_bin: str = "systemctl",
    ) -> None:
        self._unit = unit
        self._systemctl = systemctl_bin

    def start(self) -> None:
        direct = (os.environ.get("LUYUN_UPDATE_JOB_CMD") or "").strip()
        if direct:
            cmd: Sequence[str] = ["bash", "-lc", direct]
        else:
            # --no-block: return immediately; Admin polls data/update_job.json.
            cmd = [self._systemctl, "start", "--no-block", self._unit]
        try:
            completed = subprocess.run(
                list(cmd),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"failed to start Update Job: {exc}") from exc
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(
                f"failed to start Update Job ({self._unit}): {err or completed.returncode}"
            )
        logger.info("Started Update Job oneshot unit=%s", self._unit)


class DockerDetachedJobStarter:
    """Spawn ``scripts/run_update_job.py`` in a new session (Docker / bind-mount)."""

    def __init__(self, *, python_bin: Optional[str] = None) -> None:
        self._python = python_bin or sys.executable

    def start(self) -> None:
        from services.release_update.deploy_mode import (
            docker_container_name,
            docker_sock_path,
        )

        # Fail fast with an actionable message before detaching the job.
        if not docker_container_name():
            raise RuntimeError(
                "Docker 部署需设置环境变量 LUYUN_DOCKER_CONTAINER（容器名，如 luyun-order）"
            )
        sock = docker_sock_path()
        if not sock.exists():
            raise RuntimeError(
                f"未找到 Docker socket（{sock}）。请在 1Panel/Compose 中把宿主机 "
                "/var/run/docker.sock 挂载进容器，并设置 LUYUN_DEPLOY_MODE=docker"
            )

        direct = (os.environ.get("LUYUN_UPDATE_JOB_CMD") or "").strip()
        log_file = job_log_path()
        log_file.parent.mkdir(parents=True, exist_ok=True)

        if direct:
            cmd: Sequence[str] = ["bash", "-lc", direct]
            cwd = str(_project_root())
        else:
            script = _update_job_script()
            if not script.is_file():
                raise RuntimeError(f"Update Job script missing: {script}")
            cmd = [self._python, str(script)]
            cwd = str(_project_root())

        try:
            # Detach so Apply Update returns while the job runs; final step
            # restarts this container via docker.sock.
            with open(log_file, "a", encoding="utf-8") as log_fh:
                log_fh.write("\n--- detached Update Job start ---\n")
                log_fh.flush()
                proc = subprocess.Popen(
                    list(cmd),
                    cwd=cwd,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
        except OSError as exc:
            raise RuntimeError(f"failed to start Update Job: {exc}") from exc
        logger.info("Started detached Update Job pid=%s cmd=%s", proc.pid, cmd)


def build_oneshot_starter():
    """Pick systemd or Docker starter from ``LUYUN_DEPLOY_MODE`` / runtime."""
    mode = resolve_deploy_mode()
    if mode == "docker":
        return DockerDetachedJobStarter()
    return SystemdOneshotStarter()
