#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""营业日（06:00 切点）窗口的**唯一定义处**（R-T3-03 / 票 21）。

口径：一个营业日 = ``[当日 06:00, 次日 06:00)``（北京时，右端开区间）。与采集/对账/
卫生、`services/business_day.BUSINESS_DAY_CUT_HOUR` 同一把尺子（CORR-05 / DATA-03）。

背景：DATA-03 把三个经营聚合改成营业日窗口时，实现落在 `db_core/reports.py` 的局部
实现里；档口统计/速率仍是自然日。票 21 把切点提到本模块，作为 db_core 内的唯一来源：

- ``BUSINESS_DAY_CUT_HOUR``：切点小时，db_core 内只有这一处定义；
- ``business_day_range(business_date)``：营业日日期 → ``[06:00, 次日 06:00)``；
- ``business_day_window(moment)``：某时刻所在营业日 → ``[06:00, 次日 06:00)``；
- ``calendar_day_range(moment)``：自然日 ``[00:00, 次日 00:00)``，**只给刻意保留
  日历日口径的非档口路径**用（订单列表/统计等的「今日」），别在营业日路径上使用。

为什么 db_core 内独立定义而不 import `services.business_day`：`services/business_day.py`
自己 `from db_core.utils import CHINA_TZ`，db_core 反向 import 会构成 db_core ↔ services
的真环（DOC-07，全仓唯一一处）。两边同尺子由 `tests/test_sales_report_business_day.py`
的边界用例交叉核对；改切点时必须同时改本模块与
`services/business_day.BUSINESS_DAY_CUT_HOUR`。
"""

from datetime import datetime, time, timedelta
from typing import Tuple

from db_core.utils import CHINA_TZ, ensure_beijing_datetime

BUSINESS_DAY_CUT_HOUR = 6


def business_day_range(business_date: str) -> Tuple[datetime, datetime]:
    """营业日 ``YYYY-MM-DD`` → ``(当日 06:00, 次日 06:00)``（北京时，半开区间）。

    解析走 ``strptime`` 而不是 ``date.fromisoformat``：前者宽容 ``2026-9-2`` 这类不补零
    写法，换掉解析方式等于改了既有输入面（对齐原先 ``to_local_dt`` 的老行为）。
    """
    day = datetime.strptime(business_date, "%Y-%m-%d")
    start = day.replace(
        hour=BUSINESS_DAY_CUT_HOUR, minute=0, second=0, microsecond=0, tzinfo=CHINA_TZ
    )
    return start, start + timedelta(days=1)


def business_day_window(moment) -> Tuple[datetime, datetime]:
    """``moment`` 所在营业日 → ``(起点, 终点)`` = ``[当日 06:00, 次日 06:00)``。

    06:00 之前属于**前一个**营业日（起点是昨天 06:00），06:00 整算当前营业日。
    """
    local = ensure_beijing_datetime(moment)
    start = local.replace(
        hour=BUSINESS_DAY_CUT_HOUR, minute=0, second=0, microsecond=0
    )
    if local.hour < BUSINESS_DAY_CUT_HOUR:
        start -= timedelta(days=1)
    return start, start + timedelta(days=1)


def calendar_day_range(moment) -> Tuple[datetime, datetime]:
    """自然日 ``[00:00, 次日 00:00)``（北京时）——非营业日路径专用（见模块 docstring）。"""
    local = ensure_beijing_datetime(moment)
    start = datetime.combine(local.date(), time.min, tzinfo=CHINA_TZ)
    return start, start + timedelta(days=1)
