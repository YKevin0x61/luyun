#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工餐采购域的数据访问层（ADR 0135，迁移 `0053_staff_meal.sql`）。

四张表：**每日菜单**（`staff_meal_menu_days`）、**采购单**（`staff_meal_orders`）、
单内菜品快照（`staff_meal_order_dishes`）与采购条目（`staff_meal_order_items`）。

这里只有 SQL 与行转换；业务规则（谁能改哪张单、提交校验、汇总排序、推送入队）在
`services/staff_meal/service.py`。四张表只有本 store 一个写入口，Admin 数据浏览器
只读列出（`db_core/schema.py` 的 `STAFF_MEAL_TABLES`）。

写操作走 `services.identity.serialized_write`：共用 `DatabaseManager._write_lock`
（与其它域同一把全局锁），异常时回滚半截事务。脱开 DatabaseManager 单独构造
（单测、脚本）时退化为私有锁。
"""

from __future__ import annotations

import functools
import logging
from datetime import datetime
from typing import Any, Callable, Optional

import asyncpg

from db_core.errors import is_integrity_violation
from db_core.utils import CHINA_TZ
from services.identity import serialized_write

logger = logging.getLogger(__name__)

__all__ = [
    "MENU_DAY_WRITE_FIELDS",
    "ORDER_WRITE_FIELDS",
    "StaffMealError",
    "StaffMealStore",
    "month_bounds",
]

_MENU_DAY_COLUMNS = (
    'id, menu_date, weekday, lunch_main, lunch_side1, lunch_side2,'
    ' dinner_main, dinner_side1, dinner_side2, drink, freeform, source,'
    ' created_at, updated_at'
)
_ORDER_COLUMNS = (
    'id, order_date, status, remark, submitter_employee_id, submitter_name,'
    ' submitted_at, push_status, pushed_at, push_error, push_seq,'
    ' created_at, updated_at'
)
_DISH_COLUMNS = 'id, order_id, dish_name, meal, role, sort_order, created_at'
_ITEM_COLUMNS = (
    'id, order_id, dish_id, item_name, quantity, unit, sort_order,'
    ' created_at, updated_at'
)

# 可由 `upsert_menu_day` / `update_menu_day` 写的列（白名单，拼 SET 用）。
MENU_DAY_WRITE_FIELDS = (
    "weekday",
    "lunch_main",
    "lunch_side1",
    "lunch_side2",
    "dinner_main",
    "dinner_side1",
    "dinner_side2",
    "drink",
    "freeform",
    "source",
)
# 可由 `update_order` 写的列。`order_date` / `push_seq` 不在这里：前者是身份，
# 后者只由提交路径自己 +1（见 service）。
ORDER_WRITE_FIELDS = (
    "status",
    "remark",
    "submitter_employee_id",
    "submitter_name",
    "submitted_at",
    "push_status",
    "pushed_at",
    "push_error",
)


class StaffMealError(ValueError):
    """员工餐域的业务错误。``code`` 给 API 层查文案，不是给店里看的文案。"""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def _needs_migration(method):
    """0053 还没应用时，把缺表/缺列换成一句能照做的错（同损耗台账的装饰器）。"""

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            raise StaffMealError("not_migrated", str(exc)) from exc

    return wrapper


def month_bounds(year_month: str) -> tuple[str, str]:
    """``'2026-11'`` → ``('2026-11-01', '2026-12-01')``：半开区间的上下界。

    日期列是 TEXT（`YYYY-MM-DD`），ISO 字符串的字典序就是时间序，所以直接比字符串，
    不做日期运算。格式不对抛 ``StaffMealError('bad_month')``。
    """
    text = str(year_month or "").strip()
    parts = text.split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
        raise StaffMealError("bad_month", text)
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise StaffMealError("bad_month", text) from exc
    if not (1 <= month <= 12):
        raise StaffMealError("bad_month", text)
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    return start, end


def _menu_day_row(row: Any) -> dict:
    record = dict(row)
    if record.get("weekday") is not None:
        record["weekday"] = int(record["weekday"])
    record["freeform"] = bool(record.get("freeform"))
    return record


def _order_row(row: Any) -> dict:
    record = dict(row)
    record["push_seq"] = int(record.get("push_seq") or 0)
    return record


def _dish_row(row: Any) -> dict:
    record = dict(row)
    record["sort_order"] = int(record.get("sort_order") or 0)
    return record


def _item_row(row: Any) -> dict:
    record = dict(row)
    quantity = record.get("quantity")
    record["quantity"] = float(quantity) if quantity is not None else None
    return record


class StaffMealStore:
    """菜单日 / 采购单 / 菜品 / 条目的读写口。构造要一个已打开的库连接。"""

    def __init__(self, conn_or_db, *, now: Optional[Callable[[], datetime]] = None):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("StaffMealStore requires an open database connection")
        self._conn = conn
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._write_lock_owner = conn_or_db
        self._local_write_lock = None

    @property
    def _write_lock(self):
        shared = getattr(self._write_lock_owner, "_write_lock", None)
        if shared is not None:
            return shared
        if self._local_write_lock is None:
            import asyncio

            self._local_write_lock = asyncio.Lock()
        return self._local_write_lock

    # ── 通用 ──────────────────────────────────────────────────

    def _now_dt(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value

    def _now_iso(self) -> str:
        return self._now_dt().isoformat()

    def now_iso(self) -> str:
        """当前时刻（北京时间 ISO）；服务层写提交时间用。"""
        return self._now_iso()

    def today(self) -> str:
        """今天 —— **自然日**（北京时零点切），员工餐的使用日就是这个口径。"""
        return self._now_dt().date().isoformat()

    async def _rollback_quietly(self) -> None:
        try:
            await self._conn.rollback()
        except Exception:  # pragma: no cover - 回滚失败不该盖住原始错误
            logger.debug("回滚失败（忽略）", exc_info=True)

    async def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = await self._conn.execute(sql, params)
        return list(await cur.fetchall())

    # ── 每日菜单 ──────────────────────────────────────────────

    @_needs_migration
    async def menu_day(self, menu_date: str) -> Optional[dict]:
        rows = await self._rows(
            f'SELECT {_MENU_DAY_COLUMNS} FROM staff_meal_menu_days WHERE menu_date = ?',
            (str(menu_date),),
        )
        return _menu_day_row(rows[0]) if rows else None

    @_needs_migration
    async def menu_days_month(self, year_month: str) -> list[dict]:
        start, end = month_bounds(year_month)
        rows = await self._rows(
            f'SELECT {_MENU_DAY_COLUMNS} FROM staff_meal_menu_days'
            ' WHERE menu_date >= ? AND menu_date < ? ORDER BY menu_date',
            (start, end),
        )
        return [_menu_day_row(row) for row in rows]

    @_needs_migration
    async def _upsert_menu_day_row(self, day: dict, now: str) -> None:
        """写一天菜单但不提交（导入要整月一个事务，见 ``apply_menu_month``）。

        不加 ``@serialized_write``：调用方（``upsert_menu_day`` / ``apply_menu_month``）
        已经持有写锁，asyncio.Lock 不可重入，再取一次会自己等自己。
        """
        menu_date = str(day.get("menu_date") or "")
        if not menu_date:
            raise StaffMealError("bad_date", "menu_date required")
        values = [day.get(field) for field in MENU_DAY_WRITE_FIELDS]
        values = [
            bool(value) if field == "freeform" else value
            for field, value in zip(MENU_DAY_WRITE_FIELDS, values)
        ]
        # 解析器产出的行没有 source（它只知道「从表里读出来的」），这里兜底成 xlsx；
        # 单天手改在 service 层显式传 manual。
        values[MENU_DAY_WRITE_FIELDS.index("source")] = (
            values[MENU_DAY_WRITE_FIELDS.index("source")] or "xlsx"
        )
        placeholders = ", ".join("?" for _ in range(len(MENU_DAY_WRITE_FIELDS) + 3))
        updates = ", ".join(f'{field} = EXCLUDED.{field}' for field in MENU_DAY_WRITE_FIELDS)
        await self._conn.execute(
            f'INSERT INTO staff_meal_menu_days'
            f' (menu_date, created_at, updated_at, {", ".join(MENU_DAY_WRITE_FIELDS)})'
            f' VALUES ({placeholders})'
            f' ON CONFLICT (tenant_id, menu_date) DO UPDATE SET {updates},'
            ' updated_at = EXCLUDED.updated_at',
            tuple([menu_date, now, now] + values),
        )

    @_needs_migration
    @serialized_write
    async def upsert_menu_day(self, day: dict) -> dict:
        """按 ``menu_date`` 新增或覆盖一天菜单（单天手改用）。"""
        menu_date = str(day.get("menu_date") or "")
        await self._upsert_menu_day_row(day, self._now_iso())
        await self._conn.commit()
        saved = await self.menu_day(menu_date)
        assert saved is not None
        return saved

    @_needs_migration
    @serialized_write
    async def update_menu_day(self, menu_date: str, fields: dict) -> dict:
        """只改给定列；没有这天的菜单就报错（手改不负责建菜单）。"""
        updates = {
            key: value for key, value in fields.items() if key in MENU_DAY_WRITE_FIELDS
        }
        if not updates:
            saved = await self.menu_day(menu_date)
            if saved is None:
                raise StaffMealError("menu_day_not_found", menu_date)
            return saved
        assignments = ", ".join(f"{key} = ?" for key in updates)
        await self._conn.execute(
            f'UPDATE staff_meal_menu_days SET {assignments}, updated_at = ?'
            ' WHERE menu_date = ?',
            tuple(updates.values()) + (self._now_iso(), str(menu_date)),
        )
        await self._conn.commit()
        saved = await self.menu_day(menu_date)
        if saved is None:
            raise StaffMealError("menu_day_not_found", menu_date)
        return saved

    # ── 采购单 ────────────────────────────────────────────────

    @_needs_migration
    async def order_by_date(self, order_date: str) -> Optional[dict]:
        rows = await self._rows(
            f'SELECT {_ORDER_COLUMNS} FROM staff_meal_orders WHERE order_date = ?',
            (str(order_date),),
        )
        return _order_row(rows[0]) if rows else None

    @_needs_migration
    async def order_by_id(self, order_id: int) -> Optional[dict]:
        rows = await self._rows(
            f'SELECT {_ORDER_COLUMNS} FROM staff_meal_orders WHERE id = ?',
            (int(order_id),),
        )
        return _order_row(rows[0]) if rows else None

    @_needs_migration
    async def orders_month(self, year_month: str) -> list[dict]:
        start, end = month_bounds(year_month)
        rows = await self._rows(
            f'SELECT {_ORDER_COLUMNS} FROM staff_meal_orders'
            ' WHERE order_date >= ? AND order_date < ? ORDER BY order_date',
            (start, end),
        )
        return [_order_row(row) for row in rows]

    @_needs_migration
    async def _ensure_order_row(self, order_date: str, now: str) -> tuple[dict, bool]:
        """这天没有单就建一张空草稿（不提交），返回 (单据, 是否新建)。

        不加 ``@serialized_write``：调用方已持写锁（同 ``_upsert_menu_day_row``）。
        """
        existing = await self.order_by_date(order_date)
        if existing is not None:
            return existing, False
        try:
            await self._conn.execute(
                'INSERT INTO staff_meal_orders (order_date, created_at, updated_at)'
                ' VALUES (?, ?, ?)',
                (str(order_date), now, now),
            )
        except Exception as exc:
            if not is_integrity_violation(exc):
                raise
            # 并发下两边都没查到、一起插入，第二个撞唯一键 —— 读回已有的那张。
            await self._rollback_quietly()
            existing = await self.order_by_date(order_date)
            if existing is not None:
                return existing, False
            raise
        created = await self.order_by_date(order_date)
        assert created is not None
        return created, True

    @_needs_migration
    @serialized_write
    async def ensure_order(self, order_date: str) -> dict:
        """这天没有单就建一张空草稿；有就原样返回。"""
        order, _created = await self._ensure_order_row(order_date, self._now_iso())
        await self._conn.commit()
        return order

    @_needs_migration
    @serialized_write
    async def update_order(self, order_id: int, fields: dict) -> dict:
        updates = {key: value for key, value in fields.items() if key in ORDER_WRITE_FIELDS}
        if not updates:
            saved = await self.order_by_id(order_id)
            if saved is None:
                raise StaffMealError("order_not_found", str(order_id))
            return saved
        assignments = ", ".join(f"{key} = ?" for key in updates)
        await self._conn.execute(
            f'UPDATE staff_meal_orders SET {assignments}, updated_at = ? WHERE id = ?',
            tuple(updates.values()) + (self._now_iso(), int(order_id)),
        )
        await self._conn.commit()
        saved = await self.order_by_id(order_id)
        if saved is None:
            raise StaffMealError("order_not_found", str(order_id))
        return saved

    @_needs_migration
    @serialized_write
    async def bump_push_seq(self, order_id: int) -> int:
        """提交/重推时把 ``push_seq`` +1 并返回新值（拼 business_reference 用）。"""
        await self._conn.execute(
            'UPDATE staff_meal_orders SET push_seq = push_seq + 1, updated_at = ?'
            ' WHERE id = ?',
            (self._now_iso(), int(order_id)),
        )
        await self._conn.commit()
        saved = await self.order_by_id(order_id)
        if saved is None:
            raise StaffMealError("order_not_found", str(order_id))
        return int(saved["push_seq"])

    @_needs_migration
    @serialized_write
    async def delete_order(self, order_id: int) -> None:
        """删单（菜品与条目靠外键级联）。是否允许删由 service 判。"""
        await self._conn.execute(
            'DELETE FROM staff_meal_orders WHERE id = ?', (int(order_id),)
        )
        await self._conn.commit()

    # ── 单内菜品 ──────────────────────────────────────────────

    @_needs_migration
    async def dishes_for(self, order_id: int) -> list[dict]:
        rows = await self._rows(
            f'SELECT {_DISH_COLUMNS} FROM staff_meal_order_dishes'
            ' WHERE order_id = ? ORDER BY sort_order, id',
            (int(order_id),),
        )
        return [_dish_row(row) for row in rows]

    @_needs_migration
    @serialized_write
    async def replace_dishes(self, order_id: int, dishes: list[dict]) -> list[dict]:
        """整单替换菜品快照（导入刷新草稿 / 手工加菜都用它）。

        先清条目再清菜品：菜品删除会级联删条目，顺序反了会先白删一遍。
        """
        await self._conn.execute(
            'DELETE FROM staff_meal_order_items WHERE order_id = ?', (int(order_id),)
        )
        await self._conn.execute(
            'DELETE FROM staff_meal_order_dishes WHERE order_id = ?', (int(order_id),)
        )
        now = self._now_iso()
        for index, dish in enumerate(dishes):
            await self._conn.execute(
                'INSERT INTO staff_meal_order_dishes'
                ' (order_id, dish_name, meal, role, sort_order, created_at)'
                ' VALUES (?, ?, ?, ?, ?, ?)',
                (
                    int(order_id),
                    str(dish.get("dish_name") or ""),
                    str(dish.get("meal") or ""),
                    dish.get("role"),
                    int(dish.get("sort_order", index)),
                    now,
                ),
            )
        await self._conn.commit()
        return await self.dishes_for(order_id)

    @_needs_migration
    @serialized_write
    async def sync_dishes(self, order_id: int, dishes: list[dict]) -> list[dict]:
        """按菜单刷新草稿单的菜品快照，**保留同名菜的 id**。

        为什么不能直接 `replace_dishes`：菜品删除会级联删掉它下面的采购条目（菜名与
        数量是店长一条条填的），而重传一份菜单只是刷新菜单，不该把已填的食材清空。
        所以按 `(菜品名, 餐次)` 对齐：同一道菜原地保留、只更新位置与做法；菜单里没了
        的菜才删（这一道菜的食材随之消失，符合预期）；新菜插入。

        已提交单不调用它（单据是快照，Q16）。调用方负责先确认状态。
        """
        await self._sync_dishes_rows(order_id, dishes, self._now_iso())
        await self._conn.commit()
        return await self.dishes_for(order_id)

    async def _sync_dishes_rows(
        self, order_id: int, dishes: list[dict], now: str
    ) -> None:
        """``sync_dishes`` 的无提交版本：导入/手改整月时和菜单写在同一个事务里。"""
        existing = await self.dishes_for(order_id)
        by_key = {(str(d["dish_name"]), str(d["meal"])): d for d in existing}
        wanted_keys = set()
        for index, dish in enumerate(dishes):
            name = str(dish.get("dish_name") or "").strip()
            meal = str(dish.get("meal") or "").strip()
            role = dish.get("role")
            sort_order = int(dish.get("sort_order", index))
            key = (name, meal)
            wanted_keys.add(key)
            current = by_key.get(key)
            if current is None:
                await self._conn.execute(
                    'INSERT INTO staff_meal_order_dishes'
                    ' (order_id, dish_name, meal, role, sort_order, created_at)'
                    ' VALUES (?, ?, ?, ?, ?, ?)',
                    (int(order_id), name, meal, role, sort_order, now),
                )
                continue
            if current.get("role") != role or int(current.get("sort_order") or 0) != sort_order:
                await self._conn.execute(
                    'UPDATE staff_meal_order_dishes SET role = ?, sort_order = ? WHERE id = ?',
                    (role, sort_order, int(current["id"])),
                )
        for key, current in by_key.items():
            if key in wanted_keys:
                continue
            await self._conn.execute(
                'DELETE FROM staff_meal_order_dishes WHERE id = ?', (int(current["id"]),)
            )

    # ── 整月导入（一个事务）──────────────────────────────────

    @_needs_migration
    @serialized_write
    async def apply_menu_month(self, entries: list[dict]) -> dict:
        """一次写入整月菜单：菜单 UPSERT + 草稿单 ensure + 草稿菜同步，**一个事务**。

        ``entries`` 每项是一天：菜单写列 + ``_dishes``（该天的菜品快照）。已提交的单
        （``status == 'submitted'``）只 ensure 不碰菜品 —— 单据是快照（Q16：重传菜单
        不动单据）。失败整批回滚，不会出现「导了一半」的月份。

        返回 ``{"orders_created", "draft_refreshed", "submitted_kept"}``。
        """
        now = self._now_iso()
        created = 0
        refreshed = 0
        submitted_kept = 0
        for entry in entries:
            await self._upsert_menu_day_row(entry, now)
            order, was_created = await self._ensure_order_row(
                str(entry["menu_date"]), now
            )
            if was_created:
                created += 1
            if str(order.get("status") or "draft") == "submitted":
                submitted_kept += 1
                continue
            await self._sync_dishes_rows(
                int(order["id"]), list(entry.get("_dishes") or []), now
            )
            if not was_created:
                refreshed += 1
        await self._conn.commit()
        return {
            "orders_created": created,
            "draft_refreshed": refreshed,
            "submitted_kept": submitted_kept,
        }

    # ── 采购条目 ──────────────────────────────────────────────

    @_needs_migration
    async def items_for(self, order_id: int) -> list[dict]:
        rows = await self._rows(
            f'SELECT {_ITEM_COLUMNS} FROM staff_meal_order_items'
            ' WHERE order_id = ? ORDER BY sort_order, id',
            (int(order_id),),
        )
        return [_item_row(row) for row in rows]

    @_needs_migration
    @serialized_write
    async def replace_items(self, order_id: int, items: list[dict]) -> list[dict]:
        """整单替换条目（页面「保存」一次提交整张单的形状）。

        ``dish_id`` 必须是本单已存在的菜品，或 ``None``（单据级其他采购项）。
        """
        await self._conn.execute(
            'DELETE FROM staff_meal_order_items WHERE order_id = ?', (int(order_id),)
        )
        now = self._now_iso()
        for index, item in enumerate(items):
            await self._conn.execute(
                'INSERT INTO staff_meal_order_items'
                ' (order_id, dish_id, item_name, quantity, unit, sort_order,'
                ' created_at, updated_at)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    int(order_id),
                    item.get("dish_id"),
                    str(item.get("item_name") or "").strip(),
                    item.get("quantity"),
                    (item.get("unit") or "").strip() or None,
                    int(item.get("sort_order", index)),
                    now,
                    now,
                ),
            )
        await self._conn.commit()
        return await self.items_for(order_id)

    @_needs_migration
    async def item_counts_for_month(self, year_month: str) -> dict[int, int]:
        """每张单「填了数量的条目数」，月历上标「已填 n 项」用。"""
        start, end = month_bounds(year_month)
        rows = await self._rows(
            'SELECT i.order_id AS order_id, COUNT(*) AS n'
            ' FROM staff_meal_order_items i'
            ' JOIN staff_meal_orders o ON o.id = i.order_id'
            ' WHERE o.order_date >= ? AND o.order_date < ? AND i.quantity IS NOT NULL'
            ' GROUP BY i.order_id',
            (start, end),
        )
        return {int(row["order_id"]): int(row["n"]) for row in rows}

    @_needs_migration
    async def summary_rows(self, order_id: int) -> list[dict]:
        """按「食材 + 单位」汇总，供推送文本用。

        数量全空的食材名也保留一行（用户裁定：同名菜只带出名称、数量留空时，推送里
        只写名称）。排序固定为名称 + 单位，保证同一天重推的文本稳定。
        """
        rows = await self._rows(
            'SELECT item_name, unit, SUM(quantity) AS total_qty'
            ' FROM staff_meal_order_items'
            ' WHERE order_id = ?'
            ' GROUP BY item_name, unit'
            ' ORDER BY item_name, unit',
            (int(order_id),),
        )
        result = []
        for row in rows:
            total = row.get("total_qty")
            result.append(
                {
                    "item_name": row["item_name"],
                    "unit": row.get("unit") or "",
                    "quantity": float(total) if total is not None else None,
                }
            )
        return result

    @_needs_migration
    async def item_names_for_dish(self, dish_name: str, limit: int = 50) -> list[dict]:
        """这个菜名历史上买过的食材（按出现次数降序）——「同名菜带出食材名」用。

        只返回名称与单位，不返回数量。
        """
        name = str(dish_name or "").strip()
        if not name:
            return []
        rows = await self._rows(
            'SELECT i.item_name, i.unit, COUNT(*) AS uses'
            ' FROM staff_meal_order_items i'
            ' JOIN staff_meal_order_dishes d ON d.id = i.dish_id'
            ' WHERE d.dish_name = ? AND i.item_name <> ?'
            ' GROUP BY i.item_name, i.unit'
            ' ORDER BY uses DESC, i.item_name',
            (name, ""),
        )
        return [
            {
                "item_name": row["item_name"],
                "unit": row.get("unit") or "",
                "uses": int(row.get("uses") or 0),
            }
            for row in rows[: max(1, int(limit))]
        ]
