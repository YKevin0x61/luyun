#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DatabaseManager 的异常明细（refund_records 表）职责。

数据来自商龙云移动报表的 ``exceptiondetail/getexceptionco``（退单）与
``getexceptionpresent``（赠单），由 ``scraper/refund_tracker.py`` 同步；这里只管
落库与查询，不解析接口。两类靠 ``kind`` 区分（``co`` / ``present``）。

写入口径是「整单替换」：接口按账单返回，一次拉到的 children 就是这单的全部
明细，所以同步侧先 ``refund_records_replace_bill`` 删掉旧行再插新行。这样重复
同步同一天不会产生重复行，POS 撤销一笔退单后本地也能跟着消失——比按字段
``ON CONFLICT`` 增量去重更贴近接口的语义。
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 楼面（loumian）的菜品不进明细：POS 的 itemName 带「(普通)」「(推荐好茶)」这类口味前缀，
# 而 dish_stations.dish_name 存的是去掉前缀的菜名，所以比对前先把行首括号抹掉。
# 过滤放在查询侧而不是同步侧——档口映射后来改过，历史退单也要跟着变。
_LOUMIAN_FILTER_SQL = r"""
      AND NOT EXISTS (
          SELECT 1 FROM "dish_stations" AS ds
          WHERE ds."station_id" = 'loumian'
            AND ds."dish_name" = regexp_replace(
                "refund_records"."item_name", '^\([^)]*\)', ''
            )
      )"""

_INSERT_SQL = """
INSERT INTO "refund_records" (
    "shop_id", "kind", "bs_id", "bs_code", "source_line", "point_name",
    "item_name", "qty", "money", "reason", "operator", "auth_emp",
    "refund_date", "refund_time", "co_status",
    "settle_time", "open_time", "settle_man", "synced_at"
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _as_float(value) -> float:
    """asyncpg 的 NUMERIC 回来是 Decimal，JSON 与前端都按 number 用。"""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class _RefundRecordsRepoMixin:
    """退菜明细的整单替换与分页查询。"""

    async def refund_records_replace_bill(
        self, *, shop_id: str, bs_id: int, rows: List[Dict], kind: str = "co"
    ) -> int:
        """用 ``rows`` 整体替换一张退单/赠单的明细，返回落库行数。

        先删后插在同一事务里，读侧看不到中间态。``kind`` 参与删除条件：同一张
        账单可能既退菜又赠菜，两个接口各写一批行，不能互相删。
        """
        tdb = self._connection.table("refund_records")
        try:
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    'DELETE FROM "refund_records"'
                    ' WHERE "shop_id" = ? AND "kind" = ? AND "bs_id" = ?',
                    (str(shop_id), str(kind or "co"), int(bs_id)),
                )
            if rows:
                await tdb.conn.executemany(
                    _INSERT_SQL,
                    [
                        (
                            str(shop_id),
                            str(kind or "co"),
                            int(bs_id),
                            row.get("bs_code") or "",
                            int(row.get("source_line") or 0),
                            row.get("point_name") or "",
                            row.get("item_name") or "",
                            float(row.get("qty") or 0),
                            float(row.get("money") or 0),
                            row.get("reason") or "",
                            row.get("operator") or "",
                            row.get("auth_emp") or "",
                            row.get("refund_date") or "",
                            row.get("refund_time") or "",
                            int(row.get("co_status") or 0),
                            row.get("settle_time") or "",
                            row.get("open_time") or "",
                            row.get("settle_man") or "",
                            row.get("synced_at") or "",
                        )
                        for row in rows
                    ],
                )
            await tdb.commit()
            return len(rows)
        except Exception as e:
            logger.error("❌ 替换退菜明细失败 bs_id=%s: %s", bs_id, e)
            raise

    async def refund_records_detail(
        self,
        *,
        start_date: str,
        end_date: str,
        page: int = 1,
        page_size: int = 50,
        kind: Optional[str] = None,
    ) -> Dict:
        """按营业日区间分页取明细（退菜时刻倒序）。``kind`` 为空表示两类都要。"""
        page = max(1, int(page or 1))
        page_size = min(200, max(1, int(page_size or 50)))
        kind_clause = ""
        params: List = [start_date, end_date]
        if kind and kind in ("co", "present"):
            kind_clause = ' AND "kind" = ?'
            params.append(kind)
        tdb = self._connection.table("refund_records")
        try:
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    'SELECT COUNT(*) FROM "refund_records" WHERE "refund_date" BETWEEN ? AND ?'
                    + kind_clause
                    + _LOUMIAN_FILTER_SQL,
                    tuple(params),
                )
                total_row = await cursor.fetchone()
                total = int(total_row[0] or 0) if total_row else 0
                # R23：页码超过末页时落回最后一页；以前会返回空 items 但 total 仍 > 0，
                # 前端看起来像「有 120 条却一条也翻不到」。
                page_count = max(1, (total + page_size - 1) // page_size)
                page = min(page, page_count)
                offset = (page - 1) * page_size

                await cursor.execute(
                    f"""
                    SELECT "id", "kind", "bs_code", "point_name", "item_name", "qty", "money",
                           "reason", "operator", "auth_emp", "refund_date", "refund_time",
                           "co_status", "settle_man"
                    FROM "refund_records"
                    WHERE "refund_date" BETWEEN ? AND ?{kind_clause}{_LOUMIAN_FILTER_SQL}
                    ORDER BY "refund_time" DESC, "id" DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params) + (page_size, offset),
                )
                rows = await cursor.fetchall()
        except Exception as e:
            logger.error("❌ 查询退菜明细失败: %s", e)
            raise

        items = []
        for row in rows:
            item = dict(row)
            item["qty"] = _as_float(item.get("qty"))
            item["money"] = _as_float(item.get("money"))
            items.append(item)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def refund_records_summary(
        self, *, start_date: str, end_date: str, kind: Optional[str] = None
    ) -> Dict:
        """区间内汇总：单数、明细行数、份数、金额。``kind`` 为空表示两类合计。"""
        kind_clause = ""
        params: List = [start_date, end_date]
        if kind and kind in ("co", "present"):
            kind_clause = ' AND "kind" = ?'
            params.append(kind)
        tdb = self._connection.table("refund_records")
        try:
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"""
                    SELECT COUNT(DISTINCT "bs_id") AS bill_count,
                           COUNT(*) AS line_count,
                           COALESCE(SUM("qty"), 0) AS total_qty,
                           COALESCE(SUM("money"), 0) AS total_money
                    FROM "refund_records"
                    WHERE "refund_date" BETWEEN ? AND ?{kind_clause}{_LOUMIAN_FILTER_SQL}
                    """,
                    tuple(params),
                )
                row = await cursor.fetchone()
        except Exception as e:
            logger.error("❌ 退菜汇总失败: %s", e)
            raise

        if not row:
            return {"bill_count": 0, "line_count": 0, "total_qty": 0.0, "total_money": 0.0}
        return {
            "bill_count": int(row["bill_count"] or 0),
            "line_count": int(row["line_count"] or 0),
            "total_qty": _as_float(row["total_qty"]),
            "total_money": _as_float(row["total_money"]),
        }
