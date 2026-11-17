#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""健康证官方查询 API（管理端）。

三个端点：

* GET  /api/health-cert/settings  当前查询网址 + 从它拆出来的机构编号/接口地址；
* PUT  /api/health-cert/settings  保存查询网址（平台换链接时用，保存前就校验形状）；
* POST /api/health-cert/lookup    姓名 + 身份证查官方健康证。

配置放 app_settings（key=health_cert_lookup_url），不新建表：换网址是一次性的运维
动作，值只有一条。查询走 services/health_cert_lookup.py，本层只做鉴权、取配置与
错误翻译，不碰外部协议细节。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.security import require_session
from database import DatabaseManager, get_db
from services.health_cert_lookup import (
    DEFAULT_LOOKUP_URL,
    SETTINGS_KEY,
    HealthCertLookupError,
    lookup_health_certs,
    parse_lookup_url,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health-cert", tags=["health-cert"])


class LookupIn(BaseModel):
    """查询入参：只收姓名与身份证（与官方页面一致，没有别的筛选条件）。"""

    name: str = Field("", max_length=40, description="姓名")
    id_card: str = Field("", max_length=32, description="身份证号")


class LookupUrlIn(BaseModel):
    """查询网址：存整条页面地址，机构编号与接口前缀从它推。"""

    url: str = Field("", max_length=500, description="健康证查询页地址")


def _mask_id_card(value: str) -> str:
    """日志用的脱敏：留住前 6 后 4，中间打码（够核对，又不落全号）。"""
    text = str(value or "").strip()
    if len(text) <= 10:
        return "***" if text else ""
    return f"{text[:6]}********{text[-4:]}"


async def _stored_url(db: DatabaseManager) -> str:
    """读取当前查询网址：兼容 {"url": ...} 与裸字符串两种存量形状。"""
    stored = await db.settings_get_json(SETTINGS_KEY, None)
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    if isinstance(stored, dict):
        value = str(stored.get("url") or "").strip()
        if value:
            return value
    return DEFAULT_LOOKUP_URL


@router.get("/settings")
async def get_lookup_settings(
    _session_id: str = Depends(require_session),
    db: DatabaseManager = Depends(get_db),
) -> Dict[str, Any]:
    """当前配置（读取时也解析一遍：库里的值坏了要在这里就暴露，不等查询）。"""
    url = await _stored_url(db)
    updated_at = await db.settings_updated_at(SETTINGS_KEY)
    org_id = ""
    api_url = ""
    error = ""
    try:
        target = parse_lookup_url(url)
        org_id = target.org_id
        api_url = target.api_url
    except HealthCertLookupError as exc:
        error = exc.message
    return {
        "success": True,
        "url": url,
        "default_url": DEFAULT_LOOKUP_URL,
        "is_default": url == DEFAULT_LOOKUP_URL,
        "org_id": org_id,
        "api_url": api_url,
        "updated_at": updated_at,
        "error": error,
    }


@router.put("/settings")
async def update_lookup_settings(
    body: LookupUrlIn,
    _session_id: str = Depends(require_session),
    db: DatabaseManager = Depends(get_db),
) -> Dict[str, Any]:
    """保存查询网址。形状不合法直接 400，别把一个查不动的地址存进去。"""
    try:
        target = parse_lookup_url(body.url)
    except HealthCertLookupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    saved = await db.settings_set_json(SETTINGS_KEY, {"url": target.page_url})
    if not saved:
        raise HTTPException(status_code=500, detail="查询网址保存失败，请稍后重试")
    logger.info("健康证查询网址已更新：orgId=%s url=%s", target.org_id, target.page_url)
    return {
        "success": True,
        "url": target.page_url,
        "org_id": target.org_id,
        "api_url": target.api_url,
        "default_url": DEFAULT_LOOKUP_URL,
        "is_default": target.page_url == DEFAULT_LOOKUP_URL,
    }


@router.post("/lookup")
async def lookup(
    body: LookupIn,
    _session_id: str = Depends(require_session),
    db: DatabaseManager = Depends(get_db),
) -> Dict[str, Any]:
    """按姓名 + 身份证查官方健康证；没配网址/连不上/上游异常各带自己的状态码。"""
    url = await _stored_url(db)
    try:
        result = await lookup_health_certs(body.name, body.id_card, url)
    except HealthCertLookupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    logger.info(
        "健康证查询：%s %s → %d 条（orgId=%s）",
        result["name"],
        _mask_id_card(result["id_card"]),
        len(result["items"]),
        result["org_id"],
    )
    return {"success": True, **result}
