#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI contract tests for scripts/bootstrap_install.sh.

Seams (agreed):
- Bootstrap Install CLI (--dry-run): plan covers download+verify Release Bundle
  (luyun-release-bundle.tar.gz + SHA256SUMS) → extract → venv/pip → Playwright →
  Release Manifest as installed identity → enable luyun + luyun-update; no git
  clone / Deploy Key / Node/npm/build_kds / split frontend tarballs; success/
  dry-run output lists intentional manual follow-ups.
- Credentials / tools: Releases PAT is optional for public repos; Deploy Key is
  not required; missing python3/tar/curl/(sha256sum|shasum) fails; git is not
  required.
- Stubbed install: GitHub settings land mode-restricted in env.production; no
  secrets/github_deploy_key; command log shows bundle fetch (not git clone);
  RELEASE_MANIFEST.json + prebuilt Admin/KDS present; Node-free.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = REPO_ROOT / "scripts" / "bootstrap_install.sh"

BUNDLE_ASSET = "luyun-release-bundle.tar.gz"
CHECKSUMS_ASSET = "SHA256SUMS"


BASH = "/bin/bash"


def _run_bootstrap(
    *args: str,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        [BASH, str(BOOTSTRAP_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=run_env,
        cwd=str(cwd or REPO_ROOT),
        input=input_text,
    )


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)


def _make_bundle_and_sums(stage: Path) -> tuple[Path, Path]:
    """Build a minimal Release Bundle + SHA256SUMS for stubbed install tests."""
    tree = stage / "tree"
    (tree / "admin-web" / "dist").mkdir(parents=True)
    (tree / "public" / "kds" / "assets").mkdir(parents=True)
    (tree / "deploy").mkdir(parents=True)
    (tree / "scripts").mkdir(parents=True)
    (tree / "admin-web" / "dist" / "index.html").write_text("admin\n", encoding="utf-8")
    (tree / "public" / "kds" / "index.html").write_text("kds\n", encoding="utf-8")
    (tree / "public" / "kds" / "assets" / ".keep").write_text("", encoding="utf-8")
    (tree / "requirements.txt").write_text("fastapi==0.1.0\n", encoding="utf-8")
    (tree / "main.py").write_text("# stub\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "tag": "v0.1.0",
        "app_version": "0.1.0",
        "commit": "a" * 40,
        "requirements_fingerprint": "sha256:" + "b" * 64,
        "artifacts": {"bundle": BUNDLE_ASSET, "checksums": CHECKSUMS_ASSET},
    }
    (tree / "RELEASE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    for name in ("luyun.service", "luyun-update.service"):
        src = REPO_ROOT / "deploy" / name
        (tree / "deploy" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    example = REPO_ROOT / "deploy" / "env.production.example"
    (tree / "deploy" / "env.production.example").write_text(
        example.read_text(encoding="utf-8"), encoding="utf-8"
    )

    bundle = stage / BUNDLE_ASSET
    with tarfile.open(bundle, "w:gz") as tar:
        for path in tree.rglob("*"):
            if path.is_file():
                tar.add(path, arcname=str(path.relative_to(tree)))
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    sums = stage / CHECKSUMS_ASSET
    sums.write_text(f"{digest}  {BUNDLE_ASSET}\n", encoding="utf-8")
    return bundle, sums


class BootstrapInstallDryRunContractTest(unittest.TestCase):
    def test_dry_run_prints_plan_bundle_units_and_manual_followups(self):
        with tempfile.TemporaryDirectory() as tmp:
            deploy_dir = Path(tmp) / "opt" / "luyun"
            result = _run_bootstrap(
                "--dry-run",
                "--repo",
                "acme/luyun",
                "--tag",
                "v0.1.0",
                "--deploy-dir",
                str(deploy_dir),
                "--releases-token",
                "ghs_test_token",
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        out = result.stdout
        self.assertIn("BOOTSTRAP_CONTRACT", out)
        self.assertIn(BUNDLE_ASSET, out)
        self.assertIn(CHECKSUMS_ASSET, out)
        self.assertRegex(out, r"(?i)checksum|verify|校验")
        self.assertIn("venv", out.lower())
        self.assertIn("playwright", out.lower())
        self.assertRegex(out, r"(?i)RELEASE_MANIFEST|manifest")
        self.assertIn("luyun.service", out)
        self.assertIn("luyun-update.service", out)
        self.assertNotRegex(out, r"(?i)\bgit clone\b")
        self.assertNotRegex(out, r"(?i)deploy.?key")
        self.assertNotIn("admin-web-dist.tar.gz", out)
        self.assertNotIn("kds-dist.tar.gz", out)
        self.assertNotIn("npm", out.lower())
        self.assertNotIn("build_kds", out.lower())
        # Intentional manual follow-ups
        self.assertRegex(out, r"(?i)env\.production|环境变量")
        self.assertRegex(out, r"(?i)caddy|nginx|tls|反向代理")
        self.assertRegex(out, r"(?i)/setup|POS")

    def test_allows_missing_releases_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            deploy_dir = Path(tmp) / "opt" / "luyun"
            result = _run_bootstrap(
                "--dry-run",
                "--repo",
                "acme/luyun",
                "--tag",
                "v0.1.0",
                "--deploy-dir",
                str(deploy_dir),
                env={"GITHUB_RELEASES_TOKEN": ""},
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        combined = f"{result.stdout}\n{result.stderr}"
        self.assertIn("BOOTSTRAP_CONTRACT", combined)
        self.assertRegex(combined, r"(?i)anonymous|optional|无需|no Releases token")
        self.assertNotRegex(combined, r"(?i)deploy.?key")

    def test_does_not_require_deploy_key_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            deploy_dir = Path(tmp) / "opt" / "luyun"
            result = _run_bootstrap(
                "--dry-run",
                "--repo",
                "acme/luyun",
                "--tag",
                "v0.1.0",
                "--deploy-dir",
                str(deploy_dir),
                env={"GITHUB_RELEASES_TOKEN": ""},
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("BOOTSTRAP_CONTRACT", result.stdout)

    def test_refuses_missing_required_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            # Empty PATH prefix with no python3/tar/curl/sha256sum on purpose.
            env = {
                "PATH": str(bin_dir),
                "GITHUB_RELEASES_TOKEN": "ghs_test_token",
            }
            result = _run_bootstrap(
                "--dry-run",
                "--repo",
                "acme/luyun",
                "--tag",
                "v0.1.0",
                "--deploy-dir",
                str(tmp_path / "opt" / "luyun"),
                "--releases-token",
                "ghs_test_token",
                env=env,
            )
        self.assertNotEqual(result.returncode, 0)
        combined = f"{result.stdout}\n{result.stderr}"
        self.assertRegex(combined, r"(?i)缺少必需工具|missing|python3|tar|curl")
        self.assertNotRegex(combined, r"(?i)\bgit\b")


class BootstrapInstallChecksumGateTest(unittest.TestCase):
    def test_checksum_mismatch_fails_before_activating_deploy_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            deploy_dir = tmp_path / "opt" / "luyun"
            assets = tmp_path / "assets"
            assets.mkdir()
            bundle, _good_sums = _make_bundle_and_sums(assets)
            bad_sums = assets / "bad-SHA256SUMS"
            bad_sums.write_text(f"{'0' * 64}  {BUNDLE_ASSET}\n", encoding="utf-8")
            stub_home = tmp_path / "stubs"
            bin_dir = stub_home / "bin"
            bin_dir.mkdir(parents=True)
            log_file = stub_home / "command.log"

            _write_stub(
                bin_dir,
                "curl",
                textwrap.dedent(
                    f"""\
                    echo "curl $*" >> "{log_file}"
                    out=""
                    prev=""
                    for a in "$@"; do
                      if [[ "$prev" == "-o" ]]; then out="$a"; fi
                      prev="$a"
                    done
                    [[ -n "$out" ]] || exit 1
                    case "$out" in
                      *{BUNDLE_ASSET}) /bin/cp "{bundle}" "$out" ;;
                      *{CHECKSUMS_ASSET}) /bin/cp "{bad_sums}" "$out" ;;
                      *) echo "unexpected download dest: $out" >&2; exit 1 ;;
                    esac
                    exit 0
                    """
                ),
            )
            _write_stub(
                bin_dir,
                "python3",
                textwrap.dedent(
                    f"""\
                    echo "python3 $*" >> "{log_file}"
                    exit 0
                    """
                ),
            )
            _write_stub(
                bin_dir,
                "systemctl",
                textwrap.dedent(
                    f"""\
                    echo "systemctl $*" >> "{log_file}"
                    exit 0
                    """
                ),
            )
            env = {
                "PATH": f"{bin_dir}:/bin:/usr/bin",
                "LUYUN_BOOTSTRAP_SKIP_PLAYWRIGHT_DEPS": "1",
                "LUYUN_BOOTSTRAP_SKIP_SYSTEMD_ROOT_CHECK": "1",
            }
            result = _run_bootstrap(
                "--repo",
                "acme/luyun",
                "--tag",
                "v0.1.0",
                "--deploy-dir",
                str(deploy_dir),
                "--releases-token",
                "ghs_test_token",
                env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            combined = f"{result.stdout}\n{result.stderr}"
            self.assertRegex(combined, r"(?i)checksum mismatch")
            self.assertFalse(
                deploy_dir.exists(),
                "checksum failure must not activate the deploy directory",
            )
            log = log_file.read_text(encoding="utf-8")
            self.assertNotRegex(log, r"(?m)^python3 -m venv")


class BootstrapInstallStubbedBundleTest(unittest.TestCase):
    def test_stubbed_install_uses_bundle_writes_pat_and_manifest(self):
        """Stubbed install: bundle path, PAT secrets, no clone / deploy key / Node."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            deploy_dir = tmp_path / "opt" / "luyun"
            assets = tmp_path / "assets"
            assets.mkdir()
            bundle, sums = _make_bundle_and_sums(assets)
            stub_home = tmp_path / "stubs"
            bin_dir = stub_home / "bin"
            bin_dir.mkdir(parents=True)
            log_file = stub_home / "command.log"

            _write_stub(
                bin_dir,
                "curl",
                textwrap.dedent(
                    f"""\
                    echo "curl $*" >> "{log_file}"
                    out=""
                    prev=""
                    for a in "$@"; do
                      if [[ "$prev" == "-o" ]]; then out="$a"; fi
                      prev="$a"
                    done
                    [[ -n "$out" ]] || exit 1
                    case "$out" in
                      *{BUNDLE_ASSET}) /bin/cp "{bundle}" "$out" ;;
                      *{CHECKSUMS_ASSET}) /bin/cp "{sums}" "$out" ;;
                      *) echo "unexpected download dest: $out" >&2; exit 1 ;;
                    esac
                    exit 0
                    """
                ),
            )
            _write_stub(
                bin_dir,
                "python3",
                textwrap.dedent(
                    f"""\
                    echo "python3 $*" >> "{log_file}"
                    if [[ "$1" == "-m" && "$2" == "venv" ]]; then
                      mkdir -p "$3/bin"
                      printf '%s\\n' '#!/usr/bin/env bash' 'exit 0' > "$3/bin/pip"
                      printf '%s\\n' '#!/usr/bin/env bash' 'exit 0' > "$3/bin/python"
                      chmod +x "$3/bin/pip" "$3/bin/python"
                      exit 0
                    fi
                    exit 0
                    """
                ),
            )
            _write_stub(
                bin_dir,
                "systemctl",
                textwrap.dedent(
                    f"""\
                    echo "systemctl $*" >> "{log_file}"
                    exit 0
                    """
                ),
            )
            # Intentionally provide a git stub that must NOT be invoked.
            _write_stub(
                bin_dir,
                "git",
                textwrap.dedent(
                    f"""\
                    echo "git $*" >> "{log_file}"
                    echo "git must not be called during bundle bootstrap" >&2
                    exit 99
                    """
                ),
            )

            env = {
                "PATH": f"{bin_dir}:/bin:/usr/bin",
                "LUYUN_BOOTSTRAP_SKIP_PLAYWRIGHT_DEPS": "1",
                "LUYUN_BOOTSTRAP_SKIP_SYSTEMD_ROOT_CHECK": "1",
            }
            result = _run_bootstrap(
                "--repo",
                "acme/luyun",
                "--tag",
                "v0.1.0",
                "--deploy-dir",
                str(deploy_dir),
                "--releases-token",
                "ghs_test_token",
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            deploy_key = deploy_dir / "secrets" / "github_deploy_key"
            self.assertFalse(deploy_key.exists(), "deploy key must not be written")

            env_file = deploy_dir / "deploy" / "env.production"
            self.assertTrue(env_file.is_file(), "env.production should be seeded")
            env_mode = stat.S_IMODE(env_file.stat().st_mode)
            self.assertEqual(env_mode, 0o600, f"env.production mode should be 600, got {oct(env_mode)}")
            env_text = env_file.read_text(encoding="utf-8")
            self.assertIn("GITHUB_REPO=acme/luyun", env_text)
            self.assertIn("GITHUB_RELEASES_TOKEN=ghs_test_token", env_text)
            self.assertNotRegex(env_text, r"(?m)^GIT_SSH_COMMAND=")

            manifest_path = deploy_dir / "RELEASE_MANIFEST.json"
            self.assertTrue(manifest_path.is_file(), "Release Manifest must be installed")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["tag"], "v0.1.0")

            log = log_file.read_text(encoding="utf-8")
            self.assertRegex(log, rf"(?m)^curl .*{BUNDLE_ASSET}")
            self.assertRegex(log, rf"(?m)^curl .*{CHECKSUMS_ASSET}")
            self.assertNotRegex(log, r"(?m)^git ")
            self.assertRegex(log, r"(?m)^systemctl .*luyun\.service")
            self.assertRegex(log, r"(?m)^systemctl .*luyun-update\.service")
            self.assertNotRegex(log, r"(?i)\bnpm\b")
            self.assertNotRegex(log, r"(?i)build_kds")
            self.assertRegex(result.stdout, r"(?i)env\.production|环境变量")
            self.assertRegex(result.stdout, r"(?i)caddy|nginx|tls|反向代理")
            self.assertRegex(result.stdout, r"(?i)/setup|POS")
            self.assertTrue(
                (deploy_dir / "public" / "kds" / "assets").is_dir(),
                "public/kds/assets/ required by release-asset-layout",
            )
            self.assertTrue((deploy_dir / "public" / "kds" / "index.html").is_file())
            self.assertTrue((deploy_dir / "admin-web" / "dist" / "index.html").is_file())

    def test_stubbed_install_works_without_releases_token(self):
        """Public-repo path: anonymous download, empty token in env.production."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            deploy_dir = tmp_path / "opt" / "luyun"
            assets = tmp_path / "assets"
            assets.mkdir()
            bundle, sums = _make_bundle_and_sums(assets)
            stub_home = tmp_path / "stubs"
            bin_dir = stub_home / "bin"
            bin_dir.mkdir(parents=True)
            log_file = stub_home / "command.log"

            _write_stub(
                bin_dir,
                "curl",
                textwrap.dedent(
                    f"""\
                    echo "curl $*" >> "{log_file}"
                    out=""
                    prev=""
                    for a in "$@"; do
                      if [[ "$prev" == "-o" ]]; then out="$a"; fi
                      prev="$a"
                    done
                    [[ -n "$out" ]] || exit 1
                    case "$out" in
                      *{BUNDLE_ASSET}) /bin/cp "{bundle}" "$out" ;;
                      *{CHECKSUMS_ASSET}) /bin/cp "{sums}" "$out" ;;
                      *) echo "unexpected download dest: $out" >&2; exit 1 ;;
                    esac
                    exit 0
                    """
                ),
            )
            _write_stub(
                bin_dir,
                "python3",
                textwrap.dedent(
                    f"""\
                    echo "python3 $*" >> "{log_file}"
                    if [[ "$1" == "-m" && "$2" == "venv" ]]; then
                      mkdir -p "$3/bin"
                      printf '%s\\n' '#!/usr/bin/env bash' 'exit 0' > "$3/bin/pip"
                      printf '%s\\n' '#!/usr/bin/env bash' 'exit 0' > "$3/bin/python"
                      chmod +x "$3/bin/pip" "$3/bin/python"
                      exit 0
                    fi
                    exit 0
                    """
                ),
            )
            _write_stub(
                bin_dir,
                "systemctl",
                textwrap.dedent(
                    f"""\
                    echo "systemctl $*" >> "{log_file}"
                    exit 0
                    """
                ),
            )

            env = {
                "PATH": f"{bin_dir}:/bin:/usr/bin",
                "GITHUB_RELEASES_TOKEN": "",
                "LUYUN_BOOTSTRAP_SKIP_PLAYWRIGHT_DEPS": "1",
                "LUYUN_BOOTSTRAP_SKIP_SYSTEMD_ROOT_CHECK": "1",
            }
            result = _run_bootstrap(
                "--repo",
                "acme/luyun",
                "--tag",
                "v0.1.0",
                "--deploy-dir",
                str(deploy_dir),
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            env_text = (deploy_dir / "deploy" / "env.production").read_text(encoding="utf-8")
            self.assertIn("GITHUB_REPO=acme/luyun", env_text)
            self.assertRegex(env_text, r"(?m)^GITHUB_RELEASES_TOKEN=$")
            log = log_file.read_text(encoding="utf-8")
            self.assertNotRegex(log, r"(?i)Authorization")
            self.assertRegex(log, rf"(?m)^curl .*{BUNDLE_ASSET}")


if __name__ == "__main__":
    unittest.main()
