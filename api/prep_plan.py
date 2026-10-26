#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划 API（ADR 0117 重建）。

两条 router，形态照 `api/recipes.py`：

- `reader_router` —— **任一身份**（管理端会话或员工会话）。今天这张清单、首页卡片、
  员工选档口都在这一侧：师傅要能自己打开看参考值。

  ADR 0121 起**不登记产量、也不维护在库量**：页面给的是「今天参考值」，
  师傅拿它减掉自己看到的实物库存，决定还要备多少。补货 / 复盘 / 报废三个端点随之退场。
- `router` —— 管理端（注册处挂 `verify_admin_token`，见 `main.py`）。重算与改主数据
  归属属于「改全店的数」，员工不做。

新增端点时先想清楚：这条是「看/勾自己那档」还是「改全店」。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.security import require_any_identity_session, verify_admin_token
from database import get_db
from services.prep_plan_service import prep_plan_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prep-plan", tags=["备货计划"])
reader_router = APIRouter(
    prefix="/api/prep-plan",
    tags=["备货计划"],
    dependencies=[Depends(require_any_identity_session)],
)

_ADMIN_WRITE = [Depends(verify_admin_token)]


def _staff(identity: dict) -> tuple[Optional[int], str]:
    """员工身份返回 (员工 id, 姓名)；管理端返回 (None, "")。"""
    if identity.get("kind") != "staff":
        return None, ""
    employee = identity.get("employee") or {}
    employee_id = int(employee["id"]) if employee.get("id") is not None else None
    return employee_id, str(employee.get("name") or "")


def _wrap(exc: Exception, action: str) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail="只能操作自己档口的备货项")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("%s失败", action)
    return HTTPException(status_code=500, detail=f"{action}失败: {exc}")


# --------------------------------------------------------------------- 读面


@reader_router.get("/today")
async def get_today(
    station: Optional[str] = Query(None, description="档口 id；员工身份忽略（只看自己那档）"),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    try:
        employee_id, _name = _staff(identity)
        return await prep_plan_service.get_today(db, station=station, employee_id=employee_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap(exc, "读取今天备货清单")


@reader_router.get("/summary")
async def get_summary(db=Depends(get_db)):
    try:
        return await prep_plan_service.get_summary(db)
    except Exception as exc:
        raise _wrap(exc, "读取备货汇总")


@router.get("/snapshots", dependencies=_ADMIN_WRITE)
async def get_snapshots(day: str = Query(..., description="YYYY-MM-DD"), db=Depends(get_db)):
    """某一天的参考值快照 + 实际销量（ADR 0123），只读，管理端。"""
    try:
        return await prep_plan_service.get_snapshot_day(db, day)
    except Exception as exc:
        raise _wrap(exc, "读取参考值快照")


@reader_router.get("/items")
async def list_items(db=Depends(get_db)):
    try:
        return {"success": True, "items": await prep_plan_service.list_items(db)}
    except Exception as exc:
        raise _wrap(exc, "读取备货品主数据")


class StationPickRequest(BaseModel):
    station: str


@reader_router.get("/staff-station")
async def get_staff_station(
    identity: dict = Depends(require_any_identity_session), db=Depends(get_db)
):
    try:
        employee_id, _name = _staff(identity)
        if employee_id is None:
            return {"success": True, "station": None, "staff": False}
        station = await prep_plan_service.get_staff_station(db, employee_id)
        return {"success": True, "station": station, "staff": True}
    except Exception as exc:
        raise _wrap(exc, "读取员工档口")


@reader_router.post("/staff-station")
async def set_staff_station(
    payload: StationPickRequest,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    try:
        employee_id, _name = _staff(identity)
        if employee_id is None:
            raise HTTPException(status_code=400, detail="只有员工身份需要选档口")
        return await prep_plan_service.set_staff_station(db, employee_id, payload.station)
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap(exc, "选择员工档口")


class UpdateEntryRequest(BaseModel):
    final_qty: Optional[float] = None
    done: Optional[bool] = None


@reader_router.patch("/today/entries/{entry_id}")
async def update_entry(
    entry_id: int,
    payload: UpdateEntryRequest,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    """改数量或勾选。默认带建议量、能改但不强制（ADR 0117）。"""
    try:
        employee_id, done_by = _staff(identity)
        return await prep_plan_service.update_entry(
            db,
            entry_id,
            final_qty=payload.final_qty,
            done=payload.done,
            done_by=done_by,
            employee_id=employee_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap(exc, "更新备货项")


# --------------------------------------------------------------------- 管理端


@router.post("/today/recompute", dependencies=_ADMIN_WRITE)
async def recompute_today(db=Depends(get_db)):
    """按当前时刻重算建议量。手改过的数与勾选状态不动。"""
    try:
        return await prep_plan_service.get_today(db)
    except Exception as exc:
        raise _wrap(exc, "重算备货清单")


class UpdateItemRequest(BaseModel):
    kind: Optional[str] = None
    station: Optional[str] = None


@router.patch("/items/{item_id}", dependencies=_ADMIN_WRITE)
async def update_item(item_id: int, payload: UpdateItemRequest, db=Depends(get_db)):
    """改「半成品 / 原料」归属或所属档口（ADR 0117：系统先猜，人在页面上改）。"""
    try:
        return await prep_plan_service.update_item(
            db, item_id, kind=payload.kind, station=payload.station
        )
    except Exception as exc:
        raise _wrap(exc, "更新备货品")
