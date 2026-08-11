#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
菜品档口映射管理API
提供dish_stations集合的完整CRUD操作
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field
import logging

from database import DatabaseManager, get_db
from models import SuccessResponse
from config import KITCHEN_STATIONS
from api.security import verify_admin_token
from services.dish_catalog import InvalidStationError, get_dish_catalog

# 配置日志
logger = logging.getLogger(__name__)

# 创建路由器
router = APIRouter(prefix="/api/dish-stations", tags=["菜品档口映射管理"])
_ADMIN_WRITE = [Depends(verify_admin_token)]

# 数据模型
class DishStationBase(BaseModel):
    """菜品档口映射基础模型"""
    dish_name: str = Field(..., description="菜品名称", min_length=1, max_length=100)
    station_id: str = Field(..., description="档口ID")
    notes: Optional[str] = Field(None, description="备注", max_length=200)


class DishStationCreate(DishStationBase):
    """创建菜品档口映射"""
    pass


class DishStationUpdate(BaseModel):
    """更新菜品档口映射"""
    station_id: Optional[str] = Field(None, description="档口ID")
    notes: Optional[str] = Field(None, description="备注", max_length=200)


class DishStationResponse(DishStationBase):
    """菜品档口映射响应模型"""
    id: str = Field(..., description="文档ID")
    station_name: Optional[str] = Field(None, description="档口名称")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")


class BatchUpdateRequest(BaseModel):
    """批量更新请求"""
    mappings: List[DishStationCreate] = Field(..., description="映射列表")


class SearchRequest(BaseModel):
    """搜索请求"""
    dish_name: Optional[str] = Field(None, description="菜品名称关键词")
    station_id: Optional[str] = Field(None, description="档口ID")


# 辅助函数：格式化dish_stations行数据
def _format_dish_station(item: Dict) -> DishStationResponse:
    station_info = KITCHEN_STATIONS.get(item.get("station_id", ""), {})
    station_name = station_info.get("name", item.get("station_id", "未知档口"))

    created_at = item.get("created_at", datetime.now(timezone.utc))
    updated_at = item.get("updated_at", datetime.now(timezone.utc))
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at)

    return DishStationResponse(
        id=str(item.get("_id", "")),
        dish_name=item.get("dish_name", ""),
        station_id=item.get("station_id", ""),
        station_name=station_name,
        notes=item.get("notes"),
        created_at=created_at,
        updated_at=updated_at
    )


# API接口
@router.get("/", response_model=SuccessResponse)
async def get_dish_stations(
    dish_name: Optional[str] = Query(None, description="菜品名称筛选"),
    station_id: Optional[str] = Query(None, description="档口ID筛选"),
    db: DatabaseManager = Depends(get_db)
):
    """获取菜品档口映射列表"""
    try:
        query: Dict[str, Any] = {}
        if station_id:
            query["station_id"] = station_id

        total_count = await db.dish_stations.dish_stations_count(query, dish_name_contains=dish_name)
        items = await db.dish_stations.dish_stations_find(
            query, sort_field="dish_name", sort_dir=1, dish_name_contains=dish_name
        )
        results = [_format_dish_station(item) for item in items]

        logger.info(f"✅ 获取菜品档口映射: {len(results)}条/共{total_count}条")

        return SuccessResponse(
            message="获取成功",
            data={
                "items": results,
                "total_count": total_count
            }
        )

    except Exception as e:
        logger.error(f"❌ 获取菜品档口映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取数据失败: {str(e)}")


@router.get("/stats")
async def get_dish_stations_stats(db: DatabaseManager = Depends(get_db)):
    """获取菜品档口映射统计信息"""
    try:
        total_count = await db.dish_stations.dish_stations_count({})
        station_stats = await db.dish_stations.dish_stations_stats_by_station()

        formatted_station_stats = []
        for stat in station_stats:
            station_id = stat.get("station_id", "")
            count = stat.get("count", 0)
            station_info = KITCHEN_STATIONS.get(station_id, {})
            formatted_station_stats.append({
                "station_id": station_id,
                "station_name": station_info.get("name", station_id),
                "count": count
            })

        return SuccessResponse(
            message="获取统计信息成功",
            data={
                "total_count": total_count,
                "station_stats": formatted_station_stats
            }
        )

    except Exception as e:
        logger.error(f"❌ 获取统计信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")


@router.get("/{dish_name}")
async def get_dish_station(
    dish_name: str,
    db: DatabaseManager = Depends(get_db)
):
    """根据菜品名称获取档口映射"""
    try:
        item = await db.dish_stations.dish_stations_find_one({"dish_name": dish_name})
        if not item:
            raise HTTPException(status_code=404, detail="菜品映射不存在")

        result = _format_dish_station(item)
        return SuccessResponse(message="获取成功", data=result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取菜品映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取失败: {str(e)}")


@router.post("/", status_code=201, dependencies=_ADMIN_WRITE)
async def create_dish_station(
    dish_station: DishStationCreate,
    dish_catalog=Depends(get_dish_catalog),
):
    """创建菜品档口映射"""
    try:
        existing = await dish_catalog.get(dish_station.dish_name)
        if existing:
            raise HTTPException(status_code=400, detail=f"菜品 '{dish_station.dish_name}' 的映射已存在")

        result = await dish_catalog.upsert(
            dish_station.dish_name,
            dish_station.station_id,
            notes=dish_station.notes,
        )
        logger.info(f"✅ 创建菜品映射: {dish_station.dish_name} → {dish_station.station_id}")

        return SuccessResponse(
            message="创建成功",
            data={"id": result["dish_name"]},
        )

    except HTTPException:
        raise
    except InvalidStationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ 创建菜品映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建失败: {str(e)}")


@router.put("/{dish_name}", dependencies=_ADMIN_WRITE)
async def update_dish_station(
    dish_name: str,
    update_data: DishStationUpdate,
    dish_catalog=Depends(get_dish_catalog),
):
    """更新菜品档口映射"""
    try:
        existing = await dish_catalog.get(dish_name)
        if not existing:
            raise HTTPException(status_code=404, detail="菜品映射不存在")

        station_id = (
            update_data.station_id
            if update_data.station_id is not None
            else existing.get("station_id", "")
        )
        notes = update_data.notes if update_data.notes is not None else None
        # notes=None means leave unchanged inside upsert; pass existing notes only when
        # caller omitted the field — upsert treats None as "keep".
        await dish_catalog.upsert(
            dish_name,
            station_id,
            notes=notes if update_data.notes is not None else existing.get("notes"),
        )
        logger.info(f"✅ 更新菜品映射: {dish_name}")

        return SuccessResponse(message="更新成功")

    except HTTPException:
        raise
    except InvalidStationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ 更新菜品映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")


@router.delete("/{dish_name}", dependencies=_ADMIN_WRITE)
async def delete_dish_station(
    dish_name: str,
    dish_catalog=Depends(get_dish_catalog),
):
    """删除菜品档口映射"""
    try:
        existing = await dish_catalog.get(dish_name)
        if not existing:
            raise HTTPException(status_code=404, detail="菜品映射不存在")

        deleted = await dish_catalog.remove(dish_name)
        if not deleted:
            raise HTTPException(status_code=400, detail="删除失败")

        logger.info(f"✅ 删除菜品映射: {dish_name}")
        return SuccessResponse(message="删除成功")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 删除菜品映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@router.post("/batch", dependencies=_ADMIN_WRITE)
async def batch_create_dish_stations(
    request: BatchUpdateRequest,
    dish_catalog=Depends(get_dish_catalog),
):
    """批量创建菜品档口映射（已存在则记 error，不覆盖——保持原 HTTP 语义）"""
    try:
        created_count = 0
        errors = []

        for mapping in request.mappings:
            existing = await dish_catalog.get(mapping.dish_name)
            if existing:
                errors.append(f"菜品 '{mapping.dish_name}' 的映射已存在")
                continue
            try:
                await dish_catalog.upsert(
                    mapping.dish_name,
                    mapping.station_id,
                    notes=mapping.notes,
                )
                created_count += 1
            except InvalidStationError:
                errors.append(f"菜品 '{mapping.dish_name}' 的档口ID无效: {mapping.station_id}")

        if created_count == 0:
            raise HTTPException(status_code=400, detail="没有有效的映射数据")

        logger.info(f"✅ 批量创建菜品映射: {created_count}个")

        return SuccessResponse(
            message=f"批量创建成功: {created_count}个",
            data={
                "created_count": created_count,
                "errors": errors,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 批量创建菜品映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量创建失败: {str(e)}")


@router.post("/search")
async def search_dish_stations(
    search_request: SearchRequest,
    page: int = Query(1, description="页码", ge=1),
    page_size: int = Query(20, description="每页大小", ge=1, le=100),
    db: DatabaseManager = Depends(get_db)
):
    """搜索菜品档口映射"""
    try:
        query: Dict[str, Any] = {}
        if search_request.station_id:
            query["station_id"] = search_request.station_id

        skip = (page - 1) * page_size
        items = await db.dish_stations.dish_stations_find(
            query,
            sort_field="dish_name",
            sort_dir=1,
            skip=skip,
            limit=page_size,
            dish_name_contains=search_request.dish_name,
        )
        total_count = await db.dish_stations.dish_stations_count(
            query, dish_name_contains=search_request.dish_name
        )
        results = [_format_dish_station(item) for item in items]

        return SuccessResponse(
            message="搜索成功",
            data={
                "items": results,
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
                "total_pages": (total_count + page_size - 1) // page_size
            }
        )

    except Exception as e:
        logger.error(f"❌ 搜索菜品映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"搜索失败: {str(e)}")
