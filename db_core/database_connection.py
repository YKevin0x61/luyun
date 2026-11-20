#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务库连接的所有者（GLOSSARY.md「数据库连接 (Database Connection)」）。

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
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from config import settings

from db_core.schema import (
    ALL_TABLES,
    ATTENDANCE_TABLES,
    HYGIENE_TABLES,
    OVERTIME_TABLES,
    RECIPE_TABLES,
    SCHEDULING_TABLES,
    STAFF_MEAL_TABLES,
    WASTAGE_TABLES,
    WECOM_SUBSCRIPTION_TABLES,
)

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """业务库连接的所有者；接口见模块 docstring。"""

    def __init__(self):
        # 实际类型是 db_core.backend.pg.PgConnection。按鸭子类型标注，免得把驱动
        # 导入到这一层——它只是内部实现。
        self._pg: Optional[Any] = None
        self._table_views: Dict[str, Any] = {}
        self._migrations_complete = False
        # 全局写锁：**只建一次**（重连不换对象，见 CORR-01）。懒建是因为
        # asyncio.Lock 需要运行中的事件循环，而 connect() 之前就可能有人问它。
        self._write_lock: Optional[asyncio.Lock] = None
        # 写单元 / 装饰器作用域的状态。都是**实例级**的，所以判定必须带上
        # 「是不是当前任务」——否则并发的另一个任务会把自己当成在块里，
        # 它的 commit() 就被吞掉了。
        self._unit_task: Optional[asyncio.Task] = None
        self._unit_depth = 0
        self._unit_aborted = False
        self._scope_task: Optional[asyncio.Task] = None
        self._scope_depth = 0

    # ── 生命周期 ──────────────────────────────────────────────

    async def connect(self) -> bool:
        """建立连接并把表视图绑上去；已经有这个对象了就原地接回来。

        判据是「有没有这个对象」，**不是**「里面那条驱动连接活不活」——后者在
        重连的 await 窗口里会短暂为假（``_reconnect_raw`` 把 ``_raw`` 摘成 None
        再连），拿它当判据会在这里新建对象，而持有者缓存的正是旧对象。

        返回 ``False`` 表示这次没接上，**调用方必须看返回值**：恢复/导入收尾处
        接不回连接不是「稍后重试就好」——库已经被覆盖、应用连不上，进程会一直
        停在半恢复状态，所以那两处必须把失败翻译成说得出口的错误（DATA-01）。
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

            tables = (
                list(ALL_TABLES)
                + list(RECIPE_TABLES)
                + list(HYGIENE_TABLES)
                + list(SCHEDULING_TABLES)
                + list(WECOM_SUBSCRIPTION_TABLES)
                + list(OVERTIME_TABLES)
                + list(ATTENDANCE_TABLES)
                + list(WASTAGE_TABLES)
                + list(STAFF_MEAL_TABLES)
            )
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

    # ── 写单元（GLOSSARY.md「写单元 (Write Unit)」）──────────────
    #
    # 锁与提交的收尾**只有这一处**：领域 service 只管写，什么时候提交由这里决定。
    # 这样「离职 = 删排班 + 落日期」这种跨服务的编排才能落在一个事务里，
    # 新模块也不可能再「忘了加锁 / 忘了回滚」。

    @property
    def write_lock(self) -> asyncio.Lock:
        """全局写锁（**只建一次**：重连不换对象，见 CORR-01）。"""
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        return self._write_lock

    @staticmethod
    def _current_task() -> Optional[asyncio.Task]:
        try:
            return asyncio.current_task()
        except RuntimeError:  # 没有运行中的循环（同步探针）
            return None

    def _in_unit(self) -> bool:
        return self._unit_task is not None and self._unit_task is self._current_task()

    def _in_scope(self) -> bool:
        return self._scope_task is not None and self._scope_task is self._current_task()

    @asynccontextmanager
    async def write(self):
        """写单元：进块取锁，出口提交；块内异常整块回滚。

        块内的 commit() 只表示「这一步写完了」，真正落库在出口——一段编排
        （离职：删排班 + 落日期；仪容判定：读状态 + 改状态）要么一起成、要么一起不成。
        嵌套的单元只加深一层，提交留给最外层。
        """
        if self._in_unit():
            self._unit_depth += 1
            try:
                yield self
            finally:
                self._unit_depth -= 1
            return
        if self._in_scope():
            raise RuntimeError(
                "不能在 @serialized_write 里再开写单元：装饰器已经握着这把锁，"
                "单元再去等它就是等自己。把单元放到装饰方法外面。"
            )
        if self._pg is None:
            # 还没连上：没有事务可管（探针 / 假连接），原样放行让调用方自己撞错。
            yield self
            return

        lock = self.write_lock
        await lock.acquire()
        self._unit_task = self._current_task()
        self._unit_depth = 1
        self._unit_aborted = False
        try:
            try:
                yield self
            except BaseException:
                await self._pg.rollback()
                raise
            else:
                if self._unit_aborted:
                    await self._pg.rollback()
                else:
                    await self._pg.commit()
        finally:
            self._unit_depth = 0
            self._unit_task = None
            self._unit_aborted = False
            lock.release()

    @asynccontextmanager
    async def write_scope(self):
        """@serialized_write 的作用域：取锁 + 异常回滚，**提交仍归方法自己**。

        在写单元里它是被动的（锁已经在单元手里、提交也归单元），所以把一个既有写方法
        编排进单元时，它不用改一行。同一个任务里嵌套的装饰方法也直接放行——写锁是
        asyncio.Lock，不可重入，重复 acquire 会把协程挂死。
        """
        if self._in_unit():
            yield self
            return
        if self._in_scope():
            self._scope_depth += 1
            try:
                yield self
            finally:
                self._scope_depth -= 1
            return
        if self._pg is None:
            yield self
            return

        lock = self.write_lock
        await lock.acquire()
        self._scope_task = self._current_task()
        self._scope_depth = 1
        try:
            try:
                yield self
            except BaseException:
                await self._pg.rollback()
                raise
        finally:
            self._scope_depth = 0
            self._scope_task = None
            lock.release()

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
        """提交；**在写单元里只是「这一步写完了」**，落库在单元出口。"""
        if self._in_unit():
            return
        await self._require_pg().commit()

    async def rollback(self) -> None:
        """回滚；在写单元里把整块标作废，出口统一回滚（内层不提前收尾）。"""
        if self._in_unit():
            self._unit_aborted = True
            return
        await self._require_pg().rollback()

    def in_transaction(self) -> bool:
        return bool(self._pg is not None and self._pg.in_transaction())
