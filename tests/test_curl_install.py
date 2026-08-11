#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI contract tests for scripts/curl_install.sh (curl|bash entry)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CURL_INSTALL = REPO_ROOT / "scripts" / "curl_install.sh"
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap_install.sh"
BASH = "/bin/bash"


class CurlInstallContractTest(unittest.TestCase):
    def test_help_mentions_curl_pipeline(self):
        result = subprocess.run(
            [BASH, str(CURL_INSTALL), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        combined = f"{result.stdout}\n{result.stderr}"
        self.assertRegex(combined, r"(?i)curl")
        self.assertRegex(combined, r"bootstrap_install")
        self.assertNotRegex(combined, r"(?i)deploy.?key")

    def test_allows_missing_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "bootstrap_stub.sh"
            stub.write_text(
                "#!/usr/bin/env bash\necho BOOTSTRAP_CONTRACT\necho \"args:$*\"\n",
                encoding="utf-8",
            )
            stub.chmod(0o755)
            env = os.environ.copy()
            env["LUYUN_BOOTSTRAP_SCRIPT"] = str(stub)
            env["GITHUB_RELEASES_TOKEN"] = ""
            result = subprocess.run(
                [
                    BASH,
                    str(CURL_INSTALL),
                    "--repo",
                    "acme/luyun",
                    "--tag",
                    "v0.1.0",
                ],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        out = result.stdout + result.stderr
        self.assertIn("BOOTSTRAP_CONTRACT", out)
        self.assertIn("--repo acme/luyun", out)
        self.assertIn("--tag v0.1.0", out)
        self.assertNotIn("--releases-token", out)

    def test_resolves_latest_tag_via_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "bootstrap_stub.sh"
            stub.write_text(
                "#!/usr/bin/env bash\necho BOOTSTRAP_CONTRACT\necho \"args:$*\"\n",
                encoding="utf-8",
            )
            stub.chmod(0o755)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            # Fake curl: latest API → JSON; anything else fail.
            (bin_dir / "curl").write_text(
                "#!/usr/bin/env bash\n"
                'if [[ "$*" == *"/releases/latest"* ]]; then\n'
                '  echo \'{"tag_name":"v9.9.9"}\'\n'
                "  exit 0\n"
                "fi\n"
                "echo unexpected curl: $* >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            (bin_dir / "curl").chmod(0o755)
            baked = Path(tmp) / "install.sh"
            text = CURL_INSTALL.read_text(encoding="utf-8")
            text = text.replace(
                'LUYUN_EMBEDDED_REPO=""',
                'LUYUN_EMBEDDED_REPO="acme/luyun"',
                1,
            ).replace(
                'LUYUN_EMBEDDED_TAG=""',
                'LUYUN_EMBEDDED_TAG=""',
                1,
            )
            baked.write_text(text, encoding="utf-8")
            baked.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
            env["LUYUN_BOOTSTRAP_SCRIPT"] = str(stub)
            env["GITHUB_RELEASES_TOKEN"] = ""
            env["LUYUN_TAG"] = "latest"
            result = subprocess.run(
                [BASH, str(baked)],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        out = result.stdout + result.stderr
        self.assertIn("v9.9.9", out)
        self.assertIn("--tag v9.9.9", out)
        self.assertNotRegex(out, r"(?i)deploy.?key")

    def test_short_mode_uses_embedded_repo_tag_without_deploy_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            deploy_dir = Path(tmp) / "opt" / "luyun"
            stub = Path(tmp) / "bootstrap_stub.sh"
            stub.write_text(
                "#!/usr/bin/env bash\n"
                "echo BOOTSTRAP_CONTRACT\n"
                'echo "args:$*"\n',
                encoding="utf-8",
            )
            stub.chmod(0o755)
            baked = Path(tmp) / "install.sh"
            text = CURL_INSTALL.read_text(encoding="utf-8")
            text = text.replace(
                'LUYUN_EMBEDDED_REPO=""',
                'LUYUN_EMBEDDED_REPO="acme/luyun"',
                1,
            ).replace(
                'LUYUN_EMBEDDED_TAG=""',
                'LUYUN_EMBEDDED_TAG="v0.1.0"',
                1,
            )
            baked.write_text(text, encoding="utf-8")
            baked.chmod(0o755)
            env = os.environ.copy()
            env["LUYUN_BOOTSTRAP_SCRIPT"] = str(stub)
            env["GITHUB_RELEASES_TOKEN"] = ""
            env["LUYUN_DEPLOY_DIR"] = str(deploy_dir)
            result = subprocess.run(
                [BASH, str(baked)],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        out = result.stdout + result.stderr
        self.assertIn("BOOTSTRAP_CONTRACT", out)
        self.assertIn("--repo acme/luyun", out)
        self.assertIn("--tag v0.1.0", out)
        self.assertNotIn("--releases-token", out)
        self.assertNotRegex(out, r"(?i)deploy.?key|--deploy-key")

    def test_dry_run_delegates_to_local_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            deploy_dir = Path(tmp) / "opt" / "luyun"
            env = os.environ.copy()
            env["LUYUN_BOOTSTRAP_SCRIPT"] = str(BOOTSTRAP)
            env["GITHUB_RELEASES_TOKEN"] = ""
            result = subprocess.run(
                [
                    BASH,
                    str(CURL_INSTALL),
                    "--dry-run",
                    "--repo",
                    "acme/luyun",
                    "--tag",
                    "v0.1.0",
                    "--deploy-dir",
                    str(deploy_dir),
                ],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        out = result.stdout + result.stderr
        self.assertIn("BOOTSTRAP_CONTRACT", out)
        self.assertIn("luyun-release-bundle.tar.gz", out)
        self.assertRegex(out, r"(?i)install\.sh|bootstrap|using local bootstrap")
        self.assertNotRegex(out, r"(?i)deploy.?key|\bgit clone\b")


if __name__ == "__main__":
    unittest.main()
