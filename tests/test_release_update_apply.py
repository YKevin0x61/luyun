#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release Update — Apply Update intent seam tests (faked adapters)."""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, field
from typing import List, Optional
from unittest import mock

from services.release_update import (
    FormalRelease,
    InstalledIdentity,
    PreflightEnv,
    ReleaseUpdate,
    RuntimeReadiness,
    UpdateJobState,
)


@dataclass
class FakeInstalled:
    identity: InstalledIdentity

    def inspect_installed(self) -> InstalledIdentity:
        return self.identity


@dataclass
class FakeGitHub:
    releases: List[FormalRelease]

    def list_releases(self) -> List[FormalRelease]:
        return list(self.releases)

    def get_tag_commit(self, tag: str) -> Optional[str]:
        for release in self.releases:
            if release.tag == tag:
                return release.commit
        return None


@dataclass
class FakeJobStore:
    state: UpdateJobState = field(
        default_factory=lambda: UpdateJobState(stage="idle")
    )
    writes: List[UpdateJobState] = field(default_factory=list)

    def read(self) -> UpdateJobState:
        return self.state

    def write(self, state: UpdateJobState) -> None:
        self.writes.append(state)
        self.state = state


@dataclass
class FakeOneshot:
    started: bool = False

    def start(self) -> None:
        self.started = True


@dataclass
class FakeStopper:
    stopped: bool = False

    def stop(self) -> None:
        self.stopped = True


@dataclass
class FakePeak:
    peak: bool = False

    def is_peak(self) -> bool:
        return self.peak


@dataclass
class FakePreflightEnv:
    restart_ready: bool = True
    credentials_ready: bool = True
    dirty_tree: bool = False

    def inspect_env(self) -> PreflightEnv:
        return PreflightEnv(
            restart_ready=self.restart_ready,
            credentials_ready=self.credentials_ready,
            dirty_tree=self.dirty_tree,
        )


@dataclass
class FakeReadiness:
    """Readiness probe where the running process started after the restart."""

    ready: bool = True
    started_at: str = "2026-08-10T12:00:05+08:00"
    startup_id: Optional[str] = "boot-2"
    db_connected: bool = True
    migrations_complete: bool = True
    key_tables_readable: bool = True
    details: List[str] = field(default_factory=list)

    async def inspect_readiness(self) -> RuntimeReadiness:
        return RuntimeReadiness(
            ready=self.ready,
            startup_id=self.startup_id,
            started_at=self.started_at,
            db_connected=self.db_connected,
            migrations_complete=self.migrations_complete,
            key_tables_readable=self.key_tables_readable,
            details=list(self.details),
        )


@dataclass
class FakeHistory:
    entries: List[dict] = field(default_factory=list)

    def read(self) -> List[dict]:
        return list(self.entries)

    def latest_failure(self) -> Optional[dict]:
        for entry in self.entries:
            if entry.get("result") != "succeeded":
                return entry
        return None

    def record_state(self, state: UpdateJobState) -> None:
        from services.release_update import history as history_module

        entry = history_module.entry_from_state(state)
        if entry is not None:
            self.entries.insert(0, entry)


FORMAL = [
    FormalRelease(
        tag="v0.2.0",
        name="0.2.0",
        published_at="2026-08-01T00:00:00Z",
        prerelease=False,
    ),
    FormalRelease(
        tag="v0.1.0",
        name="0.1.0",
        published_at="2026-07-01T00:00:00Z",
        prerelease=False,
    ),
]


def _build(
    *,
    job_store: Optional[FakeJobStore] = None,
    oneshot: Optional[FakeOneshot] = None,
    job_stopper: Optional[FakeStopper] = None,
    peak: Optional[FakePeak] = None,
    preflight_env: Optional[FakePreflightEnv] = None,
    readiness: Optional[FakeReadiness] = None,
    history: Optional[FakeHistory] = None,
    tag: str = "v0.1.0",
) -> tuple[ReleaseUpdate, FakeJobStore, FakeOneshot]:
    store = job_store or FakeJobStore()
    starter = oneshot or FakeOneshot()
    ru = ReleaseUpdate(
        installed=FakeInstalled(
            InstalledIdentity(tag=tag, degraded=False, reason=None, commit="abc")
        ),
        github=FakeGitHub(FORMAL),
        app_version="0.1.0",
        job_store=store,
        oneshot=starter,
        job_stopper=job_stopper or FakeStopper(),
        peak_hours=peak or FakePeak(False),
        preflight_env=preflight_env or FakePreflightEnv(),
        readiness=readiness,
        history=history,
    )
    return ru, store, starter


class ApplyUpdateTest(unittest.TestCase):
    def test_apply_rejected_when_job_in_progress(self):
        store = FakeJobStore(
            UpdateJobState(
                stage="fetching_bundle",
                target_tag="v0.2.0",
                previous_ref="v0.1.0",
            )
        )
        ru, _, oneshot = _build(job_store=store)

        result = ru.apply("v0.2.0", peak_override=True)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "busy")
        self.assertFalse(oneshot.started)
        self.assertEqual(len(store.writes), 0)

    def test_apply_rejected_when_legacy_stage_still_in_progress(self):
        store = FakeJobStore(
            UpdateJobState(stage="fetching", target_tag="v0.2.0", previous_ref="v0.1.0")
        )
        ru, _, oneshot = _build(job_store=store)

        result = ru.apply("v0.2.0", peak_override=True)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "busy")
        self.assertFalse(oneshot.started)

    def test_apply_writes_intent_and_starts_oneshot(self):
        ru, store, oneshot = _build()

        result = ru.apply("v0.2.0", peak_override=False)

        self.assertTrue(result.accepted)
        self.assertIsNone(result.reason)
        self.assertIsNotNone(result.job)
        assert result.job is not None
        self.assertEqual(result.job.stage, "queued")
        self.assertEqual(result.job.target_tag, "v0.2.0")
        self.assertEqual(result.job.previous_ref, "v0.1.0")
        self.assertTrue(oneshot.started)
        self.assertEqual(store.state.stage, "queued")
        self.assertEqual(store.state.target_tag, "v0.2.0")

    def test_apply_rejected_during_peak_hours_without_override(self):
        ru, store, oneshot = _build(peak=FakePeak(True))

        result = ru.apply("v0.2.0", peak_override=False)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "peak_hours")
        self.assertFalse(oneshot.started)
        self.assertEqual(len(store.writes), 0)

    def test_apply_accepted_during_peak_hours_with_override(self):
        ru, _, oneshot = _build(peak=FakePeak(True))

        result = ru.apply("v0.2.0", peak_override=True)

        self.assertTrue(result.accepted)
        self.assertTrue(oneshot.started)
        self.assertEqual(result.job and result.job.target_tag, "v0.2.0")

    def test_apply_rejected_for_unknown_formal_release(self):
        ru, store, oneshot = _build()

        result = ru.apply("v9.9.9", peak_override=True)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "invalid_target")
        self.assertFalse(oneshot.started)
        self.assertEqual(len(store.writes), 0)

    def test_apply_older_formal_release_is_rollback_via_same_job_path(self):
        """Software rollback = Apply an older formal Release (no separate product)."""
        ru, store, oneshot = _build(tag="v0.2.0")

        result = ru.apply("v0.1.0", peak_override=True)

        self.assertTrue(result.accepted)
        self.assertTrue(oneshot.started)
        assert result.job is not None
        self.assertEqual(result.job.stage, "queued")
        self.assertEqual(result.job.target_tag, "v0.1.0")
        self.assertEqual(result.job.previous_ref, "v0.2.0")
        self.assertEqual(store.state.target_tag, "v0.1.0")

    def test_apply_rejected_when_preflight_unhealthy(self):
        ru, store, oneshot = _build(
            preflight_env=FakePreflightEnv(credentials_ready=False)
        )

        result = ru.apply("v0.2.0", peak_override=True)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "preflight")
        self.assertFalse(oneshot.started)
        self.assertEqual(len(store.writes), 0)

    def test_apply_dirty_tree_requires_discard_confirm(self):
        ru, store, oneshot = _build(preflight_env=FakePreflightEnv(dirty_tree=True))

        denied = ru.apply("v0.2.0", peak_override=True)
        self.assertFalse(denied.accepted)
        self.assertEqual(denied.reason, "dirty_tree")
        self.assertFalse(oneshot.started)

        accepted = ru.apply(
            "v0.2.0",
            peak_override=True,
            discard_local_changes=True,
        )
        self.assertTrue(accepted.accepted)
        self.assertTrue(oneshot.started)
        self.assertEqual(store.state.stage, "queued")

    def test_job_status_returns_bundle_stage_and_log_pointer(self):
        store = FakeJobStore(
            UpdateJobState(
                stage="fetching_bundle",
                target_tag="v0.2.0",
                message="downloading Release Bundle",
                log_path="data/update_job.log",
            )
        )
        ru, _, _ = _build(job_store=store)

        status = ru.job_status()

        self.assertEqual(status.stage, "fetching_bundle")
        self.assertEqual(status.target_tag, "v0.2.0")
        self.assertEqual(status.message, "downloading Release Bundle")
        self.assertEqual(status.log_path, "data/update_job.log")

    def test_job_status_does_not_declare_success_while_awaiting_health(self):
        store = FakeJobStore(
            UpdateJobState(
                stage="restarting",
                target_tag="v0.5.2",
                previous_ref="v0.5.3",
                message="已切换发行包，正在重启服务；等待健康确认",
                log_path="data/update_job.log",
                restart_requested_at="2026-08-10T12:00:00+08:00",
            )
        )
        ru, _, _ = _build(job_store=store, tag="v0.5.2")

        status = ru.job_status()

        self.assertEqual(status.stage, "restarting")
        self.assertEqual(store.state.stage, "restarting")
        self.assertIsNone(status.finished_at)

    def test_confirm_health_promotes_job_to_succeeded(self):
        store = FakeJobStore(
            UpdateJobState(
                stage="restarting",
                target_tag="v0.5.2",
                previous_ref="v0.5.3",
                log_path="data/update_job.log",
                restart_requested_at="2026-08-10T12:00:00+08:00",
            )
        )
        history = FakeHistory()
        ru, _, _ = _build(
            job_store=store,
            tag="v0.5.2",
            readiness=FakeReadiness(ready=True),
            history=history,
        )

        status = asyncio.run(ru.confirm_health())

        self.assertEqual(status.stage, "succeeded")
        self.assertIsNotNone(status.health_confirmed_at)
        self.assertEqual(status.startup_id, "boot-2")
        self.assertEqual([e["result"] for e in history.entries], ["succeeded"])

    def test_confirm_health_stays_restarting_within_grace_window(self):
        store = FakeJobStore(
            UpdateJobState(
                stage="restarting",
                target_tag="v0.5.2",
                log_path="data/update_job.log",
                restart_requested_at="2099-01-01T00:00:00+08:00",
            )
        )
        ru, _, _ = _build(
            job_store=store,
            tag="v0.5.2",
            readiness=FakeReadiness(ready=False, details=["数据库未连接"]),
        )

        status = asyncio.run(ru.confirm_health(grace_seconds=60))

        self.assertEqual(status.stage, "restarting")
        self.assertIn("数据库未连接", status.health_detail or "")

    def test_confirm_health_marks_unhealthy_after_grace(self):
        store = FakeJobStore(
            UpdateJobState(
                stage="restarting",
                target_tag="v0.5.2",
                log_path="data/update_job.log",
                restart_requested_at="2026-08-10T12:00:00+08:00",
            )
        )
        history = FakeHistory()
        ru, _, _ = _build(
            job_store=store,
            tag="v0.5.2",
            readiness=FakeReadiness(ready=False, details=["关键表不可读"]),
            history=history,
        )

        status = asyncio.run(ru.confirm_health(grace_seconds=1))

        self.assertEqual(status.stage, "succeeded_but_unhealthy")
        self.assertIn("关键表不可读", status.health_detail or "")
        self.assertEqual(
            [e["result"] for e in history.entries], ["succeeded_but_unhealthy"]
        )

    def test_confirm_health_rejects_process_older_than_restart(self):
        store = FakeJobStore(
            UpdateJobState(
                stage="restarting",
                target_tag="v0.5.2",
                log_path="data/update_job.log",
                restart_requested_at="2026-08-10T12:00:00+08:00",
            )
        )
        ru, _, _ = _build(
            job_store=store,
            tag="v0.5.2",
            readiness=FakeReadiness(ready=True, started_at="2026-08-10T11:00:00+08:00"),
        )

        status = asyncio.run(ru.confirm_health(grace_seconds=1))

        self.assertEqual(status.stage, "succeeded_but_unhealthy")

    def test_oneshot_start_failure_marks_job_failed_not_busy(self):
        class BoomOneshot:
            def start(self) -> None:
                raise RuntimeError("systemctl missing")

        store = FakeJobStore()
        ru, _, _ = _build(job_store=store, oneshot=BoomOneshot())

        with self.assertRaises(RuntimeError):
            ru.apply("v0.2.0", peak_override=True)

        self.assertEqual(store.state.stage, "failed")
        # A subsequent apply must not be rejected as busy.
        ru2, store2, oneshot2 = _build()
        result = ru2.apply("v0.2.0", peak_override=True)
        self.assertTrue(result.accepted)
        self.assertTrue(oneshot2.started)

    def test_cancel_rejected_when_idle(self):
        ru, _, _ = _build()
        stopper = FakeStopper()
        ru._job_stopper = stopper

        result = ru.cancel(wait_seconds=0)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "not_running")
        self.assertFalse(stopper.stopped)

    def test_cancel_force_finalizes_stuck_in_progress_job(self):
        store = FakeJobStore(
            UpdateJobState(
                stage="syncing_deps",
                target_tag="v0.2.0",
                previous_ref="v0.1.0",
                message="Syncing Python dependencies",
                log_path="data/update_job.log",
            )
        )
        stopper = FakeStopper()
        ru, _, _ = _build(job_store=store, job_stopper=stopper)

        with mock.patch(
            "services.release_update.job_state.request_cancel"
        ) as req, mock.patch(
            "services.release_update.job_state.clear_cancel"
        ) as clr:
            result = ru.cancel(wait_seconds=0)

        self.assertTrue(result.accepted)
        self.assertTrue(result.forced)
        self.assertTrue(stopper.stopped)
        req.assert_called_once()
        clr.assert_called()
        self.assertEqual(store.state.stage, "failed")
        self.assertTrue(store.state.cancel_requested)
        self.assertIn("cancelled by operator", store.state.error or "")
