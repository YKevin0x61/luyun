#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份相关用例的共享夹具：业务库里的照片引用、SQLite 时代的旧备份包。

两件事在多个测试文件里都要做，写一份免得各自漂移：

* :func:`seed_hygiene_photo_refs`：往**测试库**里造出「库引用了哪些照片」。
  照片分类（``backup_service.classify_hygiene_capture_ids``）现在只认业务库连接，
  用例必须像真门店一样把引用行插进 PG，而不是造一个 ``data/app.db``。
* :func:`legacy_sqlite_backup`：手搓一份 SQLite 时代的 ``.luyunbak``（成员是
  ``app.db``）。新代码不得再产出这种包，但必须**明确拒绝**它，所以需要一个能造出
  旧包的入口——只能按旧格式自己拼 tar + 加密。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import struct
import tarfile
from typing import Iterable, Sequence, Tuple

from services import credentials_store
from services.backup_service import BACKUP_MAGIC

_PHOTO_STAMP = "2026-01-01T00:00:00"


async def seed_hygiene_photo_refs(
    conn,
    *,
    standards: Sequence[str] = (),
    others: Sequence[str] = (),
    variants: Iterable[Tuple[str, str]] = (),
) -> None:
    """在测试库里插入卫生照片引用。

    ``conn`` 是 ``DatabaseManager`` 的共享连接（``db._conn``）。

    * 标准图走 ``hygiene_standards``：外键要求先有 zone 与 daily_item，所以这里
      连带建一条最小可用行（同一用例里多次调用会复用已有的 zone/item）。
    * 其它照片走 ``hygiene_teaching_examples``：它是唯一能独立插入的引用表
      （``item_id`` 可空），不必为了一张照片再搭一套日常检查实例。
    * 派生图进 ``hygiene_capture_variants``。
    """
    if standards:
        cursor = await conn.execute(
            "SELECT id FROM hygiene_zones WHERE name = '备份夹具后厨'"
        )
        row = await cursor.fetchone()
        zone_id = row[0] if row else None
        if zone_id is None:
            cursor = await conn.execute(
                "INSERT INTO hygiene_zones (name, day_shift, night_shift,"
                " created_at, updated_at) VALUES ('备份夹具后厨', 1, 1, ?, ?)"
                " RETURNING id",
                (_PHOTO_STAMP, _PHOTO_STAMP),
            )
            zone_id = (await cursor.fetchone())[0]

        cursor = await conn.execute(
            "SELECT id FROM hygiene_daily_items WHERE zone_id = ? AND name = '备份夹具项'",
            (zone_id,),
        )
        row = await cursor.fetchone()
        item_id = row[0] if row else None
        if item_id is None:
            cursor = await conn.execute(
                "INSERT INTO hygiene_daily_items (zone_id, name, created_at, updated_at)"
                " VALUES (?, '备份夹具项', ?, ?) RETURNING id",
                (zone_id, _PHOTO_STAMP, _PHOTO_STAMP),
            )
            item_id = (await cursor.fetchone())[0]

        for capture_id in standards:
            await conn.execute(
                "INSERT INTO hygiene_standards (item_id, capture_id, content_type,"
                " created_at) VALUES (?, ?, 'image/jpeg', ?)",
                (item_id, capture_id, _PHOTO_STAMP),
            )

    for capture_id in others:
        # 左右都填同一个 capture_id：分类按集合去重，用例才能按「传进去几张就是几张」
        # 断言；教学图本身的左右是否不同与分类无关。
        await conn.execute(
            "INSERT INTO hygiene_teaching_examples (kind, title, left_label, right_label,"
            " left_capture_id, right_capture_id, left_content_type, right_content_type,"
            " created_at) VALUES ('good', ?, '左', '右', ?, ?, 'image/jpeg',"
            " 'image/jpeg', ?)",
            (f"夹具-{capture_id}", capture_id, capture_id, _PHOTO_STAMP),
        )

    for source_id, variant_id in variants:
        await conn.execute(
            "INSERT INTO hygiene_capture_variants (source_capture_id, variant,"
            " capture_id, content_type, width, height, byte_size, content_sha256,"
            " created_at) VALUES (?, 'thumb', ?, 'image/jpeg', 10, 10, 4, 'x', ?)",
            (source_id, variant_id, _PHOTO_STAMP),
        )

    await conn.commit()


def legacy_sqlite_backup(
    app_db_bytes: bytes = b"SQLite format 3\x00-legacy",
    *,
    passphrase: str = "pass1234",
) -> bytes:
    """造一份成员为 ``app.db`` 的旧备份包（SQLite 时代格式）。"""
    credentials_bytes = json.dumps(
        {"phone": "13800000000", "password": "pw"}, ensure_ascii=False
    ).encode("utf-8")
    meta = {
        "version": 2,
        "exported_at": "2026-01-01T00:00:00+08:00",
        "app_version": "0.5.0",
        "provenance": "manual",
        "includes": {
            "runtime": False,
            "app_db": True,
            "recipes_db": False,
            "standard_photos": False,
            "other_photos": False,
        },
        "photos": {},
        "sha256": {
            "credentials.json": hashlib.sha256(credentials_bytes).hexdigest(),
            "app.db": hashlib.sha256(app_db_bytes).hexdigest(),
        },
    }
    meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, data in (
            ("credentials.json", credentials_bytes),
            ("app.db", app_db_bytes),
            ("meta.json", meta_bytes),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    salt = os.urandom(credentials_store.BACKUP_SALT_BYTES)
    iterations = credentials_store.BACKUP_KDF_ITERATIONS
    key = credentials_store._derive_backup_key(passphrase, salt, iterations)
    header = json.dumps(
        {
            "salt": base64.b64encode(salt).decode("ascii"),
            "iterations": iterations,
            "created_at": "2026-01-01T00:00:00+08:00",
        },
        ensure_ascii=False,
    ).encode("utf-8")

    from cryptography.fernet import Fernet

    return (
        BACKUP_MAGIC
        + struct.pack(">I", len(header))
        + header
        + Fernet(key).encrypt(buffer.getvalue())
    )
