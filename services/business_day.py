#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切日规则：营业日（06:00 切）与工作台日（00:00 切），各自唯一的实现。

**这里装的是两把尺子，不是一把。** 用哪把由「这个日期是给谁看的」决定：

- **营业日（06:00 切）**：采集、对账、报表、企微推送的「当天」。凌晨的订单属于前一晚的
  营业——这条由 POS 的 `bsCode` 决定，不是我们的开关。入口是 `business_date_of` /
  `current_business_date` / `business_date_range`。
- **工作台日（自然日，00:00 切）**：员工端与店长端的排班、卫生、考勤，以及加班与损耗。
  这些页面回答的是「我哪一天上班 / 哪一天该交检查 / 哪一天加的班」，日历日才是员工嘴里
  的那天；06:00 切会让手机时钟（11/1 00:19）与页面（「今天 10/31」）在一屏里打架。
  入口是 `workbench_date_of` / `current_workbench_date` / `workbench_day_range`。
  2026-11-01 用户裁定，见 ADR 0125。

两把尺子必须同时在场：报表那边不能改（POS 的营业日），工作台那边不该留 06:00（店里
没有跨零点的班次，06:00 在这里没有任何业务依据）。切点分派的完整理由见 ADR 0125。

历史上这条规则原先在四五个模块里各写了一遍（`scraper/state_store.py`、
`services/scraper_health.py`、`services/reconcile_job.py`、`services/hygiene/accounts.py`、
`services/wecom_push_service.py` 的派生用法），各处的边界写法略有差异（CORR-05）。

放在这里而不是放进任何业务包：调用方横跨 `scraper/`、`services/`、`db_core/`，
放在任何一侧都会造出反向依赖。代价是本模块要从 `db_core.utils` 取 `CHINA_TZ`
（构成 `services → db_core` 这条依赖）——`CHINA_TZ` 只是个时区常量、不是数据库能力，
方向按「上层用底层设施」读也说得通；除它之外只用标准库。

用法：

    from services.business_day import business_date_of, current_business_date, current_workbench_date

    business_date_of(datetime(2026, 9, 22, 5, 0, tzinfo=CHINA_TZ))    # '2026-09-21'（营业日）
    current_workbench_date()                                         # 现在所属的自然日
    current_business_date()                                          # 现在所属的营业日

营业日的切点是 **[cut, 次日 cut)** 的半开区间，与 `compute_sales_report` 的区间参数一一对应：
把 `business_date` 交给 `business_date_range()` 就能拿到两端的时间戳，无需各处再算一次。
工作台日的切点是 **[当日 00:00, 次日 00:00)**，没有可配的 cut 小时。
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


# ── 工作台日（自然日，00:00 切）────────────────────────────────────────────
# 三个函数跟上面三个一一对应，只是切点固定为 00:00。**故意不合并成一个带
# `cut_hour` 参数的函数**：切点由「这个日期给谁看」决定，参数化等于把这个判断
# 推给每个调用方，正是 CORR-05 要收掉的那种散落（见模块 docstring / ADR 0125）。


def workbench_date_of(moment: datetime) -> str:
    """``moment`` 所属的工作台日（``YYYY-MM-DD``，自然日）。"""
    return to_china_tz(moment).date().isoformat()


def current_workbench_date(now: Optional[datetime] = None) -> str:
    """当前工作台日；``now`` 可注入以便测试。"""
    return workbench_date_of(now or datetime.now(CHINA_TZ))


def workbench_day_range(work_date: str) -> tuple[datetime, datetime]:
    """工作台日 ``YYYY-MM-DD`` → ``[当日 00:00, 次日 00:00)``，两个都带北京时。"""
    day = date.fromisoformat(work_date)
    start = datetime(day.year, day.month, day.day, tzinfo=CHINA_TZ)
    return start, start + timedelta(days=1)


def shift_business_date(business_date: str, days: int) -> str:
    """营业日加减天数（``days`` 可以是负数）。"""
    return (date.fromisoformat(business_date) + timedelta(days=days)).isoformat()


def previous_business_date(business_date: str) -> str:
    """前一营业日。"""
    return shift_business_date(business_date, -1)
