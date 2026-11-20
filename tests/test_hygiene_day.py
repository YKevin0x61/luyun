#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生的一天：日界只有一把尺子（00:00，工作台日）。

三件事一起钉：

1. `services.hygiene.day` 的出口在 00:00 切日 —— 23:59:59 / 00:00 / 05:59:59 / 06:00
   四个点各钉一次。05:59:59 那条是这类 bug 的现场：页面算今天、存档算昨天，照片在盘上
   却从「今天」的窗口里消失（ADR 0125 决策 4：本店没有跨零点班次）。
2. 照片档案的窗口与标签同源。`occurred_at` 的标签早已按 00:00 算，而 `created_range`
   仍按 06:00 开窗，同一行于是有两个日期；这里钉住窗口也走 00:00。
3. 源码守卫：`services/hygiene/` 下除 `day.py` 外不许再出现 `business_date_of(...)`
   调用，也不许手写 `T06:00:00` 字面量 —— 这两样就是漂移的入口。用 AST 查调用，
   docstring 里解释「以前是 06:00」的历史说明不算违规。
"""

import ast
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.hygiene as hygiene_module
from database import CHINA_TZ
from services.hygiene.archive import created_range
from services.hygiene.attire import HygieneAttire
from services.hygiene import day

HYGIENE_DIR = Path(__file__).resolve().parent.parent / "services" / "hygiene"


class HygieneDayUnitTest(unittest.TestCase):
    """出口本身的日界与形状。"""

    def test_day_of_switches_at_midnight(self):
        cases = [
            ("2026-11-17T23:59:59+08:00", "2026-11-17"),
            ("2026-11-18T00:00:00+08:00", "2026-11-18"),
            # 05:59:59 以前算「前一天」正是被修掉的那个 bug（营业日 06:00 切）。
            ("2026-11-18T05:59:59+08:00", "2026-11-18"),
            ("2026-11-18T06:00:00+08:00", "2026-11-18"),
            ("2026-11-18T23:59:59+08:00", "2026-11-18"),
        ]
        for moment, expected in cases:
            with self.subTest(moment=moment):
                self.assertEqual(day.day_of(moment), expected)

    def test_day_of_accepts_datetime_and_utc(self):
        self.assertEqual(day.day_of(datetime(2026, 11, 18, 3, 0, tzinfo=CHINA_TZ)), "2026-11-18")
        # UTC 的 16:30 是北京时间的次日 00:30。
        self.assertEqual(day.day_of(datetime(2026, 11, 17, 16, 30, tzinfo=timezone.utc)), "2026-11-18")

    def test_today_follows_the_injected_clock(self):
        now = datetime(2026, 11, 18, 5, 59, 59, tzinfo=CHINA_TZ)
        self.assertEqual(day.today(now), "2026-11-18")
        self.assertEqual(day.today(datetime(2026, 11, 17, 23, 59, 59, tzinfo=CHINA_TZ)), "2026-11-17")

    def test_window_covers_the_whole_day(self):
        start, end = day.window("2026-11-18")
        self.assertEqual(start, datetime(2026, 11, 18, 0, 0, tzinfo=CHINA_TZ))
        self.assertEqual(end, datetime(2026, 11, 19, 0, 0, tzinfo=CHINA_TZ))

    def test_window_text_is_iso_with_offset(self):
        self.assertEqual(
            day.window_text("2026-11-18"),
            ("2026-11-18T00:00:00+08:00", "2026-11-19T00:00:00+08:00"),
        )

    def test_week_runs_monday_to_sunday(self):
        # 2026-11-18 是周三。
        self.assertEqual(day.week_of("2026-11-18"), ("2026-11-16", "2026-11-22"))
        self.assertEqual(day.week_of("2026-11-16"), ("2026-11-16", "2026-11-22"))
        self.assertEqual(day.week_of("2026-11-22"), ("2026-11-16", "2026-11-22"))

    def test_payload_shape(self):
        payload = day.payload(datetime(2026, 11, 18, 2, 0, tzinfo=CHINA_TZ))
        self.assertEqual(
            payload,
            {
                "today": "2026-11-18",
                "day_end": "2026-11-19T00:00:00+08:00",
                "weekday": 2,
                "week_from": "2026-11-16",
                "week_to": "2026-11-22",
            },
        )


class HygieneDayCutConsistencyTest(unittest.TestCase):
    """同一行的窗口与标签必须是同一个日界。"""

    def test_attire_today_follows_workbench_day(self):
        class _FakeWork:
            _conn = object()

        attire = HygieneAttire(_FakeWork(), now=lambda: datetime(2026, 11, 18, 3, 0, tzinfo=CHINA_TZ))
        self.assertEqual(attire.today(), "2026-11-18")

    def test_created_range_uses_midnight(self):
        self.assertEqual(
            created_range("2026-11-18", "2026-11-18"),
            ("2026-11-18T00:00:00+08:00", "2026-11-19T00:00:00+08:00"),
        )
        self.assertEqual(
            created_range("2026-11-16", "2026-11-22"),
            ("2026-11-16T00:00:00+08:00", "2026-11-23T00:00:00+08:00"),
        )


class HygieneDaySourceGuardTest(unittest.TestCase):
    """漂移守卫：新模块不许再拿营业日的尺子。"""

    def _modules(self):
        return sorted(
            path for path in HYGIENE_DIR.rglob("*.py") if path.name != "day.py"
        )

    def test_no_business_date_of_calls(self):
        offenders = []
        for path in self._modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name == "business_date_of":
                    offenders.append(f"{path.relative_to(HYGIENE_DIR.parent.parent)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_no_hardcoded_six_am_cut(self):
        offenders = []
        for path in self._modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if "T06:00:00" in node.value:
                        offenders.append(f"{path.relative_to(HYGIENE_DIR.parent.parent)}:{node.lineno}")
        self.assertEqual(offenders, [])


class HygieneTodayHttpTest(unittest.TestCase):
    """端点的鉴权与载荷。"""

    @staticmethod
    def _client(authed):
        app = FastAPI()
        app.include_router(hygiene_module.router)
        if authed:
            app.dependency_overrides[hygiene_module.require_session] = lambda: "test-admin"
        return TestClient(app)

    def test_today_requires_admin_session(self):
        with self._client(authed=False) as client:
            response = client.get("/api/hygiene/today")
        self.assertEqual(response.status_code, 401)

    def test_today_returns_the_day_payload(self):
        with self._client(authed=True) as client:
            response = client.get("/api/hygiene/today")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body), {"today", "day_end", "weekday", "week_from", "week_to"}
        )
        today = body["today"]
        self.assertEqual(today, day.today())
        self.assertEqual(body["day_end"], day.window_text(today)[1])
        self.assertEqual(body["weekday"], datetime.fromisoformat(today).weekday())
        self.assertEqual((body["week_from"], body["week_to"]), day.week_of(today))
