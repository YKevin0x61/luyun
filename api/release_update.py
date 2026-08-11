#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release Update Admin API (Version Check · Apply Update · job status)."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.security import require_session, verify_admin_token
from database import DatabaseManager, get_db
from services import runtime_settings
from services import github_release_config
from services.release_update import (
    REASON_BUSY,
    REASON_DIRTY_TREE,
    REASON_INVALID_TARGET,
    REASON_PEAK_HOURS,
    REASON_PREFLIGHT,
    ApplyResult,
    ReleaseUpdate,
    UpdateJobState,
    UpdatePreflight,
    VersionCheckResult,
)
from services.release_update.factory import build_release_update
from services.release_update.github_releases import GitHubReleasesError
from services.release_update.peak_hours import BusinessHoursPeakAdapter

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/release-update",
    tags=["release-update"],
    dependencies=[Depends(verify_admin_token)],
)


class ApplyUpdateIn(BaseModel):
    target_tag: str = Field(..., min_length=1)
    peak_override: bool = False
    discard_local_changes: bool = False


class GitHubConfigIn(BaseModel):
    token: Optional[str] = Field(
        default=None,
        description="New PAT; omit or blank to keep the existing token",
    )
    clear_token: bool = False


async def get_release_update(
    db: DatabaseManager = Depends(get_db),
) -> ReleaseUpdate:
    """Request-time factory (overridable in tests); peak hours from runtime settings."""
    runtime = await runtime_settings.load_runtime_settings(db)
    peak = BusinessHoursPeakAdapter(
        work_start=runtime["work_start"],
        work_end=runtime["work_end"],
    )
    return build_release_update(peak_hours=peak)


def _preflight_payload(preflight: UpdatePreflight) -> dict[str, Any]:
    return asdict(preflight)


def _version_check_payload(result: VersionCheckResult) -> dict[str, Any]:
    from services.release_update.deploy_mode import public_deploy_status

    return {
        "success": True,
        "installed_tag": result.installed_tag,
        "degraded": result.degraded,
        "degraded_reason": result.degraded_reason,
        "app_version": result.app_version,
        "latest_tag": result.latest_tag,
        "update_available": result.update_available,
        "releases": [asdict(r) for r in result.releases],
        "preflight": _preflight_payload(result.preflight),
        **public_deploy_status(),
    }


def _job_payload(job: UpdateJobState) -> dict[str, Any]:
    return asdict(job)


@router.get("/github-config")
async def get_github_config() -> dict[str, Any]:
    """Return GitHub Release settings for the Setup UI (token never returned)."""
    status = github_release_config.public_status()
    return {"success": True, **status}


@router.put("/github-config")
async def put_github_config(
    payload: GitHubConfigIn,
    _session_id: str = Depends(require_session),
) -> dict[str, Any]:
    """Save GitHub PAT for Version Check / Update Job (no process restart)."""
    try:
        github_release_config.save_config(
            token=payload.token,
            clear_token=payload.clear_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Save GitHub Release config failed: %s", exc)
        raise HTTPException(status_code=500, detail="保存 GitHub 配置失败") from exc
    return {"success": True, **github_release_config.public_status()}


@router.get("/version-check")
async def version_check(
    release_update: ReleaseUpdate = Depends(get_release_update),
) -> dict[str, Any]:
    """Read-only Version Check — does not start an Update Job."""
    try:
        result = release_update.version_check()
    except GitHubReleasesError as exc:
        logger.warning("Version Check GitHub failure: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Version Check failed: %s", exc)
        raise HTTPException(status_code=500, detail="版本检测失败") from exc
    return _version_check_payload(result)


@router.get("/job")
async def job_status(
    release_update: ReleaseUpdate = Depends(get_release_update),
) -> dict[str, Any]:
    """Poll Update Job state from the data/ state file (+ log pointer)."""
    job = release_update.job_status()
    return {"success": True, "job": _job_payload(job)}


@router.post("/apply")
async def apply_update(
    payload: ApplyUpdateIn,
    release_update: ReleaseUpdate = Depends(get_release_update),
    _session_id: str = Depends(require_session),
) -> dict[str, Any]:
    """Record Apply Update intent and start the Update Job (systemd or Docker).

    Session-required mutate (same hardening pattern as backup import).
    """
    try:
        result = release_update.apply(
            payload.target_tag,
            peak_override=payload.peak_override,
            discard_local_changes=payload.discard_local_changes,
        )
    except RuntimeError as exc:
        logger.error("Apply Update failed to start oneshot: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Apply Update failed: %s", exc)
        raise HTTPException(status_code=500, detail="应用更新启动失败") from exc

    return _apply_response(result)


def _apply_response(result: ApplyResult) -> dict[str, Any]:
    if result.accepted:
        return {
            "success": True,
            "accepted": True,
            "job": _job_payload(result.job) if result.job else None,
        }

    reason = result.reason or "rejected"
    if reason == REASON_BUSY:
        raise HTTPException(
            status_code=409,
            detail="已有更新作业进行中，请稍后再试",
        )
    if reason == REASON_PEAK_HOURS:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": REASON_PEAK_HOURS,
                "message": "当前处于营业高峰时段，确认后请勾选覆盖再试",
            },
        )
    if reason == REASON_PREFLIGHT:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": REASON_PREFLIGHT,
                "message": "更新环境自检未通过，当前不是健康的运行实例，无法应用更新",
            },
        )
    if reason == REASON_DIRTY_TREE:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": REASON_DIRTY_TREE,
                "message": "部署目录有本地改动；请确认丢弃本地改动后再应用更新",
            },
        )
    if reason == REASON_INVALID_TARGET:
        raise HTTPException(
            status_code=400,
            detail="目标发行版无效或不在正式目录中",
        )
    raise HTTPException(status_code=400, detail=f"无法启动应用更新: {reason}")
