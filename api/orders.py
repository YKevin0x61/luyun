#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
订单相关API路由
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Optional
from datetime import datetime, timezone, timedelta
import logging
import time

# 定义北京时区
CHINA_TZ = timezone(timedelta(hours=8))

from models import (
    OrderResponse,
    SuccessResponse,
    CompleteCookingRequest,
    LoadSteamerRequest,
    MoveSteamerRequest,
    UnloadSteamerRequest,
    PluckSteamerRequest,
)
from database import DatabaseManager, get_db, ensure_beijing_datetime
from services.kds_orders import complete_cooking as kds_complete_cooking
from services.kds_orders import load_steamer as kds_load_steamer
from services.kds_orders import move_steamer as kds_move_steamer
from services.kds_orders import unload_steamer as kds_unload_steamer
from services.kds_orders import pluck_steamer as kds_pluck_steamer
from services.urgency_policy import urgent_cutoff
from api.security import verify_admin_token

logger = logging.getLogger(__name__)
_ADMIN_WRITE = [Depends(verify_admin_token)]
router = APIRouter(prefix="/api/orders", tags=["订单管理"])


@router.get("/", status_code=200)
async def get_orders(
    station: Optional[str] = Query(None, description="档口筛选"),
    table_number: Optional[str] = Query(None, description="桌号筛选"),
    start_time: Optional[str] = Query(None, description="开始时间 (ISO格式)"),
    end_time: Optional[str] = Query(None, description="结束时间 (ISO格式)"),
    limit: int = Query(10000, description="限制返回数量，设置为-1表示无限制"),
    dish_status: Optional[str] = Query(None, description="KDS控菜状态筛选"),
    db: DatabaseManager = Depends(get_db)
):
    """获取订单列表"""
    try:
        parsed_start_time = None
        parsed_end_time = None

        # 默认查询当天数据
        if not start_time and not end_time:
            today_beijing = datetime.now(CHINA_TZ)
            start_of_day_beijing = today_beijing.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day_beijing = today_beijing.replace(hour=23, minute=59, second=59, microsecond=999000)
            parsed_start_time = start_of_day_beijing
            parsed_end_time = end_of_day_beijing

        if start_time:
            try:
                if 'T' in start_time:
                    parsed_start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                else:
                    parsed_start_time = datetime.fromisoformat(start_time + 'T00:00:00+08:00')
                if parsed_start_time.tzinfo is None:
                    parsed_start_time = parsed_start_time.replace(tzinfo=CHINA_TZ)
                else:
                    parsed_start_time = parsed_start_time.astimezone(CHINA_TZ)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"无效的开始时间格式: {e}")

        if end_time:
            try:
                if 'T' in end_time:
                    parsed_end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                else:
                    parsed_end_time = datetime.fromisoformat(end_time + 'T23:59:59+08:00')
                if parsed_end_time.tzinfo is None:
                    parsed_end_time = parsed_end_time.replace(tzinfo=CHINA_TZ)
                else:
                    parsed_end_time = parsed_end_time.astimezone(CHINA_TZ)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"无效的结束时间格式: {e}")

        orders = await db.orders.get_orders(
            station=station,
            table_number=table_number,
            start_time=parsed_start_time,
            end_time=parsed_end_time,
            dish_status=dish_status,
            limit=limit
        )

        for order in orders:
            if "_id" in order and "id" not in order:
                order["id"] = order["_id"]

        return {
            "success": True,
            "data": orders,
            "count": len(orders),
            "timestamp": datetime.now(CHINA_TZ).isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取订单列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取订单列表失败")


@router.get("/table/{table_number}", response_model=List[OrderResponse])
async def get_table_orders(
    table_number: str,
    start_time: Optional[str] = Query(None, description="开始时间 ISO，默认今日"),
    end_time: Optional[str] = Query(None, description="结束时间 ISO，默认今日"),
    db: DatabaseManager = Depends(get_db)
):
    """获取指定餐桌的订单（默认仅今日）"""
    try:
        parsed_start, parsed_end = None, None
        if not start_time and not end_time:
            today = datetime.now(CHINA_TZ)
            parsed_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
            parsed_end = today.replace(hour=23, minute=59, second=59, microsecond=999000)
        if start_time:
            parsed_start = ensure_beijing_datetime(start_time)
        if end_time:
            parsed_end = ensure_beijing_datetime(end_time)
        orders = await db.orders.get_orders(
            station=None,
            table_number=table_number,
            start_time=parsed_start,
            end_time=parsed_end,
            limit=-1
        )
        return orders
    except Exception as e:
        logger.error(f"获取餐桌订单失败: {e}")
        raise HTTPException(status_code=500, detail="获取餐桌订单失败")


@router.get("/stations-today-stats")
async def get_stations_today_stats(db: DatabaseManager = Depends(get_db)):
    """获取今日各档口订单数量统计"""
    try:
        today_start = datetime.now(CHINA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        stats = await db.orders.aggregate_station_counts(today_start)
        return {"success": True, "date": today_start.strftime("%Y-%m-%d"), "stats": stats}
    except Exception as e:
        logger.error(f"获取今日档口统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/station-speed")
async def get_station_speed(
    date: Optional[str] = Query(None, description="查询日期，格式 YYYY-MM-DD，默认今天"),
    db: DatabaseManager = Depends(get_db)
):
    """获取档口进单速率（按时段统计，支持多日对比）"""
    try:
        now = datetime.now(CHINA_TZ)

        # 解析目标日期
        if date:
            try:
                target = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ)
            except ValueError:
                raise HTTPException(status_code=400, detail="日期格式错误，请使用 YYYY-MM-DD")
        else:
            target = now.replace(hour=0, minute=0, second=0, microsecond=0)

        result = await db.orders.aggregate_station_speed(target)
        return {"success": True, **result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取档口进单速率失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/station/{station_id}/stats")
async def get_station_order_stats(
    station_id: str,
    start_time: Optional[str] = Query(None, description="开始时间，默认今日 00:00"),
    end_time: Optional[str] = Query(None, description="结束时间，默认今日 23:59"),
    db: DatabaseManager = Depends(get_db)
):
    """获取档口统计（默认今日）"""
    try:
        parsed_start, parsed_end = None, None
        if start_time:
            parsed_start = ensure_beijing_datetime(start_time)
        if end_time:
            parsed_end = ensure_beijing_datetime(end_time)
        stats = await db.orders.get_station_stats(station_id, parsed_start, parsed_end)
        return stats
    except Exception as e:
        logger.error(f"获取档口统计失败: {e}")
        raise HTTPException(status_code=500, detail="获取档口统计失败")


@router.get("/priority/urgent", response_model=List[OrderResponse])
async def get_urgent_orders(
    db: DatabaseManager = Depends(get_db)
):
    """获取紧急订单（今日、等待超过 urgent 阈值）"""
    try:
        today = datetime.now(CHINA_TZ)
        start = today.replace(hour=0, minute=0, second=0, microsecond=0)
        end = today.replace(hour=23, minute=59, second=59, microsecond=999000)
        orders = await db.orders.get_orders(
            station=None,
            table_number=None,
            start_time=start,
            end_time=end,
            limit=-1
        )

        urgent_orders = []
        current_time = datetime.now(CHINA_TZ)
        cutoff = urgent_cutoff(current_time)

        for order in orders:
            order_time = order.get('order_time')
            if isinstance(order_time, str):
                order_time = ensure_beijing_datetime(order_time)
            if isinstance(order_time, datetime):
                if order_time < cutoff:
                    urgent_orders.append(order)

        urgent_orders.sort(
            key=lambda x: ensure_beijing_datetime(x.get('order_time', current_time.isoformat())),
            reverse=False
        )

        return urgent_orders

    except Exception as e:
        logger.error(f"获取紧急订单失败: {e}")
        raise HTTPException(status_code=500, detail="获取紧急订单失败")


@router.get("/paginated", status_code=200)
async def get_orders_paginated(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=200, description="每页数量"),
    station: Optional[str] = Query(None, description="档口筛选"),
    table_number: Optional[str] = Query(None, description="桌号筛选"),
    start_time: Optional[str] = Query(None, description="开始时间 (ISO格式)"),
    end_time: Optional[str] = Query(None, description="结束时间 (ISO格式)"),
    db: DatabaseManager = Depends(get_db)
):
    """分页获取订单列表"""
    try:
        skip = (page - 1) * page_size

        match_condition = {}

        if station and station != 'all':
            match_condition['station'] = station
        if table_number:
            match_condition['table_number'] = table_number

        order_time_start = None
        order_time_end = None
        # 时间过滤（统一北京时区）
        if start_time or end_time:
            try:
                if start_time:
                    order_time_start = ensure_beijing_datetime(start_time)
                if end_time:
                    order_time_end = ensure_beijing_datetime(end_time)
            except Exception as e:
                logger.warning(f"时间解析失败: {e}")
                raise HTTPException(status_code=400, detail=f"无效的时间参数: {e}")
        else:
            # 默认查询当天
            today_beijing = datetime.now(CHINA_TZ)
            order_time_start = today_beijing.replace(hour=0, minute=0, second=0, microsecond=0)
            order_time_end = today_beijing.replace(hour=23, minute=59, second=59, microsecond=999000)

        start_ts = time.time()
        orders, total_count = await db.orders.aggregate_orders_paginated(
            match_condition,
            skip,
            page_size,
            order_time_start=order_time_start,
            order_time_end=order_time_end,
        )
        query_time = (time.time() - start_ts) * 1000

        total_pages = (total_count + page_size - 1) // page_size

        return {
            "success": True,
            "data": orders,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
            "performance": {"query_time_ms": round(query_time, 2)},
            "timestamp": datetime.now(CHINA_TZ).isoformat()
        }

    except Exception as e:
        logger.error(f"分页获取订单失败: {e}")
        raise HTTPException(status_code=500, detail="分页获取订单失败")


@router.get("/quick-stats", status_code=200)
async def get_quick_order_stats(
    station: Optional[str] = Query(None, description="档口筛选"),
    db: DatabaseManager = Depends(get_db)
):
    """快速获取订单统计（轻量级接口）"""
    try:
        start_ts = time.time()
        stats = await db.orders.aggregate_orders_stats(station)
        query_time = (time.time() - start_ts) * 1000

        today_beijing = datetime.now(CHINA_TZ)

        return {
            "success": True,
            "data": stats,
            "station": station or "all",
            "date": today_beijing.strftime("%Y-%m-%d"),
            "performance": {"query_time_ms": round(query_time, 2)},
            "timestamp": datetime.now(CHINA_TZ).isoformat()
        }

    except Exception as e:
        logger.error(f"获取快速统计失败: {e}")
        raise HTTPException(status_code=500, detail="获取快速统计失败")


@router.get("/search", status_code=200)
async def search_orders(
    start_date: str = Query(..., description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    station: Optional[str] = Query(None, description="档口筛选"),
    table_number: Optional[str] = Query(None, description="桌号筛选"),
    dish_name: Optional[str] = Query(None, description="菜品名称筛选"),
    limit: int = Query(10000, description="限制返回数量"),
    db: DatabaseManager = Depends(get_db)
):
    """按条件搜索订单"""
    try:
        match_condition = {}

        # 日期范围
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            start_beijing = start_dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=CHINA_TZ)
            start_utc = start_beijing.astimezone(timezone.utc)

            end_dt = datetime.strptime(end_date, "%Y-%m-%d") if end_date else start_dt
            end_beijing = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=CHINA_TZ)
            end_utc = end_beijing.astimezone(timezone.utc)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="日期格式错误，请使用 YYYY-MM-DD 格式")

        if station and station.strip():
            match_condition["station"] = station.strip()

        if table_number and table_number.strip():
            match_condition["table_number"] = table_number.strip()

        start_ts = time.time()
        actual_limit = limit if limit > 0 else 10000
        orders = await db.orders.search_orders_raw(
            match_condition,
            actual_limit,
            dish_name_contains=dish_name.strip() if dish_name and dish_name.strip() else None,
            order_time_start=start_utc,
            order_time_end=end_utc,
        )
        query_time = (time.time() - start_ts) * 1000

        for order in orders:
            if 'order_time' in order and order['order_time']:
                order['order_time'] = ensure_beijing_datetime(order['order_time'])
            if '_id' in order:
                order['_id'] = str(order['_id'])

        return {
            "success": True,
            "orders": orders,
            "total": len(orders),
            "filters": {
                "start_date": start_date,
                "end_date": end_date or start_date,
                "station": station,
                "table_number": table_number,
                "dish_name": dish_name
            },
            "performance": {"query_time_ms": round(query_time, 2)},
            "timestamp": datetime.now(CHINA_TZ).isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"搜索订单失败: {e}")
        raise HTTPException(status_code=500, detail=f"搜索订单失败: {str(e)}")


@router.get("/sales-report")
async def get_sales_report(
    start_date: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end_date: str   = Query(None, description="结束日期 YYYY-MM-DD，默认同开始日期"),
    station: Optional[str] = Query(None, description="档口筛选"),
    db: DatabaseManager = Depends(get_db)
):
    """基于数据库订单计算销售报表（含菜品销量和半成品用量）"""
    try:
        if end_date is None:
            end_date = start_date
        result = await db.reports.compute_sales_report(start_date, end_date, station)
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"销售报表计算失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _notify_orders_completed(stations: List[str]) -> None:
    """按受影响档口逐个推 `orders` nudge（scope 带 station，便于按档口过滤的
    订阅方精确收到）；没有可用档口信息时退化为不带 station 的一次广播。
    广播失败不应影响制作完成这一主流程，故只记录日志。"""
    try:
        from main import broadcast_realtime_event
        if stations:
            for station in stations:
                await broadcast_realtime_event("orders_updated", station=station)
        else:
            await broadcast_realtime_event("orders_updated")
    except Exception as exc:
        logger.debug(f"广播制作完成事件失败: {exc}")


@router.post("/complete-cooking", status_code=200, dependencies=_ADMIN_WRITE)
async def complete_cooking(
    body: CompleteCookingRequest,
    db: DatabaseManager = Depends(get_db),
):
    """KDS 制作完成：更新 dish_status 为已制作待上菜"""
    try:
        result = await kds_complete_cooking(db.orders, body.model_dump())
        stations = result.pop("stations", [])
        await _notify_orders_completed(stations)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"制作完成失败: {e}")
        raise HTTPException(status_code=500, detail="制作完成失败")


@router.post("/load-steamer", status_code=200, dependencies=_ADMIN_WRITE)
async def load_steamer(
    body: LoadSteamerRequest,
    db: DatabaseManager = Depends(get_db),
):
    """KDS 上笼：写入蒸笼位，出餐状态仍为待出餐。"""
    try:
        result = await kds_load_steamer(db.orders, body.model_dump())
        stations = result.pop("stations", [])
        await _notify_orders_completed(stations)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上笼失败: {e}")
        raise HTTPException(status_code=500, detail="上笼失败")


@router.post("/move-steamer", status_code=200, dependencies=_ADMIN_WRITE)
async def move_steamer(
    body: MoveSteamerRequest,
    db: DatabaseManager = Depends(get_db),
):
    """KDS 换孔：在蒸笼移到目标孔顶，出餐状态仍为待出餐。"""
    try:
        result = await kds_move_steamer(db.orders, body.model_dump())
        stations = result.pop("stations", [])
        if stations:
            await _notify_orders_completed(stations)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"换孔失败: {e}")
        raise HTTPException(status_code=500, detail="换孔失败")


@router.post("/unload-steamer", status_code=200, dependencies=_ADMIN_WRITE)
async def unload_steamer(
    body: UnloadSteamerRequest,
    db: DatabaseManager = Depends(get_db),
):
    """KDS 下笼：清空蒸笼位，回到待上笼，出餐状态仍为待出餐。"""
    try:
        result = await kds_unload_steamer(db.orders, body.model_dump())
        stations = result.pop("stations", [])
        if stations:
            await _notify_orders_completed(stations)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下笼失败: {e}")
        raise HTTPException(status_code=500, detail="下笼失败")


@router.post("/pluck-steamer", status_code=200, dependencies=_ADMIN_WRITE)
async def pluck_steamer(
    body: PluckSteamerRequest,
    db: DatabaseManager = Depends(get_db),
):
    """KDS 抽笼：只清退菜占位的蒸笼位，不出餐、不打票。"""
    try:
        result = await kds_pluck_steamer(db.orders, body.model_dump())
        stations = result.pop("stations", [])
        if stations:
            await _notify_orders_completed(stations)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"抽笼失败: {e}")
        raise HTTPException(status_code=500, detail="抽笼失败")


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    db: DatabaseManager = Depends(get_db)
):
    """获取单个订单详情"""
    try:
        order = await db.orders.get_order_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")
        return order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取订单详情失败: {e}")
        raise HTTPException(status_code=500, detail="获取订单详情失败")
