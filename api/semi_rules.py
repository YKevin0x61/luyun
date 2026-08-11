#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
半成品换算规则 API
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from database import DatabaseManager, get_db
from api.security import verify_admin_token
from services.dish_catalog import get_dish_catalog

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/semi-rules", tags=["半成品规则"])
_ADMIN_WRITE = [Depends(verify_admin_token)]

CHINA_TZ = timezone(timedelta(hours=8))


class RuleCreate(BaseModel):
    dish_name: str
    semi_name: str
    position: str = ""
    factor: float = 1.0
    unit: str = ""
    category: str = ""
    notes: str = ""


class RuleUpdate(BaseModel):
    semi_name: Optional[str] = None
    position: Optional[str] = None
    factor: Optional[float] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    notes: Optional[str] = None


# ---- 规则 CRUD ----

@router.get("/")
async def list_rules(db: DatabaseManager = Depends(get_db)):
    """列出全部规则"""
    rules = await db.semi_rules_all()
    return {"success": True, "rules": rules, "total": len(rules)}


@router.get("/search")
async def search_rules(
    q: str = Query("", description="搜索关键词"),
    db: DatabaseManager = Depends(get_db)
):
    """搜索规则（按菜品名模糊搜索）"""
    all_rules = await db.semi_rules_all()
    q_lower = q.lower().strip()
    if q_lower:
        filtered = [r for r in all_rules if q_lower in r.get("dish_name", "").lower()]
    else:
        filtered = all_rules
    return {"success": True, "rules": filtered, "total": len(filtered)}


@router.post("/", dependencies=_ADMIN_WRITE)
async def create_rule(
    rule: RuleCreate,
    db: DatabaseManager = Depends(get_db)
):
    """创建规则（已存在则更新）"""
    ok = await db.semi_rules_upsert(rule.model_dump())
    if not ok:
        raise HTTPException(status_code=500, detail="保存失败")
    return {"success": True, "message": "保存成功"}


@router.put("/{rule_id}", dependencies=_ADMIN_WRITE)
async def update_rule(
    rule_id: int,
    update: RuleUpdate,
    db: DatabaseManager = Depends(get_db)
):
    """更新规则"""
    existing = await db.semi_rules_get(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="规则不存在")

    merged = {**existing, **{k: v for k, v in update.model_dump().items() if v is not None}}
    await db.semi_rules_delete(rule_id)
    new_id = await db.semi_rules_upsert(merged)
    if not new_id:
        raise HTTPException(status_code=500, detail="更新失败")
    return {"success": True, "message": "更新成功"}


@router.delete("/{rule_id}", dependencies=_ADMIN_WRITE)
async def delete_rule(
    rule_id: int,
    db: DatabaseManager = Depends(get_db)
):
    """删除规则"""
    ok = await db.semi_rules_delete(rule_id)
    return {"success": True, "message": "删除成功"}


@router.get("/dishes/available")
async def get_available_dishes(
    db: DatabaseManager = Depends(get_db)
):
    """获取数据库中已有菜品名（用于快速创建规则时选择）"""
    dishes = await db.orders.get_unique_dish_names()
    # 过滤掉已有规则的菜品
    all_rules = await db.semi_rules_all()
    existing = {r["dish_name"] for r in all_rules}
    available = [d for d in dishes if d not in existing]
    return {"success": True, "dishes": available, "total": len(available)}


@router.get("/dishes/grouped")
async def get_dishes_grouped(
    db: DatabaseManager = Depends(get_db),
    dish_catalog=Depends(get_dish_catalog),
):
    """获取所有菜品（含规则状态），按档口分组"""
    all_rules = await db.semi_rules_all()
    rules_map: Dict[str, List[Dict]] = {}
    for r in all_rules:
        if r["dish_name"] not in rules_map:
            rules_map[r["dish_name"]] = []
        rules_map[r["dish_name"]].append(r)

    # 历史频次兜底（展示用，非 Dish Catalog 权威映射）
    station_info = await db._get_dish_station_mapping()
    explicit_map = await dish_catalog.as_dict()

    # 合并：explicit_map > station_info
    result: Dict[str, List[Dict]] = {}
    for dish_name, rules in rules_map.items():
        station = explicit_map.get(dish_name, station_info.get(dish_name, "未知"))
        group_key = station
        if group_key not in result:
            result[group_key] = []
        result[group_key].append({
            "dish_name": dish_name,
            "station": station,
            "has_rules": True,
            "rules": rules,
        })

    # 加上没有规则的菜品
    all_dish_names = await db.orders.get_unique_dish_names()
    for dish_name in all_dish_names:
        if dish_name in rules_map:
            continue
        station = explicit_map.get(dish_name, station_info.get(dish_name, "未知"))
        group_key = station
        if group_key not in result:
            result[group_key] = []
        result[group_key].append({
            "dish_name": dish_name,
            "station": station,
            "has_rules": False,
            "rules": [],
        })

    # 按档口排序
    ordered = {}
    for key in sorted(result.keys()):
        ordered[key] = result[key]

    return {"success": True, "stations": ordered}
