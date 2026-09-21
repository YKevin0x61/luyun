#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨后端导出成 SQLite 的逻辑（``db_core/backend/sqlite_export.py``）。

PG 没有 SQLite 的页级 backup API，整库导出只能按表重建。这段逻辑同时被
``PgConnection.backup``（后台「导出 DB」）与备份服务的配方导出使用，所以用假连接
做单元测试——CI 里没有 PostgreSQL，真实 PG 的端到端验证在 tests/test_pg_backend.py。
"""

import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

import aiosqlite

from db_core.backend.sqlite_export import (
    EXPORT_BATCH_ROWS,
    export_tables_to_sqlite,
    quote_ident,
    sqlite_bindable,
    sqlite_column_type,
)

try:  # asyncpg 是 PG 后端的依赖；缺失时只跳过契约用例
    from db_core.backend import pg as pg_backend
except ImportError:  # pragma: no cover - 未装 asyncpg 的环境
    pg_backend = None  # type: ignore[assignment]


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return list(self._rows)


class _FakeConn:
    """只实现导出用到的两个接口：``PRAGMA table_info`` 与分页 ``SELECT``。

    分页用切片模拟服务端行为，所以「有没有丢行/重复行」能真的被测出来。
    """

    def __init__(self, schema, rows=None):
        self.schema = schema          # {表名: [(列名, 类型, 主键标志)]}
        self.rows = rows or {}        # {表名: [行元组]}
        self.selects = []             # [(完整 SQL, limit, offset)]，供分页断言

    async def execute(self, sql, params=()):
        text = sql.strip()
        if text.upper().startswith("PRAGMA"):
            table = text[text.index("(") + 1:text.rindex(")")]
            # 展开成 PRAGMA table_info 的原始六列（cid, name, type, notnull,
            # dflt_value, pk），调用方按位置取值。
            rows = [
                (index, name, type_, 0, None, pk)
                for index, (name, type_, pk) in enumerate(self.schema.get(table, []))
            ]
            return _FakeCursor(rows)

        table = text.split(" FROM ", 1)[1].split(" ", 1)[0].strip('"')
        limit, offset = params
        self.selects.append((text, limit, offset))
        return _FakeCursor(self.rows.get(table, [])[offset:offset + limit])


class _RowKeyConn(_FakeConn):
    """带 ``row_key_column`` 的连接（PgConnection 的形态）。"""

    def __init__(self, schema, rows=None, key="id"):
        super().__init__(schema, rows)
        self.key = key
        self.key_calls = 0

    async def row_key_column(self, table):
        self.key_calls += 1
        return self.key


class ExportTablesToSqliteTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    async def _dst(self, name: str = "export.sqlite"):
        conn = await aiosqlite.connect(os.path.join(self._tmpdir.name, name))
        conn.row_factory = aiosqlite.Row
        self.addAsyncCleanup(conn.close)
        return conn

    async def _tables(self, db) -> set:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in await cur.fetchall()}

    async def test_exports_schema_and_rows(self):
        conn = _FakeConn(
            {"probe": [("id", "bigint", 1), ("name", "text", 0), ("qty", "integer", 0)]},
            {"probe": [(1, "甲", 2), (2, "乙", 3)]},
        )
        dst = await self._dst()

        exported = await export_tables_to_sqlite(conn, ["probe"], dst)

        self.assertEqual(exported, ["probe"])
        cur = await dst.execute("PRAGMA table_info(probe)")
        columns = {row["name"]: row["type"] for row in await cur.fetchall()}
        self.assertEqual(columns, {"id": "INTEGER", "name": "TEXT", "qty": "INTEGER"})
        cur = await dst.execute("SELECT id, name, qty FROM probe ORDER BY id")
        self.assertEqual(
            [tuple(row) for row in await cur.fetchall()],
            [(1, "甲", 2), (2, "乙", 3)],
        )

    async def test_pages_through_multiple_batches_without_losing_rows(self):
        total = EXPORT_BATCH_ROWS + 1
        rows = [(i, f"n{i}") for i in range(total)]
        conn = _FakeConn(
            {"big": [("id", "bigint", 1), ("name", "text", 0)]},
            {"big": rows},
        )
        dst = await self._dst()

        await export_tables_to_sqlite(conn, ["big"], dst)

        cur = await dst.execute("SELECT count(*) FROM big")
        self.assertEqual((await cur.fetchone())[0], total)
        # 第二批从第一批的末尾接着取，不能原地重取
        self.assertEqual([call[2] for call in conn.selects], [0, EXPORT_BATCH_ROWS])
        self.assertEqual([call[1] for call in conn.selects], [EXPORT_BATCH_ROWS] * 2)

    async def test_paging_is_ordered_by_row_identifier(self):
        """分页必须带 ORDER BY：无顺序的 OFFSET 窗口会随行的物理位置漂移而重复取行。

        实测 orders（持续被 UPDATE，HOT 更新会换物理位置）：第 10 批与前面批重复
        1039 行，导出的 sqlite 直接撞主键冲突。
        """
        conn = _FakeConn(
            {"probe": [("id", "bigint", 1), ("name", "text", 0)]},
            {"probe": [(1, "甲")]},
        )
        dst = await self._dst()

        await export_tables_to_sqlite(conn, ["probe"], dst)

        self.assertIn('ORDER BY "id"', conn.selects[0][0])

    async def test_paging_prefers_backend_row_key_column(self):
        """PG 连接自带行标识列解析（主键第一列未必叫 id，如 dish_stations.dish_name）。"""
        conn = _RowKeyConn(
            {"probe": [("code", "text", 1), ("val", "text", 0)]},
            {"probe": [("a", "1")]},
            key="code",
        )
        dst = await self._dst()

        await export_tables_to_sqlite(conn, ["probe"], dst)

        self.assertIn('ORDER BY "code"', conn.selects[0][0])
        self.assertEqual(conn.key_calls, 1)

    async def test_skips_table_absent_from_source(self):
        """源库没有的表不建空壳，否则导入侧会看到源库根本不存在的表。"""
        conn = _FakeConn({"probe": [("id", "bigint", 1)]}, {"probe": [(1,)]})
        dst = await self._dst()

        exported = await export_tables_to_sqlite(conn, ["missing", "probe"], dst)

        self.assertEqual(exported, ["probe"])
        self.assertEqual(await self._tables(dst), {"probe"})

    async def test_empty_table_is_still_created(self):
        conn = _FakeConn({"probe": [("id", "bigint", 1)]}, {"probe": []})
        dst = await self._dst()

        exported = await export_tables_to_sqlite(conn, ["probe"], dst)

        self.assertEqual(exported, ["probe"])
        cur = await dst.execute("SELECT count(*) FROM probe")
        self.assertEqual((await cur.fetchone())[0], 0)

    async def test_single_column_primary_key_is_restored(self):
        conn = _FakeConn(
            {"probe": [("code", "text", 1), ("val", "text", 0)]},
            {"probe": [("a", "1")]},
        )
        dst = await self._dst()

        await export_tables_to_sqlite(conn, ["probe"], dst)

        cur = await dst.execute("PRAGMA table_info(probe)")
        keys = {row["name"]: row["pk"] for row in await cur.fetchall()}
        self.assertEqual(keys, {"code": 1, "val": 0})

    async def test_composite_primary_key_is_declared_without_pk(self):
        """复合主键不在导出件里声明——声明错会让整个成员不可回灌，导入侧按业务唯一键去重。"""
        conn = _FakeConn(
            {"probe": [("a", "text", 1), ("b", "text", 1)]},
            {"probe": [("x", "y")]},
        )
        dst = await self._dst()

        await export_tables_to_sqlite(conn, ["probe"], dst)

        cur = await dst.execute("PRAGMA table_info(probe)")
        self.assertEqual({row["pk"] for row in await cur.fetchall()}, {0})

    async def test_reserved_word_columns_survive_quoting(self):
        """orders 的 status/source 是 SQLite 保留字，不加引号建表就语法错。"""
        conn = _FakeConn(
            {"orders": [("id", "bigint", 1), ("status", "text", 0), ("source", "text", 0)]},
            {"orders": [(1, "未结", "pos")]},
        )
        dst = await self._dst()

        await export_tables_to_sqlite(conn, ["orders"], dst)

        cur = await dst.execute('SELECT "status", "source" FROM orders')
        self.assertEqual(tuple((await cur.fetchall())[0]), ("未结", "pos"))

    async def test_pg_typed_values_land_in_sqlite(self):
        """PG 侧取回的是 datetime/Decimal/bool 对象，直接绑给 sqlite3 会 InterfaceError。"""
        conn = _FakeConn(
            {
                "probe": [
                    ("amount", "numeric", 0),
                    ("at", "timestamp with time zone", 0),
                    ("flag", "boolean", 0),
                    ("payload", "bytea", 0),
                ]
            },
            {
                "probe": [
                    (
                        Decimal("3.50"),
                        datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
                        True,
                        b"\x00\x01",
                    )
                ]
            },
        )
        dst = await self._dst()

        await export_tables_to_sqlite(conn, ["probe"], dst)

        cur = await dst.execute('SELECT "amount", "at", "flag", "payload" FROM probe')
        row = await cur.fetchone()
        self.assertEqual(row["amount"], 3.5)
        self.assertEqual(row["at"], "2026-01-02T03:04:05+00:00")
        self.assertEqual(str(row["flag"]), "1")
        self.assertEqual(row["payload"], b"\x00\x01")


class ColumnTypeMappingTest(unittest.TestCase):
    def test_pg_types_map_to_sqlite_storage_classes(self):
        cases = {
            "bigint": "INTEGER",
            "integer": "INTEGER",
            "smallint": "INTEGER",
            "double precision": "REAL",
            "numeric": "REAL",
            "real": "REAL",
            "bytea": "BLOB",
            "text": "TEXT",
            "timestamp with time zone": "TEXT",
            "boolean": "TEXT",
            "jsonb": "TEXT",
            "": "TEXT",
        }
        for sql_type, expected in cases.items():
            with self.subTest(sql_type=sql_type):
                self.assertEqual(sqlite_column_type(sql_type), expected)


class ValueCoercionTest(unittest.TestCase):
    def test_values_are_coerced_for_sqlite3(self):
        self.assertIsNone(sqlite_bindable(None))
        self.assertEqual(sqlite_bindable("x"), "x")
        self.assertEqual(sqlite_bindable(7), 7)
        self.assertEqual(sqlite_bindable(1.5), 1.5)
        self.assertEqual(sqlite_bindable(b"\x01"), b"\x01")

    def test_bool_becomes_int_not_python_bool(self):
        # bool 是 int 的子类，先判 int 的话会原样返回 True
        self.assertEqual(sqlite_bindable(True), 1)
        self.assertEqual(sqlite_bindable(False), 0)
        self.assertNotIsInstance(sqlite_bindable(True), bool)

    def test_datetime_and_decimal(self):
        self.assertEqual(sqlite_bindable(date(2026, 1, 2)), "2026-01-02")
        self.assertEqual(
            sqlite_bindable(datetime(2026, 1, 2, 3, 4, 5)), "2026-01-02T03:04:05"
        )
        self.assertEqual(sqlite_bindable(Decimal("7.0")), 7)
        self.assertEqual(sqlite_bindable(Decimal("7.5")), 7.5)

    def test_unknown_objects_fall_back_to_text(self):
        self.assertEqual(sqlite_bindable({"a": 1}), "{'a': 1}")


class QuoteIdentTest(unittest.TestCase):
    def test_quotes_and_escapes_double_quotes(self):
        self.assertEqual(quote_ident("orders"), '"orders"')
        self.assertEqual(quote_ident('we"ird'), '"we""ird"')


@unittest.skipIf(pg_backend is None, "asyncpg 不可用")
class PgBackupContractTest(unittest.TestCase):
    """``PgConnection`` 必须提供 ``backup``。

    缺了它 ``DatabaseManager.export_merged_sqlite_file``（后台「导出 DB」）在 PG
    后端下会直接 AttributeError → 500：SQLite 侧有 aiosqlite 的页级 backup API，
    PG 侧没有等价物，只能由适配层自己补一个。
    """

    def test_pg_connection_exposes_backup(self):
        self.assertTrue(callable(getattr(pg_backend.PgConnection, "backup", None)))

    def test_export_merged_sqlite_file_only_uses_backup(self):
        """导出路径只走 ``backup`` 这一个后端差异点，不要再冒出别的方法调用。"""
        import inspect

        from db_core.connection import _ConnectionMixin

        source = inspect.getsource(_ConnectionMixin.export_merged_sqlite_file)
        self.assertIn(".backup(", source)


if __name__ == "__main__":
    unittest.main()
