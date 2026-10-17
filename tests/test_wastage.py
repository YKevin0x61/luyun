#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日常损耗登记（从 worktool2 那个小程序搬过来）：台账、统计、模板、照片。

主缝 = 服务层 + 真库 + 注入时钟 + `FakeCaptureStore`。HTTP 面另有一小段薄缝，
只回答「谁能打、打进去会怎样」——两个身份走同一个面，差别在归属与统计那一档 403。

口径来源是源项目的 `cloudfunctions/manageWastage`：六个原因、单位候选、
「其他」必须写说明、每人 20 个模板、一段日期内的四样统计。
"""

import asyncio
import io
import tempfile
import unittest
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.auth as auth_module
import api.wastage as wastage_module
import main as main_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from db_core.schema import ADMIN_READ_ONLY_TABLES, WASTAGE_TABLES
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.wastage.store import (
    MAX_IMAGES,
    MAX_TEMPLATES,
    WastageActor,
    WastageError,
    WastageStore,
)

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
PHONE2 = "13900139000"
NAME2 = "李四"
# 2026-09-24 10:00（北京时）→ 自然日的今天 = 9/24。
FIXED_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-09-24"
YESTERDAY = "2026-09-23"


def _staff(employee_id, name=NAME):
    return WastageActor(kind="staff", employee_id=employee_id, name=name)


ADMIN_ACTOR = WastageActor(kind="admin", employee_id=None, name="管理端")


class WastageStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_database_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        await self.db.connect()
        self.accounts = EmployeeAccounts(self.db, now=lambda: FIXED_NOW)
        self.captures = FakeCaptureStore()
        self.store = WastageStore(
            self.db, captures=self.captures, now=lambda: FIXED_NOW
        )

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_database_dir
        self._tmpdir.cleanup()

    async def _employee(self, phone=PHONE, name=NAME):
        employee = await self.accounts.register(phone, PASSWORD, name)
        await self.accounts.approve(employee["id"])
        return employee

    # ── 登记与归属 ──────────────────────────────────────────────────────────
    async def test_employee_records_one_and_sees_only_own(self):
        """员工登记一笔：自己看得到，另一位员工看不到（归属是服务层一处判的）。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)

        created = await self.store.add_record(
            _staff(first["id"]),
            item_name="五花肉",
            quantity=1.5,
            unit="kg",
            reason="spoiled",
            remark="夏天没放住",
            wastage_date=TODAY,
        )

        self.assertEqual(created["item_name"], "五花肉")
        self.assertEqual(created["quantity"], 1.5)
        self.assertEqual(created["unit"], "kg")
        self.assertEqual(created["reason"], "spoiled")
        self.assertEqual(created["reason_text"], "变质腐败")
        self.assertEqual(created["wastage_date"], TODAY)
        self.assertEqual(created["operator_kind"], "staff")
        self.assertEqual(created["employee_id"], first["id"])
        self.assertEqual(created["operator_name"], NAME)

        mine = await self.store.list_records(_staff(first["id"]))
        self.assertEqual(mine["total"], 1)
        self.assertEqual(mine["records"][0]["id"], created["id"])

        other = await self.store.list_records(_staff(second["id"]))
        self.assertEqual(other["total"], 0)

    async def test_admin_sees_the_whole_shop(self):
        """管理端那一档不加人过滤：谁的损耗都在分析页里。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        for actor, item in ((_staff(first["id"]), "青菜"), (_staff(second["id"]), "豆腐")):
            await self.store.add_record(
                actor, item_name=item, quantity=1, unit="份", reason="expired"
            )
        await self.store.add_record(
            ADMIN_ACTOR, item_name="鸡蛋", quantity=6, unit="个", reason="damaged"
        )

        listed = await self.store.list_records(ADMIN_ACTOR)
        self.assertEqual(listed["total"], 3)
        # 新的在前（同一天登记的按 id 倒序，最后插的那条在最前）。
        self.assertEqual(listed["records"][0]["item_name"], "鸡蛋")
        self.assertEqual(listed["records"][0]["operator_kind"], "admin")
        self.assertIsNone(listed["records"][0]["employee_id"])
        self.assertEqual(listed["records"][0]["operator_name"], "管理端")

    async def test_filters_by_range_reason_and_keyword(self):
        """明细的三把筛子：日期范围、原因、物料名（关键词走参数化的 LIKE）。"""
        employee = await self._employee()
        actor = _staff(employee["id"])
        await self.store.add_record(
            actor, item_name="五花肉", quantity=1, unit="kg", reason="spoiled",
            wastage_date=TODAY,
        )
        await self.store.add_record(
            actor, item_name="豆芽", quantity=2, unit="斤", reason="excess",
            wastage_date=YESTERDAY,
        )

        today_only = await self.store.list_records(
            actor, start_date=TODAY, end_date=TODAY
        )
        self.assertEqual([r["item_name"] for r in today_only["records"]], ["五花肉"])

        by_reason = await self.store.list_records(actor, reason="excess")
        self.assertEqual([r["item_name"] for r in by_reason["records"]], ["豆芽"])

        by_keyword = await self.store.list_records(actor, keyword="花")
        self.assertEqual([r["item_name"] for r in by_keyword["records"]], ["五花肉"])

        # 关键词里的 LIKE 通配符要当字面量：搜一个 `%` 不该把全表捞出来。
        literal = await self.store.list_records(actor, keyword="%")
        self.assertEqual(literal["total"], 0)

    # ── 校验 ────────────────────────────────────────────────────────────────
    async def test_input_guards(self):
        """数量、单位、原因、「其他」的说明、未来的日子。"""
        employee = await self._employee()
        actor = _staff(employee["id"])

        async def rejected(code, **kwargs):
            payload = {
                "item_name": "青菜",
                "quantity": 1,
                "unit": "份",
                "reason": "spoiled",
            }
            payload.update(kwargs)
            with self.assertRaises(WastageError) as caught:
                await self.store.add_record(actor, **payload)
            self.assertEqual(caught.exception.code, code)

        await rejected("missing_item_name", item_name="  ")
        await rejected("quantity_not_positive", quantity=0)
        await rejected("quantity_not_positive", quantity=-1)
        await rejected("invalid_quantity", quantity="abc")
        await rejected("missing_unit", unit="")
        await rejected("invalid_reason", reason="melted")
        await rejected("missing_remark", reason="other", remark="")
        await rejected("future_day", wastage_date="2026-09-25")
        await rejected("remark_too_long", remark="字" * 201)

        # 「其他」写了说明就能过；三位小数是允许的（界面上 1.25 kg 很常见）。
        ok = await self.store.add_record(
            actor,
            item_name="青菜",
            quantity=1.25,
            unit="kg",
            reason="other",
            remark="客人退菜",
        )
        self.assertEqual(ok["quantity"], 1.25)
        self.assertEqual(ok["remark"], "客人退菜")

    # ── 统计 ────────────────────────────────────────────────────────────────
    async def test_statistics_totals_reasons_ranking_and_daily(self):
        """按月统计四样：总笔数 / 物料种类 / 原因分布 / 物料排行 / 逐日趋势。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        a, b = _staff(first["id"]), _staff(second["id"])

        await self.store.add_record(
            a, item_name="五花肉", quantity=2, unit="kg", reason="spoiled",
            wastage_date="2026-09-02",
        )
        await self.store.add_record(
            a, item_name="五花肉", quantity=1, unit="kg", reason="spoiled",
            wastage_date="2026-09-02",
        )
        await self.store.add_record(
            b, item_name="豆腐", quantity=3, unit="盒", reason="expired",
            wastage_date="2026-09-05",
        )
        # 上个月的这一笔不该进 9 月的账。
        await self.store.add_record(
            b, item_name="豆芽", quantity=1, unit="斤", reason="excess",
            wastage_date="2026-08-31",
        )

        stats = await self.store.statistics(month="2026-09")

        self.assertEqual(stats["start_date"], "2026-09-01")
        self.assertEqual(stats["end_date"], "2026-09-30")
        self.assertEqual(stats["total_count"], 3)
        self.assertEqual(stats["item_type_count"], 2)
        self.assertEqual(
            [(row["reason"], row["count"], row["percentage"]) for row in stats["by_reason"]],
            [("spoiled", 2, 67), ("expired", 1, 33)],
        )
        self.assertEqual(
            [(row["item_name"], row["count"], row["total_quantity"], row["unit"])
             for row in stats["top_items"]],
            [("五花肉", 2, 3.0, "kg"), ("豆腐", 1, 3.0, "盒")],
        )
        self.assertEqual(
            [(row["date"], row["count"]) for row in stats["daily_stats"]],
            [("2026-09-02", 2), ("2026-09-05", 1)],
        )

    async def test_statistics_rejects_bad_month(self):
        with self.assertRaises(WastageError) as caught:
            await self.store.statistics(month="2026-9")
        self.assertEqual(caught.exception.code, "invalid_month")

    # ── 模板 ────────────────────────────────────────────────────────────────
    async def test_templates_are_saved_listed_and_deleted_by_owner(self):
        """存模板 → 列表里在前 → 只能删自己的 → 自动模板来自自己的历史。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        a, b = _staff(first["id"]), _staff(second["id"])

        await self.store.save_template(a, item_name="五花肉", unit="kg", reason="spoiled")
        # 历史高频：a 记过两次「青菜 / 份 / 变质」，它会作为自动模板补进来。
        for _ in range(2):
            await self.store.add_record(
                a, item_name="青菜", quantity=1, unit="份", reason="spoiled"
            )
        # b 的记录不该出现在 a 的模板里。
        await self.store.add_record(
            b, item_name="豆腐", quantity=1, unit="盒", reason="expired"
        )

        mine = await self.store.templates(a)
        names = [(t["item_name"], t["is_custom"]) for t in mine["templates"]]
        self.assertIn(("五花肉", True), names)
        self.assertIn(("青菜", False), names)
        self.assertNotIn(("豆腐", False), names)

        custom_id = mine["templates"][0]["id"]
        # 别人的模板：对 b 来说是「不存在」。
        with self.assertRaises(WastageError) as caught:
            await self.store.delete_template(b, custom_id)
        self.assertEqual(caught.exception.code, "unknown_template")
        self.assertEqual(len((await self.store.templates(a))["templates"]), 2)

        await self.store.delete_template(a, custom_id)
        self.assertEqual(len((await self.store.templates(a))["templates"]), 1)

    async def test_templates_reject_duplicates_and_the_cap(self):
        employee = await self._employee()
        actor = _staff(employee["id"])
        await self.store.save_template(actor, item_name="五花肉", unit="kg", reason="spoiled")

        with self.assertRaises(WastageError) as caught:
            await self.store.save_template(
                actor, item_name="五花肉", unit="kg", reason="spoiled"
            )
        self.assertEqual(caught.exception.code, "template_exists")

        for index in range(MAX_TEMPLATES + 2):
            name = f"物料{index}"
            try:
                await self.store.save_template(
                    actor, item_name=name, unit="份", reason="expired"
                )
            except WastageError as exc:
                self.assertEqual(exc.code, "too_many_templates")
                break
        else:  # pragma: no cover - 上限没生效才会走到这里
            self.fail("模板数超过上限却没有被拒")

        rows = await self.store.templates(actor)
        custom = [t for t in rows["templates"] if t["is_custom"]]
        self.assertEqual(len(custom), MAX_TEMPLATES)

    # ── 照片 ────────────────────────────────────────────────────────────────
    async def test_images_bind_to_the_record_and_stay_private(self):
        """先传图、再带 id 提交：图片挂上记录，别人读不到。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        a, b = _staff(first["id"]), _staff(second["id"])

        image = await self.store.add_image(a, b"\xff\xd8\xff\xe0jpeg", "image/jpeg")
        # 真实存储给的是 32 位十六进制文件名；测试里注入的是 `FakeCaptureStore`，
        # 只断言「拿到了一个 id」——形状由那两套实现各自保证。
        self.assertTrue(image["capture_id"])

        record = await self.store.add_record(
            a,
            item_name="五花肉",
            quantity=1,
            unit="kg",
            reason="spoiled",
            image_ids=[image["capture_id"]],
        )
        self.assertEqual(len(record["images"]), 1)
        self.assertEqual(record["images"][0]["capture_id"], image["capture_id"])
        self.assertEqual(
            record["images"][0]["url"], f"/api/wastage/images/{image['capture_id']}"
        )

        # 归属：a 读得到，b 读不到（一律「不存在」，不告诉 b 这张图存在）。
        self.assertIsNotNone(await self.store.image_meta(a, image["capture_id"]))
        self.assertIsNone(await self.store.image_meta(b, image["capture_id"]))
        self.assertIsNone(await self.store.image_bytes(b, image["capture_id"]))
        # 管理端看得到全店的图（分析页要点开看）。
        self.assertIsNotNone(await self.store.image_meta(ADMIN_ACTOR, image["capture_id"]))

    async def test_image_guards(self):
        """别人的图不能被绑到自己这条记录上；超过 3 张不收。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        a, b = _staff(first["id"]), _staff(second["id"])

        stolen = await self.store.add_image(b, b"\xff\xd8\xff\xe0jpeg", "image/jpeg")
        with self.assertRaises(WastageError) as caught:
            await self.store.add_record(
                a,
                item_name="五花肉",
                quantity=1,
                unit="kg",
                reason="spoiled",
                image_ids=[stolen["capture_id"]],
            )
        self.assertEqual(caught.exception.code, "unknown_image")

        ids = [
            (await self.store.add_image(a, b"\xff\xd8\xff\xe0jpeg", "image/jpeg"))["capture_id"]
            for _ in range(MAX_IMAGES + 1)
        ]
        with self.assertRaises(WastageError) as caught:
            await self.store.add_record(
                a,
                item_name="五花肉",
                quantity=1,
                unit="kg",
                reason="spoiled",
                image_ids=ids,
            )
        self.assertEqual(caught.exception.code, "too_many_images")

    async def test_history_suggestions_come_from_own_records(self):
        """名字补全：员工只看到自己记过的，管理端看到全店的。"""
        first = await self._employee()
        second = await self._employee(PHONE2, NAME2)
        a, b = _staff(first["id"]), _staff(second["id"])
        await self.store.add_record(a, item_name="五花肉", quantity=1, unit="kg", reason="spoiled")
        await self.store.add_record(b, item_name="豆腐", quantity=1, unit="盒", reason="expired")

        self.assertEqual(await self.store.suggestions(a), ["五花肉"])
        self.assertEqual(
            sorted(await self.store.suggestions(ADMIN_ACTOR)), sorted(["五花肉", "豆腐"])
        )


class WastageTableContractTest(unittest.TestCase):
    """新表进表名清单，Admin 的数据浏览器里只读（写入口在 HTTP 那一侧钉）。"""

    def test_new_tables_are_registered_read_only(self):
        self.assertIn("wastage_records", WASTAGE_TABLES)
        for table in WASTAGE_TABLES:
            self.assertIn(table, ADMIN_READ_ONLY_TABLES)

        from api.admin import _admin_catalog

        catalog = _admin_catalog()
        for table in WASTAGE_TABLES:
            self.assertIn(table, catalog["tables"])
            self.assertEqual(catalog["table_meta"][table]["group"], "wastage")
            self.assertTrue(catalog["table_meta"][table]["read_only"])
        groups = {group["key"]: group for group in catalog["groups"]}
        self.assertEqual(set(groups["wastage"]["tables"]), set(WASTAGE_TABLES))


# ── HTTP 薄缝：谁能打、打进去会怎样 ──────────────────────────────────────────


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


@pytest.fixture
def wastage_http(tmp_path, monkeypatch):
    real_init = WastageStore.__init__

    def _pinned_init(self, *args, **kwargs):
        kwargs.setdefault("now", lambda: FIXED_NOW)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(WastageStore, "__init__", _pinned_init)
    # 照片目录：这一轮指到 tmp_path（`_captures()` 是模块级单例，先清掉上一次的）。
    monkeypatch.setattr(wastage_module, "_capture_store", None)

    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    monkeypatch.setattr(main_module, "employee_accounts", accounts)

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(wastage_module.router)
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
    monkeypatch.setattr(wastage_module, "_capture_store", None)
    settings.DATABASE_DIR = old


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
    """换回管理端 cookie（先清掉员工那个）。"""
    client.cookies.clear()
    login = client.post(
        "/api/auth/login",
        json={"username": ADMIN["username"], "password": ADMIN["password"]},
    )
    assert login.status_code == 200, login.text


def test_endpoints_need_some_session(wastage_http):
    """匿名一律 401：登记、明细、统计、上传都进不去。"""
    client, _db, _accounts = wastage_http
    client.cookies.clear()
    assert client.get("/api/wastage/meta").status_code == 401
    assert client.get("/api/wastage/records").status_code == 401
    assert client.get("/api/wastage/statistics").status_code == 401
    assert client.post("/api/wastage/records", json={"item_name": "青菜"}).status_code == 401
    assert client.post(
        "/api/wastage/images", files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")}
    ).status_code == 401


def test_staff_records_and_cannot_read_statistics(wastage_http):
    """员工能登记、只看得到自己的；统计那一条是 403（不是 404）。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    meta = client.get("/api/wastage/meta")
    assert meta.status_code == 200, meta.text
    body = meta.json()
    assert body["today"] == TODAY
    assert [row["value"] for row in body["meta"]["reasons"]] == [
        "spoiled", "expired", "damaged", "production_fail", "excess", "other",
    ]
    assert body["meta"]["max_images"] == MAX_IMAGES

    created = client.post(
        "/api/wastage/records",
        json={
            "item_name": "五花肉",
            "quantity": 1.5,
            "unit": "kg",
            "reason": "spoiled",
            "wastage_date": TODAY,
        },
    )
    assert created.status_code == 200, created.text
    record = created.json()["record"]
    assert record["item_name"] == "五花肉"

    listed = client.get("/api/wastage/records")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    # 校验失败回一句中文 400（不是 pydantic 的英文 422）。
    bad = client.post(
        "/api/wastage/records",
        json={"item_name": "青菜", "quantity": 0, "unit": "份", "reason": "spoiled"},
    )
    assert bad.status_code == 400, bad.text
    assert "数量要大于 0" in bad.json()["detail"]

    forbidden = client.get("/api/wastage/statistics", params={"month": "2026-09"})
    assert forbidden.status_code == 403, forbidden.text


def test_admin_reads_statistics_and_sees_every_record(wastage_http):
    """管理端：统计拿得到、明细是全店的。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)
    client.post(
        "/api/wastage/records",
        json={
            "item_name": "五花肉",
            "quantity": 1,
            "unit": "kg",
            "reason": "spoiled",
            "wastage_date": TODAY,
        },
    )

    _admin_cookie(client)
    stats = client.get("/api/wastage/statistics", params={"month": "2026-09"})
    assert stats.status_code == 200, stats.text
    payload = stats.json()
    assert payload["total_count"] == 1
    assert payload["by_reason"][0]["reason"] == "spoiled"
    assert payload["by_reason"][0]["percentage"] == 100

    listed = client.get("/api/wastage/records")
    assert listed.json()["total"] == 1


def test_images_upload_bind_and_read(wastage_http):
    """照片走真实的 HTTP 链路：传 → 提交时带上 → 自己读得到、别人读不到。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    upload = client.post(
        "/api/wastage/images",
        files={"file": ("shot.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert upload.status_code == 200, upload.text
    capture_id = upload.json()["image"]["capture_id"]

    created = client.post(
        "/api/wastage/records",
        json={
            "item_name": "五花肉",
            "quantity": 1,
            "unit": "kg",
            "reason": "spoiled",
            "wastage_date": TODAY,
            "image_ids": [capture_id],
        },
    )
    assert created.status_code == 200, created.text
    assert len(created.json()["record"]["images"]) == 1

    mine = client.get(f"/api/wastage/images/{capture_id}")
    assert mine.status_code == 200
    assert mine.headers["content-type"].startswith("image/jpeg")

    # 另一位员工（另一个 cookie）读同一张图：404，不是 403。
    _employee_id(accounts, PHONE2, NAME2)
    _staff_cookie(client, accounts, PHONE2)
    assert client.get(f"/api/wastage/images/{capture_id}").status_code == 404
    # 管理端读得到（分析页要点开看）。
    _admin_cookie(client)
    assert client.get(f"/api/wastage/images/{capture_id}").status_code == 200


def test_upload_rejects_a_non_image(wastage_http):
    """不是图片的字节一律 400：类型按字节内容判，不信客户端的 Content-Type。"""
    client, _db, accounts = wastage_http
    _employee_id(accounts)
    _staff_cookie(client, accounts)

    rejected = client.post(
        "/api/wastage/images",
        files={"file": ("evil.svg", b"<svg onload=alert(1)></svg>", "image/svg+xml")},
    )
    assert rejected.status_code == 400, rejected.text
