#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite → PostgreSQL 方言转换规则。

规则全集见 db_core/backend/dialect.py 的模块文档。新增方言特性必须同时在这里
补测试——PG 后端遇到未登记的方言不会报错，而是静默给出错误结果。
"""

import unittest

from db_core.backend.dialect import translate


class PlaceholderTest(unittest.TestCase):
    def test_sequential_numbering(self):
        self.assertEqual(
            translate("SELECT * FROM orders WHERE station = ? AND dish = ?"),
            "SELECT * FROM orders WHERE station = $1 AND dish = $2",
        )

    def test_no_placeholders(self):
        sql = "SELECT count(*) FROM orders"
        self.assertEqual(translate(sql), sql)

    def test_question_mark_inside_string_literal_is_kept(self):
        """字面量里的 ? 不是占位符，不能被编号。"""
        self.assertEqual(
            translate("SELECT * FROM t WHERE note = '?' AND id = ?"),
            "SELECT * FROM t WHERE note = '?' AND id = $1",
        )

    def test_escaped_single_quote(self):
        """SQL 的 '' 转义不能把后面的 ? 误判进字面量。"""
        self.assertEqual(
            translate("SELECT * FROM t WHERE note = 'it''s ?' AND id = ?"),
            "SELECT * FROM t WHERE note = 'it''s ?' AND id = $1",
        )

    def test_double_quoted_identifier(self):
        self.assertEqual(
            translate('SELECT "weird?col" FROM t WHERE id = ?'),
            'SELECT "weird?col" FROM t WHERE id = $1',
        )


class FunctionTest(unittest.TestCase):
    def test_ifnull_to_coalesce(self):
        self.assertEqual(
            translate("SELECT IFNULL(source, '') FROM orders"),
            "SELECT COALESCE(source, '') FROM orders",
        )

    def test_ifnull_lowercase(self):
        self.assertEqual(
            translate("select ifnull(a, b) from t"),
            "select COALESCE(a, b) from t",
        )

    def test_strftime_hour(self):
        self.assertEqual(
            translate("CAST(strftime('%H', order_time) AS INTEGER)"),
            "CAST(EXTRACT(HOUR FROM (order_time)::timestamptz)::int AS INTEGER)",
        )

    def test_strftime_month(self):
        self.assertEqual(
            translate("strftime('%Y-%m', order_time)"),
            "to_char((order_time)::timestamptz, 'YYYY-MM')",
        )

    def test_strftime_week_uses_iso_week(self):
        """%W（SQLite 周）与 IW（ISO 周）跨年语义不同，此处只锁代码形态。"""
        self.assertEqual(
            translate("strftime('%Y-W%W', order_time)"),
            "to_char((order_time)::timestamptz, 'IYYY\"W\"IW')",
        )


class CollateTest(unittest.TestCase):
    def test_collate_nocase_becomes_lower(self):
        self.assertEqual(
            translate("SELECT slug FROM s ORDER BY s.slug COLLATE NOCASE"),
            "SELECT slug FROM s ORDER BY lower(s.slug)",
        )


class RowidTest(unittest.TestCase):
    def test_rowid_becomes_id(self):
        self.assertEqual(
            translate("SELECT rowid, dish_name FROM orders"),
            "SELECT id, dish_name FROM orders",
        )

    def test_order_by_rowid(self):
        self.assertEqual(
            translate("SELECT DISTINCT dish_name FROM orders ORDER BY rowid DESC LIMIT ?"),
            "SELECT DISTINCT dish_name FROM orders ORDER BY id DESC LIMIT $1",
        )

    def test_rowid_uppercase(self):
        self.assertEqual(translate("WHERE ROWID = ?"), "WHERE id = $1")


class InsertOrIgnoreTest(unittest.TestCase):
    def test_appends_on_conflict_do_nothing(self):
        self.assertEqual(
            translate(
                "INSERT OR IGNORE INTO report_dishes (dish_name, display_order) "
                "VALUES (?, ?)"
            ),
            "INSERT INTO report_dishes (dish_name, display_order) "
            "VALUES ($1, $2) ON CONFLICT DO NOTHING",
        )

    def test_trailing_semicolon_is_not_duplicated(self):
        self.assertEqual(
            translate("INSERT OR IGNORE INTO t (a) VALUES (?);"),
            "INSERT INTO t (a) VALUES ($1) ON CONFLICT DO NOTHING",
        )

    def test_existing_on_conflict_is_left_alone(self):
        sql = "INSERT OR IGNORE INTO t (a) VALUES (?) ON CONFLICT (a) DO NOTHING"
        self.assertEqual(
            translate(sql),
            "INSERT INTO t (a) VALUES ($1) ON CONFLICT (a) DO NOTHING",
        )


class CombinedTest(unittest.TestCase):
    def test_realistic_orders_query(self):
        """贴近 db_core/orders_repo.py 的真实形态。"""
        sql = (
            "SELECT id, business_flow_id FROM orders "
            "WHERE station = ? AND IFNULL(source, '') != 'delivery' "
            "AND order_time >= ? ORDER BY rowid DESC LIMIT ?"
        )
        self.assertEqual(
            translate(sql),
            "SELECT id, business_flow_id FROM orders "
            "WHERE station = $1 AND COALESCE(source, '') != 'delivery' "
            "AND order_time >= $2 ORDER BY id DESC LIMIT $3",
        )


if __name__ == "__main__":
    unittest.main()
