#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性数据迁移脚本：多库（每表独立 .db 文件）→ 单库 data/app.db（WAL）。

背景
----
合库前（旧 `DATABASE_PATHS` 映射，见 git 历史 `fb19d77~1:config.py`）每张业务表
各自一个 `.db` 文件（如 `data/orders.db`、`data/dish_stations.db` ……）；
`admin_user`/`sessions`/`api_tokens` 三张 auth 表落在 `data/auth.db`；配方库
（`sop_stations`/`sop_recipes`/`sop_recipes_history`）落在 `data/recipes.db`。

合库后（本分支 2.1/2.2）代码统一改为读写 `data/app.db`。旧生产环境如果不迁移，
新代码启动会新建一个空 `app.db`，旧数据就成了打不开的孤儿文件。

`data/logs.db` 保持独立，不在本脚本迁移范围内（日志写入量大，故意不并库，见
`config.py` `DATABASE_PATHS` 注释与 `services/log_storage.py` 顶部说明）。

用法
----
    python3 scripts/archive/consolidate_dbs.py                # 正式迁移
    python3 scripts/archive/consolidate_dbs.py --dry-run       # 只报告，不写入任何文件
    python3 scripts/archive/consolidate_dbs.py --data-dir X --app-db Y   # 自定义路径（供测试/演练用）

安全设计
--------
1. **只读旧文件**：旧 `.db` 文件均以 `mode=ro` 只读 URI 打开，脚本从不写入、不
   删除、不修改任何旧文件（包括其 `-wal`/`-shm` 侧车文件）。
2. **备份**：写入前若目标 `app.db` 已存在，先复制一份带时间戳的备份
   （`data/app.db.bak.<YYYYmmdd_HHMMSS>`，连同 `-wal`/`-shm` 一并备份）。
3. **幂等**：每张表迁移前先查目标表现有行数，若 > 0 则整表跳过并告警——不做
   按主键的行级 UPSERT，因为多张表使用无业务含义的自增 `id`，旧库与"已启动
   过新代码的空 app.db"之间的自增 id 完全可能撞号但内容不同，行级 `INSERT OR
   IGNORE` 会静默丢弹旧数据；整表跳过对空表安全、对已迁移表可重跑。写入 SQL
   仍带 `INSERT OR IGNORE` 作为同一来源表内部主键冲突的兜底。
   因此可安全重复执行：第一次迁移写入数据后，第二次执行会对所有已迁移表跳过，
   不产生重复、不覆盖任何数据；对全新空环境（无任何旧库文件）整个脚本无操作。
4. **按表事务**：每张表在独立事务内迁移，某表写入出错自动回滚该表且不影响
   其它表，脚本汇总报告失败表，进程以非零退出码退出。
5. **不建 schema 之外的猜测**：目标表结构来自现有代码的 schema 定义
   （`db_core/schema.py` 与 `services/recipes/store.py`），迁移只按列名做交集
   写入，源表多出的列忽略，目标表多出的列走各自默认值。

回滚
----
详见同目录 `consolidate_dbs.rollback.md`。一句话：删除 `data/app.db`
（及其 `-wal`/`-shm`），旧多库文件从未被本脚本改动，退回旧代码即恢复原状；
或者用本脚本迁移前生成的带时间戳备份文件覆盖回 `data/app.db`。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import aiosqlite

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings  # noqa: E402
from db_core.schema import ALL_TABLES, _TABLE_SCHEMAS, _INDEX_DEFINITIONS  # noqa: E402
from db_core.utils import SQLITE_BUSY_TIMEOUT_MS, SQLITE_JOURNAL_MODE_WAL  # noqa: E402
from services.recipes.store import RecipeStore  # noqa: E402

# 配方库三张表定义于 services/recipes/store.py::RecipeStore._ensure_schema，
# 此处仅用于枚举"待迁移的表名"，不重复维护其 DDL（schema 创建时直接复用
# RecipeStore.connect()，见 _ensure_app_db_schema）。
RECIPES_SOURCE_FILE = "recipes.db"
RECIPES_TABLES = ("sop_stations", "sop_recipes", "sop_recipes_history")

_CREATE_TABLE_NAME_RE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", re.IGNORECASE)


@dataclass
class TableMigrationSpec:
    """一张待迁移表：目标表名 + 旧库文件名（相对 data 目录）+ 来源分组（仅用于展示）。"""

    table: str
    source_file: str
    source_group: str


@dataclass
class TableResult:
    table: str
    source_file: str
    status: str  # 见 _STATUS_LABELS
    source_rows: int = 0
    migrated_rows: int = 0
    target_existing_rows: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "failed"


@dataclass
class ConsolidationReport:
    results: List[TableResult] = field(default_factory=list)
    backup_path: Optional[str] = None
    dry_run: bool = False
    app_db_path: str = ""
    data_dir: str = ""

    @property
    def has_failures(self) -> bool:
        return any(not r.ok for r in self.results)

    @property
    def total_migrated_rows(self) -> int:
        return sum(r.migrated_rows for r in self.results)


_STATUS_LABELS = {
    "migrated": "✅ 已迁移",
    "would_migrate": "🔎 dry-run：待迁移",
    "skipped_no_source": "⏭️  跳过（旧库文件不存在）",
    "skipped_no_table": "⏭️  跳过（旧库文件中无此表）",
    "skipped_empty_source": "⏭️  跳过（源表为空）",
    "skipped_target_has_data": "⏭️  跳过（目标表已有数据，幂等保护）",
    "failed": "❌ 失败",
}


def build_migration_specs() -> List[TableMigrationSpec]:
    """构建全部待迁移表清单：db_core 业务表 + auth 三表 + 配方三表。logs 不在其中。"""
    specs: List[TableMigrationSpec] = []
    for table in ALL_TABLES:
        schema_sql = _TABLE_SCHEMAS.get(table, "")
        if table == "auth":
            for real_table in _CREATE_TABLE_NAME_RE.findall(schema_sql):
                specs.append(TableMigrationSpec(real_table, "auth.db", "auth"))
        else:
            specs.append(TableMigrationSpec(table, f"{table}.db", "core"))
    for recipe_table in RECIPES_TABLES:
        specs.append(TableMigrationSpec(recipe_table, RECIPES_SOURCE_FILE, "recipes"))
    return specs


def _backup_app_db_if_exists(app_db_path: str) -> Optional[str]:
    """迁移前备份现存 app.db（含 -wal/-shm）。不存在则返回 None，不做任何事。"""
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


async def _ensure_app_db_schema(app_db_path: str) -> None:
    """在 app.db 上建齐全部表结构 + 索引。复用 db_core/connection.py 相同的
    schema 定义（ALL_TABLES/_TABLE_SCHEMAS/_INDEX_DEFINITIONS 单一来源），
    CREATE TABLE IF NOT EXISTS 对已存在的表安全无害。
    """
    os.makedirs(os.path.dirname(app_db_path), exist_ok=True)
    conn = await aiosqlite.connect(app_db_path)
    try:
        await conn.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE_WAL}")
        await conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        for table in ALL_TABLES:
            schema = _TABLE_SCHEMAS.get(table, "")
            if schema:
                await conn.executescript(schema)
        for table in ALL_TABLES:
            for idx_sql in _INDEX_DEFINITIONS.get(table, []):
                await conn.execute(idx_sql)
        await conn.commit()
    finally:
        await conn.close()

    # 配方库三表结构与 RecipeStore 保持完全一致：直接复用其 connect()/_ensure_schema，
    # 避免在本脚本里重复维护一份 DDL 造成日后漂移。
    recipe_store = RecipeStore(db_path=app_db_path)
    await recipe_store.connect()
    await recipe_store.close()


async def _open_readonly(path: str) -> aiosqlite.Connection:
    uri = f"file:{os.path.abspath(path)}?mode=ro"
    conn = await aiosqlite.connect(uri, uri=True)
    conn.row_factory = aiosqlite.Row
    return conn


async def _read_source_table(data_dir: str, spec: TableMigrationSpec):
    """只读读取旧库中某张表的全部数据。
    返回 (列名列表, 行数据列表) 或 None（文件不存在 / 文件中没有该表）。
    """
    source_path = os.path.join(data_dir, spec.source_file)
    if not os.path.exists(source_path):
        return None

    conn = await _open_readonly(source_path)
    try:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (spec.table,),
        )
        if await cursor.fetchone() is None:
            return None

        info_cursor = await conn.execute(f"PRAGMA table_info({spec.table})")
        columns = [row[1] for row in await info_cursor.fetchall()]

        data_cursor = await conn.execute(f"SELECT * FROM {spec.table}")
        rows = [tuple(row) for row in await data_cursor.fetchall()]
        return columns, rows
    finally:
        await conn.close()


async def _target_row_count(conn: Optional[aiosqlite.Connection], table: str) -> int:
    """目标表现有行数；连接为空或表尚不存在时视为 0（全新环境场景）。"""
    if conn is None:
        return 0
    try:
        cursor = await conn.execute(f"SELECT COUNT(*) FROM {table}")
        row = await cursor.fetchone()
        return int(row[0])
    except aiosqlite.OperationalError:
        return 0


async def _target_table_columns(conn: aiosqlite.Connection, table: str) -> List[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in await cursor.fetchall()]


async def _migrate_one_table(
    conn: Optional[aiosqlite.Connection],
    data_dir: str,
    spec: TableMigrationSpec,
    dry_run: bool,
) -> TableResult:
    source = await _read_source_table(data_dir, spec)
    if source is None:
        source_path = os.path.join(data_dir, spec.source_file)
        status = "skipped_no_source" if not os.path.exists(source_path) else "skipped_no_table"
        return TableResult(spec.table, spec.source_file, status)

    columns, rows = source
    if not rows:
        return TableResult(spec.table, spec.source_file, "skipped_empty_source")

    existing = await _target_row_count(conn, spec.table)
    if existing > 0:
        return TableResult(
            spec.table, spec.source_file, "skipped_target_has_data",
            source_rows=len(rows), target_existing_rows=existing,
            detail=f"目标表已有 {existing} 行，为保证幂等整表跳过（不做行级去重覆盖）",
        )

    if dry_run:
        return TableResult(
            spec.table, spec.source_file, "would_migrate",
            source_rows=len(rows), detail=f"将写入 {len(rows)} 行",
        )

    assert conn is not None, "非 dry-run 模式下 app.db 连接必须已建立"
    target_columns = await _target_table_columns(conn, spec.table)
    usable_columns = [c for c in target_columns if c in columns]
    if not usable_columns:
        return TableResult(
            spec.table, spec.source_file, "failed", source_rows=len(rows),
            detail="源表与目标表没有可映射的同名列，已跳过写入",
        )

    col_index = {name: i for i, name in enumerate(columns)}
    values = [tuple(row[col_index[c]] for c in usable_columns) for row in rows]
    col_list = ", ".join(usable_columns)
    placeholders = ", ".join(["?"] * len(usable_columns))
    sql = f"INSERT OR IGNORE INTO {spec.table} ({col_list}) VALUES ({placeholders})"

    try:
        await conn.executemany(sql, values)
        await conn.commit()
    except Exception as exc:  # noqa: BLE001 - 需要把任意底层异常转成失败结果并回滚
        await conn.rollback()
        return TableResult(
            spec.table, spec.source_file, "failed", source_rows=len(rows),
            detail=f"写入失败已回滚：{exc}",
        )

    return TableResult(
        spec.table, spec.source_file, "migrated",
        source_rows=len(rows), migrated_rows=len(values), detail="迁移完成",
    )


async def consolidate(data_dir: str, app_db_path: str, dry_run: bool) -> ConsolidationReport:
    """执行（或 dry-run 报告）一次完整的多库→单库迁移。"""
    specs = build_migration_specs()
    backup_path = None
    conn: Optional[aiosqlite.Connection] = None

    try:
        if dry_run:
            if os.path.exists(app_db_path):
                conn = await _open_readonly(app_db_path)
        else:
            backup_path = _backup_app_db_if_exists(app_db_path)
            await _ensure_app_db_schema(app_db_path)
            conn = await aiosqlite.connect(app_db_path)
            conn.row_factory = aiosqlite.Row
            await conn.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE_WAL}")
            await conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")

        results = [await _migrate_one_table(conn, data_dir, spec, dry_run) for spec in specs]
    finally:
        if conn is not None:
            await conn.close()

    return ConsolidationReport(
        results=results, backup_path=backup_path, dry_run=dry_run,
        app_db_path=app_db_path, data_dir=data_dir,
    )


def print_report(report: ConsolidationReport) -> None:
    mode = "DRY-RUN（未写入任何文件）" if report.dry_run else "正式迁移"
    print(f"\n=== 多库 → 单库迁移报告（{mode}） ===")
    print(f"旧库目录: {report.data_dir}")
    print(f"目标 app.db: {report.app_db_path}")
    if report.backup_path:
        print(f"已备份原 app.db 至: {report.backup_path}")
    print()

    header = f"{'表名':<28}{'来源文件':<24}{'状态':<28}{'源行数':>8}{'已写入':>8}"
    print(header)
    print("-" * len(header))
    for r in report.results:
        label = _STATUS_LABELS.get(r.status, r.status)
        print(f"{r.table:<28}{r.source_file:<24}{label:<28}{r.source_rows:>8}{r.migrated_rows:>8}")
        if r.detail:
            print(f"    └─ {r.detail}")

    print()
    migrated = [r for r in report.results if r.status == "migrated"]
    would = [r for r in report.results if r.status == "would_migrate"]
    skipped = [r for r in report.results if r.status.startswith("skipped")]
    failed = [r for r in report.results if r.status == "failed"]
    print(
        f"汇总：迁移 {len(migrated)} 张表 / 共 {report.total_migrated_rows} 行"
        + (f"；dry-run 待迁移 {len(would)} 张表" if would else "")
        + f"；跳过 {len(skipped)} 张表；失败 {len(failed)} 张表"
    )
    if failed:
        print("失败表：" + ", ".join(f"{r.table}（{r.detail}）" for r in failed))


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多库 → 单库 app.db 一次性数据迁移脚本")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只报告将要发生的迁移，不写入任何文件（也不创建备份/不建 schema）",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="旧多库文件所在目录，默认使用 settings.DATABASE_DIR（即 data/）",
    )
    parser.add_argument(
        "--app-db", default=None,
        help="目标单库文件路径，默认使用 settings.APP_DB_PATH（即 data/app.db）",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    data_dir = args.data_dir or settings.DATABASE_DIR
    app_db_path = args.app_db or settings.APP_DB_PATH

    report = asyncio.run(consolidate(data_dir, app_db_path, args.dry_run))
    print_report(report)
    return 1 if report.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
