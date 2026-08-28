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


def test_missing_ingredients_read_as_empty_list(store):
    row = _run(store.list_recipes("changfen"))[0]
    assert row["ingredients"] == []
    got = _run(store.get_recipe(row["id"]))
    assert got["ingredients"] == []


def test_create_without_ingredients_defaults_empty(store):
    rid = _run(store.create_recipe("changfen", "配方", "无用料", "正文", None, False))
    assert _run(store.get_recipe(rid))["ingredients"] == []


def test_ingredients_roundtrip(store):
    items = [
        {"name": "面粉", "amount": "200", "unit": "g"},
        {"name": "水", "amount": "适量", "unit": ""},
    ]
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False, ingredients=items,
    ))
    got = _run(store.get_recipe(rid))
    assert got["ingredients"] == [
        {"name": "面粉", "amount": "200", "unit": "g"},
        {"name": "水", "amount": "适量", "unit": ""},
    ]
    listed = next(x for x in _run(store.list_recipes("changfen")) if x["id"] == rid)
    assert listed["ingredients"] == [
        {"name": "面粉", "amount": "200", "unit": "g"},
        {"name": "水", "amount": "适量", "unit": ""},
    ]

    updated = _run(store.update_recipe(
        rid, "配方", "面团", "body", got["sort_order"], False,
        ingredients=[{"name": "盐", "amount": "2-3", "unit": "g"}],
    ))
    assert updated["ingredients"] == [{"name": "盐", "amount": "2-3", "unit": "g"}]


def test_create_drops_blank_ingredient_rows(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False,
        ingredients=[
            {"name": "", "amount": "", "unit": ""},
            {"name": "盐", "amount": "1", "unit": "g"},
            {"name": "  ", "amount": "", "unit": ""},
        ],
    ))
    assert _run(store.get_recipe(rid))["ingredients"] == [
        {"name": "盐", "amount": "1", "unit": "g"},
    ]


def test_invalid_stored_ingredients_json_returns_empty_list(store):
    row = _run(store.list_recipes("changfen"))[0]
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE sop_recipes SET ingredients_json = ? WHERE id = ?",
        ("{not json", row["id"]),
    )
    conn.commit()
    conn.close()
    assert _run(store.get_recipe(row["id"]))["ingredients"] == []
    assert _run(store.list_recipes("changfen"))[0]["ingredients"] == []


def test_missing_steps_read_as_empty_list(store):
    row = _run(store.list_recipes("changfen"))[0]
    assert row["steps"] == []
    got = _run(store.get_recipe(row["id"]))
    assert got["steps"] == []


def test_create_without_steps_defaults_empty(store):
    rid = _run(store.create_recipe("changfen", "配方", "无步骤", "正文", None, False))
    assert _run(store.get_recipe(rid))["steps"] == []


def test_steps_roundtrip(store):
    steps = ["混合面粉与水", "静置 10 分钟"]
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False, steps=steps,
    ))
    got = _run(store.get_recipe(rid))
    assert got["steps"] == ["混合面粉与水", "静置 10 分钟"]
    listed = next(x for x in _run(store.list_recipes("changfen")) if x["id"] == rid)
    assert listed["steps"] == ["混合面粉与水", "静置 10 分钟"]

    updated = _run(store.update_recipe(
        rid, "配方", "面团", "body", got["sort_order"], False,
        steps=["分成剂子"],
    ))
    assert updated["steps"] == ["分成剂子"]


def test_create_drops_blank_step_rows(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False,
        steps=["", "混合面粉与水", "  "],
    ))
    assert _run(store.get_recipe(rid))["steps"] == ["混合面粉与水"]


def test_update_omitting_steps_preserves_existing(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False,
        ingredients=[{"name": "面粉", "amount": "200", "unit": "g"}],
        steps=["混合面粉与水"],
    ))
    updated = _run(store.update_recipe(
        rid, "配方", "面团改", "body", 0, False,
        ingredients=[{"name": "盐", "amount": "1", "unit": "g"}],
    ))
    assert updated["recipe_name"] == "面团改"
    assert updated["ingredients"] == [{"name": "盐", "amount": "1", "unit": "g"}]
    assert updated["steps"] == ["混合面粉与水"]


def test_update_omitting_ingredients_preserves_steps_and_ingredients(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False,
        ingredients=[{"name": "面粉", "amount": "200", "unit": "g"}],
        steps=["混合面粉与水"],
    ))
    got = _run(store.get_recipe(rid))
    updated = _run(store.update_recipe(
        rid, "配方", "面团改", "body", got["sort_order"], False,
        steps=["静置"],
    ))
    assert updated["ingredients"] == [{"name": "面粉", "amount": "200", "unit": "g"}]
    assert updated["steps"] == ["静置"]


def test_invalid_stored_steps_json_returns_empty_list(store):
    row = _run(store.list_recipes("changfen"))[0]
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE sop_recipes SET steps_json = ? WHERE id = ?",
        ("{not json", row["id"]),
    )
    conn.commit()
    conn.close()
    assert _run(store.get_recipe(row["id"]))["steps"] == []
    assert _run(store.list_recipes("changfen"))[0]["steps"] == []


def test_non_list_stored_steps_json_returns_empty_list(store):
    row = _run(store.list_recipes("changfen"))[0]
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE sop_recipes SET steps_json = ? WHERE id = ?",
        ("{}", row["id"]),
    )
    conn.commit()
    conn.close()
    assert _run(store.get_recipe(row["id"]))["steps"] == []


def test_missing_tips_read_as_empty_list(store):
    row = _run(store.list_recipes("changfen"))[0]
    assert row["tips"] == []
    got = _run(store.get_recipe(row["id"]))
    assert got["tips"] == []


def test_create_without_tips_defaults_empty(store):
    rid = _run(store.create_recipe("changfen", "配方", "无贴士", "正文", None, False))
    assert _run(store.get_recipe(rid))["tips"] == []


def test_tips_roundtrip(store):
    tips = ["夏天水温要更低", "饧面不要超过 20 分钟"]
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False, tips=tips,
    ))
    got = _run(store.get_recipe(rid))
    assert got["tips"] == ["夏天水温要更低", "饧面不要超过 20 分钟"]
    listed = next(x for x in _run(store.list_recipes("changfen")) if x["id"] == rid)
    assert listed["tips"] == ["夏天水温要更低", "饧面不要超过 20 分钟"]

    updated = _run(store.update_recipe(
        rid, "配方", "面团", "body", got["sort_order"], False,
        tips=["按口味调整盐"],
    ))
    assert updated["tips"] == ["按口味调整盐"]


def test_create_drops_blank_tip_rows(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False,
        tips=["", "夏天水温要更低", "  "],
    ))
    assert _run(store.get_recipe(rid))["tips"] == ["夏天水温要更低"]


def test_update_omitting_tips_preserves_existing(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False,
        ingredients=[{"name": "面粉", "amount": "200", "unit": "g"}],
        steps=["混合面粉与水"],
        tips=["夏天水温要更低"],
    ))
    updated = _run(store.update_recipe(
        rid, "配方", "面团改", "body", 0, False,
        ingredients=[{"name": "盐", "amount": "1", "unit": "g"}],
        steps=["静置"],
    ))
    assert updated["recipe_name"] == "面团改"
    assert updated["ingredients"] == [{"name": "盐", "amount": "1", "unit": "g"}]
    assert updated["steps"] == ["静置"]
    assert updated["tips"] == ["夏天水温要更低"]


def test_update_omitting_steps_preserves_tips_and_steps(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False,
        steps=["混合面粉与水"],
        tips=["夏天水温要更低"],
    ))
    got = _run(store.get_recipe(rid))
    updated = _run(store.update_recipe(
        rid, "配方", "面团改", "body", got["sort_order"], False,
        tips=["按口味调整盐"],
    ))
    assert updated["steps"] == ["混合面粉与水"]
    assert updated["tips"] == ["按口味调整盐"]


def test_invalid_stored_tips_json_returns_empty_list(store):
    row = _run(store.list_recipes("changfen"))[0]
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE sop_recipes SET tips_json = ? WHERE id = ?",
        ("{not json", row["id"]),
    )
    conn.commit()
    conn.close()
    assert _run(store.get_recipe(row["id"]))["tips"] == []
    assert _run(store.list_recipes("changfen"))[0]["tips"] == []


def test_non_list_stored_tips_json_returns_empty_list(store):
    row = _run(store.list_recipes("changfen"))[0]
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE sop_recipes SET tips_json = ? WHERE id = ?",
        ("{}", row["id"]),
    )
    conn.commit()
    conn.close()
    assert _run(store.get_recipe(row["id"]))["tips"] == []


def test_missing_base_servings_read_as_null(store):
    row = _run(store.list_recipes("changfen"))[0]
    assert row["base_servings_qty"] is None
    assert row["base_servings_unit"] is None
    got = _run(store.get_recipe(row["id"]))
    assert got["base_servings_qty"] is None
    assert got["base_servings_unit"] is None


def test_create_without_base_servings_defaults_null(store):
    rid = _run(store.create_recipe("changfen", "配方", "无份数", "正文", None, False))
    got = _run(store.get_recipe(rid))
    assert got["base_servings_qty"] is None
    assert got["base_servings_unit"] is None


def test_base_servings_roundtrip(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False,
        base_servings_qty=4.5, base_servings_unit="人份",
    ))
    got = _run(store.get_recipe(rid))
    assert got["base_servings_qty"] == 4.5
    assert got["base_servings_unit"] == "人份"
    listed = next(x for x in _run(store.list_recipes("changfen")) if x["id"] == rid)
    assert listed["base_servings_qty"] == 4.5
    assert listed["base_servings_unit"] == "人份"

    updated = _run(store.update_recipe(
        rid, "配方", "面团", "body", got["sort_order"], False,
        base_servings_qty=2, base_servings_unit="份",
    ))
    assert updated["base_servings_qty"] == 2
    assert updated["base_servings_unit"] == "份"


def test_create_zero_base_servings_qty_stores_null(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False,
        base_servings_qty=0, base_servings_unit="人份",
    ))
    got = _run(store.get_recipe(rid))
    assert got["base_servings_qty"] is None
    assert got["base_servings_unit"] is None


def test_update_omitting_base_servings_preserves_existing(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False,
        ingredients=[{"name": "面粉", "amount": "200", "unit": "g"}],
        base_servings_qty=4, base_servings_unit="人份",
    ))
    updated = _run(store.update_recipe(
        rid, "配方", "面团改", "body", 0, False,
        ingredients=[{"name": "盐", "amount": "1", "unit": "g"}],
    ))
    assert updated["recipe_name"] == "面团改"
    assert updated["ingredients"] == [{"name": "盐", "amount": "1", "unit": "g"}]
    assert updated["base_servings_qty"] == 4
    assert updated["base_servings_unit"] == "人份"


def test_update_explicit_null_clears_base_servings(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "body", None, False,
        base_servings_qty=4, base_servings_unit="人份",
    ))
    updated = _run(store.update_recipe(
        rid, "配方", "面团", "body", 0, False,
        base_servings_qty=None, base_servings_unit=None,
    ))
    assert updated["base_servings_qty"] is None
    assert updated["base_servings_unit"] is None
