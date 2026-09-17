#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release Update seam: Version Check · Apply Update intent · Update Job state."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import List, Optional, Protocol, Sequence, Tuple
import time

from database import CHINA_TZ

logger = logging.getLogger(__name__)

# Stages written by Apply Update / Update Job (stable for Admin UI polling).
# Bundle path (ADR 0011): queued → backing_up → fetching_bundle → installing
# → syncing_deps → restarting → succeeded|failed
STAGE_IDLE = "idle"
STAGE_QUEUED = "queued"
STAGE_BACKING_UP = "backing_up"
STAGE_FETCHING_BUNDLE = "fetching_bundle"
STAGE_INSTALLING = "installing"
STAGE_SYNCING_DEPS = "syncing_deps"
STAGE_RESTARTING = "restarting"
STAGE_SUCCEEDED = "succeeded"
STAGE_SUCCEEDED_BUT_UNHEALTHY = "succeeded_but_unhealthy"
STAGE_FAILED = "failed"

# Legacy git/split-asset stage names (ADR 0010) — still recognized as in-progress
# if an older state file is mid-job during cutover.
STAGE_FETCHING = "fetching"
STAGE_INSTALLING_ASSETS = "installing_assets"

# Job is switched over but the restarted service has not confirmed readiness yet.
IN_PROGRESS_STAGES = frozenset(
    {
        STAGE_QUEUED,
        STAGE_BACKING_UP,
        STAGE_FETCHING_BUNDLE,
        STAGE_INSTALLING,
        STAGE_SYNCING_DEPS,
        STAGE_RESTARTING,
        STAGE_FETCHING,
        STAGE_INSTALLING_ASSETS,
    }
)

TERMINAL_STAGES = frozenset(
    {STAGE_SUCCEEDED, STAGE_SUCCEEDED_BUT_UNHEALTHY, STAGE_FAILED}
)

# How long the Admin keeps confirming readiness before declaring the new
# Release switched-but-unhealthy.
HEALTH_CONFIRM_GRACE_SECONDS = 180.0

REASON_BUSY = "busy"
REASON_PEAK_HOURS = "peak_hours"
REASON_INVALID_TARGET = "invalid_target"
REASON_INCONSISTENT = "inconsistent"
REASON_PREFLIGHT = "preflight"
REASON_DIRTY_TREE = "dirty_tree"
REASON_NOT_RUNNING = "not_running"

PREFLIGHT_RESTART = "restart"
PREFLIGHT_CREDENTIALS = "credentials"
PREFLIGHT_JOB_IDLE = "job_idle"
PREFLIGHT_TREE_CLEAN = "tree_clean"
PREFLIGHT_LAST_UPDATE = "last_update"
PREFLIGHT_DISK_SPACE = "disk_space"


@dataclass(frozen=True)
class InstalledIdentity:
    """Installed Release identity from the local Release Manifest."""

    tag: Optional[str]
    degraded: bool
    reason: Optional[str]
    commit: Optional[str]


@dataclass(frozen=True)
class FormalRelease:
    """One GitHub Release API entry (prerelease flag set before catalogue filter)."""

    tag: str
    name: str
    published_at: str
    prerelease: bool = False
    commit: Optional[str] = None


@dataclass(frozen=True)
class PreflightEnv:
    """Environment facts for Update Preflight (adapters fill these)."""

    restart_ready: bool
    credentials_ready: bool
    dirty_tree: bool
    # 磁盘余量：adapter 读不到时保持 True/None，探测失败不该挡住更新。
    # 满盘时执行 pip sync 会把 .venv 写坏（现场复合故障的成因之一），故设为硬门禁。
    disk_ok: bool = True
    disk_free_mb: Optional[float] = None


@dataclass(frozen=True)
class PreflightCheck:
    """One Update Preflight light for Admin UI."""

    code: str
    ok: bool
    message: str


@dataclass(frozen=True)
class UpdatePreflight:
    """Aggregated Update Preflight outcome from Version Check."""

    checks: List[PreflightCheck]
    healthy_runtime: bool
    apply_allowed: bool
    dirty_tree: bool
    discard_local_changes_allowed: bool


@dataclass(frozen=True)
class VersionCheckResult:
    installed_tag: Optional[str]
    degraded: bool
    degraded_reason: Optional[str]
    app_version: str
    releases: List[FormalRelease]
    latest_tag: Optional[str]
    update_available: bool
    preflight: UpdatePreflight


@dataclass(frozen=True)
class UpdateJobState:
    """Persisted Update Job progress under data/ (survives main-service restart)."""

    stage: str
    target_tag: Optional[str] = None
    previous_ref: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    log_path: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    rollback_attempted: bool = False
    rollback_ok: Optional[bool] = None
    snapshot_ts: Optional[str] = None
    cancel_requested: bool = False
    # Two-stage success: the job only switches the bundle and asks for a restart;
    # the Admin completes readiness confirmation after the restart.
    restart_requested_at: Optional[str] = None
    health_confirmed_at: Optional[str] = None
    startup_id: Optional[str] = None
    health_detail: Optional[str] = None


@dataclass(frozen=True)
class RuntimeReadiness:
    """Readiness-probe result used to confirm a restarted service is healthy."""

    ready: bool
    startup_id: Optional[str]
    started_at: Optional[str]
    db_connected: bool
    migrations_complete: bool
    key_tables_readable: bool
    details: List[str]


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of Apply Update intent (web process only; does not run the job)."""

    accepted: bool
    reason: Optional[str] = None
    job: Optional[UpdateJobState] = None


@dataclass(frozen=True)
class CancelResult:
    """Outcome of operator cancel for an in-progress Update Job."""

    accepted: bool
    reason: Optional[str] = None
    job: Optional[UpdateJobState] = None
    forced: bool = False


class InstalledIdentityPort(Protocol):
    def inspect_installed(self) -> InstalledIdentity: ...


class GitHubReleasesPort(Protocol):
    def list_releases(self) -> Sequence[FormalRelease]: ...

    def get_tag_commit(self, tag: str) -> Optional[str]: ...


class JobStateStorePort(Protocol):
    def read(self) -> UpdateJobState: ...

    def write(self, state: UpdateJobState) -> None: ...


class OneshotStarterPort(Protocol):
    def start(self) -> None: ...


class JobStopperPort(Protocol):
    def stop(self) -> None: ...


class PeakHoursPort(Protocol):
    def is_peak(self) -> bool: ...


class PreflightEnvPort(Protocol):
    def inspect_env(self) -> PreflightEnv: ...


class ReadinessPort(Protocol):
    async def inspect_readiness(self) -> RuntimeReadiness: ...


class UpdateHistoryPort(Protocol):
    def read(self) -> List[dict]: ...

    def latest_failure(self) -> Optional[dict]: ...

    def record_state(self, state: UpdateJobState) -> None: ...


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp, assuming China time when naive; None if unparseable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed


def _semver_key(tag: str) -> Optional[Tuple[int, ...]]:
    """Parse v1.2.3 / 1.2.3 into a comparable tuple; None if not plain semver."""
    raw = tag[1:] if tag[:1] in ("v", "V") else tag
    parts = raw.split(".")
    if len(parts) < 2:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _is_newer_release(
    candidate_tag: str,
    installed_tag: str,
    catalogue: Sequence[FormalRelease],
) -> bool:
    """True when candidate is newer than installed relative to the formal catalogue."""
    tags = [r.tag for r in catalogue]
    try:
        # GitHub catalogue is newest-first; lower index ⇒ newer.
        return tags.index(candidate_tag) < tags.index(installed_tag)
    except ValueError:
        pass
    cand_key = _semver_key(candidate_tag)
    inst_key = _semver_key(installed_tag)
    if cand_key is not None and inst_key is not None:
        return cand_key > inst_key
    return False


def _default_preflight_env() -> PreflightEnv:
    """Optimistic default when no PreflightEnvPort is wired (unit tests)."""
    return PreflightEnv(
        restart_ready=True,
        credentials_ready=True,
        dirty_tree=False,
    )


def _build_preflight(
    env: PreflightEnv,
    *,
    job_idle: bool,
    last_failure: Optional[dict] = None,
) -> UpdatePreflight:
    """Aggregate env + job facts into Admin-facing Update Preflight lights."""
    dirty = bool(env.dirty_tree)
    disk_ok = bool(env.disk_ok)
    if env.disk_free_mb is None:
        disk_message = (
            "磁盘可用空间充足"
            if disk_ok
            else "磁盘可用空间不足，安装依赖可能把 .venv 写坏"
        )
    else:
        disk_message = (
            f"磁盘可用空间充足（{env.disk_free_mb:.0f}MB）"
            if disk_ok
            else (
                f"磁盘可用空间不足（仅 {env.disk_free_mb:.0f}MB）；"
                "更新会执行 pip sync，满盘可能写坏 .venv，请先清理磁盘"
            )
        )
    checks = [
        PreflightCheck(
            code=PREFLIGHT_RESTART,
            ok=bool(env.restart_ready),
            message=(
                "重启能力可用"
                if env.restart_ready
                else "当前部署模式缺少重启能力（systemd 或 Docker socket/容器名）"
            ),
        ),
        PreflightCheck(
            code=PREFLIGHT_CREDENTIALS,
            ok=bool(env.credentials_ready),
            message=(
                "可访问 GitHub Releases（公开仓无需 PAT）"
                if env.credentials_ready
                else "无法访问 GitHub Releases（检查仓库配置、网络或 API 限流）"
            ),
        ),
        PreflightCheck(
            code=PREFLIGHT_JOB_IDLE,
            ok=job_idle,
            message=(
                "当前没有进行中的更新作业"
                if job_idle
                else "已有更新作业进行中，请等待结束后再试"
            ),
        ),
        PreflightCheck(
            code=PREFLIGHT_TREE_CLEAN,
            ok=not dirty,
            message=(
                "部署目录干净"
                if not dirty
                else "部署目录有本地改动；默认禁止应用更新，需明确确认丢弃后才可继续"
            ),
        ),
        PreflightCheck(
            code=PREFLIGHT_DISK_SPACE,
            ok=disk_ok,
            message=disk_message,
        ),
    ]
    if last_failure:
        target = last_failure.get("target_tag") or "未知版本"
        label = last_failure.get("result_label") or "失败"
        log_hint = last_failure.get("log_path") or "data/update_job.log"
        checks.append(
            PreflightCheck(
                code=PREFLIGHT_LAST_UPDATE,
                ok=True,
                message=(
                    f"上一次更新（{target}）结果为「{label}」；仅提示，不阻塞本次更新。"
                    f"日志：{log_hint}"
                ),
            )
        )
    healthy_runtime = bool(env.restart_ready and env.credentials_ready)
    # Restart + credentials + idle job + free disk are hard gates; dirty may be overridden.
    gates_ok_without_dirty = healthy_runtime and job_idle and disk_ok
    apply_allowed = gates_ok_without_dirty and not dirty
    discard_local_changes_allowed = gates_ok_without_dirty and dirty
    return UpdatePreflight(
        checks=checks,
        healthy_runtime=healthy_runtime,
        apply_allowed=apply_allowed,
        dirty_tree=dirty,
        discard_local_changes_allowed=discard_local_changes_allowed,
    )


class ReleaseUpdate:
    """Product seam for release delivery on a Runtime Instance."""

    def __init__(
        self,
        *,
        installed: InstalledIdentityPort,
        github: GitHubReleasesPort,
        app_version: str,
        job_store: Optional[JobStateStorePort] = None,
        oneshot: Optional[OneshotStarterPort] = None,
        job_stopper: Optional[JobStopperPort] = None,
        peak_hours: Optional[PeakHoursPort] = None,
        preflight_env: Optional[PreflightEnvPort] = None,
        readiness: Optional[ReadinessPort] = None,
        history: Optional[UpdateHistoryPort] = None,
    ) -> None:
        self._installed = installed
        self._github = github
        self._app_version = app_version
        self._job_store = job_store
        self._oneshot = oneshot
        self._job_stopper = job_stopper
        self._peak_hours = peak_hours
        self._preflight_env = preflight_env
        self._readiness = readiness
        self._history = history

    def _inspect_preflight_env(self) -> PreflightEnv:
        if self._preflight_env is None:
            return _default_preflight_env()
        return self._preflight_env.inspect_env()

    def _job_is_idle(self) -> bool:
        if self._job_store is None:
            return True
        return self._job_store.read().stage not in IN_PROGRESS_STAGES

    def _latest_failure(self) -> Optional[dict]:
        if self._history is None:
            return None
        try:
            return self._history.latest_failure()
        except Exception:
            return None

    def _record_history(self, state: UpdateJobState) -> None:
        if self._history is None:
            return
        try:
            self._history.record_state(state)
        except Exception:
            pass

    def version_check(self) -> VersionCheckResult:
        """Compare local Release Manifest identity to the formal GitHub catalogue.

        Prereleases are excluded from the default catalogue. Git tag / dirty
        state is not the installed-Release authority. Also runs Update Preflight.
        """
        identity = self._installed.inspect_installed()
        installed_tag = identity.tag
        degraded = identity.degraded
        degraded_reason = identity.reason
        env = self._inspect_preflight_env()

        releases: List[FormalRelease] = []
        latest_tag: Optional[str] = None
        update_available = False
        catalogue_ok = True
        try:
            raw = list(self._github.list_releases())
            releases = [r for r in raw if not r.prerelease]
            latest_tag = releases[0].tag if releases else None
            if (
                not degraded
                and installed_tag
                and identity.commit
                and any(r.tag == installed_tag for r in releases)
            ):
                remote_commit = self._github.get_tag_commit(installed_tag)
                if remote_commit and remote_commit != identity.commit:
                    degraded = True
                    degraded_reason = REASON_INCONSISTENT
            update_available = bool(
                installed_tag
                and latest_tag
                and _is_newer_release(latest_tag, installed_tag, releases)
            )
        except Exception:
            # Still return Update Preflight so Admin can show a credentials/catalogue red light
            # instead of a bare Version Check failure with no lights.
            catalogue_ok = False
            releases = []
            latest_tag = None
            update_available = False

        if not catalogue_ok:
            env = replace(env, credentials_ready=False)
        preflight = _build_preflight(
            env,
            job_idle=self._job_is_idle(),
            last_failure=self._latest_failure(),
        )
        return VersionCheckResult(
            installed_tag=installed_tag,
            degraded=degraded,
            degraded_reason=degraded_reason,
            app_version=self._app_version,
            releases=releases,
            latest_tag=latest_tag,
            update_available=update_available,
            preflight=preflight,
        )

    def history(self) -> List[dict]:
        """Recent Update History (newest first), outside the business database."""
        if self._history is None:
            return []
        try:
            return self._history.read()
        except Exception:
            return []

    def job_status(self) -> UpdateJobState:
        """Read the persisted Update Job state.

        Terminal states are recorded to Update History on the way out so a
        failed/cancelled job still leaves a trace. Readiness confirmation is a
        separate step (``confirm_health``) — reading state never declares success.
        """
        if self._job_store is None:
            return UpdateJobState(stage=STAGE_IDLE)
        state = self._job_store.read()
        if state.stage in TERMINAL_STAGES:
            self._record_history(state)
        return state

    async def confirm_health(
        self,
        *,
        grace_seconds: float = HEALTH_CONFIRM_GRACE_SECONDS,
        force: bool = False,
    ) -> UpdateJobState:
        """Complete the second half of 「更新成功」: did the restarted service come up?

        The job only switches the Release Bundle and asks for a restart. Here the
        Admin confirms readiness (database connected, migrations complete, key
        tables readable) *and* that the running process started after this
        restart was requested. Until then the job stays ``restarting``; once the
        grace window expires without readiness it becomes
        ``succeeded_but_unhealthy``. Never retries and never rolls back on its own.
        """
        if self._job_store is None:
            return UpdateJobState(stage=STAGE_IDLE)
        state = self._job_store.read()
        if state.stage != STAGE_RESTARTING or not state.target_tag:
            if state.stage in TERMINAL_STAGES:
                self._record_history(state)
            return state

        try:
            identity = self._installed.inspect_installed()
        except Exception as exc:
            identity = None
            logger.warning("读取已装发行版身份失败: %s", exc)
        if identity is None or not identity.tag or identity.tag != state.target_tag:
            # Bundle not switched (or identity unreadable) — leave the job alone;
            # the runner decides failure/rollback.
            return state

        readiness = None
        if self._readiness is not None:
            try:
                readiness = await self._readiness.inspect_readiness()
            except Exception as exc:
                logger.warning("就绪检查失败: %s", exc)

        from datetime import datetime

        now = datetime.now(CHINA_TZ)
        if readiness is not None and readiness.ready and self._started_after_restart(
            readiness, state
        ):
            confirmed = replace(
                state,
                stage=STAGE_SUCCEEDED,
                message="Update Job succeeded",
                error=None,
                finished_at=state.finished_at or now.isoformat(),
                cancel_requested=False,
                health_confirmed_at=now.isoformat(),
                startup_id=readiness.startup_id,
                health_detail="；".join(readiness.details) or "就绪检查通过",
            )
            self._job_store.write(confirmed)
            self._record_history(confirmed)
            return confirmed

        detail = "；".join(readiness.details) if readiness and readiness.details else "服务尚未恢复健康"
        elapsed = self._seconds_since(state.restart_requested_at, now)
        if force or (elapsed is not None and elapsed >= grace_seconds):
            unhealthy = replace(
                state,
                stage=STAGE_SUCCEEDED_BUT_UNHEALTHY,
                message="已切换到新版本，但服务未恢复健康",
                finished_at=state.finished_at or now.isoformat(),
                health_confirmed_at=now.isoformat(),
                startup_id=readiness.startup_id if readiness else None,
                health_detail=detail,
            )
            self._job_store.write(unhealthy)
            self._record_history(unhealthy)
            return unhealthy

        # Still within the grace window: keep showing 「已切换、重启中」.
        return replace(state, health_detail=detail)

    @staticmethod
    def _started_after_restart(
        readiness: RuntimeReadiness,
        state: UpdateJobState,
    ) -> bool:
        """True when the running process started after this restart was requested."""
        requested = _parse_iso(state.restart_requested_at)
        started = _parse_iso(readiness.started_at)
        if started is None:
            return False
        if requested is None:
            # No recorded restart time (legacy state file): accept a ready service.
            return True
        return started > requested

    @staticmethod
    def _seconds_since(value: Optional[str], now) -> Optional[float]:
        parsed = _parse_iso(value)
        if parsed is None:
            return None
        return max(0.0, (now - parsed).total_seconds())

    def cancel(
        self,
        *,
        wait_seconds: float = 5.0,
        poll_interval: float = 0.25,
    ) -> CancelResult:
        """Request abort of an in-progress Update Job.

        Sets the cancel flag, asks the oneshot/process to stop, waits briefly
        for the runner to finalize (including rollback when it already left the
        previous tree). If the job stays in-progress, force-marks ``failed`` so
        Admin is not stuck forever (rollback may then need a re-Apply).
        """
        if self._job_store is None:
            raise RuntimeError("Apply Update adapters are not configured")

        from services.release_update.job_state import (
            clear_cancel,
            request_cancel,
        )

        current = self._job_store.read()
        if current.stage not in IN_PROGRESS_STAGES:
            return CancelResult(
                accepted=False,
                reason=REASON_NOT_RUNNING,
                job=current,
            )

        request_cancel()
        self._job_store.write(
            replace(
                current,
                cancel_requested=True,
                message="Cancel requested; stopping Update Job",
            )
        )

        if self._job_stopper is not None:
            try:
                self._job_stopper.stop()
            except Exception:
                # Still wait / force-finalize below so UI can recover.
                pass

        deadline = time.monotonic() + max(0.0, wait_seconds)
        while time.monotonic() < deadline:
            state = self._job_store.read()
            if state.stage not in IN_PROGRESS_STAGES:
                clear_cancel()
                return CancelResult(accepted=True, job=state, forced=False)
            time.sleep(max(0.05, poll_interval))

        state = self._job_store.read()
        if state.stage not in IN_PROGRESS_STAGES:
            clear_cancel()
            return CancelResult(accepted=True, job=state, forced=False)

        from datetime import datetime, timedelta, timezone

        finished = datetime.now(timezone(timedelta(hours=8))).isoformat()
        final = replace(
            state,
            stage=STAGE_FAILED,
            cancel_requested=True,
            message="Update Job cancelled by operator",
            error=(
                "cancelled by operator (forced); if the application tree already "
                "switched, re-Apply the previous_ref or inspect update_job.log"
            ),
            finished_at=finished,
        )
        self._job_store.write(final)
        clear_cancel()
        return CancelResult(accepted=True, job=final, forced=True)

    def apply(
        self,
        target_tag: str,
        *,
        peak_override: bool = False,
        discard_local_changes: bool = False,
    ) -> ApplyResult:
        """Record Apply Update intent and request the oneshot Update Job.

        Does not fetch/build in-process. Concurrent apply while a job is
        in progress is rejected. Update Preflight must allow Apply (dirty
        tree only with explicit discard_local_changes).
        """
        if self._job_store is None or self._oneshot is None:
            raise RuntimeError("Apply Update adapters are not configured")

        current = self._job_store.read()
        if current.stage in IN_PROGRESS_STAGES:
            return ApplyResult(accepted=False, reason=REASON_BUSY, job=current)

        env = self._inspect_preflight_env()
        preflight = _build_preflight(env, job_idle=True)
        if not preflight.healthy_runtime:
            return ApplyResult(accepted=False, reason=REASON_PREFLIGHT)
        if preflight.dirty_tree and not discard_local_changes:
            return ApplyResult(accepted=False, reason=REASON_DIRTY_TREE)

        tag = (target_tag or "").strip()
        catalogue = [r for r in self._github.list_releases() if not r.prerelease]
        if not tag or tag not in {r.tag for r in catalogue}:
            return ApplyResult(accepted=False, reason=REASON_INVALID_TARGET)

        if (
            self._peak_hours is not None
            and self._peak_hours.is_peak()
            and not peak_override
        ):
            return ApplyResult(accepted=False, reason=REASON_PEAK_HOURS)

        from services.release_update.job_state import clear_cancel

        clear_cancel()

        identity = self._installed.inspect_installed()
        previous_ref = identity.tag or identity.commit
        # Prefer store's existing log pointer when present (FileJobStateStore fills it).
        prior = current if current.log_path else None
        job = UpdateJobState(
            stage=STAGE_QUEUED,
            target_tag=tag,
            previous_ref=previous_ref,
            message="Update Job queued",
            log_path=prior.log_path if prior else None,
            cancel_requested=False,
        )
        self._job_store.write(job)
        try:
            self._oneshot.start()
        except Exception:
            # Avoid leaving a stuck "queued" that blocks concurrent apply forever.
            self._job_store.write(
                UpdateJobState(
                    stage=STAGE_FAILED,
                    target_tag=tag,
                    previous_ref=previous_ref,
                    message="Failed to start Update Job oneshot",
                    error="oneshot start failed",
                    log_path=prior.log_path if prior else None,
                )
            )
            raise
        # Re-read so file-store defaults (log_path) are visible to the caller.
        return ApplyResult(accepted=True, job=self._job_store.read())
