#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生后端审计复现用例（切片 C，隔离补验 / 计数口径 / 事务原子性）。

文件名刻意不以 ``test_`` 开头：默认 ``pytest tests/`` 不收集它（避免 CI 常红），
需要显式跑：

    .venv/bin/python -m pytest tests/audit/repro_hyg_1.py -x -q

每条用例都**断言缺陷行为**（红 = 已确证）。逐条：

- ``test_staff_reviewer_backfill_is_dropped``
  缺陷：``POST /staff/daily/{id}/accept|reject`` 收了 ``ShiftIn``（里面有
  ``business_date``）却**不往服务层传**，隔夜补验在员工端（有 daily_review 开关的
  「管理员」）等于没有；更糟的是这个字段被静默忽略 —— 同一项在今天也有一张待验收时，
  补验请求会把**今天**那张验掉。修：两个 staff 入口与 admin 一样传
  ``business_date=_optional_business_date(body.business_date)``，
  ``/staff/daily-work?review=1`` 也给 ``pending_past``。

- ``test_deep_clean_reject_reason_leaks_into_next_week``
  缺陷：``_deep_clean_rejected_reasons``（work.py:2109）去掉 ``business_date`` 过滤后，
  专项项「历史上被驳回过」变成永久为真。第 1 周驳回 → 第 2 周同一 item 是全新 instance
  （待拍、本周从没被驳回过），``list_deep_clean_work`` 仍返回 ``rejected=True`` +
  上周的原因；员工端 ``HygieneHomeView.vue`` 的 ``task.rejected`` 就直接渲染
  「已驳回：<过期原因>」。修：按「最近一次驳回事件 > 该 item 最近一次提交/最近事件」
  收口，或只在该 instance 仍有 pending 状态时带原因。

- ``test_backfilled_accept_counts_into_this_week_board``
  缺陷：红黑榜按 ``occurred_at``（`_events_in_week`，work.py:2890）分周，趋势 /
  员工成绩按 ``business_date``。补验上周日的照片时，事件记在本周一（occurred_at），
  于是「上周的工作」进了**本周**红黑榜，而趋势把它算在上周日 —— 同一个事件在两个
  页面归属两个不同营业周。修：两处统一到 ``business_date``（榜也一样按营业日分周）。

- ``test_deleting_zone_wipes_closed_history``
  缺陷：``delete_zone`` / ``_drop_daily_items``（work.py:537-578）与
  ``_drop_board_events``（work.py:624-639）把该区**已通过**的历史实拍、闭环整改单、
  全部红黑榜事件一起删掉，而 ADR 0073 与页面确认文案只说「进行中的待办」。
  修：只删进行中的待办，历史保留（或改 ADR/文案 + 输入名字二次确认）。

  已排除的候选：``EmployeeAccounts.logout`` 等无 ``serialized_write`` 的写方法曾疑似会
  把并发写者的半截事务提交掉；实测 ``db_core/backend/pg.py`` 的 ``ensure_transaction``
  在整个事务期间持 ``_TaskGuard``，另一个任务只会排队后抛 ``DatabaseBusy``（503），
  不会插进别人的事务 —— 不是数据错，故本文件不再保留那条用例。

- ``test_purge_passed_daily_submission_reverts_acceptance``
  缺陷：``_archive_drop_daily_submissions``（work.py:4843）的复位 UPDATE 只看
  ``pending_submission_id``、**不看状态**，而 ``accept_daily`` 成功后并不会清掉
  ``pending_submission_id``。于是在「数据与照片」里删掉一张**已验收**的日常照片，会把
  实例从「已通过」退回「待拍」；员工重拍、店长再验，同一天同一项会再记一次
  ``一次通过`` 事件 —— 红黑榜 / 趋势凭空多一次。修：只对 ``status='待验收'`` 的行复位，
  已通过的删除只删证据、不动状态（或明确要求二次确认 + 不重算事件）。

时钟全部注入（``_Clock``），不依赖真实时间：2026-09-08 周二、09-13 周日、
09-14 周一、09-15 周二；卫生周从周一 06:00 起。
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
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork

SUPER = {"kind": "super"}
PHONE = "13800138000"
REVIEWER_PHONE = "13900139000"

# 2026-09-08 周二（上一周）；09-13 周日（上一周）；09-14 周一（本周第一天）；
# 09-15 周二（今天）。
LAST_TUESDAY_MOMENT = datetime(2026, 9, 8, 10, 0, tzinfo=CHINA_TZ)
LAST_SUNDAY_MOMENT = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
MONDAY_MOMENT = datetime(2026, 9, 14, 10, 0, tzinfo=CHINA_TZ)
TODAY_MOMENT = datetime(2026, 9, 15, 10, 0, tzinfo=CHINA_TZ)
LAST_TUESDAY = "2026-09-08"
LAST_SUNDAY = "2026-09-13"
YESTERDAY = "2026-09-14"
TODAY = "2026-09-15"

QUEUE = "/api/hygiene/admin/daily-queue"
STAFF_ACCEPT = "/api/hygiene/staff/daily/{item_id}/accept"


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
    app.dependency_overrides[hygiene_module.require_staff_session] = lambda: {
        "employee": env.reviewer
    }
    with TestClient(app) as client:
        yield client


def test_staff_reviewer_backfill_is_dropped(env, http):
    """员工端（有 daily_review 的「管理员」）补验昨晚的照片：请求被静默忽略。"""
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
    yesterday_instance = _run(work._fetch_instance(YESTERDAY, "白班", item["id"]))
    today_instance = _run(work._fetch_instance(TODAY, "白班", item["id"]))
    assert yesterday_instance["status"] == "已通过", (
        "员工端补验被丢弃："
        f"HTTP {response.status_code} {response.text}；"
        f"昨天状态={yesterday_instance['status']}，今天状态={today_instance['status']}"
        "（business_date 没传给服务层 —— 若今天也待验收，它会把今天那张验掉）"
    )


def test_admin_queue_and_accept_do_support_backfill(env, http):
    """对照组（已核查无问题）：同一条工单走管理端入口时补验是通的。

    与上一条形成对照：缺的只是 staff 那两个入口，不是服务层能力。
    """
    work, clock = env.work, env.clock
    item = _run(work.add_daily_item(SUPER, env.zone["id"], "案板-对照", _standard()))
    clock.moment = MONDAY_MOMENT
    _run(work.submit_daily(env.actor, item["id"], _live()))
    clock.moment = TODAY_MOMENT

    response = http.post(
        f"/api/hygiene/admin/daily/{item['id']}/accept",
        json={"shift": "白班", "business_date": YESTERDAY},
    )
    assert response.status_code == 200, response.text
    assert response.json()["business_date"] == YESTERDAY


def test_deep_clean_reject_reason_leaks_into_next_week(env):
    """专项项上周被驳回 → 本周（新 instance、待拍）仍带旧的 rejected/原因。"""
    work, clock = env.work, env.clock
    item = _run(work.add_deep_clean_item(SUPER, 1, "抽油烟机"))  # 周二

    clock.moment = LAST_TUESDAY_MOMENT
    _run(work.submit_deep_clean_pair(env.actor, item["id"], _live(), _live()))
    _run(work.reject_deep_clean_pair(SUPER, item["id"], reason="滤网没拆下来洗"))

    clock.moment = TODAY_MOMENT  # 下一周的同一个周二
    payload = _run(work.list_deep_clean_work(SUPER))
    row = next(item_row for item_row in payload["items"] if item_row["item_id"] == item["id"])
    assert row["status"] == "待拍"
    assert row["rejected"] is False, (
        "本周全新的待拍项被标成「已驳回」，原因来自上周："
        f"rejected={row['rejected']} reject_reason={row['reject_reason']!r}"
    )


def test_backfilled_accept_counts_into_this_week_board(env):
    """补验上周日的照片：通过事件按 occurred_at 进了本周红黑榜，按营业日却属上周。"""
    work, clock = env.work, env.clock
    item = _run(work.add_daily_item(SUPER, env.zone["id"], "案板-周日", _standard()))

    clock.moment = LAST_SUNDAY_MOMENT  # 周日那张属于「09-07 那一周」
    _run(work.submit_daily(env.actor, item["id"], _live()))
    clock.moment = MONDAY_MOMENT  # 周一早上补验（本周从 09-14 06:00 起）
    _run(work.accept_daily(SUPER, item["id"], "白班", business_date=LAST_SUNDAY))

    boards = _run(work.list_boards())
    trend = _run(work.hygiene_trend(days=30))
    this_week_first_pass = sum(row["一次通过"] for row in boards["people"])
    trend_days = {row["business_date"]: row["first_pass"] for row in trend["daily"]}
    assert this_week_first_pass == 0, (
        "补验上周日的通过被算进了本周红黑榜："
        f"week_start={boards['week_start']} 本周一次通过={this_week_first_pass}；"
        f"趋势把它算在 {[d for d, n in trend_days.items() if n]}"
    )


def test_purge_passed_daily_submission_reverts_acceptance(env):
    """删掉一张**已验收**的日常照片，会把实例从「已通过」退回「待拍」。"""
    work, clock = env.work, env.clock
    item = _run(work.add_daily_item(SUPER, env.zone["id"], "案板-已验", _standard()))
    clock.moment = TODAY_MOMENT
    _run(work.submit_daily(env.actor, item["id"], _live()))
    accepted = _run(work.accept_daily(SUPER, item["id"], "白班"))
    assert accepted["status"] == "已通过"

    instance_row = _run(work._fetch_instance(TODAY, "白班", item["id"]))
    submission_id = int(instance_row["pending_submission_id"])
    _run(work.delete_archive_record(SUPER, "daily", submission_id))

    instance = _run(work._fetch_instance(TODAY, "白班", item["id"]))
    assert instance["status"] == "已通过", (
        "清理旧照片把已验收的记录退回了待拍：status="
        f"{instance['status']}（重拍再验收会给同一天再记一次「一次通过」）"
    )


async def _count_rows(work, table: str) -> int:
    cur = await work._conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
    return int(dict(await cur.fetchone())["n"])


def test_deleting_zone_wipes_closed_history(env):
    """删工作区把**已验收**的历史实拍、闭环整改与红黑榜事件一起删了。

    页面确认文案：「删除「X」会同时删除该区**进行中**的日常待办和未闭环整改单。」
    （admin-web/src/views/hygiene/HygieneZonesView.vue:680）
    ADR 0073 的决策原文也只说 "Open 日常检查 and 整改单 ... go away".
    """
    work, clock = env.work, env.clock
    item = _run(work.add_daily_item(SUPER, env.zone["id"], "案板-历史", _standard()))
    clock.moment = MONDAY_MOMENT
    _run(work.submit_daily(env.actor, item["id"], _live()))
    _run(work.accept_daily(SUPER, item["id"], "白班"))  # 09-14 已通过

    clock.moment = TODAY_MOMENT
    before = _run(_count_rows(work, "hygiene_board_events"))

    _run(work.delete_zone(SUPER, env.zone["id"]))

    submissions = _run(_count_rows(work, "hygiene_daily_submissions"))
    after = _run(_count_rows(work, "hygiene_board_events"))
    assert submissions == 1, (
        "删工作区把已验收的历史实拍一起删了："
        f"submissions={submissions}（确认文案只承诺删「进行中的待办」）；"
        f"红黑榜事件 before={before} after={after}"
    )
