#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release Bundle install adapter: checksum gate + preserve data + restore."""

from __future__ import annotations

import json
import tarfile
import unittest
from pathlib import Path

from services.release_update.job_adapters import (
    BUNDLE_ASSET,
    CHECKSUMS_ASSET,
    ReleaseBundleInstallAdapter,
    sha256_file,
    verify_bundle_checksum,
)


def _write_manifest(root: Path, *, tag: str, fingerprint: str) -> None:
    payload = {
        "schema_version": 1,
        "tag": tag,
        "app_version": tag.lstrip("v"),
        "commit": "a" * 40,
        "requirements_fingerprint": fingerprint,
        "artifacts": {"bundle": BUNDLE_ASSET, "checksums": CHECKSUMS_ASSET},
    }
    (root / "RELEASE_MANIFEST.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _write_bundle_tree(root: Path, *, tag: str, fingerprint: str, marker: str) -> None:
    (root / "admin-web" / "dist").mkdir(parents=True)
    (root / "public" / "kds" / "assets").mkdir(parents=True)
    (root / "admin-web" / "dist" / "index.html").write_text("admin\n", encoding="utf-8")
    (root / "public" / "kds" / "index.html").write_text("kds\n", encoding="utf-8")
    # Empty dirs are omitted by tar; keep a placeholder so assets/ is packaged.
    (root / "public" / "kds" / "assets" / ".keep").write_text("", encoding="utf-8")
    (root / "requirements.txt").write_text("fastapi==0.1.0\n", encoding="utf-8")
    (root / "APP_MARKER").write_text(marker + "\n", encoding="utf-8")
    _write_manifest(root, tag=tag, fingerprint=fingerprint)


def _make_bundle_archive(tmp: Path, *, tag: str, fingerprint: str, marker: str) -> Path:
    tree = tmp / "tree"
    tree.mkdir()
    _write_bundle_tree(tree, tag=tag, fingerprint=fingerprint, marker=marker)
    archive = tmp / BUNDLE_ASSET
    with tarfile.open(archive, "w:gz") as tar:
        for path in tree.rglob("*"):
            if path.is_file():
                tar.add(path, arcname=str(path.relative_to(tree)))
    return archive


class VerifyBundleChecksumTest(unittest.TestCase):
    def test_mismatch_hard_fails(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bundle = root / BUNDLE_ASSET
            bundle.write_bytes(b"hello-bundle")
            sums = root / CHECKSUMS_ASSET
            sums.write_text(f"{'0' * 64}  {BUNDLE_ASSET}\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                verify_bundle_checksum(bundle, sums)

    def test_missing_entry_hard_fails(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bundle = root / BUNDLE_ASSET
            bundle.write_bytes(b"hello-bundle")
            sums = root / CHECKSUMS_ASSET
            sums.write_text(f"{'a' * 64}  other.tar.gz\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "missing digest"):
                verify_bundle_checksum(bundle, sums)

    def test_matching_digest_ok(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bundle = root / BUNDLE_ASSET
            bundle.write_bytes(b"hello-bundle")
            digest = sha256_file(bundle)
            sums = root / CHECKSUMS_ASSET
            sums.write_text(f"{digest}  {BUNDLE_ASSET}\n", encoding="utf-8")
            verify_bundle_checksum(bundle, sums)  # does not raise


class ReleaseBundleInstallAdapterTest(unittest.TestCase):
    def test_activate_failure_before_switch_leaves_live_data(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            live = base / "luyun"
            live.mkdir()
            _write_bundle_tree(
                live, tag="v0.1.0", fingerprint="sha256:old", marker="old-app"
            )
            (live / "data").mkdir()
            (live / "data" / "shop.db").write_text("precious\n", encoding="utf-8")

            staging = base / "stage"
            staging.mkdir()
            # Incomplete archive: missing public/kds/assets → assert fails pre-switch.
            tree = staging / "tree"
            tree.mkdir()
            (tree / "admin-web" / "dist").mkdir(parents=True)
            (tree / "public" / "kds").mkdir(parents=True)
            (tree / "admin-web" / "dist" / "index.html").write_text("a\n", encoding="utf-8")
            (tree / "public" / "kds" / "index.html").write_text("k\n", encoding="utf-8")
            (tree / "requirements.txt").write_text("x\n", encoding="utf-8")
            _write_manifest(tree, tag="v0.2.0", fingerprint="sha256:new")
            archive = staging / BUNDLE_ASSET
            with tarfile.open(archive, "w:gz") as tar:
                for path in tree.rglob("*"):
                    if path.is_file():
                        tar.add(path, arcname=str(path.relative_to(tree)))

            adapter = ReleaseBundleInstallAdapter(
                live, github_repo="owner/repo", token=None
            )
            adapter._verified_bundle = archive
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                adapter.activate_bundle("v0.2.0")
            self.assertEqual(
                (live / "data" / "shop.db").read_text(encoding="utf-8"), "precious\n"
            )
            self.assertEqual((live / "APP_MARKER").read_text(encoding="utf-8"), "old-app\n")
            self.assertFalse(live.with_name(live.name + ".prev").exists())

    def test_activate_preserves_data_and_restore_brings_previous_tree(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            live = base / "luyun"
            live.mkdir()
            _write_bundle_tree(
                live, tag="v0.1.0", fingerprint="sha256:old", marker="old-app"
            )
            (live / "data").mkdir()
            (live / "data" / "shop.db").write_text("precious\n", encoding="utf-8")
            (live / "deploy").mkdir()
            (live / "deploy" / "env.production").write_text("TOKEN=keep\n", encoding="utf-8")

            staging = base / "stage"
            staging.mkdir()
            archive = _make_bundle_archive(
                staging, tag="v0.2.0", fingerprint="sha256:new", marker="new-app"
            )

            adapter = ReleaseBundleInstallAdapter(
                live, github_repo="owner/repo", token=None
            )
            # Inject a verified bundle without hitting GitHub.
            adapter._verified_bundle = archive

            result = adapter.activate_bundle("v0.2.0")
            self.assertEqual(result.previous_requirements_fingerprint, "sha256:old")
            self.assertEqual(result.requirements_fingerprint, "sha256:new")
            self.assertEqual((live / "APP_MARKER").read_text(encoding="utf-8"), "new-app\n")
            self.assertEqual(
                (live / "data" / "shop.db").read_text(encoding="utf-8"), "precious\n"
            )
            self.assertEqual(
                (live / "deploy" / "env.production").read_text(encoding="utf-8"),
                "TOKEN=keep\n",
            )
            prev = live.with_name(live.name + ".prev")
            self.assertTrue(prev.is_dir())
            self.assertEqual((prev / "APP_MARKER").read_text(encoding="utf-8"), "old-app\n")
            # Shop data must not linger only on the previous tree after swap.
            self.assertFalse((prev / "data").exists())

            adapter.restore_previous_tree()
            self.assertEqual((live / "APP_MARKER").read_text(encoding="utf-8"), "old-app\n")
            self.assertEqual(
                (live / "data" / "shop.db").read_text(encoding="utf-8"), "precious\n"
            )
            self.assertFalse(prev.exists())
