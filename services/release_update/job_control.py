#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop a running Update Job (systemd oneshot or pidfile process group)."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
from typing import Protocol, Sequence

from services.release_update.deploy_mode import resolve_deploy_mode
from services.release_update.job_state import read_job_pid
from services.release_update.oneshot import DEFAULT_UPDATE_UNIT

logger = logging.getLogger(__name__)


class JobStopperPort(Protocol):
    def stop(self) -> None: ...


def _kill_pid(pid: int) -> None:
    """Best-effort terminate of the Update Job process (and its group when leader)."""
    try:
        os.killpg(pid, signal.SIGTERM)
        return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        logger.info("Update Job pid=%s already gone or not killable: %s", pid, exc)


class PidfileJobStopper:
    """Stop via data/update_job.pid (Docker detached / fallback)."""

    def stop(self) -> None:
        pid = read_job_pid()
        if pid is None:
            logger.info("No Update Job pidfile; nothing to kill")
            return
        logger.info("Stopping Update Job via pidfile pid=%s", pid)
        _kill_pid(pid)


class SystemdJobStopper:
    """systemctl stop the oneshot unit, then pidfile fallback."""

    def __init__(
        self,
        unit: str = DEFAULT_UPDATE_UNIT,
        *,
        systemctl_bin: str = "systemctl",
    ) -> None:
        self._unit = unit
        self._systemctl = systemctl_bin
        self._pidfile = PidfileJobStopper()

    def stop(self) -> None:
        override = (os.environ.get("LUYUN_UPDATE_JOB_STOP_CMD") or "").strip()
        if override:
            cmd: Sequence[str] = ["bash", "-lc", override]
        else:
            cmd = [self._systemctl, "stop", "--no-block", self._unit]
        try:
            completed = subprocess.run(
                list(cmd),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("systemctl stop Update Job failed: %s", exc)
        else:
            if completed.returncode != 0:
                err = (completed.stderr or completed.stdout or "").strip()
                logger.warning(
                    "systemctl stop %s rc=%s err=%s",
                    self._unit,
                    completed.returncode,
                    err,
                )
            else:
                logger.info("Requested stop of Update Job unit=%s", self._unit)
        # Oneshot may already have exited; pidfile covers Docker-like leftovers.
        self._pidfile.stop()


def build_job_stopper() -> JobStopperPort:
    mode = resolve_deploy_mode()
    if mode == "docker":
        return PidfileJobStopper()
    return SystemdJobStopper()
