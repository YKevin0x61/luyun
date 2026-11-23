#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商鹏云打印（https://spyun.net）的开放接口客户端。

平台把打印机做成「4G/WiFi 直连云端」，我们只推文本：HTTP 进、云端下发、机器出票，
离线时订单在云端排队、联网补打。文档见 https://spyun.net/open/index.html。

这里只封 **打印链路要用到的四个接口**（add / print / info / getModel），
不提供删设备、清队列这类写操作——店里不需要，少一个能误点坏设备的入口。

两个容易踩的点，写在这里而不是交给调用方：

1. **签名**：所有非空参数按参数名 ASCII 升序拼 k=v&k=v，末尾再接
   &appsecret=<密钥>，MD5 后转大写。官方文档的示例正文里 timestamp 写错
   过一次（正文写 1544765873、签名实际用 1544765945），照正文抄会一直报
   「签名错误」；本模块的 make_sign 用文档 sign 原值做过回归（见
   tests/test_spyun_printer.py）。
2. **失败判定**：接口几乎总是返回 HTTP 200，真正的结果在 JSON 的
   errorcode 里（0 才算成功，负数全局、正数逐接口自定义）。只看状态码
   会把「签名错误」当成打印成功。
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Mapping, Optional

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_PRINTER_NAME",
    "SpyunError",
    "SpyunClient",
    "make_sign",
    "normalize_code",
]

BASE_URL = "https://open.spyun.net/v1/"
DEFAULT_TIMEOUT_SECONDS = 10.0

#: 平台全局错误码（各接口自己的正数码另见文档）。
GLOBAL_ERRORS = {
    0: "成功",
    -1: "appid 为空",
    -2: "appid 不存在",
    -3: "时间戳为空",
    -4: "签名错误",
}

#: /printer/add 的「这台设备已经添加过」。重复绑定不该算失败：设备就在
#: 这个 appid 下，接着打印就行。
CODE_ALREADY_ADDED = 5

#: printer/add 的 name 平台当必填（空串回 {"errorcode":3,"errormsg":"Field name is required"}），
#: 这里给个兜底名，免得前端只填 SN/PKEY 就绑不上。
DEFAULT_PRINTER_NAME = "厨务管家"


class SpyunError(Exception):
    """云打印平台的业务错误。

    code 是平台错误码（负数全局、正数逐接口），没有错误码时给 None
    （比如网络不通、返回体不是 JSON）。
    """

    def __init__(self, message: str, *, code: Optional[int] = None):
        self.code = code
        self.message = message
        super().__init__(message)

    @property
    def is_sign_error(self) -> bool:
        return self.code == -4

    @property
    def is_unknown_appid(self) -> bool:
        return self.code == -2


def normalize_code(raw: Any) -> Optional[int]:
    """把 errorcode 的各种写法归一成 int；认不出时返回 None。"""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def make_sign(params: Mapping[str, Any], appsecret: str) -> str:
    """按平台规则算签名。

    只签非空参数（空串视为没传——/printer/add 的 pkey 就是可选的那种），
    排除 sign 自己，排序口径是参数名的 ASCII 升序。
    """
    chunks: list[str] = []
    for key in sorted(str(name) for name in params):
        if key == "sign":
            continue
        value = params.get(key)
        if value is None:
            continue
        text = str(value)
        if text == "":
            continue
        chunks.append(key + "=" + text)
    raw = "&".join(chunks)
    raw = (raw + "&" if raw else "") + "appsecret=" + appsecret
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


class SpyunClient:
    """一个 appid 的客户端。构造不联网，第一次调用才发请求。"""

    def __init__(
        self,
        appid: str,
        appsecret: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.appid = (appid or "").strip()
        self._appsecret = (appsecret or "").strip()
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.timeout = timeout
        self._transport = transport

    # ── 底层 ──────────────────────────────────────────────────

    def _params(self, extra: Mapping[str, Any]) -> dict[str, str]:
        """补 appid / timestamp 并算签名；返回可直接塞进 body 的字符串表。"""
        params: dict[str, str] = {"appid": self.appid, "timestamp": str(int(time.time()))}
        for key, value in extra.items():
            if value is None:
                continue
            params[key] = str(value)
        params["sign"] = make_sign(params, self._appsecret)
        return params

    async def _request(self, method: str, path: str, params: Mapping[str, str]) -> dict:
        url = self.base_url + path
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self._transport
            ) as client:
                if method.upper() == "GET":
                    response = await client.get(url, params=params)
                else:
                    response = await client.post(url, data=params)
        except httpx.HTTPError as exc:
            raise SpyunError("连不上商鹏云打印服务：" + str(exc)) from exc

        try:
            payload = response.json()
        except ValueError:
            snippet = (response.text or "").strip()[:120]
            raise SpyunError(
                "商鹏云返回的不是 JSON（HTTP " + str(response.status_code) + "）：" + snippet
            ) from None
        if not isinstance(payload, dict):
            raise SpyunError("商鹏云返回了意料之外的结构：" + str(payload)[:120])

        code = normalize_code(payload.get("errorcode"))
        if code is None:
            # 少数接口只回业务字段（如 getModel 的 model_type），没有 errorcode
            if response.status_code == 200:
                return payload
            raise SpyunError("商鹏云返回 HTTP " + str(response.status_code), code=None)
        if code != 0:
            message = str(payload.get("errormsg") or "").strip()
            if not message:
                message = GLOBAL_ERRORS.get(code) or ("错误码 " + str(code))
            if code == -4:
                # 签名错误几乎都是配置填错；日志里给上下文，但别把密钥打出去。
                logger.warning("商鹏云签名错误：appid=%s，请核对 appsecret", self.appid)
            raise SpyunError(message, code=code)
        return payload

    # ── 设备 ──────────────────────────────────────────────────

    async def add_printer(
        self, sn: str, pkey: str, *, name: str = "", business: int = 1
    ) -> dict:
        """把设备绑到这个 appid 下。重复绑定时返回 already_added=True。"""
        params = self._params(
            {
                "business": business,
                "sn": sn,
                "pkey": pkey,
                "name": (name or "").strip() or DEFAULT_PRINTER_NAME,
            }
        )
        try:
            payload = await self._request("POST", "printer/add", params)
        except SpyunError as exc:
            if exc.code == CODE_ALREADY_ADDED:
                return {"already_added": True, "sn": sn}
            raise
        return {"already_added": False, "sn": sn, "raw": payload}

    async def printer_info(self, sn: str) -> dict:
        """查在线状态：online 0/1、sqsnum 待打数、model_type 型号。"""
        return await self._request("GET", "printer/info", self._params({"sn": sn}))

    async def model_type(self, sn: str, pkey: str) -> int:
        """0=58mm 热敏 2=出餐宝 3=58 票据+扫码 4=80mm 热敏 5=桌面标签。"""
        payload = await self._request(
            "POST", "printer/getModel", self._params({"sn": sn, "pkey": pkey})
        )
        try:
            return int(payload.get("model_type"))
        except (TypeError, ValueError):
            return -1

    # ── 打印 ──────────────────────────────────────────────────

    async def print_text(self, sn: str, content: str, *, times: int = 1) -> dict:
        """推一张小票。返回平台的订单信息（id / create_time）。"""
        return await self._request(
            "POST",
            "printer/print",
            self._params({"sn": sn, "content": content, "times": int(times)}),
        )

    async def order_status(self, order_id: Any) -> dict:
        """查某张票是否真的打出来了（status 布尔 + print_time）。"""
        return await self._request(
            "GET", "printer/order/status", self._params({"id": order_id})
        )
