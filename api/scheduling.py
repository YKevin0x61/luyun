#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班的 HTTP 适配层。规则在 `services/scheduling/store.py`，这里只管翻译。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.security import require_session
from database import get_db
from services.scheduling import MAX_CYCLE_DAYS
from services.scheduling.store import SchedulingError, SchedulingStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduling", tags=["排班"])

# 服务层的 `SchedulingError.code` 是稳定标识，不是给店长看的文案。这里把每个 code
# 翻成一句能照着改的话 —— 「排班参数不合法」那种一句话会把「周期太长」和「这天日期
# 打错了」说成同一件事（票 04 的规则编辑页要能说清哪里错）。
_ERROR_DETAILS = {
    "invalid_cycle": f"轮转周期不合法：1 到 {MAX_CYCLE_DAYS} 天，每格是班次或休",
    "invalid_month": "月份格式应该是 YYYY-MM",
    "invalid_business_date": "日期格式应该是 YYYY-MM-DD",
    "unknown_shift": "班次不存在或已停用",
    "unknown_employee": "员工不存在",
    "not_migrated": (
        "排班表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
        "migrations/pg/0005_scheduling.sql，然后刷新本页"
    ),
}

# `not_migrated` 不是「参数写错了」，是这台机器还没升级完 —— 503 比 400 诚实。
_ERROR_STATUS = {"unknown_employee": 404, "not_migrated": 503}


class SetRuleRequest(BaseModel):
    # 一格 = 一个营业日：班次 id 或 null（休）。周期天数 = 数组长度。
    cycle: list[Optional[int]] = Field(..., min_length=1, max_length=MAX_CYCLE_DAYS)
    anchor_date: Optional[str] = None


def _bad_request(exc: SchedulingError) -> HTTPException:
    detail = _ERROR_DETAILS.get(exc.code, "排班参数不合法")
    logger.info("排班请求被拒: code=%s", exc.code)
    return HTTPException(status_code=_ERROR_STATUS.get(exc.code, 400), detail=detail)


@router.get("/shifts")
async def list_shifts(db=Depends(get_db), _: str = Depends(require_session)) -> dict:
    """班次表（白班、夜班……）。UI 按 N 个班次渲染，不写死两个。"""
    return {"shifts": await SchedulingStore(db).list_shifts()}


@router.get("/calendar")
async def month_calendar(
    month: str = Query(..., description="YYYY-MM"),
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """一个月的月历：每天各班几个人。打开就先补齐未来 90 天（幂等）。"""
    store = SchedulingStore(db)
    try:
        await store.expand()
        return await store.month_calendar(month)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.get("/day")
async def day_detail(
    date: str = Query(..., alias="date", description="YYYY-MM-DD"),
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """某一天各班是谁（月历点开那天看的就是这个）。"""
    store = SchedulingStore(db)
    try:
        await store.expand()
        return await store.day_detail(date)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.get("/roster")
async def roster(db=Depends(get_db), _: str = Depends(require_session)) -> dict:
    """全体花名册 + 每人当前那条规则（没配的人 `rule` 是 `null`）。"""
    return await SchedulingStore(db).roster_with_rules()


@router.put("/rules/{employee_id}")
async def set_rule(
    employee_id: int,
    payload: SetRuleRequest,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """给一个人配轮转规则；今天以后的规则行立刻重铺，月历随即可见。"""
    store = SchedulingStore(db)
    try:
        return await store.set_rule(employee_id, payload.cycle, payload.anchor_date)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.delete("/rules/{employee_id}")
async def clear_rule(
    employee_id: int,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """拿掉一个人的规则（连今天以后的规则行）。过去写过的不动。"""
    store = SchedulingStore(db)
    try:
        await store.clear_rule(employee_id)
    except SchedulingError as exc:  # pragma: no cover - 现在不会抛，留个一致的出口
        raise _bad_request(exc) from exc
    return {"employee_id": employee_id, "rule": None}
