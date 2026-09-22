#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""营业日：唯一的切日规则与唯一的实现。

门店的「今天」不是日历日：06:00 之前算前一天（凌晨的订单属于前一晚的营业）。
这条规则原先在四五个模块里各写了一遍（`scraper/state_store.py`、`services/scraper_health.py`、
`services/reconcile_job.py`、`services/hygiene/accounts.py`、`services/wecom_push_service.py`
的派生用法），各处的边界写法略有差异；报表与企微推送更干脆按**日历日**切，于是同一个
「今天」在系统里有两套含义（CORR-05）。

放在这里而不是放进任何业务包：调用方横跨 `scraper/`、`services/`、`db_core/`，
放在任何一侧都会造出反向依赖。本模块只依赖标准库。

用法：

    from services.business_day import BUSINESS_DAY_CUT_HOUR, business_date_of, current_business_date

    business_date_of(datetime(2026, 9, 22, 5, 0, tzinfo=CHINA_TZ))  # '2026-09-21'
    current_business_date()                                        # 现在所属的营业日

切点是 **[cut, 次日 cut)** 的半开区间，与 `compute_sales_report` 的区间参数一一对应：
把 `business_date` 交给 `to_business_day_range()` 就能拿到两端的时间戳，无需各处再算一次。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from db_core.utils import CHINA_TZ

# 营业日切点（小时，北京时）。与 `scraper/state_store.py` 的历史行为一致。
BUSINESS_DAY_CUT_HOUR = 6


def to_china_tz(moment: datetime) -> datetime:
    """把任意 datetime 归一到北京时。

    朴素 datetime 按北京时解释（而不是按进程本地时区）：门店机器的设备时区可能是错的
    （KDS 就踩过这个坑），而所有业务时间戳都是北京时写的。
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=CHINA_TZ)
    return moment.astimezone(CHINA_TZ)


def business_date_of(moment: datetime) -> str:
    """``moment`` 所属的营业日（``YYYY-MM-DD``）。"""
    local = to_china_tz(moment)
    if local.hour < BUSINESS_DAY_CUT_HOUR:
        local = local - timedelta(days=1)
    return local.date().isoformat()


def current_business_date(now: Optional[datetime] = None) -> str:
    """当前营业日；``now`` 可注入以便测试。"""
    return business_date_of(now or datetime.now(CHINA_TZ))


def business_date_range(business_date: str) -> tuple[datetime, datetime]:
    """营业日 ``YYYY-MM-DD`` → ``[当日 cut, 次日 cut)``，两个都带北京时。"""
    day = date.fromisoformat(business_date)
    start = datetime(
        day.year, day.month, day.day, BUSINESS_DAY_CUT_HOUR, tzinfo=CHINA_TZ
    )
    return start, start + timedelta(days=1)


def shift_business_date(business_date: str, days: int) -> str:
    """营业日加减天数（``days`` 可以是负数）。"""
    return (date.fromisoformat(business_date) + timedelta(days=days)).isoformat()


def previous_business_date(business_date: str) -> str:
    """前一营业日。"""
    return shift_business_date(business_date, -1)
