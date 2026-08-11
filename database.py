#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
餐厅订单数据采集系统数据库管理器
单库 app.db（WAL）架构；`logs` 表独立在 logs.db，配方库独立在 recipes.db。
跨表查询在同一连接上写 SQL JOIN 即可。

本模块是对外统一门面：`DatabaseManager` 由 db_core/ 下的多个职责 Mixin 组合而成，
具体实现按职责拆分在 db_core/ 包中；对外导出的名称（DatabaseManager、get_db、
CHINA_TZ、ALL_TABLES、ensure_beijing_datetime 等）保持不变。
"""

import logging

from fastapi import HTTPException

from db_core.utils import CHINA_TZ, ensure_beijing_datetime
from db_core.schema import ALL_TABLES
from db_core.connection import _ConnectionMixin
from db_core.orders_repo import _OrdersRepoMixin
from db_core.tables_repo import _TablesRepoMixin
from db_core.dish_stations_repo import _DishStationsRepoMixin
from db_core.semi_rules_repo import _SemiRulesRepoMixin
from db_core.report_dishes_repo import _ReportDishesRepoMixin
from db_core.settings_repo import _SettingsRepoMixin
from db_core.wecom_repo import _WecomRepoMixin
from db_core.aggregation import _AggregationMixin
from db_core.reports import _ReportsMixin
from db_core.stats import _StatsMixin
from db_core.ports import DishStationsPort, OrdersPort, ReportsPort
from db_core.adapters import (
    DishStationsPortAdapter,
    OrdersPortAdapter,
    ReportsPortAdapter,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DatabaseManager",
    "get_db",
    "CHINA_TZ",
    "ALL_TABLES",
    "ensure_beijing_datetime",
    "OrdersPort",
    "DishStationsPort",
    "ReportsPort",
]


# ─────────────────────────────────────────────
#  多数据库管理器（对外暴露的 API 不变）
# ─────────────────────────────────────────────

class DatabaseManager(
    _ConnectionMixin,
    _OrdersRepoMixin,
    _TablesRepoMixin,
    _DishStationsRepoMixin,
    _SemiRulesRepoMixin,
    _ReportDishesRepoMixin,
    _SettingsRepoMixin,
    _WecomRepoMixin,
    _AggregationMixin,
    _ReportsMixin,
    _StatsMixin,
):
    """
    SQLite 单库管理器（app.db）。
    各表共享同一连接；外部调用方用 ``table(name)`` / ``table_or_none(name)``
    取得 ``TableView``。领域入口：``orders`` / ``dish_stations`` / ``reports``
    （各为独立 adapter，非 identity 别名）。

    具体方法按职责拆分在 db_core/ 各 Mixin 中：
    - _ConnectionMixin: 连接生命周期、WAL、备份导出
    - _OrdersRepoMixin: 订单查询/保存/批量写入
    - _TablesRepoMixin: 餐桌快照保存与统计
    - _DishStationsRepoMixin: 菜品档口映射的集合式操作
    - _SemiRulesRepoMixin: 半成品换算规则
    - _ReportDishesRepoMixin: 固定报表菜品
    - _SettingsRepoMixin: 应用运行配置（app_settings 键值表）
    - _WecomRepoMixin: 企业微信推送 webhook/任务/日志
    - _AggregationMixin: 档口统计与订单维度聚合查询
    - _ReportsMixin: 跨表销售报表与经营分析
    - _StatsMixin: 性能统计与健康检查
    """

    def __init__(self):
        super().__init__()
        self._orders_port = None
        self._dish_stations_port = None
        self._reports_port = None

    @property
    def orders(self) -> OrdersPort:
        if self._orders_port is None:
            self._orders_port = OrdersPortAdapter(self)
        return self._orders_port

    @property
    def dish_stations(self) -> DishStationsPort:
        if self._dish_stations_port is None:
            self._dish_stations_port = DishStationsPortAdapter(self)
        return self._dish_stations_port

    @property
    def reports(self) -> ReportsPort:
        if self._reports_port is None:
            self._reports_port = ReportsPortAdapter(self)
        return self._reports_port


# ─────────────────────────────────────────────
#  依赖注入（向后兼容）
# ─────────────────────────────────────────────

def get_db() -> DatabaseManager:
    from services.app_runtime import get_runtime

    runtime = get_runtime()
    if runtime is not None and runtime.db is not None:
        return runtime.db

    # Fallback: main module alias (uvicorn --reload / gradual migration).
    from main import db_manager
    if db_manager is None:
        raise HTTPException(status_code=500, detail="数据库未初始化")
    return db_manager
