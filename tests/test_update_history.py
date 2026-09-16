#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新历史：旁路 JSON、结果类型、上限淘汰与预检提示。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.release_update import UpdateJobState
from services.release_update import history as history_module
from services.release_update.history import (
    DEFAULT_HISTORY_LIMIT,
    FileUpdateHistory,
    RESULT_CANCELLED,
    RESULT_FAILED,
    RESULT_SUCCEEDED,
    RESULT_UNHEALTHY,
    read_history,
    record_state,
)


def _state(stage: str, **overrides) -> UpdateJobState:
    base = dict(
        stage=stage,
        target_tag="v0.2.0",
        previous_ref="v0.1.0",
        started_at="2026-08-10T12:00:00+08:00",
        finished_at="2026-08-10T12:01:00+08:00",
        log_path="data/update_job.log",
    )
    base.update(overrides)
    return UpdateJobState(**base)


class EntryMappingTest(unittest.TestCase):
    def test_all_outcomes_map_to_results(self):
        self.assertEqual(
            history_module.result_for_state(_state("succeeded")), RESULT_SUCCEEDED
        )
        self.assertEqual(
            history_module.result_for_state(_state("succeeded_but_unhealthy")),
            RESULT_UNHEALTHY,
        )
        self.assertEqual(history_module.result_for_state(_state("failed")), RESULT_FAILED)
        self.assertEqual(
            history_module.result_for_state(_state("failed", cancel_requested=True)),
            RESULT_CANCELLED,
        )

    def test_in_progress_has_no_history_entry(self):
        self.assertIsNone(history_module.entry_from_state(_state("restarting")))

    def test_entry_records_duration_and_rollback(self):
        entry = history_module.entry_from_state(
            _state("failed", rollback_attempted=True, rollback_ok=False)
        )
        self.assertEqual(entry["duration_seconds"], 60.0)
        self.assertTrue(entry["rolled_back"])
        self.assertFalse(entry["rollback_ok"])
        self.assertEqual(entry["previous_tag"], "v0.1.0")
        self.assertEqual(entry["log_path"], "data/update_job.log")


class HistoryFileTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_records_every_outcome_and_caps_at_limit(self):
        for index in range(DEFAULT_HISTORY_LIMIT + 5):
            record_state(
                _state(
                    "succeeded",
                    target_tag=f"v0.2.{index}",
                    finished_at=f"2026-08-10T12:{index:02d}:00+08:00",
                ),
                data_dir=self.data_dir,
            )
        entries = read_history(data_dir=self.data_dir)
        self.assertEqual(len(entries), DEFAULT_HISTORY_LIMIT)
        # Newest first
        self.assertEqual(entries[0]["target_tag"], f"v0.2.{DEFAULT_HISTORY_LIMIT + 4}")

    def test_duplicate_state_is_not_recorded_twice(self):
        state = _state("succeeded")
        record_state(state, data_dir=self.data_dir)
        record_state(state, data_dir=self.data_dir)
        self.assertEqual(len(read_history(data_dir=self.data_dir)), 1)

    def test_latest_failure_skips_successes(self):
        record_state(_state("succeeded"), data_dir=self.data_dir)
        record_state(
            _state("failed", finished_at="2026-08-11T12:01:00+08:00"),
            data_dir=self.data_dir,
        )
        record_state(
            _state("succeeded", finished_at="2026-08-12T12:01:00+08:00"),
            data_dir=self.data_dir,
        )
        failure = history_module.latest_failure(data_dir=self.data_dir)
        self.assertIsNotNone(failure)
        self.assertEqual(failure["result"], RESULT_FAILED)

    def test_history_and_job_state_live_in_separate_files(self):
        from services.release_update.job_state import job_state_path

        record_state(_state("failed"), data_dir=self.data_dir)
        self.assertNotEqual(
            history_module.history_path(self.data_dir),
            job_state_path(self.data_dir),
        )

    def test_corrupt_history_file_is_treated_as_empty(self):
        path = history_module.history_path(self.data_dir)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(read_history(data_dir=self.data_dir), [])
        record_state(_state("succeeded"), data_dir=self.data_dir)
        self.assertEqual(len(read_history(data_dir=self.data_dir)), 1)

    def test_file_update_history_port(self):
        port = FileUpdateHistory(data_dir=self.data_dir)
        port.record_state(_state("failed"))
        entries = port.read()
        self.assertEqual(len(entries), 1)
        self.assertEqual(port.latest_failure()["result"], RESULT_FAILED)
        payload = json.loads(
            history_module.history_path(self.data_dir).read_text(encoding="utf-8")
        )
        self.assertIn("entries", payload)


class PreflightHintTest(unittest.TestCase):
    def test_last_failure_is_a_non_blocking_hint(self):
        from services.release_update import (
            PREFLIGHT_LAST_UPDATE,
            PreflightEnv,
            _build_preflight,
        )

        preflight = _build_preflight(
            PreflightEnv(restart_ready=True, credentials_ready=True, dirty_tree=False),
            job_idle=True,
            last_failure={
                "target_tag": "v0.2.0",
                "result_label": "失败",
                "log_path": "data/update_job.log",
            },
        )
        hint = [c for c in preflight.checks if c.code == PREFLIGHT_LAST_UPDATE]
        self.assertEqual(len(hint), 1)
        self.assertTrue(hint[0].ok)
        self.assertIn("不阻塞", hint[0].message)
        self.assertTrue(preflight.apply_allowed)

    def test_no_hint_without_history(self):
        from services.release_update import PreflightEnv, _build_preflight

        preflight = _build_preflight(
            PreflightEnv(restart_ready=True, credentials_ready=True, dirty_tree=False),
            job_idle=True,
        )
        codes = [c.code for c in preflight.checks]
        self.assertNotIn("last_update", codes)


if __name__ == "__main__":
    unittest.main()
