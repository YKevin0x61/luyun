#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工餐采购域的业务层（ADR 0135，`.scratch/staff-meal/spec.md`）。

把三件事串起来：

1. **月度菜单导入**（xlsx 解析在 `menu_import.py`）：写当月每日菜单，给每天建一张
   草稿单并把菜单里的菜物化成单据菜品；已提交的单不碰（单据是快照，Q16）。
2. **单日录入**：按菜品填食材与数量（同名菜历史上买过的食材名会带出来，但不带数量
   —— Q9），单据级「其他采购项」用 `dish_id = None` 表达。
3. **提交与推送**：校验至少一项有数量 → `push_seq + 1` → 渲染文本入企微出站队列。
   改已提交单后重新提交 = 再走一遍提交（标题加「（更新）」），推完整清单（Q14）。

权限（2026-11-11 用户裁定：这一域**不设能力开关**，所有员工都能用）：读 / 填草稿 = 任何
员工或管理员；改已提交单 = 提交者本人或管理员；导入 / 改菜单 / 删单 = 只有管理员。判据
每次请求现读（`StaffMealActor` 由 API 层按当前会话构造，不在登录时快照）。

错误统一用 `StaffMealError(code, message)`；HTTP 映射在 `api/staff_meal.py`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Mapping, Optional

from db_core.utils import CHINA_TZ
from services.staff_meal import push
from services.staff_meal.menu_import import (
    FREEFORM_MARKER,
    MenuImportError,
    parse_menu_workbook,
)
from services.staff_meal.store import StaffMealError, StaffMealStore

logger = logging.getLogger(__name__)

#: 单据状态（库值是契约）。
DRAFT = "draft"
SUBMITTED = "submitted"

#: 菜品位置（单据快照里 `role` 的取值；前端据此把主菜放前面）。
ROLE_MAIN = "main"
ROLE_SIDE = "side"

MEAL_LUNCH = "lunch"
MEAL_DINNER = "dinner"

MENU_TEXT_FIELDS = (
    "lunch_main",
    "lunch_side1",
    "lunch_side2",
    "dinner_main",
    "dinner_side1",
    "dinner_side2",
    "drink",
)

MAX_ITEMS = 300
MAX_ITEM_NAME = 40
MAX_UNIT = 10
MAX_QUANTITY = 99999.0
MAX_REMARK = 200

ZERO_SUBSCRIBE_ERROR = (
    "没有任何群订阅「员工餐采购计划」：请到 Admin「企业微信推送」页把它勾给核心群，"
    "再点重试"
)
PUSH_ENQUEUE_ERROR = "推送入队失败：请检查企业微信推送配置后点重试"


@dataclass(frozen=True)
class StaffMealActor:
    """一次请求的操作者（由 API 层按当前会话构造，权限每次现读）。"""

    kind: str  # "admin" | "staff"
    employee_id: Optional[int] = None
    name: str = ""

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"

    @property
    def can_write(self) -> bool:
        """管理员或任何员工 —— 这一域不设能力开关（2026-11-11 用户裁定）。"""
        return self.kind in ("admin", "staff")

    @property
    def display_name(self) -> str:
        return self.name or ("管理端" if self.is_admin else "")


class MenuConflictError(StaffMealError):
    """整月导入撞上已有菜单/单据：带上「哪些天已有单」的结构化清单。"""

    def __init__(self, payload: dict):
        super().__init__("menu_exists", "这个月已经有菜单或采购单了")
        self.payload = payload


def _forbidden(message: str) -> StaffMealError:
    return StaffMealError("forbidden", message)


def _norm_updated_at(value: Any) -> Optional[str]:
    """把 ``updated_at`` 规范成可比对的形式（乐观锁 F14）。

    库里读出来是 ``datetime``（带北京时区），页面回传的是 JSON 里的 ISO 字符串
    （``2026-11-13T10:00:00+08:00``，可能带微秒），直接 ``str()`` 比会因空格 / ``T``
    的写法差异而误判冲突。两边都先按 ISO 解析再输出，解析不了就按原文本比。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        return text


def _planned_push_at(order_date: str, store: StaffMealStore) -> Optional[str]:
    """这一版该在什么时候推：None = 立即，否则计划时刻 ISO（北京时）。

    规则（`.scratch/wb-review/push-schedule-contract.md` §1）：使用日距今天 ≥ 2 天
    时，定时到「使用日前一天 10:00」；今天 / 明天提交一律立即。计划时刻已经过去
    （前一天 10 点后才补填的远期单）也立即推 —— 不能排一个已经到点的定时。
    """
    target = datetime.strptime(str(order_date), "%Y-%m-%d").date()
    today = datetime.strptime(str(store.today()), "%Y-%m-%d").date()
    if (target - today).days < 2:
        return None
    due = datetime.combine(target - timedelta(days=1), time(10, 0), tzinfo=CHINA_TZ)
    if due <= store._now_dt():
        return None
    return due.isoformat()


def _dishes_from_menu_row(row: Mapping[str, Any]) -> list[dict]:
    """菜单一行 → 单据菜品快照（主菜 + 两道副菜，午 / 晚）。

    「自主发挥」是占位菜名，不是真菜 —— 不要物化进单据，否则每张自主发挥日都躺着一道
    「自主发挥」让店长不知填什么食材（采购靠单据级「其他采购项」）。
    """
    dishes: list[dict] = []

    def add(name: Any, meal: str, role: str, sort_order: int) -> None:
        text = str(name or "").strip()
        if not text or text == FREEFORM_MARKER:
            return
        dishes.append(
            {
                "dish_name": text,
                "meal": meal,
                "role": role,
                "sort_order": sort_order,
            }
        )

    add(row.get("lunch_main"), MEAL_LUNCH, ROLE_MAIN, 0)
    add(row.get("lunch_side1"), MEAL_LUNCH, ROLE_SIDE, 1)
    add(row.get("lunch_side2"), MEAL_LUNCH, ROLE_SIDE, 2)
    add(row.get("dinner_main"), MEAL_DINNER, ROLE_MAIN, 3)
    add(row.get("dinner_side1"), MEAL_DINNER, ROLE_SIDE, 4)
    add(row.get("dinner_side2"), MEAL_DINNER, ROLE_SIDE, 5)
    return dishes


def _month_label(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def _clean_quantity(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        value = round(float(raw), 2)
    except (TypeError, ValueError):
        raise StaffMealError("item_invalid", "数量必须是数字")
    if value <= 0:
        raise StaffMealError("item_invalid", "数量必须大于 0（没买的行留空即可）")
    if value > MAX_QUANTITY:
        raise StaffMealError("item_invalid", f"数量不能超过 {int(MAX_QUANTITY)}")
    return value


def normalize_items(raw: Any) -> list[dict]:
    """页面提交的条目数组 → store 的写法；空行（没名字也没数量）直接丢掉。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise StaffMealError("item_invalid", "items 必须是数组")
    if len(raw) > MAX_ITEMS:
        raise StaffMealError("item_invalid", f"一张单最多 {MAX_ITEMS} 项")
    cleaned: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise StaffMealError("item_invalid", f"第 {index + 1} 项格式不对")
        name = str(item.get("item_name") or "").strip()
        quantity = _clean_quantity(item.get("quantity"))
        if not name and quantity is None:
            continue  # 页面上的空行
        if not name:
            raise StaffMealError("item_invalid", f"第 {index + 1} 项填了数量但没有食材名")
        if len(name) > MAX_ITEM_NAME:
            raise StaffMealError(
                "item_invalid", f"第 {index + 1} 项食材名超过 {MAX_ITEM_NAME} 个字"
            )
        unit = str(item.get("unit") or "").strip()
        if len(unit) > MAX_UNIT:
            raise StaffMealError(
                "item_invalid", f"第 {index + 1} 项单位超过 {MAX_UNIT} 个字"
            )
        dish_id = item.get("dish_id")
        if dish_id is not None:
            try:
                dish_id = int(dish_id)
            except (TypeError, ValueError):
                raise StaffMealError("item_invalid", f"第 {index + 1} 项的菜品 id 不对")
        cleaned.append(
            {
                "dish_id": dish_id,
                "item_name": name,
                "quantity": quantity,
                "unit": unit or None,
                "sort_order": len(cleaned),
            }
        )
    return cleaned


class StaffMealService:
    """无状态服务：每次调用传 db，store 在方法内构造。"""

    def _store(self, db) -> StaffMealStore:
        return StaffMealStore(db)

    # ── 权限 ──────────────────────────────────────────────────

    def _require_reader(self, actor: StaffMealActor) -> None:
        # 员工餐采购是全员都能参与的活，不设能力开关；这里只挡无法识别的身份。
        if not actor.can_write:
            raise _forbidden("请用员工或超级管理员身份登录后再看员工餐菜单")

    def _require_admin(self, actor: StaffMealActor) -> None:
        if not actor.is_admin:
            raise _forbidden("只有超级管理员能导入菜单 / 修改菜单 / 删单")

    def _can_order_write(self, actor: StaffMealActor, order: dict) -> bool:
        """「这张单我改不改得动」的判据 —— 一处，`_require_order_write` 与详情里的
        `can_edit` 都走它。给前端用的布尔值必须是**同一个判据**，否则会出现按钮亮着、
        一按 403 的错位。"""
        if actor.is_admin:
            return True
        if actor.employee_id is None:
            return False
        if str(order.get("status") or DRAFT) == SUBMITTED:
            submitter = order.get("submitter_employee_id")
            return (
                actor.employee_id is not None
                and submitter is not None
                and int(submitter) == int(actor.employee_id)
            )
        return True

    def _require_order_write(self, actor: StaffMealActor, order: dict) -> None:
        if actor.is_admin or self._can_order_write(actor, order):
            return
        raise _forbidden("这张采购单已经提交了：只有提交者本人或超级管理员能改")

    # ── 读 ────────────────────────────────────────────────────

    async def _with_push_state(
        self, db, store: StaffMealStore, order: dict
    ) -> dict:
        """推送中 / 待定时的单现读出站状态（读时不回写业务行）。"""
        if (
            str(order.get("push_status") or "") in ("pushing", "scheduled")
            and int(order.get("push_seq") or 0) > 0
        ):
            mapped = await push.latest_push_status(
                db,
                str(order["order_date"]),
                int(order["push_seq"]),
                now=store._now_dt(),
            )
            if mapped is not None:
                status, error = mapped
                order = {
                    **order,
                    "push_status": status,
                    "push_error": error or order.get("push_error"),
                }
        return order

    async def order_detail(
        self, db, store: StaffMealStore, order: dict, actor: StaffMealActor | None = None
    ) -> dict:
        """单据的完整形状：单据 + 菜品（各自带食材）+ 其他采购项 + 汇总。

        `actor` 给了就多带一个 `can_edit`（当前身份改不改得动这张单）—— 前端用它
        决定「编辑区是否只读」，判据与 `_require_order_write` 同一份。
        """
        order = await self._with_push_state(db, store, order)
        if actor is not None:
            order = {**order, "can_edit": self._can_order_write(actor, order)}
        order_id = int(order["id"])
        dishes = await store.dishes_for(order_id)
        items = await store.items_for(order_id)
        summary = await store.summary_rows(order_id)
        by_dish: dict[Optional[int], list[dict]] = {}
        for item in items:
            by_dish.setdefault(item.get("dish_id"), []).append(item)
        for dish in dishes:
            dish["items"] = by_dish.get(dish["id"], [])
        return {
            "order": order,
            "dishes": dishes,
            "other_items": by_dish.get(None, []),
            "summary": summary,
        }

    async def list_month(
        self, db, actor: StaffMealActor, year_month: str
    ) -> dict:
        """月历：菜单每天一行 + 那天的单据状态。"""
        self._require_reader(actor)
        store = self._store(db)
        menu_days = await store.menu_days_month(year_month)
        orders = await store.orders_month(year_month)
        # 待定时 / 推送中的单现读出站状态：10 点调度循环到点后，页面要立刻从
        # 「待推送」翻成「推送中 / 已推送」，不能等下次提交才刷新。
        refreshed = []
        for order in orders:
            if str(order.get("push_status") or "") in ("pushing", "scheduled"):
                order = await self._with_push_state(db, store, order)
            refreshed.append(order)
        orders = refreshed
        counts = await store.item_counts_for_month(year_month)
        order_by_date = {str(order["order_date"]): order for order in orders}
        dates = sorted(set(order_by_date) | {str(day["menu_date"]) for day in menu_days})
        days = []
        for date in dates:
            menu_day = next(
                (day for day in menu_days if str(day["menu_date"]) == date), None
            )
            order = order_by_date.get(date)
            brief = None
            if order is not None:
                brief = {
                    "id": order["id"],
                    "order_date": order["order_date"],
                    "status": order["status"],
                    "push_status": order["push_status"],
                    "push_error": order.get("push_error"),
                    "submitter_name": order.get("submitter_name"),
                    "submitted_at": order.get("submitted_at"),
                    # 定时推送的计划时刻（None = 立即 / 已不是待定时）：月历格用它
                    # 显示「待推送」与「等待 X 10:00 推送」。
                    "planned_push_at": order.get("planned_push_at"),
                    "filled_items": counts.get(int(order["id"]), 0),
                }
            days.append({"menu_day": menu_day, "order": brief})
        submitted = sum(1 for order in orders if str(order["status"]) == SUBMITTED)
        failed = sum(
            1 for order in orders if str(order.get("push_status") or "") == "failed"
        )
        scheduled = sum(
            1 for order in orders if str(order.get("push_status") or "") == "scheduled"
        )
        return {
            "month": year_month,
            "days": days,
            "summary": {
                "days": len(days),
                "orders": len(orders),
                "submitted": submitted,
                "draft": len(orders) - submitted,
                # 等待定时推送的单数（使用日 ≥ 后天的已提交单）：摘要 / 首页
                # 「待推送 N 张」用它（契约 §5）。
                "scheduled": scheduled,
                # 推送失败的单数：页面摘要「· 推送失败 N 张」与顶部提示用它，员工 /
                # 超管不用逐格翻也知道有没有单没发到群里（S1）。
                "failed": failed,
            },
        }

    async def get_day(self, db, actor: StaffMealActor, order_date: str) -> dict:
        """单日：菜单 + 单据（含菜品 / 条目 / 汇总）。"""
        self._require_reader(actor)
        store = self._store(db)
        menu_day = await store.menu_day(order_date)
        order = await store.order_by_date(order_date)
        if menu_day is None and order is None:
            raise StaffMealError(
                "day_not_found",
                f"{order_date} 还没有员工餐菜单：请让超级管理员先导入当月表格",
            )
        detail = await self.order_detail(db, store, order, actor) if order else {
            "order": None,
            "dishes": [],
            "other_items": [],
            "summary": [],
        }
        return {"menu_day": menu_day, **detail}

    async def suggestions(self, db, actor: StaffMealActor, dish_name: str) -> dict:
        """同名菜历史上买过的食材（只有名字与常用单位，不带数量 —— Q9）。"""
        self._require_reader(actor)
        name = str(dish_name or "").strip()
        if not name:
            return {"dish_name": "", "items": []}
        store = self._store(db)
        rows = await store.item_names_for_dish(name, limit=50)
        return {"dish_name": name, "items": rows}

    # ── 导入 ──────────────────────────────────────────────────

    async def _parse(self, data: bytes, year: int, month: int) -> list:
        try:
            return parse_menu_workbook(data, year, month)
        except MenuImportError as exc:
            raise StaffMealError("bad_workbook", str(exc)) from exc

    def _entries(self, days: list) -> list[dict]:
        return [
            {**day.as_row(), "_dishes": _dishes_from_menu_row(day.as_row())}
            for day in days
        ]

    async def preview_import(
        self, db, actor: StaffMealActor, data: bytes, year: int, month: int
    ) -> dict:
        """只解析不写库：上传前让管理员确认月份、天数和已有单据。"""
        self._require_admin(actor)
        days = await self._parse(data, year, month)
        store = self._store(db)
        year_month = _month_label(year, month)
        orders = await store.orders_month(year_month)
        counts = await store.item_counts_for_month(year_month)
        existing = [
            {
                "order_date": order["order_date"],
                "status": order["status"],
                "submitter_name": order.get("submitter_name"),
            }
            for order in orders
        ]
        return {
            "year": int(year),
            "month": year_month,
            "month_number": int(month),
            "days": len(days),
            "freeform_days": sum(1 for day in days if day.freeform),
            "existing_orders": existing,
            "submitted_days": [
                order["order_date"]
                for order in existing
                if str(order.get("status")) == SUBMITTED
            ],
            # 重传会刷新草稿单的菜品快照（已填的食材按菜名保留，菜单里没了的菜连同
            # 其下食材一起删）。把「哪些草稿已经填了东西、填了几项」先摆出来，超管
            # 才能决定要不要现在重传（S9）。
            "draft_with_items": [
                {
                    "order_date": order["order_date"],
                    "filled_items": counts.get(int(order["id"]), 0),
                }
                for order in orders
                if str(order.get("status") or DRAFT) == DRAFT
                and counts.get(int(order["id"]), 0) > 0
            ],
        }

    async def import_month(
        self,
        db,
        actor: StaffMealActor,
        data: bytes,
        year: int,
        month: int,
        *,
        confirm: bool = False,
    ) -> dict:
        """导入整月菜单；不带确认撞上已有单据时抛 `MenuConflictError`。"""
        self._require_admin(actor)
        days = await self._parse(data, year, month)
        store = self._store(db)
        year_month = _month_label(year, month)
        orders = await store.orders_month(year_month)
        if orders and not confirm:
            raise MenuConflictError(
                {
                    "month": year_month,
                    "days": len(days),
                    "existing_orders": len(orders),
                    "submitted_days": [
                        order["order_date"]
                        for order in orders
                        if str(order["status"]) == SUBMITTED
                    ],
                    "existing_days": [order["order_date"] for order in orders],
                }
            )
        result = await store.apply_menu_month(self._entries(days))
        logger.info(
            "员工餐菜单导入 %s：%s 天，新建单 %s，刷新草稿 %s，已提交保留 %s，操作者 %s",
            year_month,
            len(days),
            result["orders_created"],
            result["draft_refreshed"],
            result["submitted_kept"],
            actor.display_name,
        )
        return {
            "year": int(year),
            "month": year_month,
            "month_number": int(month),
            "days": len(days),
            "freeform_days": sum(1 for day in days if day.freeform),
            **result,
        }

    async def update_menu_day(
        self, db, actor: StaffMealActor, order_date: str, fields: Mapping[str, Any]
    ) -> dict:
        """手改某天菜单（导入后临时换菜；Excel 模板变了的兜底）。"""
        self._require_admin(actor)
        store = self._store(db)
        menu_day = await store.menu_day(order_date)
        if menu_day is None:
            raise StaffMealError(
                "day_not_found",
                f"{order_date} 还没有员工餐菜单：先导入当月表格，或让超级管理员在这里新建",
            )
        patch: dict[str, Any] = {}
        for field in MENU_TEXT_FIELDS:
            if field not in fields:
                continue
            value = fields.get(field)
            text = str(value).strip() if value is not None else ""
            if len(text) > 80:
                raise StaffMealError("bad_menu", "单个菜名 / 饮品格不能超过 80 个字")
            patch[field] = text or None
        if "freeform" in fields:
            patch["freeform"] = bool(fields.get("freeform"))
        if not patch:
            raise StaffMealError("bad_menu", "没有要修改的菜单字段")
        merged = {**menu_day, **patch, "menu_date": order_date, "source": "manual"}
        await store.upsert_menu_day(merged)
        order = await store.ensure_order(order_date)
        if str(order.get("status") or DRAFT) != SUBMITTED:
            await store.sync_dishes(order["id"], _dishes_from_menu_row(merged))
        return await self.get_day(db, actor, order_date)

    # ── 单日录入 ──────────────────────────────────────────────

    async def save_order(
        self, db, actor: StaffMealActor, order_date: str, payload: Mapping[str, Any]
    ) -> dict:
        """保存草稿（或改已提交单但不重推；重推要点提交）。"""
        store = self._store(db)
        order = await store.order_by_date(order_date)
        if order is None:
            raise StaffMealError(
                "order_not_found",
                f"{order_date} 还没有采购单：请让超级管理员先导入当月菜单",
            )
        self._require_order_write(actor, order)
        expected_updated_at = payload.get("expected_updated_at")
        if expected_updated_at is not None and str(expected_updated_at).strip():
            # 乐观锁（F14）：页面保存时带上它读到的版本；这里读到的已经不是那一版
            # 就拒绝（两人同一张单先后保存，后者会把前者的改动整体覆盖）。
            if _norm_updated_at(expected_updated_at) != _norm_updated_at(
                order.get("updated_at")
            ):
                raise StaffMealError("conflict", "这张单刚被别人改过：刷新后再保存")
        dishes = await store.dishes_for(order["id"])
        allowed_dish_ids = {int(dish["id"]) for dish in dishes}
        items = normalize_items(payload.get("items"))
        for item in items:
            if item["dish_id"] is not None and item["dish_id"] not in allowed_dish_ids:
                raise StaffMealError(
                    "item_invalid", f"食材「{item['item_name']}」挂的菜品不属于这张单"
                )
        await store.replace_items(order["id"], items)
        updates: dict[str, Any] = {}
        if "remark" in payload:
            remark = str(payload.get("remark") or "").strip()
            if len(remark) > MAX_REMARK:
                raise StaffMealError(
                    "bad_remark", f"单据备注不能超过 {MAX_REMARK} 个字"
                )
            updates["remark"] = remark or None
        if updates:
            order = await store.update_order(order["id"], updates)
        else:
            order = await store.order_by_id(order["id"]) or order
        return await self.order_detail(db, store, order, actor)

    async def _push_submitted(
        self,
        db,
        store: StaffMealStore,
        order: dict,
        *,
        updated: bool,
        actor: StaffMealActor,
        planned_push_at: Optional[str] = None,
    ) -> dict:
        """单据已是 submitted：加一次推送序号、入队、回写推送状态。

        ``planned_push_at`` 非空 = 这一版排定时（使用日 ≥ 后天的提交）：出站行落
        pending 并带计划时刻，10 点前再改单会作废旧 pending、只留最新版。

        入队前先作废这张单更早序号的 pending 行 —— 同一张单同一时刻只该有一版待发。
        """
        order_id = int(order["id"])
        push_seq = await store.bump_push_seq(order_id)
        push_status = "scheduled" if planned_push_at else "pushing"
        await store.update_order(
            order_id,
            {
                "status": SUBMITTED,
                "push_status": push_status,
                "planned_push_at": planned_push_at,
                "push_error": None,
                "pushed_at": None,
            },
        )
        order_date = str(order["order_date"])
        removed = await db.wecom_outbox_discard_pending(
            push.PURCHASE_REFERENCE_PREFIX,
            f"{push.PURCHASE_REFERENCE_PREFIX}:{order_date}:",
        )
        if removed:
            logger.info("员工餐采购单 %s 作废旧待发 %s 条", order_date, removed)
        summary = await store.summary_rows(order_id)
        try:
            outbox_ids = await push.enqueue_purchase_push(
                db,
                order_date=order_date,
                push_seq=push_seq,
                rows=summary,
                updated=updated,
                submitter_name=str(order.get("submitter_name") or ""),
                remark=str(order.get("remark") or ""),
                scheduled_at=planned_push_at,
            )
        except Exception:
            logger.exception("员工餐采购单 %s 推送入队失败", order["order_date"])
            await store.update_order(
                order_id,
                {
                    "push_status": "failed",
                    "push_error": PUSH_ENQUEUE_ERROR,
                    "planned_push_at": None,
                },
            )
            raise StaffMealError("push_failed", PUSH_ENQUEUE_ERROR)
        if not outbox_ids:
            await store.update_order(
                order_id,
                {
                    "push_status": "failed",
                    "push_error": ZERO_SUBSCRIBE_ERROR,
                    "planned_push_at": None,
                },
            )
        return await self.order_detail(
            db, store, await store.order_by_id(order_id) or order, actor
        )

    async def submit_order(
        self, db, actor: StaffMealActor, order_date: str
    ) -> dict:
        """提交（或改后重推）一张单：校验有数量 → 推最终清单。"""
        store = self._store(db)
        order = await store.order_by_date(order_date)
        if order is None:
            raise StaffMealError(
                "order_not_found",
                f"{order_date} 还没有采购单：请让超级管理员先导入当月菜单",
            )
        self._require_order_write(actor, order)
        # 使用日已过（自然日，北京时）：草稿还能改 / 存，但不能再提交或推送 ——
        # 否则群里会收到「11/01 计划」这种已经过期的清单（S6）。
        if str(order_date) < store.today():
            raise StaffMealError(
                "day_expired",
                f"{order_date} 已经过去了：草稿还能改，但不能再提交/推送",
            )
        items = await store.items_for(int(order["id"]))
        if not any(item.get("quantity") is not None for item in items):
            raise StaffMealError(
                "no_items", "至少填一项采购数量再提交（不买的行留空即可）"
            )
        first_submit = str(order.get("status") or DRAFT) != SUBMITTED
        # 使用日 ≥ 后天 → 排到「使用日前一天 10:00」；今天 / 明天 / 已过点 → 立即推。
        planned_push_at = _planned_push_at(str(order_date), store)
        order = await store.update_order(
            order["id"],
            {
                "submitter_employee_id": actor.employee_id,
                "submitter_name": actor.display_name,
                "submitted_at": store.now_iso(),
            },
        )
        detail = await self._push_submitted(
            db,
            store,
            order,
            updated=not first_submit,
            actor=actor,
            planned_push_at=planned_push_at,
        )
        logger.info(
            "员工餐采购单 %s 提交（%s），操作者 %s",
            order_date,
            "重推" if not first_submit else "首次提交",
            actor.display_name,
        )
        return detail

    async def retry_push(self, db, actor: StaffMealActor, order_id: int) -> dict:
        """推送失败后重试：同一份清单再发一版（新推送序号）。"""
        store = self._store(db)
        order = await store.order_by_id(int(order_id))
        if order is None:
            raise StaffMealError("order_not_found", f"采购单 {order_id} 不存在")
        self._require_order_write(actor, order)
        if str(order.get("status") or DRAFT) != SUBMITTED:
            raise StaffMealError("not_submitted", "草稿还没提交，先点提交")
        return await self._push_submitted(
            db, store, order, updated=True, actor=actor, planned_push_at=None
        )

    async def delete_draft(self, db, actor: StaffMealActor, order_id: int) -> dict:
        """删草稿单（已提交不可删 —— Q11 不做作废）。"""
        self._require_admin(actor)
        store = self._store(db)
        order = await store.order_by_id(int(order_id))
        if order is None:
            raise StaffMealError("order_not_found", f"采购单 {order_id} 不存在")
        if str(order.get("status") or DRAFT) != DRAFT:
            raise StaffMealError("already_submitted", "已提交的采购单不能删")
        await store.delete_order(int(order_id))
        return {"ok": True, "order_date": order.get("order_date")}


staff_meal_service = StaffMealService()
