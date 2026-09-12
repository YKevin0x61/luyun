#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP: which session may call hygiene staff vs admin roster routes."""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.hygiene as hygiene_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services import auth_service
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.work import HygieneWork, SEED_ZONE_NAMES

SUPER = {"kind": "super"}

PHONE = "13800138000"
PHONE_ADMIN = "13800138001"
PHONE_NIGHT = "13800138002"
PHONE_OTHER_ADMIN = "13800138003"
PASSWORD = "password123"
SHOT_A = b"SHOT-A"
SHOT_B = b"SHOT-B"
ADMIN_INIT = {
    "username": "admin",
    "password": "password123",
    "confirm_password": "password123",
}


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


@pytest.fixture
def hygiene_http(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(
        db, now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
    )
    work = HygieneWork(
        db,
        captures=FakeCaptureStore(),
        now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
        notifier=None,
    )
    _run(work.prepare())
    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(hygiene_module.router)
    app.dependency_overrides[hygiene_module._get_accounts] = lambda: accounts
    app.dependency_overrides[hygiene_module._get_work] = lambda: work
    with TestClient(app) as client:
        yield client, db, accounts, work
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def test_unauthenticated_can_register_and_attempt_login_not_roster(hygiene_http):
    client, _db, _accounts, _work = hygiene_http
    resp = client.post(
        "/api/hygiene/staff/register",
        json={"phone": PHONE, "password": PASSWORD},
    )
    assert resp.status_code == 200
    login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    )
    assert login.status_code == 401
    assert client.get("/api/hygiene/admin/roster").status_code == 401
    assert client.post("/api/hygiene/admin/roster/1/approve").status_code == 401
    assert client.post("/api/hygiene/admin/roster/1/disable").status_code == 401
    assert client.patch(
        "/api/hygiene/admin/roster/1",
        json={"permission": "管理员"},
    ).status_code == 401
    assert client.post(
        "/api/hygiene/staff/shift",
        json={"shift": "白班"},
    ).status_code == 401
    assert client.post(
        "/api/hygiene/admin/roster/1/shift",
        json={"shift": "夜班"},
    ).status_code == 401


def test_staff_session_can_me_not_admin_roster_writes(hygiene_http):
    client, _db, accounts, _work = hygiene_http
    employee = _run(accounts.register(PHONE, PASSWORD))
    _run(accounts.approve(employee["id"]))
    login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    )
    assert login.status_code == 200
    assert client.cookies.get(settings.STAFF_SESSION_COOKIE_NAME)
    me = client.get("/api/hygiene/staff/me")
    assert me.status_code == 200
    assert me.json()["employee"]["phone"] == PHONE
    assert client.post(
        f"/api/hygiene/admin/roster/{employee['id']}/approve"
    ).status_code == 401
    assert client.patch(
        f"/api/hygiene/admin/roster/{employee['id']}",
        json={"permission": "管理员"},
    ).status_code == 401
    status = client.get("/api/auth/status").json()
    assert status["logged_in"] is False
    logout = client.post("/api/hygiene/staff/logout")
    assert logout.status_code == 200
    assert client.get("/api/hygiene/staff/me").status_code == 401


def test_admin_cookie_can_roster_but_is_not_staff_phone_identity(hygiene_http):
    client, _db, accounts, _work = hygiene_http
    init = client.post("/api/auth/init", json=ADMIN_INIT)
    assert init.status_code == 200
    employee = _run(accounts.register(PHONE, PASSWORD))
    roster = client.get("/api/hygiene/admin/roster")
    assert roster.status_code == 200
    assert roster.json()["employees"][0]["phone"] == PHONE
    approve = client.post(f"/api/hygiene/admin/roster/{employee['id']}/approve")
    assert approve.status_code == 200
    title = client.patch(
        f"/api/hygiene/admin/roster/{employee['id']}",
        json={"job_title": "领班", "permission": "普通员工"},
    )
    assert title.status_code == 200
    staff_as_admin = client.post(
        "/api/hygiene/staff/login",
        json={"phone": "admin", "password": "password123"},
    )
    assert staff_as_admin.status_code == 401
    assert client.get("/api/hygiene/staff/me").status_code == 401


def test_api_token_is_not_staff_session_or_roster_cookie(hygiene_http):
    client, _db, _accounts, _work = hygiene_http
    client.post("/api/auth/init", json=ADMIN_INIT)
    login = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "password123",
            "issue_api_token": True,
        },
    )
    token = login.json()["api_token"]
    client.cookies.clear()
    headers = {"X-Admin-Token": token}
    assert client.get("/api/hygiene/staff/me", headers=headers).status_code == 401
    assert client.get("/api/hygiene/admin/roster", headers=headers).status_code == 401
    assert client.post(
        "/api/hygiene/admin/roster/1/shift",
        headers=headers,
        json={"shift": "夜班"},
    ).status_code == 401


def test_staff_can_self_pick_shift_once_not_admin_fix(hygiene_http):
    client, _db, accounts, _work = hygiene_http
    employee = _run(accounts.register(PHONE, PASSWORD))
    _run(accounts.approve(employee["id"]))
    login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    )
    assert login.status_code == 200
    me = client.get("/api/hygiene/staff/me")
    assert me.status_code == 200
    assert me.json()["employee"]["shift"] is None
    picked = client.post("/api/hygiene/staff/shift", json={"shift": "白班"})
    assert picked.status_code == 200
    assert picked.json()["shift"] == "白班"
    assert client.get("/api/hygiene/staff/me").json()["employee"]["shift"] == "白班"
    again = client.post("/api/hygiene/staff/shift", json={"shift": "夜班"})
    assert again.status_code == 409
    assert client.get("/api/hygiene/staff/me").json()["employee"]["shift"] == "白班"
    assert client.post(
        f"/api/hygiene/admin/roster/{employee['id']}/shift",
        json={"shift": "夜班"},
    ).status_code == 401


def test_staff_admin_cannot_change_another_shift_admin_cookie_can(hygiene_http):
    client, _db, accounts, _work = hygiene_http
    staff = _run(accounts.register(PHONE, PASSWORD))
    manager = _run(accounts.register(PHONE_ADMIN, PASSWORD))
    _run(accounts.approve(staff["id"]))
    _run(accounts.approve(manager["id"]))
    _run(accounts.set_permission(manager["id"], "管理员"))
    _run(accounts.pick_shift(staff["id"], "白班"))

    staff_login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE_ADMIN, "password": PASSWORD},
    )
    assert staff_login.status_code == 200
    assert client.post(
        f"/api/hygiene/admin/roster/{staff['id']}/shift",
        json={"shift": "夜班"},
    ).status_code == 401
    assert _run(accounts.current_shift(staff["id"])) == "白班"

    client.cookies.clear()
    init = client.post("/api/auth/init", json=ADMIN_INIT)
    assert init.status_code == 200
    fixed = client.post(
        f"/api/hygiene/admin/roster/{staff['id']}/shift",
        json={"shift": "夜班"},
    )
    assert fixed.status_code == 200
    assert fixed.json()["shift"] == "夜班"
    roster = client.get("/api/hygiene/admin/roster")
    assert roster.status_code == 200
    row = next(item for item in roster.json()["employees"] if item["id"] == staff["id"])
    assert row["shift"] == "夜班"


def test_admin_cookie_can_create_zone_staff_cannot(hygiene_http):
    client, _db, accounts, _work = hygiene_http
    assert client.post(
        "/api/hygiene/admin/zones", json={"name": "卫生间"}
    ).status_code == 401
    employee = _run(accounts.register(PHONE, PASSWORD))
    _run(accounts.approve(employee["id"]))
    login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    )
    assert login.status_code == 200
    assert client.post(
        "/api/hygiene/admin/zones", json={"name": "卫生间"}
    ).status_code == 401
    client.cookies.clear()
    init = client.post("/api/auth/init", json=ADMIN_INIT)
    assert init.status_code == 200
    created = client.post("/api/hygiene/admin/zones", json={"name": "卫生间"})
    assert created.status_code == 200
    assert created.json()["zone"]["name"] == "卫生间"
    listed = client.get("/api/hygiene/admin/zones")
    assert listed.status_code == 200
    names = [zone["name"] for zone in listed.json()["zones"]]
    assert names == list(SEED_ZONE_NAMES) + ["卫生间"]


def test_staff_can_get_catalog_and_current_standard_after_login(hygiene_http):
    client, _db, accounts, work = hygiene_http
    employee = _run(accounts.register(PHONE, PASSWORD))
    _run(accounts.approve(employee["id"]))
    assert client.get("/api/hygiene/staff/daily-catalog").status_code == 401
    login = client.post(
        "/api/hygiene/staff/login",
        json={"phone": PHONE, "password": PASSWORD},
    )
    assert login.status_code == 200
    empty = client.get("/api/hygiene/staff/daily-catalog")
    assert empty.status_code == 200
    zones = empty.json()["zones"]
    assert [zone["name"] for zone in zones] == list(SEED_ZONE_NAMES)
    assert zones[0]["items"] == []
    anban = next(zone for zone in _run(work.list_zones()) if zone["name"] == "案板")
    photo = b"STAFF-CATALOG-JPEG-LITERAL"
    item = _run(
        work.add_daily_item(
            SUPER,
            anban["id"],
            "案板表面",
            {"bytes": photo, "content_type": "image/jpeg", "markup": []},
        )
    )
    catalog = client.get("/api/hygiene/staff/daily-catalog")
    assert catalog.status_code == 200
    listed = next(zone for zone in catalog.json()["zones"] if zone["name"] == "案板")
    assert [row["name"] for row in listed["items"]] == ["案板表面"]
    image = client.get(f"/api/hygiene/staff/items/{item['id']}/standard")
    assert image.status_code == 200
    assert image.content == photo
    assert image.headers["content-type"].startswith("image/jpeg")


def _approve_staff(accounts, phone, shift, permission="普通员工"):
    employee = _run(accounts.register(phone, PASSWORD))
    _run(accounts.approve(employee["id"]))
    if permission != "普通员工":
        _run(accounts.set_permission(employee["id"], permission))
    _run(accounts.pick_shift(employee["id"], shift))
    return employee


def _add_anban_item(work, data=b"STD-ANBAN"):
    anban = next(zone for zone in _run(work.list_zones()) if zone["name"] == "案板")
    return _run(
        work.add_daily_item(
            SUPER,
            anban["id"],
            "案板表面",
            {"bytes": data, "content_type": "image/jpeg", "markup": []},
        )
    )


def _staff_login(client, phone):
    client.cookies.clear()
    resp = client.post(
        "/api/hygiene/staff/login",
        json={"phone": phone, "password": PASSWORD},
    )
    assert resp.status_code == 200
    return resp


def _submit_daily(client, item_id, data, shift="白班", live="true"):
    return client.post(
        f"/api/hygiene/staff/daily/{item_id}/submit",
        data={"live": live, "shift": shift},
        files={"file": ("shot.jpg", data, "image/jpeg")},
    )


def test_staff_submit_allowed_regular_accept_403_admin_cookie_can_accept(hygiene_http):
    client, _db, accounts, work = hygiene_http
    _approve_staff(accounts, PHONE, "白班")
    _approve_staff(accounts, PHONE_ADMIN, "白班", "管理员")
    item = _add_anban_item(work)
    _staff_login(client, PHONE)
    inbox = client.get("/api/hygiene/staff/daily-work")
    assert inbox.status_code == 200
    rows = inbox.json()["items"]
    assert any(row["zone_name"] == "案板" and row["shift"] == "夜班" for row in rows)
    submitted = _submit_daily(client, item["id"], SHOT_A)
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "待验收"
    capture = client.get(
        f"/api/hygiene/staff/daily/{item['id']}/capture", params={"shift": "白班"}
    )
    assert capture.status_code == 200
    assert capture.content == SHOT_A
    frozen = client.get(
        f"/api/hygiene/staff/daily/{item['id']}/frozen-standard",
        params={"shift": "白班"},
    )
    assert frozen.status_code == 200
    assert frozen.content == b"STD-ANBAN"
    regular_accept = client.post(
        f"/api/hygiene/staff/daily/{item['id']}/accept", json={"shift": "白班"}
    )
    assert regular_accept.status_code == 403
    admin_via_staff = client.post(
        f"/api/hygiene/admin/daily/{item['id']}/accept", json={"shift": "白班"}
    )
    assert admin_via_staff.status_code == 401

    _staff_login(client, PHONE_ADMIN)
    replaced = _submit_daily(client, item["id"], SHOT_B)
    assert replaced.status_code == 200
    later = client.get(
        f"/api/hygiene/staff/daily/{item['id']}/capture", params={"shift": "白班"}
    )
    assert later.content == SHOT_B
    self_accept = client.post(
        f"/api/hygiene/staff/daily/{item['id']}/accept", json={"shift": "白班"}
    )
    assert self_accept.status_code == 403

    client.cookies.clear()
    init = client.post("/api/auth/init", json=ADMIN_INIT)
    assert init.status_code == 200
    queue = client.get("/api/hygiene/admin/daily-queue")
    assert queue.status_code == 200
    pending = queue.json()["items"]
    assert any(row["item_id"] == item["id"] and row["status"] == "待验收" for row in pending)
    admin_capture = client.get(
        f"/api/hygiene/admin/daily/{item['id']}/capture", params={"shift": "白班"}
    )
    assert admin_capture.status_code == 200
    assert admin_capture.content == SHOT_B
    accepted = client.post(
        f"/api/hygiene/admin/daily/{item['id']}/accept", json={"shift": "白班"}
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "已通过"


def test_night_staff_http_cannot_submit_day_instance(hygiene_http):
    client, _db, accounts, work = hygiene_http
    _approve_staff(accounts, PHONE_NIGHT, "夜班")
    item = _add_anban_item(work)
    _staff_login(client, PHONE_NIGHT)
    denied = _submit_daily(client, item["id"], SHOT_A, shift="白班")
    assert denied.status_code == 400
    album = _submit_daily(client, item["id"], SHOT_A, shift="夜班", live="false")
    assert album.status_code == 400
    ok = _submit_daily(client, item["id"], SHOT_A, shift="夜班")
    assert ok.status_code == 200
    _approve_staff(accounts, PHONE_OTHER_ADMIN, "夜班", "管理员")
    _staff_login(client, PHONE_OTHER_ADMIN)
    rejected = client.post(
        f"/api/hygiene/staff/daily/{item['id']}/reject", json={"shift": "夜班"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "待拍"


def test_staff_admin_cannot_patch_overdue_clocks_admin_cookie_can(hygiene_http):
    client, _db, accounts, work = hygiene_http
    payload = {"day_hhmm": "15:00", "night_hhmm": "21:30"}
    assert client.get("/api/hygiene/admin/overdue-clocks").status_code == 401
    assert client.patch("/api/hygiene/admin/overdue-clocks", json=payload).status_code == 401

    _approve_staff(accounts, PHONE_ADMIN, "白班", "管理员")
    _staff_login(client, PHONE_ADMIN)
    assert client.get("/api/hygiene/admin/overdue-clocks").status_code == 401
    assert client.patch("/api/hygiene/admin/overdue-clocks", json=payload).status_code == 401
    clocks = _run(work.get_daily_overdue_clocks())
    assert clocks["day_hhmm"] == "15:00"
    assert clocks["night_hhmm"] == "21:30"

    client.cookies.clear()
    init = client.post("/api/auth/init", json=ADMIN_INIT)
    assert init.status_code == 200
    listed = client.get("/api/hygiene/admin/overdue-clocks")
    assert listed.status_code == 200
    assert listed.json()["day_hhmm"] == "15:00"
    assert listed.json()["night_hhmm"] == "21:30"
    updated = client.patch(
        "/api/hygiene/admin/overdue-clocks",
        json={"day_hhmm": "16:00", "night_hhmm": "22:15"},
    )
    assert updated.status_code == 200
    assert updated.json()["day_hhmm"] == "16:00"
    assert updated.json()["night_hhmm"] == "22:15"
    again = client.get("/api/hygiene/admin/overdue-clocks")
    assert again.json()["day_hhmm"] == "16:00"
    assert again.json()["night_hhmm"] == "22:15"
    assert _run(work.get_daily_overdue_clocks()) == {
        "day_hhmm": "16:00",
        "night_hhmm": "22:15",
    }


def _submit_deep_clean(client, item_id, before=SHOT_A, after=SHOT_B, live="true"):
    return client.post(
        f"/api/hygiene/staff/deep-clean/{item_id}/submit",
        data={"live": live},
        files={
            "before": ("before.jpg", before, "image/jpeg"),
            "after": ("after.jpg", after, "image/jpeg"),
        },
    )


def test_staff_both_shifts_submit_deep_clean_staff_cannot_configure_admin_can(hygiene_http):
    client, _db, accounts, work = hygiene_http
    template = {"weekday": 6, "name": "冷柜一号"}
    assert client.post("/api/hygiene/admin/deep-clean/items", json=template).status_code == 401
    assert client.get("/api/hygiene/admin/deep-clean/items").status_code == 401
    assert client.get("/api/hygiene/admin/deep-clean/clock").status_code == 401
    assert client.patch(
        "/api/hygiene/admin/deep-clean/clock", json={"hhmm": "20:00"}
    ).status_code == 401

    _approve_staff(accounts, PHONE, "白班")
    _approve_staff(accounts, PHONE_NIGHT, "夜班")
    _approve_staff(accounts, PHONE_ADMIN, "白班", "管理员")
    _staff_login(client, PHONE)
    assert client.post("/api/hygiene/admin/deep-clean/items", json=template).status_code == 401
    assert client.patch(
        "/api/hygiene/admin/deep-clean/clock", json={"hhmm": "20:00"}
    ).status_code == 401
    empty = client.get("/api/hygiene/staff/deep-clean")
    assert empty.status_code == 200
    assert empty.json()["items"] == []

    client.cookies.clear()
    init = client.post("/api/auth/init", json=ADMIN_INIT)
    assert init.status_code == 200
    created = client.post("/api/hygiene/admin/deep-clean/items", json=template)
    assert created.status_code == 200
    item = created.json()["item"]
    assert item["name"] == "冷柜一号"
    second = client.post(
        "/api/hygiene/admin/deep-clean/items",
        json={"weekday": 6, "name": "冷柜二号"},
    )
    assert second.status_code == 200
    other = second.json()["item"]
    clock = client.patch("/api/hygiene/admin/deep-clean/clock", json={"hhmm": "20:00"})
    assert clock.status_code == 200
    assert clock.json()["hhmm"] == "20:00"
    listed = client.get("/api/hygiene/admin/deep-clean/items")
    assert listed.status_code == 200
    names = [row["name"] for row in listed.json()["items"] if row["weekday"] == 6]
    assert names == ["冷柜一号", "冷柜二号"]
    calendar = client.get(
        "/api/hygiene/admin/deep-clean/calendar",
        params={"from_date": "2026-09-13", "to_date": "2026-09-13"},
    )
    assert calendar.status_code == 200
    assert calendar.json()["days"][0]["status"] == "待办"

    _staff_login(client, PHONE)
    inbox = client.get("/api/hygiene/staff/deep-clean")
    assert inbox.status_code == 200
    assert [row["item_name"] for row in inbox.json()["items"]] == ["冷柜一号", "冷柜二号"]
    submitted = _submit_deep_clean(client, item["id"])
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "待验收"
    album = _submit_deep_clean(client, other["id"], live="false")
    assert album.status_code == 400
    before_img = client.get(f"/api/hygiene/staff/deep-clean/{item['id']}/before")
    assert before_img.status_code == 200
    assert before_img.content == SHOT_A
    after_img = client.get(f"/api/hygiene/staff/deep-clean/{item['id']}/after")
    assert after_img.status_code == 200
    assert after_img.content == SHOT_B
    self_accept = client.post(f"/api/hygiene/staff/deep-clean/{item['id']}/accept")
    assert self_accept.status_code == 403

    _staff_login(client, PHONE_NIGHT)
    night_inbox = client.get("/api/hygiene/staff/deep-clean")
    assert night_inbox.status_code == 200
    night_submit = _submit_deep_clean(client, other["id"], before=b"NIGHT-B", after=b"NIGHT-A")
    assert night_submit.status_code == 200
    assert night_submit.json()["status"] == "待验收"

    _staff_login(client, PHONE_ADMIN)
    accepted = client.post(f"/api/hygiene/staff/deep-clean/{item['id']}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "已通过"

    client.cookies.clear()
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "password123"},
    )
    assert login.status_code == 200
    queue = client.get("/api/hygiene/admin/deep-clean/queue")
    assert queue.status_code == 200
    pending = queue.json()["items"]
    assert any(row["item_id"] == other["id"] and row["status"] == "待验收" for row in pending)
    super_accept = client.post(f"/api/hygiene/admin/deep-clean/{other['id']}/accept")
    assert super_accept.status_code == 200
    assert super_accept.json()["status"] == "已通过"
    done = _run(work.list_deep_clean_work({"kind": "super"}))
    assert done["status"] == "已完成"
