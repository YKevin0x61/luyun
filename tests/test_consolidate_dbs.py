#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scripts/archive/consolidate_dbs.py`（多库 → 单库迁移脚本）的测试。

覆盖：
- 核心业务表（orders）+ auth 表 + 配方表均能正确迁移到 app.db。
- 旧表列少于目标表列时（老 schema 缺列），按列名交集写入，缺失列走目标默认值。
- 幂等：对已迁移过数据的表重跑，行数不变、不产生重复，且会先备份原 app.db。
- 空环境（没有任何旧库文件）：无操作，不报错。
- dry-run：只报告，不创建/修改任何文件。
- 源表与目标表没有同名列时优雅失败（不会中断其它表的迁移）。
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.archive import consolidate_dbs as cdb  # noqa: E402


def _seed_sqlite(path: str, script: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(script)
        conn.commit()
    finally:
        conn.close()


def _read_all(app_db_path: str, table: str):
    conn = sqlite3.connect(app_db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(f"SELECT * FROM {table} ORDER BY 1")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


class ConsolidateDbsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self._tmpdir.name, "data")
        os.makedirs(self.data_dir)
        self.app_db_path = os.path.join(self.data_dir, "app.db")

    async def asyncTearDown(self):
        self._tmpdir.cleanup()

    def _seed_old_orders_db(self, with_kds_columns: bool = True):
        """构造旧的 data/orders.db。with_kds_columns=False 模拟更早期缺
        dish_status/ready_time 列的老 schema，用于验证列交集兜底逻辑。"""
        extra_cols = ""
        extra_vals = ""
        extra_vals_params = ()
        if with_kds_columns:
            extra_cols = ", dish_status TEXT DEFAULT '待出餐', ready_time TEXT"

        path = os.path.join(self.data_dir, "orders.db")
        _seed_sqlite(
            path,
            f"""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_flow_id TEXT,
                table_number TEXT NOT NULL,
                dish_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                order_time TEXT NOT NULL,
                price REAL DEFAULT 0.0,
                total_amount REAL DEFAULT 0.0,
                status TEXT DEFAULT '未结',
                category TEXT DEFAULT '',
                station TEXT DEFAULT '',
                priority TEXT DEFAULT 'normal',
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
                {extra_cols}
            );
            INSERT INTO orders
                (business_flow_id, table_number, dish_name, quantity, order_time,
                 price, total_amount, status, category, station, priority,
                 created_at, updated_at)
            VALUES
                ('old-001', '5', '虾饺', 2, '2026-01-01 10:00:00',
                 12.0, 24.0, '已结', '点心', 'shulong', 'normal',
                 '2026-01-01T10:00:00+08:00', '2026-01-01T10:00:00+08:00'),
                ('old-002', '6', '烧卖', 1, '2026-01-01 10:05:00',
                 10.0, 10.0, '未结', '点心', 'shulong', 'normal',
                 '2026-01-01T10:05:00+08:00', '2026-01-01T10:05:00+08:00');
            """,
        )
        return path

    def _seed_old_auth_db(self):
        path = os.path.join(self.data_dir, "auth.db")
        _seed_sqlite(
            path,
            """
            CREATE TABLE admin_user (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO admin_user VALUES
                (1, 'admin', 'bcrypt$fakehash', '2026-01-01T00:00:00', '2026-01-01T00:00:00');
            """,
        )
        return path

    def _seed_old_recipes_db(self):
        path = os.path.join(self.data_dir, "recipes.db")
        _seed_sqlite(
            path,
            """
            CREATE TABLE sop_stations (
                slug TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE sop_recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_slug TEXT NOT NULL, section TEXT NOT NULL, recipe_name TEXT NOT NULL,
                body_markdown TEXT NOT NULL, sort_order INTEGER NOT NULL,
                is_new INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            INSERT INTO sop_stations VALUES ('changfen', '肠粉档', '2026-01-01T00:00:00');
            INSERT INTO sop_recipes
                (station_slug, section, recipe_name, body_markdown, sort_order, is_new, is_active, updated_at)
            VALUES
                ('changfen', '配方', '肠粉酱油', '酱油：100g', 0, 0, 1, '2026-01-01T00:00:00');
            """,
        )
        return path

    # ---- 基本迁移正确性 ----

    async def test_migrates_core_auth_recipes_tables_correctly(self):
        self._seed_old_orders_db()
        self._seed_old_auth_db()
        self._seed_old_recipes_db()

        report = await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=False)

        self.assertFalse(report.has_failures)
        self.assertTrue(os.path.exists(self.app_db_path))

        orders_rows = _read_all(self.app_db_path, "orders")
        self.assertEqual(len(orders_rows), 2)
        by_flow = {r["business_flow_id"]: r for r in orders_rows}
        self.assertEqual(by_flow["old-001"]["dish_name"], "虾饺")
        self.assertEqual(by_flow["old-001"]["quantity"], 2)
        self.assertEqual(by_flow["old-002"]["dish_name"], "烧卖")

        admin_rows = _read_all(self.app_db_path, "admin_user")
        self.assertEqual(len(admin_rows), 1)
        self.assertEqual(admin_rows[0]["username"], "admin")

        recipe_rows = _read_all(self.app_db_path, "sop_recipes")
        self.assertEqual(len(recipe_rows), 1)
        self.assertEqual(recipe_rows[0]["recipe_name"], "肠粉酱油")
        station_rows = _read_all(self.app_db_path, "sop_stations")
        self.assertEqual(len(station_rows), 1)
        self.assertEqual(station_rows[0]["slug"], "changfen")

        # sessions/api_tokens/sop_recipes_history 源库中不存在该表 → 应标记为跳过而非失败
        by_table = {r.table: r for r in report.results}
        self.assertEqual(by_table["sessions"].status, "skipped_no_table")
        self.assertEqual(by_table["api_tokens"].status, "skipped_no_table")
        self.assertEqual(by_table["sop_recipes_history"].status, "skipped_no_table")

    async def test_missing_new_columns_fallback_to_target_defaults(self):
        """旧 orders.db 缺 dish_status/ready_time 列时，迁移后这些列走目标表默认值。"""
        self._seed_old_orders_db(with_kds_columns=False)

        report = await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=False)
        self.assertFalse(report.has_failures)

        rows = _read_all(self.app_db_path, "orders")
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["dish_status"], "待出餐")
            self.assertIsNone(row["ready_time"])

    async def test_all_ALL_TABLES_and_recipe_tables_are_covered_by_specs(self):
        """迁移清单必须覆盖 db_core.schema.ALL_TABLES 的每张真实表 + 配方三表，且不包含 logs。"""
        specs = cdb.build_migration_specs()
        table_names = {s.table for s in specs}

        self.assertIn("orders", table_names)
        self.assertIn("dish_stations", table_names)
        self.assertIn("admin_user", table_names)
        self.assertIn("sessions", table_names)
        self.assertIn("api_tokens", table_names)
        self.assertIn("sop_stations", table_names)
        self.assertIn("sop_recipes", table_names)
        self.assertIn("sop_recipes_history", table_names)
        self.assertNotIn("logs", table_names)
        self.assertNotIn("auth", table_names)  # 'auth' 只是分组键，真实表是三张子表

    # ---- 幂等 / 可重跑 ----

    async def test_rerun_is_idempotent_no_duplicates_and_backs_up_app_db(self):
        self._seed_old_orders_db()
        self._seed_old_auth_db()

        first = await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=False)
        self.assertFalse(first.has_failures)
        self.assertIsNone(first.backup_path)  # 第一次迁移时 app.db 还不存在，无需备份
        first_orders = _read_all(self.app_db_path, "orders")
        self.assertEqual(len(first_orders), 2)

        second = await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=False)
        self.assertFalse(second.has_failures)
        self.assertIsNotNone(second.backup_path)
        self.assertTrue(os.path.exists(second.backup_path))
        self.assertEqual(second.total_migrated_rows, 0)

        second_orders = _read_all(self.app_db_path, "orders")
        self.assertEqual(len(second_orders), 2)
        self.assertEqual(
            sorted(r["business_flow_id"] for r in second_orders),
            sorted(r["business_flow_id"] for r in first_orders),
        )

        by_table = {r.table: r for r in second.results}
        self.assertEqual(by_table["orders"].status, "skipped_target_has_data")
        self.assertEqual(by_table["admin_user"].status, "skipped_target_has_data")

        # 备份文件里保留着迁移后的第一版数据，而不是空库
        backup_orders = _read_all(second.backup_path, "orders")
        self.assertEqual(len(backup_orders), 2)

    async def test_rerun_three_times_still_stable(self):
        self._seed_old_orders_db()
        await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=False)
        await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=False)
        third = await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=False)

        self.assertFalse(third.has_failures)
        rows = _read_all(self.app_db_path, "orders")
        self.assertEqual(len(rows), 2)

    # ---- 空环境 ----

    async def test_empty_environment_is_a_no_op(self):
        """没有任何旧库文件：迁移全部跳过，不报错，不产生数据。"""
        report = await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=False)

        self.assertFalse(report.has_failures)
        self.assertEqual(report.total_migrated_rows, 0)
        self.assertTrue(all(r.status == "skipped_no_source" for r in report.results))

    async def test_empty_environment_dry_run_touches_nothing_on_disk(self):
        report = await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=True)

        self.assertFalse(report.has_failures)
        self.assertEqual(report.total_migrated_rows, 0)
        self.assertFalse(os.path.exists(self.app_db_path), "dry-run 不应创建 app.db")
        self.assertIsNone(report.backup_path)

    # ---- dry-run 只报告不写入 ----

    async def test_dry_run_reports_would_migrate_without_writing(self):
        self._seed_old_orders_db()

        report = await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=True)

        self.assertFalse(report.has_failures)
        self.assertFalse(os.path.exists(self.app_db_path), "dry-run 不应创建/写入 app.db")
        by_table = {r.table: r for r in report.results}
        self.assertEqual(by_table["orders"].status, "would_migrate")
        self.assertEqual(by_table["orders"].source_rows, 2)
        self.assertEqual(by_table["orders"].migrated_rows, 0)

    async def test_dry_run_never_modifies_source_files(self):
        orders_path = self._seed_old_orders_db()
        before = open(orders_path, "rb").read()

        await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=True)

        after = open(orders_path, "rb").read()
        self.assertEqual(before, after, "dry-run 不应改动旧库文件内容")

    async def test_real_run_never_modifies_source_files(self):
        orders_path = self._seed_old_orders_db()
        before = open(orders_path, "rb").read()

        await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=False)

        after = open(orders_path, "rb").read()
        self.assertEqual(before, after, "正式迁移也不应改动旧库文件内容（只读旧库）")

    # ---- 容错：源表与目标表没有同名列 ----

    async def test_incompatible_columns_marked_failed_without_aborting_others(self):
        path = os.path.join(self.data_dir, "stations.db")
        _seed_sqlite(
            path,
            """
            CREATE TABLE stations (totally_unrelated_column TEXT);
            INSERT INTO stations VALUES ('nope');
            """,
        )
        self._seed_old_orders_db()

        report = await cdb.consolidate(self.data_dir, self.app_db_path, dry_run=False)

        by_table = {r.table: r for r in report.results}
        self.assertEqual(by_table["stations"].status, "failed")
        self.assertTrue(report.has_failures)
        # 其它表不受影响，仍能正常迁移
        self.assertEqual(by_table["orders"].status, "migrated")
        orders_rows = _read_all(self.app_db_path, "orders")
        self.assertEqual(len(orders_rows), 2)


if __name__ == "__main__":
    unittest.main()
