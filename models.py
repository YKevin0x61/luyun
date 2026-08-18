#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
餐厅订单数据采集系统数据模型定义
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

# 基础模型
class OrderBase(BaseModel):
    """订单基础模型"""
    business_flow_id: str = Field(default="", description="营业流水号")
    table_number: str = Field(default="", description="桌号")
    dish_name: str = Field(default="", description="菜品名称")
    quantity: int = Field(default=1, description="数量")
    order_time: Optional[datetime] = Field(default=None, description="下单时间")
    price: float = Field(default=0.0, description="单价")
    total_amount: float = Field(default=0.0, description="小计金额")
    status: str = Field(default="未结", description="订单状态")
    category: str = Field(default="", description="菜品分类")
    station: Optional[str] = Field(default=None, description="档口")
    priority: str = Field(default="normal", description="优先级")
    notes: Optional[str] = Field(default=None, description="备注")


class OrderUpdate(BaseModel):
    """更新订单模型"""
    notes: Optional[str] = None
    priority: Optional[str] = None


class OrderResponse(OrderBase):
    """订单响应模型"""
    id: str = Field(default="", alias="_id")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        populate_by_name = True


# 档口统计模型
class StationStats(BaseModel):
    """档口统计模型"""
    station_id: str = Field(..., description="档口ID")
    station_name: str = Field(..., description="档口名称")
    total_orders: int = Field(..., description="总订单数")
    total_quantity: int = Field(..., description="总数量")
    urgent_count: int = Field(..., description="紧急菜品数量")
    avg_wait_time: int = Field(..., description="平均等待时间（毫秒）")


# 成功响应模型
class SuccessResponse(BaseModel):
    """成功响应模型"""
    success: bool = Field(default=True, description="是否成功")
    message: str = Field(..., description="响应消息")
    data: Optional[Dict[str, Any]] = Field(default=None, description="响应数据")
    timestamp: datetime = Field(default_factory=lambda: datetime.now())


class CompleteCookingOrderItem(BaseModel):
    order_id: str
    table_number: str
    complete_quantity: int
    original_quantity: int
    business_flow_id: Optional[str] = None


class CompleteCookingRequest(BaseModel):
    dish_name: str
    station: str
    complete_quantity: int
    orders: List[CompleteCookingOrderItem]
    operator_id: str = "system"
    ready_time: Optional[str] = None


class LoadSteamerRequest(BaseModel):
    order_ids: List[str]
    steamer_id: str
    port_index: int
    loaded_at: Optional[str] = None


class MoveSteamerRequest(BaseModel):
    order_ids: List[str]
    steamer_id: str
    port_index: int


class UnloadSteamerRequest(BaseModel):
    order_ids: List[str]


class PluckSteamerRequest(BaseModel):
    order_ids: List[str]


class FloorOrderIdsRequest(BaseModel):
    order_ids: List[str]
