#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""隔夜验收：06:00 切日之后，昨晚交的照片仍要能看见、能验。

审查发现两条「隔夜就验不了卫生」的路径：

* ``/admin/daily-queue`` 不带日期只回**今天**待验收，``accept_daily`` 又把营业日写死成
  ``hygiene_business_date(now)`` —— 昨晚交的照片今早既看不到也点不了通过。现在
  ``pending_past`` 给出过去几天里还没验的行，验收/驳回可以带 ``business_date`` 补验，
  下界是 ``DAILY_BACKFILL_DAYS``。
* 驳回原因按营业日查，跨天之后整改单（状态仍是「待回拍」）的原因就查不到了。现在整改
  的原因按 ticket_id 取最近一条（整改单状态跨天不变，这条不受营业日影响）；**日常与
  专项的原因仍按营业日**——专项的实例是按营业日建的，上周的驳回不能算到本周的新实例上
  （2026-10 审查 repro_hyg_1 第 2 条）。

时钟是注入的：上周二建/驳回专项、昨天建/驳回整改单、今天来看。
"""

import asyncio
import io
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.business_day import shift_business_date
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import (
    DAILY_BACKFILL_DAYS,
    EVENT_FIRST_PASS,
    HygieneWork,
    HygieneWorkError,
)

SUPER = {"kind": "super"}
PHONE = "13800138000"
QUEUE = "/api/hygiene/admin/daily-queue"
DAILY = "/api/hygiene/admin/daily"

# 2026-09-15 是周二；09-14 周一；09-08 上周二（与今天同一个 weekday，专项项能对上）。
LAST_WEEK_MOMENT = datetime(2026, 9, 8, 10, 0, tzinfo=CHINA_TZ)
YESTERDAY_MOMENT = datetime(2026, 9, 14, 10, 0, tzinfo=CHINA_TZ)
TODAY_MOMENT = datetime(2026, 9, 15, 10, 0, tzinfo=CHINA_TZ)
YESTERDAY = "2026-09-14"
TODAY = "2026-09-15"


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _jpeg():
    output = io.BytesIO()
    Image.new("RGB", (80, 60), (120, 180, 220)).save(output, format="JPEG")
    return output.getvalue()


def _live():
    return {"bytes": _jpeg(), "content_type": "image/jpeg", "live": True, "markup": []}


def _standard():
    return {"bytes": _jpeg(), "content_type": "image/jpeg", "markup": []}


class _Clock:
    """可拨动的注入时钟：一个 fixture 里要走完三个营业日。"""

    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment


@pytest.fixture
def env(tmp_path):
    old_dir = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    clock = _Clock(LAST_WEEK_MOMENT)
    accounts = EmployeeAccounts(db, now=clock)
    work = HygieneWork(
        db,
        captures=FakeCaptureStore(),
        now=clock,
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    zone = _run(work.list_zones())[0]
    actor = {
        "kind": "staff",
        "id": 1,
        "permission": "普通员工",
        "name": "张三",
        "phone": PHONE,
        "shift": "白班",
        "zone_id": zone["id"],
    }

    # 上周二：专项被驳回。本周二（同一 weekday）仍要看到原因。
    deep_item = _run(work.add_deep_clean_item(SUPER, 1, "抽油烟机"))
    _run(work.submit_deep_clean_pair(actor, deep_item["id"], _live(), _live()))
    _run(
        work.reject_deep_clean_pair(
            SUPER, deep_item["id"], reason="滤网没拆下来洗"
        )
    )

    # 昨天（周一）：交了一张日常、开了一张整改单并在昨天驳回。
    clock.moment = YESTERDAY_MOMENT
    yesterday_item = _run(work.add_daily_item(SUPER, zone["id"], "案板-台面", _standard()))
    _run(work.submit_daily(actor, yesterday_item["id"], _live()))
    opener = {**actor, "permission": "管理员", "caps": ["fix"]}
    ticket = _run(
        work.open_fix(
            opener,
            zone["id"],
            "卫生",
            "台面有油",
            timedelta(hours=2),
            _live(),
        )
    )
    _run(work.reshoot_fix(actor, ticket["id"], _live()))
    _run(work.reject_fix(opener, ticket["id"], reason="回拍角度不对"))

    # 今天（周二）：再交一张，今天这行该进 `items`，昨天的进 `pending_past`。
    clock.moment = TODAY_MOMENT
    today_item = _run(work.add_daily_item(SUPER, zone["id"], "案板-地面", _standard()))
    _run(work.submit_daily(actor, today_item["id"], _live()))

    yield SimpleNamespace(
        db=db,
        work=work,
        clock=clock,
        accounts=accounts,
        zone=zone,
        actor=actor,
        open_fix_actor=opener,
        yesterday_item=yesterday_item,
        today_item=today_item,
        deep_item=deep_item,
        ticket=ticket,
    )

    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old_dir


@pytest.fixture
def http(env):
    app = FastAPI()
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_work] = lambda: env.work
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: env.accounts
    app.dependency_overrides[hygiene_module.require_session] = lambda: "test-admin"
    with TestClient(app) as client:
        yield client


def test_yesterday_pending_shows_in_pending_past_and_can_be_accepted(env, http):
    queue = http.get(QUEUE)
    assert queue.status_code == 200, queue.text
    payload = queue.json()
    # 今天的待验收仍在 items 里，形状不变。
    assert payload["date"] == TODAY
    assert [row["item_id"] for row in payload["items"]] == [env.today_item["id"]]
    # 昨天那张在 pending_past 里，带 business_date。
    assert [row["business_date"] for row in payload["pending_past"]] == [YESTERDAY]
    past_row = payload["pending_past"][0]
    assert past_row["item_id"] == env.yesterday_item["id"]
    assert past_row["status"] == "待验收"

    accepted = http.post(
        f"{DAILY}/{env.yesterday_item['id']}/accept",
        json={"shift": "白班", "business_date": YESTERDAY},
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["status"] == "已通过"
    assert body["business_date"] == YESTERDAY

    after = http.get(QUEUE).json()
    assert after["pending_past"] == []
    assert [row["item_id"] for row in after["items"]] == [env.today_item["id"]]


def test_accept_without_business_date_keeps_today_only(env, http):
    """不传业务日的默认行为逐字不变：只认今天，昨天的实例看都不看。"""
    response = http.post(
        f"{DAILY}/{env.yesterday_item['id']}/accept", json={"shift": "白班"}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "没有待验收的实拍"


def test_past_day_below_the_backfill_floor_is_rejected(env, http):
    work = env.work
    too_old = shift_business_date(TODAY, -(DAILY_BACKFILL_DAYS + 1))
    with pytest.raises(HygieneWorkError) as raised:
        _run(
            work.accept_daily(
                SUPER, env.yesterday_item["id"], "白班", business_date=too_old
            )
        )
    assert raised.value.code == "past_day"

    # 下界内（today - 7）只是「那天没有待验收」，不是越界 —— 两件事不能混成一句。
    boundary = shift_business_date(TODAY, -DAILY_BACKFILL_DAYS)
    with pytest.raises(HygieneWorkError) as raised:
        _run(
            work.accept_daily(
                SUPER, env.yesterday_item["id"], "白班", business_date=boundary
            )
        )
    assert raised.value.code == "not_pending"

    # HTTP 侧走同一套错误映射：400 + 中文（不回显内部 code）。
    response = http.post(
        f"{DAILY}/{env.yesterday_item['id']}/accept",
        json={"shift": "白班", "business_date": too_old},
    )
    assert response.status_code == 400
    assert "7 天" in response.json()["detail"]


def test_list_pending_daily_is_newest_first(env):
    rows = _run(env.work.list_pending_daily(SUPER))
    assert [row["business_date"] for row in rows] == [TODAY, YESTERDAY]
    assert [row["item_id"] for row in rows] == [
        env.today_item["id"],
        env.yesterday_item["id"],
    ]
    assert all(row["status"] == "待验收" for row in rows)


def test_fix_reject_reason_survives_the_business_day_rollover(env):
    """整改单昨天被驳回、跨天仍是「待回拍」：原因要能在今天查到。"""
    rows = _run(env.work.list_fix_tickets(SUPER))
    row = next(item for item in rows if item["id"] == env.ticket["id"])
    assert row["status"] == "待回拍"
    assert row["rejected"] is True
    assert row["reject_reason"] == "回拍角度不对"


def test_deep_reject_reason_does_not_leak_into_the_next_week(env):
    """专项上周二被驳回：本周二同一项是**全新 instance**，不能顶着上周的原因。

    驳回原因只属于被驳回的那个营业日实例（``hygiene_deep_clean_instances`` 的键是
    business_date + item_id）。不加营业日过滤的话"历史上被驳回过"会永久为真，员工端
    会对着本周全新的待拍项渲染「已驳回：<上周的原因>」（2026-10 审查 repro_hyg_1
    第 2 条）。同一天内的原因仍然可见，见
    ``test_hygiene_reject_visibility.test_deep_clean_reject_is_visible_to_staff``。
    """
    items = _run(env.work.list_deep_clean_work(SUPER))["items"]
    row = next(item for item in items if item["item_id"] == env.deep_item["id"])
    assert row["status"] == "待拍"
    assert row["rejected"] is False
    assert row["reject_reason"] is None


# ── 撤销验收（2026-10-18）───────────────────────────────────────────────────


def _first_pass_events(db):
    cur = _run(
        db._conn.execute(
            "SELECT id FROM hygiene_board_events WHERE event_type = ? ORDER BY id ASC",
            (EVENT_FIRST_PASS,),
        )
    )
    return [int(dict(row)["id"]) for row in _run(cur.fetchall())]


def test_a_passed_day_can_be_undone_and_accepted_again(env, http):
    """验收可撤销（HTTP 面）：状态回待验收、一次通过事件删掉、还能再验一次。"""
    accepted = http.post(
        f"{DAILY}/{env.yesterday_item['id']}/accept",
        json={"shift": "白班", "business_date": YESTERDAY},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "已通过"
    assert len(_first_pass_events(env.db)) == 1

    undone = http.post(
        f"{DAILY}/{env.yesterday_item['id']}/undo-accept",
        json={"shift": "白班", "business_date": YESTERDAY},
    )
    assert undone.status_code == 200, undone.text
    assert undone.json() == {
        "item_id": env.yesterday_item["id"],
        "shift": "白班",
        "business_date": YESTERDAY,
        "status": "待验收",
    }
    # 昨天的行重新进待验收；这次验收记的一次通过撤掉了。
    queue = http.get(QUEUE).json()
    assert [row["business_date"] for row in queue["pending_past"]] == [YESTERDAY]
    assert _first_pass_events(env.db) == []

    again = http.post(
        f"{DAILY}/{env.yesterday_item['id']}/accept",
        json={"shift": "白班", "business_date": YESTERDAY},
    )
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "已通过"
    assert len(_first_pass_events(env.db)) == 1


def test_undo_accept_of_a_pending_day_is_a_readable_400(env, http):
    """还没验的行不能「撤销」：同一套错误映射，400 + 中文文案。"""
    response = http.post(
        f"{DAILY}/{env.yesterday_item['id']}/undo-accept",
        json={"shift": "白班", "business_date": YESTERDAY},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "没有待验收的实拍"


# ── 整改单排序（2026-10-18）─────────────────────────────────────────────────


def test_fix_tickets_are_ordered_by_deadline_earliest_first(env):
    """整改单按 deadline 升序：逾期最早的在最前（同一时限按 id）。

    首页「逾期」数字与门店看新增单都靠这个顺序 —— 原来按 id 升序，最急的那张可能
    沉在最后。当前 schema 里 ``deadline`` 是 NOT NULL（0001），造不出「没时限」的行；
    ``ORDER BY`` 里「NULL 归最后一档」那段是对老 / 手工数据的防御，这里钉住可造的部分。
    """
    work = env.work
    db = env.db
    opener = env.open_fix_actor
    not_due = _run(
        work.open_fix(opener, env.zone["id"], "卫生", "还没到期", timedelta(hours=2), _live())
    )
    latest = _run(
        work.open_fix(opener, env.zone["id"], "摆放", "排最后一张", timedelta(hours=2), _live())
    )
    for ticket_id, deadline in (
        (env.ticket["id"], "2026-09-14T09:00:00+08:00"),  # 最早逾期
        (not_due["id"], "2026-09-16T09:00:00+08:00"),
        (latest["id"], "2026-09-18T09:00:00+08:00"),
    ):
        _run(
            db._conn.execute(
                "UPDATE hygiene_fix_tickets SET deadline = ? WHERE id = ?",
                (deadline, ticket_id),
            )
        )
    _run(db._conn.commit())

    rows = _run(work.list_fix_tickets(SUPER))
    assert [row["id"] for row in rows] == [
        env.ticket["id"],
        not_due["id"],
        latest["id"],
    ]

