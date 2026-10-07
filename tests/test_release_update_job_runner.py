#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release Update — Update Job runner seam tests (faked adapters)."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import List, Optional

from services.release_update import UpdateJobState
from services.release_update.job_runner import (
    BundleInstallResult,
    MigrationApplyOutcome,
    UpdateJobRunner,
)


@dataclass
class FakeJobStore:
    state: UpdateJobState
    writes: List[UpdateJobState] = field(default_factory=list)

    def read(self) -> UpdateJobState:
        return self.state

    def write(self, state: UpdateJobState) -> None:
        self.writes.append(state)
        self.state = state


@dataclass
class FakeBackup:
    fail: bool = False
    called: bool = False

    def run_backup(self) -> str:
        self.called = True
        if self.fail:
            raise RuntimeError("disk full")
        return "20260810_120000"


@dataclass
class FakeBundle:
    """BundleInstallPort fake: download/verify, activate (atomic switch), restore."""

    fetched_tag: Optional[str] = None
    activated_tag: Optional[str] = None
    restored: bool = False
    left_previous: bool = False
    fail_checksum: bool = False
    fail_activate: bool = False
    previous_fp: Optional[str] = "sha256:old"
    new_fp: Optional[str] = "sha256:new"

    def fetch_bundle(self, tag: str) -> None:
        if self.fail_checksum:
            raise RuntimeError("checksum mismatch for luyun-release-bundle.tar.gz")
        self.fetched_tag = tag

    def activate_bundle(self, tag: str) -> BundleInstallResult:
        # On failure the live tree must remain unchanged (no left_previous).
        if self.fail_activate:
            raise RuntimeError("atomic switch failed")
        self.left_previous = True
        self.activated_tag = tag
        return BundleInstallResult(
            requirements_fingerprint=self.new_fp,
            previous_requirements_fingerprint=self.previous_fp,
        )

    def restore_previous_tree(self) -> None:
        self.restored = True


@dataclass
class FakeDeps:
    synced: bool = False
    fail: bool = False
    cancel: bool = False

    def sync(self) -> None:
        if self.cancel:
            raise RuntimeError("cancelled by operator")
        if self.fail:
            raise RuntimeError("pip failed")
        self.synced = True


@dataclass
class FakeService:
    restarts: int = 0

    def restart(self) -> None:
        self.restarts += 1


@dataclass
class FakeBrowser:
    synced: bool = False

    def sync(self) -> None:
        self.synced = True


@dataclass
class FakeMigrations:
    """DatabaseMigrationsPort fake：待应用版本 / 无待应用 / 应用失败三条路径。"""

    applied: tuple = ()
    note: str = ""
    fail: bool = False
    calls: int = 0

    def apply_pending(self) -> MigrationApplyOutcome:
        self.calls += 1
        if self.fail:
            raise RuntimeError(
                '0005_scheduling.sql: relation "staff_shifts" already exists'
            )
        return MigrationApplyOutcome(applied=self.applied, note=self.note)


def _queued(target: str = "v0.2.0", previous: str = "v0.1.0") -> UpdateJobState:
    return UpdateJobState(
        stage="queued",
        target_tag=target,
        previous_ref=previous,
        message="Update Job queued",
        log_path="data/update_job.log",
    )


def _runner(
    store: FakeJobStore,
    *,
    backup: Optional[FakeBackup] = None,
    bundle: Optional[FakeBundle] = None,
    deps: Optional[FakeDeps] = None,
    service: Optional[FakeService] = None,
    browser: Optional[FakeBrowser] = None,
    migrations: Optional[FakeMigrations] = None,
    is_cancelled=None,
) -> tuple[UpdateJobRunner, FakeBackup, FakeBundle, FakeDeps, FakeService]:
    b = backup or FakeBackup()
    bund = bundle or FakeBundle()
    d = deps or FakeDeps()
    s = service or FakeService()
    m = migrations or FakeMigrations()
    runner = UpdateJobRunner(
        job_store=store,
        backup=b,
        bundle=bund,
        deps=d,
        service=s,
        browser=browser,
        migrations=m,
        is_cancelled=is_cancelled,
    )
    return runner, b, bund, d, s


class UpdateJobRunnerTest(unittest.TestCase):
    def test_backup_failure_aborts_without_touching_live_tree(self):
        store = FakeJobStore(_queued())
        runner, backup, bundle, deps, service = _runner(
            store, backup=FakeBackup(fail=True)
        )

        final = runner.run()

        self.assertTrue(backup.called)
        self.assertIsNone(bundle.fetched_tag)
        self.assertIsNone(bundle.activated_tag)
        self.assertFalse(bundle.left_previous)
        self.assertFalse(deps.synced)
        self.assertEqual(service.restarts, 0)
        self.assertEqual(final.stage, "failed")
        self.assertIn("backup", (final.error or "").lower())
        stages = [w.stage for w in store.writes]
        self.assertIn("backing_up", stages)
        self.assertNotIn("fetching_bundle", stages)
        self.assertNotIn("installing", stages)

    def test_checksum_failure_aborts_before_activation(self):
        store = FakeJobStore(_queued())
        runner, backup, bundle, deps, service = _runner(
            store, bundle=FakeBundle(fail_checksum=True)
        )

        final = runner.run()

        self.assertTrue(backup.called)
        self.assertIsNone(bundle.activated_tag)
        self.assertFalse(bundle.left_previous)
        self.assertFalse(bundle.restored)
        self.assertFalse(deps.synced)
        self.assertEqual(service.restarts, 0)
        self.assertEqual(final.stage, "failed")
        self.assertIn("checksum", (final.error or "").lower())
        self.assertFalse(final.rollback_attempted)
        stages = [w.stage for w in store.writes]
        self.assertIn("fetching_bundle", stages)
        self.assertNotIn("installing", stages)

    def test_happy_path_reaches_succeeded(self):
        store = FakeJobStore(_queued())
        runner, backup, bundle, deps, service = _runner(store)

        final = runner.run()

        self.assertTrue(backup.called)
        self.assertEqual(bundle.fetched_tag, "v0.2.0")
        self.assertEqual(bundle.activated_tag, "v0.2.0")
        self.assertTrue(deps.synced)
        self.assertEqual(service.restarts, 1)
        # The job never declares success itself: the restarted service must prove
        # ready first (ReleaseUpdate.confirm_health).
        self.assertEqual(final.stage, "restarting")
        self.assertEqual(final.snapshot_ts, "20260810_120000")
        self.assertIsNotNone(final.restart_requested_at)
        self.assertFalse(final.rollback_attempted)
        stages = [w.stage for w in store.writes]
        self.assertEqual(
            stages,
            [
                "backing_up",
                "fetching_bundle",
                "installing",
                "syncing_deps",
                # 迁移阶段写两次：进入阶段 + 结果（有迁移 / 无迁移都在这里落定）
                "applying_migrations",
                "applying_migrations",
                "restarting",
            ],
        )

    def test_persists_awaiting_health_before_restart_so_docker_self_kill_is_ok(self):
        """Docker restart kills the in-container job; the switched+restarting fact
        must be on disk first so the Admin can still confirm health afterwards."""
        store = FakeJobStore(_queued())
        stage_at_restart: list[str] = []

        @dataclass
        class ProbeService:
            restarts: int = 0

            def restart(self) -> None:
                self.restarts += 1
                stage_at_restart.append(store.state.stage)

        probe = ProbeService()
        runner, _, _, _, _ = _runner(store, service=probe)

        final = runner.run()

        self.assertEqual(stage_at_restart, ["restarting"])
        self.assertEqual(final.stage, "restarting")
        self.assertEqual(probe.restarts, 1)

    def test_deps_skipped_when_requirements_fingerprint_unchanged(self):
        store = FakeJobStore(_queued())
        runner, _, _, deps, service = _runner(
            store,
            bundle=FakeBundle(previous_fp="sha256:same", new_fp="sha256:same"),
        )

        final = runner.run()

        self.assertEqual(final.stage, "restarting")
        self.assertFalse(deps.synced)
        self.assertEqual(service.restarts, 1)
        stages = [w.stage for w in store.writes]
        self.assertIn("syncing_deps", stages)

    def test_browser_sync_runs_without_changing_stage_sequence(self):
        store = FakeJobStore(_queued())
        browser = FakeBrowser()
        runner, _, _, _, _ = _runner(store, browser=browser)

        runner.run()

        self.assertTrue(browser.synced)
        self.assertEqual(
            [w.stage for w in store.writes],
            [
                "backing_up",
                "fetching_bundle",
                "installing",
                "syncing_deps",
                # 迁移阶段写两次：进入阶段 + 结果（有迁移 / 无迁移都在这里落定）
                "applying_migrations",
                "applying_migrations",
                "restarting",
            ],
        )

    def test_browser_sync_runs_even_when_deps_are_skipped(self):
        """指纹没变也要查浏览器：手动删过浏览器 / 换过镜像同样是缺失。"""
        store = FakeJobStore(_queued())
        browser = FakeBrowser()
        runner, _, _, deps, _ = _runner(
            store,
            bundle=FakeBundle(previous_fp="sha256:same", new_fp="sha256:same"),
            browser=browser,
        )

        runner.run()

        self.assertFalse(deps.synced)
        self.assertTrue(browser.synced)

    def test_activate_failure_aborts_without_rollback(self):
        store = FakeJobStore(_queued())
        runner, _, bundle, deps, service = _runner(
            store,
            bundle=FakeBundle(fail_activate=True),
        )

        final = runner.run()

        self.assertFalse(bundle.left_previous)
        self.assertFalse(bundle.restored)
        self.assertFalse(deps.synced)
        self.assertEqual(service.restarts, 0)
        self.assertEqual(final.stage, "failed")
        self.assertFalse(final.rollback_attempted)
        self.assertIn("atomic switch", (final.error or "").lower())

    def test_deps_run_when_requirements_fingerprint_changed(self):
        store = FakeJobStore(_queued())
        runner, _, _, deps, _ = _runner(
            store,
            bundle=FakeBundle(previous_fp="sha256:old", new_fp="sha256:new"),
        )

        final = runner.run()

        self.assertEqual(final.stage, "restarting")
        self.assertTrue(deps.synced)

    def test_failure_after_leaving_tree_restores_previous(self):
        store = FakeJobStore(_queued())
        runner, _, bundle, deps, service = _runner(
            store,
            deps=FakeDeps(fail=True),
        )

        final = runner.run()

        self.assertTrue(bundle.left_previous)
        self.assertEqual(bundle.activated_tag, "v0.2.0")
        self.assertTrue(bundle.restored)
        self.assertGreaterEqual(service.restarts, 1)
        self.assertEqual(final.stage, "failed")
        self.assertTrue(final.rollback_attempted)
        self.assertTrue(final.rollback_ok)
        self.assertIsNotNone(final.error)
        self.assertIn("log=", final.error or "")
        self.assertEqual(final.previous_ref, "v0.1.0")
        self.assertIsNotNone(final.log_path)

    def test_cancel_during_deps_rolls_back_previous_tree(self):
        store = FakeJobStore(_queued())
        runner, _, bundle, deps, service = _runner(
            store,
            deps=FakeDeps(cancel=True),
        )

        final = runner.run()

        self.assertTrue(bundle.left_previous)
        self.assertTrue(bundle.restored)
        self.assertGreaterEqual(service.restarts, 1)
        self.assertEqual(final.stage, "failed")
        self.assertTrue(final.rollback_attempted)
        self.assertIn("cancelled by operator", final.error or "")
        self.assertIn("cancelled", (final.message or "").lower())

    def test_cancel_before_leaving_tree_skips_rollback(self):
        cancelled = {"v": False}

        class CancelAfterBackup(FakeBackup):
            def run_backup(self) -> str:
                cancelled["v"] = True
                return super().run_backup()

        store = FakeJobStore(_queued())
        runner, _, bundle, deps, service = _runner(
            store,
            backup=CancelAfterBackup(),
            is_cancelled=lambda: cancelled["v"],
        )

        final = runner.run()

        self.assertFalse(bundle.left_previous)
        self.assertIsNone(bundle.fetched_tag)
        self.assertFalse(bundle.restored)
        self.assertFalse(deps.synced)
        self.assertEqual(service.restarts, 0)
        self.assertEqual(final.stage, "failed")
        self.assertFalse(final.rollback_attempted)
        self.assertIn("cancelled by operator", final.error or "")

    # ── 应用待执行迁移：有迁移 / 无迁移 / 迁移失败（票 09） ──

    def test_applies_pending_migrations_after_install_and_before_restart(self):
        """阶段位置：installing（已切到新树）之后、restarting 之前。

        迁移文件随新发行包下发，所以这一阶段必须在 ``activate_bundle`` 之后跑——
        用例用探针钉住「切换已经发生」，而不只是看阶段名的先后。
        """
        store = FakeJobStore(_queued())
        bundle = FakeBundle()
        seen: dict = {}

        class ProbeMigrations(FakeMigrations):
            def apply_pending(self) -> MigrationApplyOutcome:
                seen["left_previous"] = bundle.left_previous
                return super().apply_pending()

        migrations = ProbeMigrations(applied=("0005", "0006"))
        runner, _, _, _, service = _runner(store, bundle=bundle, migrations=migrations)

        final = runner.run()

        self.assertTrue(seen["left_previous"], "迁移必须跑在原子切换之后（读的是新树的迁移文件）")
        self.assertEqual(
            [w.stage for w in store.writes],
            [
                "backing_up",
                "fetching_bundle",
                "installing",
                "syncing_deps",
                "applying_migrations",
                "applying_migrations",
                "restarting",
            ],
        )
        messages = [w.message for w in store.writes if w.stage == "applying_migrations"]
        self.assertTrue(any("0005" in m and "0006" in m for m in messages), messages)
        self.assertEqual(final.stage, "restarting")
        self.assertIsNone(final.error)
        self.assertEqual(service.restarts, 1)

    def test_no_pending_migrations_skips_stage_without_failing_the_job(self):
        store = FakeJobStore(_queued())
        migrations = FakeMigrations(applied=())
        runner, _, bundle, deps, service = _runner(store, migrations=migrations)

        final = runner.run()

        self.assertEqual(migrations.calls, 1)
        self.assertTrue(deps.synced)
        self.assertFalse(bundle.restored)
        self.assertFalse(final.rollback_attempted)
        self.assertIsNone(final.error)
        self.assertEqual(final.stage, "restarting")
        self.assertEqual(service.restarts, 1)
        messages = [w.message for w in store.writes if w.stage == "applying_migrations"]
        self.assertTrue(any("跳过" in m for m in messages), messages)

    def test_migration_failure_fails_job_and_rolls_back_previous_tree(self):
        store = FakeJobStore(_queued())
        migrations = FakeMigrations(fail=True)
        runner, _, bundle, _, service = _runner(store, migrations=migrations)

        final = runner.run()

        self.assertEqual(migrations.calls, 1)
        self.assertTrue(bundle.restored, "迁移失败要按现有机制回滚代码树")
        self.assertEqual(final.stage, "failed")
        self.assertTrue(final.rollback_attempted)
        self.assertTrue(final.rollback_ok)
        self.assertIn("0005_scheduling.sql", final.error or "")
        self.assertIn("log=", final.error or "")
        self.assertGreaterEqual(service.restarts, 1)
        self.assertIn("applying_migrations", [w.stage for w in store.writes])
