#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""换工作区要留痕，而且换完之后看板/日常清单仍然对得上（票 10 起由**排班**触发）。

票 10 之前员工当天能在手机上把工作区从 A 改成 B（``pick_assignment`` 是 upsert 语义），
所以那份留痕记的是「谁自己换过区」。自选入口撤了之后，换区只剩排班侧两条路：

* 店长改**某一天的覆盖**：``SchedulingStore.set_override(employee_id, date, shift_id=…, zone_id=…)``；
* 店长改**固定工作区**：``SchedulingStore.set_zone_default(employee_id, shift_id, zone_id)``。

卫生这一侧只读排班结果（公共层的 ``DutyRoster``），所以「换区之后数据一致」这件事现在
要守的是：改完排班那一行，卫生立刻按新的区说话（会话 / 日常清单 / 提交归属），而且
**不把店长的排班改动误记成员工自换区** —— 换区的留痕搬到了排班侧
（``scheduling_overrides`` 一行 + ``staff_assignments.source='override'``）。

这里的用例锁住五件事：
1. 单日覆盖换区 → 卫生当天的班次与工作区立刻跟着变，排班侧留下覆盖记录，卫生侧 0 条换区事件；
2. 管理员那两条路（卫生后台改派已封 403、改固定区）都不算员工自换区，且单日覆盖不被固定区冲掉；
3. 换区之后每条实拍仍留在**拍摄时**所在的那个区，换区挪不动已经交上去的证据；
4. 单日覆盖只属于那一天：跨过 06:00 切日点之后回到规则给的区。
5. 留痕那一列本身还算得对：写一条换区事件，个人榜的「换区」就 +1（记录器现在没有
   任何接口能触发，见最后那条用例的说明）。
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
from services.hygiene.work import EVENT_ZONE_SWITCH, HygieneWork, HygieneWorkError
from services.scheduling.store import SchedulingStore
from tests.hygiene_duty import assign_duty

PHONE = "13800138000"
PASSWORD = "password123"
SUPER = {"kind": "super"}
# 固定时钟 2026-09-13 10:00 所属的营业日（06:00 切日，10:00 已经过了切日点）。
DAY = "2026-09-13"
NEXT_DAY = "2026-09-14"
ASSIGNMENT_FROM_SCHEDULE = "今天上哪个班、在哪个区由排班决定：去「今天」页看你的班，要改请找店长"
SHIFT_FROM_SCHEDULE = "今天的班次由排班决定：去「今天」页看你的班，要改请找店长"


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


def _standard():
    return {"bytes": _jpeg(), "content_type": "image/jpeg", "markup": []}


def _capture():
    return {"bytes": _jpeg(), "content_type": "image/jpeg", "live": True, "markup": []}


class _Clock:
    """可推进的假时钟，用来跨营业日。"""

    def __init__(self):
        self.now = datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)

    def __call__(self):
        return self.now


@pytest.fixture
def hygiene_http(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    clock = _Clock()
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=clock)
    work = HygieneWork(
        db,
        captures=FakeCaptureStore(),
        now=clock,
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    employee = _run(accounts.register(PHONE, PASSWORD, "张三"))
    _run(accounts.approve(employee["id"]))
    # 前置数据一律配排班（票 10）：今天白班档 + 第一个工作区（案板）。
    first, second = _zone_ids(work)[:2]
    _run(assign_duty(db, employee["id"], slot="day", zone_id=first, now=clock.now))
    duty = _run(accounts.current_assignment(employee["id"]))
    assert (duty["shift"], duty["zone_id"]) == ("白班", first), "排班没铺出今天的白班"
    # 排班侧的那扇门（店长改覆盖 / 改固定区）用同一个时钟，跨营业日才跟着动。
    store = SchedulingStore(db, now=clock)
    app = FastAPI()
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    app.dependency_overrides[hygiene_module.require_session] = lambda: "test-admin-session"
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            db=db,
            accounts=accounts,
            work=work,
            clock=clock,
            store=store,
            employee=employee,
            first=first,
            second=second,
        )
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _login(client, remember=False):
    response = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD, "remember": remember},
    )
    assert response.status_code == 200
    return response


def _pick(client, zone_id, shift="白班"):
    """员工端那条已经撤掉的自选路（票 10 起固定 403）。"""
    return client.post(
        "/api/hygiene/staff/assignment",
        json={"shift": shift, "zone_id": zone_id},
    )


def _switch_events(db):
    cur = _run(
        db._conn.execute(
            "SELECT COUNT(*) AS n FROM hygiene_board_events WHERE event_type = ?",
            (EVENT_ZONE_SWITCH,),
        )
    )
    return int(dict(_run(cur.fetchone()))["n"])


def _zone_ids(work):
    return [zone["id"] for zone in _run(work.list_zones())]


def _zone_names(work):
    return {zone["id"]: zone["name"] for zone in _run(work.list_zones())}


def _shift_id(store, name):
    for shift in _run(store.list_shifts()):
        if shift["name"] == name:
            return shift["id"]
    raise AssertionError(f"没有班次 {name}")


def _assignment_row(db, employee_id, business_date):
    """排班结果那一行 —— 卫生看到的就是它（没有第二份副本）。"""
    cur = _run(
        db._conn.execute(
            """SELECT shift_id, zone_id, source FROM staff_assignments
               WHERE employee_id = ? AND business_date = ?""",
            (int(employee_id), business_date),
        )
    )
    row = _run(cur.fetchone())
    return dict(row) if row is not None else None


def _override_rows(db):
    cur = _run(
        db._conn.execute(
            """SELECT employee_id, business_date, shift_id, zone_id, kind
               FROM scheduling_overrides ORDER BY id ASC"""
        )
    )
    return [dict(row) for row in _run(cur.fetchall())]


def _submission_rows(db):
    cur = _run(
        db._conn.execute(
            """SELECT capture_id, submitter_id, zone_name
               FROM hygiene_daily_submissions ORDER BY id ASC"""
        )
    )
    return [dict(row) for row in _run(cur.fetchall())]


def _actor(env):
    """按**排班结果**拼一份员工 actor（票 10 之后这里没有别的来源）。"""
    duty = _run(env.accounts.current_assignment(env.employee["id"]))
    return {
        "kind": "staff",
        "id": env.employee["id"],
        "permission": "普通员工",
        "name": "张三",
        "phone": PHONE,
        "shift": duty["shift"],
        "zone_id": duty["zone_id"],
        "zone_name": duty["zone_name"],
    }


def test_scheduling_override_moves_todays_zone_and_hygiene_follows(hygiene_http):
    """店长把某一天的区从 A 改成 B：卫生当天立刻跟着变，留痕落在排班侧。

    票 10 之前这条测的是「员工自己连着换两次区 → 两条换区事件」。自选撤了之后，
    同一件事换成了：排班的结果行就是卫生的答案（不用谁去同步），改动记在
    ``scheduling_overrides`` 上，而卫生这边一条「换区」事件都不写。
    """
    env = hygiene_http
    _login(env.client)
    day_shift = _shift_id(env.store, "白班")

    before = _run(env.accounts.current_assignment(env.employee["id"]))
    assert (before["shift"], before["zone_id"]) == ("白班", env.first)

    _run(
        env.store.set_override(
            env.employee["id"], DAY, shift_id=day_shift, zone_id=env.second
        )
    )

    after = _run(env.accounts.current_assignment(env.employee["id"]))
    assert (after["shift"], after["zone_id"], after["zone_name"]) == (
        "白班",
        env.second,
        _zone_names(env.work)[env.second],
    )
    assert _assignment_row(env.db, env.employee["id"], DAY) == {
        "shift_id": day_shift,
        "zone_id": env.second,
        "source": "override",
    }

    # 会话走的是同一条读路径：登录态下看到的区也是新的那个。
    me = env.client.get("/api/hygiene/staff/me")
    assert me.status_code == 200
    assert me.json()["employee"]["zone_id"] == env.second
    assert me.json()["employee"]["zone_name"] == _zone_names(env.work)[env.second]

    # 留痕在排班侧：一条单日覆盖，改的正是这一天、这个区。
    overrides = _override_rows(env.db)
    assert len(overrides) == 1
    assert (
        int(overrides[0]["employee_id"]),
        overrides[0]["business_date"],
        int(overrides[0]["zone_id"]),
    ) == (int(env.employee["id"]), DAY, env.second)

    assert _switch_events(env.db) == 0, "换区是店长改的排班，不是员工自换区"


def test_admin_paths_move_the_zone_without_a_self_switch_event(hygiene_http):
    """管理员两条路都不算员工自换区；已经单日覆盖过的那天不被固定区冲掉。

    票 10 之前这条测的是「管理员改派不是员工自换区」。现在管理员改派从卫生这一侧
    撤了（403），落到排班的单日覆盖与固定工作区上 —— 两条路都留在排班侧，都不该
    在卫生的看板上记成「换区」。
    """
    env = hygiene_http
    _login(env.client)
    day_shift = _shift_id(env.store, "白班")
    before = _assignment_row(env.db, env.employee["id"], DAY)

    refused = env.client.post(
        f"/api/hygiene/admin/roster/{env.employee['id']}/assignment",
        json={"shift": "夜班", "zone_id": env.second},
    )
    assert refused.status_code == 403
    assert refused.json()["detail"] == ASSIGNMENT_FROM_SCHEDULE
    assert _assignment_row(env.db, env.employee["id"], DAY) == before, "被拒的改派什么也没写"

    # 今天先被单日覆盖成 B 区……
    _run(
        env.store.set_override(
            env.employee["id"], DAY, shift_id=day_shift, zone_id=env.second
        )
    )
    # ……再改白班档的固定区：今天那行是覆盖，固定区改不动它。
    third = _zone_ids(env.work)[2]
    _run(env.store.set_zone_default(env.employee["id"], day_shift, third))

    today = _run(env.accounts.current_assignment(env.employee["id"]))
    assert today["zone_id"] == env.second, "单日覆盖是那一天的快照"
    assert int(today["employee_id"]) == int(env.employee["id"])
    assert _assignment_row(env.db, env.employee["id"], NEXT_DAY) == {
        "shift_id": day_shift,
        "zone_id": third,
        "source": "rule",
    }

    assert len(_override_rows(env.db)) == 1, "改固定区不是单日覆盖"
    assert _switch_events(env.db) == 0, "管理员排班不是员工自换区"


def test_each_capture_stays_in_the_zone_it_was_shot_in(hygiene_http):
    """换区之后看板仍然对得上：每条实拍留在它**拍摄时**所在的那个区。

    票 10 之前这条断言的是「个人榜上出现换区 1」。员工自选撤了、换区改由排班触发
    之后，同一件事换成了两面：个人榜不再有「换区」（0，因为没人自己换区），而实拍
    依旧按拍摄时的区归属 —— 换区挪不动已经交上去的证据，跨区刷实拍的人也没法把
    旧区的照片算到新区头上。
    """
    env = hygiene_http
    _login(env.client)
    names = _zone_names(env.work)
    item_a = _run(
        env.work.add_daily_item(SUPER, env.first, f"{names[env.first]}-台面", _standard())
    )
    item_b = _run(
        env.work.add_daily_item(SUPER, env.second, f"{names[env.second]}-台面", _standard())
    )

    actor_a = _actor(env)
    assert actor_a["zone_id"] == env.first
    assert [row["item_id"] for row in _run(env.work.list_daily_work(actor_a))] == [
        item_a["id"]
    ]
    first_capture = _run(env.work.submit_daily(actor_a, item_a["id"], _capture()))

    _run(
        env.store.set_override(
            env.employee["id"],
            DAY,
            shift_id=_shift_id(env.store, "白班"),
            zone_id=env.second,
        )
    )

    actor_b = _actor(env)
    assert actor_b["zone_id"] == env.second
    inbox = _run(env.work.list_daily_work(actor_b))
    assert [row["item_id"] for row in inbox] == [item_b["id"]], "清单换成新区的项"
    second_capture = _run(env.work.submit_daily(actor_b, item_b["id"], _capture()))

    # 换区之后回交旧区的项：拒（zone_mismatch），而不是悄悄记到新区名下
    with pytest.raises(HygieneWorkError) as raised:
        _run(env.work.submit_daily(actor_b, item_a["id"], _capture()))
    assert raised.value.code == "zone_mismatch"

    rows = _submission_rows(env.db)
    assert [
        (row["capture_id"], row["zone_name"], int(row["submitter_id"])) for row in rows
    ] == [
        (first_capture["capture_id"], names[env.first], int(env.employee["id"])),
        (second_capture["capture_id"], names[env.second], int(env.employee["id"])),
    ]

    boards = _run(env.work.list_boards())
    people = {int(row["employee_id"]): row for row in boards["people"]}
    me = people[int(env.employee["id"])]
    assert me["实拍"] == 2
    assert me["换区"] == 0, "换区是店长改的排班，个人榜不该记成员工自换区"


def test_the_override_only_covers_its_own_business_day(hygiene_http):
    """单日覆盖只属于那一天：跨过 06:00 切日点之后回到规则给的区。

    票 10 之前这条测的是「新营业日重新选区不算换区」。同一件事在排班侧就是：
    覆盖是**按营业日**的快照，卫生跟着营业日切（9/14 05:59 还在 9/13）。
    """
    env = hygiene_http
    # 跨营业日要过 20 小时，班次级会话会先过期，所以用「记住登录」的 30 天会话。
    _login(env.client, remember=True)
    _run(
        env.store.set_override(
            env.employee["id"],
            DAY,
            shift_id=_shift_id(env.store, "白班"),
            zone_id=env.second,
        )
    )
    assert env.client.get("/api/hygiene/staff/me").json()["employee"]["zone_id"] == env.second

    # 06:00 之前还是营业日 9/13：覆盖仍然算数。
    env.clock.now = datetime(2026, 9, 14, 5, 59, tzinfo=CHINA_TZ)
    assert _run(env.accounts.current_assignment(env.employee["id"]))["zone_id"] == env.second

    # 06:00 起是新的营业日：回到规则给的固定区。
    env.clock.now = datetime(2026, 9, 14, 6, 0, tzinfo=CHINA_TZ)
    after = _run(env.accounts.current_assignment(env.employee["id"]))
    assert (after["shift"], after["zone_id"]) == ("白班", env.first)
    me = env.client.get("/api/hygiene/staff/me")
    assert me.status_code == 200
    assert me.json()["employee"]["zone_id"] == env.first
    assert _assignment_row(env.db, env.employee["id"], NEXT_DAY) == {
        "shift_id": _shift_id(env.store, "白班"),
        "zone_id": env.first,
        "source": "rule",
    }

    assert _switch_events(env.db) == 0


def test_the_staff_cannot_switch_their_own_zone_any_more(hygiene_http):
    """员工端那两条自选路（换区 / 只换班次）都撤了：403，而且什么都改不动。

    票 10 之前这条测的是「首次选择、同区重选都不算换区」。入口没了之后，同一件事
    反过来守：这两次调用既不写换区事件，也不动排班那一行 —— 今天在哪，还是排班
    写下的那个样子（要改请找店长，或去排班页改那一天）。
    """
    env = hygiene_http
    _login(env.client)
    before = _assignment_row(env.db, env.employee["id"], DAY)

    switched = _pick(env.client, env.second)
    assert switched.status_code == 403
    assert switched.json()["detail"] == ASSIGNMENT_FROM_SCHEDULE

    repicked = _pick(env.client, env.first)
    assert repicked.status_code == 403
    assert repicked.json()["detail"] == ASSIGNMENT_FROM_SCHEDULE

    shift_only = env.client.post(
        "/api/hygiene/staff/shift", json={"shift": "夜班", "zone_id": env.second}
    )
    assert shift_only.status_code == 403
    # 两条路各走各自的服务层入口，答的也是各自那句话：只改班次这条回
    # `shift_from_schedule`（「今天的班次由排班决定」），不是笼统的「都改不了」。
    assert shift_only.json()["detail"] == SHIFT_FROM_SCHEDULE

    assert _assignment_row(env.db, env.employee["id"], DAY) == before
    today = _run(env.accounts.current_assignment(env.employee["id"]))
    assert (today["shift"], today["zone_id"]) == ("白班", env.first)
    assert _switch_events(env.db) == 0, "被拒的自选不该留下换区事件"


def test_a_switch_event_still_feeds_the_person_board(hygiene_http):
    """留痕那一列还算得对：写一条换区事件，个人榜就多一个「换区」。

    票 10 之后**没有任何接口**能走到这个记录器了（员工自选那条路 403，店长改的是
    排班，而排班不 import 卫生），所以生产上个人榜的「换区」恒为 0；这条锁的是
    「事件真被写进来看板仍然算得对」——留痕的读侧没坏。
    """
    env = hygiene_http
    _login(env.client)
    actor = _actor(env)
    names = _zone_names(env.work)

    _run(
        env.work.record_zone_switch(
            actor,
            from_zone_id=env.first,
            from_zone_name=names[env.first],
            to_zone_id=env.second,
            to_zone_name=names[env.second],
        )
    )

    cur = _run(
        env.db._conn.execute(
            """SELECT zone_id, employee_id, business_date
               FROM hygiene_board_events WHERE event_type = ? ORDER BY id ASC""",
            (EVENT_ZONE_SWITCH,),
        )
    )
    rows = [dict(row) for row in _run(cur.fetchall())]
    assert len(rows) == 1
    assert int(rows[0]["zone_id"]) == env.second, "记的是换过去的那一个区"
    assert int(rows[0]["employee_id"]) == int(env.employee["id"])
    assert rows[0]["business_date"] == DAY

    boards = _run(env.work.list_boards())
    people = {int(row["employee_id"]): row for row in boards["people"]}
    assert people[int(env.employee["id"])]["换区"] == 1
    assert "换区" in people[int(env.employee["id"])]
