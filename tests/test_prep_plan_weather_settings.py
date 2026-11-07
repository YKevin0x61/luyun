#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货复盘天气 API 设置（票 11）。

盯住三件事：

- **GET 永远不回明文 Key**，脱敏固定为 `abcd****wxyz`；未配置时各项空串。
- **保存的语义**：空 Key 保留原值；改 Key / 城市 / Host 才清 `location_id`；
  未知字段原样保留；不清空别人的键。
- **连通测试不写库、不出网**：全部走注入的假客户端；失败用 200 + 人话原因，
  且错误信息与日志里都不出现明文 Key。

`_weather_urls` 的 Host 拼接规则与老域名回落也在这一份里钉死。
"""

import asyncio
import logging
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.prep_plan import manager_router, reader_router, router
from api.security import require_any_identity_session, verify_admin_token
from database import DatabaseManager, get_db
from services.prep_plan_context import (
    QWEATHER_DAILY_URL,
    QWEATHER_GEO_URL,
    WEATHER_SETTINGS_KEY,
    PrepPlanContextService,
    _mask_api_key,
    _weather_urls,
    prep_plan_context,
)

PLAIN_KEY = "abcdEFGH1234wxyz"
MASKED_KEY = "abcd****wxyz"
CITY = "广州"
HOST = "https://abc123.re.qweatherapi.com"
STAMP = "2026-11-01T10:00:00+08:00"
WEATHER = {
    "text": "晴",
    "temp_max": 28.0,
    "temp_min": 21.0,
    "precip": 0.0,
}


class FakeWeatherClient:
    """记录调用与 URL；可注入异常；绝不出网。"""

    def __init__(self, *, location_id="101280101", daily=WEATHER, error=None):
        self.location_id = location_id
        self.daily_result = dict(daily) if daily is not None else None
        self.error = error
        self.lookups = []
        self.daily_calls = []

    async def lookup_location(self, city, api_key, url=QWEATHER_GEO_URL):
        self.lookups.append({"city": city, "api_key": api_key, "url": url})
        if self.error is not None:
            raise self.error
        return self.location_id

    async def daily(self, location_id, api_key, day, url=QWEATHER_DAILY_URL):
        self.daily_calls.append(
            {"location_id": location_id, "api_key": api_key, "day": day, "url": url}
        )
        if self.error is not None:
            raise self.error
        return dict(self.daily_result) if self.daily_result is not None else None


def _run(coro):
    return asyncio.run(coro)


def _build_app(db, identity):
    app = FastAPI()
    app.include_router(reader_router)
    app.include_router(manager_router)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_any_identity_session] = lambda: identity
    app.dependency_overrides[verify_admin_token] = lambda: True
    return app


@pytest.fixture
def fake(monkeypatch):
    client = FakeWeatherClient()
    monkeypatch.setattr(prep_plan_context, "_weather_client", client)
    return client


@pytest.fixture
def admin_client():
    db = DatabaseManager()
    _run(db.connect())
    with TestClient(_build_app(db, {"kind": "admin"})) as client:
        yield client, db
    _run(db.close())


@pytest.fixture
def staff_client():
    db = DatabaseManager()
    _run(db.connect())
    identity = {"kind": "staff", "employee": {"id": 7, "name": "阿明"}}
    with TestClient(_build_app(db, identity)) as client:
        yield client, db
    _run(db.close())


def _seed(db, **overrides):
    config = {
        "api_key": PLAIN_KEY,
        "city": CITY,
        "api_host": HOST,
        "location_id": "101280101",
        "updated_at": STAMP,
        "future_field": {"keep": True},
    }
    config.update(overrides)
    _run(db.settings_set_json(WEATHER_SETTINGS_KEY, config))
    return config


def _stored(db):
    return _run(db.settings_get_json(WEATHER_SETTINGS_KEY, {}))


# ------------------------------------------------------------------ 服务层


def test_weather_urls_fall_back_to_the_old_domains():
    assert _weather_urls({}) == (QWEATHER_GEO_URL, QWEATHER_DAILY_URL)
    assert _weather_urls({"api_host": ""}) == (QWEATHER_GEO_URL, QWEATHER_DAILY_URL)


def test_weather_urls_use_only_the_configured_host():
    geo, daily = _weather_urls({"api_host": HOST + "/"})
    assert geo == f"{HOST}/geo/v2/city/lookup"
    assert daily == f"{HOST}/v7/weather/3d"


def test_read_never_returns_the_plain_key(admin_client):
    client, db = admin_client
    _seed(db)

    resp = client.get("/api/prep-plan/context/weather")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "configured": True,
        "api_key_masked": MASKED_KEY,
        "city": CITY,
        "api_host": HOST,
        "updated_at": STAMP,
    }
    assert PLAIN_KEY not in resp.text
    assert "future_field" not in body


def test_read_when_unconfigured_is_all_empty(admin_client):
    client, _db = admin_client

    body = client.get("/api/prep-plan/context/weather").json()

    assert body == {
        "configured": False,
        "api_key_masked": "",
        "city": "",
        "api_host": "",
        "updated_at": "",
    }


def test_save_keeps_the_existing_key_when_blank(admin_client, fake):
    client, db = admin_client
    _seed(db)

    resp = client.put(
        "/api/prep-plan/context/weather",
        json={"api_key": "", "city": CITY, "api_host": HOST},
    )

    assert resp.status_code == 200
    assert resp.json()["api_key_masked"] == MASKED_KEY
    assert PLAIN_KEY not in resp.text
    assert _stored(db)["api_key"] == PLAIN_KEY


def test_save_keeps_the_existing_key_when_the_field_is_omitted(admin_client, fake):
    client, db = admin_client
    _seed(db)

    resp = client.put(
        "/api/prep-plan/context/weather",
        json={"city": CITY, "api_host": HOST},
    )

    assert resp.status_code == 200
    assert _stored(db)["api_key"] == PLAIN_KEY


def test_save_clears_location_id_and_preserves_unknown_fields(admin_client, fake):
    client, db = admin_client
    _seed(db)

    new_key = "wxyz4321HGFEabcd"
    resp = client.put(
        "/api/prep-plan/context/weather",
        json={
            "api_key": new_key,
            "city": "深圳",
            "api_host": "https://new456.re.qweatherapi.com",
        },
    )

    assert resp.status_code == 200
    stored = _stored(db)
    assert stored["api_key"] == new_key
    assert stored["city"] == "深圳"
    assert stored["api_host"] == "https://new456.re.qweatherapi.com"
    assert stored["location_id"] == ""
    assert stored["future_field"] == {"keep": True}
    assert stored["updated_at"]
    assert new_key not in resp.text


def test_save_unchanged_values_keep_location_id(admin_client, fake):
    client, db = admin_client
    _seed(db)

    resp = client.put(
        "/api/prep-plan/context/weather",
        json={"city": CITY, "api_host": HOST},
    )

    assert resp.status_code == 200
    assert _stored(db)["location_id"] == "101280101"


def test_save_with_empty_host_clears_it_and_location_id(admin_client, fake):
    client, db = admin_client
    _seed(db)

    resp = client.put(
        "/api/prep-plan/context/weather",
        json={"city": CITY, "api_host": ""},
    )

    assert resp.status_code == 200
    assert resp.json()["api_host"] == ""
    stored = _stored(db)
    assert stored["api_host"] == ""
    assert stored["location_id"] == ""


def test_save_requires_a_city(admin_client, fake):
    client, _db = admin_client

    resp = client.put(
        "/api/prep-plan/context/weather",
        json={"city": "   ", "api_host": HOST},
    )

    assert resp.status_code == 400
    assert "城市" in resp.json()["detail"]


@pytest.mark.parametrize(
    "bad_host",
    [
        "http://abc123.re.qweatherapi.com",
        "ftp://abc123.re.qweatherapi.com",
        "abc123.re.qweatherapi.com",
        "https://",
        "https:///no-host",
    ],
)
def test_save_rejects_a_bad_host(admin_client, fake, bad_host):
    client, _db = admin_client

    resp = client.put(
        "/api/prep-plan/context/weather",
        json={"city": CITY, "api_host": bad_host},
    )

    assert resp.status_code == 400


def test_save_normalizes_a_trailing_slash(admin_client, fake):
    client, db = admin_client

    resp = client.put(
        "/api/prep-plan/context/weather",
        json={"city": CITY, "api_host": HOST + "/"},
    )

    assert resp.status_code == 200
    assert resp.json()["api_host"] == HOST
    assert _stored(db)["api_host"] == HOST


def test_test_uses_the_saved_key_and_configured_hosts(admin_client, fake):
    client, db = admin_client
    _seed(db)

    resp = client.post(
        "/api/prep-plan/context/weather/test",
        json={"city": CITY, "api_host": HOST},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["location_id"] == "101280101"
    assert set(body["sample"]) == {"fxDate", "textDay", "tempMin", "tempMax"}
    assert body["sample"]["textDay"] == "晴"
    assert fake.lookups[0]["city"] == CITY
    assert fake.lookups[0]["api_key"] == PLAIN_KEY
    assert fake.lookups[0]["url"] == f"{HOST}/geo/v2/city/lookup"
    assert fake.daily_calls[0]["url"] == f"{HOST}/v7/weather/3d"
    assert _stored(db)["location_id"] == "101280101"


def test_test_prefers_the_typed_key(admin_client, fake):
    client, _db = admin_client
    _seed(_db)

    resp = client.post(
        "/api/prep-plan/context/weather/test",
        json={"api_key": "typed-key-9876", "city": CITY, "api_host": HOST},
    )

    assert resp.status_code == 200
    assert fake.lookups[0]["api_key"] == "typed-key-9876"


def test_test_does_not_write_the_database(admin_client, fake):
    client, db = admin_client

    resp = client.post(
        "/api/prep-plan/context/weather/test",
        json={"api_key": PLAIN_KEY, "city": CITY, "api_host": HOST},
    )

    assert resp.status_code == 200
    assert _run(db.settings_get_json(WEATHER_SETTINGS_KEY, None)) is None


def test_test_unknown_city_is_a_human_error(admin_client, fake):
    client, _db = admin_client
    fake.location_id = None

    resp = client.post(
        "/api/prep-plan/context/weather/test",
        json={"api_key": PLAIN_KEY, "city": "不存在市", "api_host": HOST},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "城市" in body["error"]


def test_test_missing_daily_data_is_a_human_error(admin_client, fake):
    client, _db = admin_client
    fake.daily_result = None

    resp = client.post(
        "/api/prep-plan/context/weather/test",
        json={"api_key": PLAIN_KEY, "city": CITY, "api_host": HOST},
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["error"]


def test_test_invalid_key_is_200_and_never_leaks_the_key(admin_client, fake, caplog):
    client, _db = admin_client
    request = httpx.Request("GET", f"https://geo.example/v2/city/lookup?key={PLAIN_KEY}")
    response = httpx.Response(401, request=request)
    fake.error = httpx.HTTPStatusError(
        f"401 Unauthorized for {request.url}", request=request, response=response
    )

    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            "/api/prep-plan/context/weather/test",
            json={"api_key": PLAIN_KEY, "city": CITY, "api_host": HOST},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "Key" in body["error"]
    assert PLAIN_KEY not in resp.text
    assert PLAIN_KEY not in caplog.text


def test_test_network_failure_is_a_human_error(admin_client, fake, caplog):
    client, _db = admin_client
    fake.error = httpx.ConnectError(f"boom {PLAIN_KEY}")

    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            "/api/prep-plan/context/weather/test",
            json={"api_key": PLAIN_KEY, "city": CITY, "api_host": HOST},
        )

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert PLAIN_KEY not in resp.text
    assert PLAIN_KEY not in caplog.text


def test_test_rejects_a_bad_host(admin_client, fake):
    client, _db = admin_client

    resp = client.post(
        "/api/prep-plan/context/weather/test",
        json={"api_key": PLAIN_KEY, "city": CITY, "api_host": "http://x.example"},
    )

    assert resp.status_code == 400


def test_staff_cannot_read_write_or_test_weather(staff_client, fake):
    client, _db = staff_client

    assert client.get("/api/prep-plan/context/weather").status_code == 403
    assert (
        client.put(
            "/api/prep-plan/context/weather",
            json={"city": CITY, "api_host": HOST},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/prep-plan/context/weather/test",
            json={"city": CITY, "api_host": HOST},
        ).status_code
        == 403
    )


def test_weather_collection_uses_the_configured_host(admin_client, fake):
    client, db = admin_client
    _seed(db)
    service = PrepPlanContextService(weather_client=fake)

    result = _run(service._fetch_weather(db, "2026-11-02", datetime.now()))

    assert fake.daily_calls[0]["url"] == f"{HOST}/v7/weather/3d"
    assert result["text"] == "晴"


def test_mask_is_stable_for_short_and_empty_keys():
    assert _mask_api_key("") == ""
    assert _mask_api_key("short") == "****"
    assert _mask_api_key(PLAIN_KEY) == MASKED_KEY
