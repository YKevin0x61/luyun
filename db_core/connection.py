#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的连接/生命周期职责：
建立单一 app.db 连接（WAL），按表缓存 TableView，关闭连接、备份导出。
"""

import logging
import os
from typing import Dict, Optional

import aiosqlite
from config import settings

from db_core.schema import ALL_TABLES, _TABLE_SCHEMAS, _INDEX_DEFINITIONS
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

        self.stats = {
            'queries_executed': 0,
            'slow_queries': 0,
            'connection_count': 0
        }

    async def connect(self) -> bool:
        """建立单一 app.db 连接（WAL），建齐全部表结构 + 索引；各表共享该连接。"""
        logger.info("🔗 正在连接单库 app.db (WAL)...")
        try:
            app_db_path = settings.APP_DB_PATH
            os.makedirs(os.path.dirname(app_db_path), exist_ok=True)

            self._main_conn = await aiosqlite.connect(app_db_path)
            self._main_conn.row_factory = aiosqlite.Row
            await self._main_conn.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE_WAL}")
            await self._main_conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")

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
            await self._main_conn.commit()

            # 4. 各表共享同一连接的 TableView
            for table in ALL_TABLES:
                self._table_views[table] = TableView(table, self._main_conn)

            self.stats['connection_count'] += 1
            logger.info(f"✅ 单库连接成功 ({len(self._table_views)} 表 → {app_db_path})")
            return True
        except Exception as e:
            logger.error(f"❌ 单库连接失败: {e}")
            return False

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
            await self._main_conn.close()
            self._main_conn = None
        self._table_views.clear()
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
