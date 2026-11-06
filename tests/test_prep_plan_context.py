#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货复盘的环境信号（`prep_plan_context`，票 02）。

盯住四件事：节假日命中、天气成功与失败回落（**测试里不发真网络请求**）、
档口人数按岗位聚合并把映射不到的落 `other`、以及 upsert 只补空字段
（人工备注与 `captured_at` 不被覆盖）。

外加一条接线：跑一次 `write_snapshot` 后当天 context 行存在。
"""

import asyncio
import unittest
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.prep_plan import manager_router, reader_router, router
from api.security import require_any_identity_session, verify_admin_token
from database import CHINA_TZ, DatabaseManager, get_db
from services.prep_plan_context import (
    WEATHER_SETTINGS_KEY,
    PrepPlanContextService,
)

DAY = "2026-10-22"
NOW = datetime(2026, 10, 22, 8, 0, tzinfo=CHINA_TZ)
STAMP = NOW.isoformat()
WEATHER = {"text": "晴", "temp_max": 28.0, "temp_min": 21.0, "precip": 0.0}


class FakeWeatherClient:
    """替身：记录调用、可注入失败；绝不出网。"""

    def __init__(self, *, weather=WEATHER, error=None, location_id="101280101"):
        self.weather = dict(weather)
        self.error = error
        self.location_id = location_id
        self.lookups = []
        self.daily_calls = []

    async def lookup_location(self, city, api_key):
        self.lookups.append((city, api_key))
        if self.error is not None:
            raise self.error
        return self.location_id

    async def daily(self, location_id, api_key, day):
        self.daily_calls.append((location_id, api_key, day))
        if self.error is not None:
            raise self.error
        return dict(self.weather)


class ContextRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.fake = FakeWeatherClient()
        self.service = PrepPlanContextService(weather_client=self.fake)

    async def asyncTearDown(self):
        await self.db.close()

    async def _seed_holiday(self, name="国庆节", day=DAY):
        tdb = self.db.table("statutory_holidays")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO statutory_holidays (day, name, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (day, name, STAMP, STAMP),
            )
        await tdb.commit()

    async def _seed_zone(self, name):
        tdb = self.db.table("hygiene_zones")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO hygiene_zones (name, created_at, updated_at) VALUES (?, ?, ?)",
                (name, STAMP, STAMP),
            )
            zone_id = cursor.lastrowid
        await tdb.commit()
        return zone_id

    async def _seed_assignment(self, employee_id, *, zone_id=None, shift_id=1, day=DAY):
        tdb = self.db.table("staff_assignments")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO staff_assignments (
                    employee_id, business_date, shift_id, zone_id, source,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'rule', ?, ?)
                """,
                (employee_id, day, shift_id, zone_id, STAMP, STAMP),
            )
        await tdb.commit()

    async def _seed_weather_settings(self):
        await self.db.settings_set_json(
            WEATHER_SETTINGS_KEY, {"api_key": "k1", "city": "广州"}
        )

    # ---------------------------------------------------------------- 节假日

    async def test_holiday_name_is_snapshotted(self):
        await self._seed_holiday("国庆节")

        await self.service.refresh(self.db, DAY, now=NOW)

        context = await self.service.get_context(self.db, DAY)
        self.assertEqual(context["holiday_name"], "国庆节")

    async def test_missing_holiday_is_null_and_does_not_raise(self):
        result = await self.service.refresh(self.db, DAY, now=NOW)

        self.assertIsNone(result["holiday_name"])
        context = await self.service.get_context(self.db, DAY)
        self.assertIsNotNone(context)
        self.assertIsNone(context["holiday_name"])

    # ---------------------------------------------------------------- 天气

    async def test_weather_success_is_stored_and_location_is_cached(self):
        await self._seed_weather_settings()

        await self.service.refresh(self.db, DAY, now=NOW)

        context = await self.service.get_context(self.db, DAY)
        self.assertEqual(context["weather"]["text"], "晴")
        self.assertEqual(context["weather"]["temp_max"], 28.0)
        self.assertEqual(context["weather"]["temp_min"], 21.0)
        self.assertTrue(context["weather"]["fetched_at"])
        # GEO 换出来的 id 缓存回 app_settings，key 与城市名原样保留
        config = await self.db.settings_get_json(WEATHER_SETTINGS_KEY)
        self.assertEqual(config["location_id"], "101280101")
        self.assertEqual(config["api_key"], "k1")
        self.assertEqual(config["city"], "广州")

    async def test_cached_location_is_not_looked_up_again(self):
        await self._seed_weather_settings()

        await self.service.refresh(self.db, DAY, now=NOW)
        await self.service.refresh(self.db, DAY, now=NOW + timedelta(hours=1))

        self.assertEqual(len(self.fake.lookups), 1)
        self.assertEqual(len(self.fake.daily_calls), 2)

    async def test_weather_failure_keeps_the_existing_value(self):
        await self._seed_weather_settings()
        await self.service.refresh(self.db, DAY, now=NOW)
        before = await self.service.get_context(self.db, DAY)

        failing = PrepPlanContextService(
            weather_client=FakeWeatherClient(error=RuntimeError("网络断了"))
        )
        # 不抛异常，而且日志带上日期与城市
        with self.assertLogs("services.prep_plan_context", level="WARNING") as captured:
            await failing.refresh(self.db, DAY, now=NOW + timedelta(hours=1))

        after = await self.service.get_context(self.db, DAY)
        self.assertEqual(after["weather"], before["weather"])
        joined = "\n".join(captured.output)
        self.assertIn(f"plan_day={DAY}", joined)
        self.assertIn("广州", joined)

    async def test_no_api_key_never_fetches_and_keeps_the_existing_value(self):
        await self._seed_weather_settings()
        await self.service.refresh(self.db, DAY, now=NOW)
        before = await self.service.get_context(self.db, DAY)

        # 管理员把 key 清空：后续刷新不能把已有天气抹掉，也不能出网
        await self.db.settings_set_json(WEATHER_SETTINGS_KEY, {"api_key": "", "city": ""})
        fresh = FakeWeatherClient()
        await PrepPlanContextService(weather_client=fresh).refresh(self.db, DAY, now=NOW)

        after = await self.service.get_context(self.db, DAY)
        self.assertEqual(after["weather"], before["weather"])
        self.assertEqual(fresh.lookups, [])
        self.assertEqual(fresh.daily_calls, [])

    async def test_first_refresh_without_key_writes_an_empty_weather_object(self):
        result = await self.service.refresh(self.db, DAY, now=NOW)

        context = await self.service.get_context(self.db, DAY)
        self.assertEqual(context["weather"], {})
        self.assertFalse(result["weather"])
        self.assertEqual(self.fake.daily_calls, [])

    # ---------------------------------------------------------------- 人力

    async def test_staff_counts_are_grouped_by_position_and_unknown_goes_to_other(self):
        shulong = await self._seed_zone("熟笼")
        xiandang = await self._seed_zone("馅档")
        anban = await self._seed_zone("案板")
        warehouse = await self._seed_zone("仓库")
        await self._seed_assignment(1, zone_id=shulong)
        await self._seed_assignment(2, zone_id=xiandang)
        await self._seed_assignment(3, zone_id=anban)
        await self._seed_assignment(4, zone_id=warehouse)
        await self._seed_assignment(5, zone_id=None)          # 没配区
        await self._seed_assignment(6, zone_id=shulong, shift_id=None)  # 那天休

        await self.service.refresh(self.db, DAY, now=NOW)

        context = await self.service.get_context(self.db, DAY)
        self.assertEqual(
            context["staff"], {"熟笼": 1, "馅档": 1, "案板": 1, "other": 2}
        )

    async def test_existing_staff_counts_are_kept_when_assignments_disappear(self):
        shulong = await self._seed_zone("熟笼")
        await self._seed_assignment(1, zone_id=shulong)
        await self.service.refresh(self.db, DAY, now=NOW)
        before = await self.service.get_context(self.db, DAY)

        tdb = self.db.table("staff_assignments")
        async with tdb.conn.cursor() as cursor:
            await cursor.execute("DELETE FROM staff_assignments")
        await tdb.commit()
        await self.service.refresh(self.db, DAY, now=NOW + timedelta(hours=1))

        after = await self.service.get_context(self.db, DAY)
        self.assertEqual(after["staff"], before["staff"])

    # ---------------------------------------------------------------- 备注与 upsert

    async def test_note_survives_a_refresh_and_captured_at_is_insert_only(self):
        await self.service.set_note(self.db, DAY, "  团餐 30 桌  ", now=NOW)
        first = await self.service.get_context(self.db, DAY)
        self.assertEqual(first["note"], "团餐 30 桌")

        await self.service.refresh(self.db, DAY, now=NOW + timedelta(hours=1))
        second = await self.service.get_context(self.db, DAY)

        self.assertEqual(second["note"], "团餐 30 桌")
        self.assertEqual(second["captured_at"], first["captured_at"])
        self.assertNotEqual(second["updated_at"], first["updated_at"])

    async def test_note_upsert_does_not_clobber_the_signal_fields(self):
        await self._seed_holiday("国庆节")
        await self._seed_weather_settings()
        await self.service.refresh(self.db, DAY, now=NOW)

        await self.service.set_note(self.db, DAY, "停水", now=NOW + timedelta(hours=1))

        context = await self.service.get_context(self.db, DAY)
        self.assertEqual(context["note"], "停水")
        self.assertEqual(context["holiday_name"], "国庆节")
        self.assertEqual(context["weather"]["text"], "晴")

    # ---------------------------------------------------------------- 接线

    async def test_write_snapshot_refreshes_the_context_row(self):
        from services.prep_plan_service import prep_plan_service

        await self._seed_holiday("国庆节")

        await prep_plan_service.write_snapshot(self.db, NOW)

        context = await self.service.get_context(self.db, NOW.date().isoformat())
        self.assertIsNotNone(context)
        self.assertEqual(context["holiday_name"], "国庆节")


# ------------------------------------------------------------------ API 契约


def _run(coro):
    return asyncio.run(coro)


def _deny_admin():
    raise HTTPException(status_code=401, detail="未授权")


def _build_app(db, identity):
    app = FastAPI()
    app.include_router(reader_router)
    app.include_router(manager_router)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_any_identity_session] = lambda: identity
    app.dependency_overrides[verify_admin_token] = lambda: True
    return app


@pytest.fixture
def admin_client():
    db = DatabaseManager()
    _run(db.connect())
    with TestClient(_build_app(db, {"kind": "admin"})) as client:
        yield client, db
    _run(db.close())


@pytest.fixture
def staff_client():
    db = DatabaseManager()
    _run(db.connect())
    identity = {"kind": "staff", "employee": {"id": 7, "name": "阿明"}}
    with TestClient(_build_app(db, identity)) as client:
        yield client, db
    _run(db.close())


def test_admin_can_write_the_daily_note(admin_client):
    client, _db = admin_client

    resp = client.put(
        "/api/prep-plan/context/note",
        json={"day": DAY, "note": "  团餐 30 桌  "},
    )

    assert resp.status_code == 200
    body = client.get("/api/prep-plan/snapshots", params={"day": DAY}).json()
    assert body["context"]["note"] == "团餐 30 桌"


def test_note_defaults_to_today(admin_client):
    client, _db = admin_client
    today = datetime.now(CHINA_TZ).date().isoformat()

    resp = client.put("/api/prep-plan/context/note", json={"note": "设备检修"})

    assert resp.status_code == 200
    body = client.get("/api/prep-plan/snapshots", params={"day": today}).json()
    assert body["context"]["note"] == "设备检修"


def test_staff_cannot_write_the_daily_note(staff_client):
    client, _db = staff_client

    resp = client.put(
        "/api/prep-plan/context/note", json={"day": DAY, "note": "员工写的"}
    )

    assert resp.status_code == 403


def test_bad_day_is_a_400(admin_client):
    client, _db = admin_client

    resp = client.put(
        "/api/prep-plan/context/note", json={"day": "2026/10/22", "note": "x"}
    )

    assert resp.status_code == 400


def test_snapshots_without_context_still_returns(admin_client):
    client, _db = admin_client

    body = client.get("/api/prep-plan/snapshots", params={"day": DAY}).json()

    assert body["success"] is True
    assert body["context"] is None
