#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""恢复编排：前置快照、两层校验语义与恢复生效部分。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from api.backup import _apply_parsed_backup
from config import settings
from database import DatabaseManager
from services import backup_points, backup_retention, backup_service
from services.backup_service import (
    CONTENT_APP_DB,
    CONTENT_CREDENTIALS,
    CONTENT_OTHER_PHOTOS,
    CONTENT_STANDARD_PHOTOS,
    PHOTO_OTHER,
    PHOTO_STANDARD,
    PROVENANCE_PRE_IMPORT,
)
from services.credentials_store import CredentialBundle


def _bundle() -> CredentialBundle:
    return CredentialBundle(
        phone="13800000000",
        password="pw",
        shop_id="1",
        company_id="2",
        shop_name="LuckIn",
        delivery_shop_id="2",
    )


class RestoreFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._old_cold_dir = settings.COLD_BACKUP_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        settings.COLD_BACKUP_DIR = os.path.join(self._tmpdir.name, "backups")
        self._saved_retention = backup_retention.cache_get()
        backup_retention.cache_set(backup_retention.RetentionConfig())
        from services import credentials_store

        self._credentials_store = credentials_store
        self._saved_creds = credentials_store._cache
        credentials_store._cache = _bundle()
        (Path(self._tmpdir.name) / "credentials.enc").write_bytes(b"enc")

        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.capture_root = Path(self._tmpdir.name) / "hygiene-captures"
        self.capture_root.mkdir(parents=True, exist_ok=True)

    async def asyncTearDown(self):
        await self.db.close()
        self._credentials_store._cache = self._saved_creds
        backup_retention.cache_set(self._saved_retention)
        settings.DATABASE_DIR = self._old_database_dir
        settings.COLD_BACKUP_DIR = self._old_cold_dir
        self._tmpdir.cleanup()

    async def _seed_hygiene(self, *, standard=(), other=()):
        now = "2026-01-01T00:00:00"
        # Seeding references capture rows without their parent business rows; the
        # FK checks are irrelevant to what these tests assert.
        await self.db._conn.execute("PRAGMA foreign_keys=OFF")
        for capture_id in standard:
            await self.db._conn.execute(
                "INSERT INTO hygiene_standards (item_id, capture_id, content_type,"
                " created_at) VALUES (1, ?, 'image/jpeg', ?)",
                (capture_id, now),
            )
        for capture_id in other:
            await self.db._conn.execute(
                "INSERT INTO hygiene_daily_submissions (instance_id, capture_id,"
                " content_type, frozen_standard_id, submitter_id, submitter_phone,"
                " zone_name, captured_at, created_at)"
                " VALUES (1, ?, 'image/jpeg', 1, 1, '13800000000', 'zone', ?, ?)",
                (capture_id, now, now),
            )
        await self.db._conn.commit()
        await self.db._conn.execute("PRAGMA foreign_keys=ON")
        for capture_id in list(standard) + list(other):
            (self.capture_root / capture_id).write_bytes(b"photo-" + capture_id.encode())

    async def _app_db_bytes(self) -> bytes:
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            await self.db.export_merged_sqlite_file(tmp_path)
            with open(tmp_path, "rb") as handle:
                return handle.read()
        finally:
            os.unlink(tmp_path)

    async def _build_parsed(self, *, standard=(), other=(), declared_standard=None):
        app_bytes = await self._app_db_bytes()
        manifest = {
            PHOTO_STANDARD: {
                "count": len(standard) if declared_standard is None else declared_standard,
                "bytes": 1,
                "sha256": "s",
                "referenced": len(standard),
                "missing": 0,
            },
            PHOTO_OTHER: {
                "count": len(other),
                "bytes": 1,
                "sha256": "o",
                "referenced": len(other),
                "missing": 0,
            },
        }
        members = {}
        for name in standard:
            members[f"photos/standard/{name}"] = b"photo-" + name.encode()
        for name in other:
            members[f"photos/other/{name}"] = b"photo-" + name.encode()

        blob = backup_service.build_backup(
            "pass1234",
            include_runtime=False,
            runtime_data=None,
            include_app_db=True,
            app_db_bytes=app_bytes,
            include_recipes=False,
            recipes_db_bytes=None,
            include_standard_photos=bool(standard),
            include_other_photos=bool(other),
            photo_members=members,
            photo_manifest=manifest,
            app_version="0.1.0",
        )
        return backup_service.parse_backup(blob, "pass1234")

    async def _apply(self, parsed, **overrides):
        kwargs = dict(
            mode="overwrite",
            apply_credentials=False,
            apply_runtime=False,
            apply_app_db=True,
            apply_recipes=False,
            apply_standard_photos=True,
            apply_other_photos=True,
            force=False,
        )
        kwargs.update(overrides)
        return await _apply_parsed_backup(parsed, db=self.db, **kwargs)

    async def test_overwrite_import_creates_pre_snapshot_and_restores_photos(self):
        await self._seed_hygiene(standard=["s1"], other=["o1"])
        parsed = await self._build_parsed(standard=["s1"], other=["o1"])

        # Wipe the live photos to prove recovery actually writes them back.
        (self.capture_root / "s1").unlink()
        (self.capture_root / "o1").unlink()

        result = await self._apply(parsed)

        self.assertTrue(result["success"])
        self.assertTrue(result["applied"][CONTENT_APP_DB])
        self.assertTrue(result["applied"][CONTENT_STANDARD_PHOTOS])
        self.assertTrue(result["applied"][CONTENT_OTHER_PHOTOS])
        self.assertFalse(result["applied"][CONTENT_CREDENTIALS])
        self.assertIsNotNone(result["snapshot_ts"])
        self.assertTrue((self.capture_root / "s1").is_file())
        self.assertTrue((self.capture_root / "o1").is_file())

        snaps = {s["ts"]: s for s in backup_service.list_snapshots()}
        self.assertIn(result["snapshot_ts"], snaps)

    async def test_pre_snapshot_records_pre_import_provenance(self):
        await self._seed_hygiene(standard=["s1"])
        parsed = await self._build_parsed(standard=["s1"])
        result = await self._apply(parsed)
        snaps = {s["ts"]: s for s in backup_service.list_snapshots()}
        self.assertEqual(
            snaps[result["snapshot_ts"]]["provenance"], PROVENANCE_PRE_IMPORT
        )

    async def test_in_backup_inconsistency_blocks_restore(self):
        await self._seed_hygiene(standard=["s1"])
        parsed = await self._build_parsed(standard=["s1"], declared_standard=5)
        with self.assertRaises(HTTPException) as ctx:
            await self._apply(parsed)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["reason"], "backup_corrupt")
        self.assertEqual(backup_service.list_snapshots(), [])

    async def test_cross_point_difference_requires_force(self):
        await self._seed_hygiene(standard=["s1"], other=["o1", "o2"])
        # Backup carries s1 and o2 only; o1 is referenced by the live DB but absent.
        parsed = await self._build_parsed(standard=["s1"], other=["o2"])
        with self.assertRaises(HTTPException) as ctx:
            await self._apply(parsed)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["reason"], "photo_mismatch")

        result = await self._apply(parsed, force=True)
        self.assertTrue(result["success"])

    async def test_cross_point_difference_allows_deselecting_the_class(self):
        await self._seed_hygiene(standard=["s1"], other=["o1", "o2"])
        parsed = await self._build_parsed(standard=["s1"], other=["o2"])
        result = await self._apply(parsed, apply_other_photos=False)
        self.assertTrue(result["success"])
        self.assertFalse(result["applied"][CONTENT_OTHER_PHOTOS])

    async def test_restore_flags_library_photos_missing_on_disk(self):
        """换库不换照片的典型情形：恢复后库引用的照片目标机上并不存在。"""
        await self._seed_hygiene(standard=["s1"], other=["o1"])
        parsed = await self._build_parsed(standard=["s1"], other=["o1"])
        # 目标机没有这两张照片（跨机搬运 app.db、或文件已被删）
        (self.capture_root / "s1").unlink()
        (self.capture_root / "o1").unlink()

        with self.assertLogs("api.backup", level="WARNING") as logs:
            result = await self._apply(
                parsed,
                apply_standard_photos=False,
                apply_other_photos=False,
            )

        self.assertTrue(result["success"])
        consistency = result["photo_consistency"]
        self.assertFalse(consistency["ok"])
        self.assertEqual(consistency["standard_missing"], 1)
        self.assertEqual(consistency["other_missing"], 1)
        self.assertEqual(consistency["missing"][PHOTO_STANDARD], ["s1"])
        self.assertIn("恢复后照片不一致", "\n".join(logs.output))

    async def test_restore_reports_consistent_photos(self):
        await self._seed_hygiene(standard=["s1"])
        parsed = await self._build_parsed(standard=["s1"])

        result = await self._apply(parsed, apply_standard_photos=False)

        self.assertTrue(result["photo_consistency"]["ok"])
        self.assertEqual(result["photo_consistency"]["missing"], {})

    async def test_restore_without_writing_anything_skips_consistency_check(self):
        parsed = await self._build_parsed(standard=["s1"])

        result = await self._apply(
            parsed,
            apply_credentials=False,
            apply_app_db=False,
            apply_standard_photos=False,
            apply_other_photos=False,
        )

        self.assertIsNone(result["photo_consistency"])

    async def test_pre_snapshot_failure_refuses_restore(self):
        await self._seed_hygiene(standard=["s1"])
        parsed = await self._build_parsed(standard=["s1"])
        with mock.patch.object(
            backup_points,
            "create_pre_restore_snapshot",
            side_effect=RuntimeError("disk full"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._apply(parsed)
        self.assertEqual(ctx.exception.status_code, 500)

    async def test_legacy_export_without_photos_restores_and_is_flagged(self):
        await self._seed_hygiene(standard=["s1"], other=["o1"])
        app_bytes = await self._app_db_bytes()
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
        from api.backup import _missing_contents

        labels = {m["content"] for m in _missing_contents(parsed)}
        self.assertIn(CONTENT_STANDARD_PHOTOS, labels)
        self.assertIn(CONTENT_OTHER_PHOTOS, labels)

        # Old archives restore business data; photos are simply not touched.
        result = await self._apply(
            parsed, apply_standard_photos=False, apply_other_photos=False
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["applied"][CONTENT_APP_DB])


if __name__ == "__main__":
    unittest.main()
