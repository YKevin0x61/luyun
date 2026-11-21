#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出任务注册表：四份导出共用的一台状态机。

标准图包、照片存档包、系统备份包原先各写一份 dict + TTL + 并发上限 + 临时文件
清理。这里把任务生命周期收成一个深模块：调用方只负责「把文件打出来」，注册表负责
状态、并发、过期、失败清理和下载路径。HTTP 字段投影仍留在各自的路由里，注册表不
认识 FastAPI。

一个任务的生命周期：

    start(kind, build, max_running=N)
      -> 专属 workdir + JobHandle
      -> build(handle) 写成品，返回 JobResult(artifact, filename, retain, extra)
      -> 完成/失败时起算 TTL；失败立即清 workdir，记录留到过期
      -> 过期删临时成品/工作目录；retain=True 只清记录，不删成品

运行中的任务不参与 TTL 清理（避免删正在写的包），另有一条硬上限兜住卡死；启动时
再按 TTL 清一次上次进程留下的孤儿目录。单 worker 部署下任务表放内存即可，重启丢了
重来（ADR 0089 / 0155）。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"

DEFAULT_TTL_SECONDS = 30 * 60
DEFAULT_MAX_RUNTIME_SECONDS = 2 * 60 * 60

KIND_STANDARDS = "standards"
KIND_ARCHIVE = "archive"
KIND_BACKUP = "backup"
KIND_PURCHASE_ANALYSIS = "purchase_analysis"

_SNAPSHOT_KEYS = {
    "job_id",
    "kind",
    "state",
    "done",
    "total",
    "stage",
    "unit",
    "error",
    "bytes",
    "filename",
    "artifact",
    "started_at",
    "finished_at",
    "expires_at",
    "retain",
}


class ExportJobError(Exception):
    """注册表错误的基类；路由按类型映射 HTTP 状态。"""

    code = "export_job_error"


class ExportJobNotFound(ExportJobError):
    code = "not_found"

    def __init__(self, message: str = "导出任务不存在或已过期") -> None:
        super().__init__(message)


class ExportJobNotReady(ExportJobError):
    code = "not_ready"

    def __init__(self, message: str = "还在打包，请稍候") -> None:
        super().__init__(message)


class ExportJobFailed(ExportJobError):
    """打包器对已知失败抛它；注册表原样写进 error。"""

    code = "failed"

    def __init__(self, message: str = "导出失败") -> None:
        super().__init__(message)


class ExportJobGone(ExportJobError):
    code = "gone"

    def __init__(self, message: str = "导出文件已被清理，请重新导出") -> None:
        super().__init__(message)


class ExportJobBusy(ExportJobError):
    code = "busy"

    def __init__(self, message: str = "导出任务太多，请稍候") -> None:
        super().__init__(message)


@dataclass
class JobResult:
    """打包器的产出：成品路径、下载名、是否保留，以及路由要透出的附加字段。"""

    artifact: Path
    filename: str
    retain: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JobHandle:
    """交给打包器的窄接口：专属目录 + 进度回调。"""

    job_id: str
    kind: str
    workdir: Path
    progress: Callable[..., None]


BuildFn = Callable[[JobHandle], Awaitable[JobResult]]


class ExportJobs:
    """进程内导出任务表。测试可 new 独立实例、注入假时钟与临时根目录。"""

    def __init__(
        self,
        root: Optional[Path] = None,
        *,
        clock: Callable[[], float] = time.time,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_runtime_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS,
    ) -> None:
        self._root = Path(root) if root is not None else None
        self._clock = clock
        self._ttl = ttl_seconds
        self._max_runtime = max_runtime_seconds
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._timers: Dict[str, asyncio.TimerHandle] = {}

    # ---- 生命周期 -----------------------------------------------------

    def _root_path(self) -> Path:
        if self._root is not None:
            return self._root
        # 懒解析：测试会换 settings.DATABASE_DIR；import 期不建目录、不读配置。
        from config import settings

        return Path(settings.DATABASE_DIR) / "export-tmp"

    def start(
        self,
        kind: str,
        build: BuildFn,
        *,
        max_running: int,
        ttl_seconds: Optional[int] = None,
        max_runtime_seconds: Optional[float] = None,
    ) -> str:
        """登记一个任务并立即返回 job_id；同 kind 的运行中任务超过上限时抛 Busy。"""
        if max_running < 1:
            raise ValueError("max_running 必须大于 0")
        now = self._clock()
        self._sweep_finished(now)
        running = sum(
            1
            for job in self._jobs.values()
            if job["kind"] == kind and job["state"] == STATE_RUNNING
        )
        if running >= max_running:
            raise ExportJobBusy()

        job_id = uuid.uuid4().hex
        root = self._root_path()
        root.mkdir(parents=True, exist_ok=True)
        workdir = root / job_id
        workdir.mkdir()

        job: Dict[str, Any] = {
            "job_id": job_id,
            "kind": kind,
            "state": STATE_RUNNING,
            "done": 0,
            "total": 0,
            "stage": "collecting",
            "unit": "count",
            "error": "",
            "bytes": 0,
            "filename": "",
            "artifact": None,
            "retain": False,
            "extra": {},
            "started_at": now,
            "finished_at": None,
            "expires_at": None,
            "_build": build,
            "_workdir": workdir,
            "_ttl": self._ttl if ttl_seconds is None else ttl_seconds,
            "_max_runtime": (
                self._max_runtime
                if max_runtime_seconds is None
                else max_runtime_seconds
            ),
        }

        def progress(
            done: int = 0,
            total: int = 0,
            *,
            stage: Optional[str] = None,
            unit: Optional[str] = None,
        ) -> None:
            if job["state"] != STATE_RUNNING:
                return
            if done is not None:
                job["done"] = int(done)
            if total is not None:
                job["total"] = int(total)
            if stage is not None:
                job["stage"] = str(stage)
            if unit is not None:
                job["unit"] = str(unit)

        job["handle"] = JobHandle(job_id, kind, workdir, progress)
        self._jobs[job_id] = job

        loop = asyncio.get_running_loop()
        self._tasks[job_id] = loop.create_task(
            self._run(job_id), name=f"export-job:{kind}:{job_id}"
        )
        self._timers[job_id] = loop.call_later(
            job["_max_runtime"], self._enforce_runtime, job_id
        )
        return job_id

    async def _run(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        try:
            result = await job["_build"](job["handle"])
            if not isinstance(result, JobResult):
                raise ExportJobFailed("导出器没有返回结果")
            artifact = Path(result.artifact)
            if not artifact.is_file():
                raise ExportJobFailed("导出文件没有生成")
            extra = dict(result.extra or {})
            for key in extra:
                if key in _SNAPSHOT_KEYS:
                    raise ExportJobFailed(f"导出结果字段冲突：{key}")
            job.update(
                state=STATE_DONE,
                artifact=artifact,
                filename=result.filename or artifact.name,
                bytes=artifact.stat().st_size,
                retain=bool(result.retain),
                extra=extra,
            )
        except asyncio.CancelledError:
            if job["state"] == STATE_RUNNING:
                job.update(
                    state=STATE_FAILED,
                    error=job.pop("_cancel_reason", "导出已取消"),
                )
            self._finalize(job_id)
            raise
        except ExportJobFailed as exc:
            job.update(state=STATE_FAILED, error=str(exc) or "导出失败")
        except Exception as exc:  # noqa: BLE001 —— 后台任务必须自己收口
            logger.exception("导出任务失败 job=%s kind=%s", job_id, job["kind"])
            job.update(state=STATE_FAILED, error=f"导出失败：{exc}")
        self._finalize(job_id)

    def _finalize(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        timer = self._timers.pop(job_id, None)
        if timer is not None:
            timer.cancel()
        self._tasks.pop(job_id, None)
        now = self._clock()
        job["finished_at"] = now
        job["expires_at"] = now + job["_ttl"]
        self._cleanup_workdir(job)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._timers[job_id] = loop.call_later(
            job["_ttl"], self._expire, job_id
        )

    def _cleanup_workdir(self, job: Dict[str, Any]) -> None:
        """失败立即清；成功的临时成品留到过期；retain 的成品不碰。"""
        workdir = Path(job["_workdir"])
        artifact = job.get("artifact")
        if job["state"] == STATE_FAILED:
            shutil.rmtree(workdir, ignore_errors=True)
            return
        if job.get("retain"):
            if artifact is None or not _is_within(Path(artifact), workdir):
                shutil.rmtree(workdir, ignore_errors=True)
            return
        if artifact is not None and not _is_within(Path(artifact), workdir):
            shutil.rmtree(workdir, ignore_errors=True)

    def _enforce_runtime(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None or job["state"] != STATE_RUNNING:
            return
        job["_cancel_reason"] = (
            f"导出超时（超过 {int(job['_max_runtime'] // 60)} 分钟），已取消"
        )
        task = self._tasks.get(job_id)
        if task is not None:
            task.cancel()

    def _expire(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        if job["state"] == STATE_RUNNING:
            return
        expires_at = job.get("expires_at")
        now = self._clock()
        if expires_at is not None and now < expires_at:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._timers[job_id] = loop.call_later(
                expires_at - now, self._expire, job_id
            )
            return
        self._remove(job_id)

    def _remove(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job is None:
            return
        timer = self._timers.pop(job_id, None)
        if timer is not None:
            timer.cancel()
        task = self._tasks.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()
        workdir = Path(job["_workdir"])
        artifact = job.get("artifact")
        if artifact is not None and not job.get("retain"):
            try:
                Path(artifact).unlink(missing_ok=True)
            except OSError:
                logger.debug("清理导出成品失败 job=%s", job_id, exc_info=True)
        if job.get("retain") and artifact is not None and _is_within(Path(artifact), workdir):
            return
        shutil.rmtree(workdir, ignore_errors=True)

    # ---- 查询 / 清理 --------------------------------------------------

    def state(self, job_id: str) -> Dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise ExportJobNotFound()
        return self._snapshot(job)

    def path(self, job_id: str) -> Path:
        snapshot = self.state(job_id)
        if snapshot["state"] == STATE_FAILED:
            raise ExportJobFailed(snapshot.get("error") or "导出失败")
        if snapshot["state"] != STATE_DONE:
            raise ExportJobNotReady()
        artifact = snapshot.get("artifact")
        if artifact is None or not Path(artifact).is_file():
            raise ExportJobGone()
        return Path(artifact)

    def _snapshot(self, job: Dict[str, Any]) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "job_id": job["job_id"],
            "kind": job["kind"],
            "state": job["state"],
            "done": job["done"],
            "total": job["total"],
            "stage": job["stage"],
            "unit": job["unit"],
            "error": job["error"],
            "bytes": job["bytes"],
            "filename": job["filename"],
            "artifact": job["artifact"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "expires_at": job["expires_at"],
            "retain": job["retain"],
        }
        for key, value in (job.get("extra") or {}).items():
            if key not in data:
                data[key] = value
        return data

    def _sweep_finished(self, now: float) -> int:
        removed = 0
        for job_id, job in list(self._jobs.items()):
            if job["state"] == STATE_RUNNING:
                continue
            expires_at = job.get("expires_at")
            if expires_at is not None and now >= expires_at:
                self._remove(job_id)
                removed += 1
        return removed

    def sweep(self, now: Optional[float] = None) -> int:
        """清过期记录，并给卡死的运行中任务收口。定时器和启动清理都走这里。"""
        current = self._clock() if now is None else now
        removed = self._sweep_finished(current)
        for job_id, job in list(self._jobs.items()):
            if job["state"] != STATE_RUNNING:
                continue
            if current - job["started_at"] >= job["_max_runtime"]:
                self._enforce_runtime(job_id)
        return removed

    def sweep_orphans(self, older_than: Optional[float] = None) -> int:
        """启动时清 export-tmp 下没有任务记录、且超过 TTL 的目录。"""
        root = self._root_path()
        if not root.is_dir():
            return 0
        cutoff = self._clock() - (self._ttl if older_than is None else older_than)
        removed = 0
        for entry in list(root.iterdir()):
            if entry.name in self._jobs:
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime > cutoff:
                continue
            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink(missing_ok=True)
            except OSError:
                logger.debug("清理导出孤儿失败 path=%s", entry, exc_info=True)
                continue
            removed += 1
        return removed

    def reset(self) -> None:
        """测试/关停用：取消所有任务与定时器，清掉文件。"""
        for job_id in list(self._jobs):
            self._remove(job_id)
        self._jobs.clear()
        self._tasks.clear()
        self._timers.clear()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        return path.resolve().is_relative_to(parent.resolve())
    except (OSError, ValueError):
        return False


export_jobs = ExportJobs()