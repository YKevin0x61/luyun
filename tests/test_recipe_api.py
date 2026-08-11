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
