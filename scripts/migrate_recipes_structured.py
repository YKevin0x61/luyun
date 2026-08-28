#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性脚本：把 sop_recipes.body_markdown 按最佳努力拆进
ingredients_json / steps_json / tips_json，并留下 legacy_markdown 快照与 needs_review=1。

用法
----
    python3 scripts/migrate_recipes_structured.py --dry-run
    python3 scripts/migrate_recipes_structured.py --db /path/to/app.db
    python3 scripts/migrate_recipes_structured.py --force

安全
----
- ``--dry-run`` 只读报告，不写库、不建备份。
- 正式跑会先按 ``*.bak.<YYYYmmdd_HHMMSS>`` 备份 db（及 wal/shm）。
- 已有非空 ``legacy_markdown`` 的行默认跳过；``--force`` 才重拆。
- **不**清空 ``body_markdown``。小贴士不猜测，恒为 ``[]``。

回滚见同目录 ``migrate_recipes_structured.rollback.md``。
验证通过后再按 ``scripts/README.md`` 约定移入 ``scripts/archive/``。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aiosqlite  # noqa: E402

from config import settings  # noqa: E402
from services.recipes.sop_parse import migrate_legacy_to_structured  # noqa: E402
from services.recipes.store import (  # noqa: E402
    RecipeStore,
    _ingredients_to_storage,
    _steps_to_storage,
    _tips_to_storage,
)


@dataclass
class MigrationReport:
    dry_run: bool
    db_path: str
    backup_path: Optional[str]
    processed: int = 0
    skipped: int = 0
    with_ingredients: int = 0
    zero_ingredients: int = 0
    per_station: dict[str, int] = field(default_factory=dict)
    station_titles: dict[str, str] = field(default_factory=dict)


def _backup_app_db_if_exists(app_db_path: str) -> Optional[str]:
    """Copy app.db (+ wal/shm) to ``*.bak.<YYYYmmdd_HHMMSS>``. Missing file → None."""
    if not os.path.exists(app_db_path):
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{app_db_path}.bak.{timestamp}"
    shutil.copy2(app_db_path, backup_path)
    for suffix in ("-wal", "-shm"):
        side_path = app_db_path + suffix
        if os.path.exists(side_path):
            shutil.copy2(side_path, backup_path + suffix)
    return backup_path


def _already_migrated(legacy_markdown) -> bool:
    return legacy_markdown is not None and str(legacy_markdown).strip() != ""


def _pragma_columns(rows) -> set[str]:
    return {row["name"] for row in rows}


async def run_migration(
    db_path: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> MigrationReport:
    if not os.path.isfile(db_path):
        raise FileNotFoundError(db_path)

    report = MigrationReport(
        dry_run=dry_run,
        db_path=db_path,
        backup_path=None,
    )
    store: Optional[RecipeStore] = None
    conn: Optional[aiosqlite.Connection] = None
    close_conn = False

    try:
        if dry_run:
            uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
            conn = await aiosqlite.connect(uri, uri=True)
            conn.row_factory = aiosqlite.Row
            close_conn = True
        else:
            report.backup_path = _backup_app_db_if_exists(db_path)
            store = RecipeStore(db_path)
            await store.connect()
            conn = store.conn

        cur = await conn.execute("PRAGMA table_info(sop_recipes)")
        cols = _pragma_columns(await cur.fetchall())
        has_legacy = "legacy_markdown" in cols

        select_legacy = "r.legacy_markdown" if has_legacy else "NULL AS legacy_markdown"
        cur = await conn.execute(
            "SELECT r.id, r.station_slug, r.body_markdown, "
            f"{select_legacy}, s.title AS station_title "
            "FROM sop_recipes r "
            "LEFT JOIN sop_stations s ON s.slug = r.station_slug "
            "ORDER BY r.id"
        )
        rows = await cur.fetchall()

        for row in rows:
            slug = row["station_slug"]
            title = row["station_title"] or slug
            report.station_titles.setdefault(slug, title)
            if not force and _already_migrated(row["legacy_markdown"]):
                report.skipped += 1
                continue

            parsed = migrate_legacy_to_structured(row["body_markdown"] or "")
            has_ing = len(parsed["ingredients"]) >= 1
            report.processed += 1
            if has_ing:
                report.with_ingredients += 1
            else:
                report.zero_ingredients += 1
            report.per_station[slug] = report.per_station.get(slug, 0) + 1

            if dry_run:
                continue

            await conn.execute(
                "UPDATE sop_recipes SET ingredients_json=?, steps_json=?, tips_json=?, "
                "legacy_markdown=?, needs_review=1 WHERE id=?",
                (
                    _ingredients_to_storage(parsed["ingredients"]),
                    _steps_to_storage(parsed["steps"]),
                    _tips_to_storage(parsed["tips"]),
                    row["body_markdown"],
                    row["id"],
                ),
            )

        if not dry_run:
            await conn.commit()
    finally:
        if store is not None:
            await store.close()
        elif close_conn and conn is not None:
            await conn.close()

    return report


def print_report(report: MigrationReport) -> None:
    mode = "DRY-RUN（未写入任何文件）" if report.dry_run else "正式迁移"
    print(f"\n=== 配方结构化迁移报告（{mode}） ===")
    print(f"数据库: {report.db_path}")
    if report.backup_path:
        print(f"已备份至: {report.backup_path}")
    print()
    print(f"处理条目: {report.processed}")
    print(f"识别出至少一条用料: {report.with_ingredients}")
    print(f"全部落入步骤（无用料）: {report.zero_ingredients}")
    print(f"跳过（已有 legacy_markdown）: {report.skipped}")
    print()
    print("按岗位:")
    if not report.per_station:
        print("  （无）")
    else:
        for slug, count in report.per_station.items():
            title = report.station_titles.get(slug, slug)
            if title != slug:
                print(f"  {slug}（{title}）: {count}")
            else:
                print(f"  {slug}: {count}")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将 sop_recipes.body_markdown 拆进结构化 JSON 字段",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只输出迁移报告，不写库、不创建备份",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="对已有 legacy_markdown 的条目也重新拆分并覆盖结构化字段",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="sqlite 路径，默认 settings.APP_DB_PATH",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    db_path = args.db or settings.APP_DB_PATH
    try:
        report = asyncio.run(
            run_migration(db_path, dry_run=args.dry_run, force=args.force)
        )
    except FileNotFoundError:
        print(f"数据库不存在: {db_path}", file=sys.stderr)
        return 1
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
