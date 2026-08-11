#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的性能/健康统计职责：连接数、健康检查、集合计数。
"""

import logging
from datetime import datetime
from typing import Dict, Any

from db_core.utils import CHINA_TZ

logger = logging.getLogger(__name__)


class _StatsMixin:
    """性能统计、健康检查与连接/集合计数。"""

    async def get_performance_stats(self) -> Dict[str, Any]:
        try:
            order_count = await self.table("orders").get_count()
            table_count = await self.table("tables").get_count()
            mapping_count = await self.table("dish_stations").get_count()
            return {
                'database_stats': self.stats,
                'connection_stats': {
                    'total_orders': order_count,
                    'total_tables': table_count,
                    'total_mappings': mapping_count,
                    'db_paths': self.paths,
                    'status': 'connected'
                },
                'timestamp': datetime.now(CHINA_TZ).isoformat()
            }
        except Exception as e:
            logger.error(f"❌ 获取性能统计失败: {e}")
            return {}

    async def health_check(self) -> Dict[str, Any]:
        try:
            await self._main_conn.execute("SELECT 1")
            return {
                "status": "healthy", "health_score": 100,
                "db_paths": self.paths,
                "timestamp": datetime.now(CHINA_TZ).isoformat()
            }
        except Exception as e:
            logger.error(f"❌ 数据库健康检查失败: {e}")
            return {"status": "error", "health_score": 0, "error": str(e)}

    async def get_connection_stats(self) -> Dict[str, Any]:
        try:
            order_count = await self.table("orders").get_count()
            table_count = await self.table("tables").get_count()
            mapping_count = await self.table("dish_stations").get_count()
            return {
                "total_orders": order_count,
                "total_tables": table_count,
                "total_mappings": mapping_count,
                "db_paths": self.paths,
                "status": "connected"
            }
        except Exception as e:
            return {"error": str(e)}

    async def optimize_connection_pool(self):
        return True

    async def get_collection_stats(self) -> Dict[str, Any]:
        try:
            order_count = await self.table("orders").get_count()
            table_count = await self.table("tables").get_count()
            mapping_count = await self.table("dish_stations").get_count()
            return {
                "orders": {"count": order_count},
                "tables": {"count": table_count},
                "dish_stations": {"count": mapping_count}
            }
        except Exception as e:
            return {"error": str(e)}
