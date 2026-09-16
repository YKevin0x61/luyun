#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新历史：业务库之外的旁路 JSON（默认保留 30 条）。

当前作业状态文件只表达「这一份作业」，历史是只读记录，两者互不覆盖。一次更新在
历史里记录目标版本、更新前版本、结果、是否回滚、耗时与日志位置。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from database import CHINA_TZ
from services.release_update import (
    STAGE_FAILED,
    STAGE_SUCCEEDED,
    STAGE_SUCCEEDED_BUT_UNHEALTHY,
    UpdateJobState,
)

logger = logging.getLogger(__name__)

HISTORY_FILENAME = "update_history.json"
DEFAULT_HISTORY_LIMIT = 30

RESULT_SUCCEEDED = "succeeded"
RESULT_FAILED = "failed"
RESULT_CANCELLED = "cancelled"
RESULT_UNHEALTHY = "succeeded_but_unhealthy"

RESULT_LABELS = {
    RESULT_SUCCEEDED: "成功",
    RESULT_FAILED: "失败",
    RESULT_CANCELLED: "已取消",
    RESULT_UNHEALTHY: "已切换但未健康",
}


def _data_root(data_dir: Optional[Path] = None) -> Path:
    from services.release_update.job_state import _data_root as job_data_root

    return job_data_root(data_dir)


def history_path(data_dir: Optional[Path] = None) -> Path:
    return _data_root(data_dir) / HISTORY_FILENAME


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed


def result_for_state(state: UpdateJobState) -> Optional[str]:
    """把作业终态映射为历史里的结果类型（进行中的作业没有历史结果）。"""
    if state.stage == STAGE_SUCCEEDED:
        return RESULT_SUCCEEDED
    if state.stage == STAGE_SUCCEEDED_BUT_UNHEALTHY:
        return RESULT_UNHEALTHY
    if state.stage == STAGE_FAILED:
        cancelled = bool(state.cancel_requested) or "cancel" in (state.error or "").lower()
        return RESULT_CANCELLED if cancelled else RESULT_FAILED
    return None


def entry_from_state(state: UpdateJobState) -> Optional[dict]:
    """把一次作业终态转换为一条历史记录；非终态返回 ``None``。"""
    result = result_for_state(state)
    if result is None:
        return None

    duration = None
    started = _parse_iso(state.started_at)
    finished = _parse_iso(state.finished_at)
    if started and finished:
        duration = max(0.0, (finished - started).total_seconds())

    return {
        "target_tag": state.target_tag,
        "previous_tag": state.previous_ref,
        "result": result,
        "result_label": RESULT_LABELS.get(result, result),
        "rolled_back": bool(state.rollback_attempted),
        "rollback_ok": state.rollback_ok,
        "started_at": state.started_at,
        "finished_at": state.finished_at,
        "duration_seconds": duration,
        "log_path": state.log_path,
        "snapshot_ts": state.snapshot_ts,
        "error": state.error,
    }


def _dedup_key(entry: dict) -> tuple:
    return (
        entry.get("result"),
        entry.get("target_tag"),
        entry.get("finished_at"),
    )


def read_history(
    *,
    limit: Optional[int] = None,
    data_dir: Optional[Path] = None,
) -> List[dict]:
    """读取历史（最新在前）。文件缺失或损坏时返回空列表。"""
    path = history_path(data_dir)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("更新历史文件无法解析，按空历史处理: %s", path)
        return []
    entries = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return []
    if limit is not None:
        return entries[:limit]
    return entries


def append_entry(
    entry: dict,
    *,
    limit: int = DEFAULT_HISTORY_LIMIT,
    data_dir: Optional[Path] = None,
) -> List[dict]:
    """追加一条历史（最新在前），按上限淘汰最旧；重复记录不会写入两次。"""
    entries = read_history(data_dir=data_dir)
    key = _dedup_key(entry)
    if any(_dedup_key(existing) == key for existing in entries):
        return entries
    entries.insert(0, entry)
    del entries[max(0, limit):]

    path = history_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"entries": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
    return entries


def record_state(
    state: UpdateJobState,
    *,
    limit: int = DEFAULT_HISTORY_LIMIT,
    data_dir: Optional[Path] = None,
) -> Optional[dict]:
    """把作业终态写入历史（非终态或重复记录不写入）。"""
    entry = entry_from_state(state)
    if entry is None:
        return None
    append_entry(entry, limit=limit, data_dir=data_dir)
    return entry


def latest_failure(
    *,
    data_dir: Optional[Path] = None,
) -> Optional[dict]:
    """最近一次非成功记录（失败 / 取消 / 健康确认失败），供预检提示。"""
    for entry in read_history(data_dir=data_dir):
        if entry.get("result") != RESULT_SUCCEEDED:
            return entry
    return None


def latest_success(
    *,
    data_dir: Optional[Path] = None,
) -> Optional[dict]:
    for entry in read_history(data_dir=data_dir):
        if entry.get("result") == RESULT_SUCCEEDED:
            return entry
    return None


class FileUpdateHistory:
    """基于旁路 JSON 的更新历史端口实现。"""

    def __init__(self, data_dir: Optional[Path] = None, limit: int = DEFAULT_HISTORY_LIMIT):
        self._data_dir = data_dir
        self._limit = limit

    def read(self) -> List[dict]:
        return read_history(data_dir=self._data_dir)

    def append(self, entry: dict) -> None:
        append_entry(entry, limit=self._limit, data_dir=self._data_dir)

    def latest_failure(self) -> Optional[dict]:
        return latest_failure(data_dir=self._data_dir)

    def record_state(self, state: UpdateJobState) -> None:
        record_state(state, limit=self._limit, data_dir=self._data_dir)
