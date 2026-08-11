#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FileJobStateStore — Update Job state survives across reads."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.release_update import UpdateJobState
from services.release_update.job_state import FileJobStateStore


class FileJobStateStoreTest(unittest.TestCase):
    def test_write_then_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "update_job.json"
            store = FileJobStateStore(path)
            store.write(
                UpdateJobState(
                    stage="fetching",
                    target_tag="v0.2.0",
                    previous_ref="v0.1.0",
                    message="checking out",
                )
            )
            other = FileJobStateStore(path)
            got = other.read()
            self.assertEqual(got.stage, "fetching")
            self.assertEqual(got.target_tag, "v0.2.0")
            self.assertEqual(got.previous_ref, "v0.1.0")
            self.assertTrue(got.log_path)
            self.assertIn("update_job.log", got.log_path)

    def test_missing_file_is_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileJobStateStore(Path(tmp) / "missing.json")
            self.assertEqual(store.read().stage, "idle")
