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

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from config import settings
from database import CHINA_TZ
from services.wecom_push_service import (
    WeComPushService,
    WeeklyScheduleRenderer,
    expand_messages,
    next_week_start,
    render_weekly_schedule_text,
    wecom_push_service,
)
from services.scheduling.week_view import KIND_UNSCHEDULED, cell_state
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
    """一份与 `SchedulingStore.week_grid` 同形的返回值（周一 2026-10-26 起 7 天）。

    日期用真的 `date` 递推，不是字符串拼 ``26+offset``：这一周跨月（10-26…11-01），
    拼出来的 ``2026-10-32`` 在图片渲染里会被 `date.fromisoformat` 判成非法日期。
    """
    from datetime import date, timedelta

    week_start = date(2026, 10, 26)
    days = []
    for offset, weekday in enumerate(range(7)):
        current = week_start + timedelta(days=offset)
        days.append({
            "business_date": current.isoformat(),
            "day": current.day,
            "month": current.month,
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
    from datetime import date, timedelta

    week_start = date(2026, 10, 26)
    cells = {}
    for offset in range(7):
        cells[(week_start + timedelta(days=offset)).isoformat()] = overrides.get(f"d{offset}") or {
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


def test_a_blank_shift_name_is_read_as_unscheduled_not_as_a_shift():
    """空字符串只可能是脏数据：按「上什么班」读会画出一个没有字的块/写出一句空话。"""
    state = cell_state({"scheduled": True, "shift_id": 1, "shift_name": "  "})

    assert state.kind == KIND_UNSCHEDULED


def test_the_image_gives_a_half_day_a_dot_instead_of_a_second_line_of_text():
    """列宽压到 104 才够清楚，「机动岗 · 半天」写不下：半天改画一个点，含义进页脚。"""
    import io

    from PIL import Image

    from services.wecom_schedule_image import MIN_DAY_WIDTH, render_weekly_schedule_png

    grid = _grid([
        {
            "id": 1,
            "name": "张三",
            "job_title": "",
            "has_rule": True,
            "cells": _cells(**{
                "d0": {
                    "scheduled": True, "shift_id": 1, "shift_name": "白班",
                    "zone_name": "凉菜间", "half_rest": True,
                },
            }),
        },
    ])
    data = render_weekly_schedule_png(grid, generated_at=datetime(2026, 10, 31, 13, 0))

    assert data is not None
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # 列宽就是清晰度的分母（企业微信按宽度缩到手机屏宽），下限不能被悄悄改回去。
    width, _ = Image.open(io.BytesIO(data)).size
    assert width >= 7 * MIN_DAY_WIDTH


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


def _weekly_job(weekday: Optional[int] = 6) -> Dict[str, Any]:
    return {
        "id": 1,
        "topic_id": "weekly_schedule",
        "params_json": "{}",
        "schedule_time": "13:00",
        "slots_json": json.dumps([{"weekday": weekday, "time": "13:00"}]),
    }


def _due(api_job: Dict[str, Any], business_date: str, minute: str = "13:00") -> List[Any]:
    return WeComPushService._due_slots(
        api_job, date.fromisoformat(business_date), minute, business_date=business_date
    )


@pytest.mark.parametrize(
    "business_date,should_fire",
    [
        ("2026-10-25", True),   # 周日
        ("2026-10-26", False),  # 周一
        ("2026-10-31", False),  # 周六
    ],
)
def test_a_weekly_job_only_fires_on_its_weekday(business_date, should_fire):
    assert bool(_due(_weekly_job(), business_date)) is should_fire


def test_a_daily_topic_fires_every_day():
    job = {
        "id": 2,
        "topic_id": "sales_report",
        "params_json": "{}",
        "schedule_time": "21:30",
        "slots_json": "[]",
    }
    for business_date in ("2026-10-25", "2026-10-26", "2026-10-31"):
        assert bool(_due(job, business_date, "21:30")) is True


def test_a_weekly_slot_without_a_weekday_does_not_fire():
    """周任务的点没有星期（坏数据）宁可不发：按「每天」发会变成每天一张排班图。"""
    job = _weekly_job(weekday=None)
    assert _due(job, "2026-10-25") == []


def test_a_broken_slot_is_treated_as_no_trigger():
    """触发点解析不出来时这条任务当天跳过，而不是把整个调度循环带崩。"""
    job = {
        "id": 3,
        "topic_id": "weekly_schedule",
        "params_json": "{}",
        "schedule_time": "13:00",
        "slots_json": json.dumps([{"weekday": 9, "time": "13:00"}]),
    }
    assert _due(job, "2026-10-25") == []


def test_the_registry_marks_the_topic_weekly_and_defaults_to_sunday_1300():
    topic = get_topic("weekly_schedule")

    assert topic is not None
    assert topic.weekly is True
    assert topic.payload()["weekly"] is True
    # 时间与星期都不再进参数模型：它们是任务的触发点（slots），不是内容参数。
    assert topic.default_schedule_time == "13:00"
    assert topic.default_weekday == 6
    assert topic.validate_params({}, trigger=None) == {}


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
        {},
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
            "slots": [{"weekday": 6, "time": "13:00"}],
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
    sent_images: List[bytes] = []

    class FakeSender:
        async def send_text(self, webhook_url: str, content: str):
            sent.append(content)
            return True, "ok"

        async def send_image(self, webhook_url: str, image_bytes: bytes):
            # 排班表现在发的是图（用户要求「改成截图」）：只记 send_text 的假发送器
            # 会让这条链路看起来没发出去。两种都收下，本机有中文字体就走图片分支。
            sent_images.append(image_bytes)
            return True, "ok"

    original = wecom_outbox._sender
    wecom_outbox._sender = FakeSender()
    try:
        api.client.portal.call(_dispatch_pending, db, 20)
    finally:
        wecom_outbox._sender = original

    from services.cjk_font import load_cjk_font

    assert sent or sent_images, "出站没有发出任何一条"
    # 本机有中文字体时**必须**发图（用户要的就是截图）；没有才允许退文字。
    if load_cjk_font(20) is not None:
        assert sent_images, "有中文字体却发了文字版"
        assert not sent
    if sent_images:
        # 图片是投递的正常形态（浅色排班图，PNG 尺寸远小于 2MB）。
        assert sent_images[0][:8] == b"\x89PNG\r\n\x1a\n"
    else:
        # 没有中文字体的机器上会退回文字版，这条分支也要成立。
        assert "【下周排班】" in sent[0]
    rows = _outbox_rows(api, db)
    assert rows == []


# ── 图片版：发到群里的是**一张图**（用户 2026-11-01 要求「改成截图，不要文字」）──


def _two_people_grid() -> Dict[str, Any]:
    return _grid([
        {"id": 1, "name": "张三", "job_title": "厨师", "has_rule": True, "cells": _cells()},
        {
            "id": 2,
            "name": "李四",
            "job_title": "服务员",
            "has_rule": True,
            "cells": _cells(
                d5={"scheduled": True, "shift_id": 2, "shift_name": "夜班", "zone_id": None,
                    "zone_name": None, "overridden": False, "half_rest": False, "leave": False},
                d6={"scheduled": True, "shift_id": None, "shift_name": None, "zone_id": None,
                    "zone_name": None, "overridden": False, "half_rest": False, "leave": False},
            ),
        },
    ])


def test_the_weekly_schedule_renders_a_png():
    """真画一张图：PNG 头、尺寸够放下 7 天（不是一张空图）。

    本机没有中文字体时函数按契约返回 ``None``（调用方退文字）—— 那时这条用例跳过，
    因为「画不出来」不是代码错，而是这台机器没有字体。
    """
    import io

    from PIL import Image

    from services.wecom_schedule_image import MIN_DAY_WIDTH, render_weekly_schedule_png

    png = render_weekly_schedule_png(_two_people_grid())
    if png is None:
        pytest.skip("本机没有可用中文字体")

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    image = Image.open(io.BytesIO(png))
    # 7 天列 + 姓名列：至少 7 个最小日列宽，否则表头放不下
    assert image.width >= 7 * MIN_DAY_WIDTH
    # 2 人：标题 + 表头 + 2 行 + 页脚
    assert image.height > 2 * 64


def test_the_image_renderer_degrades_to_none_without_a_cjk_font(monkeypatch):
    """没有中文字体时返回 None（不能画一排方块当图发出去）。"""
    import services.wecom_schedule_image as image_module

    monkeypatch.setattr(image_module, "load_cjk_font", lambda size: None)
    assert image_module.render_weekly_schedule_png(_two_people_grid()) is None


def test_the_delivery_falls_back_to_text_when_drawing_the_image_raises(monkeypatch, api):
    """出图抛异常不能把当天这条推送丢掉：退文字版照样发。"""
    import asyncio

    import services.wecom_schedule_image as image_module
    from services.app_runtime import get_runtime

    def boom(grid, **kwargs):
        raise RuntimeError("画不出来")

    monkeypatch.setattr(image_module, "render_weekly_schedule_png", boom)
    db = get_runtime().db

    delivery = asyncio.run(wecom_push_service.render_job_delivery(
        db, {"topic_id": "weekly_schedule", "params_json": "{}"}
    ))

    assert delivery.is_image is False
    assert "【下周排班】" in delivery.text


def test_the_preview_endpoint_returns_the_image_as_a_data_url(api):
    """预览与真发同一条路径：图片类是 `kind=image` + data URL，不是一段空文字。"""
    job_id = _setup_weekly_job(api)
    resp = api.request("POST", f"{API_PREFIX}/jobs/{job_id}/preview")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    from services.cjk_font import load_cjk_font

    assert data["kind"] in {"image", "text"}
    # 有中文字体的机器上预览必须是图：文字版只是降级路径。
    if load_cjk_font(20) is not None:
        assert data["kind"] == "image", data
    if data["kind"] == "image":
        assert data["image_data_url"].startswith("data:image/png;base64,")
        assert data["content"] == ""
        assert data["chunk_count"] == 1
        assert data["limit"] == 2 * 1024 * 1024
    else:
        # 没有中文字体的机器：退文字版，预览仍要能看到正文。
        assert data["content"]
        assert data["image_data_url"] == ""


# ── 手动推送：周次归一、标题、署名（周表页的「推送到企微」）───────────────────
#
# 手动推送复用「下周排班表」这一条出站，只是由人指定周次：同一批订阅、同一个渲染器。
# 下面两层分别钉住「周次怎么归一 / 图长什么样」与「点下去那一发落到出站里是什么样」。


@pytest.mark.parametrize(
    "day,expected",
    [
        ("2026-11-18", "2026-11-16"),  # 周三 → 那周一
        ("2026-11-16", "2026-11-16"),  # 周一 → 自己
        ("2026-11-22", "2026-11-16"),  # 周日 → 那周一（不是下周一）
        ("2026-11-23", "2026-11-23"),
    ],
)
def test_week_start_of_snaps_any_day_to_its_monday(day, expected):
    from services.wecom_push_service import week_start_of

    assert week_start_of(day) == expected


@pytest.mark.parametrize(
    "week_start,expected",
    [
        ("2026-11-16", "本周排班"),
        ("2026-11-23", "下周排班"),
        ("2026-11-09", "11/9 – 11/15 排班"),
        ("2026-11-30", "11/30 – 12/6 排班"),
    ],
)
def test_the_manual_title_is_read_against_today(week_start, expected):
    from datetime import date

    from services.wecom_push_service import weekly_schedule_title

    # 2026-11-22 是周日：本周 11-16，下周 11-23；更远的周写日期范围。
    assert weekly_schedule_title(week_start, today=date(2026, 11, 22)) == expected


def test_the_manual_image_titles_the_week_and_signs_the_pusher():
    """手动推送那一发：标题写的是这一周，页脚右侧多一句「由谁推送」。

    定时任务那张图一字不改（同一个渲染器），所以这里比的是**同一份数据**下两张图：
    宽高一样、字节不一样 —— 只换了字，没有改版。
    """
    import io

    from PIL import Image

    from services.wecom_schedule_image import render_weekly_schedule_png

    grid = _two_people_grid()
    moment = datetime(2026, 11, 22, 13, 20)
    plain = render_weekly_schedule_png(grid, generated_at=moment)
    if plain is None:
        pytest.skip("本机没有可用中文字体")
    signed = render_weekly_schedule_png(
        grid, generated_at=moment, title="11/9 – 11/15 排班", pushed_by="张三"
    )

    assert signed is not None
    assert signed[:8] == b"\x89PNG\r\n\x1a\n"
    assert signed != plain
    assert Image.open(io.BytesIO(signed)).size == Image.open(io.BytesIO(plain)).size


class _FakeWeeklyService:
    """只记「渲染器要了哪一周」，不真连库。"""

    def __init__(self, image: Any = b"\x89PNG_fake") -> None:
        self.image = image
        self.calls: List[Dict[str, Any]] = []

    async def build_weekly_schedule_image(self, db, **kwargs):
        self.calls.append(kwargs)
        return self.image


def test_the_renderer_draws_the_week_the_operator_picked():
    import asyncio

    fake = _FakeWeeklyService()
    renderer = WeeklyScheduleRenderer(db=None, service=fake)

    delivery = asyncio.run(renderer.render(
        {"created_at": "2026-11-22T13:20:00+08:00", "topic_id": "weekly_schedule"},
        {
            "week_start": "2026-11-16",
            "title": "本周排班",
            "operator_name": "张三",
            "manual": True,
        },
    ))

    assert delivery.image_bytes == b"\x89PNG_fake"
    assert fake.calls == [
        {"week_start": "2026-11-16", "title": "本周排班", "pushed_by": "张三"}
    ]


def test_a_manual_push_fails_instead_of_sending_text_without_a_font():
    """缺字体不能退文字：这一发点的是「发图」，一段文字不是他要的，也说不出是哪一周。"""
    import asyncio

    renderer = WeeklyScheduleRenderer(db=None, service=_FakeWeeklyService(image=None))

    with pytest.raises(ValueError, match="中文字体"):
        asyncio.run(renderer.render(
            {"created_at": "2026-11-22T13:20:00+08:00", "topic_id": "weekly_schedule"},
            {"week_start": "2026-11-16", "manual": True},
        ))


# ── 真 app：预览 → 点下去 → 出站行 → 周表页那行状态 ────────────────────────


def _subscribe_weekly_topic(api: WecomAdmin) -> int:
    """建一个渠道并订阅「下周排班表」—— 手动推送的收件人就是这批订阅。"""
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
    return channel_id


async def _recent_weekly_rows(db):
    return await db.wecom_outbox_recent(limit=10, topic_id="weekly_schedule")


def test_a_manual_push_queues_one_row_per_group_and_shows_it_on_the_week_page(api):
    _subscribe_weekly_topic(api)

    preview = api.request(
        "GET",
        "/api/scheduling/weekly-schedule/push-preview",
        params={"week_start": "2026-11-18"},
    )
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["week_start"] == "2026-11-16"
    assert data["week_end"] == "2026-11-22"
    assert data["title"]
    assert data["operator"]["name"] == ADMIN_USERNAME
    assert [item["name"] for item in data["targets"]] == ["门店群"]

    pushed = api.request(
        "POST", "/api/scheduling/weekly-schedule/push", json={"week_start": "2026-11-18"}
    )
    assert pushed.status_code == 200, pushed.text
    body = pushed.json()
    assert body["queued"] == 1
    assert body["week_start"] == "2026-11-16"

    from services.app_runtime import get_runtime
    from services.wecom_outbox import parse_params

    rows = api.client.portal.call(_recent_weekly_rows, get_runtime().db)
    assert len(rows) == 1
    params = parse_params(rows[0])
    assert params["manual"] is True
    assert params["week_start"] == "2026-11-16"
    assert params["operator_name"] == ADMIN_USERNAME
    assert "手动推送" in rows[0]["content_summary"]
    assert rows[0]["status"] == "pending"

    last = api.request("GET", "/api/scheduling/weekly-schedule/last-push")
    assert last.status_code == 200, last.text
    state = last.json()["last"]
    assert state["total"] == 1
    assert state["status"] == "pending"
    assert state["week_start"] == "2026-11-16"


def test_a_manual_push_without_any_subscription_is_a_400(api):
    """零订阅要说没发出去：点按钮的人要的是「发出去了」，静默成功等于骗他。"""
    resp = api.request(
        "POST", "/api/scheduling/weekly-schedule/push", json={"week_start": "2026-11-16"}
    )
    assert resp.status_code == 400, resp.text
    assert "企微推送页" in resp.json()["detail"]


def test_last_push_is_empty_before_the_first_manual_push(api):
    resp = api.request("GET", "/api/scheduling/weekly-schedule/last-push")
    assert resp.status_code == 200, resp.text
    assert resp.json()["last"] is None
