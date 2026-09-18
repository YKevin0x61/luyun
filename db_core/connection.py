#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的连接/生命周期职责：
建立单一 app.db 连接（WAL），按表缓存 TableView，关闭连接、备份导出。
"""

import logging
import os
import asyncio
import time
from typing import Any, Dict, Optional

import aiosqlite
from config import settings

from db_core.schema import (
    ALL_TABLES,
    HYGIENE_TABLES,
    RECIPE_TABLES,
    _INDEX_DEFINITIONS,
    _TABLE_SCHEMAS,
    apply_hygiene_schema,
    apply_recipe_schema,
)
from db_core.table_db import TableView, migrate_orders_kds_columns
from db_core.utils import (
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_JOURNAL_MODE_WAL,
    ensure_beijing_datetime,
    row_to_dict,
)

logger = logging.getLogger(__name__)


class _ConnectionMixin:
    """单库 app.db 连接建立、关闭与备份导出。"""

    def __init__(self):
        self.paths: Dict[str, str] = settings.DATABASE_PATHS
        # Internal cache for TableView instances (shared connection).
        self._table_views: Dict[str, TableView] = {}
        self._main_conn: Optional[aiosqlite.Connection] = None
        # Legacy: always empty under single-db architecture (ATTACH removed).
        self._attached_tables: set[str] = set()
        self._write_lock = None
        # Set once connect() has finished schema creation + migrations.
        self._migrations_complete = False

        self.stats = {
            'queries_executed': 0,
            'slow_queries': 0,
            'connection_count': 0
        }

    async def connect(self) -> bool:
        """建立业务库连接；后端由 ``settings.DATABASE_BACKEND`` 决定。

        默认仍是 sqlite，切到 postgres 需要显式配置——见 ADR 0084。
        """
        if getattr(settings, "DATABASE_BACKEND", "sqlite") == "postgres":
            return await self._connect_postgres()
        return await self._connect_sqlite()

    async def _connect_sqlite(self) -> bool:
        """建立单一 app.db 连接（WAL），建齐全部表结构 + 索引；各表共享该连接。"""
        logger.info("🔗 正在连接单库 app.db (WAL)...")
        self._migrations_complete = False
        try:
            app_db_path = settings.APP_DB_PATH
            os.makedirs(os.path.dirname(app_db_path), exist_ok=True)

            self._main_conn = await aiosqlite.connect(app_db_path)
            self._main_conn.row_factory = aiosqlite.Row
            await self._main_conn.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE_WAL}")
            await self._main_conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            await self._main_conn.execute("PRAGMA foreign_keys = ON")

            # 1. 建齐全部表结构（含 auth），CREATE TABLE IF NOT EXISTS 对已存在表安全无害
            for table in ALL_TABLES:
                schema = _TABLE_SCHEMAS.get(table, "")
                if schema:
                    await self._main_conn.executescript(schema)

            # 2. 迁移旧数据缺失的 KDS 列（orders 表）
            await migrate_orders_kds_columns(self._main_conn)

            # 3. 建齐索引
            for table in ALL_TABLES:
                for idx_sql in _INDEX_DEFINITIONS.get(table, []):
                    await self._main_conn.execute(idx_sql)

            # Recipe + hygiene tables: same file, not ALL_TABLES / TableView / Admin CRUD.
            await apply_recipe_schema(self._main_conn)
            await apply_hygiene_schema(self._main_conn)
            await self._main_conn.commit()

            # 3.5 查询统计信息：没有 sqlite_stat1 时优化器只能猜索引，实测会让
            # 「今日 + GROUP BY 菜品/档口」这类聚合退化成全索引扫描（18 万行）。
            await self._ensure_query_statistics()

            # 3.6 启动体检：quick_check 只做页级校验。业务库（订单/结算）绝不
            # 自动隔离——发现损坏只告警，交由人工决定，避免把唯一数据搬走。
            if settings.SQLITE_QUICK_CHECK_ON_START:
                await self._quick_check_or_warn(app_db_path)

            # 4. 各表共享同一连接的 TableView
            for table in ALL_TABLES:
                self._table_views[table] = TableView(table, self._main_conn)

            self.stats['connection_count'] += 1
            self._migrations_complete = True
            logger.info(f"✅ 单库连接成功 ({len(self._table_views)} 表 → {app_db_path})")
            return True
        except Exception as e:
            logger.error(f"❌ 单库连接失败: {e}")
            return False

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

    async def _quick_check_or_warn(self, app_db_path: str) -> None:
        """对 app.db 做一次页级体检；损坏只告警，不隔离。

        logs.db 损坏可以隔离重建（日志可丢），业务库不行：自动隔离会把订单/
        结算数据搬走。这里只把结论写进日志，让人来决定下一步。
        """
        assert self._main_conn is not None
        started = time.time()
        try:
            async with self._main_conn.execute("PRAGMA quick_check(1)") as cur:
                rows = await cur.fetchall()
        except Exception as exc:
            logger.error(
                "❌ app.db quick_check 执行失败（业务库不做自动隔离）: %s", exc
            )
            return
        problems = [str(r[0]) for r in rows if str(r[0]).strip().lower() != "ok"]
        elapsed = (time.time() - started) * 1000
        if problems:
            logger.error(
                "❌ app.db quick_check 未通过（业务库不做自动隔离，请人工处理）: %s",
                "; ".join(problems),
            )
            return
        logger.info(f"✅ app.db quick_check 通过（{elapsed:.0f}ms, {app_db_path}）")

    async def _orders_has_statistics(self) -> bool:
        """orders 表是否已有 sqlite_stat1 记录（空表 ANALYZE 不会写入记录）。"""
        assert self._main_conn is not None
        async with self._main_conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_stat1'"
        ) as cursor:
            row = await cursor.fetchone()
        if not row or not row[0]:
            return False
        async with self._main_conn.execute(
            "SELECT COUNT(*) FROM sqlite_stat1 WHERE tbl = 'orders'"
        ) as cursor:
            row = await cursor.fetchone()
        return bool(row and row[0])

    async def _ensure_query_statistics(self) -> None:
        """保证优化器有统计信息可用。

        没有统计信息时 SQLite 只能按内置猜测选索引：对
        `WHERE order_time >= ? AND station != 'loumian' GROUP BY dish_name, station`
        这类查询会选 idx_orders_dish_name 做全索引扫描（线上 18.4 万行），实测
        dashboard 聚合 133ms vs 0.4ms（详见 .scratch/perf-stress-test/PERF_REPORT.md）。

        注意：对空表执行 ANALYZE 不会写任何 sqlite_stat1 记录，所以判据是
        「orders 有没有统计记录」而不是「sqlite_stat1 表是否存在」；否则全新
        安装会在爬虫灌满数据后一直沿用错误计划。已有统计时改用 `PRAGMA optimize`
        由 SQLite 判断增量刷新，开销可忽略。
        """
        assert self._main_conn is not None
        if await self._orders_has_statistics():
            await self._main_conn.execute("PRAGMA optimize")
            await self._main_conn.commit()
            logger.info("📊 查询统计信息已存在，PRAGMA optimize 维护完成")
            return
        t0 = time.perf_counter()
        await self._main_conn.execute("ANALYZE")
        await self._main_conn.commit()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if await self._orders_has_statistics():
            logger.info(f"📊 已生成查询统计信息 (ANALYZE, {elapsed_ms:.0f}ms)")
        else:
            logger.info("📊 orders 暂无数据，跳过统计信息生成（下次启动再试）")

    def is_connected(self) -> bool:
        """主连接是否已建立。"""
        return self._main_conn is not None

    def migrations_complete(self) -> bool:
        """建表与迁移是否已全部完成（``connect()`` 成功后才为真）。"""
        return self._migrations_complete

    async def readable_tables(self, tables) -> Dict[str, Any]:
        """只读探测若干关键表是否存在且可查询。

        返回 ``{"readable": bool, "missing": [...], "errors": [...]}``；不修改任何数据。

        两处必须兼容两种后端，否则 PG 下会把所有关键表都判为不可读
        （现场 0.6.0 → 0.6.2 升级后就这样卡在「已切换但未健康」）：

        - **表名目录**：SQLite 在 ``sqlite_master``，PG 在 ``pg_tables``；
        - **执行姿势**：用 ``cursor = await conn.execute(...)``，而不是
          ``async with conn.execute(...)`` —— PG 后端的 ``execute`` 是 ``async def``，
          返回 coroutine，不满足异步上下文管理器协议（SQLite 的 aiosqlite 返回
          Cursor，所以这个写法一直没暴露）。
        """
        missing: list = []
        errors: list = []
        if self._main_conn is None:
            return {"readable": False, "missing": list(tables), "errors": ["数据库未连接"]}

        backend = (getattr(settings, "DATABASE_BACKEND", "sqlite") or "sqlite").lower()
        if backend == "postgres":
            names_sql = "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        else:
            names_sql = "SELECT name FROM sqlite_master WHERE type='table'"

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

    async def export_merged_sqlite_file(self, output_path: str) -> None:
        """
        导出单库 app.db 到指定路径（供后台「导出 DB」功能使用）。
        走 SQLite 官方 backup API：WAL 模式下也能拿到一致快照，无需手工建表/流式拷贝。
        """
        if os.path.exists(output_path):
            os.unlink(output_path)
        export_conn = await aiosqlite.connect(output_path)
        try:
            await self._main_conn.backup(export_conn)
        finally:
            await export_conn.close()

    async def close(self):
        """关闭单一连接"""
        if self._main_conn is not None:
            if getattr(settings, "DATABASE_BACKEND", "sqlite") == "sqlite":
                # SQLite 官方建议：关闭前跑一次 PRAGMA optimize，由它判断哪些表的
                # 统计信息因大量写入而过期（爬虫持续 INSERT 时尤其必要）。
                # PostgreSQL 没有 PRAGMA，跳过而不是让它报警告。
                try:
                    await self._main_conn.execute("PRAGMA optimize")
                    await self._main_conn.commit()
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(f"⚠️ 关闭前 PRAGMA optimize 失败（忽略）: {exc}")
            await self._main_conn.close()
            self._main_conn = None
        self._table_views.clear()
        self._migrations_complete = False
        logger.info("🔒 数据库连接已关闭")

    # ── 主连接（所有表已同库，跨表查询可直接 JOIN） ──

    @property
    def _conn(self) -> aiosqlite.Connection:
        """主连接；所有表均位于同一 app.db，跨表查询直接引用表名即可，无需 ATTACH。"""
        return self._main_conn

    # ── 表访问器 ──

    def table(self, name: str) -> TableView:
        """Public per-table view over the shared app.db connection."""
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

    def _row_to_dict(self, row: aiosqlite.Row) -> Dict:
        return row_to_dict(row)

    async def _execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        cursor = await self._main_conn.cursor()
        await cursor.execute(sql, params)
        return cursor
