import asyncio
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.recipes as recipes_module
from services.recipes.store import RecipeStore
from config import settings
from database import DatabaseManager
from services import auth_service
from services.app_runtime import AppRuntime, set_runtime


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


def _seed(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sop_stations (slug TEXT PRIMARY KEY, title TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE sop_recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, station_slug TEXT NOT NULL, section TEXT NOT NULL,
            recipe_name TEXT NOT NULL, body_markdown TEXT NOT NULL, sort_order INTEGER NOT NULL,
            is_new INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
        INSERT INTO sop_stations VALUES ('changfen','肠粉档','2026-01-01T00:00:00+00:00');
        INSERT INTO sop_recipes (station_slug,section,recipe_name,body_markdown,sort_order,is_new,is_active,updated_at)
        VALUES ('changfen','配方','肠粉酱油','酱油：100g',0,0,1,'2026-01-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "recipes.db"
    _seed(str(db_path))
    store = RecipeStore(str(db_path))
    _run(store.connect())

    old_database_dir = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    auth_db = DatabaseManager()
    _run(auth_db.connect())
    set_runtime(AppRuntime(db=auth_db))
    _run(auth_service.init_user("admin", "password123"))
    token, _meta = _run(auth_service.issue_api_token(label="test-recipe-api"))

    app = FastAPI()
    app.include_router(recipes_module.router)
    app.dependency_overrides[recipes_module._get_recipe_store] = lambda: store
    with TestClient(app) as c:
        c.headers.update({"X-Admin-Token": token})
        yield c

    _run(store.close())
    _run(auth_db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old_database_dir


def test_list_stations(client):
    r = client.get("/api/recipes/stations")
    assert r.status_code == 200
    assert r.json()["stations"][0]["slug"] == "changfen"


def test_station_detail_fragment(client):
    r = client.get("/api/recipes/stations/changfen")
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "肠粉档"
    assert "sop-doc" in data["content_html"]


def test_station_detail_stamps_recipe_id_on_cards(client):
    html = client.get("/api/recipes/stations/changfen").json()["content_html"]
    assert 'data-recipe-id="1"' in html
    assert "recipe-card" in html


def test_station_detail_renders_structured_ingredients_table(client):
    rid = client.post(
        "/api/recipes/stations/changfen/recipes",
        json={
            "section": "配方",
            "recipe_name": "面团",
            "body": "这段 Markdown 不应出现在结构化卡片里",
            "is_new": False,
            "ingredients": [{"name": "面粉", "amount": "200", "unit": "g"}],
        },
    ).json()["id"]
    html = client.get("/api/recipes/stations/changfen").json()["content_html"]
    assert "recipe-ingredients" in html
    assert "面粉" in html
    assert "200 g" in html
    assert f'data-recipe-id="{rid}"' in html
    assert "这段 Markdown 不应出现在结构化卡片里" not in html
    assert "酱油：100g" in html
    assert "sop-section-grid" in html


def test_station_detail_404(client):
    assert client.get("/api/recipes/stations/nope").status_code == 404


def test_create_recipe(client):
    r = client.post("/api/recipes/stations/changfen/recipes",
                    json={"section": "配方", "recipe_name": "新", "body": "x", "is_new": False})
    assert r.status_code == 200
    assert isinstance(r.json()["id"], int)


def test_create_recipe_validation(client):
    r = client.post("/api/recipes/stations/changfen/recipes",
                    json={"section": "配方", "recipe_name": "", "body": "x"})
    assert r.status_code == 400


def test_export_csv(client):
    r = client.get("/api/recipes/stations/changfen/export")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "section,recipe_name" in r.text


def test_docx_export(client):
    r = client.get("/api/recipes/stations/changfen/docx")
    assert r.status_code == 200
    assert "officedocument" in r.headers["content-type"]


def test_station_detail_include_inactive(client):
    r = client.post("/api/recipes/stations/changfen/recipes",
                    json={"section": "配方", "recipe_name": "停售品", "body": "y"})
    rid_off = r.json()["id"]
    client.post(f"/api/recipes/recipes/{rid_off}/toggle-active")  # 置停用
    d = client.get("/api/recipes/stations/changfen").json()
    assert "停售品" not in d["content_html"]
    d2 = client.get("/api/recipes/stations/changfen?include_inactive=1").json()
    assert "停售品" in d2["content_html"]
    assert "recipe-card--inactive" in d2["content_html"]


def test_import_csv_does_not_infer_is_new_from_name_or_body(client):
    csv_body = (
        "section,recipe_name,body_markdown,sort_order,is_new\n"
        "配方,【新】菠菜饺,正文含【新】标记,10,0\n"
    )
    r = client.post(
        "/api/recipes/stations/changfen/import",
        files={"csv_file": ("recipes.csv", csv_body.encode("utf-8"), "text/csv")},
    )
    assert r.status_code == 200
    recipes = client.get("/api/recipes/stations/changfen/recipes").json()["recipes"]
    imported = next(row for row in recipes if row["recipe_name"] == "【新】菠菜饺")
    assert imported["is_new"] == 0


def test_reorder_recipes_requires_auth(client):
    client.headers.pop("X-Admin-Token", None)
    r = client.put("/api/recipes/stations/changfen/recipes/reorder", json={"ids": [1]})
    assert r.status_code == 401


def test_reorder_recipes_assigns_unique_sort_order(client):
    first_id = client.get("/api/recipes/stations/changfen/recipes").json()["recipes"][0]["id"]
    second_id = client.post(
        "/api/recipes/stations/changfen/recipes",
        json={"section": "配方", "recipe_name": "第二", "body": "b", "is_new": False},
    ).json()["id"]
    third_id = client.post(
        "/api/recipes/stations/changfen/recipes",
        json={"section": "出品标准", "recipe_name": "第三", "body": "c", "is_new": False},
    ).json()["id"]

    r = client.put(
        "/api/recipes/stations/changfen/recipes/reorder",
        json={"ids": [third_id, first_id, second_id]},
    )
    assert r.status_code == 200

    recipes = client.get("/api/recipes/stations/changfen/recipes").json()["recipes"]
    assert [row["id"] for row in recipes] == [third_id, first_id, second_id]
    assert [row["sort_order"] for row in recipes] == [0, 1, 2]
    history = client.get(f"/api/recipes/recipes/{first_id}/history").json()["history"]
    assert history == []


def test_search_empty_query_returns_empty_groups(client):
    r = client.get("/api/recipes/search")
    assert r.status_code == 200
    assert r.json() == {"groups": []}

    r2 = client.get("/api/recipes/search", params={"q": ""})
    assert r2.status_code == 200
    assert r2.json() == {"groups": []}

    r3 = client.get("/api/recipes/search", params={"q": "   "})
    assert r3.status_code == 200
    assert r3.json() == {"groups": []}


def test_search_groups_matches_by_station(client):
    client.post("/api/recipes/stations", json={"slug": "shulong", "title": "熟笼档"})
    dumpling = client.post(
        "/api/recipes/stations/shulong/recipes",
        json={"section": "配方", "recipe_name": "鲜虾饺", "body": "虾馅", "is_new": False},
    ).json()["id"]
    sauce = client.get("/api/recipes/stations/changfen/recipes").json()["recipes"][0]
    assert sauce["recipe_name"] == "肠粉酱油"

    r = client.get("/api/recipes/search", params={"q": "虾"})
    assert r.status_code == 200
    assert r.json() == {
        "groups": [
            {
                "station_slug": "shulong",
                "station_title": "熟笼档",
                "items": [
                    {"recipe_id": dumpling, "recipe_name": "鲜虾饺", "section": "配方"},
                ],
            },
        ],
    }

    r2 = client.get("/api/recipes/search", params={"q": "肠粉"})
    assert r2.json() == {
        "groups": [
            {
                "station_slug": "changfen",
                "station_title": "肠粉档",
                "items": [
                    {"recipe_id": sauce["id"], "recipe_name": "肠粉酱油", "section": "配方"},
                ],
            },
        ],
    }


def test_search_returns_multiple_items_in_one_station(client):
    sauce = client.get("/api/recipes/stations/changfen/recipes").json()["recipes"][0]
    check_id = client.post(
        "/api/recipes/stations/changfen/recipes",
        json={"section": "出品标准", "recipe_name": "肠粉检核", "body": "x", "is_new": False},
    ).json()["id"]
    items = client.get("/api/recipes/search", params={"q": "肠粉"}).json()["groups"][0]["items"]
    assert items == [
        {"recipe_id": sauce["id"], "recipe_name": "肠粉酱油", "section": "配方"},
        {"recipe_id": check_id, "recipe_name": "肠粉检核", "section": "出品标准"},
    ]


def test_search_is_case_insensitive_and_skips_body(client):
    wrap_id = client.post(
        "/api/recipes/stations/changfen/recipes",
        json={"section": "配方", "recipe_name": "Spinach Wrap", "body": "hidden dumpling filling", "is_new": False},
    ).json()["id"]
    decoy_id = client.post(
        "/api/recipes/stations/changfen/recipes",
        json={"section": "配方", "recipe_name": "无关名字", "body": "Spinach Wrap 写在正文", "is_new": False},
    ).json()["id"]

    r = client.get("/api/recipes/search", params={"q": "spinach wrap"})
    names = [item["recipe_name"] for g in r.json()["groups"] for item in g["items"]]
    ids = [item["recipe_id"] for g in r.json()["groups"] for item in g["items"]]
    assert names == ["Spinach Wrap"]
    assert ids == [wrap_id]
    assert decoy_id not in ids

    r2 = client.get("/api/recipes/search", params={"q": "hidden dumpling"})
    assert r2.json() == {"groups": []}


def test_search_excludes_inactive_unless_flagged(client):
    r = client.post(
        "/api/recipes/stations/changfen/recipes",
        json={"section": "配方", "recipe_name": "停用虾饺", "body": "y", "is_new": False},
    )
    rid = r.json()["id"]
    client.post(f"/api/recipes/recipes/{rid}/toggle-active")

    assert client.get("/api/recipes/search", params={"q": "停用虾饺"}).json() == {"groups": []}

    flagged = client.get("/api/recipes/search", params={"q": "停用虾饺", "include_inactive": 1}).json()
    assert flagged["groups"][0]["items"] == [
        {"recipe_id": rid, "recipe_name": "停用虾饺", "section": "配方"},
    ]


def test_search_treats_like_wildcards_as_literal(client):
    percent_id = client.post(
        "/api/recipes/stations/changfen/recipes",
        json={"section": "配方", "recipe_name": "50%糖浆", "body": "x", "is_new": False},
    ).json()["id"]
    client.post(
        "/api/recipes/stations/changfen/recipes",
        json={"section": "配方", "recipe_name": "普通糖浆", "body": "x", "is_new": False},
    )

    wildcard = client.get("/api/recipes/search", params={"q": "%"}).json()
    assert wildcard == {
        "groups": [
            {
                "station_slug": "changfen",
                "station_title": "肠粉档",
                "items": [
                    {"recipe_id": percent_id, "recipe_name": "50%糖浆", "section": "配方"},
                ],
            },
        ],
    }

    underscore = client.get("/api/recipes/search", params={"q": "_"}).json()
    assert underscore == {"groups": []}


def test_search_stays_public(client):
    client.headers.pop("X-Admin-Token", None)
    r = client.get("/api/recipes/search", params={"q": "肠粉"})
    assert r.status_code == 200
    assert r.json()["groups"][0]["items"][0]["recipe_name"] == "肠粉酱油"


def test_create_omitting_ingredients_still_works(client):
    r = client.post("/api/recipes/stations/changfen/recipes",
                    json={"section": "配方", "recipe_name": "无用料", "body": "x", "is_new": False})
    assert r.status_code == 200
    rid = r.json()["id"]
    recipes = client.get("/api/recipes/stations/changfen/recipes").json()["recipes"]
    row = next(x for x in recipes if x["id"] == rid)
    assert row["ingredients"] == []
    current = client.get(f"/api/recipes/recipes/{rid}/history").json()["current"]
    assert current["ingredients"] == []


def test_create_and_list_recipes_include_ingredients(client):
    r = client.post(
        "/api/recipes/stations/changfen/recipes",
        json={
            "section": "配方",
            "recipe_name": "面团",
            "body": "",
            "is_new": False,
            "ingredients": [
                {"name": "面粉", "amount": "200", "unit": "g"},
                {"name": "水", "amount": "适量", "unit": ""},
            ],
        },
    )
    assert r.status_code == 200
    rid = r.json()["id"]
    recipes = client.get("/api/recipes/stations/changfen/recipes").json()["recipes"]
    row = next(x for x in recipes if x["id"] == rid)
    assert row["ingredients"] == [
        {"name": "面粉", "amount": "200", "unit": "g"},
        {"name": "水", "amount": "适量", "unit": ""},
    ]
    current = client.get(f"/api/recipes/recipes/{rid}/history").json()["current"]
    assert current["ingredients"] == row["ingredients"]


def test_update_recipe_ingredients(client):
    rid = client.get("/api/recipes/stations/changfen/recipes").json()["recipes"][0]["id"]
    r = client.put(
        f"/api/recipes/recipes/{rid}",
        json={
            "section": "配方",
            "recipe_name": "肠粉酱油",
            "body": "酱油：100g",
            "is_new": False,
            "ingredients": [{"name": "酱油", "amount": "100", "unit": "g"}],
        },
    )
    assert r.status_code == 200
    assert r.json()["ingredients"] == [{"name": "酱油", "amount": "100", "unit": "g"}]


def test_update_omitting_ingredients_still_works(client):
    rid = client.get("/api/recipes/stations/changfen/recipes").json()["recipes"][0]["id"]
    r = client.put(
        f"/api/recipes/recipes/{rid}",
        json={"section": "配方", "recipe_name": "改名", "body": "新正文", "is_new": False},
    )
    assert r.status_code == 200
    assert r.json()["recipe_name"] == "改名"
    assert r.json()["ingredients"] == []


def test_update_omitting_ingredients_preserves_existing_rows(client):
    rid = client.post(
        "/api/recipes/stations/changfen/recipes",
        json={
            "section": "配方",
            "recipe_name": "面团",
            "body": "",
            "ingredients": [{"name": "面粉", "amount": "200", "unit": "g"}],
        },
    ).json()["id"]
    r = client.put(
        f"/api/recipes/recipes/{rid}",
        json={"section": "配方", "recipe_name": "面团改", "body": "", "is_new": False},
    )
    assert r.status_code == 200
    assert r.json()["recipe_name"] == "面团改"
    assert r.json()["ingredients"] == [{"name": "面粉", "amount": "200", "unit": "g"}]


def test_ingredient_name_over_limit_returns_400(client):
    r = client.post(
        "/api/recipes/stations/changfen/recipes",
        json={
            "section": "配方",
            "recipe_name": "面团",
            "body": "",
            "ingredients": [{"name": "a" * 121, "amount": "1", "unit": "g"}],
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "用料名称长度不能超过 120 个字符"


def test_ingredient_amount_over_limit_returns_400(client):
    rid = client.get("/api/recipes/stations/changfen/recipes").json()["recipes"][0]["id"]
    r = client.put(
        f"/api/recipes/recipes/{rid}",
        json={
            "section": "配方",
            "recipe_name": "肠粉酱油",
            "body": "",
            "ingredients": [{"name": "酱油", "amount": "b" * 121, "unit": "g"}],
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "用料用量长度不能超过 120 个字符"


def test_ingredient_unit_over_limit_returns_400(client):
    r = client.post(
        "/api/recipes/stations/changfen/recipes",
        json={
            "section": "配方",
            "recipe_name": "面团",
            "body": "",
            "ingredients": [{"name": "盐", "amount": "1", "unit": "c" * 121}],
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "用料单位长度不能超过 120 个字符"
