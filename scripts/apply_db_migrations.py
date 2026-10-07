#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用待执行迁移的执行体（更新作业 `applying_migrations` 阶段 / 手工排障）。

为什么单独一个入口、由作业用**发行包自己那棵树**的解释器执行：更新作业进程在原子
切换之前就启动了，跑的是旧树的代码；而 `migrations/pg/` 由 `services/db_migrations.py`
按自己的模块文件定位，所以只有新树里的这份代码才读得到随发行包刚下来的迁移脚本
（ADR 0096：「先迁移再更新」在物理上不成立）。

与应用侧的 Admin 手工入口（`/api/db-migrations`）走的是**同一套** `services.db_migrations`
逻辑与同一张 `schema_migrations` 记录表，不另立一套判据。

输出约定（更新作业的适配器按它判成败）：
* stdout 只给一行结果标记：``LUYUN_DB_MIGRATION_RESULT {json}``
* 人看的进度与原因走 stderr（日志）
* 退出码：0 = 没有待应用项或全部应用成功；1 = 有失败（含连不上库）

手工排障：

    .venv/bin/python scripts/apply_db_migrations.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402
from database import DatabaseManager  # noqa: E402
from services import db_migrations  # noqa: E402

RESULT_PREFIX = "LUYUN_DB_MIGRATION_RESULT "

logger = logging.getLogger("apply_db_migrations")


async def _apply() -> dict:
    """连库 → 读待应用 → 按序应用；返回结果标记的字段。"""
    db = DatabaseManager()
    try:
        connected = await db.connect()
    except Exception as exc:
        # DATABASE_BACKEND 不是 postgres 时 connect() 直接抛（ADR 0089）。
        logger.error("数据库连接失败: %s", exc)
        return {"ok": False, "error": f"数据库连接失败：{exc}"}
    if not connected:
        return {
            "ok": False,
            "error": "数据库连接失败（检查 POSTGRES_DSN 与 PostgreSQL 服务）",
        }
    try:
        status = await db_migrations.migration_status(db)
        logger.info(
            "待应用迁移 %d 条：%s",
            len(status.pending),
            [item.filename for item in status.pending],
        )
        result = await db_migrations.apply_pending_migrations(db)
    finally:
        await db.close()
    for item in result.failed:
        logger.error("应用迁移失败 %s: %s", item.get("filename"), item.get("error"))
    return {
        "ok": result.ok,
        "pending_before": len(status.pending),
        "applied": [item.version for item in result.applied],
        "failed": result.failed,
    }


def main() -> int:
    # 进度与原因走 stderr：stdout 留给那一行机器可读的结果标记。
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stderr,
        force=True,
    )
    payload = {
        "ok": False,
        "pending_before": None,
        "applied": [],
        "failed": [],
        # 实际生效的迁移目录（按本树 services/db_migrations.py 的模块位置定位）。
        "migrations_dir": str(db_migrations.MIGRATIONS_DIR),
        "backend": getattr(settings, "DATABASE_BACKEND", ""),
    }
    try:
        payload.update(asyncio.run(_apply()))
    except Exception as exc:  # 读迁移状态/驱动异常：也要给结果标记，别只留 traceback
        logger.exception("应用待执行迁移失败")
        payload["error"] = str(exc)
    print(RESULT_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
