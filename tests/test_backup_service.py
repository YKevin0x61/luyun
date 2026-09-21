#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统完整备份 v2 服务测试。"""

import io
import json
import os
import sqlite3
import stat
import struct
import tarfile
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import aiosqlite

from config import settings
from database import CHINA_TZ, DatabaseManager
from services import backup_service, credentials_store
from services.backup_service import (
    BACKUP_MAGIC,
    PHOTO_OTHER,
    PHOTO_STANDARD,
    SNAPSHOT_KEEP,
)
from services.credentials_store import CredentialBundle


def _sample_bundle() -> CredentialBundle:
    return CredentialBundle(
        phone="13800000000",
        password="s3cret-pw",
        shop_id="100001",
        company_id="200002",
        shop_name="LuckIn",
        delivery_shop_id="200002",
    )


class BackupServiceV2Test(unittest.TestCase):
    def setUp(self):
        self._saved_cache = credentials_store._cache
        credentials_store._cache = _sample_bundle()

    def tearDown(self):
        credentials_store._cache = self._saved_cache

    def test_round_trip_credentials_only(self):
        blob = backup_service.build_backup(
            "pass1234",
            include_runtime=False,
            runtime_data=None,
            include_app_db=False,
            app_db_bytes=None,
            include_recipes=False,
            recipes_db_bytes=None,
            app_version="0.1.0",
        )
        parsed = backup_service.parse_backup(blob, "pass1234")
        self.assertEqual(parsed["credentials"]["phone"], "13800000000")
        self.assertEqual(parsed["credentials"]["password"], "s3cret-pw")
        self.assertIsNone(parsed["runtime"])
        self.assertIsNone(parsed["app_db_bytes"])
        self.assertEqual(parsed["meta"]["version"], 3)

    def test_round_trip_with_runtime(self):
        runtime = {"work_start": "08:00", "work_end": "20:00"}
        blob = backup_service.build_backup(
            "pass1234",
            include_runtime=True,
            runtime_data=runtime,
            include_app_db=False,
            app_db_bytes=None,
            include_recipes=False,
            recipes_db_bytes=None,
            app_version="0.1.0",
        )
        parsed = backup_service.parse_backup(blob, "pass1234")
        self.assertEqual(parsed["runtime"], runtime)
        self.assertTrue(parsed["meta"]["includes"]["runtime"])

    def test_round_trip_with_app_db(self):
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(tmp_path)
            conn.execute(
                "CREATE TABLE orders (id INTEGER PRIMARY KEY, business_flow_id TEXT, dish_name TEXT)"
            )
            conn.execute(
                "INSERT INTO orders (business_flow_id, dish_name) VALUES (?, ?)",
                ("bf-1", "虾饺"),
            )
            conn.commit()
            conn.close()
            with open(tmp_path, "rb") as f:
                app_bytes = f.read()
        finally:
            os.unlink(tmp_path)

        blob = backup_service.build_backup(
            "pass1234",
            include_runtime=False,
            runtime_data=None,
            include_app_db=True,
            app_db_bytes=app_bytes,
            include_recipes=False,
            recipes_db_bytes=None,
            app_version="0.1.0",
        )
        parsed = backup_service.parse_backup(blob, "pass1234")
        self.assertIsNotNone(parsed["app_db_bytes"])
        self.assertTrue(parsed["meta"]["includes"]["app_db"])

    def test_wrong_passphrase_fails(self):
        blob = backup_service.build_backup(
            "pass1234",
            include_runtime=False,
            runtime_data=None,
            include_app_db=False,
            app_db_bytes=None,
            include_recipes=False,
            recipes_db_bytes=None,
            app_version="0.1.0",
        )
        with self.assertRaises(ValueError):
            backup_service.parse_backup(blob, "wrong-pass")

    def test_tampered_token_fails(self):
        blob = bytearray(
            backup_service.build_backup(
                "pass1234",
                include_runtime=False,
                runtime_data=None,
                include_app_db=False,
                app_db_bytes=None,
                include_recipes=False,
                recipes_db_bytes=None,
                app_version="0.1.0",
            )
        )
        blob[-1] ^= 0xFF
        with self.assertRaises(ValueError):
            backup_service.parse_backup(bytes(blob), "pass1234")

    def test_tampered_member_sha256_fails(self):
        blob = backup_service.build_backup(
            "pass1234",
            include_runtime=False,
            runtime_data=None,
            include_app_db=False,
            app_db_bytes=None,
            include_recipes=False,
            recipes_db_bytes=None,
            app_version="0.1.0",
        )
        offset = len(BACKUP_MAGIC) + 4 + struct.unpack(
            ">I", blob[len(BACKUP_MAGIC) : len(BACKUP_MAGIC) + 4]
        )[0]
        token = bytearray(blob[offset:])
        token[10] ^= 0xAA
        tampered = blob[:offset] + bytes(token)
        with self.assertRaises(ValueError):
            backup_service.parse_backup(tampered, "pass1234")

    def test_invalid_magic_fails(self):
        with self.assertRaises(ValueError):
            backup_service.parse_backup(b"NOT-A-BACKUP", "pass1234")

    def test_short_passphrase_rejected(self):
        with self.assertRaises(ValueError):
            backup_service.build_backup(
                "123",
                include_runtime=False,
                runtime_data=None,
                include_app_db=False,
                app_db_bytes=None,
                include_recipes=False,
                recipes_db_bytes=None,
                app_version="0.1.0",
            )

    def test_export_without_credentials_rejected(self):
        with mock.patch.object(credentials_store, "get_credentials", return_value=None):
            with self.assertRaises(ValueError):
                backup_service.build_backup(
                    "pass1234",
                    include_runtime=False,
                    runtime_data=None,
                    include_app_db=False,
                    app_db_bytes=None,
                    include_recipes=False,
                    recipes_db_bytes=None,
                    app_version="0.1.0",
                )


class OverwriteAppDbTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name

        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _build_source_db_bytes(self) -> bytes:
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(tmp_path)
        conn.executescript(
            """
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
                dish_status TEXT DEFAULT '待出餐',
                ready_time TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        now = datetime(2026, 5, 1, 10, 0, tzinfo=CHINA_TZ).isoformat()
        conn.execute(
            """
            INSERT INTO orders (
                business_flow_id, table_number, dish_name, quantity,
                order_time, price, total_amount, status, category, station,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ow-001", "1", "虾饺", 2, now, 12.0, 24.0, "未结", "点心", "点心", now, now),
        )
        conn.commit()
        conn.close()
        with open(tmp_path, "rb") as f:
            data = f.read()
        os.unlink(tmp_path)
        return data

    async def test_overwrite_app_db_from_bytes(self):
        source_bytes = await self._build_source_db_bytes()
        await backup_service.overwrite_app_db_from_bytes(self.db, source_bytes)

        async with self.db._conn.execute("SELECT COUNT(*) FROM orders") as cur:
            count = (await cur.fetchone())[0]
        self.assertEqual(count, 1)

        async with self.db._conn.execute(
            "SELECT business_flow_id, dish_name FROM orders"
        ) as cur:
            row = await cur.fetchone()
        self.assertEqual(row[0], "ow-001")
        self.assertEqual(row[1], "虾饺")


class MergeAppDbFailureReportTest(unittest.IsolatedAsyncioTestCase):
    """合并导入的逐表报告：失败行必须计数并带出样本，不能静默报 OK。"""

    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._old_backend = settings.DATABASE_BACKEND
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        # 合并是 SQLite 语义（临时库 + PRAGMA 去重）：固定后端，别被本机 .env 影响。
        settings.DATABASE_BACKEND = "sqlite"

        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        settings.DATABASE_BACKEND = self._old_backend
        self._tmpdir.cleanup()

    def _source_bytes(self, count: int = 2) -> bytes:
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(tmp_path)
        conn.executescript(
            """
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
                dish_status TEXT DEFAULT '待出餐',
                ready_time TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        now = datetime(2026, 5, 1, 10, 0, tzinfo=CHINA_TZ).isoformat()
        for i in range(count):
            conn.execute(
                """
                INSERT INTO orders (
                    business_flow_id, table_number, dish_name, quantity,
                    order_time, price, total_amount, status, category, station,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"mg-{i}", "1", "虾饺", 1, now, 1.0, 1.0, "未结", "点心", "点心", now, now),
            )
        conn.commit()
        conn.close()
        with open(tmp_path, "rb") as fh:
            data = fh.read()
        os.unlink(tmp_path)
        return data

    async def test_failed_rows_are_counted_with_samples(self):
        # 目标库上让 INSERT 必然失败，模拟约束冲突 / 类型不匹配 / 磁盘错误
        await self.db._conn.execute(
            "CREATE TRIGGER block_orders BEFORE INSERT ON orders "
            "BEGIN SELECT RAISE(ABORT, 'blocked by test'); END"
        )
        await self.db._conn.commit()

        report = await backup_service.merge_app_db_from_bytes(
            self.db, self._source_bytes(2)
        )

        self.assertEqual(report["total_imported"], 0)
        self.assertEqual(report["total_failed"], 2)
        orders = next(r for r in report["results"] if r["table"] == "orders")
        self.assertEqual(orders["status"], "PARTIAL")
        self.assertEqual(orders["failed"], 2)
        self.assertEqual(len(orders["errors"]), 2)
        self.assertIn("blocked by test", orders["errors"][0]["error"])
        self.assertEqual(orders["errors"][0]["key"], "mg-0")

    async def test_clean_merge_reports_ok_and_zero_failures(self):
        report = await backup_service.merge_app_db_from_bytes(
            self.db, self._source_bytes(2)
        )

        self.assertEqual(report["total_imported"], 2)
        self.assertEqual(report["total_failed"], 0)
        orders = next(r for r in report["results"] if r["table"] == "orders")
        self.assertEqual(orders["status"], "OK")
        self.assertEqual(orders["failed"], 0)
        self.assertEqual(orders["errors"], [])


class SqliteOverwriteImportTest(unittest.IsolatedAsyncioTestCase):
    """覆盖导入：写成一段脚本、一次执行，避免与采集写入共用连接时被插进事务。"""

    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._old_backend = settings.DATABASE_BACKEND
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        settings.DATABASE_BACKEND = "sqlite"

        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        settings.DATABASE_BACKEND = self._old_backend
        self._tmpdir.cleanup()

    def _source_bytes(self, rows: int = 1) -> bytes:
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(tmp_path)
        conn.executescript(
            """
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
                dish_status TEXT DEFAULT '待出餐',
                ready_time TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        now = datetime(2026, 5, 1, 10, 0, tzinfo=CHINA_TZ).isoformat()
        for i in range(rows):
            conn.execute(
                """
                INSERT INTO orders (
                    business_flow_id, table_number, dish_name, quantity,
                    order_time, price, total_amount, status, category, station,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"ow-{i}", "1", "虾饺", 1, now, 1.0, 1.0, "未结", "点心", "点心", now, now),
            )
        conn.commit()
        conn.close()
        with open(tmp_path, "rb") as fh:
            data = fh.read()
        os.unlink(tmp_path)
        return data

    async def _insert_local_order(self, flow_id: str) -> None:
        now = datetime(2026, 5, 1, 11, 0, tzinfo=CHINA_TZ).isoformat()
        await self.db._conn.execute(
            """
            INSERT INTO orders (
                business_flow_id, table_number, dish_name, quantity,
                order_time, created_at, updated_at
            ) VALUES (?, '2', '烧卖', 1, ?, ?, ?)
            """,
            (flow_id, now, now, now),
        )
        await self.db._conn.commit()

    async def test_overwrite_replaces_rows(self):
        await self._insert_local_order("local-1")
        await backup_service.overwrite_app_db_from_bytes(self.db, self._source_bytes(2))

        async with self.db._conn.execute(
            "SELECT business_flow_id FROM orders ORDER BY business_flow_id"
        ) as cur:
            flows = [r[0] for r in await cur.fetchall()]
        self.assertEqual(flows, ["ow-0", "ow-1"])

    async def test_overwrite_runs_as_a_single_script(self):
        """覆盖必须一次 executescript 跑完：中间不再让出事件循环给别的写入。"""
        scripts: List[str] = []
        original = self.db._conn.executescript

        async def spy(script):
            scripts.append(script)
            return await original(script)

        self.db._conn.executescript = spy
        await backup_service.overwrite_app_db_from_bytes(self.db, self._source_bytes(2))

        self.assertEqual(len(scripts), 1)
        script = scripts[0]
        self.assertIn("BEGIN IMMEDIATE;", script)
        self.assertIn("COMMIT;", script)
        self.assertIn("DELETE FROM main.orders;", script)
        self.assertIn("INSERT INTO main.orders (", script)
        self.assertIn("FROM src.orders;", script)

    async def test_failed_script_rolls_back_and_keeps_previous_rows(self):
        await self._insert_local_order("local-1")
        source = self._source_bytes(1)

        # 让脚本在插入阶段失败（目标库上的触发器），覆盖必须整体回滚
        await self.db._conn.execute(
            "CREATE TRIGGER block_orders BEFORE INSERT ON orders "
            "BEGIN SELECT RAISE(ABORT, 'blocked by test'); END"
        )
        await self.db._conn.commit()

        with self.assertRaises(Exception):
            await backup_service.overwrite_app_db_from_bytes(self.db, source)

        async with self.db._conn.execute("SELECT business_flow_id FROM orders") as cur:
            flows = [r[0] for r in await cur.fetchall()]
        # 原有的本地行还在：DELETE 与 INSERT 在同一事务里被一起回滚
        self.assertEqual(flows, ["local-1"])


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.app_db = os.path.join(self._tmpdir.name, "app.db")
        sqlite3.connect(self.app_db).close()

    def tearDown(self):
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def test_snapshot_keep_limit_and_list_order(self):
        cred_path = os.path.join(self._tmpdir.name, "credentials.enc")
        with open(cred_path, "wb") as f:
            f.write(b"test")

        timestamps = []
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=CHINA_TZ)
        for i in range(SNAPSHOT_KEEP + 2):
            fake_now = base.replace(second=i)
            with mock.patch(
                "services.backup_service.datetime",
            ) as mock_dt:
                mock_dt.now.return_value = fake_now
                mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                ts = backup_service.create_restore_snapshot(
                    self.app_db,
                    self.app_db,
                    cred_path,
                )
            timestamps.append(ts)

        root = backup_service._snapshot_root()
        remaining = [d.name for d in root.iterdir() if d.is_dir()]
        self.assertEqual(len(remaining), SNAPSHOT_KEEP)

        listed = backup_service.list_snapshots()
        self.assertEqual(len(listed), SNAPSHOT_KEEP)
        listed_ts = [item["ts"] for item in listed]
        self.assertEqual(listed_ts, sorted(listed_ts, reverse=True))
        self.assertNotIn(timestamps[0], listed_ts)
        self.assertNotIn(timestamps[1], listed_ts)

    def test_snapshot_copies_cred_key_beside_credentials(self):
        cred_path = os.path.join(self._tmpdir.name, "credentials.enc")
        key_path = os.path.join(self._tmpdir.name, ".cred_key")
        with open(cred_path, "wb") as f:
            f.write(b"enc-blob")
        with open(key_path, "wb") as f:
            f.write(b"fernet-key-material")
        os.chmod(key_path, 0o600)

        ts = backup_service.create_restore_snapshot(
            self.app_db,
            self.app_db,
            cred_path,
        )
        snap_dir = backup_service._snapshot_root() / ts
        copied_key = snap_dir / ".cred_key"
        self.assertTrue(copied_key.is_file())
        self.assertEqual(copied_key.read_bytes(), b"fernet-key-material")
        self.assertEqual(stat.S_IMODE(copied_key.stat().st_mode), 0o600)
        cred_path = os.path.join(self._tmpdir.name, "credentials.enc")
        with open(cred_path, "wb") as f:
            f.write(b"test")

        timestamps = []
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=CHINA_TZ)
        for i in range(SNAPSHOT_KEEP + 2):
            fake_now = base.replace(second=i)
            with mock.patch(
                "services.backup_service.datetime",
            ) as mock_dt:
                mock_dt.now.return_value = fake_now
                mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                ts = backup_service.create_restore_snapshot(
                    self.app_db,
                    self.app_db,
                    cred_path,
                )
            timestamps.append(ts)

        root = backup_service._snapshot_root()
        remaining = [d.name for d in root.iterdir() if d.is_dir()]
        self.assertEqual(len(remaining), SNAPSHOT_KEEP)

        listed = backup_service.list_snapshots()
        self.assertEqual(len(listed), SNAPSHOT_KEEP)
        listed_ts = [item["ts"] for item in listed]
        self.assertEqual(listed_ts, sorted(listed_ts, reverse=True))
        self.assertNotIn(timestamps[0], listed_ts)
        self.assertNotIn(timestamps[1], listed_ts)


class ExportRecipesDbBytesTest(unittest.TestCase):
    """配方表与业务表同库时，recipes 成员应只含 sop_* 表，不重复整库。"""

    def test_get_recipes_db_path_ignores_recipes_db_path_env(self):
        old = os.environ.get("RECIPES_DB_PATH")
        os.environ["RECIPES_DB_PATH"] = "/tmp/not-app.db"
        try:
            self.assertEqual(backup_service.get_recipes_db_path(), settings.APP_DB_PATH)
        finally:
            if old is None:
                os.environ.pop("RECIPES_DB_PATH", None)
            else:
                os.environ["RECIPES_DB_PATH"] = old

    def _make_db(self) -> str:
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(tmp_path)
        conn.executescript(
            """
            CREATE TABLE orders (id INTEGER PRIMARY KEY, dish_name TEXT);
            CREATE TABLE sop_stations (slug TEXT PRIMARY KEY, title TEXT, updated_at TEXT);
            CREATE TABLE sop_recipes (id INTEGER PRIMARY KEY, station_slug TEXT, recipe_name TEXT);
            """
        )
        # 业务表塞入较多行，用于验证 recipes 成员不含它们
        conn.executemany(
            "INSERT INTO orders (dish_name) VALUES (?)",
            [(f"dish-{i}",) for i in range(200)],
        )
        conn.execute(
            "INSERT INTO sop_stations (slug, title, updated_at) VALUES (?, ?, ?)",
            ("shulong", "熟笼档", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO sop_recipes (station_slug, recipe_name) VALUES (?, ?)",
            ("shulong", "虾饺"),
        )
        conn.commit()
        conn.close()
        return tmp_path

    def test_recipes_export_contains_only_recipe_tables(self):
        src_path = self._make_db()
        try:
            data = backup_service.export_recipes_db_bytes(src_path)
        finally:
            os.unlink(src_path)

        self.assertIsNotNone(data)

        fd, out_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with open(out_path, "wb") as f:
                f.write(data)
            conn = sqlite3.connect(out_path)
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            # 只应包含配方表，不含业务表 orders
            self.assertIn("sop_stations", tables)
            self.assertIn("sop_recipes", tables)
            self.assertNotIn("orders", tables)
            station_count = conn.execute("SELECT COUNT(*) FROM sop_stations").fetchone()[0]
            recipe_count = conn.execute("SELECT COUNT(*) FROM sop_recipes").fetchone()[0]
            conn.close()
        finally:
            os.unlink(out_path)

        self.assertEqual(station_count, 1)
        self.assertEqual(recipe_count, 1)

    def test_returns_none_without_recipe_tables(self):
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(tmp_path)
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        try:
            self.assertIsNone(backup_service.export_recipes_db_bytes(tmp_path))
        finally:
            os.unlink(tmp_path)

    def test_returns_none_when_file_missing(self):
        self.assertIsNone(
            backup_service.export_recipes_db_bytes("/no/such/path/x.db")
        )


class ExportRecipesDbBytesFromConnTest(unittest.IsolatedAsyncioTestCase):
    """配方成员必须取自「当前连接」，而不是磁盘上那份可能已分叉的 SQLite 副本。"""

    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._old_backend = settings.DATABASE_BACKEND
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        settings.DATABASE_BACKEND = "sqlite"

        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        settings.DATABASE_BACKEND = self._old_backend
        self._tmpdir.cleanup()

    async def _seed(self) -> None:
        now = datetime(2026, 5, 1, 10, 0, tzinfo=CHINA_TZ).isoformat()
        await self.db._conn.execute(
            "INSERT INTO sop_stations (slug, title, updated_at) VALUES (?, ?, ?)",
            ("shulong", "熟笼档", now),
        )
        await self.db._conn.execute(
            "INSERT INTO sop_recipes (station_slug, section, recipe_name, "
            "body_markdown, sort_order, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("shulong", "点心", "虾饺", "# 虾饺", 1, now),
        )
        await self.db._conn.commit()

    def _inspect(self, data: bytes) -> dict:
        fd, out_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with open(out_path, "wb") as handle:
                handle.write(data)
            conn = sqlite3.connect(out_path)
            try:
                tables = [
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                ]
                counts = {
                    table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    for table in tables
                }
                columns = {
                    table: [
                        r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')
                    ]
                    for table in tables
                }
                return {"tables": tables, "counts": counts, "columns": columns}
            finally:
                conn.close()
        finally:
            os.unlink(out_path)

    async def test_exports_only_recipe_tables_from_connection(self):
        await self._seed()
        data = await backup_service.export_recipes_db_bytes_from_conn(self.db._conn)
        self.assertIsNotNone(data)

        info = self._inspect(data)
        self.assertIn("sop_stations", info["tables"])
        self.assertIn("sop_recipes", info["tables"])
        self.assertNotIn("orders", info["tables"])
        self.assertEqual(info["counts"]["sop_stations"], 1)
        self.assertEqual(info["counts"]["sop_recipes"], 1)
        # id 列要跟着走：覆盖恢复按列交集回灌，丢了 id 就换了主键。
        self.assertIn("id", info["columns"]["sop_recipes"])

    async def test_returns_none_when_connection_has_no_recipe_tables(self):
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = await aiosqlite.connect(tmp_path)
        try:
            await conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY)")
            await conn.commit()
            self.assertIsNone(
                await backup_service.export_recipes_db_bytes_from_conn(conn)
            )
        finally:
            await conn.close()
            os.unlink(tmp_path)

    async def test_returns_none_without_connection(self):
        self.assertIsNone(
            await backup_service.export_recipes_db_bytes_from_conn(None)
        )


class PostgresOverwriteRecipesTest(unittest.IsolatedAsyncioTestCase):
    """PG 覆盖恢复配方：没有 ATTACH，走逐表 DELETE + INSERT，失败必须整体回滚。"""

    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._old_backend = settings.DATABASE_BACKEND
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        settings.DATABASE_BACKEND = "sqlite"

        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        # RecipeStore 的最小替身：这条路径只用到 .conn
        self.store = SimpleNamespace(conn=self.db._conn)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        settings.DATABASE_BACKEND = self._old_backend
        self._tmpdir.cleanup()

    async def _seed_local(self) -> None:
        now = datetime(2026, 5, 1, 9, 0, tzinfo=CHINA_TZ).isoformat()
        await self.db._conn.execute(
            "INSERT INTO sop_stations (slug, title, updated_at) VALUES (?, ?, ?)",
            ("old-station", "旧档口", now),
        )
        await self.db._conn.execute(
            "INSERT INTO sop_recipes (station_slug, section, recipe_name, "
            "body_markdown, sort_order, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("old-station", "点心", "旧配方", "# 旧", 1, now),
        )
        await self.db._conn.commit()

    def _source_bytes(self, title: str = "新档口") -> bytes:
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(tmp_path)
        try:
            conn.executescript(
                """
                CREATE TABLE sop_stations (
                    slug TEXT PRIMARY KEY,
                    title TEXT,
                    updated_at TEXT
                );
                CREATE TABLE sop_recipes (
                    id INTEGER PRIMARY KEY,
                    station_slug TEXT,
                    section TEXT,
                    recipe_name TEXT,
                    body_markdown TEXT,
                    sort_order INTEGER,
                    updated_at TEXT,
                    tenant_id INTEGER
                );
                """
            )
            conn.execute(
                "INSERT INTO sop_stations (slug, title, updated_at) VALUES (?, ?, ?)",
                ("shulong", title, "2026-05-01T10:00:00"),
            )
            # tenant_id 只存在于源（PG 侧加成性迁移的列）：按列交集回灌时应该被忽略
            conn.execute(
                "INSERT INTO sop_recipes (id, station_slug, section, recipe_name, "
                "body_markdown, sort_order, updated_at, tenant_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (7, "shulong", "点心", "虾饺", "# 虾饺", 1, "2026-05-01T10:00:00", 1),
            )
            conn.commit()
        finally:
            conn.close()
        with open(tmp_path, "rb") as handle:
            data = handle.read()
        os.unlink(tmp_path)
        return data

    async def _rows(self, sql: str) -> list:
        async with self.db._conn.execute(sql) as cur:
            return [tuple(row) for row in await cur.fetchall()]

    async def test_overwrite_replaces_recipes_through_source_member(self):
        await self._seed_local()

        with mock.patch.object(
            backup_service, "is_postgres_backend", return_value=True
        ):
            await backup_service.overwrite_recipes_from_bytes(
                self.store, self._source_bytes()
            )

        stations = await self._rows("SELECT slug, title FROM sop_stations")
        self.assertEqual(stations, [("shulong", "新档口")])
        recipes = await self._rows("SELECT id, recipe_name FROM sop_recipes")
        self.assertEqual(recipes, [(7, "虾饺")])

    async def test_failed_insert_rolls_back_and_keeps_previous_rows(self):
        await self._seed_local()

        # 源里的 title 为 NULL：目标列 NOT NULL，插入阶段失败，整次覆盖必须回滚
        with mock.patch.object(
            backup_service, "is_postgres_backend", return_value=True
        ):
            with self.assertRaises(Exception):
                await backup_service.overwrite_recipes_from_bytes(
                    self.store, self._source_bytes(title=None)
                )

        stations = await self._rows("SELECT slug FROM sop_stations")
        self.assertEqual(stations, [("old-station",)])


class MissingHygieneCaptureIdsTest(unittest.TestCase):
    """恢复后一致性检查：库引用的原始照片缺了就必须报出来。"""

    def setUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.app_db = os.path.join(self._tmpdir.name, "app.db")
        self.capture_root = Path(self._tmpdir.name) / "hygiene-captures"
        self.capture_root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.app_db)
        conn.execute(
            "CREATE TABLE hygiene_standards (id INTEGER PRIMARY KEY, capture_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE hygiene_daily_submissions"
            " (id INTEGER PRIMARY KEY, capture_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE hygiene_capture_variants"
            " (source_capture_id TEXT, capture_id TEXT)"
        )
        conn.execute("INSERT INTO hygiene_standards (capture_id) VALUES ('std-1')")
        conn.execute("INSERT INTO hygiene_daily_submissions (capture_id) VALUES ('other-1')")
        conn.execute(
            "INSERT INTO hygiene_capture_variants (source_capture_id, capture_id)"
            " VALUES ('std-1', 'thumb-1')"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def _touch(self, capture_id):
        (self.capture_root / capture_id).write_bytes(b"photo")

    def test_reports_missing_original_photos(self):
        self._touch("std-1")

        result = backup_service.missing_hygiene_capture_ids()

        self.assertFalse(result["ok"])
        self.assertEqual(result["standard_missing"], 0)
        self.assertEqual(result["other_missing"], 1)
        self.assertEqual(result["missing"][PHOTO_OTHER], ["other-1"])
        self.assertTrue(result["checked_at"])

    def test_ok_when_every_referenced_photo_exists(self):
        self._touch("std-1")
        self._touch("other-1")

        result = backup_service.missing_hygiene_capture_ids()

        self.assertTrue(result["ok"])
        self.assertEqual(result["missing"], {})
        self.assertEqual(result["standard_missing"], 0)

    def test_missing_derivative_does_not_count_as_inconsistent(self):
        """派生图缺失时接口会回退到原图，不算库与照片不一致。"""
        self._touch("std-1")
        self._touch("other-1")

        result = backup_service.missing_hygiene_capture_ids()

        self.assertTrue(result["ok"])
        self.assertNotIn("thumb-1", result["missing"].get(PHOTO_STANDARD, []))


class ContentEquivalenceTest(unittest.TestCase):
    """PG 的整库 pg_dump 同时覆盖业务数据与配方数据。"""

    def test_app_pg_satisfies_business_and_recipes_but_not_credentials(self):
        from services.backup_service import (
            CONTENT_APP_DB,
            CONTENT_APP_PG,
            CONTENT_CREDENTIALS,
            CONTENT_RECIPES,
        )

        self.assertTrue(backup_service.contents_cover([CONTENT_APP_PG], CONTENT_APP_DB))
        self.assertTrue(backup_service.contents_cover([CONTENT_APP_PG], CONTENT_RECIPES))
        self.assertFalse(backup_service.contents_cover([CONTENT_APP_PG], CONTENT_CREDENTIALS))

    def test_sqlite_contents_do_not_satisfy_each_other(self):
        from services.backup_service import CONTENT_APP_DB, CONTENT_APP_PG, CONTENT_RECIPES

        self.assertTrue(backup_service.contents_cover([CONTENT_APP_DB], CONTENT_APP_DB))
        self.assertFalse(backup_service.contents_cover([CONTENT_APP_DB], CONTENT_RECIPES))
        self.assertFalse(backup_service.contents_cover([CONTENT_APP_DB], CONTENT_APP_PG))


class PhotoConsistencySemanticsTest(unittest.TestCase):
    """源磁盘缺失是「不完整」，不是「备份坏了」：不阻断恢复。"""

    def test_source_missing_is_advisory_not_blocking(self):
        result = backup_service.photo_consistency(
            {PHOTO_STANDARD: {"count": 0}},
            {PHOTO_STANDARD: ["gone-1", "gone-2"]},
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["blocking"])
        self.assertEqual(result["missing_total"], 2)
        self.assertIn("源磁盘", " ".join(result["errors"]))

    def test_complete_photos_report_ok_without_missing(self):
        result = backup_service.photo_consistency(
            {PHOTO_STANDARD: {"count": 3}, PHOTO_OTHER: {"count": 1}},
            {},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["missing_total"], 0)
        self.assertEqual(result["errors"], [])


class PgSnapshotPhotoTest(unittest.TestCase):
    """PG 快照的照片按库引用分类（psql），psql 不可用时退化并标注未分类。"""

    def setUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._old_backend = settings.DATABASE_BACKEND
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        settings.DATABASE_BACKEND = "postgres"

        self.capture_root = Path(settings.DATABASE_DIR) / "hygiene-captures"
        self.capture_root.mkdir(parents=True, exist_ok=True)
        self.snap_dir = Path(settings.DATABASE_DIR) / "snapshots" / "20260919_051032"
        self.snap_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        settings.DATABASE_DIR = self._old_database_dir
        settings.DATABASE_BACKEND = self._old_backend
        self._tmpdir.cleanup()

    def _photo(self, capture_id: str, payload: bytes = b"JPEG") -> None:
        (self.capture_root / capture_id).write_bytes(payload)

    def _fake_psql(self, standard, other, variants=()):
        def fake(sql):
            if "hygiene_standards" in sql:
                return [[cid] for cid in standard]
            if "hygiene_capture_variants" in sql:
                return [list(row) for row in variants]
            if "hygiene_" in sql:
                return [[cid] for cid in other]
            return []

        return fake

    def test_photos_are_classified_by_db_references(self):
        self._photo("std-1", b"S")
        self._photo("other-1", b"O")
        self._photo("orphan-1", b"X")

        with mock.patch.object(
            backup_service, "_pg_psql_rows", side_effect=self._fake_psql(["std-1"], ["other-1"])
        ):
            result = backup_service._write_snapshot_photos_pg(self.snap_dir)

        self.assertEqual(result["manifest"][PHOTO_STANDARD]["count"], 1)
        self.assertEqual(result["manifest"][PHOTO_OTHER]["count"], 1)
        self.assertFalse(result["manifest"][PHOTO_STANDARD]["unclassified"])
        self.assertTrue((self.snap_dir / "photos" / "standard" / "std-1").is_file())
        self.assertTrue((self.snap_dir / "photos" / "other" / "other-1").is_file())
        # 分类口径 = 库引用：孤儿文件不进快照
        self.assertFalse((self.snap_dir / "photos" / "other" / "orphan-1").exists())

    def test_variants_follow_their_source_photo(self):
        self._photo("std-1")
        self._photo("thumb-1")

        with mock.patch.object(
            backup_service,
            "_pg_psql_rows",
            side_effect=self._fake_psql(["std-1"], [], variants=[("std-1", "thumb-1")]),
        ):
            result = backup_service._write_snapshot_photos_pg(self.snap_dir)

        self.assertEqual(result["manifest"][PHOTO_STANDARD]["count"], 2)
        self.assertTrue((self.snap_dir / "photos" / "standard" / "thumb-1").is_file())

    def test_referenced_but_missing_photo_is_recorded(self):
        with mock.patch.object(
            backup_service, "_pg_psql_rows", side_effect=self._fake_psql(["gone-1"], [])
        ):
            result = backup_service._write_snapshot_photos_pg(self.snap_dir)

        self.assertEqual(result["manifest"][PHOTO_STANDARD]["count"], 0)
        self.assertEqual(result["manifest"][PHOTO_STANDARD]["referenced"], 1)
        self.assertEqual(result["manifest"][PHOTO_STANDARD]["missing"], 1)
        self.assertEqual(result["missing"][PHOTO_STANDARD], ["gone-1"])

    def test_psql_unavailable_falls_back_to_scan_and_marks_unclassified(self):
        self._photo("a")
        self._photo("b")

        with mock.patch.object(backup_service, "_classify_capture_ids_pg", return_value=None):
            result = backup_service._write_snapshot_photos_pg(self.snap_dir)

        self.assertEqual(result["manifest"][PHOTO_OTHER]["count"], 2)
        self.assertTrue(result["manifest"][PHOTO_OTHER]["unclassified"])
        self.assertTrue(result["manifest"][PHOTO_STANDARD]["unclassified"])
        self.assertEqual(result["manifest"][PHOTO_STANDARD]["count"], 0)

    def test_psql_missing_table_is_treated_as_no_photos(self):
        with mock.patch.object(backup_service, "_pg_psql_rows", return_value=[]):
            classified = backup_service._classify_capture_ids_pg()

        self.assertEqual(classified[PHOTO_STANDARD], [])
        self.assertEqual(classified[PHOTO_OTHER], [])


class RestorePgDumpTest(unittest.TestCase):
    """PG 整库恢复：pg_restore 参数、失败脱敏、成功后重置序列。"""

    DSN = "postgresql://luyun:sup3r-secret@127.0.0.1:5432/luyun"

    def setUp(self):
        self._old_dsn = getattr(settings, "POSTGRES_DSN", "")
        settings.POSTGRES_DSN = self.DSN
        self._tmpdir = tempfile.TemporaryDirectory()
        self.dump = Path(self._tmpdir.name) / "app.pgdump"
        self.dump.write_bytes(b"PGDMP-fake")

    def tearDown(self):
        settings.POSTGRES_DSN = self._old_dsn
        self._tmpdir.cleanup()

    def _completed(self, returncode=0, stderr=""):
        proc = mock.Mock()
        proc.returncode = returncode
        proc.stderr = stderr
        proc.stdout = ""
        return proc

    def test_missing_dump_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "app.pgdump"):
            backup_service.restore_pg_dump_sync(str(Path(self._tmpdir.name) / "nope.pgdump"))

    def test_missing_pg_restore_binary_is_reported(self):
        with mock.patch.object(backup_service.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "pg_restore"):
                backup_service.restore_pg_dump_sync(str(self.dump))

    def test_success_runs_pg_restore_then_resets_sequences(self):
        with mock.patch.object(
            backup_service.shutil, "which", return_value="/usr/bin/pg_restore"
        ), mock.patch.object(
            backup_service.subprocess, "run", return_value=self._completed()
        ) as run:
            backup_service.restore_pg_dump_sync(str(self.dump))

        first_cmd = run.call_args_list[0][0][0]
        self.assertEqual(first_cmd[0], "pg_restore")
        self.assertIn("--clean", first_cmd)
        self.assertIn("--if-exists", first_cmd)
        self.assertEqual(first_cmd[-1], str(self.dump))
        # 第二条命令是序列重置（psql）
        self.assertEqual(run.call_args_list[1][0][0][0], "psql")

    def test_failure_keeps_reason_but_not_password(self):
        with mock.patch.object(
            backup_service.shutil, "which", return_value="/usr/bin/pg_restore"
        ), mock.patch.object(
            backup_service.subprocess,
            "run",
            return_value=self._completed(
                returncode=1,
                stderr=f"pg_restore: error: could not connect to {self.DSN}",
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                backup_service.restore_pg_dump_sync(str(self.dump))

        message = str(ctx.exception)
        self.assertIn("pg_restore 失败", message)
        self.assertIn("could not connect", message)
        self.assertNotIn("sup3r-secret", message)
        self.assertIn("***", message)


if __name__ == "__main__":
    unittest.main()
