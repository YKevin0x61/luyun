#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release Manifest adapter — installed identity from RELEASE_MANIFEST.json."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.release_update.manifest_identity import ReleaseManifestAdapter


class ManifestIdentityTest(unittest.TestCase):
    def test_reads_tag_and_commit_from_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "RELEASE_MANIFEST.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "tag": "v0.1.0",
                        "app_version": "0.1.0",
                        "commit": "abc123def",
                        "requirements_fingerprint": "sha256:deadbeef",
                    }
                ),
                encoding="utf-8",
            )
            identity = ReleaseManifestAdapter(root).inspect_installed()

        self.assertEqual(identity.tag, "v0.1.0")
        self.assertEqual(identity.commit, "abc123def")
        self.assertFalse(identity.degraded)
        self.assertIsNone(identity.reason)

    def test_missing_manifest_is_degraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = ReleaseManifestAdapter(Path(tmp)).inspect_installed()

        self.assertIsNone(identity.tag)
        self.assertTrue(identity.degraded)
        self.assertEqual(identity.reason, "missing_manifest")

    def test_invalid_manifest_is_degraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "RELEASE_MANIFEST.json").write_text("{not-json", encoding="utf-8")
            identity = ReleaseManifestAdapter(root).inspect_installed()

        self.assertTrue(identity.degraded)
        self.assertEqual(identity.reason, "invalid_manifest")
        self.assertIsNone(identity.tag)

    def test_manifest_missing_commit_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "RELEASE_MANIFEST.json").write_text(
                json.dumps({"schema_version": 1, "tag": "v0.1.0"}),
                encoding="utf-8",
            )
            identity = ReleaseManifestAdapter(root).inspect_installed()

        self.assertEqual(identity.tag, "v0.1.0")
        self.assertTrue(identity.degraded)
        self.assertEqual(identity.reason, "invalid_manifest")
