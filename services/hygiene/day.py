#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生的一天：唯一的日界出口（00:00，工作台日）。

卫生的「今天」是**工作台日**（ADR 0125 决策 1），不是 POS 的营业日（06:00 切）。
切点本身只有一处实现 —— `services/business_day.py` 的 `workbench_*` —— 本模块只做
转发，不复制那条规则，也不提供「按需换个切点」的参数：切点由「这个日期给谁看」决定，
参数化等于把这个判断推回每个调用方（ADR 0125 决策 2）。

为什么要多这一层：`attire` 与 `archive` 曾各自拿着 `business_date_of`（06:00），
于是 00:00-06:00 交的照片，页面算今天、存档算昨天，还从今天的窗口里消失。收成这个
出口之后，日界只有一处可改；`tests/test_hygiene_day.py` 的源码守卫会拦住新模块再把
营业日那把尺子拿回来。

本模块只回答时间问题：不碰数据库、不认识照片、不读配置。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional, Tuple, Union

from services.business_day import (
    current_workbench_date,
    workbench_date_of,
    workbench_day_range,
)

__all__ = ["day_of", "today", "window", "window_text", "week_of", "payload"]

Moment = Union[str, datetime]


def _as_datetime(moment: Moment) -> datetime:
    """字符串时间戳按 ISO 解析；解析不了就交给上游的 `ValueError`（与既有口径一致）。"""
    if isinstance(moment, datetime):
        return moment
    return datetime.fromisoformat(str(moment).strip())


def _as_date(day_text: str) -> date:
    return date.fromisoformat(str(day_text).strip())


def day_of(moment: Moment) -> str:
    """`moment` 属于哪一天（`YYYY-MM-DD`）。朴素时间按北京时解释。"""
    return workbench_date_of(_as_datetime(moment))


def today(now: Optional[datetime] = None) -> str:
    """当前的卫生的一天；`now` 可注入以便测试。"""
    return current_workbench_date(now)


def window(day_text: str) -> Tuple[datetime, datetime]:
    """某一天的 `[00:00, 次日 00:00)`，两个都带北京时。

    ADR 0125 决策 4：本店没有跨零点的班次，所以不需要给夜班另开一个窗口。
    """
    start, end = workbench_day_range(str(day_text).strip())
    return start, end


def window_text(day_text: str) -> Tuple[str, str]:
    """同上，但给 `created_at` 那种 TEXT 比较用：带 `+08:00` 的 ISO 字符串。

    边界必须带偏移：照片的 `created_at` 是 TEXT，比较是字典序，不带偏移的上界会把
    次日 00:00:00+08:00 那一秒算进前一天。上界是半开区间的次日零点整。
    """
    start, end = window(day_text)
    return start.isoformat(), end.isoformat()


def week_of(day_text: str) -> Tuple[str, str]:
    """`day_text` 所在的一周，周一到周日（与 `date.weekday()` 同一套编号）。"""
    day = _as_date(day_text)
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def payload(now: Optional[datetime] = None) -> dict:
    """`GET /api/hygiene/today` 的响应体：页面要的日期全在这里算好。

    `day_end` 是「今天结束」的那一刻（次日 00:00+08:00），前端拿它判断缓存是否
    过期 —— 时区规则不再回到浏览器。
    """
    current = today(now)
    week_from, week_to = week_of(current)
    return {
        "today": current,
        "day_end": window_text(current)[1],
        "weekday": _as_date(current).weekday(),
        "week_from": week_from,
        "week_to": week_to,
    }
