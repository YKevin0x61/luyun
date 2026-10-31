#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补验与历史口径的回归用例（审计 repro_hyg_1 转正，2026-10）。

覆盖四条缺陷 + 一条对照组，全部由 ``tests/audit/repro_hyg_1.py`` 转来：

* ``test_staff_reviewer_backfill_keeps_business_date``
  ``POST /staff/daily/{id}/accept|reject`` 原先把请求里的 ``business_date`` 丢掉：
  补验要么报错、要么把**今天**那张验掉。现在员工端与管理端同一口径（不传 = 今天）。
* ``test_staff_review_queue_carries_pending_past``
  ``/staff/daily-work?review=1`` 原先只有今天待验收，补验昨晚的照片在员工端没有入口；
  现在与服务端 ``/admin/daily-queue`` 一样给 ``pending_past``。
* ``test_deep_reject_reason_does_not_leak_into_the_next_cycle``
  专项驳回原因原先"历史上被驳回过就永久为真"；本周全新 instance 顶着上周原因渲染
  「已驳回」。现在按营业日收口（同一天的可见性由 ``test_hygiene_reject_visibility`` 钉）。
* ``test_backfilled_accept_belongs_to_the_photo_business_week``
  红黑榜原先按 ``occurred_at`` 分周、趋势按 ``business_date``：补验上周日的照片会进
  本周榜。现在两处统一按营业日。
* ``test_deleting_accepted_photo_keeps_the_passed_state``
  删掉一张**已验收**的归档照片，原先会把实例从「已通过」退回「待拍」，重拍再验收会给
  同一天再记一次「一次通过」。现在只删证据、不动已通过的状态。

另有一条**契约**用例（不是缺陷修复）：``test_deleting_zone_wipes_the_zone_history`` ——
2026-10-26 用户裁定「对齐承诺」：删工作区继续整区清空（含历史实拍与红黑榜事件），
页面文案与 ADR 0073 已改成如实描述；这里把它钉成既定契约，防止有人再当缺陷"修"回去。

对照组 ``test_admin_queue_and_accept_do_support_backfill``（已核查无问题）：同一条工单走
管理端入口补验本来就是通的 —— 缺的只是 staff 那两个入口，不是服务层能力。

时钟全部注入（``_Clock``）：2026-09-08 周二（上周）、09-13 周日（上周）、09-14 周一
（本周第一天）、09-15 周二（今天）；卫生周从周一 **00:00** 起（ADR 0125，之前是 06:00）。
"""

import asyncio
import io
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.archive import KIND_DAILY, KIND_DEEP, KIND_FIX_RESHOOT
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork

SUPER = {"kind": "super"}
PHONE = "13800138000"
REVIEWER_PHONE = "13900139000"

LAST_TUESDAY_MOMENT = datetime(2026, 9, 8, 10, 0, tzinfo=CHINA_TZ)
LAST_SUNDAY_MOMENT = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
MONDAY_MOMENT = datetime(2026, 9, 14, 10, 0, tzinfo=CHINA_TZ)
TODAY_MOMENT = datetime(2026, 9, 15, 10, 0, tzinfo=CHINA_TZ)
YESTERDAY = "2026-09-14"
LAST_SUNDAY = "2026-09-13"
TODAY = "2026-09-15"

STAFF_ACCEPT = "/api/hygiene/staff/daily/{item_id}/accept"
STAFF_REJECT = "/api/hygiene/staff/daily/{item_id}/reject"
STAFF_DAILY_WORK = "/api/hygiene/staff/daily-work"


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
    """可拨动的注入时钟：一个 fixture 里要走完四个营业日。"""

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
    clock = _Clock(TODAY_MOMENT)
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
    # 员工账号上的「管理员」：有日常验收开关，不是交照片的人。
    reviewer = {
        "id": 2,
        "phone": REVIEWER_PHONE,
        "name": "李四",
        "permission": "管理员",
        "admin_caps": ["daily_review"],
        "shift": "白班",
        "zone_id": zone["id"],
    }
    yield SimpleNamespace(
        db=db,
        work=work,
        clock=clock,
        accounts=accounts,
        zone=zone,
        actor=actor,
        reviewer=reviewer,
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
    # 管理端那几条（对照组）走 require_session；staff 入口走员工会话。HTTP 层只做
    # 参数透传，两条依赖各给一个固定身份就够。
    app.dependency_overrides[hygiene_module.require_staff_session] = lambda: {
        "employee": env.reviewer
    }
    with TestClient(app) as client:
        yield client


async def _count_rows(work, table: str) -> int:
    cur = await work._conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
    return int(dict(await cur.fetchone())["n"])


# ── 缺陷 1：staff 补验丢 business_date ────────────────────────────────────────


def test_staff_reviewer_backfill_keeps_business_date(env, http):
    """员工端补验昨晚的照片：要落在请求给的营业日，而不是今天那张。"""
    work, clock = env.work, env.clock
    item = _run(work.add_daily_item(SUPER, env.zone["id"], "案板-隔夜", _standard()))

    clock.moment = MONDAY_MOMENT  # 昨晚（营业日 09-14）交了一张
    _run(work.submit_daily(env.actor, item["id"], _live()))
    clock.moment = TODAY_MOMENT  # 今天同一项又交了一张（实例是另一个营业日）
    _run(work.submit_daily(env.actor, item["id"], _live()))

    response = http.post(
        STAFF_ACCEPT.format(item_id=item["id"]),
        json={"shift": "白班", "business_date": YESTERDAY},
    )
    assert response.status_code == 200, response.text
    assert response.json()["business_date"] == YESTERDAY

    yesterday_instance = _run(work._fetch_instance(YESTERDAY, "白班", item["id"]))
    today_instance = _run(work._fetch_instance(TODAY, "白班", item["id"]))
    assert yesterday_instance["status"] == "已通过", "补验要落在昨天那张实例上"
    assert today_instance["status"] == "待验收", "今天的实例不能被顺带验掉"


def test_staff_reviewer_reject_backfill_keeps_business_date(env, http):
    """驳回同一条口径：带 business_date 就驳回指定营业日那张，不动今天。"""
    work, clock = env.work, env.clock
    item = _run(work.add_daily_item(SUPER, env.zone["id"], "案板-隔夜驳回", _standard()))
    clock.moment = MONDAY_MOMENT
    _run(work.submit_daily(env.actor, item["id"], _live()))
    clock.moment = TODAY_MOMENT
    _run(work.submit_daily(env.actor, item["id"], _live()))

    response = http.post(
        STAFF_REJECT.format(item_id=item["id"]),
        json={"shift": "白班", "business_date": YESTERDAY, "reason": "台面还有油"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["business_date"] == YESTERDAY

    yesterday_instance = _run(work._fetch_instance(YESTERDAY, "白班", item["id"]))
    today_instance = _run(work._fetch_instance(TODAY, "白班", item["id"]))
    assert yesterday_instance["status"] == "待拍"
    assert today_instance["status"] == "待验收", "今天的实例不能被顺带驳回"


def test_staff_review_queue_carries_pending_past(env, http):
    """`?review=1` 也要给 pending_past：补验昨晚的照片先得看得见它。"""
    work, clock = env.work, env.clock
    item = _run(work.add_daily_item(SUPER, env.zone["id"], "案板-队列", _standard()))
    clock.moment = MONDAY_MOMENT
    _run(work.submit_daily(env.actor, item["id"], _live()))
    clock.moment = TODAY_MOMENT

    payload = http.get(f"{STAFF_DAILY_WORK}?review=1").json()
    assert [row["business_date"] for row in payload["pending_past"]] == [YESTERDAY]
    past = payload["pending_past"][0]
    assert past["item_id"] == item["id"]
    assert past["status"] == "待验收"
    assert payload["items"] == []

    # 不验收那一档（员工自己的日常队列）形状不变：没有 pending_past。
    own = http.get(STAFF_DAILY_WORK).json()
    assert "pending_past" not in own


# ── 对照组：管理端补验本来就是通的 ───────────────────────────────────────────


def test_admin_queue_and_accept_do_support_backfill(env, http):
    """对照组：同一条工单走管理端入口时补验是通的（缺的只是 staff 那两个入口）。"""
    work, clock = env.work, env.clock
    item = _run(work.add_daily_item(SUPER, env.zone["id"], "案板-对照", _standard()))
    clock.moment = MONDAY_MOMENT
    _run(work.submit_daily(env.actor, item["id"], _live()))
    clock.moment = TODAY_MOMENT

    queue = http.get("/api/hygiene/admin/daily-queue").json()
    assert queue["items"] == []
    assert [row["business_date"] for row in queue["pending_past"]] == [YESTERDAY]

    response = http.post(
        f"/api/hygiene/admin/daily/{item['id']}/accept",
        json={"shift": "白班", "business_date": YESTERDAY},
    )
    assert response.status_code == 200, response.text
    assert response.json()["business_date"] == YESTERDAY

    after = http.get("/api/hygiene/admin/daily-queue").json()
    assert after["pending_past"] == []


# ── 缺陷 2：专项驳回原因串到下一周 ───────────────────────────────────────────


def test_deep_reject_reason_does_not_leak_into_the_next_cycle(env):
    """上周被驳回的专项 → 本周同一项是全新待拍实例，不带上周的 rejected/原因。"""
    work, clock = env.work, env.clock
    item = _run(work.add_deep_clean_item(SUPER, 1, "抽油烟机"))  # 周二

    clock.moment = LAST_TUESDAY_MOMENT
    _run(work.submit_deep_clean_pair(env.actor, item["id"], _live(), _live()))
    _run(work.reject_deep_clean_pair(SUPER, item["id"], reason="滤网没拆下来洗"))

    clock.moment = TODAY_MOMENT  # 下一周的同一个周二
    row = next(
        row
        for row in _run(work.list_deep_clean_work(SUPER))["items"]
        if row["item_id"] == item["id"]
    )
    assert row["status"] == "待拍"
    assert row["rejected"] is False
    assert row["reject_reason"] is None


# ── 缺陷 3：补验的通过事件按营业日归周 ───────────────────────────────────────


def test_backfilled_accept_belongs_to_the_photo_business_week(env):
    """补验上周日的照片：红黑榜与趋势都算在上周，不能一个算本周一个算上周。"""
    work, clock = env.work, env.clock
    item = _run(work.add_daily_item(SUPER, env.zone["id"], "案板-周日", _standard()))

    clock.moment = LAST_SUNDAY_MOMENT  # 周日那张属于「09-07 那一周」
    _run(work.submit_daily(env.actor, item["id"], _live()))
    clock.moment = MONDAY_MOMENT  # 周一早上补验（本周从 09-14 06:00 起）
    _run(work.accept_daily(SUPER, item["id"], "白班", business_date=LAST_SUNDAY))

    boards = _run(work.list_boards())
    assert boards["week_start"] == "2026-09-14T00:00:00+08:00"
    assert all(row["一次通过"] == 0 for row in boards["people"])

    trend = _run(work.hygiene_trend(days=30))
    trend_days = {row["business_date"]: row["first_pass"] for row in trend["daily"]}
    assert trend_days[LAST_SUNDAY] == 1, "趋势仍按营业日算在上周日（这条口径不变）"


# ── 缺陷 4：删已验收的归档照片不能回退状态 ───────────────────────────────────


def test_deleting_accepted_photo_keeps_the_passed_state(env):
    """删掉已验收的日常照片：只删证据，实例仍是「已通过」，不会重记一次通过。"""
    work, clock = env.work, env.clock
    item = _run(work.add_daily_item(SUPER, env.zone["id"], "案板-已验", _standard()))
    clock.moment = TODAY_MOMENT
    _run(work.submit_daily(env.actor, item["id"], _live()))
    _run(work.accept_daily(SUPER, item["id"], "白班"))

    instance_row = _run(work._fetch_instance(TODAY, "白班", item["id"]))
    submission_id = int(instance_row["pending_submission_id"])
    events_before = _run(_count_rows(work, "hygiene_board_events"))

    result = _run(work.delete_archive_record(SUPER, KIND_DAILY, submission_id))
    assert result["records"] == 1
    assert result["status_reset"] == 0, "已通过的实例不该被复位"

    instance = _run(work._fetch_instance(TODAY, "白班", item["id"]))
    assert instance["status"] == "已通过"
    assert _run(_count_rows(work, "hygiene_board_events")) == events_before


def test_deleting_accepted_deep_pair_keeps_the_passed_state(env):
    """专项同理：删已验收的前后对照，实例仍是「已通过」。"""
    work, clock = env.work, env.clock
    item = _run(work.add_deep_clean_item(SUPER, _run(work.list_deep_clean_work(SUPER))["weekday"], "抽油烟机"))
    clock.moment = TODAY_MOMENT
    _run(work.submit_deep_clean_pair(env.actor, item["id"], _live(), _live()))
    _run(work.accept_deep_clean_pair(SUPER, item["id"]))

    instance = _run(work._fetch_deep_clean_instance(TODAY, item["id"]))
    submission_id = int(instance["pending_submission_id"])
    result = _run(work.delete_archive_record(SUPER, KIND_DEEP, submission_id))
    assert result["status_reset"] == 0
    after = _run(work._fetch_deep_clean_instance(TODAY, item["id"]))
    assert after["status"] == "已通过"


def test_deleting_accepted_fix_reshoot_keeps_the_passed_ticket(env):
    """整改单同理：删已闭环的回拍照片，单子仍是「已通过」，不退回待回拍。"""
    from datetime import timedelta

    work, clock = env.work, env.clock
    opener = {**env.actor, "permission": "管理员", "caps": ["fix"]}
    clock.moment = TODAY_MOMENT
    ticket = _run(
        work.open_fix(
            opener, env.zone["id"], "卫生", "台面有油", timedelta(hours=2), _live()
        )
    )
    _run(work.reshoot_fix(env.actor, ticket["id"], _live()))
    _run(work.accept_fix(opener, ticket["id"]))

    cur = _run(
        work._conn.execute(
            "SELECT id FROM hygiene_fix_reshoots WHERE ticket_id = ? ORDER BY id DESC",
            (ticket["id"],),
        )
    )
    reshoot_id = int(dict(_run(cur.fetchone()))["id"])
    result = _run(work.delete_archive_record(SUPER, KIND_FIX_RESHOOT, reshoot_id))
    assert result["status_reset"] == 0
    after = _run(work.get_fix_ticket(ticket["id"], actor=SUPER))
    assert after["status"] == "已通过"


# ── 契约（不是缺陷）：删工作区即清该区历史 ──────────────────────────────────


async def _count_zone_rows(work, table: str, zone_id: int) -> int:
    cur = await work._conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE zone_id = ?", (zone_id,)
    )
    return int(dict(await cur.fetchone())["n"])


def test_deleting_zone_wipes_the_zone_history(env):
    """删工作区 = 整区清空（含已验收的历史实拍与红黑榜事件），这是既定契约。

    2026-10-26 用户裁定「对齐承诺」：不改行为、不软删、不动 schema，只把页面文案与
    ADR 0073 改成如实描述。所以这里钉的是**产品决定**，不是待修缺陷 —— 删区之后该区
    submissions 与 board_events 都归零，不可恢复。
    """
    work, clock = env.work, env.clock
    zone_id = env.zone["id"]
    item = _run(work.add_daily_item(SUPER, zone_id, "案板-历史", _standard()))
    clock.moment = MONDAY_MOMENT
    _run(work.submit_daily(env.actor, item["id"], _live()))
    _run(work.accept_daily(SUPER, item["id"], "白班"))  # 09-14 已通过

    clock.moment = TODAY_MOMENT
    assert _run(_count_rows(work, "hygiene_daily_submissions")) == 1
    assert _run(_count_zone_rows(work, "hygiene_board_events", zone_id)) >= 1

    deleted = _run(work.delete_zone(SUPER, zone_id))
    assert deleted["id"] == zone_id

    assert _run(_count_rows(work, "hygiene_daily_submissions")) == 0
    assert _run(_count_zone_rows(work, "hygiene_board_events", zone_id)) == 0
    assert _run(_count_rows(work, "hygiene_daily_instances")) == 0
    assert _run(_count_rows(work, "hygiene_daily_items")) == 0
    assert zone_id not in [zone["id"] for zone in _run(work.list_zones())]
