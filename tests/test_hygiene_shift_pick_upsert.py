#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hygiene_shift_picks 的 upsert 冲突目标必须匹配当前后端的唯一约束。

两种后端的唯一约束不一样：

    SQLite : UNIQUE (employee_id, business_date)            表里没有 tenant_id
    PG     : UNIQUE (tenant_id, employee_id, business_date) 多租户改造后加的

冲突目标写错时，PG 报「there is no unique or exclusion constraint matching the
ON CONFLICT specification」，SQLite 报「ON CONFLICT clause does not match any
PRIMARY KEY or UNIQUE constraint」。现场 0.6.0 + PG 上的
``POST /api/hygiene/staff/assignment`` 就是栽在这里。
"""

import asyncio
import sqlite3
import unittest
from unittest import mock

from config import settings
from db_core.backend import pg as pg_backend
from db_core.schema import _HYGIENE_TABLE_SCHEMAS
from services.hygiene.accounts import shift_pick_upsert_sql

try:  # asyncpg 是 PG 后端依赖；缺失时 PG 侧断言跳过
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None

# 哨兵值：不碰真实业务数据（负数主键不会与门店数据撞）
EMP_ID = -987654321
BIZ_DATE = "1970-01-01"


def sql_for_backend(backend: str, with_zone: bool) -> str:
    """按指定后端生成 upsert 语句（不依赖当前 shell 的后端设置）。"""
    with mock.patch.object(settings, "DATABASE_BACKEND", backend):
        return shift_pick_upsert_sql(with_zone=with_zone)


def pg_available() -> bool:
    if asyncpg is None:
        return False

    async def probe() -> bool:
        try:
            conn = await asyncpg.connect(pg_backend.dsn_from_env(), timeout=2)
        except Exception:
            return False
        await conn.close()
        return True

    try:
        return asyncio.run(probe())
    except Exception:
        return False


class ShiftPickUpsertSqliteTest(unittest.TestCase):
    def test_conflict_target_matches_sqlite_unique_constraint(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(_HYGIENE_TABLE_SCHEMAS["hygiene_shift_picks"])
            for with_zone in (True, False):
                with self.subTest(with_zone=with_zone):
                    sql = sql_for_backend("sqlite", with_zone)
                    self.assertNotIn("tenant_id", sql, "SQLite 表没有 tenant_id 列")
                    if with_zone:
                        first = (EMP_ID, BIZ_DATE, "白班", None, "t0", "t0")
                        second = (EMP_ID, BIZ_DATE, "夜班", None, "t1", "t1")
                    else:
                        first = (EMP_ID, BIZ_DATE, "白班", "t0", "t0")
                        second = (EMP_ID, BIZ_DATE, "夜班", "t1", "t1")
                    conn.execute(sql, first)
                    conn.execute(sql, second)  # 第二次必须走 ON CONFLICT DO UPDATE
            rows = conn.execute(
                "SELECT shift FROM hygiene_shift_picks "
                "WHERE employee_id = ? AND business_date = ?",
                (EMP_ID, BIZ_DATE),
            ).fetchall()
            self.assertEqual([r[0] for r in rows], ["夜班"])
        finally:
            conn.close()


@unittest.skipUnless(pg_available(), "PostgreSQL 不可用，跳过 PG 侧断言")
class ShiftPickUpsertPostgresTest(unittest.IsolatedAsyncioTestCase):
    async def test_conflict_target_matches_pg_unique_index(self):
        """PG 的唯一索引带 tenant_id，冲突目标必须带上它。"""
        conn = await pg_backend.connect()
        try:
            cur = await conn.execute("SELECT id FROM hygiene_employees ORDER BY id LIMIT 1")
            rows = await cur.fetchall()
            if not rows:
                self.skipTest("hygiene_employees 为空，无法满足外键约束")
            employee_id = int(rows[0][0])  # PG 侧有外键，必须用真实员工

            for with_zone in (True, False):
                with self.subTest(with_zone=with_zone):
                    sql = sql_for_backend("postgres", with_zone)
                    self.assertIn("tenant_id", sql)
                    if with_zone:
                        first = (employee_id, BIZ_DATE, "白班", None, "t0", "t0")
                        second = (employee_id, BIZ_DATE, "夜班", None, "t1", "t1")
                    else:
                        first = (employee_id, BIZ_DATE, "白班", "t0", "t0")
                        second = (employee_id, BIZ_DATE, "夜班", "t1", "t1")
                    await conn.execute(sql, first)
                    await conn.execute(sql, second)
            cur = await conn.execute(
                "SELECT shift FROM hygiene_shift_picks "
                "WHERE employee_id = ? AND business_date = ?",
                (employee_id, BIZ_DATE),
            )
            self.assertEqual([r[0] for r in await cur.fetchall()], ["夜班"])
        finally:
            # 哨兵数据不落库
            await conn.rollback()
            await conn.close()


if __name__ == "__main__":
    unittest.main()
