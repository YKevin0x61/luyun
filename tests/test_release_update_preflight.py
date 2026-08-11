#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release Update — Update Preflight seam (Version Check + Apply gates)."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import List, Optional

from services.release_update import (
    FormalRelease,
    InstalledIdentity,
    ReleaseUpdate,
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
class FakePreflightEnv:
    restart_ready: bool = True
    credentials_ready: bool = True
    dirty_tree: bool = False

    def inspect_env(self):
        from services.release_update import PreflightEnv

        return PreflightEnv(
            restart_ready=self.restart_ready,
            credentials_ready=self.credentials_ready,
            dirty_tree=self.dirty_tree,
        )


FORMAL = [
    FormalRelease(
        tag="v0.2.0",
        name="0.2.0",
        published_at="2026-08-01T00:00:00Z",
        prerelease=False,
        commit="bbb",
    ),
    FormalRelease(
        tag="v0.1.0",
        name="0.1.0",
        published_at="2026-07-01T00:00:00Z",
        prerelease=False,
        commit="abc",
    ),
]


def _build(
    *,
    env: Optional[FakePreflightEnv] = None,
    job_store: Optional[FakeJobStore] = None,
    oneshot: Optional[FakeOneshot] = None,
) -> ReleaseUpdate:
    return ReleaseUpdate(
        installed=FakeInstalled(
            InstalledIdentity(
                tag="v0.1.0",
                degraded=False,
                reason=None,
                commit="abc",
            )
        ),
        github=FakeGitHub(FORMAL),
        app_version="0.1.0",
        job_store=job_store or FakeJobStore(),
        oneshot=oneshot or FakeOneshot(),
        preflight_env=env or FakePreflightEnv(),
    )


def _check_map(preflight):
    return {c.code: c for c in preflight.checks}


class UpdatePreflightVersionCheckTest(unittest.TestCase):
    def test_version_check_reports_all_green_preflight_when_runtime_healthy(self):
        result = _build().version_check()
        pf = result.preflight

        self.assertTrue(pf.healthy_runtime)
        self.assertTrue(pf.apply_allowed)
        self.assertFalse(pf.dirty_tree)
        self.assertFalse(pf.discard_local_changes_allowed)

        by_code = _check_map(pf)
        self.assertTrue(by_code["restart"].ok)
        self.assertTrue(by_code["credentials"].ok)
        self.assertTrue(by_code["job_idle"].ok)
        self.assertTrue(by_code["tree_clean"].ok)
        self.assertTrue(by_code["restart"].message)
        self.assertTrue(by_code["credentials"].message)

    def test_missing_restart_capability_is_red_and_forbids_apply(self):
        result = _build(env=FakePreflightEnv(restart_ready=False)).version_check()
        pf = result.preflight

        self.assertFalse(pf.healthy_runtime)
        self.assertFalse(pf.apply_allowed)
        self.assertFalse(pf.discard_local_changes_allowed)
        self.assertFalse(_check_map(pf)["restart"].ok)

    def test_missing_credentials_is_red_and_forbids_apply(self):
        result = _build(env=FakePreflightEnv(credentials_ready=False)).version_check()
        pf = result.preflight

        self.assertFalse(pf.healthy_runtime)
        self.assertFalse(pf.apply_allowed)
        self.assertFalse(_check_map(pf)["credentials"].ok)

    def test_in_progress_job_is_red_and_forbids_apply(self):
        store = FakeJobStore(
            UpdateJobState(stage="fetching", target_tag="v0.2.0")
        )
        result = _build(job_store=store).version_check()
        pf = result.preflight

        self.assertTrue(pf.healthy_runtime)
        self.assertFalse(pf.apply_allowed)
        self.assertFalse(pf.discard_local_changes_allowed)
        self.assertFalse(_check_map(pf)["job_idle"].ok)

    def test_dirty_tree_is_red_by_default_but_allows_discard_override(self):
        result = _build(env=FakePreflightEnv(dirty_tree=True)).version_check()
        pf = result.preflight

        self.assertTrue(pf.healthy_runtime)
        self.assertTrue(pf.dirty_tree)
        self.assertFalse(pf.apply_allowed)
        self.assertTrue(pf.discard_local_changes_allowed)
        self.assertFalse(_check_map(pf)["tree_clean"].ok)

    def test_dirty_override_unavailable_when_restart_missing(self):
        result = _build(
            env=FakePreflightEnv(restart_ready=False, dirty_tree=True)
        ).version_check()
        pf = result.preflight

        self.assertFalse(pf.healthy_runtime)
        self.assertFalse(pf.apply_allowed)
        self.assertFalse(pf.discard_local_changes_allowed)


class CredentialsProbeTest(unittest.TestCase):
    def test_public_repo_ready_without_token(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from services.release_update.preflight_env import DefaultPreflightEnvAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = DefaultPreflightEnvAdapter(Path(tmp))
            cfg = mock.Mock(repo="YKevin0x61/luyun", token=None)
            with mock.patch(
                "services.release_update.preflight_env.get_effective_config",
                return_value=cfg,
            ):
                env = adapter.inspect_env()
            self.assertTrue(env.credentials_ready)

    def test_missing_repo_is_not_ready(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from services.release_update.preflight_env import DefaultPreflightEnvAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = DefaultPreflightEnvAdapter(Path(tmp))
            cfg = mock.Mock(repo="", token=None)
            with mock.patch(
                "services.release_update.preflight_env.get_effective_config",
                return_value=cfg,
            ):
                env = adapter.inspect_env()
            self.assertFalse(env.credentials_ready)


class DirtyTreeProbeTest(unittest.TestCase):
    def test_git_status_failure_is_dirty_fail_closed(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from services.release_update.preflight_env import DefaultPreflightEnvAdapter

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            adapter = DefaultPreflightEnvAdapter(root)
            with mock.patch("services.release_update.preflight_env.subprocess.run") as run:
                run.side_effect = OSError("git missing")
                self.assertTrue(adapter.inspect_env().dirty_tree)

    def test_clean_git_tree_is_not_dirty(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from services.release_update.preflight_env import DefaultPreflightEnvAdapter

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            adapter = DefaultPreflightEnvAdapter(root)
            completed = mock.Mock(returncode=0, stdout="")
            with mock.patch(
                "services.release_update.preflight_env.subprocess.run",
                return_value=completed,
            ):
                self.assertFalse(adapter.inspect_env().dirty_tree)


class UpdatePreflightCatalogueFailureTest(unittest.TestCase):
    def test_catalogue_failure_still_returns_preflight_with_credentials_red(self):
        class BoomGitHub:
            def list_releases(self):
                raise RuntimeError("GitHub Releases API HTTP 401")

            def get_tag_commit(self, tag: str):
                raise AssertionError("should not be called")

        ru = ReleaseUpdate(
            installed=FakeInstalled(
                InstalledIdentity(
                    tag="v0.1.0",
                    degraded=False,
                    reason=None,
                    commit="abc",
                )
            ),
            github=BoomGitHub(),
            app_version="0.1.0",
            job_store=FakeJobStore(),
            oneshot=FakeOneshot(),
            preflight_env=FakePreflightEnv(credentials_ready=True),
        )

        result = ru.version_check()
        pf = result.preflight

        self.assertEqual(result.releases, [])
        self.assertIsNone(result.latest_tag)
        self.assertFalse(result.update_available)
        self.assertFalse(pf.healthy_runtime)
        self.assertFalse(pf.apply_allowed)
        self.assertFalse(_check_map(pf)["credentials"].ok)


class UpdatePreflightApplyTest(unittest.TestCase):
    def test_apply_rejected_when_preflight_forbids(self):
        oneshot = FakeOneshot()
        ru = _build(env=FakePreflightEnv(restart_ready=False), oneshot=oneshot)

        result = ru.apply("v0.2.0", peak_override=True)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "preflight")
        self.assertFalse(oneshot.started)

    def test_apply_rejected_for_dirty_tree_without_discard_confirm(self):
        oneshot = FakeOneshot()
        ru = _build(env=FakePreflightEnv(dirty_tree=True), oneshot=oneshot)

        result = ru.apply("v0.2.0", peak_override=True)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "dirty_tree")
        self.assertFalse(oneshot.started)

    def test_apply_accepted_for_dirty_tree_with_discard_confirm(self):
        oneshot = FakeOneshot()
        ru = _build(env=FakePreflightEnv(dirty_tree=True), oneshot=oneshot)

        result = ru.apply(
            "v0.2.0",
            peak_override=True,
            discard_local_changes=True,
        )

        self.assertTrue(result.accepted)
        self.assertTrue(oneshot.started)
