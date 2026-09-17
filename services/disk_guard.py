#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""进程内磁盘守护。

现场三连故障的触发点是 `/dev/vda2` 100% 满而没有任何人知道：SQLite 写不进去、
apt/pip 装不上、Playwright 浏览器补装失败，最后以「日志库损坏 + 爬虫报浏览器
缺失」的形式暴露出来。这里补上缺失的那一层：

- :func:`read_usage` / :func:`classify`：纯函数，便于测试与复用；
- :class:`DiskGuard`：定时检查并在**跨过阈值时**告警（日志 + realtime nudge），
  同水位不重复刷屏；
- :func:`min_free_mb`：给「应用更新前置自检」判断是否还有空间写 .venv。

刻意不做的事：在容器里调 docker prune。那需要 docker.sock 与宿主特权，误删
在用镜像的代价远高于省下的几 GB；宿主侧的清理与告警交给
``deploy/disk-guard.sh`` + systemd timer。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)

LEVEL_OK = "ok"
LEVEL_WARNING = "warning"
LEVEL_CRITICAL = "critical"

_LEVEL_ORDER = {LEVEL_OK: 0, LEVEL_WARNING: 1, LEVEL_CRITICAL: 2}


@dataclass(frozen=True)
class DiskUsage:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int

    @property
    def used_pct(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return self.used_bytes * 100.0 / self.total_bytes

    @property
    def free_mb(self) -> float:
        return self.free_bytes / (1024 * 1024)


def read_usage(path: str) -> Optional[DiskUsage]:
    """读取某个路径所在分区的占用；路径不存在或无权限时返回 None。"""
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        logger.debug("读取磁盘占用失败 %s: %s", path, exc)
        return None
    return DiskUsage(
        path=path,
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
    )


def classify(
    used_pct: float,
    *,
    warn_pct: Optional[int] = None,
    critical_pct: Optional[int] = None,
) -> str:
    """把占用百分比分级为 ok / warning / critical。"""
    warn = settings.DISK_WARN_PCT if warn_pct is None else warn_pct
    critical = (
        settings.DISK_CRITICAL_PCT if critical_pct is None else critical_pct
    )
    if used_pct >= critical:
        return LEVEL_CRITICAL
    if used_pct >= warn:
        return LEVEL_WARNING
    return LEVEL_OK


def monitored_paths() -> List[str]:
    """默认监控数据目录所在分区与根分区（很多部署里二者是同一个）。"""
    paths: List[str] = []
    for candidate in (settings.DATABASE_DIR, os.path.abspath(os.sep)):
        if candidate and candidate not in paths:
            paths.append(candidate)
    return paths


def min_free_mb(paths: Optional[List[str]] = None) -> Optional[float]:
    """监控路径中最小的可用空间（MB）；全部读不到时返回 None。"""
    free_values = [
        usage.free_mb
        for usage in (read_usage(p) for p in (paths or monitored_paths()))
        if usage is not None
    ]
    return min(free_values) if free_values else None


class DiskGuard:
    """周期性检查磁盘水位并在跨阈值时告警。"""

    def __init__(
        self,
        paths: Optional[List[str]] = None,
        *,
        interval_seconds: Optional[int] = None,
    ) -> None:
        self._paths = list(paths or monitored_paths())
        self._interval = max(
            30, int(interval_seconds or settings.DISK_GUARD_INTERVAL_SECONDS)
        )
        self._task: Optional[asyncio.Task] = None
        self._last_levels: Dict[str, str] = {}

    # ── 快照 ─────────────────────────────────

    def snapshot(self) -> List[Dict[str, Any]]:
        """各监控路径的当前水位（读 statvfs，开销可忽略）。"""
        items: List[Dict[str, Any]] = []
        for path in self._paths:
            usage = read_usage(path)
            if usage is None:
                continue
            level = classify(usage.used_pct)
            items.append(
                {
                    "path": path,
                    "total_mb": round(usage.total_bytes / (1024 * 1024), 1),
                    "used_mb": round(usage.used_bytes / (1024 * 1024), 1),
                    "free_mb": round(usage.free_mb, 1),
                    "used_pct": round(usage.used_pct, 1),
                    "level": level,
                }
            )
        return items

    def worst_level(self) -> str:
        worst = LEVEL_OK
        for item in self.snapshot():
            if _LEVEL_ORDER[item["level"]] > _LEVEL_ORDER[worst]:
                worst = item["level"]
        return worst

    # ── 生命周期 ─────────────────────────────

    def start(self) -> None:
        if not settings.DISK_GUARD_ENABLED or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("🧭 磁盘守护已启动（间隔 %ss）", self._interval)

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("🧭 磁盘守护已停止")

    async def _loop(self) -> None:
        while True:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("磁盘水位检查失败")
            await asyncio.sleep(self._interval)

    async def check_once(self) -> List[Dict[str, Any]]:
        """检查一轮；返回本轮快照（也便于测试直接驱动）。"""
        items = self.snapshot()
        for item in items:
            path = item["path"]
            level = item["level"]
            previous = self._last_levels.get(path, LEVEL_OK)
            self._last_levels[path] = level
            if level == previous:
                continue
            if level == LEVEL_OK:
                logger.info("✅ 磁盘水位恢复正常: %s (%.1f%%)", path, item["used_pct"])
                continue
            emit = logger.critical if level == LEVEL_CRITICAL else logger.warning
            emit(
                "🚨 磁盘水位 %s: %s 已用 %.1f%%，剩余 %.0fMB",
                level,
                path,
                item["used_pct"],
                item["free_mb"],
            )
            await self._notify(item)
        return items

    async def _notify(self, item: Dict[str, Any]) -> None:
        try:
            from services.realtime.hub import realtime_hub

            await realtime_hub.broadcast_nudge(
                "admin",
                {"reason": "disk", "level": item["level"], "used_pct": item["used_pct"]},
            )
        except Exception:
            # 告警通道本身不能影响主流程。
            logger.debug("磁盘告警 nudge 发送失败", exc_info=True)


# 全局单例（lifespan 里 start/stop）
disk_guard = DiskGuard()
