#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Per-table view (TableView) over the shared PostgreSQL connection.

All business tables share the connection owned by
:class:`db_core.database_connection.DatabaseConnection`.
``TableView`` keeps the ``.conn`` / ``.execute()`` / ``.commit()`` / ``.get_count()``
surface used by repo mixins and ``DatabaseManager.table()``.

``.conn`` 交出去的是那个**连接所有者**，不是驱动连接（``PgConnection``）：所有者
原地不变，整库恢复换掉内部连接时持有者的引用不会僵尸。它提供 ``cursor()`` /
``execute()`` / ``commit()`` / ``rollback()``，所以既有调用点不必改。

SQLite 退场（ADR 0089）后这里不再有启动期列迁移：``migrate_orders_kds_columns``
当年靠 ``PRAGMA table_info`` + ``ALTER TABLE`` 给老库补 KDS 列，现在结构由
``migrations/pg/000N_*.sql`` 负责，启动期不改结构。
"""

import logging

from db_core.database_connection import DatabaseConnection

logger = logging.getLogger(__name__)


class TableView:
    """单表访问视图，内部持有业务库的连接所有者。"""

    def __init__(self, table: str, conn: DatabaseConnection):
        self.table = table
        self._conn = conn

    @property
    def conn(self) -> DatabaseConnection:
        return self._conn

    async def execute(self, sql: str, params: tuple = ()):
        cursor = await self._conn.cursor()
        await cursor.execute(sql, params)
        return cursor

    async def commit(self):
        await self._conn.commit()

    async def get_count(self, extra_sql: str = "") -> int:
        sql = f"SELECT COUNT(*) FROM {self.table}" + (f" WHERE {extra_sql}" if extra_sql else "")
        cursor = await self.execute(sql)
        return (await cursor.fetchone())[0]
