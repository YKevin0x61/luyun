import asyncio
import sqlite3

import pytest

from services.recipes.store import (
    LAST_RECIPE_PLACEHOLDER_BODY,
    LAST_RECIPE_PLACEHOLDER_NAME,
    LAST_RECIPE_PLACEHOLDER_SECTION,
    NEW_STATION_SEED_BODY,
    NEW_STATION_SEED_NAME,
    NEW_STATION_SEED_SECTION,
    RecipeStore,
)


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


def test_create_recipe_canonicalizes_legacy_section(store):
    rid = _run(store.create_recipe("changfen", "粥品", "艇仔粥", "米", None, False))
    assert _run(store.get_recipe(rid))["section"] == "配方"


def test_connect_rewrites_legacy_sections(tmp_path):
    db = tmp_path / "legacy-sections.db"
    _seed_db(str(db))
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO sop_recipes (station_slug,section,recipe_name,body_markdown,sort_order,is_new,is_active,updated_at) "
        "VALUES ('changfen','二十大招牌检核','咸蛋黄肉松蛋挞','x',1,0,1,'2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO sop_recipes (station_slug,section,recipe_name,body_markdown,sort_order,is_new,is_active,updated_at) "
        "VALUES ('changfen','常规检核','仪容','x',2,0,1,'2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    store = RecipeStore(str(db))
    _run(store.connect())
    by_name = {row["recipe_name"]: row["section"] for row in _run(store.list_recipes("changfen"))}
    assert by_name["肠粉酱油"] == "配方"
    assert by_name["咸蛋黄肉松蛋挞"] == "检核要求"
    assert by_name["仪容"] == "检核要求"
    _run(store.close())


def test_create_does_not_infer_is_new_from_name_or_body(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "【新】菠菜饺", "正文含【新】标记", None, False,
    ))
    r = _run(store.get_recipe(rid))
    assert r["is_new"] == 0


def test_update_writes_history(store):
    r = _run(store.list_recipes("changfen"))[0]
    _run(store.update_recipe(
        r["id"], "配方", "改名", "新正文", r["sort_order"], False,
        ingredients=[{"name": "酱油", "amount": "100", "unit": "g"}],
        steps=["兑汁"],
        tips=["别放糖"],
    ))
    hist = _run(store.list_history(r["id"]))
    assert len(hist) == 1
    assert hist[0]["recipe_name"] == "肠粉酱油"
    assert hist[0]["ingredients"] == []
    assert "id" in hist[0]


def test_restore_history_snapshots_current_then_applies_version(store):
    r = _run(store.list_recipes("changfen"))[0]
    _run(store.update_recipe(
        r["id"], "配方", "改名", "新正文", r["sort_order"], False,
        ingredients=[{"name": "酱油", "amount": "80", "unit": "g"}],
        steps=["先改"],
        tips=[],
        base_servings_qty=10,
        base_servings_unit="人份",
    ))
    hist = _run(store.list_history(r["id"]))
    first_id = hist[0]["id"]
    restored = _run(store.restore_history(r["id"], first_id))
    assert restored["recipe_name"] == "肠粉酱油"
    assert restored["body_markdown"] == "酱油：100g"
    after = _run(store.list_history(r["id"]))
    assert len(after) == 2
    assert after[0]["recipe_name"] == "改名"
    assert after[0]["ingredients"] == [{"name": "酱油", "amount": "80", "unit": "g"}]


def test_restore_history_unknown_id_returns_none(store):
    r = _run(store.list_recipes("changfen"))[0]
    assert _run(store.restore_history(r["id"], 99999)) is None


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


def test_connect_adds_legacy_markdown_and_needs_review_columns(store):
    conn = sqlite3.connect(store.db_path)
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(sop_recipes)")}
    conn.close()
    assert "legacy_markdown" in cols
    assert cols["legacy_markdown"][2].upper() == "TEXT"
    assert "needs_review" in cols
    assert cols["needs_review"][2].upper() == "INTEGER"
    assert cols["needs_review"][4] == "0"
    assert cols["needs_review"][3] == 1


def _flag_review(store, recipe_id, *, needs_review=1, legacy_markdown="旧正文\n第二行"):
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE sop_recipes SET needs_review=?, legacy_markdown=? WHERE id=?",
        (needs_review, legacy_markdown, recipe_id),
    )
    conn.commit()
    conn.close()


def test_list_and_get_include_needs_review_and_legacy_markdown(store):
    row = _run(store.list_recipes("changfen"))[0]
    assert row["needs_review"] == 0
    assert row["legacy_markdown"] is None
    got = _run(store.get_recipe(row["id"]))
    assert got["needs_review"] == 0
    assert got["legacy_markdown"] is None

    _flag_review(store, row["id"], legacy_markdown="面粉 200g")
    listed = _run(store.list_recipes("changfen"))[0]
    assert listed["needs_review"] == 1
    assert listed["legacy_markdown"] == "面粉 200g"
    fetched = _run(store.get_recipe(row["id"]))
    assert fetched["needs_review"] == 1
    assert fetched["legacy_markdown"] == "面粉 200g"


def test_create_defaults_needs_review_zero_and_legacy_null(store):
    rid = _run(store.create_recipe("changfen", "配方", "新条目", "正文", None, False))
    got = _run(store.get_recipe(rid))
    assert got["needs_review"] == 0
    assert got["legacy_markdown"] is None


def test_confirm_review_clears_flag_and_preserves_other_fields(store):
    rid = _run(store.create_recipe(
        "changfen", "配方", "面团", "正文保留", None, False,
        ingredients=[{"name": "面粉", "amount": "200", "unit": "g"}],
        steps=["混合面粉与水"],
        tips=["夏天水温要更低"],
        base_servings_qty=4,
        base_servings_unit="人份",
    ))
    snapshot = "迁移前原文\n盐 2g"
    _flag_review(store, rid, legacy_markdown=snapshot)
    before = _run(store.get_recipe(rid))
    assert before["needs_review"] == 1

    updated = _run(store.confirm_review(rid))
    assert updated["id"] == rid
    assert updated["needs_review"] == 0
    assert updated["legacy_markdown"] == snapshot
    assert updated["recipe_name"] == "面团"
    assert updated["body_markdown"] == "正文保留"
    assert updated["ingredients"] == [{"name": "面粉", "amount": "200", "unit": "g"}]
    assert updated["steps"] == ["混合面粉与水"]
    assert updated["tips"] == ["夏天水温要更低"]
    assert updated["base_servings_qty"] == 4
    assert updated["base_servings_unit"] == "人份"
    listed = next(x for x in _run(store.list_recipes("changfen")) if x["id"] == rid)
    assert listed["needs_review"] == 0
    assert listed["legacy_markdown"] == snapshot


def test_confirm_review_missing_returns_none(store):
    assert _run(store.confirm_review(99999)) is None


def test_count_needs_review_all_and_by_station(store):
    seed = _run(store.list_recipes("changfen"))[0]
    _run(store.create_station("shulong", "熟笼档"))
    other = _run(store.create_recipe("shulong", "配方", "鲜虾饺", "虾馅", None, False))
    assert _run(store.count_needs_review()) == 0
    assert _run(store.count_needs_review("changfen")) == 0
    assert _run(store.count_needs_review("shulong")) == 0

    _flag_review(store, seed["id"])
    _flag_review(store, other, legacy_markdown="虾 10只")
    extra = _run(store.create_recipe("changfen", "配方", "第二", "x", None, False))
    _flag_review(store, extra, legacy_markdown="第二原文")

    assert _run(store.count_needs_review()) == 3
    assert _run(store.count_needs_review(None)) == 3
    assert _run(store.count_needs_review("changfen")) == 2
    assert _run(store.count_needs_review("shulong")) == 1
    _run(store.confirm_review(seed["id"]))
    assert _run(store.count_needs_review("changfen")) == 1
    assert _run(store.count_needs_review()) == 2


def test_list_stations_includes_needs_review_count(store):
    rows = _run(store.list_stations())
    assert rows[0]["slug"] == "changfen"
    assert rows[0]["needs_review_count"] == 0

    seed = _run(store.list_recipes("changfen"))[0]
    _flag_review(store, seed["id"])
    _run(store.create_station("shulong", "熟笼档"))
    other = _run(store.create_recipe("shulong", "配方", "鲜虾饺", "虾馅", None, False))
    _flag_review(store, other)

    by_slug = {row["slug"]: row for row in _run(store.list_stations())}
    assert by_slug["changfen"]["needs_review_count"] == 1
    assert by_slug["shulong"]["needs_review_count"] == 1
    _run(store.confirm_review(seed["id"]))
    by_slug = {row["slug"]: row for row in _run(store.list_stations())}
    assert by_slug["changfen"]["needs_review_count"] == 0
    assert by_slug["shulong"]["needs_review_count"] == 1


def test_create_station_seed_is_not_sop_or_markdown(store):
    _run(store.create_station("xibing", "西饼档"))
    rows = _run(store.list_recipes("xibing"))
    assert len(rows) == 1
    seed = rows[0]
    assert seed["section"] == NEW_STATION_SEED_SECTION
    assert seed["recipe_name"] == NEW_STATION_SEED_NAME
    assert seed["body_markdown"] == NEW_STATION_SEED_BODY
    assert seed["section"] == "配方"
    assert "条目" not in seed["recipe_name"]
    assert "Markdown" not in seed["body_markdown"]


def test_delete_last_recipe_inserts_placeholder_not_entry_word(store):
    _run(store.create_station("xibing", "西饼档"))
    seed = _run(store.list_recipes("xibing"))[0]
    _run(store.delete_recipe(seed["id"]))
    rows = _run(store.list_recipes("xibing"))
    assert len(rows) == 1
    placeholder = rows[0]
    assert placeholder["section"] == LAST_RECIPE_PLACEHOLDER_SECTION
    assert placeholder["recipe_name"] == LAST_RECIPE_PLACEHOLDER_NAME
    assert placeholder["body_markdown"] == LAST_RECIPE_PLACEHOLDER_BODY
    assert "条目" not in placeholder["body_markdown"]
