"""配方库异步数据访问层（aiosqlite）。配方相关表与其它业务表同库存放于统一的
data/app.db（WAL），自身仍持有独立连接（不接入 DatabaseManager 的共享连接）。
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from config import settings
from db_core.utils import SQLITE_BUSY_TIMEOUT_MS, SQLITE_JOURNAL_MODE_WAL

from .sections import DEFAULT_RECIPE_SECTION, canonicalize_section
from .sop_parse import (
    ParsedRecipe,
    recipes_to_display_markdown,
)
from .structured_render import render_station_html

SLUG_MAX_LEN = 64
TEXT_FIELD_MAX_LEN = 120
INGREDIENT_FIELD_MAX_LEN = TEXT_FIELD_MAX_LEN
STEP_TEXT_MAX_LEN = TEXT_FIELD_MAX_LEN
STEPS_MAX_COUNT = 50
TIP_TEXT_MAX_LEN = TEXT_FIELD_MAX_LEN
TIPS_MAX_COUNT = 50
NEW_STATION_SEED_SECTION = DEFAULT_RECIPE_SECTION
NEW_STATION_SEED_NAME = "（示例）"
NEW_STATION_SEED_BODY = "请填写用料、步骤或小贴士。"
LAST_RECIPE_PLACEHOLDER_SECTION = DEFAULT_RECIPE_SECTION
LAST_RECIPE_PLACEHOLDER_NAME = "（占位）"
LAST_RECIPE_PLACEHOLDER_BODY = "请至少保留一条配方，或删除整个岗位。"
BODY_MAX_LEN = 50_000
LIKE_ESCAPE_CHAR = "!"
INGREDIENT_KEYS = ("name", "amount", "unit")
_UNSET = object()


def _escape_like_literal(text: str) -> str:
    """Make %, _, and the LIKE ESCAPE char literal in a user-supplied fragment."""
    return (
        (text or "")
        .replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR + LIKE_ESCAPE_CHAR)
        .replace("%", LIKE_ESCAPE_CHAR + "%")
        .replace("_", LIKE_ESCAPE_CHAR + "_")
    )


def _normalize_ingredients(value) -> list[dict]:
    if not value:
        return []
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        row = {}
        for key in INGREDIENT_KEYS:
            raw = item.get(key)
            row[key] = "" if raw is None else str(raw).strip()
        if not any(row[key] for key in INGREDIENT_KEYS):
            continue
        out.append(row)
    return out


def _ingredients_from_storage(raw) -> list[dict]:
    if raw is None or raw == "":
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return _normalize_ingredients(data)


def _ingredients_to_storage(value) -> str:
    return json.dumps(_normalize_ingredients(value), ensure_ascii=False)


def _normalize_steps(value) -> list[str]:
    if not value or not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            out.append(text)
    return out


def _steps_from_storage(raw) -> list[str]:
    if raw is None or raw == "":
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return _normalize_steps(data)


def _steps_to_storage(value) -> str:
    return json.dumps(_normalize_steps(value), ensure_ascii=False)


def _normalize_tips(value) -> list[str]:
    if not value or not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            out.append(text)
    return out


def _tips_from_storage(raw) -> list[str]:
    if raw is None or raw == "":
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return _normalize_tips(data)


def _tips_to_storage(value) -> str:
    return json.dumps(_normalize_tips(value), ensure_ascii=False)


def _normalize_base_servings_qty(value):
    if value is None:
        return None
    try:
        qty = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(qty) or qty <= 0:
        return None
    return qty


def _normalize_base_servings_unit(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _pair_base_servings(qty, unit) -> tuple:
    qty = _normalize_base_servings_qty(qty)
    if qty is None:
        return None, None
    return qty, _normalize_base_servings_unit(unit)


def _recipe_dict(row) -> dict:
    data = dict(row)
    data["ingredients"] = _ingredients_from_storage(data.pop("ingredients_json", None))
    data["steps"] = _steps_from_storage(data.pop("steps_json", None))
    data["tips"] = _tips_from_storage(data.pop("tips_json", None))
    qty, unit = _pair_base_servings(
        data.get("base_servings_qty"), data.get("base_servings_unit"),
    )
    data["base_servings_qty"] = qty
    data["base_servings_unit"] = unit
    try:
        data["needs_review"] = 1 if int(data.get("needs_review") or 0) else 0
    except (TypeError, ValueError):
        data["needs_review"] = 0
    legacy = data.get("legacy_markdown")
    data["legacy_markdown"] = None if legacy is None else str(legacy)
    return data


def _history_dict(row) -> dict:
    data = dict(row)
    data["id"] = int(data["id"])
    data["recipe_id"] = int(data["recipe_id"])
    data["ingredients"] = _ingredients_from_storage(data.pop("ingredients_json", None))
    data["steps"] = _steps_from_storage(data.pop("steps_json", None))
    data["tips"] = _tips_from_storage(data.pop("tips_json", None))
    qty, unit = _pair_base_servings(
        data.get("base_servings_qty"), data.get("base_servings_unit"),
    )
    data["base_servings_qty"] = qty
    data["base_servings_unit"] = unit
    try:
        data["is_new"] = 1 if int(data.get("is_new") or 0) else 0
    except (TypeError, ValueError):
        data["is_new"] = 0
    data["sort_order"] = int(data.get("sort_order") or 0)
    return data


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecipeStore:
    def __init__(self, db_path: str | os.PathLike | None = None):
        # 优先级：显式传入（测试用 tmp 库注入） > RECIPES_DB_PATH 环境变量覆盖
        # > 统一单库路径 settings.APP_DB_PATH（与 DatabaseManager 同一常量，见 config.py）。
        self.db_path = str(
            db_path or os.environ.get("RECIPES_DB_PATH") or settings.APP_DB_PATH
        )
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> bool:
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        # 与 app.db 同库、独立连接：显式开 WAL + busy_timeout，避免与
        # DatabaseManager 主连接的写操作发生 SQLITE_BUSY 锁竞争。
        await self._conn.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE_WAL}")
        await self._conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._ensure_schema()
        await self._conn.commit()
        return True

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("RecipeStore 未连接")
        return self._conn

    async def _ensure_schema(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sop_stations (
                slug TEXT PRIMARY KEY NOT NULL,
                title TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sop_recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_slug TEXT NOT NULL,
                section TEXT NOT NULL,
                recipe_name TEXT NOT NULL,
                body_markdown TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                is_new INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                ingredients_json TEXT,
                steps_json TEXT,
                tips_json TEXT,
                base_servings_qty REAL,
                base_servings_unit TEXT,
                legacy_markdown TEXT,
                needs_review INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (station_slug) REFERENCES sop_stations(slug) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sop_recipes_station_order
            ON sop_recipes (station_slug, sort_order);
            CREATE TABLE IF NOT EXISTS sop_recipes_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id INTEGER NOT NULL,
                station_slug TEXT NOT NULL,
                section TEXT NOT NULL,
                recipe_name TEXT NOT NULL,
                body_markdown TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                is_new INTEGER NOT NULL DEFAULT 0,
                ingredients_json TEXT,
                steps_json TEXT,
                tips_json TEXT,
                base_servings_qty REAL,
                base_servings_unit TEXT,
                changed_at TEXT NOT NULL,
                FOREIGN KEY (station_slug) REFERENCES sop_stations(slug) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sop_recipes_history_recipe
            ON sop_recipes_history (recipe_id, changed_at DESC);
            """
        )
        cur = await self.conn.execute("PRAGMA table_info(sop_recipes)")
        cols = {row["name"] for row in await cur.fetchall()}
        if "is_active" not in cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )
        if "ingredients_json" not in cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes ADD COLUMN ingredients_json TEXT"
            )
        if "steps_json" not in cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes ADD COLUMN steps_json TEXT"
            )
        if "tips_json" not in cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes ADD COLUMN tips_json TEXT"
            )
        if "base_servings_qty" not in cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes ADD COLUMN base_servings_qty REAL"
            )
        if "base_servings_unit" not in cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes ADD COLUMN base_servings_unit TEXT"
            )
        if "legacy_markdown" not in cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes ADD COLUMN legacy_markdown TEXT"
            )
        if "needs_review" not in cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0"
            )
        hist_cur = await self.conn.execute("PRAGMA table_info(sop_recipes_history)")
        hist_cols = {row["name"] for row in await hist_cur.fetchall()}
        if "ingredients_json" not in hist_cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes_history ADD COLUMN ingredients_json TEXT"
            )
        if "steps_json" not in hist_cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes_history ADD COLUMN steps_json TEXT"
            )
        if "tips_json" not in hist_cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes_history ADD COLUMN tips_json TEXT"
            )
        if "base_servings_qty" not in hist_cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes_history ADD COLUMN base_servings_qty REAL"
            )
        if "base_servings_unit" not in hist_cols:
            await self.conn.execute(
                "ALTER TABLE sop_recipes_history ADD COLUMN base_servings_unit TEXT"
            )
        await self._canonicalize_stored_sections()

    async def _canonicalize_stored_sections(self) -> None:
        for table in ("sop_recipes", "sop_recipes_history"):
            cur = await self.conn.execute(f"SELECT DISTINCT section FROM {table}")
            for (name,) in await cur.fetchall():
                canon = canonicalize_section(name)
                if canon != name:
                    await self.conn.execute(
                        f"UPDATE {table} SET section = ? WHERE section = ?",
                        (canon, name),
                    )

    # ---- 岗位 ----
    async def list_stations(self) -> list[dict]:
        cur = await self.conn.execute(
            """
            SELECT s.slug, s.title, s.updated_at,
                   (SELECT COUNT(*) FROM sop_recipes r WHERE r.station_slug = s.slug) AS recipe_count,
                   (SELECT COUNT(*) FROM sop_recipes r
                    WHERE r.station_slug = s.slug AND r.needs_review = 1) AS needs_review_count
            FROM sop_stations s
            ORDER BY s.slug COLLATE NOCASE
            """
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_station(self, slug: str) -> Optional[dict]:
        cur = await self.conn.execute(
            "SELECT slug, title, updated_at FROM sop_stations WHERE slug = ?", (slug,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def create_station(self, slug: str, title: str) -> None:
        now = utc_now_iso()
        await self.conn.execute(
            "INSERT INTO sop_stations (slug, title, updated_at) VALUES (?, ?, ?)",
            (slug, title, now),
        )
        await self.conn.execute(
            "INSERT INTO sop_recipes (station_slug, section, recipe_name, body_markdown, sort_order, is_new, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (slug, NEW_STATION_SEED_SECTION, NEW_STATION_SEED_NAME, NEW_STATION_SEED_BODY, 0, 0, now),
        )
        await self.conn.commit()

    async def station_exists(self, slug: str) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM sop_stations WHERE slug = ?", (slug,))
        return await cur.fetchone() is not None

    async def rename_station(self, slug: str, title: str) -> None:
        await self.conn.execute(
            "UPDATE sop_stations SET title = ?, updated_at = ? WHERE slug = ?",
            (title, utc_now_iso(), slug),
        )
        await self.conn.commit()

    async def delete_station(self, slug: str) -> None:
        await self.conn.execute("DELETE FROM sop_stations WHERE slug = ?", (slug,))
        await self.conn.commit()

    async def _touch_station(self, slug: str, now: str) -> None:
        await self.conn.execute(
            "UPDATE sop_stations SET updated_at = ? WHERE slug = ?", (now, slug)
        )

    # ---- recipes ----
    async def list_recipes(self, slug: str) -> list[dict]:
        cur = await self.conn.execute(
            """
            SELECT id, section, recipe_name, body_markdown, sort_order, updated_at, is_new, is_active,
                   ingredients_json, steps_json, tips_json, base_servings_qty, base_servings_unit,
                   needs_review, legacy_markdown
            FROM sop_recipes WHERE station_slug = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (slug,),
        )
        return [_recipe_dict(r) for r in await cur.fetchall()]

    async def get_recipe(self, recipe_id: int) -> Optional[dict]:
        cur = await self.conn.execute(
            """
            SELECT id, station_slug, section, recipe_name, body_markdown, sort_order, is_new, is_active, updated_at,
                   ingredients_json, steps_json, tips_json, base_servings_qty, base_servings_unit,
                   needs_review, legacy_markdown
            FROM sop_recipes WHERE id = ?
            """,
            (recipe_id,),
        )
        row = await cur.fetchone()
        return _recipe_dict(row) if row else None

    async def _allocate_sort_order(self, slug: str, section: str, explicit: int | None) -> int:
        if explicit is not None:
            insert_at = int(explicit)
            await self.conn.execute(
                "UPDATE sop_recipes SET sort_order = sort_order + 1 "
                "WHERE station_slug = ? AND sort_order >= ?",
                (slug, insert_at),
            )
            return insert_at
        cur = await self.conn.execute(
            "SELECT MAX(sort_order) FROM sop_recipes WHERE station_slug = ? AND section = ?",
            (slug, section),
        )
        section_max = (await cur.fetchone())[0]
        if section_max is not None:
            insert_at = int(section_max) + 1
            await self.conn.execute(
                "UPDATE sop_recipes SET sort_order = sort_order + 1 "
                "WHERE station_slug = ? AND sort_order >= ?",
                (slug, insert_at),
            )
            return insert_at
        cur = await self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM sop_recipes WHERE station_slug = ?",
            (slug,),
        )
        return int((await cur.fetchone())[0])

    async def create_recipe(
        self, slug: str, section: str, recipe_name: str, body: str,
        explicit_sort: int | None, is_new_checked: bool, ingredients=None, steps=None,
        tips=None, base_servings_qty=None, base_servings_unit=None,
    ) -> int:
        now = utc_now_iso()
        section = canonicalize_section(section)
        sort_order = await self._allocate_sort_order(slug, section, explicit_sort)
        is_new = is_new_checked
        qty, unit = _pair_base_servings(base_servings_qty, base_servings_unit)
        cur = await self.conn.execute(
            "INSERT INTO sop_recipes (station_slug, section, recipe_name, body_markdown, sort_order, is_new, "
            "ingredients_json, steps_json, tips_json, base_servings_qty, base_servings_unit, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                slug, section, recipe_name, body, sort_order, 1 if is_new else 0,
                _ingredients_to_storage(ingredients), _steps_to_storage(steps),
                _tips_to_storage(tips), qty, unit, now,
            ),
        )
        await self._touch_station(slug, now)
        await self.conn.commit()
        return int(cur.lastrowid)

    async def update_recipe(
        self, recipe_id: int, section: str, recipe_name: str, body: str,
        sort_order: int, is_new_checked: bool, ingredients=None, steps=None,
        tips=None, base_servings_qty=_UNSET, base_servings_unit=_UNSET,
    ) -> Optional[dict]:
        current = await self.get_recipe(recipe_id)
        if current is None:
            return None
        section = canonicalize_section(section)
        now = utc_now_iso()
        is_new = is_new_checked
        stored_ingredients = (
            current["ingredients"] if ingredients is None else ingredients
        )
        stored_steps = current["steps"] if steps is None else steps
        stored_tips = current["tips"] if tips is None else tips
        qty_in = (
            current["base_servings_qty"] if base_servings_qty is _UNSET else base_servings_qty
        )
        unit_in = (
            current["base_servings_unit"] if base_servings_unit is _UNSET else base_servings_unit
        )
        stored_qty, stored_unit = _pair_base_servings(qty_in, unit_in)
        await self._insert_history_snapshot(recipe_id, now)
        await self.conn.execute(
            "UPDATE sop_recipes SET section=?, recipe_name=?, body_markdown=?, sort_order=?, is_new=?, "
            "ingredients_json=?, steps_json=?, tips_json=?, base_servings_qty=?, base_servings_unit=?, "
            "updated_at=? WHERE id = ?",
            (
                section, recipe_name, body, sort_order, 1 if is_new else 0,
                _ingredients_to_storage(stored_ingredients), _steps_to_storage(stored_steps),
                _tips_to_storage(stored_tips), stored_qty, stored_unit, now, recipe_id,
            ),
        )
        await self._touch_station(current["station_slug"], now)
        await self.conn.commit()
        return await self.get_recipe(recipe_id)

    async def reorder_recipes(self, slug: str, ids: list[int]) -> None:
        """Rewrite sort_order for the station list. No history rows."""
        now = utc_now_iso()
        for sort_order, recipe_id in enumerate(ids):
            await self.conn.execute(
                "UPDATE sop_recipes SET sort_order = ? WHERE id = ? AND station_slug = ?",
                (sort_order, recipe_id, slug),
            )
        await self._touch_station(slug, now)
        await self.conn.commit()

    async def delete_recipe(self, recipe_id: int) -> Optional[str]:
        current = await self.get_recipe(recipe_id)
        if current is None:
            return None
        slug = current["station_slug"]
        now = utc_now_iso()
        await self.conn.execute("DELETE FROM sop_recipes WHERE id = ?", (recipe_id,))
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM sop_recipes WHERE station_slug = ?", (slug,)
        )
        remaining = (await cur.fetchone())[0]
        if remaining == 0:
            await self.conn.execute(
                "INSERT INTO sop_recipes (station_slug, section, recipe_name, body_markdown, sort_order, is_new, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (slug, LAST_RECIPE_PLACEHOLDER_SECTION, LAST_RECIPE_PLACEHOLDER_NAME,
                 LAST_RECIPE_PLACEHOLDER_BODY, 0, 0, now),
            )
        await self._touch_station(slug, now)
        await self.conn.commit()
        return slug

    async def toggle_active(self, recipe_id: int) -> Optional[dict]:
        current = await self.get_recipe(recipe_id)
        if current is None:
            return None
        new_active = 0 if int(current["is_active"]) else 1
        now = utc_now_iso()
        await self.conn.execute(
            "UPDATE sop_recipes SET is_active = ?, updated_at = ? WHERE id = ?",
            (new_active, now, recipe_id),
        )
        await self._touch_station(current["station_slug"], now)
        await self.conn.commit()
        return await self.get_recipe(recipe_id)

    async def confirm_review(self, recipe_id: int) -> Optional[dict]:
        current = await self.get_recipe(recipe_id)
        if current is None:
            return None
        await self.conn.execute(
            "UPDATE sop_recipes SET needs_review = 0 WHERE id = ?",
            (recipe_id,),
        )
        await self.conn.commit()
        return await self.get_recipe(recipe_id)

    async def count_needs_review(self, slug: str | None = None) -> int:
        if slug is None:
            cur = await self.conn.execute(
                "SELECT COUNT(*) FROM sop_recipes WHERE needs_review = 1"
            )
        else:
            cur = await self.conn.execute(
                "SELECT COUNT(*) FROM sop_recipes WHERE needs_review = 1 AND station_slug = ?",
                (slug,),
            )
        row = await cur.fetchone()
        return int(row[0])

    async def search_recipes(self, q: str, include_inactive: bool = False) -> list[dict]:
        """Cross-station substring search on recipe_name. Empty q returns no groups."""
        needle = (q or "").strip()
        if not needle:
            return []
        sql = (
            "SELECT s.slug AS station_slug, s.title AS station_title, "
            "r.id AS recipe_id, r.recipe_name, r.section "
            "FROM sop_recipes r "
            "JOIN sop_stations s ON s.slug = r.station_slug "
            "WHERE LOWER(r.recipe_name) LIKE '%' || LOWER(?) || '%' ESCAPE ? "
        )
        params: list = [_escape_like_literal(needle), LIKE_ESCAPE_CHAR]
        if not include_inactive:
            sql += "AND r.is_active = 1 "
        sql += "ORDER BY s.slug COLLATE NOCASE, r.sort_order ASC, r.id ASC"
        cur = await self.conn.execute(sql, params)
        groups: list[dict] = []
        by_slug: dict[str, dict] = {}
        for row in await cur.fetchall():
            slug = row["station_slug"]
            group = by_slug.get(slug)
            if group is None:
                group = {
                    "station_slug": slug,
                    "station_title": row["station_title"],
                    "items": [],
                }
                by_slug[slug] = group
                groups.append(group)
            group["items"].append({
                "recipe_id": int(row["recipe_id"]),
                "recipe_name": row["recipe_name"],
                "section": row["section"],
            })
        return groups

    async def list_history(self, recipe_id: int) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT id, recipe_id, station_slug, section, recipe_name, body_markdown, sort_order, is_new, "
            "ingredients_json, steps_json, tips_json, base_servings_qty, base_servings_unit, changed_at "
            "FROM sop_recipes_history WHERE recipe_id = ? ORDER BY changed_at DESC, id DESC",
            (recipe_id,),
        )
        return [_history_dict(r) for r in await cur.fetchall()]

    async def _insert_history_snapshot(self, recipe_id: int, changed_at: str) -> None:
        await self.conn.execute(
            "INSERT INTO sop_recipes_history "
            "(recipe_id, station_slug, section, recipe_name, body_markdown, sort_order, is_new, "
            "ingredients_json, steps_json, tips_json, base_servings_qty, base_servings_unit, changed_at) "
            "SELECT id, station_slug, section, recipe_name, body_markdown, sort_order, is_new, "
            "ingredients_json, steps_json, tips_json, base_servings_qty, base_servings_unit, ? "
            "FROM sop_recipes WHERE id = ?",
            (changed_at, recipe_id),
        )

    async def restore_history(self, recipe_id: int, history_id: int) -> Optional[dict]:
        current = await self.get_recipe(recipe_id)
        if current is None:
            return None
        cur = await self.conn.execute(
            "SELECT id, recipe_id, station_slug, section, recipe_name, body_markdown, sort_order, is_new, "
            "ingredients_json, steps_json, tips_json, base_servings_qty, base_servings_unit, changed_at "
            "FROM sop_recipes_history WHERE id = ? AND recipe_id = ?",
            (history_id, recipe_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        snapshot = _history_dict(row)
        now = utc_now_iso()
        await self._insert_history_snapshot(recipe_id, now)
        stored_qty, stored_unit = _pair_base_servings(
            snapshot["base_servings_qty"], snapshot["base_servings_unit"],
        )
        await self.conn.execute(
            "UPDATE sop_recipes SET section=?, recipe_name=?, body_markdown=?, sort_order=?, is_new=?, "
            "ingredients_json=?, steps_json=?, tips_json=?, base_servings_qty=?, base_servings_unit=?, "
            "updated_at=? WHERE id = ?",
            (
                canonicalize_section(snapshot["section"]), snapshot["recipe_name"], snapshot["body_markdown"],
                snapshot["sort_order"], snapshot["is_new"],
                _ingredients_to_storage(snapshot["ingredients"]),
                _steps_to_storage(snapshot["steps"]),
                _tips_to_storage(snapshot["tips"]),
                stored_qty, stored_unit, now, recipe_id,
            ),
        )
        await self._touch_station(current["station_slug"], now)
        await self.conn.commit()
        return await self.get_recipe(recipe_id)

    async def bulk_insert_recipes(self, slug: str, rows: list[tuple]) -> int:
        """rows: (section, recipe_name, body, sort_order, is_new_int, ingredients, steps, tips)."""
        now = utc_now_iso()
        await self.conn.executemany(
            "INSERT INTO sop_recipes (station_slug, section, recipe_name, body_markdown, sort_order, is_new, "
            "ingredients_json, steps_json, tips_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    slug, canonicalize_section(section), name, body, sort_order, is_new,
                    _ingredients_to_storage(ingredients), _steps_to_storage(steps),
                    _tips_to_storage(tips), now,
                )
                for (section, name, body, sort_order, is_new, ingredients, steps, tips) in rows
            ],
        )
        await self._touch_station(slug, now)
        await self.conn.commit()
        return len(rows)

    # ---- 渲染辅助 ----
    async def parsed_recipes(self, slug: str, include_inactive: bool = False) -> list[ParsedRecipe]:
        where = "WHERE station_slug = ?"
        if not include_inactive:
            where += " AND is_active = 1"
        cur = await self.conn.execute(
            "SELECT id, section, recipe_name, body_markdown, sort_order, is_new, is_active, "
            "ingredients_json, steps_json, tips_json, base_servings_qty, base_servings_unit "
            "FROM sop_recipes " + where + " ORDER BY sort_order ASC, id ASC",
            (slug,),
        )
        rows = await cur.fetchall()
        parsed: list[ParsedRecipe] = []
        for r in rows:
            qty, unit = _pair_base_servings(r["base_servings_qty"], r["base_servings_unit"])
            parsed.append(
                ParsedRecipe(
                    section=canonicalize_section(r["section"]), recipe_name=r["recipe_name"],
                    body_markdown=r["body_markdown"], sort_order=int(r["sort_order"]),
                    is_new=bool(r["is_new"]), is_active=bool(r["is_active"]),
                    id=int(r["id"]),
                    ingredients=tuple(_ingredients_from_storage(r["ingredients_json"])),
                    steps=tuple(_steps_from_storage(r["steps_json"])),
                    tips=tuple(_tips_from_storage(r["tips_json"])),
                    base_servings_qty=qty,
                    base_servings_unit=unit,
                )
            )
        return parsed

    async def station_display_markdown(self, slug: str, include_inactive: bool = False) -> Optional[str]:
        station = await self.get_station(slug)
        if station is None:
            return None
        recipes = await self.parsed_recipes(slug, include_inactive)
        if not recipes:
            return None
        return recipes_to_display_markdown(station["title"], recipes)

    async def station_display_html(self, slug: str, include_inactive: bool = False) -> Optional[str]:
        station = await self.get_station(slug)
        if station is None:
            return None
        recipes = await self.parsed_recipes(slug, include_inactive)
        if not recipes:
            return None
        return render_station_html(station["title"], recipes)
