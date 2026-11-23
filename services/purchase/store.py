#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""厨房采购域的数据访问层（迁移 `0062_kitchen_purchase.sql`）。

十二张表只有本 store 一个写入口（Admin 数据浏览器只读列出，见
`db_core/schema.py` 的 `PURCHASE_TABLES`）。这里只放 SQL 与行转换；业务规则
（岗位必选、盘点按 0、改码同步历史、谁能改哪张单、参考值怎么算）都在
`services/purchase/service.py` 与 `services/purchase/forecast.py`。

**事务边界在服务层**：本层方法不 commit，由服务层用
`db_core.write_gate.write_unit` 把「订单 + 行 + 盘点 + 事件」包成一个写单元。
这样改码（商品 + 历史行 + 审计）与提交（订单 + 行 + 盘点）都能一起成、一起回滚。

时间列是 TIMESTAMPTZ，统一传 `datetime`（北京时区）；日期列是 DATE，统一传
`datetime.date`。SQL 占位符仍写 `?`，由 PG 方言层改写。
"""

from __future__ import annotations

import functools
import logging
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

import asyncpg

from db_core.utils import CHINA_TZ

logger = logging.getLogger(__name__)

__all__ = [
    "PurchaseError",
    "PurchaseStore",
    "TAG_VALUES",
]

#: 小程序里的档位（spec §4.9）。DB 的 CHECK 也钉着同一组值。
TAG_VALUES: tuple[str, ...] = ("2万", "3万", "4万", "5万", "6万", "7万", "8万")

_SUPPLIER_COLUMNS = (
    'id, name, code, next_category_code, active, created_at, updated_at'
)
_CATEGORY_COLUMNS = (
    'id, supplier_id, name, code, next_product_seq, active, created_at, updated_at'
)
_PRODUCT_COLUMNS = (
    'id, product_code, supplier_id, category_id, name, specification, unit,'
    ' remark, order_step, safety_factor, need_stock_check, is_long_cycle,'
    ' active, created_at, updated_at'
)
_POSITION_COLUMNS = 'id, name, sort_order, show_in_order, active, created_at, updated_at'
_ORDER_COLUMNS = (
    'id, order_no, position_id, position_name, submitter_employee_id,'
    ' submitter_name, submitter_kind, status, submitted_at, completed_at,'
    ' completed_by, cancelled_at, cancelled_by, deleted_at, updated_at, updated_by'
)
_ITEM_COLUMNS = (
    'id, order_id, product_id, product_code, name, specification, unit,'
    ' supplier_id, supplier_name, category_name, position_name, quantity,'
    ' reference_qty, stock_qty, suggested_qty, order_step, sort_order'
)
_STOCK_COLUMNS = (
    'id, product_id, count_date, current_stock, counted_by_employee_id,'
    ' counted_by_name, counted_kind, counted_at, source_order_id'
)


class PurchaseError(ValueError):
    """采购域的业务错误；`code` 给 API 层查状态码与文案。"""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def _needs_migration(method):
    """0062 还没应用时，把缺表/缺列换成一句能照做的错。"""

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            raise PurchaseError("not_migrated", str(exc)) from exc

    return wrapper


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _as_date(value: Any) -> date:
    """接受 `date` / `'YYYY-MM-DD'`，统一成 `date` 再进 DATE 列。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or '').strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise PurchaseError("bad_date", text) from exc


def _as_datetime(value: Any) -> datetime:
    """接受 `datetime` / ISO 文本；无时区按北京时区。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=CHINA_TZ)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise PurchaseError("bad_datetime", str(value)) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=CHINA_TZ)


def _iso_beijing(value: Any) -> Any:
    """时间统一按北京时区输出 ISO，前端才能只截日期显示。"""
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=CHINA_TZ)
    return value.astimezone(CHINA_TZ).isoformat()


def _supplier_row(row: Any) -> dict:
    record = dict(row)
    record['next_category_code'] = int(record.get('next_category_code') or 1)
    record['active'] = bool(record.get('active'))
    return record


def _category_row(row: Any) -> dict:
    record = dict(row)
    record['next_product_seq'] = int(record.get('next_product_seq') or 1)
    record['active'] = bool(record.get('active'))
    return record


def _position_row(row: Any) -> dict:
    record = dict(row)
    record['sort_order'] = int(record.get('sort_order') or 0)
    for key in ('show_in_order', 'active'):
        record[key] = bool(record.get(key))
    return record


def _product_row(row: Any) -> dict:
    record = dict(row)
    for key in ('need_stock_check', 'is_long_cycle', 'active'):
        record[key] = bool(record.get(key))
    for key in ('order_step', 'safety_factor'):
        record[key] = _num(record.get(key))
    record['positions'] = []
    return record


def _order_row(row: Any) -> dict:
    record = dict(row)
    for key in ('submitted_at', 'completed_at', 'cancelled_at', 'deleted_at', 'updated_at'):
        value = record.get(key)
        record[key] = _iso_beijing(value)
    for key in ('item_count', 'total_quantity'):
        if key in record and record[key] is not None:
            record[key] = _num(record[key]) if key == 'total_quantity' else int(record[key])
    return record


def _item_row(row: Any) -> dict:
    record = dict(row)
    for key in ('quantity', 'reference_qty', 'stock_qty', 'suggested_qty', 'order_step'):
        record[key] = _num(record.get(key))
    record['sort_order'] = int(record.get('sort_order') or 0)
    return record


def _stock_row(row: Any) -> dict:
    record = dict(row)
    record['current_stock'] = _num(record.get('current_stock')) or 0.0
    record['count_date'] = (
        record['count_date'].isoformat()
        if isinstance(record.get('count_date'), date)
        else record.get('count_date')
    )
    counted_at = record.get('counted_at')
    if isinstance(counted_at, datetime):
        record['counted_at'] = _iso_beijing(counted_at)
    return record


class PurchaseStore:
    """供应商 / 分类 / 商品 / 岗位 / 盘点 / 标签 / 采购单的读写口。"""

    def __init__(self, conn_or_db, *, now: Optional[Any] = None):
        conn = getattr(conn_or_db, '_conn', conn_or_db)
        if conn is None:
            raise RuntimeError('PurchaseStore requires an open database connection')
        self._conn = conn
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._write_lock_owner = conn_or_db

    # ── 通用 ──────────────────────────────────────────────────

    def _now_dt(self) -> datetime:
        value = self._now()
        return value if value.tzinfo else value.replace(tzinfo=CHINA_TZ)

    def today(self) -> date:
        return self._now_dt().date()

    async def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = await self._conn.execute(sql, params)
        return [dict(row) for row in await cur.fetchall()]

    # ── 供应商 ────────────────────────────────────────────────

    @_needs_migration
    async def suppliers(self, include_inactive: bool = False) -> list[dict]:
        where = '' if include_inactive else ' WHERE active = TRUE'
        rows = await self._rows(
            f'SELECT {_SUPPLIER_COLUMNS} FROM purchase_suppliers{where}'
            ' ORDER BY code, name'
        )
        return [_supplier_row(row) for row in rows]

    @_needs_migration
    async def supplier_by_id(self, supplier_id: int) -> Optional[dict]:
        rows = await self._rows(
            f'SELECT {_SUPPLIER_COLUMNS} FROM purchase_suppliers WHERE id = ?',
            (int(supplier_id),),
        )
        return _supplier_row(rows[0]) if rows else None

    @_needs_migration
    async def supplier_by_name(self, name: str) -> Optional[dict]:
        rows = await self._rows(
            f'SELECT {_SUPPLIER_COLUMNS} FROM purchase_suppliers WHERE name = ?',
            (str(name).strip(),),
        )
        return _supplier_row(rows[0]) if rows else None

    @_needs_migration
    async def insert_supplier(self, name: str, code: str, now: datetime) -> int:
        await self._conn.execute(
            'INSERT INTO purchase_suppliers (name, code, created_at, updated_at)'
            ' VALUES (?, ?, ?, ?)',
            (str(name), str(code), now, now),
        )
        cur = await self._conn.execute(
            'SELECT id FROM purchase_suppliers WHERE code = ?', (str(code),)
        )
        row = await cur.fetchone()
        return int(row['id'])

    @_needs_migration
    async def update_supplier(self, supplier_id: int, fields: dict, now: datetime) -> None:
        allowed = {'name': 'name', 'code': 'code', 'active': 'active'}
        updates = {allowed[key]: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ', '.join(f'{column} = ?' for column in updates)
        await self._conn.execute(
            f'UPDATE purchase_suppliers SET {assignments}, updated_at = ? WHERE id = ?',
            tuple(updates.values()) + (now, int(supplier_id)),
        )

    @_needs_migration
    async def active_product_count(self, *, supplier_id: Optional[int] = None,
                                   category_id: Optional[int] = None) -> int:
        """数还有多少个启用商品挂在这个供应商/分类下。

        停用前必须没有启用商品，否则目录查询仍会带出它们（只过滤商品 active），
        停用就变成「看起来禁了、下单页照样能选」。
        """
        clauses, params = ['active = TRUE'], []
        if supplier_id is not None:
            clauses.append('supplier_id = ?')
            params.append(int(supplier_id))
        if category_id is not None:
            clauses.append('category_id = ?')
            params.append(int(category_id))
        rows = await self._rows(
            'SELECT COUNT(*) AS total FROM purchase_products'
            f" WHERE {' AND '.join(clauses)}",
            tuple(params),
        )
        return int(rows[0]['total']) if rows else 0

    @_needs_migration
    async def bump_supplier_category_code(self, supplier_id: int, now: datetime) -> int:
        """返回下一个分类编码（两位），并把供应商的计数器 +1。

        方言层对 UPDATE 只解析 command tag、取不出 RETURNING 的行（见
        services/scheduling/store.py 的同类注释），所以先 UPDATE 再 SELECT；
        调用方在全局写锁内，两步之间不会被别的写插队。
        """
        await self._conn.execute(
            'UPDATE purchase_suppliers'
            ' SET next_category_code = next_category_code + 1, updated_at = ?'
            ' WHERE id = ?',
            (now, int(supplier_id)),
        )
        rows = await self._rows(
            'SELECT next_category_code FROM purchase_suppliers WHERE id = ?',
            (int(supplier_id),),
        )
        if not rows:
            raise PurchaseError('supplier_not_found', '供应商不存在')
        return int(rows[0]['next_category_code']) - 1

    # ── 分类 ──────────────────────────────────────────────────

    @_needs_migration
    async def categories(self, supplier_id: Optional[int] = None,
                         include_inactive: bool = False) -> list[dict]:
        clauses, params = [], []
        if supplier_id is not None:
            clauses.append('supplier_id = ?')
            params.append(int(supplier_id))
        if not include_inactive:
            clauses.append('active = TRUE')
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
        rows = await self._rows(
            f'SELECT {_CATEGORY_COLUMNS} FROM purchase_categories{where}'
            ' ORDER BY supplier_id, code, name',
            tuple(params),
        )
        return [_category_row(row) for row in rows]

    @_needs_migration
    async def category_by_id(self, category_id: int) -> Optional[dict]:
        rows = await self._rows(
            f'SELECT {_CATEGORY_COLUMNS} FROM purchase_categories WHERE id = ?',
            (int(category_id),),
        )
        return _category_row(rows[0]) if rows else None

    @_needs_migration
    async def insert_category(self, supplier_id: int, name: str, code: str,
                              next_product_seq: int, now: datetime) -> int:
        await self._conn.execute(
            'INSERT INTO purchase_categories'
            ' (supplier_id, name, code, next_product_seq, created_at, updated_at)'
            ' VALUES (?, ?, ?, ?, ?, ?)',
            (int(supplier_id), str(name), str(code), int(next_product_seq), now, now),
        )
        cur = await self._conn.execute(
            'SELECT id FROM purchase_categories WHERE supplier_id = ? AND code = ?',
            (int(supplier_id), str(code)),
        )
        row = await cur.fetchone()
        return int(row['id'])

    @_needs_migration
    async def update_category(self, category_id: int, fields: dict, now: datetime) -> None:
        allowed = {'name': 'name', 'code': 'code', 'active': 'active'}
        updates = {allowed[key]: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ', '.join(f'{column} = ?' for column in updates)
        await self._conn.execute(
            f'UPDATE purchase_categories SET {assignments}, updated_at = ? WHERE id = ?',
            tuple(updates.values()) + (now, int(category_id)),
        )

    @_needs_migration
    async def bump_category_product_seq(self, category_id: int, now: datetime) -> int:
        """返回下一个商品序号（三位），并把分类的计数器 +1。

        UPDATE 的 RETURNING 行在方言层取不出来，先写再读；调用方持全局写锁。
        """
        await self._conn.execute(
            'UPDATE purchase_categories'
            ' SET next_product_seq = next_product_seq + 1, updated_at = ?'
            ' WHERE id = ?',
            (now, int(category_id)),
        )
        rows = await self._rows(
            'SELECT next_product_seq FROM purchase_categories WHERE id = ?',
            (int(category_id),),
        )
        if not rows:
            raise PurchaseError('category_not_found', '分类不存在')
        return int(rows[0]['next_product_seq']) - 1

    # ── 岗位 ──────────────────────────────────────────────────

    @_needs_migration
    async def positions(self, include_inactive: bool = False) -> list[dict]:
        where = '' if include_inactive else ' WHERE active = TRUE'
        rows = await self._rows(
            f'SELECT {_POSITION_COLUMNS} FROM purchase_positions{where}'
            ' ORDER BY sort_order, id'
        )
        return [_position_row(row) for row in rows]

    @_needs_migration
    async def position_by_id(self, position_id: int) -> Optional[dict]:
        rows = await self._rows(
            f'SELECT {_POSITION_COLUMNS} FROM purchase_positions WHERE id = ?',
            (int(position_id),),
        )
        return _position_row(rows[0]) if rows else None

    @_needs_migration
    async def insert_position(self, name: str, sort_order: int, now: datetime,
                              show_in_order: bool = True) -> int:
        await self._conn.execute(
            'INSERT INTO purchase_positions'
            ' (name, sort_order, show_in_order, created_at, updated_at)'
            ' VALUES (?, ?, ?, ?, ?)',
            (str(name), int(sort_order), bool(show_in_order), now, now),
        )
        cur = await self._conn.execute(
            'SELECT id FROM purchase_positions WHERE name = ?', (str(name),)
        )
        row = await cur.fetchone()
        return int(row['id'])

    @_needs_migration
    async def update_position(self, position_id: int, fields: dict, now: datetime) -> None:
        allowed = {
            'name': 'name',
            'sort_order': 'sort_order',
            'show_in_order': 'show_in_order',
            'active': 'active',
        }
        updates = {allowed[key]: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ', '.join(f'{column} = ?' for column in updates)
        await self._conn.execute(
            f'UPDATE purchase_positions SET {assignments}, updated_at = ? WHERE id = ?',
            tuple(updates.values()) + (now, int(position_id)),
        )

    # ── 商品 ──────────────────────────────────────────────────

    @_needs_migration
    async def products(self, *, position_id: Optional[int] = None,
                       active_only: bool = True) -> list[dict]:
        clauses, params = [], []
        if active_only:
            clauses.append('p.active = TRUE')
        if position_id is not None:
            clauses.append(
                'EXISTS (SELECT 1 FROM purchase_product_positions pp'
                ' WHERE pp.product_id = p.id AND pp.position_id = ?)'
            )
            params.append(int(position_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
        rows = await self._rows(
            'SELECT p.id, p.product_code, p.supplier_id, p.category_id, p.name,'
            ' p.specification, p.unit, p.remark, p.order_step, p.safety_factor,'
            ' p.need_stock_check, p.is_long_cycle, p.active, p.created_at, p.updated_at,'
            ' s.name AS supplier_name, s.code AS supplier_code,'
            ' c.name AS category_name, c.code AS category_code'
            ' FROM purchase_products p'
            ' JOIN purchase_suppliers s ON s.id = p.supplier_id'
            ' JOIN purchase_categories c ON c.id = p.category_id'
            f'{where}'
            ' ORDER BY s.code, c.code, p.product_code',
            tuple(params),
        )
        products = [_product_row(row) for row in rows]
        await self._attach_positions(products)
        return products

    @_needs_migration
    async def product_by_id(self, product_id: int) -> Optional[dict]:
        rows = await self._rows(
            'SELECT p.id, p.product_code, p.supplier_id, p.category_id, p.name,'
            ' p.specification, p.unit, p.remark, p.order_step, p.safety_factor,'
            ' p.need_stock_check, p.is_long_cycle, p.active, p.created_at, p.updated_at,'
            ' s.name AS supplier_name, s.code AS supplier_code,'
            ' c.name AS category_name, c.code AS category_code'
            ' FROM purchase_products p'
            ' JOIN purchase_suppliers s ON s.id = p.supplier_id'
            ' JOIN purchase_categories c ON c.id = p.category_id'
            ' WHERE p.id = ?',
            (int(product_id),),
        )
        if not rows:
            return None
        product = _product_row(rows[0])
        await self._attach_positions([product])
        return product

    async def _attach_positions(self, products: list[dict]) -> None:
        if not products:
            return
        ids = [int(item['id']) for item in products]
        rows = await self._rows(
            'SELECT pp.product_id, pp.sort_order AS link_sort,'
            ' pos.id, pos.name, pos.sort_order, pos.show_in_order, pos.active'
            ' FROM purchase_product_positions pp'
            ' JOIN purchase_positions pos ON pos.id = pp.position_id'
            ' WHERE pp.product_id = ANY(?)'
            ' ORDER BY pos.sort_order, pos.id',
            (ids,),
        )
        by_product: dict[int, list[dict]] = {}
        for row in rows:
            record = _position_row(row)
            by_product.setdefault(int(row['product_id']), []).append(record)
        for product in products:
            product['positions'] = by_product.get(int(product['id']), [])

    @_needs_migration
    async def insert_product(self, values: dict, now: datetime) -> int:
        columns = (
            'product_code', 'supplier_id', 'category_id', 'name', 'specification',
            'unit', 'remark', 'order_step', 'safety_factor', 'need_stock_check',
            'is_long_cycle', 'active',
        )
        await self._conn.execute(
            f'INSERT INTO purchase_products ({", ".join(columns)}, created_at, updated_at)'
            f' VALUES ({", ".join("?" for _ in columns)}, ?, ?)',
            tuple(values.get(column) for column in columns) + (now, now),
        )
        cur = await self._conn.execute(
            'SELECT id FROM purchase_products WHERE product_code = ?',
            (str(values['product_code']),),
        )
        row = await cur.fetchone()
        return int(row['id'])

    @_needs_migration
    async def update_product(self, product_id: int, fields: dict, now: datetime) -> None:
        columns = (
            'product_code', 'supplier_id', 'category_id', 'name', 'specification',
            'unit', 'remark', 'order_step', 'safety_factor', 'need_stock_check',
            'is_long_cycle', 'active',
        )
        updates = {column: fields[column] for column in columns if column in fields}
        if not updates:
            return
        assignments = ', '.join(f'{column} = ?' for column in updates)
        await self._conn.execute(
            f'UPDATE purchase_products SET {assignments}, updated_at = ? WHERE id = ?',
            tuple(updates.values()) + (now, int(product_id)),
        )

    @_needs_migration
    async def set_product_positions(self, product_id: int, position_ids: Iterable[int],
                                    now: datetime) -> None:
        ids = sorted({int(value) for value in position_ids})
        await self._conn.execute(
            'DELETE FROM purchase_product_positions WHERE product_id = ?', (int(product_id),)
        )
        for index, position_id in enumerate(ids):
            await self._conn.execute(
                'INSERT INTO purchase_product_positions (product_id, position_id, sort_order)'
                ' VALUES (?, ?, ?)',
                (int(product_id), position_id, index),
            )
        await self._conn.execute(
            'UPDATE purchase_products SET updated_at = ? WHERE id = ?',
            (now, int(product_id)),
        )

    @_needs_migration
    async def product_by_code(self, code: str) -> Optional[dict]:
        rows = await self._rows(
            'SELECT id FROM purchase_products WHERE product_code = ?', (str(code),)
        )
        return await self.product_by_id(int(rows[0]['id'])) if rows else None

    @_needs_migration
    async def item_snapshot_sync(self, product: dict, now: datetime) -> int:
        """改码 / 换供应商或分类后，同步所有历史订单行的快照。返回影响行数。

        只改**快照列**，不动数量与归属岗位 —— 历史单要能按今天的目录读，
        但仍属于当时的提交人 / 岗位。
        """
        cur = await self._conn.execute(
            'UPDATE purchase_order_items SET product_code = ?, supplier_id = ?,'
            ' supplier_name = ?, category_name = ? WHERE product_id = ?',
            (
                str(product['product_code']),
                product.get('supplier_id'),
                str(product.get('supplier_name') or ''),
                str(product.get('category_name') or ''),
                int(product['id']),
            ),
        )
        return int(getattr(cur, 'rowcount', 0) or 0)

    # ── 盘点快照 ──────────────────────────────────────────────

    @_needs_migration
    async def stock_counts(self, count_date: Any,
                           product_ids: Optional[Iterable[int]] = None) -> dict[int, dict]:
        params: list[Any] = [_as_date(count_date)]
        sql = (
            f'SELECT {_STOCK_COLUMNS} FROM purchase_stock_counts WHERE count_date = ?'
        )
        ids = [int(value) for value in (product_ids or [])]
        if ids:
            sql += ' AND product_id = ANY(?)'
            params.append(ids)
        rows = await self._rows(sql, tuple(params))
        return {int(row['product_id']): _stock_row(row) for row in rows}

    @_needs_migration
    async def stock_history(self, product_id: int, start: Any, end: Any) -> list[dict]:
        rows = await self._rows(
            f'SELECT {_STOCK_COLUMNS} FROM purchase_stock_counts'
            ' WHERE product_id = ? AND count_date >= ? AND count_date <= ?'
            ' ORDER BY count_date',
            (int(product_id), _as_date(start), _as_date(end)),
        )
        return [_stock_row(row) for row in rows]

    @_needs_migration
    async def latest_stock(self, product_ids: Iterable[int], on_or_before: Any) -> dict[int, dict]:
        ids = [int(value) for value in product_ids]
        if not ids:
            return {}
        rows = await self._rows(
            'SELECT DISTINCT ON (product_id) id, product_id, count_date, current_stock,'
            ' counted_by_employee_id, counted_by_name, counted_kind, counted_at,'
            ' source_order_id'
            ' FROM purchase_stock_counts'
            ' WHERE product_id = ANY(?) AND count_date <= ?'
            ' ORDER BY product_id, count_date DESC',
            (ids, _as_date(on_or_before)),
        )
        return {int(row['product_id']): _stock_row(row) for row in rows}

    @_needs_migration
    async def upsert_stock(self, product_id: int, count_date: Any,
                           current_stock: float, actor: dict, now: datetime,
                           source_order_id: Optional[int] = None) -> None:
        await self._conn.execute(
            'INSERT INTO purchase_stock_counts'
            ' (product_id, count_date, current_stock, counted_by_employee_id,'
            '  counted_by_name, counted_kind, counted_at, source_order_id)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
            ' ON CONFLICT (tenant_id, product_id, count_date) DO UPDATE SET'
            '  current_stock = EXCLUDED.current_stock,'
            '  counted_by_employee_id = EXCLUDED.counted_by_employee_id,'
            '  counted_by_name = EXCLUDED.counted_by_name,'
            '  counted_kind = EXCLUDED.counted_kind,'
            '  counted_at = EXCLUDED.counted_at,'
            '  source_order_id = EXCLUDED.source_order_id',
            (
                int(product_id),
                _as_date(count_date),
                float(current_stock),
                actor.get('employee_id'),
                actor.get('name'),
                actor.get('kind'),
                now,
                source_order_id,
            ),
        )

    # ── 日期标签 / 星期默认 ──────────────────────────────────

    @_needs_migration
    async def date_tags(self, start: Any, end: Any) -> list[dict]:
        rows = await self._rows(
            'SELECT id, tag_date, tag, updated_by, updated_at FROM purchase_date_tags'
            ' WHERE tag_date >= ? AND tag_date <= ? ORDER BY tag_date',
            (_as_date(start), _as_date(end)),
        )
        for row in rows:
            if isinstance(row.get('tag_date'), date):
                row['tag_date'] = row['tag_date'].isoformat()
            if isinstance(row.get('updated_at'), datetime):
                row['updated_at'] = _iso_beijing(row['updated_at'])
        return rows

    @_needs_migration
    async def all_date_tags(self, start: Any, end: Any) -> dict[date, str]:
        return {
            _as_date(row['tag_date']): str(row['tag'])
            for row in await self.date_tags(start, end)
        }

    @_needs_migration
    async def set_date_tag(self, tag_date: Any, tag: str,
                           actor: dict, now: datetime) -> None:
        if tag not in TAG_VALUES:
            raise PurchaseError('bad_tag', tag)
        await self._conn.execute(
            'INSERT INTO purchase_date_tags (tag_date, tag, updated_by, updated_at)'
            ' VALUES (?, ?, ?, ?)'
            ' ON CONFLICT (tenant_id, tag_date) DO UPDATE SET'
            '  tag = EXCLUDED.tag, updated_by = EXCLUDED.updated_by,'
            '  updated_at = EXCLUDED.updated_at',
            (_as_date(tag_date), str(tag), actor.get('name'), now),
        )

    @_needs_migration
    async def delete_date_tag(self, tag_date: Any) -> None:
        await self._conn.execute(
            'DELETE FROM purchase_date_tags WHERE tag_date = ?', (_as_date(tag_date),)
        )

    @_needs_migration
    async def weekday_defaults(self) -> dict[int, str]:
        rows = await self._rows(
            'SELECT weekday, tag FROM purchase_weekday_defaults ORDER BY weekday'
        )
        return {int(row['weekday']): str(row['tag']) for row in rows}

    @_needs_migration
    async def set_weekday_default(self, weekday: int, tag: str, now: datetime) -> None:
        """weekday 用前端 Date.getDay()：0=周日、1=周一 … 6=周六。"""
        if not 0 <= int(weekday) <= 6:
            raise PurchaseError('bad_weekday', str(weekday))
        if tag not in TAG_VALUES:
            raise PurchaseError('bad_tag', tag)
        await self._conn.execute(
            'INSERT INTO purchase_weekday_defaults (weekday, tag, updated_at)'
            ' VALUES (?, ?, ?)'
            ' ON CONFLICT (tenant_id, weekday) DO UPDATE SET'
            '  tag = EXCLUDED.tag, updated_at = EXCLUDED.updated_at',
            (int(weekday), str(tag), now),
        )

    # ── 采购单 ────────────────────────────────────────────────

    @_needs_migration
    async def next_order_no(self, day: date) -> str:
        prefix = f'P{day.strftime("%Y%m%d")}-'
        rows = await self._rows(
            'SELECT order_no FROM purchase_orders WHERE order_no LIKE ?'
            ' ORDER BY order_no DESC LIMIT 1',
            (prefix + '%',),
        )
        serial = 1
        if rows:
            try:
                serial = int(str(rows[0]['order_no']).rsplit('-', 1)[1]) + 1
            except (IndexError, ValueError):
                serial = 1
        return f'{prefix}{serial:04d}'

    @_needs_migration
    async def insert_order(self, values: dict) -> int:
        columns = (
            'order_no', 'position_id', 'position_name', 'submitter_employee_id',
            'submitter_name', 'submitter_kind', 'status', 'submitted_at', 'updated_at',
            'updated_by',
        )
        await self._conn.execute(
            f'INSERT INTO purchase_orders ({", ".join(columns)})'
            f' VALUES ({", ".join("?" for _ in columns)})',
            tuple(values.get(column) for column in columns),
        )
        cur = await self._conn.execute(
            'SELECT id FROM purchase_orders WHERE order_no = ?',
            (str(values['order_no']),),
        )
        row = await cur.fetchone()
        return int(row['id'])

    @_needs_migration
    async def insert_item(self, order_id: int, values: dict) -> None:
        columns = (
            'order_id', 'product_id', 'product_code', 'name', 'specification', 'unit',
            'supplier_id', 'supplier_name', 'category_name', 'position_name',
            'quantity', 'reference_qty', 'stock_qty', 'suggested_qty', 'order_step',
            'sort_order',
        )
        payload = dict(values)
        payload['order_id'] = int(order_id)
        await self._conn.execute(
            f'INSERT INTO purchase_order_items ({", ".join(columns)})'
            f' VALUES ({", ".join("?" for _ in columns)})',
            tuple(payload.get(column) for column in columns),
        )

    @_needs_migration
    async def order_by_id(self, order_id: int,
                          include_deleted: bool = True) -> Optional[dict]:
        where = 'id = ?' if include_deleted else 'id = ? AND deleted_at IS NULL'
        rows = await self._rows(
            f'SELECT {_ORDER_COLUMNS} FROM purchase_orders WHERE {where}',
            (int(order_id),),
        )
        return _order_row(rows[0]) if rows else None

    @_needs_migration
    async def order_items(self, order_id: int) -> list[dict]:
        rows = await self._rows(
            f'SELECT {_ITEM_COLUMNS} FROM purchase_order_items'
            ' WHERE order_id = ? ORDER BY sort_order, id',
            (int(order_id),),
        )
        return [_item_row(row) for row in rows]

    @_needs_migration
    async def order_items_for(self, order_ids: Iterable[int]) -> dict[int, list[dict]]:
        """批量取多张单的商品行，给列表页做「单里有什么」预览。"""
        ids = [int(value) for value in order_ids]
        if not ids:
            return {}
        placeholders = ', '.join('?' for _ in ids)
        rows = await self._rows(
            'SELECT order_id, product_code, name, quantity, unit, stock_qty, sort_order'
            ' FROM purchase_order_items'
            f' WHERE order_id IN ({placeholders})'
            ' ORDER BY order_id, sort_order, id',
            tuple(ids),
        )
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            grouped.setdefault(int(row['order_id']), []).append(
                {
                    'product_code': row.get('product_code'),
                    'name': row.get('name'),
                    'quantity': _num(row.get('quantity')) or 0.0,
                    'unit': row.get('unit') or '',
                    'stock_qty': _num(row.get('stock_qty')),
                }
            )
        return grouped

    @_needs_migration
    async def delete_order_items(self, order_id: int) -> None:
        await self._conn.execute(
            'DELETE FROM purchase_order_items WHERE order_id = ?', (int(order_id),)
        )

    @_needs_migration
    async def update_order_status(self, order_id: int, fields: dict) -> None:
        allowed = {
            'status', 'completed_at', 'completed_by', 'cancelled_at', 'cancelled_by',
            'deleted_at', 'updated_at', 'updated_by',
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ', '.join(f'{key} = ?' for key in updates)
        await self._conn.execute(
            f'UPDATE purchase_orders SET {assignments} WHERE id = ?',
            tuple(updates.values()) + (int(order_id),),
        )

    @_needs_migration
    async def order_events(self, order_id: int) -> list[dict]:
        rows = await self._rows(
            'SELECT id, order_id, kind, actor_employee_id, actor_name, actor_kind,'
            ' detail_json, created_at FROM purchase_order_events'
            ' WHERE order_id = ? ORDER BY created_at, id',
            (int(order_id),),
        )
        for row in rows:
            if isinstance(row.get('created_at'), datetime):
                row['created_at'] = _iso_beijing(row['created_at'])
        return rows

    @_needs_migration
    async def insert_event(self, order_id: int, kind: str, actor: dict,
                           now: datetime, detail: Optional[dict] = None) -> None:
        import json

        await self._conn.execute(
            'INSERT INTO purchase_order_events'
            ' (order_id, kind, actor_employee_id, actor_name, actor_kind,'
            '  detail_json, created_at)'
            ' VALUES (?, ?, ?, ?, ?, ?::jsonb, ?)',
            (
                int(order_id), str(kind), actor.get('employee_id'), actor.get('name'),
                actor.get('kind'), json.dumps(detail or {}, ensure_ascii=False), now,
            ),
        )

    @_needs_migration
    async def orders(self, *, start: Any, end: Any, include_deleted: bool = False,
                     status: Optional[str] = None,
                     position_id: Optional[int] = None,
                     submitter_employee_id: Optional[int] = None) -> list[dict]:
        """按提交时间半开区间 [start, end) 取单，附行数与总数量。

        `start` / `end` 是 `date`：start 的北京零点到 end 的北京零点。
        """
        start_dt = datetime.combine(_as_date(start), datetime.min.time(), tzinfo=CHINA_TZ)
        end_dt = datetime.combine(_as_date(end), datetime.min.time(), tzinfo=CHINA_TZ)
        clauses = ['o.submitted_at >= ?', 'o.submitted_at < ?']
        params: list[Any] = [start_dt, end_dt]
        if not include_deleted:
            clauses.append('o.deleted_at IS NULL')
        if status:
            clauses.append('o.status = ?')
            params.append(str(status))
        if position_id is not None:
            clauses.append('o.position_id = ?')
            params.append(int(position_id))
        if submitter_employee_id is not None:
            clauses.append('o.submitter_employee_id = ?')
            params.append(int(submitter_employee_id))
        rows = await self._rows(
            'SELECT o.id, o.order_no, o.position_id, o.position_name,'
            ' o.submitter_employee_id, o.submitter_name, o.submitter_kind, o.status,'
            ' o.submitted_at, o.completed_at, o.completed_by, o.cancelled_at,'
            ' o.cancelled_by, o.deleted_at, o.updated_at, o.updated_by,'
            ' COUNT(i.id) AS item_count, COALESCE(SUM(i.quantity), 0) AS total_quantity'
            ' FROM purchase_orders o'
            ' LEFT JOIN purchase_order_items i ON i.order_id = o.id'
            f' WHERE {" AND ".join(clauses)}'
            ' GROUP BY o.id'
            ' ORDER BY o.submitted_at DESC, o.id DESC',
            tuple(params),
        )
        return [_order_row(row) for row in rows]

    @_needs_migration
    async def items_between(self, start: Any, end: Any,
                            include_cancelled: bool = False) -> list[dict]:
        start_dt = datetime.combine(_as_date(start), datetime.min.time(), tzinfo=CHINA_TZ)
        end_dt = datetime.combine(_as_date(end), datetime.min.time(), tzinfo=CHINA_TZ)
        clauses = ['o.submitted_at >= ?', 'o.submitted_at < ?', 'o.deleted_at IS NULL']
        if not include_cancelled:
            clauses.append("o.status <> 'cancelled'")
        rows = await self._rows(
            'SELECT i.id, i.order_id, i.product_id, i.product_code, i.name,'
            ' i.specification, i.unit, i.supplier_id, i.supplier_name, i.category_name,'
            ' i.position_name, i.quantity, i.reference_qty, i.stock_qty, i.suggested_qty,'
            ' i.order_step, i.sort_order, o.submitted_at, o.status, o.order_no,'
            ' o.submitter_name'
            ' FROM purchase_order_items i'
            ' JOIN purchase_orders o ON o.id = i.order_id'
            f' WHERE {" AND ".join(clauses)}'
            ' ORDER BY o.submitted_at, i.id',
            (start_dt, end_dt),
        )
        for row in rows:
            if isinstance(row.get('submitted_at'), datetime):
                row['submitted_at'] = _iso_beijing(row['submitted_at'])
            row['quantity'] = _num(row.get('quantity')) or 0.0
        return rows

    @_needs_migration
    async def product_last_ordered(self, product_ids: Iterable[int],
                                   before: Any) -> dict[int, dict]:
        ids = [int(value) for value in product_ids]
        if not ids:
            return {}
        end_dt = datetime.combine(_as_date(before) + timedelta(days=1),
                                  datetime.min.time(), tzinfo=CHINA_TZ)
        rows = await self._rows(
            'SELECT DISTINCT ON (i.product_id) i.product_id, i.quantity,'
            ' o.submitted_at, o.order_no'
            ' FROM purchase_order_items i'
            ' JOIN purchase_orders o ON o.id = i.order_id'
            ' WHERE i.product_id = ANY(?) AND o.submitted_at < ?'
            "  AND o.status <> 'cancelled' AND o.deleted_at IS NULL"
            ' ORDER BY i.product_id, o.submitted_at DESC, i.id DESC',
            (ids, end_dt),
        )
        result: dict[int, dict] = {}
        for row in rows:
            record = dict(row)
            if isinstance(record.get('submitted_at'), datetime):
                record['submitted_at'] = _iso_beijing(record['submitted_at'])
            result[int(record['product_id'])] = record
        return result

    @_needs_migration
    async def purchase_history(self, product_ids: Iterable[int], start: Any,
                               end: Any, include_cancelled: bool = False) -> list[dict]:
        """批量取历史采购（按提交日，一行一个商品一天汇总），参考值算 WMA 用。"""
        ids = [int(value) for value in product_ids]
        if not ids:
            return []
        start_dt = datetime.combine(_as_date(start), datetime.min.time(), tzinfo=CHINA_TZ)
        end_dt = datetime.combine(_as_date(end) + timedelta(days=1),
                                  datetime.min.time(), tzinfo=CHINA_TZ)
        clause = '' if include_cancelled else " AND o.status <> 'cancelled'"
        rows = await self._rows(
            'SELECT i.product_id,'
            " (o.submitted_at AT TIME ZONE 'Asia/Shanghai')::date AS purchase_date,"
            ' SUM(i.quantity) AS quantity,'
            " STRING_AGG(DISTINCT o.submitter_name, '、') AS submitter_names"
            ' FROM purchase_order_items i'
            ' JOIN purchase_orders o ON o.id = i.order_id'
            ' WHERE i.product_id = ANY(?) AND o.submitted_at >= ? AND o.submitted_at < ?'
            '  AND o.deleted_at IS NULL' + clause +
            " GROUP BY i.product_id, (o.submitted_at AT TIME ZONE 'Asia/Shanghai')::date"
            ' ORDER BY purchase_date',
            (ids, start_dt, end_dt),
        )
        for row in rows:
            row['quantity'] = _num(row.get('quantity')) or 0.0
            if isinstance(row.get('purchase_date'), date):
                row['purchase_date'] = row['purchase_date'].isoformat()
        return rows

    @_needs_migration
    async def stock_series(self, product_ids: Iterable[int], start: Any,
                           end: Any) -> list[dict]:
        """批量取盘点快照（参考值算逐日用量用）。"""
        ids = [int(value) for value in product_ids]
        if not ids:
            return []
        rows = await self._rows(
            f'SELECT {_STOCK_COLUMNS} FROM purchase_stock_counts'
            ' WHERE product_id = ANY(?) AND count_date >= ? AND count_date <= ?'
            ' ORDER BY count_date',
            (ids, _as_date(start), _as_date(end)),
        )
        return [_stock_row(row) for row in rows]

    @_needs_migration
    async def item_daily_series(self, product_ids: Iterable[int], start: Any,
                                end: Any) -> list[dict]:
        """批量取历史订单行（分析钻取的采购趋势用），按提交日、商品汇总。"""
        ids = [int(value) for value in product_ids]
        if not ids:
            return []
        start_dt = datetime.combine(_as_date(start), datetime.min.time(), tzinfo=CHINA_TZ)
        end_dt = datetime.combine(_as_date(end) + timedelta(days=1),
                                  datetime.min.time(), tzinfo=CHINA_TZ)
        rows = await self._rows(
            'SELECT i.product_id,'
            " (o.submitted_at AT TIME ZONE 'Asia/Shanghai')::date AS order_date,"
            ' SUM(i.quantity) AS quantity, COUNT(DISTINCT i.order_id) AS order_count'
            ' FROM purchase_order_items i'
            ' JOIN purchase_orders o ON o.id = i.order_id'
            ' WHERE i.product_id = ANY(?) AND o.submitted_at >= ? AND o.submitted_at < ?'
            "  AND o.status <> 'cancelled' AND o.deleted_at IS NULL"
            " GROUP BY i.product_id, (o.submitted_at AT TIME ZONE 'Asia/Shanghai')::date"
            ' ORDER BY order_date',
            (ids, start_dt, end_dt),
        )
        for row in rows:
            row['quantity'] = _num(row.get('quantity')) or 0.0
            row['order_count'] = int(row.get('order_count') or 0)
            if isinstance(row.get('order_date'), date):
                row['order_date'] = row['order_date'].isoformat()
        return rows

    # ── 汇总 / 分析用的聚合 ──────────────────────────────────

    @_needs_migration
    async def summary_items(self, count_date: Any,
                           position_ids: Optional[Sequence[int]] = None) -> list[dict]:
        """某提交日的有效订单行（取消 / 软删不算），按供应商、分类、商品聚合。"""
        start_dt = datetime.combine(_as_date(count_date), datetime.min.time(), tzinfo=CHINA_TZ)
        end_dt = start_dt + timedelta(days=1)
        params: list[Any] = [start_dt, end_dt]
        position_clause = ''
        ids = [int(value) for value in (position_ids or [])]
        if ids:
            position_clause = ' AND o.position_id IN (' + ', '.join('?' for _ in ids) + ')'
            params.extend(ids)
        rows = await self._rows(
            'SELECT i.product_id, i.product_code, i.name, i.specification, i.unit,'
            ' i.supplier_id, i.supplier_name, i.category_name, i.position_name,'
            " COALESCE(sc.code, CASE WHEN i.product_code ~ '^[0-9]{7}$'"
            '  THEN substring(i.product_code from 1 for 2) END) AS supplier_code,'
            " COALESCE(cc.code, CASE WHEN i.product_code ~ '^[0-9]{7}$'"
            '  THEN substring(i.product_code from 3 for 2) END) AS category_code,'
            ' SUM(i.quantity) AS quantity, COUNT(DISTINCT i.order_id) AS order_count,'
            ' MAX(i.stock_qty) AS stock_qty,'
            " STRING_AGG(DISTINCT o.submitter_name, '、') AS submitters"
            ' FROM purchase_order_items i'
            ' JOIN purchase_orders o ON o.id = i.order_id'
            ' LEFT JOIN purchase_products p ON p.id = i.product_id'
            ' LEFT JOIN purchase_categories cc ON cc.id = p.category_id'
            ' LEFT JOIN purchase_suppliers sc'
            '  ON sc.id = COALESCE(i.supplier_id, p.supplier_id)'
            ' WHERE o.submitted_at >= ? AND o.submitted_at < ?'
            "  AND o.status <> 'cancelled' AND o.deleted_at IS NULL"
            + position_clause
            + ' GROUP BY i.product_id, i.product_code, i.name, i.specification, i.unit,'
            '  i.supplier_id, i.supplier_name, i.category_name, i.position_name,'
            '  sc.code, cc.code'
            ' ORDER BY i.supplier_name, i.category_name, i.name',
            tuple(params),
        )
        for row in rows:
            row['quantity'] = _num(row.get('quantity')) or 0.0
            row['order_count'] = int(row.get('order_count') or 0)
        return rows

    @_needs_migration
    async def count_orders(self, start: Any, end: Any,
                           include_cancelled: bool = False,
                           position_ids: Optional[Sequence[int]] = None) -> int:
        start_dt = datetime.combine(_as_date(start), datetime.min.time(), tzinfo=CHINA_TZ)
        end_dt = datetime.combine(_as_date(end), datetime.min.time(), tzinfo=CHINA_TZ)
        clause = '' if include_cancelled else " AND status <> 'cancelled'"
        params: list[Any] = [start_dt, end_dt]
        ids = [int(value) for value in (position_ids or [])]
        if ids:
            clause += ' AND position_id IN (' + ', '.join('?' for _ in ids) + ')'
            params.extend(ids)
        rows = await self._rows(
            'SELECT COUNT(*) AS total FROM purchase_orders'
            ' WHERE submitted_at >= ? AND submitted_at < ? AND deleted_at IS NULL'
            + clause,
            tuple(params),
        )
        return int(rows[0]['total'] or 0)

    # ── 改码审计 ──────────────────────────────────────────────

    @_needs_migration
    async def insert_code_change(self, product_id: int, old: dict, new: dict,
                                 actor: dict, now: datetime) -> None:
        """记一行改码审计（商品 + 旧/新编码与供应商、分类快照）。

        D4 允许改码并改写历史订单行，所以这张表是唯一能还原「当时为什么变」的
        地方；它只追加，不更新、不删除。
        """
        await self._conn.execute(
            'INSERT INTO purchase_product_code_changes'
            ' (product_id, old_code, new_code, old_supplier_id, new_supplier_id,'
            '  old_supplier_name, new_supplier_name, old_category_id, new_category_id,'
            '  old_category_name, new_category_name, actor_employee_id, actor_name,'
            '  actor_kind, created_at)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                int(product_id), str(old.get('product_code') or ''),
                str(new.get('product_code') or ''),
                old.get('supplier_id'), new.get('supplier_id'),
                old.get('supplier_name'), new.get('supplier_name'),
                old.get('category_id'), new.get('category_id'),
                old.get('category_name'), new.get('category_name'),
                actor.get('employee_id'), actor.get('name'), actor.get('kind'), now,
            ),
        )
