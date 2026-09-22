#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库迁移面板：待应用清单要准，bootstrap 脚本绝不能被执行。

背景见 services/db_migrations.py。这里的用例锁住四件事：
1. ``0001``（DROP + CREATE）永远只出现在 ``bootstrap_only``，不进待应用；
2. 已应用的不再出现在待应用；文件内容变了只报 ``changed``，不自动重跑；
3. SQLite 后端明确返回「无需手工应用」而不是给一份假清单；
4. 执行失败时回滚并停在那一条，已成功的照实报告。
"""

import asyncio

import pytest

import services.db_migrations as db_migrations
from config import settings
from services.db_migrations import (
    apply_pending_migrations,
    list_migration_files,
    migration_status,
)


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, applied_rows=None, fail_on=None):
        self.calls = []
        self._applied_rows = applied_rows or []
        self._fail_on = fail_on
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, sql, params=None):
        text = sql.strip()
        self.calls.append({"sql": text, "params": params})
        if self._fail_on and self._fail_on in text:
            raise RuntimeError("boom")
        if "FROM schema_migrations" in text:
            return FakeCursor(self._applied_rows)
        return FakeCursor([])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeDb:
    def __init__(self, conn):
        self._conn = conn


def _write_migrations(root, *, bootstrap=True):
    if bootstrap:
        (root / "0001_initial_schema.sql").write_text(
            "-- luyun:bootstrap-only\nDROP TABLE IF EXISTS x;\nCREATE TABLE x (id INT);\n",
            encoding="utf-8",
        )
    (root / "0002_hygiene_indexes.sql").write_text(
        "CREATE INDEX IF NOT EXISTS idx_a ON hygiene_standards (capture_id);\n",
        encoding="utf-8",
    )
    (root / "0003_reason.sql").write_text(
        "ALTER TABLE hygiene_board_events ADD COLUMN IF NOT EXISTS reason TEXT;\n",
        encoding="utf-8",
    )


@pytest.fixture
def pg(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_BACKEND", "postgres")
    monkeypatch.setattr(db_migrations, "MIGRATIONS_DIR", tmp_path)
    _write_migrations(tmp_path)
    return tmp_path


def test_the_real_bootstrap_file_is_marked(tmp_path):
    """仓库里真实的 0001 必须带标记，否则界面会给出一键清库的按钮。"""
    files = list_migration_files()
    bootstrap = [item for item in files if item.bootstrap_only]
    assert [item.version for item in bootstrap] == ["0001"]
    assert "0001" not in [item.version for item in files if not item.bootstrap_only]


def test_pending_excludes_applied_and_bootstrap(pg):
    conn = FakeConn(applied_rows=[{
        "version": "0002",
        "filename": "0002_hygiene_indexes.sql",
        "checksum": "",   # 故意与当前文件不一致 → 应进 changed
        "applied_at": "2026-09-20T10:00:00+08:00",
    }])
    status = _run(migration_status(FakeDb(conn)))

    assert status.supported is True
    assert [item.version for item in status.pending] == ["0003"]
    assert [item.version for item in status.bootstrap_only] == ["0001"]
    assert [item.version for item in status.changed] == ["0002"], "内容变了只报告，不自动重跑"
    assert [item.version for item in status.applied] == ["0002"]


def test_files_are_listed_in_version_order(pg):
    versions = [item.version for item in list_migration_files(pg)]
    assert versions == ["0001", "0002", "0003"]


def test_incremental_total_counts_non_bootstrap(pg):
    status = _run(migration_status(FakeDb(FakeConn())))
    assert status.incremental_total == 2
    assert status.as_dict()["incremental_total"] == 2


def test_empty_package_is_distinguishable_from_up_to_date(pg):
    """包里只剩 bootstrap 时必须能看出来，否则「没检查到」会被读成「已最新」。

    发行包按 ``git archive HEAD`` 打包：忘了提交迁移脚本，门店的面板就会看到
    一个空目录——那时它必须说"本包内没有增量脚本"，而不是"schema 已是最新"。
    """
    for name in ("0002_hygiene_indexes.sql", "0003_reason.sql"):
        (pg / name).unlink()
    status = _run(migration_status(FakeDb(FakeConn())))

    assert status.incremental_total == 0
    assert status.pending == []
    assert status.applied == []
    assert [item.version for item in status.bootstrap_only] == ["0001"]


def test_non_postgres_backend_still_reports_migrations(pg, monkeypatch):
    """SQLite 退场后（ADR 0089）迁移面板不再有「由启动自愈」的短路分支。"""
    monkeypatch.setattr(settings, "DATABASE_BACKEND", "sqlite")
    conn = FakeConn()
    status = _run(migration_status(FakeDb(conn)))

    assert status.supported is True
    assert conn.calls, "任何后端都要去查迁移记录表"


def test_apply_runs_in_order_and_records_each(pg):
    conn = FakeConn()
    result = _run(apply_pending_migrations(FakeDb(conn)))

    assert result.ok is True
    assert [item.version for item in result.applied] == ["0002", "0003"]

    executed = [call["sql"] for call in conn.calls]
    assert any("CREATE INDEX IF NOT EXISTS idx_a" in sql for sql in executed)
    assert any("ADD COLUMN IF NOT EXISTS reason" in sql for sql in executed)
    assert not any("DROP TABLE IF EXISTS x" in sql for sql in executed), "bootstrap 绝不能跑"

    recorded = [call["params"] for call in conn.calls if call["params"] and "INSERT INTO schema_migrations" in call["sql"]]
    assert [params[0] for params in recorded] == ["0002", "0003"]
    assert conn.commits >= 3


def test_apply_stops_at_the_first_failure(pg):
    conn = FakeConn(fail_on="ADD COLUMN IF NOT EXISTS reason")
    result = _run(apply_pending_migrations(FakeDb(conn)))

    assert result.ok is False
    assert [item.version for item in result.applied] == ["0002"]
    assert result.failed[0]["version"] == "0003"
    assert "boom" in result.failed[0]["error"]
    assert conn.rollbacks >= 1, "失败要回滚，不能留半截 DDL"


def test_apply_with_only_limits_the_batch(pg):
    conn = FakeConn()
    result = _run(apply_pending_migrations(FakeDb(conn), only=["0003"]))

    assert [item.version for item in result.applied] == ["0003"]
    executed = " ".join(call["sql"] for call in conn.calls)
    assert "idx_a" not in executed


# ── 版本检测顺带提示待应用迁移 ──────────────────────────────────────────────


def _version_check_result():
    from services.release_update import (
        UpdatePreflight,
        VersionCheckResult,
    )

    return VersionCheckResult(
        installed_tag="v0.6.4",
        degraded=False,
        degraded_reason=None,
        app_version="0.6.4",
        releases=[],
        latest_tag="v0.6.5",
        update_available=True,
        preflight=UpdatePreflight(
            checks=[],
            healthy_runtime=True,
            apply_allowed=True,
            dirty_tree=False,
            discard_local_changes_allowed=False,
        ),
        catalogue_ok=True,
    )


def test_version_check_payload_carries_pending_migrations():
    from api import release_update as api_module

    payload = api_module._version_check_payload(
        _version_check_result(),
        {
            "supported": True,
            "count": 2,
            "versions": ["0003", "0004"],
            "filenames": ["0003_a.sql", "0004_b.sql"],
            "note": "",
        },
    )
    assert payload["pending_migrations"]["count"] == 2
    assert payload["pending_migrations"]["versions"] == ["0003", "0004"]
    # 关键：它不能进 preflight，否则「忘了点迁移」会变成「不能发版」
    assert "pending_migrations" not in payload["preflight"]


def test_version_check_payload_defaults_to_no_pending():
    from api import release_update as api_module

    payload = api_module._version_check_payload(_version_check_result())
    assert payload["pending_migrations"]["count"] == 0
    assert payload["pending_migrations"]["supported"] is False


def test_migration_hint_never_breaks_version_check(monkeypatch):
    from api import release_update as api_module

    async def boom(_db):
        raise RuntimeError("db down")

    monkeypatch.setattr(db_migrations, "migration_status", boom)
    hint = _run(api_module._migration_hint(FakeDb(FakeConn())))

    assert hint["supported"] is False
    assert hint["count"] == 0
    assert "db down" in hint["error"]


def test_migration_hint_reports_pending(pg):
    from api import release_update as api_module

    conn = FakeConn()
    hint = _run(api_module._migration_hint(FakeDb(conn)))

    assert hint["supported"] is True
    assert hint["versions"] == ["0002", "0003"]
    assert hint["filenames"] == ["0002_hygiene_indexes.sql", "0003_reason.sql"]
