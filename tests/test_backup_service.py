#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统完整备份 v2 服务测试。"""

import base64
import io
import json
import os
import stat
import struct
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.hmac import HMAC

from config import settings
from database import CHINA_TZ, DatabaseManager
from services import backup_service, credentials_store
from services.backup_service import (
    BACKUP_MAGIC,
    CONTENT_APP_PG,
    CONTENT_CREDENTIALS,
    CONTENT_RUNTIME,
    PHOTO_OTHER,
    PHOTO_STANDARD,
    SNAPSHOT_KEEP,
)
from services.credentials_store import CredentialBundle
from tests.backup_fixtures import legacy_sqlite_backup, seed_hygiene_photo_refs


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
            app_version="0.1.0",
        )
        parsed = backup_service.parse_backup(blob, "pass1234")
        self.assertEqual(parsed["runtime"], runtime)
        self.assertTrue(parsed["meta"]["includes"]["runtime"])

    def test_round_trip_with_pg_dump_member(self):
        """业务数据成员是整库 pg_dump（app.pgdump），配方随它一起走。"""
        blob = backup_service.build_backup(
            "pass1234",
            include_runtime=False,
            runtime_data=None,
            include_app_db=True,
            app_pg_bytes=b"PGDMP-LEGACY-CONTENT",
            app_version="0.1.0",
        )
        parsed = backup_service.parse_backup(blob, "pass1234")
        self.assertEqual(parsed["app_pg_bytes"], b"PGDMP-LEGACY-CONTENT")
        self.assertIsNone(parsed["app_db_bytes"])
        self.assertTrue(parsed["meta"]["includes"]["app_pg"])
        self.assertFalse(parsed["meta"]["includes"].get("app_db"))
        # 配方表在同一个库里：清单如实标出「含配方」，否则页面会显示"含配方：否"
        self.assertTrue(parsed["meta"]["includes"]["recipes_db"])
        self.assertIsNone(parsed["meta"].get("row_counts"))

    def test_legacy_sqlite_member_is_read_but_not_a_business_member(self):
        """旧包的 app.db 成员仍能解析出来，供导入侧明确拒绝。"""
        parsed = backup_service.parse_backup(legacy_sqlite_backup(), "pass1234")

        self.assertIsNotNone(parsed["app_db_bytes"])
        self.assertIsNone(parsed["app_pg_bytes"])
        self.assertIsNone(parsed.get("recipes_db_bytes"))

    def test_wrong_passphrase_fails(self):
        blob = backup_service.build_backup(
            "pass1234",
            include_runtime=False,
            runtime_data=None,
            include_app_db=False,
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
                    app_version="0.1.0",
                )


class SnapshotTest(unittest.TestCase):
    """本机回滚快照：整库 pg_dump + 凭据，保留份数与清单。"""

    def setUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.cred_path = os.path.join(self._tmpdir.name, "credentials.enc")
        with open(self.cred_path, "wb") as f:
            f.write(b"test")
        # 快照要真跑 pg_dump（只有 PG 一条路径）；这里换成写一个假 dump，
        # 用例只关心清单与保留策略，不必每次真导整库。
        self._dump = mock.patch.object(
            backup_service, "_pg_dump_sync", side_effect=self._fake_dump
        )
        self._dump.start()

    def tearDown(self):
        self._dump.stop()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    @staticmethod
    def _fake_dump(dst_path: str, *args, **kwargs) -> None:
        with open(dst_path, "wb") as handle:
            handle.write(b"PGDMP-FAKE")

    def test_snapshot_keep_limit_and_list_order(self):
        timestamps = []
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=CHINA_TZ)
        for i in range(SNAPSHOT_KEEP + 2):
            fake_now = base.replace(second=i)
            with mock.patch(
                "services.backup_service.datetime",
            ) as mock_dt:
                mock_dt.now.return_value = fake_now
                mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                ts = backup_service.create_restore_snapshot(self.cred_path)
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

    def test_snapshot_carries_pg_dump_not_sqlite_file(self):
        ts = backup_service.create_restore_snapshot(self.cred_path)
        snap_dir = backup_service._snapshot_root() / ts

        self.assertEqual(
            (snap_dir / "app.pgdump").read_bytes(), b"PGDMP-FAKE"
        )
        self.assertFalse((snap_dir / "app.db").exists())
        self.assertFalse((snap_dir / "recipes.db").exists())
        meta = json.loads((snap_dir / "snapshot_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["contents"], [CONTENT_APP_PG, CONTENT_RUNTIME, CONTENT_CREDENTIALS])

    def test_snapshot_copies_cred_key_beside_credentials(self):
        key_path = os.path.join(self._tmpdir.name, ".cred_key")
        with open(key_path, "wb") as f:
            f.write(b"fernet-key-material")
        os.chmod(key_path, 0o600)

        ts = backup_service.create_restore_snapshot(self.cred_path)
        snap_dir = backup_service._snapshot_root() / ts
        copied_key = snap_dir / ".cred_key"
        self.assertTrue(copied_key.is_file())
        self.assertEqual(copied_key.read_bytes(), b"fernet-key-material")
        self.assertEqual(stat.S_IMODE(copied_key.stat().st_mode), 0o600)


class FernetStreamEquivalenceTest(unittest.TestCase):
    """分块加密必须与标准 Fernet 逐字节等价：否则旧版本解不开新包，回滚就没退路。"""

    IV = bytes(range(16))
    TS = 1_700_000_000

    def _encrypt(self, data: bytes, key: bytes, *, chunk_size: int) -> bytes:
        fd, src_path = tempfile.mkstemp(suffix=".bin")
        os.close(fd)
        with open(src_path, "wb") as handle:
            handle.write(data)
        out = io.BytesIO()
        try:
            backup_service._fernet_encrypt_stream(
                src_path, out, key, iv=self.IV, timestamp=self.TS, chunk_size=chunk_size
            )
        finally:
            os.unlink(src_path)
        return out.getvalue()

    def _reference_token(self, data: bytes, key: bytes) -> bytes:
        """照 Fernet 规范手工拼一遍（与 cryptography 源码同样的步骤）。"""
        raw = base64.urlsafe_b64decode(key)
        signing_key, encryption_key = raw[:16], raw[16:]
        padder = sym_padding.PKCS7(128).padder()
        encryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(self.IV)).encryptor()
        ciphertext = (
            encryptor.update(padder.update(data) + padder.finalize())
            + encryptor.finalize()
        )
        parts = b"\x80" + struct.pack(">Q", self.TS) + self.IV + ciphertext
        hasher = HMAC(signing_key, hashes.SHA256())
        hasher.update(parts)
        return base64.urlsafe_b64encode(parts + hasher.finalize())

    def test_chunk_size_does_not_change_output(self):
        key = Fernet.generate_key()
        data = os.urandom(10_000)
        baseline = self._encrypt(data, key, chunk_size=4096)
        for chunk_size in (1, 3, 15, 16, 17, 1024, 10_000, 100_000):
            self.assertEqual(
                self._encrypt(data, key, chunk_size=chunk_size),
                baseline,
                f"chunk_size={chunk_size} 改变了输出字节",
            )

    def test_matches_standard_fernet_bytes(self):
        key = Fernet.generate_key()
        for size in (0, 1, 15, 16, 17, 4096, 9999):
            data = os.urandom(size)
            self.assertEqual(
                self._encrypt(data, key, chunk_size=333),
                self._reference_token(data, key),
                f"长度 {size} 的明文输出与标准 Fernet 不一致",
            )

    def test_standard_fernet_can_decrypt_our_token(self):
        key = Fernet.generate_key()
        data = os.urandom(5000)
        self.assertEqual(Fernet(key).decrypt(self._encrypt(data, key, chunk_size=777)), data)

    def test_other_key_cannot_decrypt(self):
        """格式等价不等于放松校验：换 key 必须解不开。"""
        from cryptography.fernet import InvalidToken as _InvalidToken

        token = self._encrypt(os.urandom(1000), Fernet.generate_key(), chunk_size=64)
        with self.assertRaises(_InvalidToken):
            Fernet(Fernet.generate_key()).decrypt(token)


class MissingHygieneCaptureIdsTest(unittest.IsolatedAsyncioTestCase):
    """恢复后一致性检查：库引用的原始照片缺了就必须报出来。

    引用关系在**业务库**里（而不是 data/app.db）：这正是 PG 门店上原先静默通过
    的那条路径——分类读不到库就只能报「一张都不缺」。
    """

    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.capture_root = Path(self._tmpdir.name) / "hygiene-captures"
        self.capture_root.mkdir(parents=True, exist_ok=True)

        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        await seed_hygiene_photo_refs(
            self.db._conn,
            standards=["std-1"],
            others=["other-1"],
            variants=[("std-1", "thumb-1")],
        )

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    def _touch(self, capture_id):
        (self.capture_root / capture_id).write_bytes(b"photo")

    async def test_reports_missing_original_photos(self):
        self._touch("std-1")

        result = await backup_service.missing_hygiene_capture_ids(
            self.db, capture_root=self.capture_root
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["standard_missing"], 0)
        self.assertEqual(result["other_missing"], 1)
        self.assertEqual(result["missing"][PHOTO_OTHER], ["other-1"])
        self.assertTrue(result["checked_at"])

    async def test_ok_when_every_referenced_photo_exists(self):
        self._touch("std-1")
        self._touch("other-1")

        result = await backup_service.missing_hygiene_capture_ids(
            self.db, capture_root=self.capture_root
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["missing"], {})
        self.assertEqual(result["standard_missing"], 0)

    async def test_missing_derivative_does_not_count_as_inconsistent(self):
        """派生图缺失时接口会回退到原图，不算库与照片不一致。"""
        self._touch("std-1")
        self._touch("other-1")

        result = await backup_service.missing_hygiene_capture_ids(
            self.db, capture_root=self.capture_root
        )

        self.assertTrue(result["ok"])
        self.assertNotIn("thumb-1", result["missing"].get(PHOTO_STANDARD, []))

    async def test_derivative_follows_its_source_into_the_standard_class(self):
        """派生图随源图归类：include_variants=False 时只回原始照片。"""
        classified = await backup_service.classify_hygiene_capture_ids(self.db)
        self.assertEqual(classified[PHOTO_STANDARD], ["std-1", "thumb-1"])
        self.assertEqual(classified[PHOTO_OTHER], ["other-1"])

        originals = await backup_service.classify_hygiene_capture_ids(
            self.db, include_variants=False
        )
        self.assertEqual(originals[PHOTO_STANDARD], ["std-1"])
        self.assertEqual(originals[PHOTO_OTHER], ["other-1"])


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
