#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录凭据管理 API 路由。

提供门店账号、密码、店铺 ID 等敏感信息的读取（脱敏）、保存与清除接口。
保存后会自动通知 scraper 重新加载，无需重启服务。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from scraper.restaurant_scraper import RestaurantScraper
from scraper.sly20_auth import discover_pos_shops
from services import credentials_store
from services.credentials_store import CredentialBundle
from api.security import verify_admin_token

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/credentials",
    tags=["credentials"],
    dependencies=[Depends(verify_admin_token)],
)


# ==================== Pydantic 模型 ====================

class CredentialIn(BaseModel):
    phone: str = Field(..., description="登录账号（手机号）")
    password: Optional[str] = Field(
        None, description="登录密码；已配置凭据时留空表示沿用原密码"
    )
    shop_id: str = Field(..., description="tableList URL 第一个 ID（POS 后端的 centerId）")
    company_id: str = Field(..., description="tableList URL 第二个 ID（POS 后端的 shopId）")
    shop_name: str = Field(..., description="门店名称，用于 URL 上的 shopName 参数")
    delivery_shop_id: Optional[str] = Field(
        None, description="已结账单接口使用的 shopId，缺省时与 company_id 相同"
    )

    @field_validator("password", mode="before")
    @classmethod
    def _empty_password_to_none(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


class CredentialVerifyIn(BaseModel):
    """已存在凭据时各字段均可留空，服务端从加密文件沿用原值。"""

    model_config = ConfigDict(extra="ignore")

    phone: Optional[str] = Field(default=None, description="登录手机号，可空（沿用已保存）")
    password: Optional[str] = Field(default=None, description="登录密码，可空（沿用已保存）")
    shop_id: Optional[str] = Field(default=None, description="shop_id，可空（沿用已保存）")
    company_id: Optional[str] = Field(default=None, description="company_id，可空（沿用已保存）")
    shop_name: Optional[str] = Field(default=None, description="门店名称，可空（沿用已保存）")
    delivery_shop_id: Optional[str] = Field(
        default=None, description="delivery_shop_id，可空（沿用已保存）"
    )

    @field_validator(
        "phone",
        "password",
        "shop_id",
        "company_id",
        "shop_name",
        "delivery_shop_id",
        mode="before",
    )
    @classmethod
    def _empty_or_null_to_none(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


# ==================== 路由 ====================

@router.get("")
async def get_credentials_view() -> Dict[str, Any]:
    """获取当前凭据（脱敏）。"""
    bundle = credentials_store.get_credentials()
    if not bundle:
        return {"configured": False}
    return {
        "configured": True,
        **bundle.to_safe_dict(),
        "table_list_url": bundle.table_list_url,
    }


@router.post("")
async def save_credentials_view(payload: CredentialIn) -> Dict[str, Any]:
    """保存或更新凭据，并立即通知 scraper 重新加载。"""
    try:
        existing = credentials_store.get_credentials()
        data = payload.dict()
        if existing:
            if not data.get("password"):
                data["password"] = existing.password
            if not (data.get("delivery_shop_id") or "").strip():
                data["delivery_shop_id"] = existing.delivery_shop_id
        elif not data.get("password"):
            raise HTTPException(status_code=400, detail="首次配置必须填写密码")
        bundle = credentials_store.save_credentials(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("保存凭据失败: %s", exc)
        raise HTTPException(status_code=500, detail="保存凭据失败")

    await _notify_scraper_reload()

    return {
        "success": True,
        "configured": True,
        **bundle.to_safe_dict(),
        "table_list_url": bundle.table_list_url,
    }


def _resolve_verify_credentials(
    payload: CredentialVerifyIn,
    existing: CredentialBundle | None,
) -> tuple[str, str, str, str, str, str]:
    phone_in = (payload.phone or "").strip()
    password_in = payload.password if payload.password is not None else ""
    shop_id_in = (payload.shop_id or "").strip()
    company_id_in = (payload.company_id or "").strip()
    shop_name_in = (payload.shop_name or "").strip()
    delivery_in = (payload.delivery_shop_id or "").strip()

    if existing:
        phone = phone_in or existing.phone
        password = password_in if password_in != "" else existing.password
        shop_id = shop_id_in or existing.shop_id
        company_id = company_id_in or existing.company_id
        shop_name = shop_name_in or existing.shop_name
        delivery_shop_id = delivery_in or existing.delivery_shop_id or company_id
    else:
        phone = phone_in
        password = password_in
        shop_id = shop_id_in
        company_id = company_id_in
        shop_name = shop_name_in
        delivery_shop_id = delivery_in or company_id

    return phone, password, shop_id, company_id, shop_name, delivery_shop_id


def _validate_verify_phone_password_shop(
    phone: str,
    password: str,
    shop_id: str,
    company_id: str,
    shop_name: str,
    *,
    require_shop: bool = True,
) -> None:
    if not phone:
        raise HTTPException(status_code=400, detail="手机号不能为空（未配置凭据时请在表单填写）")
    if not password:
        raise HTTPException(status_code=400, detail="密码不能为空（未配置凭据时请在表单填写）")
    if require_shop and (not shop_id or not company_id or not shop_name):
        raise HTTPException(
            status_code=400,
            detail="缺少门店信息：未配置凭据时请填写 shop_id、company_id 与门店名称；已配置可留空沿用",
        )


@router.post("/discover-shops")
async def discover_shops_view(payload: CredentialVerifyIn) -> Dict[str, Any]:
    """龙管家 2.0 登录后拉取可管理的 cy7mm 门店列表（供 /setup 自动填充）。"""
    existing = credentials_store.get_credentials()
    phone, password, _, _, _, _ = _resolve_verify_credentials(payload, existing)
    _validate_verify_phone_password_shop(phone, password, "", "", "", require_shop=False)

    try:
        result = await asyncio.wait_for(
            discover_pos_shops(phone, password),
            timeout=45,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="拉取门店超时，请稍后重试")
    except Exception as exc:
        logger.error("拉取门店异常: %s", exc)
        raise HTTPException(status_code=500, detail="拉取门店失败")

    if not result.get("ok"):
        return {
            "success": False,
            "message": result.get("message") or "拉取门店失败",
        }
    return {
        "success": True,
        **result,
    }


@router.post("/verify-login")
async def verify_login_view(payload: CredentialVerifyIn) -> Dict[str, Any]:
    """龙管家 2.0 登录 + cy7mm SSO，再探针校验桌台/已结账单 API。"""
    existing = credentials_store.get_credentials()
    phone, password, shop_id, company_id, shop_name, delivery_shop_id = _resolve_verify_credentials(
        payload, existing
    )
    _validate_verify_phone_password_shop(phone, password, shop_id, company_id, shop_name)

    # 仅探测登录/会话是否成功，不涉及菜品分类，无需真实 DishCatalog
    probe = RestaurantScraper(None)
    probe.inject_credentials(
        CredentialBundle(
            phone=phone,
            password=password,
            shop_id=shop_id,
            company_id=company_id,
            shop_name=shop_name,
            delivery_shop_id=delivery_shop_id,
        )
    )
    probe.prepare_unrestricted_probe()

    try:
        await probe.init_browser(headless=True)
        browser_login_ok = await asyncio.wait_for(
            probe.login(phone, password),
            timeout=70,
        )
        current_url = probe.page.url if probe.page else ""
        if not browser_login_ok:
            return {
                "success": False,
                "login_ok": False,
                "message": "龙管家 2.0 登录或 cy7mm SSO 失败，请检查账号密码与门店 ID",
                "current_url": current_url,
            }

        table_list_url = probe.table_list_url
        await asyncio.wait_for(
            probe.page.goto(
                table_list_url,
                wait_until="domcontentloaded",
                timeout=30_000,
            ),
            timeout=35,
        )
        await asyncio.sleep(0.4)
        current_url = probe.page.url
        if "login" in (current_url or "").lower():
            return {
                "success": False,
                "login_ok": False,
                "message": "打开餐桌列表后被重定向到登录页，会话无效",
                "current_url": current_url,
            }

        api_result = await asyncio.wait_for(
            probe.probe_busy_point_api_login_ok(),
            timeout=40,
        )
        if not api_result.get("ok"):
            return {
                "success": False,
                "login_ok": False,
                "message": api_result.get("message") or "餐桌列表 API 校验未通过",
                "current_url": current_url,
                "api_http_status": api_result.get("http_status"),
            }

        settled_result = await asyncio.wait_for(
            probe.probe_settled_bill_apis(),
            timeout=40,
        )
        if not settled_result.get("ok"):
            return {
                "success": False,
                "login_ok": False,
                "message": settled_result.get("message") or "已结账单 API 校验未通过",
                "current_url": current_url,
                "api_http_status": settled_result.get("http_status"),
                "table_rows": api_result.get("rows_count"),
            }

        settled_message = "已结账单 API 校验通过"
        if settled_result.get("detail_skipped"):
            settled_message = settled_result.get("detail_message") or "当日无已结账单"

        return {
            "success": True,
            "login_ok": True,
            "message": "龙管家 2.0 登录与 cy7mm 业务 API 校验通过，可保存凭据",
            "current_url": current_url,
            "api_http_status": api_result.get("http_status"),
            "table_rows": api_result.get("rows_count"),
            "settled_bill_count": settled_result.get("bill_count"),
            "settled_detail_skipped": settled_result.get("detail_skipped"),
            "settled_dish_count": settled_result.get("dish_count"),
            "settled_message": settled_message,
        }
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="登录验证超时，请稍后重试")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("登录验证异常: %s", exc)
        raise HTTPException(status_code=500, detail="登录验证失败")
    finally:
        try:
            await probe.close()
        except Exception as close_error:
            logger.warning("验证爬虫关闭失败: %s", close_error)


@router.delete("")
async def delete_credentials_view() -> Dict[str, Any]:
    """清空已保存的凭据，并通知 scraper 进入待机状态。"""
    existed = credentials_store.clear_credentials()
    logger.info("🗑️ [审计] 凭据已被清空（removed=%s）", existed)
    await _notify_scraper_reload()
    return {"success": True, "removed": existed}


# ==================== 内部 ====================

async def _notify_scraper_reload() -> None:
    """让运行中的 scraper 立即重新读取凭据。"""
    try:
        from main import restaurant_scraper

        if restaurant_scraper and hasattr(restaurant_scraper, "reload_credentials"):
            await restaurant_scraper.reload_credentials()
    except Exception as exc:
        logger.warning("通知 scraper 重新加载凭据失败: %s", exc)


async def _notify_scraper_reload_runtime(db) -> None:
    """让运行中的 scraper 立即重新读取运行配置（导入包含运行配置时）。"""
    try:
        from main import restaurant_scraper

        if restaurant_scraper and hasattr(restaurant_scraper, "reload_runtime_settings"):
            await restaurant_scraper.reload_runtime_settings(db)
    except Exception as exc:
        logger.warning("通知 scraper 重新加载运行配置失败: %s", exc)
