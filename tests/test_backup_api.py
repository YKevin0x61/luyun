#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份中心 API：统一备份点列表、备份健康、保留配置与清理。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.backup import router as backup_router
from api.security import require_session, verify_admin_token
from config import settings
from database import DatabaseManager, get_db
from services import backup_points, backup_retention, backup_service


def _make_app(db: DatabaseManager) -> FastAPI:
    app = FastAPI()
    app.include_router(backup_router)
    app.dependency_overrides[verify_admin_token] = lambda: None
    app.dependency_overrides[require_session] = lambda: "session-a"
    app.dependency_overrides[get_db] = lambda: db
    return app


class BackupApiTest(unittest.TestCase):
    def setUp(self):
        import asyncio

        self._old_database_dir = settings.DATABASE_DIR
        self._old_cold_dir = settings.COLD_BACKUP_DIR
        self._saved_retention = backup_retention.cache_get()
        backup_retention.cache_set(backup_retention.RetentionConfig())
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        settings.COLD_BACKUP_DIR = os.path.join(self._tmpdir.name, "backups")
        self.db = DatabaseManager()
        self.assertTrue(asyncio.run(self.db.connect()))
        self.client = TestClient(_make_app(self.db))
        backup_points.set_health_cache(None)

    def tearDown(self):
        import asyncio

        asyncio.run(self.db.close())
        backup_retention.cache_set(self._saved_retention)
        backup_points.set_health_cache(None)
        settings.DATABASE_DIR = self._old_database_dir
        settings.COLD_BACKUP_DIR = self._old_cold_dir
        self._tmpdir.cleanup()

    def _snapshot(self, ts: str, provenance: str = "manual") -> None:
        root = Path(self._tmpdir.name) / "restore_snapshots" / ts
        root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(root / "app.db"))
        conn.executescript(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY);"
            "CREATE TABLE tables (id INTEGER PRIMARY KEY);"
            "CREATE TABLE dish_stations (id INTEGER PRIMARY KEY);"
        )
        conn.commit()
        conn.close()
        (root / "snapshot_meta.json").write_text(
            '{"ts": "%s", "created_at": "2026-01-01T00:00:00+08:00",'
            ' "provenance": "%s", "contents": ["app_db"]}' % (ts, provenance),
            encoding="utf-8",
        )

    def test_points_endpoint_returns_health_and_not_backed_up(self):
        self._snapshot("20260101_000001")
        response = self.client.get("/api/backup/points")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(len(body["points"]), 1)
        self.assertEqual(body["points"][0]["medium"], "local_snapshot")
        self.assertEqual(body["points"][0]["provenance_label"], "手动")
        self.assertIn(body["health"]["status"], {"ok", "legacy_only", "degraded"})
        names = {item["name"] for item in body["not_backed_up"]}
        self.assertIn("日志库", names)

    def test_points_endpoint_empty_state(self):
        body = self.client.get("/api/backup/points").json()
        self.assertEqual(body["points"], [])
        self.assertEqual(body["health"]["status"], "no_backup")
        self.assertIn("还没有可用于恢复的备份", body["health"]["summary"])

    def test_validate_point_read_only(self):
        self._snapshot("20260101_000001")
        before = sorted(p.name for p in Path(settings.DATABASE_DIR).rglob("*"))
        response = self.client.post("/api/backup/points/snapshot:20260101_000001/validate")
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertTrue(result["ok"])
        self.assertTrue(result["recoverable"])
        after = sorted(p.name for p in Path(settings.DATABASE_DIR).rglob("*"))
        self.assertEqual(before, after)

    def test_validate_unknown_point_is_404(self):
        response = self.client.post("/api/backup/points/snapshot:19990101_000000/validate")
        self.assertEqual(response.status_code, 404)

    def test_retention_get_and_preview(self):
        self._snapshot("20260101_000001")
        response = self.client.get("/api/backup/retention")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["config"], {"snapshot_keep": 5, "export_keep": 5, "cold_keep": 14}
        )
        self.assertEqual(body["limits"]["snapshot_keep_max"], 20)
        self.assertEqual(body["limits"]["cold_keep_max"], 90)
        self.assertIn("snapshot", body["preview"])

    def test_retention_rejects_both_one(self):
        response = self.client.put(
            "/api/backup/retention", json={"snapshot_keep": 1, "cold_keep": 1}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("不能同时只保留 1 份", response.json()["detail"])

    def test_save_retention_runs_cleanup(self):
        for ts in ("20260101_000001", "20260101_000002", "20260101_000003"):
            self._snapshot(ts)
        response = self.client.put(
            "/api/backup/retention", json={"snapshot_keep": 2, "cold_keep": 14}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["config"]["snapshot_keep"], 2)
        deleted_ids = [d["id"] for d in body["cleanup"]["deleted"]]
        self.assertIn("snapshot:20260101_000001", deleted_ids)
        remaining = {
            p.name
            for p in (Path(settings.DATABASE_DIR) / "restore_snapshots").iterdir()
        }
        self.assertEqual(remaining, {"20260101_000002", "20260101_000003"})

    def test_cleanup_preview_does_not_delete(self):
        for ts in ("20260101_000001", "20260101_000002", "20260101_000003"):
            self._snapshot(ts)
        response = self.client.post(
            "/api/backup/cleanup/preview",
            json={"snapshot_keep": 1, "cold_keep": 14},
        )
        self.assertEqual(response.status_code, 200)
        preview = response.json()["preview"]
        self.assertEqual(len(preview["snapshot"]["delete"]), 2)
        remaining = {
            p.name
            for p in (Path(settings.DATABASE_DIR) / "restore_snapshots").iterdir()
        }
        self.assertEqual(len(remaining), 3)

    def test_manual_cleanup_uses_saved_config(self):
        for ts in ("20260101_000001", "20260101_000002", "20260101_000003"):
            self._snapshot(ts)
        response = self.client.post("/api/backup/cleanup")
        self.assertEqual(response.status_code, 200)
        # Default snapshot_keep=5 keeps everything.
        self.assertEqual(response.json()["deleted"], [])

    def test_health_refresh_endpoint(self):
        response = self.client.post("/api/backup/health/refresh")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"]["status"], "no_backup")

    def test_snapshot_rollback_creates_pre_rollback_snapshot(self):
        self._snapshot("20260101_000001")
        response = self.client.post(
            "/api/backup/snapshots/20260101_000001/rollback"
            "?apply_standard_photos=false&apply_other_photos=false"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["applied"]["app_db"])
        self.assertIsNotNone(body["snapshot_ts"])

        meta = json.loads(
            (
                Path(settings.DATABASE_DIR)
                / "restore_snapshots"
                / body["snapshot_ts"]
                / "snapshot_meta.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(meta["provenance"], "pre_rollback")

    def test_snapshot_rollback_rejects_unknown_snapshot(self):
        response = self.client.post("/api/backup/snapshots/19990101_000000/rollback")
        self.assertEqual(response.status_code, 404)

    def test_snapshot_rollback_rejects_corrupt_snapshot(self):
        self._snapshot("20260101_000001")
        # Break the snapshot's database while keeping a non-zero size.
        db_path = (
            Path(settings.DATABASE_DIR) / "restore_snapshots" / "20260101_000001" / "app.db"
        )
        db_path.write_bytes(b"not a sqlite database at all")
        response = self.client.post("/api/backup/snapshots/20260101_000001/rollback")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["reason"], "backup_corrupt")


class PgSnapshotRollbackApiTest(unittest.TestCase):
    """PG 快照的页面内整库恢复：调 pg_restore、重建连接、会话失效。"""

    def setUp(self):
        import asyncio

        self._old_database_dir = settings.DATABASE_DIR
        self._old_cold_dir = settings.COLD_BACKUP_DIR
        self._old_backend = settings.DATABASE_BACKEND
        self._saved_retention = backup_retention.cache_get()
        backup_retention.cache_set(backup_retention.RetentionConfig())
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        settings.COLD_BACKUP_DIR = os.path.join(self._tmpdir.name, "backups")
        # 连接本身走 sqlite（快、不依赖外部 PG），「当前是不是 PG 后端」由 mock 决定
        settings.DATABASE_BACKEND = "sqlite"
        self.db = DatabaseManager()
        self.assertTrue(asyncio.run(self.db.connect()))
        self.client = TestClient(_make_app(self.db))
        backup_points.set_health_cache(None)

    def tearDown(self):
        import asyncio

        asyncio.run(self.db.close())
        backup_retention.cache_set(self._saved_retention)
        backup_points.set_health_cache(None)
        settings.DATABASE_DIR = self._old_database_dir
        settings.COLD_BACKUP_DIR = self._old_cold_dir
        settings.DATABASE_BACKEND = self._old_backend
        self._tmpdir.cleanup()

    def _pg_snapshot(self, ts: str) -> None:
        root = Path(self._tmpdir.name) / "restore_snapshots" / ts
        root.mkdir(parents=True, exist_ok=True)
        (root / "app.pgdump").write_bytes(b"PGDMP-fake")
        (root / "snapshot_meta.json").write_text(
            json.dumps(
                {
                    "ts": ts,
                    "created_at": "2026-01-01T00:00:00+08:00",
                    "provenance": "manual",
                    "contents": ["app_pg", "runtime", "credentials"],
                }
            ),
            encoding="utf-8",
        )

    def _rollback(self, ts: str, *, postgres: bool = True, side_effect=None):
        with mock.patch.object(
            backup_points, "create_pre_restore_snapshot", return_value="20260101_000099"
        ), mock.patch.object(
            backup_service, "is_postgres_backend", return_value=postgres
        ), mock.patch.object(
            backup_service, "restore_pg_dump_sync", side_effect=side_effect
        ) as restore:
            response = self.client.post(
                f"/api/backup/snapshots/{ts}/rollback"
                "?apply_standard_photos=false&apply_other_photos=false"
            )
        return response, restore

    def test_pg_snapshot_rollback_runs_pg_restore_and_reconnects(self):
        self._pg_snapshot("20260101_000001")

        response, restore = self._rollback("20260101_000001")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["applied"]["app_pg"])
        self.assertIn("业务数据 (PostgreSQL)", body["applied_labels"])
        # 整库被替换（含 auth 表）：当前会话必须失效
        self.assertTrue(body["session_invalidated"])
        restore.assert_called_once()
        # drop/重建过对象，连接必须已重建且可用
        self.assertTrue(self.db.is_connected())

    def test_pg_restore_failure_returns_500_with_reason(self):
        self._pg_snapshot("20260101_000002")

        response, _ = self._rollback(
            "20260101_000002",
            side_effect=RuntimeError("pg_restore 失败（退出码 1）：relation \"orders\" does not exist"),
        )

        self.assertEqual(response.status_code, 500)
        self.assertIn("pg_restore", response.json()["detail"])
        self.assertIn("orders", response.json()["detail"])

    def test_pg_snapshot_rejected_on_non_postgres_backend(self):
        self._pg_snapshot("20260101_000003")

        response, restore = self._rollback("20260101_000003", postgres=False)

        self.assertEqual(response.status_code, 400)
        self.assertIn("PostgreSQL", response.json()["detail"])
        restore.assert_not_called()


class BackupExportImportRoundTripTest(unittest.TestCase):
    """端到端：导出（含两类卫生照片）→ 备份点登记 → 预览 → 恢复。"""

    def setUp(self):
        import asyncio

        from services import credentials_store
        from services.credentials_store import CredentialBundle

        self._old_database_dir = settings.DATABASE_DIR
        self._old_cold_dir = settings.COLD_BACKUP_DIR
        self._saved_retention = backup_retention.cache_get()
        backup_retention.cache_set(backup_retention.RetentionConfig())
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        settings.COLD_BACKUP_DIR = os.path.join(self._tmpdir.name, "backups")

        self._credentials_store = credentials_store
        self._saved_creds = credentials_store._cache
        credentials_store._cache = CredentialBundle(
            phone="13800000000",
            password="pw",
            shop_id="1",
            company_id="2",
            shop_name="LuckIn",
            delivery_shop_id="2",
        )
        (Path(self._tmpdir.name) / "credentials.enc").write_bytes(b"enc")

        self.db = DatabaseManager()
        self.assertTrue(asyncio.run(self.db.connect()))
        self.client = TestClient(_make_app(self.db))
        backup_points.set_health_cache(None)

        self.capture_root = Path(self._tmpdir.name) / "hygiene-captures"
        self.capture_root.mkdir(parents=True, exist_ok=True)
        self._seed_hygiene()

    def tearDown(self):
        import asyncio

        asyncio.run(self.db.close())
        self._credentials_store._cache = self._saved_creds
        backup_retention.cache_set(self._saved_retention)
        backup_points.set_health_cache(None)
        settings.DATABASE_DIR = self._old_database_dir
        settings.COLD_BACKUP_DIR = self._old_cold_dir
        self._tmpdir.cleanup()

    def _seed_hygiene(self):
        import asyncio

        async def seed():
            now = "2026-01-01T00:00:00"
            await self.db._conn.execute("PRAGMA foreign_keys=OFF")
            await self.db._conn.execute(
                "INSERT INTO hygiene_standards (item_id, capture_id, content_type,"
                " created_at) VALUES (1, 's1', 'image/jpeg', ?)",
                (now,),
            )
            await self.db._conn.execute(
                "INSERT INTO hygiene_daily_submissions (instance_id, capture_id,"
                " content_type, frozen_standard_id, submitter_id, submitter_phone,"
                " zone_name, captured_at, created_at)"
                " VALUES (1, 'o1', 'image/jpeg', 1, 1, '13800000000', 'zone', ?, ?)",
                (now, now),
            )
            await self.db._conn.commit()
            await self.db._conn.execute("PRAGMA foreign_keys=ON")

        asyncio.run(seed())
        (self.capture_root / "s1").write_bytes(b"S1")
        (self.capture_root / "o1").write_bytes(b"O1")

    def test_export_then_preview_then_restore(self):
        export = self.client.post(
            "/api/backup/export",
            json={
                "passphrase": "pass1234",
                "include_runtime": False,
                "include_app_db": True,
                "include_recipes": False,
                "include_standard_photos": True,
                "include_other_photos": True,
            },
        )
        self.assertEqual(export.status_code, 200)
        blob = export.content
        self.assertTrue(blob)

        points_body = self.client.get("/api/backup/points").json()
        exports = [p for p in points_body["points"] if p["medium"] == "export_backup"]
        self.assertEqual(len(exports), 1)
        self.assertIn("standard_photos", exports[0]["contents"])
        self.assertIn("other_photos", exports[0]["contents"])
        self.assertTrue(exports[0]["recoverable"])

        preview = self.client.post(
            "/api/backup/import/preview",
            files={"file": ("b.luyunbak", blob, "application/octet-stream")},
            data={"passphrase": "pass1234"},
        )
        self.assertEqual(preview.status_code, 200)
        preview_body = preview.json()
        self.assertTrue(preview_body["validation"]["in_backup"]["ok"])
        self.assertEqual(preview_body["photos"]["standard"]["count"], 1)
        self.assertEqual(preview_body["photos"]["other"]["count"], 1)
        self.assertTrue(preview_body["default_apply"]["standard_photos"])
        self.assertTrue(preview_body["default_apply"]["other_photos"])
        self.assertFalse(preview_body["requires_force"])

        # Wipe the live photos to prove restore writes them back.
        (self.capture_root / "s1").unlink()
        (self.capture_root / "o1").unlink()

        apply = self.client.post(
            "/api/backup/import/apply",
            data={
                "import_token": preview_body["import_token"],
                "mode": "overwrite",
                "apply_credentials": "false",
                "apply_runtime": "false",
                "apply_app_db": "true",
                "apply_recipes": "false",
                "apply_standard_photos": "true",
                "apply_other_photos": "true",
                "force": "false",
            },
        )
        self.assertEqual(apply.status_code, 200)
        body = apply.json()
        self.assertIn("标准图", body["applied_labels"])
        self.assertIn("其它照片", body["applied_labels"])
        self.assertIsNotNone(body["snapshot_ts"])
        self.assertTrue((self.capture_root / "s1").is_file())
        self.assertTrue((self.capture_root / "o1").is_file())


class BackupExportBackendCapabilityTest(unittest.TestCase):
    """导出面板按后端能力渲染的依据：PG 不导出业务数据，但配方必须来自当前库。"""

    def setUp(self):
        import asyncio

        self._old_database_dir = settings.DATABASE_DIR
        self._old_cold_dir = settings.COLD_BACKUP_DIR
        self._saved_retention = backup_retention.cache_get()
        backup_retention.cache_set(backup_retention.RetentionConfig())
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        settings.COLD_BACKUP_DIR = os.path.join(self._tmpdir.name, "backups")
        self.db = DatabaseManager()
        self.assertTrue(asyncio.run(self.db.connect()))
        self.client = TestClient(_make_app(self.db))
        backup_points.set_health_cache(None)

    def tearDown(self):
        import asyncio

        asyncio.run(self.db.close())
        backup_retention.cache_set(self._saved_retention)
        backup_points.set_health_cache(None)
        settings.DATABASE_DIR = self._old_database_dir
        settings.COLD_BACKUP_DIR = self._old_cold_dir
        self._tmpdir.cleanup()

    def _seed_recipe(self) -> None:
        import asyncio

        async def seed():
            await self.db._conn.execute(
                "INSERT INTO sop_stations (slug, title, updated_at) VALUES (?, ?, ?)",
                ("shulong", "熟笼档", "2026-05-01T10:00:00"),
            )
            await self.db._conn.execute(
                "INSERT INTO sop_recipes (station_slug, section, recipe_name, "
                "body_markdown, sort_order, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("shulong", "点心", "虾饺", "# 虾饺", 1, "2026-05-01T10:00:00"),
            )
            await self.db._conn.commit()

        asyncio.run(seed())

    def _export_body(self, *, include_app_db: bool) -> dict:
        return {
            "passphrase": "pass1234",
            "include_runtime": False,
            "include_app_db": include_app_db,
            "include_recipes": True,
            "include_standard_photos": False,
            "include_other_photos": False,
        }

    def test_points_reports_sqlite_capability(self):
        body = self.client.get("/api/backup/points").json()
        self.assertEqual(body["backend"], "sqlite")
        self.assertTrue(body["export_app_db_supported"])

    def test_points_reports_postgres_capability(self):
        with mock.patch.object(
            backup_service, "is_postgres_backend", return_value=True
        ):
            body = self.client.get("/api/backup/points").json()
        self.assertEqual(body["backend"], "postgres")
        self.assertFalse(body["export_app_db_supported"])

    def test_pg_export_rejects_app_db_with_actionable_hint(self):
        with mock.patch.object(
            backup_service, "is_postgres_backend", return_value=True
        ):
            response = self.client.post(
                "/api/backup/export", json=self._export_body(include_app_db=True)
            )

        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        # 光说「不支持」会让用户以为整个导出备份都不可用，必须给出下一步
        self.assertIn("取消勾选", detail)
        self.assertIn("pg_dump", detail)

        export_dir = Path(settings.DATABASE_DIR) / "backup_exports"
        leftovers = list(export_dir.glob("*.luyunbak")) if export_dir.is_dir() else []
        self.assertEqual(leftovers, [])

    def test_pg_export_draws_recipes_from_live_connection(self):
        self._seed_recipe()

        with mock.patch.object(
            backup_service, "is_postgres_backend", return_value=True
        ), mock.patch.object(
            backup_service,
            "export_recipes_db_bytes",
            side_effect=AssertionError("PG 下不得再按文件路径读 recipes"),
        ) as legacy:
            response = self.client.post(
                "/api/backup/export", json=self._export_body(include_app_db=False)
            )

        self.assertEqual(response.status_code, 200)
        legacy.assert_not_called()

        parsed = backup_service.parse_backup(response.content, "pass1234")
        self.assertIsNotNone(parsed["recipes_db_bytes"])
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with open(tmp_path, "wb") as handle:
                handle.write(parsed["recipes_db_bytes"])
            conn = sqlite3.connect(tmp_path)
            try:
                recipes = conn.execute("SELECT COUNT(*) FROM sop_recipes").fetchone()[0]
                stations = conn.execute("SELECT COUNT(*) FROM sop_stations").fetchone()[0]
            finally:
                conn.close()
        finally:
            os.unlink(tmp_path)

        self.assertEqual(recipes, 1)
        self.assertEqual(stations, 1)

        points = self.client.get("/api/backup/points").json()["points"]
        exported = [p for p in points if p["medium"] == "export_backup"]
        self.assertEqual(len(exported), 1)
        self.assertIn("recipes_db", exported[0]["contents"])


if __name__ == "__main__":
    unittest.main()
