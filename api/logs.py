#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志查看 API
===========

- `/api/logs`           历史日志分页查询（持久化）
- `/api/logs/recent`    内存实时（兼容旧版）
- `/api/logs/persisted/recent` 持久化的最新 N 条
- `/api/logs/facets`    可选 level / logger
- `/api/logs/stats`     写入统计
- `/api/logs/cleanup`   手动清理旧日志
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from services.log_storage import log_storage, CHINA_TZ
from api.security import verify_admin_token
from fastapi import Depends

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/logs",
    tags=["logs"],
    dependencies=[Depends(verify_admin_token)],
)


def _parse_since(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    s = value.strip()
    # 纯数字当作 epoch 秒
    if s.isdigit():
        return float(s)
    # ISO 时间
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CHINA_TZ)
        return dt.timestamp()
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"无法解析 since 时间: {value}（支持 ISO 时间或 epoch 秒）",
        )


@router.get("")
async def list_logs(
    level: Optional[str] = Query(None, description="日志级别过滤，如 INFO / WARNING / ERROR"),
    logger_name: Optional[str] = Query(None, alias="logger", description="logger 名称过滤"),
    q: Optional[str] = Query(None, description="模糊搜索 message / exception"),
    since: Optional[str] = Query(None, description="起始时间，ISO 或 epoch 秒"),
    until: Optional[str] = Query(None, description="结束时间，ISO 或 epoch 秒"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """分页查询持久化日志。"""
    try:
        items, total = await log_storage.query(
            level=level,
            logger_name=logger_name,
            q=q,
            since_epoch=_parse_since(since),
            until_epoch=_parse_since(until),
            limit=limit,
            offset=offset,
        )
        return {
            "success": True,
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("查询日志失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"查询日志失败: {exc}")


@router.get("/recent")
async def get_recent_logs(
    limit: int = Query(200, ge=1, le=500),
    after_id: int = Query(0, ge=0),
):
    """
    实时日志（内存）。仅保留进程内最近 1200 条，用于 WebSocket-style 拉取。
    持久化的完整历史请走 `GET /api/logs`。
    """
    try:
        from main import in_memory_log_handler

        safe_limit = max(1, min(limit, 500))
        if after_id > 0:
            logs = in_memory_log_handler.after(after_id, safe_limit)
        else:
            logs = in_memory_log_handler.recent(safe_limit)
        return {
            "success": True,
            "items": logs,
            "latest_id": in_memory_log_handler.latest_id,
            "count": len(logs),
        }
    except Exception as exc:
        logger.error("获取实时日志失败: %s", exc)
        raise HTTPException(status_code=500, detail="获取实时日志失败")


@router.get("/persisted/recent")
async def get_persisted_recent(limit: int = Query(50, ge=1, le=200)):
    """持久化的最新 N 条（按 id 倒序）。"""
    try:
        items = await log_storage.latest(limit=limit)
        return {"success": True, "items": items, "count": len(items)}
    except Exception as exc:
        logger.error("获取持久化日志失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"获取持久化日志失败: {exc}")


@router.get("/facets")
async def get_facets():
    """返回可选的 level / logger 维度，便于前端构建过滤器。"""
    try:
        data = await log_storage.facets()
        return {"success": True, **data}
    except Exception as exc:
        logger.error("获取日志 facets 失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"获取 facets 失败: {exc}")


@router.get("/stats")
async def get_stats():
    """日志存储统计（总数、写入量、队列状态等）。"""
    try:
        return {"success": True, **(await log_storage.stats())}
    except Exception as exc:
        logger.error("获取日志统计失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"获取统计失败: {exc}")


@router.post("/cleanup")
async def cleanup_logs(days: int = Query(7, ge=1, le=365)):
    """删除 N 天前的日志。"""
    try:
        deleted = await log_storage.cleanup_older_than(days)
        logger.info(f"🧹 手动清理日志: 删除 {deleted} 条 {days} 天前的记录")
        return {"success": True, "deleted": deleted, "days": days}
    except Exception as exc:
        logger.error("清理日志失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"清理失败: {exc}")
