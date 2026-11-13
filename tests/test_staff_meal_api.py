#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工餐采购（ADR 0135）：月度导入 → 单日填单 → 提交推送 的 HTTP 面。

主缝在服务层（`services/staff_meal/`），这里只回答几件用户真会撞上的事：

1. 导入：11 月表拆出 30 天、每天一张草稿单、菜单里的菜被物化进单据；重传同月先 409，
   确认后才覆盖菜单；
2. 权限：匿名 401；这一域不设能力开关（2026-11-11 用户裁定），任何员工都能读、
   都能填草稿单；改已提交单只有提交者本人和管理员；删草稿只有管理员；
3. 推送：提交即入队（`wecom_push_outbox` 一行），零订阅不算成功（标 failed 并告诉
   去订阅页勾上），重试会发新的一版（`push_seq` 递增、标题带「（更新）」）；
4. 手改菜单会同步草稿单一侧的菜品快照。
"""

import asyncio
import itertools
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.staff_meal as staff_meal_module
import main as main_module
from api.security import verify_admin_token
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.staff_meal.store import StaffMealStore
from services.wecom_push_topics import TOPIC_STAFF_MEAL_PURCHASE

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
PHONE2 = "13900139000"
NAME2 = "李四"
FIXED_NOW = datetime(2026, 11, 11, 10, 0, tzinfo=CHINA_TZ)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "staff_meal"


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
def staff_meal_http(tmp_path, monkeypatch):
    old_database_dir = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    monkeypatch.setattr(main_module, "employee_accounts", accounts)
    # 「今天」钉在夹具的固定日期：过期日校验（S6）按自然日判，真实时间会让
    # 「提交 11-12 的单」在 11-13 变成过期单，测试会随运行日期漂移。
    monkeypatch.setattr(
        StaffMealStore, "today", lambda self: FIXED_NOW.date().isoformat()
    )
    # 计划时刻（push schedule）用 store 自己的时钟判「是否已过 10 点」，不只 today：
    # 真实挂钟与夹具日期不一致会把「11-13 提交」判成已过点、错走立即推。
    # 每次读前进 1 秒：定时判定仍稳定，同时 updated_at 会推进（乐观锁用例依赖）。
    ticks = itertools.count(1)
    monkeypatch.setattr(
        StaffMealStore,
        "_now_dt",
        lambda self: FIXED_NOW + timedelta(seconds=next(ticks)),
    )

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(staff_meal_module.reader_router)
    app.include_router(staff_meal_module.manager_router)
    app.include_router(
        staff_meal_module.router, dependencies=[Depends(verify_admin_token)]
    )
    with TestClient(app) as client:
        init = client.post("/api/auth/init", json=ADMIN)
        assert init.status_code == 200, init.text
        login = client.post(
            "/api/auth/login",
            json={"username": ADMIN["username"], "password": ADMIN["password"]},
        )
        assert login.status_code == 200, login.text
        yield client, db, accounts
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old_database_dir


# ── 夹具 helper ─────────────────────────────────────────────────────────────


def _admin_cookie(client):
    client.cookies.clear()
    login = client.post(
        "/api/auth/login",
        json={"username": ADMIN["username"], "password": ADMIN["password"]},
    )
    assert login.status_code == 200, login.text


def _employee(accounts, phone=PHONE, name=NAME, caps=None):
    employee = _run(accounts.register(phone, PASSWORD, name))
    _run(accounts.approve(employee["id"]))
    if caps is not None:
        _run(accounts.set_admin_caps(employee["id"], caps))
    return int(employee["id"])


def _staff_cookie(client, accounts, phone=PHONE):
    session_id = _run(accounts.login(phone, PASSWORD))["session_id"]
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, session_id)


def _month_bytes(month="11"):
    return (FIXTURE_DIR / f"2026-{month}.xlsx").read_bytes()


def _import(client, month="11", year="2026", confirm=False):
    data = {"year": year, "month": month}
    if confirm:
        data["confirm"] = "true"
    return client.post(
        "/api/staff-meal/menu/import",
        files={
            "file": (
                f"2026-{month}.xlsx",
                _month_bytes(month),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data=data,
    )


def _preview(client, month="11", year="2026"):
    return client.post(
        "/api/staff-meal/menu/import/preview",
        files={
            "file": (
                f"2026-{month}.xlsx",
                _month_bytes(month),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"year": year, "month": month},
    )


def _day(client, order_date):
    response = client.get(f"/api/staff-meal/days/{order_date}")
    assert response.status_code == 200, response.text
    return response.json()


def _dish_id(day_payload, dish_name):
    for dish in day_payload["dishes"]:
        if dish["dish_name"] == dish_name:
            return dish["id"]
    raise AssertionError(f"单据里没有菜「{dish_name}」")


def _save(client, order_date, items, remark=None, expected_updated_at=None):
    payload = {"items": items}
    if remark is not None:
        payload["remark"] = remark
    if expected_updated_at is not None:
        payload["expected_updated_at"] = expected_updated_at
    return client.put(f"/api/staff-meal/days/{order_date}/order", json=payload)


def _submit(client, order_date):
    return client.post(f"/api/staff-meal/days/{order_date}/order/submit")


def _outbox(db):
    async def _scenario():
        cursor = await db._conn.execute(
            "SELECT id, topic_id, status, idempotency_key, params_json, scheduled_at"
            " FROM wecom_push_outbox ORDER BY id"
        )
        return [dict(row) for row in await cursor.fetchall()]

    return _run(_scenario())


def _subscribe_meal_topic(db):
    async def _scenario():
        now = FIXED_NOW.isoformat()
        cursor = await db._conn.execute(
            "INSERT INTO wecom_push_webhooks"
            " (name, webhook_url_encrypted, webhook_url_masked, enabled, notes,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, 1, '', ?, ?) RETURNING id",
            ("核心群", "encrypted-placeholder", "masked", now, now),
        )
        channel_id = int((await cursor.fetchone())["id"])
        await db._conn.execute(
            "INSERT INTO wecom_push_subscriptions"
            " (topic_id, target_channel_id, enabled, created_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?)",
            (TOPIC_STAFF_MEAL_PURCHASE, channel_id, now, now),
        )
        await db._conn.commit()
        return channel_id

    return _run(_scenario())


def _mark_outbox_sent(db):
    async def _scenario():
        await db._conn.execute(
            "UPDATE wecom_push_outbox SET status = 'sent', finished_at = ?",
            (FIXED_NOW.isoformat(),),
        )
        await db._conn.commit()

    _run(_scenario())


# ── 身份门 ──────────────────────────────────────────────────────────────────


def test_anonymous_is_rejected_everywhere(staff_meal_http):
    client, _db, _accounts = staff_meal_http
    client.cookies.clear()

    assert client.get("/api/staff-meal/months/2026-11").status_code == 401
    assert client.get("/api/staff-meal/days/2026-11-13").status_code == 401
    assert client.get("/api/staff-meal/suggestions?dish=排骨").status_code == 401
    assert client.get("/api/staff-meal/meta").status_code == 401
    assert client.get("/api/staff-meal/menu/template").status_code == 401
    assert client.put("/api/staff-meal/days/2026-11-13/order", json={"items": []}).status_code == 401
    assert client.post("/api/staff-meal/days/2026-11-13/order/submit").status_code == 401
    # 管理端写面（导入 / 手改菜单 / 删单）在注册处挂管理员凭据：匿名连门都进不去。
    assert client.post("/api/staff-meal/menu/import").status_code == 401
    assert client.put("/api/staff-meal/menu/days/2026-11-13", json={}).status_code == 401
    assert client.delete("/api/staff-meal/orders/1").status_code == 401


def test_plain_staff_can_read_and_fill_without_any_capability(staff_meal_http):
    """没勾任何能力的员工也能读月历、填草稿单 —— 这一域不设能力开关（2026-11-11 裁定）。"""
    client, _db, accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200
    _employee(accounts)
    _staff_cookie(client, accounts)

    assert client.get("/api/staff-meal/months/2026-11").status_code == 200
    assert client.get("/api/staff-meal/days/2026-11-13").status_code == 200
    assert client.get("/api/staff-meal/meta").status_code == 200
    assert _save(
        client, "2026-11-13", [{"item_name": "清远鸡", "quantity": 3, "unit": "只"}]
    ).status_code == 200
    assert _submit(client, "2026-11-13").status_code == 200


def test_admin_imports_and_staff_with_cap_reads(staff_meal_http):
    client, _db, accounts = staff_meal_http
    _admin_cookie(client)

    preview = _preview(client)
    assert preview.status_code == 200, preview.text
    assert preview.json()["days"] == 30
    assert preview.json()["existing_orders"] == []
    assert preview.json()["freeform_days"] == 5

    imported = _import(client)
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["month"] == "2026-11"
    assert body["orders_created"] == 30
    assert body["submitted_kept"] == 0

    month = client.get("/api/staff-meal/months/2026-11")
    assert month.status_code == 200, month.text
    summary = month.json()["summary"]
    assert summary["days"] == 30
    assert summary["orders"] == 30
    assert summary["draft"] == 30
    assert summary["submitted"] == 0
    assert summary["failed"] == 0

    # 员工身份能读到同一个月（这一域不设能力开关，读面不区分谁）。
    employee_id = _employee(accounts)
    _staff_cookie(client, accounts)
    assert client.get("/api/staff-meal/months/2026-11").status_code == 200
    assert client.get("/api/staff-meal/days/2026-11-13").status_code == 200
    assert employee_id > 0


def test_preview_reports_drafts_with_filled_items(staff_meal_http):
    """重传预览要摆出「哪些草稿已经填了东西、填了几项」（S9），超管才敢决定重传。"""
    client, _db, _accounts = staff_meal_http
    _admin_cookie(client)

    empty = _preview(client)
    assert empty.status_code == 200, empty.text
    assert empty.json()["draft_with_items"] == []

    assert _import(client).status_code == 200
    assert (
        _save(
            client,
            "2026-11-13",
            [
                {"item_name": "清远鸡", "quantity": 3, "unit": "只"},
                {"item_name": "沙姜", "quantity": 0.5, "unit": "斤"},
            ],
        ).status_code
        == 200
    )
    assert (
        _save(
            client, "2026-11-14", [{"item_name": "南瓜", "quantity": 5, "unit": "斤"}]
        ).status_code
        == 200
    )
    # 11-15 是没填过的草稿；11-16 已提交 —— 两边都不该出现在 draft_with_items。
    assert (
        _save(
            client, "2026-11-16", [{"item_name": "排骨", "quantity": 9, "unit": "斤"}]
        ).status_code
        == 200
    )
    assert _submit(client, "2026-11-16").status_code == 200

    preview = _preview(client)
    assert preview.status_code == 200, preview.text
    assert preview.json()["draft_with_items"] == [
        {"order_date": "2026-11-13", "filled_items": 2},
        {"order_date": "2026-11-14", "filled_items": 1},
    ]


def test_menu_template_downloads_and_imports_as_full_month(staff_meal_http):
    """空模板（S10）：管理端能下载，且真的能被导入预览解析成整月 31 天。"""
    client, _db, _accounts = staff_meal_http
    _admin_cookie(client)

    response = client.get("/api/staff-meal/menu/template")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "attachment" in response.headers.get("content-disposition", "")
    template = response.content
    assert template[:2] == b"PK", "xlsx 是 zip 容器"

    preview = client.post(
        "/api/staff-meal/menu/import/preview",
        files={
            "file": (
                "staff-meal-menu-template.xlsx",
                template,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"year": "2026", "month": "12"},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["days"] == 31
    assert body["freeform_days"] == 0
    assert body["existing_orders"] == []


def test_menu_template_by_month_has_exact_days_and_filename(staff_meal_http):
    """按月份生成的模板（task-4）：30/28 天月份不用手删日期列，文件名带年月。"""
    client, _db, _accounts = staff_meal_http
    _admin_cookie(client)

    # 2026-09 = 30 天、2026-02 = 28 天（闰年 2028-02 = 29 天）都按实际天数生成。
    for year, month, days in ((2026, 9, 30), (2026, 2, 28), (2028, 2, 29)):
        response = client.get(
            "/api/staff-meal/menu/template", params={"year": year, "month": month}
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        disposition = response.headers.get("content-disposition", "")
        filename = f"员工餐菜单模板-{year:04d}-{month:02d}.xlsx"
        assert "attachment" in disposition
        assert quote(filename) in disposition
        assert response.content[:2] == b"PK", "xlsx 是 zip 容器"

        preview = client.post(
            "/api/staff-meal/menu/import/preview",
            files={
                "file": (
                    filename,
                    response.content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"year": str(year), "month": str(month)},
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert (body["year"], body["month_number"]) == (year, month)
        assert body["days"] == days
        assert body["freeform_days"] == 0
        assert body["existing_orders"] == []

    # 非法参数：400 + 可照做的中文；不会落到「生成一个坏文件」。
    bad_month = client.get(
        "/api/staff-meal/menu/template", params={"year": 2026, "month": 13}
    )
    assert bad_month.status_code == 400, bad_month.text
    assert "1–12" in bad_month.json()["detail"]

    bad_year = client.get(
        "/api/staff-meal/menu/template", params={"year": 2019, "month": 9}
    )
    assert bad_year.status_code == 400, bad_year.text
    assert "2020–2100" in bad_year.json()["detail"]

    half = client.get("/api/staff-meal/menu/template", params={"year": 2026})
    assert half.status_code == 400, half.text
    assert "一起传" in half.json()["detail"]


def test_expired_day_keeps_draft_editable_but_refuses_submit(staff_meal_http):
    """使用日已过（S6）：草稿还能改能存，但不能再提交（群里不能收到过期清单）。"""
    client, _db, _accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200

    # 夹具今天 = 2026-11-11；11-10 已经过去。
    assert (
        _save(
            client, "2026-11-10", [{"item_name": "清远鸡", "quantity": 2, "unit": "只"}]
        ).status_code
        == 200
    )
    submitted = _submit(client, "2026-11-10")
    assert submitted.status_code == 409, submitted.text
    assert submitted.json()["detail"] == (
        "2026-11-10 已经过去了：草稿还能改，但不能再提交/推送"
    )

    # 今天及以后不受限制。
    assert (
        _save(
            client, "2026-11-11", [{"item_name": "南瓜", "quantity": 5, "unit": "斤"}]
        ).status_code
        == 200
    )
    assert _submit(client, "2026-11-11").status_code == 200


def test_save_rejects_stale_version_but_accepts_omitted(staff_meal_http):
    """乐观锁（F14）：带旧版本保存 → 409；不带 expected_updated_at 保持旧行为。"""
    client, _db, _accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200

    first = _save(
        client, "2026-11-13", [{"item_name": "清远鸡", "quantity": 3, "unit": "只"}]
    )
    assert first.status_code == 200, first.text
    stale = _day(client, "2026-11-13")["order"]["updated_at"]
    assert stale

    # 拿着当前版本保存 → 通过，版本随之推进。
    accepted = _save(
        client,
        "2026-11-13",
        [{"item_name": "清远鸡", "quantity": 4, "unit": "只"}],
        expected_updated_at=stale,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["order"]["updated_at"] != stale

    # 同一个旧版本再保存一次（另一台页面还没刷新）→ 409，不能静默覆盖。
    conflict = _save(
        client,
        "2026-11-13",
        [{"item_name": "清远鸡", "quantity": 8, "unit": "只"}],
        expected_updated_at=stale,
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"] == "这张单刚被别人改过：刷新后再保存"
    assert (
        _day(client, "2026-11-13")["other_items"][0]["quantity"] == 4
    ), "冲突的保存不能落库"

    # 不带版本（旧前端 / 脚本调用）→ 照旧可保存。
    assert (
        _save(
            client,
            "2026-11-13",
            [{"item_name": "清远鸡", "quantity": 5, "unit": "只"}],
        ).status_code
        == 200
    )


def test_day_shapes_menu_dishes_and_order(staff_meal_http):
    client, _db, _accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200

    day = _day(client, "2026-11-13")
    assert day["menu_day"]["lunch_main"] == "辣子鸡"
    assert day["menu_day"]["drink"] == "圣女果"
    assert day["order"]["status"] == "draft"
    assert day["order"]["push_status"] == "not_pushed"
    assert [dish["dish_name"] for dish in day["dishes"]] == [
        "辣子鸡",
        "平菇炒牛肉丸",
        "耗油娃娃菜",
        "菠萝粒酸甜排骨",
        "洋葱炒猪颈肉",
        "耗油娃娃菜",
    ]
    assert all(dish["items"] == [] for dish in day["dishes"])
    assert day["other_items"] == []


def test_reimport_needs_confirm_and_keeps_submitted_orders(staff_meal_http):
    client, _db, accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200

    # 12 号提交一张单，重传菜单时它的菜品快照不能被改写。
    saved = _save(
        client,
        "2026-11-12",
        [{"item_name": "金针菇", "quantity": 2, "unit": "斤"}],
    )
    assert saved.status_code == 200, saved.text
    assert _submit(client, "2026-11-12").status_code == 200
    submitted_day = _day(client, "2026-11-12")
    assert submitted_day["order"]["status"] == "submitted"
    submitted_dishes = [dish["dish_name"] for dish in submitted_day["dishes"]]

    conflict = _import(client)
    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail["code"] == "menu_exists"
    assert detail["existing_orders"] == 30
    assert detail["submitted_days"] == ["2026-11-12"]

    confirmed = _import(client, confirm=True)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["draft_refreshed"] == 29
    assert confirmed.json()["submitted_kept"] == 1

    after = _day(client, "2026-11-12")
    assert [dish["dish_name"] for dish in after["dishes"]] == submitted_dishes
    assert after["other_items"][0]["item_name"] == "金针菇"
    assert accounts is not None


def test_submit_without_quantity_is_rejected(staff_meal_http):
    client, _db, _accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200

    assert _submit(client, "2026-11-13").status_code == 409
    # 只有名字没有数量的行不算「填了」（Q9：名字是带出来的种子，数量要人填）。
    saved = _save(
        client,
        "2026-11-13",
        [{"item_name": "清远鸡", "unit": "只"}],
    )
    assert saved.status_code == 200, saved.text
    assert _submit(client, "2026-11-13").status_code == 409


def test_submit_zero_subscription_marks_failed_then_retry_enqueues(staff_meal_http):
    client, db, accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200

    day = _day(client, "2026-11-13")
    dish_id = _dish_id(day, "辣子鸡")
    saved = _save(
        client,
        "2026-11-13",
        [
            {"dish_id": dish_id, "item_name": "清远鸡", "quantity": 3, "unit": "只"},
            {"dish_id": dish_id, "item_name": "沙姜", "quantity": 0.5, "unit": "斤"},
            {"item_name": "柱侯酱", "quantity": 2, "unit": "瓶"},
        ],
        remark="鸡要现杀的",
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["other_items"][0]["item_name"] == "柱侯酱"

    # 零订阅：入队返回空，不能假装成功。
    submitted = _submit(client, "2026-11-13")
    assert submitted.status_code == 200, submitted.text
    order = submitted.json()["order"]
    assert order["status"] == "submitted"
    assert order["push_status"] == "failed"
    assert "没有任何群订阅" in (order["push_error"] or "")
    assert order["submitter_name"] == "管理端"
    assert order["submitted_at"]

    # 摘要里的「推送失败 N 张」要真的数得出来（S1）。
    month = client.get("/api/staff-meal/months/2026-11")
    assert month.status_code == 200, month.text
    assert month.json()["summary"]["failed"] == 1

    # 订阅之后重试：真的入队一行，状态 pushing。
    _subscribe_meal_topic(db)
    retried = client.post(f"/api/staff-meal/orders/{order['id']}/retry-push")
    assert retried.status_code == 200, retried.text
    assert retried.json()["order"]["push_status"] == "pushing"

    rows = _outbox(db)
    assert len(rows) == 1
    assert rows[0]["topic_id"] == TOPIC_STAFF_MEAL_PURCHASE
    # 首次提交（零订阅）已经消耗了第 1 版；重试是新的一版才能绕开出站幂等键。
    assert rows[0]["idempotency_key"].startswith(
        "staff_meal_purchase:staff_meal_purchase:2026-11-13:2:"
    )
    text = rows[0]["params_json"]
    assert "【员工餐】采购计划 2026-11-13" in text
    assert "提交人：管理端" in text
    # 备注随提交 / 重试一起进群消息（S4）：提交人之后、清单之前。
    assert "备注：鸡要现杀的" in text
    assert "清远鸡 3只" in text
    assert "沙姜 0.5斤" in text
    assert "柱侯酱 2瓶" in text

    # 出站行发送成功后，页面读到 pushed（读时映射，不靠回调写业务表）。
    _mark_outbox_sent(db)
    pushed = _day(client, "2026-11-13")
    assert pushed["order"]["push_status"] == "pushed"

    # 改后重推：push_seq 递增，标题带「（更新）」。
    edited = _save(
        client,
        "2026-11-13",
        [{"dish_id": dish_id, "item_name": "清远鸡", "quantity": 4, "unit": "只"}],
    )
    assert edited.status_code == 200, edited.text
    resubmitted = _submit(client, "2026-11-13")
    assert resubmitted.status_code == 200, resubmitted.text
    rows = _outbox(db)
    assert len(rows) == 2
    assert rows[1]["idempotency_key"].startswith(
        "staff_meal_purchase:staff_meal_purchase:2026-11-13:3:"
    )
    assert "（更新）" in rows[1]["params_json"]
    assert "清远鸡 4只" in rows[1]["params_json"]


def test_only_submitter_or_admin_can_edit_submitted_order(staff_meal_http):
    client, _db, accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200

    _employee(accounts, phone=PHONE, name=NAME)
    _employee(accounts, phone=PHONE2, name=NAME2)
    _staff_cookie(client, accounts, phone=PHONE)
    day = _day(client, "2026-11-13")
    assert _save(
        client,
        "2026-11-13",
        [{"item_name": "清远鸡", "quantity": 3, "unit": "只"}],
    ).status_code == 200
    submitted = _submit(client, "2026-11-13")
    assert submitted.status_code == 200, submitted.text
    order_id = submitted.json()["order"]["id"]
    assert submitted.json()["order"]["submitter_name"] == NAME

    # 另一个员工：已提交单不归他，403。
    _staff_cookie(client, accounts, phone=PHONE2)
    forbidden = _save(
        client,
        "2026-11-13",
        [{"item_name": "清远鸡", "quantity": 8, "unit": "只"}],
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == (
        "这张采购单已经提交了：只有提交者本人或超级管理员能改"
    )
    forbidden_retry = client.post(f"/api/staff-meal/orders/{order_id}/retry-push")
    assert forbidden_retry.status_code == 403
    assert forbidden_retry.json()["detail"] == (
        "这张采购单已经提交了：只有提交者本人或超级管理员能改"
    )

    # 提交者本人能改（改完再提交就是重推）。
    _staff_cookie(client, accounts, phone=PHONE)
    assert _save(
        client,
        "2026-11-13",
        [{"item_name": "清远鸡", "quantity": 5, "unit": "只"}],
    ).status_code == 200

    # 管理端不受「提交者本人」限制。
    _admin_cookie(client)
    assert _save(
        client,
        "2026-11-13",
        [{"item_name": "清远鸡", "quantity": 6, "unit": "只"}],
    ).status_code == 200


def test_delete_draft_is_admin_only(staff_meal_http):
    client, _db, accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200
    order = _day(client, "2026-11-14")["order"]

    # 员工删不了（管理端写面在注册处挂了管理员凭据 → 401，而不是 403）。
    _employee(accounts)
    _staff_cookie(client, accounts)
    assert client.delete(f"/api/staff-meal/orders/{order['id']}").status_code == 401

    _admin_cookie(client)
    deleted = client.delete(f"/api/staff-meal/orders/{order['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["order_date"] == "2026-11-14"

    # 已提交的单不能删。
    other = _day(client, "2026-11-15")["order"]
    assert _save(
        client,
        "2026-11-15",
        [{"item_name": "排骨", "quantity": 9, "unit": "斤"}],
    ).status_code == 200
    assert _submit(client, "2026-11-15").status_code == 200
    refused = client.delete(f"/api/staff-meal/orders/{other['id']}")
    assert refused.status_code == 409
    assert "已提交" in refused.json()["detail"]


def test_manual_menu_update_syncs_draft_dishes(staff_meal_http):
    client, _db, accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200

    updated = client.put(
        "/api/staff-meal/menu/days/2026-11-13",
        json={"lunch_main": "白切鸡", "lunch_side1": "炒青菜", "lunch_side2": None},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["menu_day"]["lunch_main"] == "白切鸡"
    assert body["menu_day"]["source"] == "manual"
    names = [dish["dish_name"] for dish in body["dishes"]]
    assert names[0] == "白切鸡"
    assert "辣子鸡" not in names

    # 员工手改菜单是管理端的活：员工会话连门都过不去（401）。
    _employee(accounts)
    _staff_cookie(client, accounts)
    assert (
        client.put(
            "/api/staff-meal/menu/days/2026-11-13", json={"lunch_main": "豉油鸡"}
        ).status_code
        == 401
    )


def test_suggestions_carry_dish_item_names_without_quantities(staff_meal_http):
    client, _db, _accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200

    day = _day(client, "2026-11-13")
    dish_id = _dish_id(day, "辣子鸡")
    assert _save(
        client,
        "2026-11-13",
        [
            {"dish_id": dish_id, "item_name": "清远鸡", "quantity": 3, "unit": "只"},
            {"dish_id": dish_id, "item_name": "沙姜", "quantity": 0.5, "unit": "斤"},
        ],
    ).status_code == 200
    # 另一天的同名菜（2026-11-13 的辣子鸡只有这一天，就用同一张单验证历史来源）。
    suggestions = client.get("/api/staff-meal/suggestions?dish=辣子鸡")
    assert suggestions.status_code == 200, suggestions.text
    items = suggestions.json()["items"]
    assert {row["item_name"] for row in items} == {"清远鸡", "沙姜"}
    for row in items:
        assert "quantity" not in row
        assert "uses" in row


def test_meta_exposes_units_and_limits(staff_meal_http):
    client, _db, _accounts = staff_meal_http
    _admin_cookie(client)
    meta = client.get("/api/staff-meal/meta")
    assert meta.status_code == 200
    body = meta.json()
    assert body["units"][0] == "斤"
    assert body["max_items"] >= 100
    assert body["max_quantity"] >= 9999


def test_bad_parameters_get_readable_errors(staff_meal_http):
    client, _db, _accounts = staff_meal_http
    _admin_cookie(client)

    assert client.get("/api/staff-meal/months/2026-13").status_code == 400
    assert client.get("/api/staff-meal/days/2026-11-31").status_code in (200, 400, 404)

    # 没导入的月份：文案指向「超级管理员」（S5）。
    missing = client.get("/api/staff-meal/days/2026-12-01")
    assert missing.status_code == 404
    assert "请让超级管理员先导入当月表格" in missing.json()["detail"]

    missing_menu = client.put(
        "/api/staff-meal/menu/days/2026-12-01", json={"lunch_main": "白切鸡"}
    )
    assert missing_menu.status_code == 404
    assert "或让超级管理员在这里新建" in missing_menu.json()["detail"]

    # 传错月份的表：标题与参数不符要在导入时当场报错（别把 9 月菜单写进 11 月）。
    wrong = client.post(
        "/api/staff-meal/menu/import",
        files={
            "file": (
                "2026-09.xlsx",
                _month_bytes("09"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"year": "2026", "month": "11"},
    )
    assert wrong.status_code == 400
    assert "9" in wrong.json()["detail"] or "月份" in wrong.json()["detail"]

    not_excel = client.post(
        "/api/staff-meal/menu/import",
        files={"file": ("menu.xlsx", b"hello", "application/vnd.ms-excel")},
        data={"year": "2026", "month": "11"},
    )
    assert not_excel.status_code == 400


# ── 定时推送（`.scratch/wb-review/push-schedule-contract.md`） ─────────────


def _fill_and_submit(client, order_date):
    """填一项「其他采购项」并提交，返回单据详情（不依赖当天菜单里的菜）。"""
    saved = _save(
        client, order_date, [{"item_name": "土豆", "quantity": 1, "unit": "斤"}]
    )
    assert saved.status_code == 200, saved.text
    submitted = _submit(client, order_date)
    assert submitted.status_code == 200, submitted.text
    return submitted.json()["order"]


def test_submit_schedules_far_days_and_pushes_soon_ones(staff_meal_http):
    """今天 / 明天提交立即推；≥ 后天排到使用日前一天 10:00（契约 §1/§2/§5）。"""
    client, db, _accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200
    _subscribe_meal_topic(db)

    today = _fill_and_submit(client, "2026-11-11")
    assert today["push_status"] == "pushing"
    assert today["planned_push_at"] is None

    tomorrow = _fill_and_submit(client, "2026-11-12")
    assert tomorrow["push_status"] == "pushing"
    assert tomorrow["planned_push_at"] is None

    day_after = _fill_and_submit(client, "2026-11-13")
    assert day_after["push_status"] == "scheduled"
    assert day_after["planned_push_at"] == "2026-11-12T10:00:00+08:00"

    far = _fill_and_submit(client, "2026-11-17")
    assert far["push_status"] == "scheduled"
    assert far["planned_push_at"] == "2026-11-16T10:00:00+08:00"

    # 出站行按计划落 scheduled_at：待发行的 10:00 由调度循环把守，不发就是不发。
    scheduled_rows = {
        row["idempotency_key"].split(":")[2]: row
        for row in _outbox(db)
        if row["scheduled_at"]
    }
    assert set(scheduled_rows) == {"2026-11-13", "2026-11-17"}
    assert (
        scheduled_rows["2026-11-13"]["scheduled_at"]
        == "2026-11-12T10:00:00+08:00"
    )
    assert (
        scheduled_rows["2026-11-17"]["scheduled_at"]
        == "2026-11-16T10:00:00+08:00"
    )
    immediate_rows = [row for row in _outbox(db) if row["scheduled_at"] is None]
    assert {row["idempotency_key"].split(":")[2] for row in immediate_rows} == {
        "2026-11-11",
        "2026-11-12",
    }

    # 月历摘要与 brief 要带得出「待推送」：页面据此显示「待推送 N 张」。
    month = client.get("/api/staff-meal/months/2026-11").json()
    assert month["summary"]["scheduled"] == 2
    briefs = {
        day["order"]["order_date"]: day["order"]
        for day in month["days"]
        if day["order"] is not None
    }
    assert briefs["2026-11-13"]["planned_push_at"] == "2026-11-12T10:00:00+08:00"
    assert briefs["2026-11-17"]["planned_push_at"] == "2026-11-16T10:00:00+08:00"
    assert briefs["2026-11-11"]["planned_push_at"] is None
    assert briefs["2026-11-13"]["push_status"] == "scheduled"

    day = _day(client, "2026-11-13")
    assert day["order"]["push_status"] == "scheduled"


def test_fill_on_day_before_after_ten_is_immediate(staff_meal_http, monkeypatch):
    """使用日前一天 10 点后才补填的远期单：计划时刻已过，立即推（契约 §1/§6）。"""
    client, db, _accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200
    _subscribe_meal_topic(db)

    later = datetime(2026, 11, 12, 11, 0, tzinfo=CHINA_TZ)
    monkeypatch.setattr(StaffMealStore, "today", lambda self: "2026-11-12")
    monkeypatch.setattr(StaffMealStore, "_now_dt", lambda self: later)

    order = _fill_and_submit(client, "2026-11-13")
    assert order["push_status"] == "pushing"
    assert order["planned_push_at"] is None


def test_draft_stays_unpushed_until_submitted_after_ten(
    staff_meal_http, monkeypatch
):
    """到点仍是草稿不推；10 点后一提交立即推（契约 §6）。"""
    client, db, _accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200
    _subscribe_meal_topic(db)

    later = datetime(2026, 11, 16, 10, 30, tzinfo=CHINA_TZ)
    monkeypatch.setattr(StaffMealStore, "today", lambda self: "2026-11-16")
    monkeypatch.setattr(StaffMealStore, "_now_dt", lambda self: later)

    draft = _day(client, "2026-11-17")
    assert draft["order"]["status"] == "draft"
    assert _outbox(db) == [], "草稿不该有出站行，到点也不推"

    order = _fill_and_submit(client, "2026-11-17")
    assert order["push_status"] == "pushing"
    assert order["planned_push_at"] is None


def test_resubmit_before_ten_replaces_pending_schedule(staff_meal_http):
    """10 点前反复改单：旧待发行作废，只留最新一版 scheduled（契约 §4/§6）。"""
    client, db, _accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200
    _subscribe_meal_topic(db)

    first = _fill_and_submit(client, "2026-11-17")
    assert first["push_status"] == "scheduled"
    first_rows = _outbox(db)
    assert len(first_rows) == 1
    assert first_rows[0]["scheduled_at"] == "2026-11-16T10:00:00+08:00"

    second = _fill_and_submit(client, "2026-11-17")
    assert second["push_status"] == "scheduled"
    rows = _outbox(db)
    assert len(rows) == 1, "旧 pending 行应被作废，同一张单只留一版待发"
    assert rows[0]["idempotency_key"].startswith(
        "staff_meal_purchase:staff_meal_purchase:2026-11-17:2:"
    )
    assert rows[0]["scheduled_at"] == "2026-11-16T10:00:00+08:00"


def test_resubmit_after_due_is_immediate(staff_meal_http, monkeypatch):
    """10 点后改单：旧待发行作废，新一版立即推（标题「（更新）」）。"""
    client, db, _accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200
    _subscribe_meal_topic(db)

    first = _fill_and_submit(client, "2026-11-17")
    assert first["push_status"] == "scheduled"
    assert len(_outbox(db)) == 1

    later = datetime(2026, 11, 16, 10, 30, tzinfo=CHINA_TZ)
    monkeypatch.setattr(StaffMealStore, "today", lambda self: "2026-11-16")
    monkeypatch.setattr(StaffMealStore, "_now_dt", lambda self: later)

    second = _fill_and_submit(client, "2026-11-17")
    assert second["push_status"] == "pushing"
    assert second["planned_push_at"] is None
    rows = _outbox(db)
    assert len(rows) == 1
    assert rows[0]["idempotency_key"].startswith(
        "staff_meal_purchase:staff_meal_purchase:2026-11-17:2:"
    )
    assert rows[0]["scheduled_at"] is None


def test_retry_push_is_immediate_and_clears_plan(staff_meal_http):
    """手动重试永远立即：失败单的 planned_push_at 清空，新行不带 scheduled_at（契约 §3）。"""
    client, db, _accounts = staff_meal_http
    _admin_cookie(client)
    assert _import(client).status_code == 200

    first = _fill_and_submit(client, "2026-11-17")
    assert first["push_status"] == "failed"
    assert first["planned_push_at"] is None
    assert _outbox(db) == []

    _subscribe_meal_topic(db)
    retried = client.post(f"/api/staff-meal/orders/{first['id']}/retry-push")
    assert retried.status_code == 200, retried.text
    order = retried.json()["order"]
    assert order["push_status"] == "pushing"
    assert order["planned_push_at"] is None

    rows = _outbox(db)
    assert len(rows) == 1
    assert rows[0]["idempotency_key"].startswith(
        "staff_meal_purchase:staff_meal_purchase:2026-11-17:2:"
    )
    assert rows[0]["scheduled_at"] is None

    month = client.get("/api/staff-meal/months/2026-11").json()
    assert month["summary"]["scheduled"] == 0
    assert month["summary"]["failed"] == 0
