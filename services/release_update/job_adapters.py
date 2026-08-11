#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real adapters used by the Update Job oneshot (bundle / backup / deps / restart)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from config import settings
from services import backup_service
from services.release_update.job_runner import BundleInstallResult
from services.release_update.manifest_identity import MANIFEST_NAME

logger = logging.getLogger(__name__)

BUNDLE_ASSET = "luyun-release-bundle.tar.gz"
CHECKSUMS_ASSET = "SHA256SUMS"

# Shop state that must survive atomic application-tree swap (never from the bundle).
_PRESERVE_DIR_NAMES = ("data", ".venv", "venv", "secrets")
_PRESERVE_FILE_RELATIVE = (
    ".env",
    "deploy/env.production",
)


class SnapshotBackupAdapter:
    """Mandatory backup via local restore snapshot (no passphrase export)."""

    def run_backup(self) -> str:
        app_db = settings.APP_DB_PATH
        recipes = backup_service.get_recipes_db_path()
        creds = backup_service.get_credentials_file_path()
        try:
            return backup_service.create_restore_snapshot(app_db, recipes, creds)
        except Exception as exc:
            raise RuntimeError(f"backup failed: {exc}") from exc


class ReleaseBundleInstallAdapter:
    """Download / hard-verify / side-extract / atomic-switch a Release Bundle."""

    def __init__(
        self,
        deploy_dir: Path,
        *,
        github_repo: str,
        token: Optional[str],
    ) -> None:
        self._deploy = Path(deploy_dir)
        self._github_repo = github_repo
        self._token = (token or "").strip() or None
        self._staging: Optional[tempfile.TemporaryDirectory] = None
        self._verified_bundle: Optional[Path] = None

    def fetch_bundle(self, tag: str) -> None:
        """Download bundle + SHA256SUMS and hard-fail on integrity problems."""
        if not self._github_repo:
            raise RuntimeError("GITHUB_REPO is not configured")
        self._cleanup_staging()
        staging = tempfile.TemporaryDirectory(prefix="luyun-bundle-")
        stage_path = Path(staging.name)
        bundle_path = stage_path / BUNDLE_ASSET
        sums_path = stage_path / CHECKSUMS_ASSET
        self._download_asset(tag, BUNDLE_ASSET, bundle_path)
        try:
            self._download_asset(tag, CHECKSUMS_ASSET, sums_path)
        except RuntimeError as exc:
            staging.cleanup()
            raise RuntimeError(
                f"missing integrity file {CHECKSUMS_ASSET} for {tag}: {exc}"
            ) from exc
        try:
            verify_bundle_checksum(bundle_path, sums_path)
        except Exception:
            staging.cleanup()
            raise
        self._staging = staging
        self._verified_bundle = bundle_path
        logger.info("Verified Release Bundle for %s (%s)", tag, BUNDLE_ASSET)

    def activate_bundle(self, tag: str) -> BundleInstallResult:
        """Extract beside the live tree, carry shop state, atomically switch."""
        if self._verified_bundle is None or not self._verified_bundle.is_file():
            raise RuntimeError("Release Bundle was not fetched/verified before activate")

        live = self._deploy
        if not live.exists():
            raise RuntimeError(f"deploy directory does not exist: {live}")

        previous_fp = read_requirements_fingerprint(live)
        next_dir = self._next_dir()
        prev_dir = self._prev_dir()

        if next_dir.exists():
            shutil.rmtree(next_dir)
        next_dir.mkdir(parents=True, exist_ok=True)

        try:
            with tarfile.open(self._verified_bundle, "r:gz") as tar:
                tar.extractall(path=next_dir)
            _assert_bundle_tree(next_dir)
            # Never activate bundle-shipped shop state even if a bad archive contains it.
            for name in _PRESERVE_DIR_NAMES:
                bad = next_dir / name
                if bad.exists():
                    if bad.is_dir():
                        shutil.rmtree(bad)
                    else:
                        bad.unlink()
            for rel in _PRESERVE_FILE_RELATIVE:
                bad = next_dir / rel
                if bad.is_file():
                    bad.unlink()

            # Copy (do not move) shop state so a failed activate can rmtree(next)
            # without destroying live data/credentials.
            _copy_preserved(live, next_dir)

            if prev_dir.exists():
                shutil.rmtree(prev_dir)
            os.rename(live, prev_dir)
            try:
                os.rename(next_dir, live)
            except OSError:
                # Best-effort undo of the first rename so the live path exists again.
                if prev_dir.exists() and not live.exists():
                    os.rename(prev_dir, live)
                raise
        except Exception:
            if next_dir.exists() and next_dir.resolve() != live.resolve():
                shutil.rmtree(next_dir, ignore_errors=True)
            raise

        # Live already holds the working shop state; drop duplicates from prev.
        _remove_preserved(prev_dir)

        new_fp = read_requirements_fingerprint(live)
        self._cleanup_staging()
        logger.info("Activated Release Bundle for %s; previous tree at %s", tag, prev_dir)
        return BundleInstallResult(
            requirements_fingerprint=new_fp,
            previous_requirements_fingerprint=previous_fp,
        )

    def restore_previous_tree(self) -> None:
        """Switch live tree back to the retained previous tree."""
        live = self._deploy
        prev_dir = self._prev_dir()
        next_dir = self._next_dir()
        if not prev_dir.exists():
            raise RuntimeError(f"no previous tree to restore at {prev_dir}")

        # Carry shop state back onto the previous tree before flipping names.
        if live.exists():
            _carry_preserved(live, prev_dir)
            if next_dir.exists():
                shutil.rmtree(next_dir)
            os.rename(live, next_dir)
        os.rename(prev_dir, live)
        if next_dir.exists():
            shutil.rmtree(next_dir, ignore_errors=True)
        logger.info("Restored previous application tree at %s", live)

    def _next_dir(self) -> Path:
        return self._deploy.with_name(self._deploy.name + ".next")

    def _prev_dir(self) -> Path:
        return self._deploy.with_name(self._deploy.name + ".prev")

    def _cleanup_staging(self) -> None:
        if self._staging is not None:
            try:
                self._staging.cleanup()
            except Exception:
                pass
        self._staging = None
        self._verified_bundle = None

    def _download_asset(self, tag: str, name: str, dest: Path) -> None:
        url = (
            f"https://github.com/{self._github_repo}/releases/download/"
            f"{tag}/{name}"
        )
        headers = {"User-Agent": "luyun-update-job"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
            headers["Accept"] = "application/octet-stream"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                dest.write_bytes(resp.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"missing release asset {name} for {tag}: HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"failed to download {name} for {tag}: {exc}") from exc


def verify_bundle_checksum(bundle_path: Path, sums_path: Path) -> None:
    """Hard-fail when SHA256SUMS is missing/unusable or the digest mismatches."""
    if not sums_path.is_file():
        raise RuntimeError(f"missing integrity file {CHECKSUMS_ASSET}")
    try:
        text = sums_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"unable to read {CHECKSUMS_ASSET}: {exc}") from exc

    expected: Optional[str] = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, filename = parts[0], parts[-1]
        # sha256sum may prefix filename with "./"
        base = Path(filename).name
        if base == BUNDLE_ASSET and len(digest) == 64:
            expected = digest.lower()
            break
    if not expected:
        raise RuntimeError(
            f"{CHECKSUMS_ASSET} missing digest entry for {BUNDLE_ASSET}"
        )

    actual = sha256_file(bundle_path)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {BUNDLE_ASSET}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_requirements_fingerprint(deploy_dir: Path) -> Optional[str]:
    """Read requirements_fingerprint from RELEASE_MANIFEST.json when present."""
    path = Path(deploy_dir) / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    value = raw.get("requirements_fingerprint")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _assert_bundle_tree(root: Path) -> None:
    required_files = (
        root / MANIFEST_NAME,
        root / "admin-web" / "dist" / "index.html",
        root / "public" / "kds" / "index.html",
        root / "requirements.txt",
    )
    missing = [str(p.relative_to(root)) for p in required_files if not p.is_file()]
    kds_assets = root / "public" / "kds" / "assets"
    if not kds_assets.is_dir():
        missing.append("public/kds/assets/")
    if missing:
        raise RuntimeError(
            "Release Bundle tree incomplete after extract: " + ", ".join(missing)
        )


def _copy_preserved(src: Path, dest: Path) -> None:
    """Copy shop-local dirs/files from src into dest (overwrite dest side)."""
    for name in _PRESERVE_DIR_NAMES:
        s = src / name
        d = dest / name
        if not s.exists():
            continue
        if d.exists():
            if d.is_dir():
                shutil.rmtree(d)
            else:
                d.unlink()
        if s.is_dir():
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    for rel in _PRESERVE_FILE_RELATIVE:
        s = src / rel
        d = dest / rel
        if not s.is_file():
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            if d.is_dir():
                shutil.rmtree(d)
            else:
                d.unlink()
        shutil.copy2(s, d)


def _carry_preserved(src: Path, dest: Path) -> None:
    """Move shop-local dirs/files from src into dest (overwrite dest side)."""
    for name in _PRESERVE_DIR_NAMES:
        s = src / name
        d = dest / name
        if not s.exists():
            continue
        if d.exists():
            if d.is_dir():
                shutil.rmtree(d)
            else:
                d.unlink()
        shutil.move(str(s), str(d))
    for rel in _PRESERVE_FILE_RELATIVE:
        s = src / rel
        d = dest / rel
        if not s.is_file():
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            if d.is_dir():
                shutil.rmtree(d)
            else:
                d.unlink()
        shutil.move(str(s), str(d))


def _remove_preserved(root: Path) -> None:
    """Drop preserved shop-state paths under a retained previous tree."""
    for name in _PRESERVE_DIR_NAMES:
        path = root / name
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    for rel in _PRESERVE_FILE_RELATIVE:
        path = root / rel
        if path.is_file():
            path.unlink(missing_ok=True)


class PipDepsSyncAdapter:
    """Sync Python deps into the deploy venv after bundle activation."""

    def __init__(self, deploy_dir: Path, *, python_bin: Optional[str] = None) -> None:
        self._deploy = Path(deploy_dir)
        if python_bin:
            self._python = python_bin
        else:
            venv_python = self._deploy / ".venv" / "bin" / "python"
            self._python = str(venv_python) if venv_python.is_file() else "python3"

    def sync(self) -> None:
        req = self._deploy / "requirements.txt"
        if not req.is_file():
            raise RuntimeError("requirements.txt missing after bundle activation")
        cmd = [self._python, "-m", "pip", "install", "-r", str(req)]
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(self._deploy),
                capture_output=True,
                text=True,
                check=False,
                timeout=1800,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"pip sync failed: {exc}") from exc
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(f"pip sync failed: {err}")


class SystemdMainServiceAdapter:
    """Restart the main luyun.service unit."""

    def __init__(self, unit: str = "luyun.service") -> None:
        self._unit = unit

    def restart(self) -> None:
        # Prefer restart; allow env override for dry contract tests.
        override = (os.environ.get("LUYUN_MAIN_SERVICE_CMD") or "").strip()
        if override:
            cmd = ["bash", "-lc", override]
        else:
            cmd = ["systemctl", "restart", self._unit]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"failed to restart main service: {exc}") from exc
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(
                f"failed to restart {self._unit}: {err or completed.returncode}"
            )


class DockerMainServiceAdapter:
    """Restart this container via the Docker Engine API (``/var/run/docker.sock``)."""

    def __init__(
        self,
        *,
        container: Optional[str] = None,
        sock_path: Optional[str] = None,
    ) -> None:
        from services.release_update.deploy_mode import (
            docker_container_name,
            docker_sock_path,
        )

        self._container = (container if container is not None else docker_container_name()).strip()
        self._sock = Path(
            sock_path if sock_path is not None else str(docker_sock_path())
        )

    def restart(self) -> None:
        override = (os.environ.get("LUYUN_MAIN_SERVICE_CMD") or "").strip()
        if override:
            cmd = ["bash", "-lc", override]
            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError(f"failed to restart main service: {exc}") from exc
            if completed.returncode != 0:
                err = (completed.stderr or completed.stdout or "").strip()
                raise RuntimeError(
                    f"failed to restart via LUYUN_MAIN_SERVICE_CMD: {err or completed.returncode}"
                )
            return

        if not self._container:
            raise RuntimeError(
                "LUYUN_DOCKER_CONTAINER is not set "
                "(Docker Update Job needs the container name, e.g. luyun-order)"
            )
        if not self._sock.exists():
            raise RuntimeError(
                f"Docker socket not found at {self._sock}; "
                "mount the host docker.sock into the container (read-write)"
            )

        import httpx

        # Docker Engine API over UDS; path-style container name is URL-safe for
        # typical 1Panel names (alphanumeric + dash).
        transport = httpx.HTTPTransport(uds=str(self._sock))
        url = f"/containers/{self._container}/restart"
        try:
            with httpx.Client(
                transport=transport,
                base_url="http://localhost",
                timeout=120.0,
            ) as client:
                resp = client.post(url)
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"failed to restart Docker container {self._container!r}: {exc}"
            ) from exc

        if resp.status_code >= 400:
            body = (resp.text or "").strip()[:300]
            raise RuntimeError(
                f"Docker restart {self._container!r} HTTP {resp.status_code}: {body}"
            )
        logger.info("Restarted Docker container %s", self._container)


def build_main_service_adapter():
    """Pick systemd or Docker restart adapter from deploy mode."""
    from services.release_update.deploy_mode import resolve_deploy_mode

    if resolve_deploy_mode() == "docker":
        return DockerMainServiceAdapter()
    return SystemdMainServiceAdapter()
