#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切日规则：营业日（06:00 切）、工作台日（00:00 切）与复盘日（07:00 切），各自唯一的实现。

**这里装的是三把尺子，不是一把。** 用哪把由「这个日期是给谁看的」决定：

- **营业日（06:00 切）**：采集、对账、报表、企微推送的「当天」。凌晨的订单属于前一晚的
  营业——这条由 POS 的 `bsCode` 决定，不是我们的开关。入口是 `business_date_of` /
  `current_business_date` / `business_date_range`。
- **工作台日（自然日，00:00 切）**：员工端与店长端的排班、卫生、考勤，以及加班与损耗。
  这些页面回答的是「我哪一天上班 / 哪一天该交检查 / 哪一天加的班」，日历日才是员工嘴里
  的那天；06:00 切会让手机时钟（11/1 00:19）与页面（「今天 10/31」）在一屏里打架。
  入口是 `workbench_date_of` / `current_workbench_date` / `workbench_day_range`。
  2026-11-01 用户裁定，见 ADR 0125。
- **复盘日（07:00 切）**：备货复盘的时间单位，窗口 `[当日 07:00, 次日 07:00)`。一个复盘日
  × 一个备货品 = 一个**品-天**，是复盘指标的最小样本。07:00 是师傅真正下单备货的钟点
  （ADR 0129），快照窗口按它对齐；缺窗口的历史行也必须按同一个 07:00 兜底，否则同一份
  建议量的实际量会配上另一个窗口。入口是 `REVIEW_CLOCK` / `review_day_range` /
  `is_primary_clock`。见 ADR 0140。

三把尺子必须同时在场，也**不能互相替代**：

- 营业日的切点由 POS 决定（凌晨的单属于前一晚），复盘日的切点由快照窗口决定（师傅几点
  下单备货）。用营业日算品-天，凌晨那一小时的销量会落到相邻的品-天，建议量与实际量的
  配对就错位；用复盘日算营业日，对账和企微推送的「当天」就与 POS 的 `bsCode` 对不上。
- 工作台日回答的是员工日历上的「哪一天」（排班、卫生、考勤），复盘日回答的是「这一份
  建议量在哪个 24 小时窗口里兑现」。把工作台日换成 07:00，员工手机时钟与页面又会打架；
  把复盘日换成自然日，07:00 前后两小时的单会被算进另一个品-天。切点分派的完整理由见
  ADR 0125 / 0129 / 0140。

历史上这条规则原先在四五个模块里各写了一遍（`scraper/state_store.py`、
`services/scraper_health.py`、`services/reconcile_job.py`、`services/hygiene/accounts.py`、
`services/wecom_push_service.py` 的派生用法），各处的边界写法略有差异（CORR-05）。

放在这里而不是放进任何业务包：调用方横跨 `scraper/`、`services/`、`db_core/`，
放在任何一侧都会造出反向依赖。代价是本模块要从 `db_core.utils` 取 `CHINA_TZ`
（构成 `services → db_core` 这条依赖）——`CHINA_TZ` 只是个时区常量、不是数据库能力，
方向按「上层用底层设施」读也说得通；除它之外只用标准库。

用法：

    from services.business_day import (
        business_date_of,
        current_business_date,
        current_workbench_date,
        review_day_range,
    )

    business_date_of(datetime(2026, 9, 22, 5, 0, tzinfo=CHINA_TZ))    # '2026-09-21'（营业日）
    current_workbench_date()                                         # 现在所属的自然日
    current_business_date()                                          # 现在所属的营业日
    review_day_range('2026-11-01')       # 复盘日 07:00 窗口两端：11-01T07:00+08:00、11-02T07:00+08:00

营业日的切点是 **[cut, 次日 cut)** 的半开区间，与 `compute_sales_report` 的区间参数一一对应：
把 `business_date` 交给 `business_date_range()` 就能拿到两端的时间戳，无需各处再算一次。
工作台日的切点是 **[当日 00:00, 次日 00:00)**，没有可配的 cut 小时。
复盘日的切点是 **[当日 07:00, 次日 07:00)**，`REVIEW_CLOCK` 是主口径钟点本身。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from db_core.utils import CHINA_TZ

# 营业日切点（小时，北京时）。与 `scraper/state_store.py` 的历史行为一致。
BUSINESS_DAY_CUT_HOUR = 6

#: 复盘日的主口径钟点：师傅真正下单备货的那一次（ADR 0129 / 0140）。
#: 保留策略、复盘聚合、售罄推算、损耗窗口都只认这一个值。
REVIEW_CLOCK = "07:00"
_REVIEW_HOUR, _REVIEW_MINUTE = (int(part) for part in REVIEW_CLOCK.split(":"))


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


def _day_start(day_text: str, hour: int = 0, minute: int = 0) -> datetime:
    """``YYYY-MM-DD`` → 当日 ``hour:minute`` 的北京时 datetime（三个切日入口共用）。"""
    day = date.fromisoformat(day_text)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=CHINA_TZ)


def business_date_range(business_date: str) -> tuple[datetime, datetime]:
    """营业日 ``YYYY-MM-DD`` → ``[当日 cut, 次日 cut)``，两个都带北京时。"""
    start = _day_start(business_date, BUSINESS_DAY_CUT_HOUR)
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
    start = _day_start(work_date)
    return start, start + timedelta(days=1)


# ── 复盘日（07:00 切）─────────────────────────────────────────────────────
# 复盘日的窗口不是自然日也不是营业日：它必须与 07:00 快照的窗口对齐，否则同一份
# 建议量的实际销量会配到另一个品-天（ADR 0140）。这里只提供窗口与主口径判定，
# 复盘指标怎么算仍归 `services/prep_review/`。


def review_day_range(review_date: str) -> tuple[datetime, datetime]:
    """复盘日 ``YYYY-MM-DD`` → ``[当日 07:00, 次日 07:00)``，两个都带北京时。

    坏日期按 ``date.fromisoformat`` 的契约抛 ``ValueError``；调用方要不要兜底由
    调用方决定（与 ``business_date_range`` / ``workbench_day_range`` 一致）。
    """
    start = _day_start(review_date, _REVIEW_HOUR, _REVIEW_MINUTE)
    return start, start + timedelta(days=1)


def is_primary_clock(clock: str) -> bool:
    """``clock`` 是不是复盘主口径钟点（``HH:MM``，容忍首尾空白）。

    空值 / 坏值一律 False、不抛：这是展示与筛选用的判定，不该让一个坏快照钟点把
    整页打崩。
    """
    return str(clock or "").strip() == REVIEW_CLOCK


def shift_business_date(business_date: str, days: int) -> str:
    """营业日加减天数（``days`` 可以是负数）。"""
    return (date.fromisoformat(business_date) + timedelta(days=days)).isoformat()


def previous_business_date(business_date: str) -> str:
    """前一营业日。"""
    return shift_business_date(business_date, -1)
