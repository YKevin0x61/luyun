#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份中心 API：统一备份点列表、备份健康、保留配置与清理。

业务数据只剩整库 pg_dump 一种形态（成员 ``app.pgdump``）：快照、导出包与恢复
都按它断言；SQLite 时代的 ``app.db`` 只能作为「必须被明确拒绝」的输入出现。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi import FastAPI

from api import backup as backup_api
from api.backup import router as backup_router
from api.security import require_session, verify_admin_token
from config import settings
from database import DatabaseManager, get_db
from services import backup_points, backup_retention, backup_service
from tests.backup_fixtures import legacy_sqlite_backup, seed_hygiene_photo_refs


def _make_app(db: DatabaseManager) -> FastAPI:
    app = FastAPI()
    app.include_router(backup_router)
    app.dependency_overrides[verify_admin_token] = lambda: None
    app.dependency_overrides[require_session] = lambda: "session-a"
    app.dependency_overrides[get_db] = lambda: db
    return app


def _fake_pg_dump(dst_path: str, progress=None) -> None:
    """导出/快照用的 pg_dump 替身：用例只关心成员与结论，不真导整库。"""
    with open(dst_path, "wb") as handle:
        handle.write(b"PGDMP-CONTENT")
    if progress is not None:
        progress(14)


class _ApiTestBase(unittest.IsolatedAsyncioTestCase):
    """把所有请求跑在**测试自己的事件循环**里。

    ``TestClient`` 会在另一个线程的 loop 里执行应用，而业务库连接是在本用例的
    loop 里建的（asyncpg 的连接与 loop 绑定）：跨 loop 调用会报
    「attached to a different loop」。``httpx.ASGITransport`` 直接在本 loop 里调用
    ASGI 应用，与 ``tests/test_release_update_api.py`` 同一姿势。
    """

    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._old_cold_dir = settings.COLD_BACKUP_DIR
        self._saved_retention = backup_retention.cache_get()
        backup_retention.cache_set(backup_retention.RetentionConfig())
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        settings.COLD_BACKUP_DIR = os.path.join(self._tmpdir.name, "backups")
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.app = _make_app(self.db)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

        # 导出端点要求当前有凭据（构建端无条件打包 credentials.json）
        from services import credentials_store
        from services.credentials_store import CredentialBundle

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
        self.capture_root = Path(self._tmpdir.name) / "hygiene-captures"
        self.capture_root.mkdir(parents=True, exist_ok=True)
        backup_points.set_health_cache(None)

    async def asyncTearDown(self):
        await self.client.aclose()
        # 导出任务表是模块级状态：不清掉，下一条用例会被「已有一个导出任务在跑」挡住
        backup_api._EXPORT_JOBS.clear()
        await self.db.close()
        self._credentials_store._cache = self._saved_creds
        backup_retention.cache_set(self._saved_retention)
        backup_points.set_health_cache(None)
        settings.DATABASE_DIR = self._old_database_dir
        settings.COLD_BACKUP_DIR = self._old_cold_dir
        self._tmpdir.cleanup()

class BackupApiTest(_ApiTestBase):
    """备份点列表 / 校验 / 保留配置 / 清理。"""

    def _pg_snapshot(self, ts: str, provenance: str = "manual", *, dump: bytes = b"PGDMP-fake"):
        root = Path(self._tmpdir.name) / "restore_snapshots" / ts
        root.mkdir(parents=True, exist_ok=True)
        (root / "app.pgdump").write_bytes(dump)
        (root / "snapshot_meta.json").write_text(
            json.dumps(
                {
                    "ts": ts,
                    "created_at": "2026-01-01T00:00:00+08:00",
                    "provenance": provenance,
                    "contents": ["app_pg", "runtime"],
                }
            ),
            encoding="utf-8",
        )

    def _legacy_sqlite_snapshot(self, ts: str) -> None:
        root = Path(self._tmpdir.name) / "restore_snapshots" / ts
        root.mkdir(parents=True, exist_ok=True)
        (root / "app.db").write_bytes(b"SQLite format 3\x00legacy")
        (root / "snapshot_meta.json").write_text(
            json.dumps(
                {
                    "ts": ts,
                    "created_at": "2026-01-01T00:00:00+08:00",
                    "provenance": "manual",
                    "contents": ["app_db"],
                }
            ),
            encoding="utf-8",
        )

    async def test_points_endpoint_returns_health_and_not_backed_up(self):
        self._pg_snapshot("20260101_000001")
        response = await self.client.get("/api/backup/points")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(len(body["points"]), 1)
        self.assertEqual(body["points"][0]["medium"], "local_snapshot")
        self.assertEqual(body["points"][0]["provenance_label"], "手动")
        self.assertIn(body["health"]["status"], {"ok", "legacy_only", "degraded"})
        names = {item["name"] for item in body["not_backed_up"]}
        self.assertIn("日志库", names)

    async def test_points_endpoint_empty_state(self):
        response = await self.client.get("/api/backup/points")
        body = response.json()
        self.assertEqual(body["points"], [])
        self.assertEqual(body["health"]["status"], "no_backup")
        self.assertIn("还没有可用于恢复的备份", body["health"]["summary"])

    async def test_validate_point_read_only(self):
        self._pg_snapshot("20260101_000001")
        before = sorted(p.name for p in Path(settings.DATABASE_DIR).rglob("*"))
        response = await self.client.post("/api/backup/points/snapshot:20260101_000001/validate")
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertTrue(result["ok"])
        self.assertTrue(result["recoverable"])
        after = sorted(p.name for p in Path(settings.DATABASE_DIR).rglob("*"))
        self.assertEqual(before, after)

    async def test_validate_unknown_point_is_404(self):
        response = await self.client.post("/api/backup/points/snapshot:19990101_000000/validate")
        self.assertEqual(response.status_code, 404)

    async def test_retention_get_and_preview(self):
        self._pg_snapshot("20260101_000001")
        response = await self.client.get("/api/backup/retention")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["config"], {"snapshot_keep": 5, "export_keep": 5, "cold_keep": 14}
        )
        self.assertEqual(body["limits"]["snapshot_keep_max"], 20)
        self.assertEqual(body["limits"]["cold_keep_max"], 90)
        self.assertIn("snapshot", body["preview"])

    async def test_retention_rejects_both_one(self):
        response = await self.client.put(
            "/api/backup/retention", json={"snapshot_keep": 1, "cold_keep": 1}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("不能同时只保留 1 份", response.json()["detail"])

    async def test_save_retention_runs_cleanup(self):
        for ts in ("20260101_000001", "20260101_000002", "20260101_000003"):
            self._pg_snapshot(ts)
        response = await self.client.put(
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

    async def test_cleanup_preview_does_not_delete(self):
        for ts in ("20260101_000001", "20260101_000002", "20260101_000003"):
            self._pg_snapshot(ts)
        response = await self.client.post(
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

    async def test_manual_cleanup_uses_saved_config(self):
        for ts in ("20260101_000001", "20260101_000002", "20260101_000003"):
            self._pg_snapshot(ts)
        response = await self.client.post("/api/backup/cleanup")
        self.assertEqual(response.status_code, 200)
        # Default snapshot_keep=5 keeps everything.
        self.assertEqual(response.json()["deleted"], [])

    async def test_health_refresh_endpoint(self):
        response = await self.client.post("/api/backup/health/refresh")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"]["status"], "no_backup")

    async def test_snapshot_rollback_creates_pre_rollback_snapshot(self):
        self._pg_snapshot("20260101_000001")

        with mock.patch.object(
            backup_points, "create_pre_restore_snapshot", return_value="20260101_000099"
        ), mock.patch.object(
            backup_service, "restore_pg_dump_sync"
        ) as restore:
            response = await self.client.post(
                "/api/backup/snapshots/20260101_000001/rollback"
                "?apply_standard_photos=false&apply_other_photos=false"
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["applied"]["app_pg"])
        self.assertTrue(body["applied"]["recipes_db"])
        self.assertFalse(body["applied"]["app_db"])
        self.assertEqual(body["snapshot_ts"], "20260101_000099")
        restore.assert_called_once()

    async def test_snapshot_rollback_rejects_unknown_snapshot(self):
        response = await self.client.post("/api/backup/snapshots/19990101_000000/rollback")
        self.assertEqual(response.status_code, 404)

    async def test_snapshot_rollback_rejects_empty_dump(self):
        self._pg_snapshot("20260101_000001", dump=b"")
        response = await self.client.post(
            "/api/backup/snapshots/20260101_000001/rollback"
            "?apply_standard_photos=false&apply_other_photos=false"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["reason"], "backup_corrupt")

    async def test_snapshot_rollback_rejects_legacy_sqlite_snapshot(self):
        """SQLite 时代的快照文件还在，但不能在本机回滚：必须明确拒绝。"""
        self._legacy_sqlite_snapshot("20260101_000002")
        response = await self.client.post(
            "/api/backup/snapshots/20260101_000002/rollback"
            "?apply_standard_photos=false&apply_other_photos=false"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["reason"], "backup_corrupt")
        self.assertIn("无法恢复", response.json()["detail"]["message"])


class PgSnapshotRollbackApiTest(_ApiTestBase):
    """PG 快照的页面内整库恢复：调 pg_restore、重建连接、会话失效。"""

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

    async def _rollback(self, ts: str, *, side_effect=None):
        with mock.patch.object(
            backup_points, "create_pre_restore_snapshot", return_value="20260101_000099"
        ), mock.patch.object(
            backup_service, "restore_pg_dump_sync", side_effect=side_effect
        ) as restore:
            response = await self.client.post(
                f"/api/backup/snapshots/{ts}/rollback"
                "?apply_standard_photos=false&apply_other_photos=false"
            )
        return response, restore

    async def test_pg_snapshot_rollback_runs_pg_restore_and_reconnects(self):
        self._pg_snapshot("20260101_000001")

        response, restore = await self._rollback("20260101_000001")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["applied"]["app_pg"])
        self.assertIn("业务数据 (PostgreSQL)", body["applied_labels"])
        # 整库被替换（含 auth 表）：当前会话必须失效
        self.assertTrue(body["session_invalidated"])
        restore.assert_called_once()
        # drop/重建过对象，连接必须已重建且可用
        self.assertTrue(self.db.is_connected())

    async def test_pg_restore_failure_returns_500_with_reason(self):
        self._pg_snapshot("20260101_000002")

        response, _ = await self._rollback(
            "20260101_000002",
            side_effect=RuntimeError("pg_restore 失败（退出码 1）：relation \"orders\" does not exist"),
        )

        self.assertEqual(response.status_code, 500)
        self.assertIn("pg_restore", response.json()["detail"])
        self.assertIn("orders", response.json()["detail"])


class BackupExportImportRoundTripTest(_ApiTestBase):
    """端到端：导出（含两类卫生照片）→ 备份点登记 → 预览 → 恢复。"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # 分类只认业务库里的引用：标准图 / 其它照片各一张，文件落在照片目录里
        await seed_hygiene_photo_refs(self.db._conn, standards=["s1"], others=["o1"])
        (self.capture_root / "s1").write_bytes(b"S1")
        (self.capture_root / "o1").write_bytes(b"O1")

    async def test_export_then_preview_then_restore(self):
        with mock.patch.object(
            backup_service, "export_pg_dump_to_file", side_effect=_fake_pg_dump
        ):
            export = await self.client.post(
                "/api/backup/export",
                json={
                    "passphrase": "pass1234",
                    "include_runtime": False,
                    "include_app_db": True,
                    "include_standard_photos": True,
                    "include_other_photos": True,
                },
            )
        self.assertEqual(export.status_code, 200, export.text)
        blob = export.content
        self.assertTrue(blob)

        points_response = await self.client.get("/api/backup/points")
        points_body = points_response.json()
        exports = [p for p in points_body["points"] if p["medium"] == "export_backup"]
        self.assertEqual(len(exports), 1)
        self.assertIn("app_pg", exports[0]["contents"])
        self.assertIn("standard_photos", exports[0]["contents"])
        self.assertIn("other_photos", exports[0]["contents"])
        self.assertTrue(exports[0]["recoverable"])
        # 配方表在同一个库里：整库 dump 带走配方，清单要如实标出来
        self.assertIn("recipes_db", exports[0]["contents"])
        self.assertNotIn("业务数据", [m["label"] for m in exports[0]["missing"]])

        preview = await self.client.post(
            "/api/backup/import/preview",
            files={"file": ("b.luyunbak", blob, "application/octet-stream")},
            data={"passphrase": "pass1234"},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        preview_body = preview.json()
        self.assertTrue(preview_body["validation"]["in_backup"]["ok"])
        self.assertEqual(preview_body["photos"]["standard"]["count"], 1)
        self.assertEqual(preview_body["photos"]["other"]["count"], 1)
        self.assertTrue(preview_body["default_apply"]["standard_photos"])
        self.assertTrue(preview_body["default_apply"]["other_photos"])
        self.assertTrue(preview_body["default_apply"]["app_pg"])
        self.assertFalse(preview_body["has_sqlite_app_db"])
        self.assertFalse(preview_body["requires_force"])

        # Wipe the live photos to prove restore writes them back.
        (self.capture_root / "s1").unlink()
        (self.capture_root / "o1").unlink()

        apply = await self.client.post(
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
        self.assertEqual(apply.status_code, 200, apply.text)
        body = apply.json()
        self.assertIn("业务数据 (PostgreSQL)", body["applied_labels"])
        self.assertIn("标准图", body["applied_labels"])
        self.assertIn("其它照片", body["applied_labels"])
        self.assertIsNotNone(body["snapshot_ts"])
        self.assertTrue((self.capture_root / "s1").is_file())
        self.assertTrue((self.capture_root / "o1").is_file())


class BackupExportBackendCapabilityTest(_ApiTestBase):
    """后端的导出/导入形态：业务数据是整库 pg_dump，只能整库覆盖恢复。"""

    async def _seed_recipe(self) -> None:
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

    def _export_body(self, *, include_app_db: bool) -> dict:
        return {
            "passphrase": "pass1234",
            "include_runtime": False,
            "include_app_db": include_app_db,
            "include_standard_photos": False,
            "include_other_photos": False,
        }

    async def test_points_reports_postgres_capability(self):
        response = await self.client.get("/api/backup/points")
        body = response.json()
        self.assertEqual(body["backend"], "postgres")
        # 业务数据能进包，形态是整库 dump，只能覆盖恢复
        self.assertTrue(body["export_app_db_supported"])
        self.assertEqual(body["app_db_export_format"], "pgdump")
        self.assertEqual(body["app_db_restore_mode"], "overwrite_only")

    async def test_export_packs_pg_dump_member(self):
        """导出：业务数据成员是 app.pgdump，配方随它一起走。"""
        await self._seed_recipe()

        with mock.patch.object(
            backup_service, "export_pg_dump_to_file", side_effect=_fake_pg_dump
        ):
            response = await self.client.post(
                "/api/backup/export", json=self._export_body(include_app_db=True)
            )

        self.assertEqual(response.status_code, 200, response.text)
        parsed = backup_service.parse_backup(response.content, "pass1234")
        self.assertEqual(parsed["app_pg_bytes"], b"PGDMP-CONTENT")
        self.assertIsNone(parsed["app_db_bytes"])
        self.assertEqual(parsed["meta"]["includes"]["app_pg"], True)
        self.assertEqual(parsed["meta"]["includes"]["recipes_db"], True)

        points_response = await self.client.get("/api/backup/points")
        exported = [
            p
            for p in points_response.json()["points"]
            if p["medium"] == "export_backup"
        ]
        self.assertEqual(len(exported), 1)
        self.assertIn("app_pg", exported[0]["contents"])
        self.assertIn("recipes_db", exported[0]["contents"])
        self.assertTrue(exported[0]["recoverable"])

    async def test_no_sqlite_recipes_member_producer(self):
        """SQLite 时代的配方成员（recipes.db）不该再有产出方。"""
        self.assertFalse(hasattr(backup_service, "export_recipes_db_bytes"))
        self.assertFalse(hasattr(backup_service, "export_recipes_db_bytes_from_conn"))

    # ---- 导入：整库覆盖，旧 SQLite 包明确拒绝 ----

    def _pg_backup_blob(self) -> bytes:
        blob, _meta = backup_service.build_export_backup(
            "pass1234",
            include_runtime=False,
            runtime_data=None,
            include_app_db=True,
            app_pg_bytes=b"PGDUMP-CONTENT",
            include_standard_photos=False,
            include_other_photos=False,
            app_version="0.6.11",
        )
        return blob

    async def _preview(self, blob: bytes) -> dict:
        response = await self.client.post(
            "/api/backup/import/preview",
            files={"file": ("b.luyunbak", blob, "application/octet-stream")},
            data={"passphrase": "pass1234"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _apply_body(self, token: str, *, mode: str, apply_recipes: bool = False) -> dict:
        return {
            "import_token": token,
            "mode": mode,
            "apply_credentials": "false",
            "apply_runtime": "false",
            "apply_app_db": "true",
            "apply_recipes": "true" if apply_recipes else "false",
            "apply_standard_photos": "false",
            "apply_other_photos": "false",
            "force": "false",
        }

    async def test_pg_backup_preview_reports_pg_member_and_snapshot(self):
        preview = await self._preview(self._pg_backup_blob())
        self.assertTrue(preview["has_app_pg"])
        self.assertFalse(preview["has_sqlite_app_db"])
        self.assertTrue(preview["default_apply"]["app_pg"])
        # 会写库就要先建前置快照——少了 app_pg 这一项判断，整库包会被当成「不碰库」
        self.assertTrue(preview["pre_snapshot"]["will_create"])
        # 凭据是导出包必备成员，不该出现在「备份中不含」里
        self.assertNotIn("凭据", [m["label"] for m in preview["missing"]])

    async def test_pg_backup_merge_mode_is_rejected(self):
        preview = await self._preview(self._pg_backup_blob())
        response = await self.client.post(
            "/api/backup/import/apply",
            data=self._apply_body(preview["import_token"], mode="merge"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("覆盖", response.json()["detail"])

    async def test_recipes_without_business_data_is_rejected(self):
        """配方没有独立恢复路径：只勾配方、不恢复业务数据不能静默成功。"""
        preview = await self._preview(self._pg_backup_blob())
        body = self._apply_body(
            preview["import_token"], mode="overwrite", apply_recipes=True
        )
        body["apply_app_db"] = "false"
        response = await self.client.post("/api/backup/import/apply", data=body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("业务数据", response.json()["detail"])

    async def test_pg_backup_apply_goes_through_pg_restore(self):
        preview = await self._preview(self._pg_backup_blob())
        with mock.patch.object(
            backup_points, "create_pre_restore_snapshot", return_value="20260101_000000"
        ), mock.patch.object(
            backup_service,
            "restore_app_pg_from_bytes",
            new=mock.AsyncMock(),
        ) as restore:
            response = await self.client.post(
                "/api/backup/import/apply",
                data=self._apply_body(preview["import_token"], mode="overwrite"),
            )

        self.assertEqual(response.status_code, 200, response.text)
        restore.assert_awaited_once()
        self.assertEqual(restore.await_args.args[1], b"PGDUMP-CONTENT")
        self.assertTrue(response.json()["applied"]["app_pg"])

    async def test_legacy_sqlite_backup_is_rejected_with_guidance(self):
        """SQLite 时代的包（成员 app.db）灌不进 PostgreSQL：明确报错并给指引。"""
        preview = await self._preview(legacy_sqlite_backup())
        self.assertTrue(preview["has_sqlite_app_db"])
        self.assertFalse(preview["has_app_pg"])
        # 旧包没有独立的配方成员：默认勾选不会去恢复配方
        self.assertFalse(preview["default_apply"]["recipes_db"])

        response = await self.client.post(
            "/api/backup/import/apply",
            data=self._apply_body(preview["import_token"], mode="overwrite"),
        )
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertIn("SQLite", detail)
        self.assertIn("app.pgdump", detail)

    # ---- 任务式导出：进度可见 + 完成后才下载 ----

    async def _wait_job(self, job_id: str, timeout: float = 15.0) -> dict:
        """轮询导出任务：任务由端点 ``asyncio.create_task`` 起在同一个 loop 上。"""
        deadline = time.monotonic() + timeout
        state: dict = {}
        while time.monotonic() < deadline:
            response = await self.client.get(f"/api/backup/export/jobs/{job_id}")
            state = response.json()
            if state["state"] in ("done", "failed"):
                return state
            await asyncio.sleep(0.05)
        self.fail(f"导出任务超时未结束：{state}")

    async def test_export_job_reports_progress_then_downloads(self):
        await self._seed_recipe()

        with mock.patch.object(
            backup_service, "export_pg_dump_to_file", side_effect=_fake_pg_dump
        ):
            start = await self.client.post(
                "/api/backup/export/jobs", json=self._export_body(include_app_db=True)
            )
            self.assertEqual(start.status_code, 200, start.text)
            job_id = start.json()["job_id"]

            state = await self._wait_job(job_id)
            self.assertEqual(state["state"], "done", state)
            self.assertEqual(state["stage"], "done")
            self.assertGreater(state["bytes"], 0)
            self.assertTrue(state["name"].endswith(".luyunbak"))

            download = await self.client.get(
                f"/api/backup/export/jobs/{job_id}/download"
            )
            self.assertEqual(download.status_code, 200)
            parsed = backup_service.parse_backup(download.content, "pass1234")
            self.assertEqual(parsed["app_pg_bytes"], b"PGDMP-CONTENT")
            # 完成后的包同时登记成本机导出备份点
            points_response = await self.client.get("/api/backup/points")
            exported = [
                p
                for p in points_response.json()["points"]
                if p["medium"] == "export_backup"
            ]
            self.assertEqual(len(exported), 1)

    async def test_export_job_limits_concurrency_and_surfaces_failure(self):
        def _slow_failure(*args, **kwargs):
            time.sleep(0.4)
            raise ValueError("导出口令至少 6 位")

        with mock.patch.object(
            backup_service, "build_export_backup_to_file", side_effect=_slow_failure
        ):
            first = await self.client.post(
                "/api/backup/export/jobs", json=self._export_body(include_app_db=False)
            )
            self.assertEqual(first.status_code, 200)
            second = await self.client.post(
                "/api/backup/export/jobs", json=self._export_body(include_app_db=False)
            )
            self.assertEqual(second.status_code, 429)

            job_id = first.json()["job_id"]
            state = await self._wait_job(job_id)
            self.assertEqual(state["state"], "failed")
            self.assertIn("口令至少", state["error"])
            # 失败的任务不给下载
            download = await self.client.get(
                f"/api/backup/export/jobs/{job_id}/download"
            )
            self.assertEqual(download.status_code, 409)


if __name__ == "__main__":
    unittest.main()
