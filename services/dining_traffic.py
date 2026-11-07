#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""客流 / 翻台按小时落库（票 10 / `.scratch/prep-review/spec.md` §12 的 P2）。

桌台快照（`tables`）是**覆盖式**的「此刻状态」：每轮采集先 `DELETE` 再整批
`INSERT`，历史留不下来。复盘要回答「那天为什么备多/备少」，就需要一条按小时的
客流曲线，所以这里把每轮桌台快照汇总成三个数，写进 `dining_traffic`：

- `in_seat_people`：各行人数之和（`people`，移动银台是 `people_qty`）；
- `open_tables`：有客的桌数（人数或金额大于 0）；
- `total_tables`：本轮快照的行数。

**一小时一行**：`snapshot_at` 截断到整点，同小时再采只刷新数值
（`ON CONFLICT (tenant_id, snapshot_at) DO UPDATE`）。截断而不是「只在整点写」，
是因为采集是分钟级的常驻循环：整点那一刻可能正好失败，漏掉就永远补不回来。

调用方是 `scraper/restaurant_scraper.py::run_cycle` 的热路径 —— 一次 upsert、
不查库、不做聚合查询；失败由调用方 try/except 兜成一行 warning，不能中断采集。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from database import CHINA_TZ

logger = logging.getLogger(__name__)

_INSERT_SQL = """
INSERT INTO dining_traffic (
    tenant_id, snapshot_at, plan_day,
    in_seat_people, open_tables, total_tables, created_at
) VALUES (1, ?, ?, ?, ?, ?, ?)
ON CONFLICT (tenant_id, snapshot_at) DO UPDATE SET
    plan_day = excluded.plan_day,
    in_seat_people = excluded.in_seat_people,
    open_tables = excluded.open_tables,
    total_tables = excluded.total_tables
"""


def _as_int(value: Any) -> int:
    """桌台字段来自 POS 解析，可能是 int / float / 数字字符串 / None。"""
    if value is None:
        return 0
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _people_of(table: Dict[str, Any]) -> int:
    """在座人数：cy7mm 用 `people`，移动银台用 `people_qty`（见 `yintai_session`）。"""
    if table.get("people") is not None:
        return _as_int(table.get("people"))
    return _as_int(table.get("people_qty"))


def summarise(tables_data: List[Dict[str, Any]]) -> Dict[str, int]:
    """把一轮桌台快照汇总成 `dining_traffic` 的三个数。

    开台口径跟随仓库既有读法（`db_core/tables_repo.py::get_table_snapshot_stats`）：
    `status = 'occupied' OR amount > 0 OR people > 0` —— 空桌是「金额与人数都为 0」。
    """
    people = 0
    open_tables = 0
    for table in tables_data or []:
        row_people = _people_of(table)
        people += row_people
        if row_people > 0 or _as_int(table.get("amount")) > 0:
            open_tables += 1
    return {
        "in_seat_people": people,
        "open_tables": open_tables,
        "total_tables": len(tables_data or []),
    }


async def record_hourly(
    db,
    tables_data: Optional[List[Dict[str, Any]]],
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """把这一轮桌台快照写进 `dining_traffic`（同一小时 upsert）。

    `tables_data is None`（取材失败）与 `[]`（取材正常但没桌）都**不写**：前者是
    没有数据，后者是「这一刻确实没有桌台」，写一行 0/0/0 会把「取材失败」伪装成
    「空场」，复盘读到的曲线就会凭假数据下结论。

    返回写入的行（截断后的 `snapshot_at` / `plan_day` / 三个汇总数）；跳过时返回
    None。数据库异常不在这里吞：调用方（`run_cycle`）负责记 warning 并继续采集。
    """
    if not tables_data:
        return None

    moment = now or datetime.now(CHINA_TZ)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CHINA_TZ)
    else:
        moment = moment.astimezone(CHINA_TZ)
    snapshot_at = moment.replace(minute=0, second=0, microsecond=0)

    summary = summarise(tables_data)
    row = {
        "snapshot_at": snapshot_at.isoformat(),
        "plan_day": snapshot_at.date().isoformat(),
        **summary,
    }

    conn = db.table("dining_traffic").conn
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                _INSERT_SQL,
                (
                    row["snapshot_at"],
                    row["plan_day"],
                    row["in_seat_people"],
                    row["open_tables"],
                    row["total_tables"],
                    moment.isoformat(),
                ),
            )
        await conn.commit()
    except Exception:
        try:
            await conn.rollback()
        except Exception:  # noqa: BLE001 - 回滚失败没有补救手段
            pass
        raise

    return row


__all__ = ["record_hourly", "summarise"]
