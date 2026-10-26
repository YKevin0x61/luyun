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
from services.prep_rule_groups import group_key, group_rows, pick_main_name
from services.prep_plan_service import POSITION_STATION_MAP

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
    # 备货品主数据对上的那一行（迁移 0035 起）；为空时回落按名字匹配
    prep_item_id: Optional[int] = None
    category: str = ""
    notes: str = ""


class RuleUpdate(BaseModel):
    semi_name: Optional[str] = None
    position: Optional[str] = None
    factor: Optional[float] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    notes: Optional[str] = None
    prep_item_id: Optional[int] = None


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


@router.get("/positions")
async def list_positions():
    """岗位清单（含它映射到哪个档口）。

    这是**唯一一份**：前端不再各写一遍数组。之前前端有「明档2」、后端映射里没有，
    选了就落到空档口（库里 3 个品已经这么空了）。
    """
    return {
        "success": True,
        "positions": [
            {"name": name, "station": station}
            for name, station in sorted(POSITION_STATION_MAP.items())
        ],
    }


@router.get("/prep-items")
async def list_prep_items(db: DatabaseManager = Depends(get_db)):
    """备货品主数据（精简）：换算规则页选品用。

    岗位 / 单位 / 分类都挂在备货品上（谁做这个半成品、按什么计量），
    规则页只负责「这道菜用多少个」，所以选品时把这些一起带出来。
    """
    items = await db.prep_items_simple()
    return {"success": True, "items": items, "total": len(items)}


class PrepItemCreate(BaseModel):
    item_name: str
    unit: str = ""
    position: str = ""
    kind: str = "made"


@router.post("/prep-items", dependencies=_ADMIN_WRITE)
async def create_prep_item(body: PrepItemCreate, db: DatabaseManager = Depends(get_db)):
    """新建备货品（换算规则页选品时，清单里没有的那个）。

    只收最小信息：名字、单位、岗位、半成品/原料。档口由岗位映射派生
    （与备货计划同一张 `POSITION_STATION_MAP`）。

    **分类不再单独收**：`kind` 的二分（made / bought）就是分类，
    `category` 由它派生（半成品 / 原料）。以前弹窗里两个下拉选项一模一样，
    填出过「category=原料 但 kind=made」这种互相矛盾的组合。
    """
    name = (body.item_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="备货品名称不能为空")
    if body.kind not in ("made", "bought"):
        raise HTTPException(status_code=400, detail="kind 只能是 made / bought")
    existing = await db.prep_item_find(name)
    if existing:
        raise HTTPException(status_code=409, detail=f"清单里已经有「{name}」了")
    item = {
        "item_name": name,
        "unit": (body.unit or "").strip(),
        "position": (body.position or "").strip(),
        "category": "半成品" if body.kind == "made" else "原料",
        "kind": body.kind,
        "station": POSITION_STATION_MAP.get((body.position or "").strip(), ""),
    }
    created = await db.prep_item_create(item)
    if not created:
        raise HTTPException(status_code=500, detail="新建失败")
    return {"success": True, "item": created}


@router.get("/board")
async def get_rule_board(
    days: int = Query(90, ge=1, le=365, description="销量回看天数"),
    include_dormant: bool = Query(False, description="待配里是否带上 90 天没卖的菜"),
    db: DatabaseManager = Depends(get_db),
    dish_catalog=Depends(get_dish_catalog),
):
    """换算规则板：按「逻辑菜」分组，带覆盖率与近 N 天销量。

    与 `/dishes/grouped` 的区别：那个按档口分组、按原始菜名逐行；这个把外卖/规格变体
    折进同一个「逻辑菜」。143 条规则里 52 条是变体，按行展示重复得没法改，而店长的
    心智单位是「这道菜用多少料」。分组只在展示层（`services/prep_rule_groups`），
    计算侧口径不动。

    楼面档（茶饮 / 耗材 / 渠道费）不进覆盖率——那些菜不需要配配方系数。
    """
    rows, schema_ready = await db.semi_rules_board_rows()
    since = datetime.now(CHINA_TZ) - timedelta(days=days)
    qty_map = await db.orders_dish_qty_since(since)

    explicit_map = await dish_catalog.as_dict()
    station_info = await db._get_dish_station_mapping()

    def station_of(name: str) -> str:
        return explicit_map.get(name) or station_info.get(name) or "未知"

    active: Dict[str, float] = {}
    active_names: Dict[str, List[str]] = {}
    for name, qty in qty_map.items():
        if station_of(name) == "loumian":
            continue
        key = group_key(name)
        active[key] = active.get(key, 0.0) + qty
        active_names.setdefault(key, []).append(name)

    grouped = group_rows(rows)

    groups: List[Dict] = []
    for key, group_rows_ in grouped.items():
        names = [r["dish_name"] for r in group_rows_]
        variants = []
        for r in group_rows_:
            name = r["dish_name"]
            prep_unit = r.get("prep_item_unit")
            variants.append(
                {
                    "id": r["id"],
                    "dish_name": name,
                    "semi_name": r.get("semi_name") or "",
                    "prep_item_id": r.get("prep_item_id"),
                    "prep_item_name": r.get("prep_item_name"),
                    "prep_item_unit": prep_unit,
                    "prep_item_kind": r.get("prep_item_kind"),
                    "prep_item_position": r.get("prep_item_position"),
                    "factor": float(r.get("factor") or 0),
                    "unit": r.get("unit") or "",
                    "category": r.get("category") or "",
                    "position": r.get("position") or "",
                    "is_takeout": name.startswith("(外卖"),
                    # 规则写的单位与备货品主数据不一致：只标记，不自动改数
                    "unit_drift": bool(prep_unit) and (r.get("unit") or "") != prep_unit,
                }
            )
        main_name = pick_main_name(names)
        groups.append(
            {
                "key": key,
                "main_name": main_name,
                "station": station_of(main_name),
                "has_rules": True,
                # 同一组里「金莎脆皮红米肠」会被多条规则引用（每个备货品一条），
                # 销量是这道菜的销量，只能算一次
                "qty": sum(qty_map.get(n, 0.0) for n in set(names)),
                "variants": variants,
            }
        )

    for key, qty in active.items():
        if key in grouped:
            continue
        names = active_names.get(key, [])
        main_name = pick_main_name(names) if names else key
        groups.append(
            {
                "key": key,
                "main_name": main_name,
                "station": station_of(main_name),
                "has_rules": False,
                "qty": qty,
                "variants": [],
            }
        )

    # 「全部菜」开关：近 90 天没卖的菜也列进待配（销量 0、排最后）。
    # **不进 active** —— 覆盖率是近 90 天口径，勾一下开关不该把分母撑大。
    # 不绑档口：POS 里还没出过单的菜，档口等 dish_stations 自己长出来。
    if include_dormant:
        seen = set(grouped) | set(active)
        dormant: Dict[str, List[str]] = {}
        for name in await db.orders.get_unique_dish_names():
            if station_of(name) == "loumian":
                continue
            key = group_key(name)
            if key in seen:
                continue
            seen.add(key)
            dormant.setdefault(key, []).append(name)
        for key, names in dormant.items():
            main_name = pick_main_name(names) if names else key
            groups.append(
                {
                    "key": key,
                    "main_name": main_name,
                    "station": station_of(main_name),
                    "has_rules": False,
                    "qty": 0.0,
                    "variants": [],
                    "dormant": True,
                }
            )

    covered_groups = sum(1 for key in active if key in grouped)
    total_qty = sum(active.values())
    covered_qty = sum(qty for key, qty in active.items() if key in grouped)
    coverage = {
        "window_days": days,
        "active_groups": len(active),
        "covered_groups": covered_groups,
        "pending_groups": len(active) - covered_groups,
        "qty_covered_pct": round(covered_qty / total_qty * 100, 1) if total_qty else 0.0,
        "total_rules": len(rows),
        "total_groups": len(grouped),
        "unlinked_rules": sum(1 for r in rows if not r.get("prep_item_id")),
        # False = prep_item_id 列还不存在（迁移 0035 没应用）：页面要提示先跑迁移
        "schema_ready": schema_ready,
    }

    groups.sort(key=lambda g: (not g["has_rules"], -g["qty"], g["main_name"]))
    return {"success": True, "coverage": coverage, "groups": groups}
