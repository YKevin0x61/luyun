#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌情 API 响应 → 入库订单行的解析（cy7mm 与移动银台共用）。

原本是 ``PosSession._parse_api_order_response`` 的方法体，2026-10-29 抽出成模块函数，
好让不依赖浏览器的 ``YintaiSession`` 复用同一套解析/去重/退菜逻辑。
输入契约（两个数据源都要先转成这个形状）：

    {"success": bool, "data": {"bsCode": str, "pointName": str,
     "peopleQty": int, "scDetail": [{"itemName", "lastQty", "lastPrice",
     "orderTime": "HH:MM", "methodText"}], ...}, "errorMsg": str}
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from scraper._common import CHINA_TZ
from services.dish_normalize import strip_trailing_dash_suffix

logger = logging.getLogger(__name__)


async def parse_api_order_response(
    data: Dict[str, Any],
    table_number: str,
    *,
    order_lines,
    logger: logging.Logger = logger,
) -> Optional[List[Dict]]:
    """解析桌情 API → RawOrderLine → OrderLineBuilder 入库行。"""
    from scraper.order_line_builder import (
        FLOW_MODE_COMBO,
        FLOW_MODE_UNIT,
        RawOrderLine,
        notes_from_method_text,
    )
    from scraper.order_source import classify_order_source

    try:
        if not data.get('success', False):
            logger.error(f"❌ API响应失败: {data.get('errorMsg', '未知错误')}")
            return []

        response_data = data.get('data', {})
        if not response_data:
            logger.error("❌ API响应中没有数据")
            return []

        business_flow_id = response_data.get('bsCode', '')
        sc_detail = response_data.get('scDetail', [])
        if not sc_detail:
            logger.error("❌ 没有找到菜品详情")
            return []

        current_year = datetime.now(CHINA_TZ).year
        current_date = datetime.now(CHINA_TZ).strftime("%m-%d")
        table_overlays = {
            "status": "未结",
            "source": classify_order_source(
                table_number=table_number or response_data.get("pointName") or "",
                people_qty=response_data.get("peopleQty"),
            ),
        }
        raws: List[RawOrderLine] = []

        for item in sc_detail:
            try:
                dish_name = strip_trailing_dash_suffix(item.get('itemName', ''))
                quantity = int(float(item.get('lastQty', 0) or 0))
                price = float(item.get('lastPrice', 0) or 0)
                order_time_str = item.get('orderTime', '')
                if order_time_str:
                    formatted_time = f"{current_year}-{current_date} {order_time_str}"
                    try:
                        order_time_dt = datetime.strptime(
                            formatted_time, "%Y-%m-%d %H:%M"
                        ).replace(tzinfo=CHINA_TZ)
                    except Exception:
                        order_time_dt = datetime.now(CHINA_TZ)
                else:
                    order_time_dt = datetime.now(CHINA_TZ)

                if quantity > 0 and dish_name:
                    parent_overlays = dict(table_overlays)
                    parent_overlays["notes"] = notes_from_method_text(
                        item.get("methodText")
                    )
                    raws.append(
                        RawOrderLine(
                            bs_code=business_flow_id,
                            dish_name=dish_name,
                            quantity=quantity,
                            unit_price=price,
                            table_number=table_number,
                            order_time=order_time_dt,
                            flow_mode=FLOW_MODE_UNIT,
                            overlays=parent_overlays,
                        )
                    )
                    if quantity > 1:
                        logger.info(
                            f"🔄 拆分菜品: {dish_name} x{quantity} → {quantity}条独立记录"
                        )

                for child in item.get('children', []) or []:
                    try:
                        child_name = strip_trailing_dash_suffix(child.get('itemName', ''))
                        child_qty = int(float(child.get('lastQty', 0) or 0))
                        if child_qty <= 0 or not child_name:
                            continue
                        child_overlays = dict(table_overlays)
                        child_overlays["notes"] = notes_from_method_text(
                            child.get("methodText")
                        )
                        raws.append(
                            RawOrderLine(
                                bs_code=business_flow_id,
                                dish_name=child_name,
                                quantity=child_qty,
                                unit_price=0.0,
                                table_number=table_number,
                                order_time=order_time_dt,
                                flow_mode=FLOW_MODE_COMBO,
                                overlays=child_overlays,
                            )
                        )
                        if child_qty > 1:
                            logger.info(
                                f"🔄 拆分套餐子项: {child_name} x{child_qty} → {child_qty}条独立记录"
                            )
                    except Exception as e:
                        logger.error(f"❌ 解析套餐明细失败: {e}")
            except Exception as e:
                logger.error(f"❌ 解析菜品数据失败: {e}")

        return await order_lines.expand_many(raws)
    except Exception as e:
        # 解析期异常同样是「没拿到明细」，不能返回空列表冒充「这桌没菜」
        logger.error(f"❌ 解析API响应失败: {e}")
        return None
