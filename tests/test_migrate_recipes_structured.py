import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from scripts import migrate_recipes_structured as mrs
from services.recipes.sop_parse import migrate_legacy_to_structured
from services.recipes.store import RecipeStore


def test_qty_list_lines_become_ingredients():
    body = "面粉 200g\n水 300ml\n盐 2g"
    got = migrate_legacy_to_structured(body)
    assert got == {
        "ingredients": [
            {"name": "面粉", "amount": "200", "unit": "g"},
            {"name": "水", "amount": "300", "unit": "ml"},
            {"name": "盐", "amount": "2", "unit": "g"},
        ],
        "steps": [],
        "tips": [],
    }


def test_gfm_table_uses_first_column_as_name():
    body = (
        "| 用料 | 用量 |\n"
        "| --- | --- |\n"
        "| 面粉 | 200g |\n"
        "| 水 | 300ml |\n"
    )
    got = migrate_legacy_to_structured(body)
    assert got["tips"] == []
    assert got["ingredients"] == [
        {"name": "面粉", "amount": "200", "unit": "g"},
        {"name": "水", "amount": "300", "unit": "ml"},
    ]
    assert got["steps"] == []


def test_gfm_one_data_row_uses_header_cells_as_names():
    body = (
        "| 面粉 | 水 |\n"
        "| --- | --- |\n"
        "| 200g | 300ml |\n"
    )
    got = migrate_legacy_to_structured(body)
    assert got["tips"] == []
    assert got["ingredients"] == [
        {"name": "面粉", "amount": "200", "unit": "g"},
        {"name": "水", "amount": "300", "unit": "ml"},
    ]
    assert got["steps"] == []


def test_gfm_one_data_row_with_column_labels_uses_first_col_as_name():
    body = (
        "| 用料 | 用量 |\n"
        "| --- | --- |\n"
        "| 酱油 | 100g |\n"
    )
    got = migrate_legacy_to_structured(body)
    assert got["ingredients"] == [
        {"name": "酱油", "amount": "100", "unit": "g"},
    ]
    assert got["steps"] == []
    assert got["tips"] == []


def test_prose_only_body_becomes_steps_with_no_ingredients():
    body = "将面粉与水和成面团，静置十分钟后擀开。\n注意火候不要太大。"
    got = migrate_legacy_to_structured(body)
    assert got == {
        "ingredients": [],
        "steps": [
            "将面粉与水和成面团，静置十分钟后擀开。",
            "注意火候不要太大。",
        ],
        "tips": [],
    }


def test_mixed_qty_lines_and_prose():
    body = "面粉 200g\n水 300ml\n将面粉与水混合均匀。"
    got = migrate_legacy_to_structured(body)
    assert got["tips"] == []
    assert got["ingredients"] == [
        {"name": "面粉", "amount": "200", "unit": "g"},
        {"name": "水", "amount": "300", "unit": "ml"},
    ]
    assert got["steps"] == ["将面粉与水混合均匀。"]


def test_qty_in_middle_of_sentence_stays_in_steps():
    body = "将面粉 200g 与水和成面团"
    got = migrate_legacy_to_structured(body)
    assert got["ingredients"] == []
    assert got["steps"] == ["将面粉 200g 与水和成面团"]
    assert got["tips"] == []


def test_pipe_rows_without_header_parse_as_qty_lines():
    body = "| 面粉 200g |\n| 水 300ml |"
    got = migrate_legacy_to_structured(body)
    assert got["ingredients"] == [
        {"name": "面粉", "amount": "200", "unit": "g"},
        {"name": "水", "amount": "300", "unit": "ml"},
    ]
    assert got["steps"] == []
    assert got["tips"] == []


def test_colon_qty_line_and_range_and_fraction():
    body = "酱油：100g\n葱 2-3根\n酵母 1/2勺"
    got = migrate_legacy_to_structured(body)
    assert got["ingredients"] == [
        {"name": "酱油", "amount": "100", "unit": "g"},
        {"name": "葱", "amount": "2-3", "unit": "根"},
        {"name": "酵母", "amount": "1/2", "unit": "勺"},
    ]
    assert got["steps"] == []
    assert got["tips"] == []


def test_table_row_without_qty_goes_to_steps():
    body = (
        "| 步骤 | 说明 |\n"
        "| --- | --- |\n"
        "| 1 | 将面粉与水和成面团 |\n"
        "| 2 | 静置十分钟 |\n"
    )
    got = migrate_legacy_to_structured(body)
    assert got["ingredients"] == []
    assert got["steps"] == [
        "1 | 将面粉与水和成面团",
        "2 | 静置十分钟",
    ]
    assert got["tips"] == []


def test_empty_body_returns_empty_lists():
    assert migrate_legacy_to_structured("") == {
        "ingredients": [],
        "steps": [],
        "tips": [],
    }
    assert migrate_legacy_to_structured("   \n\n") == {
        "ingredients": [],
        "steps": [],
        "tips": [],
    }


def test_never_guesses_tips():
    body = "小贴士：夏天水温要更低\n面粉 200g"
    got = migrate_legacy_to_structured(body)
    assert got["tips"] == []
    assert {"name": "面粉", "amount": "200", "unit": "g"} in got["ingredients"]
    assert "小贴士：夏天水温要更低" in got["steps"]


def test_list_marker_qty_line_is_ingredient():
    got = migrate_legacy_to_structured("- 面粉 200g\n1. 水 300ml")
    assert got["ingredients"] == [
        {"name": "面粉", "amount": "200", "unit": "g"},
        {"name": "水", "amount": "300", "unit": "ml"},
    ]
    assert got["steps"] == []
    assert got["tips"] == []


# --- script CLI (import functions, temp sqlite only) ---


def _run(coro):
    return asyncio.run(coro)


def _seed_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sop_stations (
            slug TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE sop_recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_slug TEXT NOT NULL, section TEXT NOT NULL, recipe_name TEXT NOT NULL,
            body_markdown TEXT NOT NULL, sort_order INTEGER NOT NULL,
            is_new INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );
        INSERT INTO sop_stations VALUES
            ('changfen', '肠粉档', '2026-01-01T00:00:00+00:00'),
            ('shulong', '熟笼档', '2026-01-01T00:00:00+00:00');
        INSERT INTO sop_recipes
            (station_slug, section, recipe_name, body_markdown, sort_order, is_new, is_active, updated_at)
        VALUES
            ('changfen', '配方', '面团', '面粉 200g\n水 300ml', 0, 0, 1, '2026-01-01T00:00:00+00:00'),
            ('changfen', '做法', '说明', '将面粉与水和成面团，静置十分钟后擀开。', 1, 0, 1, '2026-01-01T00:00:00+00:00'),
            ('changfen', '配方', '表格酱油', '| 用料 | 用量 |\n| --- | --- |\n| 酱油 | 100g |\n| 糖 | 20g |', 2, 0, 1, '2026-01-01T00:00:00+00:00'),
            ('shulong', '配方', '虾饺皮', '面粉 200g\n将面粉与水混合均匀。', 0, 0, 1, '2026-01-01T00:00:00+00:00'),
            ('changfen', '配方', '空正文', '', 3, 0, 1, '2026-01-01T00:00:00+00:00'),
            ('changfen', '配方', '停用浆', '米浆 80g', 4, 0, 0, '2026-01-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()
    store = RecipeStore(str(path))
    _run(store.connect())
    _run(store.close())


def _recipe_rows(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, station_slug, recipe_name, body_markdown, ingredients_json, "
        "steps_json, tips_json, legacy_markdown, needs_review FROM sop_recipes ORDER BY id"
    )]
    conn.close()
    return rows


def _bak_files(db_path: Path) -> list[Path]:
    return list(db_path.parent.glob(db_path.name + ".bak.*"))


@pytest.fixture
def recipes_db(tmp_path):
    db = tmp_path / "app.db"
    _seed_legacy_db(db)
    return db


def test_dry_run_writes_nothing_including_no_backup(recipes_db):
    before = _recipe_rows(str(recipes_db))
    report = _run(mrs.run_migration(str(recipes_db), dry_run=True, force=False))
    after = _recipe_rows(str(recipes_db))
    assert after == before
    assert _bak_files(recipes_db) == []
    assert report.dry_run is True
    assert report.backup_path is None
    assert report.processed == 5
    assert report.with_ingredients == 4
    assert report.zero_ingredients == 1
    assert report.skipped_blank == 1
    assert report.per_station == {"changfen": 4, "shulong": 1}


def test_main_dry_run_db_tmp_writes_nothing(recipes_db, capsys):
    before = _recipe_rows(str(recipes_db))
    code = mrs.main(["--dry-run", "--db", str(recipes_db)])
    assert code == 0
    assert _recipe_rows(str(recipes_db)) == before
    assert _bak_files(recipes_db) == []
    out = capsys.readouterr().out
    assert "处理条目: 5" in out
    assert "肠粉档" in out or "changfen" in out


def test_real_run_writes_split_json_snapshot_and_needs_review(recipes_db):
    original_bodies = {r["id"]: r["body_markdown"] for r in _recipe_rows(str(recipes_db))}
    report = _run(mrs.run_migration(str(recipes_db), dry_run=False, force=False))
    assert report.dry_run is False
    assert report.backup_path is not None
    assert Path(report.backup_path).exists()
    assert report.processed == 5
    assert report.with_ingredients == 4
    assert report.zero_ingredients == 1
    assert report.skipped == 1
    assert report.skipped_blank == 1
    assert report.skipped_structured == 0

    rows = {r["id"]: r for r in _recipe_rows(str(recipes_db))}
    dough = next(r for r in rows.values() if r["recipe_name"] == "面团")
    assert dough["body_markdown"] == original_bodies[dough["id"]]
    assert dough["legacy_markdown"] == original_bodies[dough["id"]]
    assert dough["needs_review"] == 1
    assert json.loads(dough["ingredients_json"]) == [
        {"name": "面粉", "amount": "200", "unit": "g"},
        {"name": "水", "amount": "300", "unit": "ml"},
    ]
    assert json.loads(dough["steps_json"]) == []
    assert json.loads(dough["tips_json"]) == []

    prose = next(r for r in rows.values() if r["recipe_name"] == "说明")
    assert prose["body_markdown"] == original_bodies[prose["id"]]
    assert prose["legacy_markdown"] == original_bodies[prose["id"]]
    assert prose["needs_review"] == 1
    assert json.loads(prose["ingredients_json"]) == []
    assert json.loads(prose["steps_json"]) == [
        "将面粉与水和成面团，静置十分钟后擀开。",
    ]
    assert json.loads(prose["tips_json"]) == []

    table = next(r for r in rows.values() if r["recipe_name"] == "表格酱油")
    assert table["body_markdown"] == original_bodies[table["id"]]
    assert table["legacy_markdown"] == original_bodies[table["id"]]
    assert table["needs_review"] == 1
    assert json.loads(table["ingredients_json"]) == [
        {"name": "酱油", "amount": "100", "unit": "g"},
        {"name": "糖", "amount": "20", "unit": "g"},
    ]
    assert json.loads(table["steps_json"]) == []
    assert json.loads(table["tips_json"]) == []

    inactive = next(r for r in rows.values() if r["recipe_name"] == "停用浆")
    assert inactive["needs_review"] == 1
    assert json.loads(inactive["ingredients_json"]) == [
        {"name": "米浆", "amount": "80", "unit": "g"},
    ]


def test_default_skips_rows_with_legacy_markdown(recipes_db):
    _run(mrs.run_migration(str(recipes_db), dry_run=False, force=False))
    conn = sqlite3.connect(recipes_db)
    conn.execute(
        "UPDATE sop_recipes SET ingredients_json = ? WHERE recipe_name = ?",
        ('[{"name":"被改过","amount":"1","unit":"g"}]', "面团"),
    )
    conn.commit()
    conn.close()

    report = _run(mrs.run_migration(str(recipes_db), dry_run=False, force=False))
    assert report.processed == 0
    assert report.skipped == 6
    assert report.skipped_structured == 5
    assert report.skipped_blank == 1
    dough = next(r for r in _recipe_rows(str(recipes_db)) if r["recipe_name"] == "面团")
    assert json.loads(dough["ingredients_json"]) == [
        {"name": "被改过", "amount": "1", "unit": "g"},
    ]


def test_force_remigrates_rows_with_legacy_markdown(recipes_db):
    _run(mrs.run_migration(str(recipes_db), dry_run=False, force=False))
    original_body = next(
        r["legacy_markdown"] for r in _recipe_rows(str(recipes_db)) if r["recipe_name"] == "面团"
    )
    conn = sqlite3.connect(recipes_db)
    conn.execute(
        "UPDATE sop_recipes SET body_markdown = ? WHERE recipe_name = ?",
        ("盐 2g", "面团"),
    )
    conn.commit()
    conn.close()

    report = _run(mrs.run_migration(str(recipes_db), dry_run=False, force=True))
    assert report.processed == 5
    dough = next(r for r in _recipe_rows(str(recipes_db)) if r["recipe_name"] == "面团")
    assert dough["legacy_markdown"] == original_body
    assert json.loads(dough["ingredients_json"]) == [
        {"name": "盐", "amount": "2", "unit": "g"},
    ]
    assert dough["needs_review"] == 1
    assert dough["body_markdown"] == "盐 2g"


def test_skips_blank_body_without_flagging_review(recipes_db):
    report = _run(mrs.run_migration(str(recipes_db), dry_run=False, force=False))
    blank = next(r for r in _recipe_rows(str(recipes_db)) if r["recipe_name"] == "空正文")
    assert report.skipped_blank == 1
    assert blank["needs_review"] in (0, None)
    assert blank["legacy_markdown"] in (None, "")
    assert not json.loads(blank["ingredients_json"] or "[]")
    assert not json.loads(blank["steps_json"] or "[]")


def test_skips_existing_structured_without_legacy(recipes_db):
    conn = sqlite3.connect(recipes_db)
    conn.execute(
        "UPDATE sop_recipes SET ingredients_json = ? WHERE recipe_name = ?",
        ('[{"name":"人手填的","amount":"1","unit":"g"}]', "面团"),
    )
    conn.commit()
    conn.close()
    report = _run(mrs.run_migration(str(recipes_db), dry_run=False, force=False))
    dough = next(r for r in _recipe_rows(str(recipes_db)) if r["recipe_name"] == "面团")
    assert report.skipped_structured >= 1
    assert json.loads(dough["ingredients_json"]) == [
        {"name": "人手填的", "amount": "1", "unit": "g"},
    ]
    assert dough["legacy_markdown"] in (None, "")


def test_keeps_existing_legacy_when_refilling_empty_json(recipes_db):
    _run(mrs.run_migration(str(recipes_db), dry_run=False, force=False))
    snapshot = next(r for r in _recipe_rows(str(recipes_db)) if r["recipe_name"] == "面团")[
        "legacy_markdown"
    ]
    conn = sqlite3.connect(recipes_db)
    conn.execute(
        "UPDATE sop_recipes SET ingredients_json=NULL, steps_json=NULL, tips_json=NULL, "
        "body_markdown=? WHERE recipe_name=?",
        ("盐 2g", "面团"),
    )
    conn.commit()
    conn.close()
    _run(mrs.run_migration(str(recipes_db), dry_run=False, force=False))
    dough = next(r for r in _recipe_rows(str(recipes_db)) if r["recipe_name"] == "面团")
    assert dough["legacy_markdown"] == snapshot
    assert json.loads(dough["ingredients_json"]) == [
        {"name": "盐", "amount": "2", "unit": "g"},
    ]


def test_migrates_inactive_and_reports_oversize(tmp_path):
    db = tmp_path / "oversize.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE sop_stations (
            slug TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE sop_recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_slug TEXT NOT NULL, section TEXT NOT NULL, recipe_name TEXT NOT NULL,
            body_markdown TEXT NOT NULL, sort_order INTEGER NOT NULL,
            is_new INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );
        INSERT INTO sop_stations VALUES ('changfen', '肠粉档', '2026-01-01T00:00:00+00:00');
        """
    )
    long_step = "将" + ("面" * 130)
    conn.execute(
        "INSERT INTO sop_recipes "
        "(station_slug, section, recipe_name, body_markdown, sort_order, is_new, is_active, updated_at) "
        "VALUES ('changfen', '配方', '长说明', ?, 0, 0, 1, '2026-01-01T00:00:00+00:00')",
        (long_step,),
    )
    conn.commit()
    conn.close()
    store = RecipeStore(str(db))
    _run(store.connect())
    _run(store.close())
    report = _run(mrs.run_migration(str(db), dry_run=True, force=False))
    assert report.processed == 1
    assert report.zero_ingredients == 1
    assert any("长说明" in label for label in report.zero_ingredient_labels)
    assert any("超过" in label for label in report.oversize_labels)
