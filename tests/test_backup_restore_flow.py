#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""恢复编排：前置快照、两层校验语义与恢复生效部分。"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from api import backup as backup_api
from api.backup import _apply_parsed_backup
from config import settings
from database import DatabaseManager
from services import backup_import_staging, backup_points, backup_retention, backup_service
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


    async def test_upload_restore_reports_unreconnected_database(self):
        """DATA-01：上传恢复接不回连接时，响应必须点名「已恢复但连接未接回」。

        以前 ``restore_app_pg_from_bytes`` 收尾的 ``await db.connect()`` 丢返回值，
        重连失败也照样返回「恢复成功」；用户只会看到「请稍后重试」，而重试永远
        不会自愈（进程停在半恢复状态）。现在它必须是一个说得出口的失败。
        """
        parsed = await self._build_parsed()

        async def _boom(_db, _payload):
            raise backup_service.RestoreReconnectError(
                "整库恢复已执行，但数据库连接未能接回"
            )

        with mock.patch.object(backup_service, "restore_app_pg_from_bytes", _boom):
            with self.assertRaises(HTTPException) as ctx:
                await self._apply(parsed)

        self.assertEqual(ctx.exception.status_code, 500)
        detail = str(ctx.exception.detail)
        self.assertIn("连接未能接回", detail)
        self.assertIn("已恢复", detail)
        self.assertIn("重启应用", detail)
        self.assertNotIn("成功", detail, "重连失败绝不能报成功")

    async def test_concurrent_restore_is_rejected_with_409(self):
        """CORR-07：恢复/导入互斥——第二个请求 409，且不让 pg_restore 跑。

        互斥只做进程内（``_restore_guard``）：单 worker 部署下这已经覆盖了
        真实的并发入口，跨进程并发由部署形态排除（见 deploy/README.md）。
        """
        parsed = await self._build_parsed()
        owner = "sess-corr07"
        token = backup_import_staging.create_staging(owner, parsed)

        with backup_api._restore_exclusive("快照回滚"):
            with self.assertRaises(HTTPException) as ctx:
                await backup_api.import_backup_apply(
                    import_token=token,
                    mode="overwrite",
                    session_id=owner,
                    db=self.db,
                )

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("已有恢复任务在执行", str(ctx.exception.detail))
        self.assertEqual(
            getattr(backup_service.restore_app_pg_from_bytes, "await_count", 0),
            0,
            "被拒绝的恢复请求不得执行任何 pg_restore",
        )
        self.assertFalse(backup_api._restore_guard.in_progress)
        self.assertIsNone(backup_api._restore_guard.holder)
        # 被拒的请求没消费 staging token，用户解决冲突后还能重试。
        self.assertIsNotNone(backup_import_staging.load_parsed_from_staging(token, owner))

    # ---- 独立验证（verifier-db / V2）----
    # 上面 test_upload_restore_reports_unreconnected_database 把 restore_pg_dump_sync
    # 替成了 noop，只覆盖「恢复成功、但接不回连接」。下面两条不替 pg_restore 本身：
    # 恢复前的快照照旧用真库做完，随后把库换成「连不上」，让 pg_restore 与紧跟着的
    # db.connect() 都真的失败——这是 DATA-01 在生产里最常见的形态（库连不上），
    # 也是「响应会不会谎报」的真正分水岭。

    _DEAD_DSN = "postgresql://localhost:5432/luyun_ver2_no_such_db"

    async def _apply_with_dead_dsn_at_pg_restore_time(self):
        """恢复前置快照照旧（真库），到 pg_restore 那一刻把库换成连不上。

        返回 ``HTTPException``（导入路径的响应）。
        """
        real = getattr(backup_service, "_real_restore_app_pg_from_bytes")
        real_dump = backup_service.restore_pg_dump_sync
        dead_dsn = self._DEAD_DSN

        def _dump_against_dead_dsn(path):
            # pg_restore 读 settings.POSTGRES_DSN（每次现读），这里换成死 DSN：
            # 子进程真的起不来，随后 restore_app_pg_from_bytes 里的 db.connect()
            # 也会真的失败。
            settings.POSTGRES_DSN = dead_dsn
            return real_dump(path)

        async def _noop_notify():
            return True

        with mock.patch.object(
            backup_service, "restore_app_pg_from_bytes", real
        ), mock.patch.object(
            backup_service, "restore_pg_dump_sync", _dump_against_dead_dsn
        ), mock.patch.object(
            backup_service, "notify_log_storage_reconnect", _noop_notify
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._apply(await self._build_parsed())
        return ctx.exception

    async def _restore_real_with_dead_dsn_at_pg_restore_time(self):
        """同上，但直接调真 ``restore_app_pg_from_bytes``，不经过导入路径。"""
        real = getattr(backup_service, "_real_restore_app_pg_from_bytes")
        real_dump = backup_service.restore_pg_dump_sync
        dead_dsn = self._DEAD_DSN

        def _dump_against_dead_dsn(path):
            settings.POSTGRES_DSN = dead_dsn
            return real_dump(path)

        async def _noop_notify():
            return True

        with mock.patch.object(
            backup_service, "restore_pg_dump_sync", _dump_against_dead_dsn
        ), mock.patch.object(
            backup_service, "notify_log_storage_reconnect", _noop_notify
        ):
            await real(self.db, b"not-a-real-dump")

    async def test_real_pg_restore_failure_is_not_hidden_by_the_reconnect_failure(self):
        """真编排：pg_restore 的失败必须留在异常链里，不能被重连失败吞掉。"""
        original_dsn = settings.POSTGRES_DSN
        try:
            with self.assertRaises(Exception) as ctx:
                await self._restore_real_with_dead_dsn_at_pg_restore_time()
            self.assertFalse(
                self.db.is_connected(), "重连失败之后连接必须是断的（半恢复状态）"
            )
        finally:
            settings.POSTGRES_DSN = original_dsn
            await self.db.connect()

        chain = []
        exc = ctx.exception
        while exc is not None and len(chain) < 5:
            chain.append(exc)
            exc = exc.__cause__ or exc.__context__
        self.assertTrue(
            any("pg_restore" in str(item) for item in chain),
            "pg_restore 从未成功，这个事实却不在异常链里："
            f"{[type(item).__name__ for item in chain]}",
        )

    async def test_real_both_failed_restore_must_not_claim_the_data_was_restored(self):
        """真编排：pg_restore 失败 + 重连失败 ⇒ 响应不得声称「整库数据已恢复」。

        ``_rollback_snapshot_locked``（同一次修复的另一条入口）在这种形态下说的是
        「PostgreSQL 整库恢复失败，且数据库连接未能接回…库可能已被部分改写」；
        上传/导入这条入口却经由 ``RestoreReconnectError`` 固定输出「整库数据已恢复…
        重试前不需要再恢复一次」。对一次都没恢复成功的库说这句，会让运维直接跳过
        重新恢复。
        """
        original_dsn = settings.POSTGRES_DSN
        try:
            exc = await self._apply_with_dead_dsn_at_pg_restore_time()
        finally:
            settings.POSTGRES_DSN = original_dsn
            await self.db.connect()
        self.assertEqual(exc.status_code, 500)
        detail = str(exc.detail)
        self.assertFalse(
            "整库数据已恢复" in detail or "整库已恢复" in detail,
            "pg_restore 未曾成功（库连不上、什么都没改写），响应却声称数据已恢复："
            f"{detail}",
        )
        self.assertIn("连接", detail, f"文案至少要讲清连接没接回：{detail}")


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


class RestoreReconnectFailureTest(unittest.IsolatedAsyncioTestCase):
    """DATA-01：整库恢复之后接不回连接，编排必须抛出，而不是假报成功。"""

    async def asyncSetUp(self):
        self._old_dsn = settings.POSTGRES_DSN
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect(), "测试库连接失败")

    async def asyncTearDown(self):
        settings.POSTGRES_DSN = self._old_dsn
        # 用例把 DSN 换成了不存在的库，收尾时先接回真身，别把后面的用例带偏。
        await self.db.connect()
        await self.db.close()

    async def test_restore_raises_when_reconnect_fails(self):
        """真编排（只替 pg_restore）：重连失败必须变成 RestoreReconnectError。"""

        async def _noop():
            return True

        real = getattr(backup_service, "_real_restore_app_pg_from_bytes")
        with mock.patch.object(
            settings,
            "POSTGRES_DSN",
            "postgresql://localhost:5432/luyun_no_such_db_20260923",
        ), mock.patch.object(
            backup_service, "restore_pg_dump_sync", lambda _path: None
        ), mock.patch.object(
            backup_service, "notify_log_storage_reconnect", _noop
        ), mock.patch.object(
            backup_service, "sweep_stale_restore_dumps", mock.Mock(return_value=[])
        ) as sweep:
            with self.assertRaises(backup_service.RestoreReconnectError):
                await real(self.db, b"not-a-real-dump")

        sweep.assert_called_once()
        self.assertFalse(self.db.is_connected(), "重连失败后连接必须是断的（半恢复状态）")


class _FakeUpload:
    """最小 UploadFile 替身：记录每次 ``read()`` 请求的长度，不落地任何东西。"""

    def __init__(self, chunks, size=None):
        self._chunks = list(chunks)
        self.size = size
        self.read_sizes: list = []

    async def read(self, size):
        self.read_sizes.append(size)
        return self._chunks.pop(0) if self._chunks else b""


class BackupUploadBoundTest(unittest.TestCase):
    """SEC-07：备份上传在读取之前就按上限判定，超限 413 且不整份进内存。"""

    def test_limit_matches_backup_import_tier(self):
        self.assertEqual(backup_api.MAX_BACKUP_UPLOAD_BYTES, 256 * 1024 * 1024)
        # 工单里那发 300 MB 的载荷必须在读完之前就被拒掉。
        self.assertLess(backup_api.MAX_BACKUP_UPLOAD_BYTES, 300 * 1024 * 1024)

    def test_declared_oversize_is_rejected_before_reading(self):
        upload = _FakeUpload([b"x" * 1024], size=300 * 1024 * 1024)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(backup_api._read_upload(upload))
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertIn("256 MB", str(ctx.exception.detail))
        self.assertIn(str(300 * 1024 * 1024), str(ctx.exception.detail))
        self.assertEqual(upload.read_sizes, [], "判定必须发生在任何一次 read 之前")

    def test_chunked_read_stops_at_limit(self):
        chunk = backup_api.UPLOAD_CHUNK_BYTES
        upload = _FakeUpload([b"a" * chunk for _ in range(5)])
        with mock.patch.object(backup_api, "MAX_BACKUP_UPLOAD_BYTES", chunk * 2):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(backup_api._read_upload(upload))
        self.assertEqual(ctx.exception.status_code, 413)
        # 上限两块时最多读进三块就超限：占用内存不随载荷长度线性增长。
        self.assertLessEqual(len(upload.read_sizes) * chunk, chunk * 3)

    def test_within_limit_is_returned(self):
        upload = _FakeUpload([b"hello", b"world"])
        self.assertEqual(asyncio.run(backup_api._read_upload(upload)), b"helloworld")

    def test_empty_upload_is_400(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(backup_api._read_upload(_FakeUpload([])))
        self.assertEqual(ctx.exception.status_code, 400)


class StaleRestoreDumpSweepTest(unittest.TestCase):
    """SEC-08：被强杀留下的临时 dump 会被扫掉，正在写的那份不能碰。"""

    def _make(self, root: Path, name: str, age_seconds: float, now: float) -> Path:
        path = root / name
        path.write_bytes(b"dump")
        stamp = now - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_only_old_prefixed_dumps_are_removed(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale_restore = self._make(root, "luyun-restore-stale.pgdump", 8 * 3600, now)
            stale_export = self._make(root, "luyun-export-stale.pgdump", 8 * 3600, now)
            fresh = self._make(root, "luyun-restore-fresh.pgdump", 5, now)
            unrelated = self._make(root, "app.pgdump", 8 * 3600, now)

            removed = backup_service.sweep_stale_restore_dumps(
                tmp_dir=str(root), now=now, max_age_seconds=3600
            )

            self.assertEqual(
                sorted(Path(p).name for p in removed),
                ["luyun-export-stale.pgdump", "luyun-restore-stale.pgdump"],
            )
            self.assertFalse(stale_restore.exists())
            self.assertFalse(stale_export.exists())
            self.assertTrue(fresh.exists(), "另一个进程可能正写着这份 dump")
            self.assertTrue(unrelated.exists(), "不是我们的前缀就不碰")

    def test_missing_tmp_dir_is_not_an_error(self):
        self.assertEqual(
            backup_service.sweep_stale_restore_dumps(
                tmp_dir="/tmp/luyun-no-such-dir-20260923", max_age_seconds=1
            ),
            [],
        )


class StartupRestoreDumpSweepTest(unittest.TestCase):
    """SEC-08 / T2-V2：应用启动期（业务循环之前）也要扫一次残留 dump。"""

    def test_lifespan_sweeps_before_the_first_business_loop(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        start = source.index("async def lifespan(")
        body = source[start:source.index("\n# 创建FastAPI应用", start)]
        self.assertIn("_sweep_stale_restore_dumps_at_startup()", body)
        sweep_at = body.index("_sweep_stale_restore_dumps_at_startup()")
        first_loop = body.index("_start_resident_task(", sweep_at)
        self.assertLess(sweep_at, first_loop, "残留 dump 要在第一个常驻业务循环之前清掉")

    def test_startup_sweep_calls_the_service_sweep_once(self):
        import main as main_module

        calls: list = []

        def fake(**_kwargs):
            calls.append("sweep")
            return ["/tmp/luyun-restore-old.pgdump"]

        with mock.patch.object(backup_service, "sweep_stale_restore_dumps", new=fake):
            main_module._sweep_stale_restore_dumps_at_startup()
        self.assertEqual(calls, ["sweep"])

    def test_startup_sweep_failure_does_not_break_startup(self):
        import main as main_module

        def boom(**_kwargs):
            raise OSError("temp 目录不可读")

        with mock.patch.object(backup_service, "sweep_stale_restore_dumps", new=boom):
            main_module._sweep_stale_restore_dumps_at_startup()  # 只记日志，绝不抛


if __name__ == "__main__":
    unittest.main()
