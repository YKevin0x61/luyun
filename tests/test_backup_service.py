#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统完整备份 v2 服务测试。"""

import io
import json
import os
import sqlite3
import struct
import tarfile
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import aiosqlite

from config import settings
from database import CHINA_TZ, DatabaseManager
from services import backup_service, credentials_store
from services.backup_service import BACKUP_MAGIC, SNAPSHOT_KEEP
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
        self.assertEqual(parsed["meta"]["version"], 2)

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


class ExportRecipesDbBytesTest(unittest.TestCase):
    """配方表与业务表同库时，recipes 成员应只含 sop_* 表，不重复整库。"""

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


if __name__ == "__main__":
    unittest.main()
