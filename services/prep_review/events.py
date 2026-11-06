#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货复盘的事件采集（票 03 / spec §3 决策 2/18/22、§5「事件」）。

只记**真实发生过的动作**，不为了采集去改师傅的动线（决策 18）：手改数量、点完成、
窗口回填结束各追加一条 `prep_plan_events`。没有这些事件，「这条建议到底被不被采纳」
就只能靠猜 —— 快照说给了多少，但没人知道现场有没有改。

三条约定：

- **幂等**（决策 22）：`done` 按 `(plan_day, prep_item_id, kind='done')`、`window_end`
  按 `(plan_day, kind='window_end', meta_json.window_end)` 先查后插 —— 勾两次、回填
  重跑都不该多出第二条。单 worker 部署下「先查后插」够用，不为它加唯一索引/迁移。
- **失败隔离**：事件是旁路采集，写失败只 `logger.warning`，绝不能让 PATCH / 回填
  500。写失败时顺手回滚这条连接上的空事务，否则 PostgreSQL 会把后续语句一起毒死。
- **不编造**（决策 9）：查不到历史动作就留空，回填只补它真处理过的窗口。

表名与列口径见 `migrations/pg/0046_prep_review_signals.sql`。
"""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: 事件类型（与 0046 的注释一致）。
MANUAL_EDIT = "manual_edit"
DONE = "done"
SOLD_OUT = "sold_out"
WINDOW_END = "window_end"

#: 需要幂等的事件：同一个键只留最先落库的那条。
_IDEMPOTENT_KINDS = (DONE, WINDOW_END)


def _connection(db):
    """取主连接。

    `prep_plan_events` 是 0046 新加的表，还没进 `db_core/schema.py` 的表名清单，
    `db.table()` 取不到它 —— 与 `services/prep_review/retention.py` 同一姿势，走
    `DatabaseManager` 持着的那条主连接。
    """
    return db._conn


async def _exists(
    db, *, plan_day: str, prep_item_id: Optional[int], kind: str, meta: Dict[str, Any]
) -> bool:
    """同键事件是否已经记过。`window_end` 用 meta 里的窗口结束时刻区分不同窗口。"""
    conn = _connection(db)
    async with conn.cursor() as cursor:
        if kind == DONE:
            await cursor.execute(
                """
                SELECT 1 FROM prep_plan_events
                 WHERE plan_day = ? AND prep_item_id = ? AND kind = ?
                 LIMIT 1
                """,
                (plan_day, prep_item_id, DONE),
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
