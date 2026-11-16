#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""维护态：把「正在更新」与「正在整库恢复」汇成一条前端能轮询的结论。

为什么需要它：升级切包与整库恢复期间，后端会短暂 5xx / 连不上，而员工端和店长
手里正开着页面。以前只有管理端「系统更新」页知道进度，其他角色只会看到提交失败，
既不知道是维护、也不知道该等还是重试。

两个来源都不新造状态文件：
- 更新作业本来就把阶段写在 data/update_job.json（重启后仍在），这里只读；
- 恢复链路（快照回滚 / 备份导入）在进入互斥时写一个短命标记
  data/maintenance.json，结束后删除。恢复过程会替换整库，把状态存进库本身没有意义。

标记带 started_at 与 30 分钟上限：恢复进程若被强杀，标记不会永远把全站挂成维护态。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from config import settings
from services.release_update import (
    STAGE_APPLYING_MIGRATIONS,
    STAGE_INSTALLING,
    STAGE_RESTARTING,
    STAGE_SYNCING_DEPS,
)
from services.release_update.job_state import FileJobStateStore, job_state_path

MARKER_FILENAME = "maintenance.json"
STALE_AFTER_SECONDS = 30 * 60

# 从「开始往磁盘写新树」起服务就可能不稳定；下载 / 备份阶段用户照常使用，不提示。
_ACTIVE_UPDATE_STAGES = {
    STAGE_INSTALLING,
    STAGE_SYNCING_DEPS,
    STAGE_APPLYING_MIGRATIONS,
    STAGE_RESTARTING,
}

_STAGE_MESSAGES = {
    STAGE_INSTALLING: "系统正在安装新版本，部分操作可能暂时失败，请稍后再试。",
    STAGE_SYNCING_DEPS: "系统正在安装新版本的依赖，部分操作可能暂时失败，请稍后再试。",
    STAGE_APPLYING_MIGRATIONS: "系统正在执行数据库迁移，部分操作可能暂时失败，请稍后再试。",
    STAGE_RESTARTING: "系统正在重启并确认新版本健康，稍等片刻后刷新即可。",
}

_INACTIVE = {"active": False, "kind": None, "message": "", "started_at": None}


def _data_root(data_dir: Optional[Path] = None) -> Path:
    return Path(data_dir) if data_dir is not None else Path(settings.DATABASE_DIR)


def marker_path(data_dir: Optional[Path] = None) -> Path:
    return _data_root(data_dir) / MARKER_FILENAME


def begin_restore(entry: str, data_dir: Optional[Path] = None) -> bool:
    """写恢复标记。写不进去只记日志、不阻断恢复（恢复本身比提示重要）。"""
    path = marker_path(data_dir)
    payload = {
        "kind": "restore",
        "message": f"系统正在恢复数据（{entry}），恢复完成前请不要提交新的修改。",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False


def end_restore(data_dir: Optional[Path] = None) -> None:
    try:
        marker_path(data_dir).unlink(missing_ok=True)
    except OSError:
        pass


def _read_restore_marker(data_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    path = marker_path(data_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    started_at = payload.get("started_at")
    try:
        started = datetime.fromisoformat(str(started_at))
    except (TypeError, ValueError):
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - started > timedelta(seconds=STALE_AFTER_SECONDS):
        # 恢复进程被强杀留下的孤儿标记：不再据此把全站挂成维护态。
        return None
    return {
        "active": True,
        "kind": str(payload.get("kind") or "restore"),
        "message": str(payload.get("message") or "系统正在维护，请稍后再试。"),
        "started_at": started_at,
    }


def read_maintenance(
    data_dir: Optional[Path] = None,
    *,
    job_store: Optional[FileJobStateStore] = None,
) -> dict[str, Any]:
    """返回 active / kind / message / started_at；任何读取失败都按「不在维护」处理。"""
    try:
        store = job_store or FileJobStateStore(job_state_path(data_dir))
        state = store.read()
    except Exception:  # noqa: BLE001 - 维护提示不应该把探针打成 500
        state = None
    if state is not None and state.stage in _ACTIVE_UPDATE_STAGES:
        return {
            "active": True,
            "kind": "update",
            "message": _STAGE_MESSAGES.get(state.stage, "系统正在更新，请稍后再试。"),
            "started_at": state.started_at or state.restart_requested_at,
        }
    return _read_restore_marker(data_dir) or dict(_INACTIVE)
