import asyncio
import sqlite3

import pytest

from services.recipes.store import RecipeStore


def _seed_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sop_stations (slug TEXT PRIMARY KEY, title TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE sop_recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_slug TEXT NOT NULL, section TEXT NOT NULL, recipe_name TEXT NOT NULL,
            body_markdown TEXT NOT NULL, sort_order INTEGER NOT NULL,
            is_new INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
        );
        INSERT INTO sop_stations VALUES ('changfen','肠粉档','2026-01-01T00:00:00+00:00');
        INSERT INTO sop_recipes (station_slug,section,recipe_name,body_markdown,sort_order,is_new,is_active,updated_at)
        VALUES ('changfen','配方','肠粉酱油','酱油：100g',0,0,1,'2026-01-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "recipes.db"
    _seed_db(str(db))
    s = RecipeStore(str(db))
    _run(s.connect())
    yield s
    _run(s.close())


def test_list_stations(store):
    rows = _run(store.list_stations())
    assert rows[0]["slug"] == "changfen"
    assert rows[0]["recipe_count"] == 1


def test_create_and_get_recipe(store):
    rid = _run(store.create_recipe("changfen", "配方", "新条目", "正文", None, False))
    assert isinstance(rid, int)
    r = _run(store.get_recipe(rid))
    assert r["recipe_name"] == "新条目"


def test_create_does_not_infer_is_new_from_name_or_body(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "【新】菠菜饺", "正文含【新】标记", None, False,
    ))
    r = _run(store.get_recipe(rid))
    assert r["is_new"] == 0


def test_update_writes_history(store):
    r = _run(store.list_recipes("changfen"))[0]
    _run(store.update_recipe(r["id"], "配方", "改名", "新正文", r["sort_order"], False))
    hist = _run(store.list_history(r["id"]))
    assert len(hist) == 1
    assert hist[0]["recipe_name"] == "肠粉酱油"


def test_update_does_not_infer_is_new_from_name_or_body(store):
    r = _run(store.list_recipes("changfen"))[0]
    _run(store.update_recipe(
        r["id"], "配方", "【新】菠菜饺", "正文含【新】标记", r["sort_order"], False,
    ))
    updated = _run(store.get_recipe(r["id"]))
    assert updated["is_new"] == 0


def test_toggle_active(store):
    r = _run(store.list_recipes("changfen"))[0]
    _run(store.toggle_active(r["id"]))
    assert _run(store.get_recipe(r["id"]))["is_active"] == 0


def test_detail_markdown(store):
    md = _run(store.station_display_markdown("changfen"))
    assert md is not None
    assert "肠粉档" in md


def test_search_empty_query_returns_no_groups(store):
    assert _run(store.search_recipes("")) == []
    assert _run(store.search_recipes("   ")) == []


def test_search_recipes_groups_by_station_on_name(store):
    seed = _run(store.list_recipes("changfen"))[0]
    assert seed["recipe_name"] == "肠粉酱油"
    _run(store.create_station("shulong", "熟笼档"))
    dumpling_id = _run(store.create_recipe("shulong", "配方", "鲜虾饺", "虾馅", None, False))

    groups = _run(store.search_recipes("虾"))
    assert groups == [
        {
            "station_slug": "shulong",
            "station_title": "熟笼档",
            "items": [
                {"recipe_id": dumpling_id, "recipe_name": "鲜虾饺", "section": "配方"},
            ],
        },
    ]

    groups2 = _run(store.search_recipes("肠粉"))
    assert groups2 == [
        {
            "station_slug": "changfen",
            "station_title": "肠粉档",
            "items": [
                {"recipe_id": seed["id"], "recipe_name": "肠粉酱油", "section": "配方"},
            ],
        },
    ]


def test_search_recipes_skips_inactive_by_default(store):
    rid = _run(store.create_recipe("changfen", "配方", "停用虾饺", "y", None, False))
    _run(store.toggle_active(rid))
    assert _run(store.search_recipes("停用虾饺")) == []
    included = _run(store.search_recipes("停用虾饺", include_inactive=True))
    assert included[0]["items"][0]["recipe_id"] == rid
