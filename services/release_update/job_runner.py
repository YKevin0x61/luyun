#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Update Job runner: backup → fetch bundle → install → conditional deps → restart."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Optional, Protocol

from database import CHINA_TZ
from services.release_update import (
    STAGE_BACKING_UP,
    STAGE_FAILED,
    STAGE_FETCHING_BUNDLE,
    STAGE_INSTALLING,
    STAGE_QUEUED,
    STAGE_RESTARTING,
    STAGE_SUCCEEDED,
    STAGE_SYNCING_DEPS,
    JobStateStorePort,
    UpdateJobState,
)


class JobCancelled(Exception):
    """Operator requested abort of the Update Job."""


@dataclass(frozen=True)
class BundleInstallResult:
    """Outcome of activating a verified Release Bundle (after atomic switch)."""

    requirements_fingerprint: Optional[str]
    previous_requirements_fingerprint: Optional[str]


class MandatoryBackupPort(Protocol):
    def run_backup(self) -> str: ...


class BundleInstallPort(Protocol):
    def fetch_bundle(self, tag: str) -> None:
        """Download the Release Bundle and hard-verify checksums. No live-tree mutation."""

    def activate_bundle(self, tag: str) -> BundleInstallResult:
        """Extract beside the live tree, atomically switch, retain previous tree."""

    def restore_previous_tree(self) -> None:
        """Switch the live application tree back to the retained previous tree."""


class DepsSyncPort(Protocol):
    def sync(self) -> None: ...


class MainServicePort(Protocol):
    def restart(self) -> None: ...


def _now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat()


def _fingerprint_changed(
    previous: Optional[str],
    new: Optional[str],
) -> bool:
    """True when pip sync is required (missing either side counts as changed)."""
    if not previous or not new:
        return True
    return previous != new


class UpdateJobRunner:
    """Executes one Apply Update outside the web process (oneshot entry)."""

    def __init__(
        self,
        *,
        job_store: JobStateStorePort,
        backup: MandatoryBackupPort,
        bundle: BundleInstallPort,
        deps: DepsSyncPort,
        service: MainServicePort,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._store = job_store
        self._backup = backup
        self._bundle = bundle
        self._deps = deps
        self._service = service
        self._is_cancelled = is_cancelled or (lambda: False)

    def _raise_if_cancelled(self) -> None:
        if self._is_cancelled():
            raise JobCancelled("cancelled by operator")

    def run(self) -> UpdateJobState:
        intent = self._store.read()
        if intent.stage != STAGE_QUEUED or not intent.target_tag:
            return self._fail_before_leave(
                intent,
                error="Update Job has no queued intent",
            )

        previous_ref = intent.previous_ref
        base = replace(
            intent,
            previous_ref=previous_ref,
            started_at=intent.started_at or _now_iso(),
            error=None,
            finished_at=None,
            rollback_attempted=False,
            rollback_ok=None,
            cancel_requested=False,
        )
        left_previous = False

        try:
            self._raise_if_cancelled()
            self._set(base, STAGE_BACKING_UP, "Creating mandatory backup snapshot")
            snapshot_ts = self._backup.run_backup()
            base = replace(self._store.read(), snapshot_ts=snapshot_ts)

            self._raise_if_cancelled()
            self._set(
                base,
                STAGE_FETCHING_BUNDLE,
                f"Downloading and verifying Release Bundle for {base.target_tag}",
            )
            self._bundle.fetch_bundle(base.target_tag or "")
            base = self._store.read()

            self._raise_if_cancelled()
            self._set(
                base,
                STAGE_INSTALLING,
                f"Extracting and activating Release Bundle for {base.target_tag}",
            )
            # activate_bundle must leave the live tree untouched on failure.
            # Only after it returns have we left the previous tree.
            install = self._bundle.activate_bundle(base.target_tag or "")
            left_previous = True
            base = self._store.read()

            self._raise_if_cancelled()
            if _fingerprint_changed(
                install.previous_requirements_fingerprint,
                install.requirements_fingerprint,
            ):
                self._set(base, STAGE_SYNCING_DEPS, "Syncing Python dependencies")
                self._deps.sync()
            else:
                self._set(
                    base,
                    STAGE_SYNCING_DEPS,
                    "Skipping Python dependency sync (requirements fingerprint unchanged)",
                )
            base = self._store.read()

            self._raise_if_cancelled()
            self._set(base, STAGE_RESTARTING, "Restarting main service")
            self._service.restart()

            final = replace(
                self._store.read(),
                stage=STAGE_SUCCEEDED,
                message="Update Job succeeded",
                finished_at=_now_iso(),
                error=None,
                cancel_requested=False,
            )
            self._store.write(final)
            return final
        except JobCancelled as exc:
            if not left_previous:
                return self._fail_before_leave(self._store.read(), error=str(exc))
            return self._fail_with_rollback(self._store.read(), error=str(exc))
        except Exception as exc:
            # Deps may raise RuntimeError("cancelled by operator"); treat like JobCancelled.
            if not left_previous:
                return self._fail_before_leave(self._store.read(), error=str(exc))
            return self._fail_with_rollback(self._store.read(), error=str(exc))

    def _set(self, base: UpdateJobState, stage: str, message: str) -> None:
        self._store.write(replace(base, stage=stage, message=message))

    def _fail_before_leave(self, state: UpdateJobState, *, error: str) -> UpdateJobState:
        """Abort without changing the live application tree (e.g. backup/checksum)."""
        reason = error
        if state.stage == STAGE_BACKING_UP and "backup" not in error.lower():
            reason = f"backup failed: {error}"
        final = replace(
            state,
            stage=STAGE_FAILED,
            message="Update Job failed before changing installed Release",
            error=reason,
            finished_at=_now_iso(),
            rollback_attempted=False,
            rollback_ok=None,
        )
        self._store.write(final)
        return final

    def _fail_with_rollback(self, state: UpdateJobState, *, error: str) -> UpdateJobState:
        """Restore the previous application tree and try to bring the main service up."""
        rollback_ok = False
        rollback_error: Optional[str] = None
        try:
            self._bundle.restore_previous_tree()
            self._service.restart()
            rollback_ok = True
        except Exception as rb_exc:
            rollback_ok = False
            rollback_error = str(rb_exc)
            try:
                self._service.restart()
            except Exception:
                pass

        detail = error
        if rollback_error:
            detail = f"{error}; rollback: {rollback_error}"
        log_hint = state.log_path or "data/update_job.log"
        if log_hint and log_hint not in detail:
            detail = f"{detail}; log={log_hint}"
        cancelled = "cancelled by operator" in error.lower()
        final = replace(
            state,
            stage=STAGE_FAILED,
            message=(
                "Update Job cancelled; rollback attempted"
                if cancelled
                else "Update Job failed; rollback attempted"
            ),
            error=detail,
            finished_at=_now_iso(),
            rollback_attempted=True,
            rollback_ok=rollback_ok,
            cancel_requested=cancelled or state.cancel_requested,
        )
        self._store.write(final)
        return final
