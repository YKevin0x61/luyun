#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前自检：git 工作区、迁移覆盖、未跟踪依赖三项硬检查。

为什么需要它：发行包按 ``git archive HEAD`` 打包（ADR 0011 / ADR 0096），所以
「工作区有未提交改动」「迁移脚本没提交」「已跟踪代码 import 了未跟踪模块」都会
打出起不来的包——本地能跑，包里缺文件。2026-11 本机就同时占着三样：164 项未
提交改动、21 个未跟踪 .py（``main.py`` 依赖未跟踪的 ``services/export_jobs.py``），
``migrations/pg/0059``、``0060`` 未提交而真库 ``schema_migrations`` 已到 0060。

三项检查（任一失败退出码 1）：
1. git 工作区是否干净——有未提交改动就失败，列出前 20 项。
2. ``migrations/pg/*.sql`` 的编号集合是否覆盖真库 ``schema_migrations`` 里已应用的
   编号——真库已应用但本地脚本缺失的，逐条报出来。
3. 已跟踪代码 import 的本地模块里，是否有文件本身未被 ``git ls-files`` 跟踪。

``--skip-db`` 只跑静态检查（1、3），用于没有数据库可连的环境；第 2 项显示为跳过，
不计入失败。第 2 项只对真库做只读查询（``BEGIN READ ONLY``），不改任何数据。

用法：

    .venv/bin/python scripts/check_release_readiness.py --skip-db
    .venv/bin/python scripts/check_release_readiness.py
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 失败时最多列多少条明细（工作区改动、未跟踪依赖的 import 点）。
MAX_LIST_ITEMS = 20


def _git(*args: str) -> str:
    """在仓库根执行一条 git 只读命令；失败抛 RuntimeError（带 stderr）。"""
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} 失败：{message}")
    return completed.stdout


# ── 检查 1：工作区是否干净 ─────────────────────────────────────────────────


def check_git_clean() -> tuple[bool, list[str]]:
    """发行包按 HEAD 打包，工作区不干净就没有可信的「这次发的是什么」。"""
    try:
        output = _git("status", "--porcelain")
    except (OSError, RuntimeError) as exc:
        return False, [f"无法读取 git 状态：{exc}"]

    items = [line for line in output.splitlines() if line.strip()]
    if not items:
        return True, ["工作区干净，没有未提交改动。"]

    details = [
        f"有 {len(items)} 项未提交改动（发行包按 git archive HEAD 打包，这些改动不会进包）："
    ]
    details.extend(f"  {line}" for line in items[:MAX_LIST_ITEMS])
    if len(items) > MAX_LIST_ITEMS:
        details.append(f"  ……其余 {len(items) - MAX_LIST_ITEMS} 项见 `git status --porcelain`")
    return False, details


# ── 检查 2：本地迁移脚本是否覆盖真库已应用的编号 ───────────────────────────


def _describe_dsn(dsn: str) -> str:
    """只报 主机:端口/库名，不回显 DSN 里的账号口令。"""
    parsed = urlparse(dsn)
    host = parsed.hostname or "?"
    port = parsed.port or 5432
    database = (parsed.path or "").lstrip("/") or "?"
    return f"{host}:{port}/{database}"


def _load_dsn() -> str:
    """延迟导入配置取 DSN：--help 时不需要配置文件存在。"""
    from config import settings

    return (getattr(settings, "POSTGRES_DSN", "") or "").strip()


def _fetch_applied_migrations(dsn: str):
    """只读读取真库 schema_migrations；表不存在返回 None。"""
    import asyncpg

    async def _fetch():
        conn = await asyncpg.connect(dsn, timeout=10)
        try:
            # 只读事务：发布自检这条链路不允许改真库。
            await conn.execute("BEGIN READ ONLY")
            exists = await conn.fetchval("SELECT to_regclass('public.schema_migrations')")
            if exists is None:
                return None
            rows = await conn.fetch(
                "SELECT version, filename FROM schema_migrations ORDER BY version"
            )
            return {str(row["version"]): str(row["filename"] or "") for row in rows}
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def check_migrations() -> tuple[bool, list[str]]:
    """真库已应用的迁移编号必须都在本地 migrations/pg 里有对应脚本。"""
    from services.db_migrations import list_migration_files

    files = list_migration_files()
    local = {item.version: item.filename for item in files}
    details: list[str] = []
    if local:
        details.append(
            f"本地 migrations/pg：{len(local)} 个脚本（{min(local)}–{max(local)}）。"
        )
    else:
        details.append("本地 migrations/pg：没有可识别的迁移脚本。")

    dsn = _load_dsn()
    if not dsn:
        return False, details + ["config.settings.POSTGRES_DSN 为空，无法核对 schema_migrations。"]
    details.append(
        f"真库：{_describe_dsn(dsn)}（DSN 取自 config.settings.POSTGRES_DSN，只读查询）。"
    )

    try:
        applied = _fetch_applied_migrations(dsn)
    except Exception as exc:
        return False, details + [f"连接或读取 schema_migrations 失败：{exc}"]

    if applied is None:
        details.append("真库里还没有 schema_migrations 表（尚未应用任何迁移），无需对账。")
        return True, details

    details.append(f"真库 schema_migrations：已应用 {len(applied)} 条。")
    missing = [version for version in sorted(applied) if version not in local]
    if missing:
        details.append("真库已应用、本地 migrations/pg 缺失的版本（按当前代码发版，包会起不来）：")
        details.extend(
            f"  {version}（库内文件名：{applied[version] or '未知'}）" for version in missing
        )
        return False, details

    # 本地有、真库没应用的脚本不算失败：部署时由更新作业的「数据库迁移」阶段应用。
    # 0001 是 bootstrap（含 DROP TABLE），只在初次建库时用，不算待应用。
    bootstrap = {item.version for item in files if item.bootstrap_only}
    pending = [
        version
        for version in sorted(local)
        if version not in applied and version not in bootstrap
    ]
    if pending:
        shown = "、".join(pending[:10])
        if len(pending) > 10:
            shown += f" 等 {len(pending)} 条"
        details.append(
            f"本地有 {len(pending)} 条脚本尚未应用到真库（部署时由更新作业应用）：{shown}。"
        )
    else:
        details.append("本地脚本覆盖了真库已应用的全部编号，没有缺口。")

    # 附加提示：本地脚本未被 git 跟踪时，git archive HEAD 不会带上它——那种「本地
    # 看得到、包里没有」和缺版本同样致命（工作区检查也会因此失败，这里点名）。
    try:
        tracked = {
            path for path in _git("ls-files", "-z", "--", "migrations/pg").split("\0") if path
        }
    except (OSError, RuntimeError):
        tracked = set()
    untracked = [
        item.filename for item in files if f"migrations/pg/{item.filename}" not in tracked
    ]
    if untracked:
        details.append(
            "注意：以下本地迁移脚本未被 git 跟踪，git archive HEAD 打出的发行包不会包含它们："
            + "、".join(untracked)
            + "（先提交）。"
        )
    return True, details


# ── 检查 3：已跟踪代码是否 import 了未跟踪的本地模块 ───────────────────────


def _tracked_files() -> list[str]:
    return [path for path in _git("ls-files", "-z").split("\0") if path]


def _package_parts(relative_path: str) -> tuple[str, ...]:
    """文件所在包的目录部分（__init__.py 与普通模块同口径）。"""
    return tuple(relative_path.split("/")[:-1])


def _relative_base(package_parts: tuple[str, ...], level: int):
    """相对 import 的基准包；越界（非法相对导入）返回 None。"""
    depth = level - 1
    if depth > len(package_parts):
        return None
    return package_parts[: len(package_parts) - depth]


def _resolve_module_path(parts: tuple[str, ...]):
    """把模块名映射到仓库里的 .py 文件；不是本地模块（标准库/三方）返回 None。"""
    if not parts:
        return None
    base = ROOT.joinpath(*parts)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _iter_import_candidates(tree: ast.AST, package_parts: tuple[str, ...]):
    """产出 (模块名路径分段, 行号)：绝对/相对 import 都展开成仓库内的模块名。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield tuple(alias.name.split(".")), node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _relative_base(package_parts, node.level)
                if base is None:
                    continue
            else:
                base = ()
            module_parts = tuple(base)
            if node.module:
                module_parts += tuple(node.module.split("."))
            if module_parts:
                yield module_parts, node.lineno
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    yield module_parts + (alias.name,), node.lineno


def check_untracked_imports() -> tuple[bool, list[str]]:
    """已跟踪代码 import 的本地模块必须是已跟踪文件，否则发行包里缺文件。"""
    try:
        tracked = _tracked_files()
        tracked_set = set(tracked)
    except (OSError, RuntimeError) as exc:
        return False, [f"无法读取 git ls-files：{exc}"]

    python_files = [path for path in tracked if path.endswith(".py")]
    # 未跟踪模块文件 -> 引用它的已跟踪代码位置（path:line），保持发现顺序。
    problems: dict[str, list[str]] = {}
    parse_failures: list[str] = []
    for relative_path in python_files:
        path = ROOT / relative_path
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative_path)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            parse_failures.append(f"{relative_path}（{exc.__class__.__name__}）")
            continue
        package_parts = _package_parts(relative_path)
        for module_parts, lineno in _iter_import_candidates(tree, package_parts):
            resolved = _resolve_module_path(module_parts)
            if resolved is None:
                continue
            target = resolved.relative_to(ROOT).as_posix()
            if target in tracked_set:
                continue
            sites = problems.setdefault(target, [])
            site = f"{relative_path}:{lineno}"
            if site not in sites:
                sites.append(site)

    details: list[str] = []
    if problems:
        details.append(
            f"发现 {len(problems)} 个本地模块文件未跟踪、却被已跟踪代码 import"
            "（git archive HEAD 打出的包里没有它们）："
        )
        for index, (target, sites) in enumerate(sorted(problems.items()), 1):
            details.append(f"{index}. {target}")
            shown = "、".join(sites[:5])
            if len(sites) > 5:
                shown += f" 等 {len(sites)} 处"
            details.append(f"   被 import：{shown}")
    else:
        details.append(
            f"{len(python_files)} 个已跟踪 .py 文件里，没有 import 未跟踪的本地模块。"
        )
    if parse_failures:
        shown = "、".join(parse_failures[:3])
        if len(parse_failures) > 3:
            shown += f" 等 {len(parse_failures)} 个"
        details.append(
            f"（另有 {len(parse_failures)} 个已跟踪文件解析失败，未纳入扫描：{shown}）"
        )
    return not problems, details


# ── 入口 ──────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_release_readiness.py",
        description=(
            "发布前自检：git 工作区是否干净、migrations/pg 是否覆盖真库 "
            "schema_migrations、已跟踪代码是否 import 了未跟踪的本地模块。"
            "任一失败退出码 1。"
        ),
        epilog=(
            "示例：\n"
            "  .venv/bin/python scripts/check_release_readiness.py --skip-db   # 只跑静态检查\n"
            "  .venv/bin/python scripts/check_release_readiness.py             # 含真库只读对账"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="跳过数据库检查（第 2 项），只跑 git 与 import 两项静态检查",
    )
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    checks = [
        ("git 工作区是否干净", check_git_clean),
        (
            "migrations/pg 是否覆盖真库 schema_migrations 已应用的编号",
            None if args.skip_db else check_migrations,
        ),
        ("已跟踪代码是否 import 了未跟踪的本地模块", check_untracked_imports),
    ]

    failed = 0
    passed = 0
    skipped = 0
    for index, (title, checker) in enumerate(checks, 1):
        print(f"[{index}/{len(checks)}] {title}")
        if checker is None:
            print("  已跳过（--skip-db：只跑静态检查，不连数据库）")
            print("  → 跳过")
            skipped += 1
            continue
        try:
            ok, details = checker()
        except Exception as exc:  # 检查本身出错按失败处理，不静默放过
            ok, details = False, [f"检查本身出错：{exc}"]
        for line in details:
            print(f"  {line}")
        print("  → 通过" if ok else "  → 失败")
        if ok:
            passed += 1
        else:
            failed += 1

    print()
    if failed:
        print(f"结论：未就绪（失败 {failed} 项 / 通过 {passed} 项 / 跳过 {skipped} 项）。")
        print(
            "发行包按 git archive HEAD 打包：先提交/清理工作区、补齐迁移脚本与"
            "未跟踪模块，再发布。"
        )
        return 1
    print(f"结论：通过（通过 {passed} 项 / 跳过 {skipped} 项）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
