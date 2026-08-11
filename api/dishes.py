#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
菜品相关API路由
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Body
from typing import Optional
import logging
from datetime import datetime, timezone, timedelta
import time

CHINA_TZ = timezone(timedelta(hours=8))

from models import SuccessResponse
from database import DatabaseManager, get_db
from services import dish_merger_service
from services.dish_catalog import get_dish_catalog
from api.security import verify_admin_token

logger = logging.getLogger(__name__)
_ADMIN_WRITE = [Depends(verify_admin_token)]

def classify_dish(dish_name: str) -> str:
    """根据菜品名称分类 - 本地分类函数"""
    if "茶" in dish_name or "菊" in dish_name:
        return "茶水"
    elif "点" in dish_name:
        if "佳点" in dish_name:
            return "佳点"
        elif "美点" in dish_name:
            return "美点"
        elif "特点" in dish_name:
            return "特点"
        elif "禄点" in dish_name:
            return "禄点"
        else:
            return "点心"
    elif "凉" in dish_name or "拌" in dish_name:
        return "凉菜"
    elif "蒸" in dish_name or "炸" in dish_name or "炒" in dish_name:
        return "热菜"
    else:
        return "其他"
        
router = APIRouter(prefix="/api/dishes", tags=["菜品管理"])

# 删除本地的get_db函数，直接使用全局的get_db

@router.get("/merged", status_code=200)
async def get_merged_dishes(
    station: Optional[str] = Query(None, description="档口筛选"),
    sort_by: str = Query("time", description="排序方式"),
    priority: Optional[str] = Query(None, description="优先级筛选"),
    db: DatabaseManager = Depends(get_db)
):
    """获取合并后的菜品数据"""
    try:
        logger.info(f"🔄 获取合并菜品: station={station}, sort_by={sort_by}, priority={priority}")
        
        # 获取原始订单数据
        orders = await db.orders.get_orders(
            station=station,
            table_number=None,
            limit=10000
        )
        orders = [
            o for o in orders
            if o.get("dish_status", "待出餐") == "待出餐"
        ]
        
        # 合并菜品数据
        merged_dishes = dish_merger_service.merge_dishes_by_name(orders, station)
        
        # 优先级筛选
        if priority:
            merged_dishes = [
                dish for dish in merged_dishes 
                if dish.get('priority') == priority
            ]
        
        # 排序
        sorted_dishes = dish_merger_service.sort_dishes(merged_dishes, sort_by)
        
        logger.info(f"✅ 返回 {len(sorted_dishes)} 个合并菜品")
        
        # 🆕 统一返回格式
        return {
            "success": True,
            "data": sorted_dishes,
            "count": len(sorted_dishes),
            "timestamp": datetime.now(CHINA_TZ).isoformat()
        }
        
    except Exception as e:
        logger.error(f"获取合并菜品数据失败: {e}")
        raise HTTPException(status_code=500, detail="获取合并菜品数据失败")

@router.get("/merged/paginated", status_code=200)
async def get_merged_dishes_paginated(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    station: Optional[str] = Query(None, description="档口筛选"),
    sort_by: str = Query("time", description="排序方式"),
    priority: Optional[str] = Query(None, description="优先级筛选"),
    db: DatabaseManager = Depends(get_db)
):
    """🆕 分页获取合并菜品数据（性能优化版）"""
    try:
        skip = (page - 1) * page_size
        
        logger.info(f"🔄 分页获取合并菜品 - 页码:{page}, 每页:{page_size}, 档口:{station}")
        
        # 使用优化的数据库方法获取数据
        all_dishes = await db.orders.get_merged_dishes(
            station=station,
            sort_by=sort_by,
            limit_hours=24  # 只查询24小时内数据
        )
        
        # 优先级筛选
        if priority:
            filtered_dishes = [
                dish for dish in all_dishes 
                if dish.get('priority') == priority
            ]
        else:
            filtered_dishes = all_dishes
        
        # 计算分页
        total_count = len(filtered_dishes)
        total_pages = (total_count + page_size - 1) // page_size
        has_next = page < total_pages
        has_prev = page > 1
        
        # 获取当页数据
        start_idx = skip
        end_idx = skip + page_size
        page_data = filtered_dishes[start_idx:end_idx]
        
        logger.info(f"✅ 分页菜品查询完成 - 返回:{len(page_data)}条, 总计:{total_count}条")
        
        return {
            "success": True,
            "data": page_data,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": total_pages,
                "has_next": has_next,
                "has_prev": has_prev,
                "showing": f"{start_idx + 1}-{start_idx + len(page_data)} of {total_count}"
            },
            "filters": {
                "station": station or "all",
                "sort_by": sort_by,
                "priority": priority
            },
            "timestamp": datetime.now(CHINA_TZ).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ 分页获取合并菜品失败: {e}")
        raise HTTPException(status_code=500, detail="分页获取合并菜品失败")

@router.get("/quick-summary", status_code=200)
async def get_dishes_quick_summary(
    station: Optional[str] = Query(None, description="档口筛选"),
    db: DatabaseManager = Depends(get_db)
):
    """🆕 快速获取菜品汇总（轻量级接口）"""
    try:
        # 只查询当天数据
        beijing_tz = timezone(timedelta(hours=8))
        today_beijing = datetime.now(beijing_tz)

        # 🆕 使用数据库聚合方法
        start_time_query = time.time()
        rows = await db.orders.aggregate_dishes_summary(station)
        query_time = (time.time() - start_time_query) * 1000
        station_summary = rows
        total_summary = {
            "dish_count": sum(row.get("dish_count", 0) or 0 for row in rows),
            "total_quantity": sum(row.get("total_quantity", 0) or 0 for row in rows),
            "unique_dishes": sum(row.get("unique_dishes", 0) or 0 for row in rows),
            "unique_tables": sum(row.get("unique_tables", 0) or 0 for row in rows),
            "station_count": len([row for row in rows if row.get("station")])
        }
        
        logger.info(f"✅ 菜品快速汇总完成 - 耗时:{query_time:.2f}ms")
        
        return {
            "success": True,
            "data": {
                "total_summary": total_summary,
                "station_summary": station_summary
            },
            "station_filter": station or "all",
            "date": today_beijing.strftime("%Y-%m-%d"),
            "performance": {
                "query_time_ms": round(query_time, 2)
            },
            "timestamp": datetime.now(CHINA_TZ).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ 获取菜品快速汇总失败: {e}")
        raise HTTPException(status_code=500, detail="获取菜品快速汇总失败")

@router.get("/hot-dishes", status_code=200)
async def get_hot_dishes(
    limit: int = Query(10, ge=1, le=50, description="返回数量限制"),
    station: Optional[str] = Query(None, description="档口筛选"),
    db: DatabaseManager = Depends(get_db)
):
    """🆕 获取热门菜品（按数量排序）"""
    try:
        today_beijing = datetime.now(CHINA_TZ)
        start_time_query = time.time()
        result = await db.orders.aggregate_hot_dishes(station, limit)
        query_time = (time.time() - start_time_query) * 1000

        logger.info(f"✅ 热门菜品查询完成 - 耗时:{query_time:.2f}ms, 返回:{len(result)}条")

        return {
            "success": True,
            "data": result,
            "limit": limit,
            "station_filter": station or "all",
            "date": today_beijing.strftime("%Y-%m-%d"),
            "performance": {
                "query_time_ms": round(query_time, 2)
            },
            "timestamp": datetime.now(CHINA_TZ).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ 获取热门菜品失败: {e}")
        raise HTTPException(status_code=500, detail="获取热门菜品失败")

@router.get("/{dish_name}/detail")
async def get_dish_detail(
    dish_name: str,
    station: Optional[str] = Query(None, description="档口"),
    db: DatabaseManager = Depends(get_db)
):
    """获取菜品详细信息"""
    try:
        # 获取该菜品的所有订单
        orders = await db.orders.get_orders(
            station=station,
            table_number=None,
            limit=10000
        )
        
        # 过滤出指定菜品的订单
        dish_orders = [
            order for order in orders 
            if order.get('dish_name') == dish_name
        ]
        
        if not dish_orders:
            raise HTTPException(status_code=404, detail="未找到指定菜品")
        
        # 合并菜品数据
        merged_dishes = dish_merger_service.merge_dishes_by_name(dish_orders, station)
        
        if not merged_dishes:
            raise HTTPException(status_code=404, detail="未找到指定菜品")
        
        # 返回第一个匹配的菜品详情
        dish_detail = merged_dishes[0]
        
        # 添加菜品分类信息
        dish_detail['category'] = classify_dish(dish_name)
        
        return dish_detail
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取菜品详情失败: {e}")
        raise HTTPException(status_code=500, detail="获取菜品详情失败")

@router.get("/station/{station_id}/dishes")
async def get_station_dishes(
    station_id: str,
    sort_by: str = Query("time", description="排序方式"),
    db: DatabaseManager = Depends(get_db)
):
    """获取指定档口的菜品"""
    try:
        # 获取该档口的所有订单
        orders = await db.orders.get_orders(
            station=station_id,
            table_number=None,
            limit=10000
        )
        
        # 合并菜品数据
        merged_dishes = dish_merger_service.merge_dishes_by_name(orders, station_id)
        
        # 排序
        sorted_dishes = dish_merger_service.sort_dishes(merged_dishes, sort_by)
        
        return sorted_dishes
        
    except Exception as e:
        logger.error(f"获取档口菜品失败: {e}")
        raise HTTPException(status_code=500, detail="获取档口菜品失败")

@router.get("/category/{category}")
async def get_dishes_by_category(
    category: str,
    station: Optional[str] = Query(None, description="档口筛选"),
    sort_by: str = Query("time", description="排序方式"),
    db: DatabaseManager = Depends(get_db)
):
    """根据分类获取菜品"""
    try:
        # 获取所有订单
        orders = await db.orders.get_orders(
            station=station,
            table_number=None,
            limit=10000
        )
        
        # 按分类过滤
        category_orders = []
        for order in orders:
            order_category = classify_dish(order.get('dish_name', ''))
            if order_category == category:
                category_orders.append(order)
        
        # 合并菜品数据
        merged_dishes = dish_merger_service.merge_dishes_by_name(category_orders, station)
        
        # 排序
        sorted_dishes = dish_merger_service.sort_dishes(merged_dishes, sort_by)
        
        return sorted_dishes
        
    except Exception as e:
        logger.error(f"获取分类菜品失败: {e}")
        raise HTTPException(status_code=500, detail="获取分类菜品失败")

@router.get("/priority/{priority}")
async def get_dishes_by_priority(
    priority: str,
    station: Optional[str] = Query(None, description="档口筛选"),
    sort_by: str = Query("time", description="排序方式"),
    db: DatabaseManager = Depends(get_db)
):
    """根据优先级获取菜品"""
    try:
        # 获取所有订单
        orders = await db.orders.get_orders(
            station=station,
            table_number=None,
            limit=10000
        )
        
        # 合并菜品数据
        merged_dishes = dish_merger_service.merge_dishes_by_name(orders, station)
        
        # 按优先级过滤
        priority_dishes = [
            dish for dish in merged_dishes 
            if dish.get('priority') == priority
        ]
        
        # 排序
        sorted_dishes = dish_merger_service.sort_dishes(priority_dishes, sort_by)
        
        return sorted_dishes
        
    except Exception as e:
        logger.error(f"获取优先级菜品失败: {e}")
        raise HTTPException(status_code=500, detail="获取优先级菜品失败")

@router.get("/urgent/all")
async def get_urgent_dishes(
    station: Optional[str] = Query(None, description="档口筛选"),
    db: DatabaseManager = Depends(get_db)
):
    """获取所有紧急菜品"""
    try:
        # 获取所有订单
        orders = await db.orders.get_orders(
            station=station,
            table_number=None,
            limit=10000
        )
        
        # 合并菜品数据
        merged_dishes = dish_merger_service.merge_dishes_by_name(orders, station)
        
        # 过滤紧急菜品
        urgent_dishes = [
            dish for dish in merged_dishes 
            if dish.get('priority') == 'urgent'
        ]
        
        # 按等待时间排序
        sorted_dishes = dish_merger_service.sort_dishes(urgent_dishes, 'time')
        
        return sorted_dishes
        
    except Exception as e:
        logger.error(f"获取紧急菜品失败: {e}")
        raise HTTPException(status_code=500, detail="获取紧急菜品失败")

@router.get("/stats/overview")
async def get_dishes_overview(
    station: Optional[str] = Query(None, description="档口筛选"),
    db: DatabaseManager = Depends(get_db)
):
    """获取菜品统计概览"""
    try:
        # 获取所有订单
        orders = await db.orders.get_orders(
            station=station,
            table_number=None,
            limit=10000
        )
        
        # 合并菜品数据
        merged_dishes = dish_merger_service.merge_dishes_by_name(orders, station)
        
        # 计算统计信息
        total_dishes = len(merged_dishes)
        pending_dishes = len([d for d in merged_dishes if d.get('status') == 'pending'])
        ready_dishes = len([d for d in merged_dishes if d.get('status') == 'ready'])
        urgent_dishes = len([d for d in merged_dishes if d.get('priority') == 'urgent'])
        
        total_quantity = sum(d.get('total_quantity', 0) for d in merged_dishes)
        pending_quantity = sum(d.get('pending_quantity', 0) for d in merged_dishes)
        ready_quantity = sum(d.get('ready_quantity', 0) for d in merged_dishes)
        
        # 计算平均等待时间
        wait_times = []
        for dish in merged_dishes:
            if dish.get('avg_wait_time', 0) > 0:
                wait_times.append(dish.get('avg_wait_time', 0))
        
        avg_wait_time = sum(wait_times) // len(wait_times) if wait_times else 0
        
        return {
            "total_dishes": total_dishes,
            "pending_dishes": pending_dishes,
            "ready_dishes": ready_dishes,
            "urgent_dishes": urgent_dishes,
            "total_quantity": total_quantity,
            "pending_quantity": pending_quantity,
            "ready_quantity": ready_quantity,
            "avg_wait_time": avg_wait_time,
            "station": station or "all"
        }
        
    except Exception as e:
        logger.error(f"获取菜品统计失败: {e}")
        raise HTTPException(status_code=500, detail="获取菜品统计失败")

@router.post("/classify", dependencies=_ADMIN_WRITE)
async def classify_dish_name(
    dish_name: str
):
    """分类菜品名称"""
    try:
        category = classify_dish(dish_name)
        # 档口分配统一在爬虫端处理，这里不再分配
        station = ""
        
        return {
            "dish_name": dish_name,
            "category": category,
            "station": station,
            "note": "档口分配统一在爬虫端处理"
        }
    except Exception as e:
        logger.error(f"分类菜品名称失败: {e}")
        raise HTTPException(status_code=500, detail="分类菜品名称失败") 

@router.post("/import-mappings", dependencies=_ADMIN_WRITE)
async def import_dish_station_mappings(
    csv_data: str = Body(..., description="CSV格式的菜品档口映射数据"),
    dish_catalog=Depends(get_dish_catalog),
):
    """导入菜品档口映射数据（合并 upsert，不清空已有映射）"""
    try:
        import csv
        import io

        csv_reader = csv.reader(io.StringIO(csv_data))
        mappings = []

        station_name_to_id = {
            '熟笼': 'shulong',
            '肠粉': 'changfen',
            '明档1': 'mingdang1',
            '明档2': 'mingdang2',
            '西饼': 'xibing',
            '煎炸': 'jianzha',
            '楼面': 'loumian',
        }

        for row in csv_reader:
            if len(row) >= 2:
                dish_name = row[0].strip()
                station_name = row[1].strip()

                if not dish_name or not station_name:
                    continue

                station_id = station_name_to_id.get(station_name)
                if station_id:
                    mappings.append({
                        'dish_name': dish_name,
                        'station_id': station_id,
                    })
                else:
                    logger.warning(f"⚠️ 未知档口名称: {station_name}")

        result = await dish_catalog.upsert_many(mappings)
        upserted = result["upserted"]
        errors = result["errors"]
        logger.info(f"✅ 成功合并导入 {upserted} 条菜品档口映射")
        return {
            "success": True,
            "message": f"成功合并导入 {upserted} 条菜品档口映射",
            "data": {"imported_count": upserted, "errors": errors},
        }

    except Exception as e:
        logger.error(f"❌ 导入菜品档口映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")
