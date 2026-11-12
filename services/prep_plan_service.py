#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划服务（ADR 0117 重建）。

只回答一个问题：**今天从现在到打烊，各档口还要做多少、还要买多少。**

参考值本身的计算在 `services/prep_plan_forecast.py`（ADR 0137）：那是纯函数，
输入订单行 / 配方 / 备货品 / 参数 / 时钟，输出页面与落库共用的那份 dict。本模块
只负责读设置、查库装配输入、落库与回补，不参与算法：

    基准量 = 近 8 周同星期几、在同样钟点区间内的实际用量分位
    实际用量 = 卖出菜品数量 × 配方系数（semi_finished_rules）
    分位   = 主力品（占全部配料订单行 ≥ 2%）取 p70，其余取 p50
    建议量 = 基准量 × 整店水位系数

整店水位系数 = 近 7 天 / 前 21 天的点单量比（限幅 0.75~1.30，只用当天之前的数据）。
**不加安全缓冲、也不扣库存**：24 小时保质下多做出来就是报废；库存由师傅现场看，
系统不知道（ADR 0121）。

仍然不做的事（ADR 0117 砍掉）：生意日档位加权、已消耗扣减、历史近邻日混合、
把窗口截到打烊、歇业日日历。

营业日按**自然日**切，不按 06:00。备货是「今天做今天卖」的事，师傅不会在凌晨
两点打开看；零点到开店前这段落在自然日边界上的偏差是已知的，不修。
"""

import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from config import KITCHEN_STATIONS
from database import CHINA_TZ
from services.business_day import REVIEW_CLOCK, is_primary_clock
from services.dish_normalize import normalize_dish_name
from services.prep_plan_forecast import (
    FORECAST_HORIZON_HOURS,
    FORECAST_QUANTILE,
    POSITION_STATION_MAP,
    order_datetime,
    compute_reference,
    fit_qty,
    forecast_config,
    resolve_horizon_hours,
)
from services.prep_review import events as prep_review_events

logger = logging.getLogger(__name__)

SAME_WEEKDAY_LOOKBACK = 4
DEFAULT_OPEN = "07:00"
DEFAULT_CLOSE = "21:30"
RUNTIME_SETTINGS_KEY = "prep_plan_runtime"
# 参考值的参数（分位 / 主力判据 / 窗口周数 / 水位限幅）在 prep_plan_forecast；
# 这里只留设置行的键名，DB 读取与异常兜底仍在本模块。
FORECAST_SETTINGS_KEY = "prep_plan_forecast"
FALLBACK_RUNTIME_SETTINGS_KEY = "scraper_runtime"
LOUMIAN_STATION = "loumian"
# 已回填窗口的 `sold_qty` 允许重算的天数（票 08）。迟到订单 / 对账补录会在窗口结束
# 之后才进库，而轨迹列本来就会重算；只冻结 `sold_qty` 会让复盘里的「实际量」长期偏小。
# 3 天足够覆盖补录与隔日对账，再往前就是历史事实，不再动。
ACTUALS_REFRESH_DAYS = 3


def _parse_hhmm(text: Optional[str], fallback: str) -> time:
    for candidate in ((text or "").strip(), fallback):
        try:
            hour_text, minute_text = candidate.split(":", 1)
            return time(int(hour_text), int(minute_text[:2]))
        except (ValueError, AttributeError):
            continue
    return time(7, 0)


def _parse_stamp(text) -> Optional[datetime]:
    """解析库里存的 ISO 时间戳；解析不了就返回 None（宁可不结转，也不认过期货）。"""
    raw = (text or "").strip() if isinstance(text, str) else text
    if not raw:
        return None
    try:
        parsed = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(CHINA_TZ)


def _optional_float(value) -> Optional[float]:
    """NULL 保持 NULL：轨迹列「还没到观测点」不能写成 0（写 0 会被读成「之后没人买」）。"""
    return None if value is None else float(value)


def _loads_json(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _rule_totals_key(rule: dict):
    """实际销量的聚合键：优先 `prep_item_id`，规则没接上主数据时回落 (品名, 单位)。

    `prep_item_id` 是 0035 加、0036 回填的对齐主键。存量规则回填不到（新建规则、
    主数据里暂时没有这个半成品）时仍然要能算，所以保留文本键；两种键在
    `_sold_qty_map` 的同一张表里共存，读取端用 `_sold_qty_for` 一起取。
    """
    prep_item_id = rule.get("prep_item_id")
    if prep_item_id is not None:
        return int(prep_item_id)
    return (rule["semi_name"], rule["unit"])


def _sold_qty_for(totals: Dict[Any, float], item: dict) -> float:
    """按备货品取实际销量，兼容新旧两种键。

    一个备货品可能同时有「接了主数据的规则」和「没接上的规则」，所以两处相加，
    不是二选一。
    """
    if not totals:
        return 0.0
    qty = float(totals.get(int(item["prep_item_id"]), 0.0))
    qty += float(totals.get((item["item_name"], item["unit"]), 0.0))
    return qty


def _hourly_json(hourly: Dict[Any, Dict[str, float]], item: dict) -> str:
    """把逐小时分桶序列化成 0049 的 `hourly_json`（`[{"hour":"07:00","qty":12}]`）。

    与 `_sold_qty_for` 同一套键兼容：接了主数据的规则按 int 键、没接上的按
    (品名, 单位) 文本键，两处相加再按时间排序。小时用 `CHINA_TZ` 的墙上时间
    （`order_datetime` 已归一到 CHINA_TZ），所以求和恒等于同一窗口的 `sold_qty`。
    """
    merged: Dict[str, float] = {}
    for key in (int(item["prep_item_id"]), (item["item_name"], item["unit"])):
        for hour, qty in (hourly.get(key) or {}).items():
            merged[hour] = merged.get(hour, 0.0) + float(qty)
    payload = [{"hour": hour, "qty": round(qty, 2)} for hour, qty in sorted(merged.items())]
    return json.dumps(payload, ensure_ascii=False)


class PrepPlanService:
    """备货计划业务服务。"""

    def __init__(self):
        # 缺 `prep_item_id` 的规则回落到文本键。同一条规则只在第一次加载时警告，
        # 否则整点回填 / 每个窗口都会刷一条同样的日志。
        self._warned_missing_rule_ids: set = set()

    # ------------------------------------------------------------------ 配置

    async def _runtime(self, db) -> Tuple[time, time]:
        """开门 / 打烊时刻。优先 `prep_plan_runtime`，没有就沿用采集时段。"""
        cfg = await db.settings_get_json(RUNTIME_SETTINGS_KEY, None)
        if not isinstance(cfg, dict):
            cfg = await db.settings_get_json(FALLBACK_RUNTIME_SETTINGS_KEY, {}) or {}
        open_t = _parse_hhmm(cfg.get("open") or cfg.get("work_start"), DEFAULT_OPEN)
        close_t = _parse_hhmm(cfg.get("close") or cfg.get("work_end"), DEFAULT_CLOSE)
        if close_t <= open_t:
            close_t = _parse_hhmm(DEFAULT_CLOSE, DEFAULT_CLOSE)
        return open_t, close_t

    # ------------------------------------------------------------------ 基础数据

    async def _load_rules(self, db) -> Tuple[Dict[str, List[dict]], Dict[str, List[dict]]]:
        """菜品 → 备货品换算规则。原名精确匹配 + 归一化名兜底（沿用旧口径）。"""
        exact: Dict[str, List[dict]] = defaultdict(list)
        norm: Dict[str, List[dict]] = defaultdict(list)
        tdb = db.table("semi_finished_rules")
        async with tdb.conn.cursor() as cursor:
            # `prep_item_id` 是规则与备货品主数据的对齐主键（0035 加列、0036 回填）。
            # 实际销量按它聚合，品名改了历史不断；为空时才回落到 (品名, 单位) 文本键。
            await cursor.execute(
                "SELECT dish_name, semi_name, position, factor, unit, prep_item_id "
                "FROM semi_finished_rules"
            )
            rows = await cursor.fetchall()
        for row in rows:
            entry = {
                "semi_name": row["semi_name"] or "",
                "position": row["position"] or "",
                # 只有 NULL 才当 1；存成 0 就是「这一项永远算不出量」，不能静默当 1（那会让
                # 系数填错一直不被发现）。存 0 的品由 compute_suggestions 标 broken_rule。
                "factor": 1.0 if row["factor"] is None else float(row["factor"]),
                "unit": row["unit"] or "",
                "prep_item_id": int(row["prep_item_id"]) if row["prep_item_id"] is not None else None,
            }
            exact[row["dish_name"] or ""].append(entry)
            norm[normalize_dish_name(row["dish_name"] or "")].append(entry)
            if entry["prep_item_id"] is None:
                rule_key = (entry["semi_name"], entry["unit"])
                if rule_key not in self._warned_missing_rule_ids:
                    self._warned_missing_rule_ids.add(rule_key)
                    # 只警告、不中断：老规则（0035 回填不到）仍要能算实际销量，
                    # 但运维要知道是哪个品在靠名字对齐（改名就会断）。
                    logger.warning(
                        "配方规则缺少 prep_item_id，实际销量回落 (品名, 单位) 文本键："
                        "semi_name=%s unit=%s",
                        entry["semi_name"], entry["unit"],
                    )
        return exact, norm

    async def _load_items(self, db) -> List[dict]:
        tdb = db.table("prep_items")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, item_name, station, position, unit, kind, category
                FROM prep_items
                WHERE active = 1
                ORDER BY item_name
                """
            )
            rows = await cursor.fetchall()
        return [
            {
                "prep_item_id": int(row["id"]),
                "item_name": row["item_name"] or "",
                "station": row["station"] or "",
                "position": row["position"] or "",
                "unit": row["unit"] or "",
                "kind": row["kind"] or "made",
                "category": row["category"] or "",
            }
            for row in rows
        ]

    # ------------------------------------------------------------------ 数据
    #
    # 参考值的算法（分位档、整店水位、滚动分段、14 天兜底、status 判定）在
    # services/prep_plan_forecast.py（ADR 0137）；这里只负责把它要的数据取回来。

    async def _fetch_range(self, db, start_dt: datetime, end_dt: datetime) -> List[dict]:
        """一次取出一个时间跨度内的订单行，Python 侧再分桶。"""
        sql = (
            "SELECT dish_name, quantity, order_time FROM orders "
            f"WHERE station != '{LOUMIAN_STATION}' "
            "AND order_time >= ? AND order_time <= ?"
        )
        tdb = db.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(sql, (start_dt.isoformat(), end_dt.isoformat()))
            rows = await cursor.fetchall()
        return [
            {
                "dish_name": row["dish_name"] or "",
                "quantity": float(row["quantity"] or 0),
                "order_time": row["order_time"] or "",
            }
            for row in rows
        ]

    async def _fetch_orders(
        self, db, intervals: List[Tuple[str, datetime, datetime]]
    ) -> List[dict]:
        """按若干区间查订单行（复盘 / 在库量用；建议量走 `_fetch_range`）。

        `source` / `status` 一并取回：实际销量要按堂食 / 外卖拆，退菜行要靠
        `status='退菜'` 认出来（退菜行数量已归零，不能当销量算）。
        """
        clauses: List[str] = []
        params: List[Any] = []
        for _scope, start_dt, end_dt in intervals:
            clauses.append("(order_time >= ? AND order_time <= ?)")
            params.extend([start_dt.isoformat(), end_dt.isoformat()])
        sql = (
            "SELECT dish_name, quantity, order_time, source, status FROM orders "
            f"WHERE station != '{LOUMIAN_STATION}' AND (" + " OR ".join(clauses) + ")"
        )
        tdb = db.table("orders")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(sql, tuple(params))
            rows = await cursor.fetchall()
        return [
            {
                "dish_name": row["dish_name"] or "",
                "quantity": float(row["quantity"] or 0),
                "order_time": row["order_time"] or "",
                "source": (row["source"] or "").strip().lower(),
                "status": row["status"] or "",
            }
            for row in rows
        ]

    # 实现已迁到 prep_plan_forecast（ADR 0137）；复盘模块（prep_review/sold_out.py）
    # 与 `_sold_stats` 仍按实例方法取用，这里留一个别名。
    _order_datetime = staticmethod(order_datetime)

    async def compute_suggestions(
        self, db, now: datetime, horizon_hours: Optional[int] = None
    ) -> dict:
        """算出这份清单的建议量。返回纯计算结果，不落库。

        `horizon_hours` 是参考值覆盖的时间跨度（24 / 48 / 72 / 168，默认 24）：
        窗口一律从**现在**起算，不截到打烊、也不按营业日取整。
        """
        # 先校验跨度：非法值必须在查库之前 400（既有行为），所以这里先解析一次；
        # 纯 module 还会自行解析，两处用的是同一个白名单。
        horizon = resolve_horizon_hours(horizon_hours)
        # 打烊时刻只影响 window.after_close；窗口本身一律从 now 起算（ADR 0122）。
        _open_t, close_t = await self._runtime(db)
        raw = None
        try:
            raw = await db.settings_get_json(FORECAST_SETTINGS_KEY, None)
        except Exception as exc:  # 配置表读不到不该让整页挂掉
            logger.warning("备货预测参数读不到，用默认值: %s", exc)
        cfg = forecast_config(raw)
        today = now.date()
        # 起点与原实现一致：_at(today - lookback_days, 00:00)，即回看窗口首日的零点。
        orders = await self._fetch_range(
            db,
            datetime.combine(
                today - timedelta(days=cfg["lookback_days"]), time(0, 0), tzinfo=CHINA_TZ
            ),
            now,
        )
        exact_rules, norm_rules = await self._load_rules(db)
        items = await self._load_items(db)
        return compute_reference(
            now,
            horizon,
            cfg,
            orders,
            exact_rules,
            norm_rules,
            items,
            close_time=close_t,
        )

    # ------------------------------------------------------------------ 当日计划持久化

    async def _ensure_day(self, db, plan_day: str, window: dict) -> int:
        stamp = datetime.now(CHINA_TZ).isoformat()
        tdb = db.table("prep_plan_days")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id FROM prep_plan_days WHERE plan_day = ?", (plan_day,)
            )
            row = await cursor.fetchone()
            if row:
                day_id = int(row["id"])
                await cursor.execute(
                    "UPDATE prep_plan_days SET window_start = ?, window_end = ?, updated_at = ? WHERE id = ?",
                    (window["start"], window["end"], stamp, day_id),
                )
            else:
                await cursor.execute(
                    """
                    INSERT INTO prep_plan_days (plan_day, window_start, window_end, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (plan_day, window["start"], window["end"], stamp, stamp),
                )
                day_id = int(cursor.lastrowid or 0)
        await tdb.commit()
        return day_id

    async def _persist_entries(self, db, day_id: int, entries: List[dict]) -> None:
        """写当日明细。手改过的 `final_qty` 与勾选状态一律保留。"""
        stamp = datetime.now(CHINA_TZ).isoformat()
        tdb = db.table("prep_plan_entries")
        async with tdb.conn.cursor() as cursor:
            for entry in entries:
                await cursor.execute(
                    """
                    INSERT INTO prep_plan_entries (
                        plan_day_id, prep_item_id, item_name, unit, station, kind,
                        suggested_qty, final_qty, qty_source, basis_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'suggested', ?, ?, ?)
                    ON CONFLICT (plan_day_id, prep_item_id) DO UPDATE SET
                        item_name = excluded.item_name,
                        unit = excluded.unit,
                        station = excluded.station,
                        kind = excluded.kind,
                        suggested_qty = excluded.suggested_qty,
                        final_qty = CASE
                            WHEN prep_plan_entries.qty_source = 'manual'
                            THEN prep_plan_entries.final_qty
                            ELSE excluded.final_qty
                        END,
                        basis_json = excluded.basis_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        day_id,
                        entry["prep_item_id"],
                        entry["item_name"],
                        entry["unit"],
                        entry["station"],
                        entry["kind"],
                        entry["suggested_qty"],
                        entry["suggested_qty"],
                        json.dumps(entry["basis"], ensure_ascii=False),
                        stamp,
                        stamp,
                    ),
                )
        await tdb.commit()

    async def _read_entries(self, db, day_id: int) -> List[dict]:
        tdb = db.table("prep_plan_entries")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, prep_item_id, item_name, unit, station, kind,
                       suggested_qty, final_qty, qty_source,
                       done_at, done_by, basis_json
                FROM prep_plan_entries
                WHERE plan_day_id = ?
                ORDER BY station, kind, suggested_qty DESC, item_name
                """,
                (day_id,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "id": int(row["id"]),
                "prep_item_id": int(row["prep_item_id"] or 0),
                "item_name": row["item_name"] or "",
                "unit": row["unit"] or "",
                "station": row["station"] or "",
                "kind": row["kind"] or "made",
                "suggested_qty": float(row["suggested_qty"] or 0),
                "final_qty": float(row["final_qty"] or 0),
                "qty_source": row["qty_source"] or "suggested",
                "done_at": row["done_at"],
                "done_by": row["done_by"] or "",
                "basis": _loads_json(row["basis_json"]),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------ 对外

    async def get_today(
        self,
        db,
        now: Optional[datetime] = None,
        station: Optional[str] = None,
        employee_id: Optional[int] = None,
        horizon_hours: Optional[int] = None,
    ) -> dict:
        now = now or datetime.now(CHINA_TZ)
        computed = await self.compute_suggestions(db, now, horizon_hours=horizon_hours)
        day_id = await self._ensure_day(db, computed["plan_day"], computed["window"])
        await self._persist_entries(db, day_id, computed["entries"])
        entries = await self._read_entries(db, day_id)

        # 备货计划按**岗位**分组（案板 / 熟笼 / 西饼…），档口只在 POS 侧有意义。
        # `prep_plan_entries` 里只落了 station，岗位从这次算好的 entries 按品补回来。
        source_by_item = {e["prep_item_id"]: e for e in computed["entries"]}
        for entry in entries:
            source = source_by_item.get(entry["prep_item_id"])
            entry["history"] = source["history"] if source else []
            entry["position"] = (source["position"] if source else "") or ""

        staff_station: Optional[str] = None
        needs_station_pick = False
        if employee_id is not None:
            staff_station = await self.get_staff_station(db, employee_id, now)
            needs_station_pick = not staff_station
            if staff_station:
                entries = [e for e in entries if e["position"] == staff_station]
            else:
                # 员工还没选岗位：先让他选，不拿全店清单糊他一脸
                entries = []
        elif station and station not in ("", "all"):
            entries = [e for e in entries if e["position"] == station]

        position_rank = {name: index for index, name in enumerate(computed.get("positions") or [])}
        entries.sort(key=lambda e: (
            position_rank.get(e["position"], len(position_rank)),
            "" if e["position"] else "z",
            e["kind"],
            -e["suggested_qty"],
            e["item_name"],
        ))

        remaining = [e for e in entries if not e["done_at"]]
        done = [e for e in entries if e["done_at"]]
        return {
            "success": True,
            "plan_day": computed["plan_day"],
            "generated_at": computed["generated_at"],
            "window": computed["window"],
            "missing_rule": computed["missing_rule"],
            # 岗位清单（不是档口）：员工选岗位、管理端切换分组都用它。
            "stations": [
                {"id": position, "label": position}
                for position in (computed.get("positions") or [])
            ],
            "staff": employee_id is not None,
            "staff_station": staff_station,
            "needs_station_pick": needs_station_pick,
            "remaining_count": len(remaining),
            "done_count": len(done),
            "entries": entries,
        }

    async def update_entry(
        self,
        db,
        entry_id: int,
        final_qty: Optional[float] = None,
        done: Optional[bool] = None,
        done_by: str = "",
        employee_id: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> dict:
        # ADR 0121：不再登记产量，勾选只是「这项处理过了」的标记。
        tdb = db.table("prep_plan_entries")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT e.id, e.prep_item_id, e.final_qty, e.suggested_qty,
                       i.kind, d.plan_day
                  FROM prep_plan_entries e
                  LEFT JOIN prep_items i ON i.id = e.prep_item_id
                  LEFT JOIN prep_plan_days d ON d.id = e.plan_day_id
                 WHERE e.id = ?
                """,
                (entry_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("entry_not_found")
            staff_position = None
            if employee_id is not None:
                staff_position = await self.get_staff_station(db, employee_id)
                position = await self._item_position(db, int(row["prep_item_id"] or 0))
                if not staff_position or position != staff_position:
                    raise PermissionError("station_forbidden")
            stamp = datetime.now(CHINA_TZ).isoformat()
            if final_qty is not None:
                await cursor.execute(
                    """
                    UPDATE prep_plan_entries
                       SET final_qty = ?, qty_source = 'manual', updated_at = ?
                     WHERE id = ?
                    """,
                    (fit_qty(final_qty, row["kind"] or ""), stamp, entry_id),
                )
            if done is not None:
                await cursor.execute(
                    """
                    UPDATE prep_plan_entries
                       SET done_at = ?, done_by = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (stamp if done else None, done_by if done else "", stamp, entry_id),
                )
        await tdb.commit()

        # 事件采集（票 03）：手改 / 完成是现场真实动作，有就记；写失败只记日志，
        # 不影响这次 PATCH 本身（`record_event` 内部已兜住异常）。
        plan_day = row["plan_day"] or stamp[:10]
        before_qty = float(
            row["final_qty"] if row["final_qty"] is not None else row["suggested_qty"] or 0
        )
        after_qty = before_qty
        if final_qty is not None:
            after_qty = fit_qty(final_qty, row["kind"] or "")
            await prep_review_events.record_event(
                db,
                at=stamp,
                plan_day=plan_day,
                kind=prep_review_events.MANUAL_EDIT,
                prep_item_id=row["prep_item_id"],
                qty_before=before_qty,
                qty_after=after_qty,
                employee_id=employee_id,
                station=staff_position,
                # 原因只回答「为什么改」，不参与计算；不带原因时 meta 仍是空对象，
                # 与改造前的事件形状一致（票 09）。
                meta={"reason": reason} if reason else {},
            )
        if done:
            await prep_review_events.record_event(
                db,
                at=stamp,
                plan_day=plan_day,
                kind=prep_review_events.DONE,
                prep_item_id=row["prep_item_id"],
                qty_before=float(row["suggested_qty"] or 0),
                qty_after=after_qty,
                employee_id=employee_id,
                station=staff_position,
                meta={"done": True, "entry_id": entry_id},
            )
        return {"success": True, "id": entry_id}

    async def get_summary(self, db, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(CHINA_TZ)
        today = await self.get_today(db, now=now)
        buckets: Dict[str, dict] = {}
        for entry in today["entries"]:
            position = entry.get("position") or "uncategorized"
            bucket = buckets.setdefault(
                position,
                {
                    "station": position,
                    "label": "未分类" if position == "uncategorized" else position,
                    "total": 0,
                    "remaining": 0,
                },
            )
            bucket["total"] += 1
            if not entry["done_at"]:
                bucket["remaining"] += 1
        stations = sorted(buckets.values(), key=lambda b: (-b["remaining"], b["station"]))
        return {
            "success": True,
            "plan_day": today["plan_day"],
            "window": today["window"],
            "total": len(today["entries"]),
            "remaining": today["remaining_count"],
            "stations": stations,
        }

    # ------------------------------------------------------------------ 员工档口

    async def _item_position(self, db, prep_item_id: int) -> str:
        """备货品的岗位（员工越权校验用；entries 表里没落岗位）。"""
        tdb = db.table("prep_items")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute("SELECT position FROM prep_items WHERE id = ?", (prep_item_id,))
            row = await cursor.fetchone()
        return (row["position"] if row else "") or ""

    async def get_staff_station(
        self, db, employee_id: int, now: Optional[datetime] = None
    ) -> Optional[str]:
        now = now or datetime.now(CHINA_TZ)
        tdb = db.table("prep_staff_station_picks")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT station FROM prep_staff_station_picks WHERE employee_id = ? AND pick_day = ?",
                (employee_id, now.date().isoformat()),
            )
            row = await cursor.fetchone()
        return (row["station"] if row else None) or None

    async def set_staff_station(
        self, db, employee_id: int, station: str, now: Optional[datetime] = None
    ) -> dict:
        station = (station or "").strip()
        if not station:
            raise ValueError("station_required")
        now = now or datetime.now(CHINA_TZ)
        pick_day = now.date().isoformat()
        tdb = db.table("prep_staff_station_picks")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_staff_station_picks (employee_id, pick_day, station, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (tenant_id, employee_id, pick_day) DO UPDATE SET station = excluded.station
                """,
                (employee_id, pick_day, station, now.isoformat()),
            )
        await tdb.commit()
        return {"success": True, "station": station, "pick_day": pick_day}

    # ------------------------------------------------------------------ 主数据

    async def list_items(self, db) -> List[dict]:
        items = await self._load_items(db)
        return [
            {
                "id": item["prep_item_id"],
                "item_name": item["item_name"],
                "unit": item["unit"],
                "station": item["station"],
                "kind": item["kind"],
                "category": item["category"],
            }
            for item in items
        ]

    async def update_item(
        self, db, item_id: int, kind: Optional[str] = None, station: Optional[str] = None
    ) -> dict:
        sets: List[str] = []
        params: List[Any] = []
        if kind is not None:
            if kind not in ("made", "bought"):
                raise ValueError("invalid_kind")
            sets.append("kind = ?")
            params.append(kind)
        if station is not None:
            sets.append("station = ?")
            params.append(station)
        if not sets:
            raise ValueError("nothing_to_update")
        sets.append("updated_at = ?")
        params.append(datetime.now(CHINA_TZ).isoformat())
        params.append(item_id)
        tdb = db.table("prep_items")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"UPDATE prep_items SET {', '.join(sets)} WHERE id = ?", tuple(params)
            )
        await tdb.commit()
        return {"success": True, "id": item_id}


    # ------------------------------------------------------------------ 快照与复盘（ADR 0123）

    async def _sold_qty_map(self, db, start_dt: datetime, end_dt: datetime) -> Dict[Any, float]:
        """一段时间里「卖出菜品 × 配方系数」的用量，按备货品聚合。复盘用。

        键优先 `prep_item_id`（int），规则缺 id 时回落 `(品名, 单位)` 文本键；
        读取端一律走 `_sold_qty_for`，两种键都能查到。
        """
        stats = await self._sold_stats(db, start_dt, end_dt, tail_hours=0)
        return stats["window"]

    async def _sold_stats(
        self, db, start_dt: datetime, end_dt: datetime, tail_hours: int = 24
    ) -> dict:
        """一个窗口的实际销量统计：窗口内总量 + 轨迹累计 + 渠道 / 退菜 / 逐小时。

        轨迹列是**累计**量（票 04 的验收口径：`sold_next_day >= sold_2h >= sold_1h
        >= sold_qty`），即从 `start_dt` 一直累加到 `end_dt + N 小时`；页面拿
        `sold_1h - sold_qty` 就是窗口结束后新增的那部分。只查一次订单
        `[start_dt, end_dt + tail_hours]`，在 Python 里分桶。

        退菜行（`status='退菜'`）数量已被归零，不能当销量；它们的原份数无可追溯，
        按「每行 1 份 × 配方系数」近似，返回在 `refund` 里。这是已知下限口径，
        跨配方改动会失真，页面会标「近似」。

        返回值：
            {
              "window": {key: qty}, "sold_1h": ..., "sold_2h": ..., "sold_next_day": ...,
              "dine_in": {key: qty}, "delivery": {key: qty}, "refund": {key: qty},
              "hourly": {key: {"07:00": qty}}, "unknown_sources": set[str],
            }
        """
        tail_hours = max(0, int(tail_hours))
        rows = await self._fetch_orders(
            db, [("sold", start_dt, end_dt + timedelta(hours=tail_hours))]
        )
        exact_rules, norm_rules = await self._load_rules(db)

        window: Dict[Any, float] = defaultdict(float)
        sold_1h: Dict[Any, float] = defaultdict(float)
        sold_2h: Dict[Any, float] = defaultdict(float)
        sold_next_day: Dict[Any, float] = defaultdict(float)
        dine_in: Dict[Any, float] = defaultdict(float)
        delivery: Dict[Any, float] = defaultdict(float)
        refund: Dict[Any, float] = defaultdict(float)
        hourly: Dict[Any, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        unknown_sources: set = set()
        marks = (end_dt + timedelta(hours=1), end_dt + timedelta(hours=2),
                 end_dt + timedelta(hours=24))

        for row in rows:
            order_at = self._order_datetime(row["order_time"])
            if order_at is None:
                continue
            dishes = exact_rules.get(row["dish_name"]) or norm_rules.get(
                normalize_dish_name(row["dish_name"]), ()
            )
            if not dishes:
                continue
            is_refund = row["status"] == "退菜"
            quantity = row["quantity"] or 0.0
            # 退菜行数量已归零：每行按 1 份近似（见 docstring）。
            refund_portions = max(quantity, 1.0) if is_refund else 0.0
            for rule in dishes:
                key = _rule_totals_key(rule)
                factor = rule["factor"]
                if is_refund:
                    if order_at <= end_dt:
                        refund[key] += refund_portions * factor
                    continue
                if quantity == 0:
                    continue
                if order_at <= marks[0]:
                    sold_1h[key] += quantity * factor
                if order_at <= marks[1]:
                    sold_2h[key] += quantity * factor
                if order_at <= marks[2]:
                    sold_next_day[key] += quantity * factor
                if order_at <= end_dt:
                    amount = quantity * factor
                    window[key] += amount
                    source = row["source"]
                    if source == "delivery":
                        delivery[key] += amount
                    else:
                        # orders.source 的真实取值只有 dine_in / delivery（见
                        # scraper/order_source.py）；空串是 0035 之前采集的历史行，
                        # 那时只采堂食，所以空与未知值都归堂食，不另立一列。
                        if source and source != "dine_in":
                            unknown_sources.add(source)
                        dine_in[key] += amount
                    hourly[key][order_at.strftime("%H:00")] += amount
        return {
            "window": dict(window),
            "sold_1h": dict(sold_1h),
            "sold_2h": dict(sold_2h),
            "sold_next_day": dict(sold_next_day),
            "dine_in": dict(dine_in),
            "delivery": dict(delivery),
            "refund": dict(refund),
            "hourly": {key: dict(buckets) for key, buckets in hourly.items()},
            "unknown_sources": unknown_sources,
        }

    async def _rules_fingerprint(self, db) -> str:
        """当日配方表的指纹。配方一改，历史窗口的「实际销量」就与当时的预测不同源。"""
        tdb = db.table("semi_finished_rules")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT dish_name, semi_name, unit, factor FROM semi_finished_rules"
            )
            rows = await cursor.fetchall()
        parts = sorted(
            f"{row['dish_name'] or ''}|{row['semi_name'] or ''}|{row['unit'] or ''}|"
            f"{float(row['factor'] or 1):.6f}"
            for row in rows
        )
        return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()

    async def _manual_final_qty(self, db, plan_day: str) -> Dict[int, float]:
        """当日计划里**手改过**的最终量，按 `prep_item_id` 索引。

        只有 `qty_source = 'manual'` 的行才算手改：`_persist_entries` 会把建议量写进
        `final_qty`，光看数值分不出「人改成了 80」还是「算出来本来就是 80」。快照抄的
        是店长当时真正照着做的数，没手改就用自己的 `suggested_qty`（见 `write_snapshot`）。
        """
        tdb = db.table("prep_plan_entries")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT e.prep_item_id, e.final_qty
                FROM prep_plan_entries e
                JOIN prep_plan_days d ON d.id = e.plan_day_id
                WHERE d.plan_day = ? AND e.qty_source = 'manual'
                """,
                (plan_day,),
            )
            rows = await cursor.fetchall()
        return {
            int(row["prep_item_id"]): float(row["final_qty"] or 0)
            for row in rows
            if row["prep_item_id"] is not None
        }

    async def write_snapshot(self, db, now: Optional[datetime] = None) -> dict:
        """整点写一份参考值快照。同一天同一整点重复调用按 upsert 处理。

        快照**固定 24 小时**：复盘页按「一条快照 = 它自己那个 24 小时窗口」对账
        （ADR 0123/0129），页面上的时间跨度切换不改变这条口径，否则同一天不同钟点
        的快照窗口长短不一，复盘没法横向比。

        `final_qty` / `qty_source`（票 08）：当天计划里被店长手改过的品，抄那份最终量；
        其余行 `final_qty = suggested_qty`、`qty_source = 'suggested'`。这样复盘聚合的
        「建议量优先 final_qty」才真的能读到东西——在此之前这两列永远是 NULL。
        """
        now = now or datetime.now(CHINA_TZ)
        stamp = now.replace(minute=0, second=0, microsecond=0)
        computed = await self.compute_suggestions(
            db, stamp, horizon_hours=FORECAST_HORIZON_HOURS
        )
        fingerprint = await self._rules_fingerprint(db)
        manual_final = await self._manual_final_qty(db, computed["plan_day"])
        created = datetime.now(CHINA_TZ).isoformat()
        window = computed["window"]
        tdb = db.table("prep_plan_snapshots")
        written = 0
        async with tdb.conn.cursor() as cursor:
            for entry in computed["entries"]:
                basis = entry.get("basis") or {}
                manual_qty = manual_final.get(int(entry["prep_item_id"]))
                if manual_qty is not None:
                    final_qty = manual_qty
                    qty_source = "manual"
                else:
                    final_qty = entry["suggested_qty"]
                    qty_source = "suggested"
                await cursor.execute(
                    """
                    INSERT INTO prep_plan_snapshots (
                        snapshot_at, window_start, window_end, plan_day,
                        prep_item_id, item_name, unit, station,
                        suggested_qty, final_qty, qty_source, demand_qty,
                        store_factor, quantile, basis_json, rules_fingerprint, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (tenant_id, snapshot_at, prep_item_id) DO NOTHING
                    """,
                    (
                        stamp.isoformat(),
                        window["start"],
                        window["end"],
                        computed["plan_day"],
                        entry["prep_item_id"],
                        entry["item_name"],
                        entry["unit"],
                        entry["station"],
                        entry["suggested_qty"],
                        final_qty,
                        qty_source,
                        basis.get("demand_qty", entry["suggested_qty"]),
                        basis.get("store_factor", 1.0),
                        basis.get("quantile", FORECAST_QUANTILE),
                        json.dumps(basis, ensure_ascii=False),
                        fingerprint,
                        created,
                    ),
                )
                written += 1
        await tdb.commit()
        # 环境信号（节假日 / 天气 / 在岗人数）按天独立存一份。放在快照写完之后、
        # 失败只记日志：复盘页少了外部原因还能看，整点快照不能因为它写不进去。
        try:
            from services.prep_plan_context import prep_plan_context

            await prep_plan_context.refresh(db, computed["plan_day"], now=now)
        except Exception:
            logger.exception(
                "刷新备货复盘环境信号失败 plan_day=%s", computed.get("plan_day")
            )
        return {"success": True, "snapshot_at": stamp.isoformat(), "rows": written}

    async def backfill_actuals(self, db, now: Optional[datetime] = None) -> dict:
        """回填已结束窗口的实际量；最近 `ACTUALS_REFRESH_DAYS` 天的窗口允许重算。

        实际销量 = 卖出菜品 × 配方系数，与参考值同源口径；用当前配方算，
        因此把当前指纹一起存下来，复盘页据此判断两边是否同源。

        一次遍历同时处理两种窗口（票 08）：

        · **待回填**：窗口结束、还没有 actuals 行 —— `INSERT ... ON CONFLICT DO NOTHING`；
        · **待刷新**：已有 actuals 行、且 `window_end` 在最近 `ACTUALS_REFRESH_DAYS`
          天内 —— 插一遍（冲突行不动）再 `UPDATE` 实际量与轨迹列。

        为什么要刷新：迟到订单 / 对账补录会在窗口结束后才进库，原先 `sold_qty` 首次写入
        后就永久冻结，复盘里的「实际量」只会偏小。`rules_fingerprint` 按**重算时的
        当前配方**写 —— 于是配方一改，最近 3 天旧窗口的 `drifted` 标记会跟着变（它们
        本来就该按新口径重算）；更早的窗口保持首次事实不动。

        除窗口内 `sold_qty` 外，再写 0049 加的实际侧列：窗口后 1h / 2h / 24h 的
        **累计**量、堂食 / 外卖拆分、退菜近似量与逐小时曲线。轨迹列只在观测点真正
        到齐时写（窗口刚结束时 1h/2h/24h 还没发生），没到齐不写 —— 写 0 会被误读成
        「之后没人买」。刷新时同理：只覆盖已经到点的列，不把已有值抹回 NULL。
        """
        now = now or datetime.now(CHINA_TZ)
        refresh_cutoff = (now - timedelta(days=ACTUALS_REFRESH_DAYS)).isoformat()
        tdb = db.table("prep_plan_snapshots")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT s.window_start, s.window_end, MIN(s.plan_day) AS plan_day,
                       COUNT(a.id) > 0 AS has_actuals
                FROM prep_plan_snapshots s
                LEFT JOIN prep_plan_actuals a
                       ON a.window_start = s.window_start AND a.window_end = s.window_end
                WHERE s.window_end <= ?
                GROUP BY s.window_start, s.window_end
                HAVING COUNT(a.id) = 0 OR s.window_end >= ?
                ORDER BY s.window_start
                """,
                (now.isoformat(), refresh_cutoff),
            )
            pending = await cursor.fetchall()

        fingerprint = await self._rules_fingerprint(db)
        computed_at = datetime.now(CHINA_TZ).isoformat()
        items = await self._load_items(db)
        written = 0
        refreshed = 0
        adb = db.table("prep_plan_actuals")
        for row in pending:
            start = datetime.fromisoformat(row["window_start"])
            end = datetime.fromisoformat(row["window_end"])
            stats = await self._sold_stats(db, start, end)
            if stats["unknown_sources"]:
                # 归到堂食之前把未知来源点名，避免渠道拆分悄悄吞掉一类订单。
                logger.warning(
                    "备货实际销量遇到未知 order.source，按堂食归集 window=%s..%s values=%s",
                    row["window_start"], row["window_end"],
                    sorted(stats["unknown_sources"]),
                )
            one_hour_ready = now >= end + timedelta(hours=1)
            two_hours_ready = now >= end + timedelta(hours=2)
            next_day_ready = now >= end + timedelta(hours=24)
            is_refresh = bool(row["has_actuals"])
            window_rows = 0
            async with adb.conn.cursor() as cursor:
                for item in items:
                    hourly = _hourly_json(stats["hourly"], item)
                    sold_qty = round(_sold_qty_for(stats["window"], item), 2)
                    sold_1h = round(_sold_qty_for(stats["sold_1h"], item), 2)
                    sold_2h = round(_sold_qty_for(stats["sold_2h"], item), 2)
                    sold_next_day = round(_sold_qty_for(stats["sold_next_day"], item), 2)
                    dine_in = round(_sold_qty_for(stats["dine_in"], item), 2)
                    delivery = round(_sold_qty_for(stats["delivery"], item), 2)
                    refund = round(_sold_qty_for(stats["refund"], item), 2)
                    await cursor.execute(
                        """
                        INSERT INTO prep_plan_actuals (
                            window_start, window_end, prep_item_id, item_name, unit,
                            station, sold_qty, rules_fingerprint, computed_at,
                            sold_1h, sold_2h, sold_next_day,
                            dine_in_qty, delivery_qty, refund_qty, hourly_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (tenant_id, window_start, window_end, prep_item_id)
                        DO NOTHING
                        """,
                        (
                            row["window_start"],
                            row["window_end"],
                            item["prep_item_id"],
                            item["item_name"],
                            item["unit"],
                            item["station"] or POSITION_STATION_MAP.get(item["position"], ""),
                            sold_qty,
                            fingerprint,
                            computed_at,
                            sold_1h if one_hour_ready else None,
                            sold_2h if two_hours_ready else None,
                            sold_next_day if next_day_ready else None,
                            dine_in,
                            delivery,
                            refund,
                            hourly,
                        ),
                    )
                    written += 1
                    window_rows += 1

                    if not is_refresh:
                        continue
                    # 刷新窗口：实际量与轨迹按当前配方 / 当前订单重算；观测点没到的
                    # 轨迹列不进 SET，避免把已经写过的值抹回 NULL。
                    columns = [
                        "sold_qty = ?", "rules_fingerprint = ?", "computed_at = ?",
                        "dine_in_qty = ?", "delivery_qty = ?", "refund_qty = ?",
                        "hourly_json = ?",
                    ]
                    values: List[Any] = [
                        sold_qty, fingerprint, computed_at,
                        dine_in, delivery, refund, hourly,
                    ]
                    if one_hour_ready:
                        columns.append("sold_1h = ?")
                        values.append(sold_1h)
                    if two_hours_ready:
                        columns.append("sold_2h = ?")
                        values.append(sold_2h)
                    if next_day_ready:
                        columns.append("sold_next_day = ?")
                        values.append(sold_next_day)
                    await cursor.execute(
                        "UPDATE prep_plan_actuals SET " + ", ".join(columns) + """
                        WHERE tenant_id = 1
                          AND window_start = ? AND window_end = ?
                          AND prep_item_id = ?
                        """,
                        (
                            *values,
                            row["window_start"],
                            row["window_end"],
                            int(item["prep_item_id"]),
                        ),
                    )
                    refreshed += cursor.rowcount
            await adb.commit()

            if is_refresh:
                continue
            # 事件采集（票 03）：一个窗口首次回填完成 = 一条窗口级事件。`prep_item_id`
            # 留空（不对应某一个品），窗口边界与品数放 meta；刷新窗口不再记事件，
            # 否则每小时的 3 天重算会把同一条「窗口结束」刷成几十条。
            await prep_review_events.record_event(
                db,
                at=computed_at,
                plan_day=row["plan_day"] or computed_at[:10],
                kind=prep_review_events.WINDOW_END,
                meta={
                    "window_start": row["window_start"],
                    "window_end": row["window_end"],
                    "items": window_rows,
                },
            )

        # 入口沿用整点回填（main.py 的常驻任务），不另起循环：历史窗口的轨迹列在
        # 24 小时观测点到齐后由这里补，重复跑只更新新列。
        trajectories = await self.backfill_actual_trajectories(db, now=now)
        return {
            "success": True,
            "windows": len(pending),
            "rows": written,
            "refreshed": refreshed,
            "trajectories": trajectories,
        }

    async def backfill_actual_trajectories(
        self, db, days: int = 90, now: Optional[datetime] = None
    ) -> dict:
        """近 `days` 天内已回填窗口的轨迹列补齐（可重入、不覆盖 `sold_qty`）。

        观测点**逐个**判断：窗口结束后 1h 只补 `sold_1h`，2h 再补 `sold_2h`，
        24h 补 `sold_next_day`；没到的列不写（写 0 会被误读成「之后没人买」）。
        渠道 / 退菜 / 逐小时是窗口内的确定值，随第一次补齐一起写。

        只 `UPDATE` 0049 新加的那几列：`sold_qty`、`rules_fingerprint`、`computed_at`
        都是首次回填的事实，重跑不改。三列都补齐后该行不再入选
        （`sold_1h IS NULL ...` 条件），所以每小时的常驻回填不会反复扫同一批窗口。
        """
        now = now or datetime.now(CHINA_TZ)
        days = max(1, int(days))
        cutoff = (now - timedelta(days=days)).isoformat()
        adb = db.table("prep_plan_actuals")
        async with adb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT window_start, window_end, prep_item_id, item_name, unit
                FROM prep_plan_actuals
                WHERE window_end <= ?
                  AND window_end >= ?
                  AND (sold_1h IS NULL OR sold_2h IS NULL OR sold_next_day IS NULL)
                ORDER BY window_start
                """,
                (now.isoformat(), cutoff),
            )
            rows = await cursor.fetchall()
        if not rows:
            return {"success": True, "windows": 0, "rows": 0, "pending": 0}

        by_window: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
        for row in rows:
            by_window[(row["window_start"], row["window_end"])].append(dict(row))

        updated = 0
        skipped = 0
        for (window_start, window_end), window_rows in by_window.items():
            start = datetime.fromisoformat(window_start)
            end = datetime.fromisoformat(window_end)
            one_ready = now >= end + timedelta(hours=1)
            two_ready = now >= end + timedelta(hours=2)
            next_day_ready = now >= end + timedelta(hours=24)
            if not (one_ready or two_ready or next_day_ready):
                skipped += 1
                continue
            stats = await self._sold_stats(db, start, end)
            async with adb.conn.cursor() as cursor:
                for item in window_rows:
                    columns: List[str] = []
                    values: List[Any] = []
                    if one_ready:
                        columns.append("sold_1h = ?")
                        values.append(round(_sold_qty_for(stats["sold_1h"], item), 2))
                    if two_ready:
                        columns.append("sold_2h = ?")
                        values.append(round(_sold_qty_for(stats["sold_2h"], item), 2))
                    if next_day_ready:
                        columns.append("sold_next_day = ?")
                        values.append(round(_sold_qty_for(stats["sold_next_day"], item), 2))
                    columns.extend([
                        "dine_in_qty = ?", "delivery_qty = ?",
                        "refund_qty = ?", "hourly_json = ?",
                    ])
                    values.extend([
                        round(_sold_qty_for(stats["dine_in"], item), 2),
                        round(_sold_qty_for(stats["delivery"], item), 2),
                        round(_sold_qty_for(stats["refund"], item), 2),
                        _hourly_json(stats["hourly"], item),
                    ])
                    await cursor.execute(
                        "UPDATE prep_plan_actuals SET " + ", ".join(columns) + """
                        WHERE tenant_id = 1
                          AND window_start = ? AND window_end = ?
                          AND prep_item_id = ?
                        """,
                        (
                            *values,
                            window_start,
                            window_end,
                            int(item["prep_item_id"]),
                        ),
                    )
                    updated += cursor.rowcount
            await adb.commit()
        return {
            "success": True,
            "windows": len(by_window),
            "rows": updated,
            "pending": skipped,
        }

    async def get_snapshot_day(self, db, day: str) -> dict:
        """某一天的快照 + 实际。复盘页的唯一数据源。

        ADR 0129：**按快照钟点分组**给出，而不是按品聚合。同一品在 07:00 与 19:00 是
        两个完全不同的 24 小时窗口，把一天的 24 个窗口平均掉会让误差互相抵消——
        逐条预测全错也能平均出一个「偏差很小」的假象。复盘必须一条快照对一条实际。
        """
        tdb = db.table("prep_plan_snapshots")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT s.snapshot_at, s.window_start, s.prep_item_id, s.item_name,
                       s.unit, s.station, s.suggested_qty, s.final_qty, s.store_factor,
                       s.rules_fingerprint, s.basis_json,
                       a.sold_qty, a.sold_1h, a.sold_2h, a.sold_next_day,
                       a.refund_qty, a.rules_fingerprint AS actual_fingerprint
                FROM prep_plan_snapshots s
                LEFT JOIN prep_plan_actuals a
                       ON a.window_start = s.window_start
                      AND a.window_end = s.window_end
                      AND a.prep_item_id = s.prep_item_id
                WHERE s.plan_day = ?
                ORDER BY s.item_name, s.snapshot_at
                """,
                (day,),
            )
            rows = await cursor.fetchall()

        # 当天首次售罄时刻（票 05 的 `sold_out` 事件）。直接读表、不依赖采集模块：
        # 事件是旁路写入的，复盘要能读出「几点卖完」；没有事件就是 null，不能编。
        sold_out_at: Dict[int, str] = {}
        edb = db.table("prep_plan_events")
        async with edb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT prep_item_id, MIN(at) AS sold_out_at
                FROM prep_plan_events
                WHERE plan_day = ? AND kind = 'sold_out'
                GROUP BY prep_item_id
                """,
                (day,),
            )
            for event in await cursor.fetchall():
                if event["prep_item_id"] is not None:
                    sold_out_at[int(event["prep_item_id"])] = event["sold_out_at"]

        by_item: Dict[Tuple[str, str], dict] = {}
        for row in rows:
            key = (row["item_name"] or "", row["unit"] or "")
            bucket = by_item.setdefault(
                key,
                {
                    "item_name": key[0],
                    "unit": key[1],
                    "station": row["station"] or "",
                    "prep_item_id": row["prep_item_id"],
                    "sold_out_at": None,
                    "hours": [],
                },
            )
            if bucket["prep_item_id"] is None and row["prep_item_id"] is not None:
                bucket["prep_item_id"] = row["prep_item_id"]
            same_rules = (row["rules_fingerprint"] or "") == (row["actual_fingerprint"] or "")
            bucket["hours"].append(
                {
                    "snapshot_at": row["snapshot_at"],
                    "suggested_qty": float(row["suggested_qty"] or 0),
                    "final_qty": row["final_qty"],
                    "sold_qty": None if row["sold_qty"] is None else float(row["sold_qty"]),
                    "sold_1h": _optional_float(row["sold_1h"]),
                    "sold_2h": _optional_float(row["sold_2h"]),
                    "sold_next_day": _optional_float(row["sold_next_day"]),
                    "refund_qty": _optional_float(row["refund_qty"]),
                    "store_factor": float(row["store_factor"] or 1),
                    "same_rules": same_rules if row["sold_qty"] is not None else None,
                }
            )
        for bucket in by_item.values():
            prep_item_id = bucket.get("prep_item_id")
            bucket["sold_out_at"] = (
                sold_out_at.get(int(prep_item_id)) if prep_item_id is not None else None
            )
        items = sorted(by_item.values(), key=lambda b: (-len(b["hours"]), b["item_name"]))
        snapshots = self._snapshot_groups(rows, sold_out_at)
        return {
            "success": True,
            "day": day,
            "items": items,
            "snapshots": snapshots,
            "primary_clock": REVIEW_CLOCK if any(
                is_primary_clock(g["clock"]) for g in snapshots
            ) else (snapshots[0]["clock"] if snapshots else None),
            "rows": len(rows),
        }

    @staticmethod
    def _snapshot_groups(rows, sold_out_at: Optional[Dict[int, str]] = None) -> List[dict]:
        """把一天的快照按「快照钟点」分组，并给每组算一份可判读的指标。

        一条快照 = 一个 24 小时窗口的一次预测，只能跟**它自己那个窗口**的实际销量比。
        同日不同钟点的窗口高度重叠（相邻两条重叠 23 小时），所以绝不能再平均。
        """
        groups: Dict[str, dict] = {}
        for row in rows:
            clock = str(row["snapshot_at"] or "")[11:16]
            bucket = groups.setdefault(clock, {"clock": clock, "items": []})
            try:
                basis = json.loads(row["basis_json"]) if row["basis_json"] else {}
            except (TypeError, ValueError):
                basis = {}
            sold = None if row["sold_qty"] is None else float(row["sold_qty"])
            prep_item_id = row["prep_item_id"]
            bucket["items"].append(
                {
                    "item_name": row["item_name"] or "",
                    "unit": row["unit"] or "",
                    "station": row["station"] or "",
                    "suggested_qty": float(row["suggested_qty"] or 0),
                    "final_qty": None if row["final_qty"] is None else float(row["final_qty"]),
                    "sold_qty": sold,
                    "sold_1h": _optional_float(row["sold_1h"]),
                    "sold_2h": _optional_float(row["sold_2h"]),
                    "sold_next_day": _optional_float(row["sold_next_day"]),
                    "refund_qty": _optional_float(row["refund_qty"]),
                    "sold_out_at": (sold_out_at or {}).get(int(prep_item_id))
                    if prep_item_id is not None
                    else None,
                    "store_factor": float(row["store_factor"] or 1),
                    "quantile": basis.get("quantile"),
                    "is_major": bool(basis.get("is_major")),
                    "status": basis.get("status") or "ok",
                    "same_rules": bool(
                        sold is not None
                        and (row["rules_fingerprint"] or "")
                        == (row["actual_fingerprint"] or "")
                    )
                    if sold is not None
                    else None,
                }
            )
        out = []
        for clock in sorted(groups):
            group = groups[clock]
            group["items"].sort(key=lambda i: -i["suggested_qty"])
            group["summary"] = _summarise_snapshot(group["items"])
            out.append(group)
        return out


def _summarise_snapshot(items: List[dict]) -> dict:
    """够卖率 / 缺货量 / 多出量 / 参考·实际比。窗口未结束或配方变过的行不进指标。"""
    # 配方改过的行不剔除，只计数标注：剔除会让「那天改过一次配方」的整天变成空白，
    # 复盘页就没有数据可看（实际销量是按当前配方重算的，误差方向已知）。
    usable = [i for i in items if i["sold_qty"] is not None]
    pending = sum(1 for i in items if i["sold_qty"] is None)
    drifted = sum(1 for i in usable if not i["same_rules"])
    if not usable:
        return {
            "measured": 0,
            "pending": pending,
            "drifted": drifted,
            "covered": None,
            "sold_total": 0.0,
            "suggested_total": 0.0,
            "shortfall": 0.0,
            "overfill": 0.0,
            "shortfall_ratio": None,
            "overfill_ratio": None,
            "ref_ratio": None,
        }
    sold_total = sum(i["sold_qty"] for i in usable)
    sugg_total = sum(i["suggested_qty"] for i in usable)
    shortfall = sum(max(0.0, i["sold_qty"] - i["suggested_qty"]) for i in usable)
    overfill = sum(max(0.0, i["suggested_qty"] - i["sold_qty"]) for i in usable)
    covered = sum(1 for i in usable if i["suggested_qty"] >= i["sold_qty"])
    # 实际一件没卖时比例没有分母：宁可是 None（页面显示「—」），
    # 也不要拿 0 去伪装成「参考值是实际的无穷倍」或「缺货 0%」。
    denom = sold_total if sold_total > 0 else None
    return {
        "measured": len(usable),
        "pending": pending,
        "drifted": drifted,
        "covered": covered / len(usable),
        "sold_total": round(sold_total, 1),
        "suggested_total": round(sugg_total, 1),
        "shortfall": round(shortfall, 1),
        "overfill": round(overfill, 1),
        "shortfall_ratio": None if denom is None else shortfall / denom,
        "overfill_ratio": None if denom is None else overfill / denom,
        "ref_ratio": None if denom is None else sugg_total / denom,
    }


prep_plan_service = PrepPlanService()
