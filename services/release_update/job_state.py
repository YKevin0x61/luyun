#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persist Update Job state under data/ (survives main-service restart)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, Optional

from config import settings
from services.release_update import STAGE_IDLE, UpdateJobState

JOB_STATE_FILENAME = "update_job.json"
JOB_LOG_FILENAME = "update_job.log"
CANCEL_FLAG_FILENAME = "update_job.cancel"
PID_FILENAME = "update_job.pid"

DEFAULT_LOG_TAIL_LINES = 60


def _data_root(data_dir: Optional[Path] = None) -> Path:
    return Path(data_dir) if data_dir is not None else Path(settings.DATABASE_DIR)


def job_state_path(data_dir: Optional[Path] = None) -> Path:
    return _data_root(data_dir) / JOB_STATE_FILENAME


def job_log_path(data_dir: Optional[Path] = None) -> Path:
    return _data_root(data_dir) / JOB_LOG_FILENAME


def cancel_flag_path(data_dir: Optional[Path] = None) -> Path:
    return _data_root(data_dir) / CANCEL_FLAG_FILENAME


def job_pid_path(data_dir: Optional[Path] = None) -> Path:
    return _data_root(data_dir) / PID_FILENAME


def request_cancel(data_dir: Optional[Path] = None) -> None:
    """Ask the running Update Job to abort (cooperative + force-stop signal)."""
    path = cancel_flag_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("1\n", encoding="utf-8")


def clear_cancel(data_dir: Optional[Path] = None) -> None:
    cancel_flag_path(data_dir).unlink(missing_ok=True)


def is_cancel_requested(data_dir: Optional[Path] = None) -> bool:
    return cancel_flag_path(data_dir).is_file()


def write_job_pid(pid: int, data_dir: Optional[Path] = None) -> None:
    path = job_pid_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{int(pid)}\n", encoding="utf-8")


def clear_job_pid(data_dir: Optional[Path] = None) -> None:
    job_pid_path(data_dir).unlink(missing_ok=True)


def read_job_pid(data_dir: Optional[Path] = None) -> Optional[int]:
    path = job_pid_path(data_dir)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def read_log_tail(
    log_path: Optional[str] = None,
    *,
    max_lines: int = DEFAULT_LOG_TAIL_LINES,
    data_dir: Optional[Path] = None,
) -> str:
    """Return the last ``max_lines`` of the Update Job log (empty if missing)."""
    path = Path(log_path) if log_path else job_log_path(data_dir)
    if not path.is_file() or max_lines <= 0:
        return ""
    try:
        # Bound read for large logs: last ~256 KiB is enough for a UI tail.
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > 262_144:
                fh.seek(-262_144, os.SEEK_END)
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)


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
