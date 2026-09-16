#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PWA static routes and cache headers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class PwaStaticRouteTest(unittest.TestCase):
    def test_auth_exemptions_cover_pwa_entrypoints(self):
        import main as main_module

        for path in (
            "/index.html",
            "/sw.js",
            "/pwa/manifests/admin.webmanifest",
            "/pwa/icons/admin-192.png",
            "/kds/sw.js",
            "/kds/manifest.webmanifest",
        ):
            self.assertTrue(main_module._is_html_auth_exempt(path), path)

    def test_static_files_use_pwa_cache_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spa = root / "dist"
            kds = root / "public" / "kds"
            (spa / "pwa" / "manifests").mkdir(parents=True)
            (spa / "pwa" / "icons").mkdir(parents=True)
            kds.mkdir(parents=True)
            (spa / "sw.js").write_text("self.addEventListener('fetch',()=>{})", encoding="utf-8")
            (spa / "pwa" / "manifests" / "admin.webmanifest").write_text(
                '{"name":"禄云管理"}',
                encoding="utf-8",
            )
            (spa / "pwa" / "icons" / "admin-192.png").write_bytes(b"png")
            (kds / "sw.js").write_text("self.addEventListener('fetch',()=>{})", encoding="utf-8")
            (kds / "manifest.webmanifest").write_text(
                '{"name":"禄云 KDS"}',
                encoding="utf-8",
            )
            (spa / "index.html").write_text("<html></html>", encoding="utf-8")

            import main as main_module

            old_spa = main_module.spa_dir
            old_spa_index = main_module.spa_index_path
            old_public = main_module.public_dir
            main_module.spa_dir = str(spa)
            main_module.spa_index_path = str(spa / "index.html")
            main_module.public_dir = str(root / "public")
            try:
                client = TestClient(main_module.app)
                expected = {
                    "/index.html": ("text/html", "no-cache"),
                    "/sw.js": ("application/javascript", "no-cache"),
                    "/pwa/manifests/admin.webmanifest": (
                        "application/manifest+json",
                        "no-cache",
                    ),
                    "/pwa/icons/admin-192.png": (
                        "image/png",
                        "public, max-age=86400",
                    ),
                    "/kds/sw.js": ("application/javascript", "no-cache"),
                    "/kds/manifest.webmanifest": (
                        "application/manifest+json",
                        "no-cache",
                    ),
                }
                for path, (content_type, cache_control) in expected.items():
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200, path)
                    self.assertTrue(
                        response.headers["content-type"].startswith(content_type),
                        path,
                    )
                    self.assertEqual(
                        response.headers.get("cache-control"),
                        cache_control,
                        path,
                    )
            finally:
                main_module.spa_dir = old_spa
                main_module.spa_index_path = old_spa_index
                main_module.public_dir = old_public

    def test_rejects_unknown_admin_pwa_manifest(self):
        import main as main_module

        client = TestClient(main_module.app)
        self.assertEqual(
            client.get("/pwa/manifests/unknown.webmanifest").status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
