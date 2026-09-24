#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的连接门面：持有一个 :class:`DatabaseConnection`，并把它接到
DatabaseManager 的调用面上（``table`` / ``is_connected`` / ``readable_tables`` /
状态统计）。

连接的所有权在 :mod:`db_core.database_connection`——建立、替换、关闭、以及回答
「还能用吗」。这里不再自己管 ``PgConnection``，也不再自己拼判据。

SQLite 已在 ADR 0089 退场：这里不再有建表自愈、`PRAGMA` 体检、统计信息维护与
导出 .db（「导出 DB」现在直接给整库 pg_dump，见 :mod:`services.backup_service`）。
"""

import logging
import asyncio
from typing import Any, Dict, Optional

from config import settings

from db_core.database_connection import DatabaseConnection
from db_core.utils import ensure_beijing_datetime, row_to_dict

logger = logging.getLogger(__name__)


class _ConnectionMixin:
    """DatabaseManager 的连接门面（连接本身在 DatabaseConnection）。"""

    def __init__(self):
        self.paths: Dict[str, str] = settings.DATABASE_PATHS
        # 连接的所有者：建立 / 替换 / 关闭 / 可用性都在它里面。
        self._connection = DatabaseConnection()
        # 全局写锁，由 connect() 真正建出来（asyncio.Lock 必须在事件循环里创建）。
        # HygieneWork / EmployeeAccounts 通过 owner 共享这一把；这里要是 None，它们
        # 就各自退回一把局部锁，两个 service 的隐式事务会互相穿插、互相 rollback。
        self._write_lock: Optional[asyncio.Lock] = None

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
        # **只建一次**：每次 connect() 都重建会让「断开前后」两把锁并存，整库恢复
        # 窗口里 serialized_write 的互斥契约随之失效（CORR-01 的放大器）。
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        backend = (getattr(settings, "DATABASE_BACKEND", "") or "").strip().lower()
        if backend != "postgres":
            raise RuntimeError(
                "SQLite 后端已移除（ADR 0089）：请把 DATABASE_BACKEND 设为 postgres 并配置 "
                f"POSTGRES_DSN；当前值为 {backend or '(空)'!r}。"
                "SQLite → PostgreSQL 的迁移步骤见 deploy/enable_postgres.sh 与 "
                "docs/adr/0089-retire-sqlite-postgres-only.md。"
            )
        ok = await self._connection.connect()
        if ok:
            self.stats['connection_count'] += 1
        return ok

    # ── 就绪探针（只读，供健康检查使用） ──

    def is_connected(self) -> bool:
        """当前是否有一条**可用**连接。

        判据在连接所有者那里（``PgConnection.alive()`` → asyncpg 的
        ``is_closed()``）。这里不再拼 ``.raw is not None``：``close()`` 刻意不清
        ``_raw``（清了会让重连改走「新建对象」，持有者的引用随之僵尸），所以看
        ``raw`` 的写法在关闭之后会一直报「已连接」。
        """
        return self._connection.alive()

    def is_reconnecting(self) -> bool:
        """连接是否正处在「重连窗口」（``_raw`` 已摘空、新连接还没建好）。

        ``main.py`` 用它让业务路由在数据库恢复窗口里快速回 503 + ``retryable``
        （DATA-02），``/api/healthz`` 用的是同一份判据。

        取态方式与 :meth:`bound_loop` 一致：连接所有者
        （``db_core/database_connection.py``，本簇不改）没有转出这个属性，而
        ``native_connection()`` 在窗口里同样是 None（它在「已关闭」时也是 None，
        分不出两种状态），所以这里只读地看一眼 ``_pg``。
        """
        pg = getattr(self._connection, "_pg", None)
        return bool(getattr(pg, "reconnecting", False))

    async def pending_migration_count(self) -> Optional[int]:
        """待应用的增量迁移条数；连接不可用时返回 ``None``（= 不可判定）。

        ``migrations/pg/0001`` 是 bootstrap（含 ``DROP TABLE``），不计入待应用清单
        ——判据统一交给 :func:`services.db_migrations.migration_status`，这里不另
        立一套。
        """
        if not self._connection.alive():
            return None
        from services.db_migrations import migration_status

        status = await migration_status(self)
        return len(status.pending)

    async def migrations_complete(self) -> bool:
        """所有增量迁移都已应用（``schema_migrations`` 里没有待应用项）。

        旧实现回答的是「连接是否已建立」（``connect()`` 成功即真）——于是带 schema
        变更的升级、还没点「应用迁移」的门店照样报 ready（PERF-11）。就绪口径里的
        ``migrations_complete`` 现在由「待应用迁移数 == 0」驱动。

        注：``db_core/database_connection.py::migrations_complete``（``connect()``
        时置位的那份）因此不再有生产消费者；该文件属于另一个文件簇，本次不动。
        """
        try:
            pending = await self.pending_migration_count()
        except Exception as exc:
            logger.warning("读取迁移状态失败，按「迁移未完成」处理: %s", exc)
            return False
        return pending == 0

    def bound_loop(self):
        """这条连接绑在哪个事件循环上（还没连过就是 None）。

        给需要判断「进程级单例是不是绑在一个已关闭的循环上」的人用，见
        ``tests/conftest.py`` 的 ``_drop_dead_loop_connections``——那件事本来要挖
        ``_conn._raw._loop`` 三层私有字段。
        """
        return self._connection.bound_loop()

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
        if not self._connection.alive():
            return {"readable": False, "missing": list(tables), "errors": ["数据库未连接"]}

        names_sql = "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"

        try:
            cursor = await self._connection.execute(names_sql)
            names = {row[0] for row in await cursor.fetchall()}
        except Exception as exc:  # pragma: no cover - defensive
            return {"readable": False, "missing": list(tables), "errors": [str(exc)]}

        for table in tables:
            if table not in names:
                missing.append(table)
                continue
            try:
                cursor = await self._connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
                await cursor.fetchone()
            except Exception as exc:
                errors.append(f"{table}: {exc}")

        return {
            "readable": not missing and not errors,
            "missing": missing,
            "errors": errors,
        }

    async def close(self):
        """关闭连接；**本对象与持有者手里那份 ``TableView`` 都留着**，
        ``connect()`` 原地接回来。

        连接对象在装配期就分发给了持有者（``main.py`` 把 ``_conn`` 注入
        ``RecipeStore``，``TableView`` 各自也捏着一份）。把它丢掉再重建就会让那些
        引用指向已关闭的连接——2026-09-23 整库恢复后配方接口全量 500 的成因。
        恢复完再接回来，所有持有者自动跟上。

        表视图注册表照旧清空（``db.table(...)`` 在断开窗口里取不到东西，既有行为），
        但分发出去的那些 ``TableView`` 指向本对象，接回来之后照样能用。
        """
        await self._connection.close()
        logger.info("🔒 数据库连接已关闭")

    # ── 主连接（所有表已同库，跨表查询可直接 JOIN） ──

    @property
    def _conn(self) -> DatabaseConnection:
        """连接所有者。

        持有者（``RecipeStore``、卫生的两个 service、admin 通用 CRUD）用它取
        ``cursor()`` / ``execute()`` / ``commit()`` / ``rollback()``，不必知道驱动是谁。
        """
        return self._connection

    # ── 表访问器 ──

    def table(self, name: str):
        """按表名取视图——**对外的公开入口**。

        db_core 内部的 repo mixin 已直接走 ``self._connection.table(...)``
        （84 处，2026-09-23 迁完），所以这里现在只服务外部调用者：``services/``
        的备货与对账、``api/admin.py`` 的通用 CRUD、``scraper/``。
        """
        return self._connection.table(name)

    def table_or_none(self, name: str):
        """同 ``table``，未知表名返回 None（导入/备份路径）。"""
        return self._connection.table_or_none(name)

    # ── 内部工具 ──

    def _ensure_beijing_datetime(self, dt_input):
        return ensure_beijing_datetime(dt_input)

    def _row_to_dict(self, row) -> Dict:
        return row_to_dict(row)
