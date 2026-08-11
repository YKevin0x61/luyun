#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release Update seam: Version Check · Apply Update intent · Update Job state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence, Tuple

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
STAGE_FAILED = "failed"

# Legacy git/split-asset stage names (ADR 0010) — still recognized as in-progress
# if an older state file is mid-job during cutover.
STAGE_FETCHING = "fetching"
STAGE_INSTALLING_ASSETS = "installing_assets"

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

REASON_BUSY = "busy"
REASON_PEAK_HOURS = "peak_hours"
REASON_INVALID_TARGET = "invalid_target"
REASON_INCONSISTENT = "inconsistent"
REASON_PREFLIGHT = "preflight"
REASON_DIRTY_TREE = "dirty_tree"

PREFLIGHT_RESTART = "restart"
PREFLIGHT_CREDENTIALS = "credentials"
PREFLIGHT_JOB_IDLE = "job_idle"
PREFLIGHT_TREE_CLEAN = "tree_clean"


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


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of Apply Update intent (web process only; does not run the job)."""

    accepted: bool
    reason: Optional[str] = None
    job: Optional[UpdateJobState] = None


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


class PeakHoursPort(Protocol):
    def is_peak(self) -> bool: ...


class PreflightEnvPort(Protocol):
    def inspect_env(self) -> PreflightEnv: ...


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
) -> UpdatePreflight:
    """Aggregate env + job facts into Admin-facing Update Preflight lights."""
    dirty = bool(env.dirty_tree)
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
                "Releases 凭据可用"
                if env.credentials_ready
                else "Releases 凭据缺失或不可用，无法拉取发行目录/下载发行包"
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
    ]
    healthy_runtime = bool(env.restart_ready and env.credentials_ready)
    # Restart + credentials + idle job are hard gates; dirty may be overridden.
    gates_ok_without_dirty = healthy_runtime and job_idle
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
        peak_hours: Optional[PeakHoursPort] = None,
        preflight_env: Optional[PreflightEnvPort] = None,
    ) -> None:
        self._installed = installed
        self._github = github
        self._app_version = app_version
        self._job_store = job_store
        self._oneshot = oneshot
        self._peak_hours = peak_hours
        self._preflight_env = preflight_env

    def _inspect_preflight_env(self) -> PreflightEnv:
        if self._preflight_env is None:
            return _default_preflight_env()
        return self._preflight_env.inspect_env()

    def _job_is_idle(self) -> bool:
        if self._job_store is None:
            return True
        return self._job_store.read().stage not in IN_PROGRESS_STAGES

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
            env = PreflightEnv(
                restart_ready=env.restart_ready,
                credentials_ready=False,
                dirty_tree=env.dirty_tree,
            )
        preflight = _build_preflight(env, job_idle=self._job_is_idle())
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

    def job_status(self) -> UpdateJobState:
        """Read Update Job state from the persisted store."""
        if self._job_store is None:
            return UpdateJobState(stage=STAGE_IDLE)
        return self._job_store.read()

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
