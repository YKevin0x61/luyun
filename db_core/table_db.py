#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Per-table view (TableView) over the shared app.db connection.

All business tables share the connection from ``_ConnectionMixin.connect()``.
``TableView`` keeps the ``.conn`` / ``.execute()`` / ``.commit()`` / ``.get_count()``
surface used by repo mixins and ``DatabaseManager.table()``.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate_orders_kds_columns(conn: aiosqlite.Connection) -> None:
    """为已有 orders 表补 KDS 控菜字段。爬虫 INSERT-only 不写这些列。"""
    async with conn.cursor() as cursor:
        await cursor.execute("PRAGMA table_info(orders)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "dish_status" not in cols:
            await cursor.execute(
                "ALTER TABLE orders ADD COLUMN dish_status TEXT DEFAULT '待出餐'"
            )
        if "ready_time" not in cols:
            await cursor.execute("ALTER TABLE orders ADD COLUMN ready_time TEXT")
        if "source" not in cols:
            await cursor.execute(
                "ALTER TABLE orders ADD COLUMN source TEXT DEFAULT ''"
            )
        # 历史空值：notes 带外卖平台的标外卖，其余标堂食
        await cursor.execute(
            "UPDATE orders SET source = 'delivery' "
            "WHERE COALESCE(source, '') = '' AND notes LIKE '外卖平台:%'"
        )
        await cursor.execute(
            "UPDATE orders SET source = 'dine_in' "
            "WHERE COALESCE(source, '') = ''"
        )
        if "steamer_id" not in cols:
            await cursor.execute("ALTER TABLE orders ADD COLUMN steamer_id TEXT")
        if "port_index" not in cols:
            await cursor.execute("ALTER TABLE orders ADD COLUMN port_index INTEGER")
        if "stack_order" not in cols:
            await cursor.execute("ALTER TABLE orders ADD COLUMN stack_order INTEGER")
        if "loaded_at" not in cols:
            await cursor.execute("ALTER TABLE orders ADD COLUMN loaded_at TEXT")
    await conn.commit()


class TableView:
    """单表访问视图，内部持有单库 app.db 的共享连接。"""

    def __init__(self, table: str, conn: aiosqlite.Connection):
        self.table = table
        self._conn = conn

    @property
    def conn(self) -> aiosqlite.Connection:
        return self._conn

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        cursor = await self._conn.cursor()
        await cursor.execute(sql, params)
        return cursor

    async def commit(self):
        await self._conn.commit()

    async def get_count(self, extra_sql: str = "") -> int:
        sql = f"SELECT COUNT(*) FROM {self.table}" + (f" WHERE {extra_sql}" if extra_sql else "")
        async with self._conn.cursor() as c:
            await c.execute(sql)
            return (await c.fetchone())[0]
