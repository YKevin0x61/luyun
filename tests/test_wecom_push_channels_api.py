#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渠道 / 群组 / 订阅三个面的接口契约（票 06）。

缝隙（spec「Testing Decisions」第 5、6 条 + 票面「接口层断言鉴权与字段校验」）：
整套跑在**真 `main.app`** 上（lifespan 装配 runtime、会话校验真的查库），所以：

* 写接口的凭据口径是**真的**在验 —— 会话 cookie 放行、`X-Admin-Token` 401，
  这条断言不能靠 `dependency_overrides` 糊过去（那正是被测的那一行）；
* 读接口沿用 router 级的旧凭据（会话 / API token 都行），反向也钉住；
* 页面清单契约（第 6 条）住 `tests/test_spa_page_routes.py`，本票不改路由 ——
  验收命令里跑它就是那一条。

断言的是**行为**：页面上要看到什么、勾选之后下一次触发按什么投递、被引用时删除给什么
提示。不测 SQL 形状，也不测内部函数调用顺序。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from config import settings

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
AUTH_GATE_BODY = {"detail": "需要登录"}

REPO_ROOT = Path(__file__).resolve().parents[1]
TOPICS_SOURCE = REPO_ROOT / "services" / "wecom_push_topics.py"
COMPOSABLE_SOURCE = REPO_ROOT / "admin-web" / "src" / "composables" / "useWecomPush.js"


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


def _create_channel(api: WecomAdmin, name="门店群", url=WEBHOOK_URL, enabled=True):
    resp = api.as_session(
        "POST",
        f"{API_PREFIX}/webhooks",
        json={"name": name, "webhook_url": url, "enabled": enabled, "notes": ""},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["channel"]


def _create_topic_job(api: WecomAdmin, name="每日报表"):
    """建一条推送任务（票 08 的形状：内容类型 + 参数 + 时间，没有收件人）。"""
    resp = api.as_session(
        "POST",
        f"{API_PREFIX}/jobs",
        json={
            "name": name,
            "topic_id": "sales_report",
            "params": {"schedule_time": "21:30", "date_range_mode": "today", "station": ""},
            "schedule_time": "21:30",
            "enabled": True,
            "notes": "",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["job"]


def _subscribe(api: WecomAdmin, topic_id: str, channel_id: int, enabled=True):
    resp = api.as_session(
        "POST",
        f"{API_PREFIX}/subscriptions",
        json={
            "topic_id": topic_id,
            "target_channel_id": channel_id,
            "enabled": enabled,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


TOPIC_FOR_DELIVERY = "hygiene_reminder"


def _run_delivery(channel_id: int) -> str:
    """真链路走一次投递：按订阅入队 → 假发送器派发成功 → 返回终态行的完成时间。

    发送器是替身（不碰网络），入队、订阅求解、节流、状态机全是生产代码 —— 页面上那串
    「最近一次发送成功」正是从这一步的结果里算出来的。用手工发得出去的那一类内容
    （事件类，正文由触发点放进参数），而不是定时报表：报表的正文要现算一份报表数据，
    那是发送链路的题，不是本票的。``last_sent_at`` 与内容类型无关，只认渠道。
    """
    import asyncio
    from unittest.mock import AsyncMock

    from database import DatabaseManager
    from services.wecom_outbox import WeComOutbox
    from services.wecom_push_topics import PushTrigger

    async def _run() -> str:
        db = DatabaseManager()
        assert await db.connect()
        try:
            sender = AsyncMock()
            sender.send_text_chunks = AsyncMock(return_value=(True, "ok"))
            sender.send_text = AsyncMock(return_value=(True, "ok"))
            sender.send_image = AsyncMock(return_value=(True, "ok"))
            outbox = WeComOutbox(sender=sender)
            ids = await outbox.enqueue_topic(
                db,
                TOPIC_FOR_DELIVERY,
                params={"text": "【卫生提醒】今天的漏拍汇总"},
                trigger=PushTrigger.EVENT,
                business_reference="2026-10-05",
            )
            assert ids, "入队失败：订阅没生效？"
            assert await outbox.dispatch_pending(db) == 1
            row = await db.wecom_outbox_get(ids[0])
            assert row["status"] == "sent", row
            return str(row["finished_at"])
        finally:
            await db.close()

    return asyncio.run(_run())


# ── 渠道：卡片显示的四样 ────────────────────────────────────────────────────


def test_channel_list_carries_the_fields_the_card_shows(api):
    """启停 / 备注 / 所属群组 / 订阅了哪些内容 / 最近一次发送成功时间。"""
    channel = _create_channel(api, name="门店群")
    channel_b = _create_channel(api, name="日报群", url=WEBHOOK_URL_B)
    group = api.as_session(
        "POST", f"{API_PREFIX}/channel-groups", json={"name": "日报群组"}
    ).json()["group"]
    api.as_session(
        "POST",
        f"{API_PREFIX}/channel-groups/{group['id']}/members",
        json={"channel_id": channel["id"]},
    )
    _subscribe(api, "sales_report", channel["id"])

    payload = api.as_session("GET", f"{API_PREFIX}/webhooks").json()

    assert payload["success"] is True
    listed = {row["id"]: row for row in payload["webhooks"]}
    assert set(listed) == {channel["id"], channel_b["id"]}
    assert listed[channel["id"]]["notes"] == ""
    assert listed[channel["id"]]["enabled"] is True
    assert listed[channel["id"]]["last_sent_at"] == ""

    # 矩阵那一份带着所属群组与订阅内容（渠道卡片直接读它，不在前端重新推导）。
    cards = {row["id"]: row for row in payload["channels"]}
    assert [g["id"] for g in cards[channel["id"]]["groups"]] == [group["id"]]
    assert [t["name"] for t in cards[channel["id"]]["topics"]] == ["销售报表"]


def test_channel_card_shows_the_last_successful_send_time(api):
    """最近一次发送成功 = 统一出站里 status=sent 的那一行（ADR 0095）。

    走**真链路**造数据：入队 → 假发送器派发成功，不手写库（写库就测不出「从出站表算」
    这件事本身）。还没发出去的那一行不能算数。
    """
    channel = _create_channel(api, name="门店群")
    _subscribe(api, TOPIC_FOR_DELIVERY, channel["id"])

    finished_at = _run_delivery(channel["id"])

    payload = api.as_session("GET", f"{API_PREFIX}/webhooks").json()
    card = next(row for row in payload["channels"] if row["id"] == channel["id"])
    assert card["last_sent_at"] == finished_at
    # 列表那一份也带着（渠道卡片用它，不必再拉矩阵）
    listed = next(row for row in payload["webhooks"] if row["id"] == channel["id"])
    assert listed["last_sent_at"] == finished_at


def test_channel_create_edit_toggle_delete_round_trip(api):
    created = _create_channel(api, name="门店群")
    assert created["name"] == "门店群"
    assert created["last_sent_at"] == ""

    edited = api.as_session(
        "PUT",
        f"{API_PREFIX}/webhooks/{created['id']}",
        json={
            "name": "门店群（改名）",
            "webhook_url": None,
            "enabled": False,
            "notes": "早班用",
        },
    ).json()
    assert edited["webhook"]["name"] == "门店群（改名）"
    assert edited["webhook"]["enabled"] is False
    assert edited["webhook"]["notes"] == "早班用"
    # 地址留空表示不动：掩码不变
    assert edited["webhook"]["webhook_url_masked"] == created["webhook_url_masked"]

    deleted = api.as_session("DELETE", f"{API_PREFIX}/webhooks/{created['id']}")
    assert deleted.status_code == 200, deleted.text
    assert api.as_session("GET", f"{API_PREFIX}/webhooks").json()["webhooks"] == []


def test_deleting_a_channel_leaves_the_jobs_alone(api):
    """任务不再绑定渠道（票 08）：删渠道取消它的订阅，但没有任何任务因此失效。

    旧模型里任务自己持有收件人，删渠道必须先拦住（否则那条任务永远发不出去）。现在
    收件人来自订阅：渠道走了，任务照旧，缺的只是一个收件人（卡片上的目标数变 0）。
    """
    channel = _create_channel(api, name="门店群")
    _subscribe(api, "sales_report", channel["id"])
    job = _create_topic_job(api, name="每日报表")

    resp = api.as_session("DELETE", f"{API_PREFIX}/webhooks/{channel['id']}")

    assert resp.status_code == 200, resp.text
    assert "订阅" in resp.json()["message"]
    jobs = api.as_session("GET", f"{API_PREFIX}/jobs").json()["jobs"]
    assert [item["id"] for item in jobs] == [job["id"]], "任务不该跟着渠道消失"
    assert jobs[0]["target_count"] == 0


def test_deleting_a_subscribed_channel_drops_its_subscriptions(api):
    """被订阅引用时可以删（订阅是渠道的附属关系），但结果要说清。"""
    channel = _create_channel(api, name="门店群")
    _subscribe(api, "sales_report", channel["id"])
    group = api.as_session(
        "POST", f"{API_PREFIX}/channel-groups", json={"name": "日报群组"}
    ).json()["group"]
    api.as_session(
        "POST",
        f"{API_PREFIX}/channel-groups/{group['id']}/members",
        json={"channel_id": channel["id"]},
    )

    resp = api.as_session("DELETE", f"{API_PREFIX}/webhooks/{channel['id']}")

    assert resp.status_code == 200, resp.text
    message = resp.json()["message"]
    assert "删除" in message
    assert "日报群组" in message

    matrix = api.as_session("GET", f"{API_PREFIX}/subscriptions").json()
    assert all(row["channels"] == [] for row in matrix["topics"])
    groups = api.as_session("GET", f"{API_PREFIX}/channel-groups").json()["groups"]
    assert groups[0]["member_channel_ids"] == []


# ── 群组与成员 ──────────────────────────────────────────────────────────────


def test_channel_group_crud_and_members(api):
    channel_a = _create_channel(api, name="门店群")
    channel_b = _create_channel(api, name="日报群", url=WEBHOOK_URL_B)

    created = api.as_session(
        "POST", f"{API_PREFIX}/channel-groups", json={"name": "日报群组", "notes": "早班"}
    )
    assert created.status_code == 200, created.text
    group = created.json()["group"]
    assert group["name"] == "日报群组"
    assert group["member_channel_ids"] == []

    duplicate = api.as_session(
        "POST", f"{API_PREFIX}/channel-groups", json={"name": "日报群组"}
    )
    assert duplicate.status_code == 400, duplicate.text
    assert "已存在" in duplicate.json()["detail"]

    added = api.as_session(
        "POST",
        f"{API_PREFIX}/channel-groups/{group['id']}/members",
        json={"channel_id": channel_a["id"]},
    ).json()["group"]
    assert added["member_channel_ids"] == [channel_a["id"]]

    # 一个渠道可以属于多个群组（用户故事 5）。
    other = api.as_session(
        "POST", f"{API_PREFIX}/channel-groups", json={"name": "门店 A"}
    ).json()["group"]
    second = api.as_session(
        "POST",
        f"{API_PREFIX}/channel-groups/{other['id']}/members",
        json={"channel_id": channel_a["id"]},
    )
    assert second.status_code == 200, second.text
    api.as_session(
        "POST",
        f"{API_PREFIX}/channel-groups/{other['id']}/members",
        json={"channel_id": channel_b["id"]},
    )
    listed = api.as_session("GET", f"{API_PREFIX}/channel-groups").json()["groups"]
    by_name = {row["name"]: row for row in listed}
    assert by_name["日报群组"]["member_channel_ids"] == [channel_a["id"]]
    assert sorted(by_name["门店 A"]["member_channel_ids"]) == [channel_a["id"], channel_b["id"]]

    renamed = api.as_session(
        "PUT",
        f"{API_PREFIX}/channel-groups/{group['id']}",
        json={"name": "日报群组（改名）", "enabled": False, "notes": ""},
    ).json()["group"]
    assert renamed["name"] == "日报群组（改名）"
    assert renamed["enabled"] is False

    removed = api.as_session(
        "DELETE", f"{API_PREFIX}/channel-groups/{group['id']}/members/{channel_a['id']}"
    ).json()["group"]
    assert removed["member_channel_ids"] == []

    deleted = api.as_session("DELETE", f"{API_PREFIX}/channel-groups/{group['id']}")
    assert deleted.status_code == 200, deleted.text
    assert [row["name"] for row in
            api.as_session("GET", f"{API_PREFIX}/channel-groups").json()["groups"]] == ["门店 A"]


def test_group_member_rejects_unknown_channel_and_group(api):
    channel = _create_channel(api, name="门店群")
    group = api.as_session(
        "POST", f"{API_PREFIX}/channel-groups", json={"name": "日报群组"}
    ).json()["group"]

    assert api.as_session(
        "POST",
        f"{API_PREFIX}/channel-groups/{group['id']}/members",
        json={"channel_id": 999999},
    ).status_code == 404
    assert api.as_session(
        "POST",
        f"{API_PREFIX}/channel-groups/999999/members",
        json={"channel_id": channel["id"]},
    ).status_code == 404
    assert api.as_session("DELETE", f"{API_PREFIX}/channel-groups/999999").status_code == 404
    assert api.as_session(
        "PUT", f"{API_PREFIX}/channel-groups/999999", json={"name": "x"}
    ).status_code == 404


# ── 订阅矩阵 ────────────────────────────────────────────────────────────────


def test_subscription_matrix_rows_are_topics_and_columns_are_channels(api):
    channel = _create_channel(api, name="门店群")
    _create_channel(api, name="日报群", url=WEBHOOK_URL_B)
    _subscribe(api, "sales_report", channel["id"])

    payload = api.as_session("GET", f"{API_PREFIX}/subscriptions").json()

    assert payload["api_version"]
    rows = {row["id"]: row for row in payload["topics"]}
    # 八类内容类型一行不少（行序就是注册表声明顺序）
    assert list(rows) == [
        "sales_report", "reconcile_diff", "unmapped_dish", "scraper_failure",
        "hygiene_reminder", "hygiene_photo", "update_backup", "resource_watermark",
    ]
    assert rows["sales_report"]["channels"] == [
        {"id": channel["id"], "enabled": True}
    ]
    assert rows["hygiene_photo"]["contains_employee_photos"] is True
    assert rows["sales_report"]["contains_employee_photos"] is False
    # 零订阅的行：空数组（页面据此高亮 + 顶部提示）
    assert rows["hygiene_photo"]["channels"] == []
    # 列 = 全部渠道：新建、还没勾过订阅的渠道也要在矩阵上有可勾的位置
    assert len(payload["channels"]) == 2
    columns = {row["id"]: row for row in payload["channels"]}
    assert columns[channel["id"]]["topics"][0]["id"] == "sales_report"
    assert columns[channel["id"]]["topics"][0]["via_group"] is False
    other = next(cid for cid in columns if cid != channel["id"])
    assert columns[other]["topics"] == []


def test_checking_a_subscription_makes_the_next_trigger_deliver_to_that_channel(api):
    """票面验收：「保存后下一次触发即按新订阅投递」—— 用订阅求解验。"""
    import asyncio

    from database import DatabaseManager
    from services.wecom_outbox import resolve_targets

    channel = _create_channel(api, name="门店群")

    async def _targets(topic_id: str):
        db = DatabaseManager()
        assert await db.connect()
        try:
            return [target.channel_id for target in await resolve_targets(db, topic_id)]
        finally:
            await db.close()

    assert asyncio.run(_targets("sales_report")) == []

    _subscribe(api, "sales_report", channel["id"])

    assert asyncio.run(_targets("sales_report")) == [channel["id"]]


def test_subscribing_a_group_delivers_to_its_members(api):
    import asyncio

    from database import DatabaseManager
    from services.wecom_outbox import resolve_targets

    channel = _create_channel(api, name="门店群")
    group = api.as_session(
        "POST", f"{API_PREFIX}/channel-groups", json={"name": "日报群组"}
    ).json()["group"]
    api.as_session(
        "POST",
        f"{API_PREFIX}/channel-groups/{group['id']}/members",
        json={"channel_id": channel["id"]},
    )

    subscribed = api.as_session(
        "POST",
        f"{API_PREFIX}/subscriptions",
        json={"topic_id": "sales_report", "target_group_id": group["id"]},
    )
    assert subscribed.status_code == 200, subscribed.text

    async def _targets():
        db = DatabaseManager()
        assert await db.connect()
        try:
            return [target.channel_id for target in await resolve_targets(db, "sales_report")]
        finally:
            await db.close()

    assert asyncio.run(_targets()) == [channel["id"]]
    # 渠道卡片要看得出来这条订阅是经群组来的
    cards = api.as_session("GET", f"{API_PREFIX}/webhooks").json()["channels"]
    assert cards[0]["topics"][0]["via_group"] is True


def test_unchecking_a_subscription_stops_delivery(api):
    channel = _create_channel(api, name="门店群")
    _subscribe(api, "sales_report", channel["id"])

    removed = api.as_session(
        "DELETE",
        f"{API_PREFIX}/subscriptions",
        params={"topic_id": "sales_report", "target_channel_id": channel["id"]},
    )

    assert removed.status_code == 200, removed.text
    assert removed.json()["removed"] == 1
    matrix = api.as_session("GET", f"{API_PREFIX}/subscriptions").json()
    assert all(row["channels"] == [] for row in matrix["topics"])

    again = api.as_session(
        "DELETE",
        f"{API_PREFIX}/subscriptions",
        params={"topic_id": "sales_report", "target_channel_id": channel["id"]},
    )
    assert again.status_code == 404, again.text


def test_subscription_payload_validation(api):
    channel = _create_channel(api, name="门店群")
    group = api.as_session(
        "POST", f"{API_PREFIX}/channel-groups", json={"name": "日报群组"}
    ).json()["group"]

    unknown_topic = api.as_session(
        "POST",
        f"{API_PREFIX}/subscriptions",
        json={"topic_id": "no_such_topic", "target_channel_id": channel["id"]},
    )
    assert unknown_topic.status_code == 422, unknown_topic.text
    assert "未知的推送内容类型" in unknown_topic.text

    both_targets = api.as_session(
        "POST",
        f"{API_PREFIX}/subscriptions",
        json={
            "topic_id": "sales_report",
            "target_channel_id": channel["id"],
            "target_group_id": group["id"],
        },
    )
    assert both_targets.status_code == 422, both_targets.text

    no_target = api.as_session(
        "POST", f"{API_PREFIX}/subscriptions", json={"topic_id": "sales_report"}
    )
    assert no_target.status_code == 422, no_target.text

    unknown_channel = api.as_session(
        "POST",
        f"{API_PREFIX}/subscriptions",
        json={"topic_id": "sales_report", "target_channel_id": 999999},
    )
    assert unknown_channel.status_code == 404, unknown_channel.text

    unknown_group = api.as_session(
        "POST",
        f"{API_PREFIX}/subscriptions",
        json={"topic_id": "sales_report", "target_group_id": 999999},
    )
    assert unknown_group.status_code == 404, unknown_group.text


def test_subscribing_twice_does_not_create_a_second_row(api):
    channel = _create_channel(api, name="门店群")

    first = _subscribe(api, "sales_report", channel["id"])
    second = _subscribe(api, "sales_report", channel["id"])

    assert first["subscription_id"] == second["subscription_id"]
    matrix = api.as_session("GET", f"{API_PREFIX}/subscriptions").json()
    row = next(row for row in matrix["topics"] if row["id"] == "sales_report")
    assert [item["id"] for item in row["channels"]] == [channel["id"]]


def test_meta_carries_the_api_version(api):
    payload = api.as_session("GET", f"{API_PREFIX}/meta").json()

    assert payload["api_version"]
    assert payload["api_version"] == api.as_session(
        "GET", f"{API_PREFIX}/subscriptions"
    ).json()["api_version"]
    # 旧字段一个都没动（缓存着旧 bundle 的浏览器还在读）
    assert payload["text_limit"] > 0
    assert [tpl["id"] for tpl in payload["job_templates"]] == [
        "sales_report_daily", "data_quality_daily",
    ]


def test_page_contract_version_matches_the_backend():
    """前端本地版本要与 `/meta` 的接口版本**同一处来源**地对齐。

    对不上（忘了改前端）时页面永远亮着提示条 —— 那比没有提示条更糟：店长会学着忽略它。
    """
    backend = re.search(
        r'^WECOM_PUSH_API_VERSION\s*=\s*"([^"]+)"', TOPICS_SOURCE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert backend, "services/wecom_push_topics.py 里找不到 WECOM_PUSH_API_VERSION"
    frontend = re.search(
        r"WECOM_PUSH_API_VERSION\s*=\s*'([^']+)'", COMPOSABLE_SOURCE.read_text(encoding="utf-8")
    )
    assert frontend, "admin-web/src/composables/useWecomPush.js 里找不到 WECOM_PUSH_API_VERSION"
    assert frontend.group(1) == backend.group(1)


# ── 鉴权收紧：写操作只接受浏览器登录会话 ────────────────────────────────────


def test_writes_accept_the_browser_session(api):
    resp = api.as_session(
        "POST",
        f"{API_PREFIX}/webhooks",
        json={"name": "门店群", "webhook_url": WEBHOOK_URL, "enabled": True, "notes": ""},
    )
    assert resp.status_code == 200, resp.text


def test_writes_reject_api_tokens(api):
    """票面验收：写操作「用 API token 调用被拒」。

    这一票把这一页的写接口从「会话 / token / 过渡密钥」收紧成**只认会话**：配置变更
    不该来自一个来路不明的长寿命令牌（用户故事 29）。读面不动（下一条）。
    """
    channel = _create_channel(api, name="门店群")
    group = api.as_session(
        "POST", f"{API_PREFIX}/channel-groups", json={"name": "日报群组"}
    ).json()["group"]

    writes = (
        ("POST", f"{API_PREFIX}/webhooks",
         {"name": "偷加的群", "webhook_url": WEBHOOK_URL_B, "enabled": True, "notes": ""}),
        ("PUT", f"{API_PREFIX}/webhooks/{channel['id']}",
         {"name": "改名", "webhook_url": None, "enabled": False, "notes": ""}),
        ("DELETE", f"{API_PREFIX}/webhooks/{channel['id']}", None),
        ("POST", f"{API_PREFIX}/webhooks/{channel['id']}/test", None),
        ("POST", f"{API_PREFIX}/channel-groups", {"name": "另一个群组"}),
        ("PUT", f"{API_PREFIX}/channel-groups/{group['id']}", {"name": "改名"}),
        ("DELETE", f"{API_PREFIX}/channel-groups/{group['id']}", None),
        ("POST", f"{API_PREFIX}/channel-groups/{group['id']}/members",
         {"channel_id": channel["id"]}),
        ("DELETE", f"{API_PREFIX}/channel-groups/{group['id']}/members/{channel['id']}", None),
        ("POST", f"{API_PREFIX}/subscriptions",
         {"topic_id": "sales_report", "target_channel_id": channel["id"]}),
        ("DELETE", f"{API_PREFIX}/subscriptions?topic_id=sales_report"
                   f"&target_channel_id={channel['id']}", None),
    )
    for method, path, payload in writes:
        resp = api.as_token(method, path, json=payload) if payload is not None else api.as_token(
            method, path
        )
        assert resp.status_code == 401, f"{method} {path} token 居然 {resp.status_code}"
        assert resp.json() == AUTH_GATE_BODY, resp.text

    # 一条都没写进去
    assert [row["name"] for row in
            api.as_session("GET", f"{API_PREFIX}/webhooks").json()["webhooks"]] == ["门店群"]
    assert [row["name"] for row in
            api.as_session("GET", f"{API_PREFIX}/channel-groups").json()["groups"]] == ["日报群组"]
    assert all(row["channels"] == [] for row in
               api.as_session("GET", f"{API_PREFIX}/subscriptions").json()["topics"])


def test_reads_still_accept_api_tokens(api):
    """读面沿用旧凭据（会话 / API token）：这一票只收紧写操作。"""
    _create_channel(api, name="门店群")

    for path in (
        f"{API_PREFIX}/webhooks",
        f"{API_PREFIX}/channel-groups",
        f"{API_PREFIX}/subscriptions",
        f"{API_PREFIX}/meta",
    ):
        resp = api.as_token("GET", path)
        assert resp.status_code == 200, f"{path} token 读居然 {resp.status_code}"


def test_every_write_route_on_this_page_declares_the_session_gate():
    """路由表层面的契约：这一页今后**新增**的写接口也自动受这条门约束。"""
    import main as main_module
    from api.security import require_session

    def guard_names(route: APIRoute) -> set:
        names: set = set()

        def walk(dependant) -> None:
            for dependency in dependant.dependencies:
                if dependency.call is not None:
                    names.add(getattr(dependency.call, "__name__", str(dependency.call)))
                walk(dependency)

        walk(route.dependant)
        return names

    ungated = []
    for route in main_module.app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith(API_PREFIX):
            continue
        if route.path in _ROUTES_OWNED_BY_LATER_TICKETS:
            continue
        for method in sorted(route.methods):
            if method in ("GET", "HEAD", "OPTIONS"):
                continue
            names = guard_names(route)
            if require_session.__name__ not in names:
                ungated.append(f"{method} {route.path}")

    assert ungated == [], (
        "以下写接口没有 require_session（这一页的写操作只接受浏览器会话）：\n"
        + "\n".join(ungated)
    )


# `send-text` 是唯一还留旧口径的写路由：它的调用方不止这一页（销售报表页的「推送」
# 弹窗也走它），本票只收口它的落库，不动它的鉴权。定时任务那几条写路由已在票 08 里
# 收紧成会话专用（`/jobs` 的形状也换成了「内容类型 + 参数 + 时间」）。
_ROUTES_OWNED_BY_LATER_TICKETS = (
    f"{API_PREFIX}/send-text",
)
