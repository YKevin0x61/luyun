#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理本机 PostgreSQL 里 pytest 按 PID 派生、会话结束不回收的测试库。

为什么需要它：``tests/conftest.py`` 在没给 ``LUYUN_TEST_DSN`` 时按进程号派生库名
``luyun_test_<pid>``（MERGE-01 / ticket 19）。测试进程退出后没有任何回收动作，
库和 schema 都留在实例里——本机已累积 1354 个库、合计 17 GB，而业务库 luyun 只有
391 MB，备份大小与磁盘水位都被它拖累。

安全边界（默认不动任何库）：
* 只认 ``datname`` **完全匹配** ``^luyun_test_[0-9]+$`` 的库；``luyun``、
  ``luyun_test``、``luyun_system_review``、``luyun_ui_audit`` 等具名库都不匹配。
* 默认 dry-run：只列出库名、大小、连接数与合计，不执行任何 DDL。
* 真要删必须显式 ``--yes``；``pg_stat_activity`` 里仍有连接的库跳过并报告，
  不硬断别人的会话。
* DSN 取 ``config.settings.POSTGRES_DSN``（与业务代码同一份配置）。本脚本只在
  运行时读配置，不改环境变量、不在导入期 import config。

用法：

    .venv/bin/python scripts/pg_cleanup_test_dbs.py          # dry-run，只列出来
    .venv/bin/python scripts/pg_cleanup_test_dbs.py --yes    # 真正执行 DROP DATABASE
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# pytest 测试库的库名形态：luyun_test_<纯数字 PID>。SQL 侧用它缩小枚举范围，
# Python 侧在执行 DROP 前再核一次——查询条件将来被放宽也不至于误删具名库。
TEST_DB_PATTERN = r"^luyun_test_[0-9]+$"
_TEST_DB_RE = re.compile(TEST_DB_PATTERN)

# 兜底黑名单：正则已经把具名库排除在外，这里再列一遍，让「绝不碰它们」的约束
# 在代码里可见（也挡住有人把 TEST_DB_PATTERN 改宽的情况）。
_PROTECTED_DBS = frozenset(
    {
        "luyun",
        "luyun_test",
        "luyun_system_review",
        "luyun_ui_audit",
        "postgres",
        "template0",
        "template1",
    }
)

# 一条查询同时取库、大小与当前连接数：连接数用来在 --yes 时跳过「还有人在用」的库。
_LIST_SQL = """
SELECT d.datname AS name,
       pg_database_size(d.oid) AS size_bytes,
       COALESCE(a.connections, 0) AS connections
  FROM pg_database d
  LEFT JOIN (
        SELECT datname, count(*) AS connections
          FROM pg_stat_activity
         WHERE datname IS NOT NULL
         GROUP BY datname
  ) a ON a.datname = d.datname
 WHERE d.datname ~ $1
 ORDER BY size_bytes DESC, d.datname
"""

_UNITS = ("B", "KB", "MB", "GB", "TB")


def _format_bytes(num: int) -> str:
    """字节数转人看的单位（1024 进制，两位小数）。"""
    value = float(num)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


def _describe_dsn(dsn: str) -> str:
    """只报 主机:端口/库名，不回显 DSN 里的账号口令。"""
    parsed = urlparse(dsn)
    host = parsed.hostname or "?"
    port = parsed.port or 5432
    database = (parsed.path or "").lstrip("/") or "?"
    return f"{host}:{port}/{database}"


def _load_dsn() -> str:
    """延迟导入配置取 DSN：--help 与参数错误时不需要配置和数据库可用。"""
    from config import settings

    dsn = (getattr(settings, "POSTGRES_DSN", "") or "").strip()
    if not dsn:
        raise SystemExit("config.settings.POSTGRES_DSN 为空，无法连接 PostgreSQL")
    return dsn


async def _fetch_candidates(dsn: str) -> tuple[list[dict], str]:
    """列出匹配的测试库：[(库名, 大小, 连接数)]，以及当前连接的库名。"""
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=15)
    try:
        rows = await conn.fetch(_LIST_SQL, TEST_DB_PATTERN)
        current_db = str(await conn.fetchval("SELECT current_database()") or "")
    finally:
        await conn.close()

    candidates = []
    for row in rows:
        name = str(row["name"])
        # 双保险：Python 侧再核一次库名与黑名单。
        if name in _PROTECTED_DBS or not _TEST_DB_RE.fullmatch(name):
            continue
        candidates.append(
            {
                "name": name,
                "size_bytes": int(row["size_bytes"] or 0),
                "connections": int(row["connections"] or 0),
            }
        )
    return candidates, current_db


def _print_list(candidates: list[dict]) -> int:
    """按大小降序打印候选库；返回合计字节数。"""
    name_width = max([len("库名")] + [len(item["name"]) for item in candidates])
    print(f"{'序号':>4}  {'库名':<{name_width}}  {'大小':>12}  {'连接':>4}")
    print(f"{'-' * 4}  {'-' * name_width}  {'-' * 12}  {'-' * 4}")
    for index, item in enumerate(candidates, 1):
        note = "  有连接，--yes 时跳过" if item["connections"] else ""
        print(
            f"{index:>4}  {item['name']:<{name_width}}  "
            f"{_format_bytes(item['size_bytes']):>12}  {item['connections']:>4}{note}"
        )
    return sum(item["size_bytes"] for item in candidates)


async def _drop_all(dsn: str, candidates: list[dict]) -> int:
    """执行 DROP DATABASE；有连接 / 不满足安全条件的跳过。返回退出码。"""
    import asyncpg

    dropped = 0
    freed = 0
    skipped: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []

    conn = await asyncpg.connect(dsn, timeout=15)
    try:
        for item in candidates:
            name = item["name"]
            if item["connections"]:
                skipped.append((name, f"仍有 {item['connections']} 个连接"))
                continue
            # 再核一次：查询条件将来被放宽也不至于误删具名库。
            if name in _PROTECTED_DBS or not _TEST_DB_RE.fullmatch(name):
                skipped.append((name, "不满足安全条件"))
                continue
            try:
                # 库名已被正则限定为 [a-z0-9_]，可直接加双引号拼进 DDL。
                await conn.execute(f'DROP DATABASE "{name}"')
            except Exception as exc:  # 权限不足 / 查看后又有人连上来：只报告，继续下一个
                failed.append((name, str(exc)))
                continue
            dropped += 1
            freed += item["size_bytes"]
            print(f"  已删除 {name}（{_format_bytes(item['size_bytes'])}）")
    finally:
        await conn.close()

    if skipped:
        print(f"\n跳过 {len(skipped)} 个库：")
        for name, reason in skipped:
            print(f"  {name}（{reason}）")
    if failed:
        print(f"\n删除失败 {len(failed)} 个库：")
        for name, reason in failed[:20]:
            print(f"  {name}：{reason}")
        if len(failed) > 20:
            print(f"  ……其余 {len(failed) - 20} 个失败项略")
    print(f"\n本次删除 {dropped} 个库，释放 {_format_bytes(freed)}。")
    return 1 if failed else 0


async def _run(args: argparse.Namespace) -> int:
    dsn = _load_dsn()
    print("清理 pytest 测试库（luyun_test_<pid>）")
    print(f"目标实例：{_describe_dsn(dsn)}（DSN 取自 config.settings.POSTGRES_DSN）")
    print(f"匹配条件：datname 完全匹配 {TEST_DB_PATTERN}；具名库一律不动。")

    try:
        candidates, current_db = await _fetch_candidates(dsn)
    except Exception as exc:
        print(f"连接 PostgreSQL 失败：{exc}")
        return 1

    if current_db in {item["name"] for item in candidates}:
        candidates = [item for item in candidates if item["name"] != current_db]
        print(f"注意：当前连接的库 {current_db} 本身也匹配，已排除（不能 DROP 自己所在的库）。")

    if not candidates:
        print("\n没有匹配的 luyun_test_<pid> 测试库，无需清理。")
        return 0

    total_bytes = _print_list(candidates)
    busy = [item for item in candidates if item["connections"]]
    print(
        f"\n共 {len(candidates)} 个测试库，合计 {_format_bytes(total_bytes)}；"
        f"其中 {len(busy)} 个仍有活动连接。"
    )

    if not args.yes:
        print("[dry-run] 未删除任何库。确认无误后加 --yes 真正执行 DROP DATABASE。")
        return 0

    print("\n--yes 已给出：开始执行 DROP DATABASE（有活动连接的库跳过，不硬断会话）。")
    return await _drop_all(dsn, candidates)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pg_cleanup_test_dbs.py",
        description=(
            "清理本机 PostgreSQL 里 pytest 按 PID 派生、会话结束不回收的 "
            "luyun_test_<pid> 测试库（默认 dry-run，不删任何库）。"
        ),
        epilog=(
            "示例：\n"
            "  .venv/bin/python scripts/pg_cleanup_test_dbs.py          # 只看会删哪些库\n"
            "  .venv/bin/python scripts/pg_cleanup_test_dbs.py --yes    # 真正执行 DROP DATABASE"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="真正执行 DROP DATABASE；不加时只 dry-run，不删任何库",
    )
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n已中断；尚未处理的库不会删除。")
        return 130
    except Exception as exc:
        print(f"执行失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
