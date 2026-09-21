#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PostgreSQL 增量迁移的应用入口（Admin 手动触发）。

为什么需要它：SQLite 部署的 schema 变更由应用启动时自愈（``apply_hygiene_schema`` /
``migrate_hygiene_columns`` 等），而 PG 按设计**不在启动期改结构**
（见 ``db_core/connection.py::_connect_postgres`` 的说明：启动期改结构会让「schema
是谁改的」不可追溯）。于是既有库要靠人应用 ``migrations/pg/000N_*.sql``——升级后
没人记得敲 psql，就是运行时报错或者悄悄退化（少一条索引不会报错，只是一直慢）。

这里把「有哪些待应用、点一下应用」做成 Admin 能力，同时保留可追溯性：
应用记录写进 ``schema_migrations`` 表，界面能看出当前到了哪一版。

刻意不做的事：
* **不碰 0001**。它是 bootstrap（``DROP TABLE`` + ``CREATE TABLE``），会清空库，
  只用于初次建立；文件头带 ``luyun:bootstrap-only`` 标记，永久排除在待应用之外。
* **不自动重跑已应用的迁移**。已应用的文件如果内容变了（checksum 不一致）只报告，
  由人判断——自动重放历史迁移是另一种事故。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import settings
from db_core.utils import CHINA_TZ

logger = logging.getLogger(__name__)

# 迁移文件所在目录（仓库根下）。Docker 部署里它随 Release Bundle 一起挂进容器。
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations" / "pg"
# 文件头出现这个标记的迁移永远不会被自动应用（0001 是 DROP + CREATE）。
BOOTSTRAP_MARKER = "luyun:bootstrap-only"
_FILENAME_RE = re.compile(r"^(\d{3,})_[A-Za-z0-9_\-]+\.sql$")

SCHEMA_MIGRATIONS_TABLE = "schema_migrations"
_CREATE_TRACKING_TABLE = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} (
    version TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class MigrationFile:
    version: str
    filename: str
    path: Path
    checksum: str = ""
    bootstrap_only: bool = False

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "filename": self.filename,
            "checksum": self.checksum,
            "bootstrap_only": self.bootstrap_only,
        }


@dataclass
class MigrationStatus:
    backend: str
    supported: bool
    note: str = ""
    applied: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    bootstrap_only: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    # 本发行包里扫描到的增量脚本条数（不含 bootstrap）。为 0 时要能和「真的没有待
    # 应用」区分开：发行包按 ``git archive HEAD`` 打包，迁移脚本没提交就不在包里，
    # 那时面板若不说明，就会把「什么都没检查到」说成「已是最新」。
    incremental_total: int = 0

    def as_dict(self) -> dict:
        return {
            "backend": self.backend,
            "supported": self.supported,
            "note": self.note,
            "applied": [item.as_dict() for item in self.applied],
            "pending": [item.as_dict() for item in self.pending],
            "bootstrap_only": [item.as_dict() for item in self.bootstrap_only],
            "changed": [item.as_dict() for item in self.changed],
            "incremental_total": self.incremental_total,
        }


def list_migration_files(directory: Optional[Path] = None) -> list:
    """按版本号升序列出迁移文件；bootstrap 的单独标出来。"""
    root = Path(directory) if directory is not None else MIGRATIONS_DIR
    if not root.is_dir():
        return []
    files = []
    for path in sorted(root.glob("*.sql")):
        matched = _FILENAME_RE.match(path.name)
        if not matched:
            logger.warning("迁移文件名不符合 NNN_name.sql，已跳过：%s", path.name)
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("读取迁移文件失败 %s: %s", path.name, exc)
            continue
        files.append(
            MigrationFile(
                version=matched.group(1),
                filename=path.name,
                path=path,
                checksum=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                bootstrap_only=BOOTSTRAP_MARKER in raw,
            )
        )
    files.sort(key=lambda item: item.version)
    return files


async def _read_applied(conn) -> dict:
    """已应用的迁移：{version: {filename, checksum, applied_at}}；表不存在就是空。"""
    try:
        cur = await conn.execute(
            f"SELECT version, filename, checksum, applied_at FROM {SCHEMA_MIGRATIONS_TABLE}"
        )
        rows = await cur.fetchall()
    except Exception as exc:
        if not _is_missing_table_error(exc):
            # 读不到 ≠ 没应用过。把真实故障抛出去，界面显示错误——总好过把已应用的
            # 脚本当成待应用重跑一遍（对任何非幂等迁移都是灾难）。
            logger.error("读取 %s 失败：%s", SCHEMA_MIGRATIONS_TABLE, exc)
            raise
        logger.info("%s 还不存在，按「尚未应用任何迁移」处理", SCHEMA_MIGRATIONS_TABLE)
        return {}
    applied = {}
    for row in rows:
        mapping = dict(row)
        applied[str(mapping["version"])] = {
            "filename": mapping.get("filename"),
            "checksum": mapping.get("checksum"),
            "applied_at": mapping.get("applied_at"),
        }
    return applied


def _backend_supported() -> bool:
    return getattr(settings, "DATABASE_BACKEND", "sqlite") == "postgres"


# SQLite 与 PG（asyncpg 包装后）对"表不存在"的措辞。
_MISSING_TABLE_MARKERS = ("no such table", "undefinedtable", "does not exist")


def _is_missing_table_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _MISSING_TABLE_MARKERS)


async def migration_status(db) -> MigrationStatus:
    """当前后端的迁移状态：待应用、已应用、只能手工 bootstrap 的。"""
    backend = getattr(settings, "DATABASE_BACKEND", "sqlite")
    files = list_migration_files()
    incremental_total = len([item for item in files if not item.bootstrap_only])
    if not _backend_supported():
        return MigrationStatus(
            backend=backend,
            supported=False,
            note=(
                "当前是 SQLite 后端：schema 变更由应用启动时自动补齐"
                "（apply_hygiene_schema / migrate_hygiene_columns），无需手工应用。"
            ),
            bootstrap_only=[item for item in files if item.bootstrap_only],
            incremental_total=incremental_total,
        )

    applied = await _read_applied(db._conn)
    pending = [
        item
        for item in files
        if not item.bootstrap_only and item.version not in applied
    ]
    changed = [
        item
        for item in files
        if not item.bootstrap_only
        and item.version in applied
        and applied[item.version].get("checksum") != item.checksum
    ]
    return MigrationStatus(
        backend=backend,
        supported=True,
        note="",
        applied=[
            MigrationFile(
                version=version,
                filename=str(applied[version].get("filename") or ""),
                path=Path(),
                checksum=str(applied[version].get("checksum") or ""),
            )
            for version in sorted(applied)
        ],
        pending=pending,
        bootstrap_only=[item for item in files if item.bootstrap_only],
        changed=changed,
        incremental_total=incremental_total,
    )


@dataclass
class MigrationApplyResult:
    applied: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "applied": [item.as_dict() for item in self.applied],
            "failed": self.failed,
            "skipped": [item.as_dict() for item in self.skipped],
        }


async def apply_pending_migrations(db, *, only: Optional[list] = None) -> MigrationApplyResult:
    """串行化入口。

    两个管理员同时点「应用」时，PG 的 ``CREATE TABLE IF NOT EXISTS`` 并不是原子的
    （并发首次建表会撞 pg_type 的唯一约束）。应用迁移是低频动作，进程内加一把锁
    的代价可以忽略。

    锁挂在 db 对象上、在事件循环内创建：模块级 ``asyncio.Lock()`` 会绑定首次使用的
    loop，跨 loop（测试、多次启动）复用会报 "bound to a different event loop"。
    """
    lock = getattr(db, "_migration_apply_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        db._migration_apply_lock = lock
    async with lock:
        return await _apply_pending_migrations(db, only=only)


async def _apply_pending_migrations(db, *, only: Optional[list] = None) -> MigrationApplyResult:
    """按序应用待执行的迁移，逐个记录。

    每个文件整体交给驱动执行：PG 的 DDL 在隐式事务里，一个文件要么全成要么全败，
    失败的文件不会写进记录表（幂等 DDL 也保证重试安全）。
    """
    result = MigrationApplyResult()
    if not _backend_supported():
        result.skipped.append(
            MigrationFile("", "SQLite 后端由启动时自动迁移", Path())
        )
        return result

    status = await migration_status(db)
    targets = status.pending
    if only:
        wanted = {str(item) for item in only}
        targets = [item for item in targets if item.version in wanted]
    if not targets:
        return result

    await db._conn.execute(_CREATE_TRACKING_TABLE)
    await db._conn.commit()

    for item in targets:
        sql = item.path.read_text(encoding="utf-8")
        try:
            await db._conn.execute(sql)
            await db._conn.execute(
                f"""INSERT INTO {SCHEMA_MIGRATIONS_TABLE}
                    (version, filename, checksum, applied_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (version) DO UPDATE SET
                        filename = excluded.filename,
                        checksum = excluded.checksum,
                        applied_at = excluded.applied_at""",
                (item.version, item.filename, item.checksum, _now_iso()),
            )
            await db._conn.commit()
        except Exception as exc:
            try:
                await db._conn.rollback()
            except Exception:  # pragma: no cover - 回滚失败时以原始错误为准
                pass
            logger.error("应用迁移失败 %s: %s", item.filename, exc)
            result.failed.append({"version": item.version, "filename": item.filename, "error": str(exc)})
            break
        logger.info("已应用数据库迁移 %s", item.filename)
        result.applied.append(item)
    return result


def _now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat()
