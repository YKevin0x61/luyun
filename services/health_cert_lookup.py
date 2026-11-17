#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""健康证官方查询（北狐平台）：网址解析、外部请求、结果归一。

为什么要这层：花名册里的健康证状态是「办理日期 + 12 个月」派生的（ADR 0098 到期日
不落库），只反映**人工录入**的那一天。平台方给了一个公开查询页（姓名 + 身份证号），
本模块把它包成一次服务端 httpx 调用：店长点一下就能看到官方结果，并拿到「该把办理
日期填成哪天」的建议值。

三个约定：

* **网址可配置**：平台换链接时域名和 orgId 一起换，所以只存页面地址
  （app_settings.health_cert_lookup_url），接口地址与机构编号一律从它推；
* **不猜上游格式**：日期形状没有文档，做宽松归一（YYYY-MM-DD、带时间、斜杠、
  中文），认不出来回 None——宁可不给建议，也不瞎填一个日期进档案；
* **失败分三类**：没配网址 / 连不上 / 上游返回异常，各自带错误码与人话，路由层只
  负责转发，不再二次解释。

测试见 tests/test_health_cert_lookup.py（纯函数 + MockTransport，不联网）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit

import httpx

from db_core.utils import CHINA_TZ
from services.identity.profile import (
    HEALTH_CERT_VALID_MONTHS,
    add_months,
    health_cert_status,
)

# 页面地址默认值：门店目前用的那条（平台换了链接就在花名册的查证面板里改）。
DEFAULT_LOOKUP_URL = "https://yun.beihuyun.cn/app/260202/his/health-cert/cert-query"
# app_settings 里的键名。存 {"url": ...}，读的时候也兼容裸字符串（早期手工写库）。
SETTINGS_KEY = "health_cert_lookup_url"
# 接口路径固定，前缀 /appService 与域名、orgId 一起由页面地址推出来。
API_PATH = "/His/HealthCert/UserCert/GetUserHealthCerts"
REQUEST_TIMEOUT_SECONDS = 15.0
PAGE_SIZE = 20

# /app/{orgId}/...：机构编号是纯数字，平台换门店时会变。
_ORG_RE = re.compile(r"/app/(\d+)(?:/|$)")
# 宽松日期：2026-01-02 / 2026/1/2 / 2026.01.02 / 2026年1月2日 [+ 时间后缀]。
_DATE_RE = re.compile(
    r"^(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})"
)


class HealthCertLookupError(Exception):
    """带错误码与 HTTP 状态的一次查询失败（路由层直接转 HTTPException）。

    status_code 用 400 表示「调用方要改」（没配网址、没填姓名），502 表示
    「上游的事」（连不上、返回异常）——两类在页面上给的下一步动作不一样。
    """

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class LookupTarget:
    """一条页面地址拆出来的三个值。"""

    page_url: str
    origin: str
    api_url: str
    org_id: str


def parse_lookup_url(raw: Any) -> LookupTarget:
    """页面地址 → 域名 + 接口地址 + 机构编号。

    只认 http(s)://host/app/{数字}/... 这个形状：占位符、带空格的粘贴、少了
    /app/ 的首页地址都要在保存时就打回去，否则错误会拖到查询那一刻才暴露。
    """
    text = str(raw or "").strip()
    if not text:
        raise HealthCertLookupError("lookup_url_missing", "健康证查询网址还没设置")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HealthCertLookupError(
            "lookup_url_invalid", "查询网址要以 http:// 或 https:// 开头"
        )
    matched = _ORG_RE.search(parts.path)
    if not matched:
        raise HealthCertLookupError(
            "lookup_url_invalid",
            "查询网址里没找到机构编号，应形如 https://域名/app/260202/his/...",
        )
    origin = f"{parts.scheme}://{parts.netloc}"
    return LookupTarget(
        page_url=text,
        origin=origin,
        api_url=f"{origin}/appService{API_PATH}",
        org_id=matched.group(1),
    )


def normalize_upstream_date(value: Any) -> Optional[str]:
    """上游日期 → YYYY-MM-DD；认不出来回 None。

    expDate 常见形状是日期带时间（2027-01-02 00:00:00），只取年月日；
    斜杠与中文写法一并兼容，避免平台换个序列化格式就整页查不到。
    """
    text = str(value or "").strip()
    if not text:
        return None
    matched = _DATE_RE.match(text)
    if not matched:
        return None
    try:
        return date(
            int(matched.group(1)), int(matched.group(2)), int(matched.group(3))
        ).isoformat()
    except ValueError:
        return None


def health_cert_date_for_expiry(expires_on: Any) -> Optional[str]:
    """到期日 → 办理日期（−12 个月），反推不唯一时回 None。

    与员工端 PATCH /api/hygiene/staff/me/health-cert 同一条规则：2 月 29 日这类
    到期日在 −12 个月后加回来会变成 2 月 28 日，那就不能写，否则花名册显示的到期日
    会跟证上不一致。
    """
    value = normalize_upstream_date(expires_on)
    if not value:
        return None
    candidate = add_months(value, -HEALTH_CERT_VALID_MONTHS)
    if add_months(candidate, HEALTH_CERT_VALID_MONTHS) != value:
        return None
    return candidate


def build_cert_url(target: LookupTarget, cert_id: Any, id_card: Any) -> Optional[str]:
    """电子证详情链接：/app/{orgId}/his/health-cert/cert/{certId}/{md5片段}。

    路径里那段是 md5(certId + idCard) 的第 8–24 位（平台前端的算法），少了它
    详情页会拦。certId 为空就没链接可给。
    """
    cert_text = str(cert_id or "").strip()
    if not cert_text:
        return None
    digest = hashlib.md5(  # noqa: S324 — 平台约定的路径校验串，不是密码学用途
        f"{cert_text}{str(id_card or '').strip().upper()}".encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()
    return (
        f"{target.origin}/app/{quote(target.org_id)}/his/health-cert/cert/"
        f"{quote(cert_text)}/{digest[8:24]}"
    )


def _normalize_id_card(raw: Any) -> str:
    return str(raw or "").replace(" ", "").strip().upper()


def _gender_label(raw: Any) -> str:
    text = str(raw or "").strip()
    return {"1": "男", "2": "女"}.get(text, text)


def _cert_item(
    raw: Dict[str, Any], target: LookupTarget, today: date
) -> Optional[Dict[str, Any]]:
    """一条上游记录 → 页面直接能渲染的形状；软删的返回 None。"""
    if raw.get("isDelete") is True or str(raw.get("isDelete")).lower() == "true":
        return None
    id_card = _normalize_id_card(raw.get("idCard"))
    exam_date = normalize_upstream_date(raw.get("examDate"))
    expires_on = normalize_upstream_date(raw.get("expDate"))
    suggested = health_cert_date_for_expiry(expires_on) if expires_on else None
    suggested_source = "expires_on" if suggested else ""
    if suggested is None and exam_date:
        suggested = exam_date
        suggested_source = "exam_date"
    status = health_cert_status(suggested, today)
    return {
        "cert_id": str(raw.get("certId") or "").strip(),
        "name": str(raw.get("name") or "").strip(),
        "id_card": id_card,
        "gender": _gender_label(raw.get("gender")),
        "exam_date": exam_date,
        "expires_on": expires_on,
        "exam_unit": str(raw.get("examUnit") or "").strip(),
        "workplace": str(raw.get("workplace") or "").strip(),
        "reg_no": str(raw.get("regNo") or "").strip(),
        "cert_url": build_cert_url(target, raw.get("certId"), id_card),
        # 建议写进档案的办理日期 + 它是从哪个字段推出来的（页面据此说「按有效期反推」）。
        "suggested_cert_date": suggested,
        "suggested_source": suggested_source,
        # 这个证在本仓口径下的状态（与花名册同一个派生实现，不会两处不一致）。
        "state": status["state"],
        "days_left": status["days_left"],
    }


def _build_payload(name: str, id_card: str, org_id: str) -> Dict[str, Any]:
    """复刻页面发的分页对象：外层四个计数 + dataFilter。"""
    return {
        "total": 0,
        "pageSize": PAGE_SIZE,
        "pageIndex": 1,
        "pageCount": 0,
        "dataFilter": {"name": name, "idcard": id_card, "orgId": org_id},
    }


async def lookup_health_certs(
    name: Any,
    id_card: Any,
    lookup_url: Any,
    *,
    today: Optional[date] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """按姓名 + 身份证查官方健康证。

    返回 {"name", "id_card", "org_id", "page_url", "found", "items"}；
    found=False 时平台也分不清「没办证」和「姓名/身份证对不上」——文案由调用方
    决定，这里只如实报「查无数据」。

    client 只为测试注入（httpx.MockTransport）；生产走默认 AsyncClient。
    """
    name_text = str(name or "").strip()
    id_text = _normalize_id_card(id_card)
    if not name_text:
        raise HealthCertLookupError("name_required", "请填写姓名")
    if not id_text:
        raise HealthCertLookupError("id_card_required", "请填写身份证号")
    target = parse_lookup_url(lookup_url)
    day = today or datetime.now(CHINA_TZ).date()

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        try:
            response = await http_client.post(
                target.api_url,
                json=_build_payload(name_text, id_text, target.org_id),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise HealthCertLookupError(
                "upstream_unreachable",
                "连不上健康证查询平台，请检查网络后重试",
                502,
            ) from exc
        if response.status_code != 200:
            raise HealthCertLookupError(
                "upstream_status",
                f"查询平台返回 HTTP {response.status_code}，请稍后重试",
                502,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise HealthCertLookupError(
                "upstream_payload", "查询平台返回的不是 JSON，可能网址已失效", 502
            ) from exc
        if not isinstance(body, dict) or body.get("success") is not True:
            detail = ""
            if isinstance(body, dict):
                detail = str(body.get("message") or "").strip()
            raise HealthCertLookupError(
                "upstream_error",
                f"查询平台返回失败{('：' + detail) if detail else ''}",
                502,
            )
        rows = ((body.get("result") or {}).get("data")) or []
    finally:
        if owns_client:
            await http_client.aclose()

    items: List[Dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        item = _cert_item(raw, target, day)
        if item is not None:
            items.append(item)
    return {
        "name": name_text,
        "id_card": id_text,
        "org_id": target.org_id,
        "page_url": target.page_url,
        "found": bool(items),
        "items": items,
    }
