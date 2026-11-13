#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""异常明细同步：商龙云移动报表「异常汇总」的退单与赠单。

- 退单 ``exceptiondetail/getexceptionco``：菜品级退菜明细（份数 / 金额 / 原因 /
  操作人 / 授权人 / 时刻）。
- 赠单 ``exceptiondetail/getexceptionpresent``：同样的账单 + children 结构，只是
  原因变营销/赠送，且没有阶段概念。

订单表里的退菜行是爬虫按「连续缺席 + 金额变小」差分推断的，份数在软取消时被
归零、原因与操作人拿不到，POS 自己回滚的退菜更是抓不到；这个接口直接给菜品级
退单明细（份数 / 金额 / 原因 / 操作人 / 授权人 / 时刻），是更权威的一手数据。

口径：只写 ``refund_records``（迁移 0056，``kind`` 区分 co/present），**不写 orders** —— KDS 与备货仍按
``orders.status='退菜'`` 的实时口径工作，往 orders 里插第二来源会让同一笔退菜
被两条链路各记一次。

幂等：接口按账单返回（``dataList`` 一项 = 一张退单，``children`` 是明细 JSON
字符串），所以按 ``bs_id`` 整单替换——先删该单旧行再插新行。重复同步同一天不会
产生重复行，POS 撤销一笔退单后本地也能跟着消失。

频率：挂在一轮采集里但自行节流（``REFUND_POLL_INTERVAL_SECONDS``）。首次运行
补最近 ``REFUND_BACKFILL_DAYS`` 天，之后只回看 ``REFUND_INCREMENTAL_DAYS`` 天，
避免退菜跨零点入账时漏掉前一天的单。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from scraper._common import CHINA_TZ
from scraper.order_flow_ids import biz_date_from_bs_code

# 退菜变化远慢于桌台，5 分钟一次足够；太频繁只会白耗 cy7mm 配额。
REFUND_POLL_INTERVAL_SECONDS = 300
# 首次同步向前补的天数（服务刚上线时把历史退菜一次性拉回来）。
REFUND_BACKFILL_DAYS = 7
# 稳态每轮回看的天数：退菜可能跨零点入账，前一天的单也要能补到。
REFUND_INCREMENTAL_DAYS = 2
# 接口单页上限按实测取 100；超过就翻页。
REFUND_PAGE_SIZE = 100
# 翻页安全上限：接口 dataSize 异常时不至于把一轮采集拖死。
REFUND_MAX_PAGES = 50
# 只有最近这几天才额外查 coStatus 做阶段分类（1/2/3/4 各一次请求）；
# 更早的历史统一记为 0=未分类，省接口调用。
REFUND_CLASSIFY_DAYS = 2

# 赠单没有分档的概念，co_status 恒为 0。
PRESENT_STATUS = 0

# coStatus 与「异常汇总」页 tab 的对应关系（SQL 条件见 docs/pos/CY7MM_API_REFERENCE.md）：
# 1 无附加条件（= 全部退单，门店实际退菜都落这档）、2 传菜后退菜、3 结算后退单、
# 4 返位后退单。0 表示本次没做分类。
CO_STATUS_LABELS = {
    0: "退单",
    1: "预结后退单",
    2: "传菜后退菜",
    3: "结算后退单",
    4: "返位后退单",
}


class _DisabledRefunds:
    """移动银台模式下的占位实现：退菜链路仍绑定 cy7mm 会话。"""

    def __init__(self, logger):
        self._logger = logger
        self._warned = False

    async def sync(self, db) -> int:
        if not self._warned:
            self._warned = True
            self._logger.info(
                "ℹ️ 移动银台模式：退菜明细链路未接入（接口在 cy7mm），本轮跳过"
            )
        return 0


class RefundTracker:
    """拉取 cy7mm 退单明细并整单替换进 ``refund_records``。"""

    EXCEPTION_CO_URL = (
        "https://cy7mm.wuuxiang.com/cy7MobileReports/canyin/mobile/exceptiondetail/getexceptionco"
    )
    EXCEPTION_PRESENT_URL = (
        "https://cy7mm.wuuxiang.com/cy7MobileReports/canyin/mobile/exceptiondetail"
        "/getexceptionpresent"
    )
    REFERER = "https://cy7mm.wuuxiang.com/home/exceptionList"

    def __init__(self, session, *, logger_: Optional[logging.Logger] = None):
        self._session = session
        self.logger = logger_ or logging.getLogger(__name__)
        self._last_sync_at: Optional[float] = None
        self._backfilled = False
        self.last_written_rows = 0

    # ---- 时间窗口 ----

    @staticmethod
    def _biz_window(biz_date: str) -> tuple[str, str]:
        """营业日 06:00 到次日 06:00（与报表/已结账单同一口径）。"""
        year, month, day = (int(part) for part in biz_date.split("-"))
        begin = datetime(year, month, day, 6, 0, 0)
        return (
            begin.strftime("%Y-%m-%d 06:00:00"),
            (begin + timedelta(days=1)).strftime("%Y-%m-%d 06:00:00"),
        )

    def _biz_dates(self, days: int) -> List[str]:
        today = datetime.now(CHINA_TZ).date()
        first = today - timedelta(days=max(0, days) - 1)
        return [(first + timedelta(days=i)).isoformat() for i in range(days)]

    # ---- 接口 ----

    @staticmethod
    def _payload(begin: str, end: str, page: int, kind: str, co_status: str) -> Dict[str, Any]:
        """两个接口的请求体：退单要带 coStatus 等筛选字段，赠单只要日期与分页。"""
        payload: Dict[str, Any] = {
            "beginDate": begin,
            "endDate": end,
            "type": "day",
            "timeType": "day",
            "queryInfo": "",
            "currentPage": page,
            "pageSize": REFUND_PAGE_SIZE,
        }
        if kind == "co":
            payload.update(
                {
                    "coReason": "",
                    "orderSourceGroup": "",
                    "itemType": 1,
                    "isIncludeInvalidBill": False,
                    "coStatus": co_status,
                }
            )
        return payload

    async def _post_page(
        self, begin: str, end: str, page: int, *, kind: str = "co", co_status: str = ""
    ) -> Optional[Dict[str, Any]]:
        """请求一页明细；接口异常（含返回 alert 脚本的 HTTP 500）返回 None。"""
        payload = self._payload(begin, end, page, kind, co_status)
        url = self.EXCEPTION_CO_URL if kind == "co" else self.EXCEPTION_PRESENT_URL
        label = "退单" if kind == "co" else "赠单"
        http = getattr(self._session, "http", None)
        if http is None:
            return None
        try:
            headers = await http.build_headers(referer=self.REFERER)
            encoded = "data=" + _quote(json.dumps(payload, ensure_ascii=False))
            response = await http.post_form(
                url,
                headers=headers,
                encoded_data=encoded,
                context_label=f"getexception{kind} {begin[:10]} p{page}",
            )
            status = getattr(response, "status", 0)
            try:
                body = await response.json()
            except Exception:
                return None
            if status != 200 or not isinstance(body, dict) or not body.get("success"):
                self.logger.warning(
                    "⚠️ %s接口返回异常 %s p%s: status=%s body=%s",
                    label, begin[:10], page, status, str(body)[:120],
                )
                return None
            return body
        except Exception as exc:  # noqa: BLE001 - 退菜同步失败不能带停采集
            self.logger.warning("⚠️ %s接口请求失败 %s p%s: %s", label, begin[:10], page, exc)
            return None

    async def _fetch_bills(
        self, begin: str, end: str, *, kind: str = "co", co_status: str = ""
    ) -> Tuple[List[Dict], bool]:
        """拉全一个营业日的退单。第二个返回值表示「每一页都请求成功」。

        空列表既可能是当天没有退单，也可能是接口挂了：同步侧靠这个布尔值决定
        要不要把首次补历史标成完成。
        """
        bills: List[Dict] = []
        ok = True
        for page in range(1, REFUND_MAX_PAGES + 1):
            body = await self._post_page(begin, end, page, kind=kind, co_status=co_status)
            if body is None:
                ok = False
                break
            data = body.get("data") or {}
            rows = data.get("dataList") or []
            if not rows:
                break
            bills.extend(rows)
            try:
                total = int(data.get("dataSize") or 0)
            except (TypeError, ValueError):
                total = 0
            if total and len(bills) >= total:
                break
            if len(rows) < REFUND_PAGE_SIZE:
                break
        return bills, ok

    async def _status_map(self, begin: str, end: str) -> Dict[int, int]:
        """按 coStatus 2/3/4 各查一遍，得到 bs_id → 阶段；查不到就是 1（全部退单）。"""
        mapping: Dict[int, int] = {}
        for status in (2, 3, 4):
            bills, _ok = await self._fetch_bills(begin, end, kind="co", co_status=str(status))
            for bill in bills:
                bs_id = _as_int(bill.get("bsId") or bill.get("bsid"))
                if bs_id:
                    mapping[bs_id] = status
        return mapping

    # ---- 落库 ----

    def _shop_id(self) -> str:
        creds = getattr(self._session, "_creds", None)
        return str(getattr(creds, "shop_id", "") or "")

    async def _store_bill(
        self, db, bill: Dict, fallback_date: str, status_map: Dict[int, int], *, kind: str = "co"
    ) -> int:
        bs_code = str(bill.get("bsCode") or "")
        bs_id = _as_int(bill.get("bsId") or bill.get("bsid")) or _bs_id_from_code(bs_code)
        if not bs_id:
            return 0
        biz_date = biz_date_from_bs_code(bs_code) or fallback_date
        children = _parse_children(bill.get("children"))
        if not children:
            # 账单还在、明细空了（POS 撤销了这笔退单）：把旧行删干净。
            await db.refund_records_replace_bill(
                shop_id=self._shop_id(), bs_id=bs_id, rows=[], kind=kind
            )
            return 0

        synced_at = datetime.now(CHINA_TZ).isoformat()
        rows = []
        for line_no, child in enumerate(children):
            if not isinstance(child, dict):
                continue
            refund_time = _combine_datetime(biz_date, child.get("time"))
            rows.append(
                {
                    "bs_code": bs_code,
                    "source_line": line_no,
                    "point_name": _clean(bill.get("pointName")),
                    "item_name": _clean(child.get("itemName")),
                    "qty": _as_float(child.get("qty")),
                    "money": _as_float(child.get("money")),
                    "reason": _clean(child.get("reason")),
                    "operator": _clean(child.get("operator")),
                    "auth_emp": _clean(child.get("authEmp")),
                    "refund_date": biz_date,
                    "refund_time": refund_time,
                    "co_status": status_map.get(bs_id, 1) if kind == "co" else PRESENT_STATUS,
                    "settle_time": _clean(bill.get("settleTime")),
                    "open_time": _clean(bill.get("openTime")),
                    "settle_man": _clean(bill.get("settleMan")),
                    "synced_at": synced_at,
                }
            )
        if not rows:
            return 0
        return await db.refund_records_replace_bill(
            shop_id=self._shop_id(), bs_id=bs_id, rows=rows, kind=kind
        )

    async def sync(self, db) -> int:
        """拉取并落库；返回本次写入的明细行数。失败只记日志，不抛出。"""
        if db is None or not hasattr(db, "refund_records_replace_bill"):
            return 0
        now = time.monotonic()
        if self._last_sync_at is not None and (
            now - self._last_sync_at < REFUND_POLL_INTERVAL_SECONDS
        ):
            return 0
        self._last_sync_at = now
        if not getattr(self._session, "http", None):
            return 0

        days = REFUND_INCREMENTAL_DAYS if self._backfilled else REFUND_BACKFILL_DAYS
        biz_dates = self._biz_dates(days)
        status_map: Dict[int, int] = {}
        for biz_date in biz_dates[-REFUND_CLASSIFY_DAYS:]:
            begin, end = self._biz_window(biz_date)
            status_map.update(await self._status_map(begin, end))

        written = 0
        failed_days = 0
        for kind in ("co", "present"):
            for biz_date in biz_dates:
                begin, end = self._biz_window(biz_date)
                bills, ok = await self._fetch_bills(begin, end, kind=kind)
                if not ok:
                    failed_days += 1
                for bill in bills:
                    try:
                        written += await self._store_bill(
                            db, bill, biz_date, status_map, kind=kind
                        )
                    except Exception as exc:  # noqa: BLE001 - 单张失败不影响其他单
                        failed_days += 1
                        self.logger.warning(
                            "⚠️ %s落库失败 %s/%s: %s",
                            "退单" if kind == "co" else "赠单",
                            biz_date, bill.get("bsCode"), exc,
                        )

        self.last_written_rows = written
        if written:
            self.logger.info("✅ 退单/赠单明细同步 %s 行（%s 天）", written, days)
        if failed_days:
            self.logger.warning("⚠️ 退单/赠单同步有 %s 处失败，下轮重试", failed_days)
        if not failed_days:
            self._backfilled = True
        return written


# ---- 小工具 ----


def _bs_id_from_code(bs_code: str) -> int:
    """赠单接口只给第一张账单返回 bsId，其余都是 null —— 用单号里的数字兜底。

    ``YY001301-261113-0024`` → ``13012611130024``：14 位，和真实 bsId（17 位）不会
    撞；同一单号在同一门店内唯一，配上 shop_id 就能当整单替换的键。
    """
    digits = re.sub(r"\D", "", bs_code or "")
    return int(digits) if digits else 0


def _quote(text: str) -> str:
    return quote(text, safe="")


def _clean(value) -> str:
    return str(value).strip() if value is not None else ""


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_children(raw) -> List[Dict]:
    """``children`` 是 JSON 字符串（偶尔已是 list）；坏数据当空明细。"""
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def _combine_datetime(biz_date: str, time_text) -> str:
    """营业日 + ``HH:MM:SS`` → CHINA_TZ ISO 时刻。"""
    text = _clean(time_text)
    parts = text.split(":")
    try:
        hour = int(parts[0]) if parts else 0
        minute = int(parts[1]) if len(parts) > 1 else 0
        second = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        hour = minute = second = 0
    return f"{biz_date}T{hour:02d}:{minute:02d}:{second:02d}+08:00"
