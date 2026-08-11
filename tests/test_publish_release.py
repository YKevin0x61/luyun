#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI contract tests for scripts/publish_release.sh.

Seams (agreed):
- Publish script CLI (--dry-run): dirty tree / APP_VERSION↔tag refusal,
  planned Admin+KDS builds and Release Bundle asset names.
- Release Bundle layout (bundle + SHA256SUMS + install.sh) consumed by
  Update Job / Bootstrap; split frontend-only pair is not the shop contract.
"""

from __future__ import annotations

import json
import os
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLISH_SCRIPT = REPO_ROOT / "scripts" / "publish_release.sh"
CURL_INSTALL_SRC = REPO_ROOT / "scripts" / "curl_install.sh"

BUNDLE_ASSET = "luyun-release-bundle.tar.gz"
CHECKSUMS_ASSET = "SHA256SUMS"
INSTALL_ASSET = "install.sh"
MANIFEST_NAME = "RELEASE_MANIFEST.json"
# Superseded shop contract (must not be the Release attachment set).
LEGACY_ADMIN_ASSET = "admin-web-dist.tar.gz"
LEGACY_KDS_ASSET = "kds-dist.tar.gz"


def _seed_curl_install(root: Path) -> None:
    """Copy real curl_install.sh so publish can bake LUYUN_EMBEDDED_*."""
    text = CURL_INSTALL_SRC.read_text(encoding="utf-8")
    dest = root / "scripts" / "curl_install.sh"
    dest.write_text(text, encoding="utf-8")
    dest.chmod(0o755)


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_publish_fixture(
    *,
    app_version: str = "0.1.0",
    dirty: bool = False,
) -> tempfile.TemporaryDirectory:
    """Minimal git worktree that publish_release.sh can validate against."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "scripts").mkdir()
    (root / "config.py").write_text(
        textwrap.dedent(
            f'''\
            class Settings:
                APP_VERSION: str = "{app_version}"
            '''
        ),
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("fastapi==0.104.1\n", encoding="utf-8")
    # Mirror real ignore rules so shop/runtime paths can exist on disk without
    # failing the dirty-worktree gate (and must still stay out of the bundle).
    (root / ".gitignore").write_text(
        "data/\nsecrets/\nms-playwright/\n.env\n.env.*\n",
        encoding="utf-8",
    )
    _run_git(root, "init")
    _run_git(root, "config", "user.email", "test@example.com")
    _run_git(root, "config", "user.name", "publish-test")
    _run_git(root, "add", "config.py", "requirements.txt", ".gitignore")
    _run_git(root, "commit", "-m", "seed")
    if dirty:
        (root / "config.py").write_text(
            (root / "config.py").read_text(encoding="utf-8") + "# dirty\n",
            encoding="utf-8",
        )
    return tmp


def _run_publish(
    repo_root: Path,
    *args: str,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LUYUN_PUBLISH_ROOT"] = str(repo_root)
    return subprocess.run(
        ["bash", str(PUBLISH_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


class PublishReleaseDirtyTreeTest(unittest.TestCase):
    def test_dry_run_refuses_dirty_worktree(self):
        with _init_publish_fixture(dirty=True) as tmp:
            result = _run_publish(Path(tmp), "--dry-run", "v0.1.0")
        self.assertNotEqual(result.returncode, 0)
        combined = f"{result.stdout}\n{result.stderr}"
        self.assertRegex(combined, r"(?i)dirty|worktree|工作区|未提交")


class PublishReleaseVersionAlignTest(unittest.TestCase):
    def test_dry_run_refuses_app_version_tag_mismatch(self):
        with _init_publish_fixture(app_version="0.1.0", dirty=False) as tmp:
            result = _run_publish(Path(tmp), "--dry-run", "v0.2.0")
        self.assertNotEqual(result.returncode, 0)
        combined = f"{result.stdout}\n{result.stderr}"
        self.assertRegex(combined, r"(?i)APP_VERSION|mismatch|不一致|不匹配")


class PublishReleaseDryRunPlanTest(unittest.TestCase):
    def test_dry_run_plans_frontend_builds_and_release_bundle(self):
        with _init_publish_fixture(app_version="0.1.0", dirty=False) as tmp:
            result = _run_publish(Path(tmp), "--dry-run", "v0.1.0")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        out = result.stdout
        self.assertRegex(out, r"(?i)admin-web|npm run build")
        self.assertRegex(out, r"(?i)build_kds|kds")
        self.assertIn(BUNDLE_ASSET, out)
        self.assertIn(CHECKSUMS_ASSET, out)
        self.assertIn(INSTALL_ASSET, out)
        self.assertIn(MANIFEST_NAME, out)
        self.assertRegex(out, r"admin-web/dist")
        self.assertRegex(out, r"public/kds")
        self.assertRegex(out, r"(?i)gh release create|GitHub Release")
        # Split frontend-only pair is no longer the shop Release contract.
        self.assertNotIn(LEGACY_ADMIN_ASSET, out)
        self.assertNotIn(LEGACY_KDS_ASSET, out)


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


class PublishReleaseTagPinTest(unittest.TestCase):
    def test_publish_refuses_existing_tag_not_on_head(self):
        with _init_publish_fixture(app_version="0.1.0", dirty=False) as tmp:
            root = Path(tmp)
            _run_git(root, "tag", "v0.1.0")
            (root / "config.py").write_text(
                (root / "config.py").read_text(encoding="utf-8").rstrip() + "\n# bump note\n",
                encoding="utf-8",
            )
            # Keep APP_VERSION aligned; only move HEAD past the tag.
            _run_git(root, "add", "config.py")
            _run_git(root, "commit", "-m", "move head past tag")

            with tempfile.TemporaryDirectory() as stub_home:
                stub_home_path = Path(stub_home)
                bin_dir = stub_home_path / "bin"
                bin_dir.mkdir()
                _write_stub(
                    bin_dir,
                    "npm",
                    textwrap.dedent(
                        f"""\
                        mkdir -p "{root}/admin-web/dist/assets"
                        echo ok > "{root}/admin-web/dist/index.html"
                        """
                    ),
                )
                _write_stub(
                    bin_dir,
                    "gh",
                    textwrap.dedent(
                        """\
                        if [[ "$1" == "repo" ]]; then
                          echo "acme/luyun"
                          exit 0
                        fi
                        echo "gh should not run" >&2
                        exit 99
                        """
                    ),
                )
                (root / "scripts" / "build_kds.sh").write_text(
                    textwrap.dedent(
                        f"""\
                        #!/usr/bin/env bash
                        mkdir -p "{root}/public/kds/assets"
                        echo ok > "{root}/public/kds/index.html"
                        """
                    ),
                    encoding="utf-8",
                )
                (root / "scripts" / "build_kds.sh").chmod(0o755)
                _seed_curl_install(root)
                _run_git(root, "add", "scripts/build_kds.sh", "scripts/curl_install.sh")
                _run_git(root, "commit", "-m", "stub build_kds")
                (root / "admin-web").mkdir(exist_ok=True)

                env = os.environ.copy()
                env["LUYUN_PUBLISH_ROOT"] = str(root)
                env["LUYUN_PUBLISH_REPO"] = "acme/luyun"
                env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
                result = subprocess.run(
                    ["bash", str(PUBLISH_SCRIPT), "v0.1.0"],
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=str(root),
                )
            self.assertNotEqual(result.returncode, 0)
            combined = f"{result.stdout}\n{result.stderr}"
            self.assertRegex(combined, r"(?i)HEAD|tag|不指向")


class PublishReleaseHappyPathStubTest(unittest.TestCase):
    def test_publish_builds_then_creates_release_with_bundle(self):
        with _init_publish_fixture(app_version="0.1.0", dirty=False) as tmp:
            root = Path(tmp)
            # Forbidden paths that must never ship in the Release Bundle.
            (root / "data").mkdir()
            (root / "data" / "app.db").write_text("shop-db", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "github_deploy_key").write_text("SECRET", encoding="utf-8")
            (root / "ms-playwright").mkdir()
            (root / "ms-playwright" / "chromium").write_text("browser", encoding="utf-8")
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

            with tempfile.TemporaryDirectory() as stub_home:
                stub_home_path = Path(stub_home)
                log_file = stub_home_path / "command.log"
                capture_dir = stub_home_path / "release_assets"
                capture_dir.mkdir()
                bin_dir = stub_home_path / "bin"
                bin_dir.mkdir()

                _write_stub(
                    bin_dir,
                    "npm",
                    textwrap.dedent(
                        f"""\
                        echo "npm $*" >> "{log_file}"
                        mkdir -p "{root}/admin-web/dist/assets"
                        echo ok > "{root}/admin-web/dist/index.html"
                        echo ok > "{root}/admin-web/dist/assets/app.js"
                        """
                    ),
                )
                _write_stub(
                    bin_dir,
                    "build_kds.sh",
                    textwrap.dedent(
                        f"""\
                        echo "build_kds $*" >> "{log_file}"
                        mkdir -p "{root}/public/kds/assets"
                        echo ok > "{root}/public/kds/index.html"
                        echo ok > "{root}/public/kds/assets/app.js"
                        """
                    ),
                )
                _write_stub(
                    bin_dir,
                    "gh",
                    textwrap.dedent(
                        f"""\
                        if [[ "$1" == "repo" ]]; then
                          echo "acme/luyun"
                          exit 0
                        fi
                        echo "gh $*" >> "{log_file}"
                        if [[ "$1" == "release" && "$2" == "create" ]]; then
                          for arg in "$@"; do
                            if [[ -f "$arg" ]]; then
                              cp "$arg" "{capture_dir}/"
                            fi
                          done
                        fi
                        """
                    ),
                )

                (root / "scripts" / "build_kds.sh").write_text(
                    (bin_dir / "build_kds.sh").read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                (root / "scripts" / "build_kds.sh").chmod(0o755)
                _seed_curl_install(root)
                _run_git(
                    root,
                    "add",
                    "scripts/build_kds.sh",
                    "scripts/curl_install.sh",
                )
                _run_git(root, "commit", "-m", "stub build_kds")
                (root / "admin-web").mkdir(exist_ok=True)

                env = os.environ.copy()
                env["LUYUN_PUBLISH_ROOT"] = str(root)
                env["LUYUN_PUBLISH_REPO"] = "acme/luyun"
                env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
                env["LUYUN_PUBLISH_SKIP_PUSH"] = "1"
                result = subprocess.run(
                    ["bash", str(PUBLISH_SCRIPT), "v0.1.0"],
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=str(root),
                )
                self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
                log = log_file.read_text(encoding="utf-8")
                self.assertRegex(log, r"(?m)^npm ")
                self.assertRegex(log, r"(?m)^build_kds ")
                self.assertRegex(
                    log,
                    rf"(?m)^gh release create v0\.1\.0 .*{BUNDLE_ASSET}",
                )
                self.assertIn(CHECKSUMS_ASSET, log)
                self.assertIn(INSTALL_ASSET, log)
                self.assertNotIn(LEGACY_ADMIN_ASSET, log)
                self.assertNotIn(LEGACY_KDS_ASSET, log)
                self.assertIn("--target", log)
                self.assertLess(log.index("npm "), log.index("gh release create"))
                self.assertLess(log.index("build_kds "), log.index("gh release create"))

                tag_sha = subprocess.run(
                    ["git", "-C", str(root), "rev-list", "-n", "1", "v0.1.0"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                head_sha = subprocess.run(
                    ["git", "-C", str(root), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                self.assertEqual(tag_sha, head_sha)

                bundle_path = capture_dir / BUNDLE_ASSET
                checksums_path = capture_dir / CHECKSUMS_ASSET
                install_path = capture_dir / INSTALL_ASSET
                self.assertTrue(bundle_path.is_file(), msg=list(capture_dir.iterdir()))
                self.assertTrue(checksums_path.is_file())
                self.assertTrue(install_path.is_file())

                checksums = checksums_path.read_text(encoding="utf-8")
                self.assertRegex(
                    checksums,
                    rf"(?m)^[0-9a-f]{{64}}  {BUNDLE_ASSET}$",
                )
                expected_hex = checksums.split()[0]
                actual_hex = subprocess.run(
                    ["shasum", "-a", "256", str(bundle_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.split()[0]
                self.assertEqual(actual_hex, expected_hex)

                def _norm_tar_name(name: str) -> str:
                    if name.startswith("./"):
                        return name[2:]
                    return name

                with tarfile.open(bundle_path, "r:gz") as tar:
                    members = {
                        _norm_tar_name(m.name): m
                        for m in tar.getmembers()
                        if m.isfile() or m.isdir()
                    }
                    # Ignore AppleDouble / metadata noise if a host tar still emits it.
                    names = {
                        n
                        for n in members
                        if n and not n.split("/")[-1].startswith("._")
                    }
                    manifest_member = members.get(MANIFEST_NAME)
                    self.assertIsNotNone(manifest_member, msg=sorted(names))
                    manifest_file = tar.extractfile(manifest_member)
                    assert manifest_file is not None
                    manifest = json.load(manifest_file)

                self.assertIn(MANIFEST_NAME, names)
                self.assertIn("admin-web/dist/index.html", names)
                self.assertIn("public/kds/index.html", names)
                self.assertTrue(
                    any(n == "public/kds/assets" or n.startswith("public/kds/assets/") for n in names),
                    msg="bundle missing public/kds/assets/",
                )
                self.assertIn("config.py", names)
                self.assertIn("requirements.txt", names)

                forbidden_prefixes = (
                    "data/",
                    "secrets/",
                    "ms-playwright/",
                    ".env",
                )
                leaked = [
                    n
                    for n in names
                    if n == ".env"
                    or any(n == p.rstrip("/") or n.startswith(p) for p in forbidden_prefixes)
                ]
                self.assertEqual(leaked, [], msg=f"bundle leaked shop/runtime paths: {leaked}")

                self.assertEqual(manifest.get("schema_version"), 1)
                self.assertEqual(manifest.get("tag"), "v0.1.0")
                self.assertEqual(manifest.get("app_version"), "0.1.0")
                self.assertEqual(manifest.get("commit"), head_sha)
                self.assertIn("requirements_fingerprint", manifest)
                self.assertTrue(
                    str(manifest["requirements_fingerprint"]).startswith("sha256:"),
                )
                artifacts = manifest.get("artifacts") or {}
                self.assertEqual(artifacts.get("bundle"), BUNDLE_ASSET)
                self.assertEqual(artifacts.get("checksums"), CHECKSUMS_ASSET)


if __name__ == "__main__":
    unittest.main()
