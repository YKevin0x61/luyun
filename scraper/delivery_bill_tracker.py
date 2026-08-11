#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Delivery / settled-bill collection, dedupe, and cancel sweep."""

import json
import logging
import re
import time
import asyncio
from typing import Any, List, Dict, Optional
from datetime import datetime, timedelta

from scraper._common import (
    CHINA_TZ,
    ScraperSessionError,
    pos_response_indicates_auth_failure,
)
from services.dish_normalize import strip_trailing_dash_suffix

DELIVERY_ORDER_TIME_FIELDS = ("settleTime1", "settleTime2", "openTime", "settleTime")
DELIVERY_ORDER_TIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
)
DELIVERY_TIME_ONLY_FORMATS = ("%H:%M:%S", "%H:%M")
BUSINESS_FLOW_DATE_PATTERN = re.compile(r"-(\d{6})(?:-|$)")

# “消失即取消”防误杀阈值：账单需在连续 N 次“拉取成功且列表非空”中始终缺席，
# 才判定为取消；中途重现则计数清零。避免单次 API 超时/空返回导致误伤全部外卖单。
DELIVERY_CANCEL_MISS_THRESHOLD = 3

# 已结账单列表的最小拉取间隔（秒）。桌台轮询间隔是秒级，但已结账单变化很慢，
# 没必要同频；取消判定因此最长延后 DELIVERY_CANCEL_MISS_THRESHOLD × 本间隔。
DELIVERY_POLL_INTERVAL_SECONDS = 30


class DeliveryBillTracker:
    """Settled-bill fetch, delivery dedupe, and disappear-to-cancel sweep."""

    def __init__(self, session, state_store, *, logger_: Optional[logging.Logger] = None):
        self._session = session
        self._state = state_store
        self.logger = logger_ or logging.getLogger(__name__)
        self.delivery_platforms = ["美团", "淘宝闪购"]
        self._last_delivery_cancel_count = 0
        self._last_delivery_poll_at = None

    @property
    def last_delivery_cancel_count(self) -> int:
        return self._last_delivery_cancel_count

    def biz_datetime_range(self, biz_date=None):
        return self._biz_datetime_range(biz_date)


    SETTLED_BILL_LIST_URL = (
        "https://cy7mm.wuuxiang.com/cy7MobileReports/canyin/mobile/settledbill/getsettledbilldata"
    )
    SETTLED_BILL_DETAIL_URL = (
        "https://cy7mm.wuuxiang.com/cy7MobileReports/canyin/mobile/settledbill/getbillconsumedetail"
    )

    def _parse_datetime_with_china_tz(self, datetime_text: str) -> Optional[datetime]:
        """解析带日期的时间字段。"""
        if not datetime_text:
            return None

        clean_datetime_text = str(datetime_text).strip()
        for datetime_format in DELIVERY_ORDER_TIME_FORMATS:
            try:
                parsed_datetime = datetime.strptime(clean_datetime_text, datetime_format)
                if parsed_datetime.tzinfo is None:
                    return parsed_datetime.replace(tzinfo=CHINA_TZ)
                return parsed_datetime.astimezone(CHINA_TZ)
            except (ValueError, OSError):
                continue

        return None

    def _extract_date_from_business_flow_id(self, business_flow_id: str) -> Optional[datetime]:
        """从 YY01101-260425-0253 这类流水号提取营业日期。"""
        if not business_flow_id:
            return None

        date_match = BUSINESS_FLOW_DATE_PATTERN.search(str(business_flow_id))
        if not date_match:
            return None

        date_text = date_match.group(1)
        try:
            return datetime(
                int("20" + date_text[:2]),
                int(date_text[2:4]),
                int(date_text[4:6]),
                tzinfo=CHINA_TZ,
            )
        except ValueError:
            return None

    def _combine_business_date_and_time(self, business_date: datetime, time_text: str) -> Optional[datetime]:
        """把流水号日期和 settleTime 的纯时间组合为完整订单时间。"""
        if not business_date or not time_text:
            return None

        clean_time_text = str(time_text).strip()
        for time_format in DELIVERY_TIME_ONLY_FORMATS:
            try:
                parsed_time = datetime.strptime(clean_time_text, time_format).time()
                return datetime(
                    business_date.year,
                    business_date.month,
                    business_date.day,
                    parsed_time.hour,
                    parsed_time.minute,
                    parsed_time.second,
                    tzinfo=CHINA_TZ,
                )
            except (ValueError, OSError):
                continue

        return None

    def _parse_delivery_order_time(self, bill: Dict) -> Optional[datetime]:
        """按接口实际字段解析外卖账单订单时间。"""
        for time_field in DELIVERY_ORDER_TIME_FIELDS:
            parsed_datetime = self._parse_datetime_with_china_tz(bill.get(time_field, ""))
            if parsed_datetime is not None:
                return parsed_datetime

        business_flow_id = bill.get('bsCode') or bill.get('bfid') or ''
        business_date = self._extract_date_from_business_flow_id(business_flow_id)
        if business_date is None:
            return None

        return self._combine_business_date_and_time(business_date, bill.get('settleTime', ''))

    # ==================== 已结账单 / 外卖订单采集 ====================

    def _biz_datetime_range(self, biz_date: Optional[str] = None) -> tuple:
        """营业日 06:00 起止。biz_date 为 YYYY-MM-DD，默认当前营业日。"""
        if biz_date:
            parts = biz_date.split("-")
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            begin = datetime(year, month, day, 6, 0, 0, tzinfo=CHINA_TZ)
        else:
            now = datetime.now(CHINA_TZ)
            begin = now.replace(hour=6, minute=0, second=0, microsecond=0)
            if now.hour < 6:
                begin = begin - timedelta(days=1)
        end = begin + timedelta(days=1)
        return begin, end

    async def _settled_api_headers(self) -> Dict[str, str]:
        referer = (
            self._session._creds.closed_tables_referer if self._session._creds else ""
        )
        return await self._session.http.build_headers(referer=referer)

    async def _settled_bill_list_request_raw(
        self,
        begin: datetime,
        end: datetime,
    ) -> tuple[int, Any]:
        """调用已结账单列表 getsettledbilldata，返回 (HTTP 状态码, JSON 或原始文本)。"""
        import urllib.parse

        if self._session.page is None or self._session.page.is_closed():
            raise RuntimeError("浏览器页面未就绪")
        if self._session._creds is None:
            raise RuntimeError("无登录凭据")

        headers = await self._settled_api_headers()
        payload_json = {
            "beginDate": begin.strftime("%Y-%m-%d 06:00:00"),
            "endDate": end.strftime("%Y-%m-%d 06:00:00"),
            "type": "day",
            "timeType": "day",
            "condition": '"pointName"',
            "value": "",
            "shopId": self._session._creds.delivery_shop_id,
            "orderType": "settleTime1",
            "selectDeadline": "sameTime",
        }
        encoded_data = urllib.parse.urlencode({"data": json.dumps(payload_json)})
        response = await self._session.http.post_form(
            self.SETTLED_BILL_LIST_URL,
            headers=headers,
            encoded_data=encoded_data,
            context_label="getsettledbilldata",
        )
        status = response.status
        try:
            return status, await response.json()
        except Exception:
            try:
                return status, await response.text()
            except Exception:
                return status, None

    @staticmethod
    def _settled_bill_rows_from_body(body: Any) -> List[Dict]:
        if not isinstance(body, dict):
            return []
        raw = body.get("data", body)
        if isinstance(raw, list):
            return [row for row in raw if isinstance(row, dict) and row.get("bsId")]
        if isinstance(raw, dict):
            bill_list = raw.get("list", [])
            if isinstance(bill_list, list):
                return [row for row in bill_list if isinstance(row, dict) and row.get("bsId")]
        return []

    async def probe_settled_bill_apis(self) -> Dict[str, Any]:
        """校验 getsettledbilldata / getbillconsumedetail 会话是否有效。"""
        try:
            begin, end = self._biz_datetime_range()
            status, body = await self._settled_bill_list_request_raw(begin, end)
            if status != 200:
                return {
                    "ok": False,
                    "http_status": status,
                    "message": f"getsettledbilldata 请求失败（HTTP {status}）",
                }
            if not isinstance(body, dict):
                return {
                    "ok": False,
                    "http_status": status,
                    "message": "getsettledbilldata 响应无法解析为 JSON",
                }
            if pos_response_indicates_auth_failure(body):
                return {
                    "ok": False,
                    "http_status": status,
                    "message": "getsettledbilldata 判定未登录或无权",
                }
            if body.get("success") is False:
                detail = str(body.get("message") or body.get("msg") or body.get("errorMsg") or "").strip()
                return {
                    "ok": False,
                    "http_status": status,
                    "message": detail or "getsettledbilldata 返回 success=false",
                }

            bills = self._settled_bill_rows_from_body(body)
            result: Dict[str, Any] = {
                "ok": True,
                "http_status": status,
                "bill_count": len(bills),
                "message": "getsettledbilldata 校验通过",
            }
            if not bills:
                result["detail_skipped"] = True
                result["detail_message"] = "当日无已结账单，跳过 getbillconsumedetail"
                return result

            dishes = await self.fetch_settled_bill_raw_dishes(bills[0], begin, end)
            result["detail_ok"] = True
            result["dish_count"] = len(dishes)
            result["sample_bs_id"] = bills[0].get("bsId")
            result["message"] = "getsettledbilldata / getbillconsumedetail 校验通过"
            return result
        except Exception as exc:
            self.logger.error("已结账单 API 探测失败: %s", exc)
            return {"ok": False, "http_status": 0, "message": f"已结账单 API 调用异常: {exc}"}

    async def fetch_settled_bill_list(
        self,
        begin: datetime,
        end: datetime,
        *,
        delivery_only: bool = False,
    ) -> List[Dict]:
        """从 POS 拉取已结账单列表（对账用全量；外卖采集 delivery_only=True）。"""
        if self._session._creds is None:
            self.logger.error("❌ 无登录凭据，跳过获取已结账单")
            return []

        status, data = await self._session.http.request_with_recovery(
            lambda: self._settled_bill_list_request_raw(begin, end),
            context_label="getsettledbilldata",
        )
        if status != 200:
            self.logger.error("❌ 已结账单列表 API 请求失败，状态码: %s", status)
            return []
        if not isinstance(data, dict):
            self.logger.error("❌ 已结账单列表 API 响应无法解析")
            return []
        if data.get("success") is False:
            self.logger.error(
                "❌ 已结账单列表 API 业务失败: %s",
                data.get("message") or data.get("msg") or data.get("errorMsg"),
            )
            return []

        bill_list = self._settled_bill_rows_from_body(data)

        if not delivery_only:
            return bill_list

        delivery_bills = []
        for bill in bill_list:
            bill_source = bill.get("billSource", "")
            people_qty = bill.get("peopleQty", 1)
            point_name = bill.get("pointName", "")
            is_delivery = (
                people_qty == 0
                or any(platform in bill_source for platform in self.delivery_platforms)
                or any(platform in point_name for platform in self.delivery_platforms)
            )
            if is_delivery and bill.get("bsId"):
                delivery_bills.append(bill)
        if delivery_bills:
            self.logger.info(f"🚴 获取到 {len(delivery_bills)} 条外卖账单")
        return delivery_bills

    async def fetch_settled_bill_raw_dishes(
        self,
        bill: Dict,
        begin: datetime,
        end: datetime,
    ) -> List[Dict]:
        """拉取单张已结账单菜品明细（原始 API 行）。"""
        import urllib.parse

        if self._session._creds is None:
            return []

        api_url = self.SETTLED_BILL_DETAIL_URL
        headers = await self._settled_api_headers()
        bs_id = bill.get("bsId", "")
        payload_json = {
            "tsId": bill.get("tsId", ""),
            "bsId": bs_id,
            "settleTime": bill.get("settleTime", ""),
            "beginDate": begin.strftime("%Y-%m-%d 06:00:00"),
            "endDate": end.strftime("%Y-%m-%d 06:00:00"),
            "type": "day",
            "timeType": "day",
            "prop": "lastQty",
            "order": "descending",
            "shops": [int(self._session._creds.delivery_shop_id)],
            "selectDeadline": "sameTime",
        }
        encoded_data = urllib.parse.urlencode({"data": json.dumps(payload_json)})
        response = await self._session.http.post_form(
            api_url,
            headers=headers,
            encoded_data=encoded_data,
            context_label=f"getbillconsumedetail bsId={bs_id}",
        )
        data = await response.json()
        raw = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(raw, list):
            return raw
        return raw.get("dishList", []) if isinstance(raw, dict) else []

    async def fetch_settled_bills_for_biz_date(
        self,
        biz_date: str,
        *,
        ignore_pause: bool = False,
    ) -> List[Dict]:
        """对账：指定营业日全部已结账单。

        ``ignore_pause``：对账默认 22:05 触发，此时已过营业时段，需要跳过暂停门禁。
        """
        if not await self._session.ensure_ready(ignore_pause=ignore_pause):
            return []
        begin, end = self._biz_datetime_range(biz_date)
        return await self.fetch_settled_bill_list(begin, end, delivery_only=False)

    async def _get_delivery_bills(self) -> List[Dict]:
        """从已结账单 API 获取外卖订单列表"""
        _ok, bills = await self._get_delivery_bills_checked()
        return bills

    async def _get_delivery_bills_checked(self) -> tuple:
        """获取外卖账单列表，并返回 (拉取是否成功, 账单列表)。

        取消检测需要区分“成功但为空”与“拉取失败”——失败时绝不能把缺席当作取消，
        否则一次 API 超时就会误删当天全部外卖单。失败返回 (False, [])。
        """
        try:
            begin, end = self._biz_datetime_range()
            bills = await self.fetch_settled_bill_list(begin, end, delivery_only=True)
            return True, bills
        except ScraperSessionError:
            raise
        except Exception as e:
            self.logger.error(f"❌ 获取外卖账单列表出错: {e}")
            import traceback
            self.logger.error(f"详细错误: {traceback.format_exc()}")
            return False, []

    async def _get_delivery_bill_dishes(self, bill: Dict) -> List[Dict]:
        """获取单个外卖账单的菜品明细"""
        try:
            begin, end = self._biz_datetime_range()
            dish_list = await self.fetch_settled_bill_raw_dishes(bill, begin, end)
            if not dish_list:
                return []

            platform = bill.get('billSource', '外卖')
            point_name = bill.get('pointName', '外卖')
            settle_time_str = bill.get('settleTime', '')
            bs_id = bill.get('bsId', '')
            business_flow_id = bill.get('bsCode', bs_id)

            order_time_dt = self._parse_delivery_order_time(bill)

            # 无法解析则降级到当前时间
            if order_time_dt is None:
                order_time_dt = datetime.now(CHINA_TZ)
                self.logger.warning(
                    f"⚠️ 无法解析订单时间，降级到当前时间: "
                    f"bsId={bs_id}, settleTime='{settle_time_str}', "
                    f"bfid='{business_flow_id}'"
                )

            from scraper.order_line_builder import FLOW_MODE_UNIT, RawOrderLine

            builder = getattr(self._session, "order_lines", None)
            if builder is None:
                from scraper.order_line_builder import OrderLineBuilder

                builder = OrderLineBuilder(self._session.dish_catalog)

            raws = []
            for item in dish_list:
                dish_name = strip_trailing_dash_suffix(item.get('name', ''))
                if not dish_name:
                    continue

                dish_quantity = int(float(item.get('lastQty', 0) or 0))
                price = float(item.get('price', 0) or 0)
                if dish_quantity <= 0:
                    continue
                raws.append(
                    RawOrderLine(
                        bs_code=business_flow_id,
                        dish_name=dish_name,
                        quantity=dish_quantity,
                        unit_price=price,
                        table_number=point_name or '外卖',
                        order_time=order_time_dt,
                        flow_mode=FLOW_MODE_UNIT,
                        overlays={
                            'status': '已结',
                            'notes': f"外卖平台:{platform}|来源:{point_name}",
                            'change_type': '新增',
                            'source': 'delivery',
                        },
                    )
                )

            return await builder.expand_many(raws)

        except Exception as e:
            self.logger.error(f"❌ 获取外卖账单菜品明细出错: {e}")
            return []

    # ---- 外卖订单采集 ----

    def _delivery_poll_due(self) -> bool:
        """已结账单列表是否到了该拉取的时刻。

        桌台状态需要秒级跟进，已结账单变化则慢得多。实测这个接口每次约 2.6s、
        返回近百条记录，跟着桌台每几秒拉一次纯属浪费。到点才拉，其余轮次直接跳过。
        """
        now = time.monotonic()
        last = self._last_delivery_poll_at
        if last is not None and (now - last) < DELIVERY_POLL_INTERVAL_SECONDS:
            return False
        self._last_delivery_poll_at = now
        return True

    async def scrape_delivery_orders(self, db=None) -> List[Dict]:
        """采集当天外卖订单（已结账单中的外卖），并检测“消失即取消”。

        参数 ``db``：数据库管理器。每轮采集会传入，用于执行取消软删除与重现自愈；
        为 ``None`` 时仅采集新单、不做取消判定（如单元测试或对账另走 sweep）。
        """
        # 本轮取消/自愈影响的行数，供 main.py 判断是否需要广播 orders nudge
        self._last_delivery_cancel_count = 0
        try:
            # 确保已初始化
            if not await self._session.ensure_ready():
                return []

            if not self._delivery_poll_due():
                return []

            # 获取外卖账单列表（区分“成功但为空”与“拉取失败”）
            fetch_ok, delivery_bills = await self._get_delivery_bills_checked()

            all_orders: List[Dict] = []
            new_bill_count = 0
            present_bills_by_code: Dict[str, Dict] = {}

            for bill in (delivery_bills or []):
                bs_id = bill.get('bsId', '')
                if not bs_id:
                    self.logger.warning("⚠️  外卖账单缺少 bsId，已跳过")
                    continue
                bs_code = bill.get('bsCode', '') or bs_id
                present_bills_by_code[bs_code] = bill

                # 跳过已采集的账单
                if bs_id in self._state.collected_delivery_bills:
                    continue

                # 获取菜品明细
                dishes = await self._get_delivery_bill_dishes(bill)
                if dishes:
                    all_orders.extend(dishes)
                    self._state.collected_delivery_bills.add(bs_id)
                    self._state.delivery_bill_state[bs_code] = {
                        "bs_id": bs_id, "miss_count": 0, "cancelled": False,
                    }
                    self._state.save_delivery_bills()
                    new_bill_count += 1
                else:
                    self.logger.warning(f"⚠️  外卖账单 {bs_id} 未获取到明细，保留待下次重试")

                # 请求间隔，避免过快
                await asyncio.sleep(0.3)

            if all_orders:
                # 按来源分组打印摘要
                by_platform: Dict[str, List[Dict]] = {}
                for order in all_orders:
                    notes = order.get('notes', '')
                    platform = notes.split('|')[0].replace('外卖平台:', '') if '外卖平台:' in notes else '未知'
                    if platform not in by_platform:
                        by_platform[platform] = []
                    by_platform[platform].append(order)

                total_amount = sum(o.get('total_amount', 0) for o in all_orders)
                current_time = datetime.now(CHINA_TZ).strftime("%H:%M:%S")
                self.logger.info(f"🚴 [{current_time}] 外卖订单: {len(all_orders)}项 | {new_bill_count}单 | ¥{total_amount:.0f}")
                for platform, orders in by_platform.items():
                    platform_amount = sum(o.get('total_amount', 0) for o in orders)
                    self.logger.info(f"   📦 {platform}: {len(orders)}项 | ¥{platform_amount:.0f}")
            elif fetch_ok and not delivery_bills:
                current_time = datetime.now(CHINA_TZ).strftime("%H:%M:%S")
                self.logger.info(f"📦 [{current_time}] 暂无新的外卖订单")

            # 消失即取消检测 + 重现自愈：仅在“拉取成功且列表非空”时进行（防误杀）
            if db is not None and fetch_ok and delivery_bills:
                await self._sweep_cancelled_delivery(
                    db, set(present_bills_by_code.keys()), present_bills_by_code
                )

            return all_orders

        except ScraperSessionError:
            raise
        except Exception as e:
            self.logger.error(f"❌ 采集外卖订单失败: {e}")
            import traceback
            self.logger.error(f"详细错误: {traceback.format_exc()}")
            return []

    async def _sweep_cancelled_delivery(
        self,
        db,
        present_bs_codes: set,
        present_bills_by_code: Dict[str, Dict],
    ) -> None:
        """对比已采集外卖单与当前 POS 列表，处理消失（取消）与重现（自愈）。

        - 缺席累计达阈值 → 软删除该单（退菜 + 归零 + dish_status 已取消）。
        - 已取消单重现 → 重新拉明细并恢复。
        - 未达阈值又重现 → 计数清零。
        """
        # 阈值可在「配置 → 运行配置」中自定义；读不到时回退默认常量
        threshold = DELIVERY_CANCEL_MISS_THRESHOLD
        try:
            configured = (self._session.config or {}).get("settings", {}).get(
                "delivery_cancel_miss_threshold"
            )
            if configured is not None:
                threshold = max(1, int(configured))
        except (TypeError, ValueError):
            pass
        state_changed = False
        affected_total = 0

        for bs_code, st in list(self._state.delivery_bill_state.items()):
            if bs_code in present_bs_codes:
                if st.get("cancelled"):
                    # 重现 → 自愈：重新拉取明细并恢复此前软删除的行
                    bill = present_bills_by_code.get(bs_code)
                    restored_orders = await self._get_delivery_bill_dishes(bill) if bill else []
                    if restored_orders:
                        restored = await db.orders.revert_delivery_cancelled(restored_orders)
                        st["cancelled"] = False
                        st["miss_count"] = 0
                        state_changed = True
                        affected_total += restored
                        self.logger.info(f"↩️ 外卖单重现，已自愈: bsCode={bs_code} ({restored} 行)")
                elif st.get("miss_count"):
                    st["miss_count"] = 0
                    state_changed = True
            else:
                if st.get("cancelled"):
                    continue  # 已判取消，保持
                st["miss_count"] = st.get("miss_count", 0) + 1
                state_changed = True
                if st["miss_count"] >= threshold:
                    affected = await db.orders.mark_delivery_cancelled(bs_code)
                    st["cancelled"] = True
                    affected_total += affected
                    self.logger.info(
                        f"🚫 外卖单连续 {st['miss_count']} 次缺席，判定取消: "
                        f"bsCode={bs_code}，软删除 {affected} 行"
                    )

        if state_changed:
            self._state.save_delivery_bills()
        self._last_delivery_cancel_count = affected_total
