#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发送记录 tab 的读接口契约（票 07）。

缝隙沿用票 06 的 API 缝隙（spec「Testing Decisions」第 5、6 条）：整套跑在**真
`main.app`** 上，读接口沿用 router 级的旧凭据（会话 / API token 都行）。

断言的是**页面读到的东西**：每行那七样、筛选组合、分页，以及「旧表不再是数据源」——
`/api/wecom-push/logs` 返回的就是出站表（ADR 0095：队列表与发送记录是同一张表），
旧 `wecom_push_logs` 里再写多少行也不会出现在这里。不测 SQL 形状。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from config import settings
from database import CHINA_TZ

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
API_PREFIX = "/api/wecom-push"


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


def _create_channel(api: WecomAdmin, name="门店群", url=WEBHOOK_URL) -> dict:
    resp = api.as_session(
        "POST",
        f"{API_PREFIX}/webhooks",
        json={"name": name, "webhook_url": url, "enabled": True, "notes": ""},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["channel"]


# 夹具的钟：`created_at` / `finished_at` 由它派发，页面按时间倒序的断言才确定。
CLOCK = datetime(2026, 10, 5, 21, 30, tzinfo=CHINA_TZ)


def _seed(rows, *, clock=CLOCK):
    """直接造出站行：这一份测的是**读**接口的形状与筛选，不重复走出站状态机。

    （写路径由 `tests/test_wecom_outbox.py` 覆盖；这里连的是同一个测试库，
    与 TestClient 里的 app 共用一份数据。）
    """
    from database import DatabaseManager

    async def _run():
        db = DatabaseManager()
        assert await db.connect()
        try:
            ids = []
            for item in rows:
                stamp = (clock - timedelta(minutes=item.get("minutes_ago", 0))).isoformat()
                outbox_id = await db.wecom_outbox_enqueue({
                    "topic_id": item.get("topic_id", "sales_report"),
                    "params_json": json.dumps({"text": item.get("text", "一行正文")}),
                    "target_channel_id": item["channel_id"],
                    "content_summary": item.get("text", "一行正文"),
                    "message_bytes": item.get("message_bytes", 42),
                    "idempotency_key": item["key"],
                    "status": "pending",
                    "created_at": stamp,
                })
                status = item.get("status", "sent")
                if status == "pending":
                    pass
                elif status == "sending":
                    await db.wecom_outbox_mark_sending(outbox_id, sending_at=stamp)
                elif status == "failed":
                    await db.wecom_outbox_mark_failed(
                        outbox_id, item.get("error", "boom"), attempts=item.get("attempts", 4)
                    )
                elif status == "skipped":
                    await db.wecom_outbox_mark_skipped(outbox_id, item.get("error", "渠道已停用"))
                else:
                    await db.wecom_outbox_mark_sent(
                        outbox_id, item.get("message_bytes", 42),
                        attempts=item.get("attempts", 1),
                    )
                ids.append(outbox_id)
            return ids
        finally:
            await db.close()

    return asyncio.run(_run())


@pytest.fixture
def channels(api):
    return {
        "a": _create_channel(api, name="门店群"),
        "b": _create_channel(api, name="日报群", url=WEBHOOK_URL_B),
    }


def test_records_carry_the_columns_the_page_shows(api, channels):
    """每行显示：时间、目标渠道、内容类型、状态、字节数、尝试次数、最后一次错误。

    名字在服务端就配好（`topic_name` 来自注册表、`channel_name` 来自渠道表），页面
    不必拿 id 去两张表里对照 —— 渠道删掉之后那一行也还要读得出来。
    """
    already_tried = _seed([{
        "key": "hygiene-1", "channel_id": channels["a"]["id"],
        "topic_id": "hygiene_reminder", "text": "【卫生提醒】今天漏拍 2 项",
        "message_bytes": 118, "status": "failed", "attempts": 4,
        "error": "第 1/1 条发送失败：invalid webhook url",
    }])

    payload = api.as_session("GET", f"{API_PREFIX}/logs").json()

    assert payload["success"] is True
    row = payload["rows"][0]
    assert row["id"] == already_tried[0]
    assert row["created_at"] == CLOCK.isoformat()
    assert row["topic_id"] == "hygiene_reminder"
    assert row["topic_name"] == "卫生提醒"
    assert row["channel_id"] == channels["a"]["id"]
    assert row["channel_name"] == "门店群"
    assert row["status"] == "failed"
    assert row["message_bytes"] == 118
    assert row["attempts"] == 4
    assert row["last_error"] == "第 1/1 条发送失败：invalid webhook url"
    assert row["content_summary"] == "【卫生提醒】今天漏拍 2 项"


def test_records_come_newest_first_with_pagination_metadata(api, channels):
    _seed([
        {"key": f"r-{index}", "channel_id": channels["a"]["id"], "minutes_ago": index * 10}
        for index in range(5)
    ])

    first = api.as_session("GET", f"{API_PREFIX}/logs?page=1&page_size=2").json()

    assert first["total"] == 5
    assert first["page"] == 1
    assert first["page_size"] == 2
    assert first["pages"] == 3
    stamps = [row["created_at"] for row in first["rows"]]
    assert stamps == sorted(stamps, reverse=True)

    last = api.as_session("GET", f"{API_PREFIX}/logs?page=3&page_size=2").json()
    assert len(last["rows"]) == 1


def test_filters_by_content_type_channel_and_status(api, channels):
    _seed([
        {"key": "s-1", "channel_id": channels["a"]["id"], "topic_id": "sales_report"},
        {"key": "s-2", "channel_id": channels["b"]["id"], "topic_id": "sales_report",
         "status": "failed", "error": "boom"},
        {"key": "h-1", "channel_id": channels["a"]["id"], "topic_id": "hygiene_reminder",
         "status": "failed", "error": "boom"},
    ])

    by_topic = api.as_session(
        "GET", f"{API_PREFIX}/logs?topic_id=hygiene_reminder"
    ).json()
    assert by_topic["total"] == 1
    assert by_topic["rows"][0]["topic_id"] == "hygiene_reminder"

    by_channel = api.as_session(
        "GET", f"{API_PREFIX}/logs?channel_id={channels['b']['id']}"
    ).json()
    assert [row["channel_name"] for row in by_channel["rows"]] == ["日报群"]

    by_status = api.as_session("GET", f"{API_PREFIX}/logs?status=failed").json()
    assert by_status["total"] == 2
    assert {row["status"] for row in by_status["rows"]} == {"failed"}

    combined = api.as_session(
        "GET",
        f"{API_PREFIX}/logs?topic_id=sales_report&status=failed"
        f"&channel_id={channels['b']['id']}",
    ).json()
    assert combined["total"] == 1
    assert combined["rows"][0]["id"]


def test_the_old_table_is_no_longer_a_data_source(api, channels):
    """ADR 0095：发送记录就是出站表。旧表里再写多少行都不会出现在这一页上。

    旧表与它的 `logs` 那一份镜像都还在（`send-text` / `send-test` 这两个旧入口还在写
    旧表，回滚与排查也要用），但页面读的 `rows` 只认新表 —— 这条断言挡的是「顺手读回
    旧表」的静默回退。
    """
    from database import DatabaseManager

    async def _legacy_log():
        db = DatabaseManager()
        assert await db.connect()
        try:
            await db.wecom_log_add({
                "job_id": None,
                "webhook_id": channels["a"]["id"],
                "webhook_name": "门店群",
                "push_type": "sales_report_text",
                "status": "success",
                "message_bytes": 7,
                "error": "",
                "response_text": "ok",
                "sent_at": CLOCK.isoformat(),
            })
            return await db.wecom_logs_recent(10)
        finally:
            await db.close()

    assert len(asyncio.run(_legacy_log())) == 1, "夹具没把旧表那一行写进去"

    payload = api.as_session("GET", f"{API_PREFIX}/logs").json()

    assert payload["rows"] == [], "旧表的行不该出现在发送记录里"
    assert payload["total"] == 0
    # 旧形状原样保留：旧 bundle 读 `logs`，这一份是旧表的镜像（不再是数据源，
    # 只是不让缓存着旧 bundle 的浏览器白屏）。
    assert [row["push_type"] for row in payload["logs"]] == ["sales_report_text"]


def test_records_are_still_readable_after_the_channel_is_deleted(api, channels):
    """渠道删掉之后那一行记录还在（`target_channel_id` 置空，外键 ON DELETE SET NULL）。

    页面要能看出「这封当时发给谁」：名字取不到就退回 id，而不是整行消失。
    """
    ids = _seed([{"key": "orphan-1", "channel_id": channels["b"]["id"],
                  "topic_id": "unmapped_dish"}])
    deleted = api.as_session("DELETE", f"{API_PREFIX}/webhooks/{channels['b']['id']}")
    assert deleted.status_code == 200, deleted.text

    payload = api.as_session("GET", f"{API_PREFIX}/logs").json()

    assert [row["id"] for row in payload["rows"]] == ids
    assert payload["rows"][0]["channel_id"] is None
    assert payload["rows"][0]["topic_name"] == "未映射菜品提醒"


def test_a_legacy_request_still_gets_the_plain_recent_list(api, channels):
    """不带筛选参数的老调用（缓存里的旧 bundle）拿到的仍是「最近 N 条」那一份。

    页面重构前的调用是 `?limit=80`：既没有 page / page_size，也没有筛选 —— 那时它要
    的就是「最近 80 条」，切表之后这个口径必须逐字保住，否则刷新一次记录就少一截。
    """
    _seed([
        {"key": f"legacy-{index}", "channel_id": channels["a"]["id"], "minutes_ago": index}
        for index in range(5)
    ])

    payload = api.as_session("GET", f"{API_PREFIX}/logs?limit=3").json()

    assert len(payload["rows"]) == 3, "老调用的 limit 就是它拿到多少条"
    assert payload["total"] == 5, "总数按全部记录算，不受 limit 影响"
    assert payload["page_size"] == 3


def test_page_size_is_capped_so_one_request_cannot_pull_the_whole_table(api, channels):
    _seed([{"key": "cap-1", "channel_id": channels["a"]["id"]}])

    payload = api.as_session("GET", f"{API_PREFIX}/logs?page_size=100000").json()

    assert payload["page_size"] == 200


def test_reading_records_needs_the_router_level_credential(api, channels):
    """读接口沿用 router 级旧凭据：会话与 API token 都行（写接口才收紧到会话）。"""
    _seed([{"key": "auth-1", "channel_id": channels["a"]["id"]}])

    assert len(api.as_session("GET", f"{API_PREFIX}/logs").json()["rows"]) == 1
    assert len(api.as_token("GET", f"{API_PREFIX}/logs").json()["rows"]) == 1
    assert api.client.get(f"{API_PREFIX}/logs").status_code == 401
