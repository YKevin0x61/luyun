#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""法定节假日日历的 HTTP 适配层（票 02）。日历本体在 `services/holiday_calendar.py`。

三条路由，**全部只认管理端会话**（`require_session`，即超管那个共享账号）：

    GET    /api/attendance/holidays?year=YYYY   这一年标了哪些天
    PUT    /api/attendance/holidays/YYYY-MM-DD  标记（同名重复是覆盖）
    DELETE /api/attendance/holidays/YYYY-MM-DD  取消标记

不挂 `verify_admin_token`：店长用的是浏览器会话，不是 API token（同排班 / 加班那两
个面）。匿名与员工 cookie 一律 401 —— 这张日历决定补休怎么算，不是员工能改的东西。

这里只管翻译：校验与落库都在服务层（`HolidayError` 的 code 翻成中文），所以页面上
的一句提示与服务层的判据不会各说各话。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.security import require_session
from database import get_db
from services.holiday_calendar import HolidayCalendar, HolidayError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/attendance/holidays", tags=["法定节假日日历"])

# 服务层的 code → 一句能照着改的中文。同排班 / 加班的做法：`{}` 由服务层放进
# `args[0]` 的细节补上（这里是名称上限），别把 20 写死在文案里。
_ERROR_DETAILS = {
    "invalid_day": "日期要写成 YYYY-MM-DD（比如 2026-10-01）",
    "invalid_year": "年份要写 4 位数（比如 2026）",
    "invalid_month": "月份要写成 YYYY-MM（比如 2026-10）",
    "missing_name": "请写这一天的节假日名称：比如「国庆」「春节」",
    "name_too_long": "名称最多 {} 个字：请缩短一点再保存",
    # 逐条点名迁移文件（同排班的 `not_migrated`）：503 要说清该应用哪一个脚本。
    "not_migrated": (
        "法定节假日日历还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
        "migrations/pg/0026_statutory_holidays.sql，然后刷新本页"
    ),
}
_ERROR_STATUS = {
    # `not_migrated` 不是「参数写错了」，是这台机器还没升级完 —— 503 比 400 诚实。
    "not_migrated": 503,
}


class HolidayRequest(BaseModel):
    """标记一天。`name` 给了默认值：缺字段落到服务层那道闸上、回一句中文 400，
    而不是 pydantic 的英文 422（同排班的 `LeaveRequest` 与加班的 `EntryRequest`）。
    """

    name: str = ""


def _bad_request(exc: HolidayError) -> HTTPException:
    detail = _ERROR_DETAILS.get(exc.code, "节假日日历的参数不合法")
    if "{}" in detail:
        # 服务层把细节（名称上限）放在 `args[0]`。用 `str.replace` 而不是 `format`：
        # 文案里多一个花括号就会让 `format` 抛异常，把 400 变成 500。
        raw = exc.args[0] if exc.args else ""
        if not raw or raw == exc.code:
            logger.warning("节假日日历错误缺细节，用「？」占位: code=%s", exc.code)
        detail = detail.replace("{}", raw if raw and raw != exc.code else "？")
    logger.info("节假日日历请求被拒: code=%s", exc.code)
    return HTTPException(status_code=_ERROR_STATUS.get(exc.code, 400), detail=detail)


@router.get("")
async def list_holidays(
    year: str = Query(""),
    db=Depends(get_db),
    _: str = Depends(require_session),
):
    """这一年被标成法定节假日的日子（按日期升序）。"""
    calendar = HolidayCalendar(db)
    try:
        days = await calendar.days_of_year(year)
        return {"year": int(year), "days": days}
    except HolidayError as exc:
        raise _bad_request(exc)


@router.put("/{day}")
async def mark_holiday(
    day: str,
    payload: HolidayRequest,
    db=Depends(get_db),
    _: str = Depends(require_session),
):
    """把这一天标成法定节假日。同一天重复标记是覆盖那一行（`created` 说清是哪种）。"""
    calendar = HolidayCalendar(db)
    try:
        return await calendar.mark(day, payload.name)
    except HolidayError as exc:
        raise _bad_request(exc)


@router.delete("/{day}")
async def unmark_holiday(
    day: str,
    db=Depends(get_db),
    _: str = Depends(require_session),
):
    """取消这一天的标记。那天本来就没标也不报错（`removed` 是 False）。"""
    calendar = HolidayCalendar(db)
    try:
        return await calendar.unmark(day)
    except HolidayError as exc:
        raise _bad_request(exc)
