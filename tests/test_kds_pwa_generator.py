#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KDS PWA generator contract tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.generate_kds_pwa import generate_kds_pwa


def _seed_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "static" / "pwa").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<html><head><!--preload-links--></head><body></body></html>",
        encoding="utf-8",
    )
    (dist / "assets" / "index.js").write_text("console.log('v1')", encoding="utf-8")
    for name in (
        "icon-192.png",
        "icon-512.png",
        "icon-maskable-512.png",
    ):
        (dist / "static" / "pwa" / name).write_bytes(b"png")
    return dist


class KdsPwaGeneratorTest(unittest.TestCase):
    def test_generates_manifest_and_versioned_service_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = _seed_dist(Path(tmp))
            result = generate_kds_pwa(dist, "0.5.17")

            index = (dist / "index.html").read_text(encoding="utf-8")
            self.assertEqual(index.count('rel="manifest"'), 1)
            self.assertIn('href="/kds/manifest.webmanifest"', index)
            self.assertTrue((dist / "manifest.webmanifest").is_file())
            self.assertTrue((dist / "sw.js").is_file())
            self.assertIn("luyun-kds-shell-0.5.17-", result["cache_name"])
            self.assertGreaterEqual(result["precache_count"], 6)

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = _seed_dist(Path(tmp))
            first = generate_kds_pwa(dist, "0.5.17")
            first_sw = (dist / "sw.js").read_text(encoding="utf-8")
            second = generate_kds_pwa(dist, "0.5.17")
            second_sw = (dist / "sw.js").read_text(encoding="utf-8")

            self.assertEqual(first["cache_name"], second["cache_name"])
            self.assertEqual(first_sw, second_sw)
            self.assertEqual(
                (dist / "index.html").read_text(encoding="utf-8").count('rel="manifest"'),
                1,
            )

    def test_asset_change_rolls_cache_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = _seed_dist(Path(tmp))
            first = generate_kds_pwa(dist, "0.5.17")
            (dist / "assets" / "index.js").write_text("console.log('v2')", encoding="utf-8")
            second = generate_kds_pwa(dist, "0.5.17")

            self.assertNotEqual(first["cache_name"], second["cache_name"])

    def test_rejects_missing_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                generate_kds_pwa(Path(tmp), "0.5.17")


if __name__ == "__main__":
    unittest.main()
