"""配方库异步数据访问层（aiosqlite）。配方相关表与其它业务表同库存放于统一的
data/app.db（WAL），自身仍持有独立连接（不接入 DatabaseManager 的共享连接）。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from config import settings
from db_core.utils import SQLITE_BUSY_TIMEOUT_MS, SQLITE_JOURNAL_MODE_WAL

from .sop_parse import (
    ParsedRecipe,
    infer_recipe_is_new,
    recipes_to_display_markdown,
)

SLUG_MAX_LEN = 64
TEXT_FIELD_MAX_LEN = 120
BODY_MAX_LEN = 50_000


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

    # ---- 岗位 ----
    async def list_stations(self) -> list[dict]:
        cur = await self.conn.execute(
            """
            SELECT s.slug, s.title, s.updated_at,
                   (SELECT COUNT(*) FROM sop_recipes r WHERE r.station_slug = s.slug) AS recipe_count
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
            (slug, "配方", "示例条目", "在此编写 Markdown 正文。", 0, 0, now),
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

    # ---- 条目 ----
    async def list_recipes(self, slug: str) -> list[dict]:
        cur = await self.conn.execute(
            """
            SELECT id, section, recipe_name, body_markdown, sort_order, updated_at, is_new, is_active
            FROM sop_recipes WHERE station_slug = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (slug,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_recipe(self, recipe_id: int) -> Optional[dict]:
        cur = await self.conn.execute(
            """
            SELECT id, station_slug, section, recipe_name, body_markdown, sort_order, is_new, is_active, updated_at
            FROM sop_recipes WHERE id = ?
            """,
            (recipe_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

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
        explicit_sort: int | None, is_new_checked: bool,
    ) -> int:
        now = utc_now_iso()
        sort_order = await self._allocate_sort_order(slug, section, explicit_sort)
        is_new = infer_recipe_is_new(recipe_name, body) or is_new_checked
        cur = await self.conn.execute(
            "INSERT INTO sop_recipes (station_slug, section, recipe_name, body_markdown, sort_order, is_new, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (slug, section, recipe_name, body, sort_order, 1 if is_new else 0, now),
        )
        await self._touch_station(slug, now)
        await self.conn.commit()
        return int(cur.lastrowid)

    async def update_recipe(
        self, recipe_id: int, section: str, recipe_name: str, body: str,
        sort_order: int, is_new_checked: bool,
    ) -> Optional[dict]:
        current = await self.get_recipe(recipe_id)
        if current is None:
            return None
        now = utc_now_iso()
        is_new = infer_recipe_is_new(recipe_name, body) or is_new_checked
        await self.conn.execute(
            "INSERT INTO sop_recipes_history "
            "(recipe_id, station_slug, section, recipe_name, body_markdown, sort_order, is_new, changed_at) "
            "SELECT id, station_slug, section, recipe_name, body_markdown, sort_order, is_new, ? "
            "FROM sop_recipes WHERE id = ?",
            (now, recipe_id),
        )
        await self.conn.execute(
            "UPDATE sop_recipes SET section=?, recipe_name=?, body_markdown=?, sort_order=?, is_new=?, updated_at=? "
            "WHERE id = ?",
            (section, recipe_name, body, sort_order, 1 if is_new else 0, now, recipe_id),
        )
        await self._touch_station(current["station_slug"], now)
        await self.conn.commit()
        return await self.get_recipe(recipe_id)

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
                (slug, "正文", "（占位）", "请至少保留一条条目，或删除整个岗位。", 0, 0, now),
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

    async def list_history(self, recipe_id: int) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT section, recipe_name, body_markdown, sort_order, is_new, changed_at "
            "FROM sop_recipes_history WHERE recipe_id = ? ORDER BY changed_at DESC",
            (recipe_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def bulk_insert_recipes(self, slug: str, rows: list[tuple]) -> int:
        """rows: (section, recipe_name, body, sort_order, is_new_int)"""
        now = utc_now_iso()
        await self.conn.executemany(
            "INSERT INTO sop_recipes (station_slug, section, recipe_name, body_markdown, sort_order, is_new, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(slug, s, n, b, so, isn, now) for (s, n, b, so, isn) in rows],
        )
        await self._touch_station(slug, now)
        await self.conn.commit()
        return len(rows)

    # ---- 渲染辅助 ----
    async def _parsed_recipes(self, slug: str, include_inactive: bool = False) -> list[ParsedRecipe]:
        where = "WHERE station_slug = ?"
        if not include_inactive:
            where += " AND is_active = 1"
        cur = await self.conn.execute(
            "SELECT section, recipe_name, body_markdown, sort_order, is_new, is_active "
            "FROM sop_recipes " + where + " ORDER BY sort_order ASC, id ASC",
            (slug,),
        )
        rows = await cur.fetchall()
        return [
            ParsedRecipe(
                section=r["section"], recipe_name=r["recipe_name"],
                body_markdown=r["body_markdown"], sort_order=int(r["sort_order"]),
                is_new=bool(r["is_new"]), is_active=bool(r["is_active"]),
            )
            for r in rows
        ]

    async def station_display_markdown(self, slug: str, include_inactive: bool = False) -> Optional[str]:
        station = await self.get_station(slug)
        if station is None:
            return None
        recipes = await self._parsed_recipes(slug, include_inactive)
        if not recipes:
            return None
        return recipes_to_display_markdown(station["title"], recipes)
