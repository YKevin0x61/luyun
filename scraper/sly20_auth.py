#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""龙管家 2.0 auth-center login (httpx, no browser)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

CYFAST_VERSION_URL = (
    "https://cyfast.tcsl.com.cn/fast/sysapi/system/version/qdCy7AppVersion"
)
SLY_BASE = "https://sly.tcsl.com.cn/newProxy"
OKHTTP_UA = "okhttp/4.12.0"
# cy7mm 移动端报表（productCode=005）— scraper 使用的业务线
CY7MM_PRODUCT_CODE = "005"


def _sly_api_headers(token: str) -> dict[str, str]:
    return {
        "TCSL-BP-TOKEN": token,
        "User-Agent": OKHTTP_UA,
        "Content-Type": "application/json",
        "Origin": "https://cy7mm.wuuxiang.com",
        "Referer": "https://cy7mm.wuuxiang.com/",
    }


@dataclass(frozen=True)
class Sly20LoginResult:
    ok: bool
    token: Optional[str] = None
    principal: Optional[dict[str, Any]] = None
    message: str = ""
    payload: Optional[dict[str, Any]] = None


def encrypt_login_body(mobile: str, password: str, public_key_b64: str) -> str:
    public_key = serialization.load_der_public_key(
        base64.b64decode(public_key_b64),
        backend=default_backend(),
    )
    plaintext = json.dumps(
        {"mobile": mobile, "password": password},
        separators=(",", ":"),
    )
    encrypted = public_key.encrypt(plaintext.encode("utf-8"), padding.PKCS1v15())
    return json.dumps(base64.b64encode(encrypted).decode("ascii"))


async def login_sly20(
    phone: str,
    password: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> Sly20LoginResult:
    """Perform auth-center 3-step login; returns TCSL-BP-TOKEN on success."""
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    try:
        version_resp = await client.post(
            CYFAST_VERSION_URL,
            json={"appType": "housekeep", "shopId": "0", "type": 1},
            headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": OKHTTP_UA},
        )

        key_resp = await client.post(
            f"{SLY_BASE}/auth-center/api/ano/rsa/key/publick",
            json={},
            headers={"User-Agent": OKHTTP_UA},
        )
        key_payload = key_resp.json()
        public_key = (key_payload.get("data") or {}).get("publicKey")
        if not public_key:
            return Sly20LoginResult(
                ok=False,
                message="RSA public key missing",
                payload=key_payload,
            )

        login_body = encrypt_login_body(phone, password, public_key)
        login_resp = await client.post(
            f"{SLY_BASE}/auth-center/app/v1/ano/login/mobile",
            content=login_body,
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "User-Agent": OKHTTP_UA,
            },
        )
        login_payload = login_resp.json()
        if login_resp.status_code != 200 or login_payload.get("code") != 2000:
            return Sly20LoginResult(
                ok=False,
                message=str(login_payload.get("message") or "login/mobile failed"),
                payload=login_payload,
            )

        data = login_payload.get("data") or {}
        token = data.get("token")
        principal = data.get("principal")
        if not token or not principal:
            return Sly20LoginResult(
                ok=False,
                message="login/mobile response missing token or principal",
                payload=login_payload,
            )

        return Sly20LoginResult(
            ok=True,
            token=token,
            principal=principal,
            payload=login_payload,
        )
    finally:
        if owns_client:
            await client.aclose()


def build_cy7mm_biz_data(
    *,
    token: str,
    principal: dict[str, Any],
    target_path: str,
    shop_id: str | None = None,
) -> dict[str, Any]:
    """Build bizData payload for cy7mm tempPage ``window.sendData``."""
    org_id = principal.get("orgId") or principal.get("corpId")
    login_data = {
        "authenticated": True,
        "principal": {
            "mobile": principal.get("mobile") or "",
            "userId": str(principal.get("accountId") or principal.get("userId") or ""),
            "userName": principal.get("nickName") or principal.get("username") or "",
        },
    }
    return {
        "flag": "2.0",
        "defGroupId": str(org_id or ""),
        "shopId": shop_id,
        "token": token,
        "loginData": login_data,
        "name": None,
        "path": target_path,
        "code": None,
        "dimension": None,
    }


def parse_manageable_shop_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Extract cy7mm (product 005) shops from getALlManageableShopList response."""
    rows: list[dict[str, str]] = []
    data = payload.get("data")
    if not isinstance(data, list):
        return rows

    for group in data:
        if not isinstance(group, dict):
            continue
        for shop in group.get("shops") or []:
            if not isinstance(shop, dict):
                continue
            fallback_name = str(shop.get("shopName") or "").strip()
            for biz in shop.get("bizShops") or []:
                if not isinstance(biz, dict):
                    continue
                if str(biz.get("productCode") or "") != CY7MM_PRODUCT_CODE:
                    continue
                shop_id = str(biz.get("bizGroupCode") or "").strip()
                company_id = str(biz.get("bizShopCode") or "").strip()
                if not shop_id or not company_id:
                    continue
                shop_name = str(biz.get("bizShopName") or fallback_name or "").strip()
                rows.append(
                    {
                        "shop_id": shop_id,
                        "company_id": company_id,
                        "shop_name": shop_name,
                        "delivery_shop_id": company_id,
                        "product_code": CY7MM_PRODUCT_CODE,
                    }
                )
    return rows


async def discover_pos_shops(
    phone: str,
    password: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """2.0 login + housekeeper shop list → cy7mm shop_id/company_id for setup form."""
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    try:
        login = await login_sly20(phone, password, client=client)
        if not login.ok or not login.token:
            return {"ok": False, "message": login.message or "2.0 登录失败"}

        principal = login.principal or {}
        org_id = str(principal.get("orgId") or principal.get("corpId") or "").strip()
        if not org_id:
            return {"ok": False, "message": "登录成功但未返回 orgId，无法拉取门店列表"}

        shop_resp = await client.post(
            f"{SLY_BASE}/housekeeper/app/v1/org/getALlManageableShopList",
            json={"groupIds": [org_id]},
            headers=_sly_api_headers(login.token),
        )
        shop_payload = shop_resp.json()
        if shop_resp.status_code != 200 or shop_payload.get("code") != 2000:
            return {
                "ok": False,
                "message": str(shop_payload.get("message") or "getALlManageableShopList 失败"),
                "payload": shop_payload,
            }

        shops = parse_manageable_shop_payload(shop_payload)
        if not shops:
            return {
                "ok": False,
                "message": "未找到 cy7mm 报表门店（productCode=005），请确认账号权限",
                "payload": shop_payload,
            }

        from urllib.parse import quote

        for shop in shops:
            shop_name = shop.get("shop_name") or ""
            shop["table_list_url"] = (
                f"https://cy7mm.wuuxiang.com/home/tableList/1/{shop['shop_id']}/{shop['company_id']}"
                f"?shopName={quote(shop_name)}"
            )

        return {
            "ok": True,
            "message": f"找到 {len(shops)} 个可管理门店",
            "shops": shops,
            "org_id": org_id,
            "account_id": str(principal.get("accountId") or ""),
        }
    finally:
        if owns_client:
            await client.aclose()
