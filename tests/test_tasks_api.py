#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作台「任务」：票 01 的地基面（表 / 能力门 / 照片上传）。

票 01 的验收只回答三件事：

1. 迁移 0054 的七张表真在测试库里，`(definition_id, period_date)` 的唯一约束真拦得住
   重复实例（后面每一票都靠它做幂等）；
2. 「谁能发布 / 验收」从第一天起就按真判据跑：匿名 401、员工没勾「任务管理」403、
   管理端与勾了的员工放行（业务还没接上的端点回 501 也算放行 —— 门在门前，不在门后）；
3. 照片上传的两种用途（完成照必须 `live=true`、参考图允许相册）与 20 MB / 类型嗅探
   这几条硬限制。

业务路径（发布 → 提交 → 验收）在票 02~04 的测试里，不在这里。
"""

import asyncio
import io
from datetime import date, datetime

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.tasks as tasks_module
import main as main_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.tasks.service import TaskService

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-24"

TASK_TABLES = {
    "work_task_definitions",
    "work_task_definition_members",
    "work_task_instances",
    "work_task_instance_members",
    "work_task_submissions",
    "work_task_submission_photos",
    "work_task_images",
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


def _jpeg_bytes(color="red"):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="JPEG")
    return buffer.getvalue()


async def _table_names(db):
    cursor = await db._conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name LIKE 'work_task%'"
    )
    return {row["table_name"] for row in await cursor.fetchall()}


async def _insert_definition(db, **overrides):
    values = {
        "kind": "once",
        "title": "把冷库门擦干净",
        "body": "",
        "photo_count": 1,
        "assignee_kind": "group",
        "deadline_hhmm": "18:00",
        "due_date": TODAY,
        "created_by_kind": "admin",
        "created_by_name": "管理端",
        "created_at": FIXED_NOW.isoformat(),
        "updated_at": FIXED_NOW.isoformat(),
    }
    values.update(overrides)
    cursor = await db._conn.execute(
        """
        INSERT INTO work_task_definitions (
            kind, title, body, photo_count, assignee_kind, deadline_hhmm, due_date,
            created_by_kind, created_by_name, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            values["kind"],
            values["title"],
            values["body"],
            values["photo_count"],
            values["assignee_kind"],
            values["deadline_hhmm"],
            values["due_date"],
            values["created_by_kind"],
            values["created_by_name"],
            values["created_at"],
            values["updated_at"],
        ),
    )
    row = await cursor.fetchone()
    await db._conn.commit()
    return int(row["id"])


async def _insert_instance(db, definition_id, period_date=TODAY):
    cursor = await db._conn.execute(
        """
        INSERT INTO work_task_instances (
            definition_id, period_date, title, body, photo_count, assignee_kind,
            deadline_at, status, created_at, updated_at
        ) VALUES (?, ?, '把冷库门擦干净', '', 1, 'group', ?, 'todo', ?, ?)
        RETURNING id
        """,
        (
            definition_id,
            period_date,
            f"{TODAY}T18:00:00+08:00",
            FIXED_NOW.isoformat(),
            FIXED_NOW.isoformat(),
        ),
    )
    row = await cursor.fetchone()
    await db._conn.commit()
    return int(row["id"])


@pytest.fixture
def tasks_http(tmp_path, monkeypatch):
    # 照片目录指到 tmp_path：`_captures()` 是模块级单例，先清掉上一次的。
    monkeypatch.setattr(tasks_module, "_capture_store", None)
    old_database_dir = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    monkeypatch.setattr(main_module, "employee_accounts", accounts)
    # 发布/编辑的「今天」也钉住，否则测试在深夜跑会撞「截止时刻已经过了」。
    monkeypatch.setattr(tasks_module, "TaskService", lambda db: TaskService(db, now=lambda: FIXED_NOW))

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(tasks_module.router)
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
    monkeypatch.setattr(tasks_module, "_capture_store", None)
    settings.DATABASE_DIR = old_database_dir


def _employee_id(accounts, phone=PHONE, name=NAME):
    employee = _run(accounts.register(phone, PASSWORD, name))
    _run(accounts.approve(employee["id"]))
    return employee["id"]


def _staff_cookie(client, accounts, phone=PHONE):
    session_id = _run(accounts.login(phone, PASSWORD))["session_id"]
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, session_id)
    return session_id


def _admin_cookie(client):
    client.cookies.clear()
    login = client.post(
        "/api/auth/login",
        json={"username": ADMIN["username"], "password": ADMIN["password"]},
    )
    assert login.status_code == 200, login.text


# ── 表与约束 ────────────────────────────────────────────────────────────────
def test_migration_creates_the_seven_task_tables(tasks_http):
    _client, db, _accounts = tasks_http
    assert _run(_table_names(db)) == TASK_TABLES


def test_instance_uniqueness_per_definition_and_period(tasks_http):
    """同一份定义的同一期只能有一行 —— 物化循环（票 06）靠它做幂等。"""
    _client, db, _accounts = tasks_http

    async def _scenario():
        definition_id = await _insert_definition(db)
        await _insert_instance(db, definition_id)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert_instance(db, definition_id)

    _run(_scenario())


def test_once_task_must_carry_due_date(tasks_http):
    """once 与 recurring 的字段配平写在库上：漏了 due_date 的 once 插不进去。"""
    _client, db, _accounts = tasks_http

    async def _scenario():
        with pytest.raises(asyncpg.CheckViolationError):
            await _insert_definition(db, due_date=None)

    _run(_scenario())


# ── 身份与能力门 ─────────────────────────────────────────────────────────────
def test_anonymous_is_rejected_everywhere(tasks_http):
    client, _db, _accounts = tasks_http
    client.cookies.clear()

    assert client.get("/api/tasks/board").status_code == 401
    assert client.get("/api/tasks/definitions").status_code == 401
    assert client.get("/api/tasks/mine").status_code == 401
    assert client.post("/api/tasks/definitions").status_code == 401
    assert client.post("/api/tasks/instances/1/accept").status_code == 401
    assert client.get("/api/tasks/images/fake-1").status_code == 401


def test_staff_without_task_cap_is_forbidden_not_hidden(tasks_http):
    """没勾「任务管理」的员工：发布 / 验收 403（不是 404，也不是空白页）。"""
    client, _db, accounts = tasks_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    assert client.get("/api/tasks/definitions").status_code == 403
    assert client.post("/api/tasks/definitions").status_code == 403
    assert client.get("/api/tasks/board").status_code == 403
    assert client.post("/api/tasks/instances/1/accept").status_code == 403
    assert client.post("/api/tasks/instances/1/reject").status_code == 403
    assert client.post("/api/tasks/instances/1/reassign").status_code == 403
    assert client.post("/api/tasks/instances/1/cancel").status_code == 403

    # 「我的任务」是员工自己的面，不看任务管理能力。
    mine = client.get("/api/tasks/mine")
    assert mine.status_code == 200
    assert mine.json()["groups"]["todo"] == []


def test_admin_passes_the_gate(tasks_http):
    """管理端会话：门放行（业务端点回 501 表示「门后还没实现」）。"""
    client, _db, _accounts = tasks_http
    _admin_cookie(client)

    definitions = client.get("/api/tasks/definitions")
    assert definitions.status_code == 200
    assert definitions.json() == {"items": []}

    board = client.get("/api/tasks/board")
    assert board.status_code == 200
    assert board.json()["counts"]["pending_review"] == 0

    # 票 02 起发布是真的：空表单走到业务校验（400），不再是 501。
    assert client.post("/api/tasks/definitions", json={}).status_code == 400


def test_staff_with_task_cap_passes_the_gate(tasks_http):
    """勾了「任务管理」的员工也能发布 / 验收（员工端的入口就在「我的任务」页内）。"""
    client, _db, accounts = tasks_http
    employee_id = _employee_id(accounts)
    _run(accounts.set_admin_caps(employee_id, ["task"]))
    _staff_cookie(client, accounts)

    assert client.get("/api/tasks/definitions").status_code == 200
    assert client.post("/api/tasks/definitions", json={}).status_code == 400
    # 票 04 起验收是真的：门过了才走到「任务不存在」。
    assert client.post("/api/tasks/instances/1/accept").status_code == 404
    assert client.post(
        "/api/tasks/instances/1/reject", json={"reason": "重新拍"}
    ).status_code == 404


# ── 照片上传 ────────────────────────────────────────────────────────────────
def test_submission_photo_must_be_live_and_sniffed(tasks_http):
    client, _db, accounts = tasks_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    jpeg = _jpeg_bytes()

    # 完成照必须现场拍（页面里的实时取景框才会带 live）。
    missing_live = client.post(
        "/api/tasks/images",
        files={"file": ("done.jpg", jpeg, "image/jpeg")},
    )
    assert missing_live.status_code == 400
    assert "现场拍" in missing_live.json()["detail"]

    # 类型按字节识别：不是图片就不收（哪怕 Content-Type 说是）。
    not_image = client.post(
        "/api/tasks/images",
        data={"purpose": "submission", "live": "true"},
        files={"file": ("done.jpg", b"hello, not a picture", "image/jpeg")},
    )
    assert not_image.status_code == 400
    assert "不是一张能用的图片" in not_image.json()["detail"]

    # HEIC 单独说一句能照做的话（iPhone 相机默认格式，网页存不了）。
    heif = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00"
    heic_response = client.post(
        "/api/tasks/images",
        data={"purpose": "submission", "live": "true"},
        files={"file": ("IMG_0001.HEIC", heif, "image/heic")},
    )
    assert heic_response.status_code == 400
    assert "HEIC" in heic_response.json()["detail"]

    # 现场拍的 JPEG：收下，返回 capture_id，随后能读回同一份字节。
    ok = client.post(
        "/api/tasks/images",
        data={"purpose": "submission", "live": "true"},
        files={"file": ("done.jpg", jpeg, "image/jpeg")},
    )
    assert ok.status_code == 200, ok.text
    image = ok.json()["image"]
    assert image["purpose"] == "submission"
    assert image["capture_id"]
    # URL 按 capture_id 寻址（不是台账行号）：读图接口只认 capture_id。
    assert image["url"] == f"/api/tasks/images/{image['capture_id']}"

    read = client.get(f"/api/tasks/images/{image['capture_id']}")
    assert read.status_code == 200
    assert read.content == jpeg
    assert read.headers["cache-control"] == "private, max-age=86400"

    assert client.get("/api/tasks/images/fake-nope").status_code == 404


def test_reference_photo_allows_the_album(tasks_http):
    """参考图是发布任务时选的那张，允许从相册来（不需要 live）。"""
    client, _db, accounts = tasks_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    ok = client.post(
        "/api/tasks/images",
        data={"purpose": "reference"},
        files={"file": ("ref.jpg", _jpeg_bytes("blue"), "image/jpeg")},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["image"]["purpose"] == "reference"

    bad_purpose = client.post(
        "/api/tasks/images",
        data={"purpose": "avatars"},
        files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert bad_purpose.status_code == 400


def test_photo_over_20mb_is_413(tasks_http):
    client, _db, accounts = tasks_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    oversized = b"\xff\xd8" + b"\x00" * (20 * 1024 * 1024)

    response = client.post(
        "/api/tasks/images",
        data={"purpose": "submission", "live": "true"},
        files={"file": ("big.jpg", oversized, "image/jpeg")},
    )
    assert response.status_code == 413
    assert "20 MB" in response.json()["detail"]


def test_empty_photo_is_rejected(tasks_http):
    client, _db, accounts = tasks_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    response = client.post(
        "/api/tasks/images",
        data={"purpose": "submission", "live": "true"},
        files={"file": ("empty.jpg", b"", "image/jpeg")},
    )
    assert response.status_code == 400


# ── 票 02：发布即时任务 ─────────────────────────────────────────────────────
def _payload(**overrides):
    payload = {
        "kind": "once",
        "title": "把冷库门擦干净",
        "body": "",
        "photo_count": 1,
        "assignee": {"kind": "group", "employee_ids": [1]},
        "due_date": TODAY,
        "deadline_hhmm": "18:00",
    }
    payload.update(overrides)
    return payload


def _publish(client, payload):
    response = client.post("/api/tasks/definitions", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _add_assignment(db, employee_id, *, business_date=TODAY, shift_id=1, zone_id=None):
    await db._conn.execute(
        "INSERT INTO staff_assignments"
        " (employee_id, business_date, shift_id, zone_id, source, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'rule', ?, ?)",
        (employee_id, business_date, shift_id, zone_id, FIXED_NOW.isoformat(), FIXED_NOW.isoformat()),
    )
    await db._conn.commit()


async def _add_zone(db, name):
    cursor = await db._conn.execute(
        "INSERT INTO hygiene_zones (name, created_at, updated_at) VALUES (?, ?, ?) RETURNING id",
        (name, FIXED_NOW.isoformat(), FIXED_NOW.isoformat()),
    )
    row = await cursor.fetchone()
    await db._conn.commit()
    return int(row["id"])


async def _set_deadline(db, instance_id, deadline_at):
    await db._conn.execute(
        "UPDATE work_task_instances SET deadline_at = ? WHERE id = ?",
        (deadline_at, instance_id),
    )
    await db._conn.commit()


async def _insert_submission(db, instance_id, employee_id=1):
    cursor = await db._conn.execute(
        "INSERT INTO work_task_submissions"
        " (instance_id, submitter_kind, submitter_employee_id, submitter_name, submitted_at)"
        " VALUES (?, 'staff', ?, '张三', ?) RETURNING id",
        (instance_id, employee_id, FIXED_NOW.isoformat()),
    )
    row = await cursor.fetchone()
    await db._conn.commit()
    return int(row["id"])


def test_publish_group_task_snapshots_and_appears_on_board(tasks_http):
    client, _db, accounts = tasks_http
    _admin_cookie(client)
    first = _employee_id(accounts, "13800138001", "张三")
    second = _employee_id(accounts, "13800138002", "李四")

    data = _publish(
        client,
        _payload(assignee={"kind": "group", "employee_ids": [first, second]}),
    )
    instance = data["instance"]
    assert data["definition_id"] > 0
    assert instance["status"] == "todo"
    assert instance["period_date"] == TODAY
    assert instance["deadline_at"].startswith(f"{TODAY}T18:00:00")
    assert instance["title"] == "把冷库门擦干净"
    assert [row["name"] for row in instance["assignees"]] == ["张三", "李四"]
    assert instance["can_edit"] is True
    assert instance["can_cancel"] is True

    board = client.get("/api/tasks/board").json()
    assert instance["id"] in [row["id"] for row in board["groups"]["active"]]
    assert board["counts"]["active"] == 1
    assert board["counts"]["pending_review"] == 0

    detail = client.get(f"/api/tasks/instances/{instance['id']}").json()
    assert detail["submissions"] == []
    assert [row["id"] for row in detail["assignees"]] == [first, second]


def test_publish_pool_task_resolves_members_from_schedule(tasks_http):
    client, db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138003", "王五")
    _run(_add_assignment(db, employee_id))

    data = _publish(client, _payload(assignee={"kind": "pool", "pool_kind": "on_duty"}))
    instance = data["instance"]
    assert instance["assignee_kind"] == "pool"
    assert instance["pool_label"] == "当天当班的人"
    assert [row["id"] for row in instance["assignees"]] == [employee_id]

    # 空池也要能发布（当天还没排班），只提示不阻止（票 02 验收 ①）。
    empty = _publish(
        client,
        _payload(
            title="空池也发得出去",
            assignee={"kind": "pool", "pool_kind": "on_duty"},
            due_date="2026-09-25",
        ),
    )
    assert empty["instance"]["assignees"] == []


def test_publish_zone_pool_uses_that_zone_assignment(tasks_http):
    client, db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138004", "赵六")
    zone_id = _run(_add_zone(db, "凉菜间"))
    _run(_add_assignment(db, employee_id, zone_id=zone_id))

    data = _publish(
        client,
        _payload(
            assignee={"kind": "pool", "pool_kind": "zone_on_duty", "zone_id": zone_id}
        ),
    )
    instance = data["instance"]
    assert instance["pool_zone_name"] == "凉菜间"
    assert [row["id"] for row in instance["assignees"]] == [employee_id]


def test_publish_validation_errors_are_named(tasks_http):
    client, _db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138005", "钱七")

    cases = [
        (_payload(title="   "), "标题不能为空"),
        (_payload(photo_count=4), "照片张数只能是 1～3 张"),
        (_payload(due_date="2026-09-23"), "截止日期不能早于今天"),
        (_payload(deadline_hhmm="09:30"), "截止时刻已经过了"),
        (_payload(assignee={"kind": "group", "employee_ids": []}), "至少选一位员工"),
        (
            _payload(assignee={"kind": "pool", "pool_kind": "zone_on_duty"}),
            "还要选一个工作区",
        ),
        (_payload(kind="recurring"), "开始日期格式不对"),
        (
            _payload(assignee={"kind": "group", "employee_ids": [employee_id, 999999]}),
            "不在在职花名册",
        ),
    ]
    for payload, expected in cases:
        response = client.post("/api/tasks/definitions", json=payload)
        assert response.status_code == 400, (expected, response.text)
        assert expected in response.json()["detail"], (expected, response.text)


def test_edit_before_submission_updates_snapshot_then_locks(tasks_http):
    client, db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138006", "孙八")
    data = _publish(
        client, _payload(assignee={"kind": "group", "employee_ids": [employee_id]})
    )
    instance_id = data["instance"]["id"]
    definition_id = data["definition_id"]

    patched = client.patch(
        f"/api/tasks/definitions/{definition_id}",
        json=_payload(
            title="改成擦操作台",
            assignee={"kind": "group", "employee_ids": [employee_id]},
            deadline_hhmm="19:30",
        ),
    )
    assert patched.status_code == 200, patched.text
    instance = patched.json()["instance"]
    assert instance["title"] == "改成擦操作台"
    assert instance["deadline_at"].startswith(f"{TODAY}T19:30:00")
    # 实例是快照：改定义只影响这一期（即时任务本来就只有一期）。
    assert client.get(f"/api/tasks/instances/{instance_id}").json()["title"] == "改成擦操作台"

    # 有人提交之后就不能再改（受理中的提交也算「有人提交」）。
    _run(_insert_submission(db, instance_id, employee_id))
    again = client.patch(
        f"/api/tasks/definitions/{definition_id}",
        json=_payload(assignee={"kind": "group", "employee_ids": [employee_id]}),
    )
    assert again.status_code == 409, again.text
    assert "已经有人提交过" in again.json()["detail"]


def test_cancel_before_submission_moves_to_finished(tasks_http):
    client, _db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138007", "周九")
    data = _publish(
        client, _payload(assignee={"kind": "group", "employee_ids": [employee_id]})
    )
    instance_id = data["instance"]["id"]

    cancelled = client.post(f"/api/tasks/instances/{instance_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["instance"]["status"] == "cancelled"

    board = client.get("/api/tasks/board").json()
    assert instance_id in [row["id"] for row in board["groups"]["finished"]]
    assert board["counts"]["active"] == 0

    again = client.post(f"/api/tasks/instances/{instance_id}/cancel")
    assert again.status_code == 409


def test_staff_with_task_cap_publishes_and_sees_it_in_mine(tasks_http):
    client, _db, accounts = tasks_http
    employee_id = _employee_id(accounts, "13800138008", "吴十")
    _run(accounts.set_admin_caps(employee_id, ["task"]))
    _staff_cookie(client, accounts, "13800138008")

    data = _publish(
        client,
        _payload(title="员工自己发的活", assignee={"kind": "group", "employee_ids": [employee_id]}),
    )
    assert data["instance"]["created_by"]["kind"] == "staff"
    assert data["instance"]["created_by"]["name"] == "吴十"

    mine = client.get("/api/tasks/mine")
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert data["instance"]["id"] in [row["id"] for row in body["groups"]["published"]]
    assert data["instance"]["id"] in [row["id"] for row in body["groups"]["todo"]]
    assert body["counts"]["published"] == 1


def test_reference_image_can_be_attached_and_read_back(tasks_http):
    client, _db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138009", "郑十一")

    upload = client.post(
        "/api/tasks/images",
        files={"file": ("ref.jpg", _jpeg_bytes("blue"), "image/jpeg")},
        data={"purpose": "reference"},
    )
    assert upload.status_code == 200, upload.text
    capture_id = upload.json()["image"]["capture_id"]

    data = _publish(
        client,
        _payload(
            assignee={"kind": "group", "employee_ids": [employee_id]},
            reference_capture_id=capture_id,
        ),
    )
    instance = data["instance"]
    assert instance["reference_image_url"] == f"/api/tasks/images/{capture_id}"
    read = client.get(instance["reference_image_url"])
    assert read.status_code == 200
    assert read.headers["content-type"].startswith("image/jpeg")

    # 别人的参考图不能拿来发任务（防止把别人相机里的图挂到自己任务上）。
    other = _employee_id(accounts, "13800138010", "冯十二")
    _run(accounts.set_admin_caps(other, ["task"]))
    _staff_cookie(client, accounts, "13800138010")
    stolen = client.post(
        "/api/tasks/definitions",
        json=_payload(
            assignee={"kind": "group", "employee_ids": [other]},
            reference_capture_id=capture_id,
        ),
    )
    assert stolen.status_code == 400
    assert "不是你上传的" in stolen.json()["detail"]


# ── 票 03：员工提交完成照 ───────────────────────────────────────────────────
def _upload_photo(client, *, purpose="submission", live=True, color="green"):
    form = {"purpose": purpose}
    if live:
        form["live"] = "true"
    response = client.post(
        "/api/tasks/images",
        data=form,
        files={"file": ("done.jpg", _jpeg_bytes(color), "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    return response.json()["image"]["capture_id"]


def _submit(client, instance_id, capture_id):
    return client.post(
        f"/api/tasks/instances/{instance_id}/submit",
        json={"capture_ids": [capture_id]},
    )


def test_staff_submits_group_task_and_second_member_sees_claimed(tasks_http):
    client, _db, accounts = tasks_http
    _admin_cookie(client)
    first = _employee_id(accounts, "13800138020", "张三")
    second = _employee_id(accounts, "13800138021", "李四")
    data = _publish(client, _payload(assignee={"kind": "group", "employee_ids": [first, second]}))
    instance_id = data["instance"]["id"]

    # 张三先到先得。
    _staff_cookie(client, accounts, "13800138020")
    photo = _upload_photo(client)
    submitted = client.post(f"/api/tasks/instances/{instance_id}/submit", json={"capture_ids": [photo]})
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()["instance"]
    assert body["status"] == "pending"
    assert body["submitted_count"] == 1

    # 管理端待验收里能看到。
    _admin_cookie(client)
    board = client.get("/api/tasks/board").json()
    assert instance_id in [row["id"] for row in board["groups"]["pending"]]
    assert board["counts"]["pending_review"] == 1

    # 李四的「待我做」还留着这条，但显示被张三交了、不能再交。
    _staff_cookie(client, accounts, "13800138021")
    mine = client.get("/api/tasks/mine").json()
    row = next(item for item in mine["groups"]["todo"] if item["id"] == instance_id)
    assert row["status"] == "pending"
    assert row["pending_submitter_name"] == "张三"
    assert row["claimed_by_me"] is False
    assert mine["counts"]["todo"] == 0

    own_photo = _upload_photo(client, color="blue")
    conflict = client.post(
        f"/api/tasks/instances/{instance_id}/submit", json={"capture_ids": [own_photo]}
    )
    assert conflict.status_code == 409
    assert "张三" in conflict.json()["detail"]

    detail = client.get(f"/api/tasks/instances/{instance_id}")
    assert detail.status_code == 200, detail.text
    # 被指派的员工能看详情，但不能编辑/撤回别人发布的任务。
    assert detail.json()["can_edit"] is False
    assert detail.json()["can_cancel"] is False
    assert detail.json()["can_submit"] is False
    submissions = detail.json()["submissions"]
    assert len(submissions) == 1
    assert submissions[0]["submitter_name"] == "张三"
    assert submissions[0]["status"] == "pending"
    assert submissions[0]["photos"][0]["url"].startswith("/api/tasks/images/")

    # 张三自己看这条已变成「待验收」，不算在他的待做计数里。
    _staff_cookie(client, accounts, "13800138020")
    mine = client.get("/api/tasks/mine").json()
    row = next(item for item in mine["groups"]["todo"] if item["id"] == instance_id)
    assert row["claimed_by_me"] is True
    assert mine["counts"]["todo"] == 0


def test_pool_task_first_submit_wins(tasks_http):
    client, db, accounts = tasks_http
    _admin_cookie(client)
    first = _employee_id(accounts, "13800138022", "张三")
    second = _employee_id(accounts, "13800138023", "李四")
    _run(_add_assignment(db, first))
    _run(_add_assignment(db, second))
    data = _publish(client, _payload(assignee={"kind": "pool", "pool_kind": "on_duty"}))
    instance_id = data["instance"]["id"]

    _staff_cookie(client, accounts, "13800138022")
    photo = _upload_photo(client)
    assert client.post(f"/api/tasks/instances/{instance_id}/submit", json={"capture_ids": [photo]}).status_code == 200

    _staff_cookie(client, accounts, "13800138023")
    mine = client.get("/api/tasks/mine").json()
    row = next(item for item in mine["groups"]["todo"] if item["id"] == instance_id)
    assert row["pending_submitter_name"] == "张三"
    other = _upload_photo(client, color="purple")
    assert client.post(f"/api/tasks/instances/{instance_id}/submit", json={"capture_ids": [other]}).status_code == 409


def test_submit_rejects_wrong_photo_sets(tasks_http):
    client, _db, accounts = tasks_http
    _admin_cookie(client)
    first = _employee_id(accounts, "13800138024", "张三")
    second = _employee_id(accounts, "13800138025", "李四")
    data = _publish(
        client,
        _payload(photo_count=2, assignee={"kind": "group", "employee_ids": [first]}),
    )
    instance_id = data["instance"]["id"]
    _staff_cookie(client, accounts, "13800138024")

    one = _upload_photo(client, color="green")
    wrong_count = client.post(f"/api/tasks/instances/{instance_id}/submit", json={"capture_ids": [one]})
    assert wrong_count.status_code == 400
    assert "要交 2 张" in wrong_count.json()["detail"]

    same_twice = client.post(f"/api/tasks/instances/{instance_id}/submit", json={"capture_ids": [one, one]})
    assert same_twice.status_code == 400
    assert "不能交两次" in same_twice.json()["detail"]

    reference = _upload_photo(client, purpose="reference", live=False, color="blue")
    two = _upload_photo(client, color="yellow")
    reference_as_done = client.post(
        f"/api/tasks/instances/{instance_id}/submit", json={"capture_ids": [reference, two]}
    )
    assert reference_as_done.status_code == 400
    assert "参考图不能当完成照" in reference_as_done.json()["detail"]

    # 第二个人拍的照片不能拿来交（哪怕他愿意）。
    _staff_cookie(client, accounts, "13800138025")
    other_photo = _upload_photo(client, color="purple")
    _staff_cookie(client, accounts, "13800138024")
    stolen = client.post(
        f"/api/tasks/instances/{instance_id}/submit", json={"capture_ids": [one, other_photo]}
    )
    assert stolen.status_code == 400
    assert "自己拍的" in stolen.json()["detail"]

    # 同一张完成照不能交给第二个任务。
    assert client.post(
        f"/api/tasks/instances/{instance_id}/submit", json={"capture_ids": [one, _upload_photo(client, color="orange")]}
    ).status_code == 200
    _admin_cookie(client)
    other_task = _publish(
        client,
        _payload(title="另一件活", photo_count=1, assignee={"kind": "group", "employee_ids": [first]}),
    )
    _staff_cookie(client, accounts, "13800138024")
    reused = client.post(
        f"/api/tasks/instances/{other_task['instance']['id']}/submit",
        json={"capture_ids": [one]},
    )
    assert reused.status_code == 400
    assert "已经交给别的任务" in reused.json()["detail"]


def test_unassigned_staff_cannot_see_or_submit(tasks_http):
    client, _db, accounts = tasks_http
    _admin_cookie(client)
    first = _employee_id(accounts, "13800138026", "张三")
    second = _employee_id(accounts, "13800138027", "李四")
    data = _publish(client, _payload(assignee={"kind": "group", "employee_ids": [first]}))
    instance_id = data["instance"]["id"]

    _staff_cookie(client, accounts, "13800138027")
    mine = client.get("/api/tasks/mine").json()
    assert instance_id not in [row["id"] for row in mine["groups"]["todo"]]
    assert client.get(f"/api/tasks/instances/{instance_id}").status_code == 404
    photo = _upload_photo(client)
    assert client.post(f"/api/tasks/instances/{instance_id}/submit", json={"capture_ids": [photo]}).status_code == 404


def test_submit_requires_staff_session(tasks_http):
    client, _db, accounts = tasks_http
    _admin_cookie(client)
    first = _employee_id(accounts, "13800138028", "张三")
    data = _publish(client, _payload(assignee={"kind": "group", "employee_ids": [first]}))
    instance_id = data["instance"]["id"]
    # 管理端共享账号不能替员工交照片（它不是花名册里的真人）。
    response = client.post(f"/api/tasks/instances/{instance_id}/submit", json={"capture_ids": []})
    assert response.status_code == 401


# ── 票 04：验收与驳回重交 ───────────────────────────────────────────────────
def test_reject_then_resubmit_then_accept(tasks_http):
    client, _db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138029", "赵六")
    instance_id = _publish(
        client, _payload(assignee={"kind": "group", "employee_ids": [employee_id]})
    )["instance"]["id"]

    _staff_cookie(client, accounts, "13800138029")
    assert _submit(client, instance_id, _upload_photo(client)).status_code == 200
    assert client.get(f"/api/tasks/instances/{instance_id}").json()["can_review"] is False

    _admin_cookie(client)
    assert client.post(
        f"/api/tasks/instances/{instance_id}/reject", json={"reason": "   "}
    ).status_code == 400
    too_long = client.post(
        f"/api/tasks/instances/{instance_id}/reject", json={"reason": "字" * 201}
    )
    assert too_long.status_code == 400
    assert "200" in too_long.json()["detail"]

    reason = "台面还有水渍，重新擦一遍再拍"
    rejected = client.post(
        f"/api/tasks/instances/{instance_id}/reject", json={"reason": reason}
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()["instance"]
    assert body["status"] == "todo"
    assert body["last_reject_reason"] == reason
    assert body["pending_submitter_name"] is None

    # 员工在「待我做」里看到驳回理由，可以重拍重交。
    _staff_cookie(client, accounts, "13800138029")
    mine = client.get("/api/tasks/mine").json()
    row = next(item for item in mine["groups"]["todo"] if item["id"] == instance_id)
    assert row["last_reject_reason"] == reason
    again = _submit(client, instance_id, _upload_photo(client))
    assert again.status_code == 200, again.text
    assert again.json()["instance"]["submitted_count"] == 2

    _admin_cookie(client)
    detail = client.get(f"/api/tasks/instances/{instance_id}").json()
    assert detail["can_review"] is True
    accepted = client.post(f"/api/tasks/instances/{instance_id}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["instance"]["status"] == "passed"

    detail = client.get(f"/api/tasks/instances/{instance_id}").json()
    assert [item["status"] for item in detail["submissions"]] == ["rejected", "passed"]
    assert detail["submissions"][0]["reject_reason"] == reason
    assert detail["submissions"][1]["reviewed_by_name"]
    assert detail["can_review"] is False

    board = client.get("/api/tasks/board").json()
    assert instance_id in [item["id"] for item in board["groups"]["finished"]]

    # 已经验收过、已经通过的都不能再交，也不能重复验收。
    assert client.post(f"/api/tasks/instances/{instance_id}/accept").status_code == 409
    _staff_cookie(client, accounts, "13800138029")
    late = _submit(client, instance_id, _upload_photo(client, color="red"))
    assert late.status_code == 409
    assert "已经验收通过" in late.json()["detail"]


def test_manager_cannot_review_own_submission(tasks_http):
    client, _db, accounts = tasks_http
    manager = _employee_id(accounts, "13800138030", "钱七")
    _run(accounts.set_admin_caps(manager, ["task"]))
    _staff_cookie(client, accounts, "13800138030")
    instance_id = _publish(
        client, _payload(assignee={"kind": "group", "employee_ids": [manager]})
    )["instance"]["id"]
    assert _submit(client, instance_id, _upload_photo(client)).status_code == 200

    detail = client.get(f"/api/tasks/instances/{instance_id}").json()
    assert detail["can_review"] is False
    own_accept = client.post(f"/api/tasks/instances/{instance_id}/accept")
    assert own_accept.status_code == 403
    assert "不能验收自己" in own_accept.json()["detail"]
    own_reject = client.post(
        f"/api/tasks/instances/{instance_id}/reject", json={"reason": "自己驳回自己"}
    )
    assert own_reject.status_code == 403

    # 管理端共享账号不是提交人，可以验收。
    _admin_cookie(client)
    assert client.post(f"/api/tasks/instances/{instance_id}/accept").status_code == 200


# ── 票 05：逾期与补交 ───────────────────────────────────────────────────────
def test_overdue_counts_and_late_submission(tasks_http):
    client, db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138031", "孙八")
    instance_id = _publish(
        client, _payload(assignee={"kind": "group", "employee_ids": [employee_id]})
    )["instance"]["id"]
    # FIXED_NOW = 2026-09-24 10:00；把截止挪到前一天 18:00。
    _run(_set_deadline(db, instance_id, "2026-09-23T18:00:00+08:00"))

    board = client.get("/api/tasks/board").json()
    row = next(item for item in board["groups"]["active"] if item["id"] == instance_id)
    assert row["overdue"] is True
    assert board["counts"]["overdue"] == 1

    _staff_cookie(client, accounts, "13800138031")
    detail = client.get(f"/api/tasks/instances/{instance_id}").json()
    assert detail["overdue"] is True
    assert detail["can_submit"] is True

    # 逾期仍可补交，提交标 late；提交后「待验收」不再算逾期。
    submitted = _submit(client, instance_id, _upload_photo(client))
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["instance"]["overdue"] is False
    detail = client.get(f"/api/tasks/instances/{instance_id}").json()
    assert detail["submissions"][0]["late"] == 1

    _admin_cookie(client)
    board = client.get("/api/tasks/board").json()
    assert board["counts"]["overdue"] == 0
    row = next(item for item in board["groups"]["pending"] if item["id"] == instance_id)
    assert row["overdue"] is False


def test_review_queue_excludes_own_submission(tasks_http):
    client, _db, accounts = tasks_http
    manager = _employee_id(accounts, "13800138032", "周九")
    other = _employee_id(accounts, "13800138033", "吴十")
    _run(accounts.set_admin_caps(manager, ["task"]))

    _admin_cookie(client)
    own = _publish(
        client, _payload(assignee={"kind": "group", "employee_ids": [manager]})
    )["instance"]["id"]
    others = _publish(
        client, _payload(title="别人的活", assignee={"kind": "group", "employee_ids": [other]})
    )["instance"]["id"]

    _staff_cookie(client, accounts, "13800138032")
    assert _submit(client, own, _upload_photo(client)).status_code == 200
    _staff_cookie(client, accounts, "13800138033")
    assert _submit(client, others, _upload_photo(client)).status_code == 200

    _staff_cookie(client, accounts, "13800138032")
    review = client.get("/api/tasks/mine").json()["groups"]["review"]
    review_ids = [item["id"] for item in review]
    assert others in review_ids
    assert own not in review_ids


# ── 票 06：定期任务 ─────────────────────────────────────────────────────────
def test_frequency_matching_weekday_and_month_day():
    daily = {
        "frequency_kind": "daily",
        "start_date": "2026-09-20",
        "end_date": None,
        "weekday": None,
        "month_day": None,
    }
    assert TaskService._frequency_matches(daily, date(2026, 9, 20))
    assert TaskService._frequency_matches(daily, date(2026, 9, 25))
    assert not TaskService._frequency_matches(daily, date(2026, 9, 19))

    bounded = {**daily, "end_date": "2026-09-22"}
    assert TaskService._frequency_matches(bounded, date(2026, 9, 22))
    assert not TaskService._frequency_matches(bounded, date(2026, 9, 23))

    # 2026-09-21 是周一（weekday 0）。
    weekly = {
        "frequency_kind": "weekly",
        "start_date": "2026-09-01",
        "end_date": None,
        "weekday": 0,
        "month_day": None,
    }
    assert TaskService._frequency_matches(weekly, date(2026, 9, 21))
    assert not TaskService._frequency_matches(weekly, date(2026, 9, 22))

    # 每月 31 号：有 31 号的月份命中，2 月跳过（不是报错）。
    monthly = {
        "frequency_kind": "monthly",
        "start_date": "2027-01-01",
        "end_date": None,
        "weekday": None,
        "month_day": 31,
    }
    assert TaskService._frequency_matches(monthly, date(2027, 1, 31))
    assert not TaskService._frequency_matches(monthly, date(2027, 2, 28))
    assert TaskService._frequency_matches(monthly, date(2027, 3, 31))


def _recurring_payload(**overrides):
    payload = {
        "kind": "recurring",
        "title": "每天擦冷库门",
        "body": "",
        "photo_count": 1,
        "assignee": {"kind": "group", "employee_ids": [1]},
        "deadline_hhmm": "18:00",
        "frequency_kind": "daily",
        "start_date": "2026-09-20",
    }
    payload.update(overrides)
    return payload


def test_publish_recurring_materializes_recent_periods(tasks_http):
    client, db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138041", "钱一")

    data = _publish(
        client,
        _recurring_payload(assignee={"kind": "group", "employee_ids": [employee_id]}),
    )
    definition_id = data["definition_id"]
    definition = data["definition"]
    assert definition["kind"] == "recurring"
    assert definition["frequency_kind"] == "daily"
    # FIXED_NOW = 2026-09-24；从 09-20 起每天、回补最近 7 天 → 20..24 共 5 期。
    assert [row["period_date"] for row in definition["recent"]] == [
        "2026-09-20",
        "2026-09-21",
        "2026-09-22",
        "2026-09-23",
        "2026-09-24",
    ]
    assert definition["recent_total"] == 5
    assert definition["recent_passed"] == 0

    board = client.get("/api/tasks/board").json()
    active = sorted(
        item["period_date"]
        for item in board["groups"]["active"]
        if item["definition_id"] == definition_id
    )
    assert active == ["2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]

    # 幂等：常驻循环重复物化不会多出任何期。
    service = TaskService(db, now=lambda: FIXED_NOW)
    assert _run(service.materialize_recurring()) == 0

    listing = client.get("/api/tasks/definitions").json()["items"]
    assert [row["id"] for row in listing] == [definition_id]


def test_recurring_validation_errors(tasks_http):
    client, _db, _accounts = tasks_http
    _admin_cookie(client)

    cases = [
        (_recurring_payload(frequency_kind=""), "请选择频率"),
        (_recurring_payload(frequency_kind="weekly"), "星期必须是一个整数"),
        (_recurring_payload(frequency_kind="weekly", weekday=9), "请选择星期几"),
        (_recurring_payload(frequency_kind="monthly", month_day=32), "每月日期只能是 1～31 号"),
        (
            _recurring_payload(start_date="2026-09-24", end_date="2026-09-20"),
            "结束日期不能早于开始日期",
        ),
    ]
    for payload, expected in cases:
        response = client.post("/api/tasks/definitions", json=payload)
        assert response.status_code == 400, (expected, response.text)
        assert expected in response.json()["detail"], (expected, response.text)


def test_edit_recurring_only_affects_future_periods(tasks_http):
    client, db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138042", "孙一")

    data = _publish(
        client,
        _recurring_payload(
            start_date="2026-09-22",
            assignee={"kind": "group", "employee_ids": [employee_id]},
        ),
    )
    definition_id = data["definition_id"]

    patched = client.patch(
        f"/api/tasks/definitions/{definition_id}",
        json=_recurring_payload(
            title="改成擦操作台",
            deadline_hhmm="19:00",
            start_date="2026-09-22",
            assignee={"kind": "group", "employee_ids": [employee_id]},
        ),
    )
    assert patched.status_code == 200, patched.text
    definition = patched.json()["definition"]
    assert definition["title"] == "改成擦操作台"
    assert definition["deadline_hhmm"] == "19:00"

    # 已经生成的期保留快照 —— 编辑只影响未来。
    board = client.get("/api/tasks/board").json()
    existing = [
        item for item in board["groups"]["active"] if item["definition_id"] == definition_id
    ]
    assert {item["title"] for item in existing} == {"每天擦冷库门"}
    assert {item["period_date"] for item in existing} == {
        "2026-09-22",
        "2026-09-23",
        "2026-09-24",
    }

    # 下一天物化出来的期用新定义。
    service = TaskService(db, now=lambda: FIXED_NOW)
    assert _run(service.materialize_recurring(today="2026-09-25")) == 1
    board = client.get("/api/tasks/board").json()
    newer = [
        item
        for item in board["groups"]["active"]
        if item["definition_id"] == definition_id and item["period_date"] == "2026-09-25"
    ]
    assert [item["title"] for item in newer] == ["改成擦操作台"]


def test_stop_recurring_definition_keeps_history(tasks_http):
    client, db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138043", "李一")

    data = _publish(
        client,
        _recurring_payload(
            start_date="2026-09-23",
            assignee={"kind": "group", "employee_ids": [employee_id]},
        ),
    )
    definition_id = data["definition_id"]

    stopped = client.post(f"/api/tasks/definitions/{definition_id}/stop")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["definition"]["status"] == "stopped"

    # 幂等：再停一次不报错。
    assert client.post(f"/api/tasks/definitions/{definition_id}/stop").status_code == 200
    listing = client.get("/api/tasks/definitions").json()["items"]
    row = next(item for item in listing if item["id"] == definition_id)
    assert row["status"] == "stopped"

    # 已生成的期还在；停用后不再物化新期。
    service = TaskService(db, now=lambda: FIXED_NOW)
    assert _run(service.materialize_recurring(today="2026-09-26")) == 0
    board = client.get("/api/tasks/board").json()
    dates = {
        item["period_date"]
        for item in board["groups"]["active"]
        if item["definition_id"] == definition_id
    }
    assert dates == {"2026-09-23", "2026-09-24"}


if __name__ == "__main__":
    import pytest as _pytest

    raise SystemExit(_pytest.main([__file__, "-q"]))


# ── 票 07：状态变化发 tasks nudge（不带数据、原因可区分）────────────────────
class _NudgeRecorder:
    def __init__(self):
        self.calls = []

    async def broadcast_nudge(self, topic, scope):
        self.calls.append((topic, dict(scope)))


def test_state_changes_emit_task_nudges(tasks_http, monkeypatch):
    client, db, accounts = tasks_http
    recorder = _NudgeRecorder()
    monkeypatch.setattr(tasks_module, "realtime_hub", recorder)
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138044", "周五")
    group = {"kind": "group", "employee_ids": [employee_id]}

    published = _publish(client, _payload(assignee=group))
    definition_id = published["definition_id"]
    instance_id = published["instance"]["id"]
    assert recorder.calls[-1] == ("tasks", {"reason": "published"})

    edited = client.patch(
        f"/api/tasks/definitions/{definition_id}",
        json=_payload(title="改个名", assignee=group),
    )
    assert edited.status_code == 200, edited.text
    assert recorder.calls[-1] == ("tasks", {"reason": "updated"})

    _staff_cookie(client, accounts, "13800138044")
    capture_id = _upload_photo(client)
    submitted = _submit(client, instance_id, capture_id)
    assert submitted.status_code == 200, submitted.text
    assert recorder.calls[-1][1]["reason"] == "submitted"

    _admin_cookie(client)
    rejected = client.post(
        f"/api/tasks/instances/{instance_id}/reject",
        json={"reason": "再拍一张整张台面"},
    )
    assert rejected.status_code == 200, rejected.text
    assert recorder.calls[-1][1]["reason"] == "rejected"

    _staff_cookie(client, accounts, "13800138044")
    second_capture = _upload_photo(client, color="blue")
    assert _submit(client, instance_id, second_capture).status_code == 200
    _admin_cookie(client)
    accepted = client.post(f"/api/tasks/instances/{instance_id}/accept")
    assert accepted.status_code == 200, accepted.text
    assert recorder.calls[-1][1]["reason"] == "accepted"

    second = _publish(client, _payload(title="把货架擦一遍", assignee=group))
    cancelled = client.post(f"/api/tasks/instances/{second['instance']['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert recorder.calls[-1][1]["reason"] == "cancelled"

    recurring = _publish(client, _recurring_payload(assignee=group))
    stopped = client.post(f"/api/tasks/definitions/{recurring['definition_id']}/stop")
    assert stopped.status_code == 200, stopped.text
    assert recorder.calls[-1][1]["reason"] == "stopped"

    assert all(topic == "tasks" for topic, _scope in recorder.calls)
    assert [scope.get("reason") for _topic, scope in recorder.calls] == [
        "published",
        "updated",
        "submitted",
        "rejected",
        "submitted",
        "accepted",
        "published",
        "cancelled",
        "published",
        "stopped",
    ]


# ── 票 08：改派与离职标记 ────────────────────────────────────────────────────
def _reassign(client, instance_id, assignee):
    return client.post(f"/api/tasks/instances/{instance_id}/reassign", json=assignee)


async def _mark_resigned(db, employee_id, resigned_at):
    await db._conn.execute(
        "UPDATE hygiene_employees SET resigned_at = ? WHERE id = ?",
        (resigned_at, employee_id),
    )
    await db._conn.commit()


async def _instance_count(db, definition_id):
    cursor = await db._conn.execute(
        "SELECT count(*) AS n FROM work_task_instances WHERE definition_id = ?",
        (definition_id,),
    )
    row = await cursor.fetchone()
    return int(row["n"])


def test_reassign_group_moves_visibility_to_new_member(tasks_http):
    client, db, accounts = tasks_http
    _admin_cookie(client)
    first = _employee_id(accounts, "13800138050", "赵一")
    second = _employee_id(accounts, "13800138051", "钱二")
    published = _publish(client, _payload(assignee={"kind": "group", "employee_ids": [first]}))
    instance_id = published["instance"]["id"]
    definition_id = published["definition_id"]

    reassigned = _reassign(client, instance_id, {"kind": "group", "employee_ids": [second]})
    assert reassigned.status_code == 200, reassigned.text
    body = reassigned.json()["instance"]
    assert body["id"] == instance_id
    assert body["assignee_names"] == ["钱二"]
    assert body["needs_reassign"] is False
    # 改派不新建实例、不清提交历史（此时本来也没有）。
    assert _run(_instance_count(db, definition_id)) == 1
    assert body["submitted_count"] == 0

    # 旧负责人从「待我做」里消失。
    _staff_cookie(client, accounts, "13800138050")
    mine = client.get("/api/tasks/mine").json()
    assert instance_id not in [row["id"] for row in mine["groups"]["todo"]]

    # 新负责人看得到，并且能直接提交。
    _staff_cookie(client, accounts, "13800138051")
    mine = client.get("/api/tasks/mine").json()
    assert instance_id in [row["id"] for row in mine["groups"]["todo"]]
    photo = _upload_photo(client)
    assert _submit(client, instance_id, photo).status_code == 200


def test_reassign_pool_task_resolves_new_pool(tasks_http):
    client, _db, accounts = tasks_http
    _admin_cookie(client)
    first = _employee_id(accounts, "13800138052", "孙三")
    published = _publish(client, _payload(assignee={"kind": "group", "employee_ids": [first]}))
    instance_id = published["instance"]["id"]

    reassigned = _reassign(
        client, instance_id, {"kind": "pool", "pool_kind": "all_staff"}
    )
    assert reassigned.status_code == 200, reassigned.text
    body = reassigned.json()["instance"]
    assert body["assignee_kind"] == "pool"
    assert body["pool_kind"] == "all_staff"
    assert body["pool_label"] == "全体在职员工"
    assert first in [member["id"] for member in body["assignees"]]

    # 池任务按新池解析，员工端照常能看到。
    _staff_cookie(client, accounts, "13800138052")
    mine = client.get("/api/tasks/mine").json()
    assert instance_id in [row["id"] for row in mine["groups"]["todo"]]


def test_reassign_rejected_task_keeps_submission_history(tasks_http):
    client, db, accounts = tasks_http
    _admin_cookie(client)
    first = _employee_id(accounts, "13800138053", "周四")
    second = _employee_id(accounts, "13800138054", "吴五")
    published = _publish(client, _payload(assignee={"kind": "group", "employee_ids": [first]}))
    instance_id = published["instance"]["id"]
    definition_id = published["definition_id"]

    _staff_cookie(client, accounts, "13800138053")
    photo = _upload_photo(client)
    assert _submit(client, instance_id, photo).status_code == 200

    _admin_cookie(client)
    rejected = client.post(
        f"/api/tasks/instances/{instance_id}/reject", json={"reason": "重拍一张整张台面"}
    )
    assert rejected.status_code == 200, rejected.text

    # 驳回后回到待交：这时允许改派，且驳回历史保留。
    reassigned = _reassign(client, instance_id, {"kind": "group", "employee_ids": [second]})
    assert reassigned.status_code == 200, reassigned.text
    body = reassigned.json()["instance"]
    assert body["status"] == "todo"
    assert body["submitted_count"] == 1
    assert _run(_instance_count(db, definition_id)) == 1

    detail = client.get(f"/api/tasks/instances/{instance_id}").json()
    assert [item["status"] for item in detail["submissions"]] == ["rejected"]
    assert detail["submissions"][0]["reject_reason"] == "重拍一张整张台面"

    # 新负责人能交，提交累计两轮。
    _staff_cookie(client, accounts, "13800138054")
    second_photo = _upload_photo(client, color="blue")
    assert _submit(client, instance_id, second_photo).status_code == 200
    detail = client.get(f"/api/tasks/instances/{instance_id}").json()
    assert detail["submitted_count"] == 2


def test_reassign_blocked_when_pending_or_passed(tasks_http):
    client, _db, accounts = tasks_http
    _admin_cookie(client)
    first = _employee_id(accounts, "13800138055", "郑六")
    second = _employee_id(accounts, "13800138056", "王七")
    published = _publish(client, _payload(assignee={"kind": "group", "employee_ids": [first]}))
    instance_id = published["instance"]["id"]

    _staff_cookie(client, accounts, "13800138055")
    photo = _upload_photo(client)
    assert _submit(client, instance_id, photo).status_code == 200

    _admin_cookie(client)
    pending = _reassign(client, instance_id, {"kind": "group", "employee_ids": [second]})
    assert pending.status_code == 409
    assert "不能改派" in pending.json()["detail"]

    assert client.post(f"/api/tasks/instances/{instance_id}/accept").status_code == 200
    passed = _reassign(client, instance_id, {"kind": "group", "employee_ids": [second]})
    assert passed.status_code == 409

    other = _publish(client, _payload(title="收台面", assignee={"kind": "group", "employee_ids": [first]}))
    assert client.post(f"/api/tasks/instances/{other['instance']['id']}/cancel").status_code == 200
    cancelled = _reassign(client, other["instance"]["id"], {"kind": "group", "employee_ids": [second]})
    assert cancelled.status_code == 409


def test_reassign_by_staff_manager_is_allowed(tasks_http):
    client, _db, accounts = tasks_http
    _admin_cookie(client)
    worker = _employee_id(accounts, "13800138057", "冯七")
    manager = _employee_id(accounts, "13800138058", "陈八")
    _run(accounts.set_admin_caps(manager, ["task"]))
    published = _publish(client, _payload(assignee={"kind": "group", "employee_ids": [worker]}))
    instance_id = published["instance"]["id"]

    _staff_cookie(client, accounts, "13800138058")
    reassigned = _reassign(client, instance_id, {"kind": "pool", "pool_kind": "managers"})
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json()["instance"]["pool_kind"] == "managers"


def test_resigned_member_marks_needs_reassign(tasks_http):
    client, db, accounts = tasks_http
    _admin_cookie(client)
    first = _employee_id(accounts, "13800138059", "褚九")
    second = _employee_id(accounts, "13800138060", "卫十")
    published = _publish(
        client, _payload(assignee={"kind": "group", "employee_ids": [first, second]})
    )
    instance_id = published["instance"]["id"]

    _run(_mark_resigned(db, first, "2026-09-23"))
    board = client.get("/api/tasks/board").json()
    row = next(item for item in board["groups"]["active"] if item["id"] == instance_id)
    assert row["needs_reassign"] is True
    assert board["counts"]["needs_reassign"] == 1
    # 计数接口（首页/角标）也要真数，而不是写死 0。
    counts_only = client.get("/api/tasks/board", params={"counts_only": 1}).json()
    assert counts_only["counts"]["needs_reassign"] == 1

    detail = client.get(f"/api/tasks/instances/{instance_id}").json()
    assert detail["needs_reassign"] is True
    resigned = [member for member in detail["assignees"] if member["id"] == first][0]
    assert resigned["resigned"] is True

    # 员工端同样能看到「负责人已离职」标记（另一组成员卫十视角）。
    _staff_cookie(client, accounts, "13800138060")
    mine = client.get("/api/tasks/mine").json()
    row = next(item for item in mine["groups"]["todo"] if item["id"] == instance_id)
    assert row["needs_reassign"] is True

    # 改派给还在职的人之后，标记与计数都清零。
    _admin_cookie(client)
    _reassign(client, instance_id, {"kind": "group", "employee_ids": [second]})
    board = client.get("/api/tasks/board").json()
    row = next(item for item in board["groups"]["active"] if item["id"] == instance_id)
    assert row["needs_reassign"] is False
    assert board["counts"]["needs_reassign"] == 0


def test_reassign_emits_task_nudge(tasks_http, monkeypatch):
    client, _db, accounts = tasks_http
    recorder = _NudgeRecorder()
    monkeypatch.setattr(tasks_module, "realtime_hub", recorder)
    _admin_cookie(client)
    first = _employee_id(accounts, "13800138061", "蒋十一")
    second = _employee_id(accounts, "13800138062", "沈十二")
    published = _publish(client, _payload(assignee={"kind": "group", "employee_ids": [first]}))
    instance_id = published["instance"]["id"]
    assert recorder.calls[-1] == ("tasks", {"reason": "published"})

    reassigned = _reassign(client, instance_id, {"kind": "group", "employee_ids": [second]})
    assert reassigned.status_code == 200
    assert recorder.calls[-1] == ("tasks", {"reason": "reassigned"})


def test_staff_creator_can_edit_and_cancel_own_published_task(tasks_http):
    """员工端「我发布的」：没人交之前能改内容、也能撤回；撤回后进已结束。"""
    client, _db, accounts = tasks_http
    manager = _employee_id(accounts, "13800138063", "韩十三")
    worker = _employee_id(accounts, "13800138064", "杨十四")
    _run(accounts.set_admin_caps(manager, ["task"]))
    _staff_cookie(client, accounts, "13800138063")
    published = client.post(
        "/api/tasks/definitions",
        json=_payload(title="员工自己发的任务", assignee={"kind": "group", "employee_ids": [worker]}),
    )
    assert published.status_code == 200, published.text
    instance_id = published.json()["instance"]["id"]
    definition_id = published.json()["definition_id"]

    detail = client.get(f"/api/tasks/instances/{instance_id}").json()
    assert detail["can_edit"] is True
    assert detail["can_cancel"] is True

    edited = client.patch(
        f"/api/tasks/definitions/{definition_id}",
        json=_payload(
            title="员工自己发错的任务",
            assignee={"kind": "group", "employee_ids": [worker]},
        ),
    )
    assert edited.status_code == 200, edited.text

    cancelled = client.post(f"/api/tasks/instances/{instance_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["instance"]["status"] == "cancelled"

    mine = client.get("/api/tasks/mine").json()
    row = next(item for item in mine["groups"]["published"] if item["id"] == instance_id)
    assert row["status"] == "cancelled"
    assert row["can_edit"] is False
    assert row["can_cancel"] is False


# ── 票 09：企微群通知 ────────────────────────────────────────────────────────


def _subscribe_work_task_topic(db, name="门店群"):
    async def _scenario():
        now = FIXED_NOW.isoformat()
        cursor = await db._conn.execute(
            "INSERT INTO wecom_push_webhooks"
            " (name, webhook_url_encrypted, webhook_url_masked, enabled, notes,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, 1, '', ?, ?) RETURNING id",
            (name, "encrypted-placeholder", "masked", now, now),
        )
        channel_id = int((await cursor.fetchone())["id"])
        await db._conn.execute(
            "INSERT INTO wecom_push_subscriptions"
            " (topic_id, target_channel_id, enabled, created_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?)",
            ("work_task", channel_id, now, now),
        )
        await db._conn.commit()
        return channel_id

    return _run(_scenario())


def _outbox_rows(db):
    async def _scenario():
        cursor = await db._conn.execute(
            "SELECT topic_id, idempotency_key, params_json, content_summary"
            " FROM wecom_push_outbox ORDER BY id"
        )
        return [dict(row) for row in await cursor.fetchall()]

    return _run(_scenario())


def test_wecom_push_off_by_default_and_warns_without_subscription(tasks_http):
    """不勾不推；勾了但没订阅：任务照常创建，响应带去订阅页配置的提示。"""
    client, db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138071", "孙一")
    _run(accounts.set_admin_caps(employee_id, ["task"]))

    quiet = _publish(
        client,
        _payload(assignee={"kind": "group", "employee_ids": [employee_id]}),
    )
    assert "warning" not in quiet
    assert _outbox_rows(db) == []

    checked = _publish(
        client,
        _payload(
            title="擦一下出餐口",
            assignee={"kind": "group", "employee_ids": [employee_id]},
            wecom_group_push=True,
        ),
    )
    assert "warning" in checked and "企微推送" in checked["warning"]
    # 任务本身不受影响。
    assert checked["instance"]["title"] == "擦一下出餐口"
    assert _outbox_rows(db) == []


def test_wecom_push_once_enqueues_one_idempotent_message(tasks_http):
    """勾选 + 有订阅：一条群消息；同一期重复入队被幂等键挡住。"""
    from services.tasks.notify import (
        build_business_reference,
        enqueue_work_task_push,
    )

    client, db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138072", "周二")
    _run(accounts.set_admin_caps(employee_id, ["task"]))
    _subscribe_work_task_topic(db)

    data = _publish(
        client,
        _payload(
            title="把冷库门擦干净",
            assignee={"kind": "group", "employee_ids": [employee_id]},
            wecom_group_push=True,
        ),
    )
    assert "warning" not in data
    rows = _outbox_rows(db)
    assert len(rows) == 1
    assert rows[0]["topic_id"] == "work_task"
    reference = build_business_reference(data["definition_id"], TODAY)
    assert f"work_task:{reference}" in rows[0]["idempotency_key"]
    assert "把冷库门擦干净" in rows[0]["params_json"]
    assert "18:00" in rows[0]["params_json"]

    async def _push_again():
        return await enqueue_work_task_push(
            db,
            definition_id=data["definition_id"],
            period_date=TODAY,
            title="把冷库门擦干净",
            deadline_hhmm="18:00",
            assignee_text="周二",
        )

    # 幂等复用同一行（返回既有 id），不会多落一条。
    assert len(_run(_push_again())) == 1
    assert len(_outbox_rows(db)) == 1


def test_wecom_push_recurring_only_pushes_today(tasks_http):
    """定期任务只推「今天这一期」，回补的过去期不发。"""
    client, db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138073", "吴三")
    _run(accounts.set_admin_caps(employee_id, ["task"]))
    _subscribe_work_task_topic(db)

    data = _publish(
        client,
        _recurring_payload(
            title="每天擦台面",
            assignee={"kind": "group", "employee_ids": [employee_id]},
            wecom_group_push=True,
        ),
    )
    definition_id = data["definition_id"]
    rows = _outbox_rows(db)
    assert len(rows) == 1
    assert f"work_task:work_task:{definition_id}:2026-09-24" in rows[0]["idempotency_key"]

    # 第二天由常驻循环物化：只新增第二天的推送，过去的期仍只有一条。
    from services.tasks.service import TaskService

    service = TaskService(db, now=lambda: datetime(2026, 9, 25, 9, 0, tzinfo=CHINA_TZ))
    _run(service.materialize_recurring(today="2026-09-25"))
    _run(service.push_recurring_today(definition_ids=[definition_id]))
    rows = _outbox_rows(db)
    keys = sorted(row["idempotency_key"] for row in rows)
    assert f"work_task:work_task:{definition_id}:2026-09-25" in keys[-1]
    assert len(rows) == 2


def test_push_failure_does_not_block_task_creation(tasks_http, monkeypatch):
    """企微入队抛异常时只记日志：任务照常创建、响应不报错。"""
    import services.tasks.service as tasks_service

    def _boom(*args, **kwargs):
        raise RuntimeError("wecom down")

    monkeypatch.setattr(tasks_service, "enqueue_work_task_push", _boom)
    client, db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138074", "郑四")
    _run(accounts.set_admin_caps(employee_id, ["task"]))
    _subscribe_work_task_topic(db)

    data = _publish(
        client,
        _payload(
            title="推送挂了也要建任务",
            assignee={"kind": "group", "employee_ids": [employee_id]},
            wecom_group_push=True,
        ),
    )
    assert data["instance"]["title"] == "推送挂了也要建任务"
    assert "warning" not in data
    assert _outbox_rows(db) == []


def test_overdue_does_not_push_anything(tasks_http):
    """逾期不产生任何企微消息：只有发布那一条，补交也不新增。"""
    client, db, accounts = tasks_http
    _admin_cookie(client)
    employee_id = _employee_id(accounts, "13800138075", "王五")
    _run(accounts.set_admin_caps(employee_id, ["task"]))
    _subscribe_work_task_topic(db)

    data = _publish(
        client,
        _payload(
            title="逾期也不催群",
            assignee={"kind": "group", "employee_ids": [employee_id]},
            wecom_group_push=True,
        ),
    )
    assert len(_outbox_rows(db)) == 1

    _set_deadline(db, data["instance"]["id"], "2026-09-23T18:00:00+08:00")
    _staff_cookie(client, accounts, "13800138075")
    capture_id = _upload_photo(client)
    submitted = _submit(client, data["instance"]["id"], capture_id)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["instance"]["overdue"] is False  # 待验收不算逾期
    assert len(_outbox_rows(db)) == 1
