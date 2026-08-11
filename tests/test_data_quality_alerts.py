#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from pathlib import Path

from config import settings
from services.data_quality_alerts import (
    build_data_quality_status_message,
    load_reconcile_summary_for_date,
    should_alert_reconcile_from_summary,
)


class DataQualityStatusMessageTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_dir = settings.DATABASE_DIR
        settings.DATABASE_DIR = self._tmpdir.name

    def tearDown(self):
        settings.DATABASE_DIR = self._old_dir
        self._tmpdir.cleanup()

    def test_build_status_with_reconcile_json(self):
        reconcile_dir = Path(settings.DATABASE_DIR) / "reconcile"
        reconcile_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "biz_date": "2026-04-28",
            "missed_keys": 89,
            "missed_qty": 110.0,
            "miss_rate_pct": 1.2,
            "pos_total_qty": 9000,
            "db_total_qty": 8890,
            "affected_bills": 65,
        }
        (reconcile_dir / "reconcile_2026-04-28.json").write_text(
            json.dumps(summary, ensure_ascii=False),
            encoding="utf-8",
        )
        loaded = load_reconcile_summary_for_date("2026-04-28")
        self.assertEqual(loaded["missed_qty"], 110.0)
        message = build_data_quality_status_message(
            biz_date="2026-04-28",
            health={"api_failures": 2, "last_scrape_at": "2026-04-28T22:00:00+08:00"},
            reconcile_summary=loaded,
            unmapped_dishes=["新菜A", "新菜B"],
        )
        self.assertIn("数据质量日报", message)
        self.assertIn("110.0 份", message)
        self.assertIn("未映射菜品 2 个", message)
        self.assertTrue(should_alert_reconcile_from_summary(summary))

    def test_build_status_without_reconcile(self):
        message = build_data_quality_status_message(
            biz_date="2026-06-07",
            health={"api_failures": 0},
            reconcile_summary=None,
            unmapped_dishes=[],
        )
        self.assertIn("尚无报告", message)
        self.assertIn("未映射菜品: 0", message)


if __name__ == "__main__":
    unittest.main()
