#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份导入暂存测试。"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from services import backup_import_staging, backup_service, credentials_store
from services.credentials_store import CHINA_TZ, CredentialBundle


def _sample_bundle() -> CredentialBundle:
    return CredentialBundle(
        phone="13800000000",
        password="s3cret-pw",
        shop_id="100001",
        company_id="200002",
        shop_name="LuckIn",
        delivery_shop_id="200002",
    )


def _sample_parsed(*, with_runtime: bool = False, with_app_db: bool = False) -> dict:
    meta = {
        "version": 2,
        "exported_at": datetime.now(CHINA_TZ).isoformat(),
        "includes": {
            "runtime": with_runtime,
            "app_db": with_app_db,
            "recipes_db": False,
        },
    }
    credentials = {
        "phone": "13800000000",
        "password": "s3cret-pw",
        "shop_id": "100001",
        "company_id": "200002",
        "shop_name": "LuckIn",
        "delivery_shop_id": "200002",
    }
    return {
        "meta": meta,
        "credentials": credentials,
        "runtime": {"poll_min_seconds": 5} if with_runtime else None,
        "app_db_bytes": b"SQLite fake db" if with_app_db else None,
        "recipes_db_bytes": None,
    }


class BackupImportStagingTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._staging_root = Path(self._tmpdir.name) / ".import-staging"
        self._root_patcher = mock.patch.object(
            backup_import_staging,
            "_staging_root",
            return_value=self._staging_root,
        )
        self._root_patcher.start()
        self.addCleanup(self._root_patcher.stop)
        self.addCleanup(self._tmpdir.cleanup)

    def test_create_and_consume_round_trip(self):
        parsed = _sample_parsed(with_runtime=True, with_app_db=True)
        token = backup_import_staging.create_staging("session-a", parsed)
        self.assertTrue(token)

        loaded = backup_import_staging.load_parsed_from_staging(token, "session-a")
        self.assertEqual(loaded["meta"], parsed["meta"])
        self.assertEqual(loaded["credentials"], parsed["credentials"])
        self.assertEqual(loaded["runtime"], parsed["runtime"])
        self.assertEqual(loaded["app_db_bytes"], parsed["app_db_bytes"])

        consumed = backup_import_staging.consume_staging(token, "session-a")
        self.assertEqual(consumed["credentials"], parsed["credentials"])
        self.assertFalse((self._staging_root / token).exists())

    def test_owner_mismatch_rejected(self):
        token = backup_import_staging.create_staging("session-a", _sample_parsed())
        with self.assertRaises(PermissionError):
            backup_import_staging.load_parsed_from_staging(token, "session-b")

    def test_expired_staging_rejected(self):
        token = backup_import_staging.create_staging("session-a", _sample_parsed())
        staging_dir = self._staging_root / token
        meta_path = staging_dir / backup_import_staging.STAGING_META_FILENAME
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        expired = datetime.now(CHINA_TZ) - timedelta(minutes=1)
        meta["expires_at"] = expired.isoformat()
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        with self.assertRaises(TimeoutError):
            backup_import_staging.load_parsed_from_staging(token, "session-a")
        self.assertFalse(staging_dir.exists())

    def test_new_preview_replaces_previous_for_same_owner(self):
        first = backup_import_staging.create_staging("session-a", _sample_parsed(with_app_db=False))
        second = backup_import_staging.create_staging("session-a", _sample_parsed(with_app_db=True))
        self.assertNotEqual(first, second)
        self.assertFalse((self._staging_root / first).exists())

        loaded = backup_import_staging.load_parsed_from_staging(second, "session-a")
        self.assertEqual(loaded["app_db_bytes"], b"SQLite fake db")

    def test_cleanup_expired_staging(self):
        token = backup_import_staging.create_staging("session-a", _sample_parsed())
        staging_dir = self._staging_root / token
        meta_path = staging_dir / backup_import_staging.STAGING_META_FILENAME
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["expires_at"] = (datetime.now(CHINA_TZ) - timedelta(minutes=5)).isoformat()
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        removed = backup_import_staging.cleanup_expired_staging()
        self.assertEqual(removed, 1)
        self.assertFalse(staging_dir.exists())

    def test_integration_with_parse_backup(self):
        saved_cache = credentials_store._cache
        credentials_store._cache = _sample_bundle()
        self.addCleanup(setattr, credentials_store, "_cache", saved_cache)
        blob = backup_service.build_backup(
            "pass1234",
            include_runtime=True,
            runtime_data={"poll_min_seconds": 5},
            include_app_db=False,
            app_db_bytes=None,
            include_recipes=False,
            recipes_db_bytes=None,
            app_version="test",
        )
        parsed = backup_service.parse_backup(blob, "pass1234")
        token = backup_import_staging.create_staging("session-a", parsed)
        loaded = backup_import_staging.consume_staging(token, "session-a")
        self.assertEqual(loaded["meta"]["version"], 2)
        self.assertEqual(loaded["credentials"]["shop_id"], "100001")


if __name__ == "__main__":
    unittest.main()
