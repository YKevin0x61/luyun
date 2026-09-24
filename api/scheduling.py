#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班的 HTTP 适配层。规则在 `services/scheduling/store.py`，这里只管翻译。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.security import require_session, require_staff_session
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
    "invalid_anchor": "轮转规则的起点日不合法（应该是 YYYY-MM-DD）：请重新配一遍这条规则",
    "invalid_month": "月份格式应该是 YYYY-MM",
    "invalid_business_date": "日期格式应该是 YYYY-MM-DD",
    "unknown_shift": "班次不存在或已停用",
    # 带一个 `{}`：服务层会把出错的那一格序号放在 `SchedulingError.args[0]`（票 04 要求
    # 「说清哪里错」——「排班参数不合法」说不出是第 3 格写错了）。同一个事实只在下面
    # `_bad_request` 里解释一次。
    "unknown_shift_in_cycle": "轮转周期第 {} 格引用的班次不存在或已停用：请重新选那一天的班次",
    "unknown_employee": "员工不存在",
    "unknown_zone": "责任区不存在：请先在卫生的责任区页面新建，或刷新本页",
    # 单日覆盖（票 07）三种「这天改不了」的原因，各说各的：
    "past_day": "已经过去的日子改不了：排班写下的历史不重写",
    # 带一个 `{}`：服务层把展开窗口的末日放在 `args[0]`。**不写死「90 天」** ——
    # 窗口长度是 `EXPANSION_DAYS` 的事，改那个数不该让文案说谎。
    "beyond_window": "这天还没排到：排班只铺到 {}，等它进窗口再改",
    "missing_shift": "要改班次就得给一个班次；班次留空表示那天休，请用「改成休」",
    "rest_with_details": "「改成休」的那天不能再带班次或责任区：请把这两项留空",
    "not_migrated": (
        "排班表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
        "migrations/pg/0005_scheduling.sql、0006_scheduling_zone_defaults.sql 与 "
        "0007_scheduling_overrides.sql，然后刷新本页"
    ),
}

# `not_migrated` 不是「参数写错了」，是这台机器还没升级完 —— 503 比 400 诚实。
_ERROR_STATUS = {"unknown_employee": 404, "not_migrated": 503}


class SetRuleRequest(BaseModel):
    # 一格 = 一个营业日：班次 id 或 null（休）。周期天数 = 数组长度。
    cycle: list[Optional[int]] = Field(..., min_length=1, max_length=MAX_CYCLE_DAYS)
    anchor_date: Optional[str] = None


class SetZoneDefaultRequest(BaseModel):
    # 哪个班次配哪个区；`zone_id=None` = 清掉这个配置（这个人这个班次「还没定在哪」）。
    shift_id: int
    zone_id: Optional[int] = None


class SetOverrideRequest(BaseModel):
    # 某人某一天改成什么。`is_rest=True` 时班次与责任区都得留空（那天就是休）；
    # 否则 `shift_id` 必给、`zone_id=None` 表示「跟这个班次的固定区」。
    is_rest: bool = False
    shift_id: Optional[int] = None
    zone_id: Optional[int] = None


def _bad_request(exc: SchedulingError) -> HTTPException:
    detail = _ERROR_DETAILS.get(exc.code, "排班参数不合法")
    if "{}" in detail:
        # `SchedulingError(code, message)` 的 message 放在 args[0]（默认等于 code），
        # 服务层用它带「哪里错」的细节，例如周期里出错的那一格序号。
        # 用 `str.replace` 而不是 `str.format`：模板里多一个花括号、或哪天文案里出现
        # 字面 `{...}`，`format` 会抛 KeyError/IndexError/ValueError 变成 500 —— `replace`
        # 没有这些语义。服务层没给细节时填「？」，绝不把字面的 `{}` 端给店长看。
        raw = exc.args[0] if exc.args else ""
        if not raw or raw == exc.code:
            logger.warning("排班错误缺细节，用「？」占位: code=%s", exc.code)
        detail = detail.replace("{}", raw if raw and raw != exc.code else "？")
    logger.info("排班请求被拒: code=%s", exc.code)
    return HTTPException(status_code=_ERROR_STATUS.get(exc.code, 400), detail=detail)


@router.get("/me")
async def my_days(
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """员工自己这几天的班（「今天」页读的就是这一条）。

    跟旁边那七条不是一扇门：那七条是店长的（管理端会话，六条路径），这条是员工自己的
    （手机端 cookie）。**接口上故意没有 `employee_id` 参数** —— 员工会话能读到的只有自己，
    想读别人的班也没地方填。
    """
    store = SchedulingStore(db)
    try:
        data = await store.my_days(employee["id"])
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    return {
        "employee": {"id": employee["id"], "name": employee["name"]},
        "today": data["today"],
        "days": data["days"],
    }


@router.get("/me/month")
async def my_month(
    month: Optional[str] = Query(None, description="YYYY-MM；不填就是本营业月"),
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """员工自己那一个月的班（「今天」页点「整月」进来）。

    跟 `/me` 同一扇门、同一套口径（`scheduled` / `shift_id` / `shift_name`），只是
    窗口一个月；`month` 只决定看哪个月，决定不了看谁 —— **接口上同样没有
    `employee_id`**。月份写错是 400（`invalid_month`），不是 500。
    """
    store = SchedulingStore(db)
    try:
        data = await store.my_month(employee["id"], month)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    return {
        "employee": {"id": employee["id"], "name": employee["name"]},
        "month": data["month"],
        "first_date": data["first_date"],
        "today": data["today"],
        "lead": data["lead"],
        "window_end": data["window_end"],
        "days": data["days"],
    }


@router.get("/shifts")
async def list_shifts(db=Depends(get_db), _: str = Depends(require_session)) -> dict:
    """班次表（白班、夜班……）。UI 按 N 个班次渲染，不写死两个。"""
    store = SchedulingStore(db)
    try:
        return {"shifts": await store.list_shifts()}
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


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
    """全体花名册 + 每人当前那条规则 + 每人每班次的固定责任区（没配就是 `{}`）。

    名单面板会一起读固定责任区（票 03），所以缺 0006 时这里也要出 503 那句话 ——
    不然「已更新代码、还没应用迁移」这段窗口里，店长看到的是 500。
    """
    store = SchedulingStore(db)
    try:
        data = await store.roster_with_rules()
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    # 周期能写多少天由服务层的常量说了算：规则编辑页照这个数校验，别在前端再写死一份。
    data["max_cycle_days"] = MAX_CYCLE_DAYS
    return data


@router.put("/zone-defaults/{employee_id}")
async def set_zone_default(
    employee_id: int,
    payload: SetZoneDefaultRequest,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """给「某人 × 某班次」定一个固定责任区；今天以后已经铺好的行一起改。

    责任区名单是卫生建的那份（公共层读出来），所以这边新建完区不用重启就能选到。
    """
    store = SchedulingStore(db)
    try:
        return await store.set_zone_default(employee_id, payload.shift_id, payload.zone_id)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


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


@router.put("/overrides/{employee_id}/{business_date}")
async def set_override(
    employee_id: int,
    business_date: str,
    payload: SetOverrideRequest,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """改某人某一天：换班次、改成休、或只换责任区 —— **只动这一天**（票 07）。

    改过的那天在月历上有标记，改规则不会把它冲掉；撤掉覆盖（`DELETE` 同一条路径）
    那天就回到规则铺出来的样子。过去的日子不给改（400 `past_day`）。
    """
    store = SchedulingStore(db)
    try:
        return await store.set_override(
            employee_id,
            business_date,
            is_rest=payload.is_rest,
            shift_id=payload.shift_id,
            zone_id=payload.zone_id,
        )
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.delete("/overrides/{employee_id}/{business_date}")
async def clear_override(
    employee_id: int,
    business_date: str,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """撤掉某人某天的覆盖，那天回到规则铺出来的样子（票 07）。

    今天以后按**现在的规则**重算；已经过去的日子只把覆盖记录摘掉，结果行不动
    （过去就是过去：撤销不重写历史，界面靠 `day_detail.undoable` 把按钮收起来）。
    """
    store = SchedulingStore(db)
    try:
        return await store.clear_override(employee_id, business_date)
    except SchedulingError as exc:
        # 日期格式不对、管理员没应用 0007 都会走到这里 —— 跟 PUT 那条一样的出口。
        raise _bad_request(exc) from exc
