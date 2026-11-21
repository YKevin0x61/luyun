#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""厨房采购域的业务层（spec: .scratch/worktool2-import/spec.md，ADR 0156 / 0157）。

三块功能共用这一层：

1. 下单：师傅选一个采购岗位（不允许「全部」），页面按岗位列商品、给参考值；
   提交时订单 + 行 + 当天的盘点快照 + 事件在一个写单元里落库。
2. 目录：供应商 / 分类 / 商品 / 岗位 / 日期标签的唯一维护入口。商品编码由
   供应商 2 位 + 分类 2 位 + 序号 3 位组成；改编码或换供应商 / 分类时同一事务里
   同步历史订单行、写改码审计（D4）。
3. 分析：概览、异常、商品钻取。口径按 spec §10（取消 / 软删不计；人数按员工
   ID；平均值分母按自然日）。

权限（D11）：
- 读下单页 / 汇总 / 历史：管理端，或员工会话且花名册勾了「厨房采购」。
- 改单、状态变更、目录维护、分析：只认管理端。
- 取消待处理单：提交者本人或管理端。

错误统一 PurchaseError(code, message)；HTTP 映射在 api/purchase.py。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

import asyncpg

from db_core.write_gate import write_unit
from services.identity.capabilities import CAP_PURCHASE, has_cap
from services.purchase.forecast import (
    DEFAULT_SAFETY_FACTOR,
    ProductReference,
    daily_usage_series,
    reference_for_product,
    suggested_quantity,
)
from services.purchase.store import PurchaseError, PurchaseStore, TAG_VALUES

logger = logging.getLogger(__name__)

__all__ = [
    "PurchaseActor",
    "PurchaseService",
    "PurchaseError",
    "purchase_service",
]

PENDING = "pending"
COMPLETED = "completed"
CANCELLED = "cancelled"

UNITS = (
    "斤", "公斤", "只", "个", "根", "盒", "包", "瓶", "袋", "把",
    "条", "块", "件", "桶", "提", "片", "箱",
)

MAX_ITEMS = 500
MAX_QUANTITY = 999999.0
MAX_NAME = 60
MAX_SPEC = 60
MAX_UNIT = 10
MAX_REMARK = 300

_CODE_RE = re.compile(r"^[0-9]{7}$")
_SUPPLIER_CODE_RE = re.compile(r"^[0-9]{2}$")


@dataclass(frozen=True)
class PurchaseActor:
    """一次请求的操作者；由 API 层按当前会话构造，权限每次现读。"""

    kind: str  # "admin" | "staff"
    employee_id: Optional[int] = None
    name: str = ""
    caps: frozenset[str] = frozenset()

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"

    @property
    def can_order(self) -> bool:
        return self.is_admin or (
            self.kind == "staff" and has_cap(self.caps, CAP_PURCHASE)
        )

    @property
    def display_name(self) -> str:
        return self.name or ("管理端" if self.is_admin else "")


def _forbidden(message: str) -> PurchaseError:
    return PurchaseError("forbidden", message)


def _clean_text(raw: Any, label: str, *, max_length: int, required: bool = False) -> str:
    text = str(raw or "").strip()
    if required and not text:
        raise PurchaseError("bad_request", label + "不能为空")
    if len(text) > max_length:
        raise PurchaseError("bad_request", label + "不能超过 " + str(max_length) + " 个字")
    return text


def _clean_quantity(raw: Any, label: str) -> float:
    if raw is None or raw == "":
        raise PurchaseError("bad_request", label + "没有填数量")
    try:
        value = round(float(raw), 2)
    except (TypeError, ValueError) as exc:
        raise PurchaseError("bad_request", label + "必须是数字") from exc
    if value <= 0:
        raise PurchaseError("bad_request", label + "必须大于 0")
    if value > MAX_QUANTITY:
        raise PurchaseError("bad_request", label + "不能超过 " + str(int(MAX_QUANTITY)))
    return value


def _clean_stock(raw: Any, label: str) -> float:
    if raw is None or raw == "":
        return 0.0
    try:
        value = round(float(raw), 2)
    except (TypeError, ValueError) as exc:
        raise PurchaseError("bad_request", label + "必须是数字") from exc
    if value < 0:
        raise PurchaseError("bad_request", label + "不能是负数")
    if value > MAX_QUANTITY:
        raise PurchaseError("bad_request", label + "不能超过 " + str(int(MAX_QUANTITY)))
    return value


def _clean_bool(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _clean_positive(raw: Any, label: str, default: float) -> float:
    if raw is None or raw == "":
        return float(default)
    try:
        value = round(float(raw), 2)
    except (TypeError, ValueError) as exc:
        raise PurchaseError("bad_request", label + "必须是数字") from exc
    if value <= 0:
        raise PurchaseError("bad_request", label + "必须大于 0")
    return value


def _parse_date(raw: Any, default: Optional[date] = None) -> date:
    if raw is None or raw == "":
        if default is None:
            raise PurchaseError("bad_date", "缺少日期")
        return default
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise PurchaseError("bad_date", "日期格式不对：" + str(raw)) from exc


def _int_or_none(raw: Any) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _datetime_of(row: Mapping[str, Any], key: str) -> datetime:
    value = row.get(key)
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def _date_of(row: Mapping[str, Any], key: str) -> date:
    value = row.get(key)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    try:
        return date.fromisoformat(text)
    except ValueError:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()


def _reference_json(product: Mapping[str, Any], ref: ProductReference,
                    stock: Optional[float],
                    yesterday: Optional[float] = None) -> dict:
    suggested = suggested_quantity(
        ref.reference,
        safety_factor=float(product.get("safety_factor") or DEFAULT_SAFETY_FACTOR),
        current_stock=stock or 0.0,
        order_step=float(product.get("order_step") or 1.0),
    )
    return {
        "product_id": int(product["id"]),
        "reference": ref.reference,
        "reason": ref.reason,
        "target_tag": ref.target_tag,
        "base_wma": ref.base_wma,
        "same_tag_value": ref.same_tag_value,
        "same_tag_samples": ref.same_tag_samples,
        "same_tag_nonzero": ref.same_tag_nonzero,
        "recent3_avg": ref.recent3_avg,
        "cv": ref.cv,
        "is_long_cycle": ref.is_long_cycle,
        "usage_days": ref.usage_days,
        "purchase_days": ref.purchase_days,
        "current_stock": stock,
        "yesterday_quantity": yesterday,
        "suggested_quantity": suggested,
    }


def _group_summary(rows: Sequence[Mapping[str, Any]]) -> dict:
    """把按商品 + 岗位聚合的订单行折成 供应商 → 分类 → 商品 → 岗位 的树。"""
    suppliers: dict[str, dict] = {}
    positions: dict[str, dict] = {}
    total_quantity = 0.0
    for row in rows:
        quantity = float(row.get("quantity") or 0.0)
        total_quantity += quantity
        supplier_name = str(row.get("supplier_name") or "未分供应商")
        category_name = str(row.get("category_name") or "未分类")
        # 历史导入的订单可能没有岗位；商品层与顶层都用同一个兜底名，别一边空串
        # 一边「未分岗位」，小程序就得多写一套判空。
        position_name = str(row.get("position_name") or "") or "未分岗位"
        product_key = int(row.get("product_id") or 0) or str(row.get("product_code") or "")
        supplier = suppliers.setdefault(
            supplier_name,
            {"supplier_name": supplier_name, "quantity": 0.0, "categories": {}},
        )
        supplier["quantity"] += quantity
        category = supplier["categories"].setdefault(
            category_name, {"category_name": category_name, "quantity": 0.0, "products": {}}
        )
        category["quantity"] += quantity
        product = category["products"].setdefault(
            product_key,
            {
                "product_id": row.get("product_id"),
                "product_code": row.get("product_code"),
                "name": row.get("name"),
                "specification": row.get("specification") or "",
                "unit": row.get("unit") or "",
                "quantity": 0.0,
                "stock_qty": row.get("stock_qty"),
                "submitters": [],
                "positions": [],
            },
        )
        product["quantity"] += quantity
        for name in str(row.get("submitters") or "").split("、"):
            clean = name.strip()
            if clean and clean not in product["submitters"]:
                product["submitters"].append(clean)
        product["positions"].append(
            {
                "position_name": position_name,
                "quantity": quantity,
                "order_count": int(row.get("order_count") or 0),
            }
        )
        position = positions.setdefault(
            position_name,
            {"position_name": position_name, "quantity": 0.0},
        )
        position["quantity"] += quantity

    def _product_list(category: dict) -> list[dict]:
        products = sorted(
            category["products"].values(),
            key=lambda item: str(item.get("product_code") or ""),
        )
        for product in products:
            product["quantity"] = round(product["quantity"], 2)
            product["positions"] = sorted(
                product["positions"], key=lambda item: item["position_name"]
            )
            product["submitters"] = sorted(product["submitters"])
        return products

    rendered = []
    for supplier_name in sorted(suppliers):
        supplier = suppliers[supplier_name]
        categories = []
        for category_name in sorted(supplier["categories"]):
            category = supplier["categories"][category_name]
            categories.append(
                {
                    "category_name": category_name,
                    "quantity": round(category["quantity"], 2),
                    "products": _product_list(category),
                }
            )
        rendered.append(
            {
                "supplier_name": supplier_name,
                "quantity": round(supplier["quantity"], 2),
                "categories": categories,
            }
        )
    return {
        "suppliers": rendered,
        "positions": sorted(positions.values(), key=lambda item: item["position_name"]),
        "total_quantity": round(total_quantity, 2),
    }


class PurchaseService:
    """无状态服务：每次调用传 db，store 在方法内构造。"""

    def _store(self, db) -> PurchaseStore:
        return PurchaseStore(db)

    # ── 权限 ──────────────────────────────────────────────────

    def _require_reader(self, actor: PurchaseActor) -> None:
        if not actor.can_order:
            raise _forbidden(
                "只有开了「厨房采购」权限的员工或管理端能看采购单：请找超级管理员在花名册里开通"
            )

    def _require_admin(self, actor: PurchaseActor) -> None:
        if not actor.is_admin:
            raise _forbidden("只有管理端能维护采购目录、改单或看采购分析")

    def ensure_admin(self, actor: PurchaseActor) -> None:
        """导出这类「先起后台任务、再慢慢算」的入口，要在起任务前就把 403 判掉。"""
        self._require_admin(actor)

    def _can_cancel(self, actor: PurchaseActor, order: Mapping[str, Any]) -> bool:
        if str(order.get("status")) != PENDING or order.get("deleted_at"):
            return False
        if actor.is_admin:
            return True
        submitter = order.get("submitter_employee_id")
        return (
            actor.kind == "staff"
            and actor.employee_id is not None
            and submitter is not None
            and int(submitter) == int(actor.employee_id)
        )

    # ── 参考值 ────────────────────────────────────────────────

    async def _reference_map(self, store: PurchaseStore,
                             products: Sequence[Mapping[str, Any]],
                             day: date) -> dict[int, ProductReference]:
        ids = [int(product["id"]) for product in products]
        if not ids:
            return {}
        start = day - timedelta(days=62)
        purchases = await store.purchase_history(ids, start, day)
        stocks = await store.stock_series(ids, start, day)
        tags = await store.all_date_tags(day - timedelta(days=61), day + timedelta(days=1))
        weekdays = await store.weekday_defaults()
        purchase_by_product: dict[int, list[tuple[date, float]]] = {}
        for row in purchases:
            purchase_by_product.setdefault(int(row["product_id"]), []).append(
                (_date_of(row, "purchase_date"), float(row["quantity"]))
            )
        stock_by_product: dict[int, dict[date, float]] = {}
        for row in stocks:
            stock_by_product.setdefault(int(row["product_id"]), {})[
                _date_of(row, "count_date")
            ] = float(row["current_stock"])
        result: dict[int, ProductReference] = {}
        for product in products:
            product_id = int(product["id"])
            result[product_id] = reference_for_product(
                today=day,
                purchases=purchase_by_product.get(product_id, []),
                stocks=stock_by_product.get(product_id, {}),
                date_tags=tags,
                weekday_defaults=weekdays,
                is_long_cycle=bool(product.get("is_long_cycle")),
            )
        return result

    async def _reference_rows(self, store: PurchaseStore,
                              products: Sequence[Mapping[str, Any]],
                              day: date) -> list[dict]:
        ids = [int(product["id"]) for product in products]
        refs = await self._reference_map(store, products, day)
        counts = await store.stock_counts(day, ids)
        yesterday_rows = await store.purchase_history(
            ids, day - timedelta(days=1), day - timedelta(days=1)
        )
        yesterday: dict[int, float] = {}
        for row in yesterday_rows:
            product_id = int(row["product_id"])
            yesterday[product_id] = yesterday.get(product_id, 0.0) + float(
                row.get("quantity") or 0.0
            )
        rows = []
        for product in products:
            product_id = int(product["id"])
            stock = counts.get(product_id)
            current = float(stock["current_stock"]) if stock else None
            rows.append(
                {
                    **product,
                    **_reference_json(
                        product,
                        refs[product_id],
                        current,
                        round(yesterday.get(product_id, 0.0), 2),
                    ),
                }
            )
        return rows

    # ── 读：下单页 / 目录 ─────────────────────────────────────

    async def meta(self, db, actor: PurchaseActor) -> dict:
        self._require_reader(actor)
        store = self._store(db)
        return {
            "positions": await store.positions(include_inactive=actor.is_admin),
            "suppliers": await store.suppliers(include_inactive=actor.is_admin),
            "categories": await store.categories(include_inactive=actor.is_admin),
            "units": list(UNITS),
            "tags": list(TAG_VALUES),
            "weekday_defaults": await store.weekday_defaults(),
        }

    async def date_tags(self, db, actor: PurchaseActor, *, start: Any = None,
                        end: Any = None) -> dict:
        """分析 / 目录页要的日期标签与星期默认（只认管理端）。"""
        self._require_admin(actor)
        store = self._store(db)
        today = store.today()
        start_day = _parse_date(start, today - timedelta(days=60))
        end_day = _parse_date(end, today + timedelta(days=60))
        if end_day < start_day:
            raise PurchaseError("bad_date", "结束日期不能早于开始日期")
        return {
            "date_tags": await store.date_tags(start_day, end_day),
            "weekday_defaults": await store.weekday_defaults(),
        }

    async def products(self, db, actor: PurchaseActor, *, position_id: Optional[int] = None,
                       q: str = "", include_inactive: bool = False) -> dict:
        self._require_reader(actor)
        store = self._store(db)
        rows = await store.products(
            position_id=position_id,
            active_only=not (actor.is_admin and include_inactive),
        )
        keyword = str(q or "").strip().lower()
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("name") or "").lower()
                or keyword in str(row.get("product_code") or "").lower()
                or keyword in str(row.get("supplier_name") or "").lower()
            ]
        return {"products": rows}

    async def reference(self, db, actor: PurchaseActor, *,
                        position_id: Optional[int] = None, day: Any = None) -> dict:
        self._require_reader(actor)
        store = self._store(db)
        target_day = _parse_date(day, store.today())
        position = None
        if position_id is not None:
            position = await store.position_by_id(int(position_id))
            if position is None or not position.get("active"):
                raise PurchaseError("bad_position", "这个采购岗位不存在或已停用")
        # 不传岗位 = 小程序里的「全部」浏览视图；下单提交仍然必须带岗位。
        rows = await store.products(
            position_id=int(position_id) if position_id is not None else None,
            active_only=True,
        )
        enriched = await self._reference_rows(store, rows, target_day)
        return {"date": target_day.isoformat(), "position": position, "products": enriched}

    async def stock(self, db, actor: PurchaseActor, *, day: Any = None,
                    position_id: Optional[int] = None) -> dict:
        self._require_reader(actor)
        store = self._store(db)
        target_day = _parse_date(day, store.today())
        product_ids: list[int] = []
        if position_id is not None:
            product_ids = [
                int(row["id"])
                for row in await store.products(position_id=int(position_id), active_only=True)
            ]
        if position_id is not None and not product_ids:
            counts = {}
        else:
            counts = await store.stock_counts(target_day, product_ids or None)
        return {"date": target_day.isoformat(), "counts": list(counts.values())}

    async def set_stock(self, db, actor: PurchaseActor, payload: Mapping[str, Any]) -> dict:
        """分析页手工修正某商品某天的盘点（只认管理端，单条覆盖）。"""
        self._require_admin(actor)
        store = self._store(db)
        product_id = int(payload.get("product_id") or 0)
        product = await store.product_by_id(product_id)
        if product is None:
            raise PurchaseError("product_not_found", "商品 " + str(product_id) + " 不存在")
        target_day = _parse_date(payload.get("count_date"), store.today())
        if target_day > store.today():
            raise PurchaseError("bad_date", "不能给未来的日期记盘点")
        try:
            current = float(payload.get("current_stock"))
        except (TypeError, ValueError) as exc:
            raise PurchaseError("bad_request", "库存要填一个不小于 0 的数字") from exc
        if current < 0:
            raise PurchaseError("bad_request", "库存不能小于 0")
        now = store._now_dt()
        async with write_unit(db):
            await store.upsert_stock(
                product_id,
                target_day,
                current,
                {
                    "employee_id": actor.employee_id,
                    "name": actor.display_name,
                    "kind": actor.kind,
                },
                now,
            )
        return {
            "product_id": product_id,
            "count_date": target_day.isoformat(),
            "current_stock": round(current, 2),
            "counted_by": actor.display_name,
        }

    # ── 订单 ──────────────────────────────────────────────────

    async def list_orders(self, db, actor: PurchaseActor, *, day_from: Any = None,
                          day_to: Any = None, status: Optional[str] = None,
                          position_id: Optional[int] = None,
                          page: int = 1, page_size: int = 50) -> dict:
        self._require_reader(actor)
        store = self._store(db)
        start = _parse_date(day_from, store.today())
        end = _parse_date(day_to, start)
        if end < start:
            raise PurchaseError("bad_date", "结束日期不能早于开始日期")
        # 2026-11-22 用户裁定：勾了 purchase 的员工也能看全店的采购单（不再按提交人过滤）；
        # 取消仍只限本人或管理端，由 _can_cancel 单独判。
        rows = await store.orders(
            start=start,
            end=end + timedelta(days=1),
            status=status or None,
            position_id=int(position_id) if position_id else None,
        )
        total = len(rows)
        page = max(1, int(page or 1))
        page_size = min(200, max(1, int(page_size or 50)))
        offset = (page - 1) * page_size
        page_rows = rows[offset:offset + page_size]
        items_by_order = await store.order_items_for([row["id"] for row in page_rows])
        for row in page_rows:
            row["items"] = items_by_order.get(int(row["id"]), [])
        return {
            "orders": page_rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        }

    async def order_detail(self, db, actor: PurchaseActor, order_id: int) -> dict:
        self._require_reader(actor)
        store = self._store(db)
        order = await store.order_by_id(int(order_id))
        if order is None or order.get("deleted_at"):
            raise PurchaseError("order_not_found", "采购单 " + str(order_id) + " 不存在")
        # 能进到这里说明 _require_reader 已放行：管理端与勾了 purchase 的员工都能看全店
        # 单据；能不能取消由下面的 can_cancel 按提交人单独判。
        items = await store.order_items(int(order_id))
        events = await store.order_events(int(order_id))
        return {
            "order": {
                **order,
                "can_edit": actor.is_admin and str(order.get("status")) == PENDING,
                "can_complete": actor.is_admin and str(order.get("status")) == PENDING,
                "can_cancel": self._can_cancel(actor, order),
                "can_delete": actor.is_admin and not order.get("deleted_at"),
            },
            "items": items,
            "events": events,
        }

    async def submit_order(self, db, actor: PurchaseActor, payload: Mapping[str, Any]) -> dict:
        self._require_reader(actor)
        store = self._store(db)
        position_id = _int_or_none(payload.get("position_id"))
        if not position_id:
            raise PurchaseError("bad_position", "提交前必须选一个采购岗位（不能选「全部」）")
        position = await store.position_by_id(int(position_id))
        if position is None or not position.get("active"):
            raise PurchaseError("bad_position", "这个采购岗位不存在或已停用")

        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise PurchaseError("bad_items", "采购单里没有商品：至少填一项数量")
        if len(raw_items) > MAX_ITEMS:
            raise PurchaseError("bad_items", "一张单最多 " + str(MAX_ITEMS) + " 项")

        products = await store.products(position_id=int(position_id), active_only=True)
        product_by_id = {int(product["id"]): product for product in products}
        items: list[dict] = []
        seen: set[int] = set()
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                raise PurchaseError("bad_items", "第 " + str(index + 1) + " 项格式不对")
            product_id = _int_or_none(raw.get("product_id"))
            if not product_id or product_id not in product_by_id:
                raise PurchaseError("bad_items", "第 " + str(index + 1) + " 项不是这个岗位的商品")
            if product_id in seen:
                raise PurchaseError("bad_items", "第 " + str(index + 1) + " 项商品重复")
            seen.add(product_id)
            quantity = _clean_quantity(raw.get("quantity"), "第 " + str(index + 1) + " 项数量")
            items.append(
                {
                    "product": product_by_id[product_id],
                    "product_id": product_id,
                    "quantity": quantity,
                    "sort_order": len(items),
                }
            )

        stock_payload: dict[int, Any] = {}
        raw_stock = payload.get("stock")
        if isinstance(raw_stock, dict):
            for key, value in raw_stock.items():
                product_id = _int_or_none(key)
                if product_id:
                    stock_payload[product_id] = value
        elif isinstance(raw_stock, list):
            for entry in raw_stock:
                if not isinstance(entry, dict):
                    continue
                product_id = _int_or_none(entry.get("product_id"))
                if product_id:
                    stock_payload[product_id] = entry.get("current_stock")
        else:
            raw_stocks = payload.get("stocks")
            if isinstance(raw_stocks, dict):
                for key, value in raw_stocks.items():
                    product_id = _int_or_none(key)
                    if product_id:
                        stock_payload[product_id] = value

        today = store.today()
        refs = await self._reference_map(store, list(product_by_id.values()), today)
        now = store._now_dt()

        async with write_unit(db):
            order_no = await store.next_order_no(today)
            order_id = await store.insert_order(
                {
                    "order_no": order_no,
                    "position_id": int(position_id),
                    "position_name": str(position.get("name") or ""),
                    "submitter_employee_id": actor.employee_id,
                    "submitter_name": actor.display_name,
                    "submitter_kind": actor.kind,
                    "status": PENDING,
                    "submitted_at": now,
                    "updated_at": now,
                    "updated_by": actor.display_name,
                }
            )
            for item in items:
                product = item["product"]
                product_id = int(product["id"])
                ref = refs[product_id]
                stock = _clean_stock(
                    stock_payload.get(product_id),
                    "「" + str(product["name"]) + "」盘点",
                ) if product.get("need_stock_check") else None
                suggested = suggested_quantity(
                    ref.reference,
                    safety_factor=float(product.get("safety_factor") or DEFAULT_SAFETY_FACTOR),
                    current_stock=stock or 0.0,
                    order_step=float(product.get("order_step") or 1.0),
                )
                await store.insert_item(
                    order_id,
                    {
                        "product_id": product_id,
                        "product_code": str(product.get("product_code") or ""),
                        "name": str(product.get("name") or ""),
                        "specification": str(product.get("specification") or ""),
                        "unit": str(product.get("unit") or ""),
                        "supplier_id": product.get("supplier_id"),
                        "supplier_name": str(product.get("supplier_name") or ""),
                        "category_name": str(product.get("category_name") or ""),
                        "position_name": str(position.get("name") or ""),
                        "quantity": item["quantity"],
                        "reference_qty": ref.reference,
                        "stock_qty": stock,
                        "suggested_qty": suggested,
                        "order_step": float(product.get("order_step") or 1.0),
                        "sort_order": item["sort_order"],
                    },
                )
            counted = 0
            for product in products:
                if not product.get("need_stock_check"):
                    continue
                product_id = int(product["id"])
                stock = _clean_stock(
                    stock_payload.get(product_id),
                    "「" + str(product["name"]) + "」盘点",
                )
                await store.upsert_stock(
                    product_id,
                    today,
                    stock,
                    {
                        "employee_id": actor.employee_id,
                        "name": actor.display_name,
                        "kind": actor.kind,
                    },
                    now,
                    source_order_id=order_id,
                )
                counted += 1
            await store.insert_event(
                order_id,
                "submitted",
                {"employee_id": actor.employee_id, "name": actor.display_name, "kind": actor.kind},
                now,
                {"items": len(items), "stock_counted": counted, "position": position.get("name")},
            )
        logger.info(
            "厨房采购单 %s 提交：岗位 %s，%s 项，操作者 %s",
            order_no, position.get("name"), len(items), actor.display_name,
        )
        return await self.order_detail(db, actor, order_id)

    async def update_items(self, db, actor: PurchaseActor, order_id: int,
                           payload: Mapping[str, Any]) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        order = await store.order_by_id(int(order_id))
        if order is None or order.get("deleted_at"):
            raise PurchaseError("order_not_found", "采购单 " + str(order_id) + " 不存在")
        if str(order.get("status")) != PENDING:
            raise PurchaseError("not_pending", "只有待处理单能改；已完成或已取消的单不能改")
        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise PurchaseError("bad_items", "采购单里没有商品：至少保留一项")
        if len(raw_items) > MAX_ITEMS:
            raise PurchaseError("bad_items", "一张单最多 " + str(MAX_ITEMS) + " 项")

        old_items = await store.order_items(int(order_id))
        old_by_product = {
            int(item["product_id"]): item for item in old_items if item.get("product_id")
        }
        order_products = await store.products(
            position_id=int(order["position_id"]), active_only=True
        )
        product_by_id = {int(product["id"]): product for product in order_products}
        cleaned: list[dict] = []
        seen: set[int] = set()
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                raise PurchaseError("bad_items", "第 " + str(index + 1) + " 项格式不对")
            product_id = _int_or_none(raw.get("product_id"))
            if not product_id or product_id not in product_by_id:
                raise PurchaseError(
                    "bad_items", "第 " + str(index + 1) + " 项不是这张单岗位下的商品"
                )
            if product_id in seen:
                raise PurchaseError("bad_items", "第 " + str(index + 1) + " 项商品重复")
            seen.add(product_id)
            quantity = _clean_quantity(raw.get("quantity"), "第 " + str(index + 1) + " 项数量")
            cleaned.append(
                {
                    "product": product_by_id[product_id],
                    "product_id": product_id,
                    "quantity": quantity,
                    "sort_order": len(cleaned),
                    "old": old_by_product.get(product_id),
                }
            )
        submitted_day = _date_of(order, "submitted_at")
        refs = await self._reference_map(
            store, [item["product"] for item in cleaned], submitted_day
        )
        now = store._now_dt()
        async with write_unit(db):
            await store.delete_order_items(int(order_id))
            for item in cleaned:
                product = item["product"]
                old = item.get("old") or {}
                ref = refs[int(product["id"])]
                reference_qty = old.get("reference_qty")
                stock_qty = old.get("stock_qty")
                suggested_qty = old.get("suggested_qty")
                if old.get("product_id") is None:
                    reference_qty = ref.reference
                    suggested_qty = suggested_quantity(
                        ref.reference,
                        safety_factor=float(
                            product.get("safety_factor") or DEFAULT_SAFETY_FACTOR
                        ),
                        current_stock=0.0,
                        order_step=float(product.get("order_step") or 1.0),
                    )
                await store.insert_item(
                    int(order_id),
                    {
                        "product_id": int(product["id"]),
                        "product_code": str(product.get("product_code") or ""),
                        "name": str(product.get("name") or ""),
                        "specification": str(product.get("specification") or ""),
                        "unit": str(product.get("unit") or ""),
                        "supplier_id": product.get("supplier_id"),
                        "supplier_name": str(product.get("supplier_name") or ""),
                        "category_name": str(product.get("category_name") or ""),
                        "position_name": str(order.get("position_name") or ""),
                        "quantity": item["quantity"],
                        "reference_qty": reference_qty,
                        "stock_qty": stock_qty,
                        "suggested_qty": suggested_qty,
                        "order_step": float(product.get("order_step") or 1.0),
                        "sort_order": item["sort_order"],
                    },
                )
            await store.update_order_status(
                int(order_id), {"updated_at": now, "updated_by": actor.display_name}
            )
            await store.insert_event(
                int(order_id), "items_updated",
                {"employee_id": actor.employee_id, "name": actor.display_name, "kind": actor.kind},
                now, {"items": len(cleaned)},
            )
        return await self.order_detail(db, actor, int(order_id))

    async def complete_order(self, db, actor: PurchaseActor, order_id: int) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        order = await store.order_by_id(int(order_id))
        if order is None or order.get("deleted_at"):
            raise PurchaseError("order_not_found", "采购单 " + str(order_id) + " 不存在")
        if str(order.get("status")) != PENDING:
            raise PurchaseError("not_pending", "只有待处理单能标记完成")
        now = store._now_dt()
        async with write_unit(db):
            await store.update_order_status(
                int(order_id),
                {
                    "status": COMPLETED,
                    "completed_at": now,
                    "completed_by": actor.display_name,
                    "updated_at": now,
                    "updated_by": actor.display_name,
                },
            )
            await store.insert_event(
                int(order_id), "completed",
                {"employee_id": actor.employee_id, "name": actor.display_name, "kind": actor.kind},
                now,
            )
        return await self.order_detail(db, actor, int(order_id))

    async def cancel_order(self, db, actor: PurchaseActor, order_id: int,
                           payload: Optional[Mapping[str, Any]] = None) -> dict:
        self._require_reader(actor)
        store = self._store(db)
        order = await store.order_by_id(int(order_id))
        if order is None or order.get("deleted_at"):
            raise PurchaseError("order_not_found", "采购单 " + str(order_id) + " 不存在")
        if str(order.get("status")) != PENDING:
            raise PurchaseError("not_pending", "只有待处理单能取消")
        if not self._can_cancel(actor, order):
            raise _forbidden("只有提交者本人或管理端能取消这张单")
        now = store._now_dt()
        reason = _clean_text((payload or {}).get("reason"), "取消原因", max_length=200)
        async with write_unit(db):
            await store.update_order_status(
                int(order_id),
                {
                    "status": CANCELLED,
                    "cancelled_at": now,
                    "cancelled_by": actor.display_name,
                    "updated_at": now,
                    "updated_by": actor.display_name,
                },
            )
            await store.insert_event(
                int(order_id), "cancelled",
                {"employee_id": actor.employee_id, "name": actor.display_name, "kind": actor.kind},
                now, {"reason": reason} if reason else None,
            )
        return await self.order_detail(db, actor, int(order_id))

    async def delete_order(self, db, actor: PurchaseActor, order_id: int) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        order = await store.order_by_id(int(order_id))
        if order is None or order.get("deleted_at"):
            raise PurchaseError("order_not_found", "采购单 " + str(order_id) + " 不存在")
        now = store._now_dt()
        async with write_unit(db):
            await store.update_order_status(
                int(order_id),
                {"deleted_at": now, "updated_at": now, "updated_by": actor.display_name},
            )
            await store.insert_event(
                int(order_id), "deleted",
                {"employee_id": actor.employee_id, "name": actor.display_name, "kind": actor.kind},
                now,
            )
        return {"ok": True, "order_id": int(order_id)}

    # ── 汇总 ──────────────────────────────────────────────────

    async def summary(self, db, actor: PurchaseActor, *, day: Any = None,
                      position_ids: Optional[Sequence[int]] = None) -> dict:
        self._require_reader(actor)
        return await self._summary(db, day=day, position_ids=position_ids)

    async def public_summary(self, db, *, day: Any = None,
                             position_ids: Optional[Sequence[int]] = None) -> dict:
        """对外公开的今日汇总：不算身份，只算聚合。

        调用方是 `GET /api/purchase/public/summary`（小程序免登录读）。口径与
        `summary` 共用同一个实现（未取消、未软删、按提交自然日），两条路径的
        数字天然一致；**不要**在这里加权限判断或写操作。
        """
        return await self._summary(db, day=day, position_ids=position_ids)

    async def _summary(self, db, *, day: Any = None,
                       position_ids: Optional[Sequence[int]] = None) -> dict:
        store = self._store(db)
        target_day = _parse_date(day, store.today())
        ids = sorted({int(value) for value in (position_ids or [])})
        rows = await store.summary_items(target_day, ids or None)
        orders = await store.count_orders(
            target_day, target_day + timedelta(days=1), position_ids=ids or None
        )
        grouped = _group_summary(rows)
        return {
            "date": target_day.isoformat(),
            "order_count": orders,
            "item_count": len(rows),
            "position_ids": ids,
            **grouped,
        }

    # ── 分析 ──────────────────────────────────────────────────

    async def analysis_overview(self, db, actor: PurchaseActor, *, day: Any = None) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        target_day = _parse_date(day, store.today())
        start = target_day - timedelta(days=6)
        orders = await store.orders(start=start, end=target_day + timedelta(days=1))
        valid = [order for order in orders if str(order.get("status")) != CANCELLED]
        items = await store.items_between(start, target_day + timedelta(days=1))
        rows_by_day: dict[str, dict] = {}
        for offset in range(7):
            day_key = (start + timedelta(days=offset)).isoformat()
            rows_by_day[day_key] = {"date": day_key, "order_count": 0, "quantity": 0.0}
        for order in valid:
            day_key = _date_of(order, "submitted_at").isoformat()
            if day_key in rows_by_day:
                rows_by_day[day_key]["order_count"] += 1
        for item in items:
            if str(item.get("status")) == CANCELLED:
                continue
            day_key = _date_of(item, "submitted_at").isoformat()
            if day_key in rows_by_day:
                rows_by_day[day_key]["quantity"] += float(item.get("quantity") or 0.0)
        yesterday = target_day - timedelta(days=1)
        yesterday_orders = [
            order for order in await store.orders(
                start=yesterday, end=target_day + timedelta(days=1)
            )
            if str(order.get("status")) != CANCELLED
            and _date_of(order, "submitted_at") == yesterday
        ]
        today_now = store.today()
        if target_day == today_now:
            cutoff = store._now_dt().timetz()
            compare_orders = [
                order for order in yesterday_orders
                if _datetime_of(order, "submitted_at").timetz() <= cutoff
            ]
            compare_mode = "same_time"
        else:
            compare_orders = yesterday_orders
            compare_mode = "full_day"
        today_items = [
            item for item in items if _date_of(item, "submitted_at") == target_day
        ]
        item_count = round(sum(float(item.get("quantity") or 0.0) for item in today_items), 2)
        people = {
            (order.get("submitter_employee_id") or "name:" + str(order.get("submitter_name")))
            for order in valid
            if _date_of(order, "submitted_at") == target_day
        }
        return {
            "date": target_day.isoformat(),
            "order_count": sum(
                1 for order in valid if _date_of(order, "submitted_at") == target_day
            ),
            "item_count": item_count,
            "sku_count": len(
                {int(item["product_id"]) for item in today_items if item.get("product_id")}
            ),
            "submitter_count": len(people),
            "yesterday_order_count": len(compare_orders),
            "yesterday_total_order_count": len(yesterday_orders),
            "compare_mode": compare_mode,
            "trend": [
                {
                    **rows_by_day[(start + timedelta(days=i)).isoformat()],
                    "quantity": round(
                        rows_by_day[(start + timedelta(days=i)).isoformat()]["quantity"], 2
                    ),
                }
                for i in range(7)
            ],
            "window": {"start": start.isoformat(), "end": target_day.isoformat()},
        }

    async def analysis_anomalies(self, db, actor: PurchaseActor, *, day: Any = None,
                                 no_order_days: int = 3, over_ratio: float = 1.5,
                                 under_ratio: float = 0.5,
                                 history_days: int = 21) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        target_day = _parse_date(day, store.today())
        no_order_days = max(1, int(no_order_days or 3))
        history_days = min(120, max(7, int(history_days or 21)))
        over_ratio = float(over_ratio or 1.5)
        under_ratio = float(under_ratio or 0.5)
        products = await store.products(active_only=True)
        refs = await self._reference_map(store, products, target_day)
        today_items = await store.items_between(target_day, target_day + timedelta(days=1))
        quantities: dict[int, float] = {}
        today_submitters: dict[int, set[str]] = {}
        for item in today_items:
            if str(item.get("status")) == CANCELLED or not item.get("product_id"):
                continue
            product_id = int(item["product_id"])
            quantities[product_id] = quantities.get(product_id, 0.0) + float(
                item["quantity"] or 0.0
            )
            submitter = str(item.get("submitter_name") or "").strip()
            if submitter:
                today_submitters.setdefault(product_id, set()).add(submitter)

        history_start = target_day - timedelta(days=history_days - 1)
        history = await store.purchase_history(
            [int(product["id"]) for product in products], history_start, target_day
        )
        last_purchase: dict[int, date] = {}
        last_quantity: dict[int, float] = {}
        last_submitters: dict[int, list[str]] = {}
        for row in history:
            product_id = int(row["product_id"])
            day_value = _date_of(row, "purchase_date")
            quantity = float(row.get("quantity") or 0.0)
            if quantity and day_value <= target_day:
                previous = last_purchase.get(product_id)
                names = [
                    name.strip()
                    for name in str(row.get("submitter_names") or "").split("、")
                    if name.strip()
                ]
                if previous is None or day_value > previous:
                    last_purchase[product_id] = day_value
                    last_quantity[product_id] = quantity
                    last_submitters[product_id] = names
                elif day_value == previous:
                    last_quantity[product_id] = last_quantity.get(product_id, 0.0) + quantity

        stock_rows = await store.latest_stock(
            [int(product["id"]) for product in products], target_day
        )
        no_order: list[dict] = []
        over: list[dict] = []
        under: list[dict] = []
        zero_stock: list[dict] = []
        for product in products:
            product_id = int(product["id"])
            ref = refs[product_id]
            quantity = quantities.get(product_id, 0.0)
            if not product.get("is_long_cycle"):
                last = last_purchase.get(product_id)
                if last is not None:
                    gap = (target_day - last).days
                    if gap >= no_order_days:
                        no_order.append(
                            {
                                "product_id": product_id,
                                "product_code": product.get("product_code"),
                                "name": product.get("name"),
                                "specification": product.get("specification") or "",
                                "unit": product.get("unit"),
                                "supplier_name": product.get("supplier_name"),
                                "category_name": product.get("category_name"),
                                "last_ordered": last.isoformat(),
                                "last_quantity": round(last_quantity.get(product_id, 0.0), 2),
                                "last_submitters": last_submitters.get(product_id, []),
                                "days": gap,
                                "severity": (
                                    "high" if gap >= 14 else ("normal" if gap >= 7 else "low")
                                ),
                            }
                        )
            if ref.reference and ref.reference > 0 and quantity > 0:
                ratio = quantity / ref.reference
                if ratio > over_ratio:
                    over.append(
                        {
                            "product_id": product_id,
                            "product_code": product.get("product_code"),
                            "name": product.get("name"),
                            "specification": product.get("specification") or "",
                            "unit": product.get("unit"),
                            "supplier_name": product.get("supplier_name"),
                            "category_name": product.get("category_name"),
                            "quantity": round(quantity, 2),
                            "reference": ref.reference,
                            "ratio": round(ratio, 2),
                            "submitters": sorted(today_submitters.get(product_id, set())),
                            "severity": (
                                "high" if ratio >= 3 else ("normal" if ratio >= 2 else "low")
                            ),
                        }
                    )
                elif ratio < under_ratio:
                    under.append(
                        {
                            "product_id": product_id,
                            "product_code": product.get("product_code"),
                            "name": product.get("name"),
                            "specification": product.get("specification") or "",
                            "unit": product.get("unit"),
                            "supplier_name": product.get("supplier_name"),
                            "category_name": product.get("category_name"),
                            "quantity": round(quantity, 2),
                            "reference": ref.reference,
                            "ratio": round(ratio, 2),
                            "submitters": sorted(today_submitters.get(product_id, set())),
                            "severity": (
                                "high" if ratio <= 0.2 else (
                                    "normal" if ratio <= 0.35 else "low"
                                )
                            ),
                        }
                    )
            stock = stock_rows.get(product_id)
            if product.get("need_stock_check") and stock:
                stock_day = _date_of(stock, "count_date")
                if (
                    stock_day >= target_day - timedelta(days=1)
                    and float(stock.get("current_stock") or 0.0) == 0
                ):
                    zero_stock.append(
                        {
                            "product_id": product_id,
                            "product_code": product.get("product_code"),
                            "name": product.get("name"),
                            "specification": product.get("specification") or "",
                            "unit": product.get("unit"),
                            "supplier_name": product.get("supplier_name"),
                            "category_name": product.get("category_name"),
                            "current_stock": 0.0,
                            "count_date": stock_day.isoformat(),
                            "counted_by": stock.get("counted_by_name"),
                            "severity": "high",
                        }
                    )
        return {
            "date": target_day.isoformat(),
            "no_order": sorted(no_order, key=lambda row: -row["days"]),
            "over": sorted(over, key=lambda row: -row["ratio"]),
            "under": sorted(under, key=lambda row: row["ratio"]),
            "zero_stock": zero_stock,
            "parameters": {
                "no_order_days": no_order_days,
                "over_ratio": over_ratio,
                "under_ratio": under_ratio,
                "history_days": history_days,
            },
        }

    async def product_history(self, db, actor: PurchaseActor, product_id: int,
                              *, days: int = 21, day: Any = None) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        product = await store.product_by_id(int(product_id))
        if product is None:
            raise PurchaseError("product_not_found", "商品 " + str(product_id) + " 不存在")
        target_day = _parse_date(day, store.today())
        days = min(180, max(7, int(days or 21)))
        start = target_day - timedelta(days=days - 1)
        purchases = await store.item_daily_series([int(product_id)], start, target_day)
        stocks = await store.stock_history(int(product_id), start, target_day)
        purchase_map = {
            _date_of(row, "order_date"): float(row["quantity"]) for row in purchases
        }
        stock_map = {
            _date_of(row, "count_date"): float(row["current_stock"]) for row in stocks
        }
        usage = daily_usage_series(stock_map, purchase_map, start, target_day)
        timeline = []
        cursor = start
        while cursor <= target_day:
            timeline.append(
                {
                    "date": cursor.isoformat(),
                    "purchase": round(purchase_map.get(cursor, 0.0), 2),
                    "stock": stock_map.get(cursor),
                    "usage": round(usage[cursor], 2) if cursor in usage else None,
                    "counted": cursor in stock_map,
                }
            )
            cursor += timedelta(days=1)
        total_quantity = round(sum(purchase_map.values()), 2)
        total_orders = sum(int(row.get("order_count") or 0) for row in purchases)
        days = (target_day - start).days + 1
        last_dates = [day for day, value in purchase_map.items() if value > 0]
        return {
            "product": product,
            "start": start.isoformat(),
            "end": target_day.isoformat(),
            "stats": {
                "total_orders": total_orders,
                "total_quantity": total_quantity,
                "average_quantity": round(total_quantity / days, 2) if days else 0,
                "order_frequency": round(total_orders / days, 2) if days else 0,
                "days_with_purchase": len(last_dates),
                "last_order_date": max(last_dates).isoformat() if last_dates else None,
            },
            "timeline": timeline,
        }

    # ── 目录维护（只认管理端）─────────────────────────────────

    async def create_supplier(self, db, actor: PurchaseActor,
                              payload: Mapping[str, Any]) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        name = _clean_text(payload.get("name"), "供应商名称", max_length=MAX_NAME, required=True)
        raw_code = str(payload.get("code") or "").strip()
        now = store._now_dt()
        async with write_unit(db):
            code = raw_code
            if not code:
                existing = [
                    int(row["code"]) for row in await store.suppliers(include_inactive=True)
                ]
                code = f"{max(existing, default=0) + 1:02d}"
            if not _SUPPLIER_CODE_RE.match(code):
                raise PurchaseError("bad_code", "供应商编码必须是 2 位数字")
            try:
                supplier_id = await store.insert_supplier(name, code, now)
            except asyncpg.UniqueViolationError as exc:
                raise PurchaseError("duplicate_supplier", "供应商名称或编码已存在") from exc
        return {"supplier": await self._supplier(store, supplier_id)}

    async def update_supplier(self, db, actor: PurchaseActor, supplier_id: int,
                              payload: Mapping[str, Any]) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        supplier = await store.supplier_by_id(int(supplier_id))
        if supplier is None:
            raise PurchaseError("supplier_not_found", "供应商不存在")
        fields: dict = {}
        if "name" in payload:
            fields["name"] = _clean_text(
                payload.get("name"), "供应商名称", max_length=MAX_NAME, required=True
            )
        if "code" in payload:
            code = str(payload.get("code") or "").strip()
            if not _SUPPLIER_CODE_RE.match(code):
                raise PurchaseError("bad_code", "供应商编码必须是 2 位数字")
            fields["code"] = code
        if "active" in payload:
            fields["active"] = _clean_bool(payload.get("active"), True)
            if not fields["active"] and supplier.get("active"):
                used = await store.active_product_count(supplier_id=int(supplier_id))
                if used:
                    raise PurchaseError(
                        "supplier_in_use",
                        f"该供应商下还有 {used} 个启用商品，先停用或改到别的供应商再停用",
                    )
        now = store._now_dt()
        async with write_unit(db):
            try:
                await store.update_supplier(int(supplier_id), fields, now)
            except asyncpg.UniqueViolationError as exc:
                raise PurchaseError("duplicate_supplier", "供应商名称或编码已存在") from exc
        return {"supplier": await self._supplier(store, int(supplier_id))}

    async def _supplier(self, store: PurchaseStore, supplier_id: int) -> dict:
        supplier = await store.supplier_by_id(int(supplier_id))
        if supplier is None:
            raise PurchaseError("supplier_not_found", "供应商不存在")
        return supplier

    async def create_category(self, db, actor: PurchaseActor,
                              payload: Mapping[str, Any]) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        supplier_id = _int_or_none(payload.get("supplier_id"))
        if not supplier_id or await store.supplier_by_id(supplier_id) is None:
            raise PurchaseError("supplier_not_found", "请选择供应商")
        name = _clean_text(payload.get("name"), "分类名称", max_length=MAX_NAME, required=True)
        raw_code = str(payload.get("code") or "").strip()
        now = store._now_dt()
        async with write_unit(db):
            code = raw_code
            if not code:
                seq = await store.bump_supplier_category_code(int(supplier_id), now)
                code = f"{seq:02d}"
            if not _SUPPLIER_CODE_RE.match(code):
                raise PurchaseError("bad_code", "分类编码必须是 2 位数字")
            try:
                category_id = await store.insert_category(
                    int(supplier_id), name, code,
                    int(payload.get("next_product_seq") or 1), now,
                )
            except asyncpg.UniqueViolationError as exc:
                raise PurchaseError(
                    "duplicate_category", "这个供应商下已有同名或同编码分类"
                ) from exc
        return {"category": await self._category(store, category_id)}

    async def update_category(self, db, actor: PurchaseActor, category_id: int,
                              payload: Mapping[str, Any]) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        category = await store.category_by_id(int(category_id))
        if category is None:
            raise PurchaseError("category_not_found", "分类不存在")
        fields: dict = {}
        if "name" in payload:
            fields["name"] = _clean_text(
                payload.get("name"), "分类名称", max_length=MAX_NAME, required=True
            )
        if "code" in payload:
            code = str(payload.get("code") or "").strip()
            if not _SUPPLIER_CODE_RE.match(code):
                raise PurchaseError("bad_code", "分类编码必须是 2 位数字")
            fields["code"] = code
        if "active" in payload:
            fields["active"] = _clean_bool(payload.get("active"), True)
            if not fields["active"] and category.get("active"):
                used = await store.active_product_count(category_id=int(category_id))
                if used:
                    raise PurchaseError(
                        "category_in_use",
                        f"该分类下还有 {used} 个启用商品，先停用或改到别的分类再停用",
                    )
        now = store._now_dt()
        async with write_unit(db):
            try:
                await store.update_category(int(category_id), fields, now)
            except asyncpg.UniqueViolationError as exc:
                raise PurchaseError(
                    "duplicate_category", "这个供应商下已有同名或同编码分类"
                ) from exc
        return {"category": await self._category(store, int(category_id))}

    async def _category(self, store: PurchaseStore, category_id: int) -> dict:
        category = await store.category_by_id(int(category_id))
        if category is None:
            raise PurchaseError("category_not_found", "分类不存在")
        return category

    async def create_position(self, db, actor: PurchaseActor,
                              payload: Mapping[str, Any]) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        name = _clean_text(payload.get("name"), "岗位名称", max_length=MAX_NAME, required=True)
        now = store._now_dt()
        async with write_unit(db):
            try:
                position_id = await store.insert_position(
                    name,
                    int(payload.get("sort_order") or 0),
                    now,
                    _clean_bool(payload.get("show_in_order"), True),
                )
            except asyncpg.UniqueViolationError as exc:
                raise PurchaseError("duplicate_position", "岗位名称已存在") from exc
        return {"position": await self._position(store, position_id)}

    async def update_position(self, db, actor: PurchaseActor, position_id: int,
                              payload: Mapping[str, Any]) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        if await store.position_by_id(int(position_id)) is None:
            raise PurchaseError("position_not_found", "岗位不存在")
        fields: dict = {}
        if "name" in payload:
            fields["name"] = _clean_text(
                payload.get("name"), "岗位名称", max_length=MAX_NAME, required=True
            )
        if "sort_order" in payload:
            fields["sort_order"] = int(payload.get("sort_order") or 0)
        if "show_in_order" in payload:
            fields["show_in_order"] = _clean_bool(payload.get("show_in_order"), True)
        if "active" in payload:
            fields["active"] = _clean_bool(payload.get("active"), True)
        now = store._now_dt()
        async with write_unit(db):
            try:
                await store.update_position(int(position_id), fields, now)
            except asyncpg.UniqueViolationError as exc:
                raise PurchaseError("duplicate_position", "岗位名称已存在") from exc
        return {"position": await self._position(store, int(position_id))}

    async def _position(self, store: PurchaseStore, position_id: int) -> dict:
        position = await store.position_by_id(int(position_id))
        if position is None:
            raise PurchaseError("position_not_found", "岗位不存在")
        return position

    async def create_product(self, db, actor: PurchaseActor,
                             payload: Mapping[str, Any]) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        supplier_id = _int_or_none(payload.get("supplier_id"))
        category_id = _int_or_none(payload.get("category_id"))
        if not supplier_id or await store.supplier_by_id(supplier_id) is None:
            raise PurchaseError("supplier_not_found", "请选择供应商")
        category = await store.category_by_id(category_id) if category_id else None
        if category is None or int(category["supplier_id"]) != int(supplier_id):
            raise PurchaseError("category_not_found", "分类不属于这个供应商")
        name = _clean_text(payload.get("name"), "商品名称", max_length=MAX_NAME, required=True)
        unit = _clean_text(payload.get("unit"), "单位", max_length=MAX_UNIT)
        specification = _clean_text(payload.get("specification"), "规格", max_length=MAX_SPEC)
        remark = _clean_text(payload.get("remark"), "备注", max_length=MAX_REMARK)
        order_step = _clean_positive(payload.get("order_step"), "下单步长", 1.0)
        safety_factor = _clean_positive(
            payload.get("safety_factor"), "安全系数", DEFAULT_SAFETY_FACTOR
        )
        positions = payload.get("position_ids")
        if not isinstance(positions, list) or not positions:
            raise PurchaseError("bad_position", "商品至少要挂一个岗位")
        position_ids = []
        for position_id in positions:
            position = await store.position_by_id(int(position_id))
            if position is None:
                raise PurchaseError("position_not_found", "所选岗位不存在")
            position_ids.append(int(position_id))
        raw_code = str(payload.get("product_code") or "").strip()
        if raw_code and not _CODE_RE.match(raw_code):
            raise PurchaseError("bad_code", "商品编码必须是 7 位数字")
        now = store._now_dt()
        async with write_unit(db):
            code = raw_code
            if not code:
                code = await self._next_product_code(store, supplier_id, category_id, now)
            try:
                product_id = await store.insert_product(
                    {
                        "product_code": code,
                        "supplier_id": int(supplier_id),
                        "category_id": int(category_id),
                        "name": name,
                        "specification": specification,
                        "unit": unit,
                        "remark": remark,
                        "order_step": order_step,
                        "safety_factor": safety_factor,
                        "need_stock_check": _clean_bool(
                            payload.get("need_stock_check"), False
                        ),
                        "is_long_cycle": _clean_bool(payload.get("is_long_cycle"), False),
                        "active": True,
                    },
                    now,
                )
            except asyncpg.UniqueViolationError as exc:
                raise PurchaseError("duplicate_product", "商品编码已存在") from exc
            await store.set_product_positions(product_id, position_ids, now)
        return {"product": await self._product(store, product_id)}

    async def update_product(self, db, actor: PurchaseActor, product_id: int,
                             payload: Mapping[str, Any]) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        old = await store.product_by_id(int(product_id))
        if old is None:
            raise PurchaseError("product_not_found", "商品不存在")
        supplier_id = _int_or_none(payload.get("supplier_id")) or int(old["supplier_id"])
        category_id = _int_or_none(payload.get("category_id")) or int(old["category_id"])
        supplier = await store.supplier_by_id(int(supplier_id))
        category = await store.category_by_id(int(category_id))
        if supplier is None:
            raise PurchaseError("supplier_not_found", "供应商不存在")
        if category is None or int(category["supplier_id"]) != int(supplier_id):
            raise PurchaseError("category_not_found", "分类不属于这个供应商")
        fields: dict = {"supplier_id": int(supplier_id), "category_id": int(category_id)}
        if "name" in payload:
            fields["name"] = _clean_text(
                payload.get("name"), "商品名称", max_length=MAX_NAME, required=True
            )
        if "specification" in payload:
            fields["specification"] = _clean_text(
                payload.get("specification"), "规格", max_length=MAX_SPEC
            )
        if "unit" in payload:
            fields["unit"] = _clean_text(payload.get("unit"), "单位", max_length=MAX_UNIT)
        if "remark" in payload:
            fields["remark"] = _clean_text(
                payload.get("remark"), "备注", max_length=MAX_REMARK
            )
        if "order_step" in payload:
            fields["order_step"] = _clean_positive(
                payload.get("order_step"), "下单步长", 1.0
            )
        if "safety_factor" in payload:
            fields["safety_factor"] = _clean_positive(
                payload.get("safety_factor"), "安全系数", DEFAULT_SAFETY_FACTOR
            )
        if "need_stock_check" in payload:
            fields["need_stock_check"] = _clean_bool(
                payload.get("need_stock_check"), False
            )
        if "is_long_cycle" in payload:
            fields["is_long_cycle"] = _clean_bool(payload.get("is_long_cycle"), False)
        if "active" in payload:
            fields["active"] = _clean_bool(payload.get("active"), True)
        requested_code = str(payload.get("product_code") or "").strip()
        if requested_code and not _CODE_RE.match(requested_code):
            raise PurchaseError("bad_code", "商品编码必须是 7 位数字")
        supplier_changed = int(supplier_id) != int(old["supplier_id"])
        category_changed = int(category_id) != int(old["category_id"])
        now = store._now_dt()
        async with write_unit(db):
            code = requested_code or str(old.get("product_code") or "")
            if not code or ((supplier_changed or category_changed) and not requested_code):
                code = await self._next_product_code(
                    store, int(supplier_id), int(category_id), now
                )
            fields["product_code"] = code
            try:
                await store.update_product(int(product_id), fields, now)
            except asyncpg.UniqueViolationError as exc:
                raise PurchaseError("duplicate_product", "商品编码已存在") from exc
            if "position_ids" in payload:
                position_ids = payload.get("position_ids") or []
                if not isinstance(position_ids, list) or not position_ids:
                    raise PurchaseError("bad_position", "商品至少要挂一个岗位")
                for position_id in position_ids:
                    if await store.position_by_id(int(position_id)) is None:
                        raise PurchaseError("position_not_found", "所选岗位不存在")
                await store.set_product_positions(int(product_id), position_ids, now)
            new = await store.product_by_id(int(product_id))
            if new and (
                str(new.get("product_code")) != str(old.get("product_code"))
                or supplier_changed
                or category_changed
            ):
                await store.item_snapshot_sync(new, now)
                await store.insert_code_change(
                    int(product_id), old, new,
                    {
                        "employee_id": actor.employee_id,
                        "name": actor.display_name,
                        "kind": actor.kind,
                    },
                    now,
                )
        return {"product": await self._product(store, int(product_id))}

    async def import_products(self, db, actor: PurchaseActor,
                              rows: Sequence[Mapping[str, Any]], *,
                              mode: str = "upsert", dry_run: bool = False) -> dict:
        """按 Excel 行导入目录。逐行提交：某一行失败不影响已成功的行。

        dry_run 只做目录解析与字段预检，不写库；真正的规则仍由 create_product /
        update_product 校验（例如编码唯一、岗位存在）。mode=add 遇到已有编码报错，
        mode=upsert 按编码更新。
        """
        self._require_admin(actor)
        if mode not in ("add", "upsert"):
            raise PurchaseError("bad_request", "导入方式只能是 add 或 upsert")
        if not rows:
            raise PurchaseError("bad_request", "表格里没有可导入的商品行")
        store = self._store(db)
        suppliers = await store.suppliers(include_inactive=True)
        categories = await store.categories(include_inactive=True)
        positions = await store.positions(include_inactive=True)
        existing_rows = await store.products(active_only=False)
        supplier_by_code = {str(row.get("code")): row for row in suppliers if row.get("code")}
        supplier_by_name = {str(row.get("name")): row for row in suppliers if row.get("name")}
        category_by_supplier: dict[int, list[dict]] = {}
        for row in categories:
            category_by_supplier.setdefault(int(row["supplier_id"]), []).append(row)
        position_by_name = {str(row.get("name")): row for row in positions if row.get("name")}
        existing = {
            str(row.get("product_code")): row
            for row in existing_rows
            if row.get("product_code")
        }

        results: list[dict] = []
        created = updated = failed = 0
        seen_codes: set[str] = set()

        for item in rows:
            row_number = int(item.get("row") or 0)
            code = str(item.get("product_code") or "").strip()
            name = str(item.get("name") or "").strip()
            action = "update" if code and code in existing else "create"

            def fail(message: str) -> None:
                nonlocal failed
                failed += 1
                results.append({
                    "row": row_number,
                    "action": action,
                    "product_code": code,
                    "name": name,
                    "ok": False,
                    "error": message,
                })

            if item.get("error"):
                fail(str(item["error"]))
                continue
            if code and not _CODE_RE.match(code):
                fail("商品编码必须是 7 位数字")
                continue
            if code and code in seen_codes:
                fail("表格里商品编码重复")
                continue
            if code:
                seen_codes.add(code)
            if mode == "add" and action == "update":
                fail("商品编码已存在；需要覆盖请选「新增或更新」")
                continue

            supplier_code = str(item.get("supplier_code") or "").strip()
            supplier_name = str(item.get("supplier_name") or "").strip()
            supplier = supplier_by_code.get(supplier_code) if supplier_code else None
            if supplier is None and supplier_name:
                supplier = supplier_by_name.get(supplier_name)
            if supplier is None:
                label = supplier_code or supplier_name or "（空）"
                fail(f"供应商「{label}」不在目录里，请先建供应商")
                continue

            category_code = str(item.get("category_code") or "").strip()
            category_name = str(item.get("category_name") or "").strip()
            category = None
            for candidate in category_by_supplier.get(int(supplier["id"]), []):
                if category_code and str(candidate.get("code")) == category_code:
                    category = candidate
                    break
                if not category_code and category_name and str(candidate.get("name")) == category_name:
                    category = candidate
                    break
            if category is None:
                label = category_code or category_name or "（空）"
                fail(f"分类「{label}」不在供应商「{supplier['name']}」下")
                continue

            position_ids: list[int] = []
            missing_positions: list[str] = []
            for position_name in item.get("positions") or []:
                position = position_by_name.get(str(position_name))
                if position is None:
                    missing_positions.append(str(position_name))
                else:
                    position_ids.append(int(position["id"]))
            if missing_positions:
                fail("这些岗位不在目录里：" + "、".join(missing_positions))
                continue

            try:
                _clean_text(name, "商品名称", max_length=MAX_NAME, required=True)
                _clean_text(item.get("unit"), "单位", max_length=MAX_UNIT)
                _clean_text(item.get("specification"), "规格", max_length=MAX_SPEC)
                _clean_text(item.get("remark"), "备注", max_length=MAX_REMARK)
                for key, label in (("order_step", "下单步长"), ("safety_factor", "安全系数")):
                    if item.get(key) is not None:
                        _clean_positive(item.get(key), label, 1.0)
            except PurchaseError as exc:
                fail(exc.message)
                continue

            payload: dict[str, Any] = {
                "supplier_id": int(supplier["id"]),
                "category_id": int(category["id"]),
                "name": name,
                "unit": str(item.get("unit") or "").strip(),
                "specification": str(item.get("specification") or "").strip(),
                "remark": str(item.get("remark") or "").strip(),
                "position_ids": position_ids,
            }
            if code:
                payload["product_code"] = code
            for key in ("order_step", "safety_factor"):
                if item.get(key) is not None:
                    payload[key] = item[key]
            for key in ("need_stock_check", "is_long_cycle", "active"):
                if item.get(key) is not None:
                    payload[key] = item[key]

            if dry_run:
                if action == "update":
                    updated += 1
                else:
                    created += 1
                results.append({
                    "row": row_number,
                    "action": action,
                    "product_code": code,
                    "name": name,
                    "ok": True,
                    "error": "",
                })
                continue

            try:
                if action == "update":
                    await self.update_product(
                        db, actor, int(existing[code]["id"]), payload
                    )
                    updated += 1
                else:
                    result = await self.create_product(db, actor, payload)
                    product = result.get("product") or {}
                    code = str(product.get("product_code") or code)
                    if code:
                        existing[code] = product
                    created += 1
                results.append({
                    "row": row_number,
                    "action": action,
                    "product_code": code,
                    "name": name,
                    "ok": True,
                    "error": "",
                })
            except PurchaseError as exc:
                fail(exc.message)

        return {
            "mode": mode,
            "dry_run": bool(dry_run),
            "total": len(rows),
            "created": created,
            "updated": updated,
            "failed": failed,
            "rows": results,
        }

    async def _product(self, store: PurchaseStore, product_id: int) -> dict:
        product = await store.product_by_id(int(product_id))
        if product is None:
            raise PurchaseError("product_not_found", "商品不存在")
        return product

    async def _next_product_code(self, store: PurchaseStore, supplier_id: int,
                                 category_id: int, now: datetime) -> str:
        supplier = await store.supplier_by_id(int(supplier_id))
        category = await store.category_by_id(int(category_id))
        if supplier is None or category is None:
            raise PurchaseError("category_not_found", "供应商或分类不存在")
        for _ in range(950):
            seq = await store.bump_category_product_seq(int(category_id), now)
            if seq > 999:
                break
            code = f"{supplier['code']}{category['code']}{seq:03d}"
            if await store.product_by_code(code) is None:
                return code
        raise PurchaseError("code_exhausted", "这个分类的商品序号已用完（999），请换分类")

    async def set_date_tag(self, db, actor: PurchaseActor,
                           payload: Mapping[str, Any]) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        target_day = _parse_date(payload.get("tag_date"))
        tag = str(payload.get("tag") or "").strip()
        if tag not in TAG_VALUES:
            raise PurchaseError("bad_tag", "标签只能是 2万–8万")
        now = store._now_dt()
        async with write_unit(db):
            await store.set_date_tag(
                target_day, tag,
                {
                    "employee_id": actor.employee_id,
                    "name": actor.display_name,
                    "kind": actor.kind,
                },
                now,
            )
        return {"tag_date": target_day.isoformat(), "tag": tag}

    async def delete_date_tag(self, db, actor: PurchaseActor, day: Any) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        target_day = _parse_date(day)
        async with write_unit(db):
            await store.delete_date_tag(target_day)
        return {"tag_date": target_day.isoformat(), "deleted": True}

    async def set_date_tags(self, db, actor: PurchaseActor,
                            payload: Mapping[str, Any]) -> dict:
        """批量给指定日期打同一个标签（月历多选 / 周末高峰 / 周一高峰）。"""
        self._require_admin(actor)
        store = self._store(db)
        raw_days = payload.get("dates") or payload.get("tag_dates") or []
        if isinstance(raw_days, str):
            raw_days = [part for part in raw_days.replace("，", ",").split(",")]
        if not isinstance(raw_days, (list, tuple)):
            raise PurchaseError("bad_dates", "要选的日期格式不对")
        days = sorted({_parse_date(item) for item in raw_days})
        if not days:
            raise PurchaseError("bad_dates", "至少选一天")
        if len(days) > 62:
            raise PurchaseError("too_many_dates", "一次最多设置 62 天")
        tag = str(payload.get("tag") or "").strip()
        if tag not in TAG_VALUES:
            raise PurchaseError("bad_tag", "标签只能是 2万–8万")
        now = store._now_dt()
        actor_ref = {
            "employee_id": actor.employee_id,
            "name": actor.display_name,
            "kind": actor.kind,
        }
        async with write_unit(db):
            for target_day in days:
                await store.set_date_tag(target_day, tag, actor_ref, now)
        return {"dates": [day.isoformat() for day in days], "tag": tag, "count": len(days)}

    async def clear_date_tags(self, db, actor: PurchaseActor, start: Any,
                              end: Any) -> dict:
        """清掉一个日期区间里的手工标签（清空本月）。星期默认不受影响。"""
        self._require_admin(actor)
        store = self._store(db)
        start_day = _parse_date(start)
        end_day = _parse_date(end)
        if end_day < start_day:
            raise PurchaseError("bad_range", "结束日期不能早于开始日期")
        if (end_day - start_day).days > 62:
            raise PurchaseError("bad_range", "一次最多清 62 天")
        rows = await store.date_tags(start_day, end_day)
        async with write_unit(db):
            for row in rows:
                await store.delete_date_tag(row["tag_date"])
        return {
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
            "deleted": len(rows),
        }

    async def set_weekday_default(self, db, actor: PurchaseActor,
                                  payload: Mapping[str, Any]) -> dict:
        self._require_admin(actor)
        store = self._store(db)
        weekday = int(payload.get("weekday"))
        if not 0 <= weekday <= 6:
            raise PurchaseError("bad_weekday", "星期必须是 0–6")
        tag = str(payload.get("tag") or "").strip()
        if tag not in TAG_VALUES:
            raise PurchaseError("bad_tag", "标签只能是 2万–8万")
        now = store._now_dt()
        async with write_unit(db):
            await store.set_weekday_default(weekday, tag, now)
        return {"weekday": weekday, "tag": tag}


purchase_service = PurchaseService()
