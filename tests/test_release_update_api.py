#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Admin API — Release Update Version Check / Apply Update / job status."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.release_update import get_release_update, router as release_update_router
from api.security import require_session, verify_admin_token
from config import settings
from database import DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.release_update import (
    ApplyResult,
    FormalRelease,
    PreflightCheck,
    ReleaseUpdate,
    UpdateJobState,
    UpdatePreflight,
    VersionCheckResult,
)


def _run(coro):
    return asyncio.run(coro)


def _sample_preflight(**overrides) -> UpdatePreflight:
    base = dict(
        checks=[
            PreflightCheck(code="restart", ok=True, message="重启能力可用"),
            PreflightCheck(code="credentials", ok=True, message="Releases 凭据可用"),
            PreflightCheck(code="job_idle", ok=True, message="当前没有进行中的更新作业"),
            PreflightCheck(code="tree_clean", ok=True, message="部署目录干净"),
        ],
        healthy_runtime=True,
        apply_allowed=True,
        dirty_tree=False,
        discard_local_changes_allowed=False,
    )
    base.update(overrides)
    return UpdatePreflight(**base)


def _sample_result(**preflight_overrides) -> VersionCheckResult:
    return VersionCheckResult(
        installed_tag="v0.1.0",
        degraded=False,
        degraded_reason=None,
        app_version="0.1.0",
        releases=[
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
        ],
        latest_tag="v0.2.0",
        update_available=True,
        preflight=_sample_preflight(**preflight_overrides),
    )


class ReleaseUpdateApiTest(unittest.TestCase):
    def setUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        _run(self.db.connect())
        set_runtime(AppRuntime(db=self.db))

        self.app = FastAPI()
        self.app.include_router(release_update_router)
        self.app.dependency_overrides[verify_admin_token] = lambda: True
        self.app.dependency_overrides[require_session] = lambda: "test-session"

    def tearDown(self):
        self.app.dependency_overrides.clear()
        _run(self.db.close())
        set_runtime(None)
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def test_version_check_returns_payload_without_starting_job(self):
        fake = mock.Mock(spec=ReleaseUpdate)
        fake.version_check.return_value = _sample_result()
        self.app.dependency_overrides[get_release_update] = lambda: fake

        with TestClient(self.app) as client:
            resp = client.get("/api/release-update/version-check")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["installed_tag"], "v0.1.0")
        self.assertFalse(body["degraded"])
        self.assertEqual(body["latest_tag"], "v0.2.0")
        self.assertTrue(body["update_available"])
        self.assertEqual(
            [r["tag"] for r in body["releases"]],
            ["v0.2.0", "v0.1.0"],
        )
        pf = body["preflight"]
        self.assertTrue(pf["healthy_runtime"])
        self.assertTrue(pf["apply_allowed"])
        self.assertFalse(pf["dirty_tree"])
        self.assertFalse(pf["discard_local_changes_allowed"])
        self.assertEqual(
            [c["code"] for c in pf["checks"]],
            ["restart", "credentials", "job_idle", "tree_clean"],
        )
        fake.version_check.assert_called_once_with()

    def test_version_check_exposes_dirty_preflight_for_admin_ui(self):
        fake = mock.Mock(spec=ReleaseUpdate)
        fake.version_check.return_value = _sample_result(
            apply_allowed=False,
            dirty_tree=True,
            discard_local_changes_allowed=True,
            checks=[
                PreflightCheck(code="restart", ok=True, message="重启能力可用"),
                PreflightCheck(code="credentials", ok=True, message="Releases 凭据可用"),
                PreflightCheck(code="job_idle", ok=True, message="当前没有进行中的更新作业"),
                PreflightCheck(
                    code="tree_clean",
                    ok=False,
                    message="部署目录有本地改动；默认禁止应用更新，需明确确认丢弃后才可继续",
                ),
            ],
        )
        self.app.dependency_overrides[get_release_update] = lambda: fake

        with TestClient(self.app) as client:
            resp = client.get("/api/release-update/version-check")

        self.assertEqual(resp.status_code, 200)
        pf = resp.json()["preflight"]
        self.assertFalse(pf["apply_allowed"])
        self.assertTrue(pf["dirty_tree"])
        self.assertTrue(pf["discard_local_changes_allowed"])
        self.assertFalse(pf["checks"][3]["ok"])

    def test_version_check_requires_admin_auth(self):
        self.app.dependency_overrides.clear()
        # Uninitialized auth + TestClient host → 401 (no localhost bypass).
        with TestClient(self.app) as client:
            resp = client.get("/api/release-update/version-check")
        self.assertEqual(resp.status_code, 401)

    def test_apply_accepts_target_and_returns_queued_job(self):
        job = UpdateJobState(
            stage="queued",
            target_tag="v0.2.0",
            previous_ref="v0.1.0",
            message="Update Job queued",
            log_path="data/update_job.log",
        )
        fake = mock.Mock(spec=ReleaseUpdate)
        fake.apply.return_value = ApplyResult(accepted=True, job=job)
        self.app.dependency_overrides[get_release_update] = lambda: fake

        with TestClient(self.app) as client:
            resp = client.post(
                "/api/release-update/apply",
                json={"target_tag": "v0.2.0", "peak_override": False},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["accepted"])
        self.assertEqual(body["job"]["stage"], "queued")
        self.assertEqual(body["job"]["target_tag"], "v0.2.0")
        fake.apply.assert_called_once_with(
            "v0.2.0",
            peak_override=False,
            discard_local_changes=False,
        )

    def test_apply_forwards_discard_local_changes(self):
        job = UpdateJobState(stage="queued", target_tag="v0.2.0")
        fake = mock.Mock(spec=ReleaseUpdate)
        fake.apply.return_value = ApplyResult(accepted=True, job=job)
        self.app.dependency_overrides[get_release_update] = lambda: fake

        with TestClient(self.app) as client:
            resp = client.post(
                "/api/release-update/apply",
                json={
                    "target_tag": "v0.2.0",
                    "peak_override": True,
                    "discard_local_changes": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        fake.apply.assert_called_once_with(
            "v0.2.0",
            peak_override=True,
            discard_local_changes=True,
        )

    def test_apply_preflight_failure_returns_409(self):
        fake = mock.Mock(spec=ReleaseUpdate)
        fake.apply.return_value = ApplyResult(accepted=False, reason="preflight")
        self.app.dependency_overrides[get_release_update] = lambda: fake

        with TestClient(self.app) as client:
            resp = client.post(
                "/api/release-update/apply",
                json={"target_tag": "v0.2.0", "peak_override": True},
            )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"]["reason"], "preflight")

    def test_apply_dirty_tree_returns_409(self):
        fake = mock.Mock(spec=ReleaseUpdate)
        fake.apply.return_value = ApplyResult(accepted=False, reason="dirty_tree")
        self.app.dependency_overrides[get_release_update] = lambda: fake

        with TestClient(self.app) as client:
            resp = client.post(
                "/api/release-update/apply",
                json={"target_tag": "v0.2.0", "peak_override": True},
            )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"]["reason"], "dirty_tree")

    def test_apply_busy_returns_409(self):
        busy = UpdateJobState(stage="fetching_bundle", target_tag="v0.2.0")
        fake = mock.Mock(spec=ReleaseUpdate)
        fake.apply.return_value = ApplyResult(
            accepted=False, reason="busy", job=busy
        )
        self.app.dependency_overrides[get_release_update] = lambda: fake

        with TestClient(self.app) as client:
            resp = client.post(
                "/api/release-update/apply",
                json={"target_tag": "v0.2.0", "peak_override": True},
            )

        self.assertEqual(resp.status_code, 409)
        self.assertIn("进行中", resp.json()["detail"])

    def test_apply_peak_hours_returns_409_with_reason(self):
        fake = mock.Mock(spec=ReleaseUpdate)
        fake.apply.return_value = ApplyResult(
            accepted=False, reason="peak_hours"
        )
        self.app.dependency_overrides[get_release_update] = lambda: fake

        with TestClient(self.app) as client:
            resp = client.post(
                "/api/release-update/apply",
                json={"target_tag": "v0.2.0", "peak_override": False},
            )

        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertEqual(body["detail"]["reason"], "peak_hours")

    def test_apply_requires_session(self):
        self.app.dependency_overrides.pop(require_session, None)
        # Keep admin token override so we isolate session harden.
        fake = mock.Mock(spec=ReleaseUpdate)
        fake.apply.return_value = ApplyResult(accepted=True, job=UpdateJobState(stage="queued"))
        self.app.dependency_overrides[get_release_update] = lambda: fake

        with TestClient(self.app) as client:
            resp = client.post(
                "/api/release-update/apply",
                json={"target_tag": "v0.2.0", "peak_override": True},
            )
        self.assertEqual(resp.status_code, 401)

    def test_job_status_returns_state_and_log_pointer(self):
        fake = mock.Mock(spec=ReleaseUpdate)
        fake.job_status.return_value = UpdateJobState(
            stage="syncing_deps",
            target_tag="v0.2.0",
            previous_ref="v0.1.0",
            message="pip install",
            log_path="data/update_job.log",
        )
        self.app.dependency_overrides[get_release_update] = lambda: fake

        with TestClient(self.app) as client:
            resp = client.get("/api/release-update/job")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["job"]["stage"], "syncing_deps")
        self.assertEqual(body["job"]["log_path"], "data/update_job.log")
        fake.job_status.assert_called_once_with()

    def test_job_status_exposes_bundle_install_stage(self):
        fake = mock.Mock(spec=ReleaseUpdate)
        fake.job_status.return_value = UpdateJobState(
            stage="installing",
            target_tag="v0.1.0",
            previous_ref="v0.2.0",
            message="atomic switch",
            log_path="data/update_job.log",
        )
        self.app.dependency_overrides[get_release_update] = lambda: fake

        with TestClient(self.app) as client:
            resp = client.get("/api/release-update/job")

        self.assertEqual(resp.status_code, 200)
        job = resp.json()["job"]
        self.assertEqual(job["stage"], "installing")
        self.assertEqual(job["target_tag"], "v0.1.0")
        self.assertEqual(job["previous_ref"], "v0.2.0")
        self.assertEqual(job["log_path"], "data/update_job.log")
