#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""就绪口径的健康检查与启动标识。

「更新成功」的第二段判定需要两个事实：数据层可用，以及当前进程确实晚于本次
重启请求启动。前者由就绪检查给出，后者由进程级启动标识（``startup_id`` /
``started_at``）给出。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Sequence

from database import CHINA_TZ
from services.backup_service import KEY_TABLES
from services.release_update import RuntimeReadiness

logger = logging.getLogger(__name__)


@dataclass
class RuntimeReadinessTracker:
    """进程级启动身份与迁移完成标记（单 worker 部署，进程内单例即可）。"""

    startup_id: Optional[str] = None
    started_at: Optional[str] = None
    migrations_complete: bool = False
    notes: List[str] = field(default_factory=list)

    def mark_started(self, *, migrations_complete: bool, note: Optional[str] = None) -> None:
        self.startup_id = uuid.uuid4().hex
        self.started_at = datetime.now(CHINA_TZ).isoformat()
        self.migrations_complete = bool(migrations_complete)
        if note:
            self.notes.append(note)

    def snapshot(self) -> dict:
        return {
            "startup_id": self.startup_id,
            "started_at": self.started_at,
            "migrations_complete": self.migrations_complete,
        }

    def reset(self) -> None:
        self.startup_id = None
        self.started_at = None
        self.migrations_complete = False
        self.notes = []


runtime_readiness = RuntimeReadinessTracker()


class AppReadinessAdapter:
    """Inspect the live app: DB connected, migrations done, key tables readable."""

    def __init__(
        self,
        db_getter: Callable[[], object],
        tracker: Optional[RuntimeReadinessTracker] = None,
        key_tables: Sequence[str] = KEY_TABLES,
    ) -> None:
        self._db_getter = db_getter
        self._tracker = tracker or runtime_readiness
        self._key_tables = tuple(key_tables)

    async def inspect_readiness(self) -> RuntimeReadiness:
        details: List[str] = []
        db = None
        try:
            db = self._db_getter()
        except Exception as exc:
            details.append(f"数据库句柄不可用：{exc}")

        db_connected = bool(db is not None and db.is_connected())
        if not db_connected:
            details.append("数据库未连接")

        key_tables_readable = False
        if db_connected:
            try:
                probe = await db.readable_tables(self._key_tables)
                if probe["missing"]:
                    details.append("关键表不存在：" + "、".join(probe["missing"]))
                if probe["errors"]:
                    details.append("关键表不可读：" + "；".join(probe["errors"]))
                key_tables_readable = bool(probe["readable"])
            except Exception as exc:
                details.append(f"关键表不可读：{exc}")

        # 数据层完成迁移（connect 成功）+ 本进程已完成启动，二者缺一不可
        migrations_complete = bool(
            db is not None and db.migrations_complete() and self._tracker.migrations_complete
        )
        if not migrations_complete:
            details.append("数据库迁移未完成")

        tracker_notes = list(self._tracker.notes)
        details.extend(tracker_notes)

        ready = bool(db_connected and migrations_complete and key_tables_readable)
        if ready:
            details.append("数据层可用")
        return RuntimeReadiness(
            ready=ready,
            startup_id=self._tracker.startup_id,
            started_at=self._tracker.started_at,
            db_connected=db_connected,
            migrations_complete=migrations_complete,
            key_tables_readable=key_tables_readable,
            details=details,
        )

    def readiness_payload(self, readiness: RuntimeReadiness) -> dict:
        """Shape the readiness probe for ``/api/system/health``."""
        return {
            "ready": readiness.ready,
            "startup_id": readiness.startup_id,
            "started_at": readiness.started_at,
            "db_connected": readiness.db_connected,
            "migrations_complete": readiness.migrations_complete,
            "key_tables_readable": readiness.key_tables_readable,
            "details": readiness.details,
        }
