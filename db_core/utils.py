#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库层通用工具函数：时区处理、慢查询计时、行转换。
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Any
from functools import wraps

import aiosqlite

logger = logging.getLogger(__name__)

CHINA_TZ = timezone(timedelta(hours=8))
ORDER_DEDUP_BATCH_SIZE = 900
SQLITE_JOURNAL_MODE_WAL = "WAL"
SQLITE_BUSY_TIMEOUT_MS = 5000


def timing_decorator(func):
    """性能监控装饰器"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            execution_time = (time.time() - start_time) * 1000
            if execution_time > 100:
                logger.warning(f"慢查询: {func.__name__} 耗时 {execution_time:.2f}ms")
            else:
                logger.debug(f"{func.__name__} 耗时 {execution_time:.2f}ms")
            return result
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            logger.error(f"{func.__name__} 执行失败，耗时 {execution_time:.2f}ms: {e}")
            raise
    return wrapper


def ensure_beijing_datetime(dt_input) -> datetime:
    """确保日期时间为北京时区"""
    if dt_input is None:
        return datetime.now(CHINA_TZ)
    if isinstance(dt_input, datetime):
        if dt_input.tzinfo is None:
            dt_input = dt_input.replace(tzinfo=CHINA_TZ)
        return dt_input.astimezone(CHINA_TZ)
    if isinstance(dt_input, str):
        if not dt_input:
            return datetime.now(CHINA_TZ)
        try:
            if 'T' in dt_input:
                dt = datetime.fromisoformat(dt_input.replace('Z', '+00:00'))
            else:
                dt = datetime.strptime(dt_input, '%Y-%m-%d %H:%M:%S')
                dt = dt.replace(tzinfo=CHINA_TZ)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CHINA_TZ)
            else:
                dt = dt.astimezone(CHINA_TZ)
            return dt
        except Exception as e:
            logger.warning(f"日期解析失败: {dt_input}, 使用当前时间: {e}")
            return datetime.now(CHINA_TZ)
    logger.warning(f"不支持的日期类型: {type(dt_input)}, 使用当前时间")
    return datetime.now(CHINA_TZ)


def to_sql_datetime(value: Any) -> Optional[str]:
    """Bind datetime (or ISO string) for SQLite order_time comparisons."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def row_to_dict(row: aiosqlite.Row) -> Dict:
    """将 aiosqlite.Row 转为 dict，id 转为字符串"""
    d = dict(row)
    if 'id' in d:
        d['_id'] = str(d.pop('id'))
    for flag in ('is_hold', 'is_rushed'):
        if flag in d:
            try:
                d[flag] = bool(int(d[flag] or 0))
            except (TypeError, ValueError):
                d[flag] = bool(d[flag])
    for ts_field in ('order_time', 'updated_at', 'created_at', 'fired_at'):
        if ts_field in d and d[ts_field]:
            try:
                d[ts_field] = datetime.fromisoformat(d[ts_field])
            except Exception:
                pass
    return d
