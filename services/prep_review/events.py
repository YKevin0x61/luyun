#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货复盘的事件采集（票 03 / spec §3 决策 2/18/22、§5「事件」）。

只记**真实发生过的动作**，不为了采集去改师傅的动线（决策 18）：手改数量、点完成、
窗口回填结束各追加一条 `prep_plan_events`。没有这些事件，「这条建议到底被不被采纳」
就只能靠猜 —— 快照说给了多少，但没人知道现场有没有改。

三条约定：

- **幂等**（决策 22）：`done` 按 `(plan_day, prep_item_id, kind='done')`（品主键为空时
  退到 `meta_json.entry_id`）、`window_end` 按 `(plan_day, kind='window_end',
  meta_json.window_end)` 先查后插 —— 勾两次、回填
  重跑都不该多出第二条。单 worker 部署下「先查后插」够用，不为它加唯一索引/迁移。
  售罄（`sold_out`）相反：推算出的历史时刻可能因订单迟到而变早，所以同键只留
  **最早**一条，已有更晚的记录会被更早的覆盖，永远不会被改晚。
- **失败隔离**：事件是旁路采集，写失败只 `logger.warning`，绝不能让 PATCH / 回填
  500。写失败时顺手回滚这条连接上的空事务，否则 PostgreSQL 会把后续语句一起毒死。
- **不编造**（决策 9）：查不到历史动作就留空，回填只补它真处理过的窗口。

表名与列口径见 `migrations/pg/0046_prep_review_signals.sql`。
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from database import CHINA_TZ

logger = logging.getLogger(__name__)

#: 事件类型（与 0046 的注释一致）。
MANUAL_EDIT = "manual_edit"
DONE = "done"
SOLD_OUT = "sold_out"
WINDOW_END = "window_end"

#: 需要幂等的事件：同一个键只留最先落库的那条。
_IDEMPOTENT_KINDS = (DONE, WINDOW_END)


def _connection(db):
    """事件表所在的主连接。

    表已在 `db_core/schema.py` 的 `ALL_TABLES` 里登记，走 `db.table()` 拿到的就是
    同一条主连接；这样表名清单仍是唯一事实来源，改名/换库不会静默漏掉这张表。
    """
    return db.table("prep_plan_events").conn


async def _exists(
    db, *, plan_day: str, prep_item_id: Optional[int], kind: str, meta: Dict[str, Any]
) -> bool:
    """同键事件是否已经记过。`window_end` 用 meta 里的窗口结束时刻区分不同窗口。"""
    conn = _connection(db)
    async with conn.cursor() as cursor:
        if kind == DONE:
            # 正常情况下 `prep_item_id` 就是品身份；但 `prep_plan_entries.prep_item_id`
            # 允许为空（配方/品被删后的历史行），那时 `prep_item_id = ?` 永远匹配不上，
            # 勾两次会写两条。这类行退到 meta 里的 `entry_id` 判重 —— 它同样唯一。
            if prep_item_id is not None:
                await cursor.execute(
                    """
                    SELECT 1 FROM prep_plan_events
                     WHERE plan_day = ? AND prep_item_id = ? AND kind = ?
                     LIMIT 1
                    """,
                    (plan_day, prep_item_id, DONE),
                )
            else:
                entry_id = meta.get("entry_id")
                if entry_id is None:
                    return False
                await cursor.execute(
                    """
                    SELECT 1 FROM prep_plan_events
                     WHERE plan_day = ? AND kind = ? AND prep_item_id IS NULL
                       AND meta_json::jsonb ->> 'entry_id' = ?
                     LIMIT 1
                    """,
                    (plan_day, DONE, str(entry_id)),
                )
        elif kind == WINDOW_END:
            window_end = meta.get("window_end")
            if not window_end:
                return False
            await cursor.execute(
                """
                SELECT 1 FROM prep_plan_events
                 WHERE plan_day = ? AND kind = ? AND meta_json::jsonb ->> 'window_end' = ?
                 LIMIT 1
                """,
                (plan_day, WINDOW_END, str(window_end)),
            )
        else:
            return False
        return await cursor.fetchone() is not None


async def _insert_event(
    db,
    *,
    at: str,
    plan_day: str,
    prep_item_id: Optional[int],
    kind: str,
    qty_before: Optional[float],
    qty_after: Optional[float],
    employee_id: Optional[int],
    station: Optional[str],
    meta_json: str,
) -> None:
    conn = _connection(db)
    async with conn.cursor() as cursor:
        await cursor.execute(
            """
            INSERT INTO prep_plan_events (
                at, plan_day, prep_item_id, kind,
                qty_before, qty_after, employee_id, station, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                at,
                plan_day,
                prep_item_id,
                kind,
                qty_before,
                qty_after,
                employee_id,
                station,
                meta_json,
            ),
        )
    await conn.commit()


async def record_event(
    db,
    *,
    at: str,
    plan_day: str,
    kind: str,
    prep_item_id: Optional[int] = None,
    qty_before: Optional[float] = None,
    qty_after: Optional[float] = None,
    employee_id: Optional[int] = None,
    station: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """追加一条备货事件；返回是否真的写了（被幂等挡下或写失败都是 False）。

    **不抛异常**：调用方是 PATCH / 回填这类主流程，事件只是旁路证据。写失败回滚
    本条连接上的空事务后只记 warning，主流程继续。
    """
    meta = meta or {}
    conn = None
    try:
        if kind in _IDEMPOTENT_KINDS and await _exists(
            db, plan_day=plan_day, prep_item_id=prep_item_id, kind=kind, meta=meta
        ):
            return False
        conn = _connection(db)
        await _insert_event(
            db,
            at=at,
            plan_day=plan_day,
            prep_item_id=prep_item_id,
            kind=kind,
            qty_before=qty_before,
            qty_after=qty_after,
            employee_id=employee_id,
            station=station,
            meta_json=json.dumps(meta, ensure_ascii=False),
        )
        return True
    except Exception as exc:  # noqa: BLE001 - 旁路采集，任何异常都不能冒泡到主流程
        logger.warning(
            "⚠️ 备货事件写入失败（kind=%s, plan_day=%s, prep_item_id=%s）：%s",
            kind,
            plan_day,
            prep_item_id,
            exc,
        )
        if conn is not None:
            try:
                await conn.rollback()
            except Exception:  # noqa: BLE001 - 回滚失败没有补救手段
                pass
        return False


def _parse_event_at(raw: Any) -> Optional[datetime]:
    """事件时刻统一按 `CHINA_TZ` 比较；解析不了就不参与先后判断。"""
    text = (raw or "").strip() if isinstance(raw, str) else raw
    if not text:
        return None
    try:
        parsed = text if isinstance(text, datetime) else datetime.fromisoformat(str(text))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(CHINA_TZ)


async def _earliest_sold_out(
    db, *, plan_day: str, prep_item_id: int
) -> Optional[Dict[str, Any]]:
    """同 (plan_day, prep_item_id, kind='sold_out') 里 `at` 最早的那条。

    逐行解析再比时间，不把 `at::timestamptz` 交给数据库：历史事件里混过带
    不同偏移量的 ISO 文本，文本排序会得到错的「最早」，而一行按一个键解析的
    行数很少。
    """
    conn = _connection(db)
    async with conn.cursor() as cursor:
        await cursor.execute(
            """
            SELECT id, at FROM prep_plan_events
             WHERE plan_day = ? AND prep_item_id = ? AND kind = ?
             ORDER BY id
            """,
            (plan_day, prep_item_id, SOLD_OUT),
        )
        rows = await cursor.fetchall()
    earliest = None
    earliest_at = None
    for row in rows:
        parsed = _parse_event_at(row["at"])
        if parsed is None:
            continue
        if earliest_at is None or parsed < earliest_at:
            earliest = dict(row)
            earliest_at = parsed
    return earliest


async def record_sold_out(
    db,
    *,
    at: str,
    plan_day: str,
    prep_item_id: int,
    qty_before: Optional[float] = None,
    qty_after: Optional[float] = None,
    station: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """记录一个备货品的售罄时刻；同键只保留**最早**的一条。

    与 `record_event` 的「先到先得」不同，售罄是推算出来的历史事实：订单迟到、
    回填重跑都可能算出更早的跨越时刻，所以已有记录只会被**更早**的 `at` 覆盖，
    永远不会被改晚。返回是否真的插入或更新了。

    **不抛异常**：与其余事件同一失败隔离姿势，写失败只 warning + 回滚。
    """
    meta = meta or {}
    conn = None
    try:
        existing = await _earliest_sold_out(
            db, plan_day=plan_day, prep_item_id=prep_item_id
        )
        if existing is None:
            conn = _connection(db)
            await _insert_event(
                db,
                at=at,
                plan_day=plan_day,
                prep_item_id=prep_item_id,
                kind=SOLD_OUT,
                qty_before=qty_before,
                qty_after=qty_after,
                employee_id=None,
                station=station,
                meta_json=json.dumps(meta, ensure_ascii=False),
            )
            return True
        new_at = _parse_event_at(at)
        old_at = _parse_event_at(existing["at"])
        if new_at is None or old_at is None or new_at >= old_at:
            return False
        conn = _connection(db)
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE prep_plan_events
                   SET at = ?, qty_before = ?, qty_after = ?, station = ?, meta_json = ?
                 WHERE id = ?
                """,
                (
                    at,
                    qty_before,
                    qty_after,
                    station,
                    json.dumps(meta, ensure_ascii=False),
                    existing["id"],
                ),
            )
        await conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - 旁路采集，任何异常都不能冒泡到主流程
        logger.warning(
            "⚠️ 售罄事件写入失败（plan_day=%s, prep_item_id=%s）：%s",
            plan_day,
            prep_item_id,
            exc,
        )
        if conn is not None:
            try:
                await conn.rollback()
            except Exception:  # noqa: BLE001 - 回滚失败没有补救手段
                pass
        return False
