#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DishCatalog：菜品名称 → 档口(station_id) 的领域模块。

唯一写入口：upsert / upsert_many / remove（经 DishStationsPort 写库并更新缓存）。
读与派生：resolve / sync_orders_since / unmapped_dishes（orders 派生走 OrdersPort）。
外源变更（如 .db 合并导入）调用 invalidate() 后，下次 resolve 会重载。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import KITCHEN_STATIONS
from db_core.ports import DishStationsPort, OrdersPort
from db_core.utils import CHINA_TZ
from fastapi import HTTPException

logger = logging.getLogger(__name__)

_SYNC_BATCH_SIZE = 200


class InvalidStationError(ValueError):
    """station_id 不在 KITCHEN_STATIONS 中。"""


def get_dish_catalog():
    """FastAPI Depends / 运行时解析：与 get_db 同一套路。"""
    from services.app_runtime import get_runtime

    runtime = get_runtime()
    if runtime is not None and runtime.dish_catalog is not None:
        return runtime.dish_catalog

    from main import dish_catalog

    if dish_catalog is None:
        raise HTTPException(status_code=500, detail="数据库未初始化，请稍后重试")
    return dish_catalog


class DishCatalog:
    """菜品→档口映射的权威读写 + 派生查询（同步/未映射清单）。"""

    def __init__(
        self,
        db: Any = None,
        *,
        dish_stations: Optional[DishStationsPort] = None,
        orders: Optional[OrdersPort] = None,
    ):
        if dish_stations is not None and orders is not None:
            self._dish_stations = dish_stations
            self._orders = orders
        elif db is not None:
            self._dish_stations = db.dish_stations
            self._orders = db.orders
        else:
            raise TypeError("DishCatalog requires db or (dish_stations, orders)")
        self._mapping: Dict[str, str] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    async def resolve(self, dish_name: str) -> str:
        """返回 dish_name 对应的 station_id，未映射或空名返回 ''。首次调用自动加载缓存。"""
        if not dish_name:
            return ""
        await self._ensure_loaded()
        return self._mapping.get(dish_name.strip(), "")

    def invalidate(self) -> None:
        """丢弃缓存。供 .db 导入等外源写库后调用；日常 upsert/remove 会自行维护缓存。"""
        self._loaded = False

    async def get(self, dish_name: str) -> Optional[Dict[str, Any]]:
        """按菜名读取完整映射行（含 notes）；不存在返回 None。"""
        name = (dish_name or "").strip()
        if not name:
            return None
        return await self._dish_stations.dish_stations_find_one({"dish_name": name})

    async def as_dict(self) -> Dict[str, str]:
        """返回当前显式映射的浅拷贝（dish_name → station_id）。"""
        await self._ensure_loaded()
        return dict(self._mapping)

    async def upsert(
        self,
        dish_name: str,
        station_id: str,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """创建或更新一条显式映射；校验 station_id。"""
        name = (dish_name or "").strip()
        station = (station_id or "").strip()
        if not name:
            raise ValueError("dish_name is required")
        self._validate_station(station)

        existing = await self._dish_stations.dish_stations_find_one({"dish_name": name})
        now = datetime.now(CHINA_TZ).isoformat()
        if existing:
            fields: Dict[str, Any] = {"station_id": station, "updated_at": now}
            if notes is not None:
                fields["notes"] = notes
            await self._dish_stations.dish_stations_update(name, fields)
            created = False
        else:
            await self._dish_stations.dish_stations_insert(
                {
                    "dish_name": name,
                    "station_id": station,
                    "notes": notes,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            created = True

        await self._touch_cache(name, station)
        return {
            "dish_name": name,
            "station_id": station,
            "created": created,
        }

    async def upsert_many(self, mappings: List[Dict[str, Any]]) -> Dict[str, Any]:
        """合并写入多条映射（不清空表）。非法档口记入 errors，其余继续。"""
        upserted = 0
        errors: List[str] = []
        for raw in mappings or []:
            name = (raw.get("dish_name") or "").strip()
            station = (raw.get("station_id") or "").strip()
            if not name:
                errors.append("empty dish_name")
                continue
            try:
                await self.upsert(name, station, notes=raw.get("notes"))
                upserted += 1
            except InvalidStationError as exc:
                errors.append(f"{name}: {exc}")
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
        return {"upserted": upserted, "errors": errors}

    async def remove(self, dish_name: str) -> bool:
        """删除一条映射；不存在返回 False。"""
        name = (dish_name or "").strip()
        if not name:
            return False
        deleted = await self._dish_stations.dish_stations_delete(name)
        if deleted:
            if self._loaded:
                self._mapping.pop(name, None)
            return True
        return False

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            try:
                rows = await self._dish_stations.dish_stations_find({})
                mapping: Dict[str, str] = {}
                for row in rows:
                    dish_name = (row.get("dish_name") or "").strip()
                    if dish_name:
                        mapping[dish_name] = row.get("station_id", "")
                self._mapping = mapping
                self._loaded = True
                logger.info("🎯 菜品档口映射加载完成: %d 条", len(mapping))
            except Exception:
                logger.exception("❌ 加载菜品档口映射失败")

    async def _touch_cache(self, dish_name: str, station_id: str) -> None:
        if not self._loaded:
            await self._ensure_loaded()
        if self._loaded:
            self._mapping[dish_name] = station_id

    @staticmethod
    def _validate_station(station_id: str) -> None:
        if station_id not in KITCHEN_STATIONS:
            raise InvalidStationError(f"无效的档口ID: {station_id}")

    async def sync_orders_since(self, since: datetime) -> Dict[str, Any]:
        """把 dish_stations 映射同步到 since 之后的 orders.station / station_id 字段。"""
        await self._ensure_loaded()
        return await self._orders.sync_order_stations(
            since, self._mapping, batch_size=_SYNC_BATCH_SIZE
        )

    async def unmapped_dishes(self) -> Dict[str, Any]:
        """订单里出现过、但 dish_stations 里还没有映射的菜品名单。"""
        await self._ensure_loaded()
        all_order_dishes = await self._orders.list_distinct_order_dish_names(limit=100000)
        mapped = set(self._mapping.keys())
        unmapped = [d for d in all_order_dishes if d and d.strip() and d not in mapped]
        existing = list(mapped)
        return {
            "success": True,
            "dishes": unmapped,
            "existing_mappings": existing,
            "total": len(unmapped),
            "existing_count": len(existing),
        }
