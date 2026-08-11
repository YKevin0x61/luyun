#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行配置校验与持久化测试。"""

import tempfile
import unittest

from config import settings as app_settings
from database import DatabaseManager
from services import runtime_settings


class RuntimeSettingsValidationTest(unittest.TestCase):
    def test_defaults_pass_validation(self):
        result = runtime_settings.validate_runtime_settings({})
        self.assertEqual(result["work_start"], "07:30")
        self.assertEqual(result["work_end"], "21:30")
        self.assertEqual(result["interval_min"], 5)
        self.assertEqual(result["interval_max"], 20)
        self.assertTrue(result["headless"])

    def test_time_is_zero_padded(self):
        result = runtime_settings.validate_runtime_settings(
            {"work_start": "7:05", "work_end": "9:00"}
        )
        self.assertEqual(result["work_start"], "07:05")
        self.assertEqual(result["work_end"], "09:00")

    def test_rejects_cross_midnight(self):
        with self.assertRaises(ValueError):
            runtime_settings.validate_runtime_settings(
                {"work_start": "18:00", "work_end": "02:00"}
            )

    def test_rejects_equal_start_end(self):
        with self.assertRaises(ValueError):
            runtime_settings.validate_runtime_settings(
                {"work_start": "07:30", "work_end": "07:30"}
            )

    def test_rejects_interval_min_gt_max(self):
        with self.assertRaises(ValueError):
            runtime_settings.validate_runtime_settings(
                {"interval_min": 30, "interval_max": 10}
            )

    def test_rejects_bad_time_format(self):
        with self.assertRaises(ValueError):
            runtime_settings.validate_runtime_settings({"work_start": "25:00"})

    def test_rejects_out_of_range_interval(self):
        with self.assertRaises(ValueError):
            runtime_settings.validate_runtime_settings({"interval_min": 0})

    def test_coerces_bool_and_int(self):
        result = runtime_settings.validate_runtime_settings(
            {"headless": "false", "retry_count": "2", "timeout_ms": "5000"}
        )
        self.assertFalse(result["headless"])
        self.assertEqual(result["retry_count"], 2)
        self.assertEqual(result["timeout_ms"], 5000)

    def test_delivery_cancel_threshold_default_and_coerce(self):
        self.assertEqual(
            runtime_settings.validate_runtime_settings({})["delivery_cancel_miss_threshold"], 3
        )
        result = runtime_settings.validate_runtime_settings(
            {"delivery_cancel_miss_threshold": "5"}
        )
        self.assertEqual(result["delivery_cancel_miss_threshold"], 5)

    def test_rejects_out_of_range_delivery_cancel_threshold(self):
        with self.assertRaises(ValueError):
            runtime_settings.validate_runtime_settings(
                {"delivery_cancel_miss_threshold": 0}
            )
        with self.assertRaises(ValueError):
            runtime_settings.validate_runtime_settings(
                {"delivery_cancel_miss_threshold": 999}
            )


class RuntimeSettingsPersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_dir = app_settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        app_settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())

    async def asyncTearDown(self):
        await self.db.close()
        app_settings.DATABASE_DIR = self._old_dir
        self._tmpdir.cleanup()

    async def test_load_returns_defaults_when_empty(self):
        data = await runtime_settings.load_runtime_settings(self.db)
        self.assertEqual(data, runtime_settings.DEFAULT_RUNTIME_SETTINGS)

    async def test_save_then_load_round_trip(self):
        saved = await runtime_settings.save_runtime_settings(
            self.db,
            {
                "work_start": "08:00",
                "work_end": "20:00",
                "interval_min": 10,
                "interval_max": 30,
                "headless": False,
                "retry_count": 5,
                "timeout_ms": 45000,
            },
        )
        self.assertEqual(saved["work_start"], "08:00")

        reloaded = await runtime_settings.load_runtime_settings(self.db)
        self.assertEqual(reloaded["work_start"], "08:00")
        self.assertEqual(reloaded["work_end"], "20:00")
        self.assertEqual(reloaded["interval_max"], 30)
        self.assertFalse(reloaded["headless"])

        updated_at = await self.db.settings_updated_at(runtime_settings.RUNTIME_SETTINGS_KEY)
        self.assertIsNotNone(updated_at)

    async def test_save_rejects_invalid(self):
        with self.assertRaises(ValueError):
            await runtime_settings.save_runtime_settings(
                self.db, {"work_start": "22:00", "work_end": "06:00"}
            )


if __name__ == "__main__":
    unittest.main()
