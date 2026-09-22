#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宿主机冷备入口：调度 + 退出码，应用侧逻辑全部走备份服务。

冷备产物是一份可校验的单一归档（PostgreSQL 整库快照 + 凭据 + 密钥 + 两类卫生
照片 + 清单 + 校验和），并在固定位置写一份状态文件，供管理后台「备份中心」读取。
脚本本身不再自带任何数据库复制实现。

SQLite 退场后（ADR 0089）这里不再要求 ``data/app.db`` 存在：库快照由
``pg_dump`` 产出（成员 ``app.pgdump``），保留份数也从 PostgreSQL 的
``app_settings`` 读取。

用法：
    python3 scripts/cold_backup.py [--retention N]

环境变量（与 deploy/backup.sh 一致）：
    DATA_DIR     源数据目录（凭据、密钥、卫生照片），默认 ./data
    BACKUP_DIR   冷备输出根目录，默认 <仓库根>/backups

退出码：0 成功；非 0 失败（失败也会写状态文件，且不留下半成品归档）。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402
from services import backup_retention, backup_service  # noqa: E402

logger = logging.getLogger("cold_backup")


def _configure_paths() -> None:
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        settings.DATABASE_DIR = data_dir
    backup_dir = os.environ.get("BACKUP_DIR")
    if backup_dir:
        settings.COLD_BACKUP_DIR = backup_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LuckIn 冷备归档")
    parser.add_argument(
        "--retention",
        type=int,
        default=None,
        help="保留份数；默认读取运行配置中的冷备保留份数",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _configure_paths()

    keep = args.retention
    if keep is None:
        keep = backup_retention.load_from_pg_sync().cold_keep

    logger.info("📦 生成冷备归档 → %s", backup_service.get_cold_backup_dir())
    try:
        archive, manifest = backup_service.build_cold_backup_archive(
            app_version=settings.APP_VERSION,
        )
    except Exception as exc:
        # Remove any half-written product so it can never be mistaken for a backup.
        import shutil

        root = backup_service.get_cold_backup_dir()
        for entry in sorted(root.glob("*/"), key=lambda p: p.name, reverse=True):
            if not (entry / backup_service.COLD_ARCHIVE_NAME).exists():
                shutil.rmtree(entry, ignore_errors=True)
                break
        backup_service.write_cold_backup_status(
            ok=False, archive=None, error=str(exc)
        )
        logger.error("❌ 冷备失败：%s", exc)
        return 1

    status = backup_service.write_cold_backup_status(
        ok=True, archive=archive, manifest=manifest
    )
    deleted = backup_service.prune_cold_backups(keep)
    for name in deleted:
        logger.info("  🗑️  删除旧冷备: %s", name)

    size_mb = (manifest.get("archive_bytes") or 0) / (1024 * 1024)
    logger.info("✅ 冷备完成: %s (%.2f MB)", archive, size_mb)
    logger.info("   覆盖内容: %s", "、".join(manifest.get("contents_labels") or []))
    if not (status.get("consistency") or {}).get("ok", True):
        logger.warning(
            "⚠️  冷备内一致性校验未通过：%s",
            "；".join((status.get("consistency") or {}).get("errors") or []),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
