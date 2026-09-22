#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份点：统一清单、两层校验、保留清理、恢复前置快照与冷备状态。

照片引用（标准图 / 其它照片 / 派生图）现在只从**业务库**读：夹具直接往测试库里
插引用行（``tests/backup_fixtures.py``），不再造 ``data/app.db``。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import asyncpg

from config import settings
from database import DatabaseManager
from services import backup_points, backup_retention, backup_service
from services.backup_service import (
    CONTENT_APP_PG,
    CONTENT_CREDENTIALS,
    CONTENT_OTHER_PHOTOS,
    CONTENT_RUNTIME,
    CONTENT_STANDARD_PHOTOS,
    PHOTO_OTHER,
    PHOTO_STANDARD,
    PROVENANCE_MANUAL,
    PROVENANCE_PRE_UPDATE,
)
from services.credentials_store import CredentialBundle
from tests.backup_fixtures import seed_hygiene_photo_refs


def _sample_bundle() -> CredentialBundle:
    return CredentialBundle(
        phone="13800000000",
        password="s3cret-pw",
        shop_id="100001",
        company_id="200002",
        shop_name="LuckIn",
        delivery_shop_id="200002",
    )


def _fake_pg_dump(dst_path: str, *args, **kwargs) -> None:
    """替换 ``_pg_dump_sync``：这些用例只关心归档成员与结论，不真导整库。"""
    with open(dst_path, "wb") as handle:
        handle.write(b"PGDMP-FAKE")


class _FixtureMixin:
    """目录、凭据与导出包的公共夹具（同步/异步两个基类共用）。"""

    def _prepare(self) -> None:
        self._old_database_dir = settings.DATABASE_DIR
        self._old_cold_dir = settings.COLD_BACKUP_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        settings.COLD_BACKUP_DIR = os.path.join(self._tmpdir.name, "backups")
        self.capture_root = Path(self._tmpdir.name) / "hygiene-captures"
        self.capture_root.mkdir(parents=True, exist_ok=True)
        self._saved_cache = backup_retention.cache_get()
        backup_retention.cache_set(backup_retention.RetentionConfig())
        from services import credentials_store

        self._credentials_store = credentials_store
        self._saved_creds = credentials_store._cache
        credentials_store._cache = _sample_bundle()
        self._write_cred_file()

    def _cleanup(self) -> None:
        self._credentials_store._cache = self._saved_creds
        backup_retention.cache_set(self._saved_cache)
        backup_points.set_health_cache(None)
        settings.DATABASE_DIR = self._old_database_dir
        settings.COLD_BACKUP_DIR = self._old_cold_dir
        self._tmpdir.cleanup()

    def _write_cred_file(self) -> None:
        path = Path(self._tmpdir.name) / "credentials.enc"
        path.write_bytes(b"enc-blob")

    def _write_photo(self, capture_id: str, data: bytes = b"jpeg-bytes") -> None:
        (self.capture_root / capture_id).write_bytes(data)

    def _build_export(self, *, standard=(), other=(), declared_standard=None, app_pg=True):
        for capture_id in standard:
            self._write_photo(capture_id, b"standard-" + capture_id.encode())
        for capture_id in other:
            self._write_photo(capture_id, b"other-" + capture_id.encode())
        manifest = {
            PHOTO_STANDARD: {
                "count": len(standard) if declared_standard is None else declared_standard,
                "bytes": 10,
                "sha256": "s",
                "referenced": len(standard),
                "missing": 0,
            },
            PHOTO_OTHER: {
                "count": len(other),
                "bytes": 10,
                "sha256": "o",
                "referenced": len(other),
                "missing": 0,
            },
        }
        members = {}
        for name in standard:
            members[f"photos/standard/{name}"] = b"standard-" + name.encode()
        for name in other:
            members[f"photos/other/{name}"] = b"other-" + name.encode()
        blob, meta = backup_service.build_export_backup(
            "pass1234",
            include_runtime=False,
            runtime_data=None,
            include_app_db=app_pg,
            app_pg_bytes=b"PGDMP-CONTENT" if app_pg else None,
            include_standard_photos=bool(standard),
            include_other_photos=bool(other),
            photo_members=members,
            photo_manifest=manifest,
            app_version="0.1.0",
        )
        return blob, meta

    def _make_snapshot(self, provenance=PROVENANCE_MANUAL, *, ts=None):
        with mock.patch.object(
            backup_service, "_prune_old_snapshots"
        ), mock.patch.object(backup_service, "_pg_dump_sync", side_effect=_fake_pg_dump):
            real_ts = backup_service.create_restore_snapshot(
                str(Path(self._tmpdir.name) / "credentials.enc"),
                provenance=provenance,
            )
        if ts:
            old = backup_service._snapshot_root() / real_ts
            new = backup_service._snapshot_root() / ts
            if old != new:
                shutil.move(str(old), str(new))
            real_ts = ts
        return real_ts


class BackupPointTestCase(_FixtureMixin, unittest.TestCase):
    def setUp(self):
        self._prepare()

    def tearDown(self):
        self._cleanup()


class AsyncBackupPointTestCase(_FixtureMixin, unittest.IsolatedAsyncioTestCase):
    """需要业务库连接的用例：照片分类与跨备份点差异都经 ``db`` 查询。"""

    async def asyncSetUp(self):
        self._prepare()
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()
        self._cleanup()

    async def _seed_hygiene(self, *, standards=(), others=(), variants=()) -> None:
        await seed_hygiene_photo_refs(
            self.db._conn, standards=standards, others=others, variants=variants
        )


class ExportContentsTest(BackupPointTestCase):
    """凭据是构建端无条件打包的：内容清单必须如实反映，否则列表会误报「缺凭据」。"""

    def _write_export(self, blob: bytes, meta: dict, name: str):
        archive_dir = backup_service._export_root()
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive = archive_dir / name
        archive.write_bytes(blob)
        backup_service.write_export_sidecar(archive, meta)
        return archive

    def test_export_point_counts_credentials(self):
        blob, meta = self._build_export()
        self._write_export(blob, meta, "luyun_backup_20260101_000000.luyunbak")

        points = [
            p for p in backup_points.list_backup_points() if p["medium"] == "export_backup"
        ]
        self.assertEqual(len(points), 1)
        self.assertIn("credentials", points[0]["contents"])
        # 包里带了业务数据（整库 dump），凭据也在——不该有任何"缺失"
        self.assertNotIn("凭据", [m["label"] for m in points[0]["missing"]])

    def test_export_contents_helper_keeps_other_categories(self):
        contents = backup_points.export_contents({"runtime": True, "app_pg": True})
        self.assertEqual(contents[0], "credentials")
        self.assertIn("app_pg", contents)
        self.assertIn("runtime", contents)


class PhotoClassificationTest(AsyncBackupPointTestCase):
    async def test_two_classes_and_variants_follow_source(self):
        await self._seed_hygiene(
            standards=["s1", "s2"],
            others=["o1"],
            variants=[("s1", "s1_thumb"), ("o1", "o1_thumb")],
        )
        classified = await backup_service.classify_hygiene_capture_ids(self.db)
        self.assertEqual(classified[PHOTO_STANDARD], ["s1", "s1_thumb", "s2"])
        self.assertEqual(classified[PHOTO_OTHER], ["o1", "o1_thumb"])

    async def test_standard_wins_when_a_photo_is_referenced_by_both(self):
        await self._seed_hygiene(standards=["both"], others=["both", "only-other"])

        classified = await backup_service.classify_hygiene_capture_ids(self.db)

        self.assertEqual(classified[PHOTO_STANDARD], ["both"])
        self.assertEqual(classified[PHOTO_OTHER], ["only-other"])

    async def test_missing_hygiene_tables_are_not_an_error(self):
        """表不存在（旧库 / 未启用卫生模块）按「没有这类照片」处理，而不是 500。"""

        class _MissingTableConn:
            async def execute(self, sql, params=()):
                raise asyncpg.UndefinedTableError('relation "hygiene_x" does not exist')

        classified = await backup_service.classify_hygiene_capture_ids(_MissingTableConn())

        self.assertEqual(classified[PHOTO_STANDARD], [])
        self.assertEqual(classified[PHOTO_OTHER], [])


class ExportPhotoMemberTest(BackupPointTestCase):
    def test_export_carries_both_classes_and_manifest(self):
        blob, meta = self._build_export(standard=["s1"], other=["o1"])
        parsed = backup_service.parse_backup(blob, "pass1234")
        self.assertEqual(set(parsed["standard_photos"]), {"s1"})
        self.assertEqual(set(parsed["other_photos"]), {"o1"})
        self.assertTrue(meta["includes"]["standard_photos"])
        self.assertTrue(meta["includes"]["other_photos"])
        self.assertEqual(meta["photos"][PHOTO_STANDARD]["count"], 1)
        self.assertEqual(parsed["archive_integrity_errors"], [])

    def test_manifest_mismatch_is_in_backup_inconsistency(self):
        blob, _meta = self._build_export(standard=["s1"], declared_standard=3)
        parsed = backup_service.parse_backup(blob, "pass1234")
        self.assertTrue(parsed["archive_integrity_errors"])

    def test_legacy_v2_backup_parses_without_photo_members(self):
        blob = backup_service.build_backup(
            "pass1234",
            include_runtime=False,
            runtime_data=None,
            include_app_db=False,
            app_version="0.1.0",
        )
        parsed = backup_service.parse_backup(blob, "pass1234")
        self.assertEqual(parsed["standard_photos"], {})
        self.assertEqual(parsed["other_photos"], {})
        self.assertFalse(parsed["meta"]["includes"].get("standard_photos"))


class PhotoRestoreTest(BackupPointTestCase):
    def test_restore_only_requested_class(self):
        self._write_photo("s1", b"S")
        self._write_photo("o1", b"O")
        backup_points.restore_photos({}, {})
        (self.capture_root / "s1").unlink()
        (self.capture_root / "o1").unlink()

        counts = backup_points.restore_photos({"s1": b"S"}, {})
        self.assertEqual(counts[PHOTO_STANDARD], 1)
        self.assertTrue((self.capture_root / "s1").is_file())
        self.assertFalse((self.capture_root / "o1").is_file())

    def test_restore_does_not_delete_existing_photos(self):
        self._write_photo("keep", b"K")
        backup_points.restore_photos({}, {"new": b"N"})
        self.assertTrue((self.capture_root / "keep").is_file())


class CrossPointDifferenceTest(AsyncBackupPointTestCase):
    async def test_difference_reports_current_only_photos(self):
        await self._seed_hygiene(standards=["s1"], others=["o1"])
        diff = await backup_points.photo_difference(
            {PHOTO_STANDARD: ["s1"], PHOTO_OTHER: []}, db=self.db
        )
        self.assertTrue(diff["has_difference"])
        self.assertEqual(diff["missing"][PHOTO_OTHER], ["o1"])
        self.assertEqual(diff["standard_missing"], 0)

    async def test_no_difference_when_backup_covers_current(self):
        await self._seed_hygiene(standards=["s1"], others=["o1"])
        diff = await backup_points.photo_difference(
            {PHOTO_STANDARD: ["s1"], PHOTO_OTHER: ["o1"]}, db=self.db
        )
        self.assertFalse(diff["has_difference"])

    async def test_difference_without_db_is_rejected(self):
        """拒绝静默通过：没有 db 就不该给出「没有差异」的结论。"""
        with self.assertRaises(ValueError):
            await backup_points.photo_difference({PHOTO_STANDARD: [], PHOTO_OTHER: []})


class SnapshotProvenanceTest(AsyncBackupPointTestCase):
    async def test_snapshot_records_provenance_contents_and_photos(self):
        await self._seed_hygiene(standards=["s1"], others=["o1"])
        self._write_photo("s1", b"S")
        self._write_photo("o1", b"O")
        ts = self._make_snapshot(PROVENANCE_PRE_UPDATE)

        meta = json.loads(
            (backup_service._snapshot_root() / ts / "snapshot_meta.json").read_text()
        )
        self.assertEqual(meta["provenance"], PROVENANCE_PRE_UPDATE)
        self.assertIn(CONTENT_APP_PG, meta["contents"])
        self.assertIn(CONTENT_CREDENTIALS, meta["contents"])
        self.assertIn(CONTENT_STANDARD_PHOTOS, meta["contents"])
        self.assertIn(CONTENT_OTHER_PHOTOS, meta["contents"])

    async def test_snapshot_photos_are_linked_not_copied_when_possible(self):
        await self._seed_hygiene(standards=["s1"])
        self._write_photo("s1", b"S")
        ts = self._make_snapshot()
        snap_photo = backup_service._snapshot_root() / ts / "photos/standard/s1"
        self.assertTrue(snap_photo.is_file())
        self.assertEqual(
            snap_photo.stat().st_ino,
            (self.capture_root / "s1").stat().st_ino,
        )

    async def test_snapshot_flags_missing_photo_as_inconsistent(self):
        """库引用了照片、磁盘上没有：快照照建，但结论必须报出来。"""
        await self._seed_hygiene(standards=["gone-1"])
        ts = self._make_snapshot()
        listed = {s["ts"]: s for s in backup_service.list_snapshots()}[ts]
        self.assertFalse(listed["consistency"]["ok"])
        self.assertTrue(listed["consistency"]["errors"])
        self.assertEqual(
            listed["photos_missing"][PHOTO_STANDARD], ["gone-1"]
        )


class RetentionCleanupTest(BackupPointTestCase):
    def test_validation_rejects_both_one(self):
        with self.assertRaises(ValueError):
            backup_retention.validate_retention({"snapshot_keep": 1, "cold_keep": 1})

    def test_validation_enforces_upper_bounds(self):
        with self.assertRaises(ValueError):
            backup_retention.validate_retention({"snapshot_keep": 21, "cold_keep": 14})
        with self.assertRaises(ValueError):
            backup_retention.validate_retention({"snapshot_keep": 5, "cold_keep": 91})
        with self.assertRaises(ValueError):
            backup_retention.validate_retention({"export_keep": 21})

    def test_export_retention_bounds_local_copies(self):
        archive_dir = backup_service._export_root()
        archive_dir.mkdir(parents=True, exist_ok=True)
        for index in range(4):
            name = f"luyun_backup_20260101_00000{index}.luyunbak"
            (archive_dir / name).write_bytes(b"archive")
            backup_service.export_sidecar_path(archive_dir / name).write_text(
                '{"created_at": "2026-01-01T00:00:0%d+08:00"}' % index,
                encoding="utf-8",
            )

        config = backup_retention.RetentionConfig(
            snapshot_keep=5, export_keep=2, cold_keep=14
        )
        preview = backup_points.cleanup_preview(config)
        delete_names = [d["name"] for d in preview["export"]["delete"]]
        self.assertEqual(len(delete_names), 2)
        backup_points.apply_cleanup(config)
        remaining = sorted(p.name for p in archive_dir.glob("*.luyunbak"))
        self.assertEqual(len(remaining), 2)
        for name in delete_names:
            self.assertFalse(backup_service.export_sidecar_path(archive_dir / name).exists())

    def test_preview_and_apply_delete_the_same_points(self):
        for ts in ("20260101_000001", "20260101_000002", "20260101_000003"):
            self._make_snapshot(ts=ts)
        self._make_snapshot(PROVENANCE_PRE_UPDATE, ts="20260101_000004")
        # pre_update is newest here; add one newer manual snapshot so the
        # protected "newest" and the protected pre_update are different rows.
        self._make_snapshot(ts="20260101_000005")

        config = backup_retention.RetentionConfig(snapshot_keep=2, cold_keep=14)
        preview = backup_points.cleanup_preview(config)
        delete_ids = [d["ts"] for d in preview["snapshot"]["delete"]]
        protected = {p["ts"] for p in preview["snapshot"]["protected"]}
        self.assertIn("20260101_000004", protected)
        self.assertIn("20260101_000005", protected)
        self.assertNotIn("20260101_000004", delete_ids)

        backup_points.apply_cleanup(config)
        remaining = {s["ts"] for s in backup_service.list_snapshots()}
        self.assertEqual(remaining, protected)
        for ts in delete_ids:
            self.assertFalse((backup_service._snapshot_root() / ts).exists())

    def test_recent_backup_is_never_deleted(self):
        self._make_snapshot(ts="20260101_000001")
        config = backup_retention.RetentionConfig(snapshot_keep=1, cold_keep=14)
        preview = backup_points.cleanup_preview(config)
        self.assertEqual(preview["snapshot"]["delete"], [])
        self.assertEqual(
            [p["ts"] for p in preview["snapshot"]["protected"]], ["20260101_000001"]
        )

    def test_preview_kept_set_equals_survivors_with_scattered_protected(self):
        """删除预览必须与实际删除完全一致，含受保护项分散在旧端的情形。"""
        for index in range(1, 9):
            ts = f"20260101_0000{index:02d}"
            provenance = PROVENANCE_PRE_UPDATE if index in (2, 8) else PROVENANCE_MANUAL
            self._make_snapshot(provenance, ts=ts)

        config = backup_retention.RetentionConfig(snapshot_keep=5, cold_keep=14)
        preview = backup_points.cleanup_preview(config)
        preview_kept = set(preview["snapshot"]["kept"])
        preview_delete = {d["ts"] for d in preview["snapshot"]["delete"]}
        self.assertTrue(preview_kept.isdisjoint(preview_delete))
        # 更新前快照永不进入删除集合
        self.assertNotIn("20260101_000002", preview_delete)
        self.assertNotIn("20260101_000008", preview_delete)

        backup_points.apply_cleanup(config)
        survivors = {s["ts"] for s in backup_service.list_snapshots()}
        self.assertEqual(survivors, preview_kept)
        for ts in preview_delete:
            self.assertFalse((backup_service._snapshot_root() / ts).exists())


class ColdBackupTest(BackupPointTestCase):
    def _build_cold(self, **kwargs):
        with mock.patch.object(backup_service, "_pg_dump_sync", side_effect=_fake_pg_dump):
            return backup_service.build_cold_backup_archive(**kwargs)

    def test_archive_and_status_round_trip(self):
        self._write_photo("s1", b"S")
        self._write_photo("o1", b"O")
        key_path = Path(self._tmpdir.name) / ".cred_key"
        key_path.write_bytes(b"fernet-key")

        archive, manifest = self._build_cold(app_version="0.1.0")
        self.assertTrue(archive.is_file())
        self.assertEqual(archive.name, backup_service.COLD_ARCHIVE_NAME)
        self.assertIn(CONTENT_APP_PG, manifest["contents"])
        # 冷备跑在独立进程里：照片按目录全量带走并计入「其它照片」，不查库分类。
        self.assertIn(CONTENT_OTHER_PHOTOS, manifest["contents"])
        self.assertEqual(manifest["photos"][PHOTO_STANDARD]["count"], 0)
        self.assertEqual(manifest["photos"][PHOTO_OTHER]["count"], 2)

        status = backup_service.write_cold_backup_status(
            ok=True, archive=archive, manifest=manifest
        )
        self.assertTrue(status["ok"])

        points = [p for p in backup_points.list_backup_points() if p["medium"] == "cold_backup"]
        self.assertEqual(len(points), 1)
        self.assertTrue(points[0]["recoverable"])
        self.assertFalse(points[0]["restorable"])
        self.assertIn(CONTENT_OTHER_PHOTOS, points[0]["contents"])

    def test_status_file_missing_falls_back_to_scan(self):
        archive, _manifest = self._build_cold()
        backup_service.cold_status_path().unlink(missing_ok=True)

        points = [p for p in backup_points.list_backup_points() if p["medium"] == "cold_backup"]
        self.assertEqual(len(points), 1)
        self.assertFalse(points[0]["detail"]["reported"])
        # 扫描兜底分支的 contents 必须是内容代码：填中文标签会把业务数据自己标成缺失
        self.assertEqual(points[0]["contents"], [CONTENT_APP_PG])
        self.assertNotIn(CONTENT_APP_PG, {m["content"] for m in points[0]["missing"]})

    def test_failed_run_is_reported_as_not_recoverable(self):
        """脚本失败时 archive=None；失败运行仍必须作为一条冷备结论出现。"""
        backup_service.write_cold_backup_status(
            ok=False, archive=None, error="pg_dump failed"
        )
        points = [p for p in backup_points.list_backup_points() if p["medium"] == "cold_backup"]
        self.assertEqual(len(points), 1)
        self.assertFalse(points[0]["recoverable"])
        self.assertTrue(points[0]["detail"]["reported"])
        self.assertIn("失败", " ".join(points[0]["basic_check"]["messages"]))
        self.assertIn("pg_dump failed", " ".join(points[0]["basic_check"]["messages"]))

        health = backup_points.compute_backup_health()
        self.assertNotEqual(health["status"], "ok")
        self.assertEqual(health["counts"]["unusable"], 1)

    def test_failed_run_still_lists_older_archives(self):
        good_archive, manifest = self._build_cold()
        backup_service.write_cold_backup_status(
            ok=True, archive=good_archive, manifest=manifest
        )
        # A later run fails; the previous good archive must stay visible.
        backup_service.write_cold_backup_status(
            ok=False, archive=None, error="newer run failed"
        )
        points = [p for p in backup_points.list_backup_points() if p["medium"] == "cold_backup"]
        self.assertEqual(len(points), 2)
        by_reported = {p["detail"]["reported"]: p for p in points}
        self.assertFalse(by_reported[True]["recoverable"])
        self.assertTrue(by_reported[False]["recoverable"])

    def test_corrupt_cold_archive_is_not_recoverable_without_status(self):
        archive, _manifest = self._build_cold()
        archive.write_bytes(b"corrupted archive bytes")
        backup_service.cold_status_path().unlink(missing_ok=True)

        points = [p for p in backup_points.list_backup_points() if p["medium"] == "cold_backup"]
        self.assertEqual(len(points), 1)
        self.assertFalse(points[0]["recoverable"])
        self.assertIn("无法打开", " ".join(points[0]["basic_check"]["messages"]))

    def test_cold_cleanup_preview_covers_every_archive_on_disk(self):
        backup_dir = backup_service.get_cold_backup_dir()
        for ts in ("20260101_000001", "20260101_000002", "20260101_000003"):
            run_dir = backup_dir / ts
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / backup_service.COLD_ARCHIVE_NAME).write_bytes(b"archive")

        config = backup_retention.RetentionConfig(snapshot_keep=5, cold_keep=2)
        preview = backup_points.cleanup_preview(config)
        delete_ts = [d["ts"] for d in preview["cold"]["delete"]]
        self.assertEqual(delete_ts, ["20260101_000001"])
        self.assertEqual(
            [p["ts"] for p in preview["cold"]["protected"]], ["20260101_000003"]
        )

        backup_points.apply_cleanup(config)
        remaining = sorted(d.name for d in backup_dir.iterdir() if d.is_dir())
        self.assertEqual(remaining, ["20260101_000002", "20260101_000003"])


class BackupHealthTest(BackupPointTestCase):
    def test_no_backup_conclusion(self):
        health = backup_points.compute_backup_health()
        self.assertEqual(health["status"], "no_backup")
        self.assertFalse(health["checks"][0]["ok"])

    def test_legacy_snapshot_only_reports_legacy(self):
        self._make_snapshot()
        health = backup_points.compute_backup_health()
        self.assertIn(health["status"], {"legacy_only", "ok"})
        self.assertTrue(health["last_success_at"])

    def test_ok_when_export_covers_photos(self):
        blob, meta = self._build_export(standard=["s1"], other=["o1"])
        archive = backup_service._export_root() / "luyun_backup_20260101_000000.luyunbak"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(blob)
        backup_service.write_export_sidecar(archive, meta)

        health = backup_points.compute_backup_health()
        self.assertEqual(health["status"], "ok")
        self.assertIn(CONTENT_STANDARD_PHOTOS, health["coverage"])
        self.assertIn(CONTENT_OTHER_PHOTOS, health["coverage"])

    def test_corrupt_export_is_unusable(self):
        blob, meta = self._build_export()
        archive = backup_service._export_root() / "luyun_backup_20260101_000000.luyunbak"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(blob + b"tampered")
        backup_service.write_export_sidecar(archive, meta)

        points = [p for p in backup_points.list_backup_points() if p["medium"] == "export_backup"]
        self.assertFalse(points[0]["recoverable"])
        self.assertIn("校验和", " ".join(points[0]["basic_check"]["messages"]))

    def test_invalidate_cache_forces_recompute_on_next_read(self):
        """导出 / 恢复 / 回滚后只失效缓存，让下一次读取重算，而不是一直返回旧结论。"""
        cached = backup_points.refresh_backup_health()
        self.assertEqual(cached["status"], "no_backup")
        self.assertIsNotNone(backup_points.get_health_cache())

        # 磁盘上多了一份可用备份点，但缓存还在
        self._make_snapshot()
        self.assertEqual(backup_points.get_health_cache()["status"], "no_backup")

        backup_points.invalidate_health_cache()
        self.assertIsNone(backup_points.get_health_cache())
        recomputed = backup_points.refresh_backup_health()
        self.assertNotEqual(recomputed["status"], "no_backup")


class ExportPointListingTest(BackupPointTestCase):
    def test_missing_summary_marks_absent_classes(self):
        blob, meta = self._build_export()
        archive = backup_service._export_root() / "luyun_backup_20260101_000000.luyunbak"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(blob)
        backup_service.write_export_sidecar(archive, meta)

        point = [
            p for p in backup_points.list_backup_points() if p["medium"] == "export_backup"
        ][0]
        missing = {m["content"] for m in point["missing"]}
        self.assertIn(CONTENT_STANDARD_PHOTOS, missing)
        self.assertIn(CONTENT_OTHER_PHOTOS, missing)
        self.assertEqual(point["provenance"], PROVENANCE_MANUAL)


class PgSnapshotPointTest(BackupPointTestCase):
    """PG 快照的内容清单 / 基础校验 / 照片缺失语义。"""

    def test_pg_contents_do_not_report_business_or_recipes_missing(self):
        missing = backup_points.missing_contents(
            [CONTENT_APP_PG, CONTENT_RUNTIME, CONTENT_CREDENTIALS]
        )
        labels = [item["label"] for item in missing]

        # 整库 pg_dump 同时覆盖业务数据与配方数据
        self.assertNotIn("业务数据", labels)
        self.assertNotIn("配方数据", labels)
        # 照片确实不在里面
        self.assertIn("标准图", labels)
        self.assertIn("其它照片", labels)

    def test_pg_dump_snapshot_is_not_reported_as_credentials_only(self):
        ts = "20260919_051032"
        snap = backup_service._snapshot_root() / ts
        snap.mkdir(parents=True, exist_ok=True)
        dump = snap / "app.pgdump"
        dump.write_bytes(b"PGDMP-fake")

        check = backup_points._snapshot_basic_check(
            {"ts": ts, "size_bytes": dump.stat().st_size}
        )

        self.assertTrue(check["ok"])
        messages = " ".join(check["messages"])
        self.assertIn("pg_restore", messages)
        self.assertNotIn("仅含凭据", messages)

    def test_empty_pg_dump_is_not_recoverable(self):
        ts = "20260919_051033"
        snap = backup_service._snapshot_root() / ts
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "app.pgdump").write_bytes(b"")

        check = backup_points._snapshot_basic_check({"ts": ts, "size_bytes": 1})

        self.assertFalse(check["ok"])
        self.assertIn("体积为零", " ".join(check["messages"]))

    def test_legacy_sqlite_snapshot_is_not_recoverable(self):
        """SQLite 时代的快照文件还在，但灌不进 PostgreSQL：必须明确判负。"""
        ts = "20260919_051034"
        snap = backup_service._snapshot_root() / ts
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "app.db").write_bytes(b"SQLite format 3\x00")

        check = backup_points._snapshot_basic_check({"ts": ts, "size_bytes": 16})

        self.assertFalse(check["ok"])
        self.assertIn("无法恢复", " ".join(check["messages"]))

    def test_source_missing_photos_warn_instead_of_blocking(self):
        merged = backup_points._merge_in_backup_consistency(
            {"ok": True, "messages": []},
            {
                "ok": False,
                "blocking": False,
                "errors": ["标准图有 2 个文件在源磁盘上已缺失（备份里没有，恢复后仍缺）"],
            },
        )

        self.assertTrue(merged["ok"])
        self.assertEqual(len(merged["warnings"]), 1)
        self.assertIn("源磁盘", merged["warnings"][0])

    def test_blocking_inconsistency_still_blocks(self):
        merged = backup_points._merge_in_backup_consistency(
            {"ok": True, "messages": []},
            {"ok": False, "blocking": True, "errors": ["归档校验和不一致"]},
        )

        self.assertFalse(merged["ok"])
        self.assertIn("归档校验和不一致", merged["messages"])


if __name__ == "__main__":
    unittest.main()
