#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite app.db → PostgreSQL 单次迁移（停机窗口内执行）。

用法::

    # 预演：只统计两侧行数与依赖顺序，不写库
    .venv/bin/python scripts/archive/migrate_sqlite_to_postgres.py --dry-run

    # 实际迁移
    .venv/bin/python scripts/archive/migrate_sqlite_to_postgres.py --apply

前置条件
--------
1. 目标 PG 库已应用 ``migrations/pg/0001_initial_schema.sql``；
2. **应用已停止写入**——迁移期间源库必须静止，否则会漏掉增量。

设计要点
--------
- 按外键依赖**拓扑排序**插入：PG 的 FK 是即时检查的，顺序错会直接失败
  （SQLite 默认不强制，所以源库的插入顺序没有参考价值）。
- ``tenant_id`` 交给 schema 的 ``DEFAULT 1``：单店数据整体落到默认租户，
  多店时再按门店拆分。
- 每张表先 ``TRUNCATE`` 再 ``COPY``，因此**可重复执行**。
- 结束后重置全部 identity 序列。显式写入 id 不会推进序列，漏掉这步会让后续
  INSERT 报主键冲突（详见 ``migrations/pg/README.md``）。
- 任何一步失败即中止；源库全程只读，所以**回滚就是继续用 SQLite**。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
import time
from typing import Dict, List, Optional, Sequence, Set

import asyncpg

DEFAULT_SQLITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "app.db",
)
DEFAULT_DSN = "postgresql://localhost:5432/luyun"

SETVAL_SQL = """
DO $$
DECLARE r RECORD; seq TEXT; maxid BIGINT;
BEGIN
  FOR r IN
    SELECT c.relname FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r' AND n.nspname = 'public'
      AND EXISTS (SELECT 1 FROM pg_attribute a
                  WHERE a.attrelid = c.oid AND a.attname = 'id' AND a.attnum > 0)
  LOOP
    seq := pg_get_serial_sequence('public.' || quote_ident(r.relname), 'id');
    IF seq IS NOT NULL THEN
      EXECUTE format('SELECT COALESCE(MAX(id), 1) FROM public.%I', r.relname)
        INTO maxid;
      PERFORM setval(seq, maxid);
    END IF;
  END LOOP;
END $$;
"""


def list_tables(conn: sqlite3.Connection) -> List[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def topological_order(conn: sqlite3.Connection, tables: Sequence[str]) -> List[str]:
    """按外键依赖排序；同层按名字，保证结果稳定。"""
    deps: Dict[str, Set[str]] = {t: set() for t in tables}
    known = set(tables)
    for table in tables:
        for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")'):
            parent = fk["table"]
            if parent in known and parent != table:
                deps[table].add(parent)

    ordered: List[str] = []
    while deps:
        ready = sorted(t for t, d in deps.items() if not d)
        if not ready:
            # 循环依赖：剩下的按名字处理（本项目没有，但不要静默丢表）
            ordered.extend(sorted(deps))
            break
        ordered.extend(ready)
        for name in ready:
            deps.pop(name)
        for remaining in deps.values():
            remaining.difference_update(ready)
    return ordered


def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


async def migrate(
    sqlite_path: str, dsn: str, *, apply: bool, tables_filter: Optional[Sequence[str]] = None
) -> int:
    if not os.path.isfile(sqlite_path):
        print(f"✗ 源库不存在: {sqlite_path}")
        return 1

    src = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    tables = list_tables(src)
    if tables_filter:
        wanted = set(tables_filter)
        tables = [t for t in tables if t in wanted]

    order = topological_order(src, tables)

    conn = await asyncpg.connect(dsn)
    try:
        pg_tables = {
            r["tablename"]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        missing = [t for t in order if t not in pg_tables]
        if missing:
            print(f"✗ 目标库缺少这些表（先应用 migrations/pg/0001_initial_schema.sql）: {missing}")
            return 1

        print(f"源库: {sqlite_path}")
        print(f"目标: {dsn}")
        print(f"表数: {len(order)}   模式: {'APPLY' if apply else 'DRY-RUN'}")
        print()
        print(f"  {'表':<34}{'源':>9}{'目标':>9}  说明")
        print(f"  {'-' * 34}{'-' * 9}{'-' * 9}  ----")

        total_src = total_pg = 0
        truncated: List[str] = []
        copied: List[str] = []

        for table in order:
            cols = table_columns(src, table)
            if not cols:
                continue
            rows = src.execute(
                f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{table}"'
            ).fetchall()
            src_count = len(rows)
            pg_before = await conn.fetchval(f'SELECT count(*) FROM "{table}"')
            total_src += src_count

            note = ""
            if not apply:
                note = "预演"
            elif src_count:
                await conn.execute(f'TRUNCATE "{table}" CASCADE')
                truncated.append(table)
                start = time.perf_counter()
                await conn.copy_records_to_table(
                    table, records=[tuple(r) for r in rows], columns=cols
                )
                copied.append(table)
                note = f"{(time.perf_counter() - start) * 1000:.0f} ms"
            else:
                await conn.execute(f'TRUNCATE "{table}" CASCADE')
                truncated.append(table)
                note = "空表"

            pg_after = await conn.fetchval(f'SELECT count(*) FROM "{table}"') if apply else pg_before
            total_pg += pg_after
            mark = "" if not apply or src_count == pg_after else "  ← 不一致"
            print(f"  {table:<34}{src_count:>9}{pg_after:>9}  {note}{mark}")

        print()
        print(f"  {'合计':<34}{total_src:>9}{total_pg:>9}")

        if not apply:
            print("\n预演完成，未写库。加 --apply 执行迁移。")
            return 0

        await conn.execute(SETVAL_SQL)
        print("\n✓ 已重置 identity 序列")

        mismatched = []
        for table in copied:
            cols = table_columns(src, table)
            src_count = src.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            pg_count = await conn.fetchval(f'SELECT count(*) FROM "{table}"')
            if src_count != pg_count:
                mismatched.append((table, src_count, pg_count))
        if mismatched:
            print("✗ 行数校验失败:")
            for table, s, p in mismatched:
                print(f"    {table}: 源 {s} / 目标 {p}")
            return 1

        print(f"✓ 行数校验通过（{len(copied)} 张表，{total_pg} 行）")
        print("\n下一步：把 DATABASE_BACKEND 切到 postgres 并启动应用；")
        print("源库 data/app.db 保持不动，回滚即切回 sqlite。")
        return 0
    finally:
        await conn.close()
        src.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 单次迁移")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    mode.add_argument("--apply", action="store_true", help="执行迁移")
    parser.add_argument("--sqlite", default=DEFAULT_SQLITE, help="源 app.db 路径")
    parser.add_argument(
        "--dsn", default=os.environ.get("LUYUN_POSTGRES_DSN", DEFAULT_DSN), help="目标 DSN"
    )
    parser.add_argument("--tables", nargs="*", help="只迁移指定表（默认全部）")
    args = parser.parse_args()

    return asyncio.run(
        migrate(args.sqlite, args.dsn, apply=args.apply, tables_filter=args.tables)
    )


if __name__ == "__main__":
    sys.exit(main())
