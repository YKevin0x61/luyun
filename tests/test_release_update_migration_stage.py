#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新作业的「应用待执行迁移」阶段：作业用**新树**的代码与解释器执行迁移入口。

为什么值得单独一个缝隙：更新作业进程在原子切换**之前**就启动了，跑的是旧树的代码；
而迁移 SQL 随刚装好的发行包下发，`services/db_migrations.py` 又是按自己的模块文件定位
`migrations/pg/` 的。所以作业不能调自己那份 `db_migrations`——只有拿新树里的入口跑一遍，
读到的才是这次要应用的那批文件（ADR 0096）。

用例分三层：
1. `DeployTreeMigrationsAdapter`：子进程怎么被拉起（新树为 cwd 与包搜索路径首位）、
   结果怎么解析、失败/超时/入口缺失怎么落到作业日志与异常。
2. `scripts/apply_db_migrations.py`：真实入口跑真实测试库的「有迁移 / 无迁移 / 迁移失败」
   三条路径（迁移目录用临时夹具，版本号取 99xx，与仓库里的真迁移互不干扰）。
3. 生产配置那条路：适配器 → 仓库树里的真入口 → 真 `migrations/pg` → 真库。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import services.db_migrations as db_migrations
from config import settings
from scripts import apply_db_migrations as entry

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRY_SCRIPT = REPO_ROOT / "scripts" / "apply_db_migrations.py"
RESULT_PREFIX = "LUYUN_DB_MIGRATION_RESULT "

# 夹具迁移的版本号：与仓库里的真迁移（0002…）不会撞，清理时按这几个号删记录。
FIXTURE_VERSIONS = ("9901", "9902", "9903")


def _result_payload(stdout: str) -> dict:
    """取结果标记行——stdout 只有这一行是给机器看的，其余（进度）走 stderr。"""
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            return json.loads(line[len(RESULT_PREFIX):])
    raise AssertionError(f"stdout 里没有迁移结果标记：{stdout!r}")


class DeployTreeMigrationsAdapterTest(unittest.TestCase):
    """适配器契约：用新树的解释器与代码跑新树的入口。"""

    def _stand_in_entry(self, tree: Path, body: str) -> Path:
        scripts = tree / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        path = scripts / "apply_db_migrations.py"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def test_runs_new_tree_entry_from_new_tree(self):
        """子进程的 cwd 与包搜索路径首位都必须是新树。

        否则 `import services.db_migrations` 拿到的可能是作业进程自己的旧树代码，
        `MIGRATIONS_DIR` 也就指向旧树——那正是这个阶段要避免的事。
        """
        from services.release_update.job_adapters import DeployTreeMigrationsAdapter

        with _tmp_tree() as tree:
            self._stand_in_entry(
                tree,
                """\
                import json
                import os
                with open(os.path.join(os.getcwd(), "seen.json"), "w") as fh:
                    json.dump({
                        "cwd": os.getcwd(),
                        "pythonpath": os.environ.get("PYTHONPATH", ""),
                    }, fh)
                print("LUYUN_DB_MIGRATION_RESULT " + json.dumps({
                    "ok": True, "applied": ["0005", "0006"], "pending_before": 2,
                    "failed": [], "migrations_dir": "tree/migrations/pg",
                }))
                """,
            )
            log_path = tree / "update_job.log"
            adapter = DeployTreeMigrationsAdapter(
                tree, python_bin=sys.executable, log_path=log_path
            )

            outcome = adapter.apply_pending()

            self.assertEqual(outcome.applied, ("0005", "0006"))
            seen = json.loads((tree / "seen.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(seen["cwd"]).resolve(), tree.resolve())
            self.assertEqual(
                Path(seen["pythonpath"].split(os.pathsep)[0]).resolve(), tree.resolve()
            )
            # 子进程的输出进作业日志：迁移失败时门店能在日志里看到原因。
            self.assertIn("0005", _log_text(log_path))

    def test_failed_entry_raises_with_the_failing_migration(self):
        from services.release_update.job_adapters import DeployTreeMigrationsAdapter

        with _tmp_tree() as tree:
            self._stand_in_entry(
                tree,
                """\
                import json
                import sys
                print("LUYUN_DB_MIGRATION_RESULT " + json.dumps({
                    "ok": False, "applied": [],
                    "failed": [{"version": "0005", "filename": "0005_scheduling.sql",
                                "error": "relation \\"staff_shifts\\" already exists"}],
                }))
                sys.exit(1)
                """,
            )
            log_path = tree / "update_job.log"
            adapter = DeployTreeMigrationsAdapter(
                tree, python_bin=sys.executable, log_path=log_path
            )

            with self.assertRaises(RuntimeError) as ctx:
                adapter.apply_pending()

            message = str(ctx.exception)
            self.assertIn("0005_scheduling.sql", message)
            self.assertIn("already exists", message)
            self.assertIn("0005_scheduling.sql", _log_text(log_path))

    def test_entry_without_result_marker_fails_with_exit_code(self):
        """入口被别的东西顶掉（或半路崩掉）时，失败原因里要有退出码，不能静默成功。"""
        from services.release_update.job_adapters import DeployTreeMigrationsAdapter

        with _tmp_tree() as tree:
            self._stand_in_entry(
                tree,
                """\
                import sys
                print("boom: cannot import asyncpg", file=sys.stderr)
                sys.exit(3)
                """,
            )
            adapter = DeployTreeMigrationsAdapter(
                tree, python_bin=sys.executable, log_path=tree / "update_job.log"
            )

            with self.assertRaises(RuntimeError) as ctx:
                adapter.apply_pending()

            self.assertIn("exit 3", str(ctx.exception))

    def test_missing_entry_in_bundle_is_reported_as_skip(self):
        """回退到本阶段出现之前的发行包：不改库、不报错，说明书里写明交给手工入口。

        这条路径必须是非失败的：把「旧发行包里没有这个脚本」判成迁移失败，会让
        「回到上一版本」这条合法退路永远走不通。
        """
        from services.release_update.job_adapters import DeployTreeMigrationsAdapter

        with _tmp_tree() as tree:
            log_path = tree / "update_job.log"
            adapter = DeployTreeMigrationsAdapter(
                tree, python_bin=sys.executable, log_path=log_path
            )

            outcome = adapter.apply_pending()

            self.assertEqual(outcome.applied, ())
            self.assertIn("apply_db_migrations.py", outcome.note)
            self.assertIn("apply_db_migrations.py", _log_text(log_path))

    def test_timeout_is_a_failure_not_a_hang(self):
        from services.release_update.job_adapters import DeployTreeMigrationsAdapter

        with _tmp_tree() as tree:
            self._stand_in_entry(
                tree,
                """\
                import time
                time.sleep(30)
                """,
            )
            adapter = DeployTreeMigrationsAdapter(
                tree,
                python_bin=sys.executable,
                log_path=tree / "update_job.log",
                timeout_seconds=1,
            )

            with self.assertRaises(RuntimeError) as ctx:
                adapter.apply_pending()

            self.assertIn("timed out", str(ctx.exception).lower())


@contextlib.contextmanager
def _tmp_tree():
    """一份临时「部署树」（只放夹具入口脚本）。"""
    with tempfile.TemporaryDirectory(prefix="luyun-migration-tree-") as raw:
        yield Path(raw)


def _log_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


# ── 真实入口 × 真实测试库：有迁移 / 无迁移 / 迁移失败 ──────────────────────────


def _write_fixture_migrations(root: Path, *, broken: bool = False) -> None:
    (root / "9901_fixture_index.sql").write_text(
        "CREATE INDEX IF NOT EXISTS idx_fixture_9901 "
        "ON hygiene_standards (capture_id);\n",
        encoding="utf-8",
    )
    (root / "9902_fixture_table.sql").write_text(
        "CREATE TABLE IF NOT EXISTS fixture_9902 (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    if broken:
        (root / "9903_fixture_broken.sql").write_text(
            "ALTER TABLE table_that_does_not_exist_9903 "
            "ADD COLUMN IF NOT EXISTS boom TEXT;\n",
            encoding="utf-8",
        )
    else:
        (root / "9903_fixture_column.sql").write_text(
            "ALTER TABLE hygiene_standards "
            "ADD COLUMN IF NOT EXISTS fixture_9903 TEXT;\n",
            encoding="utf-8",
        )


def _cleanup_fixture_migrations() -> None:
    """把夹具迁移在测试库留下的痕迹擦掉（版本号 99xx 与真迁移不会撞）。"""
    versions = ", ".join(f"'{v}'" for v in FIXTURE_VERSIONS)
    subprocess.run(
        [
            "psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", settings.POSTGRES_DSN, "-c",
            f"DELETE FROM schema_migrations WHERE version IN ({versions});"
            " DROP INDEX IF EXISTS idx_fixture_9901;"
            " ALTER TABLE hygiene_standards DROP COLUMN IF EXISTS fixture_9903;"
            " DROP TABLE IF EXISTS fixture_9902;",
        ],
        capture_output=True,
        text=True,
    )


@contextlib.contextmanager
def _root_logging_unchanged():
    """入口是个 CLI，会 force 重配 root logger；在进程内调它不能把 pytest 的配置带走。"""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


def test_entry_applies_pending_migrations_then_reports_none_left(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(db_migrations, "MIGRATIONS_DIR", tmp_path)
    _write_fixture_migrations(tmp_path)
    try:
        with _root_logging_unchanged():
            exit_code = entry.main()
        payload = _result_payload(capsys.readouterr().out)

        assert exit_code == 0
        assert payload["ok"] is True
        assert payload["applied"] == ["9901", "9902", "9903"]
        assert payload["pending_before"] == 3
        assert payload["migrations_dir"] == str(tmp_path)

        # 记进 schema_migrations 之外，DDL 也要真的落库（迁移的产出就是库结构本身）。
        import pg_probe

        assert pg_probe.scalar("SELECT to_regclass('fixture_9902')::text") == "fixture_9902"

        # 第二次：应用记录已被读到，待应用为空——阶段据此跳过，作业照常重启。
        with _root_logging_unchanged():
            exit_code = entry.main()
        second = _result_payload(capsys.readouterr().out)

        assert exit_code == 0
        assert second["ok"] is True
        assert second["pending_before"] == 0
        assert second["applied"] == []
    finally:
        _cleanup_fixture_migrations()


def test_entry_stops_at_failed_migration_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db_migrations, "MIGRATIONS_DIR", tmp_path)
    _write_fixture_migrations(tmp_path, broken=True)
    try:
        with _root_logging_unchanged():
            exit_code = entry.main()
        payload = _result_payload(capsys.readouterr().out)

        assert exit_code == 1
        assert payload["ok"] is False
        assert payload["applied"] == ["9901", "9902"], "失败前成功的照实报告"
        assert payload["failed"][0]["version"] == "9903"
        assert "table_that_does_not_exist_9903" in payload["failed"][0]["error"]

        # 失败的那条没有记进 schema_migrations：下一次仍以「待应用」出现。
        with _root_logging_unchanged():
            exit_code = entry.main()
        again = _result_payload(capsys.readouterr().out)
        assert exit_code == 1
        assert again["pending_before"] == 1
    finally:
        _cleanup_fixture_migrations()


def test_entry_reports_connection_failure_as_result_line_not_traceback():
    """入口作为独立脚本被拉起时，连不上库也要给结果标记 + 非 0 退出。

    更新作业的适配器就是按这两样判成败的；这里用真实子进程 + 不存在的库名跑一次，
    把「脚本能独立启动」这件事也钉住。
    """
    env = dict(os.environ)
    env["POSTGRES_DSN"] = f"postgresql://localhost:5432/luyun_no_such_db_{os.getpid()}"
    env.pop("LUYUN_POSTGRES_DSN", None)

    completed = subprocess.run(
        [sys.executable, str(ENTRY_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
        timeout=120,
    )

    assert completed.returncode == 1
    payload = _result_payload(completed.stdout)
    assert payload["ok"] is False
    assert payload["error"]
    assert "Traceback" not in completed.stdout


def _recorded_versions() -> set:
    import pg_probe

    try:
        return {row[0] for row in pg_probe.fetch_all("SELECT version FROM schema_migrations")}
    except Exception:  # 表还不存在 = 一条都没记过
        return set()


def _psql(sql: str) -> None:
    subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", settings.POSTGRES_DSN, "-c", sql],
        capture_output=True,
        text=True,
    )


def _forget_versions(versions) -> None:
    if not versions:
        return
    quoted = ", ".join(f"'{v}'" for v in sorted(versions))
    _psql(f"DELETE FROM schema_migrations WHERE version IN ({quoted});")


def _record_versions(versions) -> None:
    """把给定版本记成「已应用」——内容取自发行包自己的扫描结果（filename/checksum）。"""
    rows = [
        f"('{item.version}', '{item.filename}', '{item.checksum}', '2026-01-01T00:00:00+08:00')"
        for item in db_migrations.list_migration_files()
        if item.version in versions
    ]
    if not rows:
        return
    _psql(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, filename TEXT NOT NULL,"
        " checksum TEXT NOT NULL, applied_at TEXT NOT NULL);"
        " INSERT INTO schema_migrations (version, filename, checksum, applied_at) VALUES "
        + ", ".join(rows)
        + " ON CONFLICT (version) DO NOTHING;"
    )


def test_adapter_applies_the_real_trees_pending_migrations(tmp_path):
    """生产配置的那条路：适配器 → 新树里的入口 → 新树 ``migrations/pg`` → 真库。

    这是本票最关键的一环：作业进程跑的是旧树代码，只有用**新树**里的入口跑一遍，
    读到的才是随发行包下来的迁移文件。这里不注入任何夹具目录，直接拿仓库树当部署树
    跑真实的迁移脚本，覆盖「有迁移 → 一次更新即完成」与「无迁移 → 空列表跳过」。

    用例自己把「有迁移」摆出来（清掉真实迁移的应用记录），结束时把 ``schema_migrations``
    恢复成进来时的样子：库里那份记录是整个会话共享的（conftest 不清理它），不能让本用例的
    结果取决于别的文件跑没跑过。
    """
    from services.release_update.job_adapters import DeployTreeMigrationsAdapter

    # 子进程继承这份 DSN；`.env` 里指的是真库 `luyun`，env 优先级更高（conftest 钉死）。
    assert "luyun_test" in os.environ["POSTGRES_DSN"]

    recorded_before = _recorded_versions()
    real_versions = {
        item.version
        for item in db_migrations.list_migration_files()
        if not item.bootstrap_only
    }
    adapter = DeployTreeMigrationsAdapter(
        REPO_ROOT, python_bin=sys.executable, log_path=tmp_path / "update_job.log"
    )
    applied = ()
    try:
        _forget_versions(real_versions)  # 全是待应用 —— 门店还没点过迁移的样子

        outcome = adapter.apply_pending()
        applied = outcome.applied

        assert set(applied) == real_versions, "一次更新应当把本发行包里的增量脚本全部应用"
        assert "0002" in _log_text(tmp_path / "update_job.log")

        # 再跑一次：应用记录已落库，待应用为空 —— 这正是「无待应用迁移，跳过」那条路。
        second = adapter.apply_pending()

        assert second.applied == ()
    finally:
        _forget_versions(real_versions - recorded_before)
        _record_versions(recorded_before)
