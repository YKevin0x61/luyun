#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""移动银台 Pro（商龙餐饮7 收银端）取数客户端。

2026-10-29 抓包实测的结论（详见 docs/pos/YINTAI_API.md）：

- 数据源是**门店自建服务器**（形如 ``https://<门店>.nat.tcsl.com.cn``），
  接口前缀 ``/cy7/canyin/ordererreceive/mobiledevices/``。
- 取数只需要三个请求头，**不需要账号密码、不需要浏览器**：

    ``accesskey``    设备级持久值，App 本地保存；App 重启、重走注册流程都不变，
                     只有「设备重新注册」（重装 / 清数据）才会换新并令旧值作废。
    ``accesstoken``  JWT，由 ``getaccesstokenforinside`` 下发。
    ``devid``        设备指纹。

- 因此正常的复用姿势是「抓一次、长期用」；一旦返回
  ``加密信息不正确`` / ``设备ID为空``，说明这组三件套作废，需要用 App 重新抓。

本模块只做取数，不改动既有的 cy7mm 采集链路。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import httpx

logger = logging.getLogger(__name__)

DEVICE_PATH = "/cy7/canyin/ordererreceive/mobiledevices"
BILL_PATH = "/cy7/canyin/ordererreceive/mobiledevicesbill"
TOKEN_PATH = "/cy7/canyin/apiauth/accesstoken/getaccesstokenforinside"

DART_UA = "Dart/3.2 (dart:io)"
APP_VERSION = "3.1.4"
APP_ID = "004"
DEV_TYPE = 4

#: 服务端在 accesskey/设备信息不可用时的回执关键字
AUTH_ERROR_HINTS = ("加密信息", "设备ID为空", "版本过低", "未登录", "登录失效")


class YintaiError(RuntimeError):
    """移动银台接口调用失败。"""


#: ``posserial`` 之类的接口名字看不出语义，但会真实改门店业务状态。
#: 2026-10-29 实测 ``posserial`` 返回「结班成功」，把门店班次结掉，
#: 之后所有设备（含 App 本机）登录都报「POS 尚未开班」。
#: 因此客户端**默认拒绝**白名单外的接口，新增调用必须先确认语义再登记。
READ_ONLY_ENDPOINTS: frozenset[str] = frozenset({
    # 认证链
    "regdev", "getversion", "archupdate", "getaccesstokenforinside", "login",
    # 桌台
    "pointlist", "querypointlist", "justquerypointstate", "pointtypelist", "arealist",
    # 账单（只读视图）
    "mergebillview", "querybillbytotal", "querybillbyorder", "querysettledbillswithpoints",
    "querysettleddetails", "querywuuorder", "querywuuorderdetail", "queryguestbill",
    "billremarklist", "querydepositdetail",
    # 菜品/档案
    "itemlist", "itemclasslist", "pkglist", "sizelist", "itemsizelist",
    "methodlist", "methodclasslist", "methodgrouplist", "methodgrouplistformenu",
    "allpaywaylist", "settlepaywaygroup", "shoplist", "shiftlist", "emplist",
    "dutylist", "keepaccountposs", "reasonlist", "queryconfig", "getarchclassmt",
    "unit", "dinnertypelist", "servewaylist", "dailybuffetlist", "getstoresaid",
    "getticketusedata", "queryticket", "itemmethodlist", "itemandgrouprelation",
    "ordercustomclasslist", "ordercustomitemlist", "itemplanlist",
})

#: 已知会改业务状态的接口，被拦截时用来给出可读原因。
KNOWN_WRITE_ENDPOINTS: dict[str, str] = {
    "posserial": "结班（实测确认，调用后门店所有设备无法登录）",
    "settle": "结账", "cancelsettle": "取消结账", "presettle": "预结",
    "fastenforcesettle": "强制结账", "openbill": "开台",
    "checkandopenclass": "开班", "bindselectpos": "绑定 POS", "unbindpos": "解绑 POS",
    "addorder": "加菜", "cancelorder": "撤单", "wipezero": "抹零",
}


class YintaiUnsafeCallError(RuntimeError):
    """拦截白名单外的接口调用，避免误触发门店写操作。"""


class YintaiAuthError(YintaiError):
    """三件套（accesskey / accesstoken / devid）失效，需要用 App 重新抓一组。"""


@dataclass(frozen=True)
class PointState:
    """单张桌台的实时状态。

    ``state`` 是服务端原样返回的 ``pointState``；是否占用以 ``is_occupied``
    为准（服务端对「已开台」的桌会带 ``openTime`` 或 ``bsId``，空闲桌两者皆无）。
    """

    point_id: str
    point_name: str
    point_code: str
    area_id: str
    state: int
    open_time: Optional[str] = None
    people_qty: Optional[int] = None
    last_total: Optional[float] = None
    bill_id: Optional[str] = None
    serving_progress: str = ""
    is_vip: bool = False
    capacity: int = 0
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_occupied(self) -> bool:
        return bool(self.open_time or self.bill_id)


@dataclass(frozen=True)
class YintaiCredentials:
    """取数所需的三件套 + 门店服务器地址。"""

    base_url: str
    dev_id: str
    access_key: str
    access_token: str
    dev_code: str = ""
    user_id: Optional[str] = None

    @classmethod
    def from_bundle(cls, bundle: Any) -> "YintaiCredentials":
        """从 ``CredentialBundle.extra`` 读取（缺字段时抛 ``YintaiAuthError``）。"""
        extra = getattr(bundle, "extra", None) or {}
        section = extra.get("yintai") or {}
        missing = [
            key
            for key in ("base_url", "dev_id", "access_key", "access_token")
            if not section.get(key)
        ]
        if missing:
            raise YintaiAuthError(
                "缺少移动银台取数参数：" + ", ".join(missing)
                + "（需要先用 App 抓一组 accesskey/accesstoken/devid 写入凭据 extra.yintai）"
            )
        user_id = section.get("user_id")
        return cls(
            base_url=str(section["base_url"]).rstrip("/"),
            dev_id=str(section["dev_id"]),
            access_key=str(section["access_key"]),
            access_token=str(section["access_token"]),
            dev_code=str(section.get("dev_code") or ""),
            user_id=str(user_id) if user_id else None,
        )


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_point_states(payload: Mapping[str, Any]) -> list[PointState]:
    """把 ``querypointlist`` 的响应体解析成桌台状态列表。"""
    data = payload.get("data") or {}
    rows = data.get("pointList") or []
    states: list[PointState] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        states.append(
            PointState(
                point_id=str(row.get("pointId") or ""),
                point_name=str(row.get("pointName") or ""),
                point_code=str(row.get("pointCode") or ""),
                area_id=str(row.get("areaId") or ""),
                state=_to_int(row.get("pointState")) or 0,
                open_time=row.get("openTime"),
                people_qty=_to_int(row.get("peopleqty")),
                last_total=_to_float(row.get("lastTotal")),
                bill_id=row.get("bsId"),
                serving_progress=str(row.get("progressOfServingDishes") or ""),
                is_vip=bool(row.get("isVIP")),
                capacity=_to_int(row.get("defCapacity")) or 0,
                raw=row,
            )
        )
    return states


class YintaiClient:
    """移动银台取数客户端（纯 HTTP，无浏览器）。"""

    def __init__(
        self,
        credentials: YintaiCredentials,
        *,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 20.0,
    ):
        self._creds = credentials
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    # ------------------------------------------------------------------ helpers
    def _headers(self, *, with_token: bool = True) -> dict[str, str]:
        # producttype / requesttype / cy_locale 是服务端定位「加密信息」上下文的必要条件，
        # 缺任意一个都会报「未找到加密信息」（见 docs/pos/YINTAI_API.md）。
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": DART_UA,
            "producttype": "2",
            "requesttype": "0",
            "cy_locale": "zh_CN",
            "contenttype": "application/x-www-form-urlencoded",
            "devid": self._creds.dev_id,
        }
        if with_token:
            headers["accesskey"] = self._creds.access_key
            if self._creds.access_token:
                headers["accesstoken"] = self._creds.access_token
        return headers

    def _device_payload(self, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "devId": self._creds.dev_id,
            "devCode": self._creds.dev_code,
            "devType": DEV_TYPE,
            "versionFlag": 2,
            "isCashierPos": 0,
        }
        payload.update(extra)
        return payload

    async def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        with_token: bool = True,
        allow_unsafe: bool = False,
    ) -> dict[str, Any]:
        endpoint = path.rstrip("/").rsplit("/", 1)[-1]
        if not allow_unsafe and endpoint not in READ_ONLY_ENDPOINTS:
            reason = KNOWN_WRITE_ENDPOINTS.get(endpoint, "未登记为只读接口")
            raise YintaiUnsafeCallError(
                f"拒绝调用 {endpoint}：{reason}。"
                "如确认安全，需先把它加入 READ_ONLY_ENDPOINTS 或显式传 allow_unsafe=True。"
            )
        url = f"{self._creds.base_url}{path}"
        try:
            resp = await self._client.post(url, json=dict(payload), headers=self._headers(with_token=with_token))
        except httpx.HTTPError as exc:
            raise YintaiError(f"请求 {path} 失败：{exc}") from exc

        if resp.status_code != 200:
            raise YintaiError(f"请求 {path} 返回 HTTP {resp.status_code}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise YintaiError(f"请求 {path} 返回非 JSON：{resp.text[:120]}") from exc

        if body.get("result") != 1:
            message = str(body.get("msg") or body.get("message") or "")
            if any(hint in message for hint in AUTH_ERROR_HINTS):
                raise YintaiAuthError(
                    f"{path} 鉴权失败：{message}"
                    "（accesskey/accesstoken/devid 已作废，需要用 App 重新抓一组）"
                )
            raise YintaiError(f"{path} 业务失败：{message or body}")
        return body

    # --------------------------------------------------------------------- api
    async def regdev(self) -> dict[str, Any]:
        """设备注册 / 连通性检查；返回 ``devCode`` 与 ``extranetDomain``。"""
        body = await self._post(
            f"{DEVICE_PATH}/regdev",
            {
                "appVersion": APP_VERSION,
                "devId": self._creds.dev_id,
                "devType": DEV_TYPE,
                "supportStandardFlg": 1,
                "industryFlg": 1,
                "versionFlag": 2,
                "isCashierPos": 0,
            },
            with_token=False,
        )
        data = body.get("data") or {}
        logger.info("移动银台设备注册成功 devCode=%s shop=%s", data.get("devCode"), data.get("shopName"))
        return data

    async def refresh_access_token(self) -> str:
        """换取 accesstoken（JWT）。"""
        body = await self._post(f"{TOKEN_PATH}", {"appId": APP_ID})
        token = (body.get("data") or {}).get("accessToken")
        if not token:
            raise YintaiAuthError("getaccesstokenforinside 未返回 accessToken")
        return str(token)

    async def fetch_point_states(self) -> list[PointState]:
        """取实时桌台状态（scraper 的主数据源）。"""
        payload = self._device_payload(
            userId=_to_int(self._creds.user_id) if self._creds.user_id else None,
            params={
                "withTransferPoint": True,
                "dddVirtualPointFlg": False,
                "pointListSeparateQuery": "1",
                "selectParamList": ["reservePointMap"],
                "showCustomerFeedbackFlg": 0,
            },
        )
        if payload.get("userId") is None:
            payload.pop("userId", None)
        body = await self._post(f"{DEVICE_PATH}/querypointlist", payload)
        states = parse_point_states(body)
        occupied = sum(1 for s in states if s.is_occupied)
        logger.info("移动银台桌台 %s 张（占用 %s）", len(states), occupied)
        return states

    async def fetch_bill_items(self, point_id: str, bill_id: str) -> list[dict[str, Any]]:
        """取单桌账单的菜品明细（对应 App 的「点菜明细」页）。

        返回原始 ``itemList``；字段转换由 ``YintaiSession`` 负责，这里保持数据面纯净。
        """
        # 服务端对 pointId 做 Long 强转（java.lang.String cannot be cast to java.lang.Long），
        # 必须传数字；bsId 同理。
        payload = self._device_payload(
            userId=_to_int(self._creds.user_id) if self._creds.user_id else None,
            params={
                "pointId": _to_int(point_id),
                "bsId": _to_int(bill_id),
                "showCustomerFeedbackFlg": 0,
            },
        )
        if payload.get("userId") is None:
            payload.pop("userId", None)
        body = await self._post(f"{BILL_PATH}/mergebillview", payload)
        rows = (body.get("data") or {}).get("itemList") or []
        logger.info("移动银台账单 %s 明细 %s 行", bill_id, len(rows))
        return [r for r in rows if isinstance(r, dict)]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
