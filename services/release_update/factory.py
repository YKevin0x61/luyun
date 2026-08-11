#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the default ReleaseUpdate wired to config + real adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from config import settings
from services.github_release_config import get_effective_config
from services.release_update import PeakHoursPort, ReleaseUpdate
from services.release_update.github_releases import GitHubReleasesAdapter
from services.release_update.job_control import build_job_stopper
from services.release_update.job_state import FileJobStateStore
from services.release_update.manifest_identity import ReleaseManifestAdapter
from services.release_update.oneshot import build_oneshot_starter
from services.release_update.peak_hours import BusinessHoursPeakAdapter
from services.release_update.preflight_env import DefaultPreflightEnvAdapter


def default_deploy_dir() -> Path:
    configured = (settings.RELEASE_UPDATE_REPO_DIR or "").strip()
    if configured:
        return Path(configured)
    # Project root (services/release_update/factory.py → ../../..)
    return Path(__file__).resolve().parents[2]


# Back-compat alias for callers that still import the old name.
default_repo_dir = default_deploy_dir


def build_release_update(
    *,
    peak_hours: Optional[PeakHoursPort] = None,
) -> ReleaseUpdate:
    gh = get_effective_config()
    deploy_dir = default_deploy_dir()
    return ReleaseUpdate(
        installed=ReleaseManifestAdapter(deploy_dir),
        github=GitHubReleasesAdapter(
            repo=gh.repo or "",
            token=gh.token,
        ),
        app_version=settings.APP_VERSION,
        job_store=FileJobStateStore(),
        # systemd oneshot vs Docker detached — follows LUYUN_DEPLOY_MODE / runtime.
        oneshot=build_oneshot_starter(),
        job_stopper=build_job_stopper(),
        peak_hours=peak_hours or BusinessHoursPeakAdapter(),
        preflight_env=DefaultPreflightEnvAdapter(deploy_dir),
    )
