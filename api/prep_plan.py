#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
备货计划 API。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from database import get_db
from services.prep_plan_service import prep_plan_service
from api.security import verify_admin_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/prep-plan", tags=["备货计划"])
_ADMIN_WRITE = [Depends(verify_admin_token)]




class GeneratePlanRequest(BaseModel):
    target_start: Optional[str] = None
    target_end: Optional[str] = None
    method: str = "weighted_history"
    created_by: str = ""
    station: Optional[str] = None


class CreateBatchRequest(BaseModel):
    item_name: str = Field(..., min_length=1)
    produced_qty: float = Field(..., gt=0)
    unit: str = Field("", min_length=0)
    produced_at: Optional[str] = None
    operator: str = ""
    notes: str = ""


class UpdateBatchRequest(BaseModel):
    remaining_qty: Optional[float] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    operator: str = ""


class CreateMovementRequest(BaseModel):
    batch_id: int = Field(..., gt=0)
    item_name: str = Field(..., min_length=1)
    unit: str = ""
    movement_type: str
    qty_delta: float
    reason: str = ""
    operator: str = ""
    source_type: str = ""
    source_id: str = ""


@router.get("/forecast")
async def get_forecast(
    target_start: Optional[str] = Query(None, description="ISO 时间，默认 now()"),
    target_end: Optional[str] = Query(None, description="ISO 时间，默认 target_start + 24h"),
    station: Optional[str] = Query(None, description="档口 ID，禁止 loumian"),
    db=Depends(get_db),
):
    try:
        result = await prep_plan_service.compute_forecast(
            db=db,
            target_start=target_start,
            target_end=target_end,
            station=station,
            include_inventory=True,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("forecast 计算失败")
        raise HTTPException(status_code=500, detail=f"预测计算失败: {exc}")


@router.post("/generate", dependencies=_ADMIN_WRITE)
async def generate_plan(payload: GeneratePlanRequest, db=Depends(get_db)):
    try:
        result = await prep_plan_service.create_plan_run(
            db=db,
            target_start=payload.target_start,
            target_end=payload.target_end,
            method=payload.method,
            created_by=payload.created_by,
            station=payload.station,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("生成计划失败")
        raise HTTPException(status_code=500, detail=f"生成计划失败: {exc}")


@router.get("/current")
async def get_current_plan(db=Depends(get_db)):
    try:
        return await prep_plan_service.get_current_plan(db)
    except Exception as exc:
        logger.exception("查询当前计划失败")
        raise HTTPException(status_code=500, detail=f"查询当前计划失败: {exc}")


@router.post("/batches", dependencies=_ADMIN_WRITE)
async def create_batch(payload: CreateBatchRequest, db=Depends(get_db)):
    try:
        return await prep_plan_service.record_batch(
            db=db,
            item_name=payload.item_name,
            produced_qty=payload.produced_qty,
            unit=payload.unit,
            produced_at=payload.produced_at,
            operator=payload.operator,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("创建备货批次失败")
        raise HTTPException(status_code=500, detail=f"创建备货批次失败: {exc}")


@router.post("/batches/{batch_id}/undo", dependencies=_ADMIN_WRITE)
async def undo_batch(batch_id: int, db=Depends(get_db)):
    try:
        return await prep_plan_service.undo_record(db, batch_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="批次不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("撤销登记失败")
        raise HTTPException(status_code=500, detail=f"撤销登记失败: {exc}")


@router.post("/batches/{batch_id}/discard", dependencies=_ADMIN_WRITE)
async def discard_batch(batch_id: int, db=Depends(get_db)):
    try:
        return await prep_plan_service.discard_batch(db, batch_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="批次不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("报废批次失败")
        raise HTTPException(status_code=500, detail=f"报废批次失败: {exc}")


@router.patch("/batches/{batch_id}", dependencies=_ADMIN_WRITE)
async def update_batch(batch_id: int, payload: UpdateBatchRequest, db=Depends(get_db)):
    try:
        return await prep_plan_service.update_batch(
            db,
            batch_id,
            remaining_qty=payload.remaining_qty,
            status=payload.status,
            notes=payload.notes,
            operator=payload.operator,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="批次不存在")
    except Exception as exc:
        logger.exception("更新备货批次失败")
        raise HTTPException(status_code=500, detail=f"更新备货批次失败: {exc}")


@router.get("/movements")
async def get_movements(
    item_name: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    db=Depends(get_db),
):
    try:
        return await prep_plan_service.list_movements(
            db,
            item_name=item_name,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
    except Exception as exc:
        logger.exception("查询库存流水失败")
        raise HTTPException(status_code=500, detail=f"查询库存流水失败: {exc}")


@router.post("/movements", dependencies=_ADMIN_WRITE)
async def create_movement(payload: CreateMovementRequest, db=Depends(get_db)):
    try:
        return await prep_plan_service.create_movement(
            db,
            batch_id=payload.batch_id,
            item_name=payload.item_name,
            movement_type=payload.movement_type,
            qty_delta=payload.qty_delta,
            unit=payload.unit,
            reason=payload.reason,
            operator=payload.operator,
            source_type=payload.source_type,
            source_id=payload.source_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="批次不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("新增库存流水失败")
        raise HTTPException(status_code=500, detail=f"新增库存流水失败: {exc}")


@router.get("/expiring")
async def get_expiring_batches(
    within_hours: int = Query(4, ge=1, le=72),
    db=Depends(get_db),
):
    try:
        return await prep_plan_service.list_expiring_batches(db, within_hours=within_hours)
    except Exception as exc:
        logger.exception("查询临期批次失败")
        raise HTTPException(status_code=500, detail=f"查询临期批次失败: {exc}")


@router.get("/accuracy")
async def get_accuracy(
    start_date: str = Query(...),
    end_date: str = Query(...),
    db=Depends(get_db),
):
    """
    MVP3 基础版准确率：按计划项聚合统计 abs(预测-实际)/实际。
    实际值暂以 orders + semi_rules 换算（简化版：同周期总量）。
    """
    try:
        return await prep_plan_service.get_accuracy(db, start_date, end_date)
    except Exception as exc:
        logger.exception("查询准确率失败")
        raise HTTPException(status_code=500, detail=f"查询准确率失败: {exc}")


@router.post("/init-items-from-rules", dependencies=_ADMIN_WRITE)
async def init_items_from_rules(db=Depends(get_db)):
    """
    从 semi_finished_rules 自动补齐 prep_items（仅新增不存在的 item_name+unit）。
    """
    try:
        return await prep_plan_service.init_items_from_rules(db)
    except Exception as exc:
        logger.exception("初始化 prep_items 失败")
        raise HTTPException(status_code=500, detail=f"初始化 prep_items 失败: {exc}")
