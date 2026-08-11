#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitIdentityAdapter — subprocess boundary tests (no real git required)."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from services.release_update.git_identity import GitIdentityAdapter


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=""
    )


class GitIdentityAdapterTest(unittest.TestCase):
    def setUp(self):
        self.adapter = GitIdentityAdapter(Path("/tmp/fake-repo"))

    def test_exact_clean_tag(self):
        def fake_run(args, **kwargs):
            cmd = args[1]
            if cmd == "rev-parse":
                return _completed("abc123\n")
            if cmd == "status":
                return _completed("")
            if cmd == "describe":
                return _completed("v0.1.0\n")
            return _completed(returncode=1)

        with mock.patch("subprocess.run", side_effect=fake_run):
            identity = self.adapter.inspect_installed()

        self.assertEqual(identity.tag, "v0.1.0")
        self.assertFalse(identity.degraded)
        self.assertEqual(identity.commit, "abc123")

    def test_not_on_tag_is_degraded(self):
        def fake_run(args, **kwargs):
            cmd = args[1]
            if cmd == "rev-parse":
                return _completed("deadbeef\n")
            if cmd == "status":
                return _completed("")
            if cmd == "describe":
                return _completed(returncode=1)
            return _completed(returncode=1)

        with mock.patch("subprocess.run", side_effect=fake_run):
            identity = self.adapter.inspect_installed()

        self.assertIsNone(identity.tag)
        self.assertTrue(identity.degraded)
        self.assertEqual(identity.reason, "not_on_tag")

    def test_dirty_on_exact_tag_is_degraded(self):
        def fake_run(args, **kwargs):
            cmd = args[1]
            if cmd == "rev-parse":
                return _completed("abc123\n")
            if cmd == "status":
                return _completed(" M README.md\n")
            if cmd == "describe":
                return _completed("v0.1.0\n")
            return _completed(returncode=1)

        with mock.patch("subprocess.run", side_effect=fake_run):
            identity = self.adapter.inspect_installed()

        self.assertEqual(identity.tag, "v0.1.0")
        self.assertTrue(identity.degraded)
        self.assertEqual(identity.reason, "dirty")

    def test_dirty_and_not_on_tag_reports_not_on_tag(self):
        def fake_run(args, **kwargs):
            cmd = args[1]
            if cmd == "rev-parse":
                return _completed("deadbeef\n")
            if cmd == "status":
                return _completed(" M README.md\n")
            if cmd == "describe":
                return _completed(returncode=1)
            return _completed(returncode=1)

        with mock.patch("subprocess.run", side_effect=fake_run):
            identity = self.adapter.inspect_installed()

        self.assertIsNone(identity.tag)
        self.assertEqual(identity.reason, "not_on_tag")
