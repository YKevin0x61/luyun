#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""健康证官方查询（服务层纯函数 + 路由）。

覆盖：
1. 网址解析：正常地址 / 换域名 / 缺机构编号 / 非 http，机构编号与接口前缀从地址推；
2. 日期归一：带时间、斜杠、中文、非法值；到期日反推办理日，2 月 29 日反推不唯一；
3. 电子证链接的 md5 片段与平台前端算法一致；
4. 查询链路（httpx.MockTransport，不联网）：成功、查无数据、上游 success=false、
   HTTP 500、网络错误、缺姓名；
5. 路由：默认网址可读、坏网址保存被拒、保存新网址后查询确实用它。
"""

import asyncio
import json
from datetime import date

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.health_cert as health_cert_api
from api.security import require_session
from database import DatabaseManager, get_db
from services import health_cert_lookup as lookup

URL = "https://yun.beihuyun.cn/app/260202/his/health-cert/cert-query"
TODAY = date(2026, 9, 13)


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# 1. 网址解析
# ---------------------------------------------------------------------------
def test_parse_lookup_url_derives_api_and_org():
    target = lookup.parse_lookup_url(URL)
    assert target.org_id == "260202"
    assert target.origin == "https://yun.beihuyun.cn"
    assert target.api_url == (
        "https://yun.beihuyun.cn/appService/His/HealthCert/UserCert/GetUserHealthCerts"
    )


def test_parse_lookup_url_follows_new_domain_and_org():
    """平台换链接：域名与机构编号一起换，两个都要跟着走。"""
    target = lookup.parse_lookup_url(
        "https://new.example.com/app/880088/his/health-cert/cert-query?x=1"
    )
    assert target.org_id == "880088"
    assert target.api_url.startswith("https://new.example.com/appService/")


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "yun.beihuyun.cn/app/260202/x", "ftp://a.com/app/1/x", "https://yun.example.com/"],
)
def test_parse_lookup_url_rejects_bad_shapes(raw):
    with pytest.raises(lookup.HealthCertLookupError):
        lookup.parse_lookup_url(raw)


# ---------------------------------------------------------------------------
# 2. 日期
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2027-01-02", "2027-01-02"),
        ("2027-01-02 00:00:00", "2027-01-02"),
        ("2027/1/2", "2027-01-02"),
        ("2027.01.02", "2027-01-02"),
        ("2027年1月2日", "2027-01-02"),
        ("", None),
        (None, None),
        ("abc", None),
        ("2027-02-30", None),
    ],
)
def test_normalize_upstream_date(raw, expected):
    assert lookup.normalize_upstream_date(raw) == expected


def test_expiry_maps_back_to_issue_date():
    assert lookup.health_cert_date_for_expiry("2027-01-02") == "2026-01-02"


def test_expiry_without_unique_issue_date_stays_empty():
    """2 月 29 日到期：减 12 个月再加回来会变成 2 月 28 日，不能写。"""
    assert lookup.health_cert_date_for_expiry("2028-02-29") is None


# ---------------------------------------------------------------------------
# 3. 电子证链接
# ---------------------------------------------------------------------------
def test_cert_url_uses_platform_md5_slice():
    target = lookup.parse_lookup_url(URL)
    url = lookup.build_cert_url(target, "123456", "110101199001011234")
    assert url == (
        "https://yun.beihuyun.cn/app/260202/his/health-cert/cert/123456/681376d4a53c6291"
    )


def test_cert_url_absent_without_cert_id():
    target = lookup.parse_lookup_url(URL)
    assert lookup.build_cert_url(target, "", "110101199001011234") is None


# ---------------------------------------------------------------------------
# 4. 查询链路（MockTransport）
# ---------------------------------------------------------------------------
def _lookup_with(handler, **kwargs):
    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await lookup.lookup_health_certs(
                kwargs.pop("name", "张三"),
                kwargs.pop("id_card", "110101199001011234"),
                kwargs.pop("url", URL),
                today=TODAY,
                client=client,
                **kwargs,
            )

    return _run(run())


def _ok_body(rows):
    return {
        "success": True,
        "code": 200,
        "message": None,
        "result": {"total": len(rows), "pageSize": 20, "pageIndex": 1, "data": rows},
    }


def test_lookup_success_shape_and_suggestion():
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/appService/His/HealthCert/UserCert/GetUserHealthCerts"
        payload = json.loads(request.content)
        assert payload["dataFilter"] == {
            "name": "张三",
            "idcard": "110101199001011234",
            "orgId": "260202",
        }
        return httpx.Response(
            200,
            json=_ok_body(
                [
                    {
                        "certId": "C-1",
                        "name": "张三",
                        "idCard": "110101199001011234",
                        "gender": "1",
                        "examDate": "2026-01-02",
                        "expDate": "2027-01-02 00:00:00",
                        "examUnit": "某某医院",
                        "workplace": "后厨",
                        "regNo": "R-9",
                    }
                ]
            ),
        )

    result = _lookup_with(handler)
    assert result["found"] is True
    assert result["org_id"] == "260202"
    item = result["items"][0]
    assert item["exam_date"] == "2026-01-02"
    assert item["expires_on"] == "2027-01-02"
    assert item["suggested_cert_date"] == "2026-01-02"
    assert item["suggested_source"] == "expires_on"
    assert item["gender"] == "男"
    assert item["cert_url"].startswith("https://yun.beihuyun.cn/app/260202/his/health-cert/cert/C-1/")
    # 今天是 2026-09-13：到期日 2027-01-02，还剩 111 天，状态 ok。
    assert item["state"] == "ok"
    assert item["days_left"] == 111


def test_lookup_expiry_falls_back_to_exam_date():
    """到期日缺失或反推不唯一时，退回用体检日期当办理日，并标明来源。"""

    def handler(request):
        return httpx.Response(
            200,
            json=_ok_body(
                [{"certId": "C-2", "idCard": "110101199001011234", "examDate": "2026-01-02"}]
            ),
        )

    item = _lookup_with(handler)["items"][0]
    assert item["suggested_cert_date"] == "2026-01-02"
    assert item["suggested_source"] == "exam_date"
    assert item["expires_on"] is None


def test_lookup_empty_result_is_not_an_error():
    def handler(request):
        return httpx.Response(200, json=_ok_body([]))

    result = _lookup_with(handler)
    assert result["found"] is False
    assert result["items"] == []


def test_lookup_skips_soft_deleted_rows():
    def handler(request):
        return httpx.Response(
            200,
            json=_ok_body(
                [
                    {"certId": "X", "isDelete": True, "idCard": "110101199001011234"},
                    {"certId": "Y", "idCard": "110101199001011234", "examDate": "2026-01-02"},
                ]
            ),
        )

    items = _lookup_with(handler)["items"]
    assert [item["cert_id"] for item in items] == ["Y"]


def test_lookup_upstream_failure_raises_502():
    def handler(request):
        return httpx.Response(200, json={"success": False, "message": "参数错误"})

    with pytest.raises(lookup.HealthCertLookupError) as caught:
        _lookup_with(handler)
    assert caught.value.code == "upstream_error"
    assert caught.value.status_code == 502
    assert "参数错误" in caught.value.message


def test_lookup_http_error_raises_502():
    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(lookup.HealthCertLookupError) as caught:
        _lookup_with(handler)
    assert caught.value.code == "upstream_status"


def test_lookup_network_error_raises_502():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(lookup.HealthCertLookupError) as caught:
        _lookup_with(handler)
    assert caught.value.code == "upstream_unreachable"
    assert caught.value.status_code == 502


def test_lookup_requires_name():
    with pytest.raises(lookup.HealthCertLookupError) as caught:
        _lookup_with(lambda request: httpx.Response(200), name=" ")
    assert caught.value.code == "name_required"


# ---------------------------------------------------------------------------
# 5. 路由
# ---------------------------------------------------------------------------
@pytest.fixture
def health_cert_http():
    db = DatabaseManager()
    _run(db.connect())
    app = FastAPI()
    app.include_router(health_cert_api.router)
    app.dependency_overrides[require_session] = lambda: "test-session"
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client, db
    _run(db.close())


def _reset_url(db, url=URL):
    _run(db.settings_set_json(lookup.SETTINGS_KEY, {"url": url}))


def test_settings_endpoint_reports_derived_target(health_cert_http):
    client, db = health_cert_http
    _reset_url(db)
    body = client.get("/api/health-cert/settings").json()
    assert body["url"] == URL
    assert body["org_id"] == "260202"
    assert body["error"] == ""


def test_settings_rejects_bad_url(health_cert_http):
    client, _db = health_cert_http
    resp = client.put("/api/health-cert/settings", json={"url": "https://yun.example.com/"})
    assert resp.status_code == 400
    assert "机构编号" in resp.json()["detail"]


def test_lookup_uses_saved_url(health_cert_http, monkeypatch):
    client, db = health_cert_http
    new_url = "https://new.example.com/app/880088/his/health-cert/cert-query"
    assert client.put("/api/health-cert/settings", json={"url": new_url}).status_code == 200
    seen = {}

    async def fake_lookup(name, id_card, url, **_kwargs):
        seen["url"] = url
        return {
            "name": name,
            "id_card": id_card,
            "org_id": "880088",
            "page_url": url,
            "found": True,
            "items": [{"cert_id": "C-1"}],
        }

    monkeypatch.setattr(health_cert_api, "lookup_health_certs", fake_lookup)
    resp = client.post(
        "/api/health-cert/lookup", json={"name": "张三", "id_card": "110101199001011234"}
    )
    assert resp.status_code == 200, resp.text
    assert seen["url"] == new_url
    assert resp.json()["found"] is True
    _reset_url(db)


def test_lookup_surfaces_service_error(health_cert_http, monkeypatch):
    client, _db = health_cert_http

    async def failing(name, id_card, url, **_kwargs):
        raise lookup.HealthCertLookupError("upstream_unreachable", "连不上健康证查询平台", 502)

    monkeypatch.setattr(health_cert_api, "lookup_health_certs", failing)
    resp = client.post(
        "/api/health-cert/lookup", json={"name": "张三", "id_card": "110101199001011234"}
    )
    assert resp.status_code == 502
    assert resp.json()["detail"] == "连不上健康证查询平台"
