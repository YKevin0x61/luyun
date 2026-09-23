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

import unittest

from database import DatabaseManager
from db_core.database_connection import DatabaseConnection
from services.recipes.store import RecipeStore

SEED_UPDATED_AT = "2026-01-01T00:00:00+00:00"
SEED_SLUG = "changfen-reconnect-probe"


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
