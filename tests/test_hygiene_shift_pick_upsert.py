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
import unittest
from unittest import mock

from config import settings
from db_core.backend import pg as pg_backend
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


@unittest.skipUnless(pg_available(), "PostgreSQL 不可用，跳过 PG 侧断言")
class ShiftPickUpsertPostgresTest(unittest.IsolatedAsyncioTestCase):
    async def test_conflict_target_matches_pg_unique_index(self):
        """PG 的唯一索引带 tenant_id，冲突目标必须带上它。"""
        conn = await pg_backend.connect()
        try:
            # hygiene_shift_picks.employee_id 有外键指向 hygiene_employees，而 conftest
            # 每例前 TRUNCATE 全部业务表（tenants 除外），hygiene_employees 恒为空——
            # 所以这里自己插一行哨兵员工，别去等一个永远不存在的 seed 行，否则用例
            # 在任何机器、任何 CI 上都只会跳过。
            #
            # 只给 NOT NULL 且无默认值的列：phone / password_hash / created_at /
            # updated_at（tenant_id 默认 1，指向 0001 seed 的默认门店行）。手机号复用
            # 同一个哨兵值，免得再引入一套命名；真实手机号是 1[3-9] 开头的 11 位，撞不上。
            #
            # 写语句会开显式事务（db_core/backend/pg.py 的 ensure_transaction），
            # finally 里的 rollback 把它和下面的哨兵 pick 一起撤掉，不落库。
            await conn.execute(
                "INSERT INTO hygiene_employees "
                "(id, phone, password_hash, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (EMP_ID, str(EMP_ID), "", BIZ_DATE, BIZ_DATE),
            )
            employee_id = EMP_ID

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
