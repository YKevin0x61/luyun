#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整库恢复后，持有连接引用的模块必须还能用。

`api/backup.py` 的「恢复数据库」会先 `db.close()`（pg_restore --clean 要 drop
并重建对象，我方连接上的未提交事务会持表锁让 drop 卡住），恢复完再 `db.connect()`。
`db.connect()` 如果每次都换一个新的 PgConnection 对象，启动期按 `RecipeStore(conn=db_manager._conn)`
注入配方的 `recipe_store`（`main.py`）就会一直捏着那条**已关闭**的旧连接：
2026-09-23 12:06 本机做了一次整库恢复，之后 `/api/recipes/stations` 就稳定
500（`asyncpg.exceptions.InterfaceError: connection is closed`），
`/api/recipes/search`、岗位详情、配方列表等所有配方接口一起失效。

这里钉住的是「重连对已有持有者可见」这条契约：`RecipeStore` 必须在
`close()` + `connect()` 之后照常工作。
"""

import asyncio
import contextlib
import unittest

try:  # 独立验证用：绕开被测代码直读落库结果（row-level 证据）
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None  # type: ignore[assignment]

from database import DatabaseManager
from db_core.backend import pg as pg_backend
from db_core.backend.pg import DatabaseReconnecting, DatabaseUnavailable
from db_core.database_connection import DatabaseConnection
from services.recipes.store import RecipeStore

SEED_UPDATED_AT = "2026-01-01T00:00:00+00:00"
SEED_SLUG = "changfen-reconnect-probe"
# CORR-01 回归用的写单元：三句分居重连窗口两侧（窗口开在第一句之后）。
HEAD_SLUG = "changfen-reconnect-write-head"
MID_SLUG = "changfen-reconnect-write-mid"
TAIL_SLUG = "changfen-reconnect-write-tail"
AFTER_SLUG = "changfen-reconnect-write-after"


class ReconnectVisibilityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect(), "测试库连接失败")
        self.store = RecipeStore(conn=self.db._conn)
        await self.store.prepare()
        await self.db._conn.execute(
            "INSERT INTO sop_stations (slug, title, updated_at) VALUES (?, ?, ?)",
            (SEED_SLUG, "重连探针档", SEED_UPDATED_AT),
        )
        await self.db._conn.commit()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_recipe_store_survives_close_and_reconnect(self):
        before = await self.store.list_stations()
        self.assertIn(SEED_SLUG, [row["slug"] for row in before])

        # 整库恢复的姿势：断开自己的连接 → 恢复 → 接回来。
        await self.db.close()
        self.assertTrue(await self.db.connect(), "重连失败")

        after = await self.store.list_stations()
        self.assertIn(SEED_SLUG, [row["slug"] for row in after])

    async def test_reconnect_keeps_the_same_connection_object(self):
        """连接对象本身不能换：换了就等于把老引用变成僵尸。

        这条是上一条的因——`main.py` 在启动期把 `db_manager._conn` 交给
        `RecipeStore`，对象一换，注入出去的那份引用就再也追不上来了。
        """
        original = self.db._conn
        await self.db.close()
        self.assertTrue(await self.db.connect(), "重连失败")
        self.assertIs(self.db._conn, original)

    async def test_is_connected_tells_the_truth_after_close(self):
        """``is_connected()`` 必须在 ``close()`` 之后说真话。

        它的旧判据是 ``_conn.raw is not None``，而 ``PgConnection.close()`` 刻意
        不清 ``_raw``（清了就会让 ``connect()`` 改走「新建对象」，与上一条冲突），
        所以关闭之后它一直报「已连接」——``services/release_update/readiness.py``
        的就绪确认据此认为数据库还活着。判据现在交给 ``PgConnection.alive()``，
        由 asyncpg 的 ``is_closed()`` 回答。
        """
        self.assertTrue(self.db.is_connected(), "连接建立后应报可用")
        await self.db.close()
        self.assertFalse(self.db.is_connected(), "close() 之后不该再报可用")

    async def test_reconnect_keeps_the_same_write_lock(self):
        """``connect()`` 前后必须是**同一把**全局写锁（CORR-01 的放大器）。

        旧实现每次 ``connect()`` 都 ``self._write_lock = asyncio.Lock()``：整库恢复
        前后两把锁并存，``services/hygiene/*`` 共享的那把与连接层用的那把不再是同一
        个对象，「写单元串行」的契约随之失效。
        """
        first = self.db._write_lock
        self.assertIsNotNone(first, "前置：连接建立后应当有全局写锁")
        await self.db.close()
        self.assertTrue(await self.db.connect(), "重连失败")
        self.assertIs(self.db._write_lock, first)

    async def test_reconnect_does_not_commit_a_half_finished_write_unit(self):
        """CORR-01 的实测形态：一个写 3 句的单元不许「只落最后 1 句」。

        修复前 ``_reconnect_raw`` 摘空 ``_raw``、回滚 ``_tx`` 再夺锁：受害写单元
        前两句已执行、醒来后把尾段提交到新连接上，调用方看到「成功」而前半段凭空
        消失。现在重连只「封存」——持有者收到 ``DatabaseReconnecting``，整个写单元
        失败（一条都不落），锁也不被抢走。
        """
        # ``_reconnect_raw`` / ``ensure_transaction`` / 串行锁都长在 PgConnection
        # 上；``db._conn`` 是持有者门面（DatabaseConnection），通过它拿底层对象。
        pg = self.db._connection._pg
        failures = []

        async def _foreign_write_unit() -> None:
            """模拟别人正在跑的写单元：第一句 → （重连窗口）→ 第二句 → 第三句 → commit。"""
            try:
                await pg.execute(
                    "INSERT INTO sop_stations (slug, title, updated_at) VALUES (?, ?, ?)",
                    (HEAD_SLUG, "重连写第一句", SEED_UPDATED_AT),
                )
                await asyncio.sleep(0.1)  # 窗口在这中间打开
                await pg.execute(
                    "INSERT INTO sop_stations (slug, title, updated_at) VALUES (?, ?, ?)",
                    (MID_SLUG, "重连写第二句", SEED_UPDATED_AT),
                )
                await pg.execute(
                    "INSERT INTO sop_stations (slug, title, updated_at) VALUES (?, ?, ?)",
                    (TAIL_SLUG, "重连写第三句", SEED_UPDATED_AT),
                )
                await pg.commit()
            except Exception as exc:  # noqa: BLE001 - 这里要的就是「收到了什么异常」
                failures.append(exc)
                with contextlib.suppress(Exception):
                    await pg.rollback()

        holder = asyncio.create_task(_foreign_write_unit())
        await asyncio.sleep(0.02)  # 让第一句先执行（它已经进了旧事务）
        await pg._reconnect_raw(asyncio.get_running_loop(), "重连探针")
        await asyncio.wait_for(holder, 5)

        slugs = [row["slug"] for row in await self.store.list_stations()]
        for label, slug in (
            ("第一句（窗口前）", HEAD_SLUG),
            ("第二句（窗口内）", MID_SLUG),
            ("第三句（窗口内）", TAIL_SLUG),
        ):
            self.assertNotIn(
                slug, slugs, f"写 3 句的单元不该只落{label}（修复前的形态）"
            )
        self.assertTrue(failures, "写单元必须收到明确异常，而不是静默成功")
        self.assertIsInstance(failures[0], DatabaseReconnecting)

    async def test_second_session_still_works_after_the_window_closes(self):
        """窗口关掉后：写队列照常可用，锁没被夺走、也没被永久占住。"""
        pg = self.db._connection._pg
        holder_started = asyncio.Event()

        async def _holder() -> None:
            await pg.ensure_transaction()
            holder_started.set()
            await asyncio.sleep(0.2)
            with contextlib.suppress(Exception):
                await pg.rollback()  # 被打断后 rollback 仍能正常收尾

        holder = asyncio.create_task(_holder())
        await holder_started.wait()
        await pg._reconnect_raw(asyncio.get_running_loop(), "重连探针")
        self.assertTrue(pg.reconnecting is False, "窗口已经关掉了")
        await asyncio.wait_for(holder, 5)

        await pg.execute(
            "INSERT INTO sop_stations (slug, title, updated_at) VALUES (?, ?, ?)",
            (AFTER_SLUG, "重连后仍可写", SEED_UPDATED_AT),
        )
        await pg.commit()
        self.assertIn(
            AFTER_SLUG, [row["slug"] for row in await self.store.list_stations()]
        )


class UnconnectedGuardTest(unittest.IsolatedAsyncioTestCase):
    """还没连上库时，执行面要给一句人话。

    ``_conn`` 现在恒返回连接所有者对象（不再用 None 表示「没连接」），所以
    「取到对象就以为能执行」的调用方原本会撞 ``AttributeError: 'NoneType' object
    has no attribute 'cursor'``——那看着像代码 bug，其实只是「库还没连上」。
    """

    async def test_execute_before_connect_says_what_is_wrong(self):
        conn = DatabaseConnection()
        self.assertFalse(conn.alive())

        with self.assertRaises(RuntimeError) as raised:
            await conn.execute("SELECT 1")
        self.assertIn("尚未建立", str(raised.exception))

        with self.assertRaises(RuntimeError):
            conn.cursor()
        with self.assertRaises(RuntimeError):
            await conn.commit()


class IndependentWriteUnitAtomicityTest(unittest.IsolatedAsyncioTestCase):
    """独立验证（verifier-db）：并发写单元在重连窗口两侧只许「全落」或「全不落」。

    角度与 ``ReconnectVisibilityTest`` 的用例不同：那边是**一个**写单元 + 手工核对
    三个 slug 都不在；这里是**两个**写单元同时在飞（第二个正在排队等写锁），重连
    窗口开在两者中间，然后**逐单元**检查不变量：

    * 报成功 ⇒ 三句全在（3 行）；
    * 报失败 ⇒ 一行都不在（0 行）且拿到的是可重试的领域异常。

    「报成功但只落一部分」正是 CORR-01 的实测形态（写 3 句只落最后 1 句），也是
    旧实现（回滚在飞事务 + 夺锁）会复现的结果。行级证据用**独立的 asyncpg 连接**
    直读，不经过被测代码；按约定，任何情况下都不许出现「交错提交」的半截单元。
    """

    PREFIX = "ver1-atomic"
    #: 每句之间的停顿：让重连窗口稳定地开在单元中间。
    STEP_PAUSE = 0.08

    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect(), "测试库连接失败")
        self.pg = self.db._connection._pg

    async def asyncTearDown(self):
        with contextlib.suppress(Exception):
            await self.db.close()

    async def _landed_slugs(self) -> set:
        """独立连接直读：只回本用例前缀的行，不碰业务查询路径。"""
        self.assertIsNotNone(asyncpg, "asyncpg 不可用")
        raw = await asyncpg.connect(pg_backend.dsn_from_env(), timeout=5)
        try:
            rows = await raw.fetch(
                "SELECT slug FROM sop_stations WHERE slug LIKE $1", f"{self.PREFIX}-%"
            )
        finally:
            await raw.close()
        return {row["slug"] for row in rows}

    async def test_concurrent_write_units_are_whole_or_nothing(self):
        outcomes = {}

        async def _unit(tag: str) -> None:
            """三句一单元；中途撞上重连窗口。"""
            try:
                for part in ("head", "mid", "tail"):
                    await self.pg.execute(
                        "INSERT INTO sop_stations (slug, title, updated_at) VALUES (?, ?, ?)",
                        (f"{self.PREFIX}-{tag}-{part}", f"独立验证 {tag} {part}", SEED_UPDATED_AT),
                    )
                    await asyncio.sleep(self.STEP_PAUSE)
                await self.pg.commit()
                outcomes[tag] = "committed"
            except Exception as exc:  # noqa: BLE001 - 这里要的就是「收到了什么」
                outcomes[tag] = exc
                with contextlib.suppress(Exception):
                    await self.pg.rollback()

        first = asyncio.create_task(_unit("a"))
        await asyncio.sleep(0.02)
        second = asyncio.create_task(_unit("b"))
        await asyncio.sleep(0.12)  # a 已写第一句，b 正在排队等写锁
        await self.pg._reconnect_raw(asyncio.get_running_loop(), "独立验证重连窗口")
        await asyncio.wait_for(
            asyncio.gather(first, second, return_exceptions=True), timeout=10
        )

        landed = await self._landed_slugs()
        self.assertTrue(
            all(slug.startswith(f"{self.PREFIX}-") for slug in landed), landed
        )
        for tag in ("a", "b"):
            unit_rows = {s for s in landed if s.startswith(f"{self.PREFIX}-{tag}-")}
            outcome = outcomes.get(tag)
            self.assertIsNotNone(outcome, f"写单元 {tag} 没有任何结果")
            if outcome == "committed":
                self.assertEqual(
                    len(unit_rows),
                    3,
                    f"写单元 {tag} 报了成功却只落了 {sorted(unit_rows)}——半落（CORR-01 形态）",
                )
            else:
                self.assertIsInstance(
                    outcome,
                    DatabaseUnavailable,
                    f"写单元 {tag} 应当收到领域异常，实际 {outcome!r}",
                )
                self.assertTrue(
                    getattr(outcome, "retryable", False),
                    f"写单元 {tag} 的异常必须标成可重试：{outcome!r}",
                )
                self.assertEqual(
                    unit_rows,
                    set(),
                    f"写单元 {tag} 报了失败却落了 {sorted(unit_rows)}",
                )
