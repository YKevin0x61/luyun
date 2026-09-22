#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爬虫控制面鉴权（SEC-01）。

``POST /api/scraper/start`` / ``POST /api/scraper/stop`` 是控制面写接口：以前依赖树
为空，门店局域网里任何未登录设备（KDS 平板、访客手机）乃至一张跨站表单都能停掉
采集。这里钉住「必须带管理员凭据」以及「只读的 status 仍然开放」两条契约。
"""

import pytest
from fastapi.testclient import TestClient

from config import settings


@pytest.fixture
def scraper_client(tmp_path):
    """真实 ``main.app``（含中间件与依赖树）+ 一个走公开登录接口取得的管理员 Token。"""
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    import main as main_module

    with TestClient(main_module.app) as client:
        init = client.post(
            "/api/auth/init",
            json={
                "username": "admin",
                "password": "password123",
                "confirm_password": "password123",
            },
        )
        assert init.status_code == 200, init.text
        login = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "password123",
                "remember": False,
                "issue_api_token": True,
            },
        )
        assert login.status_code == 200, login.text
        token = login.json()["api_token"]
        # 之后的请求代表「没有浏览器会话」的脚本 / KDS，别让登录 cookie 兜着。
        client.cookies.clear()
        yield client, {"X-Admin-Token": token}
    settings.DATABASE_DIR = old


def test_stop_scraper_without_credentials_is_rejected(scraper_client):
    client, _admin_headers = scraper_client
    resp = client.post("/api/scraper/stop")
    assert resp.status_code == 401, resp.text


def test_stop_scraper_with_api_token_still_works(scraper_client):
    """加鉴权不能把运维脚本 / KDS 的 ``X-Admin-Token`` 链路一起打死。"""
    client, admin_headers = scraper_client
    resp = client.post("/api/scraper/stop", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"message": "爬虫任务未在运行"}


def test_start_scraper_without_credentials_is_rejected(scraper_client):
    """跨站表单也能打到这个接口：无凭据（哪怕带跨站头）必须被拒，且不会真拉起采集。"""
    client, _admin_headers = scraper_client
    resp = client.post(
        "/api/scraper/start",
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 401, resp.text


def test_status_stays_open_for_ops_probes(scraper_client, monkeypatch):
    """只读的 status 保持开放（票面方案 A）：运维探针 / KDS 不需要凭据。"""
    client, _admin_headers = scraper_client
    import main as main_module

    # 与「别的用例是否启动过爬虫」解耦：这里只关心状态接口本身开放且返回真实状态。
    monkeypatch.setattr(main_module, "scraper_task", None)
    resp = client.get("/api/scraper/status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "not_started"
    assert body["business_hours"]["work_start"] == "07:30"
    assert body["business_hours"]["work_end"] == "21:30"


def test_bogus_token_is_rejected(scraper_client):
    """带头不等于有凭据：伪造的 ``X-Admin-Token`` 仍是 401（别退化成「有头就放行」）。"""
    client, _admin_headers = scraper_client
    resp = client.post("/api/scraper/stop", headers={"X-Admin-Token": "bogus-token"})
    assert resp.status_code == 401, resp.text


class _FakeScraperTask:
    """最小 fake 采集任务：只为观察 stop 有没有真的去 ``cancel()`` 采集循环。"""

    def __init__(self):
        self.cancel_calls = 0

    def done(self):
        return False

    def cancel(self):
        self.cancel_calls += 1

    def __await__(self):
        if False:  # 让它成为生成器函数，await 时直接返回
            yield
        return None


def test_stop_without_credentials_never_cancels_the_running_scraper(
    scraper_client, monkeypatch
):
    """票面的真实危害不是状态码，而是无凭据的一次 POST 就能把采集循环打停。"""
    client, admin_headers = scraper_client
    import main as main_module

    fake = _FakeScraperTask()
    monkeypatch.setattr(main_module, "scraper_task", fake)

    anonymous = client.post("/api/scraper/stop")
    assert anonymous.status_code == 401, anonymous.text
    assert fake.cancel_calls == 0, "无凭据请求不该碰到采集任务"

    authorized = client.post("/api/scraper/stop", headers=admin_headers)
    assert authorized.status_code == 200, authorized.text
    assert authorized.json() == {"message": "餐厅数据爬取已停止"}
    assert fake.cancel_calls == 1, "有凭据时 stop 的原有行为必须保持不变"
