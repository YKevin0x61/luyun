#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报表固定菜品 API
"""

import logging
from typing import List

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from database import DatabaseManager, get_db
from api.security import verify_admin_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/report-dishes", tags=["报表固定菜品"])
_ADMIN_WRITE = [Depends(verify_admin_token)]


class ReportDishAdd(BaseModel):
    dish_name: str
    notes: str = ""


class ReportDishReorder(BaseModel):
    ids: List[int]


@router.get("/")
async def list_report_dishes(db: DatabaseManager = Depends(get_db)):
    """列出全部固定报表菜品"""
    dishes = await db.report_dishes_all()
    return {"success": True, "dishes": dishes, "total": len(dishes)}


@router.post("/", dependencies=_ADMIN_WRITE)
async def add_report_dish(
    item: ReportDishAdd,
    db: DatabaseManager = Depends(get_db)
):
    """添加固定报表菜品"""
    if not item.dish_name.strip():
        raise HTTPException(status_code=400, detail="菜品名称不能为空")
    new_id = await db.report_dishes_add(item.dish_name.strip(), item.notes)
    if not new_id:
        raise HTTPException(status_code=500, detail="添加失败，菜品可能已存在")
    return {"success": True, "id": new_id}


@router.delete("/{dish_id}", dependencies=_ADMIN_WRITE)
async def remove_report_dish(
    dish_id: int,
    db: DatabaseManager = Depends(get_db)
):
    """删除固定报表菜品"""
    ok = await db.report_dishes_remove(dish_id)
    return {"success": True, "message": "删除成功"}


@router.put("/reorder", dependencies=_ADMIN_WRITE)
async def reorder_report_dishes(
    payload: ReportDishReorder,
    db: DatabaseManager = Depends(get_db)
):
    """更新固定报表菜品顺序"""
    ok = await db.report_dishes_reorder(payload.ids)
    if not ok:
        raise HTTPException(status_code=500, detail="排序更新失败")
    return {"success": True, "message": "排序已更新"}
