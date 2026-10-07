#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送配置的变更历史（票 11）。

缝隙（spec「Testing Decisions」第 5 条 + 既有 API 测试缝隙）：整套跑在**真
`main.app`** 上（lifespan 装配 runtime、会话校验真的查库），断言的是页面能看到什么：

* 每一次配置写操作留下一条记录 —— 时间、操作人（登录账号名）、操作类型、对象、
  变更前后值；
* 覆盖范围按票面点齐：渠道增删改与启停、群组与成员变更、订阅勾选与取消、任务增删改；
* 「变更历史」按时间倒序、可按对象类型筛；
* **审计写失败不能让成功的配置变更报错**（这一条单独钉住，见文件末尾）；
* 记录长期保留：出站表的 90 天清理碰不到它。

不测 SQL 形状，也不测内部函数调用顺序。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.wecom_outbox import WeComOutbox
from services.wecom_push_topics import PushTrigger

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
AUDIT_PATH = f"{API_PREFIX}/audit-log"

# 对象类型（``object_type``）：就是这些表名。页面把它翻成中文（见
# `admin-web/src/composables/useWecomPush.js` 的 `AUDIT_OBJECT_LABELS`）。
CHANNELS = "wecom_push_webhooks"
GROUPS = "wecom_channel_groups"
SUBSCRIPTIONS = "wecom_push_subscriptions"
JOBS = "wecom_push_jobs"


class WecomAdmin:
    """真 app 客户端 + 浏览器会话 cookie。"""

    def __init__(self, client: TestClient, session_id: str) -> None:
        self.client = client
        self.session_id = session_id

    def _send(self, method: str, path: str, **kwargs):
        self.client.cookies.clear()
        headers = dict(kwargs.pop("headers", None) or {})
        self.client.cookies.set(settings.SESSION_COOKIE_NAME, self.session_id)
        return self.client.request(method, path, headers=headers, **kwargs)

    def as_session(self, method: str, path: str, **kwargs):
        return self._send(method, path, **kwargs)

    def get(self, path: str, **kwargs):
        return self._send("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self._send("POST", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self._send("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self._send("DELETE", path, **kwargs)

    # ── 变更历史 ─────────────────────────────────────────────────────────
    def history(self, **params) -> dict:
        resp = self.get(AUDIT_PATH, params=params or None)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def rows(self, **params) -> list:
        return self.history(**params)["rows"]


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
            },
        )
        assert login.status_code == 200, login.text
        session_id = client.cookies.get(settings.SESSION_COOKIE_NAME)
        assert session_id, "登录必须下发会话 cookie"
        yield WecomAdmin(client, session_id)
    settings.DATABASE_DIR = old


def _create_channel(api: WecomAdmin, name="门店群", url=WEBHOOK_URL, enabled=True) -> dict:
    resp = api.post(
        f"{API_PREFIX}/webhooks",
        json={"name": name, "webhook_url": url, "enabled": enabled, "notes": ""},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["channel"]


def _create_group(api: WecomAdmin, name="日报群组") -> dict:
    resp = api.post(f"{API_PREFIX}/channel-groups", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["group"]


def _subscribe(api: WecomAdmin, topic_id: str, channel_id: int, enabled=True) -> dict:
    resp = api.post(
        f"{API_PREFIX}/subscriptions",
        json={"topic_id": topic_id, "target_channel_id": channel_id, "enabled": enabled},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _channel_rows(api: WecomAdmin, action: str = "") -> list:
    """变更历史里的渠道记录（按动作筛在断言侧做，接口本身只按对象类型筛）。"""
    return [
        row for row in api.rows(object_type=CHANNELS)
        if not action or row["action"] == action
    ]


# ── 订阅的勾选与取消 ────────────────────────────────────────────────────────


def test_checking_and_unchecking_a_subscription_leave_one_record_each(api):
    """票面验收：勾选与取消各生成一条记录，前后值正确。

    这两条是「谁把日报群取消了」唯一查得出来的地方，所以前后值必须是**领域字段**
    （内容类型 / 目标 / 启停），不是整行快照：第一次勾选是新建（没有改前），取消勾选
    改的是这一行的启停（改前是勾上的）。
    """
    channel = _create_channel(api, name="门店群")

    _subscribe(api, "sales_report", channel["id"], enabled=True)
    _subscribe(api, "sales_report", channel["id"], enabled=False)
    rows = api.rows(object_type=SUBSCRIPTIONS)

    assert len(rows) == 2, rows
    unchecked, checked = rows[0], rows[1]  # 时间倒序：最新那条（取消）在前
    assert checked["action"] == "create", "第一次勾选就是登记一条订阅"
    assert checked["before"] == {}
    assert checked["after"]["enabled"] is True
    assert checked["after"]["topic_id"] == "sales_report"
    assert checked["after"]["target_channel_id"] == channel["id"]
    assert checked["object_name"] == "销售报表 → 门店群"

    assert unchecked["action"] == "disable", "取消勾选就是把这一行停用"
    assert unchecked["before"]["enabled"] is True, "改前是勾上的"
    assert unchecked["after"]["enabled"] is False
    assert unchecked["after"]["target_name"] == "门店群"
    # 同一行、同一个 id：变更内容里不该冒出一项假的「id 变了」
    assert unchecked["before"]["id"] == unchecked["after"]["id"] == checked["after"]["id"]


def test_every_audit_row_says_when_who_what(api):
    """一条记录必须自带时间、操作人和动作 —— 页面上那三列就是它们。"""
    channel = _create_channel(api, name="门店群")
    _subscribe(api, "sales_report", channel["id"])

    row = api.rows(object_type=SUBSCRIPTIONS)[0]

    assert row["created_at"], "变更历史没有时间就查不出先后"
    assert row["actor"] == ADMIN_USERNAME
    assert row["action"] == "create"
    assert row["object_type"] == SUBSCRIPTIONS
    assert row["object_name"] == "销售报表 → 门店群"


def test_unchecking_by_deleting_is_a_delete_record(api):
    """勾选矩阵上「彻底取消」走 DELETE 时留的是删除记录（改前那一行还在记录里）。"""
    channel = _create_channel(api, name="门店群")
    _subscribe(api, "sales_report", channel["id"])

    removed = api.delete(
        f"{API_PREFIX}/subscriptions",
        params={"topic_id": "sales_report", "target_channel_id": channel["id"]},
    )
    assert removed.status_code == 200, removed.text

    row = api.rows(object_type=SUBSCRIPTIONS)[0]
    assert row["action"] == "delete"
    assert row["object_name"] == "销售报表 → 门店群"
    assert row["after"] == {}
    assert row["before"]["enabled"] is True
    assert row["before"]["topic_id"] == "sales_report"
    assert row["before"]["target_channel_id"] == channel["id"]


# ── 渠道的增删改与启停 ──────────────────────────────────────────────────────


def test_channel_create_disable_edit_delete_are_all_recorded(api):
    """票面覆盖范围：渠道的增删改与启停。启停单列成 enable / disable。"""
    created = _create_channel(api, name="门店群")

    api.put(f"{API_PREFIX}/webhooks/{created['id']}", json={
        "name": "门店群", "webhook_url": None, "enabled": False, "notes": "",
    })
    api.put(f"{API_PREFIX}/webhooks/{created['id']}", json={
        "name": "门店群（改名）", "webhook_url": WEBHOOK_URL_B,
        "enabled": False, "notes": "",
    })
    api.delete(f"{API_PREFIX}/webhooks/{created['id']}")

    rows = _channel_rows(api)
    assert [row["action"] for row in rows] == ["delete", "update", "disable", "create"]
    newest, edited, disabled, added = rows

    assert newest["before"]["name"] == "门店群（改名）"
    assert newest["after"] == {}

    # 改名 + 换地址：两样都在变更内容里，地址只留掩码（明文/密文都不进审计表）。
    assert edited["before"]["name"] == "门店群"
    assert edited["after"]["name"] == "门店群（改名）"
    assert edited["after"]["url"] == (
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=1111...5555"
    )
    assert "11111111-2222-3333-4444-555555555555" not in str(edited)

    # 只切启停：动作是 disable，前后值就是那一列。
    assert disabled["before"]["enabled"] is True
    assert disabled["after"]["enabled"] is False
    assert disabled["object_name"] == "门店群"

    assert added["action"] == "create"
    assert added["before"] == {}
    assert added["after"]["name"] == "门店群"
    assert added["after"]["enabled"] is True
    assert added["object_id"] == created["id"]


def test_turning_a_channel_back_on_is_an_enable_record(api):
    channel = _create_channel(api, name="门店群", enabled=False)

    api.put(f"{API_PREFIX}/webhooks/{channel['id']}", json={
        "name": "门店群", "webhook_url": None, "enabled": True, "notes": "",
    })

    assert [row["action"] for row in _channel_rows(api)] == ["enable", "create"]


# ── 群组与成员 ──────────────────────────────────────────────────────────────


def test_group_member_and_rename_changes_are_recorded_with_the_member_list(api):
    """群组的增删改 + 成员进出：成员名单是快照的一部分（否则看不出谁进谁出）。"""
    channel_a = _create_channel(api, name="门店群")
    channel_b = _create_channel(api, name="日报群", url=WEBHOOK_URL_B)
    group = _create_group(api, name="日报群组")

    api.post(f"{API_PREFIX}/channel-groups/{group['id']}/members",
             json={"channel_id": channel_a["id"]})
    api.post(f"{API_PREFIX}/channel-groups/{group['id']}/members",
             json={"channel_id": channel_b["id"]})
    api.delete(f"{API_PREFIX}/channel-groups/{group['id']}/members/{channel_b['id']}")
    api.put(f"{API_PREFIX}/channel-groups/{group['id']}",
            json={"name": "日报群组", "enabled": False, "notes": ""})
    api.put(f"{API_PREFIX}/channel-groups/{group['id']}",
            json={"name": "日报群组（改名）", "enabled": False, "notes": ""})
    api.delete(f"{API_PREFIX}/channel-groups/{group['id']}")

    rows = api.rows(object_type=GROUPS)
    assert [row["action"] for row in rows] == [
        "delete", "update", "disable", "update", "update", "update", "create",
    ]
    assert rows[0]["before"]["name"] == "日报群组（改名）"
    assert rows[0]["before"]["members"] == ["门店群"]
    # 加成员：改前没有、改后有；再删掉：反过来。名字直接可读。
    assert rows[5]["before"]["members"] == []
    assert rows[5]["after"]["members"] == ["门店群"]
    assert rows[4]["before"]["members"] == ["门店群"]
    assert rows[4]["after"]["members"] == ["门店群", "日报群"]
    assert rows[3]["before"]["members"] == ["门店群", "日报群"]
    assert rows[3]["after"]["members"] == ["门店群"]
    # 只切启停是 disable；改名是 update。
    assert rows[2]["before"]["enabled"] is True
    assert rows[2]["after"]["enabled"] is False
    assert rows[1]["before"]["name"] == "日报群组"
    assert rows[1]["after"]["name"] == "日报群组（改名）"
    assert rows[6]["action"] == "create" and rows[6]["after"]["members"] == []


# ── 推送任务 ────────────────────────────────────────────────────────────────


def test_job_create_disable_edit_delete_are_recorded(api):
    """票面覆盖范围：任务的增删改（表单里的「启用定时推送」同样是启停）。"""
    created = api.post(f"{API_PREFIX}/jobs", json={
        "name": "每日报表", "topic_id": "sales_report",
        "params": {"schedule_time": "21:30", "date_range_mode": "today", "station": ""},
        "schedule_time": "21:30", "enabled": True, "notes": "",
    })
    assert created.status_code == 200, created.text
    job_id = created.json()["job"]["id"]

    api.put(f"{API_PREFIX}/jobs/{job_id}", json={
        "name": "每日报表", "topic_id": "sales_report",
        "params": {"schedule_time": "21:30", "date_range_mode": "today", "station": ""},
        "schedule_time": "21:30", "enabled": False, "notes": "",
    })
    api.put(f"{API_PREFIX}/jobs/{job_id}", json={
        "name": "每日报表（改点）", "topic_id": "sales_report",
        "params": {"schedule_time": "22:45", "date_range_mode": "yesterday", "station": ""},
        "schedule_time": "22:45", "enabled": False, "notes": "",
    })
    api.delete(f"{API_PREFIX}/jobs/{job_id}")

    rows = api.rows(object_type=JOBS)
    assert [row["action"] for row in rows] == ["delete", "update", "disable", "create"]
    assert rows[0]["before"]["name"] == "每日报表（改点）"
    assert rows[1]["before"]["schedule_time"] == "21:30"
    assert rows[1]["after"]["schedule_time"] == "22:45"
    assert rows[2]["before"]["enabled"] is True and rows[2]["after"]["enabled"] is False
    assert rows[3]["before"] == {}
    assert rows[3]["after"]["topic_id"] == "sales_report"
    assert rows[3]["object_name"] == "每日报表"


# ── 读面：倒序与筛选 ────────────────────────────────────────────────────────


def test_history_is_newest_first_and_filterable_by_object_type(api):
    """页面默认读法就是「最近发生了什么」，所以倒序是接口契约的一部分。"""
    channel = _create_channel(api, name="门店群")
    _create_group(api, name="日报群组")

    listed = api.history()
    rows = listed["rows"]

    assert [row["object_type"] for row in rows] == [GROUPS, CHANNELS]
    ids = [row["id"] for row in rows]
    assert ids == sorted(ids, reverse=True), "时间倒序（同一秒内按 id 兜底）"
    assert listed["total"] == 2
    assert listed["page"] == 1 and listed["pages"] == 1

    only_groups = api.history(object_type=GROUPS)
    assert [row["object_type"] for row in only_groups["rows"]] == [GROUPS]
    assert only_groups["total"] == 1

    # 筛选项本身由后端给出（页面下拉读它，加一种对象类型只改后端）。
    assert [item["id"] for item in listed["object_types"]] == [
        CHANNELS, GROUPS, SUBSCRIPTIONS, JOBS,
    ]
    assert [item["name"] for item in listed["object_types"]] == [
        "推送渠道", "渠道群组", "推送订阅", "推送任务",
    ]
    assert [item["id"] for item in listed["actions"]] == [
        "create", "update", "enable", "disable", "delete",
    ]


def test_history_pages_do_not_overlap(api):
    """翻页：``created_at`` 撞在同一秒时靠 id 兜底，两页不会给出同一行。"""
    for index in range(3):
        _create_channel(api, name=f"门店群{index}")

    first = api.history(page=1, page_size=2)
    second = api.history(page=2, page_size=2)

    assert first["total"] == 3 and first["pages"] == 2
    assert len(first["rows"]) == 2 and len(second["rows"]) == 1
    assert not ({row["id"] for row in first["rows"]} & {row["id"] for row in second["rows"]})


def test_unknown_object_type_filters_to_empty_not_an_error(api):
    """认不出的对象类型筛出空表：页面据此说「没有符合条件的记录」，而不是整页报错。"""
    _create_channel(api, name="门店群")

    payload = api.history(object_type="no_such_type")

    assert payload["rows"] == []
    assert payload["total"] == 0
    assert payload["pages"] == 0


# ── 审计写失败不能让业务变更失败 ────────────────────────────────────────────


def test_a_channel_change_still_succeeds_when_the_audit_write_fails(api, monkeypatch):
    """票面硬要求：审计写失败只记日志，不让原本成功的配置变更报错。

    这里让 ``db.wecom_audit_add`` 直接抛（比"返回 0"更狠的一种失败：真探到了这个函数
    的爆炸路径），配置变更本身必须照常 200 并真的落库。之所以在路由层再兜一层
    ``_record_audit``，理由就是这个用例：repo 自己吞异常是正常路径，而路由不该**依赖**
    被调方永远守约。
    """
    async def boom(self, item):
        raise RuntimeError("audit table is gone")

    monkeypatch.setattr(DatabaseManager, "wecom_audit_add", boom)

    created = _create_channel(api, name="门店群")
    api.put(f"{API_PREFIX}/webhooks/{created['id']}", json={
        "name": "门店群", "webhook_url": None, "enabled": False, "notes": "",
    })
    deleted = api.delete(f"{API_PREFIX}/webhooks/{created['id']}")

    assert deleted.status_code == 200, deleted.text
    # 业务本身照常落库（不是"报错被吞了"，是真做完了）
    assert api.get(f"{API_PREFIX}/webhooks").json()["webhooks"] == []


def test_a_subscription_change_still_succeeds_when_the_audit_write_fails(api, monkeypatch):
    """订阅那条路同样兜住 —— 它是「谁改了收件人」最要紧的一处写操作。"""
    async def boom(self, item):
        raise RuntimeError("audit table is gone")

    monkeypatch.setattr(DatabaseManager, "wecom_audit_add", boom)
    channel = _create_channel(api, name="门店群")

    _subscribe(api, "sales_report", channel["id"])

    matrix = api.get(f"{API_PREFIX}/subscriptions").json()
    row = next(item for item in matrix["topics"] if item["id"] == "sales_report")
    assert [item["id"] for item in row["channels"]] == [channel["id"]]


# ── 长期保留 ────────────────────────────────────────────────────────────────


def test_audit_records_survive_the_outbox_retention_purge(api):
    """票面验收：审计记录不受发送记录 90 天保留策略影响。

    出站表的清理是 ``WeComOutbox.purge_expired``（终态行、按 ``created_at`` 保留天数）。
    本用例把一条**已经终结**的出站行做旧到 200 天前，再真跑一遍清理 —— 出站行确实被
    删掉（否则这条断言没有意义），而变更历史一条不少。
    """
    channel = _create_channel(api, name="门店群")
    _subscribe(api, "hygiene_reminder", channel["id"])
    before_purge = api.history()["total"]
    assert before_purge >= 2, "这条用例要拿真实的变更记录来对比"

    async def _run() -> dict:
        db = DatabaseManager()
        assert await db.connect()
        try:
            outbox = WeComOutbox()
            ids = await outbox.enqueue_topic(
                db,
                "hygiene_reminder",
                params={"text": "【卫生提醒】今天的漏拍汇总"},
                trigger=PushTrigger.EVENT,
                business_reference="2026-10-05",
            )
            assert ids
            await db.wecom_outbox_mark_skipped(ids[0], "测试用：直接终结这一行")
            conn = db.table("wecom_push_outbox").conn
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE wecom_push_outbox SET created_at = ? WHERE id = ?",
                    ((datetime.now(CHINA_TZ) - timedelta(days=200)).isoformat(), ids[0]),
                )
            await conn.commit()
            removed = await outbox.purge_expired(db, now=datetime.now(CHINA_TZ))
            remaining = await db.wecom_outbox_page()
            return {"removed": removed, "remaining": remaining["total"]}
        finally:
            await db.close()

    result = asyncio.run(_run())
    assert result["removed"] >= 1, "出站表的清理要真的跑起来，否则这条断言没有意义"
    assert result["remaining"] == 0

    rows = api.rows(object_type=CHANNELS)
    assert [row["action"] for row in rows] == ["create"]
    assert api.history()["total"] == before_purge, "配置变更历史不跟着发送记录一起清"


def test_the_audit_migration_is_additive_and_keeps_existing_rows(api, tmp_path, monkeypatch):
    """迁移 0019 只做加成性变更，且可重复执行 —— 重跑一次不能清掉已有记录。

    走**迁移面板的同一套逻辑**（``services.db_migrations``）：把仓库里真实的迁移目录
    指向临时目录、只放这一个脚本，于是执行路径与门店在 Admin 上点「应用」逐字一致。
    管理员重跑、或更新作业重复应用同一个脚本，都不该让历史归零。
    """
    import services.db_migrations as db_migrations

    repo_root = Path(__file__).resolve().parents[1]
    real_script = repo_root / "migrations" / "pg" / "0019_wecom_push_audit.sql"
    assert real_script.exists(), "票 11 的迁移脚本必须进仓库（发行包按 git archive 打包）"
    assert "DROP " not in real_script.read_text(encoding="utf-8").upper(), \
        "迁移只做加成性变更：不许有 DROP"

    package = tmp_path / "pg"
    package.mkdir()
    (package / "0019_wecom_push_audit.sql").write_text(
        real_script.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr(db_migrations, "MIGRATIONS_DIR", package)

    actor = "migration-idempotency-probe"

    async def _run() -> None:
        from database import get_db

        db = get_db()
        await db.wecom_audit_add({
            "actor": actor,
            "action": "create",
            "object_type": "wecom_push_webhooks",
            "object_name": "重跑之前的记录",
            "created_at": "2026-10-05T21:30:12+08:00",
        })
        # 第一条：在**真实 schema** 上重跑这个脚本（表已存在 → 只走 IF NOT EXISTS）。
        first = await db_migrations.apply_pending_migrations(db)
        assert first.ok is True, first.failed
        # 第二条：把这一份记录从迁移账本里撤掉，让它再应用一次（模拟重跑）。
        conn = db.table("wecom_push_audit").conn
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM schema_migrations WHERE version = '0019'"
            )
        await conn.commit()
        second = await db_migrations.apply_pending_migrations(db)
        assert second.ok is True, second.failed

        page = await db.wecom_audit_page(object_type="wecom_push_webhooks")
        kept = [row for row in page["rows"] if row["actor"] == actor]
        assert len(kept) == 1, f"重跑迁移把已有记录删掉了：{kept}"
        assert kept[0]["object_name"] == "重跑之前的记录"
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM wecom_push_audit WHERE actor = ?", (actor,)
            )
        await conn.commit()

    asyncio.run(_run())
