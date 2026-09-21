#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release Update Admin API (Version Check · Apply Update · job status)."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.security import require_session, verify_admin_token
from database import DatabaseManager, get_db
from services import runtime_settings
from services import github_release_config
from services.release_update import (
    REASON_BUSY,
    REASON_DIRTY_TREE,
    REASON_INVALID_TARGET,
    REASON_NOT_RUNNING,
    REASON_PEAK_HOURS,
    REASON_PREFLIGHT,
    ApplyResult,
    CancelResult,
    ReleaseUpdate,
    UpdateJobState,
    UpdatePreflight,
    VersionCheckResult,
)
from services.release_update.job_state import read_log_tail
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


def _version_check_payload(
    result: VersionCheckResult,
    migrations: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from services.release_update.deploy_mode import public_deploy_status

    return {
        "success": True,
        "installed_tag": result.installed_tag,
        "degraded": result.degraded,
        "degraded_reason": result.degraded_reason,
        "app_version": result.app_version,
        "latest_tag": result.latest_tag,
        "update_available": result.update_available,
        "catalogue_ok": result.catalogue_ok,
        "releases": [asdict(r) for r in result.releases],
        "preflight": _preflight_payload(result.preflight),
        # 顺带告知待应用的数据库迁移。**不放进 preflight**：那是「不通过就不许更新」
        # 的硬门槛，把迁移塞进去会把「忘了点迁移」升级成「不能发版」，更难解。
        "pending_migrations": migrations
        or {"supported": False, "count": 0, "versions": [], "filenames": [], "note": ""},
        **public_deploy_status(),
    }


async def _migration_hint(db: DatabaseManager) -> dict[str, Any]:
    """版本检测顺带读一次迁移状态。

    数据库读不出来不该让版本检测整体失败（后者才是这个接口的主职），所以全程兜底。
    """
    from services.db_migrations import migration_status

    try:
        status = await migration_status(db)
    except Exception as exc:
        logger.warning("读取数据库迁移状态失败: %s", exc)
        return {
            "supported": False,
            "count": 0,
            "versions": [],
            "filenames": [],
            "note": "",
            "error": str(exc),
        }
    data = status.as_dict()
    return {
        "supported": data["supported"],
        "count": len(data["pending"]),
        "versions": [item["version"] for item in data["pending"]],
        "filenames": [item["filename"] for item in data["pending"]],
        "note": data["note"],
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
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    """Read-only Version Check — does not start an Update Job."""
    # version_check 内部是同步 httpx 调用（最长约 15s×2）；单 worker 部署下在事件
    # 循环里直接跑会把 /healthz、/ws/realtime 和采集接口一起卡住，所以丢线程池。
    try:
        result = await run_in_threadpool(release_update.version_check)
    except GitHubReleasesError as exc:
        logger.warning("Version Check GitHub failure: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Version Check failed: %s", exc)
        raise HTTPException(status_code=500, detail="版本检测失败") from exc
    return _version_check_payload(result, await _migration_hint(db))


@router.get("/job")
async def job_status(
    release_update: ReleaseUpdate = Depends(get_release_update),
) -> dict[str, Any]:
    """Poll Update Job state; complete the health-confirmation half of success.

    The job itself only switches the Release Bundle and asks for a restart. Each
    poll re-checks readiness so the job lands on ``succeeded`` /
    ``succeeded_but_unhealthy``, never on "the process came back" alone.
    """
    await release_update.confirm_health()
    job = release_update.job_status()
    return {
        "success": True,
        "job": _job_payload(job),
        "log_tail": read_log_tail(job.log_path),
    }


@router.post("/job/health-check")
async def recheck_job_health(
    release_update: ReleaseUpdate = Depends(get_release_update),
    _session_id: str = Depends(require_session),
) -> dict[str, Any]:
    """Manual readiness re-check for a job waiting on health confirmation."""
    job = await release_update.confirm_health()
    return {
        "success": True,
        "job": _job_payload(job),
        "log_tail": read_log_tail(job.log_path),
    }


@router.get("/history")
async def update_history(
    release_update: ReleaseUpdate = Depends(get_release_update),
) -> dict[str, Any]:
    """Recent Update History (target / previous / result / rollback / duration)."""
    return {"success": True, "entries": release_update.history()}


@router.post("/job/cancel")
async def cancel_update_job(
    release_update: ReleaseUpdate = Depends(get_release_update),
    _session_id: str = Depends(require_session),
) -> dict[str, Any]:
    """Request abort of an in-progress Update Job (session-hardened)."""
    # cancel 会同步等待作业退出（最多数秒）并同步跑 systemctl stop；
    # 放在事件循环里会让整个主服务在这段时间不响应任何请求。
    try:
        result = await run_in_threadpool(release_update.cancel)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Cancel Update Job failed: %s", exc)
        raise HTTPException(status_code=500, detail="终止更新作业失败") from exc
    return _cancel_response(result)


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
        # apply 内部要读 GitHub 正式发行目录（同步 httpx），同样不能占用事件循环。
        result = await run_in_threadpool(
            release_update.apply,
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


def _cancel_response(result: CancelResult) -> dict[str, Any]:
    if result.accepted:
        job = result.job
        return {
            "success": True,
            "accepted": True,
            "forced": bool(result.forced),
            "job": _job_payload(job) if job else None,
            "log_tail": read_log_tail(job.log_path if job else None),
        }
    reason = result.reason or "rejected"
    if reason == REASON_NOT_RUNNING:
        raise HTTPException(
            status_code=409,
            detail="当前没有进行中的更新作业",
        )
    raise HTTPException(status_code=400, detail=f"无法终止更新作业: {reason}")


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
