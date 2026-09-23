#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务库连接的所有者（CONTEXT.md「数据库连接 (Database Connection)」）。

进程内**唯一**拥有业务库连接的东西：建立、替换、关闭，以及回答「现在这条连接
还能用吗」。持有者（``TableView`` / ``RecipeStore`` / 卫生的三个 service）拿到的
是这个对象，而不是里面的驱动连接——连接在整库恢复等场景下被换掉时它**原地不变**，
持有者缓存的引用不会变成僵尸。

``PgConnection``（:mod:`db_core.backend.pg`）是内部实现：它是 aiosqlite 形态的驱动
shim，``_raw`` / ``_loop`` / ``_tx`` / ``_guard`` 都留在它里面，不出现在持有者视野。

**「对象是否存在」与「驱动连接是否活着」是两件事**：前者决定要不要新建这个对象
（见 :meth:`connect`），后者只回答能不能用（见 :meth:`alive`）。混在一起就会出现
「重连的 await 窗口里被误判成没有连接 → 新建对象 → 持有者的引用僵尸」。
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from config import settings

from db_core.schema import ALL_TABLES, HYGIENE_TABLES, RECIPE_TABLES

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """业务库连接的所有者；接口见模块 docstring。"""

    def __init__(self):
        # 实际类型是 db_core.backend.pg.PgConnection。按鸭子类型标注，免得把驱动
        # 导入到这一层——它只是内部实现。
        self._pg: Optional[Any] = None
        self._table_views: Dict[str, Any] = {}
        self._migrations_complete = False

    # ── 生命周期 ──────────────────────────────────────────────

    async def connect(self) -> bool:
        """建立连接并把表视图绑上去；已经有这个对象了就原地接回来。

        判据是「有没有这个对象」，**不是**「里面那条驱动连接活不活」——后者在
        重连的 await 窗口里会短暂为假（``_reconnect_raw`` 把 ``_raw`` 摘成 None
        再连），拿它当判据会在这里新建对象，而持有者缓存的正是旧对象。
        """
        from db_core.backend import pg as pg_backend
        from db_core.table_db import TableView

        logger.info("🔗 正在连接 PostgreSQL...")
        self._migrations_complete = False
        try:
            dsn = getattr(settings, "POSTGRES_DSN", "") or None
            if self._pg is None:
                self._pg = await pg_backend.connect(dsn)
            else:
                await self._pg.rebind(dsn)

            tables = list(ALL_TABLES) + list(RECIPE_TABLES) + list(HYGIENE_TABLES)
            for table in tables:
                self._table_views[table] = TableView(table, self)

            self._migrations_complete = True
            logger.info("✅ PostgreSQL 连接成功 (%d 表)", len(self._table_views))
            return True
        except Exception as e:
            logger.error(f"❌ PostgreSQL 连接失败: {e}")
            return False

    async def close(self) -> None:
        """断开驱动连接；**本对象与持有者手里那份 ``TableView`` 都留着**。

        持有者在装配期就拿到了这个对象与各自那份 ``TableView``（``main.py`` 把
        ``_conn`` 注入 ``RecipeStore``）。把对象丢掉再重建，那些引用就指向已关闭
        的连接——2026-09-23 整库恢复后配方接口全量 500 的成因。断开之后
        :meth:`connect` 原地接回来，所有持有者自动跟上。

        注册表照旧清空（``db.table(...)`` 在断开窗口里取不到东西，这是既有行为），
        但已经分发出去的那些 ``TableView`` 指向本对象，接回来之后照样能用。
        """
        if self._pg is not None:
            await self._pg.close()
        self._table_views.clear()
        self._migrations_complete = False
        logger.info("🔒 数据库连接已关闭")

    # ── 状态 ──────────────────────────────────────────────────

    def alive(self) -> bool:
        """当前这条连接还能用吗。判据在 ``PgConnection.alive()``（asyncpg 的
        ``is_closed()``），不是「这个对象在不在」。"""
        return self._pg is not None and self._pg.alive()

    def migrations_complete(self) -> bool:
        """连接是否已建立（PG 分支启动期不改结构，所以这只意味着「连上了」）。"""
        return self._migrations_complete

    def bound_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """这条连接绑在哪个事件循环上（还没连过就是 None）。

        asyncpg 连接不能跨循环使用，所以「绑在哪个循环」是它的真实属性。给需要
        判断「全局单例是不是绑在一个已关闭的循环上」的人用，见
        ``tests/conftest.py`` 的 ``_drop_dead_loop_connections``。
        """
        return getattr(self._pg, "_loop", None)

    def native_connection(self):
        """内层驱动连接（asyncpg），**仅供诊断与探针**；还没连过就是 None。

        业务代码不要用它：执行普通 SQL 走 :meth:`execute`，它经方言层把 ``?`` 重写成
        ``$n``。这里存在是因为索引与超时探针必须用 PG 原生占位符跑 ``EXPLAIN`` /
        ``SHOW``，绕开方言层（见 ``tests/test_hygiene_indexes.py``、
        ``tests/test_pg_timeouts.py``）。
        """
        return getattr(self._pg, "raw", None)

    # ── 表视图 ────────────────────────────────────────────────

    def table(self, name: str):
        """按表名取视图。表视图是「这条连接上有哪些表」的自然归属，住在这里。"""
        try:
            return self._table_views[name]
        except KeyError as exc:
            raise KeyError(f"未知表: {name}") from exc

    def table_or_none(self, name: str):
        """同 :meth:`table`，未知表名返回 None（导入/备份路径用）。"""
        return self._table_views.get(name)

    # ── 执行面（转给内部驱动连接）──────────────────────────────
    #
    # 持有者需要执行能力，但不需要知道驱动是谁。这几个方法转发到 PgConnection；
    # 它的 cursor 类型（PgCursor）支持 `async with`，admin 的通用 CRUD 依赖这一点。

    def _require_pg(self):
        """取内层驱动连接；还没有连接时给一句人话。

        ``_conn`` 现在恒返回本对象（不再用 None 表示「没连接」），所以调用方
        「取到对象就以为能执行」的写法原本会撞 ``AttributeError: 'NoneType' object
        has no attribute ...``——看着像代码 bug，其实是「库还没连上」。判据与
        ``is_connected()`` 同源；连接**已关闭**（非 None）仍交给 asyncpg 自己的
        ``InterfaceError``，见 ``PgCursor.execute``。
        """
        if self._pg is None:
            raise RuntimeError("PostgreSQL 连接尚未建立，无法执行语句")
        return self._pg

    def cursor(self):
        return self._require_pg().cursor()

    async def execute(self, sql: str, params: tuple = ()):
        return await self._require_pg().execute(sql, params)

    async def executemany(self, sql: str, seq) -> None:
        await self._require_pg().executemany(sql, seq)

    async def commit(self) -> None:
        await self._require_pg().commit()

    async def rollback(self) -> None:
        await self._require_pg().rollback()

    def in_transaction(self) -> bool:
        return bool(self._pg is not None and self._pg.in_transaction())
