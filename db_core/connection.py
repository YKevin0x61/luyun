#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的连接/生命周期职责：
建立 PostgreSQL 连接，按表缓存 TableView，关闭连接。

SQLite 已在 ADR 0089 退场：这里不再有建表自愈、`PRAGMA` 体检、统计信息维护与
导出 .db（「导出 DB」现在直接给整库 pg_dump，见 :mod:`services.backup_service`）。
"""

import logging
import asyncio
from typing import Any, Dict, Optional

from config import settings

from db_core.schema import ALL_TABLES, HYGIENE_TABLES, RECIPE_TABLES
from db_core.table_db import TableView
from db_core.utils import ensure_beijing_datetime, row_to_dict

logger = logging.getLogger(__name__)


class _ConnectionMixin:
    """单库连接建立与关闭。"""

    def __init__(self):
        self.paths: Dict[str, str] = settings.DATABASE_PATHS
        # Internal cache for TableView instances (shared connection).
        self._table_views: Dict[str, TableView] = {}
        # 实际类型是 db_core.backend.pg.PgConnection；这里按鸭子类型标注，
        # 免得为一条注解把驱动导入到连接生命周期模块里。
        self._main_conn: Optional[Any] = None
        # 全局写锁，由 connect() 真正建出来（asyncio.Lock 必须在事件循环里创建）。
        # HygieneWork / EmployeeAccounts 通过 owner 共享这一把；这里要是 None，它们
        # 就各自退回一把局部锁，两个 service 的隐式事务会互相穿插、互相 rollback。
        self._write_lock: Optional[asyncio.Lock] = None
        # Set once connect() has finished schema creation + migrations.
        self._migrations_complete = False

        self.stats = {
            'queries_executed': 0,
            'slow_queries': 0,
            'connection_count': 0
        }

    async def connect(self) -> bool:
        """建立业务库连接（PostgreSQL，唯一后端）。

        SQLite 后端已在 ADR 0089 退场：``DATABASE_BACKEND`` 不是 ``postgres`` 时
        这里直接失败，不做静默回落——老部署需要先迁移再升级（见
        `deploy/enable_postgres.sh` 与 `docs/adr/0089-retire-sqlite-postgres-only.md`）。
        """
        # 全局写锁在这里建：asyncio.Lock 需要运行中的事件循环，而且必须早于任何
        # service 取用（service 的 _write_lock property 见到它就共享，见 #2.2）。
        self._write_lock = asyncio.Lock()
        backend = (getattr(settings, "DATABASE_BACKEND", "") or "").strip().lower()
        if backend != "postgres":
            raise RuntimeError(
                "SQLite 后端已移除（ADR 0089）：请把 DATABASE_BACKEND 设为 postgres 并配置 "
                f"POSTGRES_DSN；当前值为 {backend or '(空)'!r}。"
                "SQLite → PostgreSQL 的迁移步骤见 deploy/enable_postgres.sh 与 "
                "docs/adr/0089-retire-sqlite-postgres-only.md。"
            )
        return await self._connect_postgres()

    async def _connect_postgres(self) -> bool:
        """连接 PostgreSQL（多租户形态）。

        与 SQLite 分支的关键差异：**不在启动期建表**。schema 由
        ``migrations/pg/0001_initial_schema.sql`` 建立——启动期改结构会让
        「schema 是谁改的」不可追溯。这里只做连接与表视图绑定。
        """
        from db_core.backend import pg as pg_backend

        logger.info("🔗 正在连接 PostgreSQL...")
        self._migrations_complete = False
        try:
            dsn = getattr(settings, "POSTGRES_DSN", "") or None
            self._main_conn = await pg_backend.connect(dsn)

            tables = list(ALL_TABLES) + list(RECIPE_TABLES) + list(HYGIENE_TABLES)
            for table in tables:
                self._table_views[table] = TableView(table, self._main_conn)

            self.stats['connection_count'] += 1
            self._migrations_complete = True
            logger.info("✅ PostgreSQL 连接成功 (%d 表)", len(self._table_views))
            return True
        except Exception as e:
            logger.error(f"❌ PostgreSQL 连接失败: {e}")
            return False

    # ── 就绪探针（只读，供健康检查使用） ──

    def is_connected(self) -> bool:
        """主连接是否已建立。"""
        return self._main_conn is not None

    def migrations_complete(self) -> bool:
        """建表与迁移是否已全部完成（``connect()`` 成功后才为真）。"""
        return self._migrations_complete

    async def readable_tables(self, tables) -> Dict[str, Any]:
        """只读探测若干关键表是否存在且可查询。

        返回 ``{"readable": bool, "missing": [...], "errors": [...]}``；不修改任何数据。

        两个仍然要注意的点（PG 下踩过）：

        - **表名目录**：走 ``pg_tables``（SQLite 的 ``sqlite_master`` 已随 ADR 0089 退场）；
        - **执行姿势**：用 ``cursor = await conn.execute(...)``，而不是
          ``async with conn.execute(...)`` —— PG 后端的 ``execute`` 是 ``async def``，
          返回 coroutine，不满足异步上下文管理器协议。
        """
        missing: list = []
        errors: list = []
        if self._main_conn is None:
            return {"readable": False, "missing": list(tables), "errors": ["数据库未连接"]}

        names_sql = "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"

        try:
            cursor = await self._main_conn.execute(names_sql)
            names = {row[0] for row in await cursor.fetchall()}
        except Exception as exc:  # pragma: no cover - defensive
            return {"readable": False, "missing": list(tables), "errors": [str(exc)]}

        for table in tables:
            if table not in names:
                missing.append(table)
                continue
            try:
                cursor = await self._main_conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
                await cursor.fetchone()
            except Exception as exc:
                errors.append(f"{table}: {exc}")

        return {
            "readable": not missing and not errors,
            "missing": missing,
            "errors": errors,
        }

    async def close(self):
        """关闭单一连接"""
        if self._main_conn is not None:
            await self._main_conn.close()
            self._main_conn = None
        self._table_views.clear()
        self._migrations_complete = False
        logger.info("🔒 数据库连接已关闭")

    # ── 主连接（所有表已同库，跨表查询可直接 JOIN） ──

    @property
    def _conn(self):
        """主连接；所有表都在同一个库里，跨表查询直接写表名即可。"""
        return self._main_conn

    # ── 表访问器 ──

    def table(self, name: str) -> TableView:
        """Public per-table view over the shared database connection."""
        try:
            return self._table_views[name]
        except KeyError as exc:
            raise KeyError(f"未知表: {name}") from exc

    def table_or_none(self, name: str) -> Optional[TableView]:
        """Like ``table`` but returns None for unknown names (import/backup paths)."""
        return self._table_views.get(name)

    # ── 内部工具 ──

    def _ensure_beijing_datetime(self, dt_input):
        return ensure_beijing_datetime(dt_input)

    def _row_to_dict(self, row) -> Dict:
        return row_to_dict(row)

    async def _execute(self, sql: str, params: tuple = ()):
        cursor = await self._main_conn.cursor()
        await cursor.execute(sql, params)
        return cursor
