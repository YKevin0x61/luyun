#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送任务的新形状：内容类型 + 参数 + 时间，收件人来自订阅（票 08）。

缝隙（spec「Testing Decisions」第 3 条：内容类型注册表与 `/meta`，沿用 API 测试缝隙）：
整套跑在**真 `main.app`** 上（liferuntime 装配、会话校验真的查库），断言的是页面发出的
请求体与它拿到的形状：

* 任务不再有收件人字段 —— 写请求是新形状，任务卡片给的是「订阅目标数」；
* **旧形状（带 `webhook_id`）被明确拒绝，且一个字都不写** —— 静默忽略会让店长以为改了
  收件人其实没改；
* 参数按注册表校验（内容类型自带的 schema 是唯一入口）；
* 预览按内容类型渲染；「立即发送」按该内容类型的**全部**订阅目标投递，零订阅当场说清。

不测 SQL 形状，也不测内部函数调用顺序。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from config import settings
from services.wecom_push_service import wecom_push_service

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "password123"
WEBHOOK_URL = (
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
    "?key=693a91f6-7aoc-4bc4-97a0-0ec2sifa5aaa"
)
WEBHOOK_URL_B = (
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
    "?key=11111111-2222-3333-4444-555555555555"
)
WEBHOOK_URL_C = (
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
    "?key=99999999-8888-7777-6666-555555555555"
)
API_PREFIX = "/api/wecom-push"
AUTH_GATE_BODY = {"detail": "需要登录"}
# 旧 bundle（PWA 缓存）发来的请求体带 webhook_id：明确拒绝的文案。
STALE_PAGE_TEXT = "页面已更新，请刷新后重试"


class WecomAdmin:
    """真 app 客户端 + 两种凭据：浏览器会话 cookie 与 API token。"""

    def __init__(self, client: TestClient, session_id: str, token: str) -> None:
        self.client = client
        self.session_id = session_id
        self.token = token

    def _send(self, method: str, path: str, *, with_session: bool, **kwargs):
        self.client.cookies.clear()
        headers = dict(kwargs.pop("headers", None) or {})
        if with_session:
            self.client.cookies.set(settings.SESSION_COOKIE_NAME, self.session_id)
        else:
            headers["X-Admin-Token"] = self.token
        return self.client.request(method, path, headers=headers, **kwargs)

    def as_session(self, method: str, path: str, **kwargs):
        return self._send(method, path, with_session=True, **kwargs)

    def as_token(self, method: str, path: str, **kwargs):
        return self._send(method, path, with_session=False, **kwargs)


@pytest.fixture
def api(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    import main as main_module

    with TestClient(main_module.app) as client:
        init = client.post(
            "/api/auth/init",
            json={
                "username": ADMIN_USERNAME,
                "password": ADMIN_PASSWORD,
                "confirm_password": ADMIN_PASSWORD,
            },
        )
        assert init.status_code == 200, init.text
        login = client.post(
            "/api/auth/login",
            json={
                "username": ADMIN_USERNAME,
                "password": ADMIN_PASSWORD,
                "remember": False,
                "issue_api_token": True,
            },
        )
        assert login.status_code == 200, login.text
        session_id = client.cookies.get(settings.SESSION_COOKIE_NAME)
        assert session_id, "登录必须下发会话 cookie"
        yield WecomAdmin(client, session_id, login.json()["api_token"])
    settings.DATABASE_DIR = old


def _create_channel(api: WecomAdmin, name="门店群", url=WEBHOOK_URL, enabled=True) -> int:
    """建一个渠道，返回它的 id。"""
    resp = api.as_session(
        "POST",
        f"{API_PREFIX}/webhooks",
        json={"name": name, "webhook_url": url, "enabled": enabled, "notes": ""},
    )
    assert resp.status_code == 200, resp.text
    return int(resp.json()["channel"]["id"])


def _subscribe(api: WecomAdmin, topic_id: str, channel_id: int, enabled=True):
    resp = api.as_session(
        "POST",
        f"{API_PREFIX}/subscriptions",
        json={"topic_id": topic_id, "target_channel_id": channel_id, "enabled": enabled},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _job_payload(**overrides) -> dict:
    """一条任务的新形状请求体（页面表单发出来的就是它）。"""
    payload = {
        "name": "每日销售报表",
        "topic_id": "sales_report",
        "params": {"schedule_time": "21:30", "date_range_mode": "today", "station": ""},
        "schedule_time": "21:30",
        "enabled": True,
        "notes": "",
    }
    payload.update(overrides)
    return payload


def _create_job(api: WecomAdmin, **overrides) -> dict:
    resp = api.as_session("POST", f"{API_PREFIX}/jobs", json=_job_payload(**overrides))
    assert resp.status_code == 200, resp.text
    return resp.json()["job"]


def _jobs(api: WecomAdmin) -> list:
    resp = api.as_session("GET", f"{API_PREFIX}/jobs")
    assert resp.status_code == 200, resp.text
    return resp.json()["jobs"]


def _deliveries(api: WecomAdmin) -> list:
    resp = api.as_session("GET", f"{API_PREFIX}/logs")
    assert resp.status_code == 200, resp.text
    return resp.json()["rows"]


# ── 任务的新形状 ────────────────────────────────────────────────────────────


def test_a_job_carries_a_topic_and_params_instead_of_a_recipient(api):
    job = _create_job(
        api,
        name="早市销售报表",
        params={"schedule_time": "14:30", "date_range_mode": "today", "station": "shulong"},
        schedule_time="14:30",
    )

    assert job["topic_id"] == "sales_report"
    assert job["topic_name"] == "销售报表"
    assert job["params"]["station"] == "shulong"
    assert job["schedule_time"] == "14:30"
    assert job["last_sent_date"] == ""
    # 收件人不再是任务的一列：页面上没有「目标 webhook」这个东西了。
    for gone in ("webhook_id", "webhook_name", "push_type", "date_range_mode", "station"):
        assert gone not in job, f"{gone} 不该再出现在任务形状里"


def test_the_top_level_time_wins_over_the_one_in_the_params(api):
    """页面上只有一个时间控件（参数区那个 time 控件），顶层是它的投影。

    两处对不上时以顶层为准，避免「列里 21:30、参数里 09:00」这种各说各话的存档。
    """
    job = _create_job(
        api,
        params={"schedule_time": "09:00", "date_range_mode": "today", "station": ""},
        schedule_time="09:05",
    )

    assert job["schedule_time"] == "09:05"
    assert job["params"]["schedule_time"] == "09:05"


def test_the_same_topic_can_have_several_jobs(api):
    first = _create_job(api, name="早市", schedule_time="14:30",
                        params={"schedule_time": "14:30", "date_range_mode": "today", "station": ""})
    second = _create_job(api, name="晚市", schedule_time="21:30",
                         params={"schedule_time": "21:30", "date_range_mode": "yesterday", "station": "shulong"})

    listed = {job["id"]: job for job in _jobs(api)}
    assert set(listed) == {first["id"], second["id"]}
    assert listed[first["id"]]["params"]["date_range_mode"] == "today"
    assert listed[second["id"]]["params"]["date_range_mode"] == "yesterday"

    # 改一条不影响另一条
    resp = api.as_session(
        "PUT",
        f"{API_PREFIX}/jobs/{first['id']}",
        json=_job_payload(
            name="早市（改）",
            schedule_time="15:00",
            params={"schedule_time": "15:00", "date_range_mode": "today", "station": ""},
        ),
    )
    assert resp.status_code == 200, resp.text
    listed = {job["id"]: job for job in _jobs(api)}
    assert listed[first["id"]]["name"] == "早市（改）"
    assert listed[second["id"]]["name"] == "晚市"
    assert listed[second["id"]]["schedule_time"] == "21:30"


def test_a_job_card_counts_the_subscribed_targets(api):
    """任务卡片显示「订阅目标数」，不是单个群名（票面验收项）。"""
    first = _create_channel(api, "门店群")
    second = _create_channel(api, "日报群", url=WEBHOOK_URL_B)
    _create_channel(api, "没人订阅的群", url=WEBHOOK_URL_C)
    _subscribe(api, "sales_report", first)
    _subscribe(api, "sales_report", second)

    job = _create_job(api)
    assert job["target_count"] == 2

    # 列表里也一样（卡片直接读它）
    assert _jobs(api)[0]["target_count"] == 2

    # 停用一个渠道：订阅保留，它仍然算目标（投递时会跳过，页面上另有标注）
    api.as_session(
        "PUT",
        f"{API_PREFIX}/webhooks/{second}",
        json={"name": "日报群", "webhook_url": None, "enabled": False, "notes": ""},
    )
    assert _jobs(api)[0]["target_count"] == 2

    # 取消一条订阅：数字跟着变
    resp = api.as_session(
        "DELETE",
        f"{API_PREFIX}/subscriptions",
        params={"topic_id": "sales_report", "target_channel_id": second},
    )
    assert resp.status_code == 200, resp.text
    assert _jobs(api)[0]["target_count"] == 1


def test_another_topic_counts_its_own_targets(api):
    channel = _create_channel(api, "门店群")
    _subscribe(api, "reconcile_diff", channel)

    job = _create_job(
        api,
        name="对账差异日报",
        topic_id="reconcile_diff",
        params={"schedule_time": "22:10", "date_range_mode": "today"},
        schedule_time="22:10",
    )

    assert job["topic_id"] == "reconcile_diff"
    assert job["topic_name"] == "对账差异告警"
    assert job["target_count"] == 1


# ── 旧形状：明确拒绝，且不写任何东西 ────────────────────────────────────────


def test_the_old_shape_is_refused_with_a_refresh_hint(api):
    _create_job(api)
    before = _jobs(api)

    refused = api.as_session(
        "POST",
        f"{API_PREFIX}/jobs",
        json={
            "name": "旧页面建的任务",
            "webhook_id": 1,
            "push_type": "sales_report_text",
            "schedule_time": "21:30",
            "date_range_mode": "today",
            "station": "",
            "enabled": True,
            "notes": "",
        },
    )

    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == STALE_PAGE_TEXT
    assert _jobs(api) == before, "被拒绝的请求不能产生任何写入"


def test_the_old_shape_is_refused_on_update_too(api):
    job = _create_job(api)
    before = _jobs(api)

    refused = api.as_session(
        "PUT",
        f"{API_PREFIX}/jobs/{job['id']}",
        json={
            "name": "旧页面改的任务",
            "webhook_id": 7,
            "push_type": "sales_report_text",
            "schedule_time": "22:30",
            "date_range_mode": "today",
            "station": "",
            "enabled": True,
            "notes": "",
        },
    )

    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == STALE_PAGE_TEXT
    assert _jobs(api) == before, "被拒绝的请求不能改到任何东西"


def test_the_job_writes_only_accept_a_browser_session(api):
    """这一页的写操作只接受浏览器登录会话（spec「鉴权与审计」）。"""
    refused = api.as_token("POST", f"{API_PREFIX}/jobs", json=_job_payload())

    assert refused.status_code == 401, refused.text
    assert refused.json() == AUTH_GATE_BODY
    assert _jobs(api) == []


# ── 参数与内容类型由注册表把关 ──────────────────────────────────────────────


def test_params_are_checked_by_the_topic_schema(api):
    refused = api.as_session(
        "POST",
        f"{API_PREFIX}/jobs",
        json=_job_payload(params={"schedule_time": "21:30", "nope": "x"}),
    )

    assert refused.status_code == 400, refused.text
    assert "nope" in refused.json()["detail"]
    assert _jobs(api) == []


def test_a_topic_that_cannot_be_scheduled_is_refused(api):
    """验收照片是事件类内容：它没有定时侧，也就没有表单参数，不能建成定时任务。"""
    refused = api.as_session(
        "POST",
        f"{API_PREFIX}/jobs",
        json=_job_payload(topic_id="hygiene_photo", params={}),
    )

    assert refused.status_code == 400, refused.text
    assert "验收照片" in refused.json()["detail"]
    assert "定时" in refused.json()["detail"]


def test_an_unknown_topic_is_refused(api):
    refused = api.as_session(
        "POST", f"{API_PREFIX}/jobs", json=_job_payload(topic_id="nope")
    )

    assert refused.status_code == 400, refused.text
    assert "nope" in refused.json()["detail"]


def test_a_bad_schedule_time_is_refused(api):
    refused = api.as_session(
        "POST", f"{API_PREFIX}/jobs", json=_job_payload(schedule_time="25:00")
    )

    assert refused.status_code == 422, refused.text


# ── 预览：按内容类型渲染正文 ────────────────────────────────────────────────


def test_preview_renders_the_sales_report_of_the_business_day(api):
    job = _create_job(api)

    resp = api.as_session("POST", f"{API_PREFIX}/jobs/{job['id']}/preview", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert "【销售报表】" in body["content"]
    assert body["byte_length"] == len(body["content"].encode("utf-8"))
    assert body["within_limit"] is True
    assert body["part_count"] >= 1


def test_preview_renders_a_reconcile_diff_job(api):
    job = _create_job(
        api,
        name="对账差异日报",
        topic_id="reconcile_diff",
        params={"schedule_time": "22:10", "date_range_mode": "yesterday"},
        schedule_time="22:10",
    )

    resp = api.as_session("POST", f"{API_PREFIX}/jobs/{job['id']}/preview", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["content"]


# ── 立即发送：按该内容类型的全部订阅目标投递 ────────────────────────────────


def test_send_now_delivers_to_every_subscribed_target(api):
    first = _create_channel(api, "门店群")
    second = _create_channel(api, "日报群", url=WEBHOOK_URL_B)
    _create_channel(api, "没人订阅的群", url=WEBHOOK_URL_C)
    _subscribe(api, "sales_report", first)
    _subscribe(api, "sales_report", second)
    job = _create_job(api)

    resp = api.as_session("POST", f"{API_PREFIX}/jobs/{job['id']}/send-now", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["queued"] == 2
    assert body["target_count"] == 2
    assert body["message_bytes"] > 0

    rows = _deliveries(api)
    assert len(rows) == 2, "一次投递一行：两个订阅目标就是两行"
    assert {row["topic_name"] for row in rows} == {"销售报表"}
    assert {row["status"] for row in rows} == {"pending"}
    assert {row["channel_id"] for row in rows} == {first, second}


def test_send_now_without_any_subscription_is_refused(api):
    """零订阅时不能静默成功：店长会以为已经发出去了。"""
    job = _create_job(api)

    resp = api.as_session("POST", f"{API_PREFIX}/jobs/{job['id']}/send-now", json={})

    assert resp.status_code == 400, resp.text
    assert "订阅" in resp.json()["detail"]
    assert _deliveries(api) == []


def test_send_now_counts_the_targets_even_when_the_channel_is_disabled(api):
    """停用的渠道也算目标：订阅保留、投递跳过，出站行会写明为什么（不是静默消失）。"""
    channel = _create_channel(api, "门店群", enabled=False)
    _subscribe(api, "sales_report", channel)
    job = _create_job(api)

    resp = api.as_session("POST", f"{API_PREFIX}/jobs/{job['id']}/send-now", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["target_count"] == 1
    rows = _deliveries(api)
    assert [row["status"] for row in rows] == ["skipped"]


# ── 手工发送的两条旧链路也落进发送记录（票 07 的遗留）───────────────────────


def test_a_test_message_lands_in_the_send_log(api):
    channel = _create_channel(api, "门店群")

    with patch.object(
        wecom_push_service, "send_text", new=AsyncMock(return_value=(True, "ok"))
    ):
        resp = api.as_session(
            "POST", f"{API_PREFIX}/webhooks/{channel}/test", json={}
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
    rows = _deliveries(api)
    assert len(rows) == 1
    assert rows[0]["topic_name"] == "测试消息"
    assert rows[0]["status"] == "sent"
    assert rows[0]["channel_name"] == "门店群"
    assert rows[0]["attempts"] == 1


def test_a_manual_text_send_lands_in_the_send_log(api):
    """销售报表页的「推送」弹窗走的就是这条接口：它的结果也要能在记录里查到。"""
    channel = _create_channel(api, "门店群")

    with patch.object(
        wecom_push_service, "send_text", new=AsyncMock(return_value=(True, "ok"))
    ):
        resp = api.as_session(
            "POST",
            f"{API_PREFIX}/send-text",
            json={
                "webhook_id": channel,
                "content": "【销售报表】2026-05-02\n订单数：3",
                "push_type": "sales_report_text",
            },
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
    rows = _deliveries(api)
    assert len(rows) == 1
    assert rows[0]["topic_name"] == "手工发送"
    assert rows[0]["status"] == "sent"


def test_the_manual_send_paths_no_longer_write_the_legacy_log_table(api):
    """旧推送日志表不再有新行：发送记录只有出站表这一份（ADR 0095）。"""
    import asyncio

    from database import DatabaseManager

    channel = _create_channel(api, "门店群")
    with patch.object(
        wecom_push_service, "send_text", new=AsyncMock(return_value=(True, "ok"))
    ):
        api.as_session("POST", f"{API_PREFIX}/webhooks/{channel}/test", json={})

    async def _legacy_rows():
        db = DatabaseManager()
        assert await db.connect()
        try:
            return await db.wecom_logs_recent(10)
        finally:
            await db.close()

    assert asyncio.run(_legacy_rows()) == []

    # 但旧表那一份镜像仍在响应里（缓存着旧 bundle 的浏览器还在读它）
    payload = api.as_session("GET", f"{API_PREFIX}/logs").json()
    assert payload["logs"] == []
    assert len(payload["rows"]) == 1


# ── 页面契约版本 ────────────────────────────────────────────────────────────


def test_the_page_contract_version_moved_for_the_new_job_shape(api):
    """任务接口的形状变了 → 版本 +1：旧 bundle 加载时会看到顶部提示条。"""
    meta = api.as_session("GET", f"{API_PREFIX}/meta").json()

    assert meta["api_version"] == "v2"
