#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出任务注册表的契约：状态、TTL、并发、失败清理与源码守卫。

三份导出（标准图包、照片存档包、系统备份包）共用这一台状态机；这里只测注册表
本身，三个 HTTP 入口的字段投影仍由各自的用例钉住。
"""

from __future__ import annotations

import ast
import asyncio
import os
from pathlib import Path

import pytest

from services.export_jobs import (
    STATE_DONE,
    STATE_FAILED,
    STATE_RUNNING,
    ExportJobBusy,
    ExportJobFailed,
    ExportJobGone,
    ExportJobNotFound,
    ExportJobNotReady,
    ExportJobs,
    JobResult,
)


def _run(coro):
    """项目没有 pytest-asyncio；每条用例自己起 loop，结束时清掉未完成 task。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


async def _wait(jobs: ExportJobs, job_id: str, timeout: float = 3.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        snapshot = jobs.state(job_id)
        if snapshot["state"] != STATE_RUNNING:
            return snapshot
        await asyncio.sleep(0.01)
    raise AssertionError(f"导出任务没有在 {timeout}s 内结束")


def _jobs(tmp_path: Path, **kwargs) -> ExportJobs:
    return ExportJobs(root=tmp_path / "export-tmp", **kwargs)


def test_start_runs_builder_and_exposes_result(tmp_path):
    async def main():
        jobs = _jobs(tmp_path)
        artifact = None

        async def build(handle):
            nonlocal artifact
            handle.progress(done=1, total=3, stage="packing", unit="count")
            artifact = handle.workdir / "out.zip"
            artifact.write_bytes(b"zip")
            return JobResult(
                artifact=artifact,
                filename="标准图-20261120.zip",
                extra={"count": 3, "skipped": 1},
            )

        job_id = jobs.start("standards", build, max_running=2)
        snapshot = await _wait(jobs, job_id)
        assert snapshot["state"] == STATE_DONE
        assert snapshot["done"] == 1 and snapshot["total"] == 3
        assert snapshot["stage"] == "packing" and snapshot["unit"] == "count"
        assert snapshot["filename"] == "标准图-20261120.zip"
        assert snapshot["bytes"] == 3
        assert snapshot["count"] == 3 and snapshot["skipped"] == 1
        assert jobs.path(job_id) == artifact

    _run(main())


def test_two_instances_do_not_share_jobs(tmp_path):
    async def main():
        one = _jobs(tmp_path / "one")
        two = _jobs(tmp_path / "two")

        async def build(handle):
            artifact = handle.workdir / "out.bin"
            artifact.write_bytes(b"x")
            return JobResult(artifact, "out.bin")

        job_id = one.start("standards", build, max_running=1)
        await _wait(one, job_id)
        with pytest.raises(ExportJobNotFound):
            two.state(job_id)

    _run(main())


def test_same_kind_is_limited_but_other_kinds_are_not(tmp_path):
    async def main():
        jobs = _jobs(tmp_path)
        release = asyncio.Event()

        async def build(handle):
            await release.wait()
            artifact = handle.workdir / "out.bin"
            artifact.write_bytes(b"x")
            return JobResult(artifact, "out.bin")

        first = jobs.start("standards", build, max_running=1)
        with pytest.raises(ExportJobBusy):
            jobs.start("standards", build, max_running=1)
        other = jobs.start("archive", build, max_running=1)
        release.set()
        await _wait(jobs, first)
        await _wait(jobs, other)

    _run(main())


def test_ttl_starts_at_finish_and_removes_the_file(tmp_path):
    async def main():
        clock = [1000.0]
        jobs = _jobs(tmp_path, clock=lambda: clock[0], ttl_seconds=60)
        artifact = None

        async def build(handle):
            nonlocal artifact
            artifact = handle.workdir / "out.zip"
            artifact.write_bytes(b"zip")
            return JobResult(artifact, "out.zip")

        job_id = jobs.start("standards", build, max_running=1)
        snapshot = await _wait(jobs, job_id)
        assert snapshot["expires_at"] == 1060.0

        clock[0] = 1059.0
        jobs.sweep()
        assert jobs.state(job_id)["state"] == STATE_DONE
        assert artifact.exists()

        clock[0] = 1061.0
        jobs.sweep()
        with pytest.raises(ExportJobNotFound):
            jobs.state(job_id)
        assert not artifact.exists()

    _run(main())


def test_failed_job_keeps_the_record_but_cleans_the_workdir(tmp_path):
    async def main():
        jobs = _jobs(tmp_path)
        workdir = None

        async def build(handle):
            nonlocal workdir
            workdir = handle.workdir
            (workdir / "partial.bin").write_bytes(b"half")
            raise ExportJobFailed("没有可导出的记录")

        job_id = jobs.start("archive", build, max_running=1)
        snapshot = await _wait(jobs, job_id)
        assert snapshot["state"] == STATE_FAILED
        assert snapshot["error"] == "没有可导出的记录"
        assert not workdir.exists()
        with pytest.raises(ExportJobFailed) as raised:
            jobs.path(job_id)
        assert str(raised.value) == "没有可导出的记录"
        assert jobs.state(job_id)["state"] == STATE_FAILED

    _run(main())


def test_unexpected_exception_gets_the_prefix_and_cleans_up(tmp_path):
    async def main():
        jobs = _jobs(tmp_path)
        workdir = None

        async def build(handle):
            nonlocal workdir
            workdir = handle.workdir
            (workdir / "partial.bin").write_bytes(b"half")
            raise RuntimeError("boom")

        job_id = jobs.start("archive", build, max_running=1)
        snapshot = await _wait(jobs, job_id)
        assert snapshot["state"] == STATE_FAILED
        assert snapshot["error"].startswith("导出失败：")
        assert "boom" in snapshot["error"]
        assert not workdir.exists()

    _run(main())


def test_retained_artifact_survives_expiry(tmp_path):
    async def main():
        clock = [2000.0]
        jobs = _jobs(tmp_path, clock=lambda: clock[0], ttl_seconds=30)
        artifact = tmp_path / "backup.luyunbak"

        async def build(handle):
            artifact.write_bytes(b"backup")
            return JobResult(artifact, "backup.luyunbak", retain=True)

        job_id = jobs.start("backup", build, max_running=1)
        snapshot = await _wait(jobs, job_id)
        assert snapshot["state"] == STATE_DONE
        clock[0] = 2031.0
        jobs.sweep()
        with pytest.raises(ExportJobNotFound):
            jobs.state(job_id)
        assert artifact.exists(), "备份点是 retain，过期只清记录，不删成品"

    _run(main())


def test_path_rejects_running_failed_and_gone_jobs(tmp_path):
    async def main():
        jobs = _jobs(tmp_path)
        release = asyncio.Event()

        async def running(handle):
            await release.wait()
            artifact = handle.workdir / "out.bin"
            artifact.write_bytes(b"x")
            return JobResult(artifact, "out.bin")

        job_id = jobs.start("standards", running, max_running=1)
        with pytest.raises(ExportJobNotReady):
            jobs.path(job_id)
        release.set()
        await _wait(jobs, job_id)

        artifact = jobs.path(job_id)
        artifact.unlink()
        with pytest.raises(ExportJobGone):
            jobs.path(job_id)

        async def failed(handle):
            raise ExportJobFailed("坏了")

        failed_id = jobs.start("archive", failed, max_running=1)
        await _wait(jobs, failed_id)
        with pytest.raises(ExportJobFailed):
            jobs.path(failed_id)

    _run(main())


def test_runtime_cap_cancels_a_hung_job_and_cleans_its_workdir(tmp_path):
    async def main():
        jobs = _jobs(tmp_path, max_runtime_seconds=0.05)
        started = asyncio.Event()
        workdir = None

        async def build(handle):
            nonlocal workdir
            workdir = handle.workdir
            started.set()
            await asyncio.Event().wait()

        job_id = jobs.start("archive", build, max_running=1)
        await asyncio.wait_for(started.wait(), timeout=1)
        snapshot = await _wait(jobs, job_id)
        assert snapshot["state"] == STATE_FAILED
        assert "超时" in snapshot["error"]
        assert not workdir.exists()

    _run(main())


def test_sweep_orphans_only_removes_directories_older_than_the_cutoff(tmp_path):
    root = tmp_path / "export-tmp"
    old = root / "old-job"
    fresh = root / "fresh-job"
    old.mkdir(parents=True)
    fresh.mkdir(parents=True)
    (old / "left.zip").write_bytes(b"x")
    (fresh / "left.zip").write_bytes(b"x")
    now = 10_000.0
    os.utime(old, (now - 120, now - 120))
    os.utime(fresh, (now - 5, now - 5))

    jobs = ExportJobs(root=root, clock=lambda: now, ttl_seconds=30)
    removed = jobs.sweep_orphans(older_than=60)
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()


def test_api_modules_no_longer_own_the_job_table():
    """守卫：三份拷贝删掉后，api/ 下不许再出现任务表或 prune 函数。"""
    forbidden = {
        "_EXPORT_JOBS",
        "_ARCHIVE_EXPORT_JOBS",
        "_prune_export_jobs",
        "_prune_archive_export_jobs",
        "_run_export_job",
        "_run_archive_export",
    }
    offenders = []
    for path in sorted((Path(__file__).resolve().parent.parent / "api").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
            elif isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            if name in forbidden:
                offenders.append(f"{path.name}:{node.lineno}:{name}")
    assert not offenders, "导出任务表只能留在 services/export_jobs.py：" + ", ".join(offenders)


def test_job_snapshot_is_a_copy_not_the_live_record(tmp_path):
    async def main():
        jobs = _jobs(tmp_path)
        release = asyncio.Event()

        async def build(handle):
            await release.wait()
            artifact = handle.workdir / "out.bin"
            artifact.write_bytes(b"x")
            return JobResult(artifact, "out.bin")

        job_id = jobs.start("standards", build, max_running=1)
        snapshot = jobs.state(job_id)
        snapshot["state"] = "tampered"
        assert jobs.state(job_id)["state"] == STATE_RUNNING
        release.set()
        await _wait(jobs, job_id)

    _run(main())