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
    CONTENT_APP_PG,
    CONTENT_CREDENTIALS,
    CONTENT_OTHER_PHOTOS,
    CONTENT_STANDARD_PHOTOS,
    PHOTO_OTHER,
    PHOTO_STANDARD,
    PROVENANCE_PRE_IMPORT,
)
from services.credentials_store import CredentialBundle
from tests.backup_fixtures import seed_hygiene_photo_refs


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
        """照片引用插进**业务库**：分类与一致性检查都只认这条路径。"""
        await seed_hygiene_photo_refs(
            self.db._conn, standards=standard, others=other
        )
        for capture_id in list(standard) + list(other):
            (self.capture_root / capture_id).write_bytes(b"photo-" + capture_id.encode())

    async def _build_parsed(self, *, standard=(), other=(), declared_standard=None):
        app_bytes = b"PGDMP-CONTENT"
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
            app_pg_bytes=app_bytes,
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
        self.assertTrue(result["applied"][CONTENT_APP_PG])
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
        blob = backup_service.build_backup(
            "pass1234",
            include_runtime=False,
            runtime_data=None,
            include_app_db=True,
            app_pg_bytes=b"PGDMP-CONTENT",
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
        self.assertTrue(result["applied"][CONTENT_APP_PG])


class RestoreNotifiesLogStorageTest(unittest.IsolatedAsyncioTestCase):
    """整库恢复必须通知日志那条**独立**连接。

    ``services/log_storage.py`` 自己建了第二条 ``PgConnection``（理由见它的
    ``_connect``：日志是高频后台写入，不想和业务查询互相排队）。两条恢复路径
    （``api/backup.py`` 的快照回滚、``services/backup_service.py`` 的整库导入）
    都只关它们注入的那个 ``db``，**碰不到这条连接**。不通知的话，
    ``pg_restore --clean`` 掉 ``logs`` 表时它只能走「丢当批 + 置降级」，恢复完成后
    第一批日志白丢。这个缺口原先没有任何地方写明，所以在这里钉住。
    """

    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect(), "测试库连接失败")

    async def asyncTearDown(self):
        await self.db.close()

    async def test_restore_app_pg_from_bytes_notifies_log_storage(self):
        """真编排（只把 pg_restore 换成替身）必须通知日志换连接。

        真实现由 ``tests/conftest.py`` 的 ``_no_real_database_restore`` 另存为
        ``_real_restore_app_pg_from_bytes``——共享测试库不能被整库覆盖，所以模块
        属性被换成了 ``AsyncMock``；这里取回真身，只替换最里层的 ``pg_restore``。
        """
        calls = []

        async def _spy():
            calls.append(True)
            return True

        real = getattr(backup_service, "_real_restore_app_pg_from_bytes")
        with mock.patch.object(
            backup_service, "restore_pg_dump_sync", lambda _path: None
        ), mock.patch.object(backup_service, "notify_log_storage_reconnect", _spy):
            await real(self.db, b"not-a-real-dump")

        self.assertEqual(calls, [True], "整库恢复必须通知日志存储换连接")
        self.assertTrue(self.db.is_connected(), "恢复后业务连接必须接回来")

    async def test_notify_log_storage_reconnect_calls_through(self):
        """那个通知真的传到了 ``log_storage.reconnect()``。

        与上一条合起来构成完整链条：恢复编排 → 通知函数 → 日志那条独立连接。
        ``api/backup.py`` 的快照回滚走的是同一个通知函数，所以链路的这一半只需
        在这里钉一次。
        """
        calls = []

        async def _spy():
            calls.append(True)
            return True

        from services.log_storage import log_storage

        with mock.patch.object(log_storage, "reconnect", _spy):
            await backup_service.notify_log_storage_reconnect()

        self.assertEqual(calls, [True], "通知函数必须调用 log_storage.reconnect()")


if __name__ == "__main__":
    unittest.main()
