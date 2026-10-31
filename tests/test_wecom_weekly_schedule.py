#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「下周排班表」这一类推送（每周日 13:00 把下周排班推到企微群）。

缝隙（spec「Testing Decisions」第 3 条：内容类型注册表 + 出站渲染）：
正文渲染、每周触发的判定、以及「补发不跨周」这三件事各测一层 ——

* ``render_weekly_schedule_text`` 是纯函数：给它一份 ``week_grid`` 形状的数据，断言
  群里看到的那几行（休 / 请假 / 未排 / 半天 / 工作区都要分得开）；
* ``dispatch_due_jobs`` 在真 app 上跑：周日到点入队一次、同一天不重复、周一不触发；
* ``WeeklyScheduleRenderer`` 用出站行的 ``created_at`` 冻结营业日：周日那条失败后
  周一补发，仍然算「下一周」而不是再下一周的再下一周。

不测 SQL 形状，也不测内部函数调用顺序。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from config import settings
from database import CHINA_TZ
from services.wecom_push_service import (
    WeeklyScheduleRenderer,
    expand_messages,
    next_week_start,
    render_weekly_schedule_text,
    wecom_push_service,
)
from services.wecom_push_topics import get_topic

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "password123"
API_PREFIX = "/api/wecom-push"
WEBHOOK_URL = (
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
    "?key=693a91f6-7aoc-4bc4-97a0-0ec2sifa5aaa"
)


# ── 纯函数：下一周的起点 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "business_date,expected",
    [
        # 2026-10-25 是周日：推的是明天开始的那一周
        ("2026-10-25", "2026-10-26"),
        # 同周任何一天都指向同一个「下周一」
        ("2026-10-19", "2026-10-26"),  # 周一
        ("2026-10-21", "2026-10-26"),  # 周三
        ("2026-10-24", "2026-10-26"),  # 周六
        # 跨年：2026-12-28 周一 → 2027-01-04
        ("2026-12-28", "2027-01-04"),
    ],
)
def test_next_week_start_is_always_the_next_monday(business_date, expected):
    assert next_week_start(business_date) == expected


# ── 纯函数：正文排版 ────────────────────────────────────────────────────────


def _grid(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """一份与 `SchedulingStore.week_grid` 同形的返回值（周一 2026-10-26 起 7 天）。"""
    days = []
    for offset, weekday in enumerate(range(7)):
        day = 26 + offset
        days.append({
            "business_date": f"2026-10-{day:02d}",
            "day": day,
            "month": 10,
            "weekday": weekday,
            "is_today": False,
            "is_weekend": weekday >= 5,
            "in_window": True,
            "row_count": len(rows),
        })
    return {
        "start": "2026-10-26",
        "end": "2026-11-01",
        "today": "2026-10-25",
        "window_end": "2027-01-23",
        "spans_months": True,
        "days": days,
        "shifts": [
            {"id": 1, "name": "白班", "sort_order": 10},
            {"id": 2, "name": "夜班", "sort_order": 20},
        ],
        "employees": rows,
        "excluded": {"resigned": 0, "pending": 0},
    }


def _cells(**overrides: Dict[str, Any]) -> Dict[str, Any]:
    """7 个日期键都在（与 week_grid 的契约一致）。"""
    cells = {}
    for offset in range(7):
        cells[f"2026-10-{26 + offset:02d}"] = overrides.get(f"d{offset}") or {
            "scheduled": True,
            "shift_id": 1,
            "shift_name": "白班",
            "zone_id": None,
            "zone_name": None,
            "overridden": False,
            "half_rest": False,
            "leave": False,
        }
    return cells


def test_the_message_names_the_week_and_one_line_per_person():
    content = render_weekly_schedule_text(_grid([
        {"id": 1, "name": "张三", "job_title": "厨师", "has_rule": True, "cells": _cells()},
        {
            "id": 2,
            "name": "李四",
            "job_title": "帮厨",
            "has_rule": True,
            "cells": _cells(**{"d0": {"scheduled": True, "shift_id": 2, "shift_name": "夜班"}}),
        },
    ])).content

    lines = content.splitlines()
    assert lines[0] == "【下周排班】10/26（周一） - 11/1（周日）"
    assert lines[1] == ""
    # 七天一个班次（固定班最常见）：合并成一句「每天」，不再逐日重复七遍
    assert lines[2] == "张三：每天 白班"
    # 只有周一变了：连续相同的两天以上合并成一段，单天写星期几
    assert lines[3] == "李四：一 夜班 · 二~日 白班"


def test_rest_leave_half_rest_and_unscheduled_read_differently():
    content = render_weekly_schedule_text(_grid([
        {
            "id": 1,
            "name": "张三",
            "job_title": "",
            "has_rule": True,
            "cells": _cells(**{
                # 有这一行但没班次 = 休
                "d0": {"scheduled": True, "shift_id": None, "shift_name": None},
                # 没班次 + 请假 = 请假（不是休）
                "d1": {
                    "scheduled": True, "shift_id": None, "shift_name": None, "leave": True,
                },
                # 这一行还没写 = 未排（不能说成休）
                "d2": {"scheduled": False, "shift_id": None, "shift_name": None},
                # 半天休照旧写班次，多一句「（半天）」
                "d3": {"scheduled": True, "shift_id": 1, "shift_name": "白班", "half_rest": True},
                # 排了工作区就带上
                "d4": {"scheduled": True, "shift_id": 1, "shift_name": "白班", "zone_name": "凉菜间"},
            }),
        },
    ])).content

    assert "一 休 · 二 请假 · 三 未排 · 四 白班（半天） · 五 白班·凉菜间 · 六~日 白班" in content


def test_no_employees_still_says_something():
    content = render_weekly_schedule_text(_grid([])).content

    assert "这一周还没有排班" in content


def test_long_weeks_split_on_person_boundaries():
    """超 2048 字节时按整行切：不会把某个人切成两半。"""
    rows = [
        {
            "id": index,
            "name": f"员工{index:02d}",
            "job_title": "",
            "has_rule": True,
            # 每天一个不同的工作区：一行写满七段，60 人一定超 2048 字节
            "cells": _cells(**{
                f"d{offset}": {
                    "scheduled": True,
                    "shift_id": 1,
                    "shift_name": "白班",
                    "zone_name": f"工作区{index:02d}{offset}",
                }
                for offset in range(7)
            }),
        }
        for index in range(1, 61)
    ]
    message = render_weekly_schedule_text(_grid(rows))

    assert message.byte_length > 2048
    chunks = expand_messages([message.content])
    assert len(chunks) > 1
    # 每一段都能独立成句：首段有标题，其余段的第一行是完整的一行（人名开头）
    assert chunks[0].startswith("【下周排班】")
    for chunk in chunks[1:]:
        for line in chunk.splitlines():
            assert line == "" or "：" in line or line.startswith("员工")


# ── 每周触发的判定（纯函数） ────────────────────────────────────────────────


def _weekly_job(weekday: str = "6") -> Dict[str, Any]:
    return {
        "id": 1,
        "topic_id": "weekly_schedule",
        "params_json": f'{{"schedule_time": "13:00", "weekday": "{weekday}"}}',
        "schedule_time": "13:00",
    }


@pytest.mark.parametrize(
    "business_date,should_fire",
    [
        ("2026-10-25", True),   # 周日
        ("2026-10-26", False),  # 周一
        ("2026-10-31", False),  # 周六
    ],
)
def test_a_weekly_job_only_fires_on_its_weekday(business_date, should_fire):
    assert wecom_push_service._weekly_due(_weekly_job(), business_date) is should_fire


def test_a_daily_topic_fires_every_day():
    job = {
        "id": 2,
        "topic_id": "sales_report",
        "params_json": '{"schedule_time": "21:30", "date_range_mode": "today", "station": ""}',
        "schedule_time": "21:30",
    }
    for business_date in ("2026-10-25", "2026-10-26", "2026-10-31"):
        assert wecom_push_service._weekly_due(job, business_date) is True


@pytest.mark.parametrize("params_json", ['{}', '{"weekday": "x"}', '{"weekday": "9"}'])
def test_a_broken_weekday_does_not_fire(params_json):
    """参数坏了宁可不发（记警告）：默认成周日会悄悄每周发一次。"""
    job = {
        "id": 3,
        "topic_id": "weekly_schedule",
        "params_json": params_json,
        "schedule_time": "13:00",
    }
    assert wecom_push_service._weekly_due(job, "2026-10-25") is False


def test_the_registry_marks_the_topic_weekly_and_defaults_to_sunday_1300():
    topic = get_topic("weekly_schedule")

    assert topic is not None
    assert topic.weekly is True
    assert topic.payload()["weekly"] is True
    params = topic.validate_params({}, trigger=None)
    assert params["weekday"] == "6"
    assert params["schedule_time"] == "13:00"


# ── 渲染器：补发不跨周 ──────────────────────────────────────────────────────


class _FakeService:
    def __init__(self) -> None:
        self.business_dates: List[str] = []

    async def build_weekly_schedule_message(self, db, *, business_date: str = "", now=None):
        self.business_dates.append(business_date)
        from services.wecom_push_service import RenderedMessage

        return RenderedMessage(content="x", byte_length=1)


def test_the_renderer_freezes_the_business_day_on_the_outbox_row():
    """出站行是周日建的、周一才补发：仍然按**周日**算「下一周」。"""
    import asyncio

    fake = _FakeService()
    renderer = WeeklyScheduleRenderer(db=None, service=fake)

    delivery = asyncio.run(renderer.render(
        {"created_at": "2026-10-25T13:00:05+08:00", "topic_id": "weekly_schedule"},
        {"schedule_time": "13:00", "weekday": "6"},
    ))

    assert delivery.text == "x"
    assert fake.business_dates == ["2026-10-25"]


# ── 真 app：到点入队、同一天不重复、其它日子不触发 ──────────────────────────


class WecomAdmin:
    def __init__(self, client: TestClient, session_id: str) -> None:
        self.client = client
        self.session_id = session_id

    def request(self, method: str, path: str, **kwargs):
        self.client.cookies.set(settings.SESSION_COOKIE_NAME, self.session_id)
        return self.client.request(method, path, **kwargs)


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
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD, "remember": False},
        )
        assert login.status_code == 200, login.text
        session_id = client.cookies.get(settings.SESSION_COOKIE_NAME)
        assert session_id
        yield WecomAdmin(client, session_id)
    settings.DATABASE_DIR = old


def _setup_weekly_job(api: WecomAdmin) -> int:
    """建渠道 → 订阅「下周排班表」→ 建一条每周日 13:00 的任务，返回任务 id。"""
    resp = api.request(
        "POST",
        f"{API_PREFIX}/webhooks",
        json={"name": "门店群", "webhook_url": WEBHOOK_URL, "enabled": True, "notes": ""},
    )
    assert resp.status_code == 200, resp.text
    channel_id = int(resp.json()["channel"]["id"])
    resp = api.request(
        "POST",
        f"{API_PREFIX}/subscriptions",
        json={"topic_id": "weekly_schedule", "target_channel_id": channel_id, "enabled": True},
    )
    assert resp.status_code == 200, resp.text
    resp = api.request(
        "POST",
        f"{API_PREFIX}/jobs",
        json={
            "name": "下周排班表",
            "topic_id": "weekly_schedule",
            "params": {"schedule_time": "13:00", "weekday": "6"},
            "schedule_time": "13:00",
            "enabled": True,
            "notes": "",
        },
    )
    assert resp.status_code == 200, resp.text
    job = resp.json()["job"]
    # 卡片上那枚时间徽章：周任务要写出「每周日」，只写 13:00 会被当成每天推
    assert job["schedule_text"] == "每周日 13:00"
    return int(job["id"])


async def _fetch_pending(db, now_iso: str) -> List[Dict[str, Any]]:
    return await db.wecom_outbox_pending(limit=50, now=now_iso)


async def _dispatch_once(db, when: datetime) -> int:
    return await wecom_push_service.dispatch_due_jobs(db, now=when)


def _dispatch(api: WecomAdmin, db, when: datetime) -> int:
    """在 app 自己的事件循环里跑一轮调度（连接绑在它的 loop 上，不能另起一个 loop）。"""
    return int(api.client.portal.call(_dispatch_once, db, when))


def _outbox_rows(api: WecomAdmin, db) -> List[Dict[str, Any]]:
    return list(api.client.portal.call(
        _fetch_pending, db, "2099-01-01T00:00:00+08:00"
    ))


def test_the_sunday_job_enqueues_once_and_not_on_other_days(api):
    _setup_weekly_job(api)
    from services.app_runtime import get_runtime

    db = get_runtime().db

    # 周六同一时刻：不触发
    assert _dispatch(api, db, datetime(2026, 10, 24, 13, 0, tzinfo=CHINA_TZ)) == 0
    # 周日 13:00：入队一次
    assert _dispatch(api, db, datetime(2026, 10, 25, 13, 0, tzinfo=CHINA_TZ)) == 1
    # 同一分钟的下一轮：last_sent_date 已记，不重复
    assert _dispatch(api, db, datetime(2026, 10, 25, 13, 0, tzinfo=CHINA_TZ)) == 0
    # 下一个周日再来一次
    assert _dispatch(api, db, datetime(2026, 11, 1, 13, 0, tzinfo=CHINA_TZ)) == 1

    rows = _outbox_rows(api, db)
    assert len(rows) == 2
    assert {row["topic_id"] for row in rows} == {"weekly_schedule"}


async def _dispatch_pending(db, limit: int) -> int:
    from services.wecom_outbox import wecom_outbox

    return await wecom_outbox.dispatch_pending(db, limit=limit)


def test_the_queued_row_renders_and_sends_through_the_outbox(api):
    """入队 → 出站渲染（注册在 lifespan 里）→ 发送这一整条链路。

    渲染器没登记的话，这一行会以「正文渲染失败」落终态且不重试（出站对渲染失败的处理），
    群里永远不会收到排班表 —— 所以这里用假发送器把整条路走通一次。
    """
    _setup_weekly_job(api)
    from services.app_runtime import get_runtime
    from services.wecom_outbox import wecom_outbox

    db = get_runtime().db
    assert _dispatch(api, db, datetime(2026, 10, 25, 13, 0, tzinfo=CHINA_TZ)) == 1

    sent: List[str] = []

    class FakeSender:
        async def send_text(self, webhook_url: str, content: str):
            sent.append(content)
            return True, "ok"

    original = wecom_outbox._sender
    wecom_outbox._sender = FakeSender()
    try:
        api.client.portal.call(_dispatch_pending, db, 20)
    finally:
        wecom_outbox._sender = original

    assert sent, "出站没有发出任何一条"
    assert "【下周排班】" in sent[0]
    rows = _outbox_rows(api, db)
    assert rows == []
