#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real adapters used by the Update Job oneshot (bundle / backup / deps / migrations / restart)."""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import select
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from config import settings
from services import backup_service
from services.playwright_env import (
    BrowserCacheStatus,
    PlaywrightBrowserDriftError,
    browser_cache_status,
    ensure_chromium_installed_sync,
    format_degraded_alert,
    repair_hint,
    strict_mode_enabled,
)
from services.release_update.job_runner import BundleInstallResult, MigrationApplyOutcome
from services.release_update.job_state import is_cancel_requested, job_log_path
from services.release_update.manifest_identity import MANIFEST_NAME

PIP_SYNC_TIMEOUT_SECONDS = 1800
PIP_POLL_SECONDS = 0.5

# 应用待执行迁移：入口、结果标记与超时（建索引这类 DDL 可能不快，给足 30 分钟）。
MIGRATIONS_ENTRY_RELATIVE = Path("scripts") / "apply_db_migrations.py"
MIGRATION_RESULT_PREFIX = "LUYUN_DB_MIGRATION_RESULT "
MIGRATION_APPLY_TIMEOUT_SECONDS = 1800

logger = logging.getLogger(__name__)

BUNDLE_ASSET = "luyun-release-bundle.tar.gz"
CHECKSUMS_ASSET = "SHA256SUMS"

# ── Release 资产下载：断点续传 + 退避重试 ────────────────────────────────────
# 为什么需要这一段：GitHub 的 Release 资产实际由 `objects.githubusercontent.com`
# 提供，在弱网或被中间设备干扰的链路上经常下到一半被掐断 —— 异常是
# `http.client.RemoteDisconnected: Remote end closed connection without response`
# 或 `URLError: <urlopen error [Errno 110] Connection timed out>`。
# 原来一次 `urlopen(...).read()` 没有任何重试，撞上就整单作业失败：门店实测更新到
# v0.8.4 连着三次都挂在这两个错上（包本身在 GitHub 上完好：5.87MB、state=uploaded、
# digest 与本地 SHA256SUMS 一致），而同一台机器升 v0.8.3 时下载成功过一次（耗时 349s）
# —— 链路是"能通但很慢、时不时断"，所以重试 + 续传就能过去。
# 每次尝试都从**已落盘的字节**继续（`Range: bytes=N-`），失败按指数退避重来。
DOWNLOAD_ATTEMPTS = 5
DOWNLOAD_BACKOFF_SECONDS = 2.0  # 2 / 4 / 8 / 16 秒
DOWNLOAD_TIMEOUT_SECONDS = 120  # 单次尝试的 socket 超时（不是总时长）
DOWNLOAD_CHUNK_BYTES = 256 * 1024


class _DownloadIncomplete(RuntimeError):
    """一次尝试读到了 0 字节 —— 交给 `_download_asset` 的退避循环重试，不直接冒到作业层。"""

# Shop state that must survive atomic application-tree swap (never from the bundle).
_PRESERVE_DIR_NAMES = ("data", ".venv", "venv", "secrets")
_PRESERVE_FILE_RELATIVE = (
    ".env",
    "deploy/env.production",
)


class SnapshotBackupAdapter:
    """Mandatory backup via local restore snapshot (no passphrase export)."""

    def run_backup(self) -> str:
        creds = backup_service.get_credentials_file_path()
        try:
            return backup_service.create_restore_snapshot(
                creds,
                provenance=backup_service.PROVENANCE_PRE_UPDATE,
            )
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
                _safe_extract_bundle(tar, next_dir)
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

    def _download_base(self) -> str:
        """资产下载的前缀，默认 GitHub。

        弱网 / 被墙的门店可以把它指到自建镜像或代理（`RELEASE_DOWNLOAD_BASE`），拼法不变：
        `{base}/{repo}/releases/download/{tag}/{name}`。另外 `urllib` 默认尊重
        `https_proxy` 环境变量，所以给作业进程配代理也是通的 —— 两条逃生通道都写在
        `deploy/README.md`。"""
        base = (settings.RELEASE_DOWNLOAD_BASE or "").strip().rstrip("/")
        return base or "https://github.com"

    def _download_asset(self, tag: str, name: str, dest: Path) -> None:
        """把一份 Release 资产拉到 `dest`：分块写、失败从已下载处续传、退避重试。

        返回时文件**只保证是完整读到的**（字节数 > 0），完整性由调用方的
        `verify_bundle_checksum` 按 SHA256SUMS 硬校验 —— 续传错位会被那一步抓住。
        """
        url = f"{self._download_base()}/{self._github_repo}/releases/download/{tag}/{name}"
        last_exc: Optional[BaseException] = None
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            done = dest.stat().st_size if dest.is_file() else 0
            headers = {"User-Agent": "luyun-update-job"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
                headers["Accept"] = "application/octet-stream"
            if done:
                headers["Range"] = f"bytes={done}-"
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:
                    # 206 = 服务端接受续传（接着写）；200 = 它给的仍是整份 —— 那就从头写，
                    # 否则新旧内容会拼在一起。
                    resume = bool(done) and getattr(resp, "status", 200) == 206
                    if not resume:
                        done = 0
                    with dest.open("ab" if resume else "wb") as handle:
                        while True:
                            chunk = resp.read(DOWNLOAD_CHUNK_BYTES)
                            if not chunk:
                                break
                            handle.write(chunk)
                            done += len(chunk)
                if done <= 0:
                    raise _DownloadIncomplete(f"downloaded 0 bytes for {name}")
                logger.info(
                    "Downloaded %s: %d bytes%s", name, done,
                    f" (attempt {attempt})" if attempt > 1 else "",
                )
                return
            except urllib.error.HTTPError as exc:
                # 4xx 是「资产不存在 / 没权限」，重试没有意义；5xx 与 429 值得重来。
                if exc.code < 500 and exc.code != 429:
                    raise RuntimeError(
                        f"missing release asset {name} for {tag}: HTTP {exc.code}"
                    ) from exc
                last_exc = exc
            except (
                _DownloadIncomplete,
                urllib.error.URLError,
                http.client.HTTPException,
                OSError,
            ) as exc:
                last_exc = exc
            if attempt < DOWNLOAD_ATTEMPTS:
                delay = DOWNLOAD_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Download of %s failed (attempt %d/%d, %d bytes so far): %s — retrying in %.0fs",
                    name, attempt, DOWNLOAD_ATTEMPTS,
                    dest.stat().st_size if dest.is_file() else 0, last_exc, delay,
                )
                time.sleep(delay)
        raise RuntimeError(
            f"failed to download {name} for {tag} after {DOWNLOAD_ATTEMPTS} attempts: {last_exc}"
        ) from last_exc


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


def _safe_extract_bundle(tar: tarfile.TarFile, dest: Path) -> None:
    """把发行包解到 dest，拒绝任何逃出 dest 的成员。

    下载字节与 SHA256SUMS 同源，校验和防不住被投毒的发行包；这里再挡一层：
    绝对路径、`..` 逃逸、符号/硬链接与设备文件一律拒绝，普通文件与目录才解包。
    （Python 3.12+ 的 extractall(filter="data") 语义类似，这里手写以便跨版本一致并可单测。）
    """
    dest = Path(dest).resolve()
    for member in tar.getmembers():
        name = member.name
        if not name or name.startswith("/") or Path(name).is_absolute():
            raise RuntimeError(f"发行包含非法成员路径：{name}")
        if ".." in Path(name).parts:
            raise RuntimeError(f"发行包成员试图逃出安装目录：{name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise RuntimeError(f"发行包含不支持的成员类型：{name}")
        target = (dest / name).resolve()
        if target != dest and dest not in target.parents:
            raise RuntimeError(f"发行包成员逃出安装目录：{name}")
    tar.extractall(path=dest)


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


def _terminate_process_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    pid = proc.pid
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def resolve_deploy_python(
    deploy_dir: Path, python_bin: Optional[str] = None
) -> str:
    """Deploy venv interpreter, falling back to python3 when the venv is absent."""
    if python_bin:
        return python_bin
    venv_python = Path(deploy_dir) / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.is_file() else "python3"


class PipDepsSyncAdapter:
    """Sync Python deps into the deploy venv after bundle activation."""

    def __init__(
        self,
        deploy_dir: Path,
        *,
        python_bin: Optional[str] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
        log_path: Optional[Path] = None,
        timeout_seconds: int = PIP_SYNC_TIMEOUT_SECONDS,
    ) -> None:
        self._deploy = Path(deploy_dir)
        self._python = resolve_deploy_python(self._deploy, python_bin)
        self._is_cancelled = is_cancelled or is_cancel_requested
        self._log_path = Path(log_path) if log_path is not None else job_log_path()
        self._timeout_seconds = timeout_seconds

    def sync(self) -> None:
        req = self._deploy / "requirements.txt"
        if not req.is_file():
            raise RuntimeError("requirements.txt missing after bundle activation")
        cmd = [self._python, "-m", "pip", "install", "-r", str(req)]
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._log_path, "a", encoding="utf-8") as log_fh:
                log_fh.write("\n--- pip sync ---\n")
                log_fh.write(f"$ {' '.join(cmd)}\n")
                log_fh.flush()
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(self._deploy),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                assert proc.stdout is not None
                deadline = time.monotonic() + self._timeout_seconds
                while True:
                    if self._is_cancelled():
                        log_fh.write("\n[update] cancel requested — stopping pip\n")
                        log_fh.flush()
                        _terminate_process_group(proc)
                        raise RuntimeError("cancelled by operator")
                    if time.monotonic() > deadline:
                        log_fh.write("\n[update] pip sync timed out\n")
                        log_fh.flush()
                        _terminate_process_group(proc)
                        raise RuntimeError("pip sync timed out")
                    ready, _, _ = select.select([proc.stdout], [], [], PIP_POLL_SECONDS)
                    if ready:
                        line = proc.stdout.readline()
                        if line:
                            log_fh.write(line)
                            log_fh.flush()
                        elif proc.poll() is not None:
                            break
                    elif proc.poll() is not None:
                        # Drain any remaining buffered output.
                        rest = proc.stdout.read()
                        if rest:
                            log_fh.write(rest)
                            log_fh.flush()
                        break
                rc = proc.wait(timeout=5)
        except RuntimeError:
            raise
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"pip sync failed: {exc}") from exc
        if rc != 0:
            raise RuntimeError(f"pip sync failed: exit {rc} (see update_job.log)")


class PlaywrightBrowserSyncAdapter:
    """Keep the deploy venv's Playwright browsers aligned with its lib version.

    升级 playwright lib 后浏览器 build 必须同步更换，否则 scraper 报
    ``Executable doesn't exist at .../chromium_headless_shell-<rev>/...``。

    失败**分两类，处理故意不同**（票 10 / PERF-12）：

    - 补装命令失败（网络抖动、超时、磁盘满）：**不抛**。浏览器缺失由主服务启动后的
      自愈兜底（``PosSession._init_browser``），不该让一次网络抖动卡死整台店的更新
      （``tests/test_release_update_browser_sync.py`` 也钉住了这一点）。但也不再只是
      进程日志里的一句 warning：写一行结构化降级告警进**更新作业日志**——

          [update][DEGRADED] {"degraded": "playwright_browser_missing", ...}

      而 ``GET /api/release-update/job`` 的 ``log_tail`` 会把它送进 Admin
      「系统更新」面板，运维在更新当场就能看到，不必等采集器启动报错才发现方向错了。
    - 补装命令自称成功、缓存里**仍然**缺目标 build（确定性漂移，例如装到了别的
      ``PLAYWRIGHT_BROWSERS_PATH``）：同样写结构化告警；若置
      ``LUYUN_PLAYWRIGHT_STRICT_BROWSERS=1``（:func:`strict_mode_enabled`）则抛
      :class:`~services.playwright_env.PlaywrightBrowserDriftError`，走 ``job_runner``
      既有的失败路径（作业标红 + 回滚），把「更新说成功、采集器起不来」扭回来。
      默认关：与上面同一条决策对齐，但运维留了一把硬开关。
    """

    def __init__(
        self,
        deploy_dir: Path,
        *,
        python_bin: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        log_path: Optional[Path] = None,
        strict: Optional[bool] = None,
    ) -> None:
        self._deploy = Path(deploy_dir)
        self._python = resolve_deploy_python(self._deploy, python_bin)
        self._timeout_seconds = (
            timeout_seconds or settings.PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS
        )
        self._log_path = Path(log_path) if log_path is not None else job_log_path()
        self._strict = strict_mode_enabled() if strict is None else strict
        #: 最近一次同步的校验结论（手工排查/用例可直接读，不必解析日志）。
        self.last_status: Optional[BrowserCacheStatus] = None

    def sync(self) -> None:
        logger.info("Ensuring Playwright browsers match %s", self._python)
        install_ok = ensure_chromium_installed_sync(
            python_bin=self._python,
            timeout_seconds=self._timeout_seconds,
            log=logger,
        )
        status = browser_cache_status()
        self.last_status = status
        if status.ok:
            if not install_ok:
                logger.warning(
                    "Playwright 补装命令失败，但缓存里目标 build 已存在（%s）；继续更新",
                    status.cache_dir,
                )
            return
        alert = format_degraded_alert(
            status,
            phase="playwright_browser_sync",
            python_bin=self._python,
            install_ok=install_ok,
            strict=self._strict,
            repair=repair_hint(self._python),
        )
        self._append_job_log(alert)
        if install_ok and self._strict:
            logger.error("Playwright 浏览器漂移（严格模式）: %s", status.describe())
            raise PlaywrightBrowserDriftError(status)
        logger.error(
            "Playwright 浏览器不可用（install_ok=%s, lib=%s）: %s",
            install_ok,
            self._python,
            status.describe(),
        )

    def _append_job_log(self, line: str) -> None:
        """把结构化告警写进更新作业日志；日志写不进去也不能让更新崩掉。"""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as log_fh:
                log_fh.write("\n--- playwright browser sync ---\n")
                log_fh.write(line + "\n")
                log_fh.flush()
        except OSError as exc:
            logger.error("写更新作业日志失败（告警只在进程日志里）: %s", exc)


def parse_migration_result(stdout: str) -> Optional[dict]:
    """从入口的 stdout 里取结果标记（最后一行）；缺失或不是合法 JSON 时返回 None。"""
    for line in reversed((stdout or "").splitlines()):
        if not line.startswith(MIGRATION_RESULT_PREFIX):
            continue
        try:
            payload = json.loads(line[len(MIGRATION_RESULT_PREFIX):])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


class DeployTreeMigrationsAdapter:
    """用**新树自己的代码与解释器**应用待执行迁移（ADR 0096）。

    为什么不在作业进程里 import 自己那份 ``services.db_migrations``：作业进程在原子
    切换之前就启动了，跑的是**旧树**的代码，而 ``migrations/pg/`` 是按
    ``db_migrations.py`` 自己的模块文件定位的——旧树那份读不到随发行包刚下来的新迁移
    文件。所以这里把新树里的 ``scripts/apply_db_migrations.py`` 当执行体：用部署 venv
    的解释器，以新树为工作目录（``.env`` 相对路径也才落在新树）与包搜索路径首位跑一遍。

    三条路径（都有用例）：
    * 有待应用：入口应用并报告版本列表；
    * 无待应用：入口报告空列表，阶段跳过——作业照常重启，不是失败；
    * 失败（SQL 报错 / 连不上库 / 超时）：抛 :class:`RuntimeError`，作业走既有的
      失败 + ``restore_previous_tree`` 回滚路径，原因同时写进作业日志。

    入口不存在（回退到本阶段出现之前的发行包）：不改库，返回带说明的跳过——「回到上
    一版本」这条合法退路不该被一个当时还不存在的脚本挡住，手工「数据库迁移」入口仍是
    兜底。

    这里**不**在子进程运行期间轮询取消标志：一个迁移文件整体在 PG 的隐式事务里，中途
    掐连接只会白跑一趟；让当前文件跑完更可控，阶段结束后 ``job_runner`` 那句取消检查
    照旧生效（已切树，会走回滚）。

    入口的输出在它退出后一次性写进作业日志：迁移脚本本身不打进度，真正需要看实时流的
    是 pip 那条（见 :class:`PipDepsSyncAdapter`）；进度层面门店看到的是作业状态里的
    ``applying_migrations`` 阶段。
    """

    def __init__(
        self,
        deploy_dir: Path,
        *,
        python_bin: Optional[str] = None,
        log_path: Optional[Path] = None,
        timeout_seconds: int = MIGRATION_APPLY_TIMEOUT_SECONDS,
    ) -> None:
        self._deploy = Path(deploy_dir)
        self._python = resolve_deploy_python(self._deploy, python_bin)
        self._log_path = Path(log_path) if log_path is not None else job_log_path()
        self._timeout_seconds = timeout_seconds

    def apply_pending(self) -> MigrationApplyOutcome:
        entry = self._deploy / MIGRATIONS_ENTRY_RELATIVE
        if not entry.is_file():
            note = (
                f"发行包内没有迁移入口（{MIGRATIONS_ENTRY_RELATIVE.as_posix()}），"
                "本阶段跳过；如需应用请用「系统更新 → 数据库迁移」入口"
            )
            self._append_job_log(f"--- apply db migrations ---\n{note}")
            logger.info("%s", note)
            return MigrationApplyOutcome(note=note)

        env = dict(os.environ)
        inherited = (env.get("PYTHONPATH") or "").strip()
        env["PYTHONPATH"] = (
            os.pathsep.join([str(self._deploy), inherited]) if inherited else str(self._deploy)
        )
        cmd = [self._python, str(entry)]
        logger.info("Applying pending migrations from %s", self._deploy)
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(self._deploy),
                env=env,
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            self._append_job_log(
                f"--- apply db migrations ---\n$ {' '.join(cmd)}\n"
                f"[update] timed out after {self._timeout_seconds}s\n"
            )
            raise RuntimeError(
                f"applying pending migrations timed out after {self._timeout_seconds}s"
            ) from None
        except OSError as exc:
            raise RuntimeError(f"failed to run pending migrations: {exc}") from exc

        self._append_job_log(
            f"--- apply db migrations ---\n$ {' '.join(cmd)}\n"
            f"{completed.stdout or ''}{completed.stderr or ''}"
        )
        payload = parse_migration_result(completed.stdout)
        if completed.returncode != 0 or payload is None or not payload.get("ok"):
            raise RuntimeError(self._failure_message(completed, payload))
        applied = tuple(str(version) for version in (payload.get("applied") or []))
        logger.info("已应用待执行迁移：%s", list(applied))
        return MigrationApplyOutcome(applied=applied)

    @staticmethod
    def _failure_message(
        completed: subprocess.CompletedProcess,
        payload: Optional[dict],
    ) -> str:
        prefix = "applying pending migrations failed"
        failed = (payload or {}).get("failed") or []
        if failed:
            first = failed[0] or {}
            name = first.get("filename") or first.get("version") or "迁移"
            return f"{prefix}: {name}: {first.get('error')}"
        if payload and payload.get("error"):
            return f"{prefix}: {payload['error']}"
        # 没有结果标记（入口半路崩掉/被顶掉）：至少留下退出码与最后一行输出。
        lines = [
            line.strip()
            for line in f"{completed.stderr or ''}\n{completed.stdout or ''}".splitlines()
            if line.strip()
        ]
        detail = lines[-1] if lines else "no output"
        return f"{prefix}: exit {completed.returncode}: {detail}"

    def _append_job_log(self, text: str) -> None:
        """把入口的输出写进更新作业日志；日志写不进去也不能让更新崩掉。"""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as log_fh:
                log_fh.write("\n" + text.rstrip("\n") + "\n")
                log_fh.flush()
        except OSError as exc:
            logger.error("写更新作业日志失败（迁移原因只在进程日志里）: %s", exc)


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
