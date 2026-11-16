"""维护态探针：更新阶段与恢复标记合成一条结论（P0-4）。

员工端与店长在升级切包 / 整库恢复时只会看到提交失败；这条探针让页面能明确说
「在维护，等一等」，而不是让用户反复重试。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from services import system_maintenance
from services.release_update import (
    STAGE_APPLYING_MIGRATIONS,
    STAGE_IDLE,
    STAGE_INSTALLING,
    UpdateJobState,
)
from services.release_update.job_state import FileJobStateStore


def _store(tmp_path: Path) -> FileJobStateStore:
    return FileJobStateStore(tmp_path / "update_job.json")


def test_update_stage_reports_maintenance(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(UpdateJobState(stage=STAGE_APPLYING_MIGRATIONS, started_at="2026-11-16T10:00:00+00:00"))

    result = system_maintenance.read_maintenance(tmp_path, job_store=store)

    assert result["active"] is True
    assert result["kind"] == "update"
    assert "数据库迁移" in result["message"]
    assert result["started_at"] == "2026-11-16T10:00:00+00:00"


def test_idle_update_job_is_not_maintenance(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(UpdateJobState(stage=STAGE_IDLE))

    result = system_maintenance.read_maintenance(tmp_path, job_store=store)

    assert result == {"active": False, "kind": None, "message": "", "started_at": None}


def test_restore_marker_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(UpdateJobState(stage=STAGE_INSTALLING))

    assert system_maintenance.begin_restore("快照回滚", tmp_path) is True
    result = system_maintenance.read_maintenance(tmp_path, job_store=store)
    # 更新阶段优先于恢复标记：两个都活跃时提示更新即可。
    assert result["kind"] == "update"

    store.write(UpdateJobState(stage=STAGE_IDLE))
    restore = system_maintenance.read_maintenance(tmp_path, job_store=store)
    assert restore["active"] is True
    assert restore["kind"] == "restore"
    assert "快照回滚" in restore["message"]

    system_maintenance.end_restore(tmp_path)
    assert system_maintenance.read_maintenance(tmp_path, job_store=store)["active"] is False


def test_stale_restore_marker_is_ignored(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(UpdateJobState(stage=STAGE_IDLE))
    marker = system_maintenance.marker_path(tmp_path)
    marker.write_text(
        json.dumps(
            {
                "kind": "restore",
                "message": "旧标记",
                "started_at": "2020-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    result = system_maintenance.read_maintenance(tmp_path, job_store=store)

    assert result["active"] is False


def test_corrupt_restore_marker_is_ignored(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(UpdateJobState(stage=STAGE_IDLE))
    system_maintenance.marker_path(tmp_path).write_text("{not json", encoding="utf-8")

    assert system_maintenance.read_maintenance(tmp_path, job_store=store)["active"] is False


def test_maintenance_endpoint_is_public_and_reports_update(monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from config import settings
    from main import app

    monkeypatch.setattr(settings, "DATABASE_DIR", str(tmp_path))
    store = FileJobStateStore(tmp_path / "update_job.json")
    store.write(UpdateJobState(stage=STAGE_APPLYING_MIGRATIONS))

    with TestClient(app) as client:
        response = client.get("/api/system/maintenance")

    assert response.status_code == 200
    assert response.json()["active"] is True
    assert response.json()["kind"] == "update"
