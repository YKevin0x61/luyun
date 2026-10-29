#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""移动银台 Pro 登录：纯代码实现，不依赖 App、不依赖 Frida。

逆向结论（2026-10-29，blutter 反汇编 + 8 组真实样本交叉验证）：

1. ``accesskey`` 不是服务端下发，而是客户端用**硬编码对称密钥**算出来的::

       a = int(devRandom[0])          # 第一位
       b = int(devRandom[1:])         # 第二位起的数字
       accesskey = base64( AES-128-ECB( b"d8e579b9d49447c4",
                                        PKCS7( devId[a : a+b] ) ) )

   密钥 ``d8e579b9d49447c4`` 来自 ``package:canyin7_flutter/model/util/AESUtils.dart``
   的 ``getAccessKey``；明文永远是 ``devId`` 的一段切片，切片位置由服务端下发的
   ``devRandom`` 决定。8 组真实 (devId, devRandom, accesskey) 全部可复现。

2. **调用顺序是硬约束**：必须先 ``regdev``（不带 accesskey）注册设备拿到 ``devCode``，
   再 ``getversion`` 拿 ``devRandom`` —— ``devRandom`` 只在注册后下发，顺序反了会得到
   服务端不认的 key（表现为「未找到加密信息」）。

3. 除 ``accesskey`` / ``accesstoken`` / ``devid`` 外，还必须带
   ``producttype: 2``、``requesttype: 0``、``cy_locale: zh_CN``，否则同样报
   「未找到加密信息」或「APP版本过低」。
"""

from __future__ import annotations

import base64
import logging
import secrets
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)

#: AESUtils.getAccessKey 里硬编码的对称密钥
ACCESS_KEY_SECRET = b"d8e579b9d49447c4"

APP_VERSION = "3.1.4"
DEV_TYPE = 4
APP_ID = "004"
DART_UA = "Dart/3.2 (dart:io)"

DEVICE_PATH = "/cy7/canyin/ordererreceive/mobiledevices"
TOKEN_PATH = "/cy7/canyin/apiauth/accesstoken/getaccesstokenforinside"

_AUTH_ERROR_HINTS = ("加密信息", "设备ID为空", "版本过低", "未登录", "登录失败", "档案更新失败")


class YintaiLoginError(RuntimeError):
    """登录链路失败。"""


@dataclass(frozen=True)
class YintaiSession:
    """一次成功登录后的会话上下文。"""

    base_url: str
    dev_id: str
    dev_code: str
    access_key: str
    access_token: str
    user_id: str = ""
    user_name: str = ""
    shop_id: str = ""

    def headers(self) -> dict[str, str]:
        """后续业务接口要用的请求头。"""
        return {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": DART_UA,
            "producttype": "2",
            "requesttype": "0",
            "cy_locale": "zh_CN",
            "contenttype": "application/x-www-form-urlencoded",
            "devid": self.dev_id,
            "accesskey": self.access_key,
            "accesstoken": self.access_token,
        }


def _pkcs7(data: bytes, block: int = 16) -> bytes:
    pad_len = block - (len(data) % block)
    return data + bytes([pad_len]) * pad_len


def make_access_key(dev_id: str, dev_random: str) -> str:
    """按 App 的算法算出 accesskey。

    ``dev_random`` 是 ``getversion`` 下发的数字串，例如 ``"716"``。
    """
    rnd = str(dev_random).strip()
    if len(rnd) < 2 or not rnd.isdigit():
        raise YintaiLoginError(f"devRandom 不合法：{dev_random!r}")
    start = int(rnd[0])
    length = int(rnd[1:])
    plaintext = dev_id[start:start + length]
    if len(plaintext) != length:
        raise YintaiLoginError(
            f"devRandom={dev_random} 要求 devId[{start}:{start + length}]，"
            f"但 devId 只有 {len(dev_id)} 位"
        )
    cipher = Cipher(algorithms.AES(ACCESS_KEY_SECRET), modes.ECB())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(_pkcs7(plaintext.encode("utf-8"))) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


def _base_headers(dev_id: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": DART_UA,
        "producttype": "2",
        "requesttype": "0",
        "cy_locale": "zh_CN",
        "contenttype": "application/x-www-form-urlencoded",
        "devid": dev_id,
    }


def _is_auth_error(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    if body.get("result") == 1:
        return False
    msg = str(body.get("msg") or body.get("message") or "")
    return any(hint in msg for hint in _AUTH_ERROR_HINTS)


async def login_yintai(
    base_url: str,
    *,
    user_name: str,
    password: str,
    dev_id: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = 30.0,
) -> YintaiSession:
    """走完 regdev → getversion → accesskey → token → archupdate → login。

    ``dev_id`` 省略时随机生成一个（32 位 hex），服务端会在 ``regdev`` 时登记它。
    """
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    base = base_url.rstrip("/")
    device = dev_id or secrets.token_hex(16)

    try:
        # 1) 设备注册（此时还没有 accesskey）
        headers = _base_headers(device)
        resp = await client.post(
            f"{base}{DEVICE_PATH}/regdev",
            headers=headers,
            json={
                "appVersion": APP_VERSION,
                "devId": device,
                "devType": DEV_TYPE,
                "supportStandardFlg": 1,
                "industryFlg": 1,
                "versionFlag": 2,
                "isCashierPos": 0,
            },
        )
        body = resp.json()
        if body.get("result") != 1:
            raise YintaiLoginError(f"regdev 失败：{body.get('msg') or body}")
        dev_data = body.get("data") or {}
        dev_code = str(dev_data.get("devCode") or "")
        if not dev_code:
            raise YintaiLoginError("regdev 未返回 devCode")

        # 2) 取 devRandom（必须在上一步之后）
        resp = await client.post(
            f"{base}{DEVICE_PATH}/getversion",
            headers=headers,
            json={
                "devId": device,
                "devType": DEV_TYPE,
                "versionFlag": 2,
                "industryFlg": 1,
                "isCashierPos": 0,
            },
        )
        body = resp.json()
        if body.get("result") != 1:
            raise YintaiLoginError(f"getversion 失败：{body.get('msg') or body}")
        ver = body.get("data") or {}
        dev_random = ver.get("devRandom")
        access_key = make_access_key(device, str(dev_random))

        # 3) 带上版本头与自算的 accesskey
        headers.update(
            accesskey=access_key,
            cy_version=str(ver.get("cyVersion") or ""),
            protocal_version=str(ver.get("protocolVersion") or ""),
        )

        # 4) 换 accesstoken
        resp = await client.post(
            f"{base}{TOKEN_PATH}", headers=headers, json={"appId": APP_ID}
        )
        body = resp.json()
        token = (body.get("data") or {}).get("accessToken")
        if not token:
            raise YintaiLoginError(f"取 accesstoken 失败：{body.get('msg') or body}")
        headers["accesstoken"] = token

        # 5) 拉档案（App 在登录前必做，否则登录报「未找到加密信息」）
        resp = await client.post(
            f"{base}{DEVICE_PATH}/archupdate",
            headers=headers,
            json={
                "archType": 1,
                "devCode": dev_code,
                "devId": device,
                "versionFlag": 2,
                "supportStandardFlg": 1,
                "industryFlg": 1,
                "isCashierPos": 0,
            },
        )
        body = resp.json()
        if body.get("result") != 1:
            if _is_auth_error(body):
                raise YintaiLoginError(f"archupdate 鉴权失败：{body.get('msg')}")
            raise YintaiLoginError(f"archupdate 失败：{body.get('msg') or body}")

        # 6) 登录
        resp = await client.post(
            f"{base}{DEVICE_PATH}/login",
            headers=headers,
            json={
                "userName": user_name,
                "password": password,
                "devCode": dev_code,
                "devId": device,
                "devType": DEV_TYPE,
                "appVersion": APP_VERSION,
                "shopMenuFlg": 1,
                "versionFlag": 2,
                "isCashierPos": 0,
                "supportStandardFlg": 1,
                "industryFlg": 1,
                "supportBuffetFlg": 1,
            },
        )
        body = resp.json()
        if body.get("result") != 1:
            raise YintaiLoginError(f"login 失败：{body.get('msg') or body}")
        data = body.get("data") or {}

        session = YintaiSession(
            base_url=base,
            dev_id=device,
            dev_code=dev_code,
            access_key=access_key,
            access_token=str(token),
            user_id=str(data.get("id") or ""),
            user_name=str(data.get("name") or ""),
            shop_id=str(dev_data.get("shopId") or ""),
        )
        logger.info(
            "移动银台登录成功：user=%s userId=%s devCode=%s",
            session.user_name,
            session.user_id,
            session.dev_code,
        )
        return session

    finally:
        if owns and client is not None:
            await client.aclose()
