#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划 API。

**票 08 起模块里有两条 router**（形态照票 07 的 `api/recipes.py`）：

- `reader_router` —— 读面。备货计划搬进工作台的「后勤」组
  （`/workbench/kitchen/prep-plan`，页面清单里是 `audience: both`），后厨在工作台里
  得看得到"今天要备什么"，所以两条读端点（forecast / current）
  挂的是「任一身份」门 `require_any_identity_session`（管理端会话或员工会话）。
  门本身在 `api/security.py`，注册处是 `main.py`（**不挂** verify_admin_token）。
- `router` —— 业务面（写端点 + 不属于阅读面的那些）。注册处统一挂
  `verify_admin_token`：**进工作台只是换位置与统一导航，不扩权**（ADR 0092）。

两条 router 的路径前缀相同，靠"哪条路由挂在哪个 router 上"分工 —— 新增端点时先想清楚
它是读还是写，别顺手挂错。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_db
from services.prep_plan_service import prep_plan_service
from api.security import require_any_identity_session, verify_admin_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/prep-plan", tags=["备货计划"])

# 读面（任一身份：管理端会话或员工会话）。与 `router` 同前缀，见模块开头那段说明。
# 门挂在 **router 级**（一处，两条读端点一起受保护）：读面没有"某一条只给管理端"的分支，
# 「含停用」那种按身份裁剪的参数备货计划这边不存在 —— 有分支了再往单条路由上挂。
reader_router = APIRouter(
    prefix="/api/prep-plan",
    tags=["备货计划"],
    dependencies=[Depends(require_any_identity_session)],
)

_ADMIN_WRITE = [Depends(verify_admin_token)]




class GeneratePlanRequest(BaseModel):
    target_start: Optional[str] = None
    target_end: Optional[str] = None
    method: str = "weighted_history"
    created_by: str = ""
    station: Optional[str] = None


@reader_router.get("/forecast")
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


@reader_router.get("/current")
async def get_current_plan(db=Depends(get_db)):
    try:
        return await prep_plan_service.get_current_plan(db)
    except Exception as exc:
        logger.exception("查询当前计划失败")
        raise HTTPException(status_code=500, detail=f"查询当前计划失败: {exc}")


class ClosedDayRequest(BaseModel):
    day: str
    note: str = ""


@reader_router.get("/closed-days")
async def get_closed_days(db=Depends(get_db)):
    """歇业日日历：读面（任一身份）—— 员工也该看得出「今天为什么没建议」。"""
    try:
        return await prep_plan_service.list_closed_days(db)
    except Exception as exc:
        logger.exception("查询歇业日失败")
        raise HTTPException(status_code=500, detail=f"查询歇业日失败: {exc}")


@router.post("/closed-days", dependencies=_ADMIN_WRITE)
async def add_closed_day(payload: ClosedDayRequest, db=Depends(get_db)):
    try:
        return await prep_plan_service.add_closed_day(db, payload.day, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("新增歇业日失败")
        raise HTTPException(status_code=500, detail=f"新增歇业日失败: {exc}")


@router.delete("/closed-days/{day}", dependencies=_ADMIN_WRITE)
async def remove_closed_day(day: str, db=Depends(get_db)):
    try:
        return await prep_plan_service.remove_closed_day(db, day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("删除歇业日失败")
        raise HTTPException(status_code=500, detail=f"删除歇业日失败: {exc}")


@reader_router.get("/accuracy")
async def get_accuracy(
    start_date: str = Query(...),
    end_date: str = Query(...),
    db=Depends(get_db),
):
    """
    MVP3 基础版准确率：按计划项聚合统计 abs(预测-实际)/实际。
    实际值暂以 orders + semi_rules 换算（简化版：同周期总量）。

    **挂在 reader_router 上**（票 08 的原实现把它留在业务 router，code-review 指出这与
    spec 的 API 契约表不符 —— 那张表写的是「备货计划读端点：仅管理端 → 任一身份」，没有
    给「页面没消费的读端点」留例外）。它只读、没有副作用，放宽到任一身份不扩权；写端点
    仍然只认管理端。
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
