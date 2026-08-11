#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persist Update Job state under data/ (survives main-service restart)."""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, Optional

from config import settings
from services.release_update import STAGE_IDLE, UpdateJobState

JOB_STATE_FILENAME = "update_job.json"
JOB_LOG_FILENAME = "update_job.log"


def job_state_path(data_dir: Optional[Path] = None) -> Path:
    root = Path(data_dir) if data_dir is not None else Path(settings.DATABASE_DIR)
    return root / JOB_STATE_FILENAME


def job_log_path(data_dir: Optional[Path] = None) -> Path:
    root = Path(data_dir) if data_dir is not None else Path(settings.DATABASE_DIR)
    return root / JOB_LOG_FILENAME


def _from_dict(payload: dict[str, Any]) -> UpdateJobState:
    allowed = {f.name for f in fields(UpdateJobState)}
    kwargs = {k: v for k, v in payload.items() if k in allowed}
    if "stage" not in kwargs:
        kwargs["stage"] = STAGE_IDLE
    return UpdateJobState(**kwargs)


class FileJobStateStore:
    """JSON file store mirroring scraper_health operational pattern."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else job_state_path()

    def read(self) -> UpdateJobState:
        if not self._path.exists():
            return UpdateJobState(stage=STAGE_IDLE, log_path=str(job_log_path()))
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return UpdateJobState(stage=STAGE_IDLE, log_path=str(job_log_path()))
        if not isinstance(payload, dict):
            return UpdateJobState(stage=STAGE_IDLE, log_path=str(job_log_path()))
        state = _from_dict(payload)
        if not state.log_path:
            state = UpdateJobState(**{**asdict(state), "log_path": str(job_log_path())})
        return state

    def write(self, state: UpdateJobState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(state)
        if not payload.get("log_path"):
            payload["log_path"] = str(job_log_path(self._path.parent))
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)
