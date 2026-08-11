#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve Update Job deploy mode: systemd host vs Docker (git bind-mount)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Literal

DeployMode = Literal["systemd", "docker"]

_MODE_ENV = "LUYUN_DEPLOY_MODE"
_CONTAINER_ENV = "LUYUN_DOCKER_CONTAINER"
_SOCK_ENV = "LUYUN_DOCKER_SOCK"
_DEFAULT_SOCK = "/var/run/docker.sock"


def docker_sock_path() -> Path:
    raw = (os.environ.get(_SOCK_ENV) or _DEFAULT_SOCK).strip()
    return Path(raw or _DEFAULT_SOCK)


def docker_container_name() -> str:
    return (os.environ.get(_CONTAINER_ENV) or "").strip()


def in_docker_runtime() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "docker" in cgroup or "containerd" in cgroup


def resolve_deploy_mode() -> DeployMode:
    """Return deploy mode.

    ``LUYUN_DEPLOY_MODE=docker|systemd`` forces the choice. ``auto`` (default):
    Docker when ``/.dockerenv`` (or cgroup) is present, else systemd when
    ``systemctl`` exists, else Docker if the socket + container name are set.
    """
    raw = (os.environ.get(_MODE_ENV) or "auto").strip().lower()
    if raw in ("docker", "systemd"):
        return raw  # type: ignore[return-value]
    if raw not in ("", "auto"):
        raise ValueError(
            f"Invalid {_MODE_ENV}={raw!r}; expected auto, docker, or systemd"
        )

    if in_docker_runtime():
        return "docker"
    if shutil.which("systemctl"):
        return "systemd"
    if docker_sock_path().exists() and docker_container_name():
        return "docker"
    # Prefer an actionable Docker hint when sock is mounted without systemctl.
    if docker_sock_path().exists():
        return "docker"
    return "systemd"


def public_deploy_status() -> dict:
    """Safe fields for Admin / diagnostics (no secrets)."""
    mode = resolve_deploy_mode()
    sock = docker_sock_path()
    return {
        "deploy_mode": mode,
        "docker_container": docker_container_name() or None,
        "docker_sock": str(sock),
        "docker_sock_present": sock.exists(),
        "in_docker_runtime": in_docker_runtime(),
    }
